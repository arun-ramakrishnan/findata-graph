---
title: "note-KNN distance ranking — cosine to l2 swap after the HNSW trial"
status: executed
filed: "2026-09-22"
executed: "2026-09-22"
completed_md: "266"
area: "helpers/graph/query.py"
---

# note-KNN distance ranking — cosine to l2 swap after the HNSW trial

## 1. TL;DR

Every vector KNN consumer in `query.py` ranks with
`array_cosine_similarity`, which costs ~40 ms per unfiltered query at the
16,560-row `v_note_embeddings` scale. All stored vectors are unit-norm
(the embedder L2-normalises at write; verified `|v|^2 = 1.0000` on both
`v_embeddings` and `v_note_embeddings`), so ranking by `array_distance`
is the SAME exact ordering — at ~27-34 ms brute (~1.2-1.3x; corrected
2026-09-22, see §3a: the originally-claimed 3.7 ms / ~11x was an
index-routed measurement, not brute). The l2 form is also the shape the
HNSW planner auto-routes (3.3 ms measured with an index present), so the
swap is index-ready should a future vss build fix recall. This proposal
swaps the ranking expression at the five note/entity KNN sites, keeps
returned scores as cosine (converted from distance), fixes a latent
multi-row reference crash the section-granularity corpus exposed, makes
note-level KNN path-level (§3a), and records the full-scale DuckDB vss
HNSW trial that closed the index path this swap was originally parked
behind.

## 2. Background — why cosine, and why now

- The 2026-08-09 vss adoption (duckdb_improvs.txt N5) verified brute
  cosine at ~3 ms @ 1k and parked the HNSW family behind a quarterly
  extension re-check; brute cosine then became the shipped ranking.
- `local_embedder._normalize` L2-normalises every vector at write
  ("unit vectors so cosine == dot in every consumer"). Re-verified live
  2026-09-22: `|v|^2` range 1.0000..1.0000 on both tables. For unit
  vectors `l2^2 = 2 - 2*cos`, so ASC-distance ordering ≡ DESC-cosine
  ordering, and `cos > 0` ≡ `d < 2` — a drop-in filter translation.
- The cosine scalar normalises per row per query (sqrt/div over 384
  floats × 16.5k rows); the distance scalar is pure subtract+multiply.
  That per-row cost — not missing indexes — is the 40 ms.

## 3. HNSW full-scale trial (2026-09-22) — index path declined

Capability re-verification (vss binary unchanged since 2026-08-09,
DuckDB 1.5.5): the old "empty-signature binder errors" were
scalar-style mis-calls — `vss_match` is a table macro
`(table, col, query, k, metric)`, `pragma_hnsw_index_info()` a table
function, `hnsw_index_scan` internal-by-design. `CREATE INDEX … USING
HNSW (emb)` (l2sq) works on-disk with
`hnsw_enable_experimental_persistence=true`, survives reopen, and the
planner routes `ORDER BY array_distance … LIMIT k` through
`HNSW_INDEX_SCAN` (the 2026-08 "DuckDB does not auto-use" claim is
falsified). `USING HNSW (emb COSINE)` still fails (`opclass not
supported` — the original embed_store_consolidation blocker,
unchanged).

Real corpus (v_note_embeddings 16,560×384, 30 real query vectors, k=10):

| path | latency |
|---|---|
| brute cosine (production shape) | 40.18 ms |
| brute `array_distance` | 3.67 ms |
| HNSW planner scan (l2sq) | 3.92 ms |
| `vss_match` macro | 117.51 ms |
| brute cosine + `doc_type` filter | 12.86 ms |

Index cost: build 5.4 s, db 37→65 MB (+75%). Recall exact-vs-ANN
overlap@10: ~57% (172/300), flat across `hnsw_ef_search` 50→400.

Synthetic uniform ramp (384-dim, k=10, 20 queries):

| N | insert | HNSW build | db | brute | HNSW scan | recall |
|---|---|---|---|---|---|---|
| 50k | 4 s | 61 s | 148 MB | 9.7 ms | 9.7 ms | 41% (82/200) |
| 100k | 8 s | 144 s | 409 MB | 12.3 ms | 9.7 ms | 35% (69/200) |
| 200k | 20 s | 311 s | 710 MB | 10.7 ms | 11.1 ms | (trend set) |

Verdict: the HNSW path stays declined — scan recall is broken on this
build (worsening with N, insensitive to `ef_search`), no meaningful
speed win anywhere in 16.5k–200k, and build/size costs are heavy.
Revisit when a vss build fixes the COSINE opclass or scan recall, or
vectors approach ~10^6 (full evidence also in pending.md N5-5).

## 3a. Execution correction + section-granularity discovery (2026-09-22)

- **The 3.67 ms "brute array_distance" row above was index-routed, not
  brute**: the trial benched AFTER building the HNSW index on the same
  table, and the planner routed `ORDER BY array_distance` through
  `HNSW_INDEX_SCAN` (3.67 ≈ the 3.92 ms index-scan row). Re-verified
  same day: with no index present the identical query runs 26-27 ms
  (live db and a fresh copy), and building the index drops it to
  3.26 ms. True brute-vs-brute is ~1.2-1.3x (27.6 vs 35.5 ms), not
  ~11x. The swap survives on exactness (+ the modest win + index
  readiness), not on the 11x.
- **`v_note_embeddings` rows are per-SECTION** (the `fin.note_search`
  granularity — 9,282 company rows over 1,181 paths, ~7.9 sections per
  note; chatter/edition doc types accumulate per edition). The flat
  form's scalar reference subquery `WHERE file_path = ?` therefore
  returns multiple rows and CRASHES on the live db — a latent bug in
  the shipped cosine code (verified: old SQL fails identically), never
  hit because the wrappers were built before sectioning.
- **Path-level semantics** (all four `v_note_embeddings` wrappers): the
  reference is the note's renormalized MEAN section vector
  (`_note_ref_vector`; single-section paths are bit-identical to the
  flat form), and each candidate note is scored by its best-matching
  section (`GROUP BY file_path, title` + `MIN(distance)`) — one row per
  note, same single distance pass. Thresholds filter the CONVERTED
  cosine score with the raw bound (`sim > 0` / `sim > min_sim` /
  `sim >= min_sim`), preserving the shipped threshold semantics exactly.
  `near_duplicate_notes` collapses to per-path mean vectors in a temp
  table first — the row-level self-join is ~43M pairs at section
  granularity vs ~0.7M at path level (stays in its ~1s... measured
  3.6s end-to-end incl. Python mean pass — maintenance budget).
- Deterministic tie-break `ORDER BY dist, file_path` added (the old
  `ORDER BY sim DESC` tie order was arbitrary); tests that pinned
  boundary/tie behaviour now drop sub-1e-6 conversion noise per the
  tolerances doctrine.

## 4. Slices

- S1 `similar_notes` (query.py ~3099): rank/filter by
  `array_distance` (`d < 2` replacing `sim > 0`), convert the returned
  score back to cosine via `1 - d*d/2` so the public tuple shape and
  score semantics are unchanged; note-level ulp drift vs
  `array_cosine_similarity` is acceptable, tests use tolerances.
- S2 `notes_like_entity`, `notes_like_text`, `edition_companies`,
  `near_duplicate_notes` (query.py ~3146/3199/3260/3303): same swap;
  thresholds filter the converted cosine score with the raw bound
  (`t` stays `t` — the d < sqrt(2-2t) translation is implicit in
  `1 - d*d/2` and avoids a float32 boundary mismatch; §3a).
- S3 `semantic_neighbours` (query.py ~2972, `/api/graph/semantic/<name>`
  over `v_embeddings`, 1,181 rows): cosine branch only; the `ip` metric
  branch is untouched. 1.2k rows are fast today — this slice is
  consistency, not speed.
- S4 tests: extend `tests/test_note_embeddings.py` (and
  `test_api_graph_unit.py` where the endpoints are pinned) with a
  ranking-equivalence case (cosine vs distance ordering identical on a
  fixture) and a latency pin if the perf harness has a natural slot.

## 5. Acceptance criteria

1. Unfiltered `similar_notes` on the live db: ranking overlap vs the
   reference implementation 10/10 on sampled queries (met: 30/30),
   returned scores within 1e-6 (met: 9.7e-08), latency ~34 ms p50 vs
   ~40 ms cosine (met; brute-brute ~1.2x — sub-4 ms requires the
   declined HNSW index, see §3a).
2. Targeted tests green: `test_note_embeddings.py`,
   `test_api_graph_unit.py`, `test_fuzz_query_predicates.py`.
3. `make static-checks` + `make md-lint` clean.

## 6. Non-goals

- No ANN/HNSW wiring (declined above), no sqlite `vec0` changes — the
  `/api/search` hybrid fusion stays SQLite-side (sql_capability_unlocks
  scope note).
- No embedding regeneration, no schema/storage changes, no API shape
  change (scores stay cosine-valued).
- No `semantic_neighbours` `ip`-metric change.

## 7. References

- pending.md N5-5 (HNSW re-evaluation, trial evidence) and N5-6
  (personalized pagerank, still deferred — re-probed 2026-09-22).
- `helpers/core/local_embedder.py::_normalize` — the unit-norm
  invariant this swap relies on.
- doc/improvements/archive/database/embed_store_consolidation.md #7 —
  the original COSINE-opclass blocker.
- duckdb_improvs.txt N5 — the 2026-08-09 adoption + deferral record.
