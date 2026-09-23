---
title: "SciPy graph bridge — BSD-native analytics second lane"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "278"
---

# SciPy graph bridge — BSD-native analytics second lane

**Date:** 2026-09-23 · **Status:** EXECUTED — archived 2026-09-23

## 1. TL;DR

The S1 engine spike for L1 (`/tmp/graph_spike.txt`, PART 3) found that
`scipy.sparse.csgraph.dijkstra(indices=contract, unweighted=True)` —
BSD-licensed, already a declared dependency (`pyproject.toml:93`,
scipy 1.18.1 in `.venv`) — serves both L1a metrics in 2.87 s cold
(4-way fork split; 6.49 s single-process) with parity r=0.9571 /
top-100 overlap 91/100 vs the live stamp, digit-identical to the
license-dead igraph route. A full survey of the installed scipy
surface (`csgraph` 27 publics + `sparse.linalg`) against the engine
record shows the same dependency backfills nearly every *named gap*:
exact one-solve Katz (removes the alpha-pin fragility), exact
personalized PageRank (resurrects the Onager-dropped lane), robust
`eigsh` eigenvector (fixes small-graph convergence failure),
`maximum_flow` (replaces the retired igraph seat), MST (wires the
deferred need), Yen K-shortest (new capability), and sparse A²
link-prediction scores (L1c kernel option).

This proposal promotes that survey into a standing second lane,
`helpers/graph/scipy_bridge.py`, behind the same lazy-import /
dry-run-default posture the igraph pilot used. It does NOT replace
Onager's SQL lanes; it covers what Onager cannot do or does
fragilely. Relation to L1: this proposal OWNS the (d) route — L1 S1
names (d)+parallel as its primary and consumes this lane.

## 2. Measured state (spike evidence, VIGIL scale, this box)

Live: 22,046 endpoint nodes / 57,581 sqlite edges / 115,162 doubled
rows; contract 1,734 company sources, all resolving. Full record in
`/tmp/graph_spike.txt`; query-class map in
`doc/local/perf/perf_graph_scale.md` §F.

| Op | scipy (measured) | Incumbent |
|---|---|---|
| CSR build (115k doubled rows) | 0.03 s | 0.25 s igraph build / 29.6 ms SQL remap (old scale) |
| dijkstra, 1,734 sources (single) | 5.45 s + 1.03 s derive = 6.49 s | 156.8 s Onager leg; 1.61 s igraph (license-dead) |
| dijkstra, 1,734 sources (4-way fork) | **2.87 s wall, both metrics** | — |
| Parity vs `v_centrality_closeness` | r=0.9571, top-100 91/100 | same as igraph run (same unweighted BFS) |

Floors and ceilings established by the spike: the 292 MiB dense
distance matrix is memory-bandwidth-bound (~1 s derive regardless of
fusion); single-process per-source overhead inside scipy dominates
the 5.45 s; fork inherits the CSR read-only (CoW, no copy), result
order preserved by map, transient ~73 MiB per worker. Budget context:
the 5.0 s pre-VIGIL budgets are being re-baselined to the 15x-grown
graph by operator call — under a scale-fair budget even the
single-process 6.49 s passes; the fork split stays as the tight-budget
option. S5 (L1) owns the final numbers; this proposal consumes them.

## 3. Slices

### S1 — L1a carrier: `scipy_bridge.py` dijkstra lane

`load_projection()` (sqlite `graph_edges` → remapped CSR, same
deterministic-id discipline as the retired bridge) +
`closeness_harmonic(source_names)` via one `dijkstra(indices=…,
unweighted=True)` call with fused derive + optional `--jobs 4` fork
split + `persist()` delegating to `write_analytics()` UPSERT only on
`--apply` (dry-run default). Parity harness on a frozen subgraph vs
Onager values;ROUTING entry pinning Onager-default vs scipy lanes.

Acceptance: both L1a metrics inside the re-baselined budget cold in
`make perf` (single-process if the budget allows, fork split if not);
1,734-row contract write-back green; frozen-subgraph parity r ≥ 0.95
with absolute-scale note (normalization/N, as spiked). Parity oracles
are numpy-dense at toy scale (`eigh`/`solve` on the frozen subgraph —
exact references, not production forms; see §6 dense wall).

> Superseded in detail by `scipy_routing_dispatch.md` (2026-09-23):
> live all-edges projection reproduces the served incumbents exactly
> (closeness maxdiff 0.0, harmonic 1.8e-12 — LINEAGE parity, the rows
> are lane-written; fresh Onager on the same generation sits in a
> different universe: ex-index projection + underived ×1.2383 global
> rescale, r=1.0 — see the convention-fork note in
> `scipy_routing_dispatch.md` §2, reconciliation explicitly
> out of scope). S1 acceptance is therefore exact reproduction of
> served values, and ROUTING wiring into `algorithms.py` dispatch is
> specified there (slices S0–S2; `L1B_FOLD` activation explicitly
> future).

### S2 — exact-solve gap-fills (one sparse solve each)

> Promoted to per-algo proposals (2026-09-23; umbrella patch
> `scipy_algos`): `scipy_katz_exact_solve.md`,
> `../archive/graph/scipy_personalized_pagerank.md` (DEFERRED +
> archived, completed.md #272 — trigger: a named consumer),
> `scipy_eigenvector_eigsh.md` — this
> section stays as the survey record; acceptance lives in the children.

- Katz: `spsolve(I − αA, 1)` — exact, no iteration to diverge; retires
  the 1e-4 alpha pin (and its small-graph spread flattening). Parity
  vs pinned-Onager Katz on the live graph + divergence demo at α=0.1
  (Onager fails, solve succeeds).
- Personalized PageRank: `spsolve(I − αP, (1−α)v)` — resurrects the
  lane Onager dropped (restart-hardcoded bug); acceptance is a
  seed-concentration check (personalized mass ≫ uniform on the seed
  neighbourhood), since no incumbent exists to par against.
- Eigenvector: `eigsh(A, k=1)` with explicit `tol` — acceptance is
  convergence on the toy/small graphs where Onager raises
  `Convergence failed after 100 iterations`, plus agreement with
  Onager where Onager converges.

### S3 — resurrected + new lanes

> Promoted to per-algo proposals (2026-09-23; umbrella patch
> `scipy_algos`): `../archive/graph/scipy_maximum_flow.md` (DEFERRED +
> archived, completed.md #273),
> `../archive/graph/scipy_minimum_spanning_tree.md` (DEFERRED +
> archived, completed.md #274),
> `../archive/graph/scipy_yen_k_shortest.md` (DEFERRED + archived,
> completed.md #275).

- `maximum_flow` (Dinic): replaces the retired igraph seat; acceptance
  is the pilot's hand-verified toy (maxflow 6 = mincut) + one live
  s-t pair with cut-edge listing, dry-run only (an s-t pair has no
  per-entity metric shape — same disposition as the pilot).
- `minimum_spanning_tree`: wires the deferred Onager-MST need;
  acceptance is total-weight agreement with a Kruskal reference on
  the toy + live smoke timing.
- `yen` K-shortest: new capability (SQL/Onager cannot); acceptance is
  K-path sanity on the toy (simple paths, nondecreasing cost) — no
  consumer yet, lane is opt-in.

### S4 (conditional) — sparse link-prediction kernel for L1c

> Filed as its own DEFERRED proposal (2026-09-23; archived,
> completed.md #276):
> `../archive/graph/scipy_link_prediction_kernel.md` — the trigger is not met; SQL L1c
> delivered exact at 0.65 s. No code owed while deferred.

A² + degree vectors yield common-neighbors / jaccard / adamic-adar /
preferential-attachment / resource-allocation in sparse C ops; the
2-hop candidate join IS the nonzero pattern of A². Runs ONLY if the
SQL-side L1c pruning stalls — SQL stays primary (no engine change
was ever needed there). Whichever side wins, the numpy mechanics are
fixed: 2-hop joins as `intersect1d` on sorted CSR adjacency (C merge
loops, not Python sets), per-node top-K as `argpartition` (O(n)
partial selection, never full-sort the candidate space), strength /
per-community sums via `bincount`/`add.at`. Same family: L1b's 2-core
fold is `bincount`-degree peeling in ~15 lines of numpy (L1's slice,
noted here so nobody hand-rolls it). Acceptance is L1c's (top-10
stable vs unpruned on a frozen subgraph), not a second one.

## 4. Non-goals

- Betweenness (no Brandes anywhere in scipy — L1b folding +
  Onager-on-core stands; the fold is engine-agnostic and this lane
  can consume the folded core if faster).
- Louvain/Leiden/community detection of any kind (absent from scipy;
  HGX hy-MMSBM owns the overlapping-community seat per D16).
- All-pairs diameter/radius/APL (same RED class in every engine;
  folded-core or sampling only).
- HGX for dyadic lanes (explicitly out: wrong object — hyperedge
  incidence vs the dyadic table; wrong semantics — s-walk ≠ BFS,
  fails exactness by definition; no source surface — see spike
  record). HGX keeps its higher-order lanes; no overlap.
- Replacing Onager's healthy lanes (pagerank, louvain, WCC,
  clustering — all sub-second; uniformity alone is not a reason).

## 5. Risks

- scipy version pin: all numbers are 1.18.1 on this box; pin and
  re-spike on upgrade (sparse API is stable, but `maximum_flow` and
  `yen` are younger).
- Dense-matrix floor: 292 MiB transient for 1,734 sources; scales
  with sources × N — document the ceiling (contract-size is fine;
  all-node would be ~5.5 GiB and is NOT proposed).
- Fork in the gate: 4-way split is processes, not threads (GIL
  doctrine per §9c stands); determinism via map order, asserted in
  tests; single-process stays the default until budgets demand the
  split.
- Unweighted-only parity scope: the lane matches Onager's unweighted
  centralities; weighted variants are NOT claimed (D16's weighted-lane
  clause still governs — a weighted lane revival remains its own
  proposal).
- Division of labor with numpy (the substrate, not a second algorithm
  library): scipy.sparse owns the metric kernels; numpy owns the
  mechanics (fold peeling, candidate joins, top-K, aggregation) and
  the dense test oracles. Dense `linalg` on the full graph is a hard
  wall — 22,046² float64 = 3.9 GiB before O(n³) flops — so dense
  forms live ONLY in frozen-subgraph harnesses (and borderline at
  folded-core scale, ~7k² = 392 MiB).

## 6. Order

S1 with L1 S1 (this lane is L1's (d) route — land together, L1's
parity harness covers both). Then ROUTING dispatch
(`scipy_routing_dispatch.md` — wires this lane into
`algorithms.py`, `L1B_FOLD` activation excluded). Then S2, then S3,
then S4 only on the L1c trigger. Full gates once at the end per
house rule.

## 7. csgraph surface accounting (27 publics, footnotes)

Full survey of `scipy.sparse.csgraph` (1.18.1, 27 publics) against the
engine record — disposition per public, so the child proposals below are
the complete list of NEW work and nothing else is silently owed:

- **Shipped (S1, this proposal)**: `dijkstra` — the L1a carrier lane.
- **Child proposals (S2/S3, patch `scipy_algos`)**: `yen`
  (`../archive/graph/scipy_yen_k_shortest.md`), `maximum_flow` (`../archive/graph/scipy_maximum_flow.md`),
  `minimum_spanning_tree` (`../archive/graph/scipy_minimum_spanning_tree.md`); plus the
  `sparse.linalg` gap-fills `spsolve`/`eigsh`
  (`scipy_katz_exact_solve.md`, `../archive/graph/scipy_personalized_pagerank.md`,
  `scipy_eigenvector_eigsh.md`).
- **Deferred (S4)**: `A^2`-pattern link-prediction kernel
  (`../archive/graph/scipy_link_prediction_kernel.md`; trigger not met).
- **Owned by Onager/SQL — no scipy lane needed**:
  `connected_components` (WCC healthy, sub-second), `laplacian`
  (Onager laplacian centrality exists), `shortest_path` /
  `bellman_ford` / `johnson` / `floyd_warshall` (BFS shortest-path lane
  already SQL/Onager; floyd_warshall rejected at O(n^3) — 24 s on 1.6k
  nodes, recorded in graph_db_optimization Issue 5),
  `construct_dist_matrix` + `reconstruct_path` (helpers for the above
  family), `breadth_first_order` / `breadth_first_tree` /
  `depth_first_order` / `depth_first_tree` (traversals; no named gap —
  Onager/SQL territory).
- **Converters/test scaffolding, not algorithms**:
  `csgraph_from_dense`, `csgraph_from_masked`, `csgraph_masked_from_dense`,
  `csgraph_to_dense`, `csgraph_to_masked`, `test`, `NegativeCycleError`
  (exception class).
- **No consumer recorded** (surveyed, deliberately not sliced — file a
  child proposal first if a need appears): `maximum_bipartite_matching`,
  `min_weight_full_bipartite_matching`, `structural_rank`,
  `reverse_cuthill_mckee`.
