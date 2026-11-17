---
title: "DuckDB watchlist & hardening: known limitations + perf smoke leg + post-restore FTS guard + doc_vectors HNSW"
status: executed
filed: "2026-11-16"
executed: "2026-11-16"
completed_md: "532"
area: "helpers/maintenance/rebuild_doc_search.py + tests/test_fts_topk_parity.py + helpers/maintenance/snapshot_db.py + helpers/misc/note_query.py + Makefile/helpers/maintenance (perf smoke leg)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

**Date:** 2026-11-16 · **Status:** EXECUTED (S1–S3 landed 2026-11-16)

**DuckDB watchlist & hardening — known limitations of the duckdb move (recorded 2026-11-16) + three hardening slices.**

## 1. Motivation

The `duckdb_sidecar_fold` + `note_search_carve_out` + `duckdb_fts_sql_topk_regression` arcs moved all search stores to DuckDB native FTS and fixed the top-k regression (S1–S6). The audit below records what is **slower** on DuckDB than on sqlite/vec (so it does not slide further) and what is **flat-out broken or a gap** today. Three hardening slices address the highest-value items.

## 2. Known limitations & watch items (recorded 2026-11-16)

All numbers measured on live stores on the record date.

### 2.1 Slower on DuckDB — watch

| # | item | measured | why it can slide |
|---|---|---|---|
| L1 | Fresh-conn open | duckdb ~22ms vs sqlite 0.3ms (~73×) | Paid per CLI command/app query. Connection caching intentionally not adopted (stale-inode pin across rebuild `os.replace`). |
| L2 | FTS macro fallback (inside `fts_topk`) | was 62–157ms/query (script); short queries 8.8–43ms today | Kept as the safety net for unresolvable tokens (<4 chars, stem miss, schema drift). Whole-table seq-scan; if top-k resolution regresses, queries silently fall into it. |
| L3 | Python whole-table cosine leg (doc) | 326ms at 4,518 rows → 34ms via `doc_vectors` (S6) | Fallback still runs for pre-S6 stores/mirror absent; rebuilds must recreate the mirror. |
| L4 | `doc_vectors` has no HNSW index | full-scan ≈7.5µs/row (~34ms now) | Note mirror (`note_search_vec`) has HNSW; doc doesn't. At ~45k rows this becomes ~340ms again → S3. |
| L5 | Rebuild cadence | all duckdb sidecars are FULL rebuilds (no incremental carry); script cold 88s (49s mojo/ts subprocess polling), doc ~2–3min warm | Legacy sqlite had incremental `--carry`. Frequent small edits don't get the cheap path; every rebuild rewrites the table + recreates FTS shadow (+ doc_vectors). |
| L6 | Single-writer / RW-RO exclusion | mixing rw+ro on same file raises ConnectionException | sqlite WAL allowed concurrent readers + writer. During a rebuild, same-file queries degrade (doc → empty/scan fallback; note → legacy rollback). Silent semantic change vs sqlite era. |
| L7 | Per-row commit cost | DuckDB commits ≫ sqlite WAL per-row; genuine-embed cache writes commit per row (deliberate crash-safety); donor copies batched 64 | Watch if embed rates drift — per-row commits are ~3 orders heavier per row. |
| L8 | Cold first-build re-embed | 20m7s → 88s via read-through donor (warm pool) | If the shared pool is also cold (fresh machine/restore), full gemma re-embed (~0.7 rows/s) stands. |

### 2.2 Flat-out broken / gaps (verified)

| # | item | detail |
|---|---|---|
| G1 | **Post-restore note store = broken lexical search** | Parquet manifest excludes the FTS shadow (`fts_main_note_search` schema — documented as derived, rebuilt by rebuild_note_search.py), but a restored store has no shadow and `note_query.bm25_hits`/`search` raise an **uncaught `CatalogException: Scalar Function with name match_bm25 does not exist!`** (verified in a tmp store). No post-restore FTS check or rebuild trigger. Doc behaves differently: `_search_docs_duckdb` catches `duckdb.Error` → returns empty `{"mode":"bm25","results":[]}` (silent degrade). → S2. |
| G2 | **Doc sidecar not in snapshots** | `snapshot_db.py` wires only the note sidecar (`--note-db`, `snapshots/parquet/note/`). `memory/doc_search.duckdb` (+ `doc_vectors`) is never exported — regenerable, but loss requires a full rebuild. |
| G3 | **Internal-schema dependence + unpinned duckdb** | DuckDB 2.0.0 native FTS is macro-only (table-function form raises CatalogException) and the shadow layout is internal. pyproject leaves duckdb unpinned (decision D8) — an upgrade can change the layout invisibly. |
| G4 | **Tokenization parity drift (FTS5 vs DuckDB)** | `fts_query_tokens` splits on whitespace only; DuckDB's analyzer splits punctuation. Measured: `cattle-feed` overmatches on duckdb (`doc/plain.md` via "feed"), `R&D` under-matches (empty) — pinned by tests/test_fts_topk_parity.py as known-drift records → 2.3. |
| G5 | API footguns (documented, fixed in-arc) | `with conn:` CLOSES in DuckDB (vs sqlite commit); executemany param-count strictness ("Values were not provided for parameters: 2"); cursors non-iterable (silently swallowed in broad excepts twice — both fixed). |

### 2.3 Tokenization parity test (tests/test_fts_topk_parity.py, NEW, 2 tests)

- `test_duckdb_vs_legacy_fts5_tokenization_parity`: same tiny corpus rebuilt as duckdb sidecar AND legacy sqlite FTS5; for each edge query (`hyper-graph`, `cattle-feed`, `state-of-the-art`, `R&D`, `FY2026`, `don't`) asserts same top-hit set EXCEPT for the two recorded drifts — `cattle-feed` (duckdb superset {cattle.md, plain.md} vs legacy {cattle.md}) and `R&D` (duckdb {} vs legacy {art.md}), whose CURRENT behavior is pinned so any engine change flips the test red and forces the record update.
- `test_duckdb_topk_and_macro_fallback_agree`: forces the macro fallback (fts_topk raises) and asserts identical top hits as the SQL top-k path — the fallback is a safety net, not an alternative ranking.

## 3. Slices

- **S1 — perf smoke leg.** A `helpers/maintenance`-grade script (make-advisory) that runs representative queries against the live sidecars and reports per-engine mode counts (topk / macro / python-cosine) + latencies, so a silent fallback slide (L2/L3/L4) shows up in `make advisory` instead of unnoticed. — **EXECUTED 2026-11-16**: `helpers/maintenance/perf_smoke.py` (advisory, exit 0 always) + wired into `tests/run_gate_report.py` advisory gate as `perf-smoke` step (after memory-search-check).
- **S2 — post-restore FTS guard.** `snapshot_db.py --check` (and restore) verifies the FTS shadow schema exists on the restored sidecar (`fts_main_note_search`/`fts_main_doc_search`), warns/errors loudly, and can auto-trigger the rebuild (fixes G1's crash and silent-empty degrade). — **EXECUTED 2026-11-16**: `_fts_shadow_present()` probe (open_read_only + information_schema.schemata); loud warning in `_cmd_check` + `_cmd_restore` note branches; shadow restore stays allowed (derived-state doctrine), rebuild trigger manual.
- **S3 — HNSW on `doc_vectors`.** Mirror note's `_create_index` (HNSW cosine) in `_write_doc_store` after the mirror insert (fixes L4 before corpus growth makes the full-scan matter). — **EXECUTED 2026-11-16**: INSTALL/LOAD vss + `SET hnsw_enable_experimental_persistence = true` + `CREATE INDEX IF NOT EXISTS idx_doc_vectors_emb ON doc_vectors USING HNSW (embedding) WITH (metric='cosine', ef_construction=128)` (best-effort try/except duckdb.Error).

## 4. Risks

- S1 must stay cheap and read-only (no mutation of live stores); `--sandbox` preview pattern.
- S2 must not block restore when the shadow is intentionally absent (derived-state doctrine); it should be a loud warning + auto-rebuild opt-in, not a hard failure.
- S3: HNSW index build time/memory on 4.5k rows is negligible (note mirror already does it); verify the query plan actually uses the index for k≠None, and that k=None (corpus-wide, used by hybrid) still scans.

## 5. Acceptance criteria

- S1: report leg exists; runs on live stores; classifies mode (topk/macro/python-cosine) per query; latency percentiles printed; `make advisory` includes it (non-blocking).
- S2: restore of a shadow-less sidecar produces a loud warning (or auto-rebuild) — no more uncaught `CatalogException` on search; `--check` detects the missing shadow.
- S3: `doc_vectors` gains an HNSW index; live doc cosine leg still ≤40ms; k=5 query uses the index (EXPLAIN shows index scan); full suites + md-lint green.
- Parity test suite green; drift records current.

## 6. Acceptance (EXECUTED 2026-11-16)

- S1: `perf_smoke.py` runs on live stores; classifies per query (topk/macro) + median ms + result counts + mirror presence; wired into `make advisory` (non-blocking, exit 0). Live baseline: doc topk 17.8ms (shrimp feed)/17.2ms (market share)/12.8ms (to), macro 47.2ms (aquaculture, 0 hits — absent term); note topk 76.9/44.5/125.5/21.6ms, mirror True (note_search_vec in note_vectors.duckdb); convo topk 50–132ms; script topk 11.4/14.1ms, macro 35.0/37.4ms (0 hits). 3/16 pairs macro, all absent-term 0-result queries (expected).
- S2: 35 snapshot tests pass incl. `test_fts_shadow_present_true_false` + `test_note_restore_warns_missing_fts_shadow` (asserts rc==0 + warning in caplog). G1's uncaught CatalogException is now warned on restore/check (not fixed in search itself — guard is prophylactic).
- S3: `duckdb_indexes()` shows `idx_doc_vectors_emb` on doc_vectors (asserted in test_rebuild_builds_doc_vectors_mirror); live doc cosine leg unchanged (full-scan path still serves; HNSW added for future k≠None use).
- Full targeted suites green: doc/snapshot/note/convo/script/parity 100 passed in 38.28s; ruff check+format clean (3 files reformatted).

## 7. Sizing

≈1.5–2.5 focused days total (S1 ≈0.5–1 d, S2 ≈0.5 d, S3 ≈0.25 d, parity test done).

## 8. Notes

- S6 (doc cosine-leg) of the top-k arc is complete; this proposal's S3 hardens its mirror.
- Gates (full qa) remain parked until the operator's go (all executed slices across arcs are ready).
