"""The proxy status script and the monitor's argument handling.

Two defects this file pins:

1. `bili_status.sh` reported the T1 bar by scraping `interval=` out of the log
   -- but the plugin prints `nudgeGrowthTokens` there, NOT
   `compress.tierNudgeTokens.t1`. The two differ whenever the operator sets
   an explicit T1 bar, so the status report silently showed a bar the proxy
   was not using. The script now reads the CONFIG (the same source the proxy
   reads live, via `bili_tier_nudge.Bars`) and labels the log line as what it
   actually is.

2. `bili_monitor.py --help` died with a bare `ValueError` traceback (its
   positional args go straight into `int()`), so the one thing you would try
   first on a long-running watcher was the one thing that crashed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATUS = os.path.join(_REPO, "helpers", "analytics", "bili_status.sh")
_MONITOR = os.path.join(_REPO, "helpers", "analytics", "bili_monitor.py")

_LOG_LINE = (
    "2026-10-19T15:54:28.773Z [info] [sess=ses_abc0000] [v=0.1.202] "
    "nudge idle: usage=8% (63219/750000), growth=15548/20000 (ref=47671, "
    "interval=30000), pendingT1=17543/30000 model=minimax-m3.1-flash-free, "
    'reason="growth 15548 < floor 20000"\n'
)


def _run_status(tmp_path, config: dict) -> str:
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    log = tmp_path / "bili.log"
    log.write_text(_LOG_LINE, encoding="utf-8")
    env = dict(os.environ)
    env.update(
        BILI_LOG=str(log),
        BILI_CONFIG=str(cfg),
        BILI_WATCH_DIR=str(tmp_path / "watch"),
    )
    proc = subprocess.run(
        ["bash", _STATUS],  # noqa: S607  # repo script path, POSIX bash
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestStatusScriptReadsTheConfigBars:
    def test_explicit_t1_bar_is_reported(self, tmp_path):
        out = _run_status(
            tmp_path,
            {
                "compress": {
                    "nudgeGrowthTokens": 30000,
                    "tierNudgeTokens": {"t1": 40000, "t2": 5000, "t3": 3000},
                }
            },
        )
        assert "bar1 (T1)   : 40000" in out
        assert "bar2 (T2)   : 5000" in out
        assert "bar3 (T3)   : 3000" in out

    def test_absent_t1_falls_back_to_nudge_growth_tokens(self, tmp_path):
        out = _run_status(
            tmp_path,
            {
                "compress": {
                    "nudgeGrowthTokens": 30000,
                    "tierNudgeTokens": {"t2": 38000},
                }
            },
        )
        assert "bar1 (T1)   : 30000" in out
        assert "fallback nudgeGrowthTokens" in out
        assert "bar2 (T2)   : 38000" in out
        # t3 absent -> the plugin's 1.5x fallback, not a hardcoded number.
        assert "bar3 (T3)   : 45000" in out

    def test_growth_floor_is_the_hard_20000(self, tmp_path):
        out = _run_status(tmp_path, {"compress": {"nudgeGrowthTokens": 30000}})
        assert "growth floor: 20000" in out
        assert "hard 20000" in out

    def test_the_log_denominator_is_labelled_as_not_the_t1_bar(self, tmp_path):
        # The log prints `pendingT1=N/30000` where 30000 is nudgeGrowthTokens.
        # With an explicit T1 bar of 40000 the two disagree, so the report
        # must say which line is which.
        out = _run_status(
            tmp_path,
            {
                "compress": {
                    "nudgeGrowthTokens": 30000,
                    "tierNudgeTokens": {"t1": 40000},
                }
            },
        )
        assert "does NOT print" in out
        assert "tierNudgeTokens.t1" in out
        assert "pendingT1=17543/30000" in out


class TestMonitorArgumentHandling:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, _MONITOR, *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_help_exits_zero_without_a_traceback(self):
        proc = self._run("--help")
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr
        assert "bili_monitor.py" in proc.stdout

    def test_a_non_numeric_argument_is_reported_not_raised(self):
        proc = self._run("--bogus")
        assert proc.returncode == 2
        assert "Traceback" not in proc.stderr
        assert "usage: bili_monitor.py" in proc.stderr
