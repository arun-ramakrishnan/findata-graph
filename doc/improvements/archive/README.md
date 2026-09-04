# Improvements archive — topic index

Archived proposals grouped by topic (consolidated 2026-08-20;
the security review lives privately under doc/local (untracked);
`sqlite_improvs.txt` removed — a zero-byte stub since the initial
commit, referenced by nothing). Entry numbers point at
`../completed.md`. Live items live in `../pending.md`.

## graph/ — Graph layer — algorithms, DuckPGQ retirement, Onager, knowledge-model design

- [`graph_algos.txt`](graph/graph_algos.txt) — Proposal: Expand graph-algorithm coverage via Onager — link prediction,
- [`graph_improvs.txt`](graph/graph_improvs.txt) — Graph Algorithm Improvements — coverage gaps & DuckDB extension surface
- [`graph_pending.txt`](graph/graph_pending.txt) — Graph Pending Items — Deferred (P2.2, P3.2, P3.3)
- [`duckpgq_retirement.txt`](graph/duckpgq_retirement.txt) — Proposal: Retire duckpgq — consolidate on Onager + plain SQL — completed.md #73, #92
- [`networkx_duckpgq_gap_plan.txt`](graph/networkx_duckpgq_gap_plan.txt) — NETWORKX -> DUCKPGQ GAP ANALYSIS & DEPENDENCY PLAN
- [`hierarchy_design_roadmap.txt`](graph/hierarchy_design_roadmap.txt) — Hierarchy Design Roadmap — enriching the notes ↔ DB ↔ graph knowledge model
- [`pending_relations_triage.md`](graph/pending_relations_triage.md) — Proposal: Pending-relations triage (`triage_pending_relations`) — enclose the recurring queue workflow — completed.md #155
- [`suggested_relations_accept.md`](graph/suggested_relations_accept.md) — Proposal: Accept path for pending relations — `accept:<edge_type>[:<target>]` writes suggested + known-target rows to graph_edges — completed.md #169

## database/ — Databases — DuckDB/SQLite engine and SQL query improvements

- [`duckdb_improvs.txt`](database/duckdb_improvs.txt) — DuckDB Improvements — core features, SQL, & extension surface — completed.md #72
- [`sql_query_improvements.txt`](database/sql_query_improvements.txt) — SQL QUERY IMPROVEMENTS — FinData Knowledge Graph — completed.md #73
- [`local_embeddings.md`](database/local_embeddings.md) — In-House Semantic Embeddings via a Local bge-small-en Model — completed.md #141
- [`company_embeddings_maint.md`](database/company_embeddings_maint.md) — Proposal: Cached Company-Embeddings Refresh in maint-full — completed.md #142
- [`sql_capability_unlocks.md`](database/sql_capability_unlocks.md) — Proposal: SQL Capability Unlocks — note vectors in DuckDB (`v_note_embeddings` + 4 wrappers + 2 endpoints), BFS shortest-path fix, bind-param hardening — completed.md #143
- [`maint_full_zero_churn.md`](database/maint_full_zero_churn.md) — Proposal: Zero-Churn maint-full — stable event/embedding writes, seeded+canonical louvain, `ORDER BY ALL` parquet exports, guarded B4 bumps — completed.md #147
- [`embed_store_consolidation.md`](database/embed_store_consolidation.md) — Proposal: Single embed store — consolidate the vector sidecars (pooled content-hash cache + note_search vec0 mirror in one SQLite file; backup streams collapse to two artifacts) — completed.md #166
- [`maint_full_single_snapshot.md`](database/maint_full_single_snapshot.md) — Proposal: maint-full single snapshot + zstd parquet codec — elide the TIER1 snapshot in --full (artifacts always overwritten by the TIER2 tail), gzip→zstd SQLite parquet export, embed-store gz reuse — completed.md #174

## okf/ — OKF v0.2 — provenance vocabulary, activation, sources maintenance, read-side

- [`okf_adoption.md`](okf/okf_adoption.md) — Proposal: Adopt OKF v0.2 Provenance Vocabulary in Note Frontmatter — completed.md #130, #133
- [`okf_activation.md`](okf/okf_activation.md) — Proposal: Activating OKF Metadata — Coverage Analytics, Source-Driven Incremental Derivation, ` — completed.md #134
- [`okf_readside.md`](okf/okf_readside.md) — Proposal: OKF Read-Side — Per-Claim Footnotes + verify Helper — completed.md #136, #137
- [`okf_sources_maintenance.md`](okf/okf_sources_maintenance.md) — Proposal: OKF `sources[]` Maintenance at Render Time — completed.md #134
- [`newsletter_notes_adoption.md`](okf/newsletter_notes_adoption.md) — Proposal: Namespaced Tags, Validation & Tag Sync for Source Newsletter Notes — completed.md #132

## testing/ — Testing & QA — integration, stateful/relational, lint, coverage

- [`integration_plan.txt`](testing/integration_plan.txt) — INTEGRATION TEST PLAN — FinData Knowledge Graph
- [`integration_fuzz_enhancement.md`](testing/integration_fuzz_enhancement.md) — Proposal: Integration & Fuzz Test Suite Enhancement — write-side flows, sentinel machinery, query predicates — completed.md #144
- [`stateful_relational_test_plan.txt`](testing/stateful_relational_test_plan.txt) — STATEFUL / RELATIONAL TEST PLAN — FinData Knowledge Graph — completed.md #73
- [`lint_analysis.txt`](testing/lint_analysis.txt) — LINT ANALYSIS — ruff replaces flake8 (FinData knowledge graph) — completed.md #73
- [`coverage_extension_plan.md`](testing/coverage_extension_plan.md) — Coverage Analysis & Extension Plan (Updated)

## pipeline/ — Data pipeline — parsing, PDF conversion hardening, enrichment, corpus audit

- [`parse_extraction_gaps.txt`](pipeline/parse_extraction_gaps.txt) — Parse & Extraction Coverage Gaps — what the markdown pipeline drops
- [`pdf_conv_md_hardening_fuzz.md`](pipeline/pdf_conv_md_hardening_fuzz.md) — Proposal: Harden Paddle `parse_pages`, consolidate `slugify`, add fuzz coverage — completed.md #114
- [`metric_improvs.txt`](pipeline/metric_improvs.txt) — yfinance Enrichment Proposal — metric_improvs.txt
- [`findata_corpus_audit.txt`](pipeline/findata_corpus_audit.txt) — Findata Corpus Audit — markdown ↔ SQLite ↔ DuckDB ↔ graph coverage — completed.md #73
- [`market_data_resolution.md`](pipeline/market_data_resolution.md) — Proposal: Combined market-data resolution (FinnHub search → yfinance bulk → GF fallback; ticker-first doctrine, KNN peers, terminal classifications) — completed.md #152
- [`google_finance_ticker_fallback.md`](pipeline/google_finance_ticker_fallback.md) — Proposal: Google-Finance-assisted ticker resolution & data fallback (F1–F4; absorbed into market_data_resolution.md) — completed.md #152
- [`relation_enrichment_sources.md`](pipeline/relation_enrichment_sources.md) — Proposal: Relationship Enrichment from External Sources — Relations 2.0 (E1 prose v2, E2 yfinance KNN 3425, E3 7776 semantic_peer, E4 478 coinfer, E5 715 invested_in, E6 API/UI) — completed.md #153
- [`local_pdf_conversion_fallback.md`](pipeline/local_pdf_conversion_fallback.md) — Proposal: Local PDF conversion fallback (pymupdf4llm primary → Paddle OCR) — completed.md #156
- [`liteparse_pdf_engine.md`](pipeline/liteparse_pdf_engine.md) — Proposal: LiteParse PDF engine promotion — non-OCR default, gap-fill before cutover (Slices 0–2: lite no-ocr 0.10s bbox sidecar, lite OCR Tesseract 0.16–0.30s, pix2text formula opt-in, image sidecar, per-page verify, MPLBACKEND fix, 23 tests) — completed.md #186

## tooling/ — Tooling & performance — MCP eval, doc browser/search, perf review, tech survey
- [`corpus_uniformity.md`](tooling/corpus_uniformity.md) — Proposal: Corpus uniformity — doc/ five-class taxonomy (okf/ + design/), template seeds + PAIRINGS guards, kind='ts' script_search footprint, proposal frontmatter contract (36-file backfill), prettier + ruff-format gates — completed.md #190
- [`markdown_lint_adoption.md`](tooling/markdown_lint_adoption.md) — Proposal: Adopt markdownlint-cli2 — markdown lint gate for doc/ prose + findata Tier-1 defects (MD037 truncation writer + EOF-newline guards fixed at source, permission-gated 524-note backfill, 7 reprint editions quarantined for the reprint-recovery arc), promoted into the qa gate — completed.md #191
- [`md_lint_cache.md`](tooling/md_lint_cache.md) — Proposal: md-lint stale-scan cache — verdict sidecar keyed by content + config hash + cli2 version, symlink-mirror subset scans (cli2 globs-additive trap; search-fresh-epoch shortcut rejected), warm gate 16.8 s → 0.24 s — completed.md #192
- [`libyaml_adoption_and_regex_hotspots.md`](tooling/libyaml_adoption_and_regex_hotspots.md) — Proposal: libyaml C load/dump via shared frontmatter helper + derivation regex collapse (derive_themes `in` tests, derive_events literal prefilters, extract_relations hoist) — derive_insights 3.5–3.7 s → 2.2 s, derive_events → 0.8 s, 0/1243 parity — completed.md #193
- [`shared_corpus_incremental_derive.md`](tooling/shared_corpus_incremental_derive.md) — Proposal: Shared corpus and incremental derive — one walk and stale skip for `findata` (`1243` `5× rglob+ yaml` `→` `Corpus` `DB` `28 MB` `blake2b 8` `shard` `1080` `stale` `0.12s` `advisory` `8+2` `0` `WARNING`) — completed.md #194

- [`mcp_tool_eval.txt`](tooling/mcp_tool_eval.txt) — mcp_tool_eval.txt — codebase-memory-mcp hygiene audit (Aug 2026)
- [`doc_search_embeddings.md`](tooling/doc_search_embeddings.md) — Content-Addressable Doc Search — FTS5 + hybrid embeddings over doc/ — completed.md #148
- [`doc_browser.txt`](tooling/doc_browser.txt) — Doc Browser & Search — web UI for the doc/ corpus
- [`perf_improvs.txt`](tooling/perf_improvs.txt) — Performance review — project-wide hotspots
- [`perf_optimization.md`](tooling/perf_optimization.md) — Perf optimization plan — `make perf` hotspots: link-prediction, extract_relations, pdf layout-off — completed.md #163
- [`mojo_regex_via_python_interop.md`](tooling/mojo_regex_via_python_interop.md) — Mojo regex via the Python `regex` bridge — 51-case file-driven battery, ~2.5% overhead on real work — completed.md #180
- [`temporal_analytics.md`](tooling/temporal_analytics.md) — Proposal: Temporal Analytics — REPORT=temporal — completed.md #150
- [`tech_avenues.txt`](tooling/tech_avenues.txt) — PROPOSAL: Technology avenues — databases, YAML richness, graph, MCP exposure
- [`docs_consistency_audit.md`](tooling/docs_consistency_audit.md) — Documentation consistency pass — README/procedures/schema/Makefile-guidance repairs vs code+DB ground truth (R/M/S/F/D/E/P/U findings) — completed.md #167
- [`graph_docs_ui_polish.md`](tooling/graph_docs_ui_polish.md) — Proposal: Lens + Reading Room UI polish — graph widget overhaul, edge-filter rendering policy, Desk-register buttons, reader width/focus — completed.md #168
- [`parallel_cold_embed.md`](tooling/parallel_cold_embed.md) — Proposal: parallel cold embed — pinned spawn pool (4×1T) for the bge-small llama.cpp path; cold note_search 16m13s → 6m01s, cold company → 4m46s; measured-not-adopted record (packing/threads/EPP/unpinned-collapse) + deferred-scale triggers — completed.md #173
- [`gate_xdist_phase2.md`](tooling/gate_xdist_phase2.md) — Proposal: gate parallelism phase 2 — live-invariants xdist safety (per-worker DuckDB caches, worker-pid key), live suite 72.5→57.4s serial, advisory ~94→76–87s — completed.md #189
- [`doc_drift_audit_2026_08.md`](tooling/doc_drift_audit_2026_08.md) — Proposal: Documentation drift remediation — 7-day audit (2026-08-22 → 08-28 window + 08-29 session): F1–F10 incl. Mojo backfill, _build_meta keys, #170/#172 run-log backfills, gzip→zstd sweep, note-search --check doc — completed.md #178
- [`pending_improvs.txt`](tooling/pending_improvs.txt) — Pending Improvements — HISTORICAL (Bundles A–F closed)
- [`shared_routines_cli_guards.md`](tooling/shared_routines_cli_guards.md) — Shared routines pass 2 — stale-gate helper, graph-conn adoption, `--apply` CLI guard unification (dry-run defaults ×4, `--rewrite` retired, census advisory) — completed.md #196
- [`corpus_embeddings_scaling.md`](tooling/corpus_embeddings_scaling.md) — Scale corpus + embeddings to 100M — lazy `iter_notes`, aligned f32 `embed_matrix`, flat exact KNN (`FlatKNN` MAX opt-in), hybrid fallback vec0→flat→cosine — completed.md #195
- [`argv_seam_tail.md`](tooling/argv_seam_tail.md) — CLI test seam for the five out-of-census bare mains (hand-rolled sys.argv ×3, flag-less ×2; no argparse conversion) — completed.md #198
- [`utc_now_unification.md`](tooling/utc_now_unification.md) — utc_now unification — scope disposition closing the #196 W8 deferred item (adopt `db.utc_now()` at the two write-only DB audit stamps + backfill; pin every other timestamp producer as a documented deviation) — completed.md #199
