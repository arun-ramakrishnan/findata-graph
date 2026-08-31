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

  2. `PRAGMA journal_mode = WAL`
     Matches the production on-disk layout; preserves concurrent-reader
     semantics.

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

import sqlite3
from datetime import datetime, UTC
from pathlib import Path

# Lazy default: derive from this file's location so the helper works from
# anywhere (helpers/core/, app.py at repo root, tests/, etc.).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = _REPO_ROOT / "memory" / "research.db"

# P0: canonical schema version (mirrors helpers.graph.query._SCHEMA_VERSION).
# Used for PRAGMA user_version and db_meta.generation staleness. Keep in sync
# with helpers/graph/query.py::_SCHEMA_VERSION (string) — this is the int form.
EXPECTED_USER_VERSION = 7
EXPECTED_SCHEMA_VERSION = "7"


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
    # Bundle U1: make concurrent writers wait up to 5s for a lock instead of
    # failing immediately with SQLITE_BUSY. SQLite's default busy_timeout is
    # 0, so without this a second writer (e.g. sync_tags spawned by
    # parse_newsletter while the parent holds a write connection) gets
    # SQLITE_BUSY instantly. WAL lets readers proceed concurrently, but two
    # writers still serialize — this makes them queue rather than crash.
    conn.execute("PRAGMA busy_timeout = 5000")
    # P1.1: tuned pragmas for 30 MB DB on SSD (validated 2026-08-08):
    # - cache_size -20000 = 80 MB (vs 8 MB) — fits entire DB + indexes in memory
    # - synchronous NORMAL is safe in WAL (FULL is 2-3× slower, same durability in WAL)
    # - temp_store MEMORY avoids temp file I/O for sorts/joins
    # - journal_size_limit 64 MB caps WAL growth (was unbounded)
    # - mmap_size 256 MB enables memory-mapped I/O for reads
    # - wal_autocheckpoint 1000 (4 MB) keeps WAL bounded, same as default but explicit
    # Each is per-connection (or persistent) and cheap to re-apply; wrap individually
    # so :memory: test DBs that reject mmap don't fail the whole connect().
    for _pragma, _val in (
        ("cache_size", "-20000"),
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
    return int(row[0]) if row else None


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
    # seed schema_version mirror (advisory, not used for logic)
    if conn.execute("SELECT 1 FROM db_meta WHERE key='schema_version'").fetchone() is None:
        conn.execute(
            "INSERT INTO db_meta(key, value) VALUES ('schema_version', ?)",
            (EXPECTED_SCHEMA_VERSION,),
        )
    else:
        conn.execute(
            "UPDATE db_meta SET value=? WHERE key='schema_version'", (EXPECTED_SCHEMA_VERSION,)
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
