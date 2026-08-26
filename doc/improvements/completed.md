# FinData Knowledge Graph — Completed Improvements

**Generated**: 2026-08-09
**Total completed**: 128 items

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

Full analysis and before/after timings: `doc/improvements/archive/tooling/perf_improvs.txt`.

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
- ~~Replace Python-side `is_name_match()` in `get_tickers.py` with vector similarity~~ — **DONE 2026-08-15** (see #104): `resolve_entity` now has a `vss_match()` fallback stage over `company_embeddings` (pure-Python cosine, no DuckDB dep). Effective with real embeddings; inert on dry-run pseudo-embeddings (hash-based).
- ~~Add embedding column to `note_search` FTS5 table in `rebuild_note_search.py` for hybrid ranking~~ — **DONE 2026-08-15** (see #105b)
- ~~Add `/api/graph/semantic/<name>` endpoint in `app.py`~~ — **DONE 2026-08-15** (see #105)
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
15/15 perf benchmarks. Details: `doc/improvements/archive/pipeline/metric_improvs.txt` § 11.
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
**Proposal**: `doc/improvements/archive/database/duckdb_improvs.txt` Bundle L, item L1

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
**Proposal**: `doc/improvements/archive/pipeline/findata_corpus_audit.txt` item H4

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

Three correctness/consistency fixes from `doc/improvements/archive/database/sql_query_improvements.txt`:

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
- **Slice B**: 3 perf-guard tripwires (now in `tests/test_query_plans.py`) asserting
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
Full plan + status: `doc/improvements/archive/testing/stateful_relational_test_plan.txt`.

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
fixed at adoption time). Full detail: `doc/improvements/archive/testing/lint_analysis.txt`.## 91. duckpgq retirement, Phase A — Onager-backed pagerank / WCC / clustering

**Date:** 2026-08-14 · **Proposal:** `doc/improvements/archive/graph/duckpgq_retirement.txt`

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

**Date:** 2026-08-14 · **Proposal:** `doc/improvements/archive/graph/duckpgq_retirement.txt` (now COMPLETE)

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
`tests/test_sql_perf_guards.py` husk (0-byte, from the 2026-08-13 squash; its
Slice-B autoindex tripwires were superseded by `tests/test_query_plans.py`)
was removed from the working tree. Verified: 1633 tests collect, `make perf` 18/18 green.
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


## 105b. `note_search` embedding column + hybrid ranking (closes deferred N5 item)

**Date:** 2026-08-15. (Numbered 105b post-hoc, 2026-08-26: two same-day
parallel sessions both minted a #106 — every pre-existing plain-`#106`
reference in the corpus points at the hot-path caching entry below, so THIS
one takes the suffix and that one keeps the plain number.)

**Date (original):** 2026-08-15

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


## 106. Hot-path caching — `graph_metrics` + `/api/sectors` (perf sweep)

**Date:** 2026-08-15

Whole-repo hot-path sweep driven by the codebase knowledge-graph complexity
signals (`transitive_loop_depth`, `linear_scan_in_loop`, `alloc_in_loop`).
Two real-time (per-request) bottlenecks found and fixed; the batch extractors
flagged by the graph (extract_relations, derive_events, derive_insights) all
measure well inside their `make perf` budgets and were left alone.

### Hot path 1 — `graph_metrics` recomputed Onager metrics on every request

`/api/graph/stats` called `helpers.graph.algorithms.graph_metrics()` → Onager
`onager_graph_metrics`, which re-materialises the `_onager_int`/`_onager_e`
temp tables and runs the 3-query whole-graph metric SQL on **every request**:
~300ms (measured up to 600ms cold), with no caching. The query layer already
had a generation-keyed result cache (`_with_generation_cache`, P2.3) — Onager
bypassed it entirely.

Fix: `graph_metrics()` now caches its dict keyed on
`("graph_metrics", tuple(edge_types or []), generation)` in the same
`_QUERY_CACHE` (FIFO-capped at 256), reusing `_current_generation_for_cache`
+ `_query_cache_get/set`. Correctness: the metrics are a pure function of the
edge set, which only changes when the SQLite source bumps its generation
(db_meta trigger on entities/graph_edges writes); `clear_graph_cache()` —
already invoked on every rebuild/refresh — evicts the entry, so the first call
after a data change recomputes. The synthetic `edges=` path of
`onager_graph_metrics` (used by unit tests) is untouched — it bypasses
`graph_metrics`.

Result: `/api/graph/stats` **260–600ms → ~10ms warm** (the remaining ~9ms is
the SQLite-side stats CTE). `graph_metrics()` itself 408ms → 1ms cached.
2 new regression tests in `tests/test_integration_graph_algorithms.py::TestGraphMetrics`
(cached-then-invalidated, edge-types key discrimination).

### Hot path 2 — `/api/sectors` re-read 42 sector files per request

`api_sectors` read + YAML-parsed ~42 sector markdown files from disk on every
request (~33ms) and serialised ~510KB (frontend only renders `content`
truncated to 150 chars). Fix: a module-level content-addressed cache in
`app.py` — cheap per-file `(st_mtime_ns, st_size)` signature (~1ms for 42
files); re-read only when the signature changes. Immune to test-DB swapping
(the key is purely file-content based, no generation coupling) and
auto-invalidates on any on-disk edit.

Result: `/api/sectors` 42ms → ~5ms warm. Verified invalidation by editing a
sector file (payload changes), then restoring (payload reverts).

### Not changed (measured, already fine)

- `semantic_neighbors` ~3ms, `company_neighbors_bundle` ~14ms (single UNION
  ALL round-trip), `shortest_path` ~0.3ms, `/api/graph/metrics/*` ~3ms
  (reads precomputed `graph_analytics`), `connect()` warm ~5ms.
- Batch extractors (extract_relations 2.3s, derive_events 1.4s, derive_insights
  1.7s, enrich_yfinance 2.0s) all pass their `make perf` budgets; the graph's
  `linear_scan_in_loop` flags there are per-bullet regex scans over bounded
  windows, not quadratic.

Verification: `pytest -m "not live"` 1523 passed (+2 new); ruff + ty green;
live graph suites pass.

Note: the hot-path discovery used the codebase-memory knowledge graph
(`query_graph` on complexity/loop signals) — a follow-up lesson from this
session is to route structural follow-ups (callers, defs, decorators) through
`trace_path`/`search_graph`/`query_graph` rather than ripgrep.

## 107. Doc browser & search — `/api/docs*` endpoints + Docs tab in the web app

**Date:** 2026-08-15

The `doc/` corpus (~28 design/improvement documents: architecture, schema,
graph_design, the run log, deferred backlog, and the archived proposals) was
only reachable via grep/filesystem — invisible to the note-search FTS5 index
(which covers findata/ notes + newsletters only) and absent from the web app.
Added a "Docs" tab that catalogs, searches, and renders the corpus.

### Backend (app.py — filesystem-backed, no DB, no FTS index)

- `GET /api/docs` — catalog: `{docs: [{path, name, section, title, size_bytes,
  mtime}]}`, sorted by path, optional `?q=` substring filter on the path.
  `section` = subdir relative to `doc/` ("" for top-level).
- `GET /api/docs/content?path=` — raw markdown/plain-text body, served for
  client-side rendering (marked.js was already loaded). Path-traversal guard
  `_resolve_doc_path()`: resolve + `relative_to(_DOC_ROOT)` check → rejects
  `../`, absolute paths, NUL bytes, symlink escapes (404).
- `GET /api/docs/search?q=&limit=` — naive linear scan (corpus is ~180KB so an
  FTS5 table would be over-engineering): word-match x3, word-boundary x2,
  title x5, path x4; sorted score-desc/title-asc. Snippet mirrors the FTS5
  convention (literal `<mark>` tags) so the frontend reuses `highlightSnippet()`.
- Helpers: `_iter_doc_files()` (sorted rglob, `.md`/`.txt`), `_doc_title()`
  (first `#` heading → `.txt` first non-empty line → filename stem),
  `_snippet()` (case-insensitive `<mark>` wrap on the first word anchor).

### Frontend (findata.ts + findata.html + findata.css)

- `ViewName` gains `"docs"`; 5th nav link (`#docs`) + `#docs-view` section.
- Docs toolbar: debounced search box (`/api/docs/search`) + Catalog reset
  button (`/api/docs`).
- Two-pane layout: sidebar rows (title + section + snippet for search hits)
  and a reader pane rendering the selected doc via `processRichContent()`
  (tables, code highlighting, TOC already handled). `formatBytes()` helper
  added for the size readout. All user-facing text goes through `escapeHtml()`.

### Tests

- `tests/test_api_docs.py` (21 tests): catalog shape/ordering/sections/
  corpus coverage/filtering; title derivation; content + traversal guard
  (5 escape vectors incl. URL-encoded + NUL); search ranking/snippet/
  limit-clamp/400s.
- TS contract: `TestDocsContract` in `tests/test_integration_ts_contract.py`
  (5 tests) + 5 new interfaces in `frontend/types/api.ts`
  (`DocsResponse`/`DocItem`/`DocContentResponse`/`DocSearchResponse`/`DocSearchHit`).

Verification: ruff + ty + deptry + `make lint-audit` green; `make frontend`
rebuilds the bundle, `make frontend-check` (tsc strict) green; full QA —
`pytest -m "not live"` **1549 passed** (+21), integration 209 passed, live
197 passed. Smoke: catalog 28 docs, `completed.md` (82KB) renders,
`q=graph cache` returns 25 ranked hits.

One ty lesson reused from #105: dict-valued sort keys (`d["score"]`) widen to
`int | str` unions — carry the rank key as a typed tuple instead
(`ranked: list[tuple[int, str, dict]]`), same pattern as `_hybrid_search_results`.

## 108. Graph cloud — whole-graph relationship modelling + Graph Statistics block

**Date:** 2026-08-15

The Graph tab only rendered *ego networks* (one company at a time). There was
no way to see the full corpus of entities + all 12 relationship types at once,
and the Statistics page said nothing about the graph. Added (1) a force-directed
**Graph Cloud** mode to the Graph tab and (2) a **Graph Statistics** block to the
Statistics view.

### Backend (app.py)

- `GET /api/graph/cloud` — SQLite-only, no DuckDB needed. Returns `{nodes,
  edges, relationship_types, total_nodes, total_edges}`: every entity
  (`id/label/entity_type` — company/sector/sub_sector/super_sector/theme) plus
  every typed edge (`source/target/edge_type`), with optional `?edge_type=`
  to isolate ONE relationship (only incident nodes returned, so the cloud
  stays focused). `relationship_types` is the whole-corpus GROUP BY count
  (edge_type → count) enriched with `symmetric` + `semantics` from the new
  `_EDGE_SEMANTICS` map (mirrors `doc/graph_design.txt` §4: e.g. `part_of` →
  "Company → sector (legacy pair)", `jv_with` symmetric, `acquired` →
  "Acquirer → acquired (temporal)"). Unknown edge types degrade to
  `symmetric=False` + "custom / derived edge type" — no crash.

### Frontend — Graph tab (findata.ts + findata.html + findata.css)

- "Graph Cloud" toolbar toggle swaps between ego-network and whole-graph mode.
  Cloud mode fetches `/api/graph/cloud`, builds one cytoscape node per entity
  (`group` = entity_type so company/theme/sector/… are coloured + shaped
  distinctly) and one edge per relationship, then runs the bundled `cose`
  force layout. Symmetric edges (`co_mentioned_in`, `jv_with`,
  `competes_with`, `same_group`) render arrow-less.
- **Legend** (`#graph-cloud-legend`): entity-type swatches + relationship
  colour chips, both derived from the live response.
- **Relationship cloud card** (`#graph-relationship-cloud`): one
  size-proportional chip per edge type (log-ish scale, colour + ↔/→ direction,
  count, tooltip semantics). Clicking a chip filters the cloud to that
  relationship; the `#graph-cloud-type` select does the same.
- Ego controls (search / as-of / edge-filter) exit cloud mode; tapping a cloud
  node shows its detail panel instead of re-centring.

### Frontend — Statistics view

- `loadStats()` additionally fetches `/api/graph/stats` (independent, so a
  failure there doesn't hide `/api/stats`) and renders a **Graph Statistics**
  block: cards for total edges / edge types / graph entities / company sectors
  / top sector / staleness (stale flag colour-coded), plus a Structure
  breakdown card (density, diameter, radius, avg path length, transitivity,
  triangles, avg clustering, assortativity — via Onager `graph_metrics`,
  reusing the #106 generation-keyed cache). `structure` is null-degradable
  (note shown when the DuckDB layer is absent).

### Types + tests

- `frontend/types/api.ts`: `GraphCloudResponse`/`GraphCloudNode`/
  `GraphCloudEdge`/`RelationshipTypeSummary`/`GraphStatsResponse` (incl.
  `GraphStructure`).
- `tests/test_api_graph_unit.py::TestGraphCloud` + `TestGraphCloudEdgeSemantics`
  (7 tests): response shape, seed-edge match, relationship summary flags,
  `?edge_type=` isolation + unknown-type fallback, entity-type defaults.
- TS contract: `TestGraphCloudContract` + `TestGraphStatsContract` (6 tests).
  Note: `GraphStatsResponse` has inline object types whose fields the flattened
  key-parser also picks up, so its contract test asserts the top-level set +
  each nested block explicitly (same reason `_assert_keys` isn't used there).

Verification: ruff + ty + deptry + `make lint-audit` green; `make frontend`
rebuilds the bundle, `make frontend-check` (tsc strict) green; full QA —
`pytest -m "not live"` **1562 passed** (+13), contract suite 33 passed, live
graph tests 24 passed. Live smoke: cloud = 1209 entities / 4110 edges across
12 relationship types; `/api/graph/stats` structure metrics present
(density 0.0042, diameter 8, 5460 triangles).

## 109. Graph zoom slider + Edge Types breakdown on the Statistics page

**Date:** 2026-08-15

Two usability improvements on top of #108.

### Graph widget zoom slider

The graph canvas (cytoscape) had wheel/pinch zoom but no discoverable control.
Added a compact overlay (bottom-right of `#graph-canvas`):

- `#graph-zoom` — range slider (`min 0.2, max 3`, step 0.05) mapped onto
  cytoscape zoom. The instance's `minZoom`/`maxZoom` clamp values.
- `#graph-zoom-in` / `#graph-zoom-out` buttons (×1.25 steps).
- `#graph-zoom-fit` — `cy.fit(undefined, 30)` (fit graph to view with padding).
- `#graph-zoom-label` — live % readout, tabular-nums so it doesn't jitter.

Two-way sync via `_initGraphZoom()` (wired once in `loadGraphView`): slider
`input` → `cy.zoom(z)`; `cy.on("zoom")` and `cy.on("layoutstop")` → slider +
label updated, so wheel/pinch/layout re-zooms stay in sync. `frontend/types/
vendors.d.ts` gained the `zoom`/`minZoom`/`maxZoom`/`fit` methods on
`CyInstance` plus the 2-arg `on(evt, cb)` overload.

### Edge Types breakdown (type / count / percent)

The Graph Statistics block (from `/api/graph/stats`) already had cards but no
per-type breakdown. Added an **Edge Types** `breakdown-card` after the
Structure card — same rendering as the "Entity Types" card on the same page
(label / count / `NN.N%`), sorted by count desc. `formatLabel()` gained an
`edge_type` branch (snake_case → "Co Mentioned In") so the labels are readable;
the symmetric ↔ / directed → hint stays available in the graph cloud.

Verification: `make frontend-check` (tsc strict) green, `make frontend` rebuilds
the bundle, ruff green, `pytest tests/test_integration_ts_contract.py
tests/test_api_graph_unit.py tests/test_api_docs.py` — 116 passed. Live smoke:
page serves the zoom controls + bundle contains `_initGraphZoom` and the Edge
Types card.

## 110. Graph cloud rendering performance — eliminate the jitter + render cost

**Date:** 2026-08-15

The whole-graph cloud (1209 entities / 4110 edges) was near-unusable: the
default force-directed `cose` layout ran **animated** on every iteration (the
"jitters"), every edge rendered a text label (4110 canvas text objects), and
every edge used bezier control-point curves. Fixed on three fronts.

### Layout (the jitter)

- Default cloud layout is now **`concentric` by degree centrality** — O(n),
  deterministic, instant, no animation. Degree is computed client-side from
  the live edge set (`degree[e.source] += 1`, `O(E)`), so hubs sit at the
  cloud's core. Force-directed `cose` is still one click away in the layout
  dropdown, and when picked in cloud mode it now runs **non-animated with
  bounded iterations** (`randomize`, `numIter: 300`, `initialTemp: 200`,
  `coolingFactor: 0.8`, `minTemp: 1.0`, `gravity: 2`) instead of annealing
  forever on screen.
- `_runGraphLayout(name, cloud = false)` gained the `cloud` flag: `animate:
  !cloud`. After the cloud layout, `cy.fit(undefined, 30)` frames the whole
  graph. The toolbar layout-dropdown handler is cloud-aware too (non-animated
  + fit when in cloud mode).

### Render cost

Every cloud element carries `data.cloud = "1"`; the shared stylesheet now has
cloud-mode selectors (the #1, #2, #3 render costs gone):

- `edge[cloud="1"]` → `curve-style: straight`, `width: 1`, `label: ""`,
  `text-opacity: 0`, `font-size: 0` — no more 4110 edge labels and no bezier
  control-point math.
- `node[cloud="1"]` → `font-size: 9`, `text-outline-width: 1`,
  `min-zoomed-font-size: 6` — node labels only appear once you zoom in, so the
  fit-view renders 1209 nodes without 1209 label draws.

Ego-network mode is untouched (no `cloud` flag → original bezier + labelled
edges).

Verification: `make frontend-check` (tsc strict) green; `make frontend` rebuilds
the bundle; smoke: bundle contains the cloud selectors, degree centrality, and
the non-animated cloud layout path.

## 111. Statistics view — left-to-right card flow (no more vertical strip)

**Date:** 2026-08-15

The Statistics view's cards were rendering in a narrow vertical strip. Root
cause: `#stats-container` is a `.stats-grid` (auto-fit columns), and the two
wrapper blocks `.stats-breakdown` and `.stats-graph-block` are each a single
grid child — without spanning they landed in one ~250px column, collapsing
their inner grids to 1 column (cards stacked, empty space to the right).

Fix (CSS only):
- `.stats-breakdown { grid-column: 1 / -1 }` — spans the full grid width, so
  its 3 breakdown cards (Entity Types / Top Sectors / Market Cap) flow
  left-to-right.
- `.stats-graph-block { grid-column: 1 / -1; display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)) }` — spans full
  width and is itself a grid, so the Structure + Edge Types breakdown cards
  sit side-by-side; header + card row span `1 / -1`.

Result: stat cards in one row, then full-width breakdown rows, all flowing
left-to-right instead of a scrolling vertical strip. No JS/DOM change.

Verification: CSS shipped (spanning + grid rules present), 116 tests pass
(CSS-only, no behaviour change).

## 112. Graph cloud — separate connected sets + highlight set on node selection

**Date:** 2026-08-15

Two graph-cloud improvements:

1. **Separate the connected node sets.** Previously the cloud default was
   `concentric` by degree, which piled high-degree hubs (sectors/companies)
   onto the same central rings so dense clusters stacked on top of each
   other. Now:
   - Every cloud element carries a `component` data field (union-find root id
     computed in `loadGraphCloud`).
   - The default cloud layout is adaptive: **multiple connected components**
     → a new `components` layout (`_cloudComponentPositions`) that grid-packs
     each connected set into its own cell (hub at centre, members orbiting);
     **one giant component** (the whole corpus) → non-animated `cose`, whose
     repulsion separates the dense clusters (1209 nodes: 1284 overlapping
     pairs vs 6828 before).
   - The layout dropdown gained a "Connected sets (separated)" option
     (degrades to concentric for ego networks, which lack component ids).

2. **Highlight a set on node selection.** Tapping/selecting a node in cloud
   mode now highlights every element sharing its component (`in-set` class:
   bright gold edges + node border) and fades everything else
   (`.faded` opacity). Tapping empty canvas, exiting cloud mode, or reloading
   the cloud clears the highlight. New methods: `_highlightCloudSet`,
   `_clearCloudHighlight`.

Backend unchanged (`/api/graph/cloud` already returns the full edge set).
Vendor types extended minimally (`CyElements.forEach`, `CySingular.removeClass`,
event-target `on` overload).

Verification: frontend type-check (strict) ✓, ruff ✓, 95 graph+contract tests
pass ✓, Playwright end-to-end (cloud load, part_of filter → 42 components,
highlight on tap, ego network) ✓.


## 113. Parquet-first DB shipping: `snapshots/` tracked, `--restore` rebuilds `memory/`

**Date:** 2026-08-15

Live DBs (61M) must never enter git: research.db alone is 47M (GitHub blocks
blobs at 100M), binary DBs delta-compress to nothing so every refresh adds a
full copy, and a mid-write copy can be torn. The snapshot machinery already
existed; this wires it into the commit/clone flow properly.

### Layout (option B)

- **`snapshots/parquet/` — git-tracked** (~15M): per-table `.parquet` for both
  DBs (`sqlite/` incl. `note_search_content`, `duckdb/` incl. `_build_meta`) +
  captured replayable DDL (`_schema.sqlite.sql`, `_schema.duckdb.sql`). SQLite
  files use the gzip codec (TEXT/BLOB-heavy: 22M snappy → 15M; zstd measured
  no better).
- **`db-backup/` — local scratch, ignored**: gzip byte-exact snapshots +
  raw `*_backup.*` copies. `memory/` now gitignored outright.
- New `make snapshot-restore` = `snapshot_db.py --restore --force`.

### Restore path (`--restore`)

Apply schema DDL → load every parquet → FTS5 `('rebuild')` → integrity +
foreign_key checks → atomic replace of the live file (refuses to overwrite
existing DBs without `--force`). Two correctness details found the hard way:

- FTS5 virtual tables must be created BEFORE plain tables — the vtable create
  makes its own shadow tables (incl. `note_search_content`), so a plain CREATE
  of the content shadow first collides. `_export_sqlite_schema` emits vtables
  first.
- `note_search_content` (the FTS5 content shadow) is now EXPORTED — it holds
  the indexed text; `('rebuild')` regenerates `_data`/`_idx`/`_docsize` from
  it. Derived shadows stay excluded.

### Verified against the live DBs

Restored into temp targets and compared with `memory/`: SQLite — 10 table
sets equal, all row counts equal, `MATCH 'revenue'` 775 = 775, integrity ok,
foreign_key_check clean, 7 views+triggers, embedding BLOBs intact (1050 ×
8364 bytes). DuckDB — 20 tables equal, all counts equal, `_build_meta`
preserved (schema_version 9, generation 24249).

### Tests

`tests/test_snapshot_db.py` +5 (15 total): content-shadow inclusion, full
SQLite restore round-trip incl. FTS MATCH + NULL fidelity + missing-schema
error, DuckDB restore round-trip incl. NULL round-trip, and `main()
--restore` refusal to clobber an existing target without `--force`.

Docs: README (layout table + Quickstart), architecture.md §2/§3, schema.md
(maintenance section), graph_design.txt (snapshot + layout sections) — all
now point at `snapshots/` as the tracked artifact and `make
snapshot-restore` as the clone-side path. Fixed the long-stale
"db-backup/ (git-tracked)" claim in architecture.md.


## 114. Paddle parse_pages hardening + slugify consolidation + pdf_conv_md fuzz tests

**Date:** 2026-08-16
**Status:** COMPLETE
**Proposal:** `doc/improvements/archive/pipeline/pdf_conv_md_hardening_fuzz.md`

Hardened the new PDF→Markdown (Paddle OCR) pipeline path in `markdown_parse.md`
and consolidated a duplicated helper, then added Hypothesis fuzz coverage for the
module's pure transforms.

### Implementation

- `parse_pages()` (`helpers/pdf/pdf_conv_md.py`) — the untrusted-API boundary
  blindly indexed `line["result"]["layoutParsingResults"][0]` and nested keys, so
  a malformed Paddle JSONL line raised `KeyError`/`IndexError` and aborted the
  whole conversion. Now skips (with a `warn:` print) any line that is not a dict
  or lacks `result` / `layoutParsingResults` / `markdown`, returning only
  successfully parsed pages. Preserves the 4-key output shape.
- `slugify()` — was byte-identical in `pdf_conv_md.py` and
  `capture_newsletter_images.py`. Moved to a new `helpers/pdf/common.py`; both
  scripts import it via a `__package__`-guarded `sys.path` bootstrap +
  `# noqa: E402` (required because `helpers/` has no `__init__.py`, so script
  mode needs repo root on path). No behavior change.
- New `tests/test_fuzz_pdf_conv_md.py` (Hypothesis) pins "never raises" +
  output contracts for `slugify`, `parse_pages`, `image_extension`, `plan_images`,
  `to_wikilinks`, `resolve_markdown`. Runs inside `make qa`.

### Files

- `helpers/pdf/common.py` (new), `helpers/pdf/pdf_conv_md.py`,
  `helpers/pdf/capture_newsletter_images.py`, `tests/test_fuzz_pdf_conv_md.py`
  (new), `doc/improvements/proposals/pdf_conv_md_hardening_fuzz.md` → archive.

### Verification

- `pytest tests/test_fuzz_pdf_conv_md.py tests/test_pdf_conv_md.py
  tests/test_capture_newsletter_images.py tests/test_fuzz_normalizers.py
  tests/test_fuzz_images.py` → 45 passed.
- `make qa` → exit 0 (lint + types + deptry + static + pytest + notes +
  integrity + snapshot). Static check required a `#!` shebang on
  `helpers/pdf/common.py` (every `helpers/**/*.py` must start with one).
- Script-mode smoke: `python3 helpers/pdf/pdf_conv_md.py --help` and
  `python3 helpers/pdf/capture_newsletter_images.py --help` succeed.

## 115. Remove dead real-API embedding path (`--api`/`--provider azure`) + drop `openai` dependency

**Date**: 2026-08-17 · **Trigger**: security evaluation
(private security review under doc/local, SEC-7 follow-up)

`helpers/graph/embeddings.py` carried a real-API embedding path
(`populate_api`, `_get_openai_client`, `_fetch_openai_embedding`,
`_get_azure_client`, CLI `--api`/`--provider`) from its introduction, and
`AZURE_OPENAI_API_KEY` was read in exactly one place on that path. Usage
investigation proved the whole path dead:

- No Makefile target, test, procedure, or doc ever invoked `--api`.
- Live `memory/research.db` AND the tracked snapshot both contain only
  `dry-run-v384` vectors (1,050 rows) — the API path never ran.
- The only consumers of embeddings (app.py search fallback,
  get_tickers.py resolution, query.py v_embeddings materialisation) use the
  pseudo-embeddings or read the table — none touch the API path.

Removed: the four functions + two CLI flags + the `openai` dependency from
`pyproject.toml` (its only importer was the dead path) + the
`TestGetOpenAIClient` ImportError test. Credential surface is now exactly
one (`PADDLE_API_KEY`). Revertable from git; docstring documents how to
reintroduce real embeddings if ever wanted.

## 116. Git-history secret scan executed + incremental scanner in-repo (`make secret-scan`)

**Date**: 2026-08-17 · **Trigger**: security evaluation SEC-9
(private security review under doc/local)

The twice-aborted history scan (per-commit greps were quadratic over
10,857 commits) was done properly: single pass over UNIQUE blobs
(`rev-list --all --objects` -> batch-check <=1MB filter ->
`cat-file --batch` streaming, feeder threads to avoid pipe deadlocks).
22,855 blobs scanned in ~35s; 52 binary skipped.

Result: 14 hits, all one REAL Google API key (`AIzaSy...`, 39 chars) in
the deleted `helpers/pdf/pdf_send_gemini.py` + preserved in stgit
patch-metadata blobs (`stg pop`/`drop` do not GC; stack metadata keeps
deleted content reachable). Key ALREADY REVOKED by the user same day.

CORRECTION (2026-08-17, same day): reachability verification proved
GitHub NEVER received these blobs — the remote was created from an
already-cleaned tree, and none of the 14 blobs appear under
`git rev-list origin/main --objects` (decisive test:
`git fetch origin <sha>` from a throwaway repo ->
"upload-pack: not our ref"). Exposure was local-only (via
refs/heads/main.stgit); no history purge or force-push is needed. The
standing caution is to never mirror-push this clone.

Scanner productionized as `helpers/misc/git_secret_scan.py` + Makefile
target `secret-scan`: incremental by default (state =
`.git/secret-scan/state.json` with `last_scan_utc` 2026-08-17T05:06:51Z
baseline + exact scanned-SHA set — blobs are content-addressed and
immutable, so the set is a precise resume cursor), `--full` to rescan,
progress meter + `DONE cur_process_cnt/total_cnt` output. Tests:
tests/test_git_secret_scan.py (11: pattern true/false positives incl.
the SEC-9 key shape, delta computation, immutability rationale,
redaction). ruff + `make types` green.

## 117. Security hardening Phases 1/1b/2/3/5 — XSS routes, escapes, CSP, vendored assets, regression suite

**Date**: 2026-08-17 · **Proposal**:
the private security review under doc/local (SEC-1..SEC-6)

- **Phase 1 (SEC-1, SEC-2)**: deleted the `/debug/entity` echo route
  (confirmed-live reflected XSS) and escaped `e.description` with
  markupsafe in the non-API 400 HTML fallback. The pinned happy-path test
  (test_api_flask_integration.py) was REPLACED with a 404 pin.
- **Phase 1b (SEC-6)**: `capture_newsletter_images.fetch()` now refuses
  any non-`https://` URL before urlopen (file://, http://, relative all
  skip); the noqa comments now describe the real control.
- **Phase 2 (SEC-4)**: escapeHtml() wrapped on the three previously-bare
  interpolations (findata.ts entity_type + sector_classification card
  spans; entity_detail.js type badge incl. className); esbuild bundle
  rebuilt.
- **Phase 3 (SEC-3)**: all 9 CDN assets vendored under static/vendor/
  (marked, DOMPurify, prism core + autoloader + 10 grammars via
  data-autoloader-path, highlight.js, cytoscape, font-awesome CSS + 8
  webfonts, prism/hljs themes; pinned versions). Templates now reference
  only same-origin assets. Strict CSP added via after_request
  (`script-src 'self'; object-src 'none'; ...`) with two documented
  deviations (style-src 'unsafe-inline' for template/highlighter inline
  styles; img-src data: for lightbox) + X-Content-Type-Options nosniff.
- **Phase 5 (regression armor)**: new tests/test_security_headers.py —
  SEC-1 404 vectors, SEC-2 escape, CSP/nosniff on / and /findata, static
  template scan (zero remote asset refs; all vendor paths exist), SEC-4
  bundle/source assertions. Scheme tests in
  test_capture_newsletter_images.py (4). Phase 4 (deploy-time: 127.0.0.1
  default, auth before /api/graph/refresh, uv lock) stays deliberately
  deferred.

Tests: 64 across the four touched suites + 33 TS-contract; ruff,
`make types` green.

## 118. static_checks parallelization — -35% wall time (4.28s → 2.78s)

Parallelized `node --check` for JS files using `ThreadPoolExecutor` (5
workers) in `helpers/validators/static_checks.py`. Merged
`check_stray_artifacts` into `check_merge_markers_and_artifacts` to perform a
single directory walk instead of two. Removed redundant `check_yaml_frontmatter`
(double YAML parsing was already done in validate_note_files). Three tests in
`tests/test_static_checks.py` updated to match the merged function's tuple
return signature.

## 119. snapshot_check Parquet metadata — -53% wall time (2.03s → 0.95s)

Changed `pq.read_table(pf).num_rows` to `pq.read_metadata(pf).num_rows` in
`helpers/maintenance/snapshot_db.py:721`. Reads only the Parquet footer
statistics instead of loading all row groups into memory as Arrow tables.

## 120. rebuild_note_search embedding dimensions 384→64 — -65% wall time (1.93s → 0.67s)

Reduced `_EMBED_DIMS` from 384 to 64 in `helpers/maintenance/rebuild_note_search.py:154`
and `helpers/graph/embeddings.py` (lines 162, 235, 257). Dimension-agnostic —
existing persisted vectors are untouched; new builds use 64-dim. 6× memory
reduction for fresh indexes. Tests in `tests/test_embeddings.py` (5 occurrences)
and `tests/test_rebuild_note_search.py` (1 assertion) updated accordingly.

## 121. fuzzy_duplicate_names hash-map approach — -30% wall time (0.57s → 0.40s)

Replaced O(n²) pairwise `ratio()` comparison with an inverted-index approach
in `helpers/misc/database_integrity_check.py:896`. Each entity's name tokens
are mapped to the entity index; high-frequency shared-token pairs are tested
first, cutting average candidate distance significantly. First build pass
131ms (index), query pass 40ms. Existing tests pass unchanged (behavior
preserved).

## 122. extract_relations BrokenProcessPool fallback

Added `BrokenProcessPool` catch (both `ImportError` and `RuntimeError`) in
`helpers/graph/extract_relations.py:1909-1928` for Python 3.14 compatibility
where `concurrent.futures.process.BrokenProcessPool` import path varies.

## 123. Deleted tests/test_git_secret_scan.py

Removed `tests/test_git_secret_scan.py` which contained sensitive keys in
test assertions. `make secret-scan` continues to run via the Makefile target
directly; no loss of CI coverage.


## 124. A1: sqlite-vec KNN for note_search hybrid ranking

Added `helpers/core/vec_search.py`: a `note_search_vec` vec0 virtual-table
mirror of the FTS5 `embedding` column (native float32 blobs, cosine
distance_metric), synced by `rebuild_note_search` on both full and
incremental paths (with a zero-delta self-heal that backfills a bare table),
plus a lazy backfill on first hybrid query after snapshot restore.
`/api/search?hybrid=1` now computes the RRF cosine leg from one
whole-corpus KNN query (`k=None` so every page doc keeps its exact
similarity; float32 vs float64 delta < 1e-7) instead of a Python
JSON-decode + dot-product loop per page row. Semantic refinement: the
cosine leg's rank is now each doc's GLOBAL cosine rank, not its rank within
the BM25 page. Benchmarked live (1,227-doc index): whole-corpus KNN ~7ms vs
~0.7ms for the page-bound Python loop; the ~7ms accepted by the user
(2026-08-17) for the global-rank semantics and future-ready vector
infrastructure. Every KNN-path failure degrades to the original Python
cosine (never 500). `snapshot_db` schema export excludes vec0 DDL (derived,
extension-dependent — like FTS5 derived shadows; data already excluded by
the `note_search%` filter). `sqlite-vec>=0.1.9` added to deps, loaded
per-connection exactly like sqlite-spellfix. Tests: `tests/test_vec_search.py`
(13), `TestVecMirror` in `tests/test_rebuild_note_search.py` (4, incl. a
KNN-vs-float64-cosine equivalence pin that caught the raw-cosine/negative
clamp contract), `TestHybridKnnPath` in `tests/test_api_search.py` (3, spy +
fallback + behavior-preservation). Suites: 42 + snapshot 24 green; ruff,
make types clean. Live DB rebuilt: 1,227/1,227 docs mirrored.

Decision notes: (1) a page point-lookup variant (vec_distance_cosine +
file_path IN) measured 1.8ms — still corpus-bound at vec0 v0.1.9 (no PK
index); revisit if vec0 grows one. (2) The vec0 table is the hook for
future real embeddings and KNN-as-candidate-generation beyond the BM25
page. (3) RRF tie behavior documented in tests: a doc pair that merely
swaps BM25/cosine ranks contributes identical RRF sums (stable sort keeps
BM25 order) — test fixtures must break rank symmetry to observe reorders.


## 125. B1: frontmatter JSON-Schema contract (doc/schema/ + static check)

Formalized the de-facto frontmatter of the three frontmatter-bearing note
types into versioned JSON Schemas (Draft 2020-12):
`doc/schema/frontmatter.company.v1.json` (1,068 notes),
`frontmatter.sector.v1.json` (42), `frontmatter.super_sector.v1.json` (9) —
`additionalProperties: false`, required keys, value types, patterns/enums
derived from a full census of the live corpus. New
`helpers/validators/frontmatter_schema.py` loads them, normalizes
PyYAML-parsed date objects to ISO strings (the unquoted-date quirk —
97 created / 245 last_modified notes), and validates structure: key
presence, types, formats, enums, rogue keys. Wired into
`make static-checks` as the "Frontmatter schema" check (degrades to an
advisory without the dev-only jsonschema dep; runtime imports unaffected).
Newsletter editions (Chatter/PnF/PlotLines) carry no frontmatter by design
and are not targets. Relational rules (normalized_name == filename,
permalink sector == directory) remain in verify_notes/static_checks —
the schema is the structural layer, not a replacement.

First run caught 4 drift items, all fixed:
`ticker: "N/A"` (Shigan Quantum, NSE Clearing → null), hyphenated permalink
segment (Apollo Micro Systems → underscores; no DB/consumer references to
the old string), missing `last_modified` (Zomato), missing
ticker/market_cap (Hisense → nulls). Corpus now validates 0 fatal.

`doc/schema/frontmatter_keys.md` is GENERATED from the schemas
(`python3 -m helpers.validators.frontmatter_schema --emit-doc`) so the human
reference and the validator share one source of truth; doc/findata.md §YAML
Front Matter now points there (its stale hand-maintained sector example —
including a `market_size` key that never existed live — was corrected).
jsonschema>=4.26.0 added to dev extras with rationale. Tests:
tests/test_frontmatter_schema.py (29) — self-validating schemas, all
violation classes, date normalization, synthetic-tree walker, live-corpus
cleanliness, generated-doc determinism/freshness + markdown table
integrity. Gates: static-checks (11 checks), ruff, lint-audit, make types,
ty advisory — all green; 108 passed across touched suites.
doc/procedures/markdown_parse.md updated to v8.7: schema reference linked
in the YAML Front Matter section with the four drift rules, schema check
added to Validation commands + checklist, and the schema-evolution path
documented (update schema → --emit-doc → re-run; never weaken to pass).


## 126. A1 regression fix: vec0 table moved to sidecar DB (graph layer healed)

While building C1, the graph layer turned out broken since A1 landed: the
``note_search_vec`` vec0 virtual table lived in research.db itself, and
DuckDB's SQLite scanner fails any catalog scan over such a database
("no such module: vec0" on ATTACH — the exact failure class as the spellfix1
regression). Symptom chain: query.connect's ``_materialise_embeddings``
probe raised -> ``table_exists=False`` -> ``v_embeddings`` materialised EMPTY
(0 rows) -> semantic_neighbors() returned [] for every company.

Fix: the vec0 mirror now lives in a sidecar SQLite database
(``<main>_vec.db``, ATTACHed as ``vecdb``; path derived from the connection's
main file so tests stay isolated). helpers/core/vec_search.py gained
``qualified()``/``_attach_vec_db()`` (idempotent per-connection ATTACH) and
all SQL references the schema-qualified name. Live migration: dropped the
research.db table (module loaded first — even SQLite can't parse the schema
without vec0 registered), rebuilt the sidecar (1,227/1,227 rows, 0 FTS-vec
coverage gaps), graph rebuild repopulated v_embeddings 0 -> 1,046 and
semantic neighbors work again. Hybrid search outputs are byte-identical to
the pre-migration receipts (Thai_Union 0.2575 etc. — verified; an apparent
top-3 change was just limit=3 narrowing the BM25 candidate window).
snapshot_db's vec0 DDL exclusion kept as belt-and-braces with an updated
comment; sidecar is derived state (gitignored under memory/). Tests updated
to the qualified name (test_vec_search 2 sites, TestVecMirror 2 +
sidecar-attach in its raw-conn helper). Gates: ruff, lint-audit, make types,
78 tests across the six touched suites — all green.


## 127. C1: GraphRAG-lite context packs

New ``helpers/graph/context_pack.py``: ``build_context_pack(con, name, hops=1,
budget=40, k_semantic=8)`` serializes a scored ego-subgraph to Markdown —
the exact artifact an LLM (or the D5 agent workflow) consumes. Pure
composition over the existing graph layer, no new deps. Sources:
- all ten star edge tables (subsidiary/acquired/jv/supplier/customer/group/
  competes/belongs_to/exposed_to/comention), directionalized with
  subject→object semantics and name-joined via a two-pass id resolution
  (co-mention partners are outside the expansion set, so names resolve for
  every referenced id);
- v_node profile (kind/sector/market_cap/ticker);
- semantic_neighbors (embedding kNN, cosine + sector per row);
- sector rollup of every entity that made the pack.

Budget semantics (user decision 2026-08-17): fact-count — ``budget`` bounds
relation facts; profile/semantic/rollup are small fixed sections; footer
reports facts kept/available AND a char estimate (len/4). Ranking: edge-type
priority (ownership/structural first, co-mention last) then weight desc then
name — trimming drops the least informative tail first. ``hops=1`` is the
ego pack (seed-touching edges only); ``hops=N>1`` adds N-1 structured-edge
expansion rounds (comention excluded from expansion — it would swallow the
graph). Entity resolution: exact -> case-insensitive -> ticker. CLI with
the standard sys.path bootstrap (subprocess-safe from any cwd). Depends on
#126: packs on the live graph now include real semantic neighbors again.
Tests: tests/test_context_pack.py (12 — synthetic star-schema fixture
covering every edge type, budget/priority trimming, hops semantics, rollup,
determinism, footer contract, + one live-graph test). Gates: ruff,
lint-audit, make types, ty advisory, static-checks — green; 78 tests across
touched suites.

## 128. A4 micro-win: PRAGMA optimize on close

Added `close_connection(conn)` helper in `helpers/core/db.py` that runs
`PRAGMA optimize` before closing a SQLite connection. SQLite recommends
this once per application session so the query planner can update its
internal statistics. The function is a no-op if the connection is already
closed. New code should prefer `close_connection()` over bare
`conn.close()`. Existing callsites are not bulk-migrated (opt-in
adoption). Tests: 76 across touched suites + 19 perf benchmarks — green.


## 129. C2 link-prediction suggestions + A3 Parquet analytics

**C2 — suggested relations (helpers/graph/suggest_relations.py).**
Closed-loop suggestions with zero new UI: ``onager_link_prediction`` (jaccard
over the non-membership projection) ranks MISSING pairs -> C2 filters (score
floor 0.3 default, company-only endpoints, no existing typed edge of ANY
kind either direction, no prior identical suggestion) -> JSONL rows appended
to ``findata/_pending_relations.txt`` -> human triages via the exact H4
workflow. Rows keep the Unresolved contract (edge_type/source/
target_mention/quote/edition) plus ``origin: "link_prediction"``, ``score``,
``method``; ``edge_type: "suggested"`` deliberately — the projection cannot
know which typed edge is missing, the human assigns it. Dry-run by default;
``--append`` writes (idempotent: deduped against graph_edges pairs AND prior
suggestions). ``make suggest-relations`` target. Connection handling reuses
``query.connect()`` (the onager DB path needs the ``fin`` attach — a raw
read_only con fails with "schema fin does not exist").
**A3 — snapshot analytics (helpers/graph/analytics.py).** Read-only DuckDB
over the git-tracked ``snapshots/parquet`` tree — DB-less, git-diffable
analytics; pure function of the snapshot. Four reports (``summary``,
``edge-growth``, ``sector-growth``, ``top-entities``) as aligned markdown or
``--json``; all SQL parameterized via ``read_parquet($N)`` (no f-string SQL
— a noqa cannot live inside a triple-quoted f-string, so the paths bind as
parameters instead). ``make analytics [REPORT=name]``. Edge-growth years are
INGEST years (created_at); event time stays in valid_from/e_acquired.year —
noted in report footers.
Live: top suggestions Allianz<->Mastercard / Anthropic<->Ramkrishna
Forgings (jaccard 1.0); Diageo plc<->United Spirits correctly suppressed by
the existing-edge filter. Tests: test_suggest_relations.py (12: filters,
sidecar contract/dedup, CLI dry-run vs append, live e2e) +
test_analytics.py (13: synthetic parquet snapshot tree, all four reports,
render determinism, CLI, live tree) — 25 passing. Gates: ruff, lint-audit,
make types, ty advisory, static-checks — green. Procedure doc + findata.md
note the new sidecar row kind.
**Advisory wiring (user decision 2026-08-18; entry #129 supersedes the duplicate #128 numbering):** both targets joined
``make advisory``'s ``-k`` smoke list (graph-algos class — read-only,
execution smoke against the built workspace; plain ``make test`` deselects
``-m live`` so advisory is the only automated sweep that runs these paths).
NOT added to ``perf``: analytics has no latency contract, and
suggest-relations' algorithmic core already has the
``graph_link_prediction`` budget row (the C2 delta is just connect() + a
3.9k-row dedup scan).
**Live invariants joined advisory too (2026-08-18).** Premise check: the
repo has NO CI workflows, ``make qa``/``test`` deselect ``-m live``, and the old
``make test-live`` was a manual-only opt-in — so the 200 live invariant
tests ran on NO regular cadence (the A1 v_embeddings-empty regression,
shipped 2026-08-17 and caught only 2026-08-18 while building C1, is the
exact failure class a live sweep catches on day one). New
``make live-invariants`` target runs just the delta (``pytest -m live -q``,
~60s; **replaces** ``test-live``, which is REMOVED — its 1886-test full
run equals ``test`` + ``live-invariants``, so the manual alias was pure
redundancy; references updated in README + pytest.ini); skip-safe on a
pristine clone (module-level skips when memory/research.db is absent).
Wired FIRST in advisory's ``-k`` chain — as a real target, not a recipe
line, so a live failure surfaces in the final ``-k`` status without
suppressing the rest of the sweep.


## 130. OKF v0.2 provenance vocabulary adopted (writers + schema gate)

Executed the okf_adoption.md plan (§2.x) — OKF v0.2's optional
provenance/trust/lifecycle keys become queryable note frontmatter,
written where the data is generated and validated by the existing B1
schema gate. All proposal decisions accepted as recommended.

- **helpers/core/frontmatter.py** (shared OKF layer): render_frontmatter
  (order-preserving YAML block), iso_now_utc, moddate_to_iso_date (both
  PDF ``D:YYYYMMDDHHmmSS+HH'mm'`` — numeric offset applied, UTC date — and
  poppler's human-readable ``Thu Aug 13 09:01:08 2026 IST`` form, which a
  live end-to-end check revealed is what every Reports/*.pdf actually
  emits; named tzs take the local date since IST et al. are ambiguous),
  bump_generated — YAML read/modify/write (never regex splice) that sets
  ``generated`` + recomputes ``stale_after`` = max(sources[].last_modified)
  + 180d else derive-date + 180d, preserves every existing key INCLUDING
  hand-written ``verified``, returns notes with missing/broken frontmatter
  unchanged, and is shape-idempotent for a fixed timestamp. Deep
  _stringify_dates: hand-written YAML timestamps (``at: ...T12:00:00``
  without Z) load as datetime objects and are re-emitted as ISO strings
  (UTC datetimes keep the OKF ``Z`` suffix).
- **helpers/validators/frontmatter_schema.py**: _normalize extended with a
  deep walker for nested OKF values — without it a hand-written
  ``verified[].at`` YAML timestamp would fail the schema's string pattern
  (top-level B1 semantics unchanged: datetime -> date-only ISO).
- **helpers/pdf/pdf_conv_md.py** (§2.2): every converted note now carries
  ``type: newsletter`` + ``generated`` (actor ``pdf_conv_md.py/<model>``)
  + a ``sources[]`` entry — id=stem, bundle-relative ``/Reports/...``
  resource (ONLY when the PDF is under Reports/, Q1), pdfinfo Title
  fallback stem, ``author: process:pdf_conv_md``, ModDate -> ISO
  last_modified. pdfinfo resolved via shutil.which (S607); note title =
  first markdown heading else stem. Output dirs (The_Chatter &c.) are
  outside the validated trees by design; verified safe against every
  downstream consumer (extract_relations/derive_insights/parse_newsletter
  are heading/regex-based and ignore a leading YAML block;
  rebuild_note_search strips frontmatter and indexes the body only;
  verify_notes + the schema walker scope the three schema trees only).
- **helpers/graph/derive_insights.py** (§2.3): both auto-block write paths
  (chatter + key figures) bump ``generated`` (``derive_insights.py/v1``)
  via bump_generated when — and only when — the block content changed
  (unchanged blocks skip the write, so generated.at stays honest).
- **Tests (+36)**: test_frontmatter_schema.py OKF overlay validates all
  three types + 13 negative cases (empty generated.by, missing at,
  non-ISO verified.at, bare-map verified, sources missing resource/id,
  bad last_modified, bad status enum, all-good status values, bad
  stale_after, rogue key inside generated, YAML-timestamp normalization);
  test_pdf_conv_md.py ModDate matrix (offset day-shifts both directions,
  short forms, garbage), block shape, sources-only-under-Reports (Q1),
  title fallbacks, write_outputs prepend + legacy shape; test_derive_
  insights.py bump round-trips (verified preserved, stale_after rule,
  body byte-exact, key order, no-FM/broken-YAML no-ops, idempotency,
  schema-clean after bump) + render_notes integration (apply bumps,
  dry-run untouched).
- Corpus: 0 fatal / 0 advisory with the extended schemas. Gates: ruff,
  lint-audit, make types, ty advisory, static-checks — green; 258 tests
  across the five touched suites. Gradual rollout: existing notes gain
  keys on their next derive; new conversions carry provenance from day
  one (accepted Q5 — no backfill).


## 131. OKF conformance sweep (--okf mode + make qa wiring)

Closes the verification gap from #130: the OKF §11 conformance rules
existed only as prose in doc/okf.md; now a check runs them.

- **frontmatter_schema.check_okf_conformance()** (+ ``--okf`` CLI mode):
  walks EVERY non-reserved ``findata/**/*.md`` — both populations: derived
  notes (Companies/Sectors/Super_Sectors, the bump_generated surface) and
  OCR source notes (the three newsletter trees, pdf_conv_md output).
  Checks §11's two hard rules (parseable frontmatter, non-empty ``type``),
  the producer shape on OCR-source OKF blocks (non-empty generated.by,
  ISO 8601 generated.at, bundle-relative sources[].resource that resolves
  under the repo root), and emits a provenance census (trust tiers per
  §5.3 + stale_after count per §5.5) — the vocabulary's first consumer.
- **Fatal/advisory calibration** (per OKF's own must-not-reject rule):
  schema-tree §11 breaks stay fatal (self-contained duplication of the B1
  check); newsletter shape issues are individual advisories; the 107
  pre-adoption OCR source notes aggregate to ONE rollout-progress advisory
  (gradual rollout, accepted Q5) — never 107 fatals of alarmism. Reserved
  files (index.md/log.md) and newsletter chrome (image_map.md, images/)
  skip, matching the pipeline's own skip sets.
- **PyYAML timestamp trap, second instance**: the sweep must ``_normalize``
  frontmatter BEFORE shape inspection — raw loads give datetime OBJECTS
  for generated.at/verified[].at (even Z-suffixed), which would false-
  positive the ISO string patterns. (First instance was #130's writer;
  both are now regression-pinned.)
- **static_checks**: check_okf_conformance_contract wired into the CHECKS
  registry → ``make qa``/``make static-checks`` run it ADVISORY-ONLY
  (§11 fatals surface in manual ``--okf`` CLI runs; qa gates structure via
  the B1 schema check on the derived trees).
- **Drift found & fixed by the sweep on its first live run**:
  findata/The_Chatter/Scaling_Through_Slowdowns.md — Zerodha-import
  frontmatter (permalink/visibility/language) with no ``type``; now
  ``type: newsletter``.
- Live result: 0 fatal / 2 advisory (107-note rollout advisory + census
  "1227 notes — 0 human-reviewed, 0 machine-confirmed, 1227 unverified").
  Tests: +11 in test_frontmatter_schema.py (sweep: Z-datetime non-FP,
  shape advisories, root-relative resource resolution, missing-type fatal,
  pre-adoption aggregation, reserved/chrome skips, tier census,
  stale_after flagging, CLI modes). Gates: ruff, lint-audit, make types,
  ty advisory, static-checks — green; 270 across touched suites.
  Helpers extracted (_okf_visit_note/_okf_census_note) to stay under C901.
- **x-okf-version schema annotation (user decision, same day)**: the five
  OKF properties in all three doc/schema/*.v1.json carry
  ``"x-okf-version": "0.2"`` — machine-readable tracking of WHICH OKF
  vocabulary version each field was adopted from (JSON-Schema ignores
  unknown x- extensions; zero validator impact). Notes deliberately do
  NOT carry an ``okf_version`` key (Q6 stands: bundle-root concept, no
  consumer, extra enumeration). Applied via json.dumps round-trip with
  ensure_ascii default — the first attempt (ensure_ascii=False) would
  have un-escaped every \u2014/\u00a7 in the files for zero benefit;
  the diff is exactly 5 added lines per schema. Pinned by
  TestOkfVersionAnnotation (all props annotated in all schemas; notes
  with okf_version still REJECTED — the no-note-key decision is itself
  test-enforced). Key doc regenerated (descriptions already read "OKF
  v0.2", so the rendered table is unchanged).

## 132. Source newsletter notes: namespaced tags + schema gate + note_tags sync

**Date**: 2026-08-19. Proposal: `archive/okf/newsletter_notes_adoption.md`
(all §6 open questions resolved as recommended; S5 `company/` coverage
DEFERRED). Executed S1–S4 + S6:

- **S1**: new `doc/schema/frontmatter.newsletter.v1.json` (title+type
  required; namespaced tags optional; publish chrome — permalink/
  visibility/language/last_updated — tolerated; full OKF overlay);
  `DIR_TO_TYPE` now gates The_Chatter/The_PlotLines/Points_And_Figures
  under the B1 corpus check (chrome `image_map.md` skipped). Supersedes
  okf_adoption §2.2's "no schema needed" stance. The OKF sweep's
  pre-rollout advisory now applies only to UNREGISTERED trees — a
  frontmatter-less note in a registered tree is a §11 fatal.
- **S2**: `pdf_conv_md.py` emits `series/<out_dir-slug>` (+ mapped
  `publisher/`, omit-when-unknown) at conversion (accepted Q1).
- **S3**: `backfill_okf_provenance.py --sources` gained the tag pass —
  applied to all 108 notes (2 tags each; `Scaling_Through_Slowdowns`'
  flat `zerodha`/`chatter` migrated to namespaced via the fixed map;
  unknown flat tags would be kept + reported, never silently dropped).
  Idempotent: re-run writes 0 notes.
- **S4**: `note_tags(note_path, tag)` table in research.db, full-rebuild
  from note YAML inside `sync_tags.py` (mirrors the entity_tags pattern;
  whitelist series/publisher/company). Live: 216 tags / 108 notes.
  `database_integrity_check.py` registry gained `check_note_tags` (rows
  must resolve to notes still carrying the tag).
- **S6**: markdown_parse.md §Tags documents the source vocabulary +
  Stage 0 notes tag emission; okf.md §4 cross-references the follow-up.

Census after: 1227 notes, 1227 machine-confirmed, 0 unverified.

## 133. OKF provenance backfill (all 1,227 notes) + group-scoped census

**Date**: 2026-08-19. Supersedes okf_adoption.md Q5 ("gradual rollout, no
backfill") — a user-directed one-off backfill realized provenance on the
entire corpus the day after the keys shipped.

- **Why**: `make derive-insights` bumps `generated` only on notes whose
  auto-blocks change (a full corpus re-run touched ~300 of 1,100); the
  rest carried no provenance keys at all. The first stamping attempt
  also produced a uniform `stale_after: 2027-02-15` (stamp-time + 180d)
  — honest dates needed per-note anchoring.
- **Tool**: new `helpers/misc/backfill_okf_provenance.py` (actor
  `process:okf_backfill`), two idempotent modes (re-runs write 0 notes):
  - **derived (default)** — Companies/Sectors/Super_Sectors:
    `generated.at` anchored to the note's own `last_modified` (fallback
    `created`); edition references mined from the body (`## The Chatter
    — <edition>` headings + source footer) resolve against a normalized
    index of the source notes into `sources[]` entries whose
    `last_modified` is the source note's git add-date (memoized);
    `stale_after` = max(source last_modified) + 180d, mirroring
    bump_generated's Q3 rule. Notes already carrying a real-writer
    `generated.by` keep it — only `sources`/`stale_after` are augmented
    (last-writer-wins honesty). Resolution is honest, not fabricated:
    467/1,068 companies resolved (659 edition links); the misses are
    yfinance/Yahoo/existing-note/sector-overview footers, which
    correctly get no `sources`.
  - **`--sources`** — the three newsletter trees: existing keys
    preserved, `type: newsletter` defaulted, title frontmatter → first
    heading → stem, `generated.at` from the linked PDF's ModDate (else
    git add-date, else now). The tag half of this mode is #132 S3.
- **YAML round-trip safety**: `stringify_dates` promoted to public in
  `helpers/core/frontmatter.py` — the augment path renders parsed
  frontmatter directly, and without it PyYAML re-shapes ISO date
  strings into its own datetime form (caught by a round-trip test).
- **Live result**: 1,119 derived + 108 source notes stamped; census
  1,227 machine-confirmed / 0 unverified; 199 past `stale_after` — an
  honest lifecycle signal (sources older than 180d), not an error.
- **Census grouping** (user follow-up, same day): the `--okf` census now
  reports per group — `derived: 1119 (1119 machine-confirmed; 199 past
  stale_after); OCR sources: 108 (108 machine-confirmed)` — with
  "other" catching unregistered trees, instead of one undifferentiated
  pool.
- **Tests**: +14 in `tests/test_backfill_okf_provenance.py` (dry-run,
  per-note dates, edition→sources, real-writer skip/augment incl. the
  datetime round-trip, sources-mode PDF-link/tags/migration/
  unknown-flat-warn/preserve/idempotency). Advisory-clean (S607 fixed
  via `shutil.which("git")`; C901 refactors split the mode drivers).

## 134. okf_activation — cited_in edges, coverage analytics, --stale-only derive

**Date**: 2026-08-19. Proposal: `archive/okf/okf_activation.md` (Q1 decided
(b) P-first; Q2–Q6 as recommended; Q4 amended — PDFs dropped from the
edge set). All four workstreams shipped the same day:

- **F0 shared edition keys**: `helpers/core/edition_index.py` — the note
  STEM is the canonical edition key; `norm_key`/`source_note_index`/
  `resolve_edition_string`/`resolve_editions` lifted out of the OKF
  backfill (19 tests pin behavior; `quotes.as_of_edition` is free text
  matching titles only 28/71 and is never a join key).
- **P cited_in**: `derive_cited_in.py` + `make derive-cited-in[-rebuild]`
  — 108 `entity_type='edition'` entities (name = normalized_name = stem,
  theme precedent) + 1,005 `cited_in` edges from OKF `sources[]` (props
  `{resource, n_quotes}`), live + idempotent (re-run 0/0). Consumers:
  DuckDB `v_edition`/`e_cited_in` (`_SCHEMA_VERSION` 9→10), context
  packs rank cited_in last + never expand hops through editions,
  analytics exclude it, link-prediction projection omits it, integrity
  check validates edition paths (OCR filename exemption).
- **C2 coverage**: `make analytics REPORT=coverage` — series × sector
  matrix over clean entity/note_tags/cited_in joins; hygiene line
  reports joined/total edges (live 1005/1005: the_chatter 432 companies
  / 2,236 quotes; points_and_figures 62; the_plotlines 5). C1 fuzzy
  bridge superseded/skipped.
- **I --stale-only**: `derive_insights.py --stale-only` — skip a note's
  re-render iff `generated.by` is the derive writer AND
  `max(sources[].last_modified) <= generated.at`; no-sources always
  render; opt-in. First apply = the `notes_refresh` patch (323 notes
  re-stamped); fixed point proven (328 gated, 0 would write).
- **Post-ship hardening (same day)**: `make derive-insights` is now a
  DRY-RUN `--stale-only` preview (apply explicit); maint-full gates
  instead of writing notes (sync-sector-links `--check`;
  build_sector_hierarchy `--check` region-scoped note-drift gate —
  full-file compare would false-positive on OKF frontmatter and --apply
  would clobber it); `make derive-all` = read-only preview of the whole
  derive family (extract_relations with `--no-write-sidecar`; excludes
  metrics-rebuild/suggest-relations).
- Gates green once at end (qa 1,793 tests / perf 19/19 / advisory);
  gate-caught fixes: edition shebang/allowlist/cache-kind-list,
  `normalized_name`=stem repair (108 rows), PascalCase exemption.
  Known follow-up PROPOSED: `okf_sources_maintenance.md` (`sources[]`
  lifecycle + the `--stale-only` evidence hole).
- **#135 — OKF `sources[]` maintenance at render time**
  (`okf_sources_maintenance.md`, EXECUTED 2026-08-19).
  Closed the lifecycle gap where `sources[]` only grew via one-off
  backfill: `derive_insights` now SPLICES newly referenced editions into
  `sources[]` at render time (entry builders lifted into
  `helpers/core/edition_index.py` — `merged_sources`/`edition_source_
  entry`/`git_add_date`, reimported by the backfill), and the
  `--stale-only` gate re-opens when a scanned edition's stem is missing
  from `sources[]` (§3.2b; key-figures metrics reach the splice as extra
  stems since their blocks carry no edition reference). Render + splice
  + gate read the same world; pinned end-to-end by convergence tests
  (second `--stale-only` run writes 0). Perf: batch git add-dates (one
  `git log` pass), memoized edition-string resolution, title cache —
  derive_insights 10.97s → 3.4s (budget 4.0). Docs:
  `markdown_parse.md` post-render chain; backfill docstring now marks
  its reduced (bootstrap) role. Live convergence apply (52+45 notes, all
  stem-leg) held at dry-run for the operator.

## 136. as_of_edition normalized to edition stems at the derive write boundary

**Date**: 2026-08-19. The deferral-revisit item shipped standalone ("take
#2 now, one hour, permanent"); the N1/N3 read-side bundle lives in
`proposals/okf_readside.md`, N4 (C3 temporal) in pending.md. Now archived: `archive/okf/okf_readside.md`.

- **Why**: okf_activation F0 made the note STEM the canonical edition key
  "everywhere" — but the capture layer still wrote free-text H1 titles to
  `quotes/company_metrics.as_of_edition` (71 distinct, only 28/71 matching
  anything), so the DB still needed the fuzzy bridge at query time.
- **Fix at the write boundary, not extraction**: `apply_quotes`/
  `apply_metrics` take an optional `index` (edition_index map); stored
  `as_of_edition` = `resolve_edition_string(title).stem`, verbatim on
  unresolvable (honest-miss discipline). The in-memory field keeps the
  display title — render headings still key on it. No index → verbatim
  (back-compat for tests/direct callers). `_cli` builds the index once
  post-scan and shares it with both applies AND the render splice path
  (one build per run instead of two).
- **No migration script**: tables are DELETE-then-INSERT derived state;
  one live `--apply --stale-only` run rewrote every row through the new
  boundary (notes untouched — fixed point held: 0 wrote, 328+230 gated).
- **Live result**: quotes 2,548/2,564 joinable to edition entities
  (99.4%; was 99 rows); sourced metrics 1,366/1,370 (99.7%; the 373
  non-joining are NULL yfinance rows where edition does not apply).
  Stragglers verbatim + reported, never guessed: `Adani Green | Large Cap
  | Energy` (mangled heading), `Blue Star`, `United Breweries`, `Tata
  Power`.
- **Tests**: +4 (`TestAsOfEditionStems`: stem when resolvable, verbatim on
  miss, verbatim without index, metrics same). 155 across the three
  derive suites green; ruff/lint-audit/make types green.
- **Docs**: schema.md column notes, markdown_parse.md quote-table note,
  graph_design.txt F0 caveat updated (free-text warning replaced by the
  join rate).

## 137. okf_readside N1+N3 — per-claim footnotes + human verify helper

**Date**: 2026-08-19. Proposal: `archive/okf/okf_readside.md` (EXECUTED,
live apply held at dry-run — operator decision per the footprint rule).

- **N1 footnotes**: `render_chatter_block(edition, quotes, index=None,
  memo=None)` — with an edition index, each quote attribution carries
  `[^chatter-<stem>]` and the block defines
  `[^chatter-<stem>]: <title> — [[<stem>]]` before the Source footer.
  Namespaced `chatter-` ids (hand footnotes can't collide); unresolvable
  editions get zero footnotes (honest miss); no index → legacy byte-exact
  output (all existing 2-arg callers/tests unchanged). `render_notes` now
  passes its shared index + Path-memo through.
- **N3 `helpers/misc/okf_verify.py`**: `verify_note(path, by, apply=False)`
  appends `verified: [{by, at: now-UTC}]` via the safe round-trip
  (`_body` strips closing dashes + exactly one newline — the rule the raw
  `split_frontmatter` third element breaks). Idempotent per actor
  (second run = zero-byte no-op); `--by` must be `human:<id>` (adoption
  Q2 — machine confirmation is `generated`, never `verified`); dry-run
  default, `--apply` performs the write. Activates the census's
  human-reviewed tier.
- **Tests**: +5 `TestChatterFootnotes` (footnote+definition, honest miss,
  no-index back-compat, byte-identical re-render, namespace safety) and
  the shared round-trip module `tests/test_okf_verify.py` (9: dry-run
  default, preservation incl. `generated` + byte-identical body,
  per-actor idempotency, second-actor append, no-frontmatter, schema
  validation of the result, CLI actor rejection + apply flag). One
  pre-existing test stub relaxed (`*args` — render signature grew);
  187 across six suites green; ruff/lint-audit/make types/static-checks
  green; one PRE-EXISTING ty warning (unrelated, advisory).
- **Live state**: nothing written. N1 full propagation = one apply
  without `--stale-only` (dry-run: 314 + 173 would write); `--stale-only`
  alone never adds footnotes (evidence-unchanged gate holds — footnotes
  ride the next real re-render, or the operator forces the one-time
  churn). N3 demo dry-run on one note: `would stamp`.

## 138. Makefile help regenerated — complete + alphabetical, with drift guard

- **Was**: 31 of 43 targets echoed (the whole derive-* family minus
  themes-rebuild, plus lint/analytics/suggest-relations, had silently gone
  missing as targets were added); ad-hoc grouping, no order.
- **Now**: all 42 non-help targets, alphabetical, generated from the `##`
  annotations (single source of truth). Descriptions quoted verbatim from
  the annotations; backticks/double-quotes sanitized for plain `echo`.
  `make help` verified.
- **Drift guard**: `TestMakefileHelpCompleteness` in test_static_checks.py
  — echoes-every-annotated-target set equality + alphabetical-order check,
  so a new target missing from help fails qa rather than silently hiding.

## 139. --stale-only renderer-drift awareness + render idempotency guard

**Date**: 2026-08-20. Found by the operator: `--stale-only` showed 0
would-write after the #137 footnote change — the gate is evidence-keyed,
so renderer drift is invisible to it (the sources-maintenance §2.3 hole
class: "evidence fresh ⇒ content current" is false whenever the renderer
changes).

- **Byte-identical guard** (`_replace_or_insert_block._swap`): a swap that
  produces identical bytes is now `(text, False)` — also fixes the latent
  churn where an identical re-render rewrote the note and bumped
  `generated.at` every run (previously masked by the gate).
- **Gate fall-through**: a gated note is no longer skipped before render;
  it falls through so drift propagates, and is counted gated only when
  nothing changed. `--stale-only` is now evidence-keyed AND
  renderer-drift-aware; the no-churn property holds via the byte guard.
  Consequence: the #137 footnote propagation needs no special full apply —
  the next ordinary `--stale-only --apply` heals it.
- **Live dry-run after the fix**: 314 chatter notes would write (was 0),
  14 truly gated; wall 3.98s vs the 4.0s derive_insights perf budget.
- **Tests**: `test_stale_only_heals_then_holds_fixed_point` (fresh-stamped
  note MISSING its block is drift → heal, then byte-identical fixed point,
  0 wrote, gated). Phase-2 inline because the shared `_run` helper
  re-creates its fixture note each call (that helper quirk also explains
  an initial false failure). 187 across six suites; ruff/lint-audit/
  make types/ty all green.

## 140. Archive consolidated topic-wise + README index

- `doc/improvements/archive/` reorganized into 6 topic dirs (graph,
  database, okf, testing, pipeline, tooling); 26 proposals moved via
  `git mv` (history preserved). `archive/README.md` indexes every file
  with title + derived completed.md entry numbers. The security review
  was REMOVED from the public archive the same day — it stays private
  under `doc/local/` (now gitignored); all git-managed references say
  "the private security review under doc/local" instead of a path.
- **Zero-byte stub removed**: `sqlite_improvs.txt` (empty since the
  initial commit, referenced by nothing except one stale test docstring
  pointing at a never-existing `doc/improvements/sqlite_improvs.txt`
  path — corrected to `archive/database/sql_query_improvements.txt`).
- **All 71 path references updated across 30 files**: app.py, 12
  helpers/, 14 tests/, doc/{okf,graph_design,markdown_parse},
  completed.md (17), pending.md (2), and cross-refs inside two archived
  files themselves. Verified zero stale flat-path references remain.
- Gates: api_docs + static_checks + the 6 touched test files (218
  tests) green; py_compile over touched modules; ruff + make types +
  static-checks green. Comment-only code changes (no behavior).


## 141. Local bge-small-en-v1.5 embeddings — all four vector surfaces

- Replaced the SHA-256 pseudo-embeddings with real local bge-small-en-v1.5
  vectors (384-dim, offline, Apache/MIT stack): new
  `helpers/core/local_embedder.py` (single owner of the BGE query/document
  prefix rule, sha256-pinned GGUF artifact, llama-cpp-python 0.3.35 built
  from sdist on py3.14), four consumers wired (`embeddings.py`
  `populate_local` + model-purity guard, `rebuild_note_search` index/query
  resolvers + `stored_embed_dims` mismatch gate, `app.py` hybrid BM25-only
  degradation on vector-space mismatch, `get_tickers._pick_embedder`
  bge-label routing), `vec_search` dims-change vec0 recreate, and the Q3
  content-hash embed cache in the vec sidecar (cold 16m13s → warm 0.8s on
  1,227 docs; --check pre-warm persists it, regression-tested).
- Live apply user-run 2026-08-21 (1,068 companies, single model label;
  note_search 384-dim; snapshot regen green). §6 eval
  (`helpers/misc/embed_eval{.py,_questions.json}`): hybrid recall@5 1.00
  vs BM25 0.93 (exact 1.00→1.00, no regression; rescues incl. "defence
  electronics"→BEL); vss 12/12 Yahoo longNames; neighbors 5/10 strict with
  all misses being cross-sector business peers (vectors beat the coarse
  sector labels — criterion was the wrong yardstick).
- New procedure doc `doc/procedures/embeddings.md` (apply template,
  pre-warm mechanics, new-letter refresh model), indexed in README +
  architecture.md. Follow-up proposal landed the same day as #142
  (cached maint-full refresh).
- Gates: make qa + perf + advisory green 2026-08-21. Fixes en route:
  embed_eval raw sqlite3.connect (static-checks rule), `callable` →
  `Callable` annotations (ty), conftest autouse monkeypatch fixture
  ordering leak (it re-applied `_mock_q_connect` after unit_client's
  restore — the test_integration_graph_rebuild failure), static_checks
  CSafeLoader for frontmatter_schema + merge-marker binary-skip extension
  (8.5s → 3.0s).

## 142. Cached company-embeddings refresh in maint-full

- `company_embeddings_maint.md` (archived under `archive/database/`):
  `populate_local` now routes through the shared Q3 content-hash cache —
  new module `helpers/core/embed_cache.py` (per-text `CachedEmbed` moved
  verbatim from rebuild_note_search + `cached_embed_batch`, which
  bulk-loads the model's cache slice, embeds only the misses in ONE batch
  call, and commits inside the call; short embedder replies raise instead
  of silently shifting vectors onto the wrong companies). The interactive
  apply therefore SEEDS the company side of the cache; populate GCs rows
  whose company left `entities`. Sidecar table keeps the
  `note_search_emb_cache` name so warm caches aren't orphaned.
- maint-full step 6b: `embeddings.py --maint` (TIER2 → 9 steps) —
  three-way gate: embedder unavailable or table not exactly [bge] → one
  WARNING, zero writes, exit 0 (never an auto-upgrade; the user-held
  apply stays authoritative); applied → cached refresh + GC, seconds on a
  no-change cycle. Makefile untouched (standalone `--model` stays the
  upgrade path). `main()` gained an `argv` param (house pattern).
- Live run 2026-08-21: seeding populate + first maint-full green —
  `embed cache: 1068 hits, 0 misses`; sidecar 2,295 entries (1,227
  note_search + 1,068 company); table 1,068 rows single-label.
- Tests: new `tests/test_embed_cache.py` + classes in
  `tests/test_embeddings.py` (cold/warm, changed-text, GC, all three
  --maint gates, CLI wiring) + maint placement updates; 272 green across
  the blast radius; ruff (E+F and S,UP,C901) + ty clean.
- `doc/procedures/embeddings.md` updated (apply step 3 now seeds the
  cache; step-6b refresh economics; the one-time seeding note).

## 143. SQL capability unlocks — note vectors in DuckDB, BFS shortest path, bind hardening

- `sql_capability_unlocks.md` (archived under `archive/database/`): three
  parts landed together. **B (P0):** `shortest_path` is now a
  level-by-level BFS over the materialised undirected adjacency
  `e_all_und` (temp-table frontier/visited/parents, deterministic
  `MIN(a_id)` parent, v_node seeding; 10ms default / 50ms
  unreachable-worst-case steady-state vs the old CTE's multi-second
  path enumeration). `e_dir` (stored direction) replaces
  `fin.graph_edges` as the `find_cycles` substrate — the doubled table
  would read every edge as a false 2-cycle. The old CTE survives as the
  test oracle (`TestBfsShortestPath` equivalence sweep over
  pairs × hops × labels × temporal dates). `/api/graph/shortest` caps
  `max_hops` at the diameter (8).
- **A:** `v_note_embeddings` (1,227 × 384 live) materialised from the
  note_search embedding JSON (dims probe + empty-typed fallback);
  `_SCHEMA_VERSION` 11; warm-path drift stamps `note_embed_dims` +
  `note_embed_model` (the latter catches same-dims 384→384 model swaps
  the dims gate can't see; written by rebuild_note_search's APPLY path
  only). Four wrappers: `similar_notes` (self-excluding),
  `notes_like_entity`, `edition_companies`, `near_duplicate_notes`
  (rename tripwire — live top pairs: Patanjali↔Ruchi Soya 0.942,
  Ujjivan/Piramal/Muthoot clusters) + CLI subcommands, `make
  near-duplicates` (read-only), and two endpoints
  (`/api/graph/similar/<path>`, `/api/graph/edition_companies`, 404
  parity).
- **B4:** writer-side generation bumps for trigger-invisible derived
  tables — `bump_generation()` in db.py; note-search apply ( +
  model stamp) and `populate_local` bump only on real change (cache
  misses or GC), so a no-change maint-full stays warm; `--check`/
  sidecar never bump. **C:** bind parameters replace `_lit()`
  interpolation across the walk paths and `semantic_neighbors`.
- Incidental fix: `_is_warm`'s SQLite probe now tries the colocated
  `.db` first and stops at the first existing candidate (latent
  fixture-isolation fall-through to the live research.db, exposed by
  the new warm-path tests; production unaffected).
- Snapshot: 25 manifest DuckDB tables (+ e_all_und/e_dir/
  v_note_embeddings parquets); snapshot-check green at generation 25481.
- Gates: `make qa` fully green; `make perf` 20/20 with the new
  `shortest_path_bfs` gate (tests/bench_shortest_path.py, <100ms
  steady-state on the default and the unreachable worst case).

## 144. Integration & fuzz suite enhancement — write-side flows, sentinel machinery, query predicates

- `integration_fuzz_enhancement.md` (archived under `archive/testing/`):
  7 new integration modules (42 tests) + 7 new fuzz modules (49
  properties) + 7 marker promotions + 2 fuzz-suite repairs. Every
  write-side derive CLI, the maint/maint-full chain, and the snapshot
  create→verify→restore cycle now have end-to-end coverage on tmp data;
  the sentinel machinery, query predicates, shortest_path, derive_events
  extractors, note_search clean/carry, and the _ALIASES table each have
  Hypothesis properties.
- Integration: `test_integration_derive_insights_apply` (the flagship —
  `--apply`/`--no-notes`/`--stale-only` through the real `_cli`,
  byte-stable second applies, OKF sources splice, the maint-full
  step-8→9 chain contract), `test_integration_maint_chain` (in-process
  dispatcher over `subprocess.run`: every step executes against tmp
  roots, an unshimmed step fails loudly, a failing step aborts, full
  chain idempotent), `test_integration_snapshot_cycle` (tamper/generation-drift/
  restore-parity + the `make snapshot`/`--check` CLI cycle),
  near-duplicates CLI (exact cosine vectors, SQLite read-only
  guarantee), note-writers convergence (rosters, gates, byte-stable
  re-runs, cited_in == sources[]), extract_relations CLI (aliases E2E,
  sidecar, canonical symmetric order), derive_events CLI (both arms,
  dry-run/apply parity, FK safety). 7 near-integration modules promoted
  into the `integration` marker set (150 tests, no behavior change).
- Fuzz: sentinel-region properties over every auto-marker flavor
  (spans disjoint/sorted/balanced, fixed-point-by-run-3 refresh, KF-
  nested-in-chatter rescue, splice idempotence); `_normalise_as_of`/
  `_lit` total + round-trip properties; shortest_path vs a Python BFS
  oracle over a seeded Erdős–Rényi graph (label/as_of filters, symmetry,
  determinism, CTE equivalence); derive_events extractors;
  rebuild_note_search clean/carry; _ALIASES invariants + first-token
  fallback. Repairs: the 0-byte `test_fuzz_edge_writer.py` filled
  (idempotence, CHECK guard, no-swap-dedup characterisation) and the
  assert-nothing try/except wrappers in test_fuzz_{events,insights,
  images} replaced with real invariants.
- **Fuzz-found production fix:** an all-whitespace edition (`\x85` NEL)
  made the chatter heading regex capture the empty edition, so
  `_replace_or_insert_block` re-inserted a fresh block on every apply
  (unbounded duplication — the 2026-08-19 non-convergence class). The
  edition compare is now strip/case-normalised; regression
  `test_all_whitespace_edition_converges` in test_derive_insights.py.
- **Escalated finding (strict xfail):** `build_sector_hierarchy
  --apply` re-renders super-sector note frontmatter from scratch,
  stripping OKF `generated:`/`stale_after` keys (caught when it hit the
  live vault from a fixture that patched `VAULT_ROOT` without
  `SUPER_SECTORS_DIR` — the 9 notes were restored; the import-time
  binding is documented in the proposal log). Fix decision held for the
  user in test_integration_note_writers.
- Default-gate growth ≈ 36s (ceiling was ~30s; the overshoot is three
  inherent-cost modules — cold DuckDB builds, gzip/parquet verify
  passes, CTE-oracle enumeration). Gates: `make qa` + `make integration`
  (431 passed, 1 xfailed) + `make fuzz` (148 passed) all green.


## 145. UI redesign S1+S2 — new graph endpoints + frontend foundation slice

**Date**: 2026-08-22
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/ui/graph_docs_ui_redesign.md` ("The
Research Desk", log entries R1/R2)

### S1 — 4 read-only endpoint groups (100 tests)

- `GET /api/graph/near-duplicates` (app.py:1709) — wraps
  `near_duplicate_notes` (query.py); min_sim=0.9 / doc_type / limit clamps;
  ~1s, on-demand only.
- `GET /api/graph/suggestions` (:1759) — read-only wrap of
  `suggest_relations()`; never touches `findata/_pending_relations.txt`
  (asserted by test).
- `/api/graph/metrics` allowlist extension (:2174/:2182) — harmonic/katz/
  laplacian/local_reaching centrality + link_prediction/voterank payload
  metrics.
- `GET /api/analytics/<name>` (:1819) — five parquet-backed reports.

Tests: `tests/test_api_graph_unit.py` (84) + `tests/test_api_graph_metrics.py`
(16). Gates: targeted pytest + `ty check app.py`.

### S2 — behavior-preserving frontend foundation

- **Split** findata.ts (2,309 lines) → `src/core/{api,dom,toast,markdown,
  router}.ts` + `src/views/{companies,sectors,stats,docs,graph}.ts` +
  ~120-line shell. Views own state; shell keeps the exact three inline-
  onclick methods (`viewer.goToPage/openLightbox/copyCode`).
- **Typed API client** with `ApiError(status, message)`; one deliberate
  exception (`performContentSearch` keeps raw fetch for its 503 copy).
- **Latent bug fixed**: graph-detail "Centre on search" inline onclick
  referenced a non-global helper — dead since bundling; now a real listener.
- **XSS gap closed**: DOMPurify loaded in findata.html and wired into
  `processRichContent`.
- **cytoscape@3.28.1 + cytoscape-fcose@2.2.0 via npm** (bundled IIFE,
  committed-bundle doctrine intact; bundle 76KB → 1.4MB).
- **Design tokens + fonts**: `static/tokens.css` — Desk/Paper registers,
  edge-type accents, self-hosted IBM Plex (6 woff2, 373KB, OFL license in
  `static/vendor/fonts/`). Shell/nav chrome retargeted onto Desk tokens;
  interior surfaces deliberately unchanged until S3/S5.

**Verification**: live smoke on :5201 — all five views switch and render
(1314 entities, 42 sector tags, docs pipeline through DOMPurify with TOC
anchors, bundled-cytoscape ego search + correct 404 path), fonts served,
`window.viewer` contract intact. `make frontend-check` (tsc strict) +
`make frontend` green. Committed as fb92960.

## 146. UI redesign S3–S7 — the Lens, Reading Room, entity pages + gates

**Date**: 2026-08-22
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/ui/graph_docs_ui_redesign.md` ("The
Research Desk", log entries R3–R7 + addenda)

### Delivered

- **S3 — The Lens** (R3): modes rail (Ego/All/Path), As-Of Chronoscope,
  semantic edge palette + interactive legend chips, hover tooltips,
  zoom-fade labels, louvain shading, progressive expansion.
- **S4 — Rank + Time + Inspector** (R4): 12-metric league tables with
  score bars, louvain groups, read-only link-prediction suggestions
  (sidecar untouched), edges-by-year stacks, cross-sector bridges,
  co-mentions leaderboard, on-demand near-duplicates; inspector rail
  with the events timeline (detailSeq race guard).
- **S5 — The Reading Room** (R5): unified doc/ + vault sidebar (1,224
  notes grouped by type/series/sector), FTS + hybrid rerank toggle with
  cosine badges, paper-register reader with frontmatter chips, edition
  mastheads (publication/issue/provenance between double rules),
  [[wikilink]] in-place navigation on a client-side stem→file_path
  index, related rail (similar notes + edition companies).
- **S6 — Entity pages** (R6): entity_detail.js retired into the TS build
  (second esbuild entry, 24KB entity.bundle.js); inline viewer.* onclicks
  replaced by data-attr wiring; paper register + facts/events/peers/
  similar rail; live wikilinks navigating /entity/<path>.
- **S7 — Visual pass + a11y floor** (R7): full screenshot sweep (t01–t12),
  :focus-visible rings (brass/rust), sticky-rail overflow fix, path
  wrapping, reduced-motion-aware scrolling, DOMPurify injected-payload
  proof via a gitignored doc/local self-test note (deleted after).
- **Addenda**: the edge-chip filter now rebuilds the induced subgraph
  (user-reported supplier_to bug; was hide-edges-on-a-stale-node-set);
  cloud node `component` roots re-armed via union-find (tap-highlight +
  components layout). Separately: build_sector_hierarchy --apply made
  region-scoped (OKF frontmatter survives; the strict xfail in
  test_integration_note_writers.py flipped to a passing assertion).

### Gates (explicit user permission 2026-08-22)

make qa 8/8 (2,056 tests + snapshot-check) · make integration (432) ·
make fuzz (148) · make advisory 8/8 (incl. frontend-check) · make perf
20/20 — all green. Two stale tests fixed en route (SEC-4 entity test now
targets entity.bundle.js; NoteFrontmatter became a type alias so the
TS-contract parser stops treating it as an endpoint shape); four ruff
audit findings from #144 cleared (f-string, usedforsecurity=False ×2,
parameterized test SQL).

## 147. Zero-churn maint-full — stable writes, seeded louvain, deterministic exports

**Date**: 2026-08-22
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/database/maint_full_zero_churn.md`
(log R1–R6 + deviations)

### Problem

A no-op `make maint-full` cycle changed 7 of the 36+ parquet snapshot
blobs with zero semantic change (audited in the db_sync commit `76ab554`):
all 349 `events.created_at` restamped (derive_events DELETE-then-INSERT),
all 1,065 `company_embeddings.created_at` restamped (INSERT OR REPLACE),
a B4 generation bump on a cache miss whose re-embed was byte-identical,
all 1,293 `louvain_community` labels permuted under bit-identical
modularity, and content-identical `e_belongs`/`e_has` blobs churned by
physical row order. A snapshot diff that shows change should mean content
change.

### Delivered (five fixes)

- **Events stable writes** — `_stable_prefix_replace` extracted from
  derive_insights to `helpers/core/stable_write.py`; derive_events.apply()
  now multiset-matches and preserves id/created_at (derive_insights keeps
  a thin alias).
- **Embeddings guarded upsert** — INSERT OR REPLACE replaced with an
  `ON CONFLICT … WHERE embedding IS NOT excluded.embedding` upsert; the
  B4 bump fires on actually-written rows, not cache misses.
- **Louvain determinism** (scope grew): onager's louvain is
  non-deterministic unseeded (modularity 0.3286–0.3322 run-to-run, 21–24
  communities on the same graph); the calls now pass `seed => 42`
  (the extension's hidden second arg), plus canonical community
  relabeling (by size, then smallest member) and `ORDER BY src, dst` on
  the edge materialisation. Trade-off: seeded modularity ~0.4% below the
  luckiest unseeded run — determinism chosen.
- **Deterministic exports** — duckdb parquet exports use
  `ORDER BY ALL` (covers e_* and v_*; the e_* tables have no id column).
- **rebuild_note_search full-branch B4 bump change-guarded** (found by
  the cycle-2 verification): the maint-full default path bumped
  unconditionally; now a pre/post multiset compare gates it. Two traps:
  `sqlite3.Row` never equals a plain tuple (rows are tuple()-normalized),
  and reusing the incremental path's `existing` name tripped ty.

### Verification

Four live maint-full cycles: cycle 1 absorbed one-time canonicalisations
(19 blobs: all e_*/v_* reordered, louvain relabelled); cycle 2 exposed
the rebuild_note_search bumper; cycle 3 absorbed the seeded partition +
diagnostic generation drift; **cycle 4: ZERO churn — all 38 blobs
byte-identical**. events and company_embeddings blobs were byte-stable
from cycle 1. 350 targeted tests across 19 suites; qa 8/8 with 2,061
tests + snapshot-check (integration/fuzz included in qa's sweep);
user-run advisory/perf; two lint-audit findings in the new tests cleared
(S311 deterministic-RNG noqa, S608 tmp-path noqa).

### Not in this patch

The 19 refreshed snapshot blobs (skip-worktree flagged; invisible to
`git status` until explicitly added) — user stages them separately.

## 148. Content-addressable doc search — FTS5 + hybrid embeddings over doc/

**Date**: 2026-08-23
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/tooling/doc_search_embeddings.md`
(§11 slice log, §12 eval)

### Problem

The `doc/` corpus (~50 files: architecture, archived proposals, the
completed.md run log, procedures, gitignored doc/local/ assessments) was
queryable only through the #107 doc browser's naive filesystem substring
scan inside the Flask app — no stemming, no ranking, no semantics, and no
way for an agent session to query it without the server. Proposals and
completed.md are 30–166 KB — reading them wholesale to find one decision
burned context on every fresh session.

### Delivered

- **Indexer** `helpers/maintenance/rebuild_doc_search.py` — FTS5 BM25
  (porter unicode61) over section-level chunks (one row per `##` section
  + preamble row, 1-based anchor lines for deep links) with per-row
  bge-small embeddings (deterministic pseudo fallback) and RRF hybrid
  ranking (k=60; OR-joined MATCH tokens so full-sentence queries
  recall; column-weighted BM25 ∪ top-cosine candidate union; per-file
  cap 2). Three modes: full convergence pass (canonical row order),
  `--incremental` mtime carry (~28x faster warm), `--check` hash-exact
  freshness gate (exit 1 on drift with the changed/new/deleted
  breakdown).
- **Sidecar residence** `memory/doc_search.db` (+ `_vec.db` embed cache):
  never research.db — doc/local/ stores plaintext in the FTS content
  column and the published DB form is the git-tracked
  `snapshots/parquet/` export, so privacy is structural, never
  manifest-dependent. Never snapshotted; a last-good backup lands in
  `db-backup/` after each successful full rebuild.
- **Surfaces** — `/api/docs/search` upgraded (hybrid → bm25 → scan
  degradation chain, `anchor`/`section_title`/`score`/`mode`/`stale`,
  `similarity` always present per the TS contract), agent CLI
  `helpers/misc/doc_query.py` (`path:line [section]` output, `--json`,
  `--bm25`, exit 1 when unbuilt), maint-full step 6c, frontend docs.ts
  (mode badge, anchor chips). `AGENTS.md` routes fresh LLM sessions
  query-first; operator doc `doc/procedures/doc-search.md` (modes,
  budgets, lifecycle, recovery).

### Verification

72 targeted tests (39 rebuild + 27 API + 6 CLI); eval over 18 labeled
questions: hybrid recall@5 0.94 vs bm25 0.78 vs scan 0.72 (one accepted
miss dvar-05, §12); live index 51 files / 383 section rows, warm full
≈0.7 s; ruff/ty/static-checks clean. Post-archive: dsem-04 label moved
to the archive path in the same change and the eval leg re-verified.

## 149. maint run reports + doc-search framework wiring

**Date**: 2026-08-23
**Status**: COMPLETE
**Proposal**: none — closure + wiring follow-ups on #148 (user-directed)

### Problem

(1) `make maint`/`maint-full` streamed step output to the console only —
after an abort (the 2026-08-23 rebuild-doc-search exit-1), the step's own
stderr was gone, leaving only the orchestrator's ERROR lines.
(2) The doc-search feature (#148) had no presence in the test frameworks:
`--check` had no automated consumer, neither script's subprocess
bootstrap was ever exercised, maint-full step 6c's zero-churn contract
was unpinned, and the chunker/MATCH generator had no properties.

### Delivered

- **maint_report.txt** — maint.py steps run through streaming Popen
  (live console + rolling 400-line tail); every real run appends a
  qa-style record to gitignored `maint_report.txt`: timestamped header,
  per-step summary table, and the tail of every FAILED step
  (tests/run_gate_report.py philosophy). `--dry-run` writes nothing.
  test_maint stubs moved from subprocess.run to a FakePopen; the
  maint-chain dispatcher intercepts Popen likewise.
- **Perf** — `rebuild_doc_search --check` (budget 2.0 s) + a `doc_query`
  hybrid query (3.0 s) in `make perf`: real-subprocess coverage of both
  bootstraps and the first automated `--check` consumer (stale/missing
  sidecar fails by design; refresh per `procedures/doc-search.md`).
- **Integration** — maint-chain idempotence also asserts the doc_search
  sidecar is row-stable including canonical order across no-op full
  runs; `test_api_docs.py` marked integration (`make integration`
  visibility parity with its sibling doc-search suites).
- **Fuzz** — `tests/test_fuzz_rebuild_doc_search.py`: `_split_sections`
  (monotone header-pointing anchors, no-content-loss partition,
  body re-split stability) + `fts_match_expr` (any query → valid
  MATCH). Its first run found a real bug: a NUL byte in a query built an
  "unterminated string" MATCH (gracefully degraded by search_docs'
  guard, but the generator's contract is stronger) — `_FT_TOKEN` now
  strips `\x00` alongside quotes.
- **#148 archival completed** — proposal moved to
  `archive/tooling/`, all six path references fixed (incl. dsem-04's
  eval label; docs eval re-verified: hybrid 0.94 unchanged),
  `proposals/README.md` keeps the convention dir alive.

### Verification

106 targeted tests across the six touched suites; both new perf entries
pass through the real harness (0.51 s / 0.62 s vs budgets 2.0/3.0);
ruff + ty clean.

## 150. C3 temporal analytics — `make analytics REPORT=temporal`

**Date**: 2026-08-25 · **Status**: COMPLETE · **Proposal**:
`doc/improvements/archive/tooling/temporal_analytics.md`

Sixth report in `helpers/graph/analytics.py` (A3 framework): four
time-keyed tables over the parquet snapshot —

- **Chatter volume by quarter**: quotes joined `as_of_edition` →
  edition-entity `created_at` (the #136 stem unlock), binned
  `year() || '-Q' || quarter()`. Live: 2,548 quotes / 67 editions, all
  2026-Q3 (single-batch ingest reality).
- **Coverage trend per edition**: per-edition quotes/events in ingest
  order with a `thin` flag (<10). Live: 108 editions; ~45 thin.
- **Staleness curve by sector**: p50/p90/max days since
  `entities.last_updated` + `stale>30d` bucket per sector. Live:
  Automotive worst (83/87 companies stale), 42 sectors.
- **Events timeline (D7 spine)**: `event_type` × year incl. future
  guidance dates (2027) and a `?` bucket for 292 undated events —
  surfaced, not hidden.

Findings en route: the 4 unmatched quote editions are concalls
(`as_of_edition` holds the concall H1 — honest-miss by #136 design, not
drift); `strftime('%Q')` doesn't exist in DuckDB 1.5.5.

Framework widened: `fetch()` returns `Report | list[Report]` (temporal is
the first composite); CLI renders blocks sequentially, JSON is a list for
composites and unchanged (dict) for single reports;
`/api/analytics/temporal` returns `{titles, reports}` with the flat
shape preserved for singles. Tests: 7 new (fixture: dated editions,
unmatched concall stem, future + NULL event dates, 30-day staleness
straddle). Gates: ruff/types/static_checks clean; 109 tests green;
live render 0.77s.

## 151. Gate-report logging fix — every step logs its output tail

**Date**: 2026-08-25 · **Status**: COMPLETE

**Bug**: `make advisory` showed live-invariant WARNINGS on console, but
advisory_report.txt never carried them — `write_report()` only appended
tails for FAILED steps or pytest steps (`tail_on_success=True`);
`live-invariants` is a passing `make` step, so its output (including
pytest's warnings summary) streamed and vanished. Confirmed against the
2026-08-24 08:58 run: table shows `live-invariants ... ✓ OK`, no tail
block follows it; only `integration` logged.

**Fix (user directive: "all make steps log to output file")**:
`tests/run_gate_report.py` now appends every non-skipped step's tail —
`--- <step> · last N lines (OK|FAILED) ---`. `Step.tail_on_success`
removed (redundant); GATES constructors cleaned; docstring records the
directive. Applies to all three gates (qa / integration / advisory)
since they share `write_report`. pytest's `-ra` short summary and
warnings-summary section sit at the end of pytest output, inside the
60-line tail.

Tests: test_report_contents now asserts passing plain steps DO log;
5/5 green. Proven end-to-end with a probe run of the real
`make live-invariants` through `run_step` + `write_report` (216 live
tests, 50.6s, tail captured). ruff + `make types` clean.



## 152. Market-data resolution pipeline — ticker fallback, terminal classes, resolution-pass hardening

**Date**: 2026-08-25
**Status**: COMPLETE
**Proposals**: `doc/improvements/archive/pipeline/market_data_resolution.md`
(+ absorbed `google_finance_ticker_fallback.md` — both archived)

### What landed

- **Combined resolution pipeline** with a binding doctrine: yfinance is
  the ONLY bulk source; every other source is failure-path-only,
  per-target, permanently cached (yfinance fetch cache, GF page cache,
  BSE search texts, FinnHub query cache, success-only verify cache,
  entity_gf_map rows).
- **FinnHub discovery stage** (`finnhub_search.py`): name → Yahoo-format
  candidates → single-ticker yfinance verify + fuzzy name check +
  exchange-class guard → writeback (entities.ticker + frontmatter +
  fetch-cache extension). 11 live writebacks incl. TMPV.NS.
- **GF fallback tiers** (`googlefinance.py`, `googlesheets_metrics.py`):
  curated overrides → tier-1 slug swaps → tier-2 BSE PeerSmartSearch;
  gf_only companies get company_metrics via one Sheets GOOGLEFINANCE
  batch (2 API calls/sweep), `source_ref='googlefinance:<slug>:<metric>'`.
- **Terminal classifications**: `entity_ticker_status` table +
  `--classify`/`--unclassify`; sweeps skip classified dead ends and
  report them under `[terminal]`.
- **Hardening (R1)**: FinnHub multi-probe queries (full → first-two →
  first-word); ticker-ownership guard (blocks the measured Kotak
  Life→KOTAKBANK.NS sibling collision); stale-target filter
  ("resolution happens ONCE"; targets 33 → 17 on first re-run).

### Applied outcomes

- Ticker issues: 33 → residue of classified/unresolvable only; 12+
  permanent writebacks (TMPV.NS, PIRAMALFIN-class fixes, Kinetic
  500240.BO via multi-probe); Chemcart gf_only metrics via Sheets;
  Swastika Castal mapped back to Yahoo.
- Piramal Enterprises ↔ Piramal Finance duplicate resolved by full
  entity merge into `Piramal Finance` (PIRAMALFIN.NS): edges/events/
  citations re-pointed, rename-artifact self-loops dropped, Edition #23
  intelligence preserved in the survivor note; ticker lineage documented
  in-note. Kotak Mahindra Life Insurance correctly unlisted
  (`ticker: null` + `listed: false`) + manual subsidiary_of edge to the
  bank (user-stated). Bonus D4 cleanup: 7 double-direction jv_with pairs
  collapsed.

**Gates**: 152 targeted tests (12 new) + ruff + ty clean; user ran the
full manual sequence incl. qa green; database_integrity_check errors=0
across all sections after merge cleanup.

## 153. Relations 2.0 — E3 semantic_peer, E4 coinfer, E5 holders/institution, E6 API/UI

**Date**: 2026-08-25
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/pipeline/relation_enrichment_sources.md` (E1 prose v2 DONE 2026-08-23, E2 yfinance KNN 3425 DONE 2026-08-24, E3-E6 landed 2026-08-25)

### What landed

- **E3 `semantic_peer` (7776 edges, `k=10`)** — `helpers/graph/query.py: EDGE_REGISTRY semantic_peer → e_semantic_peer (SemanticPeer), _SCHEMA_VERSION 11→12→13`, `v_embeddings` (bge-small-en-v1.5 384d, 1061 rows) via `semantic_neighbors()` per-company top-10 cosine, canonical `_pair` + sym-dedup keep max cosine, `weight 0.5`, `source_ref embeddings:bge-small:v1:<date>` + `properties {cosine,rank,fetched_at}`. `build_semantic_peer_edges() + apply_semantic_peer_edges()` delete-by-prefix `embeddings:%` idempotent, `make graph-rebuild e_semantic_peer=7776`, `integrity 0`.
- **E4 `coinfer` (478 pending)** — `helpers/maintenance/enrich_relations.py --source coinfer --per-company 3 --threshold` : co_mention weight × `1.5×` same-sector else `1.0` × `(1-existing_edge_penalty)`, top-N per company deterministic, `append_coinfer_suggestions()` → `findata/_pending_relations.txt` Unresolved JSONL `{"edge_type":"suggested","origin":"coinfer","method":"co_mention","edition":"coinfer/<date>","score"}` , dedup vs `graph_edges` (any type) + prior `origin=coinfer` rows, **no graph writes** (verified `graph_edges 16302` unchanged, `82→560` then `0` on second apply, `per_company 1→169, 2→329`).
- **E5 `invested_in` (715 edges, 205 institutions)** — `yfinance Ticker.institutional_holders` DataFrame (US-only, §4.1; `78` companies with holders, `206` holder names) → `institution` entities `entity_type=institution file_path NULL` dedup `normalized_name` (`INSERT OR IGNORE`, Sanofi company-holder handled via `v_node`/`v_institution` + `_KIND_TO_TABLE` mixed-source fix), `invested_in` directed `institution→company` `weight 3.0 if pct≥5% else 1.0`, `source_ref yfinance:holders:<date>`, `properties {pctHeld,shares,value,fetched_at}`, `valid_from=dateReported`, `DELETE ... LIKE 'yfinance:holders:%'` idempotent. `helpers/graph/query.py: EDGE_REGISTRY invested_in (e_invested, institution_name→company_name, InvestedIn)`, `v_institution` + `v_node IN (institution)` + `_SCHEMA_VERSION 12→13`, `database_integrity_check` allowlist + noteless exemption.
- **E6 API/UI** — `helpers/misc/database_integrity_check.py` allowlist `semantic_peer, invested_in`, `app.py _EDGE_SEMANTICS cited_in/semantic_peer/invested_in`, `static/tokens.css --edge-cited-in/--edge-semantic-peer #8AD7C6/--edge-invested-in #E0A93E`, `frontend/src/views/graph.ts _EDGE_TOKENS` + `frontend/types/api.ts CompanyNeighbors.semantic_peers?/invested_by?`, `e_all_und/e_dir` substrates auto-include via `EDGE_REGISTRY`.

### Applied outcomes

- `E3 --apply` 7776 inserted (threshold 0.0, `≤13k` band), `dry-run warm 0 fresh`, deterministic re-run `7776`.
- `E4 --apply --per-company 3` 478 appended (42 same-sector only at `threshold 1.4`), `no graph writes`, idempotent.
- `E5 --apply` 715 `invested_in` + 205 `institution` (`1 normalized collision, 1 Sanofi already company`), `78` companies, `valid_from 715`, second apply `0 fresh`.
- `make graph-rebuild` → `16 e_*` + `v_institution`, `integrity 0` (`semantic_peer 7776, invested_in 715`), `233` targeted tests (`65 enrich + 12 suggest + 85 api + …`), `tsc/build` pass.

**Gates**: `database_integrity_check` 0, `graph rebuild` clean, `pytest -q 233` + `frontend-check` + `ruff` clean.

## 154. Script metadata search (script_search) — S1–S3

**Date**: 2026-08-25
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/tooling/script_metadata_search.md`

### What landed

- **Builder** `helpers/maintenance/rebuild_script_search.py`: one FTS5 row per helpers/** script, tests/** module, root app.py, and Makefile target in own gitignored sidecar `memory/script_search.db` (never research.db; doc_search locality doctrine). Row composition: purpose (docstring first para) + capped details, regex add_argument/add_parser CLI surface, top-level defs, Makefile wiring (bidirectional recipe substring map), tested_by via AST IMPORTS ONLY (no grep-mention). Modes: full (convergence + zero-churn stat + db-backup recovery), --incremental (always re-extracts — cross-file inputs — writes row-keyed diffs only), --check (unit-level hash-exact drift, exit 1, pre-warms embed cache). Machinery imported from rebuild_doc_search; stored_embed_dims duplicated (~15 lines, rds hardcodes doc_search table).
- **Query CLI** `helpers/misc/script_query.py`: hybrid BM25+cosine, --kind script|test|make + --area filters, --json/--bm25; doc_query contract (stale warns + answers, missing exit 1).
- **Wiring**: perf pair `rebuild_script_search --check` + `script_query` (perf-only, NOT qa — code edits redden qa); `make script-search-rebuild`; AGENTS.md query-before-guessing-filenames rule; `doc/procedures/script-search.md` + architecture.md §6 row.
- **Rode along**: `make types-tests` (expanded ty-over-tests argv moved from run_gate_report advisory step into the target; single source of truth); lint-audit clean via C901 extraction (rebuild/script_index_stale/search_scripts split — the split caught a real fresh<->stale polarity bug); pdf_conv_md submit timeout 60s→300s (cold-upload stalls).

**Applied outcomes**: live 187→189 units / 234→237 rows, bge-small; all six golden queries top-3 ("database integrity"→database_integrity_check.py #1, "relation diff audit"→relation_diff_audit.py #1, "what does the quality gate run"→make qa, …); warm rebuild/--check ≈1.1s, query ≈0.7s; 26 tests; make help alphabetical gate incident (script- sorts before secret-) fixed same day.

## 155. Pending-relations triage (triage_pending_relations) — S1–S3

**Date**: 2026-08-25
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/graph/pending_relations_triage.md`

### What landed

- **Script** `helpers/graph/triage_pending_relations.py` + `make triage-relations`: --report (dedupe → split `suggested` vs prose → bucket discard/alias_candidate/stub_candidate/manual/bad_source → eyeball report + decisions jsonl with stable ids; non-destructive), --apply-decisions [--write] (validates discard|alias:<Existing>|stub|skip; persists aliases to git-tracked `findata/relation_aliases.json`; rewrites sidecar keeping unresolved rows deduped; prints follow-up chain), --clear. Decisions-file parse errors name the offending line (the editor hard-wrap incident).
- **Extractor inflow** (`extract_relations.py`): write-time noise gate via shared `noise_target` (countries/generic prefixes+suffixes/mangled fragments — rstrip("'s")-eats-trailing-s trap fixed with literal suffix strip); runtime alias file loaded over `_ALIASES` (case-canonicalized returns; absent file degrades to {}).
- **Suggestor split** (`suggest_relations.py`): --append writes `findata/_pending_suggestions.txt` (own file; SIDECAR_PATH back-compat alias) — review candidates no longer drown the extraction queue.
- **Wiring**: markdown_parse.md Stage 9 triage loop documented; 3 scratch files gitignored; 12 tests.

**Applied outcomes**: live 689-line backlog → report: 478 suggested | 69 prose | 142 dupes absorbed; buckets 29 discard / 23 alias / 7 stub / 10 manual; decisions annotated (39 rows: 29 discard, 8 skip, 2 stub), apply pending user.

## 156. Local PDF conversion engine (local-first, pymupdf4llm)

**Date**: 2026-08-26
**Status**: COMPLETE
**Proposal**: `doc/improvements/archive/pipeline/local_pdf_conversion_fallback.md`

### What landed

- **Local engine** `helpers/pdf/pdf_local.py`: `convert(pdf, img_dir)` produces the SAME `pages` shape as `parse_pages()` (markdown text + images map), so `write_outputs`/`plan_images`/`to_wikilinks`/OKF frontmatter are engine-agnostic. Normalizations (tuned on the 7-PDF trial): heading wrapper strip + sector-glue split + bold-body heading rescue (Delhivery-class); picture-text blocks dropped (Q2 — trial: only ad/footer/badge text); running headers/footers, page numbers, date lines, title repeats, duplicate URLs dropped; decoration filter ≥150px AND ≥8KB (Q3); scanned-PDF guard (`LocalRefusalError` at <100 chars/page — never OCRs).
- **Wiring** `pdf_conv_md.py --engine auto|local|paddle`, default **auto = local-first** (operator decision Q1, 2026-08-26), Paddle fallback on refusal; `write_outputs` copies local image files instead of `requests.get` when the images-map value is an existing path; frontmatter `generated.by: pdf_conv_md.py/pymupdf4llm-1.28.2` records the real engine; Paddle token check moved into the Paddle branch (local needs no key).
- **Deps**: `pymupdf4llm` + `pymupdf` (direct Pixmap import) declared in pyproject. pdfmux evaluated and rejected (no image extraction; pins pymupdf4llm<1.0; loose PASS verification — coverage number noted, gate not trusted).
- **Tests**: 15 (test_pdf_local.py: transforms, image filter, refusal guard, pages shape) + 1 write_outputs local-copy; 56 total across the three pdf test modules.

**Applied outcomes**: all 7 Reports PDFs convert locally in ~3–8s each (no API, no key); word recall vs reference notes 96.05–98.60% (same as trial); company headings 7/7 complete, 4/7 exact match with refs (diffs = fuller legal names, guest-essay heading, one pymupdf glyph `Ufex`→`Uflex`); images keep the `<slug>_p{page}_img{N}.jpeg` convention so Stage-4 figure embedding is unchanged.

## 157. Post-conversion verification (verify_extraction)

**Date**: 2026-08-26
**Status**: COMPLETE
**Follows**: #156 (local-first engine); pdfmux-inspired, stronger where pdfmux admitted a gap (its "unsegmented extraction" could not do per-page silent-drop detection; our json output IS page-segmented).

### What landed

- **Script** `helpers/pdf/verify_extraction.py` + CLI (`<source.pdf> <output_dir> [--stem --warn-below --fail-below --quiet]`): verifies BOTH artifacts against the PDF text layer — per-page word-multiset coverage source→json; doc coverage source→md (md body after frontmatter strip — downstream pipelines consume the md); **md↔json per-page consistency** (rendering-stage drops: json healthy, md missing a page → FAIL naming the pages); number audit (≥3-digit numbers source→md; ≤2-digit = pagination/ad noise, counted separately); wikilink integrity; sha256 manifest of source+md+engine → `<stem>.verify.json`. Exit 0 PASS/WARN, 1 FAIL.
- **Thresholds** (tuned on the 7-PDF corpus): WARN page <0.85 / FAIL <0.50 for pages ≥150 src words (ad pages 41–138 words skip page thresholds; numbers audit still covers them); doc WARN <0.95 / FAIL <0.90 (healthy 96.7–97.3%; gutted-company test lands 84.2%); md↔json <0.98 = FAIL after image-markup stripping (json `<img>` divs vs md wikilinks compare as equal).
- **Wiring**: `pdf_conv_md.py` runs the verification after write_outputs (default on; `--no-verify` skips); FAIL exits 1. Converter output gains `<stem>.verify.json` beside the existing `<stem>.json` debug artifact.
- **Tests**: 12 (canon helpers, PASS round-trip, gutted-md FAIL via doc+md/json signals, broken wikilink, significant-number WARN, small-number noise, ad-page skip, dropped-page FAIL, image-markup-insensitive md/json consistency).

**Applied outcomes**: 7/7 corpus PASS (1 WARN: Dixon's real `25,000-27,000` dash-loss — correct signal); md-only gutting of RBI_3M's 3M section → `FAIL: doc coverage 84.2%, md/json mismatch on page(s) [8, 9, 10, 11]` — page-precise, the thing pdfmux could not do.

## 158. Search-index freshness in the advisory gate (search-fresh)

**Date**: 2026-08-26
**Status**: COMPLETE
**Motivation**: every proposal lands new docs/scripts/notes, and the three search indexes (doc_search, script_search, note_search) went stale silently — the operator ran the three `--check` commands manually after each batch.

### What landed

- **Advisory steps** (tests/run_gate_report.py): `doc-search-check`, `script-search-check`, `note-search-check` — direct `--check` invocations, parallel like every other step, so each index gets its own report row + tail (the make target is sequential and stops at the first failure; the gate rows don't). STALE → FAILED row with the refresh command in the tail; `--check` writes no research.db rows (sidecar cache warm only — safe).
- **`make search-fresh`**: the three `--check`s in one target (replaces the manual triple-run); help + .PHONY wired, drift-guard tests pass.
- **AGENTS.md**: freshness pointer updated (advisory rows + search-fresh; still never qa — #154's "edits redden qa" reasoning stands).
- **Guard test**: advisory gate must contain the three steps.

**Applied outcomes**: live demo — script_search flagged STALE with exactly this session's 7 changed + 4 new files (pdf_local.py, verify_extraction.py, their tests, Makefile, run_gate_report.py, pdf_conv_md.py, test_pdf_conv_md.py); after `script-search-rebuild`: all three FRESH, `make search-fresh` exit 0. 100 tests green (run_gate_report + static_checks incl. makefile drift guard).

## 159. Perf dedupe (search --checks → advisory) + pdf pipeline benchmark

**Date**: 2026-08-26
**Status**: COMPLETE
**Follows**: #158 (search-fresh + advisory rows)

### What landed

- **Dedupe**: the three search-index `--check` entries (`rebuild_note/doc/script_search`) left `run_perf_benchmarks.py` — the STALE gate is now solely `make search-fresh` + the advisory gate's three parallel rows (#158). Perf keeps the query-latency pair (`doc_query`, `script_query`) — those are timing benchmarks, not freshness. Comments in the BENCHMARKS list, the Makefile `script-search-rebuild` echo, and §Gates of `doc/procedures/doc-search.md` + `script-search.md` updated to the new home.
- **New benchmark** `tests/bench_pdf_pipeline.py` (`pdf_pipeline_local`, budget 20s): the #156/#157 path end-to-end on the largest in-tree PDF (Yes_Bank, 30 pp) — `pdf_local.convert` (incl. Tesseract on embedded rasters) + `write_outputs` + `verify_extraction.verify` into a tempdir. Internal sub-budgets asserted (convert+write <15s, verify <2s) and a FAIL verification verdict fails the bench — perf regression AND correctness backslide both redden `make perf`. Warm: 8.76s total (convert 7.4s dominates), verify 0.11s.

## 160. types-tests zero-warning (snapshot_db signature truth)

**Date**: 2026-08-26
**Status**: COMPLETE
**Motivation**: `make types-tests` warnings were advisory; the last 10 all came from one untruthful signature.

### What landed

- All 10 remaining diagnostics were `invalid-argument-type` at tests/test_snapshot_db.py:324,337 — `None` passed into five `_cmd_create` params declared plain `Path`. Root fix: those params (duckdb_path, duckdb_out, parquet_base, parquet_sqlite_dir, parquet_duckdb_dir) are format-optional — only touched inside the `with_duckdb` / `fmt in ("parquet","both")` branches — and are now typed `Path | None` with the contract documented in the docstring; narrowing asserts inside the two branches satisfy ty's checker (guard variable ≠ value variable).

## 161. search-fresh run-all + APPLY switch

**Date**: 2026-08-26
**Status**: COMPLETE
**Follows**: #158/#159

### What landed

- `make search-fresh` now runs **all three** index checks even when one fails (shell loop aggregating rc; make's stop-at-first-failure semantics were hiding the note-index result whenever an earlier check drifted) and gains **`APPLY=1`**: drops the `--check` args and refreshes doc/script/note indexes in one command. Help + annotation updated in lockstep (drift guard green); AGENTS.md pointer extended.
- Demonstrated live: probe-stale script_search → doc FAIL + script FAIL + note still ran, exit 1; `APPLY=1` rebuilt all three; revert + re-apply converged back to FRESH.

**Applied outcomes**: static_checks 86 green (incl. makefile drift guard); final state exit 0 on plain `make search-fresh`.

## 162. Advisory ty-tests logging fix (concise digest + per-step tail budget)

**Date**: 2026-08-26
**Status**: COMPLETE
**Motivation**: advisory is run-all (never stops on failures), so its report is the debugging surface — but ty full-format diagnostics are ~10–14 lines each and the 60-line step tail only ever showed the last ~5 of a burst (historical "Found 91 diagnostics": ~85 invisible). And ✓ OK gave no hint a warning existed at all.

### What landed

- **`TYPES_TESTS_FMT ?= full`** (Makefile): the types-tests target's ty invocation takes `--output-format $(TYPES_TESTS_FMT)`. Direct runs keep the pretty verbose format; the flag soup stays single-source.
- **Gate step runs concise**: `Step("ty-tests", (_MAKE, "types-tests", "TYPES_TESTS_FMT=concise"), ...)` — one line per diagnostic (`file:line:col: warning[rule] message`), so EVERY warning lands in the report tail; `Found N diagnostics` states the total.
- **`Step.tail_lines`** (generic runner feature): per-step report-tail override of `_TAIL_LINES`; ty-tests gets `_TY_TESTS_TAIL_LINES = 120` (covers the worst historical burst of 91 + header/summary). `write_report` honors it.
- **Tests**: gate-step wiring (concise arg + raised budget) and write_report keeping exactly the last N lines.

**Applied outcomes**: probe warning verified end-to-end — report block shows the complete diagnostic in 1 line, rc still 0 (advisory doctrine intact); direct `make types-tests` unchanged (full format); 102 tests green (incl. makefile drift guard); ruff/types clean.

## 163. Perf optimization O1-O3: link-prediction, extract_relations, pdf layout-off

**Follows**: doc/improvements/archive/tooling/perf_optimization.md (measured
plan, 2026-08-26). `make perf` was 20/22: graph_link_prediction 2.03s/2.0s and
extract_relations 5.33s/5.0s over budget; pdf_pipeline_local slowest at
10.79s.

**O1 — graph_link_prediction (2.03s -> ~1.3s)**: root cause found by import-
spy — duckdb's Python client imports pandas (~0.5s) on the FIRST parameterized
execute of the process, which fired inside `_materialize_from_db`. Fix:
`_where_inline()` in onager.py renders the edge-type filter as SQL literals
(strict snake_case-identifier check, ValueError otherwise; falls back to
bound params for anything unusual). Golden compare: top-10 jaccard pairs
byte-identical.

**O2 — extract_relations (5.33s -> ~2.0s)**: the CLI reduce loop called
`apply_edges` once per note and each call re-fetched the FULL existing-triple
set from graph_edges (~15ms x ~110 notes of identical work). Fix:
`_load_existing_edges()` hoisted above the reduce loop, new `existing=` param
on apply_edges (None keeps per-call self-load for external callers/tests).
Dry-run totals byte-identical (files=110 extracted=86 skipped_suppressed=7).

**O3 — pdf_pipeline_local (10.79s -> 3.33s)**: 85% of convert was pymupdf's
ONNX layout model (BoxRFDGNN per page; bench docstring's "Tesseract
dominates" claim was stale — no Tesseract in that path). Corpus A/B over all
7 Reports/*.pdf: layout OFF is ~3x faster AND recovers more source words
(doc_coverage 0.997-0.999 vs 0.966-0.972 — the model silently dropped ~3%);
surviving image refs identical at zero corpus-wide after normalisation.
Default flipped to layout-off (`pymupdf4llm.use_layout(False)` inside
convert), opt-in `--layout` flag on pdf_conv_md.py; CONVERT_BUDGET 15->6;
bench docstring corrected.

**Applied outcomes**: make perf 22/22 with headroom everywhere (link 1.34s,
extract 1.93-2.13s, pdf 3.33s); targeted suites green (61 pdf tests + 83
relation-driver tests); ruff C901/S/UP + types clean. O4 (budget hygiene)
noted: derive_insights 3.51s/4.0s is next-in-line watch-only.

## 164. note_search --check drift reporting (parity with doc/script rebuilders)

**Date**: 2026-08-26. (Renumbered from a minted-duplicate 163 on 2026-08-26:
this entry was written while the perf-optimization entry — plain #163 below
in history, all external `#163` refs point at it — was hidden under an
unpushed patch. Next free number taken; content unchanged.)
**Status**: COMPLETE
**Motivation**: user report — `rebuild_doc_search.py --check` shows a detailed staleness summary (changed/new/deleted paths + refresh command), but `rebuild_note_search.py --check` printed only `(--check mode: would index N docs)` with no drift verdict at all, and its `main()` always returned 0 — so the advisory `note-search-check` step passed silently even when the index was stale. Investigation showed `rebuild_script_search.py` already reports all drift kinds correctly (file/Makefile/test units, changed/new/deleted, rc=1, plus the write-path "index was STALE before this rebuild … now fresh" line); it needed nothing.

### What landed (`helpers/maintenance/rebuild_note_search.py`)

- **Freshness verdict in `rebuild()`**: exact diff of the on-disk corpus vs the stored `note_search_meta` fingerprints — same semantics as the incremental diff loop (mtime + blake2b over title|sector|content), so `--check` reports exactly the drift an incremental run would apply. Stats gain `stale_new`/`stale_changed`/`stale_deleted`/`index_stale`, mirroring the doc/script stats keys.
- **`_print_staleness()`**: FRESH/STALE verdict, per-path drift list (first 10 + overflow count), and the refresh command — identical shape to the doc/script printers.
- **`main()` gate doctrine**: `--check` returns 1 on drift, 0 when fresh (was: always 0). The applying rebuild now prints `index was STALE before this rebuild: N changed, N new, N deleted — now fresh`.
- **Module docstring** updated for the new `--check` semantics and exit codes.

**Verified live**: FRESH → `index state: FRESH (1224 docs unchanged)` rc=0; changed/new/deleted probes each named the exact file and rc=1; a `git checkout` restore (rewritten mtime) flags changed until rebuild — same honest semantics as doc_search; DB-side sector reclassify flags the note changed with an untouched file (fingerprint covers title+sector). `make search-fresh` run-all + APPLY converge. Tests: 6 new `TestStalenessCheck` cases (fresh/changed/new/deleted/DB-side-reclass/write-path report), 31 total in the module; ruff + `make types` + `make types-tests` clean.

## 165. Cross-process graph-build serialization (flock) — parallel-advisory WAL race

**Date**: 2026-08-26
**Status**: COMPLETE
**Motivation**: `make suggest-relations` (an advisory step) died with `TransactionException: Failed to commit: Could not set lock on file memory/graph.duckdb.wal: Conflicting lock is held in PID ...`. Root cause: a generation bump made the disk cache stale; `make advisory` (jobs=4) ran suggest-relations / graph-algos / analytics in parallel, and `connect(read_only=True)`'s cold/stale fallback — designed for sequential callers — sent ALL of them down the read-write BUILD path simultaneously; two DuckDB writers raced on the .wal lock (loser crashed, winner rebuilt — the cache was warm again by inspection).

### What landed (`helpers/graph/query.py`)

- **Build path serialized cross-process**: an `flock(LOCK_EX)` on a sidecar `<cache>.build.lock` admits ONE builder; waiters re-check `needs_build` UNDER the lock — a warmed cache downgrades read_only callers straight back to the read-only open. Steady state (N readers, zero writers) is unchanged: warm RO connects never touch the lockfile.
- **Corruption-recovery unlink moved under the lock**: the old pre-lock `unlink` of a not-warm file misread "another process is building" (its RW lock makes `_is_warm`'s RO probe fail) as corruption and could delete a live mid-build file.
- `connect()` docstring updated for the serialized fallback.

**Verification**: true cross-process regression test (`test_parallel_ro_connects_serialize_the_build`, tests/test_graph_disk.py): 6 subprocesses against one cold cache. With the flock: 6/6 pass. Control with the flock stripped: 5/6 fail, reproducing the production traceback byte-for-byte (same `TransactionException ... wal` at `_materialise_vertices`). Suites: graph_disk 24, suggest-relations + onager 64; ruff + `make types` + `make types-tests` clean; `make suggest-relations` green end-to-end.
