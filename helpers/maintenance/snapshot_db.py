#!/usr/bin/env python3
"""
Versioned snapshot of memory/research.db (+ memory/graph.duckdb).

S6 cutover note (2026-11-21): the canonical research store is now
memory/research.duckdb (``--db memory/research.duckdb`` takes the S4
DuckDB snapshot/verify branches). The DEFAULT ``--db`` stays on the
SQLite rollback anchor so ``--restore`` keeps its SQLite-schema contract.
The git-tracked ``snapshots/parquet/sqlite/`` axis keeps that name and
schema, but (parquet slice, 2026-11-21) its DATA source now resolves
through the S6 selector — canonical DuckDB by default, SQLite under
``FINDATA_RESEARCH_ENGINE=sqlite`` — via ``_resolve_research_source``.
The frozen-at-cutover state is gone once the tracked corpus is refreshed
from the canonical.

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

   Two large derived artifacts are exported but deliberately NOT
   git-tracked (GitHub warns at 50 MB and blocks at 100 MB per file;
   both are re-exported on every local db-sync anyway):
   - ``sqlite/note_search_content.parquet`` mirrors the FTS-indexed
     text of ``findata/**`` — the corpus is the source of truth; on a
     fresh clone rebuild via ``rebuild_note_search.py`` (``make maint``
     step 0c).
   - ``duckdb/v_note_embeddings.parquet`` — regenerate via the embed
     pipeline (``make search-fresh APPLY=1``).

2. zstd binary — LOCAL-ONLY byte-exact copies under ``db-backup/`` (gitignored).
   The SQLite DB runs in WAL mode, so a naive ``cp research.db`` would miss
   the ``-wal`` file and yield a stale/corrupt copy. This uses SQLite's
   online backup API, which produces a transactionally consistent,
   self-contained file with all committed WAL data merged in, then
   zstd-compresses it (stdlib compression.zstd, library-default level;
   zstd_binary_backups.md). The DuckDB file is checkpointed before copying.
   Fastest disaster recovery (``gunzip -c ... > memory/research.db``), but
   binary DBs churn badly in git — hence parquet-for-checkins.

Both ``memory/`` (live DBs) and ``db-backup/`` (compression scratch) are
gitignored; ``snapshots/parquet/`` is the committed, restorable state.

Usage:
  python3 helpers/maintenance/snapshot_db.py            # snapshot zstd + Parquet (both formats) + verify
  python3 helpers/maintenance/snapshot_db.py --check     # verify existing snapshots (BOTH formats)
  python3 helpers/maintenance/snapshot_db.py --no-duckdb # SQLite only
  python3 helpers/maintenance/snapshot_db.py --format binary   # zstd .db.zst only
  python3 helpers/maintenance/snapshot_db.py --format parquet  # Parquet only (git checkin path)

Restore (from the git-tracked Parquet snapshot):
  make snapshot-restore        # or: python3 helpers/maintenance/snapshot_db.py --restore [--force]
  Rebuilds memory/research.db + memory/graph.duckdb from snapshots/parquet/:
  apply schema DDL → load every .parquet → FTS5 ('rebuild') → integrity
  checks → atomic replace of the live files. Refuses to overwrite an
  existing live DB unless --force.

  (Local alternative: zstd -dc db-backup/research.snapshot.db.zst
  > memory/research.db)

See also: ``helpers/maintenance/maint.py`` — the orchestrator that runs
db_maint → snapshot_db → graph-rebuild in the right order. Prefer
``make maint`` over invoking this script directly when you want both
the SQLite VACUUM and the snapshot refreshed together.
"""

import argparse
import logging
import os
import re
import queue
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

# `duckdb` is an optional dependency (imported lazily where it is actually
# used). It appears at module scope only inside the string annotation of
# _list_duckdb_tables(); guard it under TYPE_CHECKING so static tools can
# resolve the name without forcing a runtime import (matches the lazy-import
# discipline used elsewhere in this module).
if TYPE_CHECKING:
    import duckdb
    import pyarrow as pa


class ParquetExportError(RuntimeError):
    """Fail-loud parquet export error (bulk_data_lanes S1): a SQLite
    column carries mixed storage classes that pa.array refuses to
    silently re-type. The message carries a typeof() diagnostic."""


# Repo root on sys.path so the lazy `from helpers.graph.query import ...`
# inside verify_duckdb_snapshot (Bundle O2) resolves when this script is run
# directly (``python3 helpers/maintenance/snapshot_db.py``) rather than as
# a module. Matches the backfill_valid_from.py / move_sector.py precedent.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core import duckdb_shared as ds  # noqa: E402
from helpers.core.db import _sqlite_connect  # noqa: E402  (post-bootstrap: entry point, lazy-import convention; S1: _sqlite_connect bypasses the selector dispatch)
from helpers.core.db_engine import ResearchEngine, is_research_store_path  # noqa: E402
from helpers.core.env import REPO_ROOT  # noqa: E402  (folds _compute_root)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DEFAULT_DB = "memory/research.db"
DEFAULT_OUT = "db-backup/research.snapshot.db.zst"

# Parquet slice (parquet_snapshot_duckdb_reader.md S2–S4): the tracked
# ``snapshots/parquet/sqlite/`` axis keeps its SQLite-SCHEMA restore
# contract, but its DATA source follows the S6 selector. ``--db`` and
# ``DEFAULT_DB`` stay the SQLite rollback anchor (no bare re-point — that
# would break verify/restore): only the parquet export/verify legs resolve
# the research store through the gate below.


def _resolve_research_source(db_path: Path) -> Path:
    """Parquet-axis source through the S6 selector gate.

    Under the duckdb-first default a research-store ``.db`` resolves to its
    converted ``.duckdb`` sibling when it exists; an explicit ``ENGINE=sqlite``
    keeps the rollback anchor. The ``is_research_store_path`` gate means a
    tmp fixture named ``research.db`` (or a csr ``--db`` path) is NEVER
    hijacked onto another lane's cache. Non-research paths are unchanged.
    """
    sel = ResearchEngine.from_env()
    resolved = sel.resolve_path(db_path)
    if (
        is_research_store_path(db_path)
        and resolved != db_path
        and resolved.suffix.lower() == ".duckdb"
        and resolved.exists()
    ):
        return resolved
    return db_path


DEFAULT_DUCKDB = "memory/graph.duckdb"
# D15 (2026-09-15): the SECOND DuckDB — external-source acquisition store.
# Its manifest lives HERE, deliberately not in helpers/graph/query.py:
# MATERIALISED_TABLES means "tables the graph materialisation owns" (the
# graph.duckdb drop pass clobbers by that manifest); sources.duckdb is
# primary external data with its own lifecycle.
DEFAULT_SOURCES_DUCKDB = "memory/data/sources.duckdb"
SOURCES_TABLES = frozenset(
    {
        "exchange_listings",
        "mca_cin",
        "index_constituents",
        # sync-lane products (exchange_sync / related_party_sync /
        # shareholding_sync) — surfaced 2026-10-12 when the snapshot's
        # fail-closed verify refused to drop ~370k live rows
        "credit_ratings",
        "rpt_transactions",
        "shp_filings",
        "shp_holders",
    }
)
# note_search carve-out S1: the live note index is the DuckDB sidecar
# (memory/note_search.duckdb) since 2026-11-16; its FTS5 shadow in
# research.db is the legacy rollback artifact. The sidecar's base tables
# are the snapshot's canonical note leg; the native FTS index
# (fts_main_note_search) is derived state, rebuilt by
# rebuild_note_search.py, and excluded like the fts_main_* shadows.
DEFAULT_NOTE_DUCKDB = "memory/note_search.duckdb"
NOTE_SEARCH_TABLES = frozenset(
    {
        "note_search",
        "note_search_meta",
        "note_search_info",
    }
)
DEFAULT_DUCKDB_OUT = "db-backup/graph.snapshot.duckdb.zst"
# Git-tracked, restoreable Parquet snapshot (see module docstring).
DEFAULT_PARQUET = "snapshots/parquet"


def create_snapshot(db_path: Path, out_path: Path, logger: logging.Logger) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # S4 (duckdb_research_swap_execution): a `.duckdb` research store takes
    # the first-party snapshot branch (no sqlite online-backup API there).
    if db_path.suffix.lower() == ".duckdb":
        return _create_snapshot_duckdb(db_path, out_path, logger)

    # Online backup into a temp file: consistent, WAL-merged, self-contained.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        # S1 (parquet_snapshot_duckdb_reader.md): binary snapshot sources/dests
        # are SQLite by contract — bypass the selector dispatch, which would
        # redirect a research-store path to the DuckDB canonical.
        src = _sqlite_connect(db_path, read_only=True)
        # wal=False: PRAGMA journal_mode=WAL on the fresh empty dest breaks
        # the sqlite online-backup API into it ("attempt to write a readonly
        # database" — verified 2026-09-03). All other connect() pragmas are
        # backup-safe; WAL is set nowhere downstream of this temp file.
        dest = _sqlite_connect(tmp_path, wal=False)
        try:
            with dest:
                src.backup(dest)
        finally:
            dest.close()
            src.close()

        raw = tmp_path.stat().st_size
        # Codec: zstd (stdlib, library-default level; zstd_binary_backups.md)
        # — LOCAL-ONLY recovery copies (gitignored); zstd beats gzip -9 ~3x
        # on speed AND ~10% on size at this file size.
        from helpers.core.zstd_io import compress_file

        compress_file(tmp_path, out_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    gz = out_path.stat().st_size
    logger.info(f"Snapshot: {out_path} ({gz:,} bytes compressed, {raw:,} bytes source)")
    return {"snapshot": str(out_path), "compressed_bytes": gz, "source_bytes": raw}


def _create_snapshot_duckdb(db_path: Path, out_path: Path, logger: logging.Logger) -> dict:
    """S4: DuckDB research-store snapshot — checkpoint-then-copy + zstd.

    Same safe pattern as db_maint._duckdb_zstd_backup: DuckDB has no
    online-backup API, so open read-only under the store's io.lock (SH),
    force CHECKPOINT (flush the WAL), close, and copy the quiescent file.
    A store the quack server holds RW (served) is a normal busy — logged
    and skipped, never a failed snapshot run.
    """
    import shutil

    import duckdb

    from helpers.core.duckdb_shared import memory_cap_config
    from helpers.misc.duckdb_lock import io_lock as _io_lock

    try:
        with _io_lock(db_path, exclusive=False):
            con = duckdb.connect(str(db_path), read_only=True, **memory_cap_config())
            try:
                con.execute("CHECKPOINT;")
            except duckdb.Error as e:
                logger.warning(f"read-only CHECKPOINT failed ({e}); copying as-is")
            finally:
                con.close()
    except (duckdb.Error, OSError) as e:
        logger.warning(f"{db_path} busy/served ({e}) — snapshot skipped")
        return {"snapshot": str(out_path), "skipped": "served/busy"}
    from helpers.core.zstd_io import compress_file

    raw = db_path.stat().st_size
    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        shutil.copy2(db_path, tmp_path)
        compress_file(tmp_path, out_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    gz = out_path.stat().st_size
    logger.info(f"Snapshot: {out_path} ({gz:,} bytes compressed, {raw:,} bytes source)")
    return {"snapshot": str(out_path), "compressed_bytes": gz, "source_bytes": raw}


def _verify_snapshot_duckdb(
    snapshot_path: Path, source_db: Path | None, logger: logging.Logger
) -> dict:
    """S4: round-trip check for a `.duckdb` research-store snapshot.

    Decompress, open read-only, run the first-party quick_check (DuckDB has
    no PRAGMA integrity_check) and compare entities/relations row counts to
    the source — the same contract as the SQLite verify path.
    """
    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        from helpers.core.zstd_io import decompress_file

        decompress_file(snapshot_path, tmp_path)
        import duckdb

        from helpers.core import db_engine as _db_engine
        from helpers.core.duckdb_shared import memory_cap_config

        conn = duckdb.connect(str(tmp_path), read_only=True, **memory_cap_config())
        try:
            integrity_list = _db_engine.quick_check(conn)
            integrity = "ok" if integrity_list == ["ok"] else "; ".join(integrity_list)
            snap_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]  # ty: ignore[not-subscriptable]  # COUNT always returns a row
            snap_relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]  # ty: ignore[not-subscriptable]  # COUNT always returns a row
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
        try:
            # Slice B funnel: a served store excludes file-mode opens —
            # route through the wire attach (or io_lock ladder when idle).
            from helpers.misc.duckdb_lock import open_read_only as _oro

            sconn = _oro(str(source_db))
        except (duckdb.Error, OSError) as e:
            # Served/busy source: the RW-locked live store cannot be opened
            # for the row-count compare — degrade to integrity-only match
            # with a truthful marker instead of failing the verify run.
            logger.warning(f"source {source_db} busy/served ({e}) — compare skipped")
            result["source_skipped"] = "served/busy"
            result["match"] = integrity == "ok"
            return result
        try:
            src_entities = sconn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]  # COUNT always returns a row
            src_relations = sconn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]  # COUNT always returns a row
        finally:
            sconn.close()
        result.update(source_entities=src_entities, source_relations=src_relations)
        ok = integrity == "ok" and snap_entities == src_entities and snap_relations == src_relations
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


def verify_snapshot(snapshot_path: Path, source_db: Path | None, logger: logging.Logger) -> dict:
    """Round-trip check: decompress the snapshot, open it, compare row counts to the source."""
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")
    # S4: a `.duckdb` source (or snapshot) takes the first-party verify
    # branch — the SQLite path below is PRAGMA-shaped.
    if (source_db and source_db.suffix.lower() == ".duckdb") or (
        source_db is None and ".duckdb" in snapshot_path.name
    ):
        return _verify_snapshot_duckdb(snapshot_path, source_db, logger)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        from helpers.core.zstd_io import decompress_file

        decompress_file(snapshot_path, tmp_path)

        conn = _sqlite_connect(tmp_path)
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
        sconn = _sqlite_connect(source_db, read_only=True)
        try:
            scur = sconn.cursor()
            scur.execute("SELECT COUNT(*) FROM entities")
            src_entities = scur.fetchone()[0]
            scur.execute("SELECT COUNT(*) FROM relations")
            src_relations = scur.fetchone()[0]
        finally:
            sconn.close()
        result.update(source_entities=src_entities, source_relations=src_relations)
        ok = integrity == "ok" and snap_entities == src_entities and snap_relations == src_relations
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


def create_duckdb_snapshot(duckdb_path: Path, out_path: Path, logger: logging.Logger) -> dict:
    """CHECKPOINT the DuckDB file (flush WAL), then zstd-copy to ``out_path``.

    DuckDB allows either one read-write OR many read-only connections to a
    file. We open read-only for the CHECKPOINT (which forces WAL merge
    even from a reader in recent DuckDB versions); on failure we fall
    back to copying the main file plus any WAL sidecar verbatim.

    Version-sensitive assumption: read-only CHECKPOINT relies on DuckDB
    ≥ 1.5 allowing a reader connection to flush the WAL. See
    doc/design/graph_design.md §9.3 (was §17.11, Bundle O3) for the full caveat + how to
    re-test on pin bumps; the fallback below degrades gracefully.

    Skips silently if the DuckDB file does not exist (returns
    ``{"skipped": True}``).
    """
    if not duckdb_path.exists():
        logger.info(f"DuckDB file not found, skipping: {duckdb_path}")
        return {"skipped": True}

    out_path.parent.mkdir(parents=True, exist_ok=True)

    checkpointed = True
    try:
        from helpers.misc.duckdb_lock import open_read_only

        # Slice B funnel: when the store is served the CHECKPOINT lands
        # over the wire attach (a file-mode open would collide with the
        # server's lock); unserved it is the io_lock + S1 ladder path.
        # duckdb 2.0 + quack: CHECKPOINT through a quack attach raises
        # "Checkpoint not implemented yet" (file-mode CHECKPOINT works in
        # 2.0 — verified both modes on a scratch db, 2026-10-24), so a
        # SERVED store always lands in the fallback below. That fallback
        # is complete, not lossy: the WAL sidecar ships alongside (see
        # below), so restore reassembles the un-checkpointed tail.
        con = open_read_only(str(duckdb_path))
        try:
            con.execute("CHECKPOINT;")
        finally:
            con.close()
    except Exception as e:
        # CHECKPOINT refused (2.0 quack-attach gap, or a version that
        # rejects read-only CHECKPOINT): fall back to a plain file copy.
        checkpointed = False
        if ds.is_served(duckdb_path):
            # permanent state under 2.0+quack — expected, not an alarm
            logger.info(
                "CHECKPOINT unavailable through the quack attach (2.0 gap); "
                "snapshot copies the file as-is — the WAL sidecar ships "
                "alongside, so the backup is complete"
            )
        else:
            logger.warning(f"read-only CHECKPOINT failed ({e}); copying file as-is")

    # zstd the main .duckdb file (library-default level; tiny file at
    # ~11 MB / 0.25 s even under gzip).
    from helpers.core.zstd_io import compress_file

    raw = duckdb_path.stat().st_size
    compress_file(duckdb_path, out_path)
    gz = out_path.stat().st_size
    logger.info(f"DuckDB snapshot: {out_path} ({gz:,} bytes compressed, {raw:,} bytes source)")
    # P2.4: also snapshot WAL sidecar if it exists (un-CHECKPOINTed tail).
    wal_path = duckdb_path.with_suffix(".duckdb.wal")
    if out_path.name.endswith(".duckdb.zst"):
        wal_out = out_path.parent / out_path.name.replace(".duckdb.zst", ".duckdb.wal.zst")
    else:
        wal_out = out_path.parent / (out_path.name + ".wal.zst")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        try:
            compress_file(wal_path, wal_out)
            logger.info(f"DuckDB WAL snapshot: {wal_out} ({wal_out.stat().st_size:,} bytes)")
        except Exception as e:
            logger.warning(f"DuckDB WAL snapshot failed for {wal_path}: {e}")
    else:
        if wal_out.exists():
            try:
                wal_out.unlink()
            except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
                pass
    return {
        "snapshot": str(out_path),
        "compressed_bytes": gz,
        "source_bytes": raw,
        "checkpointed": checkpointed,
    }


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
    had round-tripped. Extended 2026-09-10 (snapshot_trust_country_exposure
    S1): the list is ``helpers.graph.query.MATERIALISED_TABLES`` verbatim —
    28 tables — closing the gap where 15 materialised tables (incl. the
    v14 country layer) rode snapshots unverified.

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
    from helpers.graph.query import (
        EDGE_REGISTRY,
        EPHEMERAL_TABLES,
        MATERIALISED_TABLES,
        STAMP_OWNED_META_KEYS,
    )

    # Canonical list of materialised tables to verify: the manifest
    # frozenset verbatim, sorted for deterministic output. (Was: a
    # hardcoded ["v_node", "v_company", "v_sector"] + EDGE_REGISTRY list
    # that let 15 materialised tables — incl. the v14 country layer —
    # ride snapshots unverified; snapshot_trust_country_exposure S1,
    # 2026-09-10. The structural spot-check below stays EDGE_REGISTRY-
    # scoped: only those tables pin FK columns against v_node.id.)
    materialised_tables = sorted(MATERIALISED_TABLES)

    def _count_tables(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
        """Return ``{table_name: row_count}`` for every materialised table
        that exists in ``con``. Missing tables are omitted from the dict;
        the caller's set comparison flags missing-table regressions.
        ``_build_meta`` counts only keys the materialisation owns (S4,
        graph_perf_l1_bfs_scale): the centrality stamp's extra key is
        contract-legal drift on either side, not staleness."""
        counts: dict[str, int] = {}
        # Literal keys, not prepared params: when ``con`` is a wire attach
        # (Slice B) prepared parameters do not cross the wire, and the
        # failure would silently omit the table and flag a false mismatch.
        from helpers.core import duckdb_shared as _ds

        for t in materialised_tables:
            try:
                if t == "_build_meta" and STAMP_OWNED_META_KEYS:
                    lit = ", ".join(_ds.sql_lit(k) for k in STAMP_OWNED_META_KEYS)
                    _row = con.execute(
                        f"SELECT COUNT(*) FROM _build_meta WHERE key NOT IN ({lit}) AND key NOT LIKE 'fp:%'",  # noqa: S608  # literal keys are schema-constants; fp:* = graph_rebuild_fast_path S1 fingerprint drift (contract-legal)
                    ).fetchone()
                else:
                    _row = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608  # parameterized; interpolated parts are schema-constant identifiers
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
    snap_dbver = None
    src_gen = None
    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        from helpers.core.zstd_io import decompress_file

        decompress_file(snapshot_path, tmp_path)

        # Read-only is sufficient post-duckpgq-retirement: the structural
        # check is pure SELECTs (no DDL, no extension needed).
        con = duckdb.connect(str(tmp_path), read_only=True, **ds.memory_cap_config())
        try:
            snap_counts = _count_tables(con)
            snap_pg_ok = _property_graph_constructible(con)
            # P2.4: capture generation for staleness check
            try:
                _r = con.execute("SELECT value FROM _build_meta WHERE key='generation'").fetchone()
                snap_gen = _r[0] if _r else None
            except Exception:
                snap_gen = None
            # Bundle O3 (snapshot_trust_country_exposure S2): surface the
            # writer-version stamp so a pin bump that changed on-disk
            # semantics between build and verify is observable, not silent.
            try:
                _r = con.execute(
                    "SELECT value FROM _build_meta WHERE key='duckdb_version'"
                ).fetchone()
                snap_dbver = _r[0] if _r else None
            except Exception:
                snap_dbver = None
            if snap_dbver is not None and snap_dbver.lstrip("v") != duckdb.__version__:
                logger.warning(
                    f"DuckDB version drift: snapshot built on {snap_dbver}, "
                    f"verifying with {duckdb.__version__} (re-test drill: graph_design.md 9.3)"
                )
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
            # Slice B funnel: the source may be the LIVE served store —
            # a file-mode open would collide with the server's lock.
            from helpers.misc.duckdb_lock import open_read_only as _oro

            sconn = _oro(str(source_duckdb))
            try:
                src_counts = _count_tables(sconn)
                try:
                    _r2 = sconn.execute(
                        "SELECT value FROM _build_meta WHERE key='generation'"
                    ).fetchone()
                    src_gen = _r2[0] if _r2 else None
                except Exception:
                    src_gen = None
            finally:
                sconn.close()
        except duckdb.Error:
            src_counts = snap_counts
            src_gen = snap_gen

        # P2.4: generation must also match (O(1) staleness)
        gen_match = snap_gen == src_gen
        if snap_gen is not None or src_gen is not None:
            logger.info(
                f"DuckDB generation: snapshot={snap_gen} source={src_gen} -> {'OK' if gen_match else 'MISMATCH'}"
                + ("" if gen_match else " (stale snapshot — run make snapshot)")
            )
        # S4 (graph_perf_l1_bfs_scale): the ten v_centrality_* tables are
        # ephemeral in the snapshot contract — a data-only rebuild drops
        # them (absence == "compute on demand") and a pre-stamp snapshot
        # legitimately predates them. A one-side-only ephemeral is exempt
        # from the set/count comparison; present-on-both still must match.
        ephemeral_drift = sorted(
            t for t in EPHEMERAL_TABLES if (t in snap_counts) != (t in src_counts)
        )
        if ephemeral_drift:
            logger.info(
                "Ephemeral centrality tables absent on one side "
                "(contract-legal; make stamp-centrality restores them): %s",
                ", ".join(ephemeral_drift),
            )
        cmp_snap = {t: c for t, c in snap_counts.items() if t not in ephemeral_drift}
        cmp_src = {t: c for t, c in src_counts.items() if t not in ephemeral_drift}
        # Match requires: identical table set, identical row counts on every
        # (non-exempt) table, AND the snapshot's tables support pg
        # construction, AND generation match.
        same_tables = set(cmp_snap) == set(cmp_src)
        same_counts = cmp_snap == cmp_src
        ok = same_tables and same_counts and snap_pg_ok and gen_match

        result.update(source_tables=src_counts, match=ok)
        if snap_dbver is not None:
            result["duckdb_version"] = snap_dbver
            result["version_match"] = snap_dbver == duckdb.__version__
        # One-line summary: v_node + total edges + pg flag; per-table diffs
        # appended only when something disagrees (keeps logs scannable).
        snap_edges = sum(c for t, c in snap_counts.items() if t.startswith("e_"))
        src_edges = sum(c for t, c in src_counts.items() if t.startswith("e_"))
        diffs = {
            t: f"{cmp_snap.get(t, 'absent')}/{cmp_src.get(t, 'absent')}"
            for t in materialised_tables
            if cmp_snap.get(t) != cmp_src.get(t)
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
            f"Verify DuckDB: tables={snap_counts} pg={'ok' if snap_pg_ok else 'BAD'} (no source)"
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
    "hyper_edges",
    "hyper_incidences",
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
        # S3 (worktree_fresh_trample_guard): db_bump_log is operational
        # telemetry — a ring that grows on every real content change — so a
        # git-tracked parquet of it would sit under permanent drift (verify
        # compares live vs checked-in row counts, and every bump adds a row).
        # Not content: never exported. The schema DDL still carries the table,
        # so a restored index has it like any other.
        "AND name != 'db_bump_log' "
        # derived_name_columns_guard S2: the freshness marker records WHICH RUN
        # wrote a derived cohort (mode + entity-set fingerprint + timestamp), so
        # it changes on every derive while the cohort rows may not. Not research
        # content — exporting it would put a git-tracked parquet under permanent
        # drift for no informational gain (the snapshot's own generation counter
        # already covers "is this export current"). The restore path keeps the
        # table via the exported DDL; a restored store re-stamps on its first
        # `make derive-relational-insights`.
        "AND name != 'derived_cohort_meta' "
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
    # (A1's vector-extension exclusion guard was retired 2026-11-05,
    # duckdb_pure_stack S2: the vector mirror is a DuckDB sidecar now,
    # never a SQLite virtual table in research.db.)
    vtables = stmts(
        "table",
        "AND sql LIKE '%VIRTUAL TABLE%' AND name NOT LIKE 'entities_fuzzy%'",
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


def _export_duckdb_schema(
    con: duckdb.DuckDBPyConnection, only_tables: frozenset[str] | None = None
) -> str:
    """Replayable DuckDB DDL: base tables then views, in creation order
    (oid order) so FK/view dependencies are satisfied on replay.

    ``only_tables`` (the materialisation manifest) filters the base-table
    pass: stray scratch tables stay out of the schema listing exactly as
    they stay out of the Parquet export."""
    parts: list[str] = []
    for tname, ddl in con.execute(
        "SELECT table_name, sql FROM duckdb_tables() WHERE NOT internal ORDER BY table_oid"
    ).fetchall():
        if only_tables is not None and tname not in only_tables:
            continue
        parts.append(ddl.rstrip().rstrip(";") + ";")
    for (sql,) in con.execute(
        "SELECT sql FROM duckdb_views() WHERE NOT internal AND sql IS NOT NULL ORDER BY view_oid"
    ).fetchall():
        parts.append(sql.rstrip().rstrip(";") + ";")
    return "\n\n".join(parts) + "\n"


def _export_duckdb_tables(
    con: Any,
    tables: list[str],
    out_dir: Path,
    duckdb_path: Path,
    logger: logging.Logger,
    *,
    filters: dict[str, str] | None = None,
    serial: bool = False,
    cast_variant_to_json: bool = False,
) -> tuple[dict[str, dict], int]:
    """COPY each table to its parquet shard (parallel_parquet_export #435).

    Runs the per-table COPYs through a 4-worker cursor pool (file-mode
    connections); wire-served stores keep the serial loop — 4 concurrent
    COPYs over quack-attach cursors break the wire result stream ("Result
    has been closed"). Measured regimes: small tables (the ~56-table e_*
    class) are per-statement-overhead-bound and win 2.5x concurrent; a big
    table gains nothing (its COPY already saturates the engine thread pool)
    and loses nothing — the pool is wall-neutral there. Artifacts are
    content-stable regardless of worker order (ORDER BY ALL), so pool
    scheduling cannot churn the git-tracked blobs.

    duckdb 2.0 traps baked into this shape: cursor() does NOT inherit the
    active catalog (over the wire the store attaches under an alias and USE
    resolves unqualified names — each worker re-issues it), and even
    cursor() creation races under the concurrent-call refusal ("connection
    is busy with another call") — cursors are built HERE, parent thread,
    serially, and handed to workers through a queue.
    """
    _active_db = con.execute("SELECT current_database()").fetchone()[0]
    # Parquet slice (S2): the research-store export passes serial=True — a
    # wire-served canonical only ever gets one COPY at a time (the graph
    # mirror's wire law), and the apply-stamp filter below needs the live
    # catalog anyway. Graph/sources/note exports keep the 4-worker pool.
    workers = 1 if (serial or ds.is_served(duckdb_path)) else min(4, os.cpu_count() or 1)
    _cursor_q: queue.Queue[Any] = queue.Queue()
    for _ in range(workers):
        c = con.cursor()
        c.execute(f'USE "{_active_db}"')
        _cursor_q.put(c)

    def _copy_one(t: str, worker: Any) -> int:
        out_path = out_dir / f"{t}.parquet"
        # ORDER BY ALL (maint_full_zero_churn F4): without it the COPY
        # leaks the table's physical row order into the blob bytes, so a
        # rebuild that left content set-identical still churned every
        # e_* parquet (2026-08-22 audit: e_belongs/e_has reordered).
        # Canonical column order makes the export a pure function of
        # content; the verify path is row-count based, so this is
        # round-trip neutral.
        # Codec: ZSTD (default level 3, 2026-08-29) — standardized with
        # the SQLite parquet side (#174 D2) so both parquet trees ship
        # one codec. The earlier snappy default was speed-safe but left
        # these git-tracked blobs larger for no real export-cost
        # reason: zstd -3 sits in the same speed class while compressing
        # better. Readers (DuckDB scanner, pyarrow, --restore) are
        # codec-transparent.
        if cast_variant_to_json:
            _cols = [r[0] for r in worker.execute(f'DESCRIBE "{t}"').fetchall()]  # noqa: S608
            _select = ", ".join(
                f'"{c}"::VARCHAR AS "{c}"' if c == "properties" else f'"{c}"'
                for c in _cols
            )
            src = f"SELECT {_select} FROM {t}"  # noqa: S608  # t from fixed table inventory
        else:
            src = f"SELECT * FROM {t}"  # noqa: S608  # t from fixed table inventory
        if filters and t in filters:
            # Research db_meta: drop the machine/HEAD-specific apply stamp
            # at export, mirroring _sqlite_table_arrow's APPLY_STAMP_PREFIX
            # exclusion on the SQLite side. The WHERE clause is a code
            # constant, never caller input.
            src += f" WHERE {filters[t]}"
        worker.execute(
            f"COPY ({src} ORDER BY ALL) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"  # noqa: S608  # interpolated parts are `?`-clauses / schema-constant identifiers / code-constant filters
        )
        return out_path.stat().st_size

    def _copy_table(t: str) -> int:
        worker = _cursor_q.get()
        try:
            return _copy_one(t, worker)
        finally:
            _cursor_q.put(worker)

    results: dict[str, dict] = {}
    total_bytes = 0
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        sizes = ThreadPoolExecutor(workers).map(_copy_table, tables)
    else:
        sizes = map(_copy_table, tables)
    for t, sz in zip(tables, sizes):
        total_bytes += sz
        results[t] = {"bytes": sz}
        logger.info(f"  Parquet DuckDB: {t}.parquet ({sz:,} bytes)")
    return results, total_bytes


def export_parquet_duckdb(
    duckdb_path: Path,
    out_dir: Path,
    logger: logging.Logger,
    manifest: frozenset[str] | None = None,
    schema_filename: str = "_schema.duckdb.sql",
) -> dict:
    """Export every materialised DuckDB table to an individual Parquet file.

    ``manifest`` selects which base tables belong to THIS database's
    snapshot (default: the graph materialisation manifest). D15 passes
    SOURCES_TABLES for memory/data/sources.duckdb.

    Uses DuckDB's native ``COPY ... TO ... (FORMAT PARQUET)`` which writes
    columnar Parquet with correct type information (BIGINT, VARCHAR, DOUBLE,
    DATE, etc.). The resulting files are readable by pandas, polars, pyarrow,
    DuckDB, and any BI tool without needing DuckDB installed.
    """
    if not duckdb_path.exists():
        logger.info(f"DuckDB file not found, skipping Parquet export: {duckdb_path}")
        return {"skipped": True}

    out_dir.mkdir(parents=True, exist_ok=True)
    # Lazy import: keeps the module import light and avoids any import
    # cycle during test collection.
    from helpers.graph.query import MATERIALISED_TABLES

    if manifest is None:
        manifest = MATERIALISED_TABLES

    # Single read-only connection; COPY on base tables needs no extensions.
    # open_read_only funnel (Slice B): wire attach when served, io_lock
    # + retry ladder otherwise — a served store excludes file-mode opens.
    from helpers.misc.duckdb_lock import open_read_only

    con = open_read_only(str(duckdb_path))
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
        stray = [t for t in tables if t not in manifest]
        if stray:
            logger.warning(
                "DuckDB table(s) outside the materialisation manifest are "
                "NOT snapshotted — drop them, or extend the manifest "
                "(helpers/graph/query.py MATERIALISED_TABLES for graph.duckdb, "
                "SOURCES_TABLES here for sources.duckdb) if they are real: %s",
                ", ".join(sorted(stray)),
            )
        tables = [t for t in tables if t in manifest]

        # Replayable DDL (tables + views, in dependency-safe order), with
        # the same manifest filter so a stray table's CREATE statement
        # can't leak into _schema.duckdb.sql either.
        schema_path = out_dir.parent / schema_filename
        schema_path.write_text(_export_duckdb_schema(con, only_tables=manifest))
        logger.info(f"  Parquet DuckDB: schema DDL → {schema_path}")

        results, total_bytes = _export_duckdb_tables(con, tables, out_dir, duckdb_path, logger)
    finally:
        con.close()

    # S4 prune (graph_perf_l1_bfs_scale): a table the live DB dropped —
    # the ten ephemeral v_centrality_* after a data-only rebuild — must
    # not linger as a stale git-tracked parquet, or --restore would
    # resurrect last generation's scores over a new edge set. Prune
    # manifest-scoped files that this export did not write; files outside
    # the manifest keep the stray-table warning's remit, not silent rm.
    exported = set(results)
    pruned = []
    for stale in sorted(out_dir.glob("*.parquet")):
        if stale.stem in exported or stale.stem not in manifest:
            continue
        stale.unlink()
        pruned.append(stale.name)
    if pruned:
        logger.info(
            "Parquet DuckDB: pruned %d stale manifest file(s) for dropped tables: %s",
            len(pruned),
            ", ".join(sorted(pruned)),
        )

    logger.info(
        f"Parquet DuckDB export: {len(results)} tables, {total_bytes:,} bytes total → {out_dir}"
    )
    return {"tables": results, "total_bytes": total_bytes, "dir": str(out_dir)}


def _sqlite_table_arrow(con: sqlite3.Connection, table: str) -> pa.Table:
    """Column-wise pyarrow Table straight from a sqlite cursor.

    bulk_data_lanes S1: replaces pd.read_sql + Table.from_pandas (the
    pandas middleman). Direct pa.array keeps NULLable INTEGER columns as
    int64 + nulls (pandas would promote them to float64/NaN) and raises
    ArrowInvalid on a mixed-storage-class column instead of silently
    re-typing it — the fail-loud diagnostic below turns that into a
    one-query fix.
    """
    import pyarrow as pa

    # F4 (worktree_fresh_trample_guard): the S1 apply stamp lives in
    # db_meta as apply_* rows — extra live rows the verifier already
    # drops in _sqlite_live_count. If the EXPORT keeps them, the first
    # `make snapshot` after a stamping apply bakes them into
    # db_meta.parquet and --check reports a permanent
    # `Parquet MISMATCH: sqlite/db_meta`. Exclude at export, mirroring
    # the db_bump_log exclusion in _list_sqlite_tables on the same
    # argument: operational metadata, not content, and machine/HEAD
    # specific — a restored copy must not claim a foreign tree's apply.
    query = f"SELECT * FROM [{table}]"  # noqa: S608  # table from _list_sqlite_tables / SQLITE_PARQUET_TABLES
    params: tuple = ()
    if table == "db_meta":
        from helpers.core.db import APPLY_STAMP_PREFIX

        query += " WHERE key NOT LIKE ?"
        params = (f"{APPLY_STAMP_PREFIX}%",)
    # ORDER BY every column (range review L1): the DuckDB export above gained
    # this in this same range for a stated reason — without it the export
    # leaks the table's physical row order into the blob, so a rebuild that
    # left the content set-identical still churns the parquet. The SQLite half
    # kept the unsorted form, so the two halves of `snapshots/parquet/`
    # disagreed about whether their output was a pure function of content. A
    # VACUUM or an indexed UPDATE is enough to reorder a SQLite table.
    #
    # SQLite has no `ORDER BY ALL` (that is DuckDB syntax), so the column
    # list is built from the table's own schema. BLOB columns are excluded:
    # SQLite cannot order arbitrary blobs reliably, and every snapshot table
    # is text/numeric/JSON.
    order_cols = [
        r[1]
        for r in con.execute(f"PRAGMA table_info([{table}])").fetchall()  # noqa: S608  # identifiers from the snapshot's own table name
        if (r[2] or "").upper() != "BLOB"
    ]
    if order_cols:
        query += " ORDER BY " + ", ".join(f"[{c}]" for c in order_cols)
    cur = con.execute(query, params)
    names = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cols = list(zip(*rows)) if rows else [()] * len(names)
    fields = {}
    for name, col in zip(names, cols):
        try:
            fields[name] = pa.array(col)
        except pa.ArrowInvalid as e:
            diag = [
                tuple(r)
                for r in con.execute(
                    f"SELECT typeof([{name}]), count(*) FROM [{table}] GROUP BY 1"  # noqa: S608  # identifiers from the snapshot's own table/column names
                )
            ]
            raise ParquetExportError(
                f"mixed storage classes in [{table}].[{name}] (typeof -> {diag}): {e}"
            ) from e
    return pa.table(fields)


def _export_research_duckdb(source: Path, out_dir: Path, logger: logging.Logger) -> dict:
    """Export the DuckDB research canonical into the SQLite-schema parquet axis.

    Parquet slice S2: DATA rows come from the canonical; the restore schema
    stays the pinned ``_schema.sqlite.sql`` from the SQLite rollback anchor
    (or the already-tracked file), so ``--restore`` keeps its SQLite-schema
    contract. The roster is the tracked axis intersected with the live
    tables — DuckDB-only operational tables (db_bump_log, *_meta) and the
    SQLite-only FTS content shadow are NOT exported; an existing shadow
    parquet is left in place (no prune — unlike the graph DuckDB export).

    Wire-served stores use a single serial COPY (the graph-mirror wire law);
    ``db_meta`` drops ``apply_%`` rows exactly as the SQLite export does.
    """
    from helpers.misc.duckdb_lock import open_read_only

    out_dir.mkdir(parents=True, exist_ok=True)
    con = open_read_only(str(source))
    try:
        live = set(_list_duckdb_tables(con))
        tracked = {p.stem for p in out_dir.glob("*.parquet")}
        roster = sorted((tracked or set(SQLITE_PARQUET_TABLES)) & live)
        if not roster:
            raise RuntimeError(f"No research tables to export from {source}")

        schema_path = out_dir.parent / "_schema.sqlite.sql"
        anchor = source.with_suffix(".db")
        if anchor.exists() and is_research_store_path(anchor):
            # S1: the rollback anchor is a research-store path by name but
            # must open as SQLite — the selector would redirect it to the
            # DuckDB sibling. Bypass the dispatch.
            ac = _sqlite_connect(anchor, read_only=True)
            try:
                schema_path.write_text(_export_sqlite_schema(ac))
            finally:
                ac.close()
            logger.info(f"  Parquet research: schema DDL → {schema_path} (SQLite rollback anchor)")
        elif schema_path.exists():
            logger.info(f"  Parquet research: schema DDL kept (pinned): {schema_path}")
        else:
            raise FileNotFoundError(
                f"SQLite schema DDL not found for parquet axis: {schema_path} "
                "(restore contract requires _schema.sqlite.sql; supply the "
                "rollback anchor or the tracked file)"
            )

        results, total_bytes = _export_duckdb_tables(
            con,
            roster,
            out_dir,
            source,
            logger,
            filters={"db_meta": "key NOT LIKE 'apply_%'"},
            serial=True,
            cast_variant_to_json=True,
        )
    finally:
        con.close()
    logger.info(
        f"Parquet research (DuckDB) export: {len(results)} tables, "
        f"{total_bytes:,} bytes total → {out_dir}"
    )
    return {"tables": results, "total_bytes": total_bytes, "dir": str(out_dir)}


def export_parquet_sqlite(  # noqa: C901  # export loop with many per-type branches
    sqlite_path: Path, out_dir: Path, logger: logging.Logger
) -> dict:
    """Export SQLite data tables to individual Parquet files.

    Uses ``sqlite3`` + ``pyarrow`` (column-wise, no pandas —
    bulk_data_lanes S1) to avoid DuckDB's strict timestamp parsing on
    some legacy rows. Columns retain their SQLite TEXT types as UTF-8
    strings in Parquet — the caller can cast as needed.
    """
    if not sqlite_path.exists():
        logger.info(f"SQLite DB not found, skipping Parquet export: {sqlite_path}")
        return {"skipped": True}

    # Parquet slice S2: a DuckDB research canonical takes the dedicated
    # exporter; the function name and axis path stay stable for callers.
    if sqlite_path.suffix.lower() == ".duckdb":
        logger.info(f"Research source is DuckDB, exporting via canonical reader: {sqlite_path}")
        return _export_research_duckdb(sqlite_path, out_dir, logger)

    import pyarrow.parquet as pq

    out_dir.mkdir(parents=True, exist_ok=True)
    # S1: this branch is the SQLite-schema axis by contract — bypass the
    # selector dispatch (a research-store path would redirect to DuckDB).
    con = _sqlite_connect(sqlite_path, read_only=True)
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

    def _export_sqlite_table(t: str) -> tuple[dict, int] | None:
        # Per-worker read-only connection: the outer `con` is a shared
        # sqlite3.Connection (not thread-safe); readers are cheap and the
        # helper carries all ordering/filter semantics, so artifacts are
        # byte-identical to the serial loop regardless of worker order.
        try:
            worker_con = _sqlite_connect(sqlite_path, read_only=True)
        except Exception as e:  # noqa: BLE001  # match the serial skip contract
            logger.warning(f"  Parquet SQLite: skipped {t} ({e})")
            return None
        try:
            table = _sqlite_table_arrow(worker_con, t)
        except ParquetExportError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"  Parquet SQLite: skipped {t} ({e})")
            return None
        finally:
            worker_con.close()
        out_path = out_dir / f"{t}.parquet"
        # zstd codec (maint_full_single_snapshot.md D2): the SQLite tables
        # are TEXT/BLOB-heavy (note text, embeddings) and snappy leaves them
        # ~3x larger than needed for a git-tracked artifact; zstd matches
        # gzip's ratio at ~30x the write speed (3.2s → 0.11s on
        # note_search_content) with faster read-back. Codec lives inside
        # the parquet container, so restore/verify are transparent.
        # LEVEL POLICY: no explicit level — pyarrow's zstd default is 1
        # (the one stack whose default is NOT 3), and that's the right
        # call here: measured on this corpus, forcing level 3 gains
        # 0.06 MB on note text (0.11→0.21 s) but makes the float32 BLOB
        # table 14% BIGGER (3.54→4.03 MB). Library defaults win.
        pq.write_table(table, out_path, compression="zstd")
        sz = out_path.stat().st_size
        return {"bytes": sz, "rows": table.num_rows}, sz

    # parallel_parquet_export (2026-10-24): same 4-worker pool as the
    # DuckDB side — the sqlite read+arrow+write chain is per-table
    # overhead-bound at this corpus's table sizes.
    workers = min(4, os.cpu_count() or 1)
    names = list(tables)
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        outcomes = ThreadPoolExecutor(workers).map(_export_sqlite_table, names)
    else:
        outcomes = map(_export_sqlite_table, names)
    for t, outcome in zip(names, outcomes):
        if outcome is None:
            continue
        entry, sz = outcome
        total_bytes += sz
        results[t] = entry
        logger.info(f"  Parquet SQLite: {t}.parquet ({sz:,} bytes, {entry['rows']} rows)")

    con.close()
    logger.info(
        f"Parquet SQLite export: {len(results)} tables, {total_bytes:,} bytes total → {out_dir}"
    )
    return {"tables": results, "total_bytes": total_bytes, "dir": str(out_dir)}


def _verify_gate_stem(stem: str, live_tables: frozenset[str], pf: Path) -> str | None:
    """Verify-side stem gate (verify_injection_and_boundary_remediation S1).

    Restore raises on any unexpected stem; verify has one legitimate
    non-present outcome, so the gate is three-valued:
    - malformed stem (anything the identifier regex refuses) -> loud
      ValueError — a tampered filename is never contract-legal;
    - stem missing from the live schema -> returns ``"MISSING ON LIVE"``
      (real drift, surfaces as a mismatch instead of the old silent skip);
    - else -> None (checked normally).
    """
    if not _SNAPSHOT_IDENT_RE.match(stem):
        raise ValueError(
            f"UNEXPECTED SNAPSHOT FILE {pf.name!r}: stem {stem!r} is not a "
            "schema identifier — refusing verify (tampered snapshot?)"
        )
    if stem not in live_tables:
        return "MISSING ON LIVE"
    return None


def _verify_duckdb_counts(
    con,
    tname: str,
    pf: Path,
    stamp_meta: bool,
    apply_prefix: str | None = None,
) -> tuple[int, int]:
    """Live vs snapshot row counts for one gated stem. Raises on live-side
    errors — the caller records them as mismatches, never skips.

    ``apply_prefix`` (parquet slice S3) mirrors the SQLite side's
    APPLY_STAMP_PREFIX exclusion for the research ``db_meta`` table: apply
    stamps are operational, not content, and must not produce a phantom
    mismatch after an apply. Literal, not a bound param — prepared params
    do not cross the wire on a Slice B attach.
    """
    from helpers.graph.query import STAMP_OWNED_META_KEYS

    path_sql = str(pf).replace("'", "''")
    if apply_prefix is not None and tname == "db_meta":
        like = f"{apply_prefix}%"
        src = con.execute(
            f"SELECT COUNT(*) FROM {tname} WHERE key NOT LIKE '{like}'"  # noqa: S608  # code-constant prefix
        ).fetchone()
        snap = con.execute(
            f"SELECT COUNT(*) FROM '{path_sql}' WHERE key NOT LIKE '{like}'"  # noqa: S608  # path quote-doubled; code-constant prefix
        ).fetchone()
    elif stamp_meta:
        # Literal keys, not prepared params: prepared parameters do not
        # cross the wire when ``con`` is a Slice B wire attach, and the
        # InvalidInput would fail closed as a false mismatch.
        from helpers.core import duckdb_shared as _ds

        lit = ", ".join(_ds.sql_lit(k) for k in STAMP_OWNED_META_KEYS)
        src = con.execute(
            f"SELECT COUNT(*) FROM _build_meta WHERE key NOT IN ({lit}) AND key NOT LIKE 'fp:%'",  # noqa: S608  # literal keys are schema-constants; fp:* = graph_rebuild_fast_path S1 fingerprints (per-rebuild contract-legal drift)
        ).fetchone()
        snap = con.execute(
            f"SELECT COUNT(*) FROM '{path_sql}' WHERE key NOT IN ({lit}) AND key NOT LIKE 'fp:%'",  # noqa: S608  # path quote-doubled; stem gated (S1); fp:* excluded (graph_rebuild_fast_path S1)
        ).fetchone()
    else:
        src = con.execute(
            f"SELECT COUNT(*) FROM {tname}"  # noqa: S608  # stem gated by _verify_gate_stem (regex + live-schema membership)
        ).fetchone()
        snap = con.execute(
            f"SELECT COUNT(*) FROM '{path_sql}'"  # noqa: S608  # path quote-doubled; stem gated by _verify_gate_stem (S1)
        ).fetchone()
    return (src[0] if src is not None else 0), (snap[0] if snap is not None else 0)


def _verify_parquet_duckdb_side(
    parquet_dir: Path,
    duckdb_path: Path | None,
    logger: logging.Logger,
    subdir: str = "duckdb",
) -> dict:
    """DuckDB half of the parquet verify — its own function so the
    per-DB snapshot workers can run export+verify for each database in
    parallel (snapshot_parallel_and_compressed_backups.md D3). ``subdir``
    selects which parquet tree to compare against (D15: ``sources``)."""
    result: dict = {"tables_checked": 0, "mismatches": []}
    if duckdb_path and duckdb_path.exists():
        duckdb_pq_dir = parquet_dir / subdir
        if duckdb_pq_dir.exists():
            from helpers.graph.query import EPHEMERAL_TABLES, STAMP_OWNED_META_KEYS

            # Slice B funnel: wire attach when served (a file-mode open
            # against a served store collides with the server lock).
            from helpers.misc.duckdb_lock import open_read_only

            con = open_read_only(str(duckdb_path))
            try:
                live_tables = frozenset(
                    r[0]
                    for r in con.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
                    ).fetchall()
                )
                pq_files = sorted(duckdb_pq_dir.glob("*.parquet"))
                for pf in pq_files:
                    tname = pf.stem
                    gate = _verify_gate_stem(tname, live_tables, pf)
                    if gate is not None:
                        if tname in EPHEMERAL_TABLES:
                            logger.info(
                                "Parquet verify: ephemeral %s absent on the live side "
                                "(contract-legal; make stamp-centrality restores it)",
                                tname,
                            )
                            continue
                        result["mismatches"].append(f"{subdir}/{tname}: {gate}")
                        continue
                    # S4 (graph_perf_l1_bfs_scale): _build_meta counts only
                    # materialisation-owned keys — the centrality stamp's
                    # extra key is contract-legal drift on either side.
                    stamp_meta = tname == "_build_meta" and bool(STAMP_OWNED_META_KEYS)
                    try:
                        src_cnt, snap_cnt = _verify_duckdb_counts(con, tname, pf, stamp_meta)
                    except (
                        Exception
                    ) as exc:  # fail closed: a live-side error is a verify result, not a skip
                        result["mismatches"].append(f"{subdir}/{tname}: ERROR {type(exc).__name__}")
                        continue
                    result["tables_checked"] += 1
                    if src_cnt != snap_cnt:
                        result["mismatches"].append(f"{subdir}/{tname}: {snap_cnt}/{src_cnt}")
            finally:
                con.close()
    return result


def _sqlite_live_count(con: sqlite3.Connection, tname: str) -> int:
    """Live row count for one gated stem; db_meta drops the apply stamp.

    S1 (worktree_fresh_trample_guard): the note index records apply_head /
    apply_worktree / apply_ts in db_meta, so that table's live count drifts
    from the snapshot by three on every apply. Those keys are contract-legal
    drift — the DuckDB side excludes its own stamp-owned keys
    (STAMP_OWNED_META_KEYS) and fingerprint keys by the same argument — while
    every other table is compared exactly, so real row loss still fails.
    """
    from helpers.core.db import APPLY_STAMP_PREFIX

    if tname == "db_meta":
        row = con.execute(
            "SELECT COUNT(*) FROM db_meta WHERE key NOT LIKE ?",  # noqa: S608  # table is a schema constant in this branch (stem gated by _verify_gate_stem)
            (f"{APPLY_STAMP_PREFIX}%",),
        ).fetchone()
    else:
        row = con.execute(f"SELECT COUNT(*) FROM [{tname}]").fetchone()  # noqa: S608  # stem gated by _verify_gate_stem (regex excludes ']' and quote chars)
    return int(row[0]) if row is not None else 0


def _verify_parquet_sqlite_side(
    parquet_dir: Path, sqlite_path: Path | None, logger: logging.Logger
) -> dict:
    """SQLite-schema axis half of the parquet verify (per-DB parallel workers).

    Parquet slice S3: the axis keeps its name and ``sqlite/`` mismatch labels,
    but a DuckDB research source is read through the Slice B funnel
    (wire attach when served) and counted with ``_verify_duckdb_counts`` —
    the same fail-closed, apply-stamp-excluding semantics as SQLite.
    """
    import pyarrow.parquet as pq

    result: dict = {"tables_checked": 0, "mismatches": []}
    if sqlite_path and sqlite_path.exists():
        sqlite_pq_dir = parquet_dir / "sqlite"
        if sqlite_pq_dir.exists():
            is_duck = Path(sqlite_path).suffix.lower() == ".duckdb"
            if is_duck:
                from helpers.misc.duckdb_lock import open_read_only

                scon = open_read_only(str(sqlite_path))
                live_sql = (
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
                )
            else:
                scon = _sqlite_connect(sqlite_path, read_only=True)
                live_sql = "SELECT name FROM sqlite_master WHERE type = 'table'"
            try:
                live_tables = frozenset(r[0] for r in scon.execute(live_sql).fetchall())
                pq_files = sorted(sqlite_pq_dir.glob("*.parquet"))
                for pf in pq_files:
                    tname = pf.stem
                    gate = _verify_gate_stem(tname, live_tables, pf)
                    if gate is not None:
                        if is_duck and gate == "MISSING ON LIVE" and tname == "note_search_content":
                            # SQLite-FTS content shadow: the DuckDB canonical
                            # has no such table (FTS lives in the note sidecar),
                            # but the parquet is retained in the axis so a
                            # SQLite --restore can rebuild the FTS5 index.
                            # Contract-legal absence, mirroring the graph
                            # side's EPHEMERAL_TABLES skip.
                            logger.info(
                                "  Parquet verify: sqlite/note_search_content absent on "
                                "the DuckDB live side (FTS content shadow; contract-legal)"
                            )
                            continue
                        result["mismatches"].append(f"sqlite/{tname}: {gate}")
                        continue
                    try:
                        if is_duck:
                            src_cnt, snap_cnt = _verify_duckdb_counts(
                                scon, tname, pf, False, apply_prefix="apply_"
                            )
                        else:
                            src_cnt = _sqlite_live_count(scon, tname)
                            snap_cnt = pq.read_metadata(pf).num_rows
                    except (
                        Exception
                    ) as exc:  # fail closed: a live-side error is a verify result, not a skip
                        result["mismatches"].append(f"sqlite/{tname}: ERROR {type(exc).__name__}")
                        continue
                    result["tables_checked"] += 1
                    if src_cnt != snap_cnt:
                        result["mismatches"].append(f"sqlite/{tname}: {snap_cnt}/{src_cnt}")
            finally:
                scon.close()
    return result


def verify_parquet_snapshot(
    parquet_dir: Path,
    duckdb_path: Path | None,
    sqlite_path: Path | None,
    logger: logging.Logger,
) -> dict:
    """Verify Parquet files round-trip: read each back and compare row counts
    against the source databases.

    DuckDB Parquet files are read back via DuckDB's native Parquet scanner.
    SQLite Parquet files are read back via pyarrow. Thin merge over the two
    per-DB side verifiers (which the parallel snapshot workers call
    directly); sequential callers keep the old one-call surface.
    """
    result: dict = {"tables_checked": 0, "mismatches": [], "match": True}
    for partial in (
        _verify_parquet_duckdb_side(parquet_dir, duckdb_path, logger),
        _verify_parquet_sqlite_side(parquet_dir, sqlite_path, logger),
    ):
        result["tables_checked"] += partial["tables_checked"]
        result["mismatches"].extend(partial["mismatches"])

    if result["mismatches"]:
        result["match"] = False
        for m in result["mismatches"]:
            logger.warning(f"  Parquet MISMATCH: {m}")
        # F3 (worktree_fresh_trample_guard): the S3 citation lived on
        # --quick only, which is generation-only and can never annotate
        # the generation-MATCHING table MISMATCH class (the 2026-10-12
        # sources incident was exactly that: fail-closed verify, ~370k
        # live rows, generations equal). A sqlite-side table MISMATCH
        # gets the bump-log citation here, in the --check leg, next to
        # the warning itself. DuckDB/sources sides have no bump log —
        # their drift is table-level by construction.
        if (
            sqlite_path is not None
            and Path(sqlite_path).suffix.lower() != ".duckdb"
            and any(m.startswith("sqlite/") for m in result["mismatches"])
        ):
            logger.warning(
                _bump_log_citation(
                    sqlite_path,
                    _read_sqlite_generation(sqlite_path),
                    _read_parquet_generation(parquet_dir / "sqlite" / "db_meta.parquet"),
                )
            )
    else:
        logger.info(f"Parquet verify: {result['tables_checked']} tables checked, all OK")
    return result


# ---------------------------------------------------------------------------
# Restore: rebuild the live databases from the git-tracked Parquet snapshot
# ---------------------------------------------------------------------------


_SNAPSHOT_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*\Z")


def _snapshot_identifier(stem: str, tables: frozenset[str], src: Path) -> str:
    """Validate a snapshot parquet stem as a schema table identifier (SNAP-1).

    Restore interpolates file stems into SQL identifiers; the parquet dir
    is repo-tracked content, so provenance is not a control. The stem must
    be plain lowercase-underscore AND a base table the just-applied schema
    DDL created — anything else is a tampered or mismatched snapshot and
    restore refuses loudly instead of executing it.
    """
    if not _SNAPSHOT_IDENT_RE.match(stem) or stem not in tables:
        raise ValueError(
            f"UNEXPECTED SNAPSHOT FILE {src.name!r}: stem {stem!r} is not a "
            "table created by the snapshot schema DDL — refusing restore "
            "(tampered or mismatched snapshot?)"
        )
    return stem


def _parquet_rows(path: Path) -> tuple[list[str], list[tuple]]:
    """Columns + row tuples from one .parquet file (NaN → NULL).

    bulk_data_lanes S2: pq.read_table + column-wise to_pylist replaces
    pd.read_parquet + astype(object).where(notna) — parquet nulls map to
    None natively with no full-table object copy; float columns get an
    explicit NaN→None pass (today that laundering happened twice:
    pandas-side, then SQLite's own NaN→NULL insert coercion — both
    accidental; now deterministic and test-pinned).
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    cols: list[pa.Array] = []
    for c in table.columns:
        if pa.types.is_floating(c.type):
            # is_nan(null) is null; if_else propagates it — nulls stay
            # null, NaN becomes null, values pass through. No fill_null
            # (that would coerce genuine NULLs to 0.0). ty: the pyarrow
            # compute functions are dynamically generated — unresolvable.
            c = pc.if_else(  # ty: ignore[missing-attribute]
                pc.is_nan(c),  # ty: ignore[missing-attribute]
                pa.scalar(None, c.type),
                c,
            )
        cols.append(c)
    names = list(table.column_names)
    return names, list(zip(*[c.to_pylist() for c in cols]))


def restore_sqlite_from_parquet(
    parquet_dir: Path,
    target: Path,
    logger: logging.Logger,
    *,
    drop_agent_triggers: bool = False,
) -> dict:
    """Rebuild a SQLite DB at ``target`` from ``parquet_dir``/*.parquet.

    Applies ``_schema.sqlite.sql`` (from the snapshot dir's parent), loads
    every Parquet file, rebuilds the FTS5 index from its exported content
    shadow, runs ``PRAGMA foreign_key_check``, then atomically replaces
    ``target``. The caller is responsible for any overwrite guard.

    ``drop_agent_triggers`` (test-donor use only): drops the
    ``*_agent_id_registered_*`` triggers before loading so rows whose
    ``agent_id`` is not registered in ``provenance_agents`` still load —
    the canonical DuckDB store has no FK/trigger enforcement, so the
    parquet axis may legitimately carry such rows.
    """
    schema_path = parquet_dir.parent / "_schema.sqlite.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema DDL not found next to the Parquet dir: {schema_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restore-tmp")
    tmp.unlink(missing_ok=True)

    # S1: the restore target is a fresh SQLite file by contract — bypass the
    # selector dispatch (the tmp name could sit beside a research-store path).
    con = _sqlite_connect(tmp)
    restored: dict[str, int] = {}
    try:
        con.executescript(schema_path.read_text())
        # The sqlite snapshot schema declares the FTS5 virtual table but not
        # its plain meta companion (created by rebuild_note_search.py on
        # live stores) — restore it so test donors/templates expose the same
        # table surface as production (near_duplicates, schema drift).
        con.execute(
            "CREATE TABLE IF NOT EXISTS note_search_meta ("
            " file_path TEXT PRIMARY KEY,"
            " mtime REAL NOT NULL,"
            " content_hash TEXT NOT NULL"
            ")"
        )
        # executescript() commits and leaves scripted pragmas behind; the
        # load itself must not enforce FKs (table order is alphabetical).
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("PRAGMA journal_mode=OFF")  # fresh build: max speed
        if drop_agent_triggers:
            for (tname,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE '%_agent_id_registered_%'"
            ).fetchall():
                con.execute(f'DROP TRIGGER "{tname}"')
        schema_tables = frozenset(
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        )
        for pf in sorted(parquet_dir.glob("*.parquet")):
            tname = _snapshot_identifier(pf.stem, schema_tables, pf)
            table_cols = frozenset(
                r[1]
                for r in con.execute(
                    f"PRAGMA table_info({tname})"
                ).fetchall()  # tname validated by _snapshot_identifier (SNAP-1); no linter rule fires on this line — a noqa here would be dead
            )
            cols, rows = _parquet_rows(pf)
            if not cols:
                continue
            bad_cols = [c for c in cols if not _SNAPSHOT_IDENT_RE.match(c) or c not in table_cols]
            if bad_cols:
                raise ValueError(
                    f"UNEXPECTED SNAPSHOT COLUMN(S) {bad_cols!r} in {pf.name!r}: "
                    "not columns of the schema table — refusing restore"
                )
            collist = ", ".join(f"[{c}]" for c in cols)
            marks = ", ".join("?" * len(cols))
            con.executemany(
                f"INSERT INTO [{tname}] ({collist}) VALUES ({marks})",  # noqa: S608  # identifier + columns validated by _snapshot_identifier (SNAP-1)
                rows,
            )
            restored[tname] = len(rows)
            logger.info(f"  Restore SQLite: {tname} ← {len(rows):,} rows")
        # Regenerate the FTS5 index from its exported content shadow.
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='note_search'").fetchone():
            con.execute("INSERT INTO note_search(note_search) VALUES('rebuild')")
            logger.info("  Restore SQLite: FTS5 note_search index rebuilt")
        con.commit()
        fk_issues = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk_issues:
            raise sqlite3.IntegrityError(f"foreign_key_check failed after restore: {fk_issues[:5]}")
    finally:
        con.close()

    tmp.replace(target)
    logger.info(
        f"Restore SQLite: {target} ({len(restored)} tables, {sum(restored.values()):,} rows)"
    )
    return {"target": str(target), "tables": restored}


def restore_duckdb_from_parquet(
    parquet_dir: Path,
    target: Path,
    logger: logging.Logger,
    schema_filename: str = "_schema.duckdb.sql",
    expected_shadow_prefixes: tuple[str, ...] = (),
) -> dict:
    """Rebuild a DuckDB file at ``target`` from ``parquet_dir``/*.parquet.

    Applies the schema DDL (creation-ordered tables then views), bulk-loads
    each Parquet via ``read_parquet``, checkpoints, then atomically replaces
    ``target``. ``schema_filename`` selects the DDL next to the parquet dir
    (D15: ``_schema.sources.sql`` for sources.duckdb).

    ``expected_shadow_prefixes``: FTS shadow schemas (``fts_main_<p>``)
    the applied schema is supposed to carry (watchlist G1). After the
    restore, each expected shadow that is absent is warned about — lexical
    search on that store would raise CatalogException until the rebuild
    runs. Derived state, not an error; empty by default (stores without
    FTS expectations stay silent).
    """
    import duckdb

    schema_path = parquet_dir.parent / schema_filename
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema DDL not found next to the Parquet dir: {schema_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restore-tmp")
    for stale in (tmp, Path(str(tmp) + ".wal")):
        stale.unlink(missing_ok=True)

    con = duckdb.connect(str(tmp), **ds.memory_cap_config())
    restored: dict[str, int] = {}
    try:
        con.execute(schema_path.read_text())
        # SNAP-1: the only identifiers restore may interpolate are stems the
        # applied schema itself created — schema-derived, not manifest-listed,
        # so sources.duckdb (D15) is gated by the same rule.
        schema_tables = frozenset(
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        )
        for pf in sorted(parquet_dir.glob("*.parquet")):
            tname = _snapshot_identifier(pf.stem, schema_tables, pf)
            con.execute(
                f"INSERT INTO {tname} SELECT * FROM read_parquet('{str(pf).replace(chr(39), chr(39) * 2)}')"  # noqa: S608  # identifier + path validated by _snapshot_identifier (SNAP-1)
            )
            row = con.execute(
                f"SELECT COUNT(*) FROM {tname}"  # noqa: S608  # identifier validated by _snapshot_identifier (SNAP-1)
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
        f"Restore DuckDB: {target} ({len(restored)} tables, {sum(restored.values()):,} rows)"
    )
    for prefix in expected_shadow_prefixes:
        if not _fts_shadow_present(target, prefix):
            logger.warning(
                f"  restored store has no FTS shadow (fts_main_{prefix}) — run the "
                "rebuild for this store before searching (lexical search will raise "
                "CatalogException)"
            )
    return {"target": str(target), "tables": restored}


def _cmd_restore(  # noqa: C901  # restore orchestrator: arg branches mirror _cmd_backup
    db_path: Path,
    duckdb_path: Path,
    parquet_sqlite_dir: Path,
    parquet_duckdb_dir: Path,
    with_duckdb: bool,
    force: bool,
    logger: logging.Logger,
    sources_path: Path | None = None,
    parquet_sources_dir: Path | None = None,
    with_sources: bool = True,
    note_path: Path | None = None,
    parquet_note_dir: Path | None = None,
    with_note: bool = True,
) -> int:
    """--restore: rebuild live DBs from the git-tracked Parquet snapshot."""
    if db_path.exists() and not force:
        logger.error(f"Refusing to overwrite existing {db_path} — pass --force")
        return 1
    if with_duckdb and duckdb_path.exists() and not force:
        logger.error(f"Refusing to overwrite existing {duckdb_path} — pass --force")
        return 1
    if with_sources and sources_path is not None and sources_path.exists() and not force:
        logger.error(f"Refusing to overwrite existing {sources_path} — pass --force")
        return 1
    if with_note and note_path is not None and note_path.exists() and not force:
        logger.error(f"Refusing to overwrite existing {note_path} — pass --force")
        return 1
    targets = [str(db_path)]
    if with_duckdb:
        targets.append(str(duckdb_path))
    if with_sources and sources_path is not None:
        targets.append(str(sources_path))
    if with_note and note_path is not None:
        targets.append(str(note_path))
    logger.warning(
        "destructive op: restore live DBs from Parquet snapshot -> %s | "
        "live state since the snapshot is LOST | undo: none — snapshot "
        "the live DBs first if unsure (make snapshot)",
        ", ".join(targets),
    )
    if parquet_sqlite_dir.exists():
        restore_sqlite_from_parquet(parquet_sqlite_dir, db_path, logger)
    else:
        logger.error(f"Parquet snapshot not found: {parquet_sqlite_dir}")
        return 1
    if with_duckdb:
        if parquet_duckdb_dir.exists():
            restore_duckdb_from_parquet(parquet_duckdb_dir, duckdb_path, logger)
        else:
            logger.warning(f"DuckDB Parquet snapshot not found: {parquet_duckdb_dir}")
    if with_sources and parquet_sources_dir is not None:
        if parquet_sources_dir.exists():
            if sources_path is None:
                logger.error("sources parquet dir given but no --sources-db target")
                return 1
            restore_duckdb_from_parquet(
                parquet_sources_dir,
                sources_path,
                logger,
                schema_filename="_schema.sources.sql",
            )
        else:
            logger.info(f"Sources Parquet snapshot not found (skip): {parquet_sources_dir}")
    if with_note and parquet_note_dir is not None:
        if parquet_note_dir.exists():
            if note_path is None:
                logger.error("note parquet dir given but no --note-db target")
                return 1
            restore_duckdb_from_parquet(
                parquet_note_dir,
                note_path,
                logger,
                schema_filename="_schema.note.sql",
                expected_shadow_prefixes=("note_search",),
            )
        else:
            logger.info(f"Note Parquet snapshot not found (skip): {parquet_note_dir}")
    return 0


def _fts_shadow_present(store: Path, shadow_prefix: str) -> bool:
    """True when the DuckDB FTS shadow schema (``fts_main_<shadow_prefix>``)
    exists on the store. Derived state: restored stores legitimately lack it
    until the rebuild runs (watchlist G1 — lexical search would raise
    CatalogException without it). Best-effort: unreadable stores read as
    absent (the caller warns, never fails on this probe)."""
    try:
        from helpers.misc.duckdb_lock import open_read_only

        con = open_read_only(store)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = ?",
                (f"fts_main_{shadow_prefix}",),
            ).fetchone()
            return bool(row and row[0])
        finally:
            con.close()
    except Exception:
        return False


def _read_sqlite_generation(db_path: Path) -> str | None:
    """Live SQLite generation from db_meta (None when unreadable)."""
    try:
        # S1: SQLite by name — bypass the selector dispatch.
        con = _sqlite_connect(db_path, read_only=True)
        try:
            row = con.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception:
        return None


def _read_duckdb_generation(duckdb_path: Path) -> str | None:
    """Live DuckDB generation from _build_meta (None when unreadable).

    Routes through the Slice B funnel: a resident quack server holding
    the store excludes every file-mode open, so the raw connect returned
    None and snapshot-fresh reported a phantom MISMATCH (run 1757)."""
    try:
        from helpers.misc.duckdb_lock import open_read_only

        con = open_read_only(duckdb_path)
        try:
            row = con.execute("SELECT value FROM _build_meta WHERE key='generation'").fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception:
        return None


def _read_parquet_generation(parquet_path: Path) -> str | None:
    """Snapshot-side generation from a meta parquet (None when unreadable)."""
    try:
        import duckdb

        con = duckdb.connect(**ds.memory_cap_config())
        try:
            row = con.execute(
                "SELECT value FROM read_parquet(?) WHERE key='generation'",
                [str(parquet_path)],
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception:
        return None


def _read_bump_log(db_path: Path) -> tuple[str, str, str, str] | None:
    """Newest db_bump_log row (ts, caller, worktree, generation), or None.

    None covers every "no evidence" case alike — a pre-S3 index, a restored
    snapshot without the table, an unreadable file — so the citation can say
    so instead of inventing a cause.
    """
    try:
        con = _sqlite_connect(db_path, read_only=True)
        try:
            row = con.execute(
                "SELECT ts, caller, worktree, generation FROM db_bump_log"
                " ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001  # absent table / unreadable db -> "no evidence"
        return None
    return tuple(row) if row is not None else None


def _has_bump_log_table(db_path: Path) -> bool:
    """Whether db_bump_log exists at all (F3b: restored vs trigger-driven).

    A snapshot restored from a pre-F9 capture replays a schema without the
    ring, so the table is ABSENT; a live index whose bumps all came from
    the entities/graph_edges triggers HAS the table (created by the first
    manual bump's DDL, and carried empty by post-F9 restores) even when no
    row names the current generation. Absent table vs present-but-silent
    are different evidence and must not read identically.
    """
    try:
        con = _sqlite_connect(db_path, read_only=True)
        try:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='db_bump_log'"
            ).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001  # unreadable db -> "no evidence"
        return False
    return row is not None


def _bump_log_citation(db_path: Path, live: str | None, snap: str | None) -> str:
    """One line naming what moved the source generation since the snapshot.

    S3 (worktree_fresh_trample_guard): a generation MISMATCH used to read as
    a ghost — chasing the 2026-10-12 one needed temporary audit triggers to
    find the writer. The bump log turns it into a named event:

        bump log: source moved +2 since snapshot; last logged bump
        2026-10-12 17:52:11 by rebuild_note_search.py:1156 (worktree
        graph_algos, generation 415306)

    Only MANUAL bumps are logged (the entities/graph_edges triggers bump
    inside SQL and cannot append), so a cited generation that trails the live
    one is reported as an unlogged gap rather than passed off as the cause.
    A negative delta (snapshot ahead of live — post-restore) renders with
    its own sign, never "+-N".
    """
    moved = ""
    try:
        if live is not None and snap is not None:
            moved = f"source moved {int(live) - int(snap):+d} since snapshot; "
    except ValueError:
        moved = ""
    row = _read_bump_log(db_path)
    if row is None:
        if _has_bump_log_table(db_path):
            detail = (
                "bump log present but silent — every bump since S3 went "
                "unlogged (trigger-driven bumps cannot append)"
            )
        else:
            detail = "no bump log table (restored snapshot or pre-S3 index)"
        return f"  bump log: {moved}{detail}"
    ts, caller, worktree, generation = row
    tail = ""
    if live is not None and str(generation) != str(live):
        tail = (
            f"; generation is {live} now, so later bumps went unlogged "
            "(trigger-driven bumps cannot append)"
        )
    return (
        f"  bump log: {moved}last logged bump {ts} by {caller} "
        f"(worktree {worktree or 'main'}, generation {generation}){tail}"
    )


def _cmd_quick(
    parquet_base: Path,
    db_path: Path,
    duckdb_path: Path,
    logger: logging.Logger,
) -> int:
    """--quick: generation-only freshness (sqlite + duckdb pairs).

    O(1) point reads, no table counting: fails fast on generation drift
    with the exact remediation instead of MISMATCH archaeology 7 s into
    the full verify. Fail-closed — any unreadable side is stale (a
    missing snapshot IS stale; snapshots are git-tracked, so fresh
    clones have them).
    """
    ok = True
    pairs = []
    # Build-route P4 retired the SQLite research host (memory/research.db
    # deleted); when no sqlite host exists, the sqlite leg is skipped,
    # not stale — the legacy backup is an archived artifact, not a live
    # freshness contract. The duckdb leg is the live freshness contract.
    sqlite_live = _read_sqlite_generation(db_path)
    if sqlite_live is not None:
        pairs.append(
            (
                "sqlite",
                sqlite_live,
                _read_parquet_generation(parquet_base / "sqlite" / "db_meta.parquet"),
            )
        )
    pairs.append(
        (
            "duckdb",
            _read_duckdb_generation(duckdb_path),
            _read_parquet_generation(parquet_base / "duckdb" / "_build_meta.parquet"),
        )
    )
    for name, live, snap in pairs:
        match = live is not None and live == snap
        logger.info(
            f"{name} generation: live={live} snapshot={snap} -> {'OK' if match else 'MISMATCH'}"
        )
        if not match and name == "sqlite":
            logger.warning(_bump_log_citation(db_path, live, snap))
        ok = ok and match
    if not ok:
        logger.error("snapshot generation drift — run make snapshot to re-capture")
    return 0 if ok else 1


def _cmd_check(
    out_path: Path,
    db_path: Path,
    duckdb_out: Path,
    duckdb_path: Path,
    parquet_base: Path,
    with_duckdb: bool,
    logger: logging.Logger,
    sources_path: Path | None = None,
    with_sources: bool = True,
    note_path: Path | None = None,
    with_note: bool = True,
) -> int:
    """--check: verify zstd binary + Parquet snapshots round-trip."""
    # --check ALWAYS verifies both formats (zstd binary + Parquet),
    # regardless of --format (which only gates the create path).
    # Each verify gracefully skips whatever isn't present.
    ok = True
    # BUILD-route (P4): the SQLite rollback anchor is gone — resolve the
    # binary source through the same selector gate as the parquet axis
    # (canonical .duckdb sibling), and point the sink at the matching
    # artifact name.
    bin_src = _resolve_research_source(db_path)
    bin_out = (
        out_path.with_name(out_path.name.replace(".db.zst", ".duckdb.zst"))
        if bin_src.suffix.lower() == ".duckdb"
        else out_path
    )
    r = verify_snapshot(bin_out, bin_src, logger)
    ok = ok and r["match"]
    if with_duckdb:
        rd = verify_duckdb_snapshot(duckdb_out, duckdb_path, logger)
        ok = ok and rd["match"]
    rp = verify_parquet_snapshot(
        parquet_base,
        duckdb_path if with_duckdb else None,
        _resolve_research_source(db_path),  # parquet slice S4: selector-resolved axis source
        logger,
    )
    ok = ok and rp["match"]
    if with_sources and sources_path is not None and sources_path.exists():
        rs = _verify_parquet_duckdb_side(parquet_base, sources_path, logger, subdir="sources")
        if rs["mismatches"]:
            ok = False
            for m in rs["mismatches"]:
                logger.warning(f"  Parquet MISMATCH: {m}")
            # F3 (worktree_fresh_trample_guard): the sources side carries
            # no generation and no bump log (pure data tables — verified
            # live 2026-10-12: credit_ratings … shp_holders only), so a
            # sources MISMATCH is table-level drift by construction, never
            # a bump — the 2026-10-12 incident class (fail-closed verify,
            # generations equal). Say so next to the warning instead of
            # leaving another ghost-shaped archaeology.
            logger.warning(
                "  sources side has no generation/bump log — drift is table-level; "
                "re-capture with make snapshot"
            )
        else:
            logger.info(f"Parquet verify (sources): {rs['tables_checked']} tables checked, all OK")
    if with_note and note_path is not None and note_path.exists():
        rn = _verify_parquet_duckdb_side(parquet_base, note_path, logger, subdir="note")
        if rn["mismatches"]:
            ok = False
            for m in rn["mismatches"]:
                logger.warning(f"  Parquet MISMATCH: {m}")
            logger.warning(
                "  note side has no generation/bump log — drift is table-level; "
                "re-capture with make snapshot"
            )
        else:
            logger.info(f"Parquet verify (note): {rn['tables_checked']} tables checked, all OK")
        if not _fts_shadow_present(note_path, "note_search"):
            logger.warning(
                "  note FTS shadow (fts_main_note_search) missing — lexical "
                "search will raise CatalogException until rebuild_note_search.py runs"
            )
    return 0 if ok else 1


def _cmd_create(
    db_path: Path,
    out_path: Path,
    duckdb_path: Path | None,
    duckdb_out: Path | None,
    parquet_base: Path | None,
    parquet_sqlite_dir: Path | None,
    parquet_duckdb_dir: Path | None,
    fmt: str,
    with_duckdb: bool,
    logger: logging.Logger,
    sources_path: Path | None = None,
    parquet_sources_dir: Path | None = None,
    with_sources: bool = True,
    note_path: Path | None = None,
    parquet_note_dir: Path | None = None,
    with_note: bool = True,
) -> int:
    """Default: create the snapshot (binary / parquet / both) and verify.

    The duckdb/parquet paths are format-optional: they are only touched
    inside the ``with_duckdb`` / ``fmt in ("parquet", "both")`` branches,
    so binary-only callers (and tests) legitimately pass ``None``.

    Parallelism (snapshot_parallel_and_compressed_backups.md D3): one
    thread per database — the three binary backups and the two parquet
    exports touch disjoint files, and the heavy work (zstd, sqlite
    backup API, DuckDB CHECKPOINT/COPY, pyarrow write) releases the GIL.
    Worker exceptions propagate (fail loud, no silent partial snapshot).
    """
    ok = True
    if fmt in ("binary", "both"):
        ok = (
            _create_binary_snapshot(
                db_path,
                out_path,
                duckdb_path,
                duckdb_out,
                with_duckdb,
                logger,
            )
            and ok
        )
    if fmt in ("parquet", "both"):
        ok = (
            _create_parquet_snapshot(
                db_path,
                parquet_base,
                parquet_sqlite_dir,
                duckdb_path,
                parquet_duckdb_dir,
                with_duckdb,
                logger,
                sources_path=sources_path,
                parquet_sources_dir=parquet_sources_dir,
                with_sources=with_sources,
                note_path=note_path,
                parquet_note_dir=parquet_note_dir,
                with_note=with_note,
            )
            and ok
        )
    return 0 if ok else 1


def _snapshot_embed_store(db_path: Path, out_path: Path, logger: logging.Logger) -> None:
    """Embed store sidecar: the pooled content-hash cache lives in ONE
    consolidated DuckDB database
    (helpers.core.vec_search.EMBED_DB_PATH) since the embed-store fold
    (S0, 2026-11-15); pre-fold clones (and tests seeding tmp
    dbs) may still carry a per-db <db>_vec.db sqlite sibling. (The
    vector mirror that shared this store was retired 2026-11-05 for a
    DuckDB sidecar — memory/note_vectors.duckdb — which is derived,
    snapshot-excluded state rebuilt lazily on first hybrid search.)
    All derived state — but the cache is content-addressed and expensive to
    re-fill cold (~minutes of re-embedding). Copy-only (no
    verify_snapshot: that helper is hardcoded to
    entities/relations); the DuckDB checkpoint-then-copy backup already
    gives WAL consistency."""
    vec_src = db_path.with_name(db_path.name + "_vec.db")
    if vec_src.exists():
        vec_out = out_path.parent / out_path.name.replace(".db.zst", ".db_vec.db.zst")
        create_snapshot(vec_src, vec_out, logger)
        return
    from helpers.core.vec_search import EMBED_DB_PATH

    store = Path(EMBED_DB_PATH)
    if store.exists():
        store_out = out_path.parent / f"{store.stem}.snapshot.duckdb.zst"
        # Skip re-compress when the store is unchanged since the
        # last backup (maint_full_single_snapshot.md D3): mtime is
        # the fingerprint — db_maint never writes the store and
        # steady-state embed writes are guarded upserts (no byte
        # change ⇒ no write), so ~36 MB of compression per snapshot
        # run is pure waste in the steady state. A missing/older
        # archive always re-compresses. (mtime-only: the zstd
        # stdlib does not record frame content size, so the old
        # gzip-ISIZE size cross-check has no equivalent —
        # zstd_binary_backups.md D2.)
        if store_out.exists() and store.stat().st_mtime <= store_out.stat().st_mtime:
            logger.info("Embed store unchanged since last backup — reusing %s", store_out)
        else:
            create_duckdb_snapshot(store, store_out, logger)
    else:
        logger.info("Embed store absent — backup skipped (%s)", store)


def _snapshot_corpus(out_path: Path, logger: logging.Logger) -> None:
    """Corpus cache sidecar (S1b): memory/corpus.duckdb holds frontmatter +
    FULL NOTE BODIES for every vault note — the private-content class
    the git-side parquet snapshot deliberately excludes, so this zst
    is its only byte-exact copy (operator decision 2026-09-04).
    Copy-only + mtime-skip, same rationale as the embed store
    (D3): the cache changes only when the corpus rebuilds."""
    from helpers.core.corpus import CORPUS_DB

    cache = Path(CORPUS_DB)
    if cache.exists():
        cache_out = out_path.parent / f"{cache.stem}.snapshot.db.zst"
        if cache_out.exists() and cache.stat().st_mtime <= cache_out.stat().st_mtime:
            logger.info("Corpus cache unchanged since last backup — reusing %s", cache_out)
        else:
            create_snapshot(cache, cache_out, logger)
    else:
        logger.info("Corpus cache absent — backup skipped (%s)", cache)


def _create_binary_snapshot(
    db_path: Path,
    out_path: Path,
    duckdb_path: Path | None,
    duckdb_out: Path | None,
    with_duckdb: bool,
    logger: logging.Logger,
) -> bool:
    """Binary-format branch of ``_cmd_create``: research + embed store +
    corpus cache + (optional) DuckDB, one thread per database. Returns
    the verify match."""
    from concurrent.futures import ThreadPoolExecutor

    # --- Binary (zstd) snapshot: one worker per database ---
    ok = True

    def _duckdb_binary_worker() -> bool:
        # Narrow the format-optional paths (S101-clean explicit raise;
        # a None here is a caller contract violation, not a state to
        # assert away).
        if duckdb_path is None or duckdb_out is None:
            raise ValueError("with_duckdb=True requires duckdb paths")
        create_duckdb_snapshot(duckdb_path, duckdb_out, logger)
        return verify_duckdb_snapshot(duckdb_out, duckdb_path, logger)["match"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        research = pool.submit(_research_binary_worker, db_path, out_path, logger)
        embed = pool.submit(_snapshot_embed_store, db_path, out_path, logger)
        corpus = pool.submit(_snapshot_corpus, out_path, logger)
        futures = [research, embed, corpus]
        if with_duckdb:
            futures.append(pool.submit(_duckdb_binary_worker))
        # .result() re-raises worker exceptions; the sidecar workers are
        # copy-only (return None) — their failure surfaces as the exception.
        ok = ok and bool(research.result())
        for f in futures[1:]:
            f.result()

    return ok


def _research_binary_worker(db_path: Path, out_path: Path, logger: logging.Logger) -> bool:
    """Binary-format research leg — BUILD-route (P4) selector resolution.

    The SQLite rollback anchor (``memory/research.db``) was deleted in
    P4; the canonical research store is the converted ``.duckdb``
    sibling. Follow the same selector gate as the parquet axis
    (``_resolve_research_source``): snapshot the canonical sibling when
    it exists, skip with a log when it does not (parity with the
    embed/corpus/sources workers — never a failed run)."""
    src = _resolve_research_source(db_path)
    if not src.exists():
        logger.info("Research store absent — binary snapshot skipped (%s)", src)
        return True
    if src.suffix.lower() == ".duckdb":
        res_out = out_path.with_name(out_path.name.replace(".db.zst", ".duckdb.zst"))
        create_duckdb_snapshot(src, res_out, logger)
        # verify_snapshot dispatches on the source suffix to the
        # entities/relations-shaped duckdb verify (the graph-shaped
        # verify_duckdb_snapshot expects v_node/e_* tables).
        return verify_snapshot(res_out, src, logger)["match"]
    return (
        create_snapshot(src, out_path, logger),
        verify_snapshot(out_path, src, logger)["match"],
    )[1]


def _create_parquet_snapshot(  # noqa: C901  # parquet snapshot: per-side export ladder
    db_path: Path,
    parquet_base: Path | None,
    parquet_sqlite_dir: Path | None,
    duckdb_path: Path | None,
    parquet_duckdb_dir: Path | None,
    with_duckdb: bool,
    logger: logging.Logger,
    sources_path: Path | None = None,
    parquet_sources_dir: Path | None = None,
    with_sources: bool = True,
    note_path: Path | None = None,
    parquet_note_dir: Path | None = None,
    with_note: bool = True,
) -> bool:
    """Parquet-format branch of ``_cmd_create``: sqlite + (optional)
    DuckDB + (optional, D15) sources.duckdb + (optional, S4 note_search
    carve-out) note_search.duckdb export, each with its own verify, one
    thread per database."""
    from concurrent.futures import ThreadPoolExecutor

    if parquet_base is None or parquet_sqlite_dir is None:
        raise ValueError("parquet format requires parquet paths")

    # Parquet slice S4: the axis keeps its SQLite-schema restore contract but
    # its data source follows the S6 selector (canonical DuckDB by default).
    research_source = _resolve_research_source(db_path)
    if research_source != db_path:
        logger.info(f"Parquet research source: {research_source} (selector-resolved)")

    def _sqlite_parquet_worker() -> bool:
        export_parquet_sqlite(research_source, parquet_sqlite_dir, logger)
        return (
            _verify_parquet_sqlite_side(parquet_base, research_source, logger)["mismatches"] == []
        )

    def _duckdb_parquet_worker() -> bool:
        if with_duckdb:
            if duckdb_path is None or parquet_duckdb_dir is None:
                raise ValueError("with_duckdb=True requires duckdb paths")
            export_parquet_duckdb(duckdb_path, parquet_duckdb_dir, logger)
            return (
                _verify_parquet_duckdb_side(parquet_base, duckdb_path, logger)["mismatches"] == []
            )
        return True

    def _sources_parquet_worker() -> bool:
        # D15: sources.duckdb — external acquisition store, own manifest,
        # own schema file, own parquet tree. Skips silently when the file
        # is absent (pristine clones) so the graph lanes stay runnable.
        if with_sources and sources_path is not None and sources_path.exists():
            if parquet_sources_dir is None:
                raise ValueError("with_sources=True requires a sources parquet dir")
            export_parquet_duckdb(
                sources_path,
                parquet_sources_dir,
                logger,
                manifest=SOURCES_TABLES,
                schema_filename="_schema.sources.sql",
            )
            return (
                _verify_parquet_duckdb_side(parquet_base, sources_path, logger, subdir="sources")[
                    "mismatches"
                ]
                == []
            )
        return True

    def _note_parquet_worker() -> bool:
        # S4 (note_search carve-out): the live note index is the DuckDB
        # sidecar — own manifest, own schema file, own parquet tree under
        # snapshots/parquet/note/. Skips silently when absent (pristine
        # clones, pre-S1 stores).
        if with_note and note_path is not None and note_path.exists():
            if parquet_note_dir is None:
                raise ValueError("with_note=True requires a note parquet dir")
            export_parquet_duckdb(
                note_path,
                parquet_note_dir,
                logger,
                manifest=NOTE_SEARCH_TABLES,
                schema_filename="_schema.note.sql",
            )
            return (
                _verify_parquet_duckdb_side(parquet_base, note_path, logger, subdir="note")[
                    "mismatches"
                ]
                == []
            )
        return True

    ok = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        sq = pool.submit(_sqlite_parquet_worker)
        dq = pool.submit(_duckdb_parquet_worker)
        rc = pool.submit(_sources_parquet_worker)
        nq = pool.submit(_note_parquet_worker)
        ok = sq.result() and dq.result() and rc.result() and nq.result()
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Versioned snapshot of the research DB + DuckDB graph cache: "
            "git-tracked Parquet under snapshots/ (restorable via --restore) "
            "+ local zstd copies under db-backup/."
        )
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=(
            "Source SQLite DB / rollback anchor (relative to repo root). The "
            "parquet axis resolves its DATA source through the S6 selector "
            "(canonical .duckdb sibling by default); --restore keeps this "
            "SQLite-schema target."
        ),
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output .db.zst (relative to repo root)."
    )
    parser.add_argument(
        "--duckdb",
        default=DEFAULT_DUCKDB,
        help="Source DuckDB file (relative to repo root).",
    )
    parser.add_argument(
        "--duckdb-out",
        default=DEFAULT_DUCKDB_OUT,
        help="Output DuckDB .duckdb.zst (relative to repo root).",
    )
    parser.add_argument(
        "--no-duckdb",
        dest="with_duckdb",
        action="store_false",
        help="Skip the DuckDB snapshot (SQLite only).",
    )
    parser.add_argument(
        "--parquet-duckdb-only",
        action="store_true",
        help="CREATE path: re-export ONLY the duckdb parquet mirror "
        "(snapshots/parquet/duckdb). maint.py runs this after "
        "graph-rebuild, whose cache refresh otherwise leaves the mirror "
        "one rebuild stale.",
    )
    parser.add_argument(
        "--duckdb-snapshot-only",
        action="store_true",
        help="CREATE path: re-capture ONLY the local DuckDB .zst backup "
        "(db-backup/graph.snapshot.duckdb.zst + its WAL sidecar). maint.py "
        "runs this after graph-rebuild, which would otherwise leave the "
        ".zst one rebuild stale (the SQLite .zst keeps its step-2 "
        "restore-point position).",
    )
    parser.set_defaults(with_duckdb=True)
    parser.add_argument(
        "--sources-db",
        default=DEFAULT_SOURCES_DUCKDB,
        help="Source DuckDB for the D15 external-source store (relative to repo root).",
    )
    parser.add_argument(
        "--no-sources",
        dest="with_sources",
        action="store_false",
        help="Skip the sources.duckdb snapshot leg.",
    )
    parser.set_defaults(with_sources=True)
    parser.add_argument(
        "--note-db",
        default=DEFAULT_NOTE_DUCKDB,
        help="Note-search DuckDB sidecar (S4 note_search carve-out) relative to repo root.",
    )
    parser.add_argument(
        "--no-note",
        dest="with_note",
        action="store_false",
        help="Skip the note_search.duckdb snapshot leg.",
    )
    parser.set_defaults(with_note=True)
    parser.add_argument(
        "--format",
        choices=["binary", "parquet", "both"],
        default="both",
        help="Snapshot format for CREATE: binary (zstd .db.zst), parquet "
        "(per-table .parquet), or both. Default: both. "
        "--check always verifies both formats regardless of this flag.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify an existing snapshot round-trips against the source. "
        "Always checks BOTH formats (zstd binary + Parquet when present); "
        "--format only gates the create path.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Generation-only freshness check (sqlite + duckdb pairs, "
        "O(1) point reads): fail fast on drift with remediation instead "
        "of the full verify. For gates and humans; fail-closed.",
    )
    parser.add_argument(
        "--parquet-dir",
        default=DEFAULT_PARQUET,
        help="Root dir for the Parquet snapshot (git-tracked default: snapshots/parquet).",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Rebuild the LIVE databases from the Parquet snapshot "
        "(schema DDL + data + FTS5 rebuild). Refuses to overwrite "
        "existing live files unless --force.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --restore: overwrite existing live DB files.",
    )
    parser.add_argument("--log", default="INFO", help="Logging level.")
    args = parser.parse_args(argv)

    log_level = getattr(logging, args.log.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format=LOG_FORMAT)
    logger = logging.getLogger("snapshot_db")

    from datetime import datetime

    from helpers.maintenance.maint_timing import RunTimer

    _mode = (
        "restore"
        if args.restore
        else "check"
        if args.check
        else "quick"
        if args.quick
        else "parquet_duckdb_only"
        if args.parquet_duckdb_only
        else "duckdb_snapshot_only"
        if args.duckdb_snapshot_only
        else "create"
    )
    _timer = RunTimer("snapshot_db", mode=_mode)

    root = REPO_ROOT
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
    sources_path = Path(args.sources_db)
    sources_path = sources_path if sources_path.is_absolute() else root / sources_path
    parquet_sources_dir = parquet_base / "sources"
    note_path = Path(args.note_db)
    note_path = note_path if note_path.is_absolute() else root / note_path
    parquet_note_dir = parquet_base / "note"

    def _timed(phase: str, fn, *a, **k):
        _p0 = datetime.now()
        try:
            return fn(*a, **k)
        finally:
            _timer.record_phase(phase, _p0, datetime.now())

    try:
        if args.restore:
            rc = _timed(
                "restore",
                _cmd_restore,
                db_path,
                duckdb_path,
                parquet_sqlite_dir,
                parquet_duckdb_dir,
                args.with_duckdb,
                args.force,
                logger,
                sources_path=sources_path,
                parquet_sources_dir=parquet_sources_dir,
                with_sources=args.with_sources,
                note_path=note_path,
                parquet_note_dir=parquet_note_dir,
                with_note=args.with_note,
            )
            return _timer.finish(rc, f"mode=restore rc={rc}")
        if args.check:
            rc = _timed(
                "check",
                _cmd_check,
                out_path,
                db_path,
                duckdb_out,
                duckdb_path,
                parquet_base,
                args.with_duckdb,
                logger,
                sources_path=sources_path,
                with_sources=args.with_sources,
                note_path=note_path,
                with_note=args.with_note,
            )
            return _timer.finish(rc, f"mode=check rc={rc}")
        if args.quick:
            rc = _timed("quick", _cmd_quick, parquet_base, db_path, duckdb_path, logger)
            return _timer.finish(rc, f"mode=quick rc={rc}")
        if args.parquet_duckdb_only:
            if not duckdb_path.exists():
                print(f"ERROR: {duckdb_path} not found", file=sys.stderr)
                return _timer.finish(1, "parquet_duckdb_only: duckdb missing")
            _timed(
                "export_duckdb_parquet",
                export_parquet_duckdb,
                duckdb_path,
                parquet_duckdb_dir,
                logger,
            )
            return _timer.finish(0, "mode=parquet_duckdb_only")
        if args.duckdb_snapshot_only:
            if not duckdb_path.exists():
                print(f"ERROR: {duckdb_path} not found", file=sys.stderr)
                return _timer.finish(1, "duckdb_snapshot_only: duckdb missing")
            _timed(
                "create_duckdb_snapshot",
                create_duckdb_snapshot,
                duckdb_path,
                duckdb_out,
                logger,
            )
            return _timer.finish(0, "mode=duckdb_snapshot_only")
        rc = _timed(
            "create",
            _cmd_create,
            db_path,
            out_path,
            duckdb_path,
            duckdb_out,
            parquet_base,
            parquet_sqlite_dir,
            parquet_duckdb_dir,
            args.format,
            args.with_duckdb,
            logger,
            sources_path=sources_path,
            parquet_sources_dir=parquet_sources_dir,
            with_sources=args.with_sources,
            note_path=note_path,
            parquet_note_dir=parquet_note_dir,
            with_note=args.with_note,
        )
        return _timer.finish(rc, f"mode=create rc={rc}")
    except Exception as e:  # pragma: no cover
        print(f"ERROR: {e}", file=sys.stderr)
        return _timer.finish(1, f"ERROR: {e}"[:500])


if __name__ == "__main__":
    raise SystemExit(main())
