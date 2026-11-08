#!/usr/bin/env python3
"""DuckDB file-block anatomy: live vs dead (free-listed) blocks, read-only.

DuckDB never returns freed blocks to the OS — ``VACUUM``/``CHECKPOINT``
measured zero reclamation on a bloated copy (2026-11-06, proposal
``convo_search_dead_block_compaction`` §2.2); the only compactor is a
swap rebuild (``COPY FROM DATABASE`` / ``rebuild_convo_search.py
--swap``). This module is the waste metric that was missing when
``db_maint``'s DuckDB leg was deferred ("F4 — deferred until a valid
waste metric exists"): it counts the blocks a file actually spends on
live row data vs the ones stranded by DELETE+reinsert churn.

Read-only by construction (``open_read_only``: quack wire-attach when
served, shared flock otherwise) and advisory by contract — ``anatomy``
returns a status dict instead of raising on absent/busy/unreadable
stores so maintenance cycles can degrade to the cheap path.

  anatomy(db_path)      -> per-store anatomy dict (or unavailable note)
  above_swap_trigger(a) -> proposal S2 predicate:
                           dead_bytes >= max(MIN_DEAD_BYTES, MAX_DEAD_PCT% of file)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Standalone sanity leg (`python3 helpers/maintenance/duckdb_block_anatomy.py`)
# needs the repo root on sys.path for the helpers.* imports, same bootstrap
# as bili_tunewatch.py.
_REPO = str(Path(__file__).resolve().parents[2])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# Proposal S2 trigger: dead bytes >= max(2 GB, 60% of file). The >= (not
# the sqlite freelist's strict >) is deliberate — this is a lower-bound
# "big enough to be worth a full swap rebuild" test, not a waste alarm;
# the boundary is pinned by test_duckdb_block_anatomy.py.
#
# Recalibrated 2026-11-08 (operator: "swap criteria is too tight"): a swap
# rebuilds the LIVE data (~3570 blocks, ~27 min), so the trigger only sets
# frequency — each incremental pass strands ~650 MB dead, and at 1 GB/40%
# the first production cycle fired after just two passes (2026-11-08:
# 61.7% dead, swap 1596 s). 2 GB/60% doubles the interval; peak waste
# stays ~2.6 GB on this store, which is cheap relative to a daily swap.
MIN_DEAD_BYTES = 2_000_000_000
MAX_DEAD_PCT = 60.0


def anatomy(db_path: Path | str) -> dict:
    """Measure one DuckDB store's block anatomy, read-only.

    Returns a dict; on any failure returns a minimal note dict with
    ``status != "ok"`` (absent store, lock contention, unreadable) —
    callers treat non-ok as "cannot measure, stay on the cheap path".
    """
    path = Path(db_path)
    if not path.exists():
        return {"status": "absent", "db_path": str(path)}

    from helpers.misc.duckdb_lock import open_read_only

    try:
        con = open_read_only(path)
    except Exception as exc:  # advisory degrade — busy/locked/unreadable
        return {"status": "unavailable", "db_path": str(path), "reason": type(exc).__name__}

    try:
        return anatomy_from_con(con, path)
    finally:
        con.close()


def anatomy_from_con(con, db_path: Path | str) -> dict:
    """Measure a store's block anatomy over an ALREADY-OPEN connection.

    For callers that hold their own lock/window on the store (db_maint's
    sidecar loop measures inside its exclusive io_lock — a nested
    ``open_read_only`` would queue on the same flock and deadlock).
    Same contract as ``anatomy``: non-ok status dict instead of raising.
    """
    path = Path(db_path)
    try:
        (
            _name,
            _size,
            block_size,
            total_blocks,
            blocks_used,
            *_rest,
        ) = con.execute("PRAGMA database_size").fetchone()
        live: set[int] = set()
        tables = con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() "
            "WHERE NOT internal ORDER BY schema_name, table_name"
        ).fetchall()
        for schema, table in tables:
            qualified = table if schema == "main" else f"{schema}.{table}"
            # PRAGMA storage_info takes a quoted identifier, one table at
            # a time. block_id (index varies across versions — read it by
            # name) is per-file, so a set-union across tables yields every
            # block holding at least one live segment; additional_block_ids
            # carries the spill blocks of values too large for one block.
            rows = con.execute(f"PRAGMA storage_info('{qualified}')").fetchall()
            cols = [c[0] for c in con.description]
            bi, abi = cols.index("block_id"), cols.index("additional_block_ids")
            for row in rows:
                if row[bi] is not None:
                    live.add(row[bi])
                for extra in row[abi] or ():
                    live.add(extra)
        dead_blocks = max(blocks_used - len(live), 0)
        file_bytes = path.stat().st_size
        dead_bytes = dead_blocks * block_size
        return {
            "status": "ok",
            "db_path": str(path),
            "file_bytes": file_bytes,
            "block_size": block_size,
            "total_blocks": total_blocks,
            "blocks_used": blocks_used,
            "live_blocks": len(live),
            "dead_blocks": dead_blocks,
            "dead_bytes": dead_bytes,
            "dead_pct": round(dead_bytes * 100.0 / file_bytes, 1) if file_bytes else 0.0,
        }
    except Exception as exc:  # e.g. a concurrently replaced file mid-probe
        return {"status": "unavailable", "db_path": str(path), "reason": type(exc).__name__}
    finally:
        con.close()


def above_swap_trigger(
    a: dict, *, min_dead_bytes: int = MIN_DEAD_BYTES, max_dead_pct: float = MAX_DEAD_PCT
) -> bool:
    """True when dead space justifies upgrading this cycle to ``--swap``."""
    if a.get("status") != "ok":
        return False
    return a["dead_bytes"] >= max(min_dead_bytes, max_dead_pct / 100.0 * a["file_bytes"])


def summarize(a: dict) -> str:
    """One-line human rendering for maintenance reports.

    Report-only, never raises: the tripwire logs this AFTER the flip is
    applied, so a partial dict must degrade to the fields it has (a
    raise here would mislabel an applied flip as a probe failure).
    """
    if a.get("status") != "ok":
        note = f" ({a.get('reason')})" if a.get("reason") else ""
        return f"anatomy {a.get('status')}{note}"
    return (
        f"{a.get('file_bytes', 0) / 1e9:.2f} GB, live {a.get('live_blocks', '?')} blocks, "
        f"dead {a.get('dead_bytes', 0) / 1e6:.0f} MB ({a.get('dead_pct', '?')}%)"
    )


__all__ = [
    "MIN_DEAD_BYTES",
    "MAX_DEAD_PCT",
    "anatomy",
    "anatomy_from_con",
    "above_swap_trigger",
    "summarize",
]


def _selfcheck() -> int:  # pragma: no cover — manual sanity leg
    """`python3 helpers/maintenance/duckdb_block_anatomy.py [db]` one-liner."""
    target = sys.argv[1] if len(sys.argv) > 1 else "memory/convo_search.duckdb"
    a = anatomy(target)
    print(summarize(a))
    print("above_swap_trigger:", above_swap_trigger(a))
    return 0 if a.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(_selfcheck())
