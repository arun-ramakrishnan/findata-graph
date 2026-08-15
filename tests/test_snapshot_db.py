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
