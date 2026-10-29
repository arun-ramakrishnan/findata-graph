"""Tests for the per-session index and the lastSeen window in `bili_sessions`.

The index replaced a per-session `parse_logs(log_rotation_family(log_path),
session_filter=sid)` — a full rescan of the log family per session, plus a
whole-file `read_text` per session just to test whether the session id
appeared in it. Measured on a 35-session table that was 4.1 s of 7.7 s, and
71 `/proc` walks (22,072 `read_text` calls) for the dashboard discovery.

The equivalence tests here are the point: the optimisation must produce exactly
what the per-session parse produced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.analytics import bili_sessions as bs
from helpers.analytics.bili_analytics import (
    Analysis,
    _viol_gap_minutes,
    log_rotation_family,
    parse_logs,
)


def _line(sid: str, kind: str = "idle", *, tok: int = 1000, cached: int = 900) -> str:
    return (
        f"2026-10-17T04:25:16.517Z [info] [sess={sid}] [v=0.1.197] [{sid}] "
        f"nudge {kind}: usage=37.0% ({tok}/792576), "
        f"growth=1000/90000 (ref=500, interval=200000), "
        f"pendingT1=199877/200000 model=space-bunny-free, "
        f'reason="max compressible 199877 < threshold 200000"'
    )


def _usage_line(sid: str, *, tok: int, cached: int) -> str:
    return (
        f"2026-10-17T04:25:17.000Z [info] [sess={sid}] [v=0.1.197] [{sid}] "
        f"[acp-usage] input={tok} cached={cached} (cache hit "
        f"{round(100 * cached / tok)}%)"
    )


def _violation_line(sid: str) -> str:
    return (
        f"2026-10-17T04:25:18.000Z [info] [sess={sid}] [v=0.1.197] [{sid}] "
        f"reasoning-pair-violated: old=x new=y"
    )


def _write_family(tmp_path: Path, mapping: dict[str, list[str]]) -> Path:
    """Write one rotated file per session group; return the LIVE log path."""
    live = tmp_path / "bili.log"
    live.write_text("")
    for i, (sid, lines) in enumerate(mapping.items()):
        p = live if i == len(mapping) - 1 else tmp_path / f"bili.log.old.{i}"
        p.write_text("\n".join(lines) + "\n")
    return live


def _sig(a: Analysis) -> tuple:
    return (
        len(a.nudges),
        len(a.usages),
        len(a.folds),
        sum(n.growth for n in a.nudges),
        sum(u.input_tok for u in a.usages),
        sum(f.saved_tok for f in a.folds),
        tuple(sorted(a.intervals_seen.items())),
        a.reasoning_pair_violations,
        a.effective_interval(),
        tuple(sorted(w.kind for w in a.warnings)),
    )


class TestIndexMatchesPerSessionParse:
    def test_equivalent_for_every_session(self, tmp_path: Path):
        sids = ["ses_aaa", "ses_bbb", "ses_ccc", "ses_ddd"]
        mapping = {
            s: [
                _usage_line(s, tok=1000 * (i + 1), cached=900 * (i + 1)),
                _line(s),
                _violation_line(s),
            ]
            for i, s in enumerate(sids)
        }
        live = _write_family(tmp_path, mapping)

        index = bs._family_index(live)
        assert set(index) == set(sids), "every session in the family must get a bucket"
        for sid in sids:
            per_session = parse_logs(log_rotation_family(live), session_filter=sid)
            assert _sig(index[sid]) == _sig(per_session), sid

    def test_reasoning_pair_violations_are_partitioned(self, tmp_path: Path):
        """`parse_log` keeps this as a bare int, so it cannot be partitioned
        post-hoc; it has to come back out of `warnings`."""
        mapping = {
            "ses_aaa": [_line("ses_aaa"), _violation_line("ses_aaa"), _violation_line("ses_aaa")],
            "ses_bbb": [_line("ses_bbb")],
        }
        live = _write_family(tmp_path, mapping)
        index = bs._family_index(live)
        assert index["ses_aaa"].reasoning_pair_violations == 2
        assert index["ses_bbb"].reasoning_pair_violations == 0
        for sid in mapping:
            assert (
                index[sid].reasoning_pair_violations
                == parse_logs(
                    log_rotation_family(live), session_filter=sid
                ).reasoning_pair_violations
            )

    def test_interval_keys_stay_strings(self, tmp_path: Path):
        live = _write_family(tmp_path, {"ses_aaa": [_line("ses_aaa")]})
        bucket = bs._family_index(live)["ses_aaa"]
        # `--json` consumers see {"200000": n}; an int key would silently
        # change the shape of the data the perf fix is speeding up.
        assert all(isinstance(k, str) for k in bucket.intervals_seen)

    def test_buckets_are_isolated(self, tmp_path: Path):
        mapping = {
            "ses_aaa": [_usage_line("ses_aaa", tok=111, cached=11), _line("ses_aaa")],
            "ses_bbb": [_usage_line("ses_bbb", tok=999, cached=99), _line("ses_bbb")],
        }
        live = _write_family(tmp_path, mapping)
        index = bs._family_index(live)
        assert sum(u.input_tok for u in index["ses_aaa"].usages) == 111
        assert sum(u.input_tok for u in index["ses_bbb"].usages) == 999

    def test_sessions_are_timestamp_sorted(self, tmp_path: Path):
        early = _line("ses_aaa").replace("04:25:16.517Z", "01:00:00.000Z")
        late = _line("ses_aaa").replace("04:25:16.517Z", "23:00:00.000Z")
        live = _write_family(tmp_path, {"ses_aaa": [late, early]})
        bucket = bs._family_index(live)["ses_aaa"]
        assert [n.ts for n in bucket.nudges] == sorted(n.ts for n in bucket.nudges)

    def test_family_is_parsed_once_not_per_session(self, tmp_path: Path, monkeypatch):
        sids = [f"ses_{c}" for c in "abcdef"]
        mapping = {s: [_line(s)] for s in sids}
        live = _write_family(tmp_path, mapping)
        calls = []
        real = bs.parse_logs

        def spy(paths, session_filter=None):
            calls.append(session_filter)
            return real(paths, session_filter=session_filter)

        monkeypatch.setattr(bs, "parse_logs", spy)
        bs._family_index.cache_clear()
        bs._family_index(live)
        assert calls == [None], "one unfiltered parse for the whole family"


class TestSessionAnalysisLookup:
    def test_subsession_resolves_to_parent(self, tmp_path: Path):
        live = _write_family(tmp_path, {"ses_aaa": [_line("ses_aaa")]})
        parent = bs._session_analysis(live, "ses_aaa")
        sub = bs._session_analysis(live, "ses_aaa|sub:xyz")
        # A sub-session logs under its PARENT id; without this the row went empty.
        assert sub is not None and parent is not None and _sig(sub) == _sig(parent)

    def test_unknown_session_returns_none(self, tmp_path: Path):
        live = _write_family(tmp_path, {"ses_aaa": [_line("ses_aaa")]})
        assert bs._session_analysis(live, "ses_nope") is None

    def test_missing_log_returns_none(self):
        assert bs._session_analysis(None, "ses_aaa") is None


class TestDashboardDiscoveryIsCached:
    def test_proc_walk_happens_once(self, monkeypatch, tmp_path: Path):
        proc = tmp_path / "proc"
        (proc / "111").mkdir(parents=True)
        (proc / "111" / "cmdline").write_text("node\x00billion-context\x00--port\x0019999\x00")
        (proc / "222").mkdir()
        (proc / "222" / "cmdline").write_text("bash\x00")

        walks = []
        real_iterdir = Path.iterdir

        def fake_iterdir(self):
            walks.append(self)
            return real_iterdir(proc)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)

        bs._dashboard_urls.cache_clear()
        first = bs._dashboard_urls()
        walks_after_first = len(walks)
        second = bs._dashboard_urls()

        assert first == second
        assert walks_after_first == 1, "the /proc walk must not repeat per session"
        assert len(walks) == 1, f"walked /proc {len(walks)} times instead of once"
        assert "http://127.0.0.1:19999/__bili" in first


class TestReasoningPairViolationsAreLoud:
    """A reasoning-pair violation is a TERMINAL event, not a warning.

    Measured 2026-10-17 across the retained log family: all 3 violating
    sessions ended 3, 10 and 29 minutes after their LAST violation and never
    resumed. It used to be invisible — a row field no column read, shown only
    by `--detail`, which you consult once you already suspect trouble.
    """

    def test_column_marks_nonzero(self):
        assert bs._natural({"reason_pair_violations": 13}, "reason_pair_violations") == "*13"
        assert bs._natural({"reason_pair_violations": 1}, "reason_pair_violations") == "*1"

    def test_column_blank_when_zero(self):
        for row in ({}, {"reason_pair_violations": 0}, {"reason_pair_violations": None}):
            assert bs._natural(row, "reason_pair_violations") == ""

    def test_viol_is_a_table_column(self):
        keys = [k for k, *_ in bs._COLUMNS]
        assert "reason_pair_violations" in keys
        # Last column, unsqueezeable: a marker that can be squeezed off a
        # narrow terminal is not a marker.
        assert keys[-1] == "reason_pair_violations"
        assert bs._COLUMNS[-1][4] is False

    def test_callout_is_two_lines_and_dedupes_subsessions(self, capsys):
        rows = [
            {
                "session": "ses_p",
                "reason_pair_violations": 13,
                "model": "glm",
                "last_seen": "2026-10-17T02:54:20Z",
                "_viol_gap": "10 min",
            },
            # a sub-session shares the parent transcript — same violation
            {
                "session": "ses_p|sub:abc",
                "reason_pair_violations": 13,
                "model": "glm",
                "last_seen": "2026-10-17T02:54:20Z",
                "_viol_gap": "10 min",
            },
            {
                "session": "ses_q",
                "reason_pair_violations": 1,
                "model": "mimo",
                "last_seen": "2026-10-14T03:22:58Z",
                "_viol_gap": "29 min",
            },
        ]
        bs.print_violations(rows, None)
        out = capsys.readouterr().out
        assert "3 session(s)" not in out, "sub-session must not be counted twice"
        assert "2 session(s) flagged" in out
        assert "*13 ses_p" in out and "*1 ses_q" in out
        assert "ses_p|sub" not in out, "the sub-session row is collapsed away"
        # The mechanism lives in --detail; the table view only points at it.
        assert "--detail <session>" in out
        assert out.count("\n") == 2, f"callout must stay 2 lines, got:\n{out}"

    def test_callout_carries_no_essay(self, capsys):
        bs.print_violations([{"session": "s", "reason_pair_violations": 3}], None)
        out = capsys.readouterr().out
        for banned in (
            "strict-echo",
            "reasoning_content",
            "tool-call",
            "DeepSeek",
            "zAI",
            "ended",
            "terminal risk —",
        ):
            assert banned not in out, f"the callout must not explain itself ({banned!r})"

    def test_callout_can_go_to_stderr(self, capsys):
        import io

        buf = io.StringIO()
        bs.print_violations([{"session": "s", "reason_pair_violations": 1}], None, stream=buf)
        assert buf.getvalue(), "stream= must receive the callout"
        assert capsys.readouterr().out == "", "nothing may leak to stdout"

    def test_json_path_keeps_stdout_pure(self, capsys, monkeypatch):
        """A human callout in front of the array breaks every `--json` consumer."""
        import json as _json

        rows = [
            {"session": "ses_p", "reason_pair_violations": 13, "model": "glm"},
            {"session": "ses_q", "reason_pair_violations": 0, "model": "mimo"},
        ]
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: [{"id": "ses_p"}, {"id": "ses_q"}])
        monkeypatch.setattr(bs, "run_for_sid", lambda sid: rows[0] if sid == "ses_p" else rows[1])
        assert bs.main(["--json"]) == 0
        cap = capsys.readouterr()
        # stdout parses as JSON with nothing in front of it ...
        assert _json.loads(cap.out)[0]["session"] == "ses_p"
        # ... and the callout went to stderr.
        assert "reasoning-pair violation" in cap.err

    def test_callout_silent_when_clean(self, capsys):
        bs.print_violations([{"session": "s", "reason_pair_violations": 0}], None)
        assert capsys.readouterr().out == ""

    def test_callout_silent_on_empty_rows(self, capsys):
        bs.print_violations([], None)
        assert capsys.readouterr().out == ""

    def test_gap_is_measured_from_the_last_violation(self, tmp_path: Path):
        from helpers.analytics.bili_analytics import Warning

        def _w(ts):
            return Warning(ts=ts, session="s", kind="reasoning-pair-violated", detail="d")

        a = Analysis(log_path="")
        a.warnings = [_w("2026-10-17T04:00:00.000Z"), _w("2026-10-17T04:25:00.000Z")]
        assert "unknown" in bs._viol_gap(a), "no samples at all"
        a.nudges = [_nudge("2026-10-17T04:35:00.000Z")]
        assert bs._viol_gap(a) == "10 min"
        a.nudges.append(_nudge("2026-10-17T06:00:00.000Z"))
        assert bs._viol_gap(a) == "1.6 h"  # 04:25 -> 06:00

    def test_gap_unknown_when_nothing_follows(self):
        from helpers.analytics.bili_analytics import Warning

        a = Analysis(log_path="")
        a.warnings = [
            Warning(
                ts="2026-10-17T04:25:00.000Z",
                session="s",
                kind="reasoning-pair-violated",
                detail="d",
            )
        ]
        assert "unknown" in bs._viol_gap(a)


def _nudge(ts: str):
    from helpers.analytics.bili_analytics import NudgeSample

    return NudgeSample(
        ts=ts,
        session="s",
        usage_tok=1,
        usage_pct=1.0,
        growth=1,
        ref=0,
        interval=200000,
        pending_t1=1,
        pending_thr=200000,
        model="m",
        reason="r",
    )


class TestSinceAcceptsRanges:
    """`--since` shares `helpers.core.ranges` with the harvest `--keep` flag —
    a range that means different things in two retention flags is worse than
    no flag at all."""

    def _run(self, monkeypatch, capsys, argv, rows):
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: [{"id": r["session"]} for r in rows])
        monkeypatch.setattr(
            bs, "run_for_sid", lambda sid: next(r for r in rows if r["session"] == sid)
        )
        return bs.main(argv), capsys.readouterr()

    @pytest.mark.parametrize(
        "flag",
        [
            ["--since", "30d"],
            ["--since", "6mo"],
            ["--since", "1y"],
            ["--since", "12h"],
            ["--all"],
            ["--since", "0"],
        ],
    )
    def test_accepted(self, flag, monkeypatch, capsys):
        rows = [
            {"session": "ses_new", "last_seen": "2026-10-17T00:00:00Z"},
            {"session": "ses_old", "last_seen": "2020-01-01T00:00:00Z"},
        ]
        rc, _ = self._run(monkeypatch, capsys, flag, rows)
        assert rc == 0

    def test_ambiguous_m_is_rejected(self, monkeypatch, capsys):
        rows = [{"session": "ses_new", "last_seen": "2026-10-17T00:00:00Z"}]
        rc, err = self._run(monkeypatch, capsys, ["--since", "6m"], rows)
        assert rc == 2
        assert "ambiguous" in err.err and "6mo" in err.err

    def test_junk_is_rejected(self, monkeypatch, capsys):
        rows = [{"session": "ses_new", "last_seen": "2026-10-17T00:00:00Z"}]
        rc, err = self._run(monkeypatch, capsys, ["--since", "bogus"], rows)
        assert rc == 2
        assert "cannot parse range" in err.err

    def test_longer_range_keeps_more(self, monkeypatch, capsys):
        # older than 1d, inside 1y — the window must widen with the range
        rows = [{"session": "ses_mid", "last_seen": "2026-01-01T00:00:00Z"}]
        _, short = self._run(monkeypatch, capsys, ["--since", "1d"], rows)
        _, long = self._run(monkeypatch, capsys, ["--since", "1y"], rows)
        assert "hidden" in short.err
        assert "hidden" not in long.err


class TestLastSeenWindow:
    @pytest.mark.parametrize(
        "value,parsable",
        [
            ("2026-10-17T04:25:16.517Z", True),
            ("2026-10-17 04:25:16", True),
            ("", False),
            (None, False),
            ("not-a-date", False),
        ],
    )
    def test_parser(self, value, parsable):
        assert (bs._parse_last_seen(value) is not None) is parsable

    def test_undatable_rows_are_kept(self, tmp_path: Path, capsys, monkeypatch):
        """A window is a performance default; it must not hide rows it cannot date."""
        rows = [
            {"session": "ses_old", "last_seen": "2020-01-01T00:00:00Z"},
            {"session": "ses_new", "last_seen": None},
        ]
        monkeypatch.setattr(
            bs, "dashboard_sessions", lambda: [{"id": "ses_old"}, {"id": "ses_new"}]
        )
        monkeypatch.setattr(bs, "run_for_sid", lambda sid: rows[0] if sid == "ses_old" else rows[1])
        bs.main(["--since", "7"])
        out = capsys.readouterr().out
        assert "ses_new" in out, "a row with no parsable lastSeen must still print"
        assert "ses_old" not in out, "a 2020 row is outside a 7-day window"

    def test_all_disables_the_window(self, capsys, monkeypatch):
        rows = [{"session": "ses_old", "last_seen": "2020-01-01T00:00:00Z"}]
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: [{"id": "ses_old"}])
        monkeypatch.setattr(bs, "run_for_sid", lambda sid: rows[0])
        bs.main(["--all"])
        assert "ses_old" in capsys.readouterr().out


class TestContextNowIsNotContextTokens:
    """`ctxNow` read `contextTokens`, which is not the context size.

    Measured 2026-10-19 across 13 live sessions: `contextTokens` matched the
    real prompt size in 1 of 13, off by wildly varying factors elsewhere
    (734,357 vs 201,084; 378,583 vs 142,033; 129,666 vs 389,079). The log's
    last `input_tok` is the ground truth and is always present when a log
    bucket exists, so it wins; `lastInputTokens` is the dashboard tiebreaker.
    """

    DASH = {
        "contextTokens": 734_357,
        "lastInputTokens": 201_084,
        "contextWindow": 868_928,
        "inputTokens": 41_810_744,
        "cachedTokens": 40_746_048,
        "requests": 319,
        "lastModel": None,
        "lastSeen": "2026-10-19T08:11:07.350Z",
    }

    def test_dashboard_only_row_uses_lastInputTokens(self, monkeypatch):
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dict(self.DASH))
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_x": dict(self.DASH)})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s: None)
        row = bs.run_for_sid("ses_x")
        assert row["context_now"] == 201_084, "must not be the 734_357 decoy"
        assert row["dashboard_context_tokens"] == 734_357, "kept, but not rendered"
        assert row["context_now_src"] == "dashboard"
        assert row["context_window"] == 868_928

    def test_log_wins_and_says_so(self, tmp_path, monkeypatch):
        log = tmp_path / "bili.log"
        log.write_text(
            "2026-10-19T04:00:00.000Z [info] [sess=ses_x] [v=0.1.197] [ses_x] "
            "[acp-usage] input=150000 cached=149000 (cache hit 99.0%)\n",
            encoding="utf-8",
        )
        a = parse_logs([log], session_filter="ses_x")
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dict(self.DASH))
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_x": dict(self.DASH)})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s: a)
        monkeypatch.setattr(bs, "file_logs_for_sid", lambda s: [log])
        row = bs.run_for_sid("ses_x")
        assert row["context_now"] == 150_000, "the log is the ground truth"
        assert row["context_now_src"] == "log"

    def test_shrink_pct_is_rounded_at_the_row(self, tmp_path, monkeypatch):
        """An unrounded mean float used to hit the dashboard's `…` truncator
        and get cut mid-digit — the 46.42… oddity (pending.md 2026-10-30).
        Rounding belongs HERE at the row build, not in every renderer."""
        log = tmp_path / "bili.log"
        log.write_text(
            "2026-10-19T04:00:00.000Z [info] [sess=ses_x] [v=0.1.209] [ses_x] "
            "[acp-compress-obs] shrink 46.42696832579186% (~89256/192370 tok) "
            "blocks=10 anchor≈580 tok postCtx≈91000\n",
            encoding="utf-8",
        )
        a = parse_logs([log], session_filter="ses_x")
        assert a.folds and a.folds[0].shrink_pct == pytest.approx(46.42696832579186)
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dict(self.DASH))
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_x": dict(self.DASH)})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s: a)
        monkeypatch.setattr(bs, "file_logs_for_sid", lambda s: [log])
        row = bs.run_for_sid("ses_x")
        assert row["shrink_pct"] == 46.4


class TestDisagreementsAreNamed:
    """The row merges a dashboard snapshot with a log parse and used to prefer
    one silently. Measured 2026-10-19 on ses_eb005c4d7: requests 319 (dash) vs
    432 (log), lastModel None (dash), lastSeen 08:11 vs 13:41."""

    def _a(self, *, n_usages=3, model="glm-5.3-flash"):
        from helpers.analytics.bili_analytics import CacheUsage, NudgeSample

        return Analysis(
            log_path="bili.log",
            usages=[
                CacheUsage(
                    ts=f"2026-10-19T04:00:0{i}.000Z",
                    session="ses_x",
                    input_tok=1000,
                    cached_tok=900,
                    hit_pct=0.9,
                    fold_new=False,
                )
                for i in range(n_usages)
            ],
            nudges=[
                NudgeSample(
                    ts="2026-10-19T04:00:00.000Z",
                    session="ses_x",
                    usage_tok=1000,
                    usage_pct=1.0,
                    growth=1,
                    ref=0,
                    interval=200000,
                    pending_t1=1,
                    pending_thr=200000,
                    model=model,
                    reason="r",
                )
            ]
            if model
            else [],
        )

    def test_a_count_mismatch_is_reported(self):
        out = bs._disagreements(
            {"requests": 319, "lastModel": "glm-5.3-flash"}, self._a(n_usages=432)
        )
        assert len(out) == 1, out
        assert "requests" in out[0] and "319" in out[0] and "432" in out[0]

    def test_identical_counts_agree(self):
        row = {"requests": 3, "lastModel": "glm-5.3-flash"}
        assert bs._disagreements(row, self._a(n_usages=3)) == []

    def test_a_key_the_payload_never_had_is_not_a_disagreement(self):
        """No dashboard claim is held, so there is nothing to contradict."""
        assert bs._disagreements({}, self._a(n_usages=432)) == []

    def test_a_dashboard_absent_is_not_agreement(self):
        out = bs._disagreements(
            {"requests": 3, "lastModel": None}, self._a(n_usages=3, model="glm")
        )
        assert any("lastModel" in d and "absent" in d for d in out), (
            "a None dashboard value is missing evidence, not agreement"
        )

    def test_build_row_attaches_the_list(self, monkeypatch):
        dash = {"requests": 2, "lastModel": "glm-5.3-flash"}
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dash)
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_x": dash})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s: self._a(n_usages=7))
        row = bs.run_for_sid("ses_x")
        assert any("requests" in d for d in row["_disagreements"])


class TestViolationClaimFollowsTheData:
    """The output asserted "every violating session ended within ~30 min" and
    then printed this session's own gap — 3.8 h — as its counterexample."""

    def _a(self, gap_minutes):
        from datetime import datetime, timedelta

        from helpers.analytics.bili_analytics import CacheUsage, Warning

        base = datetime(2026, 10, 19, 4, 0, 0)
        fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
        return Analysis(
            log_path="bili.log",
            usages=[
                CacheUsage(
                    ts=(base + timedelta(minutes=gap_minutes)).strftime(fmt),
                    session="s",
                    input_tok=1,
                    cached_tok=0,
                    hit_pct=0.0,
                    fold_new=False,
                )
            ],
            warnings=[
                Warning(
                    ts=base.strftime(fmt),
                    session="s",
                    kind="reasoning-pair-violated",
                    detail="d",
                )
            ],
        )

    def test_short_gap_is_under_the_terminal_threshold(self):
        v = _viol_gap_minutes(self._a(25))
        assert v is not None and v < 60

    def test_long_gap_is_not(self):
        v = _viol_gap_minutes(self._a(228))
        assert v is not None and v > 60

    def test_a_log_line_before_the_violation_is_not_evidence(self):
        from datetime import datetime

        from helpers.analytics.bili_analytics import CacheUsage, Warning

        fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
        a = Analysis(
            log_path="bili.log",
            usages=[
                CacheUsage(
                    ts=datetime(2026, 10, 19, 3, 0).strftime(fmt),
                    session="s",
                    input_tok=1,
                    cached_tok=0,
                    hit_pct=0.0,
                    fold_new=False,
                )
            ],
            warnings=[
                Warning(
                    ts=datetime(2026, 10, 19, 4, 0).strftime(fmt),
                    session="s",
                    kind="reasoning-pair-violated",
                    detail="d",
                )
            ],
        )
        assert _viol_gap_minutes(a) is None

    def test_no_violation_is_none_not_zero(self):
        assert _viol_gap_minutes(Analysis(log_path="bili.log")) is None


class TestOutcomeLinesFollowTheGap:
    """The detail view asserted "already unwound" two lines below the measured
    gap it contradicted (3.8 h under a "dies within ~30 min" warning)."""

    def test_short_gap_is_terminal(self):
        lines = bs._viol_outcome_lines("25 min")
        assert lines[0].startswith("Terminal: this session stopped within an hour")
        # The old wording asserted death as a fact. The gap measures how far
        # the session got; the log's own clock decides whether it DIED.
        assert "did not survive" not in " ".join(lines)

    def test_live_session_is_not_called_terminal(self):
        """A live session's last line IS the violation, so its gap is tiny —
        but it has not died. Measured 2026-10-19 on
        ses_ec7af063cffew2a8xvEcV142n6, which was mis-called terminal while
        still answering."""
        lines = " ".join(bs._viol_outcome_lines("3 min", live=True))
        assert "STILL RUNNING" in lines
        assert "Terminal" not in lines
        assert "RECOVERED" not in lines

    def test_live_beats_every_gap(self):
        for gap in ("25 min", "4.1 h", "unknown (no log line)", "n/a"):
            assert "STILL RUNNING" in " ".join(bs._viol_outcome_lines(gap, live=True))

    def test_long_gap_is_recovered(self):
        lines = " ".join(bs._viol_outcome_lines("4.1 h"))
        assert "RECOVERED" in lines and "does not hold here" in lines

    def test_unparsable_gap_is_unknown_not_a_guess(self):
        assert "UNKNOWN" in bs._viol_outcome_lines("unknown (no log line)")[0]

    def test_na_gap_is_unknown(self):
        assert "UNKNOWN" in bs._viol_outcome_lines("n/a")[0]

    def test_no_branch_claims_unwound_without_a_measurement(self):
        for gap in ("25 min", "4.1 h", "unknown (no log line)", "n/a"):
            joined = " ".join(bs._viol_outcome_lines(gap))
            assert "already unwound" not in joined


class TestEveryProducerSidFormIsIndexed:
    """`_SID_RE` knew `ses_<base62>` (opencode) and `session-<uuid>` (dsh) but
    NOT the zcode `session_<hex>` form the LIVE sessions use — every zcode
    session was invisible to `sid_log_index()`, and the dashboard-down table
    fallback (`sorted(sid_log_index())`) would have dropped them all."""

    def test_index_maps_all_three_producer_forms(self, tmp_path: Path, monkeypatch):
        ids = [
            "ses_ec487e129ffe8ndwzoNFlTXbpJ",  # opencode: base62, uppercase inside
            "session_36697bd13b3b4fbf445781dceb154794",  # zcode: session_ + hex
            "c5f777d8-d511-4222-ae94-dda4319440bc",  # dsh: bare uuid
        ]
        log = tmp_path / "bili.log"
        log.write_text(
            "\n".join(_usage_line(i, tok=1000, cached=900) for i in ids) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(bs, "file_logs_for_sid", lambda s: [log])
        monkeypatch.setattr(bs, "_SID_INDEX", None)
        idx = bs.sid_log_index()
        missing = [i for i in ids if not idx.get(i)]
        assert missing == [], f"sid forms the index cannot see: {missing}"
