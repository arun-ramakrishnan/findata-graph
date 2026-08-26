#!/usr/bin/env python3
"""DuckDB query layer over the FinData knowledge graph.

Architecture (see doc/graph_design.txt §3, §5, §8):
  - SQLite (memory/research.db) is the sole writer / source of truth.
  - DuckDB attaches it read-only via the sqlite extension.
  - Graph data is materialised as plain DuckDB tables (``v_node`` vertex
    table + ``e_*`` edge tables per EDGE_REGISTRY entry); every query in
    this module is plain SQL (JOINs / recursive CTEs) over those tables.
  - Graph *algorithms* (pagerank, WCC, clustering, betweenness, ...) run on
    the Onager community extension via helpers/graph/onager.py.
  - duckpgq (SQL/PGQ: CREATE PROPERTY GRAPH / MATCH / GRAPH_TABLE) was
    RETIRED 2026-08-14 — Phases A-E of
    doc/improvements/archive/graph/duckpgq_retirement.txt — which unpinned
    DuckDB (duckpgq had no build for 1.5.5+).
  - Disk-based: DuckDB persists to ``memory/graph.duckdb`` so warm
    connects skip the ~150ms materialisation step. SQLite is re-ATTACHed
    every connect (ATTACH is session-scoped, not persisted in the catalog
    — see §17.10). Materialisation runs only when the file is cold, or
    when ``rebuild=True`` / ``fresh=True`` is passed.

Staleness contract: the ``.duckdb`` file is a read-derived cache of
SQLite. After any SQLite writer (``parse_newsletter --apply``,
``derive-relations``, etc.) the cache goes stale and must be refreshed
explicitly via ``make graph-rebuild`` or POST ``/api/graph/refresh``.
There is no auto-detection; see §18 for rationale.

Usage (library):

    from helpers.graph.query import connect, sector_of, sector_members
    con = connect()
    print(sector_of(con, "CEAT"))                 # 'Automotive'
    print(sector_members(con, "Automotive")[:3])  # ['Amara Raja ...', ...]

Usage (CLI):

    python3 helpers/graph/query.py sector-of CEAT
    python3 helpers/graph/query.py sector-members Automotive --limit 5
    python3 helpers/graph/query.py neighbors "Polycab India"
    python3 helpers/graph/query.py rebuild           # refresh stale cache
    python3 helpers/graph/query.py sql "SELECT * FROM e_belongs LIMIT 5"
"""
from __future__ import annotations

import argparse
import logging
import functools
import re
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any
from collections.abc import Callable

import duckdb

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from helpers.graph.onager import (  # noqa: E402  (sys.path set above)
    onager_clustering,
    onager_components,
    onager_pagerank,
)
DB_PATH = PROJECT_ROOT / "memory" / "research.db"
# Disk-based DuckDB cache. The materialised tables (v_node, e_*) persist
# in this file across sessions; only the SQLite ATTACH is re-established
# per connect.
DUCKDB_PATH = PROJECT_ROOT / "memory" / "graph.duckdb"

# P1.2: INSTALL is machine-local + idempotent but still hits disk; only INSTALL
# once per process, thereafter just LOAD (per-connection state).
_DUCKDB_EXT_INSTALLED = False
_DUCKDB_EXT_LOCK = threading.Lock()

# P2.3: Python-side query cache keyed by (generation, sql). In-process lru
# for query results so repeated sector_members("Automotive") etc. don't
# re-hit DuckDB. Invalidated when generation bumps (via _is_warm check).
#
# Note: the connection-object cache (_GRAPH_CACHE / _CachedDuckDBWrapper) that
# previously lived here was removed — its no-op close() violated DuckDB's
# single-writer contract (mixing a cached read-write handle with a later
# read-only open to the same file raises "different configuration"). The disk
# warmness check (_is_warm, ~5ms) already delivers the materialisation-skip
# win; app.py holds its own long-lived singleton for Flask. Each connect()
# returns a real, closeable connection.
_QUERY_CACHE: dict[tuple, Any] = {}
_QUERY_CACHE_LOCK = threading.Lock()
_QUERY_CACHE_MAX = 256

def _query_cache_get(key: tuple) -> Any | None:
    with _QUERY_CACHE_LOCK:
        return _QUERY_CACHE.get(key)

def _query_cache_set(key: tuple, value: Any) -> None:
    with _QUERY_CACHE_LOCK:
        if len(_QUERY_CACHE) >= _QUERY_CACHE_MAX:
            # evict oldest (first inserted) — simple FIFO, not LRU, cheap
            oldest = next(iter(_QUERY_CACHE))
            _QUERY_CACHE.pop(oldest, None)
        _QUERY_CACHE[key] = value

def _query_cache_clear() -> None:
    with _QUERY_CACHE_LOCK:
        _QUERY_CACHE.clear()

def _current_generation_for_cache(duckdb_path: Path | None = None) -> str | None:
    """Generation string for cache key — None if db_meta absent (pre-migration)."""
    try:
        from helpers.core.db import connect as _dbc
        cand = duckdb_path or DUCKDB_PATH
        # Try DB_PATH first, then sibling
        for c in (DB_PATH, Path(cand).with_suffix(".db")):
            if c.exists():
                try:
                    con = _dbc(str(c))
                    try:
                        row = con.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
                        if row:
                            return str(row[0])
                    finally:
                        con.close()
                    break
                except Exception:  # noqa: S112  # best-effort; skip item on failure
                    continue
    except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
        pass
    return None


def _with_generation_cache(fn):
    """Decorator for query wrappers — cache per generation+args (P2.3)."""
    @functools.wraps(fn)
    def _wrapper(con, *args, **kwargs):
        # con is DuckDB handle — not part of cache key (same logical DB)
        try:
            gen = _current_generation_for_cache()
        except Exception:
            gen = None
        # Use schema version too so code bumps invalidate
        key = (fn.__name__, args, tuple(sorted(kwargs.items())), gen, _SCHEMA_VERSION)
        cached = _query_cache_get(key)
        if cached is not None:
            return cached
        result = fn(con, *args, **kwargs)
        _query_cache_set(key, result)
        return result
    return _wrapper

# Schema-version recorded in the .duckdb file's _build_meta table. Bump
# when the materialisation shape changes in a way that requires a rebuild
# (e.g. new column on v_node, EDGE_REGISTRY structural change).
# v2 (Bundle L2): e_acquired gained a typed `year` column projected from
# properties JSON at materialise time.
# v3 (Bundle P3): the SQLite source's graph_analytics PK reversed to
# (metric, entity_name). DuckDB doesn't read graph_analytics, but the
# SQLite rebuild (rebuild_schema.py) also re-runs migrate()-equivalent DDL
# on graph_edges (json_valid CHECK) and entities (canonical layout), so
# the DuckDB cache is force-refreshed to stay consistent with the source.
# v4 (Bundle C2): entities.market_cap column was DROPPED (the market_cap/*
# tag in entity_tags is the source of truth; the column disagreed for 126
# companies). v_node now LEFT JOINs entity_tags to materialize market_cap
# from the tag. All DuckDB consumers (sector_members, sector_members_with
# _market_cap, /api/graph/sector) read c.market_cap off the vertex and keep
# working unchanged.
# v5 (Bundle C2 fix): the v4 LEFT JOIN fanned out for 41 companies that had
# MULTIPLE conflicting market_cap/* tags (a data error), producing
# duplicate v_node/v_company rows. Replaced with a correlated subselect
# that picks exactly one tag per entity (MIN) — vertex table is now
# guaranteed 1-row-per-entity regardless of tag conflicts.
# v6 (Bundle M4): the sector hierarchy landed — 9 `super_sector` + 21
# `sub_sector` entities, plus a new `belongs_to` edge type
# (sector->super_sector, sub_sector->sector). v_node now materializes all
# four entity kinds; v_super_sector/v_sub_sector projections added; a
# dedicated e_belongs_to CTAS (the generic EDGE_REGISTRY loop is binary
# company<->sector and can't represent the hierarchy's mixed endpoints).
# v9 (Phase E, duckpgq retirement): the property graph (fin_graph) is no
# longer declared — pattern queries are plain SQL JOINs over the e_* tables.
# The bump forces every existing .duckdb cache cold so no stale fin_graph /
# __duckpgq_internal catalog entries survive.
_SCHEMA_VERSION = "13"  # 13: + e_invested (Relations 2.0 E5) + semantic_peer already in 12

# Metadata table inside the .duckdb file tracking what was built + when.
# Used to detect "is this file warm or cold" and to record provenance.
_BUILD_META_DDL = """
CREATE TABLE IF NOT EXISTS _build_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# Safe literal quoting for the recursive-CTE walks (string-interpolated
# statements that predate parameter support in those code paths).
# Single quotes inside the string are doubled per SQL convention.
_STRING_LIT_RE = re.compile(r"'")
# C0 control characters (incl. NUL) cannot appear inside a DuckDB string
# literal — the parser treats them as a terminator and raises
# ParserException ("unterminated quoted string"), turning a hostile or
# corrupt company name into a query crash. Control chars are never
# meaningful in entity names, so strip them before quoting. Fuzz-discovered
# 2026-08-09 (tests/test_fuzz_semantic.py: company="\x00").
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _lit(value: str | int) -> str:
    """Render a Python string as a SQL string literal (single-quoted, escaped)."""
    return "'" + _STRING_LIT_RE.sub("''", _CONTROL_RE.sub("", str(value))) + "'"

# Edge-type registry: maps graph_edges.edge_type →
# (table_name, source_col, target_col, source_kind, target_kind, label_name).
# New edge types must be registered here so they show up in the property graph.
# source_kind / target_kind: 'company' or 'sector' (which vertex table).
# Tables that are empty at session time are silently skipped in the property
# graph declaration (duckpgq v1.1 rejects empty edge tables with a CSR error).
EDGE_REGISTRY: dict[str, dict[str, str]] = {
    "part_of": {
        "table": "e_belongs",
        "src": "company_name", "dst": "sector_name",
        "src_kind": "company", "dst_kind": "sector",
        "label": "BelongsTo",
    },
    "has_company": {
        "table": "e_has",
        "src": "sector_name", "dst": "company_name",
        "src_kind": "sector", "dst_kind": "company",
        "label": "HasCompany",
    },
    # Phase 2 edge types (company ↔ company unless noted):
    "competes_with": {
        "table": "e_competes",
        "src": "a_name", "dst": "b_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "CompetesWith",
    },
    "jv_with": {
        "table": "e_jv",
        "src": "a_name", "dst": "b_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "JvWith",
    },
    "same_group": {
        "table": "e_group",
        "src": "a_name", "dst": "b_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "SameGroup",
    },
    "supplier_to": {
        "table": "e_supplier",
        "src": "supplier_name", "dst": "customer_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "SuppliesTo",
    },
    "customer_of": {
        "table": "e_customer",
        "src": "customer_name", "dst": "supplier_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "CustomerOf",
    },
    "acquired": {
        "table": "e_acquired",
        "src": "acquirer_name", "dst": "target_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "AcquiredBy",
    },
    "subsidiary_of": {
        "table": "e_subsidiary",
        "src": "subsidiary_name", "dst": "parent_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "SubsidiaryOf",
    },
    "co_mentioned_in": {
        "table": "e_comention",
        "src": "a_name", "dst": "b_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "CoMentionedIn",
    },
    "semantic_peer": {
        "table": "e_semantic_peer",
        "src": "a_name", "dst": "b_name",
        "src_kind": "company", "dst_kind": "company",
        "label": "SemanticPeer",
    },
    "invested_in": {
        "table": "e_invested",
        "src": "institution_name", "dst": "company_name",
        "src_kind": "institution", "dst_kind": "company",
        "label": "InvestedIn",
    },
}

# Reverse lookup: edge label (e.g. "BelongsTo") → (edge_type, registry spec).
# Used by _shortest_path_cte to translate a graph label into the graph_edges
# .edge_type value for the recursive-walk WHERE filter. Built once at import.
EDGE_REGISTRY_BY_LABEL: dict[str, dict[str, str]] = {
    spec["label"]: {**spec, "edge_type": etype}
    for etype, spec in EDGE_REGISTRY.items()
}


# --------------------------------------------------------------------------- #
# Connection / property-graph setup
# --------------------------------------------------------------------------- #
def clear_graph_cache() -> None:
    """Clear the in-process query-result cache.

    Called after a rebuild/refresh so stale query results (keyed by the
    prior generation) are not served. (The connection-object cache that also
    lived here was removed; see the note by ``_QUERY_CACHE``.)
    """
    try:
        _query_cache_clear()
    except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
        pass


def _prep_graph_connection(con: duckdb.DuckDBPyConnection) -> None:
    """Load the sqlite + vss extensions (INSTALL once per process)."""
    # P1.2: only INSTALL once per process (machine-local cache hit after first)
    global _DUCKDB_EXT_INSTALLED
    with _DUCKDB_EXT_LOCK:
        if not _DUCKDB_EXT_INSTALLED:
            for ext in ("sqlite", "vss"):
                try:
                    con.execute(f"INSTALL {ext};")
                except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
                    pass
            _DUCKDB_EXT_INSTALLED = True
    # Phase E (duckpgq retirement): duckpgq is no longer loaded — the graph
    # algorithms run on onager and the pattern queries are plain SQL over
    # the materialised e_* tables. See
    # doc/improvements/archive/graph/duckpgq_retirement.txt.
    # P2.5: vss provides the array_cosine_* scalars used by
    # semantic_neighbors(). The HNSW index-accelerated scan macros
    # (hnsw_index_scan, vss_match) are broken on vss b833341; brute-force
    # scalar functions work fine at 1k scale (~3ms).
    con.execute("LOAD sqlite;")
    con.execute("LOAD vss;")
    # Onager (graph algorithms) loads lazily per call in helpers/graph/onager.py
    # (_prepare) — idempotent, so no INSTALL here.


def _attach_sqlite(con: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    """Re-attach SQLite every connect — DuckDB refuses to persist cross-
    engine ATTACHes in the catalog for safety (§17.10)."""
    try:
        con.execute(f"ATTACH '{db_path}' AS fin (TYPE sqlite, READ_ONLY);")
    except Exception:
        try:
            con.execute("DETACH fin;")
            con.execute(f"ATTACH '{db_path}' AS fin (TYPE sqlite, READ_ONLY);")
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass


def connect(  # noqa: C901
    db_path: Path | str = DB_PATH,
    duckdb_path: Path | str | None = None,
    rebuild: bool = False,
    fresh: bool = False,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with the property graph ready to query.

    Disk-based by default: opens/creates ``memory/graph.duckdb`` and skips
    materialisation when the file is warm (``_build_meta`` says the schema
    is present and neither ``rebuild`` nor ``fresh`` was requested).

    Args:
        db_path: SQLite path to ATTACH read-only as ``fin``. Defaults to
            ``DB_PATH`` (production).
        duckdb_path: DuckDB file path. When ``None`` (the default), picks
            ``DUCKDB_PATH`` if ``db_path == DB_PATH`` (production) or a
            sibling ``<db_path>.duckdb`` otherwise (test isolation).
        rebuild: Drop+repopulate the materialised tables in-place even if
            the file is warm. Use after ``parse_newsletter --apply`` /
            ``derive-relations`` so the disk cache picks up new edges.
        fresh: Drop the entire ``.duckdb`` file (and WAL sidecar) and
            rebuild from scratch. Use after DuckDB version bumps or
            materialisation-schema changes.
        read_only: Open the cache file cross-process-safe read-only.
            DuckDB allows any NUMBER of read-only openers across
            processes but a single read-write one — so pure readers
            (algorithms --compute, suggest_relations) pass True and never
            contend with (or against) a writer under `make advisory`'s
            parallel steps. Requires a warm cache; cold/stale falls back
            to the normal read-write path, since a read-only opener
            cannot materialise.

    Returns a read-write DuckDB connection (unless ``read_only``). The
    first caller to a cold file pays the ~150ms materialisation cost;
    subsequent callers on a warm file pay ~5ms (extensions + ATTACH only).

    SQLite stays the sole writer (§3.1). The ``.duckdb`` file is a
    read-derived cache with explicit invalidation — see §18.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")

    # Resolve duckdb_path with the test-isolation fallback: production
    # paths share memory/graph.duckdb; test/custom SQLite paths get a
    # colocated .duckdb file so parallel tests don't race on one file.
    if duckdb_path is None:
        if db_path == DB_PATH:
            duckdb_path = DUCKDB_PATH
        else:
            duckdb_path = db_path.with_suffix(".duckdb")
    duckdb_path = Path(duckdb_path)

    # `fresh` drops the file entirely (schema bumps, corruption recovery).
    if fresh and duckdb_path.exists():
        duckdb_path.unlink()
        duckdb_path.with_suffix(".duckdb.wal").unlink(missing_ok=True)

    # When rebuilding, bust the in-process query-result cache first — a
    # caller that queried on an earlier connection would otherwise be served
    # results keyed to the pre-rebuild generation. The rebuild()/
    # fresh_rebuild() wrappers already do this; replicate it here so direct
    # connect(rebuild=True)/connect(fresh=True) callers are safe too.
    if rebuild or fresh:
        clear_graph_cache()

    needs_build = (
        fresh
        or rebuild
        or not (duckdb_path.exists() and _is_warm(duckdb_path))
    )

    # If the file exists but is corrupted/warm-check failed, treat it as
    # cold: delete it so duckdb.connect() doesn't raise IOException. The
    # `fresh` path already handled this; this catches the mid-corruption
    # case where _is_warm returned False because the read-only open failed.
    if (
        not fresh
        and not rebuild
        and duckdb_path.exists()
        and needs_build
        and not _is_warm(duckdb_path)
    ):
        try:
            duckdb_path.unlink()
            duckdb_path.with_suffix(".duckdb.wal").unlink(missing_ok=True)
        except OSError:
            pass

    # Cross-process readers (the make advisory parallelism): N read-only
    # openers coexist with each other; only a read-write opener excludes
    # everyone. Cold/stale cache falls through to the RW path below.
    if read_only and not needs_build:
        con = duckdb.connect(str(duckdb_path), read_only=True)
        _prep_graph_connection(con)
        _attach_sqlite(con, db_path)
        return con

    con = duckdb.connect(str(duckdb_path))
    _prep_graph_connection(con)
    _attach_sqlite(con, db_path)

    if needs_build:
        _build_graph(con)
        _mark_warm(con, db_path)
    return con


def _is_warm(duckdb_path: Path) -> bool:  # noqa: C901
    """True if the ``.duckdb`` file has a populated ``_build_meta`` table.

    Used to decide whether ``connect()`` can skip materialisation. A cold
    or partially-built file returns False; the caller rebuilds.
    """
    try:
        con = duckdb.connect(str(duckdb_path), read_only=True)
        try:
            r = con.execute(
                "SELECT value FROM _build_meta WHERE key='schema_version'"
            ).fetchone()
            if r is None or r[0] != _SCHEMA_VERSION:
                return False
            # P0: generation staleness — compare SQLite generation vs
            # DuckDB _build_meta.generation. Missing generation in either
            # store means cold (needs rebuild) so old caches auto-refresh.
            duck_gen = None
            try:
                gr = con.execute("SELECT value FROM _build_meta WHERE key='generation'").fetchone()
                duck_gen = int(gr[0]) if gr and gr[0] is not None else None
            except Exception:
                duck_gen = None
            # Read SQLite generation via helper (tolerate missing table)
            sqlite_gen = None
            try:
                from helpers.core.db import EXPECTED_SCHEMA_VERSION as _exp_sv, connect as _db_connect  # noqa: F401  (keep import local to avoid cycle)
                # Candidate order: the .db COLOCATED with this .duckdb first
                # (test/custom DBs — connect() resolves the sibling
                # <db_path>.duckdb), then the production DB_PATH. Production
                # is unaffected (memory/graph.db doesn't exist); colocated-
                # first keeps tmp-fixture probes off the live research.db.
                # The loop STOPS at the first candidate that EXISTS — a
                # colocated db_meta without a generation row means "no
                # generation", not "keep looking" (falling through to the
                # live DB compares a fixture build against production's
                # counter and always reads cold).
                for cand in (duckdb_path.with_suffix(".db"), DB_PATH):
                    if not cand.exists():
                        continue
                    try:
                        _scon = _db_connect(str(cand))
                        try:
                            _row = _scon.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
                            if _row is not None:
                                sqlite_gen = int(_row[0])
                        finally:
                            _scon.close()
                    except Exception:  # noqa: S110  # best-effort; unreadable/no db_meta → no generation
                        pass
                    break
            except Exception:
                sqlite_gen = None
            # If either side has no generation yet, treat as cold so we rebuild and stamp it
            if duck_gen is None or sqlite_gen is None:
                # Old cache without generation OR SQLite without db_meta → needs rebuild
                # But don't force rebuild if both are None (pre-migration, no counter yet) — still warm for old behavior
                if duck_gen is None and sqlite_gen is None:
                    pass  # fall through to version check
                else:
                    return False
            elif duck_gen != sqlite_gen:
                return False
            # P3.4 (Phase E): check DuckDB version drift — rebuild if updated.
            # The duckpgq_version half was removed with the duckpgq
            # retirement; a stale duckpgq_version key in an old cache file is
            # ignored (harmless — schema_version "9" already forces those
            # files cold).
            try:
                _row = con.execute("SELECT version()").fetchone()
                cur_duckdb = _row[0] if _row is not None else None
                r_duck = con.execute("SELECT value FROM _build_meta WHERE key='duckdb_version'").fetchone()
                stored_duck = r_duck[0] if r_duck else None
                if stored_duck is not None and cur_duckdb is not None and str(stored_duck) != str(cur_duckdb):
                    return False
            except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
                pass
            # sql_capability_unlocks A1: note-embedding drift — a dims change
            # (model swap to a different vector size) or a model-label change
            # (same-dims swap, e.g. MiniLM-384 -> bge-384, which dims alone
            # cannot see) must force cold: a warm v_note_embeddings would
            # keep serving zip-truncated or cross-model cosines. Stamps are
            # written by _mark_warm; the live side is probed SQLite-side
            # (dims from the first non-empty note_search embedding JSON,
            # model from db_meta.note_embed_model — stamped by
            # rebuild_note_search's apply path). Skipped entirely when no
            # stamp exists AND the live side has no embeddings either.
            try:
                r_dims = con.execute(
                    "SELECT value FROM _build_meta WHERE key='note_embed_dims'"
                ).fetchone()
                r_model = con.execute(
                    "SELECT value FROM _build_meta WHERE key='note_embed_model'"
                ).fetchone()
            except Exception:
                r_dims = r_model = None
            stored_nd = r_dims[0] if r_dims else None
            stored_nm = r_model[0] if r_model else None
            if stored_nd is not None or stored_nm is not None:
                live_dims, live_model = _probe_note_embed_state(duckdb_path)
                if str(stored_nd) != str(live_dims) or str(stored_nm) != str(live_model):
                    return False
            return True
        finally:
            con.close()
    except duckdb.Error:
        # File doesn't exist, isn't a DuckDB file, or _build_meta is
        # missing — all mean "cold, needs build".
        return False


def _probe_note_embed_state(duckdb_path: Path) -> tuple[str | None, str | None]:
    """Live SQLite-side (note_embed_dims, note_embed_model) probe for _is_warm.

    Dims come from json-parsing the first non-empty note_search embedding
    (stored_embed_dims discipline: unparsable JSON counts as absent).
    Model comes from db_meta.note_embed_model. Both None when the DB has
    no embeddings/no stamp. Tries the .db COLOCATED with the .duckdb
    first, then DB_PATH — same candidate order (and for the same
    test-isolation reason) as the generation check in _is_warm.
    """
    import json as _json

    from helpers.core.db import connect as _db_connect

    for cand in (duckdb_path.with_suffix(".db"), DB_PATH):
        if not cand.exists():
            continue
        try:
            _scon = _db_connect(str(cand))
            try:
                dims = None
                try:
                    row = _scon.execute(
                        "SELECT embedding FROM note_search "
                        "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
                    ).fetchone()
                    if row and row[0]:
                        vec = _json.loads(row[0])
                        if isinstance(vec, list) and vec:
                            dims = str(len(vec))
                except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
                    pass
                model = None
                try:
                    row = _scon.execute(
                        "SELECT value FROM db_meta WHERE key='note_embed_model'"
                    ).fetchone()
                    model = row[0] if row and row[0] else None
                except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
                    pass
                return dims, model
            finally:
                _scon.close()
        except Exception:  # noqa: S112  # best-effort; skip item on failure
            continue
    return None, None


def _mark_warm(con: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    """Record build provenance in _build_meta after a successful build."""
    con.execute(_BUILD_META_DDL)
    # P0: stamp generation from SQLite db_meta so _is_warm can do O(1) staleness
    gen_val = None
    note_model = None
    try:
        from helpers.core.db import connect as _db_connect
        _scon = _db_connect(str(db_path))
        try:
            _row = _scon.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
            gen_val = str(int(_row[0])) if _row and _row[0] is not None else None
            # sql_capability_unlocks A1: the note-embedding model label lives
            # in db_meta (note_search rows carry no model column — db_meta is
            # the only SQL-side home; stamped by rebuild_note_search's apply
            # path). Stamped here so _is_warm can catch same-dims swaps.
            _row = _scon.execute(
                "SELECT value FROM db_meta WHERE key='note_embed_model'"
            ).fetchone()
            note_model = _row[0] if _row and _row[0] else None
        finally:
            _scon.close()
    except Exception:
        gen_val = None
    # sql_capability_unlocks A1: stamp the materialised note-embedding dims
    # (len() of one stored vector; empty table -> no stamp) so _is_warm can
    # detect a dims-changing model swap.
    note_dims = None
    try:
        _row = con.execute(
            "SELECT len(emb) FROM v_note_embeddings LIMIT 1").fetchone()
        note_dims = str(int(_row[0])) if _row and _row[0] else None
    except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
        pass
    # P3.4 (Phase E): capture the DuckDB version for drift detection
    # (duckpgq_version stamping removed with the duckpgq retirement).
    duckdb_ver = None
    try:
        _row = con.execute("SELECT version()").fetchone()
        duckdb_ver = _row[0] if _row is not None else None
    except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
        pass
    # Build _build_meta insert with generation + versions
    # Use upsert for each key separately to handle optional gen
    base_vals = [("schema_version", _SCHEMA_VERSION), ("built_at", date.today().isoformat()), ("source_db", str(db_path))]
    if gen_val is not None:
        base_vals.append(("generation", gen_val))
    if note_dims is not None:
        base_vals.append(("note_embed_dims", note_dims))
    if note_model:
        base_vals.append(("note_embed_model", note_model))
    if duckdb_ver:
        base_vals.append(("duckdb_version", str(duckdb_ver)))
    for k, v in base_vals:
        con.execute("INSERT OR REPLACE INTO _build_meta(key, value) VALUES (?, ?)", (k, v))


# Vertex/hierarchy/embedding tables materialised OUTSIDE the EDGE_REGISTRY
# loop (projections + mixed-endpoint edges; see the Bundle M4/D4 notes in
# _build_graph). _build_meta is stamped by connect() after the build
# (CREATE TABLE IF NOT EXISTS), so it is manifest-only — never dropped in
# the build pass. MATERIALISED_TABLES is the single-source manifest of
# every DuckDB table the materialisation owns: _build_graph's drop pass
# reads _EXTRA_MATERIALIZED, and snapshot_db.export_parquet_duckdb
# refuses to snapshot anything outside MATERIALISED_TABLES (stray scratch
# tables are skipped + warned — a 2026-08-21 benchmark leftover otherwise
# shipped an orphan parquet into a snapshot commit).
_EXTRA_MATERIALIZED = (
    "v_node", "v_company", "v_sector", "v_super_sector", "v_sub_sector",
    "v_theme", "v_edition", "v_institution", "v_embeddings", "v_note_embeddings",
    "e_belongs_to", "e_exposed_to", "e_cited_in", "e_all_und", "e_dir",
)
MATERIALISED_TABLES = frozenset(
    spec["table"] for spec in EDGE_REGISTRY.values()
).union(_EXTRA_MATERIALIZED, {"_build_meta"})


def _build_graph(con: duckdb.DuckDBPyConnection) -> None:
    """Materialise vertices + edges + declare the property graph.

    On a warm file this is skipped (see ``connect``). When called directly
    by ``rebuild=True``/``fresh=True`` paths, the caller has already
    dropped the old materialised tables (fresh) or will truncate them
    in-place (rebuild via DROP+CREATE).
    """
    # rebuild=True path: drop existing materialised tables so CREATE
    # TABLE AS SELECT doesn't fail on the warm file. fresh=True path:
    # file was deleted so none of these exist; DROP IF EXISTS is a no-op.
    for spec in EDGE_REGISTRY.values():
        con.execute(f"DROP TABLE IF EXISTS {spec['table']}")
    # Bundle M4: also drop the hierarchy vertex projections + the
    # belongs_to edge table (declared outside EDGE_REGISTRY). D4 adds v_theme
    # (theme projection) + e_exposed_to (company -> theme edge), also declared
    # outside the registry for the same mixed-endpoint reason.
    for t in _EXTRA_MATERIALIZED:
        con.execute(f"DROP TABLE IF EXISTS {t}")

    _materialise_vertices(con)
    _materialise_edges(con)
    _materialise_note_embeddings(con)
    # Phase E (duckpgq retirement): no property graph is declared any more —
    # the pattern queries are plain SQL JOINs over these materialised tables
    # and the algorithms run on onager.


def _resolve_duckdb_path(db_path: Path) -> Path:
    """Same resolution rule as connect(): production pair or sibling."""
    if db_path == DB_PATH:
        return DUCKDB_PATH
    return db_path.with_suffix(".duckdb")


def _rebuild_via_swap(db_path: Path | str = DB_PATH, *, fresh: bool) -> None:
    """Build the cache into a temp sibling, then atomically swap it in.

    Deadlock fix (2026-08-26, second instance): rebuilding IN PLACE needs
    an exclusive read-write open of the live file, which conflicts with
    every concurrent reader — observed live as POST /api/graph/refresh
    failing 500 against the RO-holding parallel advisory-gate steps (and
    vice versa). Building into ``<path>.rebuild-<pid>.tmp`` never touches
    the live file; ``os.replace`` swaps it in atomically. In-flight
    readers keep serving the old inode until they close (stale-by-one-
    rebuild is the documented refresh contract anyway).

    Two concurrent rebuilds each get their own pid-tagged temp and race
    only on the final rename — last writer wins, both files valid.
    """
    import os

    duckdb_path = _resolve_duckdb_path(Path(db_path))
    tmp = duckdb_path.with_name(f"{duckdb_path.name}.rebuild-{os.getpid()}.tmp")
    tmp_wal = tmp.with_name(tmp.name + ".wal")
    tmp.unlink(missing_ok=True)
    tmp_wal.unlink(missing_ok=True)
    try:
        c = connect(db_path=db_path, duckdb_path=tmp, rebuild=True, fresh=fresh)
        c.close()
        # Clean close leaves no WAL behind; refuse to swap otherwise.
        if tmp_wal.exists():
            raise RuntimeError(
                f"rebuild temp not cleanly closed: {tmp_wal} still exists")
        os.replace(tmp, duckdb_path)
    finally:
        tmp.unlink(missing_ok=True)
        tmp_wal.unlink(missing_ok=True)


def rebuild(db_path: Path | str = DB_PATH) -> None:
    """Rebuild materialised tables (drop + recreate + redeclare).

    Use after any SQLite-side change to ``entities`` or ``graph_edges``
    (``parse_newsletter --apply``, ``derive-relations``, stub batches).
    Idempotent.

    Since 2026-08-26 the build happens in a pid-tagged temp file swapped
    in atomically (see :func:`_rebuild_via_swap`) — a rebuild no longer
    requires an exclusive lock on the live cache, so it cannot deadlock
    against concurrent read-only holders (app server, parallel gate
    steps).
    """
    # Drop stale cached results so the next query re-reads post-rebuild.
    clear_graph_cache()
    _rebuild_via_swap(db_path, fresh=False)


def fresh_rebuild(db_path: Path | str = DB_PATH) -> None:
    """Drop the ``.duckdb`` file entirely and rebuild from scratch.

    Use after DuckDB/Onager version bumps, materialisation-schema
    changes (bump ``_SCHEMA_VERSION``), or to recover from corruption.
    Idempotent. Same atomic temp-swap strategy as :func:`rebuild`.
    """
    clear_graph_cache()
    _rebuild_via_swap(db_path, fresh=True)


def update_extensions() -> list[tuple[str, str]]:
    """Check installed DuckDB extensions against their source repo and update.

    Returns a list of ``(extension_name, new_version)`` tuples for the
    extensions that were updated. Network-dependent — may take several
    seconds. Run periodically (weekly is sufficient) via
    ``make update-extensions``. DuckDB recommends a process restart after
    updating; that happens naturally for us since every ``connect()`` is
    a fresh process.

    Not called by ``connect()`` — the ~5s network round-trip would
    dominate warm-connect cost (see §18).
    """
    con = duckdb.connect()
    try:
        # DuckDB exposes installed extensions via duckdb_extensions().
        before = {
            r[1]: r[4] for r in con.execute(
                "SELECT * FROM duckdb_extensions() WHERE installed"
            ).fetchall()
        }
        con.execute("UPDATE EXTENSIONS;")
        after = {
            r[1]: r[4] for r in con.execute(
                "SELECT * FROM duckdb_extensions() WHERE installed"
            ).fetchall()
        }
        changed = [(name, after[name]) for name in after
                   if name in before and before[name] != after[name]]
        return changed
    finally:
        con.close()


def _materialise_vertices(con: duckdb.DuckDBPyConnection) -> None:
    """Create a single vertex table with globally-unique contiguous IDs.

    duckpgq v1.5 silently merges vertex-table IDs across tables when more
    than one VERTEX TABLE is declared — the CSR builder treats the union of
    all vertex IDs as a single pool, so `v_company.id=3` and `v_sector.id=3`
    become the same vertex and weakly_connected_component returns bogus
    component counts (3 super-components where 42 are expected). See
    the retired-duckpgq archive proposal for the full bug history.

    The fix is to use ONE vertex table (`v_node`) containing both companies
    and sectors, with `row_number() OVER ()` producing globally-unique
    contiguous IDs. The `kind` column ('company' | 'sector') preserves the
    label information for WHERE filters and presentation.

    For backwards compatibility with existing MATCH queries that referenced
    `:Company` and `:Sector` labels, we also create `v_company` and `v_sector`
    as **filtered projections** of `v_node`. These projections share the same
    `id` column so edge-table JOINs against either work identically.
    However, only `v_node` is declared as a VERTEX TABLE in the property
    graph — the projections are NOT separate labels.
    """
    con.execute(
        """
        CREATE TABLE v_node AS
        SELECT row_number() OVER (ORDER BY e.entity_type, e.name) AS id,
               e.name,
               e.entity_type AS kind,
               e.sector_classification,
               -- Bundle C2: market_cap is sourced from entity_tags (the
               -- market_cap/* tag is the source of truth; the entities
               -- column was dropped). SUBSTR strips the 'market_cap/' prefix
               -- so the value still reads 'large_cap' etc., matching what
               -- every c.market_cap consumer (sector_members,
               -- sector_members_with_market_cap) expects.
               --
               -- Bundle C2 fix (2026-07-28): a correlated subselect (not a
               -- LEFT JOIN) picks exactly ONE tag per entity, so a company
               -- with MULTIPLE conflicting market_cap/* tags (a data error
               -- — 41 companies have this, e.g. Alkem Labs has both
               -- large_cap and mid_cap) does NOT fan out into multiple
               -- v_node rows. MIN(tag) is the deterministic tie-break; it
               -- surfaces the conflict by always picking the
               -- alphabetically-first (most-optimistic) tier. The right
               -- fix is to de-duplicate the tags at the source; this CTAS
               -- just guarantees the vertex table stays 1-row-per-entity
               -- regardless.
               SUBSTR(
                 (SELECT MIN(t.tag) FROM fin.entity_tags t
                  WHERE t.entity_name = e.name AND t.tag LIKE 'market_cap/%'),
                 LENGTH('market_cap/') + 1
               ) AS market_cap,
               e.ticker
        FROM fin.entities e
        WHERE e.entity_type IN ('company', 'sector', 'super_sector',
                                'sub_sector', 'theme', 'edition',
                                'institution')
        """
    )
    # Filtered projections used by edge-table JOINs (resolve by name → id).
    # Both share v_node's id space, so no collision is possible.
    con.execute(
        "CREATE TABLE v_company AS "
        "SELECT id, name, sector_classification, market_cap, ticker "
        "FROM v_node WHERE kind='company'"
    )
    con.execute(
        "CREATE TABLE v_sector AS SELECT id, name FROM v_node WHERE kind='sector'"
    )
    # Bundle M4: super_sector / sub_sector projections. Used by the
    # e_belongs_to CTAS and the hierarchy query helpers (super_sector_of,
    # sub_sectors_of). Sectors, super-sectors, and sub-sectors all share
    # v_node's id space, so a belongs_to edge resolves to consistent ids.
    con.execute(
        "CREATE TABLE v_super_sector AS "
        "SELECT id, name FROM v_node WHERE kind='super_sector'"
    )
    con.execute(
        "CREATE TABLE v_sub_sector AS "
        "SELECT id, name FROM v_node WHERE kind='sub_sector'"
    )
    # D4: theme projection. Cross-sector dimension nodes (China+1, PLI, ...).
    # Endpoint of the exposed_to edge (company -> theme); mixed kind-pair, so
    # like belongs_to it gets a dedicated CTAS rather than the binary
    # EDGE_REGISTRY loop (which only resolves company<->sector).
    con.execute(
        "CREATE TABLE v_theme AS SELECT id, name FROM v_node WHERE kind='theme'"
    )
    # okf_activation P: edition projection. Newsletter-edition nodes (name =
    # note stem) are the target of cited_in (company/sector -> edition).
    # Out-of-registry like v_theme for the same mixed-endpoint reason.
    con.execute(
        "CREATE TABLE v_edition AS SELECT id, name FROM v_node WHERE kind='edition'"
    )
    # Relations 2.0 E5: institution projection — endpoint of invested_in
    # (institution → company). Out-of-registry size is trivial (dozens of US
    # holders), but keeping a dedicated projection mirrors the v_theme/v_edition
    # pattern and lets the generic EDGE_REGISTRY loop JOIN on v_institution.
    con.execute(
        "CREATE TABLE v_institution AS SELECT id, name FROM v_node WHERE kind='institution'"
    )
    # P2.5: v_embeddings — vector embeddings for semantic similarity search.
    # Sourced from fin.company_embeddings (FLOAT[emb_dim]) if it exists in the
    # SQLite DB; created as an empty table with a matching schema if not (so
    # wrappers can test existence without a special case). The FLOAT[N] type
    # is required by DuckDB's VSS scalar functions (array_cosine_similarity,
    # array_distance, etc.).
    #
    # Population is handled by helpers/graph/embeddings.py, which fetches
    # real embeddings from an LLM API (OpenAI text-embedding-3-small by
    # default) and writes them to the SQLite source. On the next rebuild
    # (or connect with rebuild=True), the embeddings are projected from
    # SQLite into DuckDB via the CTAS below.
    #
    # If the SQLite table doesn't exist yet (fresh DB), create an empty
    # DuckDB table with the right schema so semantic_neighbors() can return
    # empty results instead of an error.
    _materialise_embeddings(con)


def _materialise_embeddings(con: duckdb.DuckDBPyConnection) -> None:
    """Project company embeddings from SQLite into DuckDB.

    If ``fin.company_embeddings`` exists in the SQLite source (populated by
    ``helpers/graph/embeddings.py``), materialises it as a DuckDB table
    joined to ``v_node.id``. The ``embedding`` column is ``FLOAT[]`` — the
    type required by DuckDB VSS scalar functions (``array_cosine_similarity``,
    ``array_negative_inner_product``, ``array_distance``).

    If the SQLite table does not exist, creates an empty DuckDB table with
    the matching schema so ``semantic_neighbors()`` returns empty results
    instead of raising. This lets the module load cleanly on databases that
    haven't been embedded yet.

    HNSW index acceleration (``CREATE INDEX ... USING HNSW``) is not
    applied here — the vss extension's index-scan macros are broken on
    vss b833341/duckdb 1.5.4. Scalar functions are used instead; at ~1k
    companies the brute-force scan is ~3ms. If the macros are fixed in a
    future version, the index can be added to this function.
    """
    try:
        r = con.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='company_embeddings'"
        ).fetchone()
        table_exists = r is not None and r[0] > 0
    except Exception:
        table_exists = False

    if table_exists:
        # The SQLite bridge tries to auto-convert FLOAT[N] columns (stored as
        # JSON text) and fails with a TypeMismatchError. Setting
        # sqlite_all_varchar=true makes the bridge read columns as VARCHAR,
        # then we CAST to FLOAT[] for the VSS scalar functions.
        con.execute('SET sqlite_all_varchar=true')
        con.execute(
            """
            CREATE TABLE v_embeddings AS
            SELECT ve.company_name,
                   v.id,
                   CAST(ve.embedding AS FLOAT[]) AS embedding
            FROM fin.company_embeddings ve
            JOIN v_node v ON v.name = ve.company_name
            """
        )
    else:
        con.execute(
            """
            CREATE TABLE v_embeddings (
                company_name VARCHAR,
                id BIGINT,
                embedding FLOAT[]
            )
            """
        )


def _materialise_note_embeddings(con: duckdb.DuckDBPyConnection) -> int:
    """Project the note_search FTS index's embedding column into DuckDB.

    sql_capability_unlocks A1: ``v_note_embeddings`` is the whole-corpus
    vector table (one row per embedded findata doc) that backs
    similar_notes / notes_like_entity / edition_companies /
    near_duplicate_notes. Source is ``fin.note_search`` — an FTS5 virtual
    table, so the DuckDB scanner reads its shadow content table; the
    bridge needs ``sqlite_all_varchar=true`` issued at THIS site (the
    build calls this function directly — the SET inside
    _materialise_embeddings may not have run yet; the SET itself is
    idempotent and connection-wide).

    Dims are probed on one stored row filtered the same way the CTAS
    filters (``IS NOT NULL AND != ''`` — the stored_embed_dims()
    discipline; unparsable JSON counts as absent, via json_array_length
    returning NULL). Zero probeable rows (fresh DB, unembedded index)
    falls back to an empty typed table so wrappers degrade to ``[]``
    instead of raising (mirrors _materialise_embeddings' fallback).

    Returns the resolved dims (0 = empty/unembedded) — _mark_warm stamps
    it into _build_meta as ``note_embed_dims`` for the warm-path drift
    check in _is_warm.
    """
    try:
        r = con.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='note_search'"
        ).fetchone()
        table_exists = r is not None and r[0] > 0
    except Exception:
        table_exists = False

    dims = 0
    if table_exists:
        con.execute('SET sqlite_all_varchar=true')
        try:
            row = con.execute(
                "SELECT json_array_length(embedding) FROM fin.note_search "
                "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
            ).fetchone()
            dims = int(row[0]) if row and row[0] is not None else 0
        except Exception:
            dims = 0

    if table_exists and dims > 0:
        con.execute(
            f"""
            CREATE TABLE v_note_embeddings AS
            SELECT file_path, doc_type, title,
                   CAST(embedding AS FLOAT[{dims}]) AS emb
            FROM fin.note_search
            WHERE embedding IS NOT NULL AND embedding != ''
            """  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        )
    else:
        con.execute(
            """
            CREATE TABLE v_note_embeddings (
                file_path VARCHAR,
                doc_type VARCHAR,
                title VARCHAR,
                emb FLOAT[]
            )
            """
        )
    return dims


def _stage_edges(con: duckdb.DuckDBPyConnection) -> None:
    """Copy ``fin.graph_edges`` into a session-local TEMP table once per build.

    The ~16 edge CTAS in this module each re-scan the attached SQLite
    table, and the sqlite-scanner's fixed per-scan overhead dominates on
    small corpora (measured on a 370-entity fixture: 0.84s of execute
    time in a 1.2s cold build, roughly flat in row count). Staging once
    and pointing every CTAS at the local copy collapses that to a single
    scanner pass.

    TEMP tables are session-scoped and never reach the ``.duckdb`` file,
    so warm connects, snapshots, and the post-close catalog are
    unaffected. ``CREATE OR REPLACE`` makes a same-session second build
    (e.g. a future in-place refresh path) safe.

    Type parity note: when ``_materialise_embeddings`` has already run
    (company_embeddings exists), ``sqlite_all_varchar=true`` is set
    connection-wide, and the stage copies columns as VARCHAR — exactly
    what the CTAS previously read from the attached table under the same
    setting, so the e_* schemas are unchanged either way.
    """
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _stg_edges AS "
        "SELECT * FROM fin.graph_edges"
    )


def _materialise_edges(con: duckdb.DuckDBPyConnection) -> None:
    """Create per-label edge tables for every registered edge type.

    All CTAS here read the staged ``_stg_edges`` copy (see
    ``_stage_edges``), not the attached SQLite table directly.

    Source/target columns are resolved to integer vertex IDs at
    materialisation time via JOIN on `name`. Integer keys are what the
    Onager table functions require (src/dst as BIGINT; see
    helpers/graph/onager.py) — and what the retired duckpgq CSR layer
    required before them.

    Tables are created unconditionally (even if empty) so wrappers can
    query them without a special case. Empty tables are filtered out at
    property-graph declaration time.

    The JOINs go against `v_company`/`v_sector` projections, which are
    filtered views of `v_node` sharing the SAME id space (see
    `_materialise_vertices`). The result is that all edge endpoints resolve
    to globally-unique v_node ids, regardless of whether the endpoint is a
    company or sector. This is what makes the single-vertex-table property
    graph declaration work correctly.
    """
    _stage_edges(con)
    # Generic kind → projection table mapping (supports the institution kind
    # introduced for invested_in without special-casing each branch).
    _KIND_TO_TABLE: dict[str, str] = {
        "company": "v_company",
        "sector": "v_sector",
        "super_sector": "v_super_sector",
        "sub_sector": "v_sub_sector",
        "theme": "v_theme",
        "edition": "v_edition",
        "institution": "v_institution",
    }
    for etype, spec in EDGE_REGISTRY.items():
        # E5: invested_in has a mixed source — institution holders plus a
        # handful of company holders (e.g. Sanofi as holder) that already
        # exist as company entities. The generic v_institution JOIN would
        # drop the company-holder edge (714 vs 715). Handle via v_node
        # with kind IN so both institutions and companies are resolved.
        if etype == "invested_in":
            src_id_col = spec["src"]
            dst_id_col = spec["dst"]
            con.execute(
                f"""
                CREATE TABLE {spec["table"]} AS
                SELECT src.id   AS {src_id_col},
                       dst.id   AS {dst_id_col},
                       ge.weight, ge.properties, ge.source_ref,
                       ge.valid_from, ge.valid_to
                FROM _stg_edges ge
                JOIN v_node src ON src.name = ge.source
                              AND src.kind IN ('institution', 'company')
                JOIN v_node dst ON dst.name = ge.target
                              AND dst.kind = 'company'
                WHERE ge.edge_type = '{etype}'
                """  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            )
            continue
        src_table = _KIND_TO_TABLE.get(spec["src_kind"], "v_company")
        dst_table = _KIND_TO_TABLE.get(spec["dst_kind"], "v_company")
        src_id_col = spec["src"]
        dst_id_col = spec["dst"]
        # Bundle L2: e_acquired carries a typed `year` column projected from
        # properties JSON ONCE at materialise time, so acquisitions() and the
        # AcquiredBy arm of company_neighbors_bundle read it directly instead
        # of calling json_extract_string(e.properties, 'year') per query.
        # json_extract_string returns VARCHAR (matches the consumers' string
        # contract); COALESCE gives '' for the ~10/22 edges with no year.
        # Only e_acquired gets the extra column — other edge types have no
        # single hot-read JSON key worth denormalising (see Bundle L2 notes).
        year_col = ""
        if etype == "acquired":
            year_col = (
                ", COALESCE(json_extract_string(ge.properties, 'year'), '') "
                "AS year"
            )
        con.execute(
            f"""
            CREATE TABLE {spec["table"]} AS
            SELECT src.id   AS {src_id_col},
                   dst.id   AS {dst_id_col},
                   ge.weight, ge.properties, ge.source_ref,
                   ge.valid_from, ge.valid_to{year_col}
            FROM _stg_edges ge
            JOIN {src_table} src ON src.name = ge.source
            JOIN {dst_table} dst ON dst.name = ge.target
            WHERE ge.edge_type = '{etype}'
            """  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        )

    # Bundle M4: `belongs_to` is the sector-hierarchy edge (sector ->
    # super_sector AND sub_sector -> sector). It can't go through the
    # generic registry loop above because that loop is structurally binary
    # (src_kind/dst_kind each resolve to exactly one of v_company/v_sector).
    # belongs_to has MIXED endpoints, so it gets a dedicated CTAS that JOINs
    # v_node directly with kind filters. The two valid kind-pairs are:
    #   sector -> super_sector   (42 edges)
    #   sub_sector -> sector     (21 edges)
    # Created unconditionally even if empty (no hierarchy built yet) so the
    # property-graph declaration can skip it cleanly.
    con.execute(
        """
        CREATE TABLE e_belongs_to AS
        SELECT src.id AS child_id,
               dst.id AS parent_id,
               ge.weight, ge.properties, ge.source_ref,
               ge.valid_from, ge.valid_to
        FROM _stg_edges ge
        JOIN v_node src ON src.name = ge.source
                      AND src.kind IN ('sector', 'sub_sector')
        JOIN v_node dst ON dst.name = ge.target
                      AND dst.kind IN ('super_sector', 'sector')
        WHERE ge.edge_type = 'belongs_to'
        """
    )
    # D4: the exposed_to edge (company -> theme). Same out-of-registry reason
    # as belongs_to: mixed endpoint kinds (company vs theme) don't fit the
    # binary EDGE_REGISTRY loop (which resolves only company<->sector). JOINs
    # v_node directly with kind filters. Created unconditionally so the
    # property-graph declaration can skip it cleanly when empty.
    con.execute(
        """
        CREATE TABLE e_exposed_to AS
        SELECT src.id AS company_id,
               dst.id AS theme_id,
               ge.weight, ge.properties, ge.source_ref,
               ge.valid_from, ge.valid_to
        FROM _stg_edges ge
        JOIN v_node src ON src.name = ge.source
                      AND src.kind = 'company'
        JOIN v_node dst ON dst.name = ge.target
                      AND dst.kind = 'theme'
        WHERE ge.edge_type = 'exposed_to'
        """
    )
    # okf_activation P: the cited_in edge (company/sector -> edition) — OKF
    # provenance made traversable. Same out-of-registry reason as the two
    # above: mixed endpoint kinds. Created unconditionally so it exists
    # cleanly when empty.
    con.execute(
        """
        CREATE TABLE e_cited_in AS
        SELECT src.id AS company_id,
               dst.id AS edition_id,
               ge.weight, ge.properties, ge.source_ref,
               ge.valid_from, ge.valid_to
        FROM _stg_edges ge
        JOIN v_node src ON src.name = ge.source
                      AND src.kind IN ('company', 'sector', 'super_sector')
        JOIN v_node dst ON dst.name = ge.target
                      AND dst.kind = 'edition'
        WHERE ge.edge_type = 'cited_in'
        """
    )
    # sql_capability_unlocks B1: whole-graph adjacency substrates for the
    # walk queries (see _materialise_walk_substrate for the details).
    _materialise_walk_substrate(con)


def _materialise_walk_substrate(con: duckdb.DuckDBPyConnection) -> None:
    """Create the whole-graph adjacency substrates for the walk queries.

    e_dir keeps each edge in its STORED direction (the directed walk
    find_cycles needs — on a doubled table every edge would read as a
    false 2-cycle and drown the diagnostic). e_all_und doubles every edge
    in both directions (the undirected BFS shortest_path walks). Both
    cover ALL edge types (registry + belongs_to/exposed_to/cited_in — the
    relations view is unfiltered) and carry edge_type + validity so
    per-query label/temporal filters stay WHERE clauses instead of joins
    back to fin.graph_edges. Endpoints resolve to v_node ids like every
    other e_* table; edges with a dangling endpoint (name not in
    entities) drop here, which FK constraints already forbid.

    Reads ``_stg_edges`` (the staged local copy of fin.graph_edges), so
    a direct caller must run ``_stage_edges(con)`` first — the
    production path gets that via ``_materialise_edges``.

    Split out of _materialise_edges so tests with a hand-built minimal
    v_node(id, name) can mount the SAME substrate the production build
    produces — single source for the CTAS shapes.
    """
    con.execute(
        """
        CREATE TABLE e_dir AS
        SELECT src.id AS a_id, dst.id AS b_id,
               ge.edge_type, ge.valid_from, ge.valid_to
        FROM _stg_edges ge
        JOIN v_node src ON src.name = ge.source
        JOIN v_node dst ON dst.name = ge.target
        """
    )
    con.execute(
        """
        CREATE TABLE e_all_und AS
        SELECT a_id, b_id, edge_type, valid_from, valid_to FROM e_dir
        UNION ALL
        SELECT b_id, a_id, edge_type, valid_from, valid_to FROM e_dir
        """
    )


# --------------------------------------------------------------------------- #
# Query wrappers
# --------------------------------------------------------------------------- #
def _normalise_as_of(as_of: str | None) -> str | None:
    """Accept '2023', '2023-06', or '2023-06-15' → 'YYYY-MM-DD' string.

    Returns None if `as_of` is None, empty, or whitespace-only. Raises
    ValueError on shapes that don't match the three supported forms so
    callers can surface a 400 instead of letting the malformed value reach SQL.
    """
    if not as_of:
        return None
    s = str(as_of).strip()
    if not s:
        return None
    # Year only: '2023'
    if len(s) == 4 and s.isdigit():
        return f"{s}-01-01"
    # Year-month: '2023-06'
    if len(s) == 7 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit():
        return f"{s}-01"
    # Full date: '2023-06-15'
    if (len(s) == 10 and s[:4].isdigit() and s[4] == "-"
            and s[5:7].isdigit() and s[7] == "-" and s[8:10].isdigit()):
        return s
    raise ValueError(
        "as_of must be a year (YYYY), year-month (YYYY-MM), or date (YYYY-MM-DD)")


def _as_of_predicate(as_of: str | None, edge_alias: str = "e") -> str:
    """Build the temporal WHERE fragment for a GRAPH_TABLE query.

    NULL semantics: edges with `valid_from IS NULL` are treated as always
    valid (open-ended). This matters because today only the `acquired` edge
    type carries `valid_from` (12 of 3,501 edges); treating NULL as "drop
    when filtered" would nuke the structural backbone of the graph. Returns
    '' when `as_of` is None/empty (today's default behaviour).
    """
    iso = _normalise_as_of(as_of)
    if iso is None:
        return ""
    # iso is already validated by the strict shape check in _normalise_as_of,
    # so it is safe to interpolate into this SQL fragment (it is composed
    # into larger CTE SQL, not passed as a bound parameter).
    return (
        f" AND ({edge_alias}.valid_from IS NULL "
        f"OR {edge_alias}.valid_from <= '{iso}')"
        f" AND ({edge_alias}.valid_to IS NULL "
        f"OR {edge_alias}.valid_to >= '{iso}')"
    )


@_with_generation_cache
def sector_of(con: duckdb.DuckDBPyConnection, company: str,
              as_of: str | None = None) -> str | None:
    """Return the sector name for a company, or None.

    `as_of` (optional ISO date or year) filters out part_of edges that weren't
    valid at the given date. NULL valid_from is treated as always-valid.
    """
    r = con.execute(
        """
        SELECT v_s.name AS sector
        FROM e_belongs e
        JOIN v_node v_c ON v_c.id = e.company_name
        JOIN v_node v_s ON v_s.id = e.sector_name
        WHERE v_c.name = ? AND v_c.kind = 'company' AND v_s.kind = 'sector'
        """ + _as_of_predicate(as_of),  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [company],
    ).fetchall()
    return r[0][0] if r else None


@_with_generation_cache
def sector_members(con: duckdb.DuckDBPyConnection, sector: str,
                   market_cap: str | None = None) -> list[str]:
    """All companies in a sector, optionally filtered by market_cap."""
    where = "v_s.name = ? AND v_s.kind = 'sector' AND v_c.kind = 'company'"
    params: list[str] = [sector]
    if market_cap:
        where += " AND v_c.market_cap = ?"
        params.append(market_cap)
    r = con.execute(
        """
        SELECT v_c.name AS company
        FROM e_belongs e
        JOIN v_node v_c ON v_c.id = e.company_name
        JOIN v_node v_s ON v_s.id = e.sector_name
        WHERE """ + where,  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        params,
    ).fetchall()
    return sorted(row[0] for row in r)


@_with_generation_cache
def theme_members(con: duckdb.DuckDBPyConnection, theme: str) -> list[str]:
    """All companies exposed to a cross-sector theme (D4).

    The exposed_to edge is company -> theme; this returns the source companies.
    Themes cut across the GICS hierarchy (China+1 = Electronics + EMS + Pharma
    + Textiles), so this is the orthogonal-membership query.
    """
    r = con.execute(
        """
        SELECT v_c.name AS company
        FROM e_exposed_to e
        JOIN v_node v_c ON v_c.id = e.company_id
        JOIN v_node v_t ON v_t.id = e.theme_id
        WHERE v_t.name = ? AND v_t.kind = 'theme' AND v_c.kind = 'company'
        """,
        [theme],
    ).fetchall()
    return sorted(row[0] for row in r)


@_with_generation_cache
def sector_members_with_market_cap(
    con: duckdb.DuckDBPyConnection, sector: str,
    market_cap: str | None = None,
) -> list[tuple[str, str | None]]:
    """Same as ``sector_members`` but also returns each member's market_cap.

    Bundle K2: the /api/graph/neighbors sector-focal path used to fire a
    SECOND query (SQLite ``WHERE name IN (...) GROUP BY market_cap``) to get
    the market-cap distribution — a Python-mediated GROUP BY between two
    databases. Projecting ``c.market_cap`` in the same GRAPH_TABLE that
    fetches the members collapses the two-trip cross-DB hop into one DuckDB
    query.

    Returns sorted list of ``(company_name, market_cap_or_None)`` tuples.
    The market_cap comes from the same DuckDB vertex row as the membership,
    so ``bucketize()`` over this list preserves the "sum(buckets) == len"
    invariant the old code documented (DuckDB graph edges vs the SQLite
    column can drift — Bundle E5).
    """
    where = "v_s.name = ? AND v_s.kind = 'sector' AND v_c.kind = 'company'"
    params: list[str] = [sector]
    if market_cap:
        where += " AND v_c.market_cap = ?"
        params.append(market_cap)
    r = con.execute(
        """
        SELECT v_c.name AS company, v_c.market_cap AS market_cap
        FROM e_belongs e
        JOIN v_node v_c ON v_c.id = e.company_name
        JOIN v_node v_s ON v_s.id = e.sector_name
        WHERE """ + where,  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        params,
    ).fetchall()
    return sorted((row[0], row[1]) for row in r)


# --------------------------------------------------------------------------- #
# Bundle M4: sector-hierarchy queries (super-sector / sub-category)            #
# --------------------------------------------------------------------------- #
# These traverse the `belongs_to` edge (label BelongsToHierarchy), the
# sector->super_sector and sub_sector->sector links added by
# build_sector_hierarchy.py. They mirror sector_of/sector_members but operate
# one level up/down the hierarchy. A sector with no authored sub-categories
# returns [] from sub_sectors_of (37 of 42 sectors).


@_with_generation_cache
def super_sector_of(con: duckdb.DuckDBPyConnection, sector: str) -> str | None:
    """Return the super-sector name for a sector, or None."""
    r = con.execute(
        """
        SELECT v_p.name AS super_sector
        FROM e_belongs_to e
        JOIN v_node v_c ON v_c.id = e.child_id
        JOIN v_node v_p ON v_p.id = e.parent_id
        WHERE v_c.name = ? AND v_c.kind = 'sector' AND v_p.kind = 'super_sector'
        """,
        [sector],
    ).fetchall()
    return r[0][0] if r else None


@_with_generation_cache
def sectors_in_super(con: duckdb.DuckDBPyConnection, super_sector: str) -> list[str]:
    """All sectors belonging to a super-sector, sorted."""
    r = con.execute(
        """
        SELECT v_c.name AS sector
        FROM e_belongs_to e
        JOIN v_node v_c ON v_c.id = e.child_id
        JOIN v_node v_p ON v_p.id = e.parent_id
        WHERE v_p.name = ? AND v_p.kind = 'super_sector' AND v_c.kind = 'sector'
        """,
        [super_sector],
    ).fetchall()
    return sorted(row[0] for row in r)


@_with_generation_cache
def sub_sectors_of(con: duckdb.DuckDBPyConnection, sector: str) -> list[str]:
    """Sub-categories within a sector (empty for the 37 sectors without any)."""
    r = con.execute(
        """
        SELECT v_c.name AS sub_sector
        FROM e_belongs_to e
        JOIN v_node v_c ON v_c.id = e.child_id
        JOIN v_node v_p ON v_p.id = e.parent_id
        WHERE v_p.name = ? AND v_p.kind = 'sector' AND v_c.kind = 'sub_sector'
        """,
        [sector],
    ).fetchall()
    return sorted(row[0] for row in r)


@_with_generation_cache
def neighbors(con: duckdb.DuckDBPyConnection, entity: str,
              max_hops: int = 1) -> list[tuple[str, str, str]]:
    """1-hop neighbours of an entity via BelongsTo/HasCompany.

    Returns list of (direction, other_name, edge_label):
      direction is 'out' (entity→other) or 'in' (other→entity).
    """
    if max_hops != 1:
        raise NotImplementedError("multi-hop neighbors — use sql() with SHORTEST")

    # Bundle K3 (rewritten plain-SQL, Phase B of the duckpgq retirement):
    # the 4 arms (out/in x BelongsTo/HasCompany) as one UNION ALL of JOINs
    # over the materialised e_* tables. Same (dir, other, label) shape per
    # arm; the final set-dedup + sort (Bundle F3) is unchanged.
    r = con.execute(
        """
        SELECT 'out' AS dir, v_s.name AS other, 'BelongsTo' AS label
        FROM e_belongs e
        JOIN v_node v_c ON v_c.id = e.company_name
        JOIN v_node v_s ON v_s.id = e.sector_name
        WHERE v_c.name = ?
        UNION ALL
        SELECT 'out', v_c.name, 'HasCompany'
        FROM e_has e
        JOIN v_node v_s ON v_s.id = e.sector_name
        JOIN v_node v_c ON v_c.id = e.company_name
        WHERE v_s.name = ?
        UNION ALL
        SELECT 'in', v_c.name, 'BelongsTo'
        FROM e_belongs e
        JOIN v_node v_c ON v_c.id = e.company_name
        JOIN v_node v_s ON v_s.id = e.sector_name
        WHERE v_s.name = ?
        UNION ALL
        SELECT 'in', v_s.name, 'HasCompany'
        FROM e_has e
        JOIN v_node v_s ON v_s.id = e.sector_name
        JOIN v_node v_c ON v_c.id = e.company_name
        WHERE v_c.name = ?
        """,
        [entity] * 4,
    ).fetchall()
    # Dedup (Bundle F3): inverse edge types (BelongsTo vs HasCompany) can
    # emit the same (dir, other, label) tuple twice when both directions are
    # materialised. peers()/group_siblings() already dedup via set(); this
    # brings neighbors() in line. Sorted for stable CLI output.
    return sorted({(d, o, label) for (d, o, label) in r})


def shortest_path(con: duckdb.DuckDBPyConnection, src: str, dst: str,
                  max_hops: int = 5, edge_label: str | None = "BelongsTo",
                  as_of: str | None = None) -> list[tuple[str, int]] | None:
    """Shortest path src → dst via the given edge label (level-by-level BFS).

    sql_capability_unlocks B2: the recursive-CTE walk over the attached
    ``fin.graph_edges`` table is replaced by a BFS over the materialised
    undirected adjacency ``e_all_und``. The old CTE materialised every
    simple path ≤ max_hops before ``ORDER BY depth LIMIT 1`` picked one —
    multi-second at hops=5, unbounded at hops=10. BFS touches each edge
    once per level: O(max_hops · (V + E)), with true hop-shortest
    semantics guaranteed by construction (layer order) rather than
    approximated after full enumeration. ``_shortest_path_cte`` survives
    below as the small-fixture oracle the equivalence tests compare
    against.

    Returns a list of (vertex_name, hop_index) tuples from src to dst
    (inclusive), or None if no path within max_hops.

    ``edge_label`` restricts traversal to that edge type (resolved via
    EDGE_REGISTRY); ``None`` or an unrecognized label means no filter —
    traverses all edge types. ``as_of`` filters each hop temporally (valid_from/valid_to
    window must contain the date; NULL valid_from is always-valid).
    """
    return _shortest_path_bfs(con, src, dst, max_hops,
                              edge_label=edge_label, as_of=as_of)


def _bfs_step_where(edge_label: str | None,
                    as_of: str | None) -> tuple[str, list]:
    """Per-query WHERE fragments + binds for one BFS step.

    edge_label resolves via EDGE_REGISTRY; None or an unrecognized label =
    no filter (the historical behaviour). The temporal window reads
    e_all_und's carried validity columns.
    """
    params: list = []
    clauses = ""
    if edge_label is not None:
        reg = EDGE_REGISTRY_BY_LABEL.get(edge_label)
        if reg is not None:
            clauses += " AND e.edge_type = ?"
            params.append(reg["edge_type"])
    iso = _normalise_as_of(as_of)
    if iso is not None:
        clauses += (
            " AND (e.valid_from IS NULL OR e.valid_from <= ?)"
            " AND (e.valid_to IS NULL OR e.valid_to >= ?)"
        )
        params.extend([iso, iso])
    return clauses, params


def _shortest_path_bfs(con: duckdb.DuckDBPyConnection, src: str, dst: str,
                       max_hops: int,
                       edge_label: str | None = None,
                       as_of: str | None = None) -> list[tuple[str, int]] | None:
    """BFS over ``e_all_und`` — the primary shortest-path implementation.

    Mechanics (pinned by the sql_capability_unlocks review): temp tables
    for frontier/visited/parents (visited reaches ~1.2k nodes, so an
    ``?``-list bind is the wrong shape — ``NOT EXISTS`` against a temp
    table scales and stays in SQL); each discovered node's parent is
    picked deterministically (``MIN(a_id)``) so path reconstruction is
    stable across runs; the frontier seeds from ``v_node`` by name.

    Contract pins mirroring the old CTE: ``src == dst`` → ``[(src, 0)]``
    when src is known; unknown src/dst → None; unreachable dst → None (a
    full-graph traversal — bounded by construction, which the enumeration
    CTE was not).

    All value interpolations are bind parameters (Part C): endpoint
    names, edge_type, and the validated as_of date never touch the SQL
    text, so the ``_CONTROL_RE`` NUL-crack class cannot reach this path.
    """
    hops = int(max_hops)
    if hops < 0:
        raise ValueError("max_hops must be >= 0")

    row = con.execute("SELECT id, name FROM v_node WHERE name = ?", [src]).fetchone()
    if row is None:
        return None
    src_id, src_name = row
    row = con.execute("SELECT id, name FROM v_node WHERE name = ?", [dst]).fetchone()
    if row is None:
        return None
    dst_id, dst_name = row

    if src_id == dst_id:
        return [(src_name, 0)]
    if hops == 0:
        return None

    step_where, params = _bfs_step_where(edge_label, as_of)

    try:
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _bfs_frontier AS "
            "SELECT ?::BIGINT AS id", [src_id])
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _bfs_visited AS "
            "SELECT ?::BIGINT AS id", [src_id])
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _bfs_parents ("
            "id BIGINT PRIMARY KEY, parent BIGINT)")
        for _level in range(hops):
            con.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE _bfs_next AS
                SELECT e.b_id AS id, MIN(e.a_id) AS parent
                FROM e_all_und e
                WHERE e.a_id IN (SELECT id FROM _bfs_frontier)
                  AND NOT EXISTS (SELECT 1 FROM _bfs_visited v
                                  WHERE v.id = e.b_id){step_where}
                GROUP BY e.b_id
                """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                params,
            )
            found = con.execute(
                "SELECT 1 FROM _bfs_next WHERE id = ? LIMIT 1", [dst_id]
            ).fetchone() is not None
            con.execute(
                "INSERT INTO _bfs_parents SELECT id, parent FROM _bfs_next")
            con.execute("INSERT INTO _bfs_visited SELECT id FROM _bfs_next")
            con.execute(
                "CREATE OR REPLACE TEMP TABLE _bfs_frontier AS "
                "SELECT id FROM _bfs_next")
            if found:
                return _bfs_reconstruct(con, src_name, dst_id, dst_name)
            if con.execute(
                    "SELECT 1 FROM _bfs_frontier LIMIT 1").fetchone() is None:
                return None  # walk exhausted — no path exists
    finally:
        for t in ("_bfs_frontier", "_bfs_visited", "_bfs_parents", "_bfs_next"):
            con.execute(f"DROP TABLE IF EXISTS temp.{t}")
    return None


def _bfs_reconstruct(con: duckdb.DuckDBPyConnection, src_name: str,
                     dst_id: int, dst_name: str) -> list[tuple[str, int]]:
    """Walk ``_bfs_parents`` from dst back to src; emit (name, hop) pairs.

    src has no parent row (it was seeded, never discovered), so the chain
    terminates exactly at src. Parents form a BFS tree rooted at src —
    strictly decreasing level — so the walk cannot loop.
    """
    seq: list[str] = []
    node = dst_id
    while True:
        row = con.execute(
            """
            SELECT p.parent, v.name
            FROM _bfs_parents p
            JOIN v_node v ON v.id = p.parent
            WHERE p.id = ?
            """,
            [node],
        ).fetchone()
        if row is None:
            break
        node, name = row
        seq.append(name)
    seq.reverse()          # [src, ..., parent(dst)]
    seq.append(dst_name)   # [src, ..., dst]
    return [(name, hop) for hop, name in enumerate(seq)]


def _shortest_path_cte(con: duckdb.DuckDBPyConnection, src: str, dst: str,
                       max_hops: int = 5,
                       edge_label: str | None = None,
                       as_of: str | None = None) -> list[tuple[str, int]] | None:
    """Recursive-CTE shortest-path walk (TEST ORACLE — not production).

    sql_capability_unlocks B2 retired this from the production path: it
    enumerates every simple path ≤ max_hops over the attached SQLite
    ``fin.graph_edges`` before picking one, which is the multi-second
    latency bomb ``_shortest_path_bfs`` replaces. Kept because the
    equivalence tests (tests/test_graph.py) use it as the oracle on small
    fixtures, where its cost is irrelevant and its independence from the
    BFS implementation is the point.

    Walks graph_edges directly as an undirected adjacency matrix and
    returns the full vertex sequence.

    `edge_label` restricts traversal to the requested edge type (matching
    the native path's semantics). Resolved via EDGE_REGISTRY (label →
    edge_type). None or an unrecognized label means no filter — traverses
    all edge types (the historical behavior for unknown labels).

    `as_of` filters each hop: edges whose valid_from/valid_to window does
    not contain the as_of date are excluded. NULL valid_from is treated as
    always-valid.
    """
    # Resolve edge_label → edge_type for the WHERE filter. An unrecognized
    # label (or None) means "traverse all edge types" — the historical
    # behavior when the caller passes an unknown label.
    edge_type_clause = ""
    if edge_label is not None:
        reg = EDGE_REGISTRY_BY_LABEL.get(edge_label)
        if reg is not None:
            edge_type_clause = f" AND ge.edge_type = {_lit(reg['edge_type'])}"

    # Build the temporal predicate against fin.graph_edges. The recursive
    # walk JOINs on ge.source / ge.target, so the filter goes in the JOIN's
    # WHERE clause.
    iso = _normalise_as_of(as_of)
    temporal_clause = ""
    if iso is not None:
        temporal_clause = (
            f" AND (ge.valid_from IS NULL OR ge.valid_from <= '{iso}')"
            f" AND (ge.valid_to IS NULL OR ge.valid_to >= '{iso}')"
        )
    query = f"""
    WITH RECURSIVE walk(node, depth, path) AS (
        SELECT name, 0, name AS path
        FROM fin.entities
        WHERE name = {_lit(src)}
      UNION ALL
        SELECT
            CASE WHEN ge.source = w.node THEN ge.target ELSE ge.source END,
            w.depth + 1,
            w.path || '||' ||
              CASE WHEN ge.source = w.node THEN ge.target ELSE ge.source END
        FROM walk w
        JOIN fin.graph_edges ge
          ON (ge.source = w.node OR ge.target = w.node){edge_type_clause}{temporal_clause}
        WHERE w.depth < {int(max_hops)}
          -- Cycle guard (Bundle F1): token-exact membership test, NOT a
          -- substring `instr()` (which falsely matched "ITC" inside
          -- "ITC Infotech" and pruned valid paths). Path is '||'-delimited,
          -- so string_to_array gives the vertex list and array_contains is
          -- exact-token comparison.
          AND NOT array_contains(
              string_to_array(w.path, '||'),
              CASE WHEN ge.source = w.node THEN ge.target ELSE ge.source END
          )
    )
    SELECT path, depth FROM walk WHERE node = {_lit(dst)}
    ORDER BY depth LIMIT 1
    """  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
    r = con.execute(query).fetchall()
    if not r:
        return None
    path_str, hops = r[0]
    nodes = path_str.split("||")
    return [(n, i) for i, n in enumerate(nodes)]


def sql(con: duckdb.DuckDBPyConnection, query: str) -> list[tuple]:
    """Run an arbitrary SQL query (typically with GRAPH_TABLE) and return rows."""
    return con.execute(query).fetchall()


def find_cycles(con: duckdb.DuckDBPyConnection, *,
                max_hops: int = 4,
                edge_label: str | None = None,
                limit: int = 100) -> list[list[str]]:
    """Find cycles in the directed graph (Bundle G3 — diagnostic helper).

    A cycle is a directed path ``A -> ... -> A`` of length 2..max_hops (a
    self-loop is impossible — graph_edges has CHECK (source != target)).
    Returns a list of cycles, each as the list of vertices visited in order
    (start vertex repeated at the end), e.g. ``[['A', 'B', 'C', 'A']]``.

    Unlike ``_shortest_path_cte`` (undirected, prunes cycles), this walks
    edges in their stored direction so a true directional cycle is required
    — ``A -> B`` plus ``B -> A`` (two directed rows) is a 2-cycle, but a
    single symmetric row ``A -> B`` with no return edge is NOT.

    Relevant for sanity-checking edges that are logically directed but could
    be mistakenly doubled: ``same_group``, ``co_mentioned_in`` are declared
    ``symmetric=1`` and stored as one directed row per pair (alphabetical),
    so they should produce NO cycles. ``acquired`` / ``subsidiary_of`` are
    strictly acyclic by definition — any cycle here is a data bug.

    Args:
      max_hops: cycle length cap (2..max_hops). Default 4 keeps the walk
        bounded on the 1070-node graph. Larger values explode combinatorially.
      edge_label: restrict to one edge type (e.g. 'SubsidiaryOf'). None =
        traverse all edge types.
      limit: cap on the number of cycles returned (default 100). The walk
        is bounded by max_hops but a dense subgraph can still emit many.

    Returns:
      List of cycles (each a vertex list, start repeated at end). Empty if
      the graph (or the filtered subgraph) is acyclic.
    """
    if max_hops < 2:
        raise ValueError("max_hops must be >= 2 (self-loops are schema-forbidden)")
    if max_hops > 6:
        raise ValueError("max_hops > 6 is combinatorially explosive; lower the cap")

    # Bind-parameter filter (Part C). An unrecognized label deliberately
    # yields no cycles (no edges match).
    params: list = []
    type_clause = ""
    if edge_label is not None:
        reg = EDGE_REGISTRY_BY_LABEL.get(edge_label)
        if reg is not None:
            type_clause = " AND e.edge_type = ?"
            params.append(reg["edge_type"])

    # Seed every node from v_node, then walk e_dir (stored direction) by
    # vertex id, carrying the name alongside for the path string — the
    # materialised directed substrate gives the ~2.9x constant factor over
    # the attached fin.graph_edges scan this walk used to pay per step
    # (sql_capability_unlocks B1). A cycle closes when an edge leads back
    # to the START node at depth >= 1.
    #
    # The cycle guard is subtler than the undirected walk's: we MUST allow the
    # closing edge (target == start) so the cycle can complete, but still
    # prevent the walk from revisiting any INTERMEDIATE vertex (which would
    # make the cycle non-simple). So the guard excludes start from the
    # visited-set: "target is not in path, UNLESS target is the start node
    # (the closing hop) AND depth >= 1 (can't close at depth 0)".
    query = f"""
    WITH RECURSIVE walk(start, start_id, node, node_id, depth, path) AS (
        SELECT v.name, v.id, v.name, v.id, 0, v.name FROM v_node v
      UNION ALL
        SELECT
            w.start,
            w.start_id,
            vb.name,
            e.b_id,
            w.depth + 1,
            w.path || '||' || vb.name
        FROM walk w
        JOIN e_dir e ON e.a_id = w.node_id{type_clause}
        JOIN v_node vb ON vb.id = e.b_id
        WHERE w.depth < ?
          -- Token-exact guard (same primitive as the undirected walks / F1).
          -- Allow the closing hop back to `start`; forbid revisiting any
          -- other vertex already on the path (keeps cycles simple).
          AND (
              e.b_id = w.start_id
              OR NOT array_contains(string_to_array(w.path, '||'), vb.name)
          )
          -- Once a cycle has closed (we are back at `start` at depth >= 1)
          -- stop extending: re-entering `start` and walking further would
          -- produce non-simple closed walks (vertices revisited). Each simple
          -- cycle is still enumerated once per starting vertex via its own
          -- seed row, so completeness is preserved.
          AND (w.depth = 0 OR w.node != w.start)
    )
    -- A closed cycle: current node is back at the start, at depth >= 2
    -- (depth 1 would be a self-loop, which CHECK (source != target) forbids).
    SELECT path FROM walk
    WHERE depth >= 2 AND node = start
    LIMIT ?
    """  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
    rows = con.execute(query, params + [int(max_hops), int(limit)]).fetchall()
    return [r[0].split("||") for r in rows]


# --------------------------------------------------------------------------- #
# Phase 2 wrappers — multi-hop / multi-label queries
# --------------------------------------------------------------------------- #
@_with_generation_cache
def peers(con: duckdb.DuckDBPyConnection, company: str,
          as_of: str | None = None) -> list[str]:
    """Companies that compete with the given company (symmetric).

    Returns a sorted list of competitor names. Empty if no competes_with
    edges are populated for this company. `as_of` filters temporally (NULL
    valid_from is treated as always-valid).
    """
    r = con.execute(
        """
        SELECT CASE WHEN v_a.name = ? THEN v_b.name ELSE v_a.name END AS peer
        FROM e_competes e
        JOIN v_node v_a ON v_a.id = e.a_name
        JOIN v_node v_b ON v_b.id = e.b_name
        WHERE (v_a.name = ? OR v_b.name = ?)
        """ + _as_of_predicate(as_of),  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [company, company, company],
    ).fetchall()
    return sorted({row[0] for row in r})


@_with_generation_cache
def jv_partners(con: duckdb.DuckDBPyConnection, company: str,
                as_of: str | None = None) -> list[tuple[str, str]]:
    """JV partners of the given company.

    Returns a sorted list of (partner_name, venture_name) tuples.
    `as_of` filters temporally (NULL valid_from is treated as always-valid).
    """
    r = con.execute(
        """
        SELECT CASE WHEN v_a.name = ? THEN v_b.name ELSE v_a.name END AS partner,
               COALESCE(json_extract_string(e.properties, 'venture'), '') AS venture
        FROM e_jv e
        JOIN v_node v_a ON v_a.id = e.a_name
        JOIN v_node v_b ON v_b.id = e.b_name
        WHERE (v_a.name = ? OR v_b.name = ?)
        """ + _as_of_predicate(as_of),  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [company, company, company],
    ).fetchall()
    # Bundle F4: json extraction pushed down to DuckDB (json_extract_string).
    # Missing key -> NULL -> '' via COALESCE. Malformed JSON now raises
    # (previously the bare `except Exception` silently masked it as ''). This
    # is the data-quality win: a bad row is surfaced instead of hidden.
    return sorted((partner, venture) for partner, venture in r)


def group_siblings(con: duckdb.DuckDBPyConnection, company: str,
                   as_of: str | None = None) -> list[str]:
    """Companies in the same promoter group as `company` (symmetric).

    `as_of` filters temporally (NULL valid_from is treated as always-valid).
    """
    r = con.execute(
        """
        SELECT CASE WHEN v_a.name = ? THEN v_b.name ELSE v_a.name END AS sibling
        FROM e_group e
        JOIN v_node v_a ON v_a.id = e.a_name
        JOIN v_node v_b ON v_b.id = e.b_name
        WHERE (v_a.name = ? OR v_b.name = ?)
        """ + _as_of_predicate(as_of),  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [company, company, company],
    ).fetchall()
    return sorted({row[0] for row in r})


def acquisitions(con: duckdb.DuckDBPyConnection, acquirer: str,
                 as_of: str | None = None) -> list[tuple[str, str]]:
    """Companies acquired by `acquirer`.

    Returns sorted list of (acquired_name, year_str_or_empty).
    `as_of` filters temporally (NULL valid_from is treated as always-valid).
    """
    r = con.execute(
        """
        SELECT v_b.name AS acquired, e.year AS year
        FROM e_acquired e
        JOIN v_node v_a ON v_a.id = e.acquirer_name
        JOIN v_node v_b ON v_b.id = e.target_name
        WHERE v_a.name = ?
        """ + _as_of_predicate(as_of),  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [acquirer],
    ).fetchall()
    # Bundle L2: `year` is a typed column on e_acquired (projected from
    # properties JSON once at materialise time), so this read is now a
    # direct column access — no per-row json_extract_string. Pre-L2 this
    # did COALESCE(json_extract_string(e.properties, 'year'), '') per row.
    return sorted((acquired, year) for acquired, year in r)


def subsidiary_of_company(con: duckdb.DuckDBPyConnection, company: str,
                          as_of: str | None = None) -> str | None:
    """Parent company of `company` via SubsidiaryOf, or None.

    Inverse-direction lookup: matches the edge where `company` is the
    `subsidiary_name` (source) and returns the `parent_name` (target).
    `as_of` filters temporally (NULL valid_from is treated as always-valid).
    """
    r = con.execute(
        """
        SELECT v_b.name AS parent
        FROM e_subsidiary e
        JOIN v_node v_a ON v_a.id = e.subsidiary_name
        JOIN v_node v_b ON v_b.id = e.parent_name
        WHERE v_a.name = ?
        """ + _as_of_predicate(as_of),  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [company],
    ).fetchall()
    return r[0][0] if r else None


def suppliers_and_customers(
    con: duckdb.DuckDBPyConnection, company: str,
    as_of: str | None = None,
) -> tuple[list[str], list[str]]:
    """(suppliers_of_company, customers_of_company).

    Supply-chain edges are stored under two inverse labels:
      - SuppliesTo:  supplier → customer
      - CustomerOf:  customer → supplier  (the same fact, opposite viewpoint)

    So a company's suppliers are:
      (a) sources of incoming `SuppliesTo` edges, UNION
      (b) destinations of outgoing `CustomerOf` edges.
    And its customers are:
      (a) destinations of outgoing `SuppliesTo` edges, UNION
      (b) sources of incoming `CustomerOf` edges.

    `as_of` filters temporally across all four queries (NULL valid_from is
    treated as always-valid).
    """
    pred = _as_of_predicate(as_of)
    # Bundle K3 (rewritten plain-SQL, Phase B of the duckpgq retirement):
    # the 4 arms as one UNION ALL of JOINs over e_supplier/e_customer with a
    # `role` discriminator. The four arms mirror the original logic:
    #   suppliers = incoming SuppliesTo ∪ outgoing CustomerOf
    #   customers = outgoing SuppliesTo ∪ incoming CustomerOf
    # Bucket into two sets in Python (same dedup the old code did).
    r = con.execute(
        """
        SELECT 'supplier' AS role, v_a.name AS x
        FROM e_supplier e
        JOIN v_node v_a ON v_a.id = e.supplier_name
        JOIN v_node v_b ON v_b.id = e.customer_name
        WHERE v_b.name = ?
        """ + pred + """
        UNION ALL
        SELECT 'supplier', v_b.name
        FROM e_customer e
        JOIN v_node v_a ON v_a.id = e.customer_name
        JOIN v_node v_b ON v_b.id = e.supplier_name
        WHERE v_a.name = ?
        """ + pred + """
        UNION ALL
        SELECT 'customer', v_b.name
        FROM e_supplier e
        JOIN v_node v_a ON v_a.id = e.supplier_name
        JOIN v_node v_b ON v_b.id = e.customer_name
        WHERE v_a.name = ?
        """ + pred + """
        UNION ALL
        SELECT 'customer', v_a.name
        FROM e_customer e
        JOIN v_node v_a ON v_a.id = e.customer_name
        JOIN v_node v_b ON v_b.id = e.supplier_name
        WHERE v_b.name = ?
        """ + pred,  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
        [company] * 4,
    ).fetchall()
    suppliers: set[str] = set()
    customers: set[str] = set()
    for role, name in r:
        (suppliers if role == "supplier" else customers).add(name)
    return sorted(suppliers), sorted(customers)


def company_neighbors_bundle(
    con: duckdb.DuckDBPyConnection, company: str,
    as_of: str | None = None,
) -> dict:
    """One-round-trip ego-network bundle for a company.

    Coalesces sector_of, peers, jv_partners, group_siblings, acquisitions,
    subsidiary_of_company, and suppliers_and_customers into a single UNION ALL
    of GRAPH_TABLE blocks (10 arms: the 7 wrappers' labels plus the inverse
    direction for SuppliesTo / CustomerOf that supplies the supplier/customer
    split). ~5x faster than the 7-serial-wrapper form (~9ms vs ~45ms measured
    on the live graph) — one trip through duckpgq's planner instead of seven.

    Returns a dict with the same keys and value shapes that
    ``_company_neighbors_bundle`` in app.py assembles today, so the JSON
    response is byte-identical:

      - ``sector``            : str | None   (first BelongsTo target)
      - ``peers``             : list[str]    (sorted, deduped)
      - ``jv_partners``       : list[{"partner", "venture"}]  (sorted)
      - ``group_siblings``    : list[str]    (sorted, deduped)
      - ``acquired``          : list[{"name", "year"}]        (sorted)
      - ``subsidiary_of``     : str | None   (first SubsidiaryOf target)
      - ``suppliers``         : list[str]    (sorted, deduped across both labels)
      - ``customers``         : list[str]    (sorted, deduped across both labels)

    `as_of` (ISO date / year / None) threads the same temporal filter into every
    arm; NULL valid_from is treated as always-valid, matching each wrapper.

    Direction handling preserves each wrapper's exact semantics:
      - BelongsTo    (c→s, company=c)              → sector
      - CompetesWith (symmetric)                    → peer
      - JvWith       (symmetric)                    → jv_partner  (+ props.venture)
      - SameGroup    (symmetric)                    → group_sibling
      - AcquiredBy   (a→b, company=a)               → acquired    (+ props.year)
      - SubsidiaryOf (a→b, company=a)               → parent
      - SuppliesTo   (company=b incoming → supplier; company=a outgoing → customer)
      - CustomerOf   (company=a → supplier; company=b incoming → customer)

    The individual wrappers (sector_of, peers, ...) remain the source of truth
    and stay exported for callers that need just one relation (notably the
    /api/graph/peers/<name> route and the CLI).
    """
    pred = _as_of_predicate(as_of)
    # Phase B of the duckpgq retirement: the 10 arms are plain-SQL JOINs over
    # the materialised e_* tables (same rows fin_graph was declared from).
    # Each arm projects a uniform (kind, other, props) shape so the UNION ALL
    # type-checks. NULL props for arms that don't need JSON. For JvWith /
    # AcquiredBy, Bundle K1 keeps the JSON extraction INSIDE DuckDB
    # (COALESCE(json_extract_string(...), '')); `year` is the typed column on
    # e_acquired (Bundle L2). Malformed JSON surfaces as a query error
    # instead of being masked as '' (F4 data-quality contract). The CAST on
    # the first arm pins the column type so NULLs in later arms don't widen
    # it ambiguously. Symmetric labels (CompetesWith/JvWith/SameGroup) match
    # both orientations via the CASE/WHERE-pair pattern; e_customer arms use
    # v_ct(customer)/v_sb(supplier) aliases.
    q = """
    WITH bag AS (
      SELECT 'sector' AS kind, v_s.name AS other,
             CAST(NULL AS VARCHAR) AS props
      FROM e_belongs e
      JOIN v_node v_c ON v_c.id = e.company_name
      JOIN v_node v_s ON v_s.id = e.sector_name
      WHERE v_c.name = ? AND v_c.kind = 'company' AND v_s.kind = 'sector'
      """ + pred + """
      UNION ALL
      SELECT 'peer', CASE WHEN v_a.name = ? THEN v_b.name ELSE v_a.name END,
             CAST(NULL AS VARCHAR)
      FROM e_competes e
      JOIN v_node v_a ON v_a.id = e.a_name
      JOIN v_node v_b ON v_b.id = e.b_name
      WHERE (v_a.name = ? OR v_b.name = ?)
      """ + pred + """
      UNION ALL
      SELECT 'jv_partner', CASE WHEN v_a.name = ? THEN v_b.name ELSE v_a.name END,
             COALESCE(json_extract_string(e.properties, 'venture'), '')
      FROM e_jv e
      JOIN v_node v_a ON v_a.id = e.a_name
      JOIN v_node v_b ON v_b.id = e.b_name
      WHERE (v_a.name = ? OR v_b.name = ?)
      """ + pred + """
      UNION ALL
      SELECT 'group_sibling', CASE WHEN v_a.name = ? THEN v_b.name ELSE v_a.name END,
             CAST(NULL AS VARCHAR)
      FROM e_group e
      JOIN v_node v_a ON v_a.id = e.a_name
      JOIN v_node v_b ON v_b.id = e.b_name
      WHERE (v_a.name = ? OR v_b.name = ?)
      """ + pred + """
      UNION ALL
      SELECT 'acquired', v_b.name, e.year
      FROM e_acquired e
      JOIN v_node v_a ON v_a.id = e.acquirer_name
      JOIN v_node v_b ON v_b.id = e.target_name
      WHERE v_a.name = ?
      """ + pred + """
      UNION ALL
      SELECT 'parent', v_b.name, CAST(NULL AS VARCHAR)
      FROM e_subsidiary e
      JOIN v_node v_a ON v_a.id = e.subsidiary_name
      JOIN v_node v_b ON v_b.id = e.parent_name
      WHERE v_a.name = ?
      """ + pred + """
      UNION ALL
      SELECT 'supplier', v_a.name, CAST(NULL AS VARCHAR)
      FROM e_supplier e
      JOIN v_node v_a ON v_a.id = e.supplier_name
      JOIN v_node v_b ON v_b.id = e.customer_name
      WHERE v_b.name = ?
      """ + pred + """
      UNION ALL
      SELECT 'supplier', v_sb.name, CAST(NULL AS VARCHAR)
      FROM e_customer e
      JOIN v_node v_ct ON v_ct.id = e.customer_name
      JOIN v_node v_sb ON v_sb.id = e.supplier_name
      WHERE v_ct.name = ?
      """ + pred + """
      UNION ALL
      SELECT 'customer', v_b.name, CAST(NULL AS VARCHAR)
      FROM e_supplier e
      JOIN v_node v_a ON v_a.id = e.supplier_name
      JOIN v_node v_b ON v_b.id = e.customer_name
      WHERE v_a.name = ?
      """ + pred + """
      UNION ALL
      SELECT 'customer', v_ct.name, CAST(NULL AS VARCHAR)
      FROM e_customer e
      JOIN v_node v_ct ON v_ct.id = e.customer_name
      JOIN v_node v_sb ON v_sb.id = e.supplier_name
      WHERE v_sb.name = ?
      """ + pred + """
    )
    SELECT kind, other, props FROM bag
    """  # noqa: S608  # parameterized; interpolated parts (`_as_of_predicate`/`where`/`pred`) emit ?-clauses & constants only
    # Parameter order mirrors the arms above: sector 1, peer 3, jv 3,
    # group 3, acquired 1, parent 1, supplier 2, customer 2.
    rows = con.execute(q, [company] * 16).fetchall()

    # Bucket rows by kind, then unpack each bucket into its wrapper's shape.
    # Bundle K1: for jv_partner rows, `props` is the pre-extracted venture
    # string (DuckDB did the json_extract in the GRAPH_TABLE COLUMNS clause).
    # Bundle L2: for acquired rows, `props` is the typed `year` column on
    # e_acquired (projected from properties JSON once at materialise time —
    # no per-read json_extract). Both: no Python-side _json.loads + bare
    # except; malformed JSON surfaces as a query error (F4 data-quality
    # contract).
    by_kind: dict[str, list[tuple[str, str | None]]] = {}
    for kind, other, props in rows:
        by_kind.setdefault(kind, []).append((other, props))

    def _jv_list() -> list[dict]:
        out: list[dict] = []
        for partner, venture in by_kind.get("jv_partner", []):
            out.append({"partner": partner, "venture": venture or ""})
        return sorted(out, key=lambda d: (d["partner"], d["venture"]))

    def _acquired_list() -> list[dict]:
        out: list[dict] = []
        for name, year in by_kind.get("acquired", []):
            out.append({"name": name, "year": year or ""})
        return sorted(out, key=lambda d: (d["name"], d["year"]))

    sector_rows = by_kind.get("sector", [])
    parent_rows = by_kind.get("parent", [])
    return {
        "sector": sector_rows[0][0] if sector_rows else None,
        "peers": sorted({o for o, _ in by_kind.get("peer", [])}),
        "jv_partners": _jv_list(),
        "group_siblings": sorted({o for o, _ in by_kind.get("group_sibling", [])}),
        "acquired": _acquired_list(),
        "subsidiary_of": parent_rows[0][0] if parent_rows else None,
        "suppliers": sorted({o for o, _ in by_kind.get("supplier", [])}),
        "customers": sorted({o for o, _ in by_kind.get("customer", [])}),
    }


# --------------------------------------------------------------------------- #
# C-series: read-only analytical queries (C1, C3, C4).                        #
# These aggregate over the raw SQLite graph_edges table — they do NOT need     #
# the DuckDB property graph or duckpgq planner. Each is also exposed as an     #
# /api/graph/* endpoint.                                                        #
# --------------------------------------------------------------------------- #

def co_mention_top(n: int = 20, conn=None) -> list[dict]:
    """C1: Top entities by co-mention frequency.

    ``co_mentioned_in`` is the richest unconsumed signal (1329 edges today).
    Each edge means the company appeared alongside others in the same
    newsletter edition. More co-mentions = more central to the newsletter
    narrative. Returns the top-N entities with their co-mention counts.

    If *conn* is given, uses it (caller owns the lifecycle); otherwise opens
    a fresh ``connect()`` and closes it. Pure SQLite — no DuckDB/duckpgq needed.
    """
    if conn is not None:
        rows = conn.execute(
            """
            SELECT source AS entity, COUNT(*) AS co_mentions
            FROM graph_edges
            WHERE edge_type = 'co_mentioned_in'
            GROUP BY source
            ORDER BY co_mentions DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        return [{"entity": r["entity"], "co_mentions": r["co_mentions"]}
                for r in rows]
    from helpers.core.db import connect
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT source AS entity, COUNT(*) AS co_mentions
            FROM graph_edges
            WHERE edge_type = 'co_mentioned_in'
            GROUP BY source
            ORDER BY co_mentions DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        return [{"entity": r["entity"], "co_mentions": r["co_mentions"]}
                for r in rows]
    finally:
        conn.close()


def cross_sector_bridges(conn=None) -> list[dict]:
    """C3: Cross-sector bridges — where capital flows between sectors.

    Counts ``acquired`` and ``jv_with`` edges where the two endpoints are in
    different sectors. Reveals which sector pairs have the most M&A / JV
    activity (e.g. FMCG <-> Consumer, Automotive <-> Technology).

    If *conn* is given, uses it (caller owns the lifecycle); otherwise opens
    a fresh ``connect()`` and closes it. Pure SQLite — no DuckDB/duckpgq needed.
    """
    SQL = """
        SELECT e.edge_type,
               c1.sector_classification AS sector_a,
               c2.sector_classification AS sector_b,
               COUNT(*) AS n
        FROM graph_edges e
        JOIN entities c1 ON c1.name = e.source
        JOIN entities c2 ON c2.name = e.target
        WHERE e.edge_type IN ('jv_with', 'acquired')
          AND c1.sector_classification IS NOT NULL
          AND c2.sector_classification IS NOT NULL
          AND c1.sector_classification <> c2.sector_classification
        GROUP BY e.edge_type, c1.sector_classification, c2.sector_classification
        ORDER BY n DESC, e.edge_type
    """
    def _build(rows):
        return [{"edge_type": r["edge_type"],
                 "sector_a": r["sector_a"],
                 "sector_b": r["sector_b"],
                 "count": r["n"]}
                for r in rows]
    if conn is not None:
        return _build(conn.execute(SQL).fetchall())
    from helpers.core.db import connect
    conn = connect()
    try:
        return _build(conn.execute(SQL).fetchall())
    finally:
        conn.close()


def edges_by_year(conn=None) -> list[dict]:
    """C4: Temporal edge formation — M&A and JV activity by year.

    Only ``acquired`` and ``jv_with`` edges carry ``valid_from`` dates.
    Returns one row per (year, edge_type) with the edge count, sorted
    chronologically. Pairs naturally with the ``as_of`` temporal views.

    If *conn* is given, uses it (caller owns the lifecycle); otherwise opens
    a fresh ``connect()`` and closes it. Pure SQLite — no DuckDB/duckpgq needed.
    """
    SQL = """
        SELECT substr(valid_from, 1, 4) AS year,
               edge_type,
               COUNT(*) AS n
        FROM graph_edges
        WHERE valid_from IS NOT NULL
        GROUP BY substr(valid_from, 1, 4), edge_type
        ORDER BY year, edge_type
    """
    def _build(rows):
        return [{"year": r["year"], "edge_type": r["edge_type"], "count": r["n"]}
                for r in rows]
    if conn is not None:
        return _build(conn.execute(SQL).fetchall())
    from helpers.core.db import connect
    conn = connect()
    try:
        return _build(conn.execute(SQL).fetchall())
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# P2.5 - Vector similarity search (DuckDB VSS scalar functions)               #
# --------------------------------------------------------------------------- #
@_with_generation_cache
def semantic_neighbors(
    con: duckdb.DuckDBPyConnection,
    company: str,
    k: int = 10,
    metric: str = "cosine",
    cross_sector: bool = False,
    as_of: str | None = None,
) -> list[tuple[str, str, float]]:
    """Find companies with embeddings most similar to a given company.

    Requires a populated v_embeddings table (see helpers/graph/embeddings.py
    for population). Uses DuckDB VSS scalar functions -- no HNSW index needed
    at current scale (~3ms for 1k companies).

    Args:
        con: DuckDB connection (from connect()).
        company: Reference company name to find similar companies to.
        k: Number of nearest neighbors to return (default 10).
        metric: Distance metric -- "cosine" (default) or "ip"
                (inner product, for L2-normalized vectors).
        cross_sector: If True, exclude companies in the same
                sector_classification as company (finds cross-sector
                analogues). Default False.
        as_of: Currently unused -- kept for API symmetry with other wrappers.
                Temporal filtering of embeddings is not supported (embeddings
                are recomputed, not versioned).

    Returns:
        Sorted list of (company_name, sector_classification, similarity_score).
        Higher score = more similar. Empty list if no embeddings are populated
        or the reference company has no embedding.
    """
    # Clamp k to [0, inf) — DuckDB rejects negative LIMIT/OFFSET with a
    # BinderException ("LIMIT/OFFSET cannot be negative"), and k=0 is a
    # valid "return nothing" request. Fuzz-tested invariant (2026-08-09).
    k = max(0, int(k))

    try:
        _row = con.execute("SELECT COUNT(*) FROM v_embeddings").fetchone()
        n = _row[0] if _row is not None else 0
    except Exception:
        return []
    if n == 0:
        return []

    if metric == "cosine":
        sim_expr = "array_cosine_similarity"
        direction = "DESC"
        filter_cond = "sim > 0"
    elif metric == "ip":
        sim_expr = "array_negative_inner_product"
        direction = "DESC"
        filter_cond = "sim < 0"
    else:
        raise ValueError("Unknown metric: " + repr(metric) + " (use cosine or ip)")

    # Determine the embedding dimension at runtime.  The vss extension on
    # duckdb 1.5.4 does not accept variable-length FLOAT[] arrays in its
    # scalar functions -- only fixed-length FLOAT[N] matches.  We read the
    # dimension from the table so the cast is always correct.
    dim_row = con.execute("SELECT len(embedding) FROM v_embeddings LIMIT 1").fetchone()
    dim = dim_row[0] if dim_row else 0
    if dim == 0:
        return []

    # Part C (sql_capability_unlocks): the company name travels as a bind
    # parameter everywhere it appears (reference vector, self-exclusion,
    # cross-sector subquery) — _lit() interpolation is gone from this path,
    # so the _CONTROL_RE NUL-crack class can't reach it. dim/k/metric are
    # internal ints and schema-constant identifiers, safe by construction.
    ref_vec = (
        "(SELECT embedding FROM v_embeddings WHERE company_name = ?)"
    )

    sector_filter = ""
    if cross_sector:
        sector_filter = (
            " AND v.sector_classification != "
            "(SELECT sector_classification FROM v_node "
            "WHERE name = ? AND kind = 'company')"
        )

    # Interpolated parts are metric-whitelist identifiers (sim_expr /
    # filter_cond / direction), int casts (dim, k), or fixed subqueries
    # with ? binds (ref_vec, sector_filter); the company name never
    # touches the SQL text.
    query = (
        "SELECT v.name, v.sector_classification, ce.sim "  # noqa: S608
        "FROM ( "
        "  SELECT id, "
        "         " + sim_expr + "(CAST(embedding AS FLOAT[" + str(dim) + "]), CAST(" + ref_vec + " AS FLOAT[" + str(dim) + "])) AS sim "
        "  FROM v_embeddings "
        "  WHERE company_name != ? "
        ") ce "
        "JOIN v_node v ON v.id = ce.id AND v.kind = 'company' "
        "WHERE ce.sim IS NOT NULL "
        "  AND " + filter_cond + " "
        "  " + sector_filter + " "
        "ORDER BY ce.sim " + direction + " "
        "LIMIT " + str(int(k))
    )

    # Bind order = ?-appearance order: ref_vec (inner CAST subquery),
    # self-exclusion, then the cross-sector subquery when enabled.
    params: list[str] = [company, company]
    if cross_sector:
        params.append(company)
    r = con.execute(query, params).fetchall()
    return [(row[0], row[1], row[2]) for row in r]


# --------------------------------------------------------------------------- #
# Note-embedding wrappers (sql_capability_unlocks A2 — v_note_embeddings)
#
# Whole-corpus KNN/join queries over the note_search embedding projection.
# All bind-parameterised; all degrade to None/[] when the table is empty
# (the _materialise_note_embeddings fallback) so unwired databases just
# return empty results. Query-prefix asymmetry note (proposal §3.2): these
# are doc-doc joins — prefix-free on both sides, correct by construction.
# Any FUTURE text-query wrapper over v_note_embeddings MUST go through
# rebuild_note_search.query_embedder() (BGE instruction prefix), never
# embed_document.
# --------------------------------------------------------------------------- #
_EDITION_DOC_TYPES = ("chatter", "points_and_figures", "plotlines")


def _note_emb_dims(con: duckdb.DuckDBPyConnection) -> int:
    """Dims of the stored note vectors; 0 when the table is empty."""
    try:
        row = con.execute("SELECT len(emb) FROM v_note_embeddings LIMIT 1").fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


@_with_generation_cache
def similar_notes(con: duckdb.DuckDBPyConnection, file_path: str, k: int = 10,
                  doc_type: str | None = None) -> list[tuple[str, str, float]] | None:
    """K nearest notes to a note, by cosine over ``v_note_embeddings``.

    Returns ``list[(file_path, title, sim)]`` sorted by descending
    similarity, EXCLUDING the query note itself (self-cosine is 1.0 by
    construction). ``None`` when the reference file_path has no embedded
    row (unknown note or unembedded doc); ``[]`` when it is the only note.
    ``doc_type`` optionally restricts candidates ('company', 'sector',
    'chatter', ...).
    """
    k = max(0, int(k))
    dim = _note_emb_dims(con)
    if dim == 0:
        return None
    ref = con.execute(
        "SELECT 1 FROM v_note_embeddings WHERE file_path = ? LIMIT 1",
        [file_path],
    ).fetchone()
    if ref is None:
        return None
    type_clause = ""
    params: list = [file_path, file_path]
    if doc_type is not None:
        type_clause = " AND doc_type = ?"
        params.append(doc_type)
    r = con.execute(
        f"""
        SELECT file_path, title, sim FROM (
          SELECT file_path, title,
                 array_cosine_similarity(
                     CAST(emb AS FLOAT[{dim}]),
                     CAST((SELECT emb FROM v_note_embeddings WHERE file_path = ?)
                          AS FLOAT[{dim}])) AS sim
          FROM v_note_embeddings
          WHERE file_path != ?{type_clause}
        )
        WHERE sim IS NOT NULL AND sim > 0
        ORDER BY sim DESC
        LIMIT {int(k)}
        """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        params,
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in r]


@_with_generation_cache
def notes_like_entity(
    con: duckdb.DuckDBPyConnection, entity: str, k: int = 10,
    doc_types: tuple[str, ...] = _EDITION_DOC_TYPES,
) -> list[tuple[str, str, float]] | None:
    """Newsletter notes semantically closest to an entity's note.

    The reverse of the ``cited_in`` edge, but needs no edge: KNN from the
    entity's embedded note over the newsletter doc types. ``entity`` is a
    company/sector normalized_name; its note row is resolved through
    ``fin.entities.file_path``. Returns ``list[(file_path, title, sim)]``
    or ``None`` when the entity is unknown / has no embedded note.
    """
    k = max(0, int(k))
    dim = _note_emb_dims(con)
    if dim == 0:
        return None
    ref = con.execute(
        "SELECT file_path FROM fin.entities WHERE name = ? "
        "AND file_path IS NOT NULL",
        [entity],
    ).fetchone()
    if ref is None:
        return None
    ref_path = ref[0]
    in_ph = ", ".join("?" for _ in doc_types)
    r = con.execute(
        f"""
        SELECT file_path, title, sim FROM (
          SELECT file_path, title,
                 array_cosine_similarity(
                     CAST(emb AS FLOAT[{dim}]),
                     CAST((SELECT emb FROM v_note_embeddings WHERE file_path = ?)
                          AS FLOAT[{dim}])) AS sim
          FROM v_note_embeddings
          WHERE file_path != ?
            AND doc_type IN ({in_ph})
        )
        WHERE sim IS NOT NULL AND sim > 0
        ORDER BY sim DESC
        LIMIT {int(k)}
        """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        [ref_path, ref_path, *doc_types],
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in r]


def notes_like_text(
    con: duckdb.DuckDBPyConnection, text: str, k: int = 5,
    doc_type: str = "company", min_sim: float = 0.0,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[tuple[str, str, float]] | None:
    """Embedded notes closest to arbitrary TEXT (not an existing note).

    The parse_newsletter --cross-check primitive: a NEW-flagged company
    has no note yet, so notes_like_entity cannot resolve it — embed the
    text itself (query prefix; the get_tickers vss_match pattern) and
    KNN over ``v_note_embeddings``. ``embed_fn`` overrides the embedder
    (tests inject fakes); the default is local_embedder.embed_query
    when the model is available, else ``None`` is returned so callers
    treat the check as unavailable. Returns ``list[(file_path, title,
    sim)]`` or ``None`` when the embedder / vector table is unusable.
    """
    dim = _note_emb_dims(con)
    if dim == 0:
        return None
    if embed_fn is None:
        from helpers.core import local_embedder
        if not local_embedder.available():
            return None
        embed_fn = local_embedder.embed_query
    vec = embed_fn(text)
    if not vec or len(vec) != dim:
        return None
    k = max(0, int(k))
    r = con.execute(
        f"""
        SELECT file_path, title, sim FROM (
          SELECT file_path, title,
                 array_cosine_similarity(
                     CAST(emb AS FLOAT[{dim}]),
                     CAST(? AS FLOAT[{dim}])) AS sim
          FROM v_note_embeddings
          WHERE doc_type = ?
        )
        WHERE sim IS NOT NULL AND sim > ?
        ORDER BY sim DESC
        LIMIT {int(k)}
        """,  # noqa: S608  # parameterized; interpolated parts are int casts / schema-constant identifiers
        [vec, doc_type, float(min_sim)],
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in r]


@_with_generation_cache
def edition_companies(con: duckdb.DuckDBPyConnection, edition: str,
                      k: int = 10) -> list[tuple[str, str, float]] | None:
    """Companies most similar to an edition (newsletter) note.

    ``edition`` is resolved against the newsletter doc types by exact
    title, full file_path, or filename stem (with or without .md) —
    deterministic first match by file_path order. Returns
    ``list[(file_path, title, sim)]`` over ``doc_type='company'``
    candidates, or ``None`` when the edition can't be resolved.
    """
    k = max(0, int(k))
    dim = _note_emb_dims(con)
    if dim == 0:
        return None
    stem = edition[:-3] if edition.endswith(".md") else edition
    in_ph = ", ".join("?" for _ in _EDITION_DOC_TYPES)
    ref = con.execute(
        f"""
        SELECT file_path FROM v_note_embeddings
        WHERE doc_type IN ({in_ph})
          AND (title IN (?, ?)
               OR file_path IN (?, ?, ?)
               OR split_part(file_path, '/', -1) IN (?, ?))
        ORDER BY file_path
        LIMIT 1
        """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        [*_EDITION_DOC_TYPES, edition, stem, edition, stem,
         f"findata/{edition}", stem, f"{stem}.md"],
    ).fetchone()
    if ref is None:
        return None
    ref_path = ref[0]
    r = con.execute(
        f"""
        SELECT file_path, title, sim FROM (
          SELECT file_path, title,
                 array_cosine_similarity(
                     CAST(emb AS FLOAT[{dim}]),
                     CAST((SELECT emb FROM v_note_embeddings WHERE file_path = ?)
                          AS FLOAT[{dim}])) AS sim
          FROM v_note_embeddings
          WHERE file_path != ? AND doc_type = 'company'
        )
        WHERE sim IS NOT NULL AND sim > 0
        ORDER BY sim DESC
        LIMIT {int(k)}
        """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        [ref_path, ref_path],
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in r]


def near_duplicate_notes(con: duckdb.DuckDBPyConnection, min_sim: float = 0.9,
                         doc_type: str = "company",
                         limit: int = 100) -> list[tuple[str, str, str, str, float]]:
    """Near-duplicate note pairs above a cosine threshold (QA tripwire).

    Pairwise self-join over ``v_note_embeddings`` restricted to one
    doc_type; ``a.file_path < b.file_path`` emits each unordered pair
    once. Top cosine pairs are exactly the rename-candidates / duplicate
    clusters the rename machinery cares about (measured 2026-08-21:
    Patanjali-Ruchi Soya rename, Ujjivan/Piramal/Muthoot pairs). ~1s at
    ~1k company docs — a maintenance command, deliberately NOT an API
    hot path and NOT generation-cached. Returns
    ``list[(path_a, path_b, title_a, title_b, sim)]`` sorted by
    descending similarity.
    """
    limit = max(0, int(limit))
    dim = _note_emb_dims(con)
    if dim == 0:
        return []
    r = con.execute(
        f"""
        SELECT file_path_a, file_path_b, title_a, title_b, sim FROM (
          SELECT a.file_path AS file_path_a, a.title AS title_a,
                 b.file_path AS file_path_b, b.title AS title_b,
                 array_cosine_similarity(
                     CAST(a.emb AS FLOAT[{dim}]),
                     CAST(b.emb AS FLOAT[{dim}])) AS sim
          FROM v_note_embeddings a
          JOIN v_note_embeddings b ON a.file_path < b.file_path
          WHERE a.doc_type = ? AND b.doc_type = ?
        )
        WHERE sim >= ?
        ORDER BY sim DESC
        LIMIT {limit}
        """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        [doc_type, doc_type, float(min_sim)],
    ).fetchall()
    return [(row[0], row[1], row[2], row[3], row[4]) for row in r]


# --------------------------------------------------------------------------- #
# Metric algorithm wrappers (Onager-backed; formerly duckpgq-native)
#
# Phase A of the duckpgq-retirement proposal
# (doc/improvements/archive/graph/duckpgq_retirement.txt): pagerank /
# weakly_connected_component / local_clustering_coefficient now run via the
# Onager extension over a plain (src, dst, weight) projection of the label's
# graph_edges rows — the same rows the e_* tables / fin_graph were built
# from, so the outputs are equivalent (wcc partition + clustering verified
# exact on the live graph; pagerank preserves ranking, scale differs by
# normalisation). The public contract is unchanged: companies-only rows,
# same sort orders, same signatures.
# --------------------------------------------------------------------------- #
def _label_to_edge_type(label: str) -> str:
    """Map an edge label (e.g. 'BelongsTo') to its graph_edges.edge_type.

    Covers the EDGE_REGISTRY labels plus the two out-of-registry tables
    (hierarchy / exposure) declared outside the EDGE_REGISTRY loop
    separately.
    """
    spec = EDGE_REGISTRY_BY_LABEL.get(label)
    if spec is not None:
        return spec["edge_type"]
    if label == "BelongsToHierarchy":
        return "belongs_to"
    if label == "ExposedTo":
        return "exposed_to"
    raise ValueError(f"unknown edge label: {label}")


def _company_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    """The set of company vertex names (replaces the ``JOIN v_company`` the
    duckpgq wrappers used to filter sector vertices out of the results)."""
    return {r[0] for r in con.execute("SELECT name FROM v_company").fetchall()}


def pagerank(con: duckdb.DuckDBPyConnection, edge_label: str = "BelongsTo",
             vertex_label: str = "Entity") -> list[tuple[str, float]]:
    """PageRank over the graph (Onager-backed).

    Returns a list of (entity_name, pagerank_score) sorted by score desc.
    Only company names are returned (sector vertices are filtered out,
    matching the previous duckpgq-native implementation).

    ``edge_label`` is a property-graph label (e.g. 'BelongsTo', 'CompetesWith',
    'BelongsToHierarchy', 'ExposedTo'); it is resolved to the underlying
    graph_edges.edge_type. ``vertex_label`` is accepted for signature
    compatibility and ignored (Onager has no property-graph vertex labels).
    """
    scores = onager_pagerank(con, edge_types=[_label_to_edge_type(edge_label)])
    companies = _company_names(con)
    rows = [(name, float(score)) for name, score in scores.items() if name in companies]
    rows.sort(key=lambda kv: kv[1], reverse=True)
    return rows


def weakly_connected_components(con: duckdb.DuckDBPyConnection,
                                edge_label: str = "BelongsTo",
                                vertex_label: str = "Entity") -> list[tuple[str, int]]:
    """Weakly-connected component labels (Onager-backed).

    Returns a list of (entity_name, component_id) sorted by (component_id,
    name). Only company names are returned (sector vertices are filtered
    out, matching the previous duckpgq-native implementation). Component
    ids are arbitrary labels — only the partition is meaningful.
    """
    comps = onager_components(con, edge_types=[_label_to_edge_type(edge_label)])
    companies = _company_names(con)
    rows = [(name, int(cid)) for name, cid in comps.items() if name in companies]
    rows.sort(key=lambda kv: (kv[1], kv[0]))
    return rows


def clustering_coefficient(con: duckdb.DuckDBPyConnection,
                           edge_label: str = "BelongsTo",
                           vertex_label: str = "Entity") -> list[tuple[str, float]]:
    """Local clustering coefficient per vertex (Onager-backed).

    Returns a list of (entity_name, coefficient) sorted by coefficient desc.
    Only company names are returned (sector vertices are filtered out,
    matching the previous duckpgq-native implementation).
    """
    ccs = onager_clustering(con, edge_types=[_label_to_edge_type(edge_label)])
    companies = _company_names(con)
    rows = [(name, float(cc)) for name, cc in ccs.items() if name in companies]
    rows.sort(key=lambda kv: kv[1], reverse=True)
    return rows


def _label_to_table(label: str) -> str | None:
    """Map an edge label (e.g. 'BelongsTo') to its table name (e_g: 'e_belongs')."""
    for spec in EDGE_REGISTRY.values():
        if spec["label"] == label:
            return spec["table"]
    return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(description="FinData graph query CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sector-of", help="Get sector for a company").add_argument("company")
    sp = sub.add_parser("sector-members", help="List companies in a sector")
    sp.add_argument("sector")
    sp.add_argument("--market-cap", default=None)
    sp.add_argument("--limit", type=int, default=50)

    sp = sub.add_parser("neighbors", help="1-hop neighbours of an entity")
    sp.add_argument("entity")

    sp = sub.add_parser("shortest", help="Shortest path src → dst")
    sp.add_argument("src")
    sp.add_argument("dst")
    sp.add_argument("--max-hops", type=int, default=5)

    sp = sub.add_parser("sql", help="Run arbitrary SQL over the attached databases (fin.*, e_*, v_node)")
    sp.add_argument("query")

    sp = sub.add_parser("peers", help="Competitors of a company (competes_with)")
    sp.add_argument("company")

    sp = sub.add_parser("jv-partners", help="JV partners of a company")
    sp.add_argument("company")

    sp = sub.add_parser("group-siblings", help="Promoter-group siblings of a company")
    sp.add_argument("company")

    sp = sub.add_parser("acquisitions", help="Companies acquired by the given acquirer")
    sp.add_argument("acquirer")

    sp = sub.add_parser("cycles", help="Find directed cycles (diagnostic — Bundle G3). "
                                       "Should be empty for same_group/co_mentioned_in (one row per pair) "
                                       "and acquired/subsidiary_of (strictly acyclic).")
    sp.add_argument("--max-hops", type=int, default=4, help="Max cycle length (2..6; default 4)")
    sp.add_argument("--edge-label", default=None, help="Restrict to one edge label (e.g. SubsidiaryOf)")
    sp.add_argument("--limit", type=int, default=100, help="Cap on cycles returned")

    sp = sub.add_parser("semantic-neighbors", help="Find companies with similar embeddings (VSS)")
    sp.add_argument("company")
    sp.add_argument("-k", "--k", type=int, default=10, help="Number of neighbors (default 10)")
    sp.add_argument("--metric", choices=["cosine", "ip"], default="cosine")
    sp.add_argument("--cross-sector", action="store_true", help="Exclude same-sector companies")

    sp = sub.add_parser("similar-notes", help="K nearest notes by embedding cosine (v_note_embeddings)")
    sp.add_argument("file_path", help="Reference note path (e.g. findata/Companies/Agriculture/Avanti_Feeds.md)")
    sp.add_argument("-k", "--k", type=int, default=10)
    sp.add_argument("--doc-type", default=None, help="Restrict candidates to one doc_type")

    sp = sub.add_parser("notes-like", help="Newsletters semantically closest to an entity's note")
    sp.add_argument("entity")
    sp.add_argument("-k", "--k", type=int, default=10)

    sp = sub.add_parser("edition-companies", help="Companies most similar to an edition note")
    sp.add_argument("edition", help="Edition title or file stem")
    sp.add_argument("-k", "--k", type=int, default=10)

    sp = sub.add_parser("near-duplicates", help="Near-duplicate note pairs above a cosine threshold (QA tripwire)")
    sp.add_argument("--min-sim", type=float, default=0.9)
    sp.add_argument("--doc-type", default="company")
    sp.add_argument("--limit", type=int, default=100)

    sub.add_parser("rebuild", help="Rebuild materialised tables in-place (run after parse_newsletter --apply / derive-relations)")
    sub.add_parser("fresh", help="Drop the .duckdb file and rebuild from scratch (use after version bumps or corruption)")
    sub.add_parser("update-extensions", help="Check installed DuckDB extensions for updates and install them")

    args = p.parse_args(argv)

    # rebuild / fresh / update-extensions don't need a query connection.
    if args.cmd == "rebuild":
        rebuild()
        print(f"✓ DuckDB graph rebuilt ({DUCKDB_PATH})", file=sys.stderr)
        return 0
    if args.cmd == "fresh":
        fresh_rebuild()
        print(f"✓ DuckDB graph rebuilt from scratch ({DUCKDB_PATH})", file=sys.stderr)
        return 0
    if args.cmd == "update-extensions":
        changed = update_extensions()
        if changed:
            for name, version in changed:
                print(f"  updated {name} → {version}")
        else:
            print("✓ all extensions up to date")
        return 0

    con = connect()

    if args.cmd == "sector-of":
        print(sector_of(con, args.company) or "<no sector>")
    elif args.cmd == "sector-members":
        members = sector_members(con, args.sector, market_cap=args.market_cap)
        for m in members[: args.limit]:
            print(m)
        print(f"({len(members)} total)", file=sys.stderr)
    elif args.cmd == "neighbors":
        for direction, other, label in neighbors(con, args.entity):
            print(f"{direction:3} --{label}--> {other}")
    elif args.cmd == "shortest":
        path = shortest_path(con, args.src, args.dst, max_hops=args.max_hops)
        if path is None:
            print(f"no path {args.src!r} → {args.dst!r} within {args.max_hops} hops")
            return 1
        for name, hop in path:
            print(f"  hop {hop}: {name}")
    elif args.cmd == "sql":
        for row in sql(con, args.query):
            print(row)
    elif args.cmd == "peers":
        peers_list = peers(con, args.company)
        if not peers_list:
            print(f"no competitors recorded for {args.company!r}")
        for p in peers_list:
            print(p)
    elif args.cmd == "jv-partners":
        partners = jv_partners(con, args.company)
        if not partners:
            print(f"no JVs recorded for {args.company!r}")
        for partner, venture in partners:
            print(f"{partner:30} {venture}")
    elif args.cmd == "group-siblings":
        sibs = group_siblings(con, args.company)
        if not sibs:
            print(f"no group siblings recorded for {args.company!r}")
        for s in sibs:
            print(s)
    elif args.cmd == "acquisitions":
        acqs = acquisitions(con, args.acquirer)
        if not acqs:
            print(f"no acquisitions recorded for {args.acquirer!r}")
        for acquired, year in acqs:
            print(f"{acquired:30} {year}")
    elif args.cmd == "cycles":
        # Bundle G3 diagnostic: directed cycles in the graph. Should be empty
        # for symmetric edge types (one directed row per pair) and strictly
        # acyclic types (acquired, subsidiary_of). Any cycle is a data bug.
        cycles = find_cycles(con, max_hops=args.max_hops,
                             edge_label=args.edge_label, limit=args.limit)
        if not cycles:
            label_note = f" for edge_label={args.edge_label!r}" if args.edge_label else ""
            print(f"no directed cycles found{label_note} (max_hops={args.max_hops})")
        else:
            for c in cycles:
                print("  " + " -> ".join(c))
            print(f"({len(cycles)} cycle(s))", file=sys.stderr)
    elif args.cmd == "semantic-neighbors":
        results = semantic_neighbors(
            con, args.company, k=args.k,
            metric=args.metric, cross_sector=args.cross_sector
        )
        if not results:
            print("no embeddings found for " + repr(args.company) + " (run helpers/graph/embeddings.py)")
            return 1
        for name, sector, sim in results:
            print("{:.4f}  {}  [{}]".format(sim, name, sector or "unknown"))
        print(f"({len(results)} results)", file=sys.stderr)
    elif args.cmd == "similar-notes":
        results = similar_notes(con, args.file_path, k=args.k, doc_type=args.doc_type)
        if results is None:
            print(f"no embedded note for {args.file_path!r}")
            return 1
        for path, title, sim in results:
            print(f"{sim:.4f}  {title}  ({path})")
        print(f"({len(results)} results)", file=sys.stderr)
    elif args.cmd == "notes-like":
        results = notes_like_entity(con, args.entity, k=args.k)
        if results is None:
            print(f"no embedded note for entity {args.entity!r}")
            return 1
        for path, title, sim in results:
            print(f"{sim:.4f}  {title}  ({path})")
        print(f"({len(results)} results)", file=sys.stderr)
    elif args.cmd == "edition-companies":
        results = edition_companies(con, args.edition, k=args.k)
        if results is None:
            print(f"no edition note matches {args.edition!r}")
            return 1
        for path, title, sim in results:
            print(f"{sim:.4f}  {title}  ({path})")
        print(f"({len(results)} results)", file=sys.stderr)
    elif args.cmd == "near-duplicates":
        pairs = near_duplicate_notes(con, min_sim=args.min_sim,
                                     doc_type=args.doc_type, limit=args.limit)
        if not pairs:
            print(f"no note pairs above {args.min_sim} (doc_type={args.doc_type!r})")
        for pa, pb, ta, tb, sim in pairs:
            print(f"{sim:.4f}  {ta or pa}  <->  {tb or pb}")
        print(f"({len(pairs)} pair(s))", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
