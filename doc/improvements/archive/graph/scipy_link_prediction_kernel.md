---
title: "SciPy sparse link-prediction kernel — DEFERRED"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "276"
---

# SciPy sparse link-prediction kernel — DEFERRED (trigger not met)

**Date:** 2026-09-23 · **Status:** DEFERRED — archived 2026-09-23

Child of `scipy_graph_bridge.md` (S4, conditional). Would land in the
umbrella patch `scipy_algos` — **no code is owed while deferred**.

## 1. Status: DEFERRED

The parent trigger is "runs ONLY if the SQL-side L1c pruning stalls —
SQL stays primary (no engine change was ever needed there)". The SQL
side delivered (`graph_perf_l1` S3, 2026-09-23): all-pairs extension
scoring (57-61 s) replaced by an exact SQL 2-hop candidate join over
the materialised projection — 0.65 s in-process, top-10 bit-identical
vs the unpruned run, `graph_link_prediction` GREEN at budget 4.0.
Trigger not met; revisit only if that leg blows budget at future scale
(watch ~E x avg_deg growth of the 2-hop slot count).

## 2. The algorithm family, in one page

**Idea.** Two nodes are likely to connect if their neighbourhoods
overlap ("friends of friends"). All five scores are different
bookkeepings of the shared-neighbour set ``N(u) cap N(v)``:

- common-neighbors: ``|N(u) cap N(v)|`` — raw overlap count.
- jaccard: overlap / union — overlap normalized by how much there is
  to overlap (penalizes big neighbourhoods).
- adamic-adar: ``sum_w 1/ln(deg(w))`` over shared w — each shared
  neighbour counted LESS the more connected (hub-like) it is.
- resource-allocation: ``sum_w 1/deg(w)`` — the hub penalty in
  linear, not log-log, form.
- preferential-attachment: ``deg(u)*deg(v)`` — no overlap needed at
  all; pure "the rich get richer" prior (this is why it is the one
  method the SQL 2-hop join cannot express — see the parent).

**The sparse trick (the kernel's whole point).** The candidate pairs
with ANY overlap are exactly the nonzeros of ``A^2`` — so the join is
a sparse matrix product, not a pair enumeration; the degree vectors
then turn the overlap into any of the four overlap scores in C-speed
sparse ops.

## 3. Design sketch (frozen from the parent, for the day it un-defers)

- A^2 nonzero pattern IS the 2-hop candidate join; degree vectors give
  the four overlap scores in sparse C ops.
- Numpy mechanics fixed: 2-hop joins as `intersect1d` on sorted CSR
  adjacency (C merge loops, not Python sets), per-node top-K via
  `argpartition` (O(n) partial selection), aggregations via
  `bincount`/`add.at`.

## 4. Acceptance (if ever un-deferred)

L1c's own: top-10 stable vs the unpruned run on a frozen subgraph —
not a second criterion.

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md` §3 S4;
lane-to-be `helpers/graph/scipy_bridge.py`.
