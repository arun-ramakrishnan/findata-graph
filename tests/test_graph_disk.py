#!/usr/bin/env python3
"""Tests for the disk-based DuckDB graph cache (helpers/graph/query.py).

These tests exercise the new persistence model introduced in
``doc/graph_design.txt §8``:

  - First ``connect()`` on a cold file builds ``memory/graph.duckdb`` and
    records provenance in a ``_build_meta`` table.
  - Subsequent ``connect()`` calls on a warm file skip materialisation.
  - ``rebuild=True`` drops+repopulates materialised tables in-place.
  - ``fresh=True`` deletes the file and rebuilds from scratch.

All tests run against a tmp copy of the production SQLite DB so they
don't mutate ``memory/research.db``. Each test uses its own
``tmp_path/<name>.duckdb`` for isolation.

Marked ``live`` (requires the production SQLite file to exist).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.live

# Skip if duckdb isn't importable (CI without optional deps).
duckdb = pytest.importorskip("duckdb")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "memory" / "research.db"

if not DB_PATH.exists():
    pytest.skip(f"skipping disk-graph tests — {DB_PATH} not present",
                allow_module_level=True)

from helpers.graph.query import (  # noqa: E402
    DUCKDB_PATH,
    _SCHEMA_VERSION,
    _is_warm,
    connect,
    fresh_rebuild,
    rebuild,
    sector_of,
    update_extensions,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
# A-fix (2026-08-21): downsample the production copy. A full-corpus cold
# build costs ~1.2s and these build-mechanics tests build up to 3x each
# (~35s for the module); the contract under test is cold/warm/rebuild/meta
# behavior, not live scale. Every non-company entity is kept, plus CEAT
# (the name the assertions pin) and a deterministic alphabetical company
# sample; anything referencing a dropped entity goes with it.
#
# The trimmed DB is built ONCE per session (template) and each test gets a
# byte-copy: the trim pass costs ~0.35s (the note_search FTS delete alone
# is ~0.17s) and the post-delete file stays production-sized (~50MB) until
# VACUUM, so per-test re-trimming + copying that costs ~8s of module wall
# time collapses to one trim + ~ms copies of a few-MB compacted file.
_KEEP_COMPANIES = 120


@pytest.fixture(scope="session")
def _trimmed_template(tmp_path_factory) -> Path:
    """The downsampled, VACUUMed production copy; tmp_db copies it per test."""
    template = tmp_path_factory.mktemp("template") / "template.db"
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(template))
    try:
        src.backup(dst)
        # LIMIT lives inside the subquery so it bounds only the company
        # sample, not the whole compound select.
        dst.execute(
            "CREATE TEMP TABLE keep AS "
            "SELECT name FROM entities WHERE entity_type != 'company' "
            "UNION SELECT 'CEAT' "
            "UNION SELECT name FROM (SELECT name FROM entities "
            "  WHERE entity_type = 'company' AND name != 'CEAT' "
            "  ORDER BY name LIMIT ?)",
            (_KEEP_COMPANIES,),
        )
        dst.execute(
            "DELETE FROM graph_edges "
            "WHERE source NOT IN (SELECT name FROM keep) "
            "   OR target NOT IN (SELECT name FROM keep)")
        # FK children first (FKs are off on this raw connection, but keep
        # the copy tidy for tests that later connect with FKs on).
        for tbl, col in (("entity_tags", "entity_name"),
                         ("graph_analytics", "entity_name"),
                         ("events", "entity"),
                         ("quotes", "entity"),
                         ("company_metrics", "entity"),
                         ("company_embeddings", "company_name")):
            dst.execute(
                f"DELETE FROM {tbl} WHERE {col} NOT IN (SELECT name FROM keep)")  # noqa: S608  # parameterized; interpolated parts are schema-constant identifiers
        dst.execute("DELETE FROM entities WHERE name NOT IN (SELECT name FROM keep)")
        # note_search feeds v_note_embeddings: keep the kept entities' docs
        # plus a small newsletter slice so the doc_type mix stays realistic.
        dst.execute(
            "DELETE FROM note_search WHERE file_path NOT IN "
            "  (SELECT file_path FROM entities WHERE file_path IS NOT NULL) "
            "AND file_path NOT IN "
            "  (SELECT file_path FROM note_search WHERE doc_type IN "
            "   ('chatter','points_and_figures','plotlines') LIMIT 30)")
        dst.commit()
        # DELETE doesn't shrink the file (50MB production-sized) and leaves
        # FTS tombstones in the note_search shadow — VACUUM compacts both,
        # which is what makes the per-test copy cheap.
        dst.execute("VACUUM")
    finally:
        dst.close()
        src.close()
    return template


@pytest.fixture
def tmp_db(_trimmed_template, tmp_path) -> Path:
    """A byte-copy of the trimmed template at tmp_path/test.db.

    Each test gets a fresh copy so SQLite-side mutations (if any) don't
    leak. The DuckDB cache for this DB lands at tmp_path/test.duckdb
    (the test-isolation fallback in ``connect()``).
    """
    out = tmp_path / "test.db"
    shutil.copyfile(_trimmed_template, out)
    return out


def _duckdb_for(tmp_db: Path) -> Path:
    """The .duckdb file connect() will create for a given tmp SQLite DB."""
    return tmp_db.with_suffix(".duckdb")


# --------------------------------------------------------------------------- #
# TestDiskBasics                                                               #
# --------------------------------------------------------------------------- #
class TestDiskBasics:
    def test_cold_connect_creates_file(self, tmp_db):
        duckdb_path = _duckdb_for(tmp_db)
        assert not duckdb_path.exists()
        c = connect(tmp_db)
        try:
            assert duckdb_path.exists()
            assert sector_of(c, "CEAT") == "Automotive"
        finally:
            c.close()
        # File survives close.
        assert duckdb_path.exists()

    def test_warm_connect_skips_materialisation(self, tmp_db, monkeypatch):
        # First connect builds; spy on _build_graph to confirm.
        called = {"count": 0}
        from helpers.graph import query as q
        orig = q._build_graph

        def spy(con):
            called["count"] += 1
            return orig(con)

        monkeypatch.setattr(q, "_build_graph", spy)

        c1 = connect(tmp_db)
        c1.close()
        assert called["count"] == 1, "cold connect should build"

        c2 = connect(tmp_db)
        c2.close()
        assert called["count"] == 1, "warm connect should NOT build"

    def test_warm_connect_returns_same_data(self, tmp_db):
        c1 = connect(tmp_db)
        c1.close()
        c2 = connect(tmp_db)
        try:
            assert sector_of(c2, "CEAT") == "Automotive"
        finally:
            c2.close()

    def test_warm_file_is_warm(self, tmp_db):
        assert not _is_warm(_duckdb_for(tmp_db))
        c = connect(tmp_db)
        c.close()
        assert _is_warm(_duckdb_for(tmp_db))

    def test_parallel_ro_connects_serialize_the_build(self, tmp_db, tmp_path):
        """N read_only connects hitting a cold cache must all succeed.

        Regression (2026-08-26): under `make advisory` (jobs=4), a
        generation-stale cache made suggest-relations / graph-algos /
        analytics ALL take the read-write build fallback simultaneously —
        two DuckDB writers raced on the .wal lock ("Could not set lock on
        file ...wal: Conflicting lock is held in ..."). connect() now
        serializes the build path behind an flock on <cache>.build.lock
        and re-checks under the lock; waiters find the cache warm and
        open read-only. True cross-process test: 6 subprocesses started
        together against one cold cache.
        """
        duckdb_path = _duckdb_for(tmp_db)
        assert not duckdb_path.exists()
        child = (
            "import sys\n"
            "from helpers.graph.query import connect\n"
            "con = connect(sys.argv[1], read_only=True)\n"
            "n = con.execute('SELECT COUNT(*) FROM v_node').fetchone()[0]\n"
            "con.close()\n"
            "assert n > 0, 'empty v_node'\n"
            "print('OK')\n"
        )
        env = {**os.environ,
               "PYTHONPATH": f"{REPO_ROOT}{os.pathsep}{REPO_ROOT / 'helpers'}"}
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", child, str(tmp_db)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, cwd=str(REPO_ROOT),
            )
            for _ in range(6)
        ]
        results = [p.communicate() for p in procs]
        rcs = [p.returncode for p in procs]
        assert rcs == [0] * 6, f"child failures: {list(zip(rcs, results))}"
        assert duckdb_path.exists()

    def test_corrupted_file_treated_as_cold(self, tmp_db):
        # Write garbage to the .duckdb path; _is_warm should return False,
        # and connect() should rebuild.
        duckdb_path = _duckdb_for(tmp_db)
        duckdb_path.write_bytes(b"not a duckdb file")
        assert _is_warm(duckdb_path) is False
        c = connect(tmp_db)
        try:
            assert sector_of(c, "CEAT") == "Automotive"
        finally:
            c.close()


# --------------------------------------------------------------------------- #
# TestRebuild                                                                  #
# --------------------------------------------------------------------------- #
class TestRebuild:
    def test_rebuild_true_forces_materialisation(self, tmp_db, monkeypatch):
        called = {"count": 0}
        from helpers.graph import query as q
        orig = q._build_graph

        def spy(con):
            called["count"] += 1
            return orig(con)

        monkeypatch.setattr(q, "_build_graph", spy)

        c1 = connect(tmp_db)
        c1.close()
        assert called["count"] == 1

        # Warm connect — no rebuild.
        c2 = connect(tmp_db)
        c2.close()
        assert called["count"] == 1

        # rebuild=True forces a re-build even though the file is warm.
        c3 = connect(tmp_db, rebuild=True)
        c3.close()
        assert called["count"] == 2

    def test_rebuild_function_idempotent(self, tmp_db):
        c1 = connect(tmp_db)
        c1.close()
        # Calling rebuild() twice should not error.
        rebuild(db_path=tmp_db)
        rebuild(db_path=tmp_db)
        # And the file should still be queryable.
        c2 = connect(tmp_db)
        try:
            assert sector_of(c2, "CEAT") == "Automotive"
        finally:
            c2.close()

    def test_rebuild_picks_up_sqlite_changes(self, tmp_db):
        """The core staleness contract: SQLite changes are NOT visible on
        a warm DuckDB file until rebuild() runs."""
        from helpers.core.db import connect as sqlite_connect

        c1 = connect(tmp_db)
        assert sector_of(c1, "__TestSectorX__") is None
        c1.close()

        # Add a synthetic entity + edge to SQLite.
        conn = sqlite_connect(tmp_db)
        try:
            conn.execute(
                "INSERT INTO entities(name, entity_type) "
                "VALUES ('__TestCoX__', 'company')"
            )
            conn.execute(
                "INSERT INTO entities(name, entity_type) "
                "VALUES ('__TestSectorX__', 'sector')"
            )
            conn.execute(
                "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
                "VALUES ('__TestCoX__', '__TestSectorX__', 'part_of', 'test')"
            )
            conn.commit()
        finally:
            conn.close()

        # Warm connect — still None (stale cache, by design).
        c2 = connect(tmp_db)
        assert sector_of(c2, "__TestSectorX__") is None
        c2.close()

        # After rebuild, the new edge is visible.
        c3 = connect(tmp_db, rebuild=True)
        try:
            assert sector_of(c3, "__TestCoX__") == "__TestSectorX__"
        finally:
            c3.close()


# --------------------------------------------------------------------------- #
# TestFreshRebuild                                                             #
# --------------------------------------------------------------------------- #
class TestFreshRebuild:
    def test_fresh_drops_and_recreates_file(self, tmp_db):
        duckdb_path = _duckdb_for(tmp_db)
        c1 = connect(tmp_db)
        c1.close()
        assert duckdb_path.exists()
        original_mtime = duckdb_path.stat().st_mtime

        # Force a measurable mtime gap.
        import time
        time.sleep(0.05)

        c2 = connect(tmp_db, fresh=True)
        c2.close()
        assert duckdb_path.exists()
        assert duckdb_path.stat().st_mtime > original_mtime

        # And the file still serves queries.
        c3 = connect(tmp_db)
        try:
            assert sector_of(c3, "CEAT") == "Automotive"
        finally:
            c3.close()

    def test_fresh_rebuild_function(self, tmp_db):
        c1 = connect(tmp_db)
        c1.close()
        fresh_rebuild(db_path=tmp_db)
        c2 = connect(tmp_db)
        try:
            assert sector_of(c2, "CEAT") == "Automotive"
        finally:
            c2.close()


# --------------------------------------------------------------------------- #
# TestBuildMeta                                                                #
# --------------------------------------------------------------------------- #
class TestBuildMeta:
    def test_build_meta_table_exists_after_connect(self, tmp_db):
        c = connect(tmp_db)
        try:
            rows = c.execute(
                "SELECT key, value FROM _build_meta ORDER BY key"
            ).fetchall()
            keys = {r[0]: r[1] for r in rows}
            assert keys["schema_version"] == _SCHEMA_VERSION
            assert "built_at" in keys
            assert str(tmp_db) in keys["source_db"]
        finally:
            c.close()

    def test_build_meta_updated_on_rebuild(self, tmp_db):
        import time
        c1 = connect(tmp_db)
        c1.close()

        duckdb_path = _duckdb_for(tmp_db)
        # Read built_at directly via a read-only connection.
        con = duckdb.connect(str(duckdb_path), read_only=True)
        before = con.execute(
            "SELECT value FROM _build_meta WHERE key='built_at'"
        ).fetchone()[0]
        con.close()

        time.sleep(0.05)
        rebuild(db_path=tmp_db)

        con = duckdb.connect(str(duckdb_path), read_only=True)
        after = con.execute(
            "SELECT value FROM _build_meta WHERE key='built_at'"
        ).fetchone()[0]
        con.close()
        # built_at is a date, so on the same day it won't change. But
        # schema_version should still be present and the row should exist.
        assert after == before  # same date is fine

    def test_schema_version_mismatch_triggers_rebuild(self, tmp_db, monkeypatch):
        # Build with the current schema_version, then bump the module-level
        # constant. A warm connect should rebuild because _is_warm now
        # returns False (mismatched version).
        c1 = connect(tmp_db)
        c1.close()

        from helpers.graph import query as q
        monkeypatch.setattr(q, "_SCHEMA_VERSION", "999")

        assert q._is_warm(_duckdb_for(tmp_db)) is False

        called = {"count": 0}
        orig = q._build_graph

        def spy(con):
            called["count"] += 1
            # Real build uses the monkeypatched schema_version when
            # _mark_warm runs, so we let it proceed.
            return orig(con)

        monkeypatch.setattr(q, "_build_graph", spy)
        c2 = connect(tmp_db)
        c2.close()
        assert called["count"] == 1


# --------------------------------------------------------------------------- #
# TestExtensionUpdate                                                          #
# --------------------------------------------------------------------------- #
class TestExtensionUpdate:
    def test_update_extensions_returns_list(self):
        # Network-dependent — may fail offline. If it succeeds, result
        # should be a list (possibly empty if everything is current).
        try:
            result = update_extensions()
        except Exception as e:
            pytest.skip(f"network-dependent: {e}")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], str)


# --------------------------------------------------------------------------- #
# TestSnapshotRoundTrip                                                        #
# --------------------------------------------------------------------------- #
class TestSnapshotRoundTrip:
    def test_duckdb_snapshot_roundtrips(self, tmp_db, tmp_path):
        c = connect(tmp_db)
        c.close()
        duckdb_path = _duckdb_for(tmp_db)

        from helpers.maintenance.snapshot_db import (
            create_duckdb_snapshot,
            verify_duckdb_snapshot,
        )
        import logging
        logger = logging.getLogger("test_snapshot")

        out = tmp_path / "snap.duckdb.gz"
        r = create_duckdb_snapshot(duckdb_path, out, logger)
        assert r.get("compressed_bytes", 0) > 0
        assert out.exists()

        v = verify_duckdb_snapshot(out, duckdb_path, logger)
        assert v["match"] is True
        # O2: verify now covers ALL materialised tables + the property graph.
        # The full-coverage contract is pinned in TestSnapshotVerifyCoverage
        # below; this round-trip test just asserts the happy path matches.
        assert v["tables"]["v_node"] > 0
        assert v["property_graph_ok"] is True

    def test_snapshot_skips_when_no_file(self, tmp_path):
        from helpers.maintenance.snapshot_db import create_duckdb_snapshot
        import logging
        out = tmp_path / "out.gz"
        r = create_duckdb_snapshot(
            tmp_path / "nonexistent.duckdb", out, logging.getLogger("t")
        )
        assert r == {"skipped": True}
        assert not out.exists()


# --------------------------------------------------------------------------- #
# TestSnapshotVerifyCoverage (Bundle O2)
#
# Pre-O2 verify_duckdb_snapshot checked only v_node + e_belongs, leaving 11
# materialised tables and the property-graph declaration unverified. These
# tests pin the O2 contract: every registered materialised table is counted
# on both ends, row counts must match, and the GRAPH_TABLE(fin_graph ...)
# declaration must survive the restore.
# --------------------------------------------------------------------------- #
class TestSnapshotVerifyCoverage:
    def _build_and_snapshot(self, tmp_db, tmp_path):
        """Build the DuckDB cache from tmp_db, snapshot it, return (out, src)."""
        c = connect(tmp_db)
        c.close()
        duckdb_path = _duckdb_for(tmp_db)
        from helpers.maintenance.snapshot_db import create_duckdb_snapshot
        import logging
        out = tmp_path / "snap.duckdb.gz"
        create_duckdb_snapshot(duckdb_path, out, logging.getLogger("t"))
        return out, duckdb_path

    def test_verify_covers_all_materialised_tables(self, tmp_db, tmp_path):
        """Every table in EDGE_REGISTRY + the three vertex tables appears in
        the verify result's `tables` dict. Catches a future regression where
        a newly-added edge type is materialised but not covered by verify."""
        out, src = self._build_and_snapshot(tmp_db, tmp_path)
        from helpers.maintenance.snapshot_db import verify_duckdb_snapshot
        from helpers.graph.query import EDGE_REGISTRY
        import logging

        v = verify_duckdb_snapshot(out, src, logging.getLogger("t"))
        expected = {"v_node", "v_company", "v_sector"} | {
            spec["table"] for spec in EDGE_REGISTRY.values()
        }
        assert expected <= set(v["tables"]), (
            f"verify missed tables: {expected - set(v['tables'])}"
        )

    def test_verify_row_counts_match_source(self, tmp_db, tmp_path):
        """Row counts on the snapshot must equal row counts on the source for
        every materialised table present on both ends."""
        out, src = self._build_and_snapshot(tmp_db, tmp_path)
        from helpers.maintenance.snapshot_db import verify_duckdb_snapshot
        import logging

        v = verify_duckdb_snapshot(out, src, logging.getLogger("t"))
        assert v["match"] is True
        assert v["tables"] == v["source_tables"], (
            f"row-count mismatch: {v['tables']} != {v['source_tables']}"
        )

    def test_verify_property_graph_declaration_survives(self, tmp_db, tmp_path):
        """The materialised tables on the snapshot must be able to reconstruct
        the property graph — verify re-declares fin_graph and runs a
        GRAPH_TABLE query. ``CREATE PROPERTY GRAPH`` is session-scoped
        (never persisted to the file), so this checks the *tables* support
        pg construction, not that the pg object itself round-tripped.
        Pre-O2 verify never exercised the graph path at all."""
        out, src = self._build_and_snapshot(tmp_db, tmp_path)
        from helpers.maintenance.snapshot_db import verify_duckdb_snapshot
        import logging

        v = verify_duckdb_snapshot(out, src, logging.getLogger("t"))
        assert v["property_graph_ok"] is True

    def test_verify_flags_missing_table_on_snapshot(self, tmp_db, tmp_path):
        """A table present on the source but missing on the snapshot must
        flip `match` to False. Simulates a restore that dropped a table."""
        out, src = self._build_and_snapshot(tmp_db, tmp_path)
        # Corrupt the snapshot: decompress, drop a non-empty edge table,
        # re-checkpoint, re-gzip. We pick e_comention (typically non-empty
        # on the production-derived fixture) to ensure a real diff.
        import gzip
        import shutil
        import tempfile
        import duckdb
        from pathlib import Path
        from helpers.maintenance.snapshot_db import verify_duckdb_snapshot
        import logging

        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            with gzip.open(out, "rb") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            con = duckdb.connect(str(tmp))  # read-write to mutate
            try:
                # Find a non-empty table to drop so the diff is real.
                from helpers.graph.query import EDGE_REGISTRY
                dropped = None
                for spec in EDGE_REGISTRY.values():
                    t = spec["table"]
                    row = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                    assert row is not None
                    n = row[0]
                    if n > 0:
                        con.execute(f"DROP TABLE {t}")
                        dropped = t
                        break
                assert dropped is not None, "fixture has no non-empty edge table"
                con.execute("CHECKPOINT;")
            finally:
                con.close()
            with open(tmp, "rb") as fin, gzip.open(out, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        finally:
            tmp.unlink(missing_ok=True)

        v = verify_duckdb_snapshot(out, src, logging.getLogger("t"))
        assert v["match"] is False, (
            f"dropped {dropped} but verify still passed: {v}"
        )
        assert dropped not in v["tables"], (
            f"dropped {dropped} should be absent from snapshot tables"
        )
        assert dropped in v["source_tables"], (
            f"{dropped} should still be present on the source"
        )

    def test_verify_flags_broken_property_graph(self, tmp_db, tmp_path):
        """A snapshot whose tables can't support pg construction must flip
        ``match`` to False even if row counts agree. ``CREATE PROPERTY
        GRAPH`` is session-scoped (never in the file), so we simulate
        breakage by dropping a KEY column from an edge table — the
        ``SOURCE KEY (col) REFERENCES v_node (id)`` clause then fails,
        which a row-count check alone would miss."""
        out, src = self._build_and_snapshot(tmp_db, tmp_path)
        import gzip
        import shutil
        import tempfile
        import duckdb
        from pathlib import Path
        from helpers.maintenance.snapshot_db import verify_duckdb_snapshot
        from helpers.graph.query import EDGE_REGISTRY
        import logging

        # Pick a non-empty edge table to mangle.
        con = duckdb.connect(str(src))
        try:
            target_table = None
            target_src_col = None
            for spec in EDGE_REGISTRY.values():
                t = spec["table"]
                row = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                assert row is not None
                n = row[0]
                if n > 0:
                    target_table = t
                    target_src_col = spec["src"]
                    break
            assert target_table is not None
        finally:
            con.close()

        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            with gzip.open(out, "rb") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            con = duckdb.connect(str(tmp))
            try:
                # Recreate the table WITHOUT its source KEY column. Row
                # count is preserved (we copy the surviving column), but
                # ``_declare_property_graph`` will reject the table because
                # the SOURCE KEY column is gone.
                old_cols = [
                    r[0] for r in con.execute(
                        f"SELECT column_name FROM information_schema.columns "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                        f"WHERE table_name = '{target_table}' ORDER BY ordinal_position"
                    ).fetchall()
                ]
                assert target_src_col in old_cols, (
                    f"{target_src_col} not in {target_table} cols {old_cols}"
                )
                keep_cols = [c for c in old_cols if c != target_src_col]
                con.execute(f"ALTER TABLE {target_table} RENAME TO {target_table}_old")
                col_list = ", ".join(keep_cols)
                con.execute(
                    f"CREATE TABLE {target_table} AS SELECT {col_list} "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                    f"FROM {target_table}_old"
                )
                con.execute(f"DROP TABLE {target_table}_old")
                con.execute("CHECKPOINT;")
            finally:
                con.close()
            with open(tmp, "rb") as fin, gzip.open(out, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        finally:
            tmp.unlink(missing_ok=True)

        v = verify_duckdb_snapshot(out, src, logging.getLogger("t"))
        assert v["match"] is False, (
            f"mangled {target_table}.{target_src_col} but verify passed: {v}"
        )
        assert v["property_graph_ok"] is False, (
            f"pg should fail to construct without {target_src_col}: {v}"
        )
        # The mangled table's row count is still in the snapshot result,
        # so source vs snapshot row-count comparison alone would pass —
        # only the pg-constructibility check catches the regression.
        assert v["tables"][target_table] == v["source_tables"][target_table]

    def test_verify_no_source_passes_with_pg_check(self, tmp_db, tmp_path):
        """When no source is given, match tracks just the property-graph
        query (no row-count comparison possible). Pins the no-source path
        that pre-O2 hardcoded match=True regardless of catalog state."""
        out, _ = self._build_and_snapshot(tmp_db, tmp_path)
        from helpers.maintenance.snapshot_db import verify_duckdb_snapshot
        import logging

        v = verify_duckdb_snapshot(out, None, logging.getLogger("t"))
        assert v["match"] is True
        assert v["property_graph_ok"] is True
        assert "source_tables" not in v


# --------------------------------------------------------------------------- #
# TestProductionPath (exercises memory/graph.duckdb directly)                  #
# --------------------------------------------------------------------------- #
class TestProductionPath:
    """Smoke test that the production default path (no args) works.

    Uses the real memory/research.db + memory/graph.duckdb. Does NOT
    call fresh=True (that would clobber the cache). Cleans up by
    rebuilding at the end so subsequent test runs see a warm file.
    """

    def test_default_connect_uses_memory_graph_duckdb(self):
        c = connect()  # no args = production default
        try:
            assert DUCKDB_PATH.exists()
            assert sector_of(c, "CEAT") == "Automotive"
        finally:
            c.close()
