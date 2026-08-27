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

## database/ — Databases — DuckDB/SQLite engine and SQL query improvements

- [`duckdb_improvs.txt`](database/duckdb_improvs.txt) — DuckDB Improvements — core features, SQL, & extension surface — completed.md #72
- [`sql_query_improvements.txt`](database/sql_query_improvements.txt) — SQL QUERY IMPROVEMENTS — FinData Knowledge Graph — completed.md #73
- [`local_embeddings.md`](database/local_embeddings.md) — In-House Semantic Embeddings via a Local bge-small-en Model — completed.md #141
- [`company_embeddings_maint.md`](database/company_embeddings_maint.md) — Proposal: Cached Company-Embeddings Refresh in maint-full — completed.md #142
- [`sql_capability_unlocks.md`](database/sql_capability_unlocks.md) — Proposal: SQL Capability Unlocks — note vectors in DuckDB (`v_note_embeddings` + 4 wrappers + 2 endpoints), BFS shortest-path fix, bind-param hardening — completed.md #143
- [`maint_full_zero_churn.md`](database/maint_full_zero_churn.md) — Proposal: Zero-Churn maint-full — stable event/embedding writes, seeded+canonical louvain, `ORDER BY ALL` parquet exports, guarded B4 bumps — completed.md #147
- [`embed_store_consolidation.md`](database/embed_store_consolidation.md) — Proposal: Single embed store — consolidate the vector sidecars (pooled content-hash cache + note_search vec0 mirror in one SQLite file; backup streams collapse to two artifacts) — completed.md #166

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

## tooling/ — Tooling & performance — MCP eval, doc browser/search, perf review, tech survey

- [`mcp_tool_eval.txt`](tooling/mcp_tool_eval.txt) — mcp_tool_eval.txt — codebase-memory-mcp hygiene audit (Aug 2026)
- [`doc_search_embeddings.md`](tooling/doc_search_embeddings.md) — Content-Addressable Doc Search — FTS5 + hybrid embeddings over doc/ — completed.md #148
- [`doc_browser.txt`](tooling/doc_browser.txt) — Doc Browser & Search — web UI for the doc/ corpus
- [`perf_improvs.txt`](tooling/perf_improvs.txt) — Performance review — project-wide hotspots
- [`perf_optimization.md`](tooling/perf_optimization.md) — Perf optimization plan — `make perf` hotspots: link-prediction, extract_relations, pdf layout-off — completed.md #163
- [`temporal_analytics.md`](tooling/temporal_analytics.md) — Proposal: Temporal Analytics — REPORT=temporal — completed.md #150
- [`tech_avenues.txt`](tooling/tech_avenues.txt) — PROPOSAL: Technology avenues — databases, YAML richness, graph, MCP exposure
- [`docs_consistency_audit.md`](tooling/docs_consistency_audit.md) — Documentation consistency pass — README/procedures/schema/Makefile-guidance repairs vs code+DB ground truth (R/M/S/F/D/E/P/U findings) — completed.md #167
- [`graph_docs_ui_polish.md`](tooling/graph_docs_ui_polish.md) — Proposal: Lens + Reading Room UI polish — graph widget overhaul, edge-filter rendering policy, Desk-register buttons, reader width/focus — completed.md #168
- [`pending_improvs.txt`](tooling/pending_improvs.txt) — Pending Improvements — HISTORICAL (Bundles A–F closed)
