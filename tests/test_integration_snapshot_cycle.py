#!/usr/bin/env python3
"""Integration tests for the snapshot create → verify → restore cycle
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A3).

The unit suites (test_snapshot.py / test_snapshot_db.py) pin the pieces:
zstd round-trips, parquet restore, the stray-table manifest guard. What
no test exercised is the CYCLE over a real graph cache: a tmp SQLite DB
that ``helpers.graph.query.connect`` has actually materialised into a
.duckdb file, snapshotted through the production functions, restored to
a second location, and reconnected — with query-level parity (the
`make snapshot` / `make snapshot-check` / `make snapshot-restore` path).

The source DB is a schema-only backup of production (the test_graph.py
``_minimal_db`` pattern) seeded with a handful of entities/edges, so the
materialisation runs the real DDL against the real schema.
"""
from __future__ import annotations

import logging

from helpers.core.zstd_io import compress_file, decompress_file
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph.query import connect, sector_of  # noqa: E402
from helpers.maintenance.snapshot_db import (  # noqa: E402
    create_duckdb_snapshot,
    export_parquet_duckdb,
    export_parquet_sqlite,
    main as snapshot_main,
    restore_duckdb_from_parquet,
    restore_sqlite_from_parquet,
    verify_duckdb_snapshot,
)

pytestmark = [pytest.mark.integration]

duckdb = pytest.importorskip("duckdb")

from helpers.graph.query import DB_PATH  # noqa: E402

_log = logging.getLogger("test_snapshot_cycle")

# (name, type, sector_classification) + belongs_to edges: one sector, two
# companies — enough for sector_of parity without a live-corpus build.
_ENTITIES = [
    ("HDFC Bank", "company", "Banking"),
    ("ICICI Bank", "company", "Banking"),
    ("Banking", "sector", None),
]
_EDGES = [
    ("HDFC Bank", "Banking", "part_of"),
    ("ICICI Bank", "Banking", "part_of"),
]


def _seeded_db(tmp_path: Path, name: str = "src.db") -> Path:
    """Schema-only production backup, wiped + reseeded (test_graph.py's
    _minimal_db pattern: real schema + db_meta, scenario rows only).
    VACUUM compacts the freed production pages away — the file is then
    ~100KB instead of tens of MB, which the snapshot paths care
    about (they copy the whole file)."""
    db = tmp_path / name
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(db))
    src.backup(dst)
    src.close()
    for t in ("graph_edges", "entity_tags", "graph_analytics", "events",
              "quotes", "company_metrics", "company_embeddings",
              "note_search"):
        dst.execute(f"DELETE FROM {t}")  # noqa: S608  # schema-constant identifiers
    dst.execute("DELETE FROM entities")
    dst.executemany(
        "INSERT INTO entities (name, entity_type, sector_classification) "
        "VALUES (?,?,?)", _ENTITIES)
    dst.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?,?,?,'seed')", _EDGES)
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    return db


@pytest.fixture(scope="module")
def cycle(tmp_path_factory):
    """A materialised tmp graph: (sqlite_path, duckdb_path) after ONE real
    ``connect(fresh=True)`` build, shared by the module (each cold build
    costs ~0.9s). Tests never mutate these files in ways later tests
    observe — the drift test restores the generation it bumps."""
    tmp_path = tmp_path_factory.mktemp("snapshot_cycle")
    db = _seeded_db(tmp_path)
    con = connect(db, fresh=True)
    con.close()
    return db, db.with_suffix(".duckdb")


class TestDuckdbSnapshotCycle:
    def test_create_then_verify_matches(self, cycle):
        db, ddb = cycle
        out = db.parent / "snap.duckdb.zst"
        info = create_duckdb_snapshot(ddb, out, _log)
        assert info.get("skipped") is None  # actually snapshotted
        assert out.exists()
        result = verify_duckdb_snapshot(out, ddb, _log)
        assert result["skipped"] is False
        assert result["match"] is True
        assert result["tables"]["v_node"] == 3
        assert result["property_graph_ok"] is True

    def test_tampered_snapshot_dropped_table_fails_verify(self, cycle):
        """A table dropped from the SNAPSHOT side must surface as a set
        mismatch, not a silent pass (row counts alone would flag it only
        as 'absent' — the set comparison is the guard)."""
        db, ddb = cycle
        out = db.parent / "snap.duckdb.zst"
        create_duckdb_snapshot(ddb, out, _log)
        # Tamper: decompress, drop an edge table, recompress.
        raw = db.parent / "tampered.duckdb"
        decompress_file(out, raw)
        con = duckdb.connect(str(raw))
        con.execute("DROP TABLE e_competes")
        con.close()
        compress_file(raw, out)
        result = verify_duckdb_snapshot(out, ddb, _log)
        assert result["match"] is False
        assert "e_competes" not in result["tables"]
        assert "e_competes" in result["source_tables"]

    def test_generation_drift_flagged(self, cycle):
        """Counts identical but the source generation moved (a rebuild ran
        without re-snapshotting) -> match False. The O(1) staleness check
        (P2.4): a stale snapshot must fail even when the data happens to
        agree."""
        db, ddb = cycle
        out = db.parent / "snap.duckdb.zst"
        create_duckdb_snapshot(ddb, out, _log)
        # The RW connection must be CLOSED before verify: an open writer
        # blocks verify's read-only source connect, whose except-fallback
        # would then compare the snapshot against itself (always green).
        con = duckdb.connect(str(ddb))
        old = con.execute(
            "SELECT value FROM _build_meta WHERE key = 'generation'"
        ).fetchone()[0]
        con.execute(
            "UPDATE _build_meta SET value = CAST(value AS BIGINT) + 1 "
            "WHERE key = 'generation'")
        con.close()
        try:
            result = verify_duckdb_snapshot(out, ddb, _log)
        finally:
            con = duckdb.connect(str(ddb))
            con.execute(
                "UPDATE _build_meta SET value = ? "
                "WHERE key = 'generation'", (str(old),))
            con.close()
        assert result["tables"] == result["source_tables"]  # data agrees…
        assert result["match"] is False                     # …but gen drifted

    def test_create_skips_cleanly_when_no_duckdb_exists(self, tmp_path):
        """SQLite-only deployments: no .duckdb file -> create skips, and
        verify treats the missing snapshot as optional (match=True,
        skipped=True) so a SQLite-only --check stays green."""
        missing = tmp_path / "never.duckdb"
        info = create_duckdb_snapshot(missing, tmp_path / "out.zst", _log)
        assert info == {"skipped": True}
        assert not (tmp_path / "out.zst").exists()
        r = verify_duckdb_snapshot(tmp_path / "out.zst", missing, _log)
        assert r["match"] is True and r["skipped"] is True


class TestParquetRestoreParity:
    def test_restore_reconnect_query_parity(self, cycle):
        """The flagship cycle: parquet-export BOTH stores, restore into a
        second tree, reconnect through query.connect, and assert
        query-level parity — sector_of answers + v_node/e_belongs counts."""
        db, ddb = cycle
        pq = db.parent / "pq"
        export_parquet_sqlite(db, pq / "sqlite", _log)
        export_parquet_duckdb(ddb, pq / "duckdb", _log)

        rdb = db.parent / "restored.db"
        rddb = db.parent / "restored.duckdb"
        si = restore_sqlite_from_parquet(pq / "sqlite", rdb, _log)
        di_ = restore_duckdb_from_parquet(pq / "duckdb", rddb, _log)
        assert si["tables"]["entities"] == 3
        assert di_["tables"]["v_node"] == 3

        con = connect(rdb)  # warm reuse: restored .duckdb generation matches
        try:
            assert sector_of(con, "HDFC Bank") == "Banking"
            assert sector_of(con, "ICICI Bank") == "Banking"
            v = con.execute("SELECT COUNT(*) FROM v_node").fetchone()
            e = con.execute("SELECT COUNT(*) FROM e_belongs").fetchone()
            assert v is not None and v[0] == 3
            assert e is not None and e[0] == 2
        finally:
            con.close()


class TestCliCycle:
    def test_main_create_then_check_green(self, cycle, monkeypatch):
        """The `make snapshot` then `make snapshot-check` wiring end-to-end:
        main() with tmp --db/--out/--duckdb/--duckdb-out/--parquet-dir
        creates both formats and self-verifies; a following --check run
        over the same paths returns 0."""
        db, ddb = cycle
        out = db.parent / "cli.db.zst"
        dout = db.parent / "cli.duckdb.zst"
        pq = db.parent / "pq"

        def _main(*flags):
            monkeypatch.setattr(sys, "argv", [
                "snapshot_db.py", *flags,
                "--db", str(db), "--out", str(out),
                "--duckdb", str(ddb), "--duckdb-out", str(dout),
                "--parquet-dir", str(pq),
            ])
            return snapshot_main()

        assert _main("--format", "both") == 0
        assert out.exists() and dout.exists()
        assert (pq / "sqlite" / "entities.parquet").exists()
        assert _main("--check") == 0
