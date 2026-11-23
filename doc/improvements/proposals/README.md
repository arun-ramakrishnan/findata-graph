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

- `test_coverage_gap_sweep_nov24` (testing/, filed 2026-11-24) — the
  post-11-01 re-pin: S1 Rust/Mojo test legs into qa, S2 Rust parity +
  `embed_cache` boundary, S3 unit pins (`maint_timing`,
  `algorithm_wrappers`, HGX lane disposition), S4 fuzz, S5 integration
  chains, S6 perf legs, S7 Mojo unit tests. Supersedes the snapshot in
  #463 for everything that landed after 2026-11-01.

- *(archived 2026-11-23)* — `misc_fix_gate_arc_maintenance` (tooling/,
  filed + executed 2026-11-23, completed.md #566 — the post-#565
  maintenance sweep in stgit patch `misc_fix`: gate-query index heal
  (constraint wipe, NULL run_id era), hyper_derive 18.34 → 4.58 s batched
  engine-neutral SQL (budget 12.0), the DuckDB 2 GB + TMPDIR cap as a law
  across all 115 connect sites with a validator + conftest guard, the
  stamp-centrality hop-histogram worker (70 → 46 s jobs=4), the script-index
  statement-list walk (pending.md :58 CLOSED), and the master_query notes
  mapping fix).

- *(archived 2026-11-21)* — `duckdb_build_route` (database/, filed +
  executed 2026-11-21, completed.md #552 — SQLite retirement BUILD route:
  P1 cascade rewrites under the zero-FK canonical store, P2 verified
  native DuckDB FTS, P3 integrity orphan checks, P4 frozen-store deletion
  + backup-coverage audit, P5 corpus/md_lint cache moves).
- *(archived 2026-11-21)* — `parquet_snapshot_duckdb_reader` (database/,
  filed + executed 2026-11-21, completed.md #553 — S1 selector-aware house
  connect + dual-engine writer helpers; S2–S5 parquet snapshot axis
  engine-aware export/verify/defaults with pinned restore contract).
- *(archived 2026-11-20)* — `hgx_unlock_lanes_and_emeans` (graph/, filed +
  executed 2026-11-20, completed.md #549 — HGX unlock: `hypergraphx[ml]`
  (sklearn 1.9.1) + faiss-cpu 1.16.0 installed; HySC / core-periphery /
  HypergraphMT community lanes; multi-order spectral consumer
  `hyper_spectral_cluster.py` (#8) with e-means default; reducibility
  representative layers adopted as the `--orders reducible` default (#6);
  hyperlink_communities parked upstream (pickle None.hlcd), motifs census
  NO-GO, s-walk repo-native).
- *(archived 2026-11-20)* — `findata_misc_json_git_inclusion` (tooling/, filed
  + executed 2026-11-20, completed.md #547 — writes down the inclusion
  criterion for `findata/Misc/*.json` operator surfaces (track iff runtime
  input the code reads OR a human-curation carrier; regenerable triage
  scratch stays ignored) and whitelists the seven audited files: cascade
  manifest (runtime input), index/subsector worklists (producer-declared),
  retro_resolution_worklist (one-shot curation, never regenerate), and the
  country (235 → 19,141) / nic (journal source gone) / counterparty
  worklists regenerated same-day; no eval-gate bullet — data files only).
- *(archived 2026-11-21)* — `harvest_promotions_and_census_writeup` (graph/,
  filed 2026-11-20 + executed 2026-11-20/21, completed.md #550 — records
  the 2026-11-20 estate hunt: the U5 SQL-native 3-node motif census
  **REJECTED** (1,258,775 distinct triples at k≤40; cross-edge mult≥2 only
  17.0% — edition temporal-panel duplication (77.1% within-family) plus
  partition-family definitional zeros; #451 NO-GO cause corrected, refined
  reopen trigger = motif/overlap QA on a (period, filer)-deduplicated
  panel); slices landed: S5 frontend/desktop test gate (real `test`
  script + `desktop-contract` target), S2a spectral ARI≈0 pin + `--max-ari`
  guard + `hyper-spectral-check` (+ the undeclared-scikit-learn prune
  side-find re-declared in the `lens` extra), S2b wrapper-trio Standing
  dispositions, S2c hyper-predict bank target + tests + prereg realized
  (0 scoreable events — all deals predate snapshot generations), S2d
  `allow_giant` removed + U6 confirmatory (anchors reproduce, EM-collapse
  doesn't — null-level, stays gated), S4 all 32 stem-unmentioned modules
  dispositioned; S3 read-only MCP server DEFERRED).
- *(archived 2026-11-21)* — `duckdb_research_swap_execution` (database/,
  filed 2026-11-19 + executed 2026-11-19/21, completed.md #551 — canonical
  research store cut over to `memory/research.duckdb` (quack-served
  :9495, selector `FINDATA_RESEARCH_ENGINE=duckdb`); sqlite frozen as the
  rollback anchor (pre-cutover db-backup, SHA256 f547a037…, restore
  rehearsed); eval gate ACCEPT 239/239 at cutover; gates qa 15/15,
  perf 34/35, advisory reds chronic-only; follow-up: parquet snapshot
  leg DuckDB reader stays OPEN in pending.md).
- *(archived 2026-11-20)* — `gate_artifact_sidecar_zstd` (tooling/, filed +
  executed 2026-11-19, completed.md #548 — zstd-compress gate artifacts at
  retain time: pytest metadata sidecars (111.7 MB = 63% of `outputs/.artifacts`,
  load-bearing for `--rebuild-tests` since retained junit carries no pytest
  properties), junit (34.2 → 5.2 MB) and step logs (31.0 → 5.3 MB) —
  `.artifacts` 176.9 → 26.8 MB; parity 369/369 runs multiset-identical after
  rebuilding 275,335 test_facts rows from compressed artifacts; caught the
  `removesuffix(".junit.xml")`-vs-`.zst` enrichment bug en route).
- *(archived 2026-11-19)* — `embed_preload_sidecar_lock_bfs` (tooling/, filed
  + executed 2026-11-19, completed.md #546 — sidecar readers queue on a held
  DuckDB lock (embed-gc crash + 7 raw openers incl. the two convo migration
  probes that misread a held lock as "table absent" → ~45-min pointless
  re-embeds), `CachedEmbed.preload()` dict preload closes #528 deferred
  candidate 2 (one 44.2 ms scan replaces 4,807 × 0.91 ms point lookups per
  doc apply), and `bench_shortest_path._unreachable_dst`'s recursive CTE
  (10.65 s of a 12.0 s leg wall — planner pathology) replaced by a Python
  BFS over one `e_all_und` scan: leg 11.9 s → 1.21 s at unchanged worst-case
  semantics; plus doctrine corrections (psi = contention attribution, not a
  baseline; Mojo perf work continues — only the replace-Python goal retired)).
- *(archived 2026-11-19)* — `live_search_perf_slices` (tooling/, filed
  2026-11-18, executed 2026-11-19, completed.md #545 — four measured latency
  slices: S1 note_search cosine 504.3 → 50.28 ms k=None / 21.4 ms k=10 at
  bit-identical parity (sort stayed client-side — SQL ORDER BY is not stable
  and the tie order feeds RRF ranks), S1.5 company_embeddings 38.33 →
  0.07–0.15 ms/call (parity 7.8e-16), S2 bench harness reads live BLOBs at
  derived dims (published shape 384 → 512), S3 fuzzy_duplicate_names 3,814 →
  269 ms with the same 8,764 pairs (whole check 7,771 → 3,406 ms), S3b Mojo
  names 5,488 → 1,521 ms at 92/92 parity, S4 cosine gate rebuilt on a
  two-sided feasibility rule (Rayon pinned 1, floor 0.14×, median-of-rounds,
  top-1 by score; 3/3 healthy PASS, red at 2×); plus the derive_insights
  perf snafu resolved — the scan ProcessPool's fork/IPC overhead dominated
  (30.8 s pooled vs 7.9 s serial), serial is now the default, leg 8.77 s.)
- *(archived 2026-11-18)* — `search_check_corpus_walk_s3` +
  `fts_query_token_parity` + `fts_stem_ladder_and_topk_coverage`
  (tooling/, filed + executed 2026-11-18, completed.md #542 / #543 / #544
  — the DuckDB search-index sweep: `--check` verdicts made hash-only
  (script AST walk 3.18 s → 0.33 s, git whole-history numstat
  short-circuited 6,890 ms → 77 ms median via a sha-set + extraction-
  policy stamp), `fts_query_tokens_duckdb` aligned with the DuckDB
  analyzer (punctuated queries leave the 12–160 ms macro scan for the
  top-k path; `R&D` documented as unfixable), `_stem_guesses` rebuilt as
  a depth-3 superset generator at stemmer parity (corpus misses
  112/400 → 7/400, residuals = analyzer stopwords), and the memory/git
  legs routed through `fts_topk`.)
- *(archived 2026-11-18)* — `rust_betweenness_kernel` + `rust_closeness_kernel`
  (graph/, filed + executed 2026-11-18, completed.md #538 / #539 — the two
  L1 fold lanes' BFS-shaped work moved into `graph_kernels` as opt-in
  kernels: `brandes_fold_sources` returns the U/TU/KK accumulators for a
  batched source set (51.8 s → 3.9 s at jobs=1, **13.3×**, parity
  4.6e-13), `core_bfs_sources_batch` returns a `sources × n` distance
  matrix (7.44 s → 3.07 s, **2.42×**, scores bit-identical). Fold
  decomposition and score assembly stay in Python; lanes are DEFAULT-ON
  with an opt-out (`L1B_RUST=0` / `L1C_RUST=0`) and loud failure when
  forced.)
- *(archived 2026-11-18)* — `duckdb_long_holder_backoff` +
  `signal_sweep_forward_return_cache` (tooling/, filed + executed
  2026-11-18, completed.md #540 / #541 — the 60 s reader ladder
  `_EXTENDED_RETRY_DELAYS_S = (2.0, 10.0, 48.0)` beside the untouched
  1.55 s default, wired into the four reader CLIs #319 missed plus
  memory_search writer LOCK_EX coordination; and
  `signal_sweep::sweep` 55.077 s → 17.10 s via `(ticker, t, h_bars)`
  memoization at 0 parity diffs, closing R14 with a hermetic `smoke`
  subcommand.)
- *(archived 2026-11-16)* — `note_search_carve_out` (database/, filed
  2026-11-15, executed 2026-11-16, completed.md #530 — the last FTS5 index
  left `research.db` for a DuckDB native-FTS sidecar: S0 embed-cache fold,
  S1 sidecar build (`memory/note_search.duckdb`, composed `searchtext`,
  hash-only `--check`), S2 query-surface flip (`match_bm25` branch,
  sidecar model/dims stamps, `/api/search` hybrid, engine-aware TUI legs),
  S3 graph-projection reads, S4 ops wiring (snapshot export/verify, db_maint,
  gc, seed, store_sweep), S5 legacy FTS5 rollback kept green + 15 new tests).
- *(archived 2026-11-16)* — `duckdb_fts_sql_topk_regression` (database/,
  filed 2026-11-16, executed 2026-11-16, completed.md #531 — lexical queries
  were doing macro-predicate full scans; S1 proved 100% top-5 overlap
  (23/23) at 6.2× before engineering, S2 `fts_topk` in
  `helpers/core/duckdb_shared.py`, S3 doc/convo/note rollout (found and fixed
  a `convo_query` `by_id`-keyed-on-`harness` bug), S4 dropped explicit
  `LOAD fts` after measuring auto-load, S4b donor-embed read-through (cold
  script rebuild 20m7s → 88s), S6 vectorized doc cosine leg 326ms → 34.4ms).
- *(archived 2026-11-16)* — `duckdb_watchlist_hardening` (database/, filed
  2026-11-16, executed 2026-11-16, completed.md #532 — recorded the DuckDB
  watchlist (doc sidecar not snapshotted, internal `fts_main_*` layout
  dependence with `duckdb` unpinned, measured tokenization drift, three API
  footguns) and closed three slices: S1 `perf_smoke.py` advisory leg, S2
  post-restore FTS-shadow probe (fixes the uncaught
  `CatalogException: Scalar Function with name match_bm25 does not exist!`),
  S3 `idx_doc_vectors_emb` HNSW on `doc_vectors`).
- *(archived 2026-11-17)* — `derived_name_columns_guard` (database/,
  filed 2026-11-17, executed 2026-11-17, completed.md #529 —
  FK-shaped integrity for the 6 derived tables / 10 columns that carry
  entity names with no FK: the 2026-11-16 fold hand-rewrote
  `hidden_connections` in place, leaving 41,319 pairs where a true
  re-derivation gives 41,328, and only a pinned bank question noticed.
  S1 machine-enumerated registry + two-way test guard (which immediately
  caught 5 columns missing from `_REFOLD_COLUMNS`), S2
  `derived_cohort_meta` freshness marker, S3 fold stamps `repointed`
  instead of claiming derivation, S4 ERROR-level dangling-name check).
- *(archived 2026-11-16)* — `duckdb_rebuild_incremental_carry` (database/,
  filed 2026-11-16, executed 2026-11-16, completed.md #528 —
  restore the silently-dropped `--incremental` contract to the DuckDB
  rebuild legs: doc-leg per-file carry (mtime fast path + hash fallback)
  with stable section ids + one Arrow batch insert replacing 69.5 s of
  per-row executemany (warm rebuild 81.7 s → 16.98 s / no-op carry
  4.68 s); git-leg duckdb apply scoping (delta 13.9 s vs 40 s full);
  FTS-shadow delta refresh on the script/memory/note incremental paths
  (shadow is one-shot, not DML-updated — lexical search was missing
  deltas); content-keyed memory diff; one-off compaction of
  doc_search.duckdb 89.7 → 70.3 MB).
- *(archived 2026-11-14)* — `graph_kernels_ppr_lane` (graph/, filed
  2026-11-14, executed 2026-11-14, completed.md #523 — first-party
  power-iteration PPR kernel wired kernel-first into
  `personalized_pagerank_scores` with the scipy spsolve solve as
  fallback: 10.3×/seed on the live projection, parity 1.69e-13,
  spsolve fill-in-blowup finding on unstructured graphs; batch (16.7
  ms/seed), set-teleport (50 ms), α-curve and pair-asymmetry (90.7×)
  surfaces; cross-sector signal verdict §6).
- *(archived 2026-11-13)* — `group_dyad_wave2` (graph/, filed 2026-11-13,
  executed 2026-11-13, completed.md #521 — S1 `psu_dyad_pass.py` 26 labels /
  419 members; S2 `jv_dyad_census.py` 208 JV-shaped entities, 1,507 live
  `jv_with` edges (graph_edges, not hyper_edges); S3 `mnc_arm_probe.py`
  — ZFCV already-homed in `vigil:rpt:ZFCVINDIA` (56 members), ASTRAZENEC/
  NOVARTIS/ABBOTINDIA documented absence; S4 NIFTY 500 ≡ Multicap 499/1.0
  verified; 14/14 tests, catalog-only default).

- *(archived 2026-11-12)* — `semantic_cluster_label_metric`
  (graph/, filed 2026-11-12, executed 2026-11-12, completed.md #513 —
  `graph/semantic_cluster_label_metric.md`). Add a semantic_cluster
  label metric — e-means (faiss contrib) over the 1,209×512
  company_embeddings as a taxonomy-drift instrument (live sector ARI
  0.2401, re-anchored 0.21–0.24; NMI ceiling 0.537; optional faiss-cpu
  dep, pinned seed/k/nepochs). Evidence in
  `doc/local/engineering/emeans_algorithm.md` (machine-local), §4–§5.

- *(archived 2026-11-12)* — `name_token_family_seed_lane`
  (graph/, filed 2026-11-12, executed 2026-11-12, completed.md #510 —
  `graph/name_token_family_seed_lane.md`). Extend the
  name-token/family-seed group lane: a read-only candidate generator over
  entity names (76× lift, works on unlisted parents) feeding the curated
  `family_seeds.json` sink; vector-space detection measured and rejected
  (4.8×, blind on 99.4% of subsidiary_of edges). Evidence in
  `doc/local/engineering/emeans_algorithm.md` (machine-local), §9.

- *(archived 2026-11-12)* — `group_discovery_scale_lane`
  (graph/, filed 2026-11-12, executed 2026-11-12, completed.md #514 —
  `../archive/graph/group_discovery_scale_lane.md`). Component inventory +
  curated family seeds at corpus scale: 657 weak components over 13,233
  company-company same_group dyads; v6 first-wave (16 clean single-filer
  families) + v7 blob-core families (9 new + 2 extensions; 39 families /
  2,439 members / 0 dropped); gate ACCEPT 207/0 then 217/0; growth
  monitor + hyperedge co-occurrence lens. Evidence in
  `bench_data/group_discovery_inventory/` and `bench_data/hyperedge_cooccurrence/`.

- *(archived 2026-11-12)* — `group_membership_compaction`
  (graph/, filed + executed 2026-11-12, completed.md #512 —
  `../archive/graph/group_membership_compaction.md`). Compact vigil group-hyperedge
  membership (964 stale incidences, n_members mismatches 234 -> 0), purge the
  334 stranded-orphan tail (317 purged, 17 named event-referenced residue);
  first group-membership gate pins hyper-group-{tcs,reliance,mam}, gate ACCEPT
  191/0. Evidence in `bench_data/orphan_cleanup/`.

- *(archived 2026-11-12)* — `canonical_name_resolution`
  (graph/, filed + executed 2026-11-12, #509 —
  `../archive/graph/canonical_name_resolution.md`). NSE-certified duplicate-spelling
  folds: 22 foldable clusters, 28 variants folded, 147 rows re-pointed, zero loss.

- *(archived 2026-11-12)* — `entity_resolution_ladder_indexing`
  (graph/, filed + executed 2026-11-11, #508 —
  `../archive/graph/entity_resolution_ladder_indexing.md`). The archival
  step was missed at execution time; flipped and moved during the #507
  close-out. Ladder indexing 2.8x/4.35x, parity 823/823.

- *(archived 2026-11-12)* — `rpt_attribution_filer_resolution_and_group_family`
  (graph/, filed 2026-11-11, executed same arc, #507
  — `../archive/graph/rpt_attribution_filer_resolution_and_group_family.md`).
  Ruled C1 + A4 + A-prime + B0/B4 (m02492): ticker bridge, entity-subject
  attribution, step-down polarity with a reroute-made recency tiebreak, the
  replay-mint fork guard, and the curated family lane
  (helpers/misc/family_seeds.json). Gate ACCEPT 182/0 with --expect-change;
  probe v11 213 answered / 5 negative / 10 drifted / 0 errored. Evidence in
  bench_data/vigil_attribution/ + bench_data/stepdown_polarity/.

- *(archived 2026-11-11)* — `company_metrics_grain_and_bank` filed and
  executed same day, #506
  (`../archive/database/company_metrics_grain_and_bank.md`). The trial
  (`bench_data/metric_grain/`) proved the 18,956 "conflicting"
  `(entity, metric_label, period)` keys were 99.7% consolidated-vs-standalone
  SCOPE, not duplicates (0 of 63,390 document-level keys carry two values) —
  so ruling A1 keys the grain `(entity, metric_label, period, consolidated)`
  and keeps BOTH scopes; certification is READ-TIME (a `company_metrics_ruled`
  view, 76,976 ruled / 2,420 collapsed) rather than write-time dedup, which
  would have destroyed provenance. The `consolidated` flags moved from
  `properties` JSON to a real column (63,390 backfilled; ingest writes it
  directly), the gate gained a `metric_value` shape with the frozen set at v7
  (6 pins, both scopes), the probe gained the same rule over Parquet plus a
  git-tracked bank at `helpers/misc/answerability_questions.json` (seed 15,
  grown to 46 entries in v2 the same day),
  and `check_company_metrics` gained an advisory grain audit. Gate ACCEPT on
  179 questions with 0 reasons; probe parity 0 mismatches; 163 targeted tests
  green. Naive `SUM(value_num)` was inflating `q_revenue` by 1.69x
  (12,680,960.89 vs 7,519,934.98) — that is the migration notice.

- *(archived 2026-11-10)* — `bold_line_guard_failing_path` executed same
  day, #501 (`../archive/testing/bold_line_guard_failing_path.md`; the
  BOLD_LINE_RE rewrite was correct but BOTH guards were blind to the pattern
  it replaced — monkeypatching the old `(?:\*\*.+?\*\*\s*)+` back in still
  gave `2 passed`; S1 added a failing-path ladder
  (`("**" + "a"*1000 + "** ") * segments + "|"`, 8,041 → 32,161 bytes, 2 s
  SIGALRM per rung, median-of-5-rounds ratio < 8.0), S3 made the Hypothesis
  strategy assemble complete segments plus a non-closing terminator AND
  replaced `deadline` with an explicit SIGALRM cap because deadline cannot
  interrupt a running regex (it HUNG instead of failing), S2 corrected the
  misleading comment (the shape is quadratic in LENGTH by construction, so
  the 8.0 budget only catches a cubic regression), S4 pinned the deliberate
  exclusion of unbalanced bold with the 0/28-PDF differential as the reason
  it is safe; discrimination proven end to end — the old pattern injected
  turns `-k bold` from `4 passed` into `2 failed`)
- *(archived 2026-11-10)* — `bili_first_class_convo_search` executed same
  day, #500 (`../archive/convo_search/bili_first_class_convo_search.md`;
  bili compression summaries as first-class convo_search citizens:
  leg-level `--agent/--part-type/--bili-active/--bili-tier` filters +
  `--summary-first` partition, orphan relabel kept — `--agent bili`
  pierces it, `bili_active/bili_tier` backfilled on the
  `(file_path, part_id)` join across 1,607 rows / 63 files; the as-filed
  `agent_norm/part_type_norm` columns measured redundant and the ×1.5
  post-fusion boost measured inoperative — the pool is drawn before
  fusion — so S4 became the partition)
- *(archived 2026-11-10)* — `timeshift_snapshot_lane` executed same day,
  #499 (`../archive/convo_search/timeshift_snapshot_lane.md`;
  `snap-images:seen` stamp registry replaces the inert source-count scalar
  (an ABSENT backup mount is never drift), dsh snapshot glob adds 16,901
  `dsh-snap:<stamp>` rows where there were none, the live-id probe unions
  `session_v2`/`session`/`session_message` and fails closed instead of
  relabelling 3 live sessions, `zcode-snap:<stamp>` per-image labels (plus
  the double-prefix composition bug caught by querying the lane), and a
  166-record `--history` lane in memory_search for doctrine that only
  survives in snapshots)

- *(archived 2026-11-09)* — `rusqlite_bump_rustsec_2026_0459` executed
  same day, #498 (`../archive/tooling/rusqlite_bump_rustsec_2026_0459.md`;
  rusqlite 0.41.1 both workspaces, `libsqlite3-sys` 0.39.1, `cargo audit`
  rc=0 both locks, embed_cache README re-baselined 3.1–4.7× pipeline)
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
- *(archived 2026-11-11)* — `convo_fresh_new_parts_table` executed same
  day, #503 (`../archive/convo_search/convo_fresh_new_parts_table.md`;
  per-harness new-parts tally in APPLY and check — check dry-runs the
  lanes on a never-flushing writer over the real corpus_root so the
  table shows exactly the next APPLY's numbers without writing;
  `est embed ≈Ns` from `convo_meta.embed_s_per_row`, recorded by the
  rebuild on real cache-miss embeds).
