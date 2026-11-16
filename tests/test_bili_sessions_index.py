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
            "ses_aaa": [
                _line("ses_aaa"),
                _violation_line("ses_aaa"),
                _violation_line("ses_aaa"),
            ],
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


class TestReasoningPairViolationsAreQuiet:
    """A reasoning-pair violation used to be LOUD: a `viol` table column plus a
    two-line `!!` banner above the table. The operator removed both from the
    display on 2026-11-16 — the signal stays in `--json`
    (`reason_pair_violations`) and in `--detail`, which also reports the
    measured gap.

    Measured 2026-10-17 across the retained log family: violating sessions
    ended 3, 10 and 29 minutes after their LAST violation and never resumed —
    the field stays worth computing, just not worth a permanent row of screen
    real estate.
    """

    def test_viol_and_switches_are_not_table_columns(self):
        keys = [k for k, *_ in bs._COLUMNS]
        assert "reason_pair_violations" not in keys
        assert "switches" not in keys

    def test_row_keys_survive_for_json(self):
        # The display contract changed, not the data contract: run_for_sid
        # still emits both keys for --json consumers.
        import inspect

        src = inspect.getsource(bs.run_for_sid)
        assert '"reason_pair_violations"' in src
        assert '"switches"' in src

    def test_table_prints_no_banner_even_when_flagged(self, capsys):
        rows = [
            {
                "session": "ses_p",
                "reason_pair_violations": 13,
                "model": "glm",
                "last_seen": "2026-10-17T02:54:20Z",
                "_viol_gap": "10 min",
            }
        ]
        bs.print_table(rows)
        out = capsys.readouterr().out
        assert "reasoning-pair violation" not in out
        assert "!!" not in out
        assert "--detail <session>" not in out

    def test_json_path_keeps_stdout_pure_and_stderr_silent(self, capsys, monkeypatch):
        """`--json` is machine-readable: nothing in front of the array on
        stdout, and (since the banner removal) nothing on stderr either."""
        import json as _json

        rows = [
            {"session": "ses_p", "reason_pair_violations": 13, "model": "glm"},
            {"session": "ses_q", "reason_pair_violations": 0, "model": "mimo"},
        ]
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: [{"id": "ses_p"}, {"id": "ses_q"}])
        monkeypatch.setattr(
            bs, "run_for_sid", lambda sid, only=None: rows[0] if sid == "ses_p" else rows[1]
        )
        assert bs.main(["--json"]) == 0
        cap = capsys.readouterr()
        # stdout parses as JSON with nothing in front of it ...
        assert _json.loads(cap.out)[0]["session"] == "ses_p"
        # ... and stderr carries no violation callout.
        assert "reasoning-pair violation" not in cap.err

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
        # /sessions carries lastSeen: the --since window runs on it BEFORE the
        # detail fan-out, so the mock must expose the same field the live
        # dashboard does (2026-11-02 filter-first change).
        monkeypatch.setattr(
            bs,
            "dashboard_sessions",
            lambda: [{"id": r["session"], "lastSeen": r.get("last_seen")} for r in rows],
        )
        monkeypatch.setattr(
            bs, "run_for_sid", lambda sid, only=None: next(r for r in rows if r["session"] == sid)
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
        # Dashboard path: the window runs on /sessions meta lastSeen; a sid
        # with no parsable meta lastSeen is KEPT (undateable, never hidden).
        monkeypatch.setattr(
            bs,
            "dashboard_sessions",
            lambda: [
                {"id": "ses_old", "lastSeen": "2020-01-01T00:00:00Z"},
                {"id": "ses_new", "lastSeen": None},
            ],
        )
        monkeypatch.setattr(
            bs, "run_for_sid", lambda sid, only=None: rows[0] if sid == "ses_old" else rows[1]
        )
        bs.main(["--since", "7"])
        out = capsys.readouterr().out
        assert "ses_new" in out, "a row with no parsable lastSeen must still print"
        assert "ses_old" not in out, "a 2020 row is outside a 7-day window"

    def test_fallback_window_filters_rows_without_meta(self, tmp_path: Path, capsys, monkeypatch):
        """Dashboard down: the window falls back to the built row's last_seen."""
        rows = [
            {"session": "ses_old", "last_seen": "2020-01-01T00:00:00Z"},
            {"session": "ses_new", "last_seen": "2099-01-01T00:00:00Z"},
        ]
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: [])  # dashboard down
        monkeypatch.setattr(bs, "sid_log_index", lambda want=None: {"ses_old": [], "ses_new": []})
        monkeypatch.setattr(
            bs, "run_for_sid", lambda sid, only=None: next(r for r in rows if r["session"] == sid)
        )
        rc = bs.main(["--since", "7"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ses_new" in out and "ses_old" not in out

    def test_all_disables_the_window(self, capsys, monkeypatch):
        rows = [{"session": "ses_old", "last_seen": "2020-01-01T00:00:00Z"}]
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: [{"id": "ses_old"}])
        monkeypatch.setattr(bs, "run_for_sid", lambda sid, only=None: rows[0])
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
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: None)
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
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: a)
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
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: a)
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
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: self._a(n_usages=7))
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
        monkeypatch.setattr(bs, "_SID_INDEX", {})
        idx = bs.sid_log_index()
        missing = [i for i in ids if not idx.get(i)]
        assert missing == [], f"sid forms the index cannot see: {missing}"


def _fold_line(
    sid: str, *, saved: int, shrink: int = 50, ts: str = "2026-10-17T04:25:18.000Z"
) -> str:
    return (
        f"{ts} [info] [v=0.1.209] [{sid}] [plugin] [acp-compress-obs] "
        f"shrink {shrink}% this-fold (~{saved}/100000 tok) · cumulative {shrink}% "
        f"(~{saved}/100000 tok) foldPoint=m00001 blocks=2 anchor≈1000 tok "
        f"(5 active blocks, sys excluded) postCtx≈50000"
    )


class TestRotatorArchiveDirectoryIsInTheFamily:
    """billion-context rotates into `bili.log.archive/<stamp>/bili.log`, a
    DIRECTORY, not a `.old` sibling file. `log_rotation_family` globbed
    `bili.log*` and kept only `is_file()` matches, so the whole archive was
    invisible: measured 2026-11-15 a 10.7 MB archive holding 33 folds, 15
    absorbs and 28 T1 fires never reached any reader."""

    def test_archive_directory_member_joins_the_family(self, tmp_path: Path):
        live = tmp_path / "bili.log"
        live.write_text(_line("ses_aaa") + "\n", encoding="utf-8")
        arch = tmp_path / "bili.log.archive" / "20261114T054102619-2382534-1"
        arch.mkdir(parents=True)
        (arch / "bili.log").write_text(_line("ses_bbb") + "\n", encoding="utf-8")

        fam = log_rotation_family(live)
        assert set(fam) == {arch / "bili.log", live}, "the archive directory must join the family"
        assert len(parse_logs(fam).nudges) == 2

    def test_both_generations_are_read(self, tmp_path: Path):
        """A session split across the live log and the archive must report the
        UNION of its samples, not whichever file happened to be larger."""
        live = tmp_path / "bili.log"
        live.write_text(_fold_line("ses_aaa", saved=111) + "\n", encoding="utf-8")
        arch = tmp_path / "bili.log.archive" / "20261114T054102619-2382534-1"
        arch.mkdir(parents=True)
        (arch / "bili.log").write_text(
            _fold_line("ses_aaa", saved=222, ts="2026-10-16T01:00:00.000Z") + "\n",
            encoding="utf-8",
        )

        a = parse_logs(log_rotation_family(live), session_filter="ses_aaa")
        assert len(a.folds) == 2, "both files' folds must survive"
        assert sum(f.saved_tok for f in a.folds) == 333

    def test_session_union_beats_a_single_member(self, tmp_path: Path, monkeypatch):
        """`run_for_sid` used to `max(family_len, st_size)` one file out of the
        set that named the session — silently dropping the other's history."""
        live = tmp_path / "bili.log"
        live.write_text(_fold_line("ses_aaa", saved=111) + "\n", encoding="utf-8")
        arch = tmp_path / "bili.log.archive" / "stamp"
        arch.mkdir(parents=True)
        (arch / "bili.log").write_text(
            _fold_line("ses_aaa", saved=222, ts="2026-10-16T01:00:00.000Z") + "\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(bs, "dashboard_detail", lambda s: {"requests": 1})
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_aaa": {"requests": 1}})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(
            bs, "sid_log_index", lambda want=None: {"ses_aaa": [arch / "bili.log", live]}
        )
        row = bs.run_for_sid("ses_aaa")
        assert row["folds"] == 2
        assert row["saved"] == 333


class TestSavedFollowsThePageLedger:
    """`saved` once preferred the log's per-fold sum over the dashboard's own
    counter (2026-11-15 ruling) so cross-session tallies compared one unit.
    That made the table disagree with the dashboard PAGE, whose saved cell is
    `netSaved ?? tokensSaved` (the ledger identity: netSaved = grossSaved −
    repayCost − summaryCost, with grossSaved = Σ saved-per-fold × requests
    AFTER the fold — ses_e464ba95 2026-11-16: 4,263,196,220 − 2,140,144 −
    99,273 = 4,260,956,803 vs a raw fold sum of 1.55M). The operator called
    the fold-sum figure "still wrong" (2026-11-16), so `saved` now follows the
    page rule and the raw fold sum lives under `saved_folds`.
    """

    def test_net_saved_wins_and_both_other_figures_are_kept(self, tmp_path: Path, monkeypatch):
        dash = {"netSaved": 4_260_956_803, "tokensSaved": 125_343}
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dash)
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_aaa": dash})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "sid_log_index", lambda want=None: {})
        log = tmp_path / "bili.log"
        log.write_text(_fold_line("ses_aaa", saved=111) + "\n", encoding="utf-8")
        monkeypatch.setattr(bs, "LIVE_LOG", log)
        monkeypatch.setattr(bs, "file_logs_for_sid", lambda s: [log])

        row = bs.run_for_sid("ses_aaa")
        assert row["saved"] == 4_260_956_803, "the page rule: netSaved ?? tokensSaved"
        assert row["saved_dashboard"] == 125_343, "the dashboard counter stays reachable"
        assert row["saved_folds"] == 111, "the raw fold sum keeps its own key"

    def test_tokens_saved_when_the_ledger_has_no_net(self, monkeypatch):
        dash = {"tokensSaved": 555}
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dash)
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_aaa": dash})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "sid_log_index", lambda want=None: {})
        monkeypatch.setattr(bs, "LIVE_LOG", Path("/nonexistent/bili.log"))
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: None)
        row = bs.run_for_sid("ses_aaa")
        assert row["saved"] == 555, "savedTd's fallback: tokensSaved"

    def test_log_folds_stand_in_when_the_dashboard_has_nothing(self, tmp_path: Path, monkeypatch):
        """No netSaved, no tokensSaved: the raw fold sum stands in rather than
        faking 0 — savedTd's honest-dash rule, log side."""
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: {})
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "sid_log_index", lambda want=None: {})
        log = tmp_path / "bili.log"
        log.write_text(_fold_line("ses_aaa", saved=222) + "\n", encoding="utf-8")
        monkeypatch.setattr(bs, "LIVE_LOG", log)
        monkeypatch.setattr(bs, "file_logs_for_sid", lambda s: [log])

        row = bs.run_for_sid("ses_aaa")
        assert row["saved"] == 222
        assert row["saved_folds"] == 222

    def test_dashboard_figure_used_when_the_log_has_no_folds(self, monkeypatch):
        dash = {"tokensSaved": 555}
        monkeypatch.setattr(bs, "dashboard_detail", lambda s: dash)
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {"ses_aaa": dash})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "sid_log_index", lambda want=None: {})
        monkeypatch.setattr(bs, "LIVE_LOG", Path("/nonexistent/bili.log"))
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: None)
        row = bs.run_for_sid("ses_aaa")
        assert row["saved"] == 555, "no log evidence: fall back rather than zero"


class TestStartupFastPath:
    """m00827: `--since 1d` took ~5s because every row fanned out to a 6.3 MB
    /detail payload and the sid index findall'd the full 30 MB corpus. The
    bulk /sessions list already carries every ledger field, `--top N` trims
    the row set, and the parse is restricted to the kept sids."""

    def test_top_trims_to_the_n_newest_sessions(self, capsys, monkeypatch):
        seen: list[str] = []
        meta = [
            {"id": f"ses_{i}", "lastSeen": f"2026-11-1{6 - i}T00:00:00Z"} for i in range(4)
        ]  # ses_0 newest (2026-11-16) … ses_3 oldest (2026-11-13)
        monkeypatch.setattr(bs, "dashboard_sessions", lambda: meta)
        monkeypatch.setattr(
            bs,
            "run_for_sid",
            lambda sid, only=None: seen.append(sid) or {"session": sid, "last_seen": "2026-11-16"},
        )
        bs.main(["--top", "2"])
        out = capsys.readouterr().out
        assert seen == ["ses_0", "ses_1"], "only the 2 newest sessions are built"
        assert "ses_2" not in out and "ses_3" not in out

    def test_row_comes_from_the_list_map_without_detail(self, monkeypatch):
        """The fast path must not touch /detail at all: ledger fields flow
        from the bulk list map (dashboard_sessions_map)."""
        dash = {
            "netSaved": 4_260_956_803,
            "tokensSaved": 125_343,
            "grossSaved": 4_263_196_220,
            "costUsd": {"total": 7.47},
            "savedUsd": 49.34,
            "savedUsdGross": 42.63,
            "payback": {"spentUsd": 0.22, "breakevenTurns": 1365.35, "actualTurns": 285294},
        }

        def _no_detail(s):
            raise AssertionError("the fast path must not fetch /detail")

        monkeypatch.setattr(bs, "dashboard_detail", _no_detail)
        monkeypatch.setattr(bs, "dashboard_sessions_map", lambda: {"ses_aaa": dash})
        monkeypatch.setattr(bs, "dashboard_stats_map", lambda: {})
        monkeypatch.setattr(bs, "find_report", lambda s: None)
        monkeypatch.setattr(bs, "sid_log_index", lambda want=None: {})
        monkeypatch.setattr(bs, "LIVE_LOG", Path("/nonexistent/bili.log"))
        monkeypatch.setattr(bs, "_session_analysis", lambda p, s, only=None: None)

        row = bs.run_for_sid("ses_aaa")
        assert row["saved"] == 4_260_956_803
        assert row["cost_usd"] == 7.47
        assert row["saved_usd"] == 49.34
        assert row["payback_spent_usd"] == 0.22
        assert row["payback_breakeven"] == 1365.35

    def test_parse_filter_drops_other_sessions_lines(self, tmp_path):
        """parse_logs(session_filter=frozenset) is what keeps the 10 MB live
        log cheap under --top/--since: other sessions' lines die at the
        prefix match and never become samples."""
        from helpers.analytics.bili_analytics import parse_logs

        log = tmp_path / "bili.log"
        log.write_text(
            "2026-11-16T09:00:00.000Z [info] [sess=ses_keep] [v=0.1.215] [ses_keep] "
            "[acp-usage] input=150000 cached=149000 (cache hit 99.0%)\n"
            "2026-11-16T09:01:00.000Z [info] [sess=ses_drop] [v=0.1.215] [ses_drop] "
            "[acp-usage] input=999999 cached=900000 (cache hit 90.0%)\n",
            encoding="utf-8",
        )
        a = parse_logs([log], session_filter=frozenset({"ses_keep"}))
        assert [u.session for u in a.usages] == ["ses_keep"], "the other session is gone"
        assert a.usages[0].input_tok == 150_000
        # a str filter still means EXACT session identity, not a substring
        a2 = parse_logs([log], session_filter="ses_keep")
        assert [u.session for u in a2.usages] == ["ses_keep"]
