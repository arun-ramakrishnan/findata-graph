"""Unit tests for helpers/maintenance/migrate_to_graph_edges.py."""

from __future__ import annotations
import sqlite3
import sys
from pathlib import Path


HELPERS = Path(__file__).resolve().parents[1] / "helpers" / "maintenance"
sys.path.insert(0, str(HELPERS))


def test_view_exists_true():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIEW my_view AS SELECT 1")
    import migrate_to_graph_edges as mte

    assert mte._view_exists(conn, "my_view") is True
    conn.close()


def test_view_exists_false_for_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE my_table (x INTEGER)")
    import migrate_to_graph_edges as mte

    assert mte._view_exists(conn, "my_table") is False
    conn.close()


def test_view_exists_false_when_missing():
    conn = sqlite3.connect(":memory:")
    import migrate_to_graph_edges as mte

    assert mte._view_exists(conn, "nope") is False
    conn.close()


def test_table_exists_true():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x INTEGER)")
    import migrate_to_graph_edges as mte

    assert mte._table_exists(conn, "t") is True
    conn.close()


def test_table_exists_false():
    conn = sqlite3.connect(":memory:")
    import migrate_to_graph_edges as mte

    assert mte._table_exists(conn, "nope") is False
    conn.close()


def test_object_kind_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x INTEGER)")
    import migrate_to_graph_edges as mte

    assert mte._object_kind(conn, "t") == "table"
    conn.close()


def test_object_kind_view():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIEW v AS SELECT 1")
    import migrate_to_graph_edges as mte

    assert mte._object_kind(conn, "v") == "view"
    conn.close()


def test_object_kind_missing():
    conn = sqlite3.connect(":memory:")
    import migrate_to_graph_edges as mte

    assert mte._object_kind(conn, "nope") is None
    conn.close()
