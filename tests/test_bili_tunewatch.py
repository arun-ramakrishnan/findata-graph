"""Tier-ladder coverage for the bili watcher.

`bili_tunewatch.py` is the instrument the repo uses to read the proxy log. Its
injection regex used to know only T1 and the ESC (tier-unknown) path, but the
log family does contain a real `INJECT T2` line (2026-10-18T04:40:23) -- so a
T2 injection was silently dropped, invisible to `tunewatch`, `bili_monitor.py`
and every watcher built on them. These tests pin that the tier is captured and
reported, whatever the tier config turns out to be.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from helpers.analytics import bili_tunewatch  # noqa: E402

SID = "ses_test0000ffeTSTTESTTESTTESTTESTTSTEST"

HDR = "2026-10-19T12:16:16.000Z [info] [sess={sid}] [v=0.1.201] [{sid}] [plugin] "


def _nudge(kind: str, reason: str) -> str:
    return (
        HDR.format(sid=SID) + f"nudge INJECT {kind}: usage=75% (126335/168000), growth=13506/45000 "
        f"(ref=112829, interval=100000), pendingT1=14375/100000 "
        f'model=deepseek-v4.1-flash-free, reason="{reason}"\n'
    )


def _write(tmp_path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, errors="replace")
    return str(p)


def _scan(tmp_path, body: str) -> dict:
    """Run scan() over a synthetic log family (LOG_OLD empty)."""
    log = _write(tmp_path, "bili.log", body)
    old = _write(tmp_path, "bili.log.old", "")
    orig = (bili_tunewatch.LOG, bili_tunewatch.LOG_OLD)
    bili_tunewatch.LOG, bili_tunewatch.LOG_OLD = log, old
    try:
        return bili_tunewatch.scan()
    finally:
        bili_tunewatch.LOG, bili_tunewatch.LOG_OLD = orig


class TestInjectionTierCapture:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("T1", "T1"),
            ("T2", "T2"),
            ("T3", "T3"),
            ("-ESC T?", "ESC"),
        ],
    )
    def test_regex_captures_every_tier(self, kind, expected):
        m = bili_tunewatch.RE_T1.search(_nudge(kind, "some reason"))
        assert m is not None, f"{kind} injection not matched"
        assert ("ESC" if "-ESC" in kind else kind) == expected

    def test_t2_injection_is_not_dropped(self, tmp_path):
        body = _nudge(
            "T2",
            "OVER-LIMIT T2 distill: max pending 41229 (T1 effective 14375, T2 41229)",
        )
        row = _scan(tmp_path, body)[SID]
        assert len(row["t1_fires"]) == 1
        assert row["t1_fires"][0]["kind"] == "T2"
        assert row["tier_fires"]["T2"] == 1
        assert "T1" not in row["tier_fires"]

    def test_tier_fires_breaks_down_by_tier(self, tmp_path):
        body = (
            _nudge("T1", "T1 effective 200000 >= threshold 100000, growth 90000")
            + _nudge("T2", "T2 distill ready: 14 tier-1 blocks >= tier2Trigger 1000")
            + _nudge("T3", "T3 distill ready: 10 tier-2 blocks >= tier3Trigger 2000")
            + _nudge("-ESC T?", "max compressible 1234 < threshold 100000")
        )
        row = _scan(tmp_path, body)[SID]
        assert row["tier_fires"] == {"T1": 1, "T2": 1, "T3": 1, "ESC": 1}

    def test_over_limit_flag_is_tier_agnostic(self, tmp_path):
        body = _nudge("T2", "OVER-LIMIT T2 distill: max pending 41229")
        row = _scan(tmp_path, body)[SID]
        assert row["t1_fires"][0]["over_limit"] is True


class TestReportSurface:
    def test_report_prints_the_tier_ladder_line(self, tmp_path, capsys):
        body = _nudge("T2", "T2 distill ready: 14 tier-1 blocks >= tier2Trigger 1000")
        log = _write(tmp_path, "bili.log", body)
        old = _write(tmp_path, "bili.log.old", "")
        orig = (bili_tunewatch.LOG, bili_tunewatch.LOG_OLD)
        bili_tunewatch.LOG, bili_tunewatch.LOG_OLD = log, old
        try:
            bili_tunewatch.cmd_report([SID])
        finally:
            bili_tunewatch.LOG, bili_tunewatch.LOG_OLD = orig
        out = capsys.readouterr().out
        assert "REAL INJECTIONS (tier ladder)" in out
        assert "T2=1" in out
