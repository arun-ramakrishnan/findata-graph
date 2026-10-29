# Pending improvements

Full annotated triage map with live-verified trigger status.
Open items below keep their revisit triggers inline; executed work is compressed to records.

- **Thin-tail note quality — D1–D3 remainder — DEFERRED 2026-10-29**
  (archive/pipeline/notes_thin_tail_quality.md; completed.md #452):
  S1–S5 executed — KF evidence UNION + `--kf-manifest` backfill (KF
  526 → 1,046), `--from-manifest` yfinance lane (139/152 stubs, 1,701
  rows), `--untickered` hygiene (15 resolved, 132 queued in
  `outputs/ticker_hygiene_report.md`), the S2 magnitude skip-guard root
  cause (verified against the entity note, not the edition file; 2,226
  rows recovered, `company_metrics` 76,172 → 78,683), S2.4 ten new label
  patterns (NULL 3,119 → 2,539). Remainder re-measured live 2026-10-29:
  D1 144 stubs at `-size -4k` (≤3072 B; 202 below 4096 B), D2 132
  untickered note-backed entities, D3 1,946 NULL `metric_label` of
  78,683 rows → same-day D3 census round 2 (+9 patterns, credit_cost
  rejected) relabeled 28 id-keyed → same-day LLM carrier pass (session
  agent as carrier over all 1,918 residue groups; 365 rows labeled
  id-keyed under `metric_label IS NULL`, 53 new nouns promoted,
  artifacts in bench_data/thin_tail/) → D3 remainder 1,553 (2.0% of
  78,683; sub-segment levels/growths, peer/industry figures,
  mix/coverage shares, bps deltas, and truncated-subject fragments
  stay NULL by doctrine). Same-day D2 operator triage via menu
  buckets (rulings m00469): 15 tickers filled (5 confirmed India +
  Forbes and Company FORBESCO.BO + 6 foreign home-market — UBS
  UBSG.SW, SMBC 8316.T, NEC 6701.T, Thai Union TU.BK, Piaggio PIA.MI,
  Liberty Media FWONA — + 2 NSE-SME India CGRAPHICS.NS/TECHERA.NS +
  Gujarat Gas GUJGASLTD.NS), 5 dispositions in `entity_ticker_status`
  (Piramal Enterprises amalgamated → Piramal Finance; BPSL/Wabco/
  Shri Lakshmi Cotsyn/Sterlite Power delisted), Forbes Enviro note-meta
  ticker misattribution cleared (EUREKAFORB.NS → null; belongs to
  Eureka Forbes), untickered note-backed 132 → 117; decisions in
  bench_data/thin_tail/d2_triage_decisions.json. Revisit
  triggers: D1 new newsletter editions, ~90
  section-variant + 47 collision curation follow-ups, an explicit
  disposition pass (+4 routed stubs: Baba Jewellers, HKC, TSF Group,
  Time-bound Solutions; Mod-Tek Packaging merged into Mold
  Tek Packaging MOLDTKPAC.NS — operator ruling 2026-10-29:
  `entity_identifiers` alias + stub redirect; fabricated body ticker
  MOTE.NS removed; 0 metrics rows, count stays 117); D2 quarterly re-run + the 5 verify-open
  rows (Paul Merchants Finance, Carraro Group, Kering Beaute, Hitachi
  Energy, Jos Alukkas) + geography-aware suffix preference in
  `identity_ok`; D3 vocabulary
  growth from new corpus (re-run the bigram census on the then-NULL
  set), #308 desktop em-dash fallback posture.

- **Review lanes (semantic vs mechanical + cosmetic floor) — EXECUTED 2026-10-22**
  (archive/tooling/ocr_review_lanes_7b122d915_e3147ef3c.md; completed.md #432):
  S1 procedure §4 lanes (`cosmetic` row below Low + anti-inflation rule 3)
  → S2 `review_scan.py` lane labels (`cosmetic` never ranks; collapse to count
  + file list) → S3 roster grouping → S4 freshness record with lane counts
  (`repeat_ledger.json`, line-free fingerprint keyed by rule/path/repeat).
  Measured on `7b122d915..e3147ef3c`: 113 findings → 21 ranked + 92 collapsed.
  No query-visible semantics change → eval-gate not triggered. Gates deferred
  to the operator's next full run.

- **Merger/demerger n-ary hyperedges — EXECUTED 2026-10-21**
  (archive/graph/merger_demerger_hyperedges.md; completed.md #426):
  S1 `hyperedge_types.py` registry (12 families, per-family roles,
  integrity-gated) → S2 `derive_merger_hyperedges.py` derive lane →
  S3 extractor routing + `a[/merger|/demerger[/NAME]]` triage verbs →
  S4 `hyperedge_labels` eval shape (2 question ids, ACCEPT 166/0) →
  S5 docs. First live sitting landed 5 hyperedges with zero dyad rows;
  two apply-time bugs fixed with regression tests. Gates deferred to the
  operator's next full run.

- **Prompt-driven review menu — EXECUTED 2026-10-21** (archive/tooling/
  review_menu_prompt_driven.md; completed.md #424): S1–S6 landed —
  `review_menu.py` backends over the `review_kit` spine, relations/quotes/
  NIC adoption, `coverage_ledger.review` + `security_findings.triage`,
  OCR + markdown-parse checklist walkers; AC#1 journal equivalence;
  eval-gate not triggered (no query-visible semantics change). Demoed
  live on the relations queue (dry-run, 3 real rows).

- **Automated carrier pre-annotation + recommended hints — EXECUTED 2026-10-21**
  (archive/tooling/review_preannotate_hints.md; completed.md #425):
  S1 auto-run `annotate_surface` per surface on every review sitting
  (default-on `--preannotate`, idempotent, non-fatal), S2 `★ recommended`
  menu/packet hints via `carrier_verdict` → per-surface verb mappers;
  captures the decisions-preannotate/menu/carrier work as S0; eval-gate
  not triggered (no query-visible semantics change). Gates (qa +
  integration + perf + advisory) deferred to the operator's go.

- **C901 D1 splits — four named functions scheduled (operator ruling
  2026-09-30)** (`archive/graph/c901_complexity_debt.md` §4.1 "no new
  suppression" vs the lint-audit arc's four new masks):
  `csr.try_shortest_path` (11>10), `extract_relations._process_pattern_matches`
  (12>10), `query.near_duplicate_notes` (12>10), `review_selection.main`
  (11>10; 12 in the working tree since the house checklist grew `main`).
  Each split lands → its `# noqa: C901` comes off. Disposition record:
  `archive/tooling/review_findings_collation.md` (class 8, S6).
  **Batch 1 landed 2026-10-01** (csr, extract_relations,
  review_selection — masks off, parity green; see
  `archive/tooling/c901_d1_split_batch1.md`).
  **`query.near_duplicate_notes` landed 2026-10-01** (split behind a
  parity fixture — byte-identical across pinned hash seeds; mask off;
  see `archive/tooling/c901_d1_split_near_duplicate_notes.md`; the earlier
  batch-2 hold expired — the P2.2 arc was doc-only). **All four ruling
   masks executed.** What remains in `query.py` are the two pre-existing
   unruled C901 masks (`_materialise_centrality_cache` at `:1594` booked
   to the next c901 pass by collation S7; `_cli` at `:4055` unruled).

- **production-store copy census — TRIGGER ARMED 2026-09-29, FIRED
  2026-10-13 — RE-RUN DONE 2026-10-19 (completed.md #415) — BASELINE
  REFRESHED 2026-10-27** (`archive/testing/production_db_copy_audit.md`,
  executed same day): every copy/backup site is verdict-tabled there, and
  the chokepoint is gated deny-by-default —
  `copy_production_db(requestor=...)` + the operator-owned
  `SANCTIONED_REQUESTORS` registry in `tests/helpers.py`, with a static
  check holding call sites and registry in 1:1 lockstep. **REVISIT
  TRIGGER: re-run the census (store sizes, per-run transient,
  backup-lane cost) when any production store roughly doubles** — a
  sanction is sized against the data of its day and decays silently
  otherwise (the keep_all class: copies sanctioned as small became
  307 MiB × 30 per gate run). Store sizes at original filing:
  convo_search 820.5, research 307.2, embed_store 184.6, convo_fts
  102.0, corpus 52.6, graph 39.3 + 39.0, doc_search 24.7 MiB. **Measured
  2026-10-13: convo_fts 232.5 (2.28× — FIRED), embed_store 311.7
  (1.69× — also past 1.5×), research 313.7, convo_search.duckdb 841,
  graph 48.0 + 48.5, doc_search 31.8, corpus 27.2 MiB.** Growth
  drivers: convo_fts/embed_store scale with the harvest+embed lanes
  (gemma cohort rows), so per-copy cost has roughly doubled for the two
  biggest sanctioned classes — the census re-run (size per lane,
  transient per run, backup-lane cost) is the outstanding action.
  **2026-10-14: dead-page reclamation + a maint path for every store
  landed** (store_wide_reclaim, completed.md #391 — ≈84 MiB reclaimed
  across the two passes) — this census re-run stays armed for payload
  growth: reclaim frees pages, never shrinks payloads (store_wide_reclaim
  §F5). **2026-10-19 re-run verdicts:** embed_store 2.08× SANCTIONED
  (downtime-window insurance — it rescued multiple 2-5 h
  cache-blowouts; size was never the factor, operator ruling),
  convo_search_fts 2.51× → its per-run backup RETIRED from the db_maint.
  **2026-10-27 baseline refresh** (`store_sweep.py` `_FILING_MB` updated):
  convo_search.duckdb 414.3, convo_search_fts 304.0, embed_store 435.9,
  corpus 53.3, doc_search 25.1, script_search 3.9, memory_search 2.4,
  md_lint_cache 0.3, graph.xdist-shared.duckdb 58.5, agent_traces
  185.8, sources 41.5, model_usage 1.8 MiB. All size-watch lines now
  silent (ratio 1.0×).
  lane (−79 MiB/run; derived lookup index, minutes-class re-index),
  transient 7 MB (the 2026-09-29 mechanisms held), backup lane 1.96×.
  Standing watch: `size_watch` in store_sweep.py (warn 1.5× / ACT 2×,
  advisory-only) — trigger RE-ARMED on the same rule against the
  2026-10-19 live sizes.

- **production-DB copies in tests (§D2) — CLOSED 2026-09-29 (schema class
  migrated; downsampler class adjudicated VALID)**: the schema-only class
  migrated onto a shared per-run `schema_template()`
  (`tests/_tmp_hygiene.py`, same flock+atomic-publish S1 mechanism as
  `trimmed_template`; production schema + db_meta, zero rows, VACUUMed
  tiny — FTS5 gotchas encoded: `delete-all` only for contentless/
  external-content tables, never DELETE shadows). Migrated consumers:
  test_fuzz_shortest_path (the ~308 MB/worker vacuumed copy), test_graph
  `_minimal_db`, both extraction/events CLIs, snapshot_cycle,
  near_duplicates (both sites). **Operator adjudication 2026-09-29: the
  remaining downsampler class (`test_integration_maint_chain.py`,
  `test_integration_note_writers.py`) is a VALID use case** — they pin
  assertions against real downsampled production data (company subsets
  with live edges/tags/metrics), which no template can substitute; their
  per-scope backups stay, guarded by the allowlist. `helpers.py`
  `copy_production_db` stays as their sanctioned builder. No further
  D2 work — superseded the same day by the estate-wide chase
  (`archive/testing/production_db_copy_audit.md`, the census item above),
  which fixed the keep_all amplifiers D2's census never listed,
  content-pruned note_writers, and gated the chokepoint.

- **trace-analyzer coverage — EXECUTED 2026-09-26 (#303)**
  (`archive/tooling/trace_analyzer_legs.md`): 10 legs + `--legs` landed
  (`reliability`, `spend`, `side_effects`, `load_health`, `plan_routing`,
  `failure_forensics`, `session_economics`, `overhead_tax`, `turn_quality`,
  `store_range`); census now reads 29 of the original 39 columns (111
  populated store-wide, 90 read, 21 unread) and S1 surfaces the orphaned
  `fact_log` (527 prime "provider stream failure" + 1419 model-resolution
  warnings). S5 materialized zcode cost from the existing price table
  (3160/3160 requests priced, $71.5713) and, in doing so, exposed a token
  convention bug — zcode's `input` is inclusive of cache reads, opencode's is
  exclusive — that had been double-counting every cross-source token total
  and billing zcode's cache at the uncached rate; the subset invariant
  against `mu` went from violated on 9/10 days to holding on 9/11, and the
  local/quota-wide cost ratio from an impossible 1.87x to 0.346x. Lane-4's
  open question (§6 of `doc/local/engineering/capture_traces.md`) also
  settled: the 22M-token turn is 134 sequential requests summed, not a
  context overrun. S11 concluded with no join — 6 of 7 dead columns are
  empty at the source, and the one genuinely partial column
  (`error_message`, 16/3160) needs a schema addition, not a loader fix.
  Closed 2026-10-02 by `trace_error_forensics` (completed.md #337): the
  column landed on the request schema and every provider-absent column
  got a disposition label (`report --legs column_dispositions`).

- **gate_query integrity/verify grammar gap — IMPLEMENTED 2026-09-29 (completed.md #313; designed #302)**
  (`archive/tooling/gate_index_grammar_coverage.md`): the integrity and verify
  report titles never matched the `# make <target> — gate report` block grammar,
  so parse offsets stayed 0 and every gate_query invocation (main() auto-refresh)
  rescanned them in full: 47.3 of 48.9 MB corpus bytes. S1–S3 executed exactly as
  designed (search_tui grammar reuse → both gates indexed; rotate catch-up —
  97 runs / 31.5 MB archived, live file 22 MB; loud zero-block invariant).
  Measured: backfill 918 s one-time (564 runs: integrity 195, verify 170,
  0 parse errors); incremental refresh 1.29 s → **0.65 s median**;
  `latest --gate integrity` / `failures` / `timing` live.

- **gate_query historical/artifact intelligence — EXECUTED 2026-09-24 (#286)**
  (`archive/tooling/gate_query_improvements.md`): added run comparison,
  historical test queries, failure clustering, generic/native artifact
  ingestion, timing/critical-path views, and an end-to-end CLI example.
  Full-QA fixes covered xdist custom-option registration in QA/integration/
  advisory and the scheduler-sensitive regex guard.

- **test_data normalization — EXECUTED 2026-09-24 (#285)**
  (`archive/testing/test_data_items.md`): `gate_query` now retains immutable
  per-run JUnit/manifest artifacts, indexes 15-field normalized test facts with
  phase/marker/worker/fingerprint metadata, handles missing/corrupt artifacts,
  and rebuilds retained facts through `refresh --full`. Existing DuckDB schemas
  migrate additively; the non-live suite passed 3476 tests with 3 skips.

- **QA live-test isolation — EXECUTED 2026-09-24 (#284)**
  (`archive/graph/qa_live_test_isolation.md`): `gate_query` history
  identified the run-34→37 pytest jump (116.64s → 155.62s). Three live
  parity tests were moved to advisory `live-invariants`; the synthetic
  `--all` test now stubs routed lanes and runs in 0.46s instead of 16.68s
  after pair reuse (39.15s before it).

- **gate_query test-run search — EXECUTED 2026-09-24 (#282)**
  (`archive/tooling/gate_run_search.md`): DuckDB index + CLI over the
  append-only gate reports — latest-run digest, failed-test details
  (junitxml-backed), timing-vs-budget history, byte-offset incremental
  refresh, zstd rotation into `outputs/archives/`. Solves the
  prompts-parsing-huge-gate-output problem; writer enhancements land in
  `tests/run_gate_report.py` (junitxml, Commit/Worktree/Exit meta).

- **scipy routing dispatch — EXECUTED 2026-09-23; pair-reuse follow-up
  EXECUTED 2026-09-24 (#283)**
  (`archive/graph/scipy_routing_dispatch.md`,
  `archive/graph/scipy_all_pair_reuse.md`): all five
  routes are closed. SCIPY serves closeness/harmonic, `L1B_FOLD` serves
  betweenness, pref-attach has the consent gate plus exact bounded top-K
  heap, routed metrics are skipped by the centrality stamp lane, and hop
  metrics are closed under their measured/cache-backed path. The follow-up
  makes `--all` compute the closeness/harmonic dijkstra pair once instead
  of once per metric: 14.16 s median became 7.26 s, saving 6.90 s with
  invocation-scoped reuse and no persistent cache surface.

- **scipy_algos umbrella batch — FILED 2026-09-23, five children
  DEFERRED + ARCHIVED 2026-09-23** (completed.md #272–276; triggers
  recorded in each record): `personalized_pagerank` (no consumer —
  **structural blocker now recorded**: leaf-heavy/star-dominated graph
  makes PPR a 1-hop ego rank; see `scipy_personalized_pagerank.md` §7,
  do not re-litigate on solver grounds),
  `maximum_flow` (**un-deferred + implemented 2026-09-24** via
  `archive/graph/scipy_st_lanes_routing_switch.md`), `minimum_spanning_tree`
  (need still deferred), `yen_k_shortest` (**un-deferred + implemented
  2026-09-24** via the same proposal),
  `link_prediction_kernel` (SQL L1c delivered exact at 0.65 s).
  Implemented from the batch: Katz exact-solve + eigsh eigenvector
  lanes (`helpers/graph/scipy_bridge.py`, opt-in, no dispatch flip) +
  the two s-t lanes (Yen / max-flow) behind the ROUTING switch with
  wall-clock budget guards.
  (children of `archive/graph/scipy_graph_bridge.md`, umbrella patch
  `scipy_algos`): Katz (exact solve retires the 1e-4 alpha pin;
  near-critical alpha restores the flattened spread),
  personalized PageRank — archived (restart-hardcoded bug),
  eigenvector eigsh (robust fallback where Onager
  hits "Convergence failed after 100 iterations"; Onager stays
  default), maximum flow — **un-deferred 2026-09-24** (Dinic, retired
  igraph seat, read-only — s-t has no per-entity metric shape; ROUTING
  switch + budget guard), minimum spanning tree — archived (deferred
  need wired; Kruskal toy oracle), Yen K-shortest — **un-deferred
  2026-09-24** (new capability, opt-in; ROUTING switch + budget guard).
  Full 27-public csgraph surface accounting added as
  the umbrella's §7 footnotes (owned-elsewhere / converters /
  no-consumer-recorded dispositions, nothing else silently owed).

- **Served-vs-stamp convention fork (closeness/harmonic) — RESOLVED
  2026-10-02 (Onager C++ source verification)**: the fork is the
  `listed_on_index` edge set, not a formula or constant. Verification
  (appendix to `doc/improvements/archive/graph/scipy_routing_dispatch.md`): (a) Onager C++/Rust source
  (`onager/src/algorithms/centrality.rs`, `external/graphina/src/centrality/`)
  has **no global normalization** — closeness = improved
  Wasserman-Faust `(reachable/sum)*(reachable/(n-1))`, harmonic =
  `sum(1/d)`, both pass through FFI unchanged; (b) stored
  `graph_analytics` `closeness_centrality`/`harmonic_centrality` are
  **bit-exact** with the lane on the all-edges projection
  (`max_abs = 0.00e+00`, n=22054 nodes, 1734 contract names); (c) the
  "underived ×1.2383 constant" is **disproven** — the all-edges/ex-index
  ratio is not constant (closeness p10/med/p90: 0.8829/0.9133/0.9538;
  harmonic: 1.1077/1.1419/1.1795) and ranks stay nearly identical
  (ρ ≥ 0.9596). Resolution: keep the current posture
  (scipy_routing_dispatch §2) — serve the all-edges lane, preserve
  bit-exact. The "black-box C++ normalization" and "underived constant"
  theories were refuted by source inspection; reconciling by unifying
  the projection would shift served absolutes (-4-12% closeness,
  -10-18% harmonic on disconnected components) with no net gain since
  rankings are preserved.

- **graph_perf_l1 S1 write-surface call — RESOLVED 2026-09-23**: the
  restricted-source lane writes `graph_analytics` (the contract's own
  home; write_analytics UPSERT, --apply); the v_centrality_* stamp
  keeps full-walk semantics. Implementation:
  `helpers/graph/scipy_bridge.py` (scipy route (d), owned by
  scipy_graph_bridge.md; 2.9 s wall both metrics; contract alive in
  research.db, mirrored at
  snapshots/parquet/sqlite/). See the §3 surface note in
  graph_perf_l1_bfs_scale.md. (An earlier "orphan parquet" reading of
  the sqlite mirror was wrong — corrected 2026-09-23.)

- **§G2 word-overlap alias guard + §G3 discard-persistence noise gate** — executed as `doc/improvements/archive/graph/word_overlap_alias_guard.md` (completed.md #218, 2026-09-09).

- **HGX D10 prediction/motifs lanes** (D4 EXECUTED as hgx_first_scaling
  #241: h_edge/h_incidence cache + SQL consumers + hyper API —
  remaining trigger: hyper-native link prediction, unstarted). D10
  **LANDED 2026-10-22 (S1):** `helpers/graph/hyper_predict.py` — three
  score lanes (jaccard, s-walk s∈{2,3}, stationary-π via
  RW_stationary_state), star-native only (no clique projection), per
  prediction-grade family (edition/theme/sector/industry/sub_sector/
  org_membership), report-first, `--apply` (D13 opt-in) writes the
  `hyper_link_prediction` metric. `event`/`jv`/`country` stay opt-in.
  stays gated on incidence density (measured 2026-09-15: group 8/k=3.8,
  jv 6/k=2.0; hyper_edges 533 live).
  [2026-10-19 RESTATEMENT (hyperedge_density_dq): the two-family gate
  (group/jv) watched two of the LEAST-dense families while edition/
  industry/sub_sector/sector/theme carried the real higher-order mass —
  and the event family (the largest) was structurally capped at k=2
  until `event_participants` (v16) landed. The gate is now the
  per-family density TABLE below, re-measured live after the S1
  event-family convergence (stale/sub-threshold event labels pruned,
  1,252 → 1,133). D10 reopens PER-FAMILY, never globally:

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
    | **total** | **1,708** | **55** | **1,173** | **478** (28.0%) |

  mean density 5.93. One-query re-measure:
  `SELECT edge_type, COUNT(*) edges, SUM(k=1) k1, SUM(k=2) k2,
  SUM(k>=3) kge3 FROM (SELECT he.id, he.edge_type, COUNT(hi.entity_name)
  k FROM hyper_edges he LEFT JOIN hyper_incidences hi ON
  hi.edge_id=he.id GROUP BY he.id) GROUP BY edge_type;`
  Reopen triggers, per family: edition/industry/sub_sector/sector/theme
  are prediction-grade NOW (k≥3 ≥ 60% of family edges) — the blocker
  for D10 is the engine decision (hgx-native predictor vs D15-class),
  not density. event/jv reopen when participant data arrives (speakers
  cannot join events upstream — measured 0/1,477); group re-measures on
  every same_group derive. Register note: the old "edge-touched n ~10×
  above 1,722" condition was the longest_chains S3 cap baseline
  (`archive/graph/longest_chains_cap_policy.md`), NOT a D10 density
  condition — two gates had been merged in the scratch register;
  density is the only D10 gate.]
  D11 residue: RESOLVED 2026-09-19
  by the subsector authoring pass (completed.md #258,
  `archive/graph/subsector_authoring_pass.md`) — 16 buckets → 8
  cleared by SUB_SECTOR_ALIASES additions (incl. the Gaming leaf) +
  8 covered by per-note authored `subsector:` (81 notes; every member
  company resolved, `unmapped_authored` empty). The label-level
  worklist detector still lists the 8 authored-covered labels — they
  re-ask on future label drift by design (S4 reopen intent).

- **HGX unlock shelf — 6 trigger-gated capabilities** (D10 archive §4
  claims these "land in pending.md"; they never did — recorded here
  2026-10-28 with first probe verdicts; full detail + do-not-re-probe
  gates: `archive/graph/hgx_unlock_shelf_probe.md` #451). All six
  verified present in
  hypergraphx 1.8.0 (`prediction` module is NOT — §3 first-party lane
  stands). Entry-point corrections vs the archive table:
  `compute_directed_motifs` → `hypergraphx.motifs.directed_motifs`;
  `calc_HO_shortest_paths` / `calc_prop_true_dyad_paths_per_spl` /
  `calc_sizes_redundancies_of_shortest_paths` →
  `hypergraphx.measures.shortest_paths` (not s_centralities);
  `core_periphery` → `hypergraphx.communities.core_periphery.model`.

  1. **Motifs census** (`compute_motifs`, `directed_motifs`) — PROBED
     2026-10-28, **NO-GO on current data**: order-3 census (runs=10,
     seed=42) over every k≥3 family returns ONLY the trivial
     within-edge triple (industry 12, sub_sector 9, org_membership 7,
     group 2, sector 1, country 1; edition 0, theme 0) — zero
     multi-edge intersection motifs, norm_delta 0.0 throughout (no
     enrichment vs config model). Cause: node pools (270–1,211) are
     large relative to edge counts (4–85) → cross-edge 3-node overlaps
     essentially never occur. REOPEN trigger sharpened: motif signal
     requires cross-edge node-overlap at motif order — re-probe when a
     family gains dense shared-membership edges (e.g. event family
     once participant data arrives, or post-merger same_group churn).
  2. **Reducibility** (`layer_reducibility`) — MEASURED 2026-10-28,
     one-shot QA recorded (not wired): editions reducible at layer 3
     (0.637) / layer 4 (0.485) — a majority of edition structure is
     explainable at order ≤4; industry/sub_sector/sector/org_membership
     ≈ 0.0 at layers 2–6 — genuinely higher-order. This VALIDATES the
     s=2 default of the landed `s_betweenness`/`s_closeness` lanes on
     the non-edition families (irreducible → s-walk is the right lens)
     and flags editions as the family where dyadic/lower-order
     explanations partially suffice. Re-run per-family on major store
     growth; no production lane justified (one query, no recurring
     consumer).
  3. **Multi-order Laplacian** (`compute_multiorder_laplacian`,
     `are_commuting`) — still gated: needs a named spectral-feature
     consumer (entity-clustering QA candidate; none exists).
  4. **s-walk shortest paths** (`calc_HO_shortest_paths` et al.) —
     still gated: first UI/API path-explanation ask that traverses
     hyperedges. Directed B/F-hyperpath (Gallo) remains out of HGX;
     the directed lane stays plain-SQL BFS (`graph_design.md` D9).
  5. **RW density** (`random_walk_density`, sig `(HG, s: ndarray,
     time) -> list`) — still gated: needs an information-diffusion
     analysis question (edition/news spread). `RW_stationary_state`
     itself is already landed as `ho_pagerank` (S12/S14).
  6. **Community lanes 2–4** (`core_periphery.model.core_periphery`,
     `hyperlink_comm.hyperlink_communities`, `hypergraph_mt.model`,
     `hy_sc.model`; hy_mmsbm landed in `hyper_communities.py`) — still
     gated: hy-MMSBM block-disagreement QA or a second-opinion metric
     validation ask.

- **Convo gemma embedder upgrade** (deferred #442,
  `archive/tooling/convo_gemma_adoption.md`). S0 bank + hardened verdict
  on record: pv_gemma 24/57 vs pv_granite 14/57 (+71%) but bm25 alone
  22/57 beats both hybrids — the hybrid is bm25-bound, so the fusion
  parameters, not the embedder, are the bottleneck; the 27–51 h re-embed
  wall (gemma ≈ 288 tok/s vs granite ≈ 1,265 tok/s measured) waits
  behind fusion repair. **FUSION REPAIRED 2026-10-27** (#449):
  bm25-anchored fusion (`_anchored_fusion`, FTS top-5 kept as ranks 1–5,
  fused order fills) lands 25/57 granite / 27/57 gemma on the frozen
  bank vs the 22/57 floor — trigger 1 FIRED and closed with granite
  production vectors, no re-embed. The embedder decision (gemma's +2
  under the anchor) stays parked behind the 27–51 h wall.
  **Reopen triggers (embedder):** a semantic-paraphrase recall miss the
  anchored fusion demonstrably can't cover; faster gemma serving (batch
  endpoint/quantization) bringing the wall under ~1 day.

- **DuckDB 2.0 async Parquet writes for `make snapshot`** (parked
  2026-10-27, operator ruling). The 58-table mirror measures 2.6–3.7 s
  at live scale (P2.2 review) — async I/O attacks a non-pain-term, and
  the async-write API surface is new in 2.0. **Revisit trigger:** the
  mirror leg exceeding its perf budget, or the mirror growing >10×.

- **Re-evaluate HNSW index macros** (deferred N5 item 5). UNBLOCKED
  then DECLINED on quality+perf — full-scale trial 2026-09-22, DuckDB
  1.5.5, vss binary unchanged since 2026-08-09. Capability: the old
  "empty-signature binder errors" were scalar-style mis-calls —
  `vss_match` is a table MACRO `(table, col, query, k, metric)`,
  `pragma_hnsw_index_info()` a table function, `hnsw_index_scan` is
  internal-by-design; `CREATE INDEX … USING HNSW` works on-disk
  (`hnsw_enable_experimental_persistence=true`) and survives reopen,
  and the planner DOES route `ORDER BY array_distance … LIMIT k`
  through `HNSW_INDEX_SCAN` (the 2026-08 "DuckDB does not auto-use"
  claim is falsified). BUT: `USING HNSW (emb COSINE)` still fails
  (`opclass not supported` — the original embed_store_consolidation
  blocker, unchanged), the index carries +75% db size (37→65 MB on
  v_note_embeddings), and recall is broken on this build — exact-vs-ANN
  overlap@10 only ~57% on the real 16.5k corpus, 41% @50k and 35% @100k
  synthetic, flat across `hnsw_ef_search` 50→400. Speed: no win —
  16.5k: brute l2 3.7 ms ≈ HNSW 3.9 ms (production brute cosine 40 ms —
  the cosine-vs-l2 gap is the real cost; vectors are unit-norm so l2
  ordering ≡ cosine); 50k: 9.7 ≈ 9.7 ms; 100k: 12.3 vs 9.7 ms;
  200k: 10.7 vs 11.1 ms; build time grows 5→61→144→311 s (db 710 MB
  at 200k, +75% over vectors). Actionable without any index: ordering
  `similar_notes`-style queries by `array_distance` instead of
  `array_cosine_similarity` is ~11× on the same exact results (unit
  norm). Brute-force stays the shipped path; revisit when a vss build
  fixes the COSINE opclass or scan recall, or vectors approach 10⁶ —
  plus the quarterly `make update-extensions` re-check
  (graph_design.md §18.5/§5.4). The actionable swap EXECUTED and
  archived 2026-09-22 as completed.md #266
  (`archive/graph/note_knn_distance_ranking.md`) — with the 11× figure
  corrected to ~1.2–1.3× (the trial's 3.7 ms was HNSW-index-routed;
  true brute l2 is 26–27 ms vs cosine 35.5 ms).
  **Re-checked 2026-10-13 (DuckDB 1.5.6, vss build `b833341` unchanged
  since 2026-09-29): `CREATE INDEX … USING HNSW (emb cosine)` still
  raises `Not implemented Error: Index with opclass not supported yet!`
  — upstream blocker live, no change.**
  **Re-checked 2026-10-24 (DuckDB 2.0.0, vss `3685131eac` after `make
  update-extensions`): the grammar CHANGED — every `col opclass` suffix
  is now a parser error; the index builds bare as `USING HNSW (emb)`
  and ONLY on fixed-size `FLOAT[N]` arrays (`FLOAT[]` lists refuse:
  "index keys must be of type FLOAT[N]"). The 1.5.x opclass blocker is
  obsolete by grammar removal. Measured on 5k×32 unit-norm synthetic:
  recall@10 mean 98% (min 90%) via `vss_match(…, 'l2sq')` — the 1.5.6
  ~57% recall failure is FIXED; build 0.25 s. BUT the planner no longer
  routes `ORDER BY array_distance … LIMIT k` through `HNSW_INDEX_SCAN`
  (it did on 1.5.5) — the index is reachable only via explicit
  `vss_match`/`hnsw_index_scan`, and vss_match (4.69 ms) still loses to
  brute `array_distance` (3.09 ms) at this scale. Production adoption
  would additionally need a `FLOAT[512]` type migration (embeds are
  `FLOAT[]` lists). Verdict: brute-force stays shipped; the decision
  stands. Re-trial trigger unchanged (vectors → 10⁶, or a probe shape
  where brute hurts) + next quarterly re-check.**
  **Re-checked 2026-10-27 (DuckDB 2.0.0, vss `3685131eac`, 5k×512
  FLOAT[512]): the 2026-10-24 note was WRONG about planner routing.
  The planner NOW routes `ORDER BY array_distance(emb, CAST(? AS
  FLOAT[512])) LIMIT 10` through `HNSW_INDEX_SCAN` (16ms) —
  contradicting the "planner no longer routes" claim. EXPLAIN shows
  Hnsw Index Scan. HOWEVER: `ORDER BY array_cosine_similarity(emb,
  CAST(? AS FLOAT[512]))` uses brute force (Seq Scan + Top N, 11ms) —
  no HNSW route for cosine. `vss_match` is a table macro requiring a
  query table, not a parameter. Production adoption path unchanged:
  needs `FLOAT[512]` migration (current embeddings are `FLOAT[]` lists
  in `note_search_vec` etc.), and the query path must use
  `array_distance` (not cosine) to hit the index. Cosine-vs-L2 gap
  remains the real cost (unit-norm vectors so L2 ordering ≡ cosine,
  but cosine queries don't use HNSW).**
  **Re-checked 2026-10-27 (2.0.0, REAL scale — convo store 168,479×384,
  cosine index via `WITH (metric='cosine')`):** routing confirmed at
  production dims (l2 AND cosine indexes both route
  `ORDER BY array_distance` — cosine-similarity DESC does not);
  recall@10 = 100% vs exact (the 1.5.5 ~57% failure is dead). BUT the
  2.0 ANN plan never beats brute at production scale: index probe runs
  as a Hnsw-Index-Scan arm in a SEMI JOIN with a Top-N re-sort whose
  overhead scales with n — ANN/brute ms: 9.9/9.0 @5k, 10.6/10.7 @10k,
  3.3/19.0 @20k (noisy run), 45.1/40.9 @50k, 81.8/79.6 @100k,
  171.7/116.6 @168k; `hnsw_ef_search` 200/400 only raises ANN cost
  (230 ms) with recall already saturated. Index size +212% (790.6 vs
  253.2 MB @168k), build 41.2 s; `hnsw_index_scan` not user-callable;
  `vss_match`/`vss_join` = brute-force macros (array ops + min_by).
  **Verdict: recall fixed, routing live, plan overhead real — HNSW
  stays deferred (brute cosine remains the shipped path). Next-check
  on a vss build with an index-only top-k plan (no semi-join re-sort)
  or vectors → 10⁶.**
- **MAX engine = the viable future small-batch GEMM route** (recorded
  2026-10-26, Mojo-vs-Rust review; no execution yet). When embedding
  scale hits the trigger below, the small-batch rung of the kernel
  selector is the `max` PYTHON PACKAGE (pip; `max.graph`/`max.engine`,
  `Inner_matmul_default`) — NOT Mojo-the-language. Measured edge
  (doc/local/mojo/mojo_pilot.md §Scale, same box): n=8 query batches
  beat OpenBLAS on every shape ≥7.6M elements (2.5×/2.2×/1.7×/2.4× at
  7.6M/10M/12.8M/100M); numpy sgemm wins n=64 at EVERY shape incl.
  K=768 (4.50 vs 5.97 ms) — the rung is strictly n≈2–16. Banked traps:
  inputs must be 32B-aligned (vmovaps SEGV, root-caused 2026-09-02);
  `InferenceSession(num_threads=…)` hard-crashes for any value — leave
  default. Mojo itself stays retired (bench-side only, #437; the
  std.python bridge holds the GIL — no in-process kernel boundary
  exists, so Mojo cannot follow the PyO3/cosine_knn model). Selector
  ladder for the trigger day: n=1–16 big-matrix → max package; n≥64 →
  numpy sgemm; ≥~500k rows → numba prange (Phase C 1.94× numpy, recall
  1.000); custom zero-copy logic → Rust/PyO3; set-SQL → DuckDB.
  Trigger: embeddings ~10⁵+ (current 1,209, DuckDB brute ~3 ms) or any
  measured batch-GEMM wall in production.

- **Wrap `onager_ctr_personalized_pagerank`** (deferred N5 item 6). Onager bug:
  personalisation column ignored, restart node hardcoded to `node_id 1`, and it
  requires a weight column of type `BIGINT`; variants A/B produce identical
  output. Documented at `helpers/graph/onager.py:836` and graph_design.md §5.5.
  Do not wrap until a future Onager build honours the personalisation vector.
  (2026-10-24 reinstatement — `archive/graph/onager_vendored_source_build.md`,
  completed.md #434 — changed NOTHING here: v0.5.0 is the 2.0.0 API port, the
  personalised-pagerank algorithm is untouched by both the vendored build and
  the community dist artifact.)
  Even a fixed build does not unblock the lane: the scipy exact-solve
  measured the graph-structure degeneracy (PPR ≈ 1-hop ego rank on the
  leaf-heavy graph) — see `archive/graph/scipy_personalized_pagerank.md` §7.
  Re-probed 2026-09-22 (same build): both bugs live — pers→node2×100 vs
  pers→node4×100 byte-identical (neither equals plain pagerank, so restart is
  hardcoded to node 1), and any projection without `node_id 1` errors
  "Personalization node 1 not found". **Re-probed 2026-10-13 on the
  rebuilt extension (build `ab472a7`, installed 2026-10-05): still live**
  — pers on node2 vs node4 byte-identical (peak 0.3022 at node 1), and the
  projection without node 1 raises `Invalid Input Error: Personalized
  PageRank failed: Invalid argument: Personalization node 1 not found in
  graph`. Contract note: named args required,
  weight col must be `BIGINT`, pers col `DOUBLE` strictly positive (zeros
  rejected). Wrap path + projection ladder tracked in
  `archive/graph/pagerank_graph_enhancements.md` (S5 watch; proposal
  executed 2026-09-22 as completed.md #270 — S5 stayed parked).

- **Security Phase 4 (deploy-time; app confirmed NOT deployed 2026-08-17)**
  (private security review under doc/local, untracked;
  Phases 1/1b/2/3/5 DONE — completed.md #116/#117; SEC-9 closed: key
  revoked, GitHub never had the blobs — standing caution: never
  `git push --mirror` / push `main.stgit`; keep running `make secret-scan`
  after big pushes). Activates only if the Flask app is ever deployed
  publicly: dev-default `FLASK_HOST=127.0.0.1`; auth/shared-secret in
  front of `POST /api/graph/refresh`; `uv lock`. Re-verified 2026-09-18 by
  the security-coverage arc (completed.md #247b): SEC-5 re-checked three
  separate times and still deploy-gated, and the arc added two more
  unauthenticated findings that strengthen this gate — AVAIL-1 (53 s
  quadratic GET, remediated #249), AVAIL-2 (cubic metric regex, remediated
  #250) and CONC-1 (shared connection returning wrong rows, remediated
  #251). All three fix proposals are executed and archived under
  `archive/security/`. The coverage claim itself is
  now machine-checked: `helpers/validators/coverage_ledger.py check`.
  Follow-up EXECUTED 2026-09-20 (completed.md #256,
  `archive/testing/test_gap_closure.md`): the availability class now has
  hot-route budgets + the first perf legs to drive the Flask request path,
  the worker/`BrokenProcessPool` fallback is tested, and the last
  quadratic regex has a fuzz guard. `make qa` 10/10.

- **B2 relation sidecars — IMPLEMENTED 2026-10-03** (tech-avenues leftover,
  `archive/tooling/tech_avenues.md` §3): per-relation YAML sidecars in
  `findata/Misc/_relations/<source>-<edge_type>-<target>.yaml` with
  edge_type, counterparties, as_of, confidence, provenance. Sidecar promotion
  is wired into `extract_relations` (B2 lane, `promote:sidecar` source_ref) —
  idempotent re-ingestion of human-accepted edges. Closed the B2 triage queue
  in one pass (8 prose rows: 1 discard to the noise gate, 7 accepts -> 7
  edges in `graph_edges`/`graph.duckdb`), including a fix for
  `triage_pending_relations.py::_parse_accept` (reverse-capture rows with an
  explicit accept target produced a self-loop; now writes the mention as
  source with the accepted target).

- **OpenViking context-server pilot DEFERRED** (2026-08-20; gap targeted real semantic embeddings — closed in-house #141, bge-small-en). Revive only for the context-server differentiators (L0/L1 hierarchy,
  automatic memory extraction, retrieval traces); the labeled eval set
  transfers verbatim. Known-if-revived: default embedder is Chinese-tuned
  with a one-model registry (swap needs `model_path` + explicit `dimension` +
  full rebuild — upstream issue #1523); `vlm` key is `api_base`.

- **P2.2 incremental DuckDB materialization — RE-EVALUATED 2026-10-15,
  S1/S2 + rebuild-on-apply shipped; trigger criterion retired**
  (`archive/graph/graph_pending.md`; `graph_rebuild_fast_path` S1/S2,
  `helpers/graph/query.py`, `tests/test_graph_rebuild_fast_path.py` 12/12).
  Remaining (ii)/(iii) are measurement-gated optimization only.
  — row trigger FIRED 2026-09-05 (17,323 graph_edges > 10k) but the deferred
  reason (rebuild cost) is not binding: full rebuild measures 3.0 s against
  vault_scaling's 5 s DuckDB budget. Scale strategy is owned by
  `archive/graph/vault_scaling.md` (#204); re-evaluate when its T1 fires
  (~1M doubled rows; currently 34K) or measured rebuild > 5 s.
  **Re-decided 2026-10-01 on measurement, not on the 5 s line** (that line is
  the T2 ladder LABEL, and the ladder's own §2 answers the over-budget band
  with a recorded waiver + ART bridge, not with this item). Scratch-isolated
  rebuild cost, R = `e_all_und` doubled rows: **T(R) ≈ 2.28 s + 3.0 µs·R**
  (measured R=0 → 2.28 s, R=115,030 live → 2.62 s, R=460,120 → 3.72 s), so at
  live scale the rebuild is **~87% fixed statement overhead** (0.29 s ext
  INSTALL/LOAD, 0.21 s for 56 `DROP TABLE IF EXISTS`, ~1.70 s planning +
  executing ~50 CTAS) and only ~13% per-row. P2.2's row-level diff therefore
  targets the small term while adding partial-application risk against the
  single file-level `_build_meta.generation` stamp read by 23 `connect()`
  callers, `csr.py:298` and snapshot verification. **HOLD in favour of:**
  (ii) fold the 12 `EDGE_REGISTRY` CTAS + `e_all_und`/`e_dir` into one
  `edge_type`-discriminated scan of `fin.graph_edges` and drop the
  duplicated `v_centrality_*` DROPs (aims at the 1.70 s);
  (iii) table-level dirty tracking via per-input hashes in `_build_meta`,
  rebuilding only dirty tables — scales with the *dominant* term and keeps
  per-table drop-and-rebuild correctness; (C) call `rebuild()` from
  `derive-relations --apply` / `parse_newsletter --apply` and make the
  stale-cache path fail fast, because **no ingest lane calls `rebuild()`
  today** so the cost is paid inline by the first graph query after any
  generation bump (and `read_only=True` callers cannot take the RO fast path
  when stale — they serialise on `<cache>.build.lock`). Also note the record
  itself is stale: `PROPERTY GRAPH` was retired (#92) and the build is ~118
  statements, not "34 tables". Re-anchor + in-repo probe filed as
  `archive/graph/graph_rebuild_scaling_probe.md` (DEFERRED); the ladder's materialize
  column is re-anchored in `archive/graph/vault_scaling.md`.

  - **graph_db optimization — ALL ISSUES EXECUTED 2026-09-10**
    (`archive/graph/graph_db_optimization.md`, completed.md #222):
    closeness_centrality result cache (repeat calls 1.62s→0.001s),
    graph_metrics connectivity short-circuit (disconnected 1.2s→0.21s),
    with_onager_connection batch context (14→7 materialisations per
    --all run), Issue 4 `_splice_sources` (source_note_index now warms
    _TITLE_MEMO — ~1,400 per-run source re-reads eliminated; derive_insights
    CLI 3.33s→2.75s measured).

## Executed — records only (kept for audit; details in completed.md / archive)

- **Technology avenues** — EXECUTED & ARCHIVED 2026-08-17→18
  (`archive/tooling/tech_avenues.txt`): A1 sqlite-vec KNN #124, B1 JSON-Schema
  contract #125, C1 context packs #127, C2 link-prediction + A3 parquet
  analytics #129, A4 PRAGMA-optimize #128. Parked: D MCP server (§5 seam
  sketch; re-open on operator request). Blocked: C4 Kùzu (upstream archived).
  Dropped: Obsidian-UI. Anti-recs standing: LanceDB/third vector store,
  Turso, pgvector, YAML anchors.
- **C3 temporal analytics** — DONE 2026-08-25 (#150;
  `archive/tooling/temporal_analytics.md`): `make analytics REPORT=temporal`.
- **OKF read-side live propagation** — DONE 2026-08-25. N1: 318 notes carry
  `[^chatter-*]` footnotes (commit 4da573b). N3: first `okf_verify.py`
  `verified[]` stamps operator-recorded. No further work.
- **Parallel cold embed** — DONE 2026-08-29 (#173;
  `archive/tooling/parallel_cold_embed.md`): cold note_search 16m13s → 6m01s
  (2.70×); company ~11–15 min → 4m46s. Its §7 deferred-at-scale record holds
  the remaining deferred levers with revisit triggers (all unmet as of
  2026-09-05). Incremental-snapshot item closed by #174.

- **Ontology governance #244 deferred** (2026-09-17; (c) resolved
  2026-09-19) — (a) Mapping record glossary note (EvoOntology Mapping:
  term→table/column/filter/grain) — trigger: a metrics Q&A lane
  proposal; (b) LLM-judge protocol 2 for the change gate (`"llm_judge"`
  slot reserved in the report schema, aggregation from EvoOntology
  evaluation.py:63) — trigger: an LLM-API posture (terrain C4 revival
  condition); (c) DONE 2026-09-19: `helpers/core/vocab.py` exports
  MATCH_TYPE_VALUES / IDENTIFIER_TYPE_VALUES / SOURCE_TIER_VALUES /
  CONCEPT_STATUS_VALUES; the three DDL CHECK sites (seed_concepts,
  backfill_identifiers, backfill_row_provenance) build their clauses
  from it, and all four rosters are now gated in
  `roster_registry_sources()` against the master doc (documented-only
  exemption closed).
- **Perf graph-leg budgets post-ingest** — RESOLVED 2026-09-19, root
  cause found and fixed (not corpus growth): a stray empty
  `memory/graph.db` (4KB, zero tables) poisoned `_is_warm`'s
  colocated-sibling guess, so every graph CLI connect unlinked and fully
  rebuilt the ~35MB cache (~2s) before computing — eigenvector's native
  compute is ~0.04s, its entire overage was the rebuild. Fix in
  `helpers/graph/query.py` (`_is_warm`/`_probe_note_embed_state` take
  the real `db_path` from `connect()`; legacy sibling guess kept only
  for direct calls) + `test_graph_disk.py` regression test. Operator
  waivers (2.0s → 6.0s) removed, original budgets restored: 22/22 at
  eigenvector 0.37s, link_prediction 1.34s, closeness 2.97s,
  betweenness 2.23s. The same arc also excluded `listed_on_index`
  from the Onager structural projections (completed.md #254) and
  filed the persistent-cache follow-up — EXECUTED 2026-09-21 as
  completed.md #259
  (`archive/graph/graph_centrality_persistent_cache.md`): ten
  `v_centrality_*` tables stamped at rebuild (schema 17), warm CLI
  reads 0.35–0.45s, centrality perf legs now run `--compute` so
  budgets keep measuring compute. SUPERSEDED-in-part 2026-09-23 by
  completed.md #271 (centrality_rebuild_contract): the rebuild is
  data-only and DROPS the tables (BFS family = minutes at 56k edges);
  stamping is the explicit stamp-centrality lane. The advisory-side twin
  (live-invariants wall 95–226s) resolved 2026-09-20 —
  completed.md #255 (`archive/tooling/advisory_gate_perf_reports.md`);
  original state below:
  eigenvector 0.37s, link_prediction 1.34s, closeness 2.97s,
  betweenness 2.23s. FOLLOW-UP FILED 2026-09-23:
  `archive/graph/graph_perf_l1_bfs_scale.md` — first perf run at VIGIL
  scale blew the three cold `--compute` legs 12-31x (closeness 156.8s,
  link_prediction 57.0s, betweenness 48.9s); budgets deliberately left
  RED as the tracker; slices L1a source-restricted closeness/harmonic,
  L1b 2-core betweenness, L1c link-pred candidate pruning, snapshot
  manifest ephemerality, gate re-tightening. CLOSED 2026-09-23
  (completed.md #277 — all legs green: centrality 2.66/5.0,
  betweenness 3.67/8.0, link-prediction 2.08/4.0; perf gate 23/23).
- **test_fuzz_shortest_path leaks a 176MB sp.db tempdir per run** —
  DONE 2026-09-17 (#245; `archive/testing/tmpdir_sanitization.md`): the
  tmpdir-hygiene arc — fixture onto pytest basetemp with a vacuumed copy,
  bench/pdf/TUI-log scratch lifecycles, gate-front `make tmp-sweep`
  (owner + 24 h + prefix guards, dry-run default).
- **ty-tests warnings** — DONE 2026-09-19: all 16 warnings cleared to
  zero (RichLog-typed `#preview` queries + honest drive() return
  annotations in test_search_tui, fetchone() None guards in
  test_snapshot/test_search_tui, importlib spec asserts in
  test_mca_cin_resolve/test_seed_nic2008, db_filter_rows row typing).
  `make types-tests` now reports zero diagnostics.
- **`listed_on_index` membership edge** (deferred N5 item 7) — DONE
  2026-09-19 (#253, `archive/graph/index_membership_fill.md`): the
  09-09 re-deferral's revisit trigger — "a real index-constituent data
  source appears" — fired (NSE constituent CSVs; the `index_membership`
  frontmatter key stays dropped, the data comes from the sidecar
  instead). `make refresh-indices` lands 57 indices / 6,661 constituent
  rows; `derive_indices` projects fileless `index` entities + 6,615
  dyadic `listed_on_index` edges, cache v16. The induced structural
  noise (36% of all edges, 57 star hubs) is kept out of metrics by #254
(`EDGE_TYPES_EXCLUDED_FROM_CENTRALITY` + `_CHAIN_FORBIDDEN`).   Closes
   listed_on_index.
- **Dirty-gate the remaining static_checks legs** — EXECUTED
  2026-09-21 (archived to `archive/tooling/gate_latency_followups.md`,
  completed.md #262). Syntax + chokepoint + data_format
  gated via `_DIRTY_PY_SCOPE` (3.35 s → ms on small-dirty trees);
  end-to-end `--dirty` 0.72 s vs `--full` 4.97 s best-of-3, identical
  verdicts. Proposal lifecycle stays full (0.01 s); SQLite/ledger/JS
  legs untouched (no file-set semantics or no prize).
- **Kill the enumeration floor: cached corpus file list** —
  REDIRECTED 2026-09-21, no persistent cache built (recorded in
  completed.md #262, `archive/tooling/gate_latency_followups.md`). Scope-driven
  iteration (legs iterate live scope members, zero rglob when scoped)
  captures the prize (~0.22 s) with zero staleness surface; a
  cross-run cache would save ~10 ms more for an invalidation-correctness
  burden — declined unless a future profile says otherwise. Known
  divergence class: gitignored files (rglob sees them, porcelain
  doesn't); `--full` remains the arbiter.
- **Trim gate-process import costs** — DONE 2026-09-21 (recorded in
  completed.md #262, `archive/tooling/gate_latency_followups.md`). Slice 1
  (empty-scope early return, ≈77 ms/run) landed earlier; slice 2
  resolved by deletion, not lazy-loading: the `helpers.core.corpus`
  import was dead (imported, never referenced) — removed, pinned by a
  fresh-interpreter test. Honest correction: wall effect ≈ nil
  (0.076 → ~0.070 s; interpreter startup dominates) — kept as
  hygiene, not a latency win.
- **fastjsonschema split-track — EXECUTED 2026-09-21 (archived to
  `archive/tooling/fastjsonschema_split_track.md`, completed.md
  #261).** Soundness: 1456/1456 corpus verdict agreement (0 FP/FN),
  28/28 mutations both-flagged. Gate defaults to the fast engine
  (`--strict` opts into jsonschema); schema leg 0.93 → 0.40 s
  best-of-3, `--full` end-to-end 7.17 → 6.15 s; maint-full keeps
  blocking `static-checks --full` + new advisory `--report` twin.
  350 tests green, deptry clean, pin + `uv lock` landed.

- **resolver/extractor hardening — DEFERRED 2026-10-10 → EXECUTED 2026-10-15**
  (`archive/tooling/resolver_extractor_hardening.md` §D, completed.md
  #378): the four in-run stop-gaps (cross-check dims-match + min_sim
  0.80, extractor tuple crash, alias precedence) landed first;
  **S1–S4 executed 2026-10-15** (canonical-fold resolution, tie-break
  defers, section-head noise gate, stub chatter removal — 4 hand-aliases
  retired from `relation_aliases.json`) and **S5–S8 executed same day**
  (`archive/pipeline/resolver_extractor_hardening.md`, completed.md
  #396: sector precedence, worklist routing, cross-check handle
  release, listed-contradiction hints). **AUDIT REVISIT (2026-10-15):**
  S6 + S8 detection rewritten same day (word-boundary name resolution,
  analyst-marker attribution, company-note headings never route,
  ticker-less entities surface as manual-check rows; 8 tests).
  Backfill-script bugs (UPDATE keying, exit code, ruff) fixed same day
  — see #395. S7's commit() remains a hypothesis, not the identified
  root cause. Original trigger: the next alias-file addition for a name
  class §S1 covers, or any wrong-company edge traced to a tie-break.
  Checked 2026-10-15: alias file SHRANK by 4 net entries (the S1
  classes now covered structurally).
- **resolver extractor S9 — analyst-marker calibration — DEFERRED
  2026-10-15** (`archive/pipeline/resolver_extractor_hardening.md`
  S9): S6's `_ANALYST_MARKER_RE` is a first cut (8 phrases); uncovered
  attribution styles ("in a note dated…", "street expects", speaker
  title lines, channel checks) fail safe — the section stays on the
  company worklist. **REVISIT TRIGGER: the next real parse run where an
  analyst-voiced section (JM-Financial shape) fails to route, or a
  manual spot-check of N editions.** Calibration acceptance: marker
  additions test-pinned, zero management sections flagged in the repro
  set.
