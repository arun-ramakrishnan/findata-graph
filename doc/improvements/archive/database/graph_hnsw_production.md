---
title: "Graph-lane HNSW productionization (v_note_embeddings + v_embeddings)"
status: executed
filed: '2026-11-22'
executed: '2026-11-22'
completed_md: "555"
area: helpers/graph/query.py, helpers/graph/embeddings.py, schema/duckdb/graph.sql, memory/graph.duckdb
---

# Proposal: Graph-lane HNSW productionization

**Date:** 2026-11-22 · **Status:** PROPOSED
**Area:** helpers/graph/query.py (KNN entry points, materialise site) +
helpers/graph/embeddings.py + helpers/misc/quack_server_child.py (served-path vss) +
schema/duckdb/graph.sql (already FLOAT[512]) + memory/graph.duckdb.

**Follows:** doc/improvements/archive/database/graph_hnsw_next.md (deferred
status); N1 VARIANT re-evaluation (completed.md #554) is unrelated but
preceded this probe.

## 1. Motivation

The graph lane brute-forces its vectors (v_embeddings 1,209 rows,
v_note_embeddings 19,356 rows). Earlier HNSW deferral (pending.md, 2026-09
through 2026-10-27) rested on three blockers: COSINE opclass unsupported,
planner not routing, and FLOAT[] type migration. A short probe on DuckDB
2.0.1 (2026-11-22) shows all three are now resolved and HNSW delivers
~11× speedup at 100% recall@10.

## 2. Probe evidence (2026-11-22, DuckDB 2.0.1)

On a copy of memory/graph.duckdb (19,356 × FLOAT[512]):

| metric | value |
|---|---|
| index build time | 3.0 s |
| db size delta | 116 → 126 MB (+8.6%) |
| planner routing | `Hnsw Index Scan` confirmed via EXPLAIN for `ORDER BY array_distance(emb, ?) ASC LIMIT k` |
| brute (cosine) | 41.65 ms |
| HNSW (l2) | 3.71 ms (~11×) |
| recall@10 | 10/10 |
| index persists across reopen | yes |
| vectors unit-norm (self-cosine = 1.0) | yes → l2 ordering ≡ cosine, so the l2-routed index is exact-equivalent |

Prior +75% size figure was at a different scale/older build; production
scale (19k rows) shows +8.6%.

## 3. Why l2 works (cosine equivalence)

All embedding vectors are unit-norm (self-cosine = 1.0). For unit-norm
vectors, `argmin(array_distance(a,b))` ≡ `argmax(array_cosine_similarity(a,b))`.
DuckDB 2.0 only routes `array_distance` (l2) through `HNSW_INDEX_SCAN`;
`array_cosine_similarity` uses brute force (Seq Scan + Top N). So the
production rewrite swaps the ORDER BY from `array_cosine_similarity DESC`
to `array_distance ASC` with identical results.

## 4. Slices

### S1 — Index at materialise site
- helpers/graph/query.py ~:1735: add
  `CREATE INDEX … USING HNSW (emb)` on v_note_embeddings and
  `CREATE INDEX … USING HNSW (embedding)` on v_embeddings (both FLOAT[512]).
- Set `hnsw_enable_experimental_persistence=true` before build.

### S2 — Quack-server coordination
- The store is served 24/7 by helpers/misc/quack_server_child.py.
- (a) materialise/index window: stop server (stop-file), build index,
  restart.
- (b) server session must INSTALL/LOAD vss + SET
  hnsw_enable_experimental_persistence=true (today only quack is loaded;
  served bounded-KNN would otherwise brute-force).

### S3 — Re-route bounded top-k
- helpers/graph/query.py:3691 (company_neighbors_bundle) and :4260
  (similar_notes): `ORDER BY array_distance(emb, CAST(? AS FLOAT[512])) ASC LIMIT k`.
- Keep k=None exact scan (whole-corpus; brute stays correct there).

### S4 — Cleanup
- Delete stopgap comment at helpers/graph/query.py:415.
- Delete the "HNSW deferred" pending.md entry once bank re-baseline passes.

### S5 — Validation
- Recall@10 vs exact scan ≥ .99 on v_note_embeddings (probe already 10/10).
- Bank re-baseline (graph lane) before retiring brute-force as the
  shipped path; brute remains fallback if index missing.

## 5. Risks / rollback
- Index build is 3 s; safe to rebuild. Rollback = DROP INDEX; queries
  fall back to brute force automatically (planner uses Seq Scan).
- Size +8.6% (~10 MB) — acceptable.
- Experimental persistence: index survives reopen (verified in probe).

## 6. Out of scope
- Other embedding lanes (doc/script/memory/git) — no vector query path
  exists there yet; tracked separately in graph_hnsw_next.md S2.
- HNSW on convo_search / note_search (already shipped there).
EOF
echo written
