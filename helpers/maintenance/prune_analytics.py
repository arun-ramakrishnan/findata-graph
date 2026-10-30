#!/usr/bin/env python3
"""Prune orphaned graph_analytics rows (graph_analytics_integrity S4).

The store is the serving layer for ``/api/graph/metrics/<metric>`` and had
no reconciliation path at all: the only ``DELETE FROM graph_analytics`` in
the tree was in a test. UPSERT-only writes accumulated generations (voterank
served a 2.5-month-old seed list; link_prediction held three date
generations) and rows for entities that left ``entities`` lingered as
orphans (11 live at the 2026-10-30 census, plus historical stragglers).

The FK cascade already removes a metric's rows when its entity is deleted
going forward; this sweep is for what predates the discipline. Dry-run by
default — a census of orphaned rows per metric; ``--apply`` deletes them.

Note S4 also fixed the WRITE side: payload metrics (voterank,
link_prediction) now reconcile on write via
``write_analytics(replace_population=True)``, so future generations cannot
accumulate. This tool covers the residue the write-side fix cannot reach.

Usage:
    python3 helpers/maintenance/prune_analytics.py            # dry-run census
    python3 helpers/maintenance/prune_analytics.py --apply    # delete orphans
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.core.db import connect  # noqa: E402  (research.db writes go through connect())


def orphan_census(conn) -> list[tuple[str, int]]:
    """Per-metric counts of rows whose entity_name has no entities row."""
    rows = conn.execute(
        "SELECT metric, COUNT(*) FROM graph_analytics a "
        "WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.name = a.entity_name) "
        "GROUP BY metric ORDER BY metric"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete the orphaned rows (default: dry-run census)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="research.db path (default: the repo store)",
    )
    args = parser.parse_args(argv)
    conn = connect(args.db)
    try:
        orphans = orphan_census(conn)
        total = sum(n for _, n in orphans)
        if not orphans:
            print("prune-analytics: 0 orphaned rows — nothing to do")
            return 0
        for metric, n in orphans:
            print(f"  {metric:36} {n:6} orphaned row(s)")
        print(f"total: {total} row(s) across {len(orphans)} metric(s)")
        if not args.apply:
            print("DRY-RUN (pass --apply to delete)")
            return 0
        with conn:
            cur = conn.execute(
                "DELETE FROM graph_analytics WHERE NOT EXISTS "
                "(SELECT 1 FROM entities e WHERE e.name = graph_analytics.entity_name)"
            )
        print(f"APPLY: deleted {cur.rowcount} orphaned row(s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
