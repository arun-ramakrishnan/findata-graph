"""Parquet slice (parquet_snapshot_duckdb_reader.md S2–S5): the tracked
``snapshots/parquet/sqlite/`` axis keeps its SQLite-schema restore contract
but its DATA source resolves through the S6 selector — canonical DuckDB by
default. These tests pin the export/verify/restore round-trip on a DuckDB
source without touching the live stores.
"""

import logging
import sqlite3
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from helpers.maintenance.snapshot_db import (
    _resolve_research_source,
    export_parquet_sqlite,
    restore_sqlite_from_parquet,
    verify_parquet_snapshot,
)


def _log() -> logging.Logger:
    return logging.getLogger("test_snapshot_parquet_duckdb_source")


def _seed_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE entities (name VARCHAR PRIMARY KEY, entity_type VARCHAR)")
        con.execute("CREATE TABLE graph_edges (id BIGINT, source VARCHAR, target VARCHAR)")
        con.execute("CREATE TABLE db_meta (key VARCHAR, value VARCHAR)")
        con.execute(
            "INSERT INTO entities VALUES ('Reliance','company'),('TCS','company'),('Nifty','index')"
        )
        con.execute("INSERT INTO graph_edges VALUES (1,'Reliance','TCS')")
        con.execute(
            "INSERT INTO db_meta VALUES ('generation','7'),('schema_version','17'),"
            "('apply_head','abc'),('apply_ts','2026-11-21 12:00:00')"
        )
    finally:
        con.close()


def _seed_schema(parquet_base: Path) -> None:
    parquet_base.mkdir(parents=True, exist_ok=True)
    (parquet_base / "_schema.sqlite.sql").write_text(
        "CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT);\n"
        "CREATE TABLE graph_edges (id INTEGER, source TEXT, target TEXT);\n"
        "CREATE TABLE db_meta (key TEXT, value TEXT);\n"
    )


def test_duckdb_source_export_produces_parquet_and_excludes_apply_stamp(tmp_path):
    src = tmp_path / "research.duckdb"
    base = tmp_path / "parquet"
    _seed_duckdb(src)
    _seed_schema(base)

    result = export_parquet_sqlite(src, base / "sqlite", _log())
    assert not result.get("skipped")
    assert {"entities", "graph_edges", "db_meta"} <= set(result["tables"])
    assert pq.read_table(base / "sqlite" / "entities.parquet").num_rows == 3
    meta = pq.read_table(base / "sqlite" / "db_meta.parquet")
    keys = meta.column("key").to_pylist()
    assert "generation" in keys
    assert not any(k.startswith("apply_") for k in keys)


def test_duckdb_source_verify_matches_then_detects_drift(tmp_path):
    src = tmp_path / "research.duckdb"
    base = tmp_path / "parquet"
    _seed_duckdb(src)
    _seed_schema(base)
    export_parquet_sqlite(src, base / "sqlite", _log())

    ok = verify_parquet_snapshot(base, None, src, _log())
    assert ok["match"] is True
    assert ok["tables_checked"] >= 3
    assert ok["mismatches"] == []

    con = duckdb.connect(str(src))
    con.execute("INSERT INTO entities VALUES ('Extra','company')")
    con.close()
    bad = verify_parquet_snapshot(base, None, src, _log())
    assert bad["match"] is False
    assert any("entities" in m for m in bad["mismatches"])


def test_duckdb_source_restore_roundtrip(tmp_path):
    src = tmp_path / "research.duckdb"
    base = tmp_path / "parquet"
    _seed_duckdb(src)
    _seed_schema(base)
    export_parquet_sqlite(src, base / "sqlite", _log())

    target = tmp_path / "restored.db"
    info = restore_sqlite_from_parquet(base / "sqlite", target, _log())
    assert info["tables"]["entities"] == 3
    con = sqlite3.connect(target)
    try:
        assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 1
    finally:
        con.close()


def test_research_source_selector_gate(tmp_path, monkeypatch):
    research_db = tmp_path / "research.db"
    research_duck = tmp_path / "research.duckdb"
    research_db.touch()
    research_duck.touch()
    # The gate recognises this .db as the research store via the override;
    # resolve_path then does the gated .db → .duckdb sibling redirect.
    monkeypatch.setenv("FINDATA_RESEARCH_DB_PATH", str(research_db))

    # Default (duckdb-first): the research-store .db resolves to its
    # converted sibling.
    monkeypatch.delenv("FINDATA_RESEARCH_ENGINE", raising=False)
    assert _resolve_research_source(research_db) == research_duck

    # Explicit rollback keeps the SQLite anchor.
    monkeypatch.setenv("FINDATA_RESEARCH_ENGINE", "sqlite")
    assert _resolve_research_source(research_db) == research_db

    # A tmp fixture that merely shares the name is NOT the research store;
    # its sibling .duckdb (another lane's cache) must never hijack it.
    other_db = tmp_path / "other.db"
    other_duck = tmp_path / "other.duckdb"
    other_db.touch()
    other_duck.touch()
    monkeypatch.delenv("FINDATA_RESEARCH_ENGINE", raising=False)
    assert _resolve_research_source(other_db) == other_db
