#!/usr/bin/env python3
"""
Versioned snapshot of memory/research.db (+ memory/graph.duckdb).

Two artifact families, two roles:

1. Parquet (Bundle L1) — the GIT-TRACKED artifact under ``snapshots/parquet/``.
   Each materialised DuckDB table (v_node, v_company, e_*) and each SQLite
   data table (entities, graph_edges, ...) is exported to its own .parquet
   file under ``snapshots/parquet/duckdb/`` and ``snapshots/parquet/sqlite/``.
   Portable + columnar: readable by pandas/polars/pyarrow/duckdb without
   needing the original DB engines. Alongside the data, the replayable
   schema DDL is captured (``_schema.sqlite.sql`` / ``_schema.duckdb.sql``)
   so the snapshot is fully self-describing. FTS5 derived shadows
   (``note_search_data``/``_idx``/``_docsize``/``_config``) are excluded,
   but the FTS5 *content* shadow (``note_search_content``) IS exported —
   it carries the indexed text, and ``--restore`` regenerates the index
   from it via the FTS5 ``('rebuild')`` command.

2. gzip binary — LOCAL-ONLY byte-exact copies under ``db-backup/`` (gitignored).
   The SQLite DB runs in WAL mode, so a naive ``cp research.db`` would miss
   the ``-wal`` file and yield a stale/corrupt copy. This uses SQLite's
   online backup API, which produces a transactionally consistent,
   self-contained file with all committed WAL data merged in, then
   gzip-compresses it. The DuckDB file is checkpointed before copying.
   Fastest disaster recovery (``gunzip -c ... > memory/research.db``), but
   binary DBs churn badly in git — hence parquet-for-checkins.

Both ``memory/`` (live DBs) and ``db-backup/`` (gzip scratch) are
gitignored; ``snapshots/parquet/`` is the committed, restorable state.

Usage:
  python3 helpers/maintenance/snapshot_db.py            # snapshot gzip + Parquet (both formats) + verify
  python3 helpers/maintenance/snapshot_db.py --check     # verify existing snapshots (BOTH formats)
  python3 helpers/maintenance/snapshot_db.py --no-duckdb # SQLite only
  python3 helpers/maintenance/snapshot_db.py --format binary   # gzip .db.gz only
  python3 helpers/maintenance/snapshot_db.py --format parquet  # Parquet only (git checkin path)

Restore (from the git-tracked Parquet snapshot):
  make snapshot-restore        # or: python3 helpers/maintenance/snapshot_db.py --restore [--force]
  Rebuilds memory/research.db + memory/graph.duckdb from snapshots/parquet/:
  apply schema DDL → load every .parquet → FTS5 ('rebuild') → integrity
  checks → atomic replace of the live files. Refuses to overwrite an
  existing live DB unless --force.

  (Local gzip alternative: gunzip -c db-backup/research.snapshot.db.gz
  > memory/research.db)

See also: ``helpers/maintenance/maint.py`` — the orchestrator that runs
db_maint → snapshot_db → graph-rebuild in the right order. Prefer
``make maint`` over invoking this script directly when you want both
the SQLite VACUUM and the snapshot refreshed together.
"""

import argparse
import gzip
import logging
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

# `duckdb` is an optional dependency (imported lazily where it is actually
# used). It appears at module scope only inside the string annotation of
# _list_duckdb_tables(); guard it under TYPE_CHECKING so static tools can
# resolve the name without forcing a runtime import (matches the lazy-import
# discipline used elsewhere in this module).
if TYPE_CHECKING:
    import duckdb

# Repo root on sys.path so the lazy `from helpers.graph.query import ...`
# inside verify_duckdb_snapshot (Bundle O2) resolves when this script is run
# directly (``python3 helpers/maintenance/snapshot_db.py``) rather than as
# a module. Matches the backfill_valid_from.py / move_sector.py precedent.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DEFAULT_DB = "memory/research.db"
DEFAULT_OUT = "db-backup/research.snapshot.db.gz"
DEFAULT_DUCKDB = "memory/graph.duckdb"
DEFAULT_DUCKDB_OUT = "db-backup/graph.snapshot.duckdb.gz"
# Git-tracked, restoreable Parquet snapshot (see module docstring).
DEFAULT_PARQUET = "snapshots/parquet"


def _compute_root() -> Path:
    # helpers/maintenance/snapshot_db.py -> repo root is two parents up
    return Path(__file__).resolve().parents[2]


def create_snapshot(db_path: Path, out_path: Path, logger: logging.Logger) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Online backup into a temp file: consistent, WAL-merged, self-contained.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        src = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(tmp_path))
        try:
            with dest:
                src.backup(dest)
        finally:
            dest.close()
            src.close()

        raw = tmp_path.stat().st_size
        with open(tmp_path, "rb") as fin, gzip.open(out_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    finally:
        tmp_path.unlink(missing_ok=True)

    gz = out_path.stat().st_size
    logger.info(f"Snapshot: {out_path} ({gz:,} bytes compressed, {raw:,} bytes source)")
    return {"snapshot": str(out_path), "compressed_bytes": gz, "source_bytes": raw}


def verify_snapshot(
    snapshot_path: Path, source_db: Path | None, logger: logging.Logger
) -> dict:
    """Round-trip check: decompress the snapshot, open it, compare row counts to the source."""
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        with gzip.open(snapshot_path, "rb") as fin, open(tmp_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)

        conn = sqlite3.connect(str(tmp_path))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            integrity = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM entities")
            snap_entities = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM relations")
            snap_relations = cur.fetchone()[0]
        finally:
            conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    result = {
        "integrity": integrity,
        "entities": snap_entities,
        "relations": snap_relations,
    }

    if source_db and source_db.exists():
        sconn = sqlite3.connect(str(source_db))
        try:
            scur = sconn.cursor()
            scur.execute("SELECT COUNT(*) FROM entities")
            src_entities = scur.fetchone()[0]
            scur.execute("SELECT COUNT(*) FROM relations")
            src_relations = scur.fetchone()[0]
        finally:
            sconn.close()
        result.update(source_entities=src_entities, source_relations=src_relations)
        ok = (
            integrity == "ok"
            and snap_entities == src_entities
            and snap_relations == src_relations
        )
        result["match"] = ok
        logger.info(
            f"Verify: integrity={integrity} | entities {snap_entities}/{src_entities} "
            f"| relations {snap_relations}/{src_relations} -> {'OK' if ok else 'MISMATCH'}"
        )
    else:
        result["match"] = integrity == "ok"
        logger.info(
            f"Verify: integrity={integrity} | entities={snap_entities} relations={snap_relations}"
        )
    return result


def create_duckdb_snapshot(
    duckdb_path: Path, out_path: Path, logger: logging.Logger
) -> dict:
    """CHECKPOINT the DuckDB file (flush WAL), then gzip-copy to ``out_path``.

    DuckDB allows either one read-write OR many read-only connections to a
    file. We open read-only for the CHECKPOINT (which forces WAL merge
    even from a reader in recent DuckDB versions); on failure we fall
    back to copying the main file plus any WAL sidecar verbatim.

    Version-sensitive assumption: read-only CHECKPOINT relies on DuckDB
    ≥ 1.5 allowing a reader connection to flush the WAL. See
    doc/graph_design.txt §9.3 (was §17.11, Bundle O3) for the full caveat + how to
    re-test on pin bumps; the fallback below degrades gracefully.

    Skips silently if the DuckDB file does not exist (returns
    ``{"skipped": True}``).
    """
    if not duckdb_path.exists():
        logger.info(f"DuckDB file not found, skipping: {duckdb_path}")
        return {"skipped": True}

    out_path.parent.mkdir(parents=True, exist_ok=True)

    import duckdb

    try:
        con = duckdb.connect(str(duckdb_path), read_only=True)
        try:
            con.execute("CHECKPOINT;")
        finally:
            con.close()
    except duckdb.Error as e:
        # read-only CHECKPOINT may be rejected on some DuckDB versions;
        # fall back to a plain file copy (the WAL sidecar carries the
        # pending changes if any).
        logger.warning(f"read-only CHECKPOINT failed ({e}); copying file as-is")

    # gzip the main .duckdb file.
    raw = duckdb_path.stat().st_size
    with open(duckdb_path, "rb") as fin, gzip.open(out_path, "wb") as fout:
        shutil.copyfileobj(fin, fout)

    gz = out_path.stat().st_size
    logger.info(
        f"DuckDB snapshot: {out_path} ({gz:,} bytes compressed, {raw:,} bytes source)"
    )
    # P2.4: also snapshot WAL sidecar if it exists (un-CHECKPOINTed tail).
    wal_path = duckdb_path.with_suffix(".duckdb.wal")
    if out_path.name.endswith(".duckdb.gz"):
        wal_out = out_path.parent / out_path.name.replace(".duckdb.gz", ".duckdb.wal.gz")
    else:
        wal_out = out_path.parent / (out_path.name + ".wal.gz")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        try:
            with open(wal_path, "rb") as fin, gzip.open(wal_out, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            logger.info(f"DuckDB WAL snapshot: {wal_out} ({wal_out.stat().st_size:,} bytes)")
        except Exception as e:
            logger.warning(f"DuckDB WAL snapshot failed for {wal_path}: {e}")
    else:
        if wal_out.exists():
            try:
                wal_out.unlink()
            except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
                pass
    return {"snapshot": str(out_path), "compressed_bytes": gz, "source_bytes": raw}


def verify_duckdb_snapshot(  # noqa: C901
    snapshot_path: Path, source_duckdb: Path | None, logger: logging.Logger
) -> dict:
    """Round-trip check: decompress the DuckDB snapshot, compare row counts
    for ALL materialised tables to the source, and assert the restored
    tables can support property-graph construction.

    Coverage (Bundle O2, 2026-07-27): the three vertex tables (``v_node``,
    ``v_company``, ``v_sector``) plus every edge table registered in
    ``EDGE_REGISTRY`` (``e_belongs``, ``e_has``, ``e_competes``, ``e_jv``,
    ``e_group``, ``e_supplier``, ``e_customer``, ``e_acquired``,
    ``e_subsidiary``, ``e_comention``). Pre-O2 this checked only ``v_node``
    and ``e_belongs``, giving false confidence that the other 11 tables
    had round-tripped.

    Structural check (formerly the property-graph check): duckpgq was
    retired (2026-08-14, doc/improvements/archive/graph/duckpgq_retirement.txt)
    — the graph layer is now plain SQL over the materialised ``v_node`` /
    ``e_*`` tables. What still needs verifying is that those tables are
    structurally sound: every edge table's source/destination columns must
    resolve against ``v_node.id``. The verify runs the FK JOINs for every
    EDGE_REGISTRY entry on the decompressed snapshot, catching structural
    breakage (dropped columns, wrong references, type mismatches) that a
    row-count check alone would miss. The result key keeps its historical
    name ``property_graph_ok`` for API compatibility.

    Returns a dict with shape::

        {
            "tables": {<table_name>: <row_count>, ...},  # snapshot side
            "property_graph_ok": bool,   # e_* FKs resolve against v_node.id
            "skipped": False,
            "source_tables": {...},        # only when source_duckdb is given
            "match": bool,                 # tables+counts agree AND pg ok
        }
    """
    if not snapshot_path.exists():
        # DuckDB snapshot is optional — return ok=True with skipped flag
        # rather than raising, so verify passes on a SQLite-only snapshot.
        logger.info(f"DuckDB snapshot not found, skipping verify: {snapshot_path}")
        return {"match": True, "skipped": True}

    # Defer the duckdb + EDGE_REGISTRY imports until the snapshot exists;
    # the SQLite-only verify path never pays this cost.
    import duckdb
    from helpers.graph.query import EDGE_REGISTRY

    # Canonical list of materialised tables to verify, in creation order
    # (vertex tables first, then edge tables in EDGE_REGISTRY order).
    materialised_tables = ["v_node", "v_company", "v_sector"] + [
        spec["table"] for spec in EDGE_REGISTRY.values()
    ]

    def _count_tables(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
        """Return ``{table_name: row_count}`` for every materialised table
        that exists in ``con``. Missing tables are omitted from the dict;
        the caller's set comparison flags missing-table regressions."""
        counts: dict[str, int] = {}
        for t in materialised_tables:
            try:
                _row = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                counts[t] = _row[0] if _row is not None else 0
            except duckdb.Error:
                # Table absent or schema-mismatched — leave it out so the
                # set diff against the source surfaces the regression.
                continue
        return counts

    def _property_graph_constructible(
        con: duckdb.DuckDBPyConnection,
    ) -> bool:
        """True iff every edge table's FK columns resolve against
        ``v_node.id`` (the structural invariant the retired duckpgq
        property-graph declaration used to pin; name kept for API compat).

        Catches structural breakage a row-count check misses: dropped
        columns, wrong references, type mismatches between edge tables
        and ``v_node.id``."""
        try:
            for spec in EDGE_REGISTRY.values():
                con.execute(
                    f"SELECT COUNT(*) FROM {spec['table']} e "  # noqa: S608  # table names come from the in-repo EDGE_REGISTRY constant
                    f"JOIN v_node a ON a.id = e.{spec['src']} "
                    f"JOIN v_node b ON b.id = e.{spec['dst']}"
                ).fetchone()
            return True
        except duckdb.Error:
            return False

    snap_gen = None
    src_gen = None
    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        with gzip.open(snapshot_path, "rb") as fin, open(tmp_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)

        # Read-only is sufficient post-duckpgq-retirement: the structural
        # check is pure SELECTs (no DDL, no extension needed).
        con = duckdb.connect(str(tmp_path), read_only=True)
        try:
            snap_counts = _count_tables(con)
            snap_pg_ok = _property_graph_constructible(con)
            # P2.4: capture generation for staleness check
            try:
                _r = con.execute("SELECT value FROM _build_meta WHERE key='generation'").fetchone()
                snap_gen = _r[0] if _r else None
            except Exception:
                snap_gen = None
        finally:
            con.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    result = {
        "tables": snap_counts,
        "property_graph_ok": snap_pg_ok,
        "skipped": False,
    }

    if source_duckdb and source_duckdb.exists():
        # Source-side check is row-counts only: the source is the known-good
        # reference and its pg is always rebuilt by connect() at query time,
        # so there's nothing to verify about its pg constructibility.
        try:
            sconn = duckdb.connect(str(source_duckdb), read_only=True)
            try:
                src_counts = _count_tables(sconn)
                try:
                    _r2 = sconn.execute("SELECT value FROM _build_meta WHERE key='generation'").fetchone()
                    src_gen = _r2[0] if _r2 else None
                except Exception:
                    src_gen = None
            finally:
                sconn.close()
        except duckdb.Error:
            src_counts = snap_counts
            src_gen = snap_gen

        # P2.4: generation must also match (O(1) staleness)
        gen_match = (snap_gen == src_gen)
        if snap_gen is not None or src_gen is not None:
            logger.info(f"DuckDB generation: snapshot={snap_gen} source={src_gen} -> {'OK' if gen_match else 'MISMATCH'}")
        # Match requires: identical table set, identical row counts on every
        # table, AND the snapshot's tables support pg construction, AND generation match.
        same_tables = set(snap_counts) == set(src_counts)
        same_counts = snap_counts == src_counts
        ok = same_tables and same_counts and snap_pg_ok and gen_match

        result.update(source_tables=src_counts, match=ok)
        # One-line summary: v_node + total edges + pg flag; per-table diffs
        # appended only when something disagrees (keeps logs scannable).
        snap_edges = sum(c for t, c in snap_counts.items() if t.startswith("e_"))
        src_edges = sum(c for t, c in src_counts.items() if t.startswith("e_"))
        diffs = {
            t: f"{snap_counts.get(t, 'absent')}/{src_counts.get(t, 'absent')}"
            for t in materialised_tables
            if snap_counts.get(t) != src_counts.get(t)
        }
        suffix = f" DIFFS={diffs}" if diffs else ""
        pg_flag = "ok" if snap_pg_ok else "BAD"
        logger.info(
            f"Verify DuckDB: v_node {snap_counts.get('v_node', '?')}/"
            f"{src_counts.get('v_node', '?')} | edges {snap_edges}/{src_edges} "
            f"| pg={pg_flag} -> {'OK' if ok else 'MISMATCH'}{suffix}"
        )
    else:
        # No source to compare against — match tracks just the pg check.
        result["match"] = snap_pg_ok
        logger.info(
            f"Verify DuckDB: tables={snap_counts} "
            f"pg={'ok' if snap_pg_ok else 'BAD'} (no source)"
        )
    return result


# ---------------------------------------------------------------------------
# Parquet snapshot (Bundle L1)
# ---------------------------------------------------------------------------

# DuckDB tables eligible for Parquet export: the canonical manifest lives
# in helpers/graph/query.py::MATERIALISED_TABLES (single source of truth
# with _build_graph's drop pass — a table created by the materialisation
# is added there, in the same change). Tables found in the live file
# OUTSIDE the manifest are stray scratch state (e.g. a benchmark table
# left behind by a measurement session): NOT exported, NOT written to the
# schema DDL, and warned about loudly. Hardening added 2026-08-21 after
# the e_all_und leftover leaked an orphan parquet into a snapshot commit
# (snapshot-check passed — it compares live vs checked-in, so a stray on
# both sides is "consistent").

# SQLite data tables to export. FTS5 derived shadows (note_search_data/
# _idx/_docsize/_config, entities_fuzzy*) are skipped; note_search_content
# (the FTS5 content shadow = the indexed text) IS exported so --restore can
# ('rebuild') the index.
SQLITE_PARQUET_TABLES = [
    "entities",
    "graph_edges",
    "events",
    "quotes",
    "entity_tags",
    "company_metrics",
    "company_embeddings",
    "graph_analytics",
    "db_meta",
    "note_search_content",
]

# Default Parquet snapshot location (git-tracked; see DEFAULT_PARQUET).
PARQUET_DUCKDB_DIR = "snapshots/parquet/duckdb"
PARQUET_SQLITE_DIR = "snapshots/parquet/sqlite"


def _list_duckdb_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Return the names of all BASE TABLEs in the DuckDB file."""
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def _list_sqlite_tables(con: sqlite3.Connection) -> list[str]:
    """Return the names of all data tables (skip FTS5 *derived* shadows).

    ``note_search_content`` is deliberately KEPT: for a regular (non
    external-content) FTS5 table it holds the indexed column values, so a
    Parquet restore can regenerate the index via ``('rebuild')``. The
    derived shadows (``_data``/``_idx``/``_docsize``/``_config``) are
    excluded — they are rebuilt, not data.
    """
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' "
        "AND NOT (name LIKE 'note_search%' AND name != 'note_search_content') "
        "AND name NOT LIKE 'entities_fuzzy%' "
        "AND sql NOT LIKE '%VIRTUAL TABLE%fts5%' "
        "ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def _export_sqlite_schema(con: sqlite3.Connection) -> str:
    """Replayable DDL for a Parquet restore: base tables, FTS5 virtual
    tables, indexes, views, triggers — in dependency-safe creation order
    (``sqlite_master`` rowid ≈ creation order), excluding derived FTS5
    shadows and spellfix leftovers (regenerated / not wanted).
    """
    def stmts(kind: str, where: str = "") -> list[str]:
        rows = con.execute(
            "SELECT sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL "  # noqa: S608  # `where` is a hardcoded constant from callers below
            f"AND name NOT LIKE 'sqlite_%' {where} ORDER BY rowid",
            (kind,),
        ).fetchall()
        return [r[0].rstrip().rstrip(";") + ";" for r in rows]

    # FTS5 virtual tables FIRST: ``CREATE VIRTUAL TABLE ... USING fts5``
    # creates every shadow table (incl. ``note_search_content``) itself, so
    # the plain-table pass must not try to create them again.
    # A1: vec0 virtual tables are excluded too — derived state (rebuilt by
    # rebuild_note_search / lazily on first hybrid search), like FTS shadows.
    # Since the A1-regression fix (2026-08-18) the vec0 table lives in a
    # SIDECAR db (research.db_vec.db), not research.db, so this filter is a
    # belt-and-braces guard against it ever migrating back (a vec0 table in
    # research.db breaks DuckDB's ATTACH catalog scan — see vec_search.py).
    vtables = stmts(
        "table",
        "AND sql LIKE '%VIRTUAL TABLE%' "
        "AND name NOT LIKE 'entities_fuzzy%' "
        "AND sql NOT LIKE '%vec0%'",
    )
    base_tables = stmts(
        "table",
        "AND sql NOT LIKE '%VIRTUAL TABLE%' "
        "AND name NOT LIKE 'note_search%' "
        "AND name NOT LIKE 'entities_fuzzy%'",
    )
    # ``sql IS NOT NULL`` drops sqlite_autoindex_* (auto-created) — good.
    parts = (
        ["PRAGMA foreign_keys=OFF;"]
        + vtables
        + base_tables
        + stmts("index")
        + stmts("view")
        + stmts("trigger")
    )
    return "\n\n".join(parts) + "\n"


def _export_duckdb_schema(con: duckdb.DuckDBPyConnection,
                          only_tables: frozenset[str] | None = None) -> str:
    """Replayable DuckDB DDL: base tables then views, in creation order
    (oid order) so FK/view dependencies are satisfied on replay.

    ``only_tables`` (the materialisation manifest) filters the base-table
    pass: stray scratch tables stay out of the schema listing exactly as
    they stay out of the Parquet export."""
    parts: list[str] = []
    for (tname, ddl) in con.execute(
        "SELECT table_name, sql FROM duckdb_tables() "
        "WHERE NOT internal ORDER BY table_oid"
    ).fetchall():
        if only_tables is not None and tname not in only_tables:
            continue
        parts.append(ddl.rstrip().rstrip(";") + ";")
    for (sql,) in con.execute(
        "SELECT sql FROM duckdb_views() WHERE NOT internal AND sql IS NOT NULL "
        "ORDER BY view_oid"
    ).fetchall():
        parts.append(sql.rstrip().rstrip(";") + ";")
    return "\n\n".join(parts) + "\n"


def export_parquet_duckdb(
    duckdb_path: Path, out_dir: Path, logger: logging.Logger
) -> dict:
    """Export every materialised DuckDB table to an individual Parquet file.

    Uses DuckDB's native ``COPY ... TO ... (FORMAT PARQUET)`` which writes
    columnar Parquet with correct type information (BIGINT, VARCHAR, DOUBLE,
    DATE, etc.). The resulting files are readable by pandas, polars, pyarrow,
    DuckDB, and any BI tool without needing DuckDB installed.
    """
    if not duckdb_path.exists():
        logger.info(f"DuckDB file not found, skipping Parquet export: {duckdb_path}")
        return {"skipped": True}

    import duckdb

    out_dir.mkdir(parents=True, exist_ok=True)
    # Lazy import: keeps the module import light and avoids any import
    # cycle during test collection.
    from helpers.graph.query import MATERIALISED_TABLES

    # Single read-only connection; COPY on base tables needs no extensions.
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        tables = _list_duckdb_tables(con)
        # Filter to only data tables (skip internal/system tables; the
        # __duckpgq_internal tables no longer exist post-retirement, but the
        # guard also covers any future internal prefixes).
        tables = [t for t in tables if not t.startswith("__")]
        # Manifest guard (2026-08-21): stray scratch tables are NEVER
        # snapshotted — skipping them keeps the git-tracked artifacts
        # canonical, and the WARNING is the signal to drop the table (or
        # extend the manifest when a new materialised table legitimately
        # lands).
        stray = [t for t in tables if t not in MATERIALISED_TABLES]
        if stray:
            logger.warning(
                "DuckDB table(s) outside the materialisation manifest are "
                "NOT snapshotted — drop them, or extend "
                "MATERIALISED_TABLES in helpers/graph/query.py if they are "
                "real: %s", ", ".join(sorted(stray))
            )
        tables = [t for t in tables if t in MATERIALISED_TABLES]

        # Replayable DDL (tables + views, in dependency-safe order), with
        # the same manifest filter so a stray table's CREATE statement
        # can't leak into _schema.duckdb.sql either.
        schema_path = out_dir.parent / "_schema.duckdb.sql"
        schema_path.write_text(
            _export_duckdb_schema(con, only_tables=MATERIALISED_TABLES)
        )
        logger.info(f"  Parquet DuckDB: schema DDL → {schema_path}")

        results: dict[str, dict] = {}
        total_bytes = 0
        for t in tables:
            out_path = out_dir / f"{t}.parquet"
            # ORDER BY ALL (maint_full_zero_churn F4): without it the COPY
            # leaks the table's physical row order into the blob bytes, so a
            # rebuild that left content set-identical still churned every
            # e_* parquet (2026-08-22 audit: e_belongs/e_has reordered).
            # Canonical column order makes the export a pure function of
            # content; the verify path is row-count based, so this is
            # round-trip neutral.
            con.execute(
                f"COPY (SELECT * FROM {t} ORDER BY ALL) TO '{out_path}' (FORMAT PARQUET)"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            )
            sz = out_path.stat().st_size
            total_bytes += sz
            results[t] = {"bytes": sz}
            logger.info(f"  Parquet DuckDB: {t}.parquet ({sz:,} bytes)")
    finally:
        con.close()

    logger.info(
        f"Parquet DuckDB export: {len(results)} tables, "
        f"{total_bytes:,} bytes total → {out_dir}"
    )
    return {"tables": results, "total_bytes": total_bytes, "dir": str(out_dir)}


def export_parquet_sqlite(
    sqlite_path: Path, out_dir: Path, logger: logging.Logger
) -> dict:
    """Export SQLite data tables to individual Parquet files.

    Uses ``sqlite3`` + ``pyarrow`` (via pandas) to avoid DuckDB's strict
    timestamp parsing on some legacy rows. Columns retain their SQLite TEXT
    types as UTF-8 strings in Parquet — the caller can cast as needed.
    """
    if not sqlite_path.exists():
        logger.info(f"SQLite DB not found, skipping Parquet export: {sqlite_path}")
        return {"skipped": True}

    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(sqlite_path))
    try:
        tables = _list_sqlite_tables(con)
    except Exception:
        tables = list(SQLITE_PARQUET_TABLES)

    # Capture replayable DDL next to the data so the snapshot restores
    # without any schema knowledge baked into this script.
    schema_path = out_dir.parent / "_schema.sqlite.sql"
    schema_path.write_text(_export_sqlite_schema(con))
    logger.info(f"  Parquet SQLite: schema DDL → {schema_path}")

    results: dict[str, dict] = {}
    total_bytes = 0
    for t in tables:
        try:
            df = pd.read_sql(f"SELECT * FROM [{t}]", con)  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        except Exception as e:
            logger.warning(f"  Parquet SQLite: skipped {t} ({e})")
            continue
        out_path = out_dir / f"{t}.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        # gzip codec: the SQLite tables are TEXT/BLOB-heavy (note text,
        # embeddings) and snappy leaves them ~3x larger than needed for a
        # git-tracked artifact.
        pq.write_table(table, out_path, compression="gzip")
        sz = out_path.stat().st_size
        total_bytes += sz
        results[t] = {"bytes": sz, "rows": len(df)}
        logger.info(f"  Parquet SQLite: {t}.parquet ({sz:,} bytes, {len(df)} rows)")

    con.close()
    logger.info(
        f"Parquet SQLite export: {len(results)} tables, "
        f"{total_bytes:,} bytes total → {out_dir}"
    )
    return {"tables": results, "total_bytes": total_bytes, "dir": str(out_dir)}


def verify_parquet_snapshot(  # noqa: C901
    parquet_dir: Path,
    duckdb_path: Path | None,
    sqlite_path: Path | None,
    logger: logging.Logger,
) -> dict:
    """Verify Parquet files round-trip: read each back and compare row counts
    against the source databases.

    DuckDB Parquet files are read back via DuckDB's native Parquet scanner.
    SQLite Parquet files are read back via pyarrow.
    """
    import pyarrow.parquet as pq

    result: dict = {"tables_checked": 0, "mismatches": [], "match": True}

    # --- DuckDB Parquet verify ---
    if duckdb_path and duckdb_path.exists():
        duckdb_pq_dir = parquet_dir / "duckdb"
        if duckdb_pq_dir.exists():
            import duckdb
            con = duckdb.connect(str(duckdb_path), read_only=True)
            try:
                pq_files = sorted(duckdb_pq_dir.glob("*.parquet"))
                for pf in pq_files:
                    tname = pf.stem
                    try:
                        _row = con.execute(
                            f"SELECT COUNT(*) FROM {tname}"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                        ).fetchone()
                        src_cnt = _row[0] if _row is not None else 0
                    except Exception:  # noqa: S112  # best-effort; skip item on failure
                        continue  # table may not exist in this version
                    _row = con.execute(
                        f"SELECT COUNT(*) FROM '{pf}'"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                    ).fetchone()
                    snap_cnt = _row[0] if _row is not None else 0
                    result["tables_checked"] += 1
                    if src_cnt != snap_cnt:
                        result["mismatches"].append(
                            f"duckdb/{tname}: {snap_cnt}/{src_cnt}"
                        )
            finally:
                con.close()

    # --- SQLite Parquet verify ---
    if sqlite_path and sqlite_path.exists():
        sqlite_pq_dir = parquet_dir / "sqlite"
        if sqlite_pq_dir.exists():
            scon = sqlite3.connect(str(sqlite_path))
            try:
                pq_files = sorted(sqlite_pq_dir.glob("*.parquet"))
                for pf in pq_files:
                    tname = pf.stem
                    try:
                        src_cnt = scon.execute(
                            f"SELECT COUNT(*) FROM [{tname}]"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                        ).fetchone()[0]
                    except Exception:  # noqa: S112  # best-effort; skip item on failure
                        continue
                    snap_cnt = pq.read_metadata(pf).num_rows
                    result["tables_checked"] += 1
                    if src_cnt != snap_cnt:
                        result["mismatches"].append(
                            f"sqlite/{tname}: {snap_cnt}/{src_cnt}"
                        )
            finally:
                scon.close()

    if result["mismatches"]:
        result["match"] = False
        for m in result["mismatches"]:
            logger.warning(f"  Parquet MISMATCH: {m}")
    else:
        logger.info(
            f"Parquet verify: {result['tables_checked']} tables checked, all OK"
        )
    return result


# ---------------------------------------------------------------------------
# Restore: rebuild the live databases from the git-tracked Parquet snapshot
# ---------------------------------------------------------------------------


def _parquet_rows(path: Path) -> tuple[list[str], list[tuple]]:
    """Columns + row tuples from one .parquet file (NaN → NULL)."""
    import pandas as pd

    df = pd.read_parquet(path)
    cols = list(df.columns)
    df = df.astype(object).where(pd.notna(df), None)
    return cols, list(df.itertuples(index=False, name=None))


def restore_sqlite_from_parquet(
    parquet_dir: Path, target: Path, logger: logging.Logger
) -> dict:
    """Rebuild a SQLite DB at ``target`` from ``parquet_dir``/*.parquet.

    Applies ``_schema.sqlite.sql`` (from the snapshot dir's parent), loads
    every Parquet file, rebuilds the FTS5 index from its exported content
    shadow, runs ``PRAGMA foreign_key_check``, then atomically replaces
    ``target``. The caller is responsible for any overwrite guard.
    """
    schema_path = parquet_dir.parent / "_schema.sqlite.sql"
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema DDL not found next to the Parquet dir: {schema_path}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restore-tmp")
    tmp.unlink(missing_ok=True)

    con = sqlite3.connect(str(tmp))
    restored: dict[str, int] = {}
    try:
        con.executescript(schema_path.read_text())
        # executescript() commits and leaves scripted pragmas behind; the
        # load itself must not enforce FKs (table order is alphabetical).
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("PRAGMA journal_mode=OFF")  # fresh build: max speed
        for pf in sorted(parquet_dir.glob("*.parquet")):
            tname = pf.stem
            cols, rows = _parquet_rows(pf)
            if not cols:
                continue
            collist = ", ".join(f"[{c}]" for c in cols)
            marks = ", ".join("?" * len(cols))
            con.executemany(
                f"INSERT INTO [{tname}] ({collist}) VALUES ({marks})",  # noqa: S608  # identifiers come from the snapshot's own file names
                rows,
            )
            restored[tname] = len(rows)
            logger.info(f"  Restore SQLite: {tname} ← {len(rows):,} rows")
        # Regenerate the FTS5 index from its exported content shadow.
        if con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='note_search'"
        ).fetchone():
            con.execute("INSERT INTO note_search(note_search) VALUES('rebuild')")
            logger.info("  Restore SQLite: FTS5 note_search index rebuilt")
        con.commit()
        fk_issues = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk_issues:
            raise sqlite3.IntegrityError(
                f"foreign_key_check failed after restore: {fk_issues[:5]}"
            )
    finally:
        con.close()

    tmp.replace(target)
    logger.info(
        f"Restore SQLite: {target} ({len(restored)} tables, "
        f"{sum(restored.values()):,} rows)"
    )
    return {"target": str(target), "tables": restored}


def restore_duckdb_from_parquet(
    parquet_dir: Path, target: Path, logger: logging.Logger
) -> dict:
    """Rebuild a DuckDB file at ``target`` from ``parquet_dir``/*.parquet.

    Applies ``_schema.duckdb.sql`` (creation-ordered tables then views),
    bulk-loads each Parquet via ``read_parquet``, checkpoints, then
    atomically replaces ``target``.
    """
    import duckdb

    schema_path = parquet_dir.parent / "_schema.duckdb.sql"
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema DDL not found next to the Parquet dir: {schema_path}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restore-tmp")
    for stale in (tmp, Path(str(tmp) + ".wal")):
        stale.unlink(missing_ok=True)

    con = duckdb.connect(str(tmp))
    restored: dict[str, int] = {}
    try:
        con.execute(schema_path.read_text())
        for pf in sorted(parquet_dir.glob("*.parquet")):
            tname = pf.stem
            con.execute(
                f"INSERT INTO {tname} SELECT * FROM read_parquet('{pf}')"  # noqa: S608  # identifiers come from the snapshot's own file names
            )
            row = con.execute(
                f"SELECT COUNT(*) FROM {tname}"  # noqa: S608  # identifiers come from the snapshot's own file names
            ).fetchone()
            if row is None:  # COUNT(*) always returns a row
                raise RuntimeError(f"COUNT(*) returned no row for {tname}")
            cnt = row[0]
            restored[tname] = cnt
            logger.info(f"  Restore DuckDB: {tname} ← {cnt:,} rows")
        con.execute("CHECKPOINT")
    finally:
        con.close()

    tmp.replace(target)
    logger.info(
        f"Restore DuckDB: {target} ({len(restored)} tables, "
        f"{sum(restored.values()):,} rows)"
    )
    return {"target": str(target), "tables": restored}


def _cmd_restore(
    db_path: Path,
    duckdb_path: Path,
    parquet_sqlite_dir: Path,
    parquet_duckdb_dir: Path,
    with_duckdb: bool,
    force: bool,
    logger: logging.Logger,
) -> int:
    """--restore: rebuild live DBs from the git-tracked Parquet snapshot."""
    if db_path.exists() and not force:
        logger.error(f"Refusing to overwrite existing {db_path} — pass --force")
        return 1
    if with_duckdb and duckdb_path.exists() and not force:
        logger.error(
            f"Refusing to overwrite existing {duckdb_path} — pass --force"
        )
        return 1
    if parquet_sqlite_dir.exists():
        restore_sqlite_from_parquet(parquet_sqlite_dir, db_path, logger)
    else:
        logger.error(f"Parquet snapshot not found: {parquet_sqlite_dir}")
        return 1
    if with_duckdb:
        if parquet_duckdb_dir.exists():
            restore_duckdb_from_parquet(parquet_duckdb_dir, duckdb_path, logger)
        else:
            logger.warning(
                f"DuckDB Parquet snapshot not found: {parquet_duckdb_dir}"
            )
    return 0


def _cmd_check(
    out_path: Path,
    db_path: Path,
    duckdb_out: Path,
    duckdb_path: Path,
    parquet_base: Path,
    with_duckdb: bool,
    logger: logging.Logger,
) -> int:
    """--check: verify gzip binary + Parquet snapshots round-trip."""
    # --check ALWAYS verifies both formats (gzip binary + Parquet),
    # regardless of --format (which only gates the create path).
    # Each verify gracefully skips whatever isn't present.
    ok = True
    r = verify_snapshot(out_path, db_path, logger)
    ok = ok and r["match"]
    if with_duckdb:
        rd = verify_duckdb_snapshot(duckdb_out, duckdb_path, logger)
        ok = ok and rd["match"]
    rp = verify_parquet_snapshot(
        parquet_base,
        duckdb_path if with_duckdb else None,
        db_path,
        logger,
    )
    ok = ok and rp["match"]
    return 0 if ok else 1


def _cmd_create(
    db_path: Path,
    out_path: Path,
    duckdb_path: Path,
    duckdb_out: Path,
    parquet_base: Path,
    parquet_sqlite_dir: Path,
    parquet_duckdb_dir: Path,
    fmt: str,
    with_duckdb: bool,
    logger: logging.Logger,
) -> int:
    """Default: create the snapshot (binary / parquet / both) and verify."""
    ok = True

    # --- Binary (gzip) snapshot ---
    if fmt in ("binary", "both"):
        create_snapshot(db_path, out_path, logger)
        r = verify_snapshot(out_path, db_path, logger)
        ok = ok and r["match"]
        if with_duckdb:
            create_duckdb_snapshot(duckdb_path, duckdb_out, logger)
            rd = verify_duckdb_snapshot(duckdb_out, duckdb_path, logger)
            ok = ok and rd["match"]

    # --- Parquet snapshot ---
    if fmt in ("parquet", "both"):
        export_parquet_sqlite(db_path, parquet_sqlite_dir, logger)
        if with_duckdb:
            export_parquet_duckdb(duckdb_path, parquet_duckdb_dir, logger)
        rp = verify_parquet_snapshot(
            parquet_base,
            duckdb_path if with_duckdb else None,
            db_path,
            logger,
        )
        ok = ok and rp["match"]

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Versioned snapshot of the research DB + DuckDB graph cache: "
            "git-tracked Parquet under snapshots/ (restorable via --restore) "
            "+ local gzip copies under db-backup/."
        )
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB, help="Source SQLite DB (relative to repo root)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output .db.gz (relative to repo root)."
    )
    parser.add_argument(
        "--duckdb", default=DEFAULT_DUCKDB,
        help="Source DuckDB file (relative to repo root).",
    )
    parser.add_argument(
        "--duckdb-out", default=DEFAULT_DUCKDB_OUT,
        help="Output DuckDB .duckdb.gz (relative to repo root).",
    )
    parser.add_argument(
        "--no-duckdb", dest="with_duckdb", action="store_false",
        help="Skip the DuckDB snapshot (SQLite only).",
    )
    parser.set_defaults(with_duckdb=True)
    parser.add_argument(
        "--format", choices=["binary", "parquet", "both"], default="both",
        help="Snapshot format for CREATE: binary (gzip .db.gz), parquet "
             "(per-table .parquet), or both. Default: both. "
             "--check always verifies both formats regardless of this flag.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify an existing snapshot round-trips against the source. "
             "Always checks BOTH formats (gzip binary + Parquet when present); "
             "--format only gates the create path.",
    )
    parser.add_argument(
        "--parquet-dir", default=DEFAULT_PARQUET,
        help="Root dir for the Parquet snapshot (git-tracked default: "
             "snapshots/parquet).",
    )
    parser.add_argument(
        "--restore", action="store_true",
        help="Rebuild the LIVE databases from the Parquet snapshot "
             "(schema DDL + data + FTS5 rebuild). Refuses to overwrite "
             "existing live files unless --force.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="With --restore: overwrite existing live DB files.",
    )
    parser.add_argument("--log", default="INFO", help="Logging level.")
    args = parser.parse_args()

    log_level = getattr(logging, args.log.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format=LOG_FORMAT)
    logger = logging.getLogger("snapshot_db")

    root = _compute_root()
    db_path = Path(args.db)
    db_path = db_path if db_path.is_absolute() else root / db_path
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else root / out_path
    duckdb_path = Path(args.duckdb)
    duckdb_path = duckdb_path if duckdb_path.is_absolute() else root / duckdb_path
    duckdb_out = Path(args.duckdb_out)
    duckdb_out = duckdb_out if duckdb_out.is_absolute() else root / duckdb_out
    parquet_path = Path(args.parquet_dir)
    parquet_base = parquet_path if parquet_path.is_absolute() else root / parquet_path
    parquet_duckdb_dir = parquet_base / "duckdb"
    parquet_sqlite_dir = parquet_base / "sqlite"

    try:
        if args.restore:
            return _cmd_restore(
                db_path, duckdb_path, parquet_sqlite_dir, parquet_duckdb_dir,
                args.with_duckdb, args.force, logger,
            )
        if args.check:
            return _cmd_check(
                out_path, db_path, duckdb_out, duckdb_path, parquet_base,
                args.with_duckdb, logger,
            )
        return _cmd_create(
            db_path, out_path, duckdb_path, duckdb_out, parquet_base,
            parquet_sqlite_dir, parquet_duckdb_dir, args.format,
            args.with_duckdb, logger,
        )
    except Exception as e:  # pragma: no cover
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
