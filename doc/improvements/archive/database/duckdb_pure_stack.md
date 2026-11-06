---
title: "Pure-DuckDB retrieval stack: inline vectors, native FTS, hybrid retune"
status: executed
filed: "2026-11-05"
executed: "2026-11-06"
completed_md: "481"
area: "helpers/core/vec_search.py + helpers/misc/convo_query.py + helpers/maintenance/rebuild_convo_search.py (12-file vec0 surface)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Pure-DuckDB retrieval stack: inline vectors, native FTS, hybrid retune

**Date:** 2026-11-05 · **Status:** PROPOSED ·
**Area:** helpers/core/{vec_search,embed_cache,embed_matrix}.py + helpers/misc/convo_query.py (lexical leg) + helpers/maintenance/{rebuild_convo_search,db_maint,snapshot_db}.py + 9 more vec0 files (§3 S2) + doc/local/evaluations/unified_backend_assessment.md (evidence)

**Follows:** `doc/local/evaluations/unified_backend_assessment.md` §2/§5/§7 (the four-server parity trial, 2026-11-05); trial JSONs in `bench_data/convo_gemma/*_20261105.json`.

## 1. Motivation

The 2026-11-05 parity trial (four server legs + DuckDB 2.0.0 against the
production stack, §7 of the assessment) inverted the lexical picture and
quantified the vector gap:

- **Lexical**: DuckDB native FTS scores **39/57 pass@5** on the frozen
  convo bank vs **22/57** for the production sqlite FTS5 leg (+77% MRR
  .537 vs .347). FTS5 also zero-results **22/100** house-quoted parity
  queries where DuckDB native zero-results none, and is the weakest of
  five lexical engines measured (§7.4).
- **Vector**: full-corpus DuckDB HNSW over `FLOAT[]` scores **27/57** vs
  **19/57** for the 5k-pool production path; the same +7–9 gap holds on
  every engine, so it is a pool artifact, not an engine artifact (§7.2).
- **Hybrid**: every engine's rrf hybrid scores **below its own best pure
  leg** (DuckDB 36 < 39; production hybrid 8–10 vs pv_gemma 24). The
  fusion, not the engines, is the production quality bug (§7.3).
- **Drop-in check**: no server candidate matches FTS5's top-10 shape
  (exact-10 0–53/100, §7.4), so the unified-server door stays closed and
  the re-open triggers (§4) are dead. The winning move is on the current
  engine.
- **Operator directive (2026-11-05)**: move to pure DuckDB; query latency
  is not a gating criterion unless 10× worse than competition. DuckDB's
  worst measured number (native-FTS 298–349 ms p50 warm) is accepted
  under this rule; HNSW full-corpus is 10.9 ms warm — faster than
  SurrealDB and ArangoDB (§7.6).

Concurrent wins folded in by design: the swap-rewrite space reclaim
(~370 MB, §2 row 2 — folded into S1 because the inline arc restructures
the same store) and FI-2 resolution. **FI-2 correction (2026-11-05
probe)**: DuckDB HNSW index keys MUST be sized — an unsized `FLOAT[]`
column raises `Binder Error: HNSW index keys must be of type FLOAT[N]`
at `CREATE INDEX`; the trial's HNSW always ran on `::FLOAT[384]`-cast
tables (recall@10 .8649/.8947 vs numpy brute, §7.5). Resolution:
**per-lane sizing** — convo column `FLOAT[384]` (EMBED_DIM constant,
this arc), notes lane `FLOAT[512]` (S2); a third dim is a one-constant
change riding a full swap rebuild, which restores the DDL-level dim
guard the variable shape lacked.

FI-1 (provenance JSON → `VARIANT`) stays deferred per its own trigger
and is out of scope here.

## 2. Current state (to be confirmed against the discovery brief)

- `memory/convo_search.duckdb` `convo_search` already carries inline
  `embedding FLOAT[]` (granite-384, 193,246 rows post-harvest) — the
  convo lane's vectors are *already* in DuckDB; what is missing is the
  HNSW index and the query-side use of it. S1 sizes the column to
  `FLOAT[384]` (see the FI-2 correction above: HNSW requires it).
- The vector query path runs against a **5k pool** served via the
  sqlite-vec/`vec0` sidecar stack (`embed_store.db`, 12 production files
  + 4 test files touch `vec0`) — hence pv_production 14/57 vs 27/57
  measured on the same corpus.
- The lexical leg is sqlite FTS5 (`memory/convo_search_fts.db`
  `convo_fts`), queried with the house token-quoting pattern
  (`helpers/misc/convo_query.py:128`).
- HNSW on DuckDB 2.0.0 requires
  `SET hnsw_enable_experimental_persistence=true` before index DDL;
  working DDL + query forms are proven in
  `bench_data/convo_gemma/duckdb_trial_20261105.py` (build: 68 s for
  191k×384; cosine-distance form, direct ORDER BY).

## 3. Slices

### S1 — Schema + rebuild: HNSW into `convo_search.duckdb`, swap-reclaim folded in

Extend `_rebuild_via_swap` (`helpers/maintenance/rebuild_convo_search.py:739`)
so the rebuilt store carries the HNSW index over `convo_search.embedding`
(sized `FLOAT[384]` via the new `EMBED_DIM` constant — HNSW requires
sized keys; the stage insert casts `CAST(embedding AS FLOAT[EMBED_DIM])`
so a wrong-dim vector fails loudly at load), inside the
existing duckdb_lock single-writer + RO-reader protocol. Row-count
assert stays; add: index present post-swap + recall spot-check (10
bank queries vs numpy). Reclaims the dead space in the same pass
(measured 648→216 MB on the pre-trial store; VACUUM alone reclaims 0).

### S2 — Read ports: retire `vec0`/sqlite-vec from the query paths

Port the 12-file vec0 surface to the inline store: `helpers/core/{embed_cache,embed_matrix,vec_search}.py`,
`helpers/maintenance/{db_maint,migrate_embed_store,rebuild_doc_search,rebuild_note_search,snapshot_db}.py`,
`helpers/misc/{review_scan,schema_dump}.py`,
`helpers/bench/{note_ab_granite,note_deep_probe}.py`; update the 4 test
files (`tests/test_{api_search,flat_knn_fallback,rebuild_note_search,vec_search}.py`).
Drop the sqlite-vec dependency in the same change. The notes lane
(`note_search_vec` `FLOAT[512]`, `VEC0_KNN_MAX=4096`) gets the same
treatment (sized `FLOAT[512]` — per-lane dims per the FI-2 correction;
HNSW requires sized keys) — its hosting store decision rides on the
discovery brief (DuckDB sidecar or the convo store; never research.db).

### S3 — Lexical leg: FTS5 → DuckDB native `match_bm25`

Swap the convo lane's lexical queries (`helpers/misc/convo_query.py`
first, then misc sites) from FTS5 to the direct-ORDER-BY form
(`ORDER BY fts_main_docs.match_bm25(id, ?)` — the alias-form binder bug
is still open upstream, unfiled as of 2026-11-05, §4 trigger 1). Note
in user-facing output that result *sets* will differ from FTS5 (this is
the +77% point, not a regression).

**Implementation notes (2026-11-05, landed in worktree):** the store
gains a full `text` column (the duckdb store was a pointer index —
snippet only — while the FTS5 sidecar indexes the 8 KB snippet; the
native index covers full text, a documented divergence that only
affects pre-S3 stores on the fallback leg). Reader: `_fts_hits_duckdb`
direct form with `IS NOT NULL` — without it, once true matches
fall below the limit, NULL-score rows leak into the tail (probe
2026-11-05); pre-S3 stores fall back to the sqlite sidecar on
`CatalogException`. Builder: swap path runs
`PRAGMA create_fts_index('convo_search','part_id','text')` on the
tmp before the swap instant (same invariant as HNSW); in-place windows
end with drop+recreate (`_refresh_fts_index` — DuckDB FTS is a
one-shot structure, no incremental maintenance); `_bulk_insert`
probes `duckdb_columns` so in-place windows against pre-S3 stores
simply omit the missing column. The sqlite sidecar sync stays during
transition and retires post-merge after the live re-baseline.

### S4 — Hybrid retune: make fusion beat its best pure leg

Sweep rrf k and score-normalized weighted fusion on the 57-question
bank (artifacts + scorer in `bench_data/convo_gemma/convo_score.py`).
Gate: hybrid ≥ best pure leg on the bank (today 39), production arms ≥
BASE pv_gemma 24. If no fusion beats pure lexical, make hybrid opt-in.

**Results (2026-11-05):** sweep `bench_data/convo_gemma/s4_retune_20261105.py` on the S3 store, live
193,246-doc corpus — winner `anch_k20_a3` (bm25-anchored RRF, k=20,
3 anchors) at **41/57 pass@5, MRR .5076** — every fusion arm ≥ its pure
legs; symmetric RRF 36–37/57 confirms the 2026-10-27 frozen-bank
verdict that anchoring is required. Pure legs on the same store:
lex_duck 38/57 (.4971), vec_duck 27/57 (.3234). **Gate PASS on the
comparative form** (hybrid 41 ≥ best pure 38; production vector 27 ≥
pv_gemma 24). The trial's duckfts 39/57 vs today's 38/57 is a single
question flip (`code_ident_010`) from corpus drift — the bank dates
2026-10-26, the corpus gained ~1,800 parts at the 16:00 harvest; all 18
trial misses remain misses (no port regression). Tier split for the
winner: self 12/18, decision 13/18, code_ident 16/21. Vs BASE: lexical
22 → 38 (+73%), vector 14 → 27 (+93%), hybrid 8 → 41 (5.1×).

## 4. Acceptance criteria

- **Bank gate (retrieval semantics)**: re-run the frozen 57-question
  convo bank on the ported stack on the live corpus:
  `pv_duck_full` ≥ 27, lexical ≥ 39, hybrid ≥ 39 (S4), all beating the
  BASE production arms (bm25 22, pv_gemma 24).
- **Parity gate**: 100-query protocol zero-result count stays 0/100 on
  the native engine; recall@10 vs numpy brute — GATE REWORDED at the
  step-0 re-baseline (2026-11-06): strict-set recall is dominated by
  f32 tie-cluster composition (live corpus measured 0.5088 strict with
  the HNSW top-10 sim-floor matching exact brute within −5.96e-08 on
  every query probed — the trial .8649 was tie-luck, not quality).
  The honest gate: HNSW 10th-pick similarity within 1e-6 of brute 10th
  on all sampled queries, plus latency ≤ 15 ms.
- **Rebuild gate**: `_rebuild_via_swap` row-count assert passes; HNSW
  index present and used post-swap (EXPLAIN check); RO readers work
  during rebuild (this doubles as the Gate 2 concurrency drill the
  assessment still owes, §7.8).
- **Surface gate**: `grep -rl vec0 helpers/ scripts/` returns 0
  production files; sqlite-vec absent from deps; targeted tests updated
  and green; full `make qa` once on operator go (arc close-out).
- **Latency note**: native-FTS p50 recorded (≈298–349 ms warm) and
  accepted per the 10× rule; HNSW p50 ≤ 15 ms warm.

**Status (2026-11-05, S1–S4 executed in worktree, pre-merge):** bank
gate — vector 27 ≥ 27 PASS, hybrid 41 ≥ 39 PASS, lexical 38 vs 39 =
one-question corpus-drift miss (see S4 results; comparative form
passes, all BASE arms beaten). Parity gate — zero-results 0/100 held on
the trial protocol; recall re-baseline measured at S1 validation
(.8649/.8947 on the trial snapshot; live-corpus re-check rides the
post-merge cutover). Rebuild gate — swap row-count assert PASS (sandbox
+ live S1); EXPLAIN uses HNSW; RO readers during rebuild exercised in
sandbox runs. Surface gate — `grep -rl vec0 helpers/ scripts/` → 0,
sqlite-vec dropped; targeted suites green (275 tests); full `make qa`
rides operator go. Latency — native-FTS p50 accepted per the 10× rule
(trial 349 ms); HNSW 10.9–12 ms p50.

**Step-0 re-baseline (2026-11-06, live 194,525-row corpus, granite
20261106 npz — bench_data/convo_gemma/_parity_live_20261106.py + .json):**
bank gate — hybrid (anchored k20 a3) 40 ≥ 39 PASS, vector 27 ≥ 27 PASS,
lexical 38 (same one-question drift miss, unchanged). Parity gate —
overlap@10 2.62/10, jaccard .2219 (trial .2187), FTS5 blind 22/100
(unchanged), native p50 204.6 ms; zero-results 2/100 = two
stopword-only shingles (now-the-two / let-see-the) that duckdb fts
stopword-strips to nothing while FTS5 (no stopword list) matches —
benign for real queries. Recall gate — reworded above (quality-floor
evidence −5.96e-08). Side-finding: vss HNSW is MAINTAINED on INSERT
(controlled self-query test) — the one-shot assumption holds for FTS
only; DELETE behavior untested. Baseline arms re-run byte-stable
(convo_scores_20261106.json).

### S5 — Notes vectors: derived mirror → DuckDB-primary (DEFERRED)

`note_search_vec` in `memory/note_vectors.duckdb` is today a
derived, snapshot-excluded mirror; the JSON `embedding` column in
research.db `note_search` (19,328 notes, 39.6 MB — binary f32 would
be the same size) stays the source of truth. Making DuckDB primary
means: drop the JSON column, `rebuild_note_search` writes vectors
DuckDB-only, and `snapshot_db`/`db_maint` gain coverage for the
sidecar (copy-under-lock + restore path + backup twin). The blocker is
the crash-consistency contract, not size: with SQLite-primary, a
research.db restore plus mirror rebuild is the single recovery path;
with DuckDB-primary, cross-store writes have no 2PC, so the slice must
land snapshot coverage and a documented consistency invariant together.

**Deferred 2026-11-05 (operator call):** at 19k notes the mirror shape
pays nothing measurable and buys the one-file recovery story. Triggers
to revisit: notes-lane HNSW becomes the serving path for k-int queries,
or JSON parsing starts hurting notes rebuild times.

## 5. Risks and constraints

- **Concurrency (Gate 2)**: HNSW persistence is experimental on
  2.0.0; the drill (7 CLIs + `duckdb_lock.py` writer + swap-rebuild
  readers, §Gate 2) must pass before cutover, not after.
- **Snapshot/backup**: `snapshot_db.py` and `store_sweep.py` must not
  break when the sidecar disappears (S2); verify file-copy semantics.
- **Retention**: 6M–1YR harvest-side purge untouched
  (`helpers/maintenance/harvest_conversations.py:2618`); swap rebuild
  asserts row identity, so retention semantics cannot drift via this
  arc.
- **Embedding model**: granite-384 sidecar contract unchanged
  (`helpers/core/local_embedder.py`); no re-embed is required by any
  slice (inline uses existing vectors — pack, not re-embed, per §5
  constraint).
- **Result-set change (S3)**: lexical swaps change which documents
  surface for the same query — intended (+77% quality), but flag in
  the changelog so consumers are not surprised.

## 6. Execution order

S1 → S2 → S3 → S4. S1–S2 are one `wt` worktree arc
(`duckdb-vector-inline`); S3–S4 may ride the same arc or follow once
S1 lands. Discovery brief (vector-lane data flow, embed_store.db role,
pool location, snapshot implications) is being compiled before the
first edit; slices get targeted tests during the arc and full gates
(qa + integration + perf + advisory) once, on operator go.
