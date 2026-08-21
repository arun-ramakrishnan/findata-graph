#!/usr/bin/env python3
"""Perf gate: BFS shortest_path (sql_capability_unlocks B2).

Two measured cases, best-of-3 per case:
  1. default-shaped request: CEAT -> Automotive at hops=5 (the
     /api/graph/shortest default) — asserted < 100ms (stable 8-13ms).
  2. unreachable worst case: two nodes from DIFFERENT undirected
     components — the BFS exhausts the source component (a full
     traversal, all edge types) before returning None — asserted
     < 250ms.

The FIRST call per connection pays a one-time ~200-300ms plan/page-cache
warm-up, so an unmeasured warm-up call absorbs it — production serves
from a long-lived connection where steady-state is what matters.

Budgets recalibrated 2026-08-22: a flat 100ms bar (set at #143 on an
idle machine) flaked under real desktop load — the unreachable case's
steady-state is bimodal (29-83ms idle, 120ms+ when the box is busy;
load avg ~3.5 during a flake storm, 3/12 best-of-3 failures). Best-of-3
absorbs per-call scheduler jitter; the 250ms unreachable budget leaves
~2x headroom over the observed loaded floor while still failing
instantly on the regression class this gate exists for (the retired CTE
was multi-second — orders of magnitude away).

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

BUDGET_DEFAULT_S = 0.100
BUDGET_UNREACHABLE_S = 0.250
_REPS = 3


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


def _best_of(con, src: str, dst: str, hops: int, label: str | None):
    """Run one case REPS times; return (best_seconds, last_result)."""
    best = float("inf")
    result = None
    for _ in range(_REPS):
        t0 = time.perf_counter()
        result = shortest_path(con, src, dst, max_hops=hops, edge_label=label)
        best = min(best, time.perf_counter() - t0)
    return best, result


def main() -> int:
    con = connect()
    try:
        dst_unreachable = _unreachable_dst(con)

        # Warm-up (unmeasured): absorbs one-time plan/page-cache costs.
        shortest_path(con, "CEAT", "Automotive", max_hops=5)
        shortest_path(con, "CEAT", dst_unreachable, max_hops=8,
                      edge_label="NoSuchLabel")

        dt1, r1 = _best_of(con, "CEAT", "Automotive", 5, None)
        dt2, r2 = _best_of(con, "CEAT", dst_unreachable, 8, "NoSuchLabel")
    finally:
        con.close()

    ok1 = r1 is not None and dt1 < BUDGET_DEFAULT_S
    ok2 = r2 is None and dt2 < BUDGET_UNREACHABLE_S
    print(f"  shortest_path default   (CEAT->Automotive, hops=5): "
          f"{dt1*1000:7.1f}ms  [{'OK' if ok1 else 'FAIL'}]")
    print(f"  shortest_path unreachable(CEAT->{dst_unreachable}, hops=8): "
          f"{dt2*1000:7.1f}ms  [{'OK' if ok2 else 'FAIL'}]")
    if not (ok1 and ok2):
        print(f"  budget: {BUDGET_DEFAULT_S*1000:.0f}ms default / "
              f"{BUDGET_UNREACHABLE_S*1000:.0f}ms unreachable, "
              f"best of {_REPS}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
