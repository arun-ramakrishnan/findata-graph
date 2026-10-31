#!/usr/bin/env python3
"""Shared SQLite connection helper.

Every DB-touching module in the project should go through `connect()` here
rather than calling `sqlite3.connect()` directly. This guarantees two things
that are easy to forget and silently break the schema:

  1. `PRAGMA foreign_keys = ON`
     SQLite defaults FK enforcement to OFF per-connection, which means the
     `ON DELETE CASCADE` / `ON UPDATE CASCADE` rules declared on `relations`
     and `entity_tags` silently never fire. Forgetting this is the reason
     deletes and renames leave orphaned rows. Centralising the pragma here
     makes that class of bug impossible.

  2. `PRAGMA journal_mode = WAL` + `PRAGMA journal_size_limit`
     Matches the production on-disk layout; preserves concurrent-reader
     semantics. The size limit (16 MiB) bounds the WAL's high-water mark:
     without it a write burst (maint-full's note_search DELETE+reinsert,
     derive passes) leaves a permanently large -wal file — observed at
     64 MiB fully-checkpointed-but-never-shrunk (2026-09-19). Checkpoints
     still run at the default 1000-page boundary; the limit only lets the
     file shrink back after one.

  3. `sqlite3.Row` row factory by default
     So callers can do `row["name"]` instead of `row[0]`. Tuples are still
     available by passing `row_factory=None`.

Usage:
    from helpers.core.db import connect, close_connection
    with connect() as conn:                 # auto-commit/rollback
        conn.execute("UPDATE entities ...")
    # or:
    conn = connect()
    try:
        ...
    finally:
        close_connection(conn)

`connect()` is idempotent with respect to the on-disk file: it does NOT
create tables or run migrations. For schema bootstrap see
`helpers/maintenance/db_maint.py` or `snapshot_db.py`.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, UTC
from pathlib import Path

from helpers.core.env import REPO_ROOT as _REPO_ROOT

# Lazy default: derived from the canonical REPO_ROOT so the helper works from
# anywhere (helpers/core/, app.py at repo root, tests/, etc.).
DEFAULT_DB_PATH = _REPO_ROOT / "memory" / "research.db"

# P0: SQLite research.db schema generation. EXPECTED_USER_VERSION is the int
# PRAGMA user_version form; EXPECTED_SCHEMA_VERSION mirrors it into
# db_meta.schema_version (informational at runtime — only
# database_integrity_check.py reads it, erroring on drift). These version the
# SQLite schema only: 7 was the knowledge-graph landing; 8 adds the
# provenance_agents registry + agent_id/source_tier columns
# (ontology_convention_stack S1); 9 adds concept_schemes/concepts/
# concept_mappings (S2 SKOS conventions); 10 adds entities.cin + five
# cin_* facets + entity_identifiers (S3 identifiers); 11 adds role/
# valid_from/valid_to on hyper_incidences (S4 n-ary event facets); 12
# adds the lifecycle status column on concepts/concept_mappings
# (ontology_governance S1); 13 adds the nic2008 vocabulary table
# (nic2008_seed_table S1, canonical-only like concepts — no DuckDB cache
# bump); 14 adds the graph_analytics_contract served-universe table
# (graph_analytics_contract S1, canonical-only — no DuckDB cache bump);
# 15 adds speaker_identity + v_quote_speaker — the quote-speaker resolution
# map (person_ontology_spine S2, canonical-only; the map resolves the
# existing corpus with zero per-quote writes); 16 adds event_participants —
# the n-ary event participant store (person_ontology_spine S4, fed by
# derive_event_participants; the event hyperedge family reads it);
# new edge types are data, not schema.
# helpers/graph/query.py::_SCHEMA_VERSION versions a DIFFERENT surface — the
# disposable DuckDB cache layout — and bumps on cache-only changes as well
# (note vectors, lane tables). A SQLite schema change implies a cache bump,
# not the reverse: do NOT keep the two in sync.
EXPECTED_USER_VERSION = 16
EXPECTED_SCHEMA_VERSION = "16"


def utc_now() -> str:
    """Current UTC timestamp as ``YYYY-MM-DD HH:MM:SS`` (the shape SQLite's
    ``CURRENT_TIMESTAMP`` produces).

    Use this for any DATETIME/``last_updated`` column that participates in a
    staleness comparison against a ``CURRENT_TIMESTAMP``-defaulted column
    (e.g. ``entities.last_updated`` vs ``graph_analytics.computed_at``).
    Writing ``date.today().isoformat()`` (local, date-only) into such a
    column produces a TEXT value that sorts inconsistently against the
    UTC-full-datetime default — making the staleness flag unreliable
    (Bundle T1).

    Returns the UTC time so the comparison is apples-to-apples regardless
    of the server's local timezone.
    """
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def utc_today_iso() -> str:
    """Current UTC date as ``YYYY-MM-DD`` (date-only label).

    Sibling of :func:`utc_now` for path-identity / date-label contexts
    (report ``source_ref`` stamps, ``coinfer/`` edition dirs) that want the
    shared shape, not a full datetime. Zero-output-change swap for the
    ``datetime.now(UTC).date().isoformat()`` copies.
    """
    return datetime.now(UTC).date().isoformat()


def market_cap_sql(column_alias: str = "market_cap") -> str:
    """SQL fragment that derives a company's market_cap from ``entity_tags``.

    The ``entities.market_cap`` column was dropped (Bundle C2, 2026-07-28)
    because it disagreed with the ``market_cap/*`` tag for 126 companies —
    the tag (synced from note YAML via sync_tags.py) is the source of truth.
    This fragment lets SQLite-side SELECTs project market_cap without the
    column, by parsing the ``market_cap/<value>`` tag suffix.

    Returns a correlated subselect against the aliased ``entities`` row, so
    it can be dropped into any SELECT that has ``entities`` in its FROM
    clause (the alias must be the literal ``entities``). The returned value
    matches the old column's shape (``large_cap``/``mid_cap``/``small_cap``/
    ``micro_cap``, or NULL if no tag).

    Example:
        SELECT name, {market_cap_sql()} FROM entities WHERE entity_type='company'
    """
    return (
        f"(SELECT substr(MIN(t.tag), length('market_cap/')+1) "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        f"FROM entity_tags t WHERE t.entity_name = entities.name "
        f"AND t.tag LIKE 'market_cap/%') AS {column_alias}"
    )


def connect(
    db_path: Path | str | None = None,
    *,
    row_factory=sqlite3.Row,
    enable_fk: bool = True,
    wal: bool = True,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with project-standard pragmas applied.

    Args:
        db_path: Path to the .db file. Defaults to `memory/research.db` under
            the repo root.
        row_factory: `sqlite3.Row` by default for dict-style access. Pass
            `None` to get raw tuples, or any callable conforming to the
            `sqlite3.Connection.row_factory` signature.
        enable_fk: Set `PRAGMA foreign_keys = ON` so ON DELETE/UPDATE CASCADE
            rules in the schema actually fire. Keep this ON unless you are
            intentionally constructing inconsistent state (e.g. test fixtures).
        wal: Ensure `journal_mode = WAL` for concurrent-reader semantics.
        read_only: Open via SQLite URI ``mode=ro`` — the file is neither
            created nor mutated and writes raise. For reading sidecars/
            snapshots that must stay byte-frozen (e.g. legacy cache files
            about to be renamed away by a migration). ``wal`` is ignored
            (an RO connection cannot switch journal modes); the other
            pragmas still apply.

    Returns:
        A `sqlite3.Connection` with the requested settings. Caller is
        responsible for closing (use as a context manager for auto-commit).
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if read_only:
        # as_uri() percent-encodes ?/# so unusual filenames stay one arg;
        # resolve() because the URI form demands an absolute path.
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(path))
    if row_factory is not None:
        conn.row_factory = row_factory
    if enable_fk:
        # Must be set outside a transaction. sqlite3.connect() opens in
        # autocommit-off mode but the pragma still takes effect.
        conn.execute("PRAGMA foreign_keys = ON")
    if wal and not read_only:
        # WAL is persistent on the DB file once set, but re-setting is cheap
        # and guards against a snapshot/restore losing the mode.
        conn.execute("PRAGMA journal_mode = WAL")
        # Bound the WAL high-water mark (per-connection pragma, NOT
        # persistent): after a checkpoint the file shrinks back to the
        # limit instead of parking at the burst maximum forever.
        conn.execute("PRAGMA journal_size_limit = 16777216")  # 16 MiB
    # Bundle U1: make concurrent writers wait up to 5s for a lock instead of
    # failing immediately with SQLITE_BUSY. SQLite's default busy_timeout is
    # 0, so without this a second writer (e.g. sync_tags spawned by
    # parse_newsletter while the parent holds a write connection) gets
    # SQLITE_BUSY instantly. WAL lets readers proceed concurrently, but two
    # writers still serialize — this makes them queue rather than crash.
    conn.execute("PRAGMA busy_timeout = 5000")
    # P1.1: tuned pragmas for 30 MB DB on SSD (validated 2026-08-08):
    # - cache_size: 80 MB (was 8 MB) — fits entire DB + indexes in memory.
    #   Now env-overridable (FINDATA_SQLITE_CACHE_MB) for 2x scale: default 256 MB.
    # - synchronous NORMAL is safe in WAL (FULL is 2-3× slower, same durability in WAL)
    # - temp_store MEMORY avoids temp file I/O for sorts/joins
    # - journal_size_limit 64 MB caps WAL growth (was unbounded)
    # - mmap_size 256 MB enables memory-mapped I/O for reads
    # - wal_autocheckpoint 1000 (4 MB) keeps WAL bounded, same as default but explicit
    # Each is per-connection (or persistent) and cheap to re-apply; wrap individually
    # so :memory: test DBs that reject mmap don't fail the whole connect().
    cache_mb = int(os.environ.get("FINDATA_SQLITE_CACHE_MB", "256"))
    for _pragma, _val in (
        ("cache_size", f"{-cache_mb * 256}"),
        ("synchronous", "NORMAL"),
        ("temp_store", "MEMORY"),
        ("journal_size_limit", "67108864"),
        ("mmap_size", "268435456"),
        ("wal_autocheckpoint", "1000"),
    ):
        try:
            conn.execute(f"PRAGMA {_pragma} = {_val}")
        except sqlite3.Error:
            pass
    return conn


def close_connection(conn: sqlite3.Connection) -> None:
    """Close a SQLite connection after running ``PRAGMA optimize``.

    SQLite recommends running ``PRAGMA optimize`` once at the end of each
    application session (or on a representative connection close) so the
    query planner can update its internal statistics.  This is a no-op if
    the connection is already closed.

    Prefer this over bare ``conn.close()`` in new code.
    """
    try:
        conn.execute("PRAGMA optimize")
    except sqlite3.Error:
        pass
    conn.close()


# --------------------------------------------------------------------------- #
# P0: generation counter + user_version helpers                               #
# --------------------------------------------------------------------------- #
def get_generation(conn: sqlite3.Connection) -> int | None:
    """Return current generation from db_meta, or None if table absent."""
    try:
        row = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        return None


def get_user_version(conn: sqlite3.Connection) -> int | None:
    """Return PRAGMA user_version."""
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None


# S3 (worktree_fresh_trample_guard): permanent, trimmed bump log.
# A generation bump is the writer-side staleness signal every warm DuckDB
# consumer and the snapshot pair key off, but until now nothing recorded WHO
# bumped: the 2026-10-12 "ghost generation" chase (four gate failures before
# noon) needed temporary audit triggers and a caller-tagging wrapper around
# this function to find the writer. The ring keeps the last BUMP_LOG_RING
# manual bumps — ts, caller file:line, worktree, post-bump generation — and
# snapshot_db --check cites the newest entry next to a MISMATCH, so a stale
# snapshot reads as a named event instead of a mystery.
#
# Scope note: the entities/graph_edges triggers bump generation inside SQL
# (ensure_db_meta) and cannot append here — one bulk ingest would flush the
# whole ring. A cited entry whose generation trails the live one is exactly
# that signal (see snapshot_db._bump_log_citation).
DB_BUMP_LOG_DDL = (
    "CREATE TABLE IF NOT EXISTS db_bump_log ("
    " ts TEXT NOT NULL,"
    " caller TEXT NOT NULL,"
    " worktree TEXT NOT NULL,"
    " generation INTEGER NOT NULL"
    ")"
)

BUMP_LOG_RING = 100

# S1 (worktree_fresh_trample_guard): key prefix of the per-index apply stamp.
# The stamp lives in each index's key/value meta table (db_meta for
# note_search), so its three rows appear as EXTRA ROWS there. That is
# contract-legal drift for snapshot_db's row-count verify — the DuckDB side
# already whitelists its own stamp-owned keys (STAMP_OWNED_META_KEYS) and
# fp:* fingerprints the same way — so the prefix is named here once and read
# by both the writer (rebuild_common) and the verifier (snapshot_db).
APPLY_STAMP_PREFIX = "apply_"


def _bump_caller() -> str:
    """`<repo-relative file>:<line>` of the first frame outside this module.

    The bump always originates in a writer module (rebuild_note_search /
    graph.embeddings); db.py frames are the mechanism, not the cause, so they
    are skipped. A path outside the repo (a test fixture, an installed copy)
    is kept absolute rather than faked relative.
    """
    import traceback

    here = Path(__file__).resolve()
    for frame in reversed(traceback.extract_stack()):
        try:
            path = Path(frame.filename).resolve()
        except OSError:
            path = Path(frame.filename)
        if path == here:
            continue
        try:
            return f"{path.relative_to(_REPO_ROOT)}:{frame.lineno}"
        except ValueError:
            return f"{path}:{frame.lineno}"
    return "unknown"


def _log_bump(conn: sqlite3.Connection, generation: int) -> None:
    """Append one ring entry and trim. Best-effort, never raises.

    The generation is load-bearing; this log is diagnostic, so a failure
    (locked db, absent worktree facts, read-only fixture) must not turn a
    successful bump into an exception.
    """
    try:
        from helpers.core.git_state import worktree_name

        conn.execute(DB_BUMP_LOG_DDL)
        conn.execute(
            "INSERT INTO db_bump_log (ts, caller, worktree, generation) VALUES (?, ?, ?, ?)",
            (
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                _bump_caller(),
                worktree_name(),
                int(generation),
            ),
        )
        conn.execute(
            "DELETE FROM db_bump_log WHERE rowid NOT IN "
            "(SELECT rowid FROM db_bump_log ORDER BY rowid DESC LIMIT ?)",
            (BUMP_LOG_RING,),
        )
        conn.commit()
    except Exception:  # noqa: BLE001, S110  # diagnostic ring; the bump already committed
        pass


def bump_generation(conn: sqlite3.Connection, by: int = 1) -> int | None:
    """Manually bump db_meta.generation (writer-side staleness signal).

    The entities/graph_edges triggers can't see derived-index writes:
    note_search (FTS5) and company_embeddings are invisible to the trigger
    set (sql_capability_unlocks B4), so a warm DuckDB cache whose
    v_note_embeddings/v_embeddings projections depend on them would keep
    serving stale vectors. Writers that change those tables call this
    AFTER their commit — the generation mismatch flips _is_warm and the
    next connect() rebuilds (~2s, correctness over warm-up). --check /
    dry-run / sidecar-only paths must NEVER call it.

    No-op (returns None) when db_meta doesn't exist — bare test fixtures
    bypass ensure_db_meta, and a DB without db_meta has no
    generation-keyed consumer to invalidate.

    Returns the new generation value.
    """
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='db_meta'"
    ).fetchone()
    if not has_meta:
        return None
    conn.execute(
        "UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+? AS TEXT) WHERE key='generation'",
        (int(by),),
    )
    conn.commit()
    row = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
    new_gen = int(row[0]) if row else None
    if new_gen is not None:
        _log_bump(conn, new_gen)
    return new_gen


def ensure_db_meta(conn: sqlite3.Connection) -> int:
    """Idempotently create db_meta + generation triggers + user_version.

    Creates:
      - table db_meta(key TEXT PK, value TEXT)
      - seed row generation=1 if absent
      - triggers on entities/graph_edges AFTER INSERT/DELETE/UPDATE that
        bump generation (per-row; monotonic, see doc)
      - PRAGMA user_version = EXPECTED_USER_VERSION if mismatched

    Returns the current generation after ensure. Safe to call on every
    connect() path or as a one-shot migration.
    """
    # table
    conn.execute("CREATE TABLE IF NOT EXISTS db_meta ( key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    # S3: the bump log is created here (not only on the first manual bump).
    # Restore carries the EMPTY table (snapshots/parquet/_schema.sqlite.sql
    # includes its DDL — regenerated 2026-10-12, F9), so a restored index
    # has the ring before any writer fires. note_search_meta is the
    # contrast case: the snapshot schema deliberately excludes note_search%
    # tables, so that one stays writer-created (IF NOT EXISTS in
    # rebuild_note_search) — pre-existing gap, out of scope here.
    conn.execute(DB_BUMP_LOG_DDL)
    # seed generation if missing
    row = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
    if row is None:
        conn.execute("INSERT INTO db_meta(key, value) VALUES ('generation','1')")
        cur_gen = 1
    else:
        try:
            cur_gen = int(row[0])
        except ValueError, TypeError:
            conn.execute("UPDATE db_meta SET value='1' WHERE key='generation'")
            cur_gen = 1
    # seed schema_version mirror (informational at runtime; integrity check
    # errors on drift)
    if conn.execute("SELECT 1 FROM db_meta WHERE key='schema_version'").fetchone() is None:
        conn.execute(
            "INSERT INTO db_meta(key, value) VALUES ('schema_version', ?)",
            (EXPECTED_SCHEMA_VERSION,),
        )
    else:
        conn.execute(
            "UPDATE db_meta SET value=? WHERE key='schema_version'",
            (EXPECTED_SCHEMA_VERSION,),
        )

    # triggers — idempotent: drop then create
    # Bump generation by 1 per row-change (INSERT/DELETE/UPDATE on the two
    # graph-relevant tables). entity_tags/quotes/etc. are derived and don't
    # affect the DuckDB graph, so they don't bump.
    for tbl in ("entities", "graph_edges"):
        for op in ("insert", "delete", "update"):
            tname = f"trg_{tbl}_{op}_gen"
            conn.execute(f"DROP TRIGGER IF EXISTS {tname}")
            conn.execute(
                f"CREATE TRIGGER {tname} AFTER {op.upper()} ON {tbl} "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                f"BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) "
                f"WHERE key='generation'; END"
            )
    # user_version
    try:
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        if int(uv) != EXPECTED_USER_VERSION:
            conn.execute(f"PRAGMA user_version = {EXPECTED_USER_VERSION}")
    except sqlite3.Error:
        pass
    conn.commit()
    # re-read generation after any migration that bumped it
    row2 = conn.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
    try:
        return int(row2[0]) if row2 else cur_gen
    except ValueError, TypeError:
        return cur_gen
