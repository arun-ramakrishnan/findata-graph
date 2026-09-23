---
title: "SciPy maximum flow — Dinic, retired-seat replacement — DEFERRED"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "273"
---

# SciPy maximum flow — Dinic, retired-seat replacement — DEFERRED (trigger: a named s-t question)

**Date:** 2026-09-23 · **Status:** DEFERRED — archived 2026-09-23

Child of `scipy_graph_bridge.md` (S3, first item). Implementation lands
in the umbrella patch `scipy_algos`.

## 1. TL;DR

``csgraph.maximum_flow`` (Dinic) replaces the retired igraph max-flow
seat. An s-t pair has no per-entity metric shape — dry-run only, same
disposition as the igraph pilot: value + min-cut edge listing to
stdout.

## 2. The algorithm, in one page

**Idea.** Edges are pipes with capacities; push as much "flow" as
possible from a source ``s`` to a sink ``t``. Flow conserves at every
intermediate node (in = out) and respects each edge's capacity.

**The max-flow / min-cut theorem (Ford-Fulkerson).** The maximum s-t
flow equals the minimum total capacity of any edge set whose removal
separates s from t — the bottleneck. The algorithm and the certificate
come together: when no augmenting path remains, the set of nodes still
reachable from s in the RESIDUAL graph (capacity minus already-pushed
flow) is one side of a minimum cut, and its crossing edges ARE the
bottleneck. That is why this lane reports the cut edges, not just the
number: the cut is the explanation of the value.

**Dinic's algorithm** (what scipy implements): repeat (a) BFS to build
a level graph over the residual, (b) send blocking flows along level
paths. Worst case O(V^2 E), very fast on real graphs — microseconds at
toy scale, sub-second live.

**Worked example (CLRS classic).** Edges (capacities): s->v1 16,
s->v2 13, v2->v1 4, v1->v3 12, v3->v2 9, v2->v4 14, v4->v3 7, v3->t 20,
v4->t 4. Maximum flow = 23; one minimum cut is
``{s, v1, v2, v4}`` vs ``{v3, t}`` — crossing edges v1->v3 (12) +
v4->v3 (7) + v4->t (4) = 23. The lane must reproduce both the 23 and
that cut set.

**Reading it on our graph.** Capacities are relation weights (or 1s
for edge-disjoint counts). The min cut between two companies is the
smallest set of RELATIONS whose removal disconnects them — a
bottleneck/narrow-channel detector, distinct from shortest paths.

## 3. Incumbent state

None — igraph seat retired with the license lane (bridge deleted at
HEAD, revival path commit 720e38ff). The pilot's hand-verified toy
(max flow 6 = min cut) is the acceptance oracle pattern.

## 4. Design

- Capacity matrix: int64 (scipy requirement) — edge weights cast;
  unweighted mode uses ones (edge-disjoint count).
- Min cut from the residual: BFS reachable set from source in
  (capacity - flow); cut edges = crossing arcs. Listed with names.
- ``max-flow --source X --sink Y [--unweighted]``; no ``--apply``
  (no metric shape — writing one would be a new contract by fiat).

## 5. Acceptance

1. Toy: hand-verified maxflow 6 = mincut with the cut edge set exact
   (CLRS example above carried in the test as the second oracle).
2. Live: one s-t pair, cut listing, timing smoke (< 2 s expected).
3. ``tests/test_scipy_bridge.py`` additions green.

## 6. Non-goals / risks

- No write surface (documented above).
- Integer capacities only (weights rounded) — documented limitation,
  matching scipy's API.

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; lane
`helpers/graph/scipy_bridge.py`.
