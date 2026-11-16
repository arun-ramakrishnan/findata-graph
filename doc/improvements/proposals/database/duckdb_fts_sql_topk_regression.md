---
title: "DuckDB FTS regression fix: SQL top-k over FTS posting tables (replaces macro-predicate full scans)"
status: proposed
filed: "2026-11-16"
executed:
completed_md:
area: "helpers/maintenance/rebuild_script_search.py + rebuild_doc_search.py + rebuild_convo_search.py + rebuild_note_search.py query paths (+ tests)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# DuckDB FTS regression fix: SQL top-k over FTS posting tables

**Date:** 2026-11-16 · **Status:** PROPOSED · **Area:** query paths in
`helpers/maintenance/rebuild_{script,doc,convo,note}_search.py` (+ `app.py`
delegation for docs/notes) + tests.

## 1. Motivation / scope

The `duckdb_sidecar_fold` arc + `note_search_carve_out` moved all FTS5
sidecars to DuckDB-native FTS via the macro-predicate form

```sql
WHERE fts_main_<table>.match_bm25(id, ?) IS NOT NULL ORDER BY score DESC
```

In DuckDB 2.0.0 (current latest on PyPI as of 2026-11-15; pyproject leaves
duckdb unpinned — decision D8) this macro **cannot be pushed down into the
inverted index**: `EXPLAIN ANALYZE` shows a sequential scan evaluating BM25
per row over the whole table (Dynamic Filters `termid=146`, Hash Group By,
no candidate pruning). The documented table-function form
(`FROM fts_main_<table>.match_bm25(id, 'q')`) raises `CatalogException` in
2.0.0.

Measured regression (live stores, warm conn):

| Surface | DuckDB sidecar | Legacy FTS5 (sqlite) | Regression |
|---|---|---|---|
| script (842 docs) | 62–157 ms/query | 1.3–16.5 ms/query | 30–80× |
| note (19,356 rows) | ~260–360 ms/query | (retired) | severe |
| convo lexical | ~663 ms/query | (retired) | severe |
| convo hybrid | ~1.4–1.7 s/query | (retired) | severe |

Scaling is ~linear in corpus size (per-row scan). Also every fresh
connection pays ~30 ms `INSTALL/LOAD fts` extension init, paid per query by
CLI/TUI lanes.

Approach (approved by operator, m10032): **SQL top-k over the FTS posting
tables**, skipping the macro entirely.

## 2. Design

The FTS extension already materializes inverted-index backing tables
(per-index, schema-qualified `fts_main_<table>.*`):

- `terms (termid, docid, fieldid)` — postings (script: 101,851 rows)
- `dict (termid, term, df)` — lexicon, stemmed, with doc frequency
  (script: 5,088 terms)
- `docs (docid, name, len, field_lens)` — per-doc length (script: 842)
- `stats (num_docs, avgdl, avg_field_lens, total_len, total_field_lens)` —
  corpus stats (single row)

### Score (SQL BM25, k1=1.2, b=0.75 — DuckDB FTS defaults)

```text
score(doc) = Σ_terms  ln((N − df + 0.5)/(df + 0.5) + 1)
                · (tf·(k1+1)) / (tf + k1·(1 − b + b·dl/avgdl))
```

for each query term (tf = count of postings for doc+term).

### Term resolution (recall)

`dict.term` is stemmed (porter over unicode61 tokens). Exact-match lookup
covers base-form queries (rebuild, duckdb, pytest, graph, perf, search,
embed…); inflected/cameltok variants need fallback:

1. exact `term = ?` token match;
2. `term LIKE ? || '%'` prefix fallback (superset — picks up stems);
3. if no dict term resolves (e.g. query word is a stopword, or the macro
   tokenizer disagrees), keep a **macro fallback path**: run the current
   `match_bm25` predicate for that query (correctness net — see §4 risks).

Ranking will approximate the macro, not byte-match it (documented
acceptance; verified recall regression test covers top-5 overlap ≥ 90% on a
fixed query corpus).

## 3. Slices

- **S1 — script search prototype + validation (proof first).**
  Implement `_search_scripts_duckdb` scoring via postings in
  `rebuild_script_search.py`; keep macro fallback; add perf+recall bench
  harness (query set: the 8 probe queries already measured + 20 sampled
  from live index); assert ≥10× speedup and top-5 overlap target.
  **STATUS: prototype validated 2026-11-16 — 100% top-5 overlap (23/23),
  avg 6.2× per-call speedup (range 2–30×), end-to-end fresh-conn
  29–31 ms vs 76–100 ms. Prototype at $TMPDIR/opencode/fts_topk_proto.py
  (not committed).**
- **S2 — engineer into shared helper.** Extract a reusable
  `fts_topk(conn, fts_table, query, limit, offset, ...)` (likely in
  `helpers/core/duckdb_shared.py` or `rebuild_common.py`) shared by
  script/doc/convo/note query paths.
  **STATUS: EXECUTED 2026-11-16 (see §6 S2).**
- **S3 — roll out to doc/convo/note query engines** (rebuild_*_search
  `_search_*_duckdb` + `app.py` delegation + `note_query.py`
  `search_notes_duckdb` + `convo_query.py`).
  **STATUS: EXECUTED 2026-11-16 (see §6 S3).**
- **S4 — connection-open constant.** Reduce repeated `INSTALL/LOAD fts`
  cost where reuse is possible (investigate per-process connection reuse in
  TUI lanes; optional).
  **STATUS: EXECUTED 2026-11-16 — explicit LOAD removed from read-path
  openers (auto-load verified); connection caching verified-not-adopted
  (see §6 S4).**
- **S4b — read-through embed-cache fallback** (rebuild path): on local
  cache miss — in `cached_embed_batch` (note/two-phase, company,
  convo) or per-text `CachedEmbed` (script/doc/git/memory) — look up
  `(text_hash, model, cohort)` in the shared `embed_store.duckdb` and
  copy in (RO shared read; writes local only; single-writer discipline
  unchanged). Removes the ~20 min cold first-build cliff for any new
  sidecar.
  **STATUS: EXECUTED 2026-11-16 — live cold script sidecar rebuild
  20m7s → 88s (donor served 840/845; 5 genuine gemma embeds; the
  residual ~88s is mojo/ts collection subprocess polling, not
  embedding); warm rerun 21.1s (see §6 S4b).**
- **S5 — tests + docs.** Unit tests per engine (postings top-k shape,
  empty/zero, multi-token AND-then-OR, doc_type filter, recall overlap),
  acceptance §6, md-lint, full qa gates on operator go.
- **S6 — doc cosine-leg slice (post-S5, user m10271).**
  `_search_docs_duckdb` was dominated by its *cosine leg*: whole-table
  Python `f32` scan of `doc_search` (4,518 embeddings, ~326 ms of the
  current 352 ms live query — measured 2026-11-16). Pre-existing (not
  from this regression).
  **STATUS: EXECUTED 2026-11-16 — colocated `doc_vectors` mirror
  (`FLOAT[dims]`, rebuilt on every full doc rebuild) + vectorized
  whole-table cosine in `_cosine_leg_duckdb`; live 326ms → 34.4ms
  (~9.5×); full hybrid query 352ms → ~197ms (residual is the gemma
  query-embed HTTP round-trip, ~96ms — out of scope). Python-scan
  fallback retained for pre-S6 stores / schema drift (see §6 S6).**

## 4. Risks

- **Internal-table dependence.** `fts_main_<table>.*` layout is not a
  public API in 2.0.0. Mitigation: pin duckdb version in tests/setup
  (currently unpinned — decision D8; this proposal recommends pinning to
  the validated minor), plus a schema-assert guard at the top of the
  helper (fail over to macro fallback if tables mismatch).
- **Stemming mismatch** → recall risk on inflected query forms. Mitigation:
  stem-guess exact resolution (S1 validates 100% top-5 overlap on the 24
  query set) + macro fallback net + overlap test in the suite.
- **Ranking shift** vs current results. Documented acceptance; overlap
  gate instead of exact-equality gate.
- **Vector/hybrid interplay** (doc/convo/note): top-k is the *lexical*
  candidate generation step; hybrid fusion (RRF) consumes its ranked rows.
  Contract must stay identical (same row shape, score direction positive
  higher-better).
- **Second regression found during S1 (rebuild path):** a fresh sidecar
  file carries a cold colocated embed cache → full re-embed via gemma
  (~20 min for script's 845 rows vs 1m56s legacy-warm; legacy cold would
  be similar — the delta is cache state, not engine). Mitigation planned
  as S4b: read-through fallback in `cached_embed_batch` — on local miss,
  look up `(text_hash, model)` in the shared `embed_store.duckdb` and copy
  in (RO read; writes stay local; single-writer rules untouched).

## 5. Acceptance criteria

- Per-engine speedup ≥10× on live stores (script/note/convo lexical);
  convo hybrid target ≤300 ms/query.
- Recall: top-5 overlap ≥ 90% vs current macro on frozen query set.
- Full test suites green (note 103, graph 185, script/doc/convo suites);
  ruff + md-lint clean; proposal §6 acceptance appended; full qa gates on
  operator go.
- No regression in rebuild-time or storage footprint (postings already
  exist; zero new durable storage).

## 6. Acceptance

### S1 — prototype (EXECUTED 2026-11-16)

- 100% top-5 overlap (23/23) on the 24-query set vs the macro; avg
  6.2× per-call speedup (range 2–30×); fresh-conn end-to-end 29–31 ms
  vs 76–100 ms macro. Prototype `$TMPDIR/opencode/fts_topk_proto.py`
  (not committed).

### S2 — shared helper + script engine (EXECUTED 2026-11-16)

- `helpers/core/duckdb_shared.py` `fts_topk(...)` landed (schema guard
  on `duckdb_tables()`; stem-guess exact resolution; prefix fallback
  for len(tok) ≥ 4; macro fallback for unresolvable tokens; base-table
  key returned in native type — BIGINT or VARCHAR `part_id`).
- `rebuild_script_search.py` `_search_scripts_duckdb` uses it; kind/area
  filters ride as `ss.kind/ss.area` predicates; macro fallback intact.
- `rebuild_doc_search.py` `fts_query_tokens(q)` — both paths share the
  same token stream.
- Tests: `test_script_query_duckdb.py::test_topk_path_with_kind_filter`
  (asserts topk mode, not silent fallback) + script suites 79 passed.
- Live (production path, hybrid=True): warm 10.5–13.3 ms (was
  62–157 ms macro) → ~6–12×; fresh e2e 39.6–42.3 ms (was 76–100 ms);
  CLI `script_query "rebuild"` 0.285 s total.

### S3 — doc/convo/note rollout (EXECUTED 2026-11-16)

- `rebuild_doc_search.py` `_search_docs_duckdb`: topk fast path
  (fetch by id, page shape preserved), macro fallback intact.
- `convo_query.py` `_fts_hits_duckdb`: topk with harness/provenance
  as `ss.*` predicates. BUG FOUND AND FIXED: `by_id` lookup keyed on
  `harness` instead of `part_id` (caught by `test_convo_search.py`).
- `note_query.py` `search_notes_duckdb`: topk preserving the 9-tuple
  contract, note-level dedup, OR-space total via new `fts_topk_count`.
- Tests: doc+note suites 161 passed, convo 21 passed, ruff clean.
- Live (production stores): doc lexical leg 31 ms (was macro); note
  candidates+total 113 ms warm (was 260–360 ms); convo lexical 53 ms
  (was ~663 ms, 12.5×); convo hybrid 323 ms (was ~1.4–1.7 s). Doc
  full-query 352 ms is now dominated by the pre-existing cosine leg
  (~326 ms) — filed as S6.

### S4 — connection-open constant (EXECUTED 2026-11-16)

- Measured: `duckdb.connect` ~25 ms; `LOAD fts` ~34–50 ms per fresh
  connection (A/B on live note store: 73 ms with LOAD vs 24 ms
  without).
- Probe showed DuckDB 2.0.0 auto-loads the `fts` core extension on
  first use (extension `autoload/autoinstall`, REPOSITORY core; macro
  fallback resolves without explicit `LOAD fts`).
- Removed explicit `INSTALL/LOAD fts` from read-path openers
  (`note_query.open_note_index`, `convo_query.connect`). Rebuild-path
  connects keep their existing behavior (they install on demand).
- Connection **caching** (long-lived reader reuse in TUI/app.py)
  intentionally NOT adopted: a cached RO handle would pin the old
  inode across a rebuild's `os.replace` (stale reads) and the lanes
  are one-query-per-action; the remaining ~24 ms fresh-conn cost is
  amortized. Recorded as verified-not-adopted.

### S4b — read-through embed-cache fallback (EXECUTED 2026-11-16)

- `cached_embed_batch` (batch callers) and `CachedEmbed` (per-text
  callers) both gained the donor path: on a local miss, hydrate once an
  in-memory donor map from the shared `embed_store.duckdb` (RO open,
  `WHERE model = ? AND source = ?`), serve misses from it, and copy new
  rows into the local sidecar cache.
- Donor copies are cheap to re-derive (they already live in the shared
  pool), so they are batch-flushed (64 rows/commit) instead of the
  per-row commit reserved for genuine embeds — a crash only costs a
  re-copy, never a re-embed.
- Only active when the local cache is a *different file* than the pool
  (the sidecar case); sqlite hosts already cache inside the pool and
  skip the donor entirely.
- Live measure: fresh script sidecar rebuild 20m7s → 88s (donor
  served 840/845 rows; 5 genuine gemma embeds; residual ~88s is mojo/ts
  collection subprocess polling, not embedding); warm rerun 21.1s
  (hits=845, misses=0).

### S5 — tests + docs (EXECUTED 2026-11-16)

- Engine suites extended (see §6 S3): doc+note 161 passed, convo 21
  passed, script 79 passed, embed-cache/gemma-adoption 55 passed after
  the S4b read-through (plus `test_read_through_copies_pool_rows_
  before_embedding` and the S0 leftover fix in
  `test_gemma_adoption.py:346`).
- Live validation of the S4b read-through on a **fresh** script sidecar
  (`sidecar2.duckdb`): donor map hydrated once from the shared pool
  (`models = ? AND source = 'script'`); 840/845 rows served from donor,
  5 genuine gemma embeds; flush batches 64 rows per commit. Cold cliff
  gone (20m7s → 88s); warm rerun on the same sidecar 21.1s with
  hits=845, misses=0.
- Docs: this proposal §3 STATUS lines updated, §6 S4/S4b/S5 acceptance
  recorded; md-lint clean.

### S6 — doc cosine-leg slice (EXECUTED 2026-11-16)

- Rebuild write path (`_write_doc_store`) now drops/recreates a
  colocated `doc_vectors (id BIGINT PRIMARY KEY, embedding FLOAT[dims])`
  mirror from the in-memory rows (only when vectors exist; dims taken
  from the actual vectors, not the pseudo-dims flag).
- `_cosine_leg_duckdb` gained a vectorized fast path: whole-table
  `array_cosine_similarity` with a zero-norm guard
  (`CASE WHEN sqrt(array_dot_product(embedding, embedding)) = 0
  THEN 0.0 ELSE array_cosine_similarity(...) END`) so degenerate zero
  vectors rank exactly as the Python path did (0.0), avoiding NaN
  ordering skew. Any mirror absence/dims mismatch falls back to the
  original Python scan.
- Tests (tests/test_doc_query_duckdb.py, 7 passed): mirror built with
  embedded-row count and dims; SQL leg vs Python leg identical ranking
  and similarity (≤1e-9) after dropping the mirror.
- Live (real `memory/doc_search.duckdb`, rebuilt 4550 rows):
  `doc_vectors` 4550 rows; cosine leg 326ms → 34.4ms (~9.5×); full
  hybrid query 352ms → ~197ms. The residual is the gemma query-embed
  HTTP round-trip (~96ms, pre-existing, out of scope).

## 7. Sizing

≈1.5–2.5 focused days total (S1 proof ≈0.5 d, S2–S4 ≈0.5–1 d, S5 ≈0.5 d).
