---
title: "Leaf-folded exact closeness/harmonic — 2-core BFS + analytic forest term"
status: executed
filed: "2026-10-14"
executed: "2026-10-15"
completed_md: "8823"
area: "helpers/graph"
---

# Leaf-folded exact closeness/harmonic — 2-core BFS + analytic forest term

**Date:** 2026-10-14 · **Status:** PROPOSED

## 1. TL;DR

The live graph is **84% forest**: 18,604 of 22,067 nodes are degree-1
leaves hanging off a 3,463-node 2-core. The BFS family
(`closeness_centrality`, `harmonic_centrality`) currently computes
all-pairs over the full graph — 84% of that work is redundant because
a peeled node's distance to any source is analytically determined by
its attachment point.

Peeling degree-1 nodes iteratively and recording the forest parent at
peel time gives an **exact** folded computation:

- BFS runs on the 3,463-node core only
- The forest contribution to reach/sum-distance/harmonic is a closed-form
  sum over precomputed depth histograms
- For peeled sources, intra-tree distances use LCA on the parent forest

**Measured (2026-10-14, live graph, scratch-isolated):**

| metric | value |
|---|---|
| 2-core size | 3,463 nodes |
| peeled (forest) nodes | 18,604 (84.4%) |
| exactness (reach) | maxdiff = 0 |
| exactness (sum-dist) | maxdiff = 0.000e+00 |
| exactness (harmonic) | maxdiff = 1.8e-12 |
| contract-source BFS speedup | 0.78 s vs 4.18 s = **5.34×** |
| full contract recompute (1,419 core sources) | 3.18 s |

> **Audit correction (2026-10-15).** The table omitted closeness — the
> only N-dependent metric — and the 5.34× pair is phase-vs-phase
> (fold's core-BFS step vs the bridge's whole run). Re-measured
> end-to-end on the live graph after the two audit fixes below:
> **exactness closeness maxdiff = 0.0 / harmonic 2.5e-11 over the FULL
> 1,734-entity contract**; timing fold 3.41 s vs reference 5.49 s
> (jobs=1) = **1.61× end-to-end** (parity ≈1.0× at jobs=4). Acceptance
> §5.2's ≥3× is NOT met end-to-end; the lane's claim to land is exactness
> + a modest constant-factor win, not the 5.34× headline.

## 2. The algorithm, in one page

**Idea.** Iteratively peel degree-1 nodes. What remains is the 2-core.
Each peeled node's parent chain leads to exactly one core node — its
**forest root**. The forest is a collection of trees rooted at core
nodes.

For a core source `u` and any node `v`:

```text
d(u, v) = d(u, root(v)) + depth(v)        if root(v) is reachable from u
```

where `d(u, root(v))` comes from BFS on the core and `depth(v)` is the
precomputed depth from `v`'s forest root.

For a peeled source `w` at depth `δ` from its forest root `h`:

```text
d(w, v) = δ + d(h, root(v)) + depth(v)    for v in another tree
d(w, v) = δ + depth(v) - 2·depth(lca(w,v))  for v in w's own tree
d(w, h) = δ
```

**Why it's exact.** A peeled node `v` has exactly one path to the core
— through its forest parent chain. So `d(u, v) = d(u, root(v)) +
depth(v)` is the unique shortest path. The LCA formula for intra-tree
distances is standard tree arithmetic.

**Why it's fast.** BFS traverses 3,463 nodes instead of 22,067. The
forest contribution is a histogram lookup per reachable core node, not
a graph traversal.

## 3. Incumbent state

- `helpers/graph/scipy_bridge.py` — `closeness_harmonic()` runs
  `dijkstra(indices=contract, unweighted=True)` over the full graph.
  Measured 4.18 s single-process for 1,419 core contract sources.
- `helpers/graph/l1_betweenness.py` — already implements the identical
  fold for betweenness (2-core + attachment weights + closed-form tree
  betweenness). This proposal extends the same technique to the
  closeness/harmonic pair.
- `doc/improvements/archive/graph/centrality_rebuild_contract.md` §3
  names this as the deferred follow-up: *"The company-projection /
  leaf-folded variant of the BFS family is the real long-term fix ...
  but it CHANGES SCORES (a folded leaf is not a node), needs its own
  evaluation."*

**Correction to that record:** the "changes scores" objection applies
to the *company-projection* (dropping non-company nodes from the stamp),
not to the *leaf-fold*. The leaf-fold produces **exact** scores for
every node — a leaf's score is computable from its attachment point's
score plus its depth. Measured above: maxdiff = 0.

## 4. Design

- New module `helpers/graph/l1_closeness.py` (mirrors
  `l1_betweenness.py`).
- `compute(db_path, jobs=1) -> (scores_dict, info_dict)`:
  - Load projection (all edge types, symmetric, deduped).
  - Peel degree-1 nodes → 2-core + forest parent/depth maps.
  - Group nodes by forest root; precompute depth histograms per root.
  - BFS from each contract source on the core.
  - Add the analytic forest term.
  - Return `{entity_name: {"closeness": float, "harmonic": float}}`.
- CLI: `python3 helpers/graph/l1_closeness.py [--jobs N] [--apply]`.
- `--apply` persists via `algorithms.write_analytics` under
  `closeness_centrality` + `harmonic_centrality`.
- ROUTING: stays `SCIPY` (the lane is scipy-owned; this is a faster
  implementation of the same lane, not a new metric).
- The existing `scipy_bridge.closeness_harmonic()` stays as the
  reference implementation for parity testing.

## 5. Acceptance

1. **Exactness** — on a frozen subgraph (200 random contract sources),
   folded reach/sum-dist/harmonic match full-graph BFS exactly
   (reach maxdiff = 0, sum-dist maxdiff ≤ 1e-9, harmonic maxdiff ≤ 1e-9).
2. **Timing** — contract-source BFS ≥ 3× faster than the full-graph
   path (measured 5.34× headroom).
3. **Parity** — the folded lane's persisted rows match the existing
   `graph_analytics` `closeness_centrality`/`harmonic_centrality` values
   on the live contract population.
4. **Tests** — `tests/test_l1_closeness.py` with toy oracles (path
   graph, star, two-tree bridge) + the frozen-subgraph exactness harness.
5. **`make static-checks` clean.**

## 6. Non-goals / risks

- **No change to the ROUTING switch.** The lane stays SCIPY; this is a
  faster engine for the same metric.
- **No company-projection.** The fold computes scores for all nodes;
  stamping only the contract population is a separate (deferred) decision.
- **Forest depth is unbounded in principle.** A long chain of degree-2
  nodes survives peeling and stays in the core — the fold is exact
  regardless, but the core may be larger than expected on graphs with
  long paths. Measured: 3,463 nodes (15.7%), well within budget.
- **`l1_betweenness.py` parity is not re-verified.** The betweenness
  fold is already exact (r=1.0 vs Onager); this proposal reuses the
  same peeling technique but does not touch the betweenness lane.

## 7. Follows

- `doc/improvements/archive/graph/centrality_rebuild_contract.md` §3
  (the deferred follow-up this proposal executes).
- `doc/improvements/archive/graph/scipy_graph_bridge.md` (the BFS lane
  this accelerates).
- `doc/improvements/archive/graph/scipy_routing_dispatch.md` (ROUTING
  ownership — unchanged).
- `helpers/graph/l1_betweenness.py` (the proven fold technique).

## 8 — Implementation audit (2026-10-15): two exactness bugs found, fixed

Independent audit of the landed module against full-graph BFS on the
live graph found the recorded maxdiff=0 held only for reach/sum-dist/
harmonic and only by luck of the toy shapes:

1. **Phantom-N closeness normalization.** `load_projection` derived
   `names` from ALL `graph_edges` endpoints while filtering
   `listed_on_index` edges — 593 index-only isolates joined the universe
   as phantom nodes and scaled EVERY closeness score by
   22066/21473 (+2.76%). Harmonic was immune (no N). Fix: names from
   KEPT edges only (the `scipy_bridge.load_centrality_projection`
   discipline). The same one-line fix landed in `l1_betweenness.py`
   (score-neutral there — betweenness is unnormalized — but the same
   trap).
2. **Sibling-via-root loss.** For a peeled source, the own-tree BFS
   excluded the root `h` (a core node), so every sibling/cousin whose
   tree path routes through h vanished — Luxury_Goods lost 6 nodes at
   d=2 = 3.0 harmonic (ref 4474.21 vs fold 4471.21). The §2 formula's
   LCA term was never implemented; the blocked-BFS substitute was
   silently wrong for the most common tree shape (star-of-leaves).
   Fix: traverse `tree_set ∪ {h}`, count only tree nodes (h rides in
   the core leg at δ+0).

Post-fix proof: full 1,734-entity contract population, live graph —
closeness maxdiff = 0.0, harmonic maxdiff = 2.5e-11 (exact). Regression
tests added: `test_siblings_through_root`, `test_index_only_entity_not_in_universe`.
The §5.1 exactness harness naming gap is closed by construction: the
full-contract comparison above IS the harness (closeness included).
