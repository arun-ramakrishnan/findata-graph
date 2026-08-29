#!/usr/bin/env python3
"""One-shot schema rebuild: bring the live SQLite DB into conformance with
the canonical DDL constants (Bundle P, 2026-07-27).

WHY THIS EXISTS
---------------
``migrate()`` (migrate_to_graph_edges.py) uses ``CREATE TABLE IF NOT EXISTS``,
which is a **no-op on pre-existing tables**. The live ``memory/research.db``
was built out-of-band and mutated with ``ALTER TABLE ADD COLUMN``, so three
divergences have accumulated between the canonical DDL constants and the
physical live schema:

  - **P1**: live ``graph_edges`` is missing ``CHECK (json_valid(properties))``
    (verified: malformed-JSON inserts succeed). Silent corruption risk.
  - **P2**: live ``entities`` shows the ``ALTER TABLE ADD COLUMN`` signature
    (trailing ``, sector_classification, ticker)``) and a CHECK that accepts
    ``Foo Ltd`` (no dot), which canonical rejects.
  - **P3**: live ``graph_analytics`` PK is ``(entity_name, metric)`` but every
    hot query filters on ``metric`` first — a full SCAN. Reversing to
    ``(metric, entity_name)`` turns it into a prefix SEARCH + free ORDER BY.

This script rebuilds all three tables from the canonical DDL constants in a
single transaction, preserving all data. It is the ONLY way to land CHECK
constraints and PK-order changes on a table that ``CREATE TABLE IF NOT
EXISTS`` cannot touch.

SAFETY
------
- Runs entirely inside one ``BEGIN``/``COMMIT``; any failure rolls back.
- Pre-flight count check aborts if any row is lost during the copy.
- Idempotent: re-running on an already-conformant DB is a no-op (same
  counts, no error).
- Does NOT run VACUUM (can't run inside a transaction); run ``make maint``
  afterward if compaction is desired.

USAGE
-----
    python3 helpers/maintenance/rebuild_schema.py           # rebuild live DB
    python3 helpers/maintenance/rebuild_schema.py --dry-run  # report only, no writes
    python3 helpers/maintenance/rebuild_schema.py --db PATH  # rebuild a copy

Post-rebuild: run ``make snapshot`` to refresh the committed artifacts, and
``make graph-rebuild`` (or the script's built-in call) to refresh the DuckDB
cache (its _SCHEMA_VERSION bumps to "3" so warm files auto-rebuild).

See doc/improvements/sqlite_improvs.txt Bundle P for the full finding + rationale.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect, ensure_db_meta  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import (  # noqa: E402
    DB_PATH,
    ENTITIES_DDL,
    ENTITIES_INDEXES,
    ENTITIES_NAME_NOCASE_INDEX,
    GRAPH_ANALYTICS_DDL,
    GRAPH_EDGES_DDL,
    GRAPH_EDGES_INDEXES,
    RELATIONS_VIEW_DDL,
)


def _ddl_for_new_table(ddl: str, original_name: str, new_name: str) -> str:
    """Transform a ``CREATE TABLE IF NOT EXISTS <original> (...)`` constant
    into ``CREATE TABLE <new> (...)`` (stripping IF NOT EXISTS, swapping the
    name). The canonical constants all follow this exact shape."""
    # Strip "IF NOT EXISTS" so CREATE fails loudly if _new already exists
    # (a leftover from a prior failed run — the DROP at the end of
    # _rebuild_table cleans it up, but we want the CREATE to be unconditional).
    stripped = re.sub(r"\bIF NOT EXISTS\s+", "", ddl, count=1)
    # Replace only the first occurrence of the table name (the one in
    # CREATE TABLE), not any column or constraint that happens to share it.
    return stripped.replace(original_name, new_name, 1)


def _rebuild_table(
    conn: sqlite3.Connection,
    name: str,
    canonical_ddl: str,
    columns: list[str],
) -> int:
    """Rebuild ``name`` from ``canonical_ddl`` preserving all data.

    Uses the SQLite-safe CREATE-INSERT-DROP-RENAME pattern:
      1. CREATE TABLE <name>_new <canonical body>
      2. INSERT INTO <name>_new (cols) SELECT cols FROM <name>
      3. Assert row count preserved (abort if any row lost)
      4. DROP TABLE <name>          (invalidates dependents temporarily)
      5. ALTER TABLE <name>_new RENAME TO <name>

    The whole sequence runs inside the caller's transaction; a failure at
    any step triggers ROLLBACK, leaving the original table untouched.

    Returns the row count copied (== pre-rebuild count).
    """
    new_name = f"{name}_new"
    # Clean up any leftover _new from a prior failed run (idempotency).
    conn.execute(f"DROP TABLE IF EXISTS {new_name}")

    before = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers

    conn.execute(_ddl_for_new_table(canonical_ddl, name, new_name))
    col_list = ", ".join(columns)
    conn.execute(
        f"INSERT INTO {new_name} ({col_list}) SELECT {col_list} FROM {name}"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
    )

    after = conn.execute(f"SELECT COUNT(*) FROM {new_name}").fetchone()[0]  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
    if after != before:
        # Abort before the destructive DROP — ROLLBACK restores everything.
        raise RuntimeError(
            f"row-count mismatch rebuilding {name}: {before} -> {after} "
            f"(aborted before DROP; transaction will roll back)"
        )

    conn.execute(f"DROP TABLE {name}")
    conn.execute(f"ALTER TABLE {new_name} RENAME TO {name}")
    return after


# Column lists (must match the canonical DDL exactly, in order).
# Bundle C2/L2 (2026-07-28): market_cap + index_membership dropped.
_ENTITIES_COLS = [
    "name", "entity_type", "created_at",
    "file_path", "last_updated", "normalized_name", "sector_classification",
    "ticker",
]
_GRAPH_EDGES_COLS = [
    "id", "source", "target", "edge_type", "weight", "properties",
    "valid_from", "valid_to", "source_ref", "symmetric", "created_at",
]
_GRAPH_ANALYTICS_COLS = [
    "entity_name", "metric", "value", "computed_at",
]


def rebuild(db_path: Path | str = DB_PATH, *, dry_run: bool = False) -> dict:
    """Rebuild entities, graph_edges, graph_analytics from canonical DDL.

    Returns a stats dict with before/after row counts and the list of
    indexes recreated. When ``dry_run`` is True, reports what would happen
    without writing (opens read-only after the initial count).
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = connect(db_path)
    stats: dict = {"db": str(db_path), "dry_run": dry_run}
    try:
        # Pre-flight: capture counts + verify 0 FK violations (the rebuild
        # re-validates every FK via INSERT-SELECT; a violation would surface
        # here as a clearer error than mid-rebuild).
        pre_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        pre_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        pre_analytics = conn.execute("SELECT COUNT(*) FROM graph_analytics").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise RuntimeError(
                f"FK violations pre-rebuild ({len(fk_violations)} rows) — "
                f"fix before rebuilding: {fk_violations[:3]}"
            )
        stats.update(pre_entities=pre_entities, pre_edges=pre_edges,
                     pre_analytics=pre_analytics)

        if dry_run:
            stats["action"] = "dry-run, no writes"
            return stats

        # Disable FK enforcement for the rebuild. Must be done OUTSIDE the
        # transaction (SQLite silently ignores PRAGMA foreign_keys inside a
        # transaction). Rationale: the rebuild drops + recreates entities
        # (the FK target). With FK ON, dropping entities would fire
        # ON DELETE CASCADE on graph_edges and graph_analytics, destroying
        # the data we're about to copy. The pre-flight foreign_key_check
        # above already confirmed 0 violations, so the data is
        # referentially clean; disabling FKs here only suppresses the
        # cascade, not a real integrity check. We re-run foreign_key_check
        # after the copy to verify the rebuilt tables are still consistent
        # (the INSERT-SELECT preserves all FK values verbatim).
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")

        # 1. entities (FK target — rebuild first; FK OFF prevents cascade).
        n_ent = _rebuild_table(conn, "entities", ENTITIES_DDL, _ENTITIES_COLS)
        for idx in ENTITIES_INDEXES:
            conn.execute(idx)
        conn.execute(ENTITIES_NAME_NOCASE_INDEX)

        # 2. Drop the relations VIEW before rebuilding graph_edges. SQLite
        #    validates view definitions during ALTER TABLE RENAME, so the
        #    view's "FROM graph_edges" reference would break the rename in
        #    _rebuild_table. Drop first, recreate after the rebuild completes.
        conn.execute("DROP VIEW IF EXISTS relations")

        # 3. graph_edges (FK → entities; rebuild re-validates every edge).
        n_edge = _rebuild_table(conn, "graph_edges", GRAPH_EDGES_DDL, _GRAPH_EDGES_COLS)
        for idx in GRAPH_EDGES_INDEXES:
            conn.execute(idx)

        # 4. relations VIEW (references graph_edges; recreate from canonical
        #    constant now that graph_edges is back under its original name).
        conn.execute(RELATIONS_VIEW_DDL)

        # 5. graph_analytics (FK → entities; P3 PK reversal lands here).
        n_an = _rebuild_table(conn, "graph_analytics", GRAPH_ANALYTICS_DDL, _GRAPH_ANALYTICS_COLS)

        # Post-copy integrity check: foreign_key_check reports violations
        # regardless of the enforcement flag. If the copy preserved all FK
        # values (it must, since it's a verbatim column copy), this returns
        # empty. Any violation means data loss — abort before COMMIT.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"FK violations post-rebuild ({len(violations)} rows) — "
                f"data copy lost referential integrity: {violations[:3]}"
            )

        conn.commit()
        # Re-enable FK enforcement AFTER the transaction closes (PRAGMA
        # foreign_keys is a no-op inside a transaction). The next connect()
        # sets it ON regardless; this just restores this connection's state.
        conn.execute("PRAGMA foreign_keys = ON")

        # The DROP TABLE inside _rebuild_table destroyed the per-row
        # generation triggers on entities/graph_edges (SQLite drops a
        # table's triggers with it). Without them, every later write stops
        # bumping db_meta.generation and _is_warm goes blind — the DuckDB
        # cache would serve stale graph data with the snapshot gen check
        # none the wiser. ensure_db_meta is idempotent: re-creates the six
        # trg_*_gen triggers, preserves the generation value, re-stamps
        # schema_version (graph_analytics carries no triggers).
        ensure_db_meta(conn)
        stats["generation_triggers"] = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE 'trg_%_gen'"
        ).fetchone()[0]

        stats.update(
            post_entities=n_ent, post_edges=n_edge, post_analytics=n_an,
            relations_view_rows=conn.execute(
                "SELECT COUNT(*) FROM relations"
            ).fetchone()[0],
            indexes_recreated=len(ENTITIES_INDEXES) + 1 + len(GRAPH_EDGES_INDEXES),
            json_valid_check=(
                "json_valid(properties)"
                in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name='graph_edges'"
                ).fetchone()[0]
            ),
        )
    except Exception:
        # Only ROLLBACK if a transaction is actually open. The pre-flight checks
        # (FK violations, dry-run) raise BEFORE the `BEGIN` at line 193, so an
        # unconditional ROLLBACK here would crash with "cannot rollback - no
        # transaction is active" and MASK the real error (e.g. "FK violations
        # pre-rebuild"). in_transaction reflects whether BEGIN has run.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return stats


def _refresh_duckdb_cache() -> None:
    """Force-rebuild the DuckDB cache so it picks up the new SQLite schema.

    The _SCHEMA_VERSION bump (3) means the next connect() rebuilds anyway,
    but calling fresh_rebuild() here avoids the first-request latency and
    guarantees the cache is consistent immediately after the rebuild."""
    try:
        from helpers.graph.query import fresh_rebuild
        fresh_rebuild()
        print("DuckDB cache rebuilt (schema v3).")
    except Exception as e:
        # Non-fatal: the cache auto-rebuilds on next connect(). Just warn.
        print(f"WARNING: DuckDB cache rebuild skipped ({e}); "
              f"first request will rebuild it (~150ms).", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-shot rebuild of entities/graph_edges/graph_analytics "
                    "from canonical DDL (Bundle P)."
    )
    parser.add_argument(
        "--db", default=str(DB_PATH),
        help="SQLite DB path (default: memory/research.db).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be rebuilt without writing.",
    )
    parser.add_argument(
        "--no-duckdb-refresh", dest="refresh_duckdb", action="store_false",
        help="Skip the DuckDB cache refresh (it will rebuild on next connect).",
    )
    parser.set_defaults(refresh_duckdb=True)
    args = parser.parse_args()

    stats = rebuild(args.db, dry_run=args.dry_run)

    print("== Bundle P schema rebuild ==")
    for k, v in stats.items():
        print(f"  {k:25} {v}")

    if args.dry_run:
        print("  (dry-run — no changes made)")
        return 0

    if args.refresh_duckdb:
        _refresh_duckdb_cache()

    print("\nNext steps:")
    print("  make snapshot   # refresh db-backup/*.zst artifacts")
    print("  make qa         # verify the full gate passes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
