---
title: "Graph perf L1 — BFS-family centrality and link prediction at VIGIL scale"
status: proposed
filed: "2026-09-23"
area: "helpers/graph"
executed:
completed_md:
---

# Graph perf L1 — BFS-family centrality and link prediction at VIGIL scale

## 1. TL;DR

The VIGIL intake tripled the graph (18,291 → 56,014 edges, 26,120
nodes; perf_investigations §10d). Layer 0 already removed the rebuild
cost (centrality_rebuild_contract #271: 369.9 s → 3.19 s data-only,
explicit stamp lane, warm reads 0.017 s). The first `make perf` run at
the new scale (2026-09-23 00:52) shows the remaining debt: the three
cold `--compute` centrality/link legs and nothing else broke their
pre-VIGIL budgets — 31x, 28x and 12x over. Everything non-BFS scaled
fine (pagerank 0.54 s, louvain 0.78 s, eigenvector 0.50 s, rebuild
2.54 s, shortest-path inner asserts 12 ms / 76 ms).

This proposal executes the three algorithmic kills (L1a/L1b/L1c), the
snapshot-manifest fix their interplay with the Layer-0 contract now
needs, and records the operator's deliberate-red budget stance as the
tracking table to revisit at implementation start.

## 2. Measured state (the table to revisit)

`make perf` 2026-09-23 00:52 (run 1, budgets pre-VIGIL) and 2026-09-23
01:0x (run 2, after this proposal's interim re-pins):

| leg | run 1 | run 2 | budget | verdict |
|---|---|---|---|---|
| graph_closeness | 156.8 s | 128.8 s | 5.0 | RED by operator call |
| graph_link_prediction | 57.0 s | 52.9 s | 2.0 | RED by operator call |
| graph_betweenness | 48.9 s | 37.2 s | 4.0 | RED by operator call |
| integrity_check | 4.7 s | 4.4 s | 10.0 | re-pinned, approved |
| shortest_path_bfs | 8.2 s | 8.3 s | 10.0 | re-pinned, approved |
| graph_rebuild | 2.7 s | 2.5 s | 8.0 | OK (Layer-0 win) |
| graph_pagerank | 0.6 s | 0.5 s | 3.0 | OK |
| graph_louvain | 1.0 s | 0.8 s | 4.0 | OK |
| graph_eigenvector | 0.6 s | 0.5 s | 2.0 | OK |
| route_graph_stats | 3.2 s | 3.1 s | 5.0 | OK |

Operator decision 2026-09-23 (recorded in `run_perf_benchmarks.py`
comments): closeness / link_prediction / betweenness budgets are
DELIBERATELY left at their pre-VIGIL values — minutes-scale cold
compute is not acceptable and the red legs are the tracker for this
proposal. integrity_check and shortest_path_bfs got approved headroom
(their inner assertions pass; only legitimate db growth and post-BFS
harness pressure moved them). REVISIT THIS TABLE when S1-S3 land:
re-tighten the three red budgets to ~5 s class and re-decide lane
placement (S5).

REVISITED 2026-09-23 (S5, below): all three red legs retired — the
scipy closeness lane (S1, pending `scipy_improvs` apply), the folded
betweenness lane (S2) and the SQL 2-hop link-prediction join (S3)
measured 2.5-5.3 s end-to-end; the revisited table now lives as the
header comment on `BENCHMARKS` in `tests/run_perf_benchmarks.py`
with per-leg scaling classes.

## 3. Why only the BFS family broke

Cold closeness/harmonic/betweenness are all-pairs-shortest-path
derived: O(V*(V+E)) per metric. The 2026-09-22 stage profile
(perf_graph_scale §A/§B) is unchanged in shape — harmonic 192.1 s,
closeness 183.4 s, betweenness 87.7 s of the old ~465 s stamp; tonight
the same three are 128.8-156.8 s (closeness), 52.9-57.0 s
(link_prediction), 37.2-48.9 s (betweenness) standalone because the
stamp lane and live-compute fallback share the code path. The leverage note
from §B still stands and is the core of L1a: `graph_analytics`
persists only **1,734 company rows** for these centralities, but the
compute walks all **26,120 nodes** as sources, including ~19K degree-1
VIGIL subsidiary leaves. Restricting sources to the persisted contract
is exact (distances from a source do not depend on who else is a
source), not an approximation. Surface RESOLVED (2026-09-23, S1
implementation): the contract is ALIVE in SQLite —
`research.db.graph_analytics` (1,734 closeness rows) — and S1 writes
back THERE (write_analytics UPSERT, --apply); the source set is one
SELECT away, no reconstruction. The DuckDB cache-side stamp tables
`v_centrality_*` (21,453 scored nodes, 20,830 companies) keep
full-walk semantics and are untouched by S1. The 15x source saving
holds for any consumer that needs only the company contract — BFS cost
is source-driven, not output-driven.

link_prediction (jaccard over the co_mention / jv_with / competes_with
/ same_group projection) is pair-space, not BFS: the candidate space
exploded with the VIGIL-widened symmetric types (competes_with 3,578 +
jv_with 1,070 + co_mention growth; e_jv 73 -> 1,070 in the tracked
snapshot, snapshots/parquet/duckdb/e_jv.parquet).

Engine context (graph_layer.md + graph_design.md §10 decisions): D14
hybrid adopt stands — Onager default for the 10 production lanes;
EasyGraph eliminated (C++ re-test). D16 (2026-09-13) then RETIRED and
deleted the igraph pilot (`igraph_bridge.py` + tests, commit acbba048)
— the second-engine seat passed to the HGX hypergraph lane — so S1(b)
revival means restoring the bridge from git history (commit 720e38ff).
D16's "reviving a dyadic weighted lane needs its own proposal" clause
is not triggered here: L1a/L1b are unweighted (the clause governed the
weighted lanes). Onager has no source-restricted centrality surface;
igraph does (`g.closeness(vertices=...)`, `g.distances(source=...)` —
the python-igraph kwarg is `vertices=`, not `vs=`; the graph_layer.md
handover documents only `weights=` usage).

## 4. Slices

### S1 — L1a: company-source-restricted closeness + harmonic (exact)

Compute the BFS family from the 1,734 persisted-contract sources
instead of all 26,120 nodes (~15x less source work; the leaves stay as
INTERMEDIATE nodes, so scores remain exact for the company contract).

Route DECIDED 2026-09-23 — all candidates spiked on the live db
(/tmp/graph_spike.txt); winner: **(d) scipy multi-source dijkstra**,
owned by the companion proposal scipy_graph_bridge.md and shipped as
`helpers/graph/scipy_bridge.py`:

- (d) PRIMARY — `scipy.sparse.csgraph.dijkstra(indices=contract,
  unweighted=True)` over the e_all_und CSR: one call, one distance
  matrix, both metrics fused from it. 2.9 s wall 4-way (6.5 s
  single-process) for BOTH metrics; parity vs the live stamp r=0.9571,
  top-100 overlap 91/100 — digit-identical to the igraph spike (same
  unweighted BFS, same endpoint set). BSD, scipy already in .venv via
  hyper_centralities: zero new deps, zero revival politics, zero
  37.6M temp rows.
- (b) igraph revival: spike-validated (closeness 1.61 s, harmonic 3.86
  s, +0.25 s build) but SHELVED on posture — it needs the pilot-venv
  split the scipy lane makes unnecessary (license-dead per the route
  table). Bridge restored-then-dropped; scipy_bridge keeps its
  semantics (sorted-name deterministic ids, SQLite graph_edges as sole
  source of truth). D16 context: the bridge was retired/deleted
  (commit acbba048); revival meant git-history restore (commit
  720e38ff); the weighted-lane clause of D16 was never triggered
  (L1a/L1b are unweighted).
- (a) DuckDB lockstep walk: 12.4 s over 5 s budget — fallback harness
  only (spike_a2.py; also proved TEMP tables work on read-only
  handles).
- (c) CSR+Mojo: unbuilt, last-ditch — the remaining gap after (d) is
  single-digit seconds.

Acceptance: one combined `graph_l1_centrality` leg in `make perf`
measures BOTH metrics from the single dijkstra pass — < 5 s cold
(measured 2.6-2.9 s, --jobs 4). Persisted row contract: the 1,734
company rows in `graph_analytics` are THE surface (§3 surface note) —
`--apply` UPSERTs closeness_centrality + harmonic_centrality there
(done 2026-09-23: 1,734 + 1,734 rows, 2.7 s); the v_centrality_* stamp
keeps full-walk semantics and is untouched. Stamp lane re-measurement
(was 5m59s; closeness+harmonic were ~375 s of it) moves to S5 with the
lane-policy call. Parity: r=0.9571 / top-100 91-of-100 vs the live
stamp, recorded at the spike; unit parity is pinned by
tests/test_scipy_bridge.py (10 tests, in-repo).

### S2 — L1b: betweenness 2-core folding

Brandes on the 2-core: fold the ~19K degree-1 leaves (their
contribution routes through their attachment point), run betweenness
on the residual core (~7K nodes ≈ (7/26)^2 ≈ 7% of the work), add the
leaf terms back analytically. Engine-agnostic; same route choice as
S1 applies if (b) wins.

Acceptance: cold betweenness < 5 s or a documented residual with the
re-tightened budget set from the measurement; parity harness as S1.

### S3 — L1c: link-prediction candidate pruning — DELIVERED 2026-09-23

Replace the all-pairs scoring with a 2-hop candidate join: candidates
must share a neighbor; degree caps on the hub side (the VIGIL group
hubs create quadratic star joins); top-K early exit. SQL-side in the
Onager projection — no engine change.

Acceptance: jaccard `--top 10 --no-apply` < 5 s; top-10 list stable
vs the unpruned run on a frozen subgraph (same pairs, same order).

Delivered (same-day, full graph — stronger than the frozen-subgraph
clause): the four shared-neighbour methods (jaccard / adamic-adar /
common-neighbors / resource-alloc) score in SQL over the materialised
projection; the 2-hop candidate set is EXACT for them (score > 0 iff a
shared neighbour exists), so pruning costs nothing but the all-pairs
scan: ~485M pair evaluations -> 633k candidate slots. Hub-side degree
cap 512 (no-op at live max degree 304; bounds the star join at future
scale) and per-lo top-K early exit (exact for the final LIMIT).
61 s -> 0.65 s in-process, 2.5-2.7 s end-to-end; top-10 bit-identical
vs the unpruned run. Two incidental fixes: a correlated NOT-EXISTS with
an OR that DuckDB cannot decorrelate (nested-loop, 4.9 s of the first
draft) became a canonical-pair anti-join, and zero-attachment forest
comps now get their closed-form scores in the S2 lane. pref-attach has
no shared-neighbour requirement, so it stays on the all-pairs extension
(documented; not perf-gated). Gate: 61/61 onager capability tests.
Leg `graph_link_prediction` GREEN at budget 4.0 (measured 2.5).

### S4 — snapshot manifest: centrality tables are ephemeral

Layer 0 made `v_centrality_*` presence optional by design (rebuild
drops them; readers fall back to live compute). The snapshot manifest
records them as required, so ANY rebuild (including the perf gate's
own graph_rebuild leg, 2026-09-23 run) invalidates the manifest and
`snapshot_check` fails with `v_centrality_*: 21453/absent` until the
next `make snapshot`. Same class: `_build_meta` 8/7 (the stamp writes
a meta row the manifest does not expect).

Fix: mark the ten centrality tables (and the stamp's `_build_meta`
row) as ephemeral in the manifest — absent OR present both verify;
when present, counts must match. No snapshot cadence change needed.

Acceptance: `snapshot_check --check` green immediately after a plain
`rebuild` (no stamp) and after a stamp, against the same snapshot.

### S5 — gate re-tightening + lane policy — DELIVERED 2026-09-23

Decision (recorded in `run_perf_benchmarks.py`, S5 header table):

1. **No perf-cold split.** The ~10-minute wall was the three all-pairs
   legs; S1-S3 replaced them with cheap lanes (2.5-5.3 s each), so the
   default gate is again production-representative. The only
   minutes-scale job left — the `v_centrality_*` stamp — was already an
   explicit `make stamp-centrality` target and stays there.
2. **Budgets re-pinned from fresh measurements, with the scaling class
   each budget guards** (full table in the harness header):
   l1_centrality 5.0, l1_betweenness 8.0, link_prediction 4.0,
   graph_rebuild 8.0->5.0 (was a bare ceiling), snapshot_check
   4.0->6.0 (S4 added per-table zstd verification — legitimate
   assurance growth), integrity/shortest-path headrooms unchanged
   (operator-approved).
3. **What the budgets catch:** super-linear decay shows as a leg
   blowing past budget at constant corpus — 2-hop slots grow ~E x d,
   Brandes ~core^2, dijkstra ~sources x E; none track total node count
   unless the CORE grows, which is exactly what the S2 fold contains.

Gate state after S5: `make perf` 22/23 — the single red is
`graph_l1_centrality`, which crashes on import until `scipy_improvs`
applies (its file lives in that unapplied patch); measured 2.6-2.9 s
when the file was in-tree.

## 5. Non-goals

- integrity_check optimization (budget approved at 10.0 s; batching
  slice only if it creeps).
- shortest_path BFS (healthy: 12 ms / 76 ms inner asserts at scale).
- Engine swap / igraph lane promotion beyond what S1 chooses (D14/D15
  govern; any wider promotion is its own proposal).
- Sampling / approximate centralities (rejected in the centrality
  arc — the persisted contract is exact).
- Layout `_MAX_NODES` cloud-endpoint ceiling (perf_graph_scale §2a,
  separate track).

## 6. Risks

- S1 route (b) revives a GPL dependency into a production lane — the
  licence is cleared (D14) but the pilot-venv posture needs an
  explicit operator call if chosen.
- The 1,734-source set is the `graph_analytics` contract (live in
  SQLite, §3 surface note); if the contract ever widens (e.g.
  institution rows), S1's source list is a parameter, not a hardcode —
  keep it derived from the write side (scipy_bridge reads it per
  run).
- 2-core folding (S2) has exactness edge cases on leaf CHAINS
  (degree-2 paths); the fold must iteratively peel, not single-pass —
  parity harness gates this.

## 7. Order

S4 first (small, unblocks clean perf/qa runs), then S1 (the headline),
then S2, then S3, then S5. Full gates once at the end per house rule.
## 8. S2 implementation log — normalization verdict + fold defect (2026-09-23)

### Normalization verdict (settled)

The lane's raw terms are unordered Brandes counts
(`raw = U/2 + TU + KK + tree_scores`: the unit accumulator U counts each
core-core pair twice — s and w both run as sources — while TU/KK/tree count
once), normalized by `(n-1)(n-2)` with **n = 21,453** = endpoints of the
ex-index projection (index-only isolates have no path and are not in
Onager's node set). Calibrated exactly on the tree-node segment:
onager/mine = 1/460,166,852 = 2.173e-9 with p10 = p90 tight. The rejected
variants miss the same measurement: `(n-1)(n-2)/2` by exactly 2x (the
networkx-formula reading applies /2 to an already-ordered raw), and
n = 22,046 by 5.3% (wrong node universe).

### Fold defect — FOUND AND FIXED (2026-09-23, same day)

An 11-node toy (4-cycle core, one-leaf trees) against brute-force Brandes
on the unfolded graph reproduced the live anomaly in isolation: core nodes
56/68/56/90 vs truth 15/19/15/26; leaves exact. Two root causes, both fixed:

1. **Non-disjoint coverage.** `brandes_core` ran core-only *sources* over
   the *full adjacency*, so the unit accumulator U also counted core->leaf
   pairs as targets — on top of the attachment credit and TU (a triple
   count of {core, leaf} pairs). Fix: BFS restricted to the core-induced
   graph; coverage is now disjoint by construction — U owns core-core,
   TU (with the target-side self term k_v) owns core<->tree — the target
   self term IS the old attachment credit, which is therefore dropped —
   and KK/2 owns tree<->tree across attachments (source-side self term
   k_s*sum(k_a) lands on v=s in the chunk).
2. **Source-self pollution.** Standard Brandes never accumulates delta_s(s);
   the wholesale `U += du` / `TU += dk` added it (delta_s(s) counts target
   continuations, not credit). Fix: zero the source entry before the adds.

Also closed: zero-attachment forest components (19 live) were silently
scored 0 — the closed form now runs per tree with the forest's own size as
the world.

### Verification

- Toy (16 nodes: core cycle, leaf trees, a multi-node tree, a special
  forest comp): EXACT on all nodes vs brute force.
- 30/30 seeded random graphs: exact vs brute force. Permanent gate:
  `tests/test_l1_betweenness.py` (4 tests).
- Live: fresh Onager walk vs the lane, ratio **flat 1.000000** (r = 1.0,
  n = 1,382 scored nodes, max 0.4380 = the historical incumbent max).

### Normalization (final)

The unordered raw is rescaled networkx-style: `raw * 2/((n-1)(n-2))` with
n = **21,453** = endpoints of the ex-index projection (index-only isolates
have no path and are absent from Onager's node set). The original code's
divisor SHAPE `(n-1)(n-2)/2` was correct — its real defects were the node
universe (22,046, +593 isolates) and the ordered, non-disjoint raw. The
`v_centrality_*` stamp serves HALF the contract scale (its own lane
convention); calibration against the stamp therefore reads 1/d where the
contract reads 2/d — recorded here because it caused a long misread.

### Status

1,734 `graph_analytics.betweenness_centrality` rows re-applied 2026-09-23
and now contract-exact. `graph_l1_betweenness` perf leg: 3.76 s measured
vs 8.0 s budget. Remaining scale-fair work is L1c (link-prediction
candidate pruning). Full measurement log + contra rebuttal:
/tmp/graph_divisor.txt (scratch — the durable facts are this section).
