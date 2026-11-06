---
title: "Graph-lane HNSW and the remaining retrieval unlocks"
status: deferred
filed: "2026-11-06"
executed: "2026-11-06"
completed_md: "482"
area: "helpers/graph/query.py + helpers/graph/embeddings.py + helpers/maintenance/rebuild_convo_search.py (FTS5 sidecar retirement) + helpers/misc/quack_server_child.py (served-path vss) + 4 lane query CLIs + helpers/core/{db,vec_search,embed_matrix}.py (embed-store consolidation, S4) + doc/improvements/proposals/duckdb_pure_stack.md:60 (FI-1 tracker)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Graph-lane HNSW and the remaining retrieval unlocks

**Date:** 2026-11-06 · **Status:** PROPOSED ·
**Area:** helpers/graph/{query,embeddings}.py (graph-lane HNSW) +
helpers/maintenance/rebuild_convo_search.py (FTS5 sidecar retirement) +
helpers/misc/{doc,script,memory,git}_query.py (opt-in semantic legs) +
helpers/core/{db,vec_search,embed_matrix}.py (embed-store consolidation) +
doc/improvements/{pending.md,proposals/duckdb_pure_stack.md:60} (FI-1)

**Follows:** doc/improvements/proposals/duckdb_pure_stack.md (the whole
arc — S1–S4 — is executed and landed; see doc/improvements/proposals/duckdb_pure_stack.md:178-180
bank gate: vector 27 ≥ 27 PASS, hybrid 41 ≥ 39 PASS, lexical 38/39 one-question
corpus-drift miss) and the 2026-11-06 unlock survey. Prior art for the HNSW
shape: doc/improvements/pending.md:559-640 (deferred N5 item 5).

## 1. Motivation

The pure-stack arc closed the convo high-water marks (HNSW full-corpus
>13 ms warm @ 194krows, native match_bm25 39/57 vs FTS5 22/57, hybrid
anch_k20_a3 41/57 MRR .5076). What remains is not more of the same engine —
it is the long tail the pivot has now made measurable: the graph lane still
brute-forces its vectors, four populated embedding lanes have no serving
path, the FTS5 sidecar is transitional only, and provenance is
still a deferred JSON column. Each of these is a one-lane extension of a
proven pattern, not new machinery. Latency is not the gate (the operator
directive carries over: pure-DuckDB, not gating unless 10× worse) — the
gate is recall parity at the same call signatures.

## 2. Current state

- The graph lane stores two unindexed vector tables in
  memory/graph.duckdb (72 MB): v_embeddings (1,209 rows) and
  v_note_embeddings (19,328 rows, one per note section). No HNSW index
  exists on either; the KNN entry points
  (helpers/graph/query.py:397 `_prep_graph_connection`,
  helpers/graph/query.py:3691 company_neighbors_bundle,
  helpers/graph/query.py:4260 similar_notes) go through brute-force
  scalar functions. helpers/graph/query.py:415 documents the stopgap
  ("hnsw_index_scan/vss_match are broken on vss b833341") but DuckDB 2.0.0
  indexes USING HNSW (emb) on sized FLOAT[512] — the same live shape
  already serving convo_search (idx_convo_search_emb, ef_construction=128,
  metric=cosine) and notes (memory/note_vectors.duckdb
  note_search_vec FLOAT[512], idx_note_search_vec_emb — knn 15.95 ms).
  The older empirical block no longer holds: at 168k unfiltered ANN never
  beat brute (171.7 vs 116.6 ms, doc/improvements/pending.md:559-640),
  but the bounded/filtered top-k evidence measured 2026-11-05/06 shows 20/20
  recall (238.7 ms exact vs 16.5 ms HNSW @dsh 17,475 rows; those probe
  outputs live only in session stdout — persist them under bench_data/
  before citing them as acceptance evidence) — the trigger
  for re-checking has fired at the lane scale (19.3k sections well
  below the 10^6 next-check).
- Five populated embedding tables across four lane stores have no
  vector query path at all:
  doc 4,108/4,108 · script 755/755 · memory 188/188 · git 516/516 ·
  git-body 1,476/1,476. There is no vector code with RRF in
  helpers/misc/doc_query.py, helpers/misc/script_query.py,
  helpers/misc/memory_query.py, or helpers/misc/git_query.py.
- The FTS5 sidecar (memory/convo_search_fts.db, 483 MB, rebuilt
  2026-11-06 07:05) is still built and read as fallback:
  helpers/maintenance/rebuild_convo_search.py:474 `_fts_sync` writes it,
  helpers/misc/convo_query.py:177 `_fts_hits_sqlite` reads it. The
  proposal text at doc/improvements/proposals/duckdb_pure_stack.md:133-134
  says it stays during transition and retires post-merge after the live
  re-baseline. Correction (review 2026-11-06): the live STORE is
  verified (194,525 rows, 0 nulls, avg 1,437 chars; native match_bm25
  primary; EXPLAIN routes HNSW; production smoke sane) — but the BANK
  re-baseline (57-question bank + 100-query parity on the live corpus,
  per duckdb_pure_stack.md:178-186) has NOT been run; the newest bank
  artifact is bench_data/convo_gemma/convo_scores_20261105.json. S2
  step 0 is that bank re-run; the sidecar retires only after it passes.
- FI-1 (provenance JSON -> VARIANT) stays deferred per its own trigger
  (doc/improvements/proposals/duckdb_pure_stack.md:60) and is out of
  scope here except as a tracked follow-up.
- Sidecars that remain: memory/embed_store.db (455 MB) +
  memory/embed_matrix.f32 (39.6 MB). helpers/core/vec_search.py:90
  still points at embed_store.db for the JSON-blob blob cache; the flat
  matrix serves notes semantic_hits (helpers/misc/note_query.py:180)
  with EmbedMatrixStore.top_k over embed_matrix.f32. The vectors were
  already mirrored; the remaining engine question is whether the cache/matrix
  layer consolidates to DuckDB.

## 3. Slices

### S1 — Graph-lane HNSW (the headline unlock)

Index both vector tables with the proven shape and re-wire the KNN
entry points to the HNSW routing form (no signature change):

- helpers/graph/query.py: add CREATE INDEX ... USING HNSW
  (<col>) WITH (metric=cosine, ef_construction=128) — the column is
  `embedding` on v_embeddings but `emb` on v_note_embeddings
  (schema/duckdb/graph.sql:39,:44) — for both FLOAT[512] tables at the
  materialise site alongside helpers/graph/query.py:1735;
  _prep_graph_connection loads vss + sets
  hnsw_enable_experimental_persistence=true (the
  helpers/graph/query.py:397 LOAD quack line pattern already exists at
  helpers/misc/quack_server_child.py:122).
- Quack-server coordination (review addition 2026-11-06): the store is
  served 24/7 by helpers/misc/quack_server_child.py, which holds the
  read-write lock (helpers/graph/query.py:453-455 routes reads over the
  wire when served). Two requirements: (a) the materialise/index window
  must stop the server (stop-file mechanism) and restart it after;
  (b) the SERVER session must INSTALL/LOAD vss + SET
  hnsw_enable_experimental_persistence=true — today it loads only quack
  (helpers/misc/quack_server_child.py:122), so served bounded-KNN
  queries execute server-side WITHOUT vss and silently fall back to
  brute force; the client-side SET does not reach the served path.
- Re-route the bounded top-k queries (helpers/graph/query.py:3691,
  :4260) to ORDER BY array_cosine_distance(embedding, ?) ASC LIMIT k
  — the only ORDER BY form that routes on DuckDB 2.0.0; keep
  k=None exact as the whole-corpus scan (the unfiltered ANN never
  beat brute at 168k, and helpers/core/vec_search.py:469 already
  encodes the k=None/k=int split).
- Acceptance: stopgap comment at helpers/graph/query.py:415 deleted;
  recall@10 against the exact scan ≥ .99 on the 19k-section table —
  evidence chain (review-corrected 2026-11-06): 100% @168k came from
  the 2026-10-27 semi-join plan shape (doc/improvements/pending.md:628),
  the pure-stack trial gated strict .8649 / tie .8947 @191k on the
  direct ORDER BY form, and the 2026-11-06 survey measured 20/20 @dsh
  17,475; recall degrades with corpus size, so ≥ .99 is a bet on 19k
  scale that this slice's own probe must verify. EXPLAIN shows the
  HNSW index scan for the bounded leg on BOTH the local path and the
  SERVED path (a query issued through the quack server), and the
  existing unfiltered path is unchanged; existing graph goldens green;
  latency not gated (directive: ≤ 10× worse). Expected cost: index
  size — prior art measured +75% db @16.5k (vss 1.5.x) and +212% @168k
  (2.0.0), so plan for graph.duckdb ~72 → ~190 MB.

### S2 — Retire the FTS5 sidecar

- Step 0 — run the pending live bank re-baseline (see §2): 57-question
  bank arms + 100-query lexical parity on the live store; the sidecar
  retires only after it passes.
- Delete the sqlite build/read path: _fts_sync + convo_fts DDL
  (helpers/maintenance/rebuild_convo_search.py:474,:106,:489,:493,:554,:823,:925,:1112),
  _fts_hits_sqlite + the sidecar connect fallback
  (helpers/misc/convo_query.py:114,:153,:177,:182), and the
  helpers/maintenance/db_maint.py:717 xref entry. Keep native FTS
  refresh (_refresh_fts_index, drop+recreate one-shot) untouched —
  the house token-quoting pattern is already over text. Incremental
  windows then pay the one-shot FTS rebuild each run (~33 s @194k, the
  fts_build phase) — comparable to the sqlite sync it replaces
  (26-61 s), no regression.
- Parity reference (review addition 2026-11-06): the 100-query parity
  protocol measures overlap AGAINST the sqlite FTS5 sidecar this slice
  deletes. The final parity run must build a one-off FTS5 index from
  the same corpus (scratch store, discarded after) so the reference
  exists; afterwards parity re-baselines as native-vs-exact house
  expectations.
- Known loss, accepted: post-S2 code cannot serve pre-S3 store copies
  (point-in-time backups lose their lexical fallback leg).
- Acceptance: the 483 MB convo_search_fts.db no longer written on
  rebuild; zero grep hits for convo_fts in helpers/; step-0 bank
  re-baseline passed (0/100 zero-results held, native recall
  .8649/.8947 from the pure-stack bank gate).

### S3 — New semantic legs for doc/script/memory/git (opt-in)

Mirror the note_vectors pattern for each populated lane: sized
FLOAT[dims] vector table + persistent HNSW + k-int array_cosine_distance
routing, dispatched behind the existing --kinds selection so the
default BM25/fusion path is byte-identical. Start with one lane
(doc, largest population) and repeat for script/memory/git after the
house gate passes.

- Acceptance: populated-but-unindexed gaps closed (doc 4,108/4,108 …
  git-body 1,476/1,476); lane CLIs grow --kinds vector|hybrid options;
  no default legs change; targeted tests per lane green; probe bank per
  lane recorded under bench_data/<lane>/.

### S4 — SUITE: embed-store consolidation (follow-up)

Merge research.db + embed_store.db + graph.duckdb into one
DuckDB source of truth — kills two-engine sync, sqlite-sidecar
backup debt (helpers/maintenance/db_maint.py:475), and the dual-snapshot
problem, per doc/local/evaluations/duckdb_consolidation_assessment.md.
Gate on the proposal §4 Re-open triggers (quack stable, fts parity
snippet/highlight/weighted-bm25, HNSW production status) before any
production embed lookup migrates; the vectors already mirror — this is
the blob cache + flat matrix decision. Filed as
doc/improvements/proposals/duckdb_full_consolidation.md 2026-11-06 —
the gate ran (trigger verdicts, B2/B1-lite, posture fork): recommendation
is keep-the-split, merge gated on the posture decision.

## 4. Risks

- The unfiltered ANN path never beat brute at 168k. Mitigation: keep
  k=None exact scan for whole-corpus coverage (coding pattern already
  in helpers/core/vec_search.py:469); only the bounded leg routes.
- HNSW is experimental on DuckDB 2.0.0 — index build failure breaks the
  in-place window unless _load_vss + SET
  hnsw_enable_experimental_persistence=true precede every bind
  (the pure-stack "unknown index type HNSW" bind bug, 280 s of embed
  lost — the same guard is required on the graph lane).
- Retention harvest purge (helpers/maintenance/harvest_conversations.py:2618)
  stays untouched by any rebuild rewrite; the graph lane vendor
  refresh (helpers/graph/query.py:1339) must not regress.

## 5. Acceptance (whole proposal)

- Graph lane: uses_hnsw_index=true in EXPLAIN for the bounded leg;
  recall@10 ≥ .99 vs exact on 19,328-section table; stopgap comment
  deleted; all prior graph goldens pass unchanged.
- Coverage: 4 lanes flip from populated-but-unindexed to queryable
  (doc 4,108 · script 755 · memory 188 · git 516/1,476), each with a
  recall parity probe vs the brute-force leg for that lane.
- Sidecar: 483 MB gone from the write path; zero convo_fts grep hits;
  house-quoted 100-query parity 0/100 zero-results held.
- Rollback surface: one slice lands as one wt arc per lane; each
  arc gate is the bank/exact-parity probe + targeted tests.

## 6. Order

S1 -> S2 -> S3 -> S4. S1 is the headline (bounded scope, proven shape,
19k-section scale). S2 rides once the live re-baseline is stable in
production. S3 repeats the mirror pattern per lane (one arc per lane
or one arc for all four at operator discretion). S4 is a separate
consolidation proposal gated on the §4 re-open triggers.
FI-1 (provenance -> VARIANT) remains deferred per its own trigger and
is out of scope.
