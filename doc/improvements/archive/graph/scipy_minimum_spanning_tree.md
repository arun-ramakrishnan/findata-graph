---
title: "SciPy minimum spanning tree — wiring the deferred need — DEFERRED"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "274"
---

# SciPy minimum spanning tree — wiring the deferred need — DEFERRED (trigger: the need un-deferred)

**Date:** 2026-09-23 · **Status:** DEFERRED — archived 2026-09-23

Child of `scipy_graph_bridge.md` (S3, second item). Implementation
lands in the umbrella patch `scipy_algos`.

## 1. TL;DR

``csgraph.minimum_spanning_tree`` over the weighted CSR — total weight
+ edge list, dry-run only. Wires the need Onager never had a lane for.

## 2. The algorithm, in one page

**Idea.** Keep the cheapest subset of edges that still connects
everything: a spanning TREE has exactly ``V - 1`` edges and no cycles;
the minimum one minimizes total weight. It is the canonical
"backbone/denoising" operator — e.g. in correlation networks (the
classic asset-graph use) the MST keeps the dominant linkage structure
and drops redundant triangle edges.

**Why greedy works (the two exchange properties).**
- Cut property: for any partition of the nodes, the minimum-weight
  edge crossing it belongs to some MST.
- Cycle property: the heaviest edge on any cycle is never needed.
Kruskal (sort edges; add each that joins two components — union-find)
and Prim (grow one tree outward) both follow from these; scipy's
implementation is Kruskal-family on the CSR.

**Worked example.** Triangle with weights 1, 2, 3: the MST is the two
cheapest edges (total 3) — the 3-edge is the heaviest on the only
cycle, so the cycle property discards it. Square with sides 1, 2, 3, 4
plus a diagonal of 1.5: MST = {1, 1.5, 2} total 4.5 — the diagonal
replaces the two expensive sides.

**Forests.** On a disconnected input there is no spanning tree — scipy
returns a spanning FOREST (one MST per component, ``V - C`` edges
total). The lane prints the component count so a forest is never
mistaken for a tree.

## 3. Incumbent state

Deferred-need note in the graph algos record; no incumbent lane.

## 4. Design

- Weighted CSR (sum-per-pair, mirrored); MST result CSR read back to a
  named edge list; total weight reported.
- Disconnected input yields a per-component spanning FOREST (scipy
  semantics) — component count reported alongside.
- ``mst`` command; no ``--apply`` (no per-entity metric shape).
- Output is capped: total weight + component count always; edge list
  truncated to the first 1,000 with the total stated (a 22k-node forest
  listing is its own output blowup — same posture as the pref-attach
  per-node cap).

## 5. Acceptance

1. Toy: total weight AND edge set match a Kruskal reference
   (union-find in the test, ~15 lines — no new dependency); the
   triangle and square+diagonal examples above are hand oracles.
2. Live: smoke timing + component count printed.
3. ``tests/test_scipy_bridge.py`` additions green.

## 6. Non-goals / risks

- No write surface; opt-in reporting lane.
- Tie-breaking on equal weights is scipy-internal (deterministic per
  CSR order, not specified upstream) — tests compare WEIGHT and SET
  size, not edge identity, on tie-heavy toys.

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; lane
`helpers/graph/scipy_bridge.py`.
