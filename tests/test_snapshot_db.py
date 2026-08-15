"""Unit tests for helpers/maintenance/snapshot_db.py."""
from __future__ import annotations
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance.snapshot_db import (  # noqa: E402
    _compute_root,
    _list_sqlite_tables,
    _list_duckdb_tables,
    create_snapshot,
    verify_snapshot,
    export_parquet_sqlite,
    export_parquet_duckdb,
    restore_sqlite_from_parquet,
    restore_duckdb_from_parquet,
    main as snapshot_main,
)


_log = logging.getLogger("test_snapshot")


# ---------------------------------------------------------------------------
# _compute_root
# ---------------------------------------------------------------------------
def test_compute_root_is_path():
    root = _compute_root()
    assert isinstance(root, Path)
    assert root.name == "pdf-ocr-obsidian"


# ---------------------------------------------------------------------------
# _list_sqlite_tables
# ---------------------------------------------------------------------------
def test_list_sqlite_tables_basic():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.execute("CREATE TABLE graph_edges (source TEXT)")
    tables = _list_sqlite_tables(conn)
    assert "entities" in tables
    assert "graph_edges" in tables
    conn.close()


def test_list_sqlite_tables_excludes_sqlite_internal():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE foo (x INTEGER)")
    tables = _list_sqlite_tables(conn)
    assert "foo" in tables
    assert not any(t.startswith("sqlite_") for t in tables)
    conn.close()


def test_list_sqlite_tables_sorted():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE zebra (x)")
    conn.execute("CREATE TABLE apple (x)")
    conn.execute("CREATE TABLE mango (x)")
    tables = _list_sqlite_tables(conn)
    assert tables == sorted(tables)
    conn.close()


def test_list_sqlite_tables_excludes_fts():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.execute("CREATE VIRTUAL TABLE note_search USING FTS5(content)")
    tables = _list_sqlite_tables(conn)
    assert "entities" in tables
    assert "note_search" not in tables
    conn.close()


# ---------------------------------------------------------------------------
# _list_duckdb_tables
# ---------------------------------------------------------------------------
def test_list_duckdb_tables():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE a (x INTEGER)")
    con.execute("CREATE TABLE b (y TEXT)")
    tables = _list_duckdb_tables(con)
    assert "a" in tables
    assert "b" in tables
    assert tables == sorted(tables)
    con.close()


def test_list_duckdb_tables_empty():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    assert _list_duckdb_tables(con) == []
    con.close()


# ---------------------------------------------------------------------------
# create_snapshot / verify_snapshot — round-trip with temp DB
# verify_snapshot requires entities + relations tables
# ---------------------------------------------------------------------------
def _make_test_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE relations (source TEXT, target TEXT, edge_type TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Test Co')")
    conn.execute("INSERT INTO relations VALUES ('A', 'B', 'acquired')")
    conn.commit()
    conn.close()


def test_snapshot_roundtrip(tmp_path):
    src_db = tmp_path / "src.db"
    _make_test_db(src_db)

    snap_path = tmp_path / "snapshot.db.gz"
    info = create_snapshot(src_db, snap_path, _log)
    assert snap_path.exists()
    assert info["compressed_bytes"] > 0

    result = verify_snapshot(snap_path, src_db, _log)
    assert result["match"] is True


def test_snapshot_detects_modification(tmp_path):
    src_db = tmp_path / "src.db"
    _make_test_db(src_db)

    snap_path = tmp_path / "snapshot.db.gz"
    create_snapshot(src_db, snap_path, _log)

    # Modify the source AFTER snapshot
    conn = sqlite3.connect(str(src_db))
    conn.execute("INSERT INTO entities VALUES ('Another Co')")
    conn.commit()
    conn.close()

    result = verify_snapshot(snap_path, src_db, _log)
    assert result["match"] is False


def test_snapshot_no_source_db(tmp_path):
    src_db = tmp_path / "src.db"
    _make_test_db(src_db)

    snap_path = tmp_path / "snapshot.db.gz"
    create_snapshot(src_db, snap_path, _log)

    # Verify without source — just checks integrity
    result = verify_snapshot(snap_path, None, _log)
    assert result["integrity"] == "ok"


# ---------------------------------------------------------------------------
# Parquet restore: schema DDL + data + FTS5 rebuild round-trip
# ---------------------------------------------------------------------------
def _make_fts_db(path):
    """entities + regular FTS5 table (content shadow) + a NULL-heavy table."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Alpha', 'revenue growth')")
    conn.execute("INSERT INTO entities VALUES ('Beta', NULL)")
    conn.execute(
        "CREATE VIRTUAL TABLE note_search USING FTS5("
        "name, content, tokenize='porter unicode61')"
    )
    conn.execute("INSERT INTO note_search(name, content) VALUES ('Alpha', 'revenue growth')")
    conn.execute("INSERT INTO note_search(name, content) VALUES ('Beta', 'cost cuts')")
    conn.commit()
    conn.close()


def test_list_sqlite_tables_keeps_fts_content_shadow():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.execute("CREATE VIRTUAL TABLE note_search USING FTS5(content)")
    conn.execute("INSERT INTO note_search(content) VALUES ('hello')")
    tables = _list_sqlite_tables(conn)
    # content shadow IS data (needed to rebuild the index); derived shadows are not
    assert "note_search_content" in tables
    assert "note_search" not in tables
    assert "note_search_data" not in tables
    assert "note_search_idx" not in tables
    conn.close()


def test_restore_sqlite_roundtrip_with_fts(tmp_path):
    src_db = tmp_path / "src.db"
    _make_fts_db(src_db)

    pq_base = tmp_path / "snapshots" / "parquet"
    export_parquet_sqlite(src_db, pq_base / "sqlite", _log)
    assert (pq_base / "_schema.sqlite.sql").exists()
    # content shadow exported; derived shadows not
    files = {p.name for p in (pq_base / "sqlite").glob("*.parquet")}
    assert "note_search_content.parquet" in files
    assert not any("note_search_data" in f for f in files)

    target = tmp_path / "restored.db"
    info = restore_sqlite_from_parquet(pq_base / "sqlite", target, _log)
    assert target.exists()
    assert info["tables"]["entities"] == 2
    assert info["tables"]["note_search_content"] == 2

    conn = sqlite3.connect(str(target))
    # data + NULL fidelity
    rows = dict(conn.execute("SELECT name, note FROM entities"))
    assert rows == {"Alpha": "revenue growth", "Beta": None}
    # FTS index rebuilt from the content shadow
    n = conn.execute(
        "SELECT COUNT(*) FROM note_search WHERE note_search MATCH 'revenue'"
    ).fetchone()[0]
    assert n == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_restore_sqlite_missing_schema_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        restore_sqlite_from_parquet(tmp_path / "nope", tmp_path / "t.db", _log)


def test_restore_duckdb_roundtrip(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    src = tmp_path / "src.duckdb"
    con = duckdb.connect(str(src))
    con.execute("CREATE TABLE v_node (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE e_belongs (node1 INTEGER, node2 INTEGER)")
    con.execute("INSERT INTO v_node VALUES (1, 'A'), (2, NULL)")
    con.execute("INSERT INTO e_belongs VALUES (1, 2)")
    con.close()

    pq_base = tmp_path / "snapshots" / "parquet"
    export_parquet_duckdb(src, pq_base / "duckdb", _log)
    assert (pq_base / "_schema.duckdb.sql").exists()

    target = tmp_path / "restored.duckdb"
    info = restore_duckdb_from_parquet(pq_base / "duckdb", target, _log)
    assert info["tables"]["v_node"] == 2
    assert info["tables"]["e_belongs"] == 1

    con = duckdb.connect(str(target), read_only=True)
    names = con.execute("SELECT name FROM v_node WHERE id=2").fetchone()
    assert names == (None,)
    assert con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0] == 2
    con.close()


def test_main_restore_refuses_existing_target_without_force(
    tmp_path, monkeypatch
):
    # an existing "live" DB + no --force -> refusal before anything runs
    live = tmp_path / "live.db"
    live.write_bytes(b"sentinel")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "snapshot_db.py", "--restore",
            "--db", str(live),
            "--parquet-dir", str(tmp_path / "pq"),
        ],
    )
    assert snapshot_main() == 1
    assert live.read_bytes() == b"sentinel"  # untouched
