"""Unit tests for maintenance utility helpers.
"""
from __future__ import annotations

import sqlite3

from helpers.maintenance.rename_entity import _normalize_name
from helpers.maintenance.move_sector import move_entity, normalize_sector_tag_value


def test_rename_normalize_name():
    assert _normalize_name("Reliance Industries Ltd") == "Reliance_Industries_Ltd"
    assert _normalize_name("TCS Limited") == "TCS_Limited"


def test_normalize_sector_tag_value():
    assert normalize_sector_tag_value("Information Technology") == "information technology"
    assert normalize_sector_tag_value("BANKING") == "banking"


# ---------------------------------------------------------------------------
# Slice D — transactional hardening for move_entity
# ---------------------------------------------------------------------------
def _make_move_db(tmp_path):
    """Create a test DB + markdown file for move_entity tests.

    'Test Co' lives in the canonical sector 'Healthcare'; 'Technology' is the
    destination sector. graph_edges carries the part_of / has_company pair so a
    move must rewrite those rows inside the transaction.
    """
    db_path = tmp_path / "move.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT,
            created_at TEXT,
            file_path TEXT,
            last_updated TEXT,
            normalized_name TEXT,
            sector_classification TEXT,
            ticker TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE graph_edges (
            source TEXT, target TEXT, edge_type TEXT, source_ref TEXT,
            FOREIGN KEY (source) REFERENCES entities(name) ON UPDATE CASCADE,
            FOREIGN KEY (target) REFERENCES entities(name) ON UPDATE CASCADE,
            UNIQUE(source, target, edge_type)
        )
    """)
    companies = tmp_path / "findata" / "Companies"
    (companies / "Healthcare").mkdir(parents=True)
    note = """---
title: Test Co
type: company
normalized_name: Test_Co
sector: Healthcare
file_path: findata/Companies/Healthcare/Test_Co.md
permalink: companies/healthcare/test_co
tags:
  - sector/healthcare
last_modified: '2025-01-01'
---

# Test Co

Body.
"""
    (companies / "Healthcare" / "Test_Co.md").write_text(note)
    conn.execute(
        "INSERT INTO entities VALUES ('Test Co','company','2025-01-01',"
        "'findata/Companies/Healthcare/Test_Co.md','2025-01-01','Test_Co','Healthcare',NULL)"
    )
    conn.execute(
        "INSERT INTO entities VALUES ('Healthcare','sector','2025-01-01',"
        "'findata/Sectors/Healthcare.md','2025-01-01','Healthcare',NULL,NULL)"
    )
    conn.execute(
        "INSERT INTO entities VALUES ('Technology','sector','2025-01-01',"
        "'findata/Sectors/Technology.md','2025-01-01','Technology',NULL,NULL)"
    )
    conn.execute("INSERT INTO graph_edges VALUES ('Test Co','Healthcare','part_of','test')")
    conn.execute("INSERT INTO graph_edges VALUES ('Healthcare','Test Co','has_company','test')")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    return db_path


class TestMoveEntity:
    def _conn(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_same_sector_idempotent_skip(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms
        monkeypatch.setattr(ms, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_db(tmp_path)
        conn = self._conn(db_path)
        try:
            ok = move_entity(conn, "Test Co", "Healthcare")
            assert ok is True
            row = conn.execute(
                "SELECT sector_classification, file_path FROM entities WHERE name='Test Co'"
            ).fetchone()
            assert row[0] == "Healthcare"
            assert row[1] == "findata/Companies/Healthcare/Test_Co.md"
        finally:
            conn.close()
        # File must NOT have been moved: the relation-update error aborted
        # before the filesystem step, so the markdown stays in the old sector
        # (the fix orders DB writes before the file move).
        assert (tmp_path / "findata" / "Companies" / "Healthcare" / "Test_Co.md").exists()
        assert not (tmp_path / "findata" / "Companies" / "Technology" / "Test_Co.md").exists()
        # File untouched.
        assert (tmp_path / "findata" / "Companies" / "Healthcare" / "Test_Co.md").exists()

    def test_destination_file_exists_refused(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms
        monkeypatch.setattr(ms, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_db(tmp_path)
        # Pre-create the destination file so the move must be refused.
        dst = tmp_path / "findata" / "Companies" / "Technology" / "Test_Co.md"
        dst.parent.mkdir(parents=True)
        dst.write_text("# placeholder\n")
        conn = self._conn(db_path)
        try:
            ok = move_entity(conn, "Test Co", "Technology")
            assert ok is False
            row = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Test Co'"
            ).fetchone()
            assert row[0] == "Healthcare"
        finally:
            conn.close()
        assert (tmp_path / "findata" / "Companies" / "Healthcare" / "Test_Co.md").exists()
        assert dst.exists()

    def test_non_canonical_sector_refused(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms
        monkeypatch.setattr(ms, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_db(tmp_path)
        conn = self._conn(db_path)
        try:
            ok = move_entity(conn, "Test Co", "NotARealSector")
            assert ok is False
            row = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Test Co'"
            ).fetchone()
            assert row[0] == "Healthcare"
        finally:
            conn.close()

    def test_rollback_on_relation_update_error(self, tmp_path, monkeypatch):  # noqa: C901
        import helpers.maintenance.move_sector as ms
        monkeypatch.setattr(ms, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_db(tmp_path)

        # move_entity performs the filesystem move BEFORE the graph_edges
        # writes, so we wrap the call in an explicit transaction and force any
        # graph_edges write to raise a simulated relation-update error. The
        # point of the test is that the DB (entities + graph_edges) is fully
        # rolled back even though the markdown file (non-transactional) was
        # already moved -- move_entity must not leave the DB half-mutated.
        real = self._conn(db_path)

        # sqlite3.Cursor / Connection are immutable, so wrap the connection in
        # a proxy whose cursor() raises a simulated relation-update error on any
        # graph_edges write. move_entity moves the markdown file BEFORE the
        # graph_edges writes, so we run it inside an explicit transaction and
        # verify the DB (entities + graph_edges) is fully rolled back -- it must
        # not be left half-mutated.
        class _EdgeFailingCursor:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, sql, parameters=()):
                if "graph_edges" in sql:
                    raise sqlite3.OperationalError("simulated relation-update error")
                return self._cur.execute(sql, parameters)

            def fetchone(self):
                return self._cur.fetchone()

            def fetchall(self):
                return self._cur.fetchall()

            def __getattr__(self, name):
                return getattr(self._cur, name)

        class _FailingConn:
            def __init__(self, real):
                self._real = real

            def cursor(self, *a, **k):
                return _EdgeFailingCursor(self._real.cursor(*a, **k))

            def __getattr__(self, name):
                return getattr(self._real, name)

        conn = _FailingConn(real)
        raised = False
        try:
            conn.execute("BEGIN")
            move_entity(conn, "Test Co", "Technology")  # ty: ignore[invalid-argument-type]
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raised = True
        finally:
            conn.close()
        assert raised, "expected a relation-update error to propagate"

        # DB fully rolled back: sector unchanged and old edges intact, no new edge.
        conn = self._conn(db_path)
        try:
            row = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Test Co'"
            ).fetchone()
            assert row[0] == "Healthcare"
            assert conn.execute(
                "SELECT 1 FROM graph_edges WHERE source='Test Co' AND target='Healthcare' "
                "AND edge_type='part_of'"
            ).fetchone() is not None
            assert conn.execute(
                "SELECT 1 FROM graph_edges WHERE source='Test Co' AND target='Technology' "
                "AND edge_type='part_of'"
            ).fetchone() is None
        finally:
            conn.close()
