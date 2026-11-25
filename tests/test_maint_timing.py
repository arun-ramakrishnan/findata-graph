"""Unit pins for ``helpers.maintenance.maint_timing``.

Fourteen helper modules import this module and every instrumented CLI
writes its timing row through ``RunTimer.finish`` on exit;
``helpers.misc.maint_query`` reads the rows back. Until the 2026-11-24
coverage sweep it had no test of its own
(doc/improvements/proposals/test_coverage_gap_sweep_nov24.md, S3).

Everything here runs against a scratch ``gate_runs.duckdb`` -- never the
live ``outputs/gate_runs.duckdb``.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from helpers.maintenance import maint_timing as mt
from helpers.misc import maint_query as mq

HELPERS = Path(__file__).resolve().parents[1] / "helpers"

#: The whole importable surface; every importer in helpers/ sticks to it.
PUBLIC = {"RunTimer", "active", "current", "phase"}
#: What those importers actually use (they time phases through RunTimer.phase).
IN_USE = {"RunTimer", "active", "current"}

_RUN_KEYS = {
    "maint_run_id",
    "cmd",
    "mode",
    "target",
    "started_at",
    "elapsed_s",
    "exit_code",
    "summary",
}
_PHASE_KEYS = {"maint_run_id", "phase", "started_at", "ended_at", "extra"}


@pytest.fixture
def scratch_store(tmp_path, monkeypatch) -> Path:
    """Redirect the recorder to a scratch store with no inherited target."""
    path = tmp_path / "gate_runs.duckdb"
    monkeypatch.setattr(mt, "DB_PATH", path)
    monkeypatch.delenv("MAINT_TARGET", raising=False)
    return path


def _rows(path: Path, sql: str) -> list[dict]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def test_run_and_phase_rows_have_the_shape_the_query_layer_reads(scratch_store):
    with mt.RunTimer(cmd="pytest_sim", mode="dry-run", target="t1") as timer:
        with timer.phase("load", extra="x" * 600):
            pass
        with timer.phase("derive"):
            pass

    runs = _rows(scratch_store, "SELECT * FROM maint_runs")
    assert len(runs) == 1
    run = runs[0]
    assert (run["cmd"], run["mode"], run["target"], run["exit_code"], run["summary"]) == (
        "pytest_sim",
        "dry-run",
        "t1",
        0,
        "",
    )
    assert run["started_at"] <= run["ended_at"]
    assert run["elapsed_s"] >= 0.0
    # maint_query addresses every column by name.
    assert _RUN_KEYS <= run.keys()

    phases = _rows(scratch_store, "SELECT * FROM maint_phases ORDER BY started_at, phase")
    assert [p["phase"] for p in phases] == ["load", "derive"]
    assert {p["maint_run_id"] for p in phases} == {run["maint_run_id"]}
    assert phases[0]["extra"] == "x" * 500  # extra is capped at 500 chars
    assert phases[1]["extra"] == ""
    assert all(p["started_at"] <= p["ended_at"] and p["elapsed_s"] >= 0.0 for p in phases)
    assert _PHASE_KEYS <= phases[0].keys()


def test_explicit_elapsed_wins_and_long_fields_are_capped(scratch_store):
    started = datetime(2026, 11, 24, 12, 0, 0)
    ended = started + timedelta(seconds=5)
    with mt.RunTimer(cmd="c", target="t") as timer:
        timer.record_phase("explicit", started, ended, elapsed_s=1.5, extra="e" * 501)
        timer.record_phase("derived", started, ended)

    phases = {p["phase"]: p for p in _rows(scratch_store, "SELECT * FROM maint_phases")}
    assert phases["explicit"]["elapsed_s"] == pytest.approx(1.5)
    assert phases["derived"]["elapsed_s"] == pytest.approx(5.0)
    assert phases["explicit"]["extra"] == "e" * 500

    timer = mt.RunTimer(cmd="c", target="t")
    assert timer.finish(0, "s" * 2500) == 0  # summary is capped at 2000 chars
    (run,) = _rows(scratch_store, "SELECT summary FROM maint_runs ORDER BY 1 DESC LIMIT 1")
    assert run["summary"] == "s" * 2000


def test_run_ids_continue_after_the_largest_existing_one(scratch_store):
    con = duckdb.connect(str(scratch_store))
    try:
        con.execute(mt._SCHEMA)
        con.execute("INSERT INTO maint_runs (maint_run_id, cmd) VALUES (41, 'seed')")
    finally:
        con.close()

    with mt.RunTimer(cmd="a", target="t"):
        pass
    with mt.RunTimer(cmd="b", target="t"):
        pass

    ids = [r["maint_run_id"] for r in _rows(scratch_store, "SELECT maint_run_id FROM maint_runs")]
    assert sorted(ids) == [41, 42, 43]


def test_without_a_target_nothing_is_stored_or_printed(scratch_store, capsys):
    assert mt.RunTimer(cmd="c").finish(3) == 3

    assert not scratch_store.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_step_table_is_printed_to_stderr_with_a_target(scratch_store, capsys):
    t0 = datetime(2026, 1, 1)
    with mt.RunTimer(cmd="c", target="t") as timer:
        timer.record_phase("scan", t0, t0, elapsed_s=1.25)
        timer.record_phase("write", t0, t0, elapsed_s=2.5)

    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert lines[0].startswith("Step") and lines[0].rstrip().endswith("Time (s)")
    assert "scan" in lines[1] and lines[1].rstrip().endswith("1.250")
    assert "write" in lines[2] and lines[2].rstrip().endswith("2.500")
    assert "total" in lines[3] and lines[3].rstrip().endswith("3.750")
    assert len(lines) == 4
    assert scratch_store.exists()


def test_an_exception_records_exit_1_the_summary_and_the_raised_phase(scratch_store):
    with pytest.raises(ValueError, match="boom"):
        with mt.RunTimer(cmd="c", target="t") as timer:
            with timer.phase("raises"):
                raise ValueError("boom")

    run = _rows(scratch_store, "SELECT * FROM maint_runs")[0]
    assert run["exit_code"] == 1
    assert run["summary"] == "ValueError: boom"
    phases = _rows(scratch_store, "SELECT phase FROM maint_phases")
    assert [p["phase"] for p in phases] == ["raises"]


def test_a_failed_write_warns_and_keeps_the_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mt, "DB_PATH", tmp_path / "missing" / "gate_runs.duckdb")

    assert mt.RunTimer(cmd="c", target="t").finish(7) == 7

    assert "[maint_timing] timing record skipped (" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()


def test_active_stack_routes_module_phase_to_the_innermost_timer():
    assert mt.current() is None

    with mt.phase("idle"):
        pass  # no active timer: pass-through, nothing recorded, nothing raised

    outer, inner = mt.RunTimer(cmd="outer", target="t"), mt.RunTimer(cmd="inner", target="t")
    with mt.active(outer):
        with mt.active(inner):
            assert mt.current() is inner
            with mt.phase("deep", extra="e"):
                pass
        assert mt.current() is outer
    assert mt.current() is None
    assert [p["phase"] for p in inner.phases] == ["deep"]
    assert inner.phases[0]["extra"] == "e"
    assert outer.phases == []

    with pytest.raises(RuntimeError, match="x"):
        with mt.active(inner):
            raise RuntimeError("x")
    assert mt.current() is None  # popped even when the body raises


def test_target_precedence_and_cmd_default(monkeypatch):
    monkeypatch.setenv("MAINT_TARGET", "from-env")
    assert mt.RunTimer().target == "from-env"
    assert mt.RunTimer(target="arg").target == "arg"
    monkeypatch.delenv("MAINT_TARGET")
    assert mt.RunTimer().target == ""
    assert mt.RunTimer(cmd="named").cmd == "named"
    assert mt.RunTimer().cmd == Path(sys.argv[0]).stem  # pytest: the pytest executable


def _timing_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.endswith("maint_timing")
        ):
            names.update(alias.name for alias in node.names)
    return names


def test_every_importer_sticks_to_the_public_surface():
    importers: dict[str, set[str]] = {}
    imported: set[str] = set()
    for path in sorted(HELPERS.rglob("*.py")):
        if "maint_timing" not in path.read_text(encoding="utf-8"):  # cheap prefilter
            continue
        names = _timing_imports(ast.parse(path.read_text(encoding="utf-8")))
        if names:
            importers[path.name] = names
            imported |= names

    assert len(importers) >= 10  # the module is widely wired; a lone caller is a regression
    assert imported == IN_USE  # the module-level phase() has no importer (measured 2026-11-24)
    strays = {name: sorted(names - PUBLIC) for name, names in importers.items() if names - PUBLIC}
    assert not strays, strays


def test_maint_query_reads_back_a_recorded_run(scratch_store, monkeypatch):
    monkeypatch.setattr(mq, "DB_PATH", scratch_store)
    with mt.RunTimer(cmd="pytest_sim", mode="dry-run", target="t1") as timer:
        with timer.phase("load", extra="n=12"):
            pass

    con = mq.connect()
    try:
        recent = mq.cmd_recent(con, SimpleNamespace(cmd="pytest_sim", last=5))
        phases = mq.cmd_phases(con, SimpleNamespace(cmd="pytest_sim", last=5))
    finally:
        con.close()

    assert "pytest_sim" in recent
    assert "dry-run [t1]" in recent
    assert "OK" in recent
    assert "load" in phases
    assert "exit 0" in phases
    assert "Step" in phases
