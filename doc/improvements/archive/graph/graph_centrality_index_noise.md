---
title: "Graph centrality index-noise — exclude listed_on_index from the centrality projections"
status: executed
filed: "2026-09-19"
executed: "2026-09-19"
completed_md: "254"
area: "helpers/graph/algorithms.py (centrality wrappers), tests (projection semantics), doc/design/graph_design.md"
---

# Graph centrality index-noise — exclude `listed_on_index` from the centrality projections

**Date:** 2026-09-19 · **Status:** EXECUTED 2026-09-19 ·
completed.md entry 254 ·
**Area:** `helpers/graph/algorithms.py` (nine Onager-backed centrality
wrappers), `tests/test_centrality_projection.py` (new),
`doc/design/graph_design.md` (metric semantics note)

## 1. Motivation (measured 2026-09-19)

`listed_on_index` (#253) added 6,615 edges — **36% of all edges** — as
57 star hubs (top degree: NIFTY TOTAL MARKET 752) and pulled 773
previously-isolated stub companies into the endpoint set (V 1,735 →
2,508). Measurement pass over the live graph:

- **Ranking pollution (API-visible via `graph_analytics` routes,
  `s_betweenness`/`s_closeness`):** with membership edges in the
  projection, NIFTY SME EMERGE ranks betweenness **#2** (0.355) and
  NIFTY TOTAL MARKET ranks closeness **#3** (0.393); betweenness
  ranks #7–#10 are SME smallcaps (TBI Corn, K2 Infragen, Felix
  Industries, Krishca) whose ONLY graph presence is the SME index —
  "brokerage" that is list membership, not mediation.
- **Compute:** closeness 3.04s → 1.33s and betweenness 2.11s → 0.92s
  without the edges (V back to 1,735). Scaling validated as
  O(V·(V+E)) (measured ×2.29 vs theoretical ×2.35); Wave 2 (~90 more
  indices) projects 4.6s flat-V / 6.7s with +500 new smallcaps
  against the 5.0s budget — this change removes that exposure.
- Index membership is the most mechanical hub class in the graph: a
  semi-annual CSV list, no editorial relation between members, nested
  (500 ⊃ 200 ⊃ 100). The remaining hub classes (country `india` via
  `listed_in`, sector nodes via `has_company`, newsletter editions via
  `cited_in`) stay in: they answer "position in the relationship +
  authored-taxonomy graph", which is the metric semantics this change
  pins.

## 2. Proposal

Exclude `listed_on_index` from the DB-backed projection of the ten
Onager-backed structural wrappers in `helpers/graph/algorithms.py`
(degree, closeness, betweenness, eigenvector, harmonic, katz,
laplacian, local_reaching, voterank — the nine centralities — plus
louvain, added by amendment 2026-09-19 on the operator's decision
after the §9.7 measurement):

- `EDGE_TYPES_EXCLUDED_FROM_CENTRALITY = frozenset({"listed_on_index"})`
- `_centrality_edge_types(con)`: `SELECT DISTINCT edge_type FROM
  fin.graph_edges` minus the exclusion, resolved at call time — new
  edge types join the projection automatically (no whitelist drift);
  the resolver falls back to all-edges if `fin` is unreadable.
- Applied ONLY on the `edges=None` DB path. Synthetic `edges=` lists
  and explicit `edge_types` from callers bypass the exclusion
  (test-covered both ways).
- pagerank / wcc / clustering are OUT OF SCOPE for a different
  reason than first drafted: their `query.py` paths are Onager-backed
  but single-edge-type projections (default `BelongsTo` →
  `belongs_to`), so they never included membership edges by default.
  louvain (`louvain_communities` / `compute_louvain_modularity`) is
  the remaining full-graph metric with membership edges in its
  projection — community semantics over membership is a separate
  decision (see §4, §9 item 7 for the measured evidence).

Expected outcome (measured on the live graph): closeness top-10 =
india, the newsletter edition, then large caps (M&M, Voltas, Ashok
Leyland, Varun Beverages, Muthoot, TCS…); betweenness top-10 = india,
the edition, then sector nodes (Technology, Automotive, FMCG,
Banking…) — peer-group bridging through the authored taxonomy.

## 3. Non-goals (evaluated, deliberately not taken)

- **Link prediction over membership edges** — NO. Measured: adding
  `listed_on_index` floods the top pairs with unrelated 1.000 pairs
  (Action Construction Equipment × Bikaji Foods / Balrampur /
  Birlasoft — co-listed in MidSmallcap 400, nothing else in common);
  the default set's top pairs are true peers that ALSO share indices
  (Hyundai|TVS, BoB|PNB, Godrej|Marico, Cummins|Siemens) — membership
  signal is redundant behind co-mentions/competes, not missing.
  Adamic-Adar over the full graph degenerates to index-vs-index pairs
  (score 233) at 9.8s. `DEFAULT_PREDICTION_EDGE_TYPES` stays
  membership-free. If narrow-index signal is ever wanted: degree-capped
  index set as a RERANK pass, not a projection change.
- **Persistent per-generation centrality cache** — DEFERRED with
  trigger; the design is now filed as its own live proposal
  (`graph_centrality_persistent_cache.md` — eager `v_centrality_*`
  stamping at rebuild, mirroring app.py per-generation semantics). Current state: `_QUERY_CACHE` is in-process only; the
  long-lived app already caches per generation; CLIs pay one cold
  compute (1.33s/0.92s after this change — within budget). Trigger to
  revisit: Wave 2 pushing centrality over budget, or centrality
  appearing in hot cross-process API paths (multi-worker serving).
  Design sketch when triggered: stamp centrality into the graph cache
  at rebuild time (`v_centrality_*` tables; +~2.3s per rebuild at
  Cut-B sizes) rather than a sidecar store — keeps the single-writer
  (rebuild) contract and serves readers from the read-only cache.
- **`directed := false` on the Onager calls** — dead end, measured:
  3.17s vs 3.04s closeness, 2.10s vs 2.11s betweenness (noise).

## 4. Deferred decisions

- ~~pagerank/louvain/wcc community semantics~~ — RESOLVED 2026-09-19.
  Correction first: pagerank/wcc/clustering project a single edge type
  (default `BelongsTo`) and never included membership edges. Louvain
  was the one full-graph metric affected; the operator took option A
  (same exclusion) after the §9.7 measurement — louvain now goes
  through the same rule, and nothing remains parked here.

## 5. Verification

- Eval gate: `ontology_eval_gate.py` — centrality scores are not in
  the 164 frozen questions and this change mutates NO database state,
  so the gate passes trivially (run and record anyway, per the house
  rule for query-visible semantic changes).
- New `tests/test_centrality_projection.py`: fixture graph with a
  membership hub — default path excludes it from all nine wrappers;
  synthetic `edges=` bypasses; `_centrality_edge_types` resolves the
  exclusion dynamically.
- Existing: `tests/test_integration_graph_algorithms.py` (synthetic
  paths), `tests/test_api_graph_unit.py` / `test_api_graph_live.py`
  (API consumers), `tests/test_note_embeddings.py`,
  `tests/test_graph_disk.py` (untouched by this change, run as
  regression).
- `make perf`: closeness/betweenness drop to ≈1.6–1.9s CLI wall;
  budgets stay at 5.0s/4.0s (headroom grows; no retightening —
  Wave 2 may still add relationship edges).

Ran 2026-09-19 (in-worktree, pre-archival): eval gate **ACCEPT**
(164 questions, 0 reasons — identical DB, code-only change);
`test_centrality_projection.py` 12/12; graph suites 218/218
(integration + api unit/live + graph_disk); `make perf` 22/22 with
closeness **1.51s** / betweenness **1.17s** / eigenvector 0.38s /
link_prediction 1.35s / pagerank 0.37s / louvain 0.47s. Live top-5s
match §2 exactly (closeness: india, edition, M&M, Voltas, Ashok
Leyland; betweenness: india, edition, Technology, Automotive, FMCG).

## 6. Risks

- **API-visible value changes:** `s_betweenness`/`s_closeness` and the
  analytics routes serve different numbers (the point of the change);
  consumers comparing historical dumps will see a step. Documented
  here as the semantic pin: centrality = "position in the
  relationship + authored-taxonomy graph".
- **Metric-family consistency:** pagerank/louvain still include
  membership edges (§4) — a user comparing across metrics sees
  different vertex universes until those are decided.
- Reconstitution churn no longer perturbs centrality at all (a
  feature, but worth stating).

## 7. References

- `doc/improvements/archive/graph/index_membership_fill.md` (#253) —
  the edge source.
- `doc/improvements/pending.md` — perf graph-leg entry (warm-check
  poison fix; the phantom-rebuild context for this arc).
- Measurement scripts and raw numbers: this proposal §1–§3 (measured
  live 2026-09-19, generation 112773).

## 8. Execution status

Core executed 2026-09-19 in-worktree (nine wrappers + resolver +
12 tests + this proposal + README line). AMENDED same day: louvain +
`compute_louvain_modularity` join the exclusion (operator decision,
option A) — 4 more tests (15 total), eval gate re-run ACCEPT 164/0,
perf 22/22 with louvain 0.49s (cheaper on the smaller projection).
Full gates once, at arc end, per house rule. Archival (status flip,
completed.md number, archive move) awaits the operator's number —
multi-worktree coordinated.

## 9. Findings recap — quick reference for future arcs

Everything measured live (generation 112773, 2026-09-19). Numbers are
the evidence; conclusions are one line each.

1. **The perf "slide" was a phantom rebuild, not corpus growth.** A
   stray empty `memory/graph.db` (4 KB, zero tables) poisoned
   `_is_warm`'s colocated-sibling guess → every graph CLI connect
   unlinked + fully rebuilt the 35 MB cache (~2.07s) before computing.
   Fix: `_is_warm`/`_probe_note_embed_state` take the real `db_path`
   (landed; `test_graph_disk` regression test). Savings: ~2s on EVERY
   graph benchmark; 4 over-budget legs → 22/22 at ORIGINAL budgets.
2. **Link prediction does NOT want membership edges by default.**
   Adding `listed_on_index`: top pairs flood with unrelated 1.000
   co-listings (Action Construction Equipment × Bikaji Foods /
   Balrampur / Birlasoft — shared MidSmallcap 400, nothing else);
   adamic-adar degenerates to index-vs-index pairs (score 233) at
   9.8s. The default set's top pairs are true peers that ALSO share
   indices (Hyundai|TVS, BoB|PNB, Godrej|Marico, Cummins|Siemens) —
   membership signal is redundant behind co-mentions/competes.
   `DEFAULT_PREDICTION_EDGE_TYPES` is load-bearing: full-graph jaccard
   = 5.05s vs 1.34s default. If narrow-index signal is ever wanted:
   degree-capped index set as a RERANK pass, never a projection change.
3. **Index hubs pollute centrality rankings (this proposal's core).**
   With membership edges: NIFTY SME EMERGE betweenness #2 (0.355),
   TOTAL MARKET closeness #3, SME smallcaps #7–#10 whose only graph
   presence is the index. After exclusion: large caps (closeness) and
   sector nodes (betweenness) — see §2.
4. **Centrality compute scales as O(V·(V+E)); V is the lever.** The
   6,615 idx edges added 773 new endpoint vertices (+44%): measured
   ×2.29 vs theoretical ×2.35. Per-1k-edges figures mislead — new
   ISOLATED companies entering the endpoint set cost more than edges.
   Wave-2 projection: 4.6s flat-V (survives) / 6.7s with +500 new
   smallcaps (over) — moot after this change (relationship-only V).
5. **`directed := false` on Onager centralities is a dead end.**
   3.17s vs 3.04s closeness, 2.10s vs 2.11s betweenness — noise; the
   functions symmetrise regardless. Do not revisit.
6. **Persistent per-generation centrality cache: deferred, trigger-
   defined.** `_QUERY_CACHE` is in-process only; the long-lived app
   already caches per generation; CLIs pay one cold compute
   (1.33s/0.92s relationship-only — within budget). Build it ONLY if
   Wave 2 pushes centrality over budget or centrality lands in hot
   cross-process API paths. Design when triggered: stamp centrality
   into the graph cache at rebuild (`v_centrality_*`; +~2.3s per
   rebuild), not a sidecar — preserves the single-writer contract.
7. **Louvain is the one remaining full-graph metric that includes
   membership edges** (CORRECTED 2026-09-19: pagerank/wcc/clustering
   were initially listed here in error — their `query.py` paths are
   Onager-backed but project a SINGLE edge type, default
   `BelongsTo`→`belongs_to`, and never included membership edges by
   default; they are a different API shape, "metric over one edge
   label", company-vertex-filtered). Measured for louvain (0.04s —
   no perf angle, purely semantic): with `listed_on_index`, 18
   communities and the largest (495 members) IS the NIFTY SME EMERGE
   roster collapsed into one community (Aakaar Medical, Aatmaj
   Healthcare, Abha Power — SME list-mates with no relations); the
   community partition mirrors the index list, not relationships.
   Without: 21 communities, largest 253, sample members Qualys /
   Crocs / Figma / Salesforce (relationship-driven). Consumers:
   `_LABEL_GRAPH_METRICS` serves `louvain_community` labels per
   entity via the API. Deferred decision: apply the same exclusion
   (communities become relationship+taxonomy driven, stable across
   reconstitutions — the SME cluster remains first-class via the
   `listed_on_index` edges themselves, so no information is lost,
   just not duplicated in a second metric) or keep louvain
   full-graph and document the difference. Churn argument for
   excluding: every reconstitution relabels communities. **DECIDED
   2026-09-19 (operator): option A** — "SME index is not a hub for
   anything"; louvain routes through the same exclusion. Live check
   after: 20–21 relationship-driven communities, largest ≈221–253,
   no index hub in any of them.
8. **`graph_metrics` keeps full-edge semantics by design** (density /
   diameter over the complete graph is a different question than
   brokerage); its docstring pins this — do not "fix" it to match §2.

**Follows:** none. **Precedes:** none yet.
