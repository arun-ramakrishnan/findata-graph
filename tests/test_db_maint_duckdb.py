#!/usr/bin/env python3
"""Tests for the DuckDB backup feature in helpers/maintenance/db_maint.py.

Covers the pre-mutation DuckDB recovery backup that mirrors the existing
SQLite backup contract: CHECKPOINT + shutil.copy2 of the .duckdb file
BEFORE any VACUUM runs.

The tests need a real DuckDB file to back up, so they're marked ``live``.
They use the production ``memory/graph.duckdb`` as the source and write
backups to ``tmp_path`` for isolation.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

duckdb = pytest.importorskip("duckdb")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQLITE_DB = PROJECT_ROOT / "memory" / "research.db"
DUCKDB_DB = PROJECT_ROOT / "memory" / "graph.duckdb"

if not SQLITE_DB.exists() or not DUCKDB_DB.exists():
    pytest.skip(
        "skipping db_maint DuckDB backup tests — "
        "memory/research.db or memory/graph.duckdb not present",
        allow_module_level=True,
    )

sys.path.insert(0, str(PROJECT_ROOT))
from helpers.core.zstd_io import decompress_file
from helpers.maintenance.db_maint import DBMaintainer  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def built_cache(tmp_path_factory) -> Path:
    """One production-copy SQLite DB + materialised DuckDB cache, shared by
    the whole module: connect() on a full production copy costs ~1.5-3s per
    build and every test here only READS/backups it — CHECKPOINT, VACUUM and
    backup don't change row counts, so sharing one cache across tests keeps
    every assertion intact (test_backup_overwrites_existing already runs
    m.run() twice against the same cache).
    """
    tmp = tmp_path_factory.mktemp("dbmaint")
    out = tmp / "test.db"
    src = sqlite3.connect(str(SQLITE_DB))
    dst = sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    from helpers.graph.query import connect

    c = connect(out)
    c.close()
    return out


@pytest.fixture
def tmp_sqlite(built_cache) -> Path:
    return built_cache


@pytest.fixture
def tmp_duckdb(built_cache) -> Path:
    return built_cache.with_suffix(".duckdb")


# --------------------------------------------------------------------------- #
# TestDuckDBBackup                                                             #
# --------------------------------------------------------------------------- #
class TestDuckDBBackup:
    """Verify the pre-mutation DuckDB recovery backup works correctly."""

    def test_backup_creates_file(self, tmp_sqlite, tmp_duckdb, tmp_path):
        backup_zst = tmp_path / "graph_backup.duckdb.zst"
        assert not backup_zst.exists()

        m = DBMaintainer(
            db_path=tmp_sqlite,
            backup_path=tmp_path / "sqlite_backup.db",
            duckdb_path=tmp_duckdb,
            duckdb_backup_path=tmp_path / "graph_backup.duckdb",
        )
        r = m.run()
        assert r["status"] == "complete"
        assert backup_zst.exists()
        assert backup_zst.stat().st_size > 0
        assert "backup" in r["duckdb"]
        assert r["duckdb"]["backup"]["size"] > 0

    def test_backup_is_readable_duckdb_file(self, tmp_sqlite, tmp_duckdb, tmp_path):
        # The backup must be a valid DuckDB file with the same row counts
        # as the source (i.e. it's not truncated or corrupt).
        backup_zst = tmp_path / "graph_backup.duckdb.zst"
        m = DBMaintainer(
            db_path=tmp_sqlite,
            backup_path=tmp_path / "sqlite_backup.db",
            duckdb_path=tmp_duckdb,
            duckdb_backup_path=tmp_path / "graph_backup.duckdb",
        )
        m.run()
        backup_path = tmp_path / "graph_backup_roundtrip.duckdb"
        decompress_file(backup_zst, backup_path)

        # Read source row counts.
        src_con = duckdb.connect(str(tmp_duckdb), read_only=True)
        try:
            src_v = src_con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0]
        finally:
            src_con.close()

        # Read backup row counts.
        bk_con = duckdb.connect(str(backup_path), read_only=True)
        try:
            bk_v = bk_con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0]
            bk_e = bk_con.execute("SELECT COUNT(*) FROM e_belongs").fetchone()[0]
        finally:
            bk_con.close()

        assert bk_v == src_v
        assert bk_e > 0

    def test_backup_overwrites_existing(self, tmp_sqlite, tmp_duckdb, tmp_path):
        # A second run must overwrite, not append or error. The backup
        # file must remain valid and readable after the overwrite.
        backup_zst = tmp_path / "graph_backup.duckdb.zst"
        m = DBMaintainer(
            db_path=tmp_sqlite,
            backup_path=tmp_path / "sqlite_backup.db",
            duckdb_path=tmp_duckdb,
            duckdb_backup_path=tmp_path / "graph_backup.duckdb",
        )
        m.run()
        first_size = backup_zst.stat().st_size

        # Second run — must overwrite cleanly (no error, same size,
        # still readable). zstd compression is deterministic here (same
        # input bytes, same level) so the size must be identical.
        m.run()
        second_size = backup_zst.stat().st_size
        assert second_size == first_size, "overwrite should not change size"

        # Verify the overwritten file is still a valid DuckDB file.
        backup_path = tmp_path / "graph_backup_roundtrip.duckdb"
        decompress_file(backup_zst, backup_path)
        bk_con = duckdb.connect(str(backup_path), read_only=True)
        try:
            v = bk_con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0]
        finally:
            bk_con.close()
        assert v > 0

    def test_no_duckdb_backup_path_skips_backup(self, tmp_sqlite, tmp_duckdb, tmp_path):
        # When duckdb_backup_path is None, no backup is taken but
        # maintenance still runs (CHECKPOINT + VACUUM).
        m = DBMaintainer(
            db_path=tmp_sqlite,
            backup_path=tmp_path / "sqlite_backup.db",
            duckdb_path=tmp_duckdb,
            duckdb_backup_path=None,  # explicitly None
        )
        r = m.run()
        assert r["status"] == "complete"
        assert "duckdb" in r
        # No backup key when duckdb_backup_path was None.
        assert "backup" not in r["duckdb"]


# --------------------------------------------------------------------------- #
# TestDryRunIncludesDuckDBBackup                                              #
# --------------------------------------------------------------------------- #
class TestDryRunIncludesDuckDBBackup:
    def test_dry_run_lists_duckdb_backup_step(self, tmp_sqlite, tmp_duckdb, tmp_path):
        m = DBMaintainer(
            db_path=tmp_sqlite,
            backup_path=tmp_path / "sqlite_backup.db",
            duckdb_path=tmp_duckdb,
            duckdb_backup_path=tmp_path / "graph_backup.duckdb",
            dry_run=True,
        )
        r = m.run()
        assert r["status"] == "dry_run"
        assert "DuckDB BACKUP" in r["steps"]
        assert "DuckDB CHECKPOINT" in r["steps"]
        assert "DuckDB VACUUM" in r["steps"]
        # DuckDB BACKUP must come before CHECKPOINT/VACUUM.
        steps = r["steps"]
        assert steps.index("DuckDB BACKUP") < steps.index("DuckDB CHECKPOINT")
        assert steps.index("DuckDB CHECKPOINT") < steps.index("DuckDB VACUUM")

    def test_dry_run_omits_duckdb_steps_when_no_file(self, tmp_sqlite, tmp_path):
        # When duckdb_path points at a nonexistent file, the dry-run plan
        # must NOT include any DuckDB steps.
        m = DBMaintainer(
            db_path=tmp_sqlite,
            backup_path=tmp_path / "sqlite_backup.db",
            duckdb_path=tmp_path / "nonexistent.duckdb",
            duckdb_backup_path=tmp_path / "graph_backup.duckdb",
            dry_run=True,
        )
        r = m.run()
        assert r["status"] == "dry_run"
        duckdb_steps = [s for s in r["steps"] if "DuckDB" in s]
        assert duckdb_steps == [], f"DuckDB steps should be empty: {duckdb_steps}"
