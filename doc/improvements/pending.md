# Pending improvements

Full annotated triage map with live-verified trigger status:
`doc/local/notes/future_items.md` (2026-09-05). Open items below keep their
- **§G2 word-overlap alias guard + §G3 discard-persistence noise gate** — executed as `doc/improvements/archive/graph/word_overlap_alias_guard.md` (completed.md #218, 2026-09-09).
revisit triggers inline; executed work is compressed to records.

- **HGX D10 prediction/motifs lanes** (D4 EXECUTED as hgx_first_scaling
  #241: h_edge/h_incidence cache + SQL consumers + hyper API —
  remaining trigger: hyper-native link prediction, unstarted). D10
  stays gated on incidence density (measured 2026-09-15: group 8/k=3.8,
  jv 6/k=2.0; hyper_edges 533 live). D11 residue: 16 unmapped industry-label buckets in
  `findata/Misc/subsector_worklist.json` (top: Banks - Regional ×38)
  are operator taxonomy decisions; note-level `subsector:` stays
  0-authored (D7 lane open).
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

- **`listed_on_index` membership edge** (deferred N5 item 7). The
    `index_membership` column was dropped 2026-07-28; the edge was never built.
    Requires a re-ingest pass extracting `index_membership:` from company YAML
    frontmatter before it can be materialised. Live 2026-09-05: only 9/1,079
    company notes carry the key — not worth the pass until coverage grows.
    Deferred by design. REVIEWED 2026-09-09 and re-deferred: the key is a
    DROPPED key, not a low-coverage one — `doc/okf/frontmatter.company.v1.json`
    types it `"type": "null"` ("Dropped key (2026-07-28); tolerated as null on
    legacy notes, absent on new ones"), so writing it would fail the OKF
    conformance check, and all 9 notes that carry it have `null`. No pipeline
    produces index-membership data; tickers (945/1,165 companies, 850
    India-exchange) prove exchange listing, not index membership. Revisit only
    if a real index-constituent data source appears. Detail in
    `doc/local/notes/future_items.md` §G1 and `word_overlap_alias_guard.md` §7.

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
  filed the persistent-cache follow-up (live proposal
  `graph_centrality_persistent_cache.md`); original state below:
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
