"""Unit tests for helpers/core/db.py."""

from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import pytest  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import (  # noqa: E402
    connect,
    market_cap_sql,
    get_generation,
    get_user_version,
    ensure_db_meta,
    utc_now,
)


# ---------------------------------------------------------------------------
# market_cap_sql — pure function
# ---------------------------------------------------------------------------
def test_market_cap_sql_default_alias():
    sql = market_cap_sql()
    assert "AS market_cap" in sql
    assert "market_cap/%" in sql


def test_market_cap_sql_custom_alias():
    sql = market_cap_sql("mc")
    assert "AS mc" in sql


# ---------------------------------------------------------------------------
# utc_now
# ---------------------------------------------------------------------------
def test_utc_now_returns_string():
    result = utc_now()
    assert isinstance(result, str)
    # SQLite CURRENT_TIMESTAMP format: YYYY-MM-DD HH:MM:SS
    assert "-" in result
    assert ":" in result


# ---------------------------------------------------------------------------
# connect — defaults and options
# ---------------------------------------------------------------------------
def test_connect_in_memory():
    conn = connect(":memory:")
    assert conn.row_factory == sqlite3.Row
    conn.close()


def test_connect_no_row_factory():
    conn = connect(":memory:", row_factory=None)
    assert conn.row_factory is None
    conn.close()


def test_connect_disable_fk():
    conn = connect(":memory:", enable_fk=False)
    # Should not raise
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 0
    conn.close()


def test_connect_disable_wal():
    conn = connect(":memory:", wal=False)
    # Should not raise
    conn.close()


def test_connect_applies_busy_timeout(tmp_path):
    conn = connect(str(tmp_path / "test.db"))
    bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert bt == 5000
    conn.close()


# ---------------------------------------------------------------------------
# get_generation
# ---------------------------------------------------------------------------
def test_get_generation_no_table():
    conn = sqlite3.connect(":memory:")
    assert get_generation(conn) is None
    conn.close()


def test_get_generation_with_value():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO db_meta VALUES ('generation', '42')")
    assert get_generation(conn) == 42
    conn.close()


def test_get_generation_null_value():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO db_meta VALUES ('generation', '1')")
    # Simulate missing key
    conn.execute("DELETE FROM db_meta WHERE key='generation'")
    assert get_generation(conn) is None
    conn.close()


# ---------------------------------------------------------------------------
# get_user_version
# ---------------------------------------------------------------------------
def test_get_user_version_default():
    conn = sqlite3.connect(":memory:")
    result = get_user_version(conn)
    assert result == 0  # default for new DB
    conn.close()


def test_get_user_version_set():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA user_version = 3")
    assert get_user_version(conn) == 3
    conn.close()


# ---------------------------------------------------------------------------
# ensure_db_meta
# ---------------------------------------------------------------------------
def _make_meta_db():
    """Create an in-memory DB with the tables ensure_db_meta expects."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    return conn


def test_ensure_db_meta_creates_table():
    conn = _make_meta_db()
    gen = ensure_db_meta(conn)
    assert gen is not None
    row = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
    assert row is not None
    conn.close()


def test_ensure_db_meta_seeds_generation():
    conn = _make_meta_db()
    gen = ensure_db_meta(conn)
    assert gen >= 1
    conn.close()


def test_ensure_db_meta_idempotent():
    conn = _make_meta_db()
    gen1 = ensure_db_meta(conn)
    gen2 = ensure_db_meta(conn)
    assert gen2 >= gen1
    conn.close()


def test_ensure_db_meta_triggers_bump_generation():
    conn = _make_meta_db()
    ensure_db_meta(conn)
    gen_before = get_generation(conn)
    conn.execute("INSERT INTO entities VALUES ('Test')")
    conn.commit()
    gen_after = get_generation(conn)
    assert gen_after is not None
    assert gen_before is not None
    assert gen_after > gen_before
    conn.close()


def test_ensure_db_meta_corrupt_generation_fixed():
    conn = _make_meta_db()
    conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO db_meta VALUES ('generation', 'not_a_number')")
    gen = ensure_db_meta(conn)
    assert gen == 1
    conn.close()


class TestConnectReadOnly:
    """read_only=True opens via URI mode=ro — no create, no mutate."""

    def test_reads_existing_file(self, tmp_path):
        db = tmp_path / "ro.db"
        from helpers.core.db import connect as _c

        con = _c(db)
        with con:
            con.execute("CREATE TABLE t (v TEXT)")
            con.execute("INSERT INTO t VALUES ('hello')")
        con.close()

        ro = _c(db, read_only=True)
        try:
            assert ro.execute("SELECT v FROM t").fetchone()[0] == "hello"
        finally:
            ro.close()

    def test_write_raises(self, tmp_path):
        from helpers.core.db import connect as _c

        db = tmp_path / "ro.db"
        con = _c(db)
        with con:
            con.execute("CREATE TABLE t (v TEXT)")
        con.close()
        # busy_timeout pragma makes the writer queue 5s before failing on a
        # mode=ro file where no writer can ever appear; bypass the wait.
        ro = _c(db, read_only=True)
        try:
            ro.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError):
                with ro:
                    ro.execute("INSERT INTO t VALUES ('nope')")
        finally:
            ro.close()

    def test_missing_file_is_not_created(self, tmp_path):
        from helpers.core.db import connect as _c

        missing = tmp_path / "never.db"
        with pytest.raises(sqlite3.OperationalError):
            _c(missing, read_only=True)
        assert not missing.exists()  # plain connect() would have created it

    def test_does_not_flip_journal_mode(self, tmp_path):
        from helpers.core.db import connect as _c

        db = tmp_path / "delete_mode.db"  # non-WAL file
        con = _c(db)
        with con:
            con.execute("CREATE TABLE t (v TEXT)")
        mode_before = con.execute("PRAGMA journal_mode").fetchone()[0]
        con.close()
        ro = _c(db, read_only=True)
        try:
            assert ro.execute("PRAGMA journal_mode").fetchone()[0] == mode_before
        finally:
            ro.close()
