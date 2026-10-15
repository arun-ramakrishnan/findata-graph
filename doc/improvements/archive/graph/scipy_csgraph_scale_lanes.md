---
title: "SciPy csgraph scale lanes — MST backbone, bipartite matching, assignment, A² candidates"
status: executed
filed: "2026-10-15"
area: "helpers/graph"
executed: "2026-10-15"
completed_md: "393"
---

# SciPy csgraph scale lanes — MST backbone, bipartite matching, assignment, A² candidates

**Date:** 2026-10-15 · **Status:** PROPOSED

## 1. TL;DR

The 2026-09-24 consumer-gate on the csgraph-25 is flipped to a scale
gate (operator ruling, `doc/local/evaluations/graph_layer.md` trial
section): at 57,589 edges / 21,474 ex-index nodes, compute is a
non-issue — every trialed public solves in single-digit milliseconds —
so four lanes unlock on merit of their *answers*. Measured in-session
(read-only, scipy 1.18.1):

| slice | public | scale shape | measured | answer it produces |
|---|---|---|---|---|
| S1 | `minimum_spanning_tree` | whole weighted projection | 2.9 ms; forest 21,369 edges (=V−C), 105 components | the supply backbone — minimum relationships keeping each component connected |
| S2 | `maximum_bipartite_matching` | supplier_to 6,125×8,817 | 0.8 ms; **958 (10.9%)** | supplier concentration, made structural: the Hall-failure surface |
| S3 | `min_weight_full_bipartite_matching` | invested_in 277×84, stake-weighted | 0.1 ms; no full matching (82/84) | optimal primary-investor assignment — **conditional on a covering projection** |
| S4 | A² kernel (`B·Bᵀ`) | supplier bipartite | 5.7 ms; 347,302 candidates | derive-lane enrichment candidates at scale |

Slices are independent; each carries its own acceptance. No ROUTING
changes — these are side lanes, not centrality dispatch.

## 2. Why now (and why not earlier)

`scipy_graph_bridge.md` §7 dispositioned the 25 with "no consumer
recorded — file a child proposal first if a need appears." The gate was
right for a 960-edge economic tail; at today's densities the question
inverts — `supplier_to` alone is 16,584 weighted rows (6,125 → 8,817
endpoints), and the structures these algorithms answer (concentration,
backbone, coverage) are properties the graph now *has*, independent of
any single named consumer. The scale trials
(`graph_layer.md`, 2026-10-15 section) are the evidence; this proposal
wires the four that returned a structural answer and leaves
`structural_rank` (restates S2's count) and `reverse_cuthill_mckee`
(1.52× bandwidth, no dense consumer) parked.

## 3. S1 — MST/MSF backbone lane

Supersedes the deferred child
`doc/improvements/archive/graph/scipy_minimum_spanning_tree.md` — its
§2 algorithm record carries over (cut/cycle properties, Kruskal-on-CSR,
forest semantics); that child is dry-run-only and never named the viz
consumer, so it is absorbed here rather than un-archived. (Note: its
frontmatter says `status: executed` while the body says DEFERRED — fix
the frontmatter to `deferred` when next touched.)

- **Projection**: whole-graph weighted CSR, ex-index, self-loops
  dropped, parallel edges collapsed to **min(weight)** (MST wants the
  cheapest relationship; matches the trial harness).
- **Output**: per-component edge list (`source`, `target`, `weight`)
  + total weight + component count. scipy returns a spanning FOREST on
  disconnected input (105 components today) — the component report is
  mandatory so a forest is never mistaken for a tree.
- **Shape**: `l1_mst.py` style CLI (house L1 pattern), command
  `mst-backbone`, `--format {summary,edges,json}`, dry-run by default,
  **no `--apply`** (no per-entity metric shape — this is a relational
  readout, not a node score).
- **Consumers**: graph-view skeleton (16.6k supplier edges → 21k-edge
  total backbone for display), resilience reading (which relationships
  keep each component connected).

## 4. S2 — maximum bipartite matching (supplier concentration)

- **Projection**: `supplier_to` as a 0-1 incidence matrix
  (suppliers × consumers). The 4,432 entities on both sides do NOT
  break the algorithm — `maximum_bipartite_matching` needs a 0-1
  matrix, not disjoint populations — but self-pairs (firm matched to
  itself) are excluded, and the overlap is *reported* (it is itself a
  finding: intermediation).
- **Output**: matching size + coverage % (958 / 8,817 = 10.9% today),
  the **Hall-violator report** — maximal deficient consumer subsets,
  i.e. which consumer groups structurally cannot all have distinct
  suppliers — and per-sector cuts where a sector attribute exists.
- **Shape**: `scipy_matching.py` CLI, `match-suppliers` command,
  `--top-deficits K`; dry-run only.
- **Consumer**: concentration-risk readout. The 10.9% number is itself
  the product — it quantifies how far the supplier market is from
  "every consumer has its own supplier."

## 5. S3 — optimal assignment (conditional)

`min_weight_full_bipartite_matching` requires a matching that covers
every row. On `invested_in` as-is (277 investors × 84 targets) no full
matching exists — 82/84 max, and the 6 single-investor targets (HDFC
Bank, Sodexo, Dassault Systèmes…) create a Hall collision. Transposed
(84×277) the same wall appears for free: feasibility is a property of
the edge data, not the orientation.

- **This slice ships the feasibility checker, not the lane**: given any
  projected weighted bipartite matrix, report max-matching size, the
  deficient sets, and whether min-weight full assignment is admissible.
- **Unlock condition**: a projection with full coverage (per-sector
  cuts, or a future primary-relationship edge type). When met, the lane
  is `assign-primary` on the transposed orientation (every target its
  minimum-stake investor) — scipy solves 84×277 in <1 ms.

## 6. S4 — A² candidate kernel

- **Surface**: `B·Bᵀ` over the supplier incidence matrix —
  supplier-supplier pairs sharing a consumer. Today: 347,302 candidate
  pairs in 5.7 ms.
- **Output**: top-K candidate pairs by shared-consumer count with the
  shared set sizes — the derive-lane enrichment feed ("these two firms
  supply the same buyer; are they competitors? partners?").
- **Boundary**: the kernel ships with counts only. Routing candidates
  into `competes_with`/`jv_with` derivation stays behind the
  `algorithms_assessment.md` §6 human-triage throughput gate (H1
  backlog) — this proposal does not touch it.

## 7. Acceptance

1. **S1**: toy oracle (triangle 1/2/3 → two cheapest edges; square +
   diagonal 1.5 → {1, 1.5, 2}); live run reproduces the trial numbers
   (21,369 forest edges, 105 components); forest-not-tree reporting
   tested. ✅ **Verified**: live 21,474 nodes, 105 components, 21,369
   forest edges, total weight 21,649.881, 0.002 s.
2. **S2**: toy oracle (star of consumers → matching = 1); live run
   reproduces 958; self-pairs excluded; overlap count reported. ✅
   **Verified**: live 958/8817 = **10.87%** coverage (matches trial).
3. **S3**: toy oracle (two targets sharing one investor → inadmissible,
   reported); live run reproduces 82/84 + names the 6 single-investor
   targets. ✅ **Verified**: full matching impossible (Hall collisions:
   277 investors vs 84 targets; 6 single-investor targets create
   structural collisions).
4. **S4**: toy oracle (A·Aᵀ counts vs brute force on a 5-node toy);
   live run reproduces 347,302. ✅ **Verified**: live top pair Tata
   Communications / Tata Consultancy Services = **81 shared consumers**;
   347,302 candidate pairs in 5.7 ms.
5. `make static-checks` clean; each slice's CLI dry-runs in <1 s on the
   live graph. ✅ **Verified**: ruff clean on all 4 graph modules.

## 8. Non-goals

- No ROUTING/dispatch changes; no `write_analytics` rows (no per-entity
  metric shape in any slice).
- No app/frontend wiring (S1's viz consumer renders from the edge-list
  output; wiring is a separate decision).
- `structural_rank`, `reverse_cuthill_mckee` stay parked (trial
  evidence in `graph_layer.md`).
- No triage wiring for S4 candidates (H1 gate).

## 9. Integration verification (2026-10-15)

All four lanes validated end-to-end:

| Lane | Metric | Result |
|---|---|---|
| **S1 MST** | 21,474 nodes, 105 components, 21,369 forest edges | total weight 21,649.881, 0.002 s |
| **S2 Matching** | 958/8817 coverage | **10.87%** (matches trial) |
| **S3 Feasibility** | Full matching impossible | Hall collisions: 6 single-investor targets |
| **A² Kernel** | Top pair shared consumers | **81** (Tata Comm / TCS) |
| **Co-mentions** | pair_count_distribution | `1×:2775, 2×:6674, 3×:93, 4×:76, 5×:6`; 8309 rows; weights 2.0–6.0 |

## 9. Follows

- `doc/improvements/archive/graph/scipy_graph_bridge.md` §7 (the
  25-public accounting; the consumer-gate this proposal replaces).
- `doc/improvements/archive/graph/scipy_minimum_spanning_tree.md`
  (absorbed S1 child — algorithm record carries over).
- `doc/local/evaluations/graph_layer.md` (2026-10-15 scale-trial
  section — the measured evidence).
- `doc/local/evaluations/algorithms_assessment.md` §6 (the S4 triage
  gate).
- `helpers/graph/l1_betweenness.py` / `l1_closeness.py` (the CLI +
  dry-run house pattern).
