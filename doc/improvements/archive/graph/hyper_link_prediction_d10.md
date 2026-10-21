---
title: "D10 — hyper-native link prediction + the HGX unlock shelf"
status: executed
filed: "2026-10-21"
executed: "2026-10-22"
completed_md: "429"
area: "helpers/graph"
---

# D10 — hyper-native link prediction + the HGX unlock shelf

**Date:** 2026-10-21 · **Status:** EXECUTED (2026-10-22, completed.md #429) — S1 lane shipped (`helpers/graph/hyper_predict.py`), S2 eval bank (`helpers/bench/hyper_predict_bank.py`), S3 pending.md truth pass.

## 1. TL;DR

The 2026-10-19 restatement (`pending.md` "HGX D10", from
`hyperedge_density_dq.md` #420) removed density as the D10 blocker for
five families — edition/industry/sub_sector/sector/theme all clear
k≥3 ≥ 60% of family edges — leaving the recorded blocker as "the
engine decision (hgx-native predictor vs D15-class)". This proposal
makes that decision. Verified against the installed **hypergraphx
1.8.0**: HGX ships **no prediction module** (no `hypergraphx.prediction`),
so "hgx-native predictor" means a first-party lane built on HGX
primitives — incidence-neighborhood jaccard (`measures/edge_similarity`),
s-walk connectance (`measures/s_centralities` paths), and stationary-π
proximity (`dynamics/randwalk`) — reading the star store through the
proven Arrow bridge (`hyper_arrow.hypergraph_from_arrow`). The lane is
report-first with opt-in `--apply` (D13 shape); UI/API promotion stays
D15-consumer-gated. An eval bank (temporal split on incidence
`valid_from` + retrospective holdout, recall@k floors) gates every
promotion claim. The same arc records the verified-but-unwired HGX
algorithm shelf (motifs, multi-order Laplacian, s-walk shortest paths,
RW density, three further community algorithms, reducibility) in
`pending.md` with per-item reopen triggers, so the "what next in
higher-order" answer stops being re-derived per session.

## 2. Evidence

**Live density baseline** (post-prune, `pending.md` restatement
2026-10-19; 1,708 hyperedges / 10,134 incidences / mean 5.93;
k≥3 = 478 = 28.0%):

| family | edges | k=1 | k=2 | k≥3 |
|---|---|---|---|---|
| event | 1,133 | 0 | 1,133 | **0** |
| edition | 222 | 1 | 0 | 221 |
| industry | 119 | 22 | 12 | 85 |
| sub_sector | 110 | 17 | 12 | 79 |
| sector | 42 | 0 | 1 | 41 |
| org_membership | 34 | 4 | 1 | 29 |
| country | 21 | 11 | 3 | 7 |
| theme | 12 | 0 | 0 | 12 |
| group | 9 | 0 | 5 | 4 |
| jv | 6 | 0 | 6 | 0 |

Prediction-grade now: edition, theme, sector, industry, sub_sector,
org_membership. Structurally gated: event (k=2 until participant data
arrives upstream — speaker→event joins measured 0/1,477) and jv.

**HGX 1.8.0 inventory** (import-verified 2026-10-21 against
`.venv/lib/python3.14/site-packages/hypergraphx`): top-level modules
are `communities, core, dynamics, exceptions, filters, generation,
linalg, measures, motifs, readwrite, representations, utils, viz` —
**no `prediction`**. Verified primitives:

| module | entry points (verified) | relevance |
|---|---|---|
| `measures/edge_similarity` | `jaccard_similarity`, `jaccard_distance`, `intersection` | closest thing to a predictor primitive: shared-hyperedge membership similarity |
| `motifs` | `compute_motifs`, `compute_directed_motifs`, `annotations` | hyper-native motif census (the D10 motif half, without the projection) |
| `measures/shortest_paths` | `calc_HO_shortest_paths`, `calc_prop_true_dyad_paths_per_spl`, `calc_sizes_redundancies_of_shortest_paths` | s-walk paths + the diagnostic: how much of a "hyperpath" is really dyadic |
| `dynamics/randwalk` | `transition_matrix`, `random_walk`, `RW_stationary_state`, `random_walk_density` | stationary-π already landed as `ho_pagerank` (`hyper_centralities.py:57`); the rest unwired |
| `linalg` | `laplacian_matrices_all_orders`, `compute_multiorder_laplacian`, `dual_random_walk_adjacency`, `are_commuting` | one Laplacian per hyperedge size; `are_commuting` says which orders combine (assessment §9.2: "the interesting part") |
| `communities` | `core_periphery`, `hyperlink_comm`, `hypergraph_mt`, `hy_sc` (unwired; `hy_mmsbm` landed) | three complementary community views beyond overlapping blocks |
| `measures/reducibility` | `layer_reducibility` | quantitative version of the s-walk caveat: how much higher-order structure reduces to dyads |
| `dynamics` | `contagion`, `synch` | listed for completeness; no current consumer |

**Infra already in place**: `hyper_arrow.load_incidence_arrow` /
`hypergraph_from_arrow` (bridge proven by `hyper_communities`),
`make hif-export` + snapshot rider (D2), the h_* SQL cache (#241),
the D13 opt-in-apply convention (`graph_design.md` §5.2 — metric JSON
`{"method","edge_types","candidates":[{name,score}...]}`, recompute
replaces wholesale, dry-run default), and the s-walk caveat (projections
flatten hyperedges to cliques — the 41-member country edge acting as a
41-clique, `hgx_first_scaling.md` §5).

## 3. Engine decision

**(a) First-party HGX-primitive lane — RECOMMENDED.** A
`helpers/graph/hyper_predict.py` in the house lane shape. Reasons: the
density gate is satisfied per-family; the Arrow bridge is proven; the
lane shape has precedent (`hyper_communities.py` — CLI + opt-in apply
+ `graph_analytics` rows); and arc #420's capture work was executed
precisely to make this lane possible. The D15 discipline is *not*
violated: it gates prod-route promotion (app.py / API / Onager dispatch
handover) on a real consumer, not the existence of an offline lane.

**(b) D15-class blanket deferral — rejected.** It would leave #420's
payoff unrealized and force the same decision to be re-derived every
session. The discipline is preserved by scoping instead: lane = CLI +
opt-in apply; UI/API surface untouched.

**(c) External predictor stacks (HGNN/HyperGAT, torch frameworks) —
closed** by standing verdicts (CPU-only prod rule; the EasyGraph
forced-1.3-GB-torch lesson; assessment §9.2).

## 4. The unlock shelf (recorded, trigger-gated — not executed here)

Each row lands in `pending.md` with its trigger (S3); none is wired by
this arc. Verified entry points in §2.

| capability | question it answers | reopen trigger |
|---|---|---|
| Hyper-native motifs (`compute_motifs`) | higher-order motif census on the prediction-grade families | after S2 bank floors — motif counts are meaningful only where k≥3 mass exists; event family stays gated (k=2 structural) |
| Multi-order Laplacian (`compute_multiorder_laplacian`, `are_commuting`) | which hyperedge-size orders carry separable signal; per-order spectral embeddings | a consumer needing spectral features (e.g. entity-clustering QA) |
| s-walk shortest paths (`calc_HO_shortest_paths`) + `calc_prop_true_dyad_paths_per_spl` | when the dyadic path lane suffices; path redundancy | first UI/API path-explanation ask that traverses hyperedges. Directed B/F-hyperpath (Gallo-style) is **not in HGX** — the directed shortest-path lane stays plain-SQL BFS (`graph_design.md` D9) |
| Higher-order RW density (`random_walk_density`) | diffusion/reachability beyond stationary π (e.g. edition/news spread); the table's upgrade path for the pagerank/voterank importance lanes (`ho_pagerank` already landed) | an analysis question about information diffusion |
| Community lanes 2–4 (`core_periphery`, `hyperlink_comm`, `hypergraph_mt`, `hy_sc`) | core-periphery structure vs hy-MMSBM's overlapping blocks; cross-algorithm consensus | hy-MMSBM block-disagreement QA, or a second opinion for metric validation |
| Reducibility (`layer_reducibility`) | quantitative s-walk caveat per family | same density-sensitivity as motifs |

Dyadic counterpart for the same shelf-slot: the D17 igraph seat serves
Leiden, weighted centralities/shortest paths, maxflow/mincut, and
isomorphism/motifs — each still needs a named consumer before prod
wiring (`graph_design.md:447`).

## 5. Slices

- **S1 `hyper_predict.py`** — three score lanes over the star store,
  per prediction-grade family (edition/theme/sector/industry/
  sub_sector/org_membership), **star-native only** (no clique
  projection): (i) incidence-neighborhood jaccard (shared-hyperedge
  membership, `edge_similarity` semantics); (ii) s-walk connectance at
  s∈{2,3} (pairs reachable through ≥s shared hyperedges; cap s≤3 —
  s-walk cost grows fast, and the cap is documented); (iii)
  stationary-π proximity (`RW_stationary_state` / dual-random-walk
  adjacency product; determinism seed pinned like the eigen trio,
  seed=42). Output: ranked candidate pairs per family, report-first;
  `--apply` (D13 opt-in) writes per-node metric `hyper_link_prediction`
  in the link_prediction JSON shape; recompute replaces wholesale;
  `--top N` caps output; dry-run default.
- **S2 Eval bank** — `helpers/bench/hyper_predict_bank.py`, two legs:
  (1) temporal split — train on incidences with `valid_from < D`,
  score at D, recall@k against post-D-formed hyperedges (the store's
  validity windows make this a real forward test); (2) retrospective
  holdout — mask a fraction of k≥3-family edges, rank, recall@k.
  Floors recorded in the bank header; one-command re-measure (house
  bank doctrine).
- **S3 Truth pass** — `pending.md` "HGX D10" entry updated to point at
  the landed lane + bank verdict; unlock-shelf rows added with
  triggers; `hgx_first_scaling.md` §5 annotated with a pointer (the
  #420 S3 house pattern). No archived file is edited beyond the
  pointer annotation.

## 6. Acceptance

- **Eval-gate bullet** — `hyper_link_prediction` rows are
  query-visible `graph_analytics` values (new metric): bank floors (S2)
  must pass on live before any `--apply` run is recorded, and
  `helpers/misc/ontology_eval_gate.py` runs over the frozen question
  set between dry-run and canonical apply.
- Hyper parity pins hold: `h_edge`/`h_incidence` row counts == star
  store on every rebuild path (#241 contract).
- Zero-churn: the predictor writes only its own metric rows;
  `make recompute-hyper` output set for existing metrics unchanged
  (`computed_at` column untouched).
- Per-family reports carry family label + member-count stats so the
  event/jv gating is visible without re-measuring.
- No UI/API surface changes (D15): no app.py roster, no template, no
  graph.ts edits.

## 7. Risks & non-goals

- **Star-native only** — no clique-projection predictors (the
  41-clique country artifact; `hgx_first_scaling.md` §5).
- **No event/jv lanes** — structural gate: event k≥3 = 0 of 1,133
  until participants arrive upstream (speakers 0/1,477);
  per-family reopen only, never globally.
- **`country` stays opt-in** as a source (2026-09-15 re-baseline
  artifact: the usa edge made SpaceX #1 s_betweenness).
- **D4 h_* materialisation stays deferred** — the predictor reads via
  helpers (`hyper_arrow`), not SQL-over-incidence; the trigger stays
  unarmed (`hyper_lane_wiring.md` §5).
- No HGNN/HyperGAT/torch; no KaHyPar (assessment §9.2 verdicts).
- No UI/API promotion; no Onager/igraph route changes (D15 discipline;
  D17 seat untouched).
- `--redecide` re-grading of historical `acquired` rows stays a
  separate cleanup (merger arc §6 non-goal).
- s-walk lanes are exponential-in-s in the worst case — the s≤3 cap is
  part of the contract, not a tunable.

## 8. References

- `pending.md` "HGX D10" entry (2026-10-19 restatement + per-family
  table + one-query re-measure)
- `../archive/graph/hyperedge_density_dq.md` (#420),
  `../archive/graph/hgx_first_scaling.md` §5,
  `../archive/graph/hypergraph_incidence_hyx.md`,
  `../archive/graph/hyper_lane_wiring.md` §5 (D-ledger)
- `doc/local/evaluations/hyper_graph_assessment.md` §9.1–9.2
- `doc/design/graph_design.md` D13/D15/D17 rows + §5.2
- completed.md #233 (star store), #239 (lane wiring), #241 (h_* cache),
  #420 (density DQ)
