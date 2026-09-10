---
title: "Embedding storage — five-surface f32 BLOB migration + cache wipe"
status: executed
filed: "2026-09-10"
executed: "2026-09-10"
completed_md: "223"
area: "helpers/core (vec_codec, embed_cache, vec_search) + rebuilders + query.py materialisation + maintenance CLI"
---

# Embedding storage — five-surface f32 BLOB migration + cache wipe

**Date:** 2026-09-10 · **Status:** EXECUTED 2026-09-10 ·
completed.md entry 223 ·
**Area:** `helpers/core/` (new `vec_codec.py`, `embed_cache.py`,
`vec_search.py`), `helpers/maintenance/` (rebuild_note_search /
rebuild_doc_search / rebuild_script_search + new migration CLI),
`helpers/graph/` (`embeddings.py`, `query.py` materialisation)

## 1. Motivation

Measured in the 2026-09-10 vector-storage audit: every embedding in the
stack is stored as **JSON TEXT** (~8.3 KB per 384-d vector) while every
consumer computes in f32 binary (1.5 KB). Five surfaces hold ~530 MB of
text for ~96 MB of actual vector data. Additionally the duckdb
consolidation review asked how to shrink embed_store.db; the answer is
this arc, not a trim.

## 2. Evidence (measured 2026-09-10, this box)

| Surface | Rows | TEXT payload | f32 BLOB equivalent |
|---|---|---|---|
| embed_store.db `embed_cache.embedding` | 43,259 | 367.3 MB | (wiped, re-warms as BLOB) |
| research.db `note_search.embedding` | 16,479 | 140.4 MB | 25.3 MB |
| research.db `company_embeddings.embedding` | 1,165 | 9.9 MB | 1.8 MB |
| doc_search.db `doc_search.embedding` | 1,102 | 9.4 MB | 1.7 MB |
| script_search.db `script_search.embedding` | 348 | 3.0 MB | 0.5 MB |

Precision (60-vec, 59-query sample): f32 pack vs stored f64 — max
|dcos| 8.4e-9, 0/59 top-5 neighbor flips. The stored JSON carries f64
precision no consumer uses; every retrieval surface (sqlite-vec chunks
at 1,622.6 B/vector, DuckDB `FLOAT[]`) already quantizes to f32.

Rejected riders, measured on this Skylake i5-6500 (AVX2+F16C, no
AVX512-FP16): f16 storage — 0/59 flips but **3.6x CPU penalty** anywhere
it touches compute (numpy f16 ufuncs unpack per-op; 25.8 ms vs 7.1 ms
full scan) for only ~12 MB more savings. int8 — 18/59 top-5 flips
naive; needs calibrated quant + rescore pipeline. Both parked behind
revisit triggers (§6).

## 3. Design

### S1 — `helpers/core/vec_codec.py` (single choke point)

`pack_f32(vec) -> bytes` (array('f').tobytes()), `unpack_f32(blob) ->
list[float]`, `load_vec(value)` tolerant reader (BLOB -> unpack, TEXT ->
json.loads) so data and code can land in either order.

### S2 — BLOB-native writers

rebuild_note_search / rebuild_doc_search / rebuild_script_search /
embeddings.py (company) / embed_cache.py write `pack_f32(vec)` instead
of `json.dumps(vec)`. Tolerant readers in vec_search.py (2 sites),
rebuild_* warm paths, `query.py:_note_emb_dims`.

### S3 — DuckDB materialisation switch (the wrinkle)

`v_note_embeddings` and `v_embeddings` are today built INSIDE DuckDB SQL
(`SET sqlite_all_varchar=true; CAST(embedding AS FLOAT[dims]) FROM
fin.note_search`), i.e. the JSON is parsed by DuckDB — a BLOB column
breaks that. Switch both to a Python-side read: sqlite3 fetch of
(path, doc_type, title, blob) -> `np.frombuffer` zero-copy -> pyarrow
table -> `con.register()` -> CTAS. Dims come from `len(blob) // 4`
(no more `json_array_length` probe). Expected FASTER than today: the
CTAS parses 140 MB of JSON per rebuild; the arrow path moves 25 MB of
binary. TEXT fallback retained via `load_vec` for un-migrated DBs.

### S4 — one-shot migration CLI (new `helpers/maintenance/migrate_embedding_blob.py`)

`--check` / `--apply`. Per surface: single transaction, registered SQL
function `vec_pack` (deterministic), new-table swap (plain tables) or
in-place UPDATE (FTS5 virtual tables: note_search, doc_search,
script_search — UNINDEXED columns hold BLOBs fine). `embed_cache`:
**WIPE, not migrate** (user decision 2026-09-10: regeneration beats
trim — the embedder is local bge-small, re-warm is compute-only, full
re-embed is a runbook'd operation). Idempotent: `--check` after
`--apply` reports clean.

### S5 — VACUUM + measurement

VACUUM all four DBs post-migration; report before/after file sizes and
row counts. Expected landing: embed_store 411 MB -> ~40 MB; research.db
280 MB -> ~150 MB; doc/script ~12 MB -> ~4 MB. DuckDB caches stay valid
(bit-identical vectors: same round-to-nearest on the same f64 values).

### S6 — tests

vec_codec unit tests; tolerant-reader tests; migration round-trip
(pack preserves vectors bit-exactly through the DuckDB FLOAT[] path);
dims-from-blob probe; writer emits BLOB (`typeof() = 'blob'`).

## 4. Acceptance criteria

- All five surfaces BLOB (or wiped) after `--apply`; `--check` clean.
- Hybrid search unchanged: doc_query/script_query results identical on
  fixed queries; note KNN identical top-5 on the 60-vec sample.
- `make qa` 9/9, `make perf` 22/22 (graph_rebuild within budget — the
  S3 arrow switch must not regress it; measure).
- Measured before/after table appended at execution.

## 5. Risks

- FTS5 UPDATE rewrites rows (16,479 on note_search) — seconds, single
  writer lock, backup first (existing zst pool).
- DuckDB build path changes shape (S3) — pinned by existing
  rebuild/materialisation tests; TEXT fallback keeps un-migrated DBs
  working.
- Cache wipe makes the first post-migration embed pass cold (local
  model, minutes) — acceptable per user decision.

## 6. Revisit triggers

- f16 storage: revisit when the box gains AVX512-FP16 (Zen 4 / Sapphire
  Rapids+) — then f16 math is native (often faster than f32) and the
  extra ~12 MB is free.
- int8 + rescore: revisit at O(100k+) vectors (vault_scaling §6
  trigger) with calibrated per-dim quantization and eval certification
  via embed_eval_questions.json.
## Execution results (2026-09-10)

Measured before/after (file sizes):

| DB | Before | After | Delta |
|---|---|---|---|
| research.db | 280.6 MB | 125.4 MB | -155 MB |
| embed_store.db | 410.6 MB | 29.1 MB | -382 MB |
| doc_search.db | 18.8 MB | 8.3 MB | -10 MB |
| script_search.db | 4.7 MB | 1.8 MB | -3 MB |
| deleted outright | embed_matrix.f32/json 26.5 MB, corpus.db 55.2 MB | — | -82 MB |
| **Total** | **~796 MB** | **~165 MB** | **~-630 MB** |

Cache economics (measured): wiping a content-addressed store forces one
full re-embed pass across the three rebuilds at the serial per-text rate
(~5/s observed on doc_search: miss #640+ while this entry was written).
note_search's ~16.5k sections dominate — expect up to ~1 h wall for the
first post-migration `make search-fresh`, after which every content hash
is warm again and no-change cycles stay warm. The compute is local
(bge-small), so this is wall time only, no API cost.

Correctness: DuckDB rebuild over the arrow path green;
`v_note_embeddings` 16,479/16,479 rows with sampled overlap
bit-identical to the sqlite BLOBs; doc_query hybrid alive; cache
re-warm through production wiring stores `blob` (verified).
Reader codec found three extra JSON-only seams during execution
(`vss_index._decoded_vss_table` dims probe, `get_tickers`, and
embeddings.py's own dims probe) — all converted to the tolerant
`load_vec`, plus housekeeping: 5 unused imports dropped, one test-side
writer assertion converted to the codec. The first-caching-attempt
lesson from graph_db_optimization held: no module-level memo state was
added; the warm memo already existed (`_TITLE_MEMO` analog pattern —
here `_warm` is per-run via the migration CLI, not persistent).

## 7. Non-goals

- No model changes, no re-embedding of live tables (in-place lossless
  pack), no HNSW/ANN, no quantization, no research.db schema changes
  beyond the embedding column codec.
