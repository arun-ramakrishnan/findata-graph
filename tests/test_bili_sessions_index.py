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
        assert "2 session(s) at terminal risk" in out
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
