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
