---
title: "SciPy Yen K-shortest paths — new capability, opt-in — DEFERRED"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "275"
---

# SciPy Yen K-shortest paths — new capability, opt-in — DEFERRED (trigger: first consumer)

**Date:** 2026-09-23 · **Status:** DEFERRED — archived 2026-09-23

> **Un-deferred 2026-09-24** by
> `doc/improvements/archive/graph/scipy_st_lanes_routing_switch.md`
> (implemented in `helpers/graph/scipy_bridge.py` as `yen_paths` / the
> `yen` CLI command, registered in the ROUTING switch with a wall-clock
> budget guard). This record stays as the filed DEFERRED rationale and the
> acceptance design.

Child of `scipy_graph_bridge.md` (S3, third item). Implementation
lands in the umbrella patch `scipy_algos`.

## 1. TL;DR

``csgraph.yen(csgraph, source, sink, K)`` — K simple shortest paths,
something neither SQL nor Onager can express. Opt-in lane, no consumer
yet; parked after acceptance.

## 2. The algorithm, in one page

**Idea.** Dijkstra answers "the shortest path". Yen answers the next
K-1 too: the K shortest SIMPLE paths (no node repeated), ranked by
total cost. Simple-ness matters — without it the "2nd shortest path"
would just be the shortest with a pointless loop in it.

**How it works.** Start from the Dijkstra path. For each node along
it (the "spur node"), temporarily forbid the edges that earlier
answers used at that position, re-run Dijkstra from the spur, and
splice "deviation" candidates into a candidate heap; pop the best K
times. Cost: roughly K Dijkstras — cheap at our scale.

**Worked example.** Nodes 0-4; edges: 0-1 (1), 1-2 (2), 2-4 (1),
0-3 (2), 3-2 (2), 1-3 (3). The three shortest 0->4 paths:

    0-1-2-4    cost 1+2+1 = 4
    0-3-2-4    cost 2+2+1 = 5
    0-1-3-2-4  cost 1+3+2+1 = 7

All simple, costs nondecreasing, first equals the Dijkstra answer —
exactly the acceptance shape.

**Reading it on our graph.** Relation chains between two entities:
the primary path plus the next-cheapest ALTERNATIVES. Useful when the
top chain's weakest edge is suspect — the runner-ups show which
linkage survives without it (diversity of explanation; complements
max-flow's bottleneck view).

## 3. Incumbent state

None — new capability.

## 4. Design

- Weighted CSR as in the other children; ``--source/--sink/--k``.
- K is bounded: ``--k`` default 5, max 25 (cost ≈ K Dijkstras —
  unbounded K is refused, same fail-loud posture as the pref-attach
  gate: no silent multi-minute scan).
- Output: per-path cost + node sequence (named), dry-run only.
- Directed flag exposed (default undirected, matching the lane family).

## 5. Acceptance

1. Toy: K paths are SIMPLE, costs nondecreasing, and the first equals
   the dijkstra shortest path (the 0->4 example above is the oracle).
2. Live: one pair, K=5, smoke timing.
3. ``tests/test_scipy_bridge.py`` additions green.

## 6. Non-goals / risks

- No consumer, no write surface, no perf leg.
- Costs are weighted sums; unweighted variant available via the same
  ``unweighted`` discipline as the L1a lane if ever needed.

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; lane
`helpers/graph/scipy_bridge.py`.
