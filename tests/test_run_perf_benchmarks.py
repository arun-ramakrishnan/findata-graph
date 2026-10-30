"""Perf-report failure attribution (2026-10-24, run-1787 lesson).

A loaded box painted 15 legs red and the report read as a regression
wall; the md Status cell now carries the why — ``FAIL(budget)`` /
``FAIL(rc)`` / ``FAIL(contended)`` / a load fragment — while keeping the
literal ``FAIL`` substring every failure-surface query keys on.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from helpers.misc.search_tui import parse_perf_report


@pytest.mark.parametrize(
    ("status", "ok", "want"),
    [
        ("OK", True, "✓ OK"),
        ("OVER_BUDGET", False, "✗ FAIL(budget)"),
        ("FAIL(rc)", False, "✗ FAIL(rc)"),
        ("CONTENDED", False, "✗ FAIL(contended)"),
        ("OVER_BUDGET [psi avg10 18.3%]", False, "✗ FAIL(budget, psi avg10 18.3%)"),
        ("FAIL(rc) [lane qa]", False, "✗ FAIL(rc, lane qa)"),
        ("FAIL(rc) [load1 3.41]", False, "✗ FAIL(rc, load1 3.41)"),
    ],
)
def test_md_status_attribution_matrix(status: str, ok: bool, want: str) -> None:
    from tests import run_perf_benchmarks as rpb

    assert rpb._md_status(status, ok) == want


@pytest.mark.parametrize(
    ("status", "ok"),
    [("OVER_BUDGET [psi avg10 18.3%]", False), ("FAIL(rc)", False), ("CONTENDED", False)],
)
def test_every_failure_cell_keeps_fail_substring(status: str, ok: bool) -> None:
    """gate_query's failure surfaces key on 'FAIL'; TUI pass-count keys on
    'OK' — the attribution must not break either."""
    from tests import run_perf_benchmarks as rpb

    cell = rpb._md_status(status, ok)
    assert "FAIL" in cell
    assert "OK" not in cell.upper()


def test_load_pressure_reason_lane_beats_psi(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: "gate lane qa active (pid 42, started x)")
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": pytest.fail("unreachable"))
    assert rpb._load_pressure_reason() == "lane qa"


def test_load_pressure_reason_psi_ceilings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sustained-external pressure tags; our own bench bursts (~10.3% avg10
    documented max) must NOT — the tag would libel the code."""
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: None)
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": 18.3)
    assert rpb._load_pressure_reason() == "psi avg10 18.3%"
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": 10.3)
    assert rpb._load_pressure_reason() is None


def test_load_pressure_reason_load_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: None)
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": None)
    monkeypatch.setattr(rpb.os, "getloadavg", lambda: (3.41, 2.0, 1.5))
    assert rpb._load_pressure_reason() == "load1 3.41"
    monkeypatch.setattr(rpb.os, "getloadavg", lambda: (1.0, 1.0, 1.0))
    assert rpb._load_pressure_reason() is None


def _leg(monkeypatch: pytest.MonkeyPatch, pre, post, run_one_ret) -> list[tuple]:
    """Run _run_all_benchmarks over one fake leg with stubbed probes."""
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "BENCHMARKS", [("fake_leg", [sys.executable, "-c", "pass"], 1.0)])
    monkeypatch.setattr(rpb, "_contention_reason", lambda midrun=False: None)
    probe = MagicMock(side_effect=[pre, post])
    monkeypatch.setattr(rpb, "_load_pressure_reason", probe)
    monkeypatch.setattr(rpb, "run_one", lambda *a: run_one_ret)
    results, contended_from, _why = rpb._run_all_benchmarks()
    assert contended_from is None
    return results


def test_leg_loop_tags_pre_leg_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pressure seen BEFORE the leg counts even if it cleared by leg end."""
    results = _leg(monkeypatch, "psi avg10 20.0%", None, (9.9, "OVER_BUDGET", False, ""))
    assert results[0][2] == "OVER_BUDGET [psi avg10 20.0%]"
    assert results[0][3] is False


def test_leg_loop_tags_post_leg_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _leg(monkeypatch, None, "lane qa", (9.9, "FAIL(rc)", False, "boom"))
    assert results[0][2] == "FAIL(rc) [lane qa]"


def test_leg_loop_quiet_failure_stays_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean-box failure gets no load tag — attribution must not excuse."""
    results = _leg(monkeypatch, None, None, (9.9, "OVER_BUDGET", False, ""))
    assert results[0][2] == "OVER_BUDGET"


def test_leg_loop_passing_leg_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _leg(monkeypatch, "psi avg10 20.0%", "psi avg10 20.0%", (0.9, "OK", True, ""))
    assert results[0][2] == "OK"
    assert results[0][3] is True


def test_write_report_load_tag_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The md cell parses back with FAIL + the load fragment intact."""
    from tests import run_perf_benchmarks as rpb

    report = tmp_path / "perf_report.md"
    monkeypatch.setattr(rpb, "REPORT", report)
    rpb.write_report(
        [
            ("bench_ok", 1.2, "OK", True, 2.0, ""),
            ("bench_slow", 9.9, "OVER_BUDGET [psi avg10 18.3%]", False, 2.0, ""),
        ],
        "2026-10-24 10:00:00",
        12.0,
    )
    blocks = parse_perf_report(report)
    assert len(blocks) == 1
    ok_step, slow_step = blocks[0].steps
    assert ok_step.status == "✓ OK"
    assert "FAIL" in slow_step.status
    assert "psi avg10 18.3%" in slow_step.status


def test_write_report_failure_sections_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A red leg emits ``## <label> (FAILED)`` + stderr tail; gate_query's
    _leg_err_head — the err_head source for the legs table — reads it back.
    (Run 1869 lesson: integrity_check FAIL(rc) stored err_head NULL because
    perf reports had no sections to grep.)"""
    from tests import run_perf_benchmarks as rpb

    from helpers.misc.gate_query import _leg_err_head

    report = tmp_path / "perf_report.md"
    monkeypatch.setattr(rpb, "REPORT", report)
    rpb.write_report(
        [
            ("bench_ok", 1.2, "OK", True, 2.0, ""),
            ("bench_int", 5.2, "FAIL(rc)", False, 10.0, "exit 1: validation_rate 91.05"),
        ],
        "2026-10-26 10:00:00",
        12.0,
    )
    lines = report.read_text().splitlines()
    head = _leg_err_head(lines, "bench_int")
    assert head is not None
    assert "status: FAIL(rc)" in head
    assert "validation_rate 91.05" in head
    # passing legs get no section
    assert _leg_err_head(lines, "bench_ok") is None
    # and the table parser still sees exactly two steps
    (block,) = parse_perf_report(report)
    assert len(block.steps) == 2


# ── refusal policy (2026-10-31 operator directive): load1 vs cores, ─────
# ── PSI demoted to loadavg-less fallback + attribution; --force escape ──


def test_contention_reason_runs_at_or_below_core_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unless load > num_cpus the run proceeds — even with PSI screaming.
    Light desktop load (the run-2066 all-contended wall) must never wall
    perf off again."""
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: None)
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": 18.3)
    monkeypatch.setattr(rpb.os, "getloadavg", lambda: (rpb._LOAD_RUN_CEIL, 3.0, 2.0))
    assert rpb._contention_reason() is None
    assert rpb._contention_reason(midrun=True) is None


def test_contention_reason_refuses_above_core_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: None)
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": 0.0)
    monkeypatch.setattr(rpb.os, "getloadavg", lambda: (rpb._LOAD_RUN_CEIL + 0.01, 3.0, 2.0))
    reason = rpb._contention_reason()
    assert reason is not None
    assert "load1" in reason


def test_contention_reason_lane_still_blocks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: "gate lane qa active (pid 42)")
    monkeypatch.setattr(rpb.os, "getloadavg", lambda: (0.1, 0.1, 0.1))
    assert rpb._contention_reason() is not None


def test_contention_reason_psi_only_host_keeps_legacy_ceilings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No loadavg -> the calibrated PSI ceilings still guard (burst at
    start, sustained mid-run)."""
    from tests import run_perf_benchmarks as rpb

    def _no_loadavg() -> tuple[float, float, float]:
        raise OSError("no loadavg on this host")

    monkeypatch.setattr(rpb, "_gate_lane_active", lambda: None)
    monkeypatch.setattr(rpb.os, "getloadavg", _no_loadavg)
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": {"avg10": 11.0, "avg60": 3.0}[w])
    assert "avg10" in (rpb._contention_reason() or "")
    assert rpb._contention_reason(midrun=True) is None  # sustained 3% < 15%
    monkeypatch.setattr(rpb, "_psi_some", lambda w="avg10": {"avg10": 11.0, "avg60": 20.0}[w])
    assert "avg60" in (rpb._contention_reason(midrun=True) or "")


def test_force_runs_through_start_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--force: a refused-box run proceeds, the appended block is labelled
    FORCED, and the exit code reflects leg results only."""
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
    monkeypatch.setattr(
        rpb, "_contention_reason", lambda midrun=False: "load1 9.99 > 4 cores (box saturated)"
    )
    monkeypatch.setattr(
        rpb,
        "_run_all_benchmarks",
        lambda force=False: ([("b", 1.0, "OK", True, 2.0, "")], None, None),
    )
    monkeypatch.setattr(rpb, "_cleanup_scratch", lambda: None)
    rc = rpb.main(["--force"])
    assert rc == 0
    text = (tmp_path / "perf_report.md").read_text()
    assert "**Status:** FORCED" in text


def test_no_force_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --force a contended box still refuses with exit 3 and
    appends nothing."""
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
    monkeypatch.setattr(rpb, "_contention_reason", lambda midrun=False: "load1 9.99")
    rc = rpb.main([])
    assert rc == 3
    assert not (tmp_path / "perf_report.md").exists()
