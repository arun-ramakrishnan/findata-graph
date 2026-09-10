#!/usr/bin/env python3
"""Bundle O3 / snapshot_trust_country_exposure S2: DuckDB version-drill tests.

The read-only CHECKPOINT in ``create_duckdb_snapshot`` assumes DuckDB
>= 1.5 lets a reader connection flush the WAL (graph_design.txt §9.3).
These tests make that assumption observable from the DEFAULT pytest
suite: the full create -> verify -> restore cycle over a real
materialised cache lives in test_integration_snapshot_cycle.py, which is
``integration``-marked and opt-in — it never runs in ``make qa``, so a
pin bump that breaks the assumption would degrade silently.

- happy-path drill: snapshot a hand-built mini graph cache, verify
  round-trip parity, and assert the ``duckdb_version`` stamp matches the
  running library.
- fallback drill: force the read-only open to fail; the verbatim
  main+WAL copy must still be produced (``checkpointed: False``) and the
  artifact must still verify.
- drift guard: a snapshot stamped with a foreign ``duckdb_version``
  reports ``version_match: False`` and logs the §9.3 warning.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from helpers.graph.query import EDGE_REGISTRY  # noqa: E402
from helpers.maintenance.snapshot_db import (  # noqa: E402
    create_duckdb_snapshot,
    verify_duckdb_snapshot,
)

_log = logging.getLogger("test_version_drill")


def _build_mini_cache(path: Path, version_stamp: str | None = None) -> None:
    """Hand-built minimal graph cache: v_node + every EDGE_REGISTRY table
    (empty except one row in the first) + a stamped _build_meta. Columns
    beyond src/dst are unnecessary — verify counts rows and FK-joins
    src/dst against v_node.id, nothing else."""
    con = duckdb.connect(str(path))
    try:
        con.execute('CREATE TABLE v_node(id BIGINT, "name" VARCHAR)')
        con.execute("INSERT INTO v_node VALUES (1, 'a'), (2, 'b')")
        for spec in EDGE_REGISTRY.values():
            con.execute(
                f"CREATE TABLE {spec['table']}({spec['src']} BIGINT, {spec['dst']} BIGINT)"  # noqa: S608  # table/column names from the in-repo registry constant
            )
        first = next(iter(EDGE_REGISTRY.values()))
        con.execute(f"INSERT INTO {first['table']} VALUES (1, 2)")  # noqa: S608
        con.execute("CREATE TABLE _build_meta(key VARCHAR, value VARCHAR)")
        con.execute(
            "INSERT INTO _build_meta VALUES ('duckdb_version', ?), ('generation', '42')",
            [version_stamp or duckdb.__version__],
        )
    finally:
        con.close()


def test_roundtrip_drill(tmp_path: Path) -> None:
    """Happy path: reader CHECKPOINT works, artifact verifies, stamp matches."""
    ddb = tmp_path / "graph.duckdb"
    _build_mini_cache(ddb)
    out = tmp_path / "graph.duckdb.zst"
    info = create_duckdb_snapshot(ddb, out, _log)
    assert info["checkpointed"] is True
    result = verify_duckdb_snapshot(out, ddb, _log)
    assert result["match"] is True
    assert result["tables"]["v_node"] == 2
    assert result["duckdb_version"] == duckdb.__version__
    assert result["version_match"] is True


def test_fallback_drill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reader open fails -> verbatim copy (checkpointed: False) that still
    verifies. This is the §9.3 degrade path, exercised, not assumed."""
    ddb = tmp_path / "graph.duckdb"
    _build_mini_cache(ddb)
    real_connect = duckdb.connect

    def boom(*args, **kwargs):
        if kwargs.get("read_only"):
            raise duckdb.Error("reader CHECKPOINT rejected (simulated pin bump)")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", boom)
    out = tmp_path / "graph.duckdb.zst"
    info = create_duckdb_snapshot(ddb, out, _log)
    assert info["checkpointed"] is False

    monkeypatch.undo()
    result = verify_duckdb_snapshot(out, ddb, _log)
    assert result["match"] is True
    assert result["version_match"] is True


def test_version_drift_guard(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A snapshot built on a foreign version verifies but flags the drift."""
    ddb = tmp_path / "graph.duckdb"
    _build_mini_cache(ddb, version_stamp="0.0.0-testforge")
    out = tmp_path / "graph.duckdb.zst"
    create_duckdb_snapshot(ddb, out, _log)
    with caplog.at_level(logging.WARNING, logger="helpers.maintenance.snapshot_db"):
        result = verify_duckdb_snapshot(out, ddb, _log)
    assert result["version_match"] is False
    assert result["duckdb_version"] == "0.0.0-testforge"
    assert result["match"] is True  # drift is observable, not fatal
    assert any("version drift" in r.getMessage().lower() for r in caplog.records)
