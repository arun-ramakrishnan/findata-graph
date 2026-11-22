---
title: "Search-lane HNSW rollout (doc/script/memory/git) — S0: lanes already carry cosine legs; two decorative HNSW indexes removed"
status: executed
filed: '2026-11-22'
executed: '2026-11-22'
completed_md: "556"
area: helpers/maintenance/rebuild_doc_search.py, helpers/maintenance/rebuild_script_search.py, helpers/maintenance/rebuild_memory_search.py, helpers/maintenance/rebuild_git_search.py, helpers/core/vec_search.py, memory/embed_store.duckdb, helpers/misc
---

# Proposal: Search-lane HNSW rollout (doc/script/memory/git)

**Date:** 2026-11-22 · **Status:** EXECUTED (S0 verdicts + decorative-index
removal; no new indexes were wired — the measured verdict is that none are
needed; evidence: bench_data/search_lane_hnsw_s0/verdicts.md)
**Area:** the four lane rebuild helpers + the helpers/core/vec_search.py
mirror machinery + memory/embed_store.duckdb + the lane query CLIs
(doc_query, script_query, memory_query, git_query).

**Follows:** doc/improvements/archive/database/graph_hnsw_next.md
(§3 S3 — deferred with the parent proposal) and
doc/improvements/archive/database/graph_hnsw_production.md
(completed.md #555, landed 2026-11-22 — the proven in-house shape this
proposal repeats).

## 1. Motivation

The parent proposal's S3 deferred the four-lane semantic rollout while
the graph-lane shape was unproven. #555 has now validated every element
of that shape on a live, 24/7-served store: persistent HNSW created at
the materialise site, l2 routing on unit-norm vectors (l2 ordering ≡
cosine, so no cosine opclass is needed), a stop-server build window,
and a bank re-baseline (45.93 → 9.99 ms, recall@10 200/200). What
remains is exactly the parent's S3 population:

- populated-but-unindexed gaps: doc 4,108 · script 755 · memory 188 ·
  git 516 (+1,476 body chunks) — the parent acceptance figures.
- Lanes are small relative to the graph lane (≤4.2k rows vs 19,356),
  so per-lane index builds are ~1s-class at #555's measured 3.0 s at
  19k, and the payoff is recall quality (full corpus vs any pool) plus
  one uniform vector house pattern. Prior art: the pure-stack trial
  scored full-corpus DuckDB HNSW 27/57 vs 19/57 for the pool-limited
  production path on the same corpus (duckdb_pure_stack.md §1).

## 2. Design (per lane, identical shape)

- A vec mirror table per lane (sized FLOAT[dims] + persistent HNSW) in
  memory/embed_store.duckdb, following the note_search_vec pattern
  already carried by helpers/core/vec_search.py. Per-lane dims per the
  FI-2 correction (duckdb_pure_stack.md:50-58): doc/script/memory are
  FLOAT[512] (gemma working dim, gemma_embedder DIM=512 Matryoshka
  rule), git is FLOAT[384] (granite default, 2026-10-17 operator
  ruling).
- Bounded top-k routed ORDER BY array_distance(vec, ?) ASC LIMIT k with
  score 1 − d²/2 — the #555 landed form. k=None stays the exact scan
  (helpers/core/vec_search.py:469 encodes the k=None/k=int split).
- Unit-norm precondition: gemma vectors are re-normalised after the
  768→512 truncation (gemma_embedder.py:242), so l2 ≡ cosine holds;
  the S0 probe verifies self-cosine ≈ 1.0 per lane (granite included).
  A non-unit lane normalises at mirror-write time — deterministic and
  cosine-ranking-equivalent.
- Stamp discipline inherited: a gemma-stamped index refuses
  fallback-model rebuilds (helpers.core.gemma_embedder
  guard_gemma_stamp); the vec mirror carries the same model label so an
  embedder swap forces a rebuild instead of silent mixing. A dim change
  rides a full swap rebuild (FI-2 law).
- Dispatch strictly behind --kinds vector|hybrid; the default BM25 /
  fusion path stays byte-identical.

## 3. Slices

### S0 — probe (one pass, all lanes)

Live population counts; mirror-catalog dims (the helpers/core/vec_search.py:250
pattern); unit-norm check per lane; and the current vector-leg surface of each
CLI (which lanes already run a vector leg and from which store). Also confirm
the lane stores are CLI-opened rather than quack-served — if any store IS
served, copy the #555 S2 stop-file window for it.

### S1 — doc lane (largest first)

doc_search_vec + HNSW at the rebuild site (rebuild_doc_search.py),
idempotent DROP+CREATE, best-effort (a failed build leaves brute force
correct — the #555 rollback shape); CLI --kinds vector|hybrid routed;
EXPLAIN shows Hnsw Index Scan on the bounded leg.

### S2 — script / memory / git repeat

Repeat S1 after the doc lane's gate passes. Git goes last (granite
384 + the 400-char embed basis and body-chunk fusion legs; both commit
and body populations indexed).

### S3 — validation

recall@10 ≥ .99 per lane vs the exact scan; probe bank per lane under
bench_data/<lane>/; targeted tests per lane green; rebuild-wall delta
recorded (expected ~1s-class per lane).

## 4. Risks

- The LOAD-vss bind law: every session binding a store that carries an
  HNSW index needs INSTALL/LOAD vss plus
  hnsw_enable_experimental_persistence=true before the bind, or the
  unknown-index-type bind bug bites (it cost 280 s of embed work once —
  duckdb_pure_stack.md §4). Both the rebuild helpers and the CLIs need
  the guard.
- Index build failure must never break a rebuild (best-effort CREATE;
  brute fallback stays correct).
- io.lock single-writer discipline during rebuild windows (the
  convo_search law extends to these stores).

## 5. Acceptance

1. Populated-but-unindexed gaps closed: doc 4,108/4,108 · script
   755/755 · memory 188/188 · git 516/1,476.
2. recall@10 ≥ .99 per lane vs exact; EXPLAIN shows the HNSW index scan
   on the bounded leg; default legs byte-identical. No eval-gate bullet
   (proposals/README house rule targets query-visible semantics
   changes; these legs are opt-in behind --kinds and store semantics
   are unchanged — the parent S3 posture).
3. Targeted tests green; make search-fresh APPLY then plain check
   rc=0.

## 6. Out of scope

- Embed-store consolidation (parent S4 — gated on the posture decision).
- convo and note lanes (already shipped: pure-stack S1 + note_search_vec).
- Hybrid fusion-weight retuning (a separate arc if the probe banks ask).

## 7. S0 execution findings (2026-11-22) — the premise dissolved

S0 (§3) overturned the proposal's motivation before any wiring:

- **Every lane already stores embeddings and serves a cosine leg.**
  script 881/881 and memory 416/416 carry BLOB-encoded f32 vectors with
  the rust/numpy/pure backend selector and a zero-copy fast path
  (0.34 ms / 0.04 ms measured); git carries 619 + 1,860 body-chunk
  blobs with max(head, best-chunk) scoring; doc serves a whole-corpus
  SQL cosine sweep over its doc_vectors mirror (21.84 ms at 4,830×512);
  the note sidecar hosts 19,356 vectors. Hybrid is already the CLI
  default (`--bm25` opts out) with shared `--query-vector` fan-out —
  the parent S3 acceptance shape exists under different names.
- **The routing matrix (2.0.1, vss loaded)**: only the #555 house
  combination routes (plain-l2 index + `ORDER BY array_distance ASC
  LIMIT k`, top5 = exact); the `metric='cosine'` WITH form routes
  nothing in any query form. Both live indexes — idx_doc_vectors_emb
  and idx_note_search_vec_emb — were therefore decorative: the doc leg
  is whole-corpus by design (fusion needs every doc's cos_rank) and the
  only production knn caller (app.py:1009) uses k=None.
- **Fixes landed**: index creation removed from the doc mirror builder
  (with the now-dead apply-window vss LOAD) and from vec_search
  (_create_index gone; int-k leg stays a correct unindexed top-k scan);
  the doc test pin flipped to index-absent. Live stores migrated (doc
  via search-fresh APPLY --allow-stale after the mid-arc HEAD
  divergence; note via sync_vec_table full=True) — both indexes gone,
  mirrors intact, both knn legs verified.
- Reopen trigger (both lanes): a bounded top-k vector consumer appears
  → create the plain-l2 index and route array_distance (vectors are
  unit-norm). Script/memory/git: no action, verdict recorded in
  bench_data/search_lane_hnsw_s0/verdicts.md.
