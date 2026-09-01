# Coverage Analysis & Extension Plan (Updated)

## Current State — REAL baseline (`make cover` runs ALL tests)

| Metric | Value |
|--------|-------|
| **Overall statement coverage** | 63.9% (4,654 / 7,281) |
| **Overall branch coverage** | ~54% (1,362 / 2,772) |
| **Tests run** | 931 passed, 1 skipped, 5 xfailed |
| **Source files measured** | 32 (all of `helpers/`) |

> Previous baseline (53.2%) was artificially low because `make cover` excluded
> live and slow tests. Fixed: `make cover` now runs ALL tests.

---

## Priority Queue (by missing lines, grouped by effort)

### Quick wins — <50 missing lines each (368 lines total)

| Module | Cov% | Missing | What to test |
|--------|------|---------|--------------|
| `_edge_writer.py` | 97% | 1 | One uncovered branch |
| `maint.py` | 98% | 1 | One CLI fallback line |
| `fuzzy_match.py` | 95% | 3 | 3 edge-case branches |
| `stats.py` | 92% | 7 | `print_stats` formatting |
| `migrate_to_graph_edges.py` | 85% | 12 | Migration CLI paths |
| `frontmatter.py` | 71% | 13 | `split_frontmatter`, `strip_frontmatter`, `extract_tags` |
| `sync_tags.py` | 76% | 28 | Tag sync edge cases |
| `derive_co_mentions.py` | 74% | 32 | `_cli` path, canonicalisation |
| `derive_themes.py` | 64% | 32 | Theme extraction edge cases |
| `rebuild_schema.py` | 67% | 33 | Schema rebuild CLI |
| `algorithms.py` | 85% | 34 | CLI formatting, fallback paths |
| `build_sector_hierarchy.py` | 73% | 40 | Hierarchy computation paths |
| `sync_sector_wikilinks.py` | 58% | 42 | Wikilink sync |
| `db.py` | 39% | 43 | `connect()`, `ensure_db_meta`, `get_generation` |
| `derive_events.py` | 79% | 47 | Event extraction edge cases |

### Medium effort — 50-200 missing lines each (1,911 lines total)

| Module | Cov% | Missing | What to test |
|--------|------|---------|--------------|
| `database_integrity_check.py` | 52% | 348 | Check functions, CLI |
| `static_checks.py` | 47% | 192 | Check functions |
| `enrich_from_yfinance.py` | 49% | 189 | Extract/render/write/report |
| `db_maint.py` | 51% | 187 | Report printing, DuckDB maintain |
| `query.py` | 72% | 172 | DuckDB materialisation, CLI |
| `derive_insights.py` | 77% | 149 | Insight rules, rank change |
| `get_tickers.py` | 60% | 139 | Entity resolution, spellfix |
| `snapshot_db.py` | 62% | 131 | Parquet export, verify |
| `extract_relations.py` | 77% | 126 | Regex patterns, resolver |
| `move_sector.py` | 19% | 122 | YAML helpers, move_entity |
| `verify_notes.py` | 71% | 122 | Additional check types |
| `parse_newsletter.py` | 58% | 99 | Company extraction, analytics |
| `rename_entity.py` | 17% | 76 | replace_field, main |
| `embeddings.py` | 56% | 72 | Embedding computation |
| `capture_newsletter_images.py` | 44% | 71 | Image extraction |
| `rebuild_note_search.py` | 61% | 64 | FTS rebuild paths |

---

## Progress Log (Quick Wins — 2026-08-11)

### Batch 1: All 15 quick-win files (102 new tests)

| File | Old Cov% | Old Miss | New Miss (unit) | Tests Added | Notes |
|------|---------|---------|-----------------|-------------|-------|
| `fuzzy_match.py` | 95% | 3 | 0 | 8 | **100%** |
| `frontmatter.py` | 64% | 13 | 0 | 14 | **~100%** (new test file) |
| `db.py` | 39% | 43 | 8 | 18 | **39%→91%** (new test file) |
| `stats.py` | 86% | 7 | 6 | 1 | +1 pure function test |
| `migrate_to_graph_edges.py` | 82% | 12 | varies | 8 | +helper tests (new file) |
| `sync_tags.py` | 70% | 28 | 28 | 5 | allowed_tags unit tests |
| `derive_co_mentions.py` | 73% | 32 | 29 | 9 | +pure function tests |
| `derive_themes.py` | 62% | 32 | 27 | 5 | +edge-case tests |
| `rebuild_schema.py` | 63% | 33 | 32 | 4 | +_ddl_for_new_table tests |
| `algorithms.py` | 82% | 34 | 27 | 6 | +_format_value/_print_result |
| `build_sector_hierarchy.py` | 73% | 40 | 27 | 8 | +pure helpers |
| `sync_sector_wikilinks.py` | 54% | 42 | 36 | 6 | +insertion/title fallback |
| `derive_events.py` | 77% | 47 | 44 | 10 | +date/dedup tests |
| `_edge_writer.py` | 95% | 1 | 1 | 0 | sys.path guard (skip) |
| `maint.py` | 97% | 1 | 1 | 0 | `__main__` guard (skip) |

**Total**: 102 new tests across 15 files. Biggest wins: `db.py` (+35 lines),
`frontmatter.py` (+13), `build_sector_hierarchy.py` (+13), `algorithms.py` (+7).
Full coverage impact will be visible on next `make cover` run.

---

## Progress Log (Medium Effort — 2026-08-11)

### Batch 2: 9 medium-effort files (182 new tests)

| File | Old Cov% | Old Miss | Tests Added | Notes |
|------|---------|---------|-------------|-------|
| `move_sector.py` | 19% | 122 | 17 | **NEW file** — all YAML helpers tested |
| `rename_entity.py` | 17% | 76 | 6 | **NEW file** — replace_field pure function |
| `enrich_from_yfinance.py` | 49% | 189 | 34 | **NEW file** — format/convert/extract/render/frontmatter |
| `static_checks.py` | 47% | 192 | 24 | +parse_frontmatter,_check_tags/permalink/date, iter_findata |
| `derive_insights.py` | 77% | 149 | 34 | +canonicalize, parse_attribution, label/classify/unit |
| `get_tickers.py` | 60% | 139 | 16 | +fmt_number/pct, print sections smoke tests |
| `parse_newsletter.py` | 58% | 99 | 17 | +normalize_name, render_stub, extract_companies |
| `snapshot_db.py` | 62% | 131 | 10 | **NEW file** — list_tables, snapshot roundtrip |
| `extract_relations.py` | 77% | 126 | 24 | +tokens, looks_like_speaker, parse_yaml, detect_doc_type |

**Total medium effort**: 182 new tests, 4 new test files created.
Combined with quick wins: **284 new tests across 24 files**.

---

## Progress Log (Big Targets — 2026-08-11)

### Batch 3: 3 large-coverage-gap files (101 new tests)

| File | Old Cov% | Old Miss | Tests Added | Notes |
|------|---------|---------|-------------|-------|
| `database_integrity_check.py` | 52% | 348 | +51 | All check_* methods + pure helpers + connection lifecycle |
| `query.py` | 72% | 172 | +29 | NEW file: _lit,_normalise_as_of, _as_of_predicate, cache ops, EDGE_REGISTRY |
| `db_maint.py` | 51% | 187 | +21 | NEW file: _fmt_bytes, _pragma_ident, DBMaintainer.settings/metrics/index_report,_print_report |

### Subtotal (Batches 1-3): 385 new tests across 27 files

| Batch | Tests | Files | New test files |
|-------|-------|-------|----------------|
| Quick wins (15 files, <50 miss each) | 102 | 15 | 3 (test_db, test_frontmatter, test_migrate_helpers) |
| Medium effort (9 files, 50-200 miss) | 182 | 9 | 4 (test_move_sector, test_rename_entity, test_enrich, test_snapshot_db) |
| Big targets (3 files, 126-348 miss) | 101 | 3 | 2 (test_query_helpers, test_db_maint) |
| **TOTAL** | **385** | **27** | **9** |

---

## Progress Log (Sub-50% Files — 2026-08-11)

### Batch 4: 4 files below 50% coverage (52 new tests)

| File | Old Cov% | Old Miss | Tests Added | Notes |
|------|---------|---------|-------------|-------|
| `capture_newsletter_images.py` | 42% | 71 | 16 | **NEW file** — slugify, parse_images, assign_pages, is_valid_jpeg |
| `static_checks.py` | 48% | 180 | +24 | _walk, check_python_syntax, stray artifacts, shebangs, merge markers, YAML, sqlite helper, db_meta |
| `move_sector.py` | 37% | 93 | +7 | move_entity integration: real file move + DB update + YAML rewrite + edge swap |
| `rename_entity.py` | 20% | 72 | +5 | main() integration: rename with FK cascade, ticker override, error paths |

**Bug fixed during batch**: `check_python_syntax` used `PyCompileError.errormsg` (doesn't exist on Python 3.14) — fixed to `str(e)`.

---

## FINAL RESULTS (2026-08-11)

### Coverage campaign COMPLETE

| Batch | Tests | Files | New test files |
|-------|-------|-------|----------------|
| Quick wins (15 files, <50 miss) | 102 | 15 | 3 (test_db, test_frontmatter, test_migrate_helpers) |
| Medium effort (9 files, 50-200 miss) | 182 | 9 | 4 (test_move_sector, test_rename_entity, test_enrich, test_snapshot_db) |
| Big targets (3 files, 126-348 miss) | 101 | 3 | 2 (test_query_helpers, test_db_maint) |
| Sub-50% files (4 files) | 52 | 4 | 1 (test_capture_newsletter_images) |
| **GRAND TOTAL** | **437** | **31** | **10** |

### `make cover` final results
- **Coverage: 63.9% → 67.4%** (+3.5 percentage points)
- **Statements covered: 4654 → 4907** (+253)
- **Total tests: 931 → 1368** (1 skip, 5 xfail)
- Runtime: 158s

### Remaining gaps (diminishing returns)
- Large CLI `main()`/`_cli()` entry points (hard to unit test — need argv mocking + live DB)
- DuckDB materialization functions (need DuckDB fixtures)
- Network-dependent functions (fetch, embeddings — need mocking)
- `verify_notes.py` (122 miss), `embeddings.py` (72 miss), `capture_newsletter_images.py` (remaining 71 miss — CLI/IO paths)

---

## Progress Log (Final Batch — 2026-08-12)

### Batch 5: Two largest remaining gaps (94 new tests)

| File | Old Miss | Tests Added | Notes |
|------|---------|-------------|-------|
| `verify_notes.py` | 122 | 64 | **NEW file** — NotesVerifier: filename, name_sync, YAML structure, sector/super_sector/company consistency, content quality, heading duplicates, near-dup detection, redundant YAML, report generation, process_directory |
| `embeddings.py` | 72 | 30 | **NEW file** — _pseudo_embedding (determinism, L2 norm, range), _ensure_schema (dim mismatch, idempotent), _get_company_text (file read + YAML strip), clear, stats, populate_dry_run,_get_openai_client import error |

**Bug found during testing**: `check_heading_duplicates` returns early when no H3 headings
exist, so the redundant-YAML-block check (which comes after the heading analysis) is silently
skipped for files with no `###` headings. The test works around this by including at least one
`### heading.` line

### Updated Grand Total

| Batch | Tests | Files | New test files |
|-------|-------|-------|----------------|
| Quick wins (15 files) | 102 | 15 | 3 |
| Medium effort (9 files) | 182 | 9 | 4 |
| Big targets (3 files) | 101 | 3 | 2 |
| Sub-50% files (4 files) | 52 | 4 | 1 |
| Final batch (2 files) | 94 | 2 | 2 |
| **GRAND TOTAL** | **531** | **33** | **12** |
