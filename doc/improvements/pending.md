# Pending improvements

Full annotated triage map with live-verified trigger status:
`doc/local/notes/future_items.md` (2026-09-05). Open items below keep their
- **§G2 word-overlap alias guard + §G3 discard-persistence noise gate** — executed as `doc/improvements/archive/graph/word_overlap_alias_guard.md` (completed.md #218, 2026-09-09).
revisit triggers inline; executed work is compressed to records.

- **HGX D10 prediction/motifs lanes** (D4 EXECUTED as hgx_first_scaling
  #241: h_edge/h_incidence cache + SQL consumers + hyper API —
  remaining trigger: hyper-native link prediction, unstarted). D10
  stays gated on incidence density (measured 2026-09-15: group 8/k=3.8,
  jv 6/k=2.0; hyper_edges 533 live). D11 residue: RESOLVED 2026-09-19
  by the subsector authoring pass (completed.md #258,
  `archive/graph/subsector_authoring_pass.md`) — 16 buckets → 8
  cleared by SUB_SECTOR_ALIASES additions (incl. the Gaming leaf) +
  8 covered by per-note authored `subsector:` (81 notes; every member
  company resolved, `unmapped_authored` empty). The label-level
  worklist detector still lists the 8 authored-covered labels — they
  re-ask on future label drift by design (S4 reopen intent).
- **Re-evaluate HNSW index macros** (deferred N5 item 5). `hnsw_index_scan`,
  `vss_match`, and `pragma_hnsw_index_info` emit empty-signature binder errors
  on the vss build (these are DuckDB extension macro names, not repo symbols —
  an undefined-anchor reading here is intended; re-verified on DuckDB 1.5.5 +
  Onager 49ad15b; extension
  binaries unchanged since 2026-08-14/09 — nothing new upstream to test).
  Brute-force VSS works (~3ms @ 1k) so nothing is broken today; revisit via
  quarterly `make update-extensions` (~Nov 2026) and re-test the macros
  (graph_design.md §18.5/§5.4).

- **Wrap `onager_ctr_personalized_pagerank`** (deferred N5 item 6). Onager bug:
  personalisation column ignored, restart node hardcoded to `node_id 1`, and it
  requires a weight column of type `BIGINT`; variants A/B produce identical
  output. Documented at `helpers/graph/onager.py:597` and graph_design.md §5.5.
  Do not wrap until a future Onager build honours the personalisation vector.

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

- **B2 relation sidecars** — optional tech-avenues leftover
  (`archive/tooling/tech_avenues.txt` §3): per-relation YAML sidecars with
  provenance (edge_type, counterparties, as_of, confidence, source permalink).
  The only unblocked medium item anywhere in the backlog — but the driver is
  weak while `findata/_pending_relations.txt` stays near-empty (see
  `doc/local/notes/future_items.md` §D for the queue run book).

- **OpenViking context-server pilot DEFERRED** (2026-08-20; proposal with
  full fact-check at `doc/local/openviking_pilot_proposal.md`). The gap
  it targeted — real semantic embeddings — closed in-house (#141, bge-small-en).
  Revive only for the context-server differentiators (L0/L1 hierarchy,
  automatic memory extraction, retrieval traces); the labeled eval set
  transfers verbatim. Known-if-revived: default embedder is Chinese-tuned
  with a one-model registry (swap needs `model_path` + explicit `dimension` +
  full rebuild — upstream issue #1523); `vlm` key is `api_base`.

- **P2.2 incremental DuckDB materialization** (archive/graph/graph_pending.txt)
  — row trigger FIRED 2026-09-05 (17,323 graph_edges > 10k) but the deferred
  reason (rebuild cost) is not binding: full rebuild measures 3.0 s against
  vault_scaling's 5 s DuckDB budget. Scale strategy is owned by
  `archive/graph/vault_scaling.md` (#204); re-evaluate when its T1 fires
  (~1M doubled rows; currently 34K) or measured rebuild > 5 s.

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
  budgets keep measuring compute. The advisory-side twin
  (live-invariants wall 95–226s) resolved 2026-09-20 —
  completed.md #255 (`archive/tooling/advisory_gate_perf_reports.md`);
  original state below:
  eigenvector 0.37s, link_prediction 1.34s, closeness 2.97s,
  betweenness 2.23s.
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
  future_items.md §G1/B3.
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
