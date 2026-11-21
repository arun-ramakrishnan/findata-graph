#!/usr/bin/env python3
"""Perf gate: BFS shortest_path (sql_capability_unlocks B2).

Three measured cases, best-of-3 per case:
  1. default-shaped request: CEAT -> Automotive at hops=5 with
     edge_label=None (the /api/graph/shortest production shape —
     the CSR lane serves it) — asserted < 100ms (steady 8-14ms).
  2. unreachable worst case, production shape: two edge ENDPOINTS from
     DIFFERENT undirected components, edge_label=None — the CSR lane
     exhausts the source component (a full traversal, ~150ms) before
     returning None — asserted < 250ms.
  3. SQL fallback guard: the same unreachable pair forced onto the SQL
     BFS via an unrecognized edge_label ("NoSuchLabel" resolves to no
     filter in _bfs_step_where but fails the lane gate, which requires
     a literal None). This is the path production takes when the CSR
     substrate is stale (generation drift) or when ?as_of= filters the
     traversal — asserted < 2s.

The FIRST call per connection pays a one-time ~200-300ms plan/page-cache
warm-up, so unmeasured warm-up calls absorb it — production serves
from a long-lived connection where steady-state is what matters.

Budgets recalibrated 2026-08-22: a flat 100ms bar (set at #143 on an
idle machine) flaked under real desktop load — the unreachable case's
steady-state is bimodal (29-83ms idle, 120ms+ when the box is busy;
load avg ~3.5 during a flake storm, 3/12 best-of-3 failures). Best-of-3
absorbs per-call scheduler jitter.

Reshaped 2026-11-21 (spike, engine matrix on the live CSR — 22,837
nodes / 178,766 rows): the old single unreachable case ran
edge_label="NoSuchLabel", which fails the lane gate and measured
0.7-1.6s under contention (load ~4) against a 250ms budget — 4 of the
prior 8 recorded runs were FAIL(contended). The lane exhaustion shape
measures 147-149ms (no DB in the hot loop after the freshness check)
and the Mojo binary 61-65ms; a subprocess kernel cannot beat the
in-process lane's 0.04ms reachable shape, so the lever was routing the
probe through the CSR substrate at all. The SQL guard keeps its own 2s
budget (contended floor 0.7-1.6s) while still failing instantly on the
regression class this gate exists for (the retired CTE was multi-second
— 10.6s of a 12.0s leg wall, orders of magnitude away).

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
BUDGET_SQL_FALLBACK_S = 2.0
_REPS = 3


def _component_and_endpoints(con, src_id: int) -> tuple[set[int], set[int]]:
    """The undirected component of ``src_id`` + every e_all_und endpoint.

    One scan over e_all_und (~0.1 s for 201,534 edges; the table stores
    both orientations per dyad, so a single a->b pass is complete). The
    previous recursive CTE walked the component with per-iteration UNION
    anti-joins — 10.6 s of a 12.0 s leg wall (2026-11-19), pure planner
    pathology: the same relation serves the BFS itself in <100 ms, and a
    correlated NOT EXISTS over it costs 13.7 s.
    """
    comp = {src_id}
    frontier = [src_id]
    adj: dict[int, list[int]] = {}
    endpoints: set[int] = set()
    for a, b in con.execute("SELECT a_id, b_id FROM e_all_und").fetchall():
        adj.setdefault(a, []).append(b)
        endpoints.add(a)
        endpoints.add(b)
    while frontier:
        nxt = []
        for node in frontier:
            for nb in adj.get(node, ()):
                if nb not in comp:
                    comp.add(nb)
                    nxt.append(nb)
        frontier = nxt
    return comp, endpoints


def _unreachable_dst(con) -> tuple[str, str]:
    """Two nodes in a different undirected component than CEAT.

    Returns (dst_any, dst_endpoint): the first v_node name outside the
    component (any node, for the SQL guard), and the first name that is
    an e_all_und ENDPOINT outside the component (for the lane case — an
    endpoint is guaranteed to live in the CSR universe, so the lane
    actually traverses instead of short-circuiting on the pos check).
    """
    src = con.execute("SELECT id FROM v_node WHERE name = 'CEAT'").fetchone()
    if src is None:
        raise SystemExit("CEAT not in the live graph")
    comp, endpoints = _component_and_endpoints(con, src[0])
    dst_any: str | None = None
    dst_endpoint: str | None = None
    for nid, name in con.execute("SELECT id, name FROM v_node ORDER BY name").fetchall():
        if nid in comp:
            continue
        if dst_any is None:
            dst_any = name
        if nid in endpoints:
            dst_endpoint = name
            break
    if dst_any is None:
        raise SystemExit("live graph is fully connected — no unreachable pair")
    if dst_endpoint is None:
        raise SystemExit("no unreachable ENDPOINT pair — CSR lane case vacuous")
    return dst_any, dst_endpoint


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
        dst_any, dst_endpoint = _unreachable_dst(con)

        # Warm-up (unmeasured): absorbs one-time plan/page-cache costs.
        shortest_path(con, "CEAT", "Automotive", max_hops=5, edge_label=None)
        shortest_path(con, "CEAT", dst_endpoint, max_hops=8, edge_label=None)
        shortest_path(con, "CEAT", dst_any, max_hops=8, edge_label="NoSuchLabel")

        dt1, r1 = _best_of(con, "CEAT", "Automotive", 5, None)
        dt2, r2 = _best_of(con, "CEAT", dst_endpoint, 8, None)
        dt3, r3 = _best_of(con, "CEAT", dst_any, 8, "NoSuchLabel")
    finally:
        con.close()

    ok1 = r1 is not None and dt1 < BUDGET_DEFAULT_S
    ok2 = r2 is None and dt2 < BUDGET_UNREACHABLE_S
    ok3 = r3 is None and dt3 < BUDGET_SQL_FALLBACK_S
    print(
        f"  shortest_path default       (CEAT->Automotive, hops=5, lane): "
        f"{dt1 * 1000:7.1f}ms  [{'OK' if ok1 else 'FAIL'}]"
    )
    print(
        f"  shortest_path unreachable   (CEAT->{dst_endpoint}, hops=8, lane): "
        f"{dt2 * 1000:7.1f}ms  [{'OK' if ok2 else 'FAIL'}]"
    )
    print(
        f"  shortest_path sql-fallback  (CEAT->{dst_any}, hops=8, NoSuchLabel): "
        f"{dt3 * 1000:7.1f}ms  [{'OK' if ok3 else 'FAIL'}]"
    )
    if not (ok1 and ok2 and ok3):
        print(
            f"  budget: {BUDGET_DEFAULT_S * 1000:.0f}ms default / "
            f"{BUDGET_UNREACHABLE_S * 1000:.0f}ms lane-exhaustion / "
            f"{BUDGET_SQL_FALLBACK_S * 1000:.0f}ms sql-fallback, "
            f"best of {_REPS}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
