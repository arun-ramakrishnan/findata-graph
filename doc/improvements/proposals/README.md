# Live proposals

Home for proposals awaiting execution (first occupant: the doc-search
proposal, archived 2026-08-23). House rule (2026-08-21): file a proposal
here BEFORE implementing multi-slice work. Acceptance criteria for any
change that alters query-visible semantics (rosters, crosswalks,
hierarchies, extractor rules) MUST carry an eval-gate bullet —
`helpers/misc/ontology_eval_gate.py` over the frozen question set
between dry-run and canonical apply (ontology_governance S2). On Status
EXECUTED, move it to
`../archive/<topic>/` and add the `../completed.md` entry in the same
change — and update any `embed_eval_questions.json` labels referencing
the old path (see `doc/procedures/search.md` §Corpus lifecycle;
reference-rot sweeps: `doc/procedures/doc-hygiene.md`).

Full archival checklist (extended 2026-08-26 after finding a duplicate
entry number and stale DONE pointers):

1. `git mv` to `../archive/<topic>/`; repoint any `**Follows**:` /
   cross-references at the old `proposals/` path.
2. `../completed.md`: entry exists and number is UNIQUE (audit
   `rg '^## \d+\.'` for duplicates — parallel sessions can mint the same
   number; suffix the un-referenced one, e.g. `105b`, never renumber).
3. `../pending.md`: grep the topic; close/annotate deferred items the
   work completes.
4. `../archive/README.md`: add the topic-index line with the completed.md
   number; reset the live-proposal pointer below to `_(none)_`.
5. `make search-fresh APPLY=1`, then plain `make search-fresh` (rc=0)
   to converge the doc index.
6. Flip the frontmatter block in the same change: `status: executed`
   + the executed date + the completed.md number — the Proposal
   lifecycle static check fails an archived proposal still saying
   `proposed` (corpus_uniformity S3).

## Current live proposals

- **wikidata_qid_crosswalk.md** — filed 2026-09-25. Wikidata QID
  crosswalk: politeness-cached fetch lane → sidecar parquet (mca_cin
  pattern) → `concept_mappings` SKOS rows (no schema change) → E3
  same-QID suppression constraining semantic_peer; retires the stale
  bge label on the live granite producer. Tier-1 item 4.
- **recompute_graph_parallel_fanout.md** — filed 2026-09-25. Parallel
  recompute-graph fan-out: the L1B fold and scipy pair lanes run on
  worker threads beside the sequential cheap lanes (`--jobs N`, default
  1), projected recompute-graph step 34.4 s → ≤ 15 s under -j4.
  Un-defers the 2026-08-29 deferred fan-out row of the cold-embed perf
  review (~4–5 s estimate re-measured at ~13 s; graph_analytics
  65k → 193k rows). Follows #283 (pair reuse), #277 (fold lane),
  #279 (ROUTING).
_(Previously: search_tui_semantic_notes.md archived 2026-09-25 as
completed.md entry 294 — notes lane now fuses BM25 with cached f32 cosine
via RRF60; stale-matrix fallback and pre-warm landed; 20-query warm median
62.7 ms. Execution record in `../archive/tooling/search_tui_semantic_notes.md`.)_

_(Previously: person_resolver_lane.md archived 2026-09-25 as
completed.md entry 293 — person/HUF/trust resolver, 65-category SHP map,
20 artifact rows skipped, and management-change audit reduced 5→3; S4
holder-change machinery remains trigger-gated. Execution record in
`../archive/graph/person_resolver_lane.md`.)_

_(Previously: identifier_fold_validation.md archived 2026-09-25 as
completed.md entry 292 — 5,776 exchange ISIN rows folded into the registry,
with ambiguity/ownership guards, optional CIK support, format/overlap/NULL
validation, and idempotent maint wiring. Execution record in
`../archive/database/identifier_fold_validation.md`.)_

_(Previously: agpl_license_migration.md archived 2026-09-25 as
completed.md entry 291 — AGPL-3.0-or-later adopted for first-party code with
third-party/asset boundaries, source-offer runbook, metadata checks, and
python-igraph compatibility gating. Execution record in
`../archive/tooling/agpl_license_migration.md`.)_

_(Previously: coverage_tags_editions.md archived 2026-09-25 as
completed.md entry 290 — edition company coverage tags converged from
1,286 quote pairs to 91 changed The_Chatter notes and 1,380 note_tags rows;
three reruns were idempotent and the 164-question ontology gate accepted.
Execution record in `../archive/okf/coverage_tags_editions.md`.)_

_(Previously: granite_reprobe_multilingual.md archived 2026-09-25 as
completed.md entry 289 — granite re-probe and multilingual capability gate;
hybrid 12/15 versus BM25 14/15, tuning experiments reverted, multilingual
results recorded without auto-wiring. Execution record in
`../archive/database/granite_reprobe_multilingual.md`.)_

_(Previously: gate_report_quality_traces.md archived 2026-09-25 as
completed.md entry 288 — gate report quality traces: incremental refresh
diagnostics, pending-tail/parser warnings, structured advisory warning capture,
JUnit/artifact visibility, and writer-contract coverage. Execution record in
`../archive/tooling/gate_report_quality_traces.md`.)_

_(Previously: scipy_st_lanes_routing_switch.md archived 2026-09-24 as
completed.md entry 287 — SciPy s-t lanes (Yen / max-flow) behind the
ROUTING switch with wall-clock budget guards; un-deferred
`scipy_yen_k_shortest` (#275) and `scipy_maximum_flow` (#273), while
PPR/MST/A² stayed deferred. Execution record in
`../archive/graph/scipy_st_lanes_routing_switch.md`.)_

_(Previously: the gate_query_improvements and test_data_items arcs
archived 2026-09-24 as completed.md entries 286 and 285; see the
Previously chain below.)_

_(Previously: gate_query_improvements.md archived 2026-09-24 as
completed.md entry 286 — historical comparison, test history, failure
clusters, generic/native artifacts, timing/critical-path views, and an
end-to-end CLI example. Execution record in
`../archive/tooling/gate_query_improvements.md`.)_

_(Previously: test_data_items.md archived 2026-09-24 as completed.md entry
285 — normalized test facts, immutable artifacts, xdist-safe metadata, and
rebuildable ingestion. Execution record in
`../archive/testing/test_data_items.md`.)_

_(Previously: qa_live_test_isolation.md archived 2026-09-24 as
completed.md entry 284 — restored the QA critical path by isolating live
graph checks. Execution record in `../archive/graph/qa_live_test_isolation.md`.)_

_(Previously: scipy_all_pair_reuse.md archived 2026-09-24 as
completed.md entry 283 — ROUTING-gated SciPy closeness/harmonic pair
reuse in `--all` (14.16 s → 7.26 s). Execution record in
`../archive/graph/scipy_all_pair_reuse.md`.)_

_(Previously: gate_run_search.md archived 2026-09-24 as completed.md
entry 282 — gate_query DuckDB index + CLI over the gate-run corpus;
junitxml + Commit/Worktree/Exit metadata; zstd rotation. Execution
record in `../archive/tooling/gate_run_search.md`.)_

_(Previously: graph_perf_l1_bfs_scale.md archived 2026-09-23 as
completed.md entry 277 — VIGIL-scale BFS-family perf: restricted-source
scipy route (156.8 s → 2.9 s), 2-core fold betweenness exact
(48.9 s → 3.3 s), SQL 2-hop link-pred (61 s → 0.65 s), snapshot
ephemerality, re-baselined budgets; perf gate 23/23. Execution record
in `../archive/graph/graph_perf_l1_bfs_scale.md`.)_

_(Previously: scipy_graph_bridge.md archived 2026-09-23 as
completed.md entry 278 — BSD-native scipy second lane (S1 dijkstra
carrier; S2 Katz+eigsh implemented; S3/S4 children deferred with
triggers; §7 27-public surface accounting). Execution record in
`../archive/graph/scipy_graph_bridge.md`.)_

_(Previously: scipy_routing_dispatch.md archived 2026-09-23 as
completed.md entry 279 — ROUTING wired into dispatch (S1+S3 flips,
five-route program, pref-attach guard+heap, stamp diet, destructive
preambles; convention fork pending). Execution record in
`../archive/graph/scipy_routing_dispatch.md`.)_

_(Previously: scipy_katz_exact_solve.md archived 2026-09-23 as
completed.md entry 280 — exact-solve Katz lane (unweighted finding,
admissibility guard, star demo, live parity; applied). Execution
record in `../archive/graph/scipy_katz_exact_solve.md`.)_

_(Previously: scipy_eigenvector_eigsh.md archived 2026-09-23 as
completed.md entry 281 — eigsh robustness lane (P100 case, star
correction, live agreement; applied). Execution record in
`../archive/graph/scipy_eigenvector_eigsh.md`.)_

_(Previously: note_knn_distance_ranking.md archived 2026-09-22 as
completed.md entry 266 — note-KNN cosine→l2 distance swap at all six
query.py KNN sites (scores still cosine-scaled via 1 − d²/2),
per-path section semantics with renormalized-mean reference vectors,
near-duplicate pair space 43M→0.7M, 11× claim corrected to ~1.2–1.3×,
and the DuckDB vss HNSW trial concluded/declined (scan recall 35–57%,
+75% disk). Execution record in
`../archive/graph/note_knn_distance_ranking.md`.)_

_(Previously: pagerank_graph_enhancements.md archived 2026-09-22 as
completed.md entry 270 — PageRank S1 economic projection (0/10 top-10
overlap vs the membership view), S2 weight carry-through re-scoped to
onager's native weight consumption (distinct pagerank_weighted metric,
cited_in n_quotes+1 backfill), S3 temporal backfill (listed_on_index
valid_from 6,615/6,615 + as-of ranking); S4 scoped for future source
arcs, S5 parked on the Onager bug. Execution record in
`../archive/graph/pagerank_graph_enhancements.md`.)_

_(Previously: centrality_rebuild_contract.md archived 2026-09-23 as
completed.md entry 271 — centrality rebuild contract: rebuild data-only
(v_centrality_* dropped; 369.9s → 3.19s), explicit stamp lane
(make stamp-centrality; 5m59s at scale, warm reads 0.017s), stamp-vs-
rebuild swap race guarded with an inode check + retry; follow-up
company-source restriction for the BFS family scoped in perf §B2.
Execution record in
`../archive/graph/centrality_rebuild_contract.md`.)_

_(Previously: ownership_ingestion_nse_shp.md archived 2026-09-22 as
completed.md entry 267 — NSE shareholding XBRL lane: RSS→XBRL fetch +
in-bse-shp parser, 38 holder entities (30 person-kind, first in
store), 40 invested_in regulator-tier edges, upsert-latest
shp_filings/shp_holders; PAN masking means invested_in, not
promoter_of. Execution record in
`../archive/pipeline/ownership_ingestion_nse_shp.md`.)_

_(Previously: related_party_groups_vigil.md archived 2026-09-22 as
completed.md entry 268 — VIGIL bulk RPT lane: 3 sync passes over the
reverse-engineered CC0 bulk API; subsidiary_of 11,348 / same_group
9,781 / jv_with 994 / supplier_to 15,533 / rated_by 216; graph
18,291→56,014 edges with ~19K counter-party entities; BSE
group-repository claim falsified. Execution record in
`../archive/pipeline/related_party_groups_vigil.md`.)_

_(Previously: bse_shareholding_rss.md archived 2026-09-22 as
completed.md entry 269 — BSE SHP second discovery stream: open RSS +
per-filing HTML, col35 promoter-group tags restore the signal NSE
masks; 5 filings / 44 edges / 21 promoter-tagged holders live; no
history endpoint on either exchange. Execution record in
`../archive/pipeline/bse_shareholding_rss.md`.)_

_(Previously: search_tui_ux_pass.md archived 2026-09-22 as
completed.md entry 265 — search_tui UX pass: busy indicators (query
overlay, index-monitor row spinners + elapsed note + double-`R`
busy-guard) and the report panel overhaul (timed-header parse fix,
worktree copies listed separately, collapsed run tree with scoped `V`,
verify/integrity run histories, writer append-at-tail pinned by tests),
plus DbScreen/rerun overlays and theme-aware verdict colors. Frames
ported from ratatui-spinner `FluxFrames::CLASSIC`; zero new
dependencies. Execution record in
`../archive/tooling/search_tui_ux_pass.md`.)_

_(Previously: snapshot_fresh_gate.md archived 2026-09-21 as
completed.md entry 264 — snapshot freshness pre-gate:
generation-only `--quick` over sqlite + DuckDB pairs with
fail-closed semantics and remediation; qa `snapshot-fresh` step +
target. Execution record in
`../archive/tooling/snapshot_fresh_gate.md`.)_

_(Previously: validator_lint_cleanup.md archived 2026-09-21 as
completed.md entry 263 — validator lint cleanup: two C901 splits +
S607 annotation, extraction-only. Execution record in
`../archive/tooling/validator_lint_cleanup.md`.)_

_(Previously: gate_latency_followups.md archived 2026-09-21 as
completed.md entry 262 — gate-latency bundle: syntax/chokepoint/
data_format/sqlite/js/shebang legs gated via py/js scopes with
scope-driven iteration; hygiene refined to in-scope staleness; dead
import dropped. Execution record in
`../archive/tooling/gate_latency_followups.md`.)_

_(Previously: fastjsonschema_split_track.md archived 2026-09-21 as
completed.md entry 261 — fast validation engine by default,
`--strict` opts into jsonschema; 1456/1456 verdict agreement;
maint-full advisory `--report` twin. Execution record in
`../archive/tooling/fastjsonschema_split_track.md`.)_

_(Previously: dirty_gated_corpus_validation.md archived 2026-09-21 as
completed.md entry 260 — dirty-gated corpus validation with flipped
default and maint-full backstop. Execution record in
`../archive/tooling/dirty_gated_corpus_validation.md`.)_
_(Previously: graph_centrality_persistent_cache.md archived 2026-09-21 as
completed.md entry 259 — persistent per-generation centrality cache: ten
`v_centrality_*` tables stamped into `graph.duckdb` at rebuild (schema 17);
Arrow-CTAS stamping after the executemany WAL finding; readers serve tables,
`--compute` keeps benchmarks honest; warm CLI reads 0.35–0.45s. Execution
record in `../archive/graph/graph_centrality_persistent_cache.md`.)_

_(Previously: subsector_authoring_pass.md archived 2026-09-21 as
completed.md entry 258 — sub-sector authoring pass: the 16 parked
worklist buckets resolved via 8 SUB_SECTOR_ALIASES additions (incl.
the Gaming leaf) + per-note authored `subsector:` on 81 company
notes; D7 convergence prune; eval gate ACCEPT. Execution record in
`../archive/graph/subsector_authoring_pass.md`.)_
_(Previously: test_gap_closure.md archived 2026-09-20 as completed.md entry
  256 — test-gap closure: the date-rot class (S1, already fixed by the
  operator), availability budgets for the hot routes (S2, 10 tests), the
  first perf legs to drive the Flask request path (S3, `bench_routes.py`),
  worker + BrokenProcessPool fallback tests (S4, 6 tests), and a fuzz guard
  for the last quadratic regex (S5). All mutation-verified; `make qa` 10/10;
  execution record in `../archive/testing/test_gap_closure.md`.)_

Per-patch rule (2026-09-19): a
proposal's index line is added by the SAME patch that adds its file,
so this section never references content from future patches.

_(Previously: conc1_graph_connection_isolation.md archived 2026-09-19 as
completed.md entry 251 — CONC-1 fix: requests no longer share one
process-wide read-only connection (63 wrong rows + 126 errors / 3,000 ->
0 / 0); each request gets its own connection on `flask.g`, closed at
teardown, with the direct-call singleton preserved for tests and CLI;
execution record in `../archive/security/conc1_graph_connection_isolation.md`.)_

_(Previously: avail2_metric_regex_deAmbiguate.md archived 2026-09-19 as
completed.md entry 250 — AVAIL-2 fix: the cubic ReDoS in the metric range
patterns is removed at the pattern (`\s*(?:[-–]|to)\s*`, both derive
copies); a drafted length cap was rejected on corpus measurement (a 300-char
cap drops 1.21% of captures) in favour of the adversarial fuzz guard, which
takes 10,349 ms on the old class and ~1 ms on the fix; execution record in
`../archive/security/avail2_metric_regex_deAmbiguate.md`.)_

_(Previously: near_duplicates_api_compute_cap.md archived 2026-09-19 as
completed.md entry 249 — AVAIL-1 fix: the unauthenticated O(n^2)
near-duplicates self-join is memoized per cache generation (59.31 s ->
0.010 ms measured) and refused over a 10,000-doc corpus ceiling (503);
execution record in `../archive/security/near_duplicates_api_compute_cap.md`;
an LRU bound on the memo was added as-implemented because min_sim is
client-controlled.)_

_(Previously: mojo_footprint_repair.md executed 2026-09-19 as
completed.md entry 247 (archived to `../archive/tooling/`);
industry_coding_completion.md executed 2026-09-19 as entry 248
(archived to `../archive/database/`); nic2008_seed_table.md executed
2026-09-19 as entry 246 (archived to `../archive/database/`);
tmpdir_sanitization.md archived 2026-09-17 as entry 245.)_
