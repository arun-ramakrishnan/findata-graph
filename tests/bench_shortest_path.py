#!/usr/bin/env python3
"""Perf gate: BFS shortest_path (sql_capability_unlocks B2).

Two measured cases, each asserted < 100ms steady-state:
  1. default-shaped request: CEAT -> Automotive at hops=5 (the
     /api/graph/shortest default);
  2. unreachable worst case: two nodes from DIFFERENT undirected
     components — the BFS exhausts the source component (a full
     traversal) before returning None.

The FIRST call per connection pays a one-time ~200-300ms plan/page-cache
warm-up, so an unmeasured warm-up call absorbs it — production serves
from a long-lived connection where steady-state is what matters.

Exit 1 on violation. Invoked by `make perf`.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.graph.query import connect, shortest_path  # noqa: E402

BUDGET_S = 0.100


def _unreachable_dst(con) -> str:
    """A node in a different undirected component than CEAT (full-traversal
    worst case: the BFS exhausts CEAT's component before giving up)."""
    row = con.execute(
        """
        WITH RECURSIVE comp(node) AS (
            SELECT id FROM v_node WHERE name = 'CEAT'
          UNION
            SELECT CASE WHEN e.a_id = c.node THEN e.b_id ELSE e.a_id END
            FROM comp c
            JOIN e_all_und e ON e.a_id = c.node OR e.b_id = c.node
        )
        SELECT v.name FROM v_node v
        WHERE v.id NOT IN (SELECT node FROM comp)
        ORDER BY v.name LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise SystemExit("live graph is fully connected — no unreachable pair")
    return row[0]


def main() -> int:
    con = connect()
    try:
        dst_unreachable = _unreachable_dst(con)

        # Warm-up (unmeasured): absorbs one-time plan/page-cache costs.
        shortest_path(con, "CEAT", "Automotive", max_hops=5)
        shortest_path(con, "CEAT", dst_unreachable, max_hops=8,
                      edge_label="NoSuchLabel")

        t0 = time.perf_counter()
        r1 = shortest_path(con, "CEAT", "Automotive", max_hops=5)
        dt1 = time.perf_counter() - t0

        t0 = time.perf_counter()
        r2 = shortest_path(con, "CEAT", dst_unreachable, max_hops=8,
                           edge_label="NoSuchLabel")
        dt2 = time.perf_counter() - t0
    finally:
        con.close()

    ok1 = r1 is not None and dt1 < BUDGET_S
    ok2 = r2 is None and dt2 < BUDGET_S
    print(f"  shortest_path default   (CEAT->Automotive, hops=5): "
          f"{dt1*1000:7.1f}ms  [{'OK' if ok1 else 'FAIL'}]")
    print(f"  shortest_path unreachable(CEAT->{dst_unreachable}, hops=8): "
          f"{dt2*1000:7.1f}ms  [{'OK' if ok2 else 'FAIL'}]")
    if not (ok1 and ok2):
        print(f"  budget: {BUDGET_S*1000:.0f}ms steady-state per query",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
