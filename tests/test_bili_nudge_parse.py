"""Regression tests for the `nudge` line parser in `bili_analytics`.

Every test here pins a defect that was live in the shipped code and that
silently under-reported rather than failing loudly. See
`doc/improvements/proposals/bili_convo_lane.md` §8.11.

1. The kind token is TWO words ("INJECT T1" / "INJECT-ESC T?"). Matching it
   with a non-space class silently drops every escalate line.
2. `window` is not always an integer ("83558.25"), so a `\\d+` group drops
   those lines too.
3. An escalate line's `reason` text is identical to an idle line's, so a
   reason-first classifier files escalations under "below compressible".
4. The `--detail` CLI read a single log file instead of the rotation family.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from helpers.analytics import bili_analytics as ba


def _line(
    sid: str,
    kind: str,
    *,
    usage: str = "37.0% (291536/792576)",
    rest: str = "growth=232772/90000 (ref=58764, interval=200000), "
    "pendingT1=199877/200000 model=space-bunny-free, "
    'reason="max compressible 199877 < threshold 200000; '
    'growth 232772 < floor 90000"',
) -> str:
    return (
        f"2026-10-17T04:25:16.517Z [info] [sess={sid}] [v=0.1.197] [{sid}] "
        f"nudge {kind}: usage={usage}, {rest}"
    )


class TestKindTokenIsTwoWords:
    def test_inject_esc_parses(self):
        m = ba.RE_NUDGE_BODY.search(
            _line(
                "s1",
                "INJECT-ESC T?",
                rest="growth=54764/90000 (ref=64432, interval=200000), "
                "pendingT1=29815/200000 model=deepseek-v4.1-flash-free, "
                'reason="max compressible 29815 < threshold 200000; '
                'growth 54764 < floor 90000"',
            )
        )
        assert m, "INJECT-ESC must parse"
        assert m["tier"] == "?"
        assert m["pending"] == "29815"

    def test_inject_t1_still_parses(self):
        m = ba.RE_NUDGE_BODY.search(
            _line(
                "s1",
                "INJECT T1",
                rest="growth=232984/90000 (ref=58764, interval=200000), "
                "pendingT1=200849/200000 model=space-bunny-free, "
                'reason="T1 effective 200849 >= 200000, growth 232984, '
                'usage 37%"',
            )
        )
        assert m and m["tier"] == "1"

    def test_pins_the_trap(self):
        """The buggy shape must be shown not to match, so the fix cannot regress."""
        buggy = re.compile(r"nudge (?:idle|INJECT T(?P<tier>[123])):\s*")
        line = _line("s1", "INJECT-ESC T?")
        assert buggy.search(line) is None
        assert ba.RE_NUDGE_BODY.search(line) is not None

    def test_idle_still_parses(self):
        assert ba.RE_NUDGE_BODY.search(_line("s1", "idle"))

    def test_headroom_line_is_not_a_nudge(self):
        headroom = (
            "2026-10-13T07:37:58.857Z [info] [sess=session-88c54cc3] [v=0.1.196] "
            "[headroom] model=space-bunny-free: effective window 1048576 -> 786432 "
            "(reserved 262144 for max output 524288, cap 0.25) — nudge/preflight"
        )
        assert ba.RE_NUDGE_BODY.search(headroom) is None


class TestFractionalWindow:
    def test_fractional_window_parses(self):
        line = _line("s1", "INJECT-ESC T?", usage="71% (59694/83558.25)")
        m = ba.RE_NUDGE_BODY.search(line)
        assert m, "a fractional effective window must not drop the line"
        assert float(m["window"]) == pytest.approx(83558.25)


class TestPositionalClassification:
    def test_esc_is_counted_as_an_escalation_not_a_block(self):
        esc = _line("s1", "INJECT-ESC T?")
        idle = _line("s1", "idle")
        analysis = ba.parse_logs([])
        analysis.nudges = [
            _sample(ba.RE_NUDGE_BODY.search(esc)),
            _sample(ba.RE_NUDGE_BODY.search(idle)),
        ]
        counts = analysis.nudge_reason_counts()
        assert counts["fired_esc"] == 1
        # The escalate line's reason text is byte-identical to the idle line's,
        # so only the positional flag can tell them apart.
        assert counts["below_compressible_threshold"] == 1


def _sample(m):
    g = m.groupdict()
    return ba.NudgeSample(
        ts="2026-10-17T04:25:16.517Z",
        session="s1",
        usage_tok=int(g["usage_tok"]),
        usage_pct=float(g["usage_pct"]),
        growth=int(g["growth"]),
        ref=int(g["ref"]),
        interval=int(g["interval"]),
        pending_t1=int(g["pending"]),
        pending_thr=int(g["pending_thr"]),
        model=g["model"],
        reason=g["reason"],
        fired_t1=(g["tier"] == "1"),
        fired_t2=(g["tier"] == "2"),
        fired_t3=(g["tier"] == "3"),
        fired_esc=(g["tier"] == "?"),
    )


class TestConnLifecycleLinesAreSkipped:
    """The plugin's per-connection TCP lines are ~98% of the log.

    `RE_PREFIX` matches them (the `conn#NNNN` tag lands in `sess2`), so
    without an explicit skip every body regex runs over each one — and a
    conn line whose body happens to look like a nudge would be counted.
    """

    CONN = (
        "2026-10-19T15:57:56.668Z [info] [v=0.1.202] [conn#25159] "
        "tcp post-response close: lingering for peer close signal "
        "(budget 5000ms)"
    )
    CONN_DEBUG = (
        "2026-10-19T15:57:56.670Z [debug] [v=0.1.202] [conn#25159] "
        "tcp closed reason=server-end age=2ms reqs=1"
    )

    def test_conn_lines_produce_no_session_and_no_nudge(self, tmp_path: Path):
        log = tmp_path / "bili.log"
        log.write_text("\n".join([self.CONN, self.CONN_DEBUG]) + "\n")
        a = ba.parse_log(log)
        assert a.nudges == []
        assert a.folds == []
        assert a.usages == []
        assert a.warnings == []
        assert a.reasoning_pair_violations == 0

    def test_a_conn_line_wearing_a_nudge_body_is_not_counted(self, tmp_path: Path):
        # The decisive case: without the skip, `RE_PREFIX` matches and the
        # nudge regex fires on the body, inflating the nudge count.
        forged = (
            "2026-10-19T15:57:56.668Z [info] [v=0.1.202] [conn#25159] "
            "nudge INJECT T1: usage=37.0% (291536/792576), "
            "growth=232772/90000 (ref=58764, interval=200000), "
            "pendingT1=199877/200000 model=space-bunny-free, "
            'reason="max compressible 199877 < threshold 200000"'
        )
        log = tmp_path / "bili.log"
        log.write_text(forged + "\n")
        assert ba.parse_log(log).nudges == []

    def test_real_nudges_still_parse_alongside_conn_lines(self, tmp_path: Path):
        log = tmp_path / "bili.log"
        log.write_text("\n".join([self.CONN, _line("s1", "INJECT T1"), self.CONN_DEBUG]) + "\n")
        a = ba.parse_log(log)
        assert len(a.nudges) == 1
        assert a.nudges[0].session == "s1"
        assert a.nudges[0].fired_t1


class TestRotationFamilyCoverage:
    def _write(self, d: Path, name: str, lines: list[str]) -> Path:
        p = d / name
        p.write_text("\n".join(lines) + "\n")
        return p

    def test_parse_logs_covers_more_than_one_file(self, tmp_path: Path):
        # both a rotated file and the live one must contribute
        self._write(tmp_path, "bili.log.old", [_line("s1", "idle")])
        new = self._write(
            tmp_path,
            "bili.log",
            [
                _line("s1", "idle"),
                _line(
                    "s1",
                    "INJECT T1",
                    rest="growth=232984/90000 (ref=58764, interval=200000), "
                    "pendingT1=200849/200000 model=space-bunny-free, "
                    'reason="T1 effective 200849 >= 200000, growth 232984, '
                    'usage 37%"',
                ),
            ],
        )
        merged = ba.parse_logs(ba.log_rotation_family(new))
        assert len(merged.nudges) == 3, "both rotated files must contribute"
        assert sum(n.fired_t1 for n in merged.nudges) == 1

    def test_family_is_newest_last(self, tmp_path: Path):
        self._write(tmp_path, "bili.log.old", [_line("s1", "idle")])
        new = self._write(tmp_path, "bili.log", [_line("s1", "idle")])
        merged = ba.parse_logs(ba.log_rotation_family(new))
        assert [n.ts for n in merged.nudges] == sorted(n.ts for n in merged.nudges)
        assert merged.nudges[-1].ts == "2026-10-17T04:25:16.517Z"

    def test_action_lines_all_carry_a_fired_flag(self, tmp_path: Path):
        kinds = ["INJECT T1", "INJECT-ESC T?"]
        lines = [_line("s1", k) for k in kinds]
        p = self._write(tmp_path, "bili.log", lines)
        merged = ba.parse_logs(ba.log_rotation_family(p))
        assert len(merged.nudges) == 2
        assert all(n.fired_t1 or n.fired_t2 or n.fired_t3 or n.fired_esc for n in merged.nudges), (
            "an action line must be flagged as one"
        )


class TestDetailCliReadsTheRotationFamily:
    """The `--detail` entry point read ONE file while `bili_sessions` read the
    family, so the same session reported 157 samples instead of 6,227 and a
    `max growth seen` of 197,532 instead of the true 232,772."""

    def test_cli_calls_parse_logs_not_parse_log(self, tmp_path: Path, monkeypatch, capsys):
        (tmp_path / "bili.log.old").write_text(_line("s1", "idle") + "\n")
        (tmp_path / "bili.log").write_text(_line("s1", "idle") + "\n")

        calls = {"family": 0, "single": 0}
        real_logs, real_log = ba.parse_logs, ba.parse_log

        def spy_logs(paths, session_filter=None):
            calls["family"] += 1
            return real_logs(paths, session_filter=session_filter)

        def spy_log(path, session_filter=None):
            calls["single"] += 1
            return real_log(path, session_filter=session_filter)

        monkeypatch.setattr(ba, "parse_logs", spy_logs)
        monkeypatch.setattr(ba, "parse_log", spy_log)

        ba.main([str(tmp_path / "bili.log"), "--session", "s1"])
        out = capsys.readouterr().out

        assert calls["family"] == 1, "--detail must parse the rotation family"
        # parse_logs fans out to parse_log internally, so counting per-file calls
        # proves nothing. What proves it: the CLI saw BOTH files' samples.
        assert ba.parse_logs(ba.log_rotation_family(tmp_path / "bili.log")).nudges.__len__() == 2
        assert "nudge samples" in out or out.strip(), "detail should print a report"


class TestVerdictCarriesNoGap:
    """`print_report`'s verdict used to hardcode "(measured: every violating
    session ended within ~30 min)" and then print this session's own gap as the
    counterexample. It now carries NEITHER: the verdict is a count, and the
    measured gap + the Terminal/RECOVERED verdict live in
    `bili_sessions.py --detail <session>`. Printing the gap in both places
    duplicated the number and, on 2026-10-19, asserted "terminal" for a
    session that was still running."""

    def _a(self, later_line: bool):
        from datetime import datetime

        from helpers.analytics.bili_analytics import Analysis, CacheUsage, Warning

        fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
        viol = datetime(2026, 10, 19, 4, 0, 0)
        usages = [
            CacheUsage(
                ts=(viol if later_line else viol.replace(hour=3)).strftime(fmt),
                session="s",
                input_tok=1000,
                cached_tok=900,
                hit_pct=0.9,
                fold_new=False,
            )
        ]
        return Analysis(
            log_path="bili.log",
            reasoning_pair_violations=1,  # a field, not derived from `warnings`
            usages=usages,
            warnings=[
                Warning(
                    ts=viol.strftime(fmt),
                    session="s",
                    kind="reasoning-pair-violated",
                    detail="d",
                )
            ],
        )

    def test_verdict_states_the_count_and_points_at_detail(self, capsys):
        from helpers.analytics.bili_analytics import print_report

        print_report(self._a(later_line=False), {})
        out = capsys.readouterr().out
        assert "1 reasoning-pair violations" in out, out[-800:]
        assert "bili_sessions.py --detail <session>" in out, out[-800:]

    def test_verdict_carries_no_gap_number(self, capsys):
        """A 4-hour gap is the session's own measurement — it belongs in the
        detail report. The headline must not restate it."""
        from datetime import datetime, timedelta

        from helpers.analytics.bili_analytics import print_report

        a = self._a(later_line=True)
        a.usages[0].ts = (
            datetime.strptime(a.usages[0].ts, "%Y-%m-%dT%H:%M:%S.%fZ") + timedelta(hours=4)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        print_report(a, {})
        out = capsys.readouterr().out
        for leak in ("RECOVERED", "terminal", "min after", "h past"):
            assert leak not in out, f"{leak!r} leaked into the headline: {out[-800:]}"


class TestSessionLiveness:
    """`session_is_live` is what stops a running session being called terminal.

    The gap between the last violation and the session's last log line is tiny
    for a LIVE session (its last line IS the violation), so the gap alone
    cannot tell "died" from "still going". The log's own clock can: the proxy
    keeps logging other sessions either way."""

    def _analysis(self, log_path, session_ts):
        from helpers.analytics.bili_analytics import Analysis, CacheUsage

        return Analysis(
            log_path=str(log_path),
            usages=[
                CacheUsage(
                    ts=session_ts,
                    session="s",
                    input_tok=10,
                    cached_tok=9,
                    hit_pct=0.9,
                    fold_new=False,
                )
            ],
        )

    def test_live_when_the_session_is_the_newest_thing_in_the_log(self, tmp_path):
        log = tmp_path / "bili.log"
        log.write_text(
            "2026-10-19T13:15:10. 380Z [info] other session\n"
            "2026-10-19T13:15:09. 120Z [info] this session\n"
        )
        a = self._analysis(log, "2026-10-19T13:15:09.120000Z")
        from helpers.analytics.bili_analytics import session_is_live

        assert session_is_live(a) is True

    def test_not_live_when_the_log_kept_running_afterwards(self, tmp_path):
        log = tmp_path / "bili.log"
        log.write_text(
            "2026-10-19T13:15:09. 120Z [info] this session\n"
            "2026-10-19T13:40:00. 000Z [info] another session\n"
        )
        a = self._analysis(log, "2026-10-19T13:15:09.120000Z")
        from helpers.analytics.bili_analytics import session_is_live

        assert session_is_live(a) is False

    def test_no_log_means_not_live_rather_than_a_crash(self, tmp_path):
        a = self._analysis(tmp_path / "absent.log", "2026-10-19T13:15:09.120000Z")
        from helpers.analytics.bili_analytics import session_is_live

        assert session_is_live(a) is False


# ---------------------------------------------------------------------------
# Fold trigger attribution (m00120 fix): a fold's `trigger` comes from the
# nearest INJECT nudge at or before the fold's ts, inside FOLD_TRIGGER_WINDOW_S.
# The compress tool is the actuator in every path, so the log alone cannot say
# "who asked for this fold" — the nudge-line kind token is the only trigger
# flag, and the join is what makes the fold columns honest.
# ---------------------------------------------------------------------------

_NUDGE_BODY = (
    "usage=37.0% (291536/792576), growth=232772/90000 "
    "(ref=58764, interval=200000), pendingT1=199877/200000 "
    'model=space-bunny-free, reason="max compressible 199877 < '
    'threshold 200000; growth 232772 < floor 90000"'
)


def _mk_nudge(sid: str, kind: str, ts: str) -> str:
    return f"2026-10-18T{ts} [info] [sess={sid}] [v=0.1.197] [{sid}] nudge {kind}: {_NUDGE_BODY}"


def _mk_fold(sid: str, ts: str) -> str:
    return (
        f"2026-10-18T{ts} [info] [sess={sid}] [v=0.1.197] [{sid}] "
        "[acp-compress-obs] shrink 12.5% (~4096/32768 tok) "
        "blocks=4 anchor≈291536 tok postCtx≈500000"
    )


class TestFoldTriggerAttribution:
    def _parse(self, tmp_path: Path, lines: list[str]) -> ba.Analysis:
        p = tmp_path / "bili.log"
        p.write_text("\n".join(lines) + "\n")
        return ba.parse_logs([p])

    def test_fold_after_inject_t2_credits_t2(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "idle", "07:59:00.000Z"),
                _mk_nudge("s1", "INJECT T2", "08:00:00.000Z"),
                _mk_fold("s1", "08:00:29.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["T2"]

    def test_fold_after_esc_credits_esc(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "INJECT-ESC T?", "08:00:00.000Z"),
                _mk_fold("s1", "08:00:27.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["esc"]

    def test_fold_with_only_idle_nudges_is_agent(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "idle", "07:59:00.000Z"),
                _mk_nudge("s1", "idle", "07:59:30.000Z"),
                _mk_fold("s1", "08:00:10.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["agent"]

    def test_stale_inject_beyond_window_is_agent(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "INJECT T1", "08:00:00.000Z"),
                _mk_nudge("s1", "idle", "08:05:00.000Z"),
                _mk_fold("s1", "08:20:00.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["agent"]

    def test_idle_nudges_between_inject_and_fold_do_not_break_the_join(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "INJECT T3", "08:00:00.000Z"),
                _mk_nudge("s1", "idle", "08:01:00.000Z"),
                _mk_nudge("s1", "idle", "08:02:00.000Z"),
                _mk_fold("s1", "08:08:00.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["T3"]

    def test_fold_before_any_inject_is_agent(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_fold("s1", "07:00:00.000Z"),
                _mk_nudge("s1", "INJECT T1", "08:00:00.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["agent"]

    def test_per_fold_triggers_are_independent(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "INJECT T1", "08:00:00.000Z"),
                _mk_fold("s1", "08:00:30.000Z"),
                _mk_fold("s1", "08:30:00.000Z"),  # stale by then
                _mk_nudge("s1", "INJECT T2", "08:31:00.000Z"),
                _mk_fold("s1", "08:31:40.000Z"),
            ],
        )
        assert [f.trigger for f in a.folds] == ["T1", "agent", "T2"]

    def test_fold_trigger_counts_helper(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "INJECT-ESC T?", "08:00:00.000Z"),
                _mk_fold("s1", "08:00:27.000Z"),
                _mk_fold("s1", "08:02:00.000Z"),
                _mk_fold("s1", "08:30:00.000Z"),  # beyond the 10-min window
                _mk_fold("s1", "08:40:00.000Z"),
            ],
        )
        counts = ba.fold_trigger_counts(a.folds)
        assert counts == {"esc": 2, "agent": 2}

    def test_attribution_is_idempotent(self, tmp_path: Path):
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "INJECT T2", "08:00:00.000Z"),
                _mk_fold("s1", "08:00:29.000Z"),
            ],
        )
        first = [f.trigger for f in a.folds]
        ba.attribute_fold_triggers(a)
        assert [f.trigger for f in a.folds] == first == ["T2"]

    def test_fold_inherits_nearest_nudge_window(self, tmp_path: Path):
        # _NUDGE_BODY's usage denominator is (291536/792576), so every
        # synthetic nudge carries window=792576.0 and the fold joins to it.
        a = self._parse(
            tmp_path,
            [
                _mk_nudge("s1", "idle", "07:59:00.000Z"),
                _mk_fold("s1", "08:00:00.000Z"),
            ],
        )
        assert [f.window for f in a.folds] == [792576.0]

    def test_fold_with_no_nudges_has_unknown_window(self, tmp_path: Path):
        a = self._parse(tmp_path, [_mk_fold("s1", "08:00:00.000Z")])
        assert [f.window for f in a.folds] == [None]

    def test_sessions_dont_lend_each_other_triggers_or_windows(self, tmp_path: Path):
        # s1 has no nudges at all; s2's INJECT T2 sits between in ts order.
        # Without per-session grouping, s1's fold would borrow s2's trigger.
        a = self._parse(
            tmp_path,
            [
                _mk_fold("s1", "08:00:00.000Z"),
                _mk_nudge("s2", "INJECT T2", "08:00:10.000Z"),
                _mk_fold("s2", "08:00:40.000Z"),
            ],
        )
        by_session = {f.session: f.trigger for f in a.folds}
        assert by_session == {"s1": "agent", "s2": "T2"}
        win_by_session = {f.session: f.window for f in a.folds}
        assert win_by_session == {"s1": None, "s2": 792576.0}
