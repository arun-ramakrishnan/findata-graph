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
   number; reset the live-proposal pointer below to `*(none)*`.
5. `make search-fresh APPLY=1`, then plain `make search-fresh` (rc=0)
   to converge the doc index.
6. Flip the frontmatter block in the same change: `status: executed`
   + the executed date + the completed.md number — the Proposal
   lifecycle static check fails an archived proposal still saying
   `proposed` (corpus_uniformity S3).
7. Reference-rot sweep: grep by proposal NAME, not by tracked path —
   proposals are filed untracked, so path-based sweeps never see code
   references (range review 4fd3a20f, H2; the 2026-10-11 sweep
   repointed 16 dead sites).

## Current live proposals

- *(archived 2026-11-09)* — `rust_bfs_kernels_stats_trails` executed
  same day, #497
  (`../archive/graph/rust_bfs_kernels_stats_trails.md`; `graph_kernels`
  abi3 crate: exact all-pairs BFS stats — stats CLI 76.63 s → 11.27 s,
  peak 2.9 GB → 456 MB, canonical ties; batched trail kernel — full map
  166.8 s → 3.56 s at 0.17 ms/source with hop-level parity; SciPy/pool
  fallbacks kept behind `TRAIL_BFS_RUST=0`; cosine-shaped Rust stays
  closed).
- *(archived 2026-11-09)* — `gate_output_fidelity_and_integration_leg`
  filed and executed the same day, #496
  (`../archive/testing/gate_output_fidelity_and_integration_leg.md`;
  node-id fix 118→783, retained step logs + `gate_query output` verb,
  the ~141 s redundant `integration` leg retired from the arc battery,
  and a §7 addendum covering retention for the four lanes that never
  ride the runner — perf, maint, integrity, verify).
- *(archived 2026-11-09)* — `convo_search_drop_hnsw_numpy_copy_compact`
  executed same day, #495
  (`../archive/convo_search/convo_search_drop_hnsw_numpy_copy_compact.md`;
  HNSW was the bloat source, not the write path; compaction 4.40 GB →
  0.977 GB in ~15 s by dropping the index BEFORE the COPY — the naive
  copy carried the 370 MB graph blob and cost 158 s; exact-KNN numpy leg
  is opt-in after F1, `--swap` retired, tripwire kept as a regression
  watch).
- *(archived 2026-11-08)* — `star_hyperedge_consumer_wiring` executed
  same day, #492
  (`../archive/graph/star_hyperedge_consumer_wiring.md`; S0 dyad audit
  reversed the rev-1 writer-bug claim — same-period two-way emission is
  documented design — and applied the k≥3 threshold for the first time,
  re-deriving the stars to 386/683 with delete-then-insert convergence;
  S1 six-family `_HYPER_FAMILIES` context-pack lane; S2 `--families`
  selector + `rederive-hyper` maint step (snapshot-ordering lesson
  included); S3 `make hyper-star-centrality` opt-in).
- *(archived 2026-11-08)* — `ontology_mapping_glossary_enhancement`
  executed same day, #493
  (`../archive/database/ontology_mapping_glossary_enhancement.md`;
  7 repo-native mapping entries in
  `doc/reference/ontology_glossary.md` — semantics-only,
  magnitudes in `doc/design/db_schema.md` per the anti-drift rule,
  refreshed same sitting; per-type dispositions for all 11 exclusions).
- *(archived 2026-11-08)* — `ifub_frontier_bfs_and_store_census_closeout`
  executed same day, #491
  (`../archive/maintenance/ifub_frontier_bfs_and_store_census_closeout.md`;
  iFUB vectorized frontier BFS verified 1.5-2.6× faster with parity pin;
  `_FILING_MB` refreshed to 2026-11-08 live values after the 10-27
  refresh was found never to have reached the code; first production
  `--swap` via the S2 tripwire (~1.88 GiB reclaimed off convo_search);
  owner dispositions recorded; pending.md C901 + census records
  corrected).
- *(archived 2026-11-06)* — `scipy_personalized_pagerank` filed +
  executed same day, #484 (`../archive/graph/scipy_personalized_pagerank.md`;
  deferred exact-solve PPR lane shipped beside Onager — whose
  personalised-pagerank bug the custom build does NOT fix — CLI + API +
  dispatch + `--all-ppr-top50` (50 India-listed large_cap seeds, 5,000
  rows live); review outcomes §5b (BLOCKER-1 `--seeds-file` shipped,
  MAJOR-1 recorded); hybrid per-sector slice §5c (`--sector` /
  `sector_scope=True` / `?sector=1`); replaces the stale 2026-09-23
  deferral copy, whose §7 gate record is preserved in the archived doc).
- *(archived 2026-11-06)* — `ppr_results_table` filed + executed same
  day, #484 (`../archive/database/ppr_results_table.md`; seed-keyed
  `ppr_results` schema v17 live + the same-day projection-evolution
  record: `listed_in` country-hub drop, include-only 8-type relational
  PPR projection (`co_mentioned_in` volume + `invested_in` dropped),
  and the per-seed `replace=True` writer fix for payload-dropout
  staleness).
- *(archived 2026-11-06)* — `graph_hnsw_next` filed + executed S1 same
  day, #482 (`../archive/database/graph_hnsw_next.md`; graph-lane HNSW
  landed: indexes + stopgap-reroute + served-path vss, step-0 gates
  pass; S2 FTS5-sidecar retirement + S3 lane-CLI semantic legs
  deferred remainder, tracked in the archived copy).
- *(archived 2026-11-06)* — `duckdb_full_consolidation` filed + deferred
  same day 2026-11-06, #483
  (`../archive/database/duckdb_full_consolidation.md`; trigger verdicts
  gate NOT met, B2-lite contention map, B1-lite port sizing — 113
  importers, 33 CASCADE sites, FK CASCADE parser-rejected on 2.0.0 —
  P1/P2 posture fork; recommendation: do not merge now, revisit on
  trigger 2 second-writer or a backup-pain event).
- *(archived 2026-11-07)* — `polyadic_capture_edge_fidelity` filed
  2026-11-06, executed 2026-11-07, #486–#490
  (`../archive/graph/polyadic_capture_edge_fidelity.md`; S1 n-ary
  event participants 11 new k≥3; S2 supply k=1 census 200 buyer
  entities alias-folded; S3 sidecar as_of→valid_from promotion +
  integrity check; S3b standing-edge date backfill rated_by 211/214;
  S4 agency-family canonicalization; S5 edition clique retired —
  9,638 dyads → 0, incidence canonical, `--expect-shrink` gate
  extension; S6 parked). No fuzzy entity expansion (2026-10 operator
  ruling baked in as constraint).
- *(archived 2026-11-07)* — `convo_search_dead_block_compaction` filed
  2026-11-06, executed 2026-11-07, #485
  (`../archive/convo_search/convo_search_dead_block_compaction.md`;
  S1 swap compaction 4.84→1.27 GB, S2 dead-block tripwire + db_maint
  F4 closure, S3 swap-rebuild-only size law, S4 zero-churn content-hash
  delta + part-addressed pointers, S5 retention window filed deferred
  trigger-gated; migration-route doctrine recorded in-proposal).
- *(archived 2026-11-03)* — `served_store_read_funnel` filed and executed
  the same day out of a parallel unlocked-work session, #476
  (`../archive/tooling/served_store_read_funnel.md`; PID-namespace
  blindness in the served-store liveness probe broke the blocking
  schema-drift gate — wire attach is byte-identical, so the failure was
  never data; S1 wire-attach fallback in `open_read_only`, S2
  namespace-aware liveness probe, S3 routed the five raw bypass sites).
  This entry sat un-archived in the live list from 2026-11-03 to
  2026-11-08 — the lifecycle static check only inspected the archived
  file's frontmatter, never this pointer (see the guard added
  2026-11-08).
- *(archived 2026-11-05)* — `market_cap_f1_hypergated` filed + deferred
  same day 2026-11-05, #480
  (`../archive/database/market_cap_f1_hypergated.md`; post-review
  correction: F1 41 conflicts fixed at source 2026-08-05, live 0;
  S4 giant-k probe banked as one-shot QA, S5/S6 parked).
- *(archived same day)* — `scaling_next_walls` filed + executed +
  archived same day 2026-11-02, #474
  (`../archive/graph/scaling_next_walls.md`; five measured scaling walls
  all landed: B-C rebuild sharding (hash-range e_all_und, parity),
  trail-map parallelism (multiprocessing pool) + Mojo bipartite BFS
  parity twin (promotion gated on >2× crossover), chains-cap exactness
  (iFUB diameter), incidence-native bipartite community (louvain on
  live h_incidence), blue-green reads (rebuild-window DEGRADED mirror
  fallback)).
- *(archived same day)* — `hyper_trail_bipartite_bfs_lane` filed + executed +
  archived same day 2026-11-02, #473
  (`../archive/graph/hyper_trail_bipartite_bfs_lane.md`;
  HGX unlock shelf lane 4: upstream `dict_to_df` is a dead end twice
  over — crash unfixed AND n^2-dense design; repo-native bipartite BFS
  trail lane landed instead — full live map 18,233 entities in 166.8 s,
  156 MB).
- *(archived same day)* — `opencode_drift_check_fastpath` filed + executed +
  archived same day 2026-11-02, #470
  (`../archive/database/opencode_drift_check_fastpath.md`;
  db_scaling_tuning §6.4 spin-off — warm convo-fresh `--check` 31s -> 5.5s,
  zero harness-store mutation).

- *(none before this)* — `rust_jemalloc_global_allocator` executed + archived
  same day (S1 desktop adopt on operator ruling, S3 parked).
- *(none live)* — `opencode_drift_check_fastpath` filed + executed +
  archived same day 2026-11-02, #470
  (`../archive/database/opencode_drift_check_fastpath.md`;
  db_scaling_tuning §6.4 spin-off — warm convo-fresh `--check` 31s -> 5.5s,
  zero harness-store mutation), and `bili_sessions_filter_first_window`
  retro-filed + executed same day, #469
  (`../archive/tooling/bili_sessions_filter_first_window.md`;
  `--since 1d` startup 10.35s -> 1.5-1.7s), then
  `app_server_jemalloc_promotion` retro-filed + executed same day, #471
  (`../archive/tooling/app_server_jemalloc_promotion.md`; resident
  app.py RSS −10-12% under jemalloc, wired as `make app-server`), then
  `hgx_shelf_spectral_rw_measurements` wrapped + wired + archived
  (#472, `../archive/graph/hgx_shelf_spectral_rw_measurements.md`;
  lanes 3+5 production wire, sub_sector commute verdict corrected).
  *(before these: `rust_jemalloc_global_allocator` executed + archived
  same day (S1 desktop adopt on operator ruling, S3 parked).)*
  *(previously live: the 2026-11-01 tooling batch (`rust_proptest_properties`,
  `desktop_lock_advisory_remediation`, `rust_cargo_llvm_cov`) executed and
  archived same day.)* Most recent archived:
  `rust_jemalloc_global_allocator` (#468, S1 desktop adopt, S3 parked),
  then `rust_cargo_llvm_cov` (#467, llvm-cov one-shot, MEASURED-NO-ACTION),
  then `desktop_lock_advisory_remediation` (#466, tokio/yoke cleared,
  zbus family accept-risk), then `rust_proptest_properties` (#465, 8
  properties, shrunken-counterexample shakedown), then
  `cargo_deny_superset_scan` (filed + executed same day
  2026-11-01, #464, retro-filed at operator request —
  `../archive/tooling/cargo_deny_superset_scan.md`; Rust/ fully clean, AGPL
  first-party licenses + per-crate exceptions, `make rust-deny` advisory
  leg), then `test_coverage_gap_sweep` (filed + executed
  same day 2026-11-01, #463,
  `../archive/testing/test_coverage_gap_sweep.md`; P1 unit gaps closed,
  fuzz legs incl. the as-of leak property, two integration chains, 4
  perf legs, 5 production bugs fixed — then `graph_alpha_lane` (filed +
  executed same day 2026-10-31, #462,
  `../archive/graph/graph_alpha_lane.md`; 46k edges dated, leak-free
  re-sweep, first lookahead artifact caught — `competes` — no
  pre-registration; `rpt_disclosure` h=4 abnormal = watch), then `price_lane_alpha_research` (filed + executed
  same day 2026-10-31, #461,
  `../archive/graph/price_lane_alpha_research.md`; as-of substrate,
  weekly OHLCV sidecar 90.2%, event studies, pre-registered precision
  instrument), then `graph_lane_unlocks` (filed + executed same
  day 2026-10-31, #460,
  `../archive/graph/graph_lane_unlocks.md`; S1 matching Hall print,
  S2 group convergence, S3 index opt-in decision, S4 backbone
  resilience 82.7% bridges, §5 trigger register frozen), then
  `mst_backbone_wiring` (filed 2026-10-30, executed
  2026-10-30, #458,
  `../archive/graph/mst_backbone_wiring.md`; stats.py advisory section
  plus `make graph-mst`) and `maximum_bipartite_matching_wiring` (filed
  2026-10-30, executed 2026-10-30, #459,
  `../archive/graph/maximum_bipartite_matching_wiring.md`; stats.py
  advisory section + `make graph-matching`), then
  `graph_analytics_integrity` (filed + executed
  same day 2026-10-30, #456,
  `../archive/graph/graph_analytics_integrity.md`; voterank
  freshest-wins + replace-on-write, clustering/wcc off the star at
  dispatch level, live-vs-stored drift print, `prune_analytics.py`
  sweep, debris-entity quarantine; ledger-amend fingerprint-restamp
  defect fixed as by-catch) and `hgx_over_dyads` (filed + executed same
  day 2026-10-30, #457, `../archive/graph/hgx_over_dyads.md`; hy-MMSBM
  NaN guard, `index` family 57/6,615, `group` widened filer-keyed
  498/10,738 with fold provenance 1,545→1,450, `subsidiary_of`/
  `supplier_to` DEFERRED, #451 record corrected), then
  `db_scaling_tuning` (filed 2026-10-29, executed 2026-10-30,
  #454, `../archive/database/db_scaling_tuning.md`; §3.2 conn cache +
  §3.3 SQLite cache 80→256MB landed in `db_tuning`, §3.1 threads/
  memory_limit declined, §6.3 orjson ADOPTED via `helpers/core/jsonio.py`,
  §6.1 FTS5-optimize and §6.2 preserve_insertion_order
  MEASURED-NOT-ADOPTED, §6.4 opencode drift-query lever parked to
  pending.md), then `bili_absorb_stats_display` (filed + executed +
  archived same day 2026-10-29, #452,
  `../archive/tooling/bili_absorb_stats_display.md`) and
  `notes_thin_tail_quality` (filed 2026-10-28, executed with its D1–D3
  remainder DEFERRED 2026-10-29, #453,
  `../archive/pipeline/notes_thin_tail_quality.md`; remainder tracked
  in `../pending.md`). Detail bullets for every archived proposal live
  in `../archive/README.md`; `../completed.md` is the canonical
  record.

- Family provenance: split 2026-10-21 from the bundled
  `duckdb20_quack_variant` filing; the executed 2.0-compat migration
  is completed.md #427. Upstream changeset:
  <https://github.com/duckdb/duckdb/releases/tag/v2.0.0>.
