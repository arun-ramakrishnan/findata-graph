# FinData Knowledge Graph — Completed Improvements

**Generated**: 2026-08-09
**Total completed**: 80 items

> **Note:** Full implementation details, code references, and rationale are in the `doc/improvements/archive/` subdirectory. This file is a summary view.

---

## SQLite Improvements

### P — Schema & constraints

- **P1** — Live graph_edges missing CHECK (json_valid(properties))
- **P2** — Live entities table is the legacy ALTER-TABLE-add-column shape
- **P3** — graph_analytics PK column order defeats the hot query

### Q — Indexing

- **Q1** — No index on entities.file_path — api_entity_detail full SCAN
- **Q2** — EXPLAIN QUERY PLAN regression test covers only 1 of ~10 hot queries

### R — Integrity checks

- **R1** — No entity_tags orphan check in the integrity report
- **R2** — orphan_companies count not in the integrity report file

### S — Upsert patterns

- **S1** — INSERT OR REPLACE → ON CONFLICT DO UPDATE (UPSERT)
- **S2** — Generated column for properties.year on the SQLite side **[EVALUATED — NOT ADOPTED]**
- **S3** — Partial index for entity_type='company' **[EVALUATED — NOT ADOPTED]**
- **S4** — FTS5 for entity typeahead/search **[EVALUATED — NOT ADOPTED]**

### T — Timestamps

- **T1** — Mixed UTC/local timestamps make the staleness flag unreliable

### U — Concurrency

- **U1** — No PRAGMA busy_timeout — concurrent writers get immediate SQLITE_BUSY
- **U2** — Most writers use manual commit(), not `with conn:`
- **U3** — apply_edges dry-run does N+1 SELECTs

### V — API query consolidation

- **V1** — api_graph_stats fires 9 serial queries per request
- **V2** — /api/graph/metrics json.loads per row
- **V3** — derive_co_mentions does per-file SELECT for entity name

### X — Fuzzy name matching

- **X1** — sqlite-spellfix for company-name fuzzy matching **[EVALUATED — PARTIAL]** — *word-overlap heuristic (85% accuracy) beats spellfix1 (55%) on real company matching; rejected as the primary matcher. Adopted only as the typo-only fallback (stage 4) in `helpers/core/fuzzy_match.py`, with `build_spellfix_table()` and `sqlite-spellfix` in requirements. Full evidence: `x1_spellfix_evaluation.md`.*

## DuckDB Improvements

### K — Push Python-side work back into SQL

- **K1** — company_neighbors_bundle re-introduces the F4 anti-pattern
- **K2** — _sector_neighbors_bundle does a cross-DB market_cap hop
- **K3** — neighbors() and standalone suppliers_and_customers() fire serial queries

### L — DuckDB-native snapshot / portability

- **L2** — Promote year/since out of properties JSON into a typed column

### N — New DuckDB 1.5.x features

- **N1** — VARIANT type for graph_edges.properties **[EVALUATED — NOT ADOPTED]** — *2.6–3.1× slower; breaks json_extract_string*
- **N3** — COPY TO Parquet for ad-hoc export
- **N4** — Macros (SQL functions) for the repeated GRAPH_TABLE patterns **[EVALUATED — NOT ADOPTED]** — *DuckDB segfaults at CREATE MACRO when body contains GRAPH_TABLE*
- **N5** — vss (vector similarity search) **[ADOPTED — 2026-08-09]**
- **N6** — spatial / GEOMETRY **[NOT APPLICABLE]**

### O — Maintenance / observability

- **O1** — duckpgq version-unlock regression test
- **O2** — Snapshot verify checks only v_node + e_belongs
- **O3** — read-only CHECKPOINT version assumption

## Graph Algorithm Improvements

### G — Algorithm coverage gaps

- **G1** — Closeness & eigenvector centrality not implemented
- **G2** — Louvain returns partition only, no modularity score
- **G3** — No standalone cycle detection

### I — Engine extension opportunities

- **I1** — Louvain runs in NetworkX, not DuckDB **[SKIPPED]**
- **I2** — Betweenness runs in NetworkX, not DuckDB **[SKIPPED]**

### J — Temporal & API surface

- **J2** — clustering_coefficient computed but NOT persisted
- **J3** — No /api endpoint serves centrality/community scores

## Findata Corpus Audit

### CRITICAL — data-loss bugs

- **C1** — Three YAML tag namespaces silently dropped by sync_tags.py — Fixed 2026-07-30: ALLOWED_CATEGORIES widened; 6,642 entity_tags rows populated
- **C2** — market_cap column disagrees with market_cap/* tag for 126 companies

### HIGH — coverage gaps

- **H1** — Typed relationship edges capture ~10% of the available signal **[MEASURED — IMPROVED]**
- **H2** — 45% of acquired edges lack valid_from (10/22), even when dated **[RESOLVED — PARTIAL]**
- **H3** — Only 10% of companies are wikilinked from their sector note

### MEDIUM — structured-vs-prose gaps

- **M1** — Financials are entirely prose (~199 notes with ## Financial Profile) **[DONE — REMOVED]**
- **M4** — No sector parent/child hierarchy (0 sector-to-sector edges)

### LOW — data hygiene

- **L1-findata** — Ticker format inconsistency (203/1021 ≈ 20%)
- **L2-findata** — index_membership is 99.4% empty (6/1031 populated)
- **L4-findata** — 68 distinct subsector/* tags each appear exactly once

## Hierarchy Design Roadmap

### EXPANSION — new data sources

- **D3** — Markdown schema normalization + frontmatter validator
- **D4** — Theme nodes — the cross-sector dimension analysts actually reason in
- **D7** — Event nodes — timestamped happenings, the temporal backbone

### STORAGE / ENGINE HYGIENE

- **D13** — fin_graph is INERT under a plain DuckDB connection **[EVALUATED — NOT ADOPTED]**

## Parse & Extraction Gaps

### G1 — Relation-verb coverage

- **G1.1** — JV synonyms → jv_with (tie-up/partnership/alliance) **[SHIPPED]**
- **G1.2** — Competes-with synonyms → competes_with **[MEASURED & REVERTED]** — *0 net new edges; dominated by non-company generics (IT/China/OTT/Ecuador/PSU)*
- **G1.3** — Supplier/customer synonyms → supplier_to / customer_of **[SHIPPED]**
- **G1.4** — Ownership / M&A synonyms → subsidiary_of / acquired **[SHIPPED]**

### G2 — Management_change event coverage

- **G2.1** — 'Incoming <Title>' form for management_change events **[SHIPPED]**

### G3 — Acquisition / guidance magnitudes

- **G3** — Acquisition / guidance magnitudes (backfill_magnitudes.py) **[APPLIED]**

## MCP Tool Eval

### MCP tool eval — codebase hygiene

- **(0)** — search_ticker dead code + no tests
- **(5)** — _super_sector vs _theme neighbors_bundle false duplicate **[DON'T — false positive]**
- **(6)** — _resolve_entity_or_404 vs _with_type duplication **[DON'T — false positive]**
- **(7)** — is_likely_correct_company empty-shortName
- **§D** — apply_edges ×2 (co_mentions vs themes)

---

## Performance Optimization (2026-08-10)

Four graph + maintenance scripts optimized based on a wall-clock sweep of the
full pipeline (`perf_improvs.txt`, archived). All 4 unit test suites pass
(113 tests); 4 new perf-gate tests added to `test_performance.py` to guard
against regression. `make perf` now covers 16 benchmarks + 3 scaling tests.

Full analysis and before/after timings: `doc/improvements/archive/perf_improvs.txt`.

### P0 — closeness_centrality weighted Dijkstra → unweighted BFS  [CRITICAL]

- **P0** — `helpers/graph/algorithms.py:207` — dropped `distance="weight"` from
  `nx.closeness_centrality()`. Weighted Dijkstra (O(V·E·log V)) was ~3.3s on the
  1192-node / 4052-edge graph; unweighted BFS is ~1.3s. The fix is also a
  correctness improvement: `load_graph()` sums weights for collapsed multi-edges,
  so the 1051 bidirectional `part_of`/`has_company` pairs got weight=2.0,
  artificially doubling shortest-path distances. Unweighted BFS treats each
  logical relationship as one hop. Added optional `approximate=True` param
  (k=√n sampling, mirrors betweenness pattern) for future scaling. Perf gate:
  `test_graph_algorithms_closeness_under_4s`.

### P1 — derive_events regex compilation + iteration dedup  [HIGH]

- **P1** — `helpers/graph/derive_events.py` — five fixes: (1) compiled inline
  `re.split` in `_iter_bullets` at module level (`_SENTENCE_SPLIT_RE`); (2) fixed
  double-yield bug — `_iter_bullets` walked `body.splitlines()` twice with `seen`
  set only deduping pass-2; (3) shared one `_iter_bullets` pass across both
  `_extract_guidance` and `_extract_management` via a `windows=` param (was 4×
  iteration: 2 extractors × 2 splitlines passes); (4) removed redundant
  `_PCT_RE.search` (computed once, reused); (5) compiled inline month-year regex
  in `_capture_period_token` (`_MONTH_YEAR_RE`). 1.8s → 1.1s, identical event
  counts. Perf gate: `test_derive_events_under_3s`.

### P2 — rebuild_note_search O(N²) scan + uncompiled regex  [MEDIUM]

- **P2** — `helpers/maintenance/rebuild_note_search.py` — four fixes: (1) compiled
  inline whitespace-collapse regex in `_clean_body` (`_WS_RE`); (2) moved
  `import hashlib` to module level (was per-file); (3) replaced O(N²) row-scan
  in `rebuild()` meta-row loop with a dict lookup (`rows_by_path`); (4) eliminated
  double fingerprint computation in incremental upsert path (stash `(row, mtime,
  chash)` tuples instead of recomputing). Perf gate:
  `test_rebuild_note_search_under_2s`.

### P3 — derive_insights inline regex compilation + heading-classification cache  [MEDIUM]

- **P3** — `helpers/graph/derive_insights.py` — compiled all inline regexes in
  hot loops at module level: (1) `_SENTENCE_SPLIT_RE` in `extract_metrics` (50K+
  calls); (2) `_PARAPHRASE_WS_RE` in `extract_quotes`; (3) pre-compiled label
  classifiers in `_label_from_window` (`_L_MARGIN`, `_L_REVENUE`, etc.); (4)
  `_ATTR_DASH_RE`, `_ATTR_DASH_CAP_RE`, `_ROLE_HEADING_RE` in
  `iter_company_sections`; (5) `_WS_CANON_RE`, `_SUFFIX2_RE` in `_canonicalize`;
  (6) `_CONCALL_HEADING_RE`, `_CONCALL_SUBHEADING_RE`, `_H1_TITLE_RE` for
  section/note-title detection; (7) `_U_CRORE_RE`, `_U_BN_RE`, etc. in
  `_unit_of`. Also cached heading classification in `iter_company_sections` to
  eliminate duplicate `_canonicalize` + `lower()` + `has_cap` calls in the
  second pass (1.19M → 918K function calls). 1.7s → 1.5s median (12% improvement).
  Perf gate: `test_derive_insights_under_4s`.


## parse_newsletter Performance Optimization (2026-08-10)

### Root cause: broken import in get_tickers.py (correctness + perf bug)

`get_tickers.py` line 18 used a bare `from fuzzy_match import word_overlap_match`
that silently failed (ModuleNotFoundError). The import was caught by a try/except,
so `word_overlap_match` was never defined. Inside `search_ticker`, every call to it
raised `NameError`, caught by a bare `except: continue`. Phase 1 (yf.Search, which
works) was skipped entirely. Fell through to Phase 2 which constructed wrong exchange
symbols ("RELIANCEINDUSTRIES.NS" instead of "RELIANCE.NS"), making 8-20 HTTP requests
that all 404'd. Returned None after 3-4s of wasted network I/O. `search_ticker` had
NEVER resolved a single ticker correctly.

### P0 — Fix broken import  [CRITICAL]

- **P0** — `helpers/core/get_tickers.py` — Fixed bare import to package-qualified
  `from helpers.core.fuzzy_match import ...`. Added `sys.path` bootstrap for subprocess
  invocation. `search_ticker` now actually resolves tickers via yf.Search (0.3-0.8s)
  instead of always returning None after 3-4s of failed HTTP 404s (5× faster + correct).

### P1 — Skip search_ticker in dry-run mode  [HIGH]

- **P1** — `helpers/core/parse_newsletter.py` — Wrapped `search_ticker` call in
  `if args.apply:`. Dry-run no longer hits the network at all. Dry-run timing on top
  10 notes: 55.6s → 7.0s total (8× faster).

### P2 — Strip broken Phase 2/3 from search_ticker  [HIGH]

- **P2** — `helpers/core/get_tickers.py` — Removed Phase 2 (exchange-suffix guessing
  that constructed wrong symbols like "RELIANCEINDUSTRIES.NS" → always 404) and Phase 3
  (requests-based fallback, unreachable). Replaced with single-pass yf.Search →
  word_overlap_match validation → prefer .NS/.BO → fetch .info once. Eliminates 8-20
  useless HTTP 404 requests per company.

### P3 — Parallelize search_ticker in --apply mode  [MEDIUM]

- **P3** — `helpers/core/parse_newsletter.py` — Resolved sectors for all new companies
  first (CPU-only), then resolved tickers concurrently via ThreadPoolExecutor(max_workers=4)
  in --apply mode. Sequential 4×0.7s = 2.8s → ~0.7s parallel.

### P4 — Pre-compute lowercase existing_names in classify()  [LOW]

- **P4** — `helpers/core/parse_newsletter.py` — Moved `{n.lower() for n in existing_names}`
  out of the per-candidate loop (was rebuilt 20-35× per note). Also cached `list(existing_names)`
  for the fuzzy_match call.

### P5 — Hoist constants to module level  [LOW]

- **P5** — `helpers/core/parse_newsletter.py` — Moved CAP_TOKENS, SECTOR_WORDS, CAP_CUT
  from extract_companies function-local to module-level constants (`_CAP_TOKENS`,
  `_SECTOR_WORDS`, `_CAP_CUT_RE`). Compiled inline `re.sub(r"\s+", ...)` and
  `re.sub(r"\s+(Limited|Ltd...)")` into `_WS_RE` and `_SUFFIX2_RE` at module level.

### P6 — Add timeout to search_ticker  [ROBUSTNESS]

- **P6** — `helpers/core/get_tickers.py` — Added `timeout=5` to `yf.Search()` call so
  network hangs can't stall the pipeline indefinitely.


## DuckDB VSS Integration (2026-08-09)

### N5 — vss (vector similarity search) **[ADOPTED — 2026-08-09]**

Adopted the DuckDB `vss` extension (`b833341`, core repo) for semantic/vector
similarity search over company embeddings, integrated into the FinData knowledge graph.

**Changes**:
- `helpers/graph/query.py` — `connect()` now loads `INSTALL vss; LOAD vss;` alongside `sqlite` and `duckpgq`; `_SCHEMA_VERSION` bumped 7 to 8 to force cache rebuild; `_build_graph()` drops/recreates `v_embeddings`; new `_materialise_embeddings()` projects company embeddings from SQLite (`fin.company_embeddings`) into DuckDB with `SET sqlite_all_varchar=true` + `CAST(ve.embedding AS FLOAT[])`; new `semantic_neighbors()` wrapper using `array_cosine_similarity` / `array_negative_inner_product` with dynamic dimension detection, `cross_sector` filtering, and `@_with_generation_cache`; CLI subcommand `semantic-neighbors`
- `helpers/graph/embeddings.py` — new module for generating and persisting company embeddings to SQLite; `populate_dry_run()` (SHA-256 pseudo-embeddings, no API key needed), `populate_api()` (OpenAI/Azure text-embedding-3-small); CLI with `--clear`, `--dims`, `--company`, `--stats`

**Key technical decisions**:
- DuckDB stays pinned at 1.5.4 (`duckpgq` only available for that version; 1.5.5 returns 404)
- vss scalar functions are safe to use; brute-force scan at ~3ms for 1,050 companies x 384 dims (well under the 10ms threshold from section 8.4)
- HNSW index-accelerated scan (`hnsw_index_scan`, `vss_match`, `vss_join`, `pragma_hnsw_index_info`) is still broken in vss `b833341` — functions register with empty parameter signatures `[]`; DuckDB does NOT auto-use HNSW indexes for `ORDER BY array_cosine_similarity(...)` queries
- vss requires fixed-length `FLOAT[N]` (not variable-length `FLOAT[]`) — `semantic_neighbors()` detects dimension dynamically at query time
- SQLite bridge reads `FLOAT[N]` columns as JSON text — `SET sqlite_all_varchar=true` + `CAST(... AS FLOAT[])` needed to materialize as proper DuckDB arrays

**Verification**: 1,050 companies x 384-dim pseudo-embeddings; CEAT (auto/tires) returns semantically similar companies across sectors (Pharma, Consumer, Retail, FMCG, NBFC); `cross_sector` filter excludes same-sector neighbors; CLI subcommand exit code 0; performance ~0.7-3.5ms after warm-up

**Next steps** (deferred):
- ~~Replace Python-side `is_name_match()` in `get_tickers.py` with vector similarity~~ — **DONE 2026-08-15** (see #103): `resolve_entity` now has a `vss_match()` fallback stage over `company_embeddings` (pure-Python cosine, no DuckDB dep). Effective with real embeddings; inert on dry-run pseudo-embeddings (hash-based).
- ~~Add embedding column to `note_search` FTS5 table in `rebuild_note_search.py` for hybrid ranking~~ — **DONE 2026-08-15** (see #105)
- ~~Add `/api/graph/semantic/<name>` endpoint in `app.py`~~ — **DONE 2026-08-15** (see #104)
- Re-evaluate HNSW index macros via quarterly `make update-extensions` (section 18.5)


## yfinance Data Enrichment (2026-08-10)

### yfinance — Company metrics + industry edges **[COMPLETE — 2026-08-10]**

Standalone enrichment pipeline that fetches structural and financial data for all
931 listed companies from yfinance and writes it to three destinations:

- **`company_metrics`** (SQLite) — 13 metrics per company (market cap, revenue,
  margins, P/E, debt/equity, beta, growth, ownership %) with `source_ref='yfinance'`
- **`graph_edges`** (SQLite) — `competes_with` edges from shared yfinance industry
  (5900 projected edges across 86 industries, up from 7)
- **Company notes** — structural data only: `industry:` frontmatter field +
  sentinel-wrapped `## Company Profile (yfinance)` body section (employees, holdings,
  business summary)

**Key decisions**: standalone `make metrics-rebuild` (not in maint-full); notes get
structural data only (volatile financials stay in DB); `--max-age-days` opt-in
(default 0); idempotent DELETE-then-INSERT; USD→INR conversion for companies
reporting in USD.

**Files**: `helpers/maintenance/enrich_from_yfinance.py`,
`tests/test_enrich_yfinance.py` (32 tests), Makefile `metrics-rebuild` target.

**Verification**: 801/931 tickers fetched (130 bad), 61.6s, 32/32 unit tests,
15/15 perf benchmarks. Details: `doc/improvements/archive/metric_improvs.txt` § 11.
70. **Frontmatter dedup — consolidate all 7 duplicate functions** (2026-08-11)

Created `helpers/core/frontmatter.py` as the single canonical frontmatter module
with 4 functions:
- `strip_frontmatter(text)` — body without FM block
- `split_frontmatter(text)` — `("---", yaml, rest)` 3-tuple
- `split_frontmatter_with_title(text)` — `(title|None, body)` for note-search
- `extract_tags(text)` — tag list from YAML `tags:` block

Replaced **all 7** duplicated implementations across 7 files:
derive_events, derive_themes, extract_relations, move_sector, rename_entity,
rebuild_note_search, sync_tags. Net **-97 lines** of duplicated code. Each source
module keeps a backward-compatible alias so all existing test imports work unchanged.

The unified `_FM_RE` uses the more permissive `\s*\n` regex (from extract_relations)
which subsumes the bare `\n` variant (from derive_events/themes).

**Files**: `helpers/core/frontmatter.py` (new),
`helpers/graph/derive_events.py`, `helpers/graph/derive_themes.py`,
`helpers/graph/extract_relations.py`, `helpers/maintenance/move_sector.py`,
`helpers/maintenance/rename_entity.py`, `helpers/maintenance/rebuild_note_search.py`,
`helpers/core/sync_tags.py`.

**Verification**: 270 tests passed across all affected modules.
71. **H4 — _pending_relations.txt cleanup** (2026-08-11)

Triaged 484 accumulated rows → 95 unique (source, target, edge_type) pairs.
Of the 95 unique pairs:
- **8 new edges created** (1 already existed): Eicher Motors→Amazon, India Cements→UltraTech
  Cement, Trident Techlabs→Kaynes Technologies, John Cockerill India→Steel Authority of India,
  Fredun Pharma→Sun Pharmaceutical Industries, Dev IT→Xduce Infotech, Vibhor Steel Tubes→
  Jindal Pipes, Bosch→Tata AutoComp
- **3 entity stubs created**: Xduce Infotech, Jindal Pipes, Tata AutoComp
- **2 dropped** (Clix Capital merger never completed; Adani Power→Jaiprakash already existed)
- **49 noise items permanently dropped**: 21 generic descriptors, 14 sentence fragments, 8 geo,
  5 truncated names, 1 self-loop
- **18 foreign-entity JV mentions deferred** (need stub creation)

File reduced from 183KB/484 rows → 8KB/18 rows.

**Files**: `findata/_pending_relations.txt`, `memory/research.db`,
`findata/Companies/Technology/Xduce_Infotech.md`, `findata/Companies/Metals/Jindal_Pipes.md`,
`findata/Companies/Automotive/Tata_AutoComp.md`.

---

## 72. L1 — Parquet snapshot of DuckDB + SQLite tables

**Date**: 2026-08-11
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/duckdb_improvs.txt` Bundle L, item L1

Export all materialised DuckDB tables (20 tables) and SQLite data tables
(9 tables, excluding FTS5 virtual tables) to portable Parquet files under
`db-backup/parquet/duckdb/` and `db-backup/parquet/sqlite/`.

### Implementation

- **`helpers/maintenance/snapshot_db.py`**: Added `export_parquet_duckdb()`,
  `export_parquet_sqlite()`, `verify_parquet_snapshot()`, table-discovery
  helpers, and `_list_duckdb_tables()` / `_list_sqlite_tables()`.
- **CLI**: New `--format {binary|parquet|both}` option (default: **both**).
  `make snapshot` produces BOTH gzip binary + Parquet by default; `--format`
  narrows to one or the other. **`--check` (snapshot-check) verifies BOTH
  formats** (gzip binary + Parquet when present); missing Parquet files
  gracefully skip.
- **Makefile**: `snapshot`/`snapshot-check` output/check both formats by
  default (no separate `snapshot-parquet` target — kept the design simple).
- **`.gitignore`**: `db-backup/` already covers the parquet/ subdir.
- **Tests**: 6 new tests in `tests/test_snapshot.py` (export round-trip,
  FTS5 exclusion, verify mismatch detection, verify pass, pandas readability,
  missing-DB skip). All 9 tests pass.

### Output structure (produced by default via `make snapshot`)

```
db-backup/parquet/
  duckdb/   (20 files, ~134KB)
    v_node.parquet, v_company.parquet, v_sector.parquet, ...
    e_belongs.parquet, e_acquired.parquet, ...
    _build_meta.parquet, v_embeddings.parquet
  sqlite/   (9 files, ~7.4MB)
    entities.parquet, graph_edges.parquet, events.parquet,
    quotes.parquet, entity_tags.parquet, company_metrics.parquet,
    company_embeddings.parquet, graph_analytics.parquet, db_meta.parquet
```

### Design decisions

- **Per-table files** (not one combined file): each table loads independently.
- **SQLite export via pyarrow** (not DuckDB ATTACH): DuckDB's strict timestamp
  parsing rejects some legacy rows. pyarrow preserves SQLite TEXT types.
- **DuckDB export via native COPY**: DuckDB knows its own types (BIGINT, DATE,
  DOUBLE) and writes them correctly.
- **FTS5 shadow tables excluded**: `note_search*`, `entities_fuzzy*` filtered.
- **Both formats by default**: `make snapshot` produces gzip binary + Parquet;
  `--format` narrows when a consumer needs only one.

---

## 73. H4 follow-up — foreign-entity JV stub creation (18 deferred rows → 15 stubs)

**Date**: 2026-08-11
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/findata_corpus_audit.txt` item H4

Closed out the 18 deferred foreign-entity rows left by the H4 pass. All
`_pending_relations.txt` rows are now resolved — the backlog is EMPTY.

### Stubs created (15)

Al Habtoor Group (International), Stanley Electric (Automotive), Hyosung
(Engineering_Capital_Goods), General Atomics Aeronautical Systems (Defense),
MUFG (Banking), Indorama (Chemicals), Philip Morris International (FMCG),
Mastercard (Fintech_Payments), New York Life (Insurance), Anthropic
(Technology), Microsoft (Technology), Randstad (Technology), Carlsberg
(FMCG), NSDL (Capital_Markets), Pizza Hut (FMCG).

### Edges created (45)

- 15 main edges: 12 `jv_with` (JBM Auto→Al Habtoor Group, Lumax→Stanley
  Electric, Quality Power→Hyosung, L&T→General Atomics, Shriram Finance→MUFG,
  EPL→Indorama, Godfrey Phillips→PMI, Jio Financial→Mastercard, Max
  Estates→New York Life, Titagarh→Anthropic, Infosys→Microsoft, Varun
  Beverages→Carlsberg), 2 `competes_with` symmetric (CDSL→NSDL,
  Jubilant→Pizza Hut), 1 `acquired` (LTM→Randstad).
- 30 sector edges: `part_of` + `has_company` for each stub → its sector.

### Drops (2 misparses)

- KSH International→MUFG: identical quote text to Shriram Finance's MUFG
  deal; extractor mis-attributed the deal to KSH International.
- Vijaya Diagnostic Centre→Hyosung: quote is Quality Power Electrical
  Equipments' GIIT deal text, mis-attributed.

### Verification

- `PRAGMA integrity_check` = ok; 0 orphan edges
- DuckDB rebuilt (`make graph-rebuild`): v_node 1193→1211, all 15 new
  entities + 18 new edges present in materialised tables
- `note_search` re-indexed: 1227 docs (was 1209)
- 88 tests pass (fuzzy_match, fuzz_relations, extract_relations_resolver)
- All 16 mentions (incl. "Al Habtoor" variant) resolve via EntityResolver
- Snapshots refreshed (`make snapshot`): entities 1211/1211, relations
  4111/4111, DuckDB v_node 1211/1211, 29 parquet tables OK

### Regression fixed (same pass)

The 2026-08-11 ticker enrichment (get_tickers.py) built its spellfix1 fuzzy
table (`entities_fuzzy`) inside the production `memory/research.db`. That
left a spellfix1 virtual table in the source-of-truth DB; DuckDB's sqlite
extension then failed every catalog scan (`information_schema.columns`,
`duckdb_columns()`) with `no such module: spellfix1` (this Python 3.14
sqlite3 build has no spellfix1 compiled in) — surfacing as
`test_e_acquired_has_typed_year_column` failing. Hidden earlier because
`test_graph.py` is live-marked (excluded from `make qa`).

Fix (2 parts):
- `helpers/core/get_tickers.py`: build the spellfix table in an in-memory
  `sqlite3.connect(":memory:")` DB instead of `memory/research.db` — the
  virtual table no longer lands in the source of truth. Added `import
  sqlite3`.
- Dropped the leftover `entities_fuzzy*` tables from `memory/research.db`
  (needs `sqlite_spellfix` loaded to destroy the virtual table).

Test updates (expected data change):
- `test_graph.py::test_jv_partners_returns_venture`: Jio Financial Services
  partners 2 → 3 (new real `Mastercard` edge from the H4 follow-up).

### Orphan company fix (post-integrity-check)

The 3 original H4-pass stubs (Xduce Infotech, Jindal Pipes, Tata AutoComp)
still lacked `part_of`/`has_company` sector edges — flagged by
`database_integrity_check.py` as orphan companies. Added 6 edges
(`source_ref=manual:h4-sector-edges:2026-08-11`): 3 `part_of` (company→sector)
+ 3 `has_company` (sector→company). Integrity check now reports 0 orphans.

72. **Coverage quick wins — 102 unit tests across 15 low-coverage files** (2026-08-11)

Added 102 targeted unit tests to boost coverage on the 15 "quick-win" files
(<50 missing lines each, 368 total). Three new test files created
(`test_db.py`, `test_frontmatter.py`, `test_migrate_helpers.py`), 10 existing
files extended.

Key improvements (unit-test-only numbers; full impact visible on `make cover`):
- `db.py`: 39% → 91% (+35 lines recovered — new test file with 18 tests)
- `frontmatter.py`: 64% → ~100% (+13 lines — new test file with 14 tests)
- `fuzzy_match.py`: 95% → 100% (+3 lines — word_overlap_match success path,
  spellfix1 exception/no-result paths)
- `build_sector_hierarchy.py`: 73% → 80% (+13 lines — _normalize, _note_path,
  _super_sector_note, _inject_uplink)
- `algorithms.py`: 82% → 86% (+7 lines — _format_value, _print_result for
  wcc/louvain/ranked paths)

Also fixed `test_snapshot_check_under_4s` to refresh the snapshot before timing
(other slow tests bump DuckDB generation, causing a stale-snapshot mismatch).

73. **Coverage medium effort — 182 unit tests across 9 medium-coverage files** (2026-08-11)

Extended coverage work to the 9 medium-effort files (50-200 missing lines each).
Four new test files created (`test_move_sector.py`, `test_rename_entity.py`,
`test_enrich_from_yfinance.py`, `test_snapshot_db.py`), 5 existing files extended.

Key new tests:
- `enrich_from_yfinance.py`: 34 tests (NEW file) — format/convert_value,
  extract_metrics with USD conversion, extract_profile, render_profile_block,
  _update_frontmatter, _insert_profile_section, write_metrics, write_competitor_edges,
  get_stale_companies, get_enriched_companies
- `static_checks.py`: +24 tests — _parse_frontmatter (valid/absent/bad-yaml),
  _check_tags_one (5 paths), _check_permalink_one (4 paths), _check_date_one (5 paths),
  _iter_findata_md, _has_node, check_dependency_pinning
- `derive_insights.py`: +34 tests — _canonicalize (5), _parse_attribution (7),
  _label_from_window (5), _classify_metric (3), _parse_value_num (4), _unit_of (9)
- `extract_relations.py`: +24 tests — _tokens, _looks_like_speaker, _parse_yaml_field,
  _detect_doc_type, _make_properties, _split_sections
- `parse_newsletter.py`: +17 tests — normalize_name (9), render_stub (3), extract_companies (5)
- `get_tickers.py`: +16 tests — _fmt_number/_fmt_pct, _print_header/basic/valuation/history
- `move_sector.py`: 17 tests (NEW file) — all 8 YAML helper functions + err/ok
- `snapshot_db.py`: 10 tests (NEW file) — _list_sqlite/duckdb_tables, snapshot round-trip
- `rename_entity.py`: 6 tests (NEW file) — replace_field pure function

**Combined totals**: 284 new tests across 24 files (quick wins + medium effort).

74. **Coverage big targets — 101 unit tests for database_integrity_check, query, db_maint** (2026-08-11)

Tackled the three largest remaining coverage gaps:

- `database_integrity_check.py` (+51 tests): `_meaningful_tokens` (5 tests),
  `_check_directory_structure` (6 tests), `_check_filename_format` (4 tests),
  `validate_file_path` (4 tests), `check_orphan_companies` (3 tests),
  `check_normalization` (5 tests), `check_market_cap_conflicts` (3 tests),
  `check_validity_window` (4 tests), `check_fuzzy_duplicate_names` (3 tests),
  `check_quotes` (3 tests), `check_company_metrics` (3 tests),
  `check_graph_summary` (2 tests), `check_db_meta` (2 tests),
  `_query` (1 test), `get_connection` (3 tests).

- `query.py` (+29 tests, NEW file `test_query_helpers.py`): `_lit` (5 tests),
  `_normalise_as_of` (8 tests), `_as_of_predicate` (4 tests),
  `_label_to_table` (3 tests), `_query_cache` set/get/clear/FIFO eviction (4 tests),
  `clear_graph_cache` (1 test), `EDGE_REGISTRY` validation (4 tests).

- `db_maint.py` (+21 tests, NEW file `test_db_maint.py`): `_fmt_bytes` (5 tests),
  `_pragma_ident` (5 tests), `_compute_root` (1 test),
  `DBMaintainer.settings` (2 tests), `DBMaintainer.metrics` (2 tests),
  `DBMaintainer.stat_staleness` (1 test), `DBMaintainer.index_report` (2 tests),
  `_print_report` (3 tests).

**Coverage campaign grand total: 385 new tests across 27 files.**

75. **Coverage: sub-50% files — 52 tests for capture_newsletter_images, static_checks, move_sector, rename_entity** (2026-08-11)

Targeted the 4 remaining files below 50% coverage:

- `capture_newsletter_images.py` (42%→improved): 16 tests (NEW file) — slugify (4),
  parse_images (4), assign_pages (3), is_valid_jpeg (5)
- `static_checks.py` (48%→improved): +24 tests — _walk, check_python_syntax,
  check_stray_artifacts (DS_Store/.bak), check_helper_shebangs (5 paths),
  check_merge_markers (3 paths), check_required_files (2 paths),
  check_yaml_frontmatter (3 paths), check_findata_yaml, _db_path,
  check_sqlite_helper_usage (2 paths), check_db_meta_generation (2 paths)
- `move_sector.py` (37%→improved): +7 tests — move_entity integration tests
  (move, dry_run, invalid sector, entity not found, same sector, YAML update, edge update)
- `rename_entity.py` (20%→improved): +5 tests — main() integration tests
  (rename success, ticker override, entity not found, no args, cascade edges)

Also fixed: Python 3.14 `py_compile.PyCompileError` no longer has `.errormsg`
attribute — production code in `check_python_syntax` would crash on syntax errors.
Fixed to use `str(e)` instead. Test now verifies the fix works.

**Coverage campaign COMPLETE: 437 new tests across 31 files, 63.9%→67.4% (+3.5pp).**

76. **Coverage: verify_notes + embeddings — 94 tests for two biggest remaining gaps** (2026-08-12)

Targeted the two largest remaining coverage gaps:

- `verify_notes.py` (122 miss → improved): 64 tests (NEW file `test_verify_notes.py`) —
  NotesVerifier with full coverage of:
  - `check_filename_format` (6 tests: valid, leading digit, consecutive/trailing underscore, too long, special chars)
  - `check_name_sync` (3 tests: matching, missing, mismatch)
  - `check_yaml_structure` (10 tests: valid, missing delimiters, empty, missing fields, invalid type,
    tags not list, duplicate tags, bad YAML syntax, tag format/value warnings)
  - `check_sector_yaml_consistency` (6 tests: valid, bad permalink, unquoted date, unexpected field,
    field order, quoted title)
  - `check_super_sector_yaml_consistency` (3 tests: valid, bad permalink, unexpected field)
  - `check_company_yaml_consistency` (8 tests: valid, bad permalink, invalid market_cap, missing field,
    ticker null + listed missing, sector casing, sector unknown, quoted title)
  - `check_content_quality` (6 tests: long ok, no content, placeholder, missing heading, blank, no delimiters)
  - `check_heading_duplicates` (6 tests: exact dup, near dup, case variant, none, redundant YAML, false positive suffix)
  - `_norm_heading` + `_heading_false_positive` (6 tests)
  - `_totals` + `generate_report` (4 tests)
  - `process_directory` (3 tests: nonexistent, empty, with file)
  - `log_issue` / `log_warning` (3 tests)

- `embeddings.py` (72 miss → improved): 30 tests (NEW file `test_embeddings.py`) —
  - `_pseudo_embedding` (12 tests: dimension, deterministic, different text/seed, L2-normalized,
    value range, dims=1, large dims, empty text, invalid/negative dims, hash extension)
  - `_ensure_schema` (6 tests: creates table/index, idempotent, dim mismatch drops+recreates,
    same dims keeps data, invalid dims)
  - `_get_company_text` (3 tests: entity not found, no file path, with file + YAML strip)
  - `clear` (2 tests: empty, with rows)
  - `stats` (3 tests: no table, empty, with rows)
  - `populate_dry_run` (3 tests: single company, all companies (sector exclusion), replace existing)
  - `_get_openai_client` (1 test: import error)

**Coverage campaign grand total: 531 new tests across 33 files (10 + 2 = 12 new test files).**

77. **Fuzz tests for frontmatter.py — 13 hypothesis property tests** (2026-08-12)
78. **P1 Integration Tests: parse_newsletter E2E pipeline** (2026-08-12)
79. **P5 Integration Tests: graph-algorithm compute→persist→read round-trip** (2026-08-12)
   - New file: `tests/test_integration_graph_algorithms.py` — 34 tests, 7 classes
   - Coverage: compute→write→read round-trip for 5 NetworkX metrics (parametrized),
     value sanity (degree ordering, betweenness bridge, closeness range, eigenvector ≥0,
     louvain int labels), write_analytics UPSERT idempotency, _wrap_for_analytics shape
     (scalar/label/modularity), graph mutation→recompute→delta (3 tests), API
     /api/graph/metrics endpoint (8 tests: scalar ranked, top param, entity filter,
     label groups, error 400s, empty metric), _format_value (3)
   - Synthetic 6-node graph with two sectors, monkeypatched algorithms.connect
   - All 34 tests pass in 0.6s; full suite: 1253 passed (254 deselected)

   - New file: `tests/test_integration_parse_newsletter.py` — 36 tests, 9 classes
   - Coverage: entity creation (7), note files (6), graph edges (4), idempotency (3),
     dry-run mode (3), worklist JSON (3), sector guessing (4), existing-entity
     classification (3), non-company heading filtering (3) ... wait, wrong total.
   - Coverage: entity creation (7), note files (6), graph edges (4), idempotency (3),
     dry-run mode (3), worklist content (3), sector guessing (4), existing-entity
     classification (3), non-company headings (3)
   - Synthetic newsletter → full pipeline → verify entities, notes, edges, worklist
   - Mocked: search_ticker, capture_images, run_validation, run_graph_analytics
   - Registered new `integration` pytest marker in `pytest.ini`
   - Fixed pre-existing `test_fuzz_frontmatter.py` whitespace-tag failure
   - All 36 tests pass in 1.3s; full suite: 1183 passed (290 deselected)

After analyzing all 12 coverage-campaign modules for fuzz/perf gaps, found exactly one:
`frontmatter.py` (4 pure text-parsing functions) had no fuzz coverage despite being the
canonical frontmatter parser used by 7+ modules.

Created `tests/test_fuzz_frontmatter.py` with 13 hypothesis tests:
- **Never-raises invariants** (4 tests): `strip_frontmatter`, `split_frontmatter`,
  `split_frontmatter_with_title`, `extract_tags` on arbitrary text (200 examples each)
- **Idempotency** (1 test): `strip(strip(x)) == strip(x)`
- **Consumption invariant** (1 test): stripped result never starts with `---\n`
- **Reconstruction** (1 test): `opening + yaml_body + rest == original` when FM detected
- **Passthrough** (1 test): non-`---` text returns `("", "", text)`
- **Tag invariant** (1 test): all returned tags are non-empty stripped strings
- **Title type** (1 test): title is None or non-empty string
- **Structured FM** (3 tests): strip consumption, split reconstruction on valid FM blocks,
  and extract_tags round-trip on generated tag lists

**Gap analysis result**: All other coverage-campaign modules already have appropriate
fuzz/perf coverage or are not suitable (DB I/O, network calls, boolean checks).

80. **P6 Integration Tests: TypeScript type-contract validation** (2026-08-12)

New file: `tests/test_integration_ts_contract.py` — 22 tests, 7 classes

**Purpose**: Treat `frontend/types/api.ts` as the contract between the app.py
backend and the findata frontend. The api.ts interfaces are hand-written to
mirror the `jsonify({...})` blocks in app.py; shape-drift is caught ONLY by
manual `make frontend-check` (which validates findata.ts against api.ts, but
NOT api.ts against app.py). This suite closes the REVERSE direction: for each
endpoint, hit it via a Flask test_client and assert every key the api.ts
interface declares is actually present in the response body. If app.py changes a
response shape without updating api.ts, these tests fail.

**Coverage**:
- Parse 20 TypeScript interfaces from api.ts (ErrorResponse, SectorEntity,
  SectorsResponse, StatsResponse, EntityListItem, EntitiesResponse,
  EntityDetail, SearchResult, SearchResponse, GraphRefreshResponse,
  CompanyNeighbors, SectorNeighbors, SuperSectorNeighbors, SubSectorNeighbors,
  ThemeNeighbors, ShortestHop, ShortestPathResponse, EventItem, EventsResponse)
- Map 9 SQLite-backed API endpoints to their contract interfaces:
  - `/api/sectors` → `SectorsResponse` (classifications, sector_entities[],
    super_sectors[])
  - `/api/stats` → `StatsResponse` (entity_counts, top_sectors,
    market_cap_counts, total_entities)
  - `/api/entities` → `EntitiesResponse` (entities[], total_count, limit,
    offset); each item → `EntityListItem`
  - `/api/entity/<path>` → `EntityDetail` (extends EntityListItem +
    frontmatter, content, raw_content)
  - `/api/search` → `SearchResponse` (results[], total_count, limit, offset);
    each hit → `SearchResult` (doc_type, file_path, title, sector, snippet)
  - `/api/events/<name>` → `EventsResponse` (entity, entity_type, file_path,
    event_count, events[]); each event → `EventItem`
  - `/api/graph/neighbors/<name>` → `CompanyNeighbors` / `SectorNeighbors`
    tagged union (validated error shape without DuckDB)
  - `/api/graph/shortest` → `ShortestPathResponse` (validated error shape
    without DuckDB)
- DuckDB-dependent endpoints (neighbors, shortest) verify ErrorResponse shape
  without needing DuckDB by monkeypatching `get_graph_connection`
- Self-consistency checks: every parsed api.ts interface has ≥1 field;
  ErrorResponse has required `error` key

**Files**: `tests/test_integration_ts_contract.py` (new),
`findata/Companies/Banking/HDFC_Bank.md`, `findata/Sectors/Banking.md`,
`findata/Companies/Technology/Infosys.md`, `findata/Sectors/Technology.md`
(note fixtures for /api/entity content test)

**Verification**: All 22 tests pass in 0.36s; `make integration` → 92 passed
(36 parse_newsletter + 34 graph_algorithms + 22 ts_contract).

81. **P2 Integration Tests: Flask API SQLite→DuckDB bridge** (2026-08-12)

New file: `tests/test_api_flask_integration.py` — 13 tests, 6 classes

**Purpose**: Cover the 6 Flask routes in app.py that had zero test coverage,
and exercise the SQLite → DuckDB → Flask bridge.

**Coverage (6 previously-uncovered routes)**:
- `GET /` — index/home page (HTML)
- `GET /findata` — findata viewer page (HTML)
- `GET /api/sectors` — sector listing API (validates classifications[],
  sector_entities[], super_sectors[] shape)
- `GET /api/entity/<path:entity_path>` — entity detail API (validates
  EntityDetail shape: EntityListItem + frontmatter + content + raw_content)
- `GET /debug/entity/<path:entity_path>` — debug page (always 200, echoes path)
- `GET /points_and_figures/images/<path:filename>` — image serving (not 500)

**Shape validation** (loose assertions, not full api.ts contract — that's P6):
- `/api/entities` → EntitiesResponse keys + EntityListItem keys per item
- `/api/stats` → StatsResponse keys (entity_counts, top_sectors, market_cap_counts, total_entities)
- `/api/search` → SearchResponse keys + SearchResult keys per hit
- `/api/events/<name>` → EventsResponse keys + EventItem keys per event

**Fixture**: Seeded SQLite DB with entities, entity_tags, graph_edges, events,
and note_search FTS5 tables. Uses `helpers.core.db.connect()` for proper
Row factory and pragma configuration.

**Verification**: All 13 tests pass in 0.35s; `make integration` → 105 passed
(36 P1 parse_newsletter + 34 P5 graph_algorithms + 22 P6 ts_contract + 13 P2 flask).

82. **P3 Integration Tests: derive_* chain (prose→edges→events→metrics)** (2026-08-12)

New file: `tests/test_integration_derive_chain.py` — 15 tests, 6 classes

**Purpose**: Verify the cross-module derive_* pipeline end-to-end.

**Chain tested**:
- Stage 1: `extract_relations(content, resolver)` → `apply_edges(edges, conn)`
- Stage 2: `derive_events.promote_from_edges(conn)` → `apply_events(events, conn)`
- Stage 3: `derive_insights.scan(file, conn)` → `apply_quotes` / `apply_metrics`
- Full chain: prose → edges → events (acquisition language → edge → event)
- Dry-run vs apply consistency across all four apply functions

**Key invariants verified**:
- Acquisition edge properties (year, stake, quote) flow through to event fields
- Date precision correctly derived from valid_from shape (year/month/day)
- DELETE-then-INSERT idempotency for events
- Quote extraction from [Concall] blocks with speaker attribution
- Metric extraction from financial prose (₹/crore/% patterns)
- Dry-run counts match actual insert counts

**Schema**: entities, graph_edges, events, quotes, company_metrics tables

**Verification**: All 15 tests pass in 0.26s
83. **P4 Integration Tests: Sector/filesystem layout invariants** (2026-08-12)

New file: `tests/test_integration_filesystem_layout.py` — 15 tests, 8 classes

**Purpose**: Verify filesystem↔DB consistency between findata/ tree and entities table.

**Invariants tested**:
- Every company entity has a .md file on disk
- Every sector entity has a .md file on disk
- Company files are under findata/Companies/ (sector files under Sectors/)
- sector_classification matches directory name in file_path
- file_path follows findata/Companies/<sector>/<slug>.md pattern
- Every company has a belongs_to edge to a sector
- belongs_to source is a company, target is a sector
- DB entity count matches file count on disk
- normalized_name is lowercase version of name
- Orphaned files (on disk but not in DB) are detectable

**Fixture**: Synthetic findata tree with 5 companies (Banking + Technology) and
2 sectors, plus seeded DB with entities + belongs_to edges.

**Verification**: All 15 tests pass in 0.22s

84. **P7 Integration Tests: Performance — algorithm scaling + correctness** (2026-08-12)

New file: `tests/test_integration_perf.py` — 27 tests (22 pass, 5 skip), 6 classes

**Purpose**: Verify graph algorithm correctness and scaling behavior on
synthetic graphs, plus write_analytics persistence round-trips.

**Test areas**:
1. **Metric correctness** (7 tests): degree, pagerank, betweenness, closeness,
   eigenvector, louvain — each produces valid output on known graphs
2. **Algorithm scaling** (4 tests): degree O(V+E), pagerank O(iter·(V+E)),
   approx betweenness O(sqrt(n)·E), louvain near-linear — doubling nodes
   stays within expected complexity bounds
3. **Mutation correctness** (5 tests): adding/removing edges is reflected
   by re-running metrics; bridge nodes have highest betweenness; clustered
   graphs have higher modularity than random
4. **write_analytics round-trip** (5 tests): numeric values, dict values,
   UPSERT, idempotency, multi-metric coexistence
5. **Multi-metric consistency** (3 tests): all metrics cover all nodes,
   running metrics doesn't mutate the graph
6. **End-to-end compute→persist** (3 tests): degree, betweenness, louvain
   computed on synthetic graph → written to DB → read back verified

**Scipy dependency**: 4 tests skip when scipy is not installed
(nx.pagerank requires scipy). All other metrics work without scipy.

**Verification**: 22 passed, 5 skipped in 0.64s

85. **P8 Integration Tests: parse_newsletter → validators round-trip** (2026-08-12)

New file: `tests/test_integration_validators.py` — 8 tests, 4 classes

**Purpose**: Verify entities created by parse_newsletter pass NotesValidator
checks, and that malformed entities are caught.

**Test areas**:
- Clean entity notes from `render_stub()` pass validation with 0 issues
- Multiple clean entities (across sectors) validate cleanly
- Validator catches: bad filename (lowercase), missing normalized_name,
  name mismatch, consecutive underscores
- `create_entity()` writes correct DB row + note file + bidirectional edges
- `create_entity()` is idempotent (second call is a no-op)

86. **P9 Integration Tests: SQLite → graph rebuild → DuckDB propagation** (2026-08-12)

New file: `tests/test_integration_graph_rebuild.py` — 12 tests, 5 classes

**Purpose**: Verify the critical "write to SQLite, rebuild graph, query via
DuckDB" workflow works correctly after mutations.

**Test areas**:
- Initial rebuild: node/company/sector/edge counts match SQLite
- Add entity → rebuild → verify visible in DuckDB (new company + new sector)
- Delete entity → rebuild → verify removed from DuckDB
- Edge mutation → rebuild → topology changes reflected
- Rebuild idempotency: double rebuild produces identical state

**Key finding**: duckpgq skips empty edge tables from the property graph
declaration — when all `part_of` edges are deleted, `sector_members()` raises
BinderException because the BelongsTo label isn't registered. This is correct
duckpgq behavior (CSR construction fails on empty tables).

87. **SQL Query Improvements A1-A3: cross-engine consistency fixes** (2026-08-13)

Three correctness/consistency fixes from `doc/improvements/archive/sql_query_improvements.txt`:

- **A1**: `market_cap_sql()` (db.py) — wrapped tag with `MIN()` so the SQLite
  correlated subselect matches DuckDB's deterministic alphabetically-first
  tie-break. Without this, a company with 2+ `market_cap/*` tags would get
  an arbitrary value from SQLite but a deterministic one from DuckDB.
- **A2**: `/api/stats` market_cap_counts (app.py) — dedup per company before
  grouping so `sum(buckets) == member_count` holds even under tag conflicts.
  The old SQL counted each tag separately, double-counting conflicted companies.
- **A3**: `v_node` CTAS (query.py) — added `ORDER BY e.entity_type, e.name`
  to `row_number()` so vertex IDs are deterministic across rebuilds.

All three are latent (0 conflicting tags, 4107 edges weight=1.0 today) but
close a real cross-engine divergence gap. Verified: 132 tests pass (test_db,
test_api_graph_bundles, test_graph, test_api_graph_metrics, test_api_graph_unit).
Conflict-injection test confirms A1+A2 handle dual market_cap tags correctly.

88. **SQL Query Improvements C6: graph-health CTE + conflicting_market_cap** (2026-08-13)

Replaced the 6 inline scalar subselects in `/api/graph/stats` (app.py) with a
single named-CTE block (`WITH mc_conflicts, edge_issues, company_issues`). The
CTE shares table scans (companies, edges, tags) for readability and adds a
new `conflicting_market_cap` hygiene counter — companies with >1 distinct
`market_cap/*` tag. This counter is the runtime tripwire for the A1/A2 fixes:
it surfaces the exact condition that would cause SQLite/DuckDB divergence.

New `hygiene.conflicting_market_cap` key in the `/api/graph/stats` response.
3 new tests in `TestGraphStatsConflictingMarketCap`:
- Clean DB → 0 conflicts
- Inject mid_cap on large_cap company → counter detects 1
- Inject conflicts on 2 companies → counter detects 2

89. **SQL Query Improvements B2-B4: profiled, B3 applied** (2026-08-13)

Profiled all three B-series items against the live graph (1208 entities,
4107 edges, DuckDB 1.5.4):

- **B2 (GRAPH_TABLE ORDER BY)**: SKIP. Python sort is 2.5x faster than
  DuckDB ORDER BY (0.74ms vs 1.83ms for 51 members). duckpgq's planner has
  ~1.5ms fixed overhead that dwarfs microsecond sorts on small lists.
- **B3 (/api/graph/metrics 2→1 queries)**: APPLIED. COUNT(*) OVER() replaces
  the separate COUNT(*) round-trip. 5.66ms → 1.66ms (3.4x faster). The second
  query was re-scanning graph_analytics (1109 rows with json_extract).
- **B4 (company_neighbors_bundle 10-UNION vs relational)**: SKIP. Relational
  query is 2.1x faster (~12ms saving) but would require duplicating duckpgq's
  edge-direction semantics, property extraction, and 8-bucket grouping in
  Python. Not worth the complexity at this scale.

90. **SQL Query Improvements C1/C3/C4: new graph analytics endpoints** (2026-08-13)

Three new read-only analytical query wrappers + matching API endpoints that
surface unconsumed signals from the live graph (no schema change needed):

- **C1: `/api/graph/co-mentions`** — Co-mention centrality ranking. 1329
  `co_mentioned_in` edges (the richest unconsumed signal) ranked by frequency.
  Top entities: HDFC AMC (28), CEAT (25), Canara Bank (23).
  Wrapper: `co_mention_top(n, conn=None)` in query.py. Supports `?top=N`.
- **C3: `/api/graph/bridges`** — Cross-sector M&A/JV bridges. 46 sector pairs
  where `acquired` or `jv_with` edges cross sector boundaries.
  Top: acquired FMCG↔Consumer (4), jv Automotive↔International (2).
  Wrapper: `cross_sector_bridges(conn=None)` in query.py.
- **C4: `/api/graph/edges-by-year`** — Temporal edge formation timeline.
  8 year×edge_type buckets from 2020-2026 (only acquired/jv_with carry dates).
  Wrapper: `edges_by_year(conn=None)` in query.py.

All three wrappers accept an optional `conn` param for testability (use the
monkeypatched test DB) or open a fresh `connect()` when called standalone.
10 new tests in `TestCoMentions` (4), `TestCrossSectorBridges` (3),
`TestEdgesByYear` (3) covering: empty DB, injected data, edge cases.

91. **Stateful/relational test plan — Slices 0+A+B+C+D complete** (2026-08-13)

Deterministic, seeded-fixture, oracle-based test strategy for the stateful /
relational layer (SQL builders, DuckDB graph analytics, Flask /api/graph stats,
transactional maintenance ops). All slices implemented and green:

- **Slice 0**: seeded SQLite fixture (`tests/fixtures/seed_research_db.py`) with a
  deterministic, hermetic graph (Cap Conflict Co carries two market_cap/* tags;
  co_mentioned_in / acquired / jv_with with valid_from; part_of membership).
- **Slice A**: 8 correctness tests (`tests/test_sql_query_correctness.py`) — F1/F2/F3
  green regression guards + 4 wrapper differential tests (co_mention_top,
  cross_sector_bridges, edges_by_year, sector_members_with_market_cap) vs independent
  SQL/Python references.
- **Slice B**: 3 perf-guard tripwires (`tests/test_sql_perf_guards.py`) asserting
  index usage (sqlite_autoindex_graph_edges_1 / entities_1) and no bare SCAN.
- **Slice C**: 3 DuckDB↔NetworkX equivalence tests (`tests/test_graph_algorithms.py::
  TestSliceCDuckDBEquivalence`) — shortest_path & find_cycles vs NetworkX over the
  seeded graph + pagerank/wcc determinism. **Surfaced + FIXED a real bug**:
  `find_cycles()` emitted non-simple closed walks; fixed by stopping the recursion
  once back at `start`.
- **Slice D**: 5 transactional-hardening tests (`tests/test_rename_entity.py` +
  `tests/test_maintenance_utils.py::TestMoveEntity`) — rename-to-existing-PK rollback,
  move_entity idempotent skip / dest-exists refused / non-canonical sector refused /
  rollback on relation-update error. **Surfaced + FIXED a real bug**: `move_entity()`
  moved the file before the DB writes (split-brain on DB failure); reordered so DB
  writes happen first, file move last.

Two production bugs found by the differential/transactional tests are now fixed.
Full plan + status: `doc/improvements/archive/stateful_relational_test_plan.txt`.

92. **ruff lint cleanup — 166 → 0, lint gated into `make qa`** (2026-08-13)

Adopted ruff as the single linter (replaces flake8; subsumes isort + most of
pyupgrade). Config: `ruff.toml` with `select = ["E","F"]`, `ignore = ["E501"]`,
`line-length = 100` — bug-catching only, not the full cosmetic ruleset. All 166
findings cleared with **0 false positives**:

- **126 safe + 6 unsafe auto-fixed** (`ruff check . --fix` / `--unsafe-fixes`):
  F401 unused-import ×99, F541 stray f-prefix ×21, F841 unused-var ×8 (incl. a
  genuinely-dead `EntityResolver` refactor leftover in `extract_relations.py`
  `_main` — the workers each build their own, by design), F811 dup-imports ×3,
  E713 ×1.
- **16 E402** → `ruff.toml` `[lint.per-file-ignores]` for `tests/**` + the 4
  helper scripts that bootstrap `sys.path`. Every E402 was an intentional
  sys.path / `importorskip` bootstrap, so a narrow per-file-ignore is cleaner
  than 16 inline `# noqa` (a real late-import elsewhere is still caught).
- **16 manual fixes**: E722 bare-except ×7 → `except Exception` (1 in `app.py`,
  6 yfinance guards in `get_tickers.py` — these were swallowing Ctrl-C);
  E741 ambiguous `l` ×7 → `label`/`line`; F821 ×1 (`snapshot_db.py` duckdb string
  annotation) → `TYPE_CHECKING`-guarded import.

**Real bug found & fixed (F811):** `tests/test_static_checks.py` defined
`test_merge_markers_detects_conflict` twice (lines 224 & 1087); Python's
last-wins silently shadowed the first body, so it was **never collected by
pytest**. Deleted the dead definition and ported its stronger `== 1` assertion
(verified `check_merge_markers` emits one failure per file) into the survivor.

**Gate:** `make lint` is green; `ruff check .` added as the first step of
`make qa` (fastest fail) so the 166 can't regress. ruff 0.16.2 caught a prior
real bug too (`db_maint.py` referenced `sys.stderr` with no `import sys` —
fixed at adoption time). Full detail: `doc/improvements/archive/lint_analysis.txt`.## 91. duckpgq retirement, Phase A — Onager-backed pagerank / WCC / clustering

**Date:** 2026-08-14 · **Proposal:** `doc/improvements/archive/duckpgq_retirement.txt`

Replaced the three duckpgq-native algorithm wrappers with Onager equivalents
(`onager_pagerank` / `onager_components` / `onager_clustering` in
`helpers/graph/onager.py`; `query.py` wrappers re-routed via
`_label_to_edge_type` + `_company_names`, signatures and sort orders
unchanged; `algorithms.py` handlers re-pointed and now honour the synthetic
`edges=` path). First step toward unpinning DuckDB 1.5.4 (duckpgq has no
1.5.5 build; onager installs on 1.4.3/1.5.4/1.5.5).

**Parity (live graph):** WCC partition identical to duckpgq (42 components,
same membership); clustering exact (max abs diff 0.0). duckpgq's pagerank
turned out to be **degenerate** on the BelongsTo graph — directed PR over
company→sector gives every company the identical teleport-floor score
(0.000471968752, zero variance, "ranking" carried no information). Onager's
undirected pagerank yields 33 distinct scores (tie groups = component sizes)
— a strict improvement, not just parity.

**Bonus fixes** — three latent bugs in `onager.py`'s `_materialize_from_db`
`edge_types` path (never exercised: all earlier metrics ran `edge_types=None`):
missing `params` on the first CTAS, `{where}`-before-JOIN ordering, and
positional `?` params repeated across two subqueries (switched to numbered
`$1` params).

**Deploy:** `graph_analytics` re-applied for the three metrics (1,068 company
rows each) with 42 stale non-company pagerank rows pruned. 10 new
known-value tests in `tests/test_onager_capabilities.py`; all affected
suites green (187 targeted tests); ruff + ty clean on touched files.
## 92. duckpgq retirement, Phases B/C/E — plain-SQL pattern queries, CTE shortest-path, infrastructure removed

**Date:** 2026-08-14 · **Proposal:** `doc/improvements/archive/duckpgq_retirement.txt` (now COMPLETE)

**Phase B** — all 26 `GRAPH_TABLE` MATCH sites in 17 query functions rewritten
as parameterised JOINs over the materialised `e_*`/`v_node` tables (`?` params
replace `_lit` interpolation; symmetric labels via CASE/WHERE-pair; bundles
stay single-round-trip UNION ALLs). **Phase C** — `shortest_path` is now a pure
recursive-CTE walk returning the full vertex sequence (the duckpgq ANY SHORTEST
branch's endpoints-only shape is gone). **Phase E** — `INSTALL/LOAD duckpgq`,
`_declare_property_graph`, the `DROP PROPERTY GRAPH` call, and the duckpgq half
of the P3.4 version-drift check deleted; `_SCHEMA_VERSION` bumped 8→9 (cold
rebuild drops stale `fin_graph`/`__duckpgq_internal` catalog entries);
`snapshot_db` verify switched to a read-only FK-join structural check (same
dropped-column detection contract, key `property_graph_ok` kept);
`test_duckpgq_capabilities.py` deleted (moot canary); `graph_design.txt`
rewritten (§5 marked HISTORICAL).

**Unpin validated:** duckdb 1.5.5 in a throwaway venv — connect (cold v9
rebuild), every rewritten query, and all three Onager metrics green with zero
duckpgq; pagerank values identical across 1.5.4/1.5.5. Bonus fix found by that
run: `COALESCE(e.weight, 1.0)` → `TRY_CAST` (1.5.5 rejects VARCHAR/DECIMAL
COALESCE). Project venv intentionally left on 1.5.4 for the user to bump.

**Verification:** 434 targeted tests green across 16 affected suites
(graph/query/wrappers/snapshot/maint/api/perf/fuzz); ruff + `ty check helpers
app.py` clean. One self-inflicted incident during E — an over-broad deletion
removed 1,186 lines of query functions — was fully recovered by re-applying
the B/C/E edit sequence against `git show HEAD` (assertion-checked
replacements); final file verified complete (all 24 public functions present).
## 93. Perf-test consolidation — wall-clock budgets single-homed in `make perf`; `slow` marker retired

**Date:** 2026-08-14

`tests/test_performance.py` held 13 wall-clock budget tests (`test_*_under_*s`)
marked `@pytest.mark.slow` that spawned canonical scripts via `subprocess.run`.
They duplicated `make perf` (`tests/run_perf_benchmarks.py`) with looser budgets
in 2 cases (static_checks 15s vs 5s, extract_relations 15s vs 5s), never ran
under `make qa`/`make test`, and contributed zero coverage under `make cover`
(subprocesses are invisible to pytest-cov) while burning ~22s. Folded into the
benchmark runner — a strict superset (18 budgets incl. louvain / betweenness /
eigenvector / parse_newsletter / enrich_yfinance) — and deleted.

What remains in `test_performance.py`, now in the default qa gate (0.06s):
2 complexity-class scaling guards (fuzzy-pair quadratic ratio < 6x,
fuzzy_match per-query linear ratio < 3x — ratio assertions, so no wall-clock
flakiness) + 2 connection-reuse units (get_connection memoization, idempotent
close). The `slow` marker was removed from pytest.ini (it had no users left;
--strict-markers guards reintroduction) and `-m "not live and not slow"`
simplified to `-m "not live"` in `make qa`/`make test`. The empty
`tests/test_sql_perf_guards.py` (0-byte husk since the 2026-08-13 squash; its
Slice-B autoindex tripwires were superseded by `tests/test_query_plans.py`)
was deleted. Verified: 1633 tests collect, `make perf` 18/18 green.
## 94. Onager link prediction — candidate missing-edge hypotheses (graph_algos Phase 1)

**Date:** 2026-08-14

Wired Onager's five link-prediction table functions into the graph layer —
a genuinely NEW capability (nothing in the repo previously suggested
candidate edges):

- `helpers/graph/onager.py`: `onager_link_prediction(con, edge_types,
  edges, method, top)` over `onager_lnk_{jaccard,adamic_adar,
  common_neighbors,pref_attach,resource_alloc}`. Returns
  `[(name1, name2, score)]` sorted desc, existing edges excluded (both
  directions), zero-signal pairs dropped. Default DB projection = the
  non-membership types (co_mentioned_in, jv_with, competes_with,
  same_group) so scores aren't trivially sector co-occurrence.
- `helpers/graph/algorithms.py`: advisory `link_prediction()` (deliberately
  NOT in `_METRIC_DISPATCH` — pair-valued, not node-keyed; `--apply`
  refused) + CLI `link-predict [--method ...] [--edge-types ...] [--top N]`.
- Two Onager quirks documented in-code: pair direction is NOT canonical
  (canonicalised via LEAST/GREATEST + DISTINCT in SQL — a `node1 < node2`
  filter silently returns {} on some layouts), and pref-attach is the
  undirected degree product.
- Tests: 11 unit (hand-computed scores on a 4-cycle: J=1.0, AA=2/ln2,
  RA=1.0, CN=2; chain/star top-N + ordering determinism; empty/unknown-
  method safety; name-keyed + default-projection DB paths) + 7 integration
  (dispatcher projection variants, CLI output, --apply refusal writes no
  graph_analytics rows). 32+41 green; ruff/ty/lint-audit green.
- `make perf` +1 benchmark (graph_link_prediction 0.76s / 2.0s, 19/19
  green); `make graph-algos` smoke now runs link-predict too.
- Phase 4 (onager version pin) dropped by user decision — no pin; onager
  tracks DuckDB (1.5.5). Phases 2-3 remain proposed (pending.md).
- **Update (same day): apply by default.** Open question #2 re-answered:
  the CLI persists to `graph_analytics` by default (metric
  `link_prediction` — per-node JSON candidates fanned out to both
  endpoints, with method + projection provenance; `--no-apply` opts out;
  `--top` caps display only). `link-predict` is part of `--all` /
  `make recompute-graph`; smoke + perf surfaces pass `--no-apply`.
  Live: 3509 candidate pairs -> 236 entity rows (jaccard, default
  projection). The canonicalisation fix also doubled the live candidate
  set (1813 -> 3509 pairs — the pre-fix `node1 < node2` filter was
  silently dropping half). Snapshot refreshed after the writes;
  `make perf` 19/19.

## 95. Onager whole-graph structural metrics — graph-stats + /api/graph/stats (graph_algos Phase 2)

**Date:** 2026-08-14

All eight `onager_mtr_*` functions wired into one round-trip
(`onager_graph_metrics`): density, diameter, radius, avg_path_length,
transitivity, triangles (unique = sum/3), avg_clustering, assortativity.
Exposed via `algorithms.graph_metrics()` (dispatcher seam), a new
"Structure (Onager, full edge set)" section in `make graph-stats`
(graceful degradation), and a `structure` block on `/api/graph/stats`
(null on graph-layer failure; SQLite payload stays authoritative).

Verified semantics (hand-checked): unweighted; reverse/duplicate rows
dedup; path metrics NULL on disconnected projections; assortativity 0.0 on
regular graphs; node set = edge endpoints. Live full-edge-set projection
is connected: density 0.0042, 5460 triangles, transitivity 0.271, avg
clustering 0.193, assortativity -0.405, diameter 8, radius 5, APL 4.32.

conftest improvement: `seeded_graph_sqlite_db` now patches
`helpers.graph.query.connect` (pinning the temp DB via its native
test-isolation `db_path` arg) instead of nothing — the app's real
connection caching/TTL/locking runs hermetically, the guard tests still
pass, and the module-global `_graph_con` cache is reset around each test
(previously leaked across tests).

Tests: 9 capability + 3 integration + 3 API-unit + 2 live-stats; 149
green across the touched suites; ruff/ty/lint-audit green. Phase 3 (extra
centralities) remains proposed; Phase 4 (pin) dropped.

## 96. Extra Onager centralities — harmonic, katz, laplacian, local_reaching, voterank (graph_algos Phase 3)

**Date:** 2026-08-15

Five new Onager wrappers + dispatcher/CLI entries; `--all`
(`make recompute-graph`) now refreshes 14 metrics. Each metric's semantics
was hand-verified before wrapping (exact star/path parity; see the
proposal's Phase 3 outcome log). Two findings of note:

- **katz alpha pin (1e-4)**: Onager's default alpha=0.1 diverges on the
  live graph (hub degree 89 → lambda_max > 10); the pinned default keeps
  a ~100x margin and its ranking is stable across [1e-4, 1e-2]. The
  divergence itself is guarded by a unit test on a 200-leaf star.
- **personalized_pagerank dropped (Onager bug)**: its personalization
  column is ignored, the restart node is hardcoded to node_id 1, and it
  errors without one. Documented in onager.py; revisit on a future Onager
  build.

voterank is list-valued: CLI `voterank` applies by default (like
link-predict), persisting {"seeds": [ordered names]} per seed node. Live
seed set: 10 sector hubs (Automotive first). Tests: 11 capability + 8
integration (incl. `--all --apply` persisting all five); snapshot
refreshed, `make perf` 19/19, ruff/ty/lint-audit green.

## 97. Docs refresh & token optimization — graph_design.txt, schema.md, findata.md, architecture.md

**Date:** 2026-08-15

All four `doc/` reference files rewritten against live state (every count,
column, and name re-verified against the DB/code before writing; no fact
copied from stale text). Combined 68,169 → 45,347 chars (~17k → ~11.3k
tokens, −33%).

- **graph_design.txt (−72%)**: duckpgq-era property-graph/PGQ/migration
  sections and the changelog blob collapsed into a History appendix; new
  §5 "Graph Algorithms Catalog" (node metrics incl. the katz alpha pin,
  link prediction, graph-valued, query-layer, persistence). 13 code
  §-references repointed.
- **schema.md (−38%)**: entity kinds corrected to 5, missing `events`
  indexes added, graph_analytics documented at 14 metrics with JSON shape,
  and a DuckDB cache schema section added (20 objects with semantic
  endpoint column names — previously documented nowhere).
- **findata.md (−38%)**: duplicated DB-schema section replaced by a
  pointer to schema.md; YAML templates rebuilt from a probe of all 1,068
  live notes (capitalized `sector:`, leading-slash permalinks, real
  market_cap value set); tag namespaces updated to live vocabulary.
- **architecture.md (−74%)**: data model now cross-points at schema.md
  with verified live counters; two factual errors corrected (FK pragma
  IS enabled in db.py; NetworkX/duckpgq retired); §9.7 doc-status list
  replaced by a doc map matching disk; codebase-memory-mcp worked
  examples compressed to the reusable dialect notes + audit patterns.

Dedup principle: schema.md is the single DB-schema source; the other docs
cross-point instead of duplicating.

## 98. Proposal-archive reference hygiene + query-cache test isolation fix

**Date:** 2026-08-15

Two closing items from the docs-refresh pass:

- **Reference hygiene**: 19 dangling `doc/improvements/proposals/` paths
  repointed to `archive/` across 13 files (query.py, algorithms.py,
  onager.py, snapshot_db.py, pending.md, completed.md, 8 test files) —
  residue from the duckpgq and integration_plan archives. No
  `proposals/` reference remains outside `archive/` itself.
- **Test isolation (real bug)**: running the six integration files before
  `test_graph.py` made `sector_members("Banking")` return a 2-row temp-DB
  result while `sector_members_with_market_cap` returned the real 51.
  Root cause: `_with_generation_cache` keys the process-global
  `_QUERY_CACHE` on the SQLite `db_meta` generation read from module
  globals, so results computed against a temp source DB pinned by
  `query.connect(db_path=...)` (whose seeded generation equals the live
  one) are served to later tests against the live DB. Production is
  unaffected (single fixed DB). Fix: autouse `_clear_graph_query_cache`
  fixture in tests/conftest.py clearing the cache before every test.
  Previously-poisoned 8-file batch + test_query_helpers + test_graph_disk:
  266 passed; ruff green (3 ty diagnostics on conftest are the
  pre-existing sys.path-injected imports, identical at HEAD).

## 99. `make graph-algos` smoke now exercises all 14 metrics read-only

**Date:** 2026-08-15

The smoke target ran only `pagerank --top 10` + `link-predict --top 5
--no-apply` — covering just 2 of the 14 metrics (both calls were already
read-only; node metrics require `--apply` — the "wrote by default" claim
in an earlier draft of this entry was wrong, see #100). Now a single `algorithms.py --all --no-apply`: all 12 node metrics +
link-predict + voterank, nothing written (verified: graph_analytics stayed
at 14,331 rows; three `--no-apply: nothing written` skip notices). Runtime
2.5s. `recompute-graph` (`--all --apply`) remains the persisting variant.

## 100. Uniform opt-in `--apply` for all graph-algorithm CLI writes (reverses D10)

**Date:** 2026-08-15

`algorithms.py` had two write regimes: the 12 node metrics were opt-in
`--apply`, but link-predict and voterank applied by DEFAULT (decision D10,
"hypotheses belong in the DB"). Two problems: (1) an exploratory
`link-predict --method adamic-adar` or custom `--edge-types` run silently
replaced the canonical persisted `link_prediction` table; (2) it was the
only default-writing CLI in a repo where every writer (parse_newsletter,
derive_*, build_sector_hierarchy) is dry-run by default.

Now one rule (D13): **nothing writes without `--apply`** — link-predict and
voterank included. `--no-apply` is kept as an explicit no-op alias (mutually
exclusive with `--apply`), so the Makefile smoke, the perf benchmark, and
existing habits keep working. `make recompute-graph` (`--all --apply`)
remains the canonical bulk refresh; `make maint-full` / `parse_newsletter
--with-analytics` cadence is unchanged.

Verified live: bare `link-predict` / `voterank` print the dry-run notice and
`graph_analytics` stays at 14,331 rows; `--apply` restores link_prediction
(236 rows) identically; `--apply --no-apply` errors cleanly;
`make recompute-graph` + `make graph-algos` + `make snapshot` all green.
Tests: 4 flipped/added in test_integration_graph_algorithms.py (dry-run
default + apply-with-flag for both commands); 83 passed across the three
pinned suites; ruff/ty green. graph_design.txt §5.2/§5.3 updated, D13 added
to the decision log; #99's incorrect "pagerank wrote by default" claim
corrected.

## 101. Uniform per-metric dry-run notice in `algorithms.py` CLI output

**Date:** 2026-08-15

Follow-up to #100: the node metrics ran silent in dry-run mode while
link-predict/voterank printed `[dry-run: nothing written to graph_analytics
(pass --apply to persist)]` — an inconsistent signal for a default that had
just flipped. Every node metric now prints the same notice after its result
(uniform per-metric write status; FAILed metrics still skip it since nothing
ran). Verified live on all 13 commands (exactly one notice each;
`graph_analytics` unchanged at 14,331 rows; `make graph-algos` prints 14 —
12 node metrics + link-predict + voterank). Test added
(`test_cli_node_metric_dry_run_notice`, using katz: pagerank/wcc/clustering
subset via `v_company`, which the integration fixture's raw-attach mock
doesn't build — worth knowing if CLI-testing those three). 58 passed; ruff/ty
green.

## 102. Doc-drift fixes: schema.md integrity table (12→15), stale duckpgq-era comments

**Date:** 2026-08-15

Found while fact-checking README.md against the four doc/ sources (the
README rewrite itself went into the base stack's `graph` patch):

- **`doc/schema.md`** — the integrity-check table listed 12 checks but
  `_CHECKS` registers 15; `check_quotes`, `check_company_metrics` and
  `check_db_meta` were missing. Table now mirrors the registry 1:1
  (same order): quotes/company_metrics = orphan + bad-`properties` JSON
  (tolerating a DB without the table); db_meta = missing table/generation
  row, non-integer generation, `PRAGMA user_version` drift vs
  `helpers.core.db.EXPECTED_USER_VERSION`, `schema_version` mirror drift.
- **`pyproject.toml`** — the duckdb dep still said "duckpgq is installed at
  runtime via INSTALL duckpgq" and "duckpgq is RETAINED for PGQ …" (stale
  since the 2026-08-14 retirement, #92). Replaced with the Onager-era
  truth: `INSTALL onager FROM community`, duckpgq/NetworkX retired,
  duckdb unpinned (D8). The floating orphan comment block above `pyarrow`
  was folded in.
- **Same-class present-tense drift swept**: `Makefile` graph-smoke help ×2
  and `helpers/graph/__init__.py` docstring ("DuckPGQ graph layer" → graph
  query layer / DuckDB+Onager); `onager.py` module + `_prepare` docstrings
  ("DuckPGQ already uses", "connect() already loaded duckpgq + onager" →
  connect() loads sqlite+vss, Onager loads lazily in `_prepare`);
  `tests/test_graph.py` module docstring; `query.py` `fresh_rebuild`
  docstring (duckpgq → Onager version bumps), the `as_of` interpolation
  comment (real reason: CTE fragment composition, not "duckpgq has no
  parameter binding"), and `_materialise_edges` (integer keys now required
  by Onager BIGINT endpoints, duckpgq mentioned as history only).
  Historical/phase-annotated mentions (query.py Phase B/C/E notes,
  completed.md, archives, graph_design decision log) deliberately kept.

Comment/docstring-only changes; ruff + `make types` green. No behavior
change.

## 103. Project venv bumped to DuckDB ≥ 1.5.5 (closes pending.md)

**Date:** 2026-08-15

`uv sync` upgraded the project venv from 1.5.4 to 1.5.5 — the last (and only)
item in `pending.md`, safe since the duckpgq retirement (#91/#92). Verified on
the 1.5.5 venv: `helpers.graph.query.connect()` loads and the Onager layer
loads cleanly (onager build `49ad15b` on 1.5.5, vs `eaaf2ea` on 1.5.4).
`pending.md` now empty.

## 104. VSS fallback stage in `get_tickers.resolve_entity` (closes deferred N5 item)

**Date:** 2026-08-15

Closed the first deferred N5 "Next steps" item: "Replace Python-side
`is_name_match()` in `get_tickers.py` with vector similarity".

Context: `is_name_match` (a pure string-containment/token heuristic) had
already been superseded by the `fuzzy_match` hybrid (exact → abbreviation →
word-overlap → spellfix1) in `helpers/core/fuzzy_match.py`, but the deferred
item's actual intent — vector similarity — was never wired in.

What landed:
- `vss_match(query, entities, conn=None, db_path=None, threshold=0.5,
  embed_fn=None)` in `helpers/core/get_tickers.py`: pure-Python cosine scan
  over the SQLite `company_embeddings` table (no DuckDB dependency, keeping
  get_tickers a standalone CLI). Query is embedded with the same embedder
  used at population time (dry-run pseudo-embeddings by default); real (API)
  models skip unless `embed_fn` is injected. Restricts to the `entities`
  allowlist, returns `(name, score)` or `(None, 0.0)`, never raises.
- `resolve_entity(..., vss_conn=None)` gains a VSS fallback stage: fires only
  after every heuristic misses, returns `method="vss"`.

Honest limitation (documented in the docstring): with dry-run hash-based
pseudo-embeddings the stage is effectively inert for fuzzy names (hashes of
different strings don't align — verified cosine 0.08 for CEAT name vs note).
It becomes meaningful when real (OpenAI/`--api`) embeddings are populated,
where "Tata Consultancy Services" and "Tata Consultancy Services Limited" are
semantically close.

Tests: 7 new in `tests/test_get_tickers.py` (exact-name match, entities
allowlist restriction, threshold gating, empty table, missing table,
resolve_entity VSS-fallback, heuristic-wins-before-VSS ordering). 35 passed
(was 28);
ruff/ty green. Test-file header updated (still referenced removed
`is_name_match`).

Related doc edits: `completed.md` N5 "Next steps" line marked DONE.


## 105. `/api/graph/semantic/<name>` endpoint — VSS neighbours over HTTP (closes deferred N5 item)

**Date:** 2026-08-15

Closed the second deferred N5 "Next steps" item: "Add `/api/graph/semantic/<name>`
endpoint in `app.py`". The VSS stage already existed on the CLI side
(`semantic-neighbors` in `helpers/graph/query.py`); this exposes it over HTTP.

What landed:
- `GET /api/graph/semantic/<name>` in `app.py` (registered before
  `/api/graph/sector/<name>` so `<path:name>` never swallows `sector`).
- Resolves `<name>` case-insensitively through `_resolve_entity_or_404` →
  JSON 404 for unknown entities (matches `/api/graph/peers` behaviour).
- Query params: `k` (default 10, non-negative int, 400 on bad/negative),
  `metric` (`cosine` | `ip`, 400 on unknown), `cross_sector` (bool-ish).
- Delegates to `helpers.graph.query.semantic_neighbors`, returns
  `{company, k, metric, cross_sector, neighbors: [{name, sector, similarity}]}`.
  Empty `neighbors` is a valid 200 (no embeddings in the DB), not an error.
- Documented in the `graph_design.txt` API table.

Verification:
- Unit tests (7) in `tests/test_api_graph_unit.py::TestGraphSemantic`: response
  shape + canonical-name plumbing (monkeypatched `semantic_neighbors`), Ducks/
  cross_sector flag, 404 unknown, 400 bad/negative k, 400 bad metric, empty
  neighbours → 200. 55 passed in that file.
- Live tests (3) in `tests/test_api_graph_live.py`: CEAT?k=5 shape + self
  excluded, cross_sector=true, 404 unknown — 24 passed in that file (fresh
  `_reset_graph_connection()` first, since the module's synthetic-connect-failure
  test TTL-caches a broken graph connection).
- Live smoke: `GET /api/graph/semantic/CEAT?k=3` → 200, 3 neighbours
  (Emcure Pharmaceuticals 0.178, Bevco 0.149, Ethos 0.137); unknown → 404;
  `k=abc` / `metric=bogus` / `k=-1` → 400.
- ruff + ty green on `app.py` and both test files.

Honest limitation (shared with #103): with dry-run hash-based pseudo-embeddings
the neighbours are low-similarity (max ~0.18) and only loosely topical; the
endpoint is fully wired and tested, and becomes genuinely useful once real
(OpenAI/`--api`) embeddings are populated.


## 106. `note_search` embedding column + hybrid ranking (closes deferred N5 item)

**Date:** 2026-08-15

Closed the third deferred N5 "Next steps" item: "Add embedding column to
`note_search` FTS5 table in `rebuild_note_search.py` for hybrid ranking".

What landed:

- **`rebuild_note_search.py`**: `note_search` gains an `embedding UNINDEXED`
  FTS5 column (stored per row, never tokenized). `_collect_rows` now emits
  6-tuples; each doc is embedded over `title + sector + body` (truncated to
  8k chars) with a deterministic pseudo-embedding by default (`_default_embed`
  → `helpers.graph.embeddings._pseudo_embedding`, 384-dim), or any injected
  `embed_fn(text) -> list[float]` for the real-embedding path. Embedder
  failure degrades to a NULL embedding (row stays searchable, never raises).
- **Schema migration**: FTS5 can't `ALTER TABLE ADD COLUMN`, so `_migrate_schema`
  drops a stale pre-embedding table and lets the new DDL recreate it (safe:
  the rebuild repopulates every run). `stats["migrated"]` reports it; the CLI
  prints an explicit `(schema migrated: ...)` line + `embedded N rows`.
- **`app.py` `/api/search`**: new `hybrid=1|true` param. When set, the query is
  embedded with the same pseudo-embedder, each candidate's cosine similarity is
  computed, and the BM25 + cosine ranks are fused with Reciprocal Rank Fusion
  (RRF, k=60). Pagination is preserved by fetching the top `limit+offset`
  candidates then slicing after fusion. Each hit gains `similarity` (float;
  `null` in plain mode). Degrades gracefully to FTS-only (no 500) when the
  `embedding` column is absent (pre-migration schema).
- **`frontend/types/api.ts`**: `SearchResult.similarity: number | null`.

Verification:
- Rebuild: 1226 docs indexed + embedded on the live DB; schema migrated.
- Unit tests: 6 new in `tests/test_rebuild_note_search.py::TestEmbeddingColumn`
  (schema has column, all rows embedded, injected embed_fn used, embedder
  failure keeps row searchable, legacy-table migration, idempotence) — 10 pass
  in that file. 5 new in `tests/test_api_search.py::TestHybridSearch` (shape +
  similarity float, plain → null, RRF re-orders vs plain, pagination window,
  graceful degradation on missing column) — 12 pass in that file.
- `pytest -m "not live"` 1521 passed; `make frontend-check` (tsc strict) green;
  ruff + ty + deptry green; TS-contract SearchResult updated and green.
- Live smoke: `?q=shrimp feed&hybrid=true` re-orders Thai_Union_Frozen_Products
  (highest cosine 0.11) above Avanti/Sharat under RRF.

Honest limitation (shared with #103/#104): the default pseudo-embedding is
hash-based, so out-of-the-box hybrid re-ranking is lexical-ish (query and doc
pseudo-vectors rarely align semantically). The plumbing — column, migration,
embed_fn injection, RRF fusion, HTTP param — is complete and testable, and
becomes genuinely semantic once real (`--api`) embeddings are populated on both
the rebuild and (via the matching embed_fn) the query side.
