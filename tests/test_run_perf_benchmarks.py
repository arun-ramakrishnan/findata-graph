"""Perf-report failure attribution (2026-10-24, run-1787 lesson) + skip
visibility (2026-11-24, perf_followon item 4).

A loaded box painted 15 legs red and the report read as a regression
wall; the md Status cell now carries the why — ``FAIL(budget)`` /
``FAIL(rc)`` / ``FAIL(contended)`` / a load fragment — while keeping the
literal ``FAIL`` substring every failure-surface query keys on.

A leg that exits 0 with a ``SKIP:`` line on stdout (the documented
contract for optional modules) used to score as a fast pass —
``l1_kernels`` "passed" at 0.19s against a 240s budget for six runs
while its store was gone. A skip is now SKIPPED: never OK, never FAIL,
counted on its own in the footer, ``warn`` in perf.json, and the run
still exits 0 when nothing actually failed.
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
        ("SKIPPED", False, "⏸ SKIPPED"),
        ("OVER_BUDGET [psi avg10 18.3%]", False, "✗ FAIL(budget, psi avg10 18.3%)"),
        ("FAIL(rc) [lane qa]", False, "✗ FAIL(rc, lane qa)"),
        ("FAIL(rc) [load1 3.41]", False, "✗ FAIL(rc, load1 3.41)"),
    ],
)
def test_md_status_attribution_matrix(status: str, ok: bool, want: str) -> None:
    from tests import run_perf_benchmarks as rpb

    assert rpb._md_status(status, ok) == want


def test_skipped_cell_has_neither_ok_nor_fail_and_carries_skip_marker() -> None:
    """The skip cell must stay out of both failure surfaces (``FAIL``) and
    the pass count (``OK``), while keeping the ``SKIP`` marker that
    gate_query's md-table parser maps to info (gate_query.py:1594)."""
    from tests import run_perf_benchmarks as rpb

    cell = rpb._md_status("SKIPPED", False)
    assert "SKIP" in cell.upper()
    assert "FAIL" not in cell
    assert "OK" not in cell.upper()


@pytest.mark.parametrize(
    ("status", "ok"),
    [
        ("OVER_BUDGET [psi avg10 18.3%]", False),
        ("FAIL(rc)", False),
        ("CONTENDED", False),
    ],
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
    monkeypatch.setattr(rpb, "run_one", lambda *a, **k: run_one_ret)
    results, contended_from, _why = rpb._run_all_benchmarks()
    assert contended_from is None
    return results


def test_leg_loop_tags_pre_leg_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pressure seen BEFORE the leg counts even if it cleared by leg end."""
    results = _leg(monkeypatch, "psi avg10 20.0%", None, (9.9, "OVER_BUDGET", False, "", False))
    assert results[0][2] == "OVER_BUDGET [psi avg10 20.0%]"
    assert results[0][3] is False
    assert results[0][6] is False


def test_leg_loop_tags_post_leg_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _leg(monkeypatch, None, "lane qa", (9.9, "FAIL(rc)", False, "boom", False))
    assert results[0][2] == "FAIL(rc) [lane qa]"


def test_leg_loop_quiet_failure_stays_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean-box failure gets no load tag — attribution must not excuse."""
    results = _leg(monkeypatch, None, None, (9.9, "OVER_BUDGET", False, "", False))
    assert results[0][2] == "OVER_BUDGET"


def test_leg_loop_passing_leg_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    results = _leg(monkeypatch, "psi avg10 20.0%", "psi avg10 20.0%", (0.9, "OK", True, "", False))
    assert results[0][2] == "OK"
    assert results[0][3] is True


def test_leg_loop_skip_is_never_load_tagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A skipped leg did no work — a load fragment must not try to excuse
    it, and the skip flag must survive the leg loop."""
    results = _leg(
        monkeypatch, "psi avg10 20.0%", "lane qa", (0.2, "SKIPPED", False, "SKIP: gone", True)
    )
    assert results[0][2] == "SKIPPED"
    assert results[0][3] is False
    assert results[0][6] is True


# ── run_one skip detection (perf_followon item 4) ───────────────────────


class _FakeProc:
    def __init__(self, out: bytes, err: bytes = b"", rc: int = 0):
        self._out = out
        self._err = err
        self.returncode = rc

    def communicate(self):
        return self._out, self._err


def _run_one_with(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc, budget: float = 60.0):
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "_CURRENT_LOG_DIR", None)
    monkeypatch.setattr(rpb.subprocess, "Popen", lambda *a, **k: proc)
    return rpb.run_one("fake_leg", ["x"], budget)


def test_run_one_detects_stdout_skip_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """exit 0 + a ``SKIP:`` line on stdout ⇒ SKIPPED: not passed, and the
    tail leads with the leg's own skip line so the report section says why."""
    dt, status, ok, tail, skipped = _run_one_with(
        monkeypatch,
        _FakeProc("prep\nSKIP: no live research store — leg skipped (exit 0)\n".encode(), b"", 0),
    )
    assert dt < 60.0
    assert status == "SKIPPED"
    assert ok is False
    assert skipped is True
    assert tail.startswith("SKIP: no live research store")


def test_run_one_plain_ok_stays_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    dt, status, ok, tail, skipped = _run_one_with(monkeypatch, _FakeProc(b"all good\n", b"", 0))
    assert status == "OK"
    assert ok is True
    assert skipped is False
    assert tail == ""


def test_run_one_failure_with_skip_text_is_not_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonzero rc is a failure even when the output mentions SKIP — the
    declaration needs exit 0."""
    dt, status, ok, _tail, skipped = _run_one_with(
        monkeypatch, _FakeProc(b"SKIP: tried\n", b"boom", 1)
    )
    assert status == "FAIL(rc)"
    assert ok is False
    assert skipped is False


def test_run_one_over_budget_skip_is_still_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skip above budget stays SKIPPED (it did no work) — but it must
    never read as OK against its budget."""
    _dt, status, ok, _tail, skipped = _run_one_with(
        monkeypatch, _FakeProc(b"SKIP: nothing to do\n", b"", 0), budget=0.001
    )
    assert status == "SKIPPED"
    assert ok is False
    assert skipped is True


def test_run_one_retries_once_on_store_lock_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 'Conflicting lock is held' rc-failure retries once after a short
    wait (transient holder releases); the retry is re-measured from zero
    and its outcome is the leg's outcome."""
    from tests import run_perf_benchmarks as rpb

    procs = iter(
        [
            _FakeProc(b"", b"IO Error: Conflicting lock is held in python3.14 (PID 1)", 1),
            _FakeProc(b"wrote 3 tags\n", b"", 0),
        ]
    )
    seen: list[_FakeProc] = []
    monkeypatch.setattr(rpb, "_CURRENT_LOG_DIR", None)
    monkeypatch.setattr(rpb.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        rpb.subprocess, "Popen", lambda *a, **k: seen.append(next(procs)) or seen[-1]
    )
    dt, status, ok, tail, skipped = rpb.run_one("sync_tags", ["x"], 30.0)
    assert len(seen) == 2
    assert status == "OK"
    assert ok is True
    assert skipped is False
    assert "wrote 3 tags" not in tail  # stdout is not the tail
    assert dt < 30.0


def test_run_one_lock_conflict_still_failing_stays_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the retry loses the race too, the leg stays FAIL(rc) with the
    lock-conflict tail — a persistent holder is a real failure."""
    from tests import run_perf_benchmarks as rpb

    err = b"IO Error: Conflicting lock is held in python3.14 (PID 1)"
    procs = iter([_FakeProc(b"", err, 1), _FakeProc(b"", err, 1)])
    monkeypatch.setattr(rpb, "_CURRENT_LOG_DIR", None)
    monkeypatch.setattr(rpb.time, "sleep", lambda _s: None)
    monkeypatch.setattr(rpb.subprocess, "Popen", lambda *a, **k: next(procs))
    dt, status, ok, tail, skipped = rpb.run_one("sync_tags", ["x"], 30.0)
    assert status == "FAIL(rc)"
    assert ok is False
    assert skipped is False
    assert "Conflicting lock is held" in tail
    assert dt < 30.0


def test_perf_run_lock_refuses_a_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the exclusive run lock held, main() refuses (rc 3) and points
    at the holder instead of racing the active run."""
    import fcntl
    import os

    from tests import run_perf_benchmarks as rpb

    lock_path = tmp_path / "outputs" / ".perf_run.lock"
    monkeypatch.setattr(rpb, "REPO_ROOT", tmp_path)
    lock_path.parent.mkdir(parents=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(fd, b"pid 1 started 2026-11-25 00:00:00")
    try:
        rc = rpb.main(["--force"])
        assert rc == 3
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # The lock is released on the refusal path (no fd leak into the suite).
    fd2 = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd2, fcntl.LOCK_UN)
    os.close(fd2)


class TestStoreClone:
    """S1: writer legs measure against an ephemeral clone of the live store."""

    def test_prepare_copies_db_and_wal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil as _shutil

        from tests import run_perf_benchmarks as rpb

        src = tmp_path / "research.duckdb"
        src.write_bytes(b"duckdb-bytes")
        wal = tmp_path / "research.duckdb.wal"
        wal.write_bytes(b"wal-bytes")
        import tests._live_store as _ls

        monkeypatch.setattr(_ls, "live_store_path", lambda: src)
        monkeypatch.setattr(rpb, "_STORE_CLONE_DIR", tmp_path / "clone")
        clone = rpb._prepare_store_clone()
        assert clone == tmp_path / "clone" / "research.duckdb"
        assert clone.read_bytes() == b"duckdb-bytes"
        assert clone.with_name(clone.name + ".wal").read_bytes() == b"wal-bytes"
        _shutil.rmtree(tmp_path / "clone")

    def test_prepare_without_wal_leaves_no_stale_wal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tests._live_store as _ls
        from tests import run_perf_benchmarks as rpb

        src = tmp_path / "research.duckdb"
        src.write_bytes(b"duckdb-bytes")
        monkeypatch.setattr(_ls, "live_store_path", lambda: src)
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / "research.duckdb.wal").write_bytes(b"stale")
        monkeypatch.setattr(rpb, "_STORE_CLONE_DIR", clone_dir)
        clone = rpb._prepare_store_clone()
        assert clone is not None
        assert not clone.with_name(clone.name + ".wal").exists()

    def test_prepare_without_store_is_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tests._live_store as _ls
        from tests import run_perf_benchmarks as rpb

        monkeypatch.setattr(_ls, "live_store_path", lambda: None)
        monkeypatch.setattr(rpb, "_STORE_CLONE_DIR", tmp_path / "clone")
        assert rpb._prepare_store_clone() is None

    def test_drop_removes_the_clone_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests import run_perf_benchmarks as rpb

        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / "research.duckdb").write_bytes(b"x")
        monkeypatch.setattr(rpb, "_STORE_CLONE_DIR", clone_dir)
        rpb._drop_store_clone(clone_dir / "research.duckdb")
        assert not clone_dir.exists()

    def test_drop_with_no_clone_is_noop(self, tmp_path: Path) -> None:
        from tests import run_perf_benchmarks as rpb

        rpb._drop_store_clone(None)  # must not raise

    def test_sync_tags_leg_points_at_the_clone_placeholder(self) -> None:
        from tests.run_perf_benchmarks import BENCHMARKS

        legs = {label: args for label, args, _budget in BENCHMARKS}
        assert "--db" in legs["sync_tags"]
        assert "{clone_db}" in legs["sync_tags"]

    def test_expand_clone_substitutes_only_when_present(self, tmp_path: Path) -> None:
        from tests import run_perf_benchmarks as rpb

        clone = tmp_path / "research.duckdb"
        argv = rpb._expand_clone(["--db", "{clone_db}"], clone)
        assert argv == ["--db", str(clone)]
        assert rpb._expand_clone(["--db", "{clone_db}"], None) == ["--db", "{clone_db}"]
        assert rpb._expand_clone(["--plain"], clone) == ["--plain"]


def test_write_report_load_tag_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The md cell parses back with FAIL + the load fragment intact."""
    from tests import run_perf_benchmarks as rpb

    report = tmp_path / "perf_report.md"
    monkeypatch.setattr(rpb, "REPORT", report)
    rpb.write_report(
        [
            ("bench_ok", 1.2, "OK", True, 2.0, "", False),
            ("bench_slow", 9.9, "OVER_BUDGET [psi avg10 18.3%]", False, 2.0, "", False),
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
            ("bench_ok", 1.2, "OK", True, 2.0, "", False),
            (
                "bench_int",
                5.2,
                "FAIL(rc)",
                False,
                10.0,
                "exit 1: validation_rate 91.05",
                False,
            ),
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


def test_write_report_skip_never_passes_and_never_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """perf_followon item 4 acceptance: a leg that skips never appears
    under the passed count; the footer counts it on its own; the run's
    Exit line stays 0 when nothing failed; the skipped leg gets its own
    (SKIPPED) section — visible, but outside gate_query's FAILED greps."""
    from tests import run_perf_benchmarks as rpb

    from helpers.misc.gate_query import _leg_err_head

    report = tmp_path / "perf_report.md"
    monkeypatch.setattr(rpb, "REPORT", report)
    rpb.write_report(
        [
            ("bench_ok", 1.2, "OK", True, 2.0, "", False),
            ("bench_skip", 0.19, "SKIPPED", False, 240.0, "SKIP: no live research store", True),
        ],
        "2026-11-24 21:30:00",
        12.0,
    )
    text = report.read_text()
    lines = text.splitlines()
    assert "| **1/2 passed, 1 skipped** | | | |" in text
    assert "**Exit:** 0" in text
    assert "| bench_skip | 0.19 | 240.0s | ⏸ SKIPPED |" in text
    # skipped legs never ride the FAILED section gate_query greps
    assert _leg_err_head(lines, "bench_skip") is None
    assert "## bench_skip (SKIPPED)" in text
    assert "SKIP: no live research store" in text
    # the TUI table still parses both rows (the footer row is not a step)
    (block,) = parse_perf_report(report)
    assert len(block.steps) == 2
    assert block.steps[1].status == "⏸ SKIPPED"


def test_write_report_skip_plus_failure_still_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip never rescues a failing run: Exit stays 1."""
    from tests import run_perf_benchmarks as rpb

    report = tmp_path / "perf_report.md"
    monkeypatch.setattr(rpb, "REPORT", report)
    rpb.write_report(
        [
            ("bench_skip", 0.2, "SKIPPED", False, 90.0, "SKIP: gone", True),
            ("bench_bad", 9.9, "OVER_BUDGET", False, 3.0, "", False),
        ],
        "2026-11-24 21:30:00",
        12.0,
    )
    text = report.read_text()
    assert "**Exit:** 1" in text
    assert "| **0/2 passed, 1 skipped** | | | |" in text


def test_perf_json_marks_a_skip_warn_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gate_query's perf-json.v1 adapter COERCES unknown statuses to pass —
    so a skipped leg must ride ``warn``, the adapter's non-pass slot, and
    keep SKIPPED as raw_status."""
    from tests import run_perf_benchmarks as rpb

    log_dir = tmp_path / "artifacts"
    log_dir.mkdir()
    monkeypatch.setattr(rpb, "_CURRENT_LOG_DIR", log_dir)
    monkeypatch.setattr(rpb, "_RUN_ID", "t")
    monkeypatch.setattr(rpb.gate_retain, "prune_old_runs", lambda *a, **k: None)
    meta = rpb._retain_run_artifacts(
        [
            ("bench_ok", 1.2, "OK", True, 2.0, "", False),
            ("bench_skip", 0.2, "SKIPPED", False, 240.0, "SKIP: gone", True),
        ],
        "2026-11-24 21:30:00",
        12.0,
    )
    assert meta is not None
    payload = (log_dir / "perf.json").read_text()
    assert '"status": "warn"' in payload
    assert '"raw_status": "SKIPPED"' in payload
    assert '"status": "pass"' in payload


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
    monkeypatch.setattr(rpb, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(
        rpb, "_contention_reason", lambda midrun=False: "load1 9.99 > 4 cores (box saturated)"
    )
    monkeypatch.setattr(
        rpb,
        "_run_all_benchmarks",
        lambda force=False, clone_db=None, repeats=3: (
            [("b", 1.0, "OK", True, 2.0, "", False)],
            None,
            None,
        ),
    )
    monkeypatch.setattr(rpb, "_cleanup_scratch", lambda: None)
    rc = rpb.main(["--force"])
    assert rc == 0
    text = (tmp_path / "perf_report.md").read_text()
    assert "**Status:** FORCED" in text


def test_no_force_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --force a contended box still refuses with exit 3 and
    appends nothing. Default --wait-minutes would poll for real minutes, so
    the pin runs with --wait-minutes 0 (instant refusal) and a second pin
    covers the wait path; the sleep stub keeps either instant."""
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
    monkeypatch.setattr(rpb, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(rpb, "_contention_reason", lambda midrun=False: "load1 9.99")
    monkeypatch.setattr(rpb.time, "sleep", lambda _s: None)
    rc = rpb.main(["--wait-minutes", "0"])
    assert rc == 3
    assert not (tmp_path / "perf_report.md").exists()


def _main_with_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows) -> str:
    from tests import run_perf_benchmarks as rpb

    monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
    monkeypatch.setattr(rpb, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(rpb, "_contention_reason", lambda midrun=False: None)
    monkeypatch.setattr(rpb, "_cleanup_scratch", lambda: None)
    monkeypatch.setattr(rpb, "_retain_run_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(
        rpb,
        "_run_all_benchmarks",
        lambda force=False, clone_db=None, repeats=3: (rows, None, None),
    )
    return "ran"


def test_console_footer_counts_legs_not_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The console footer counts PASSING legs, not non-zero budgets.

    The old `*_, ok, _` unpack bound the fifth field of the 6-tuple —
    `budget`, a truthy float — so a run with over-budget legs still
    printed "N/N passed" while the report said FAILED and the exit code
    was 1 (the forced perf run of 2026-11-24 printed "35/35 passed"
    against five OVER_BUDGET rows).
    """
    from tests import run_perf_benchmarks as rpb

    _main_with_rows(
        tmp_path,
        monkeypatch,
        [
            ("good", 1.0, "OK", True, 2.0, "", False),
            ("slow", 9.0, "OVER_BUDGET", False, 3.0, "", False),
        ],
    )
    rc = rpb.main([])
    assert rc == 1
    assert "1/2 passed" in capsys.readouterr().out


def test_console_footer_counts_skips_separately_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A skip is not a pass and not a failure: the footer splits it out and
    the run exits 0 when nothing actually failed (perf_followon item 4)."""
    from tests import run_perf_benchmarks as rpb

    _main_with_rows(
        tmp_path,
        monkeypatch,
        [
            ("good", 1.0, "OK", True, 2.0, "", False),
            ("skipped", 0.2, "SKIPPED", False, 240.0, "SKIP: gone", True),
        ],
    )
    rc = rpb.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1/2 passed, 1 skipped" in out
    assert "35/35" not in out


class TestQuietWindowWait:
    """S2 (--wait-minutes): a contended start polls for a quiet window while
    holding the run lock; expiry refuses with the last reason."""

    def test_wait_proceeds_when_the_box_goes_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from tests import run_perf_benchmarks as rpb

        monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
        monkeypatch.setattr(rpb, "ARTIFACTS_ROOT", tmp_path / "artifacts")
        monkeypatch.setattr(rpb, "_cleanup_scratch", lambda: None)
        monkeypatch.setattr(rpb, "_retain_run_artifacts", lambda *a, **k: None)
        sleeps: list[float] = []
        monkeypatch.setattr(rpb.time, "sleep", sleeps.append)
        probes = iter(["load1 9.9 > 4 cores (box saturated)", None])
        # next(..., None): _main_locked probes again after the wait clears.
        monkeypatch.setattr(rpb, "_contention_reason", lambda midrun=False: next(probes, None))
        monkeypatch.setattr(
            rpb,
            "_run_all_benchmarks",
            lambda force=False, clone_db=None, repeats=3: ([], None, None),
        )
        rc = rpb.main(["--wait-minutes", "5"])
        assert rc == 0
        assert sleeps, "the wait loop must poll, not proceed immediately"
        err = capsys.readouterr().err
        assert "waiting up to 5m" in err

    def test_wait_expiry_refuses_with_the_last_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from tests import run_perf_benchmarks as rpb

        monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
        monkeypatch.setattr(rpb, "ARTIFACTS_ROOT", tmp_path / "artifacts")
        monkeypatch.setattr(rpb.time, "sleep", lambda _s: None)
        # monotonic: first call sets the deadline (t=0 -> deadline 300), the
        # next loop check reads t=400 — past it, so the loop gives up.
        clock = iter([0.0, 400.0])
        monkeypatch.setattr(rpb.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(
            rpb, "_contention_reason", lambda midrun=False: "load1 9.9 > 4 cores (box saturated)"
        )
        rc = rpb.main(["--wait-minutes", "5"])
        assert rc == 3
        assert "still contended after 5m wait" in capsys.readouterr().err

    def test_wait_zero_is_instant_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--wait-minutes 0 restores the old behavior: one probe, refuse now."""
        from tests import run_perf_benchmarks as rpb

        monkeypatch.setattr(rpb, "REPORT", tmp_path / "perf_report.md")
        monkeypatch.setattr(rpb, "ARTIFACTS_ROOT", tmp_path / "artifacts")
        calls: list[bool] = []
        monkeypatch.setattr(
            rpb,
            "_contention_reason",
            lambda midrun=False: calls.append(midrun) or "load1 9.9",
        )
        rc = rpb.main(["--wait-minutes", "0"])
        assert rc == 3
        assert len(calls) == 1, "wait 0 must not poll"


class TestMedianRepeats:
    """S3 (--repeats): sub-10s legs run N reps, the median wall is judged."""

    @staticmethod
    def _run(monkeypatch: pytest.MonkeyPatch, rets, repeats: int):
        from tests import run_perf_benchmarks as rpb

        calls: list[str] = []

        def fake_run_one(label, args, budget, clone_db=None):
            calls.append(label)
            return rets[min(len(calls) - 1, len(rets) - 1)]

        monkeypatch.setattr(rpb, "run_one", fake_run_one)
        return rpb._run_leg_median("leg", ["x"], 2.0, clone_db=None, repeats=repeats), calls

    def test_median_of_three_is_judged_not_the_first_rep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # rep1 = cold-clone spike (2.5s), reps 2-3 warm (1.5s) -> median 1.5 OK.
        rets = [
            (2.5, "OVER_BUDGET", False, "cold", False),
            (1.5, "OK", True, "warm", False),
            (1.5, "OK", True, "warm", False),
        ]
        (dt, status, ok, tail, skipped), calls = self._run(monkeypatch, rets, repeats=3)
        assert calls == ["leg", "leg#rep2", "leg#rep3"]
        assert (dt, status, ok, skipped) == (1.5, "OK", True, False)
        assert tail == "warm"

    def test_skip_rep_returns_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rets = [(0.2, "SKIPPED", False, "SKIP: gone", True)]
        (dt, status, ok, tail, skipped), calls = self._run(monkeypatch, rets, repeats=3)
        assert calls == ["leg"], "a skip must dominate: no further reps"
        assert skipped is True

    def test_all_reps_fail_rc_returns_the_last_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rets = [(1.0, "FAIL", False, "boom", False)]
        (dt, status, ok, tail, _skipped), calls = self._run(monkeypatch, rets, repeats=3)
        assert len(calls) == 3
        assert status == "FAIL" and ok is False and tail == "boom"

    def test_median_mixes_over_budget_reps_into_the_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # OVER_BUDGET is rc-clean: its wall belongs in the median set, and the
        # recomputed status comes from the median, not from any one rep.
        rets = [
            (2.5, "OVER_BUDGET", False, "slow", False),
            (1.5, "OK", True, "warm", False),
            (1.5, "OK", True, "warm", False),
        ]
        (dt, status, ok, _tail, _skipped), _calls = self._run(monkeypatch, rets, repeats=3)
        assert (dt, status, ok) == (1.5, "OK", True)

    def test_leg_loop_wires_repeats_for_small_budgets_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """budget >= _MEDIAN_BUDGET_MAX legs stay single-shot even at repeats=3."""
        from tests import run_perf_benchmarks as rpb

        seen: list[int] = []

        def fake_run_one(label, args, budget, clone_db=None):
            seen.append(1)
            return (1.0, "OK", True, "", False)

        big = ("big", ["x"], rpb._MEDIAN_BUDGET_MAX)  # at the cap: not medianed
        small = ("small", ["x"], rpb._MEDIAN_BUDGET_MAX - 0.1)
        monkeypatch.setattr(rpb, "BENCHMARKS", [big, small])
        monkeypatch.setattr(rpb, "_contention_reason", lambda midrun=False: None)
        monkeypatch.setattr(rpb, "_load_pressure_reason", lambda: None)
        monkeypatch.setattr(rpb, "run_one", fake_run_one)
        results, _cf, _why = rpb._run_all_benchmarks(repeats=3)
        assert len(seen) == 4  # big: 1 run; small: 3 reps
        assert [r[0] for r in results] == ["big", "small"]


def test_run_one_writes_the_step_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The S0 extraction regressed once: _main_locked assigned the module
    global without `global`, so run_one saw None and no leg log was ever
    written — the artifact dir stayed empty and retention bailed. This pin
    exercises the real run_one against a stubbed subprocess."""
    from tests import run_perf_benchmarks as rpb

    log_dir = tmp_path / "artifacts" / "run"
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(rpb, "_CURRENT_LOG_DIR", log_dir)
    monkeypatch.setattr(rpb.subprocess, "Popen", lambda *a, **k: _FakeProc(b"", b"", 0))
    dt, status, ok, tail, skipped = rpb.run_one("fake_leg", ["x"], 2.0)
    assert (status, ok, skipped) == ("OK", True, False)
    log = log_dir / "perf.fake_leg.log"
    assert log.exists(), "run_one must persist the leg log when the run dir is set"
    assert "fake_leg" in log.read_text(encoding="utf-8")
