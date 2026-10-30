---
title: "duckdb_vector_search_assessment: engine split (FTS5/sqlite-vec vs DuckDB) + 2.0 VARIANT re-check"
status: deferred
filed: "2026-10-21"
executed: "2026-10-22"
completed_md: "430"
area: "helpers/maintenance + helpers/graph (storage typing) + schema"
---

# duckdb_vector_search_assessment: engine split + 2.0 VARIANT re-check

**Date:** 2026-10-21 (merged 2026-10-22) · **Status:** DEFERRED
(2026-10-22, completed.md #430) — questions settled for now, revisit
triggers at §7.

Merged document: the 2026-08-27 engine assessment
(`duckdb_vector_search_assessment.md`, previously a standalone note
under `doc/local/evaluations/`) folded together with the
`duckdb_20_variant` proposal (filed 2026-10-21) and its 2.0 re-check.
One home so the two questions — *should search move into DuckDB?* and
*should our JSON columns become VARIANT?* — are answered from the same
measurements instead of being re-derived per session.

Family provenance: split 2026-10-21 from the bundled
`duckdb20_quack_variant` filing. Siblings: `duckdb_20_quack.md`
(executed, completed.md #428), `duckdb_20_sqlite_reentry.md` (live,
gated). Migration record: completed.md #427. Upstream changeset:
<https://github.com/duckdb/duckdb/releases/tag/v2.0.0>.

## 1. TL;DR

**Engine split, unchanged:** SQLite (`FTS5` + `sqlite-vec` sidecar) owns
search; DuckDB owns set-SQL analytics over materialised caches. The
MotherDuck "search in DuckDB" stack is one we largely already run, and
the parts that would replace SQLite are strictly weaker; the decisive
objection was **process semantics**, which 2.0's Quack solved — but the
re-pilot shows the wire is not a search-store answer either
(RTT-dominated: 24.9 ms warm KNN vs sub-ms local).

**VARIANT, not warranted at our scale.** DuckDB 2.0 makes `VARIANT` a
first-class shredded columnar type (compression + path pushdown,
Parquet round-trip). Re-measured against our largest real JSON payload:
path extraction 1.28× faster, but storage **1.71× larger**, Parquet
read **6.2× slower**, Parquet bytes unchanged. Migration surface is also
tiny post-L2 (§5.4). Verdict: no column clears the ≥2× bar → deferred,
not adopted.

## 2. Why not DuckDB for the search/doc/notes store (2026-08-27)

**Trigger:** the MotherDuck blog series ["Search using
DuckDB" parts 1–3](https://motherduck.com/blog/search-using-duckdb-part-1)
suggests keyword+vector+hybrid search purely in DuckDB; asked why we
don't use that instead of SQLite for our search/doc/notes DBs and the
embed sidecar. **Context:** consolidation design lives in
`embed_store_consolidation.md` (§3 constraint 7, §4C) — archived,
completed.md #166.

| Post | Their technique | Our status |
|---|---|---|
| Part 1 | Brute-force `array_cosine_similarity` over `FLOAT[]`, `ORDER BY score DESC LIMIT k` (CROSS JOIN) | **Already production**: how graph.duckdb serves `v_embeddings` / `v_note_embeddings` SQL cosine joins (`helpers/graph/query.py` materialisations + 15 wrappers) |
| Part 2 | LlamaIndex `DuckDBVectorStore` (no SQL vector layer) | We own chunk/retrieval plumbing deterministically; a heavy retrieval framework contradicts the local-embeddings non-goals (their model choice is ours: bge-small-en-v1.5 @ 384) |
| Part 3 | Hybrid = DuckDB `fts` (`match_bm25` macro) + convex fusion α=0.8 | We implement hybrid fusion and eval'd it (hybrid 1.00 vs BM25-only 0.93); RRF k=60 is live in app.py |

### 2.1 DuckDB `fts` is thinner than FTS5 — every consumer would be rewritten

- Fixed-parameter `match_bm25` (k=1.2, b=0.75, no tuning surface); index
  hidden in an `fts_main_<table>` shadow schema.
- No column-weighted `bm25()`, no `snippet()` / `highlight()`, no
  NEAR / phrase / column-filter syntax.
- Porting cost: `/api/docs/search`, `doc_query` + `script_query` CLIs,
  three `--check` gates, incremental rebuilders keyed on `_meta`
  fingerprints, hermetic test fixtures, FTS-rebuild restore path — all
  rewritten onto a weaker primitive, at 247–1,224-row scale where
  everything is sub-ms either way. Zero measured retrieval gain.

### 2.2 Process model (the real deal-breaker, since solved for new designs)

- SQLite WAL: N concurrent Flask readers + rebuilder writer + parallel
  `--check` cache-warms at once, verified under load.
- DuckDB 1.5.x: one read-write connection excludes every other process
  (the 2026-08-25 `make advisory` graph-algos crash). app.py legitimately
  WRITES the sidecar (lazy vec0 backfill), so every web request would
  need RW → one request blocks all CLI queries.
- Backups assume the sqlite online-backup API; the DuckDB snapshot path
  (read-only CHECKPOINT + file copy) carries a version-sensitive
  assumption (graph_design.md §9.3/O3).
- **(2026-08-27 addendum → resolved by 2.0)** DuckDB
  [Quack](https://duckdb.org/2026/05/12/quack-remote-protocol) adds a
  server-process remote protocol: many OS processes `ATTACH` with
  concurrent read AND write. Production release landed with v2.0; the
  2.0.0 re-pilot is §4. It does not flip the search decision: blockers
  §2.1/§2.3 are untouched, and every consumer would depend on a
  supervised daemon (port + token lifecycle instead of "file exists →
  works"). Legitimate use on this repo even without the search
  migration: simultaneous RW on `graph.duckdb` — **now adopted**
  (quack server-per-store, completed.md #428).

### 2.3 ANN/HNSW unavailable on the 1.5.x pin (empirical, 2026-08-27)

```text
duckdb 1.5.5, LOAD vss -> OK
scalar array_cosine_similarity           -> OK   (prod pattern)
CREATE TABLE t (emb FLOAT[384])          -> OK
INSERT 2000 rows                         -> OK
CREATE INDEX t_hnsw ON t USING HNSW (emb COSINE)
  -> NotImplementedException: Index with opclass not supported yet!
brute-force KNN ORDER BY array_distance  -> OK
```

HNSW is a documented non-goal regardless (~3 ms brute force at ~1k
vectors; `local_embeddings.md` non-goals). The series also targets
million-row prototypes; our corpora are ≤2.5k embedded rows per index.

## 3. Quack pilot — DuckDB 1.5.5 (2026-08-27)

Method per [the official docs](https://duckdb.org/docs/current/quack/overview).
In-memory catalog shaped like note-search: 2,000 rows × `FLOAT[384]`
(seed 42) + a `counter` table; `CALL quack_serve('quack:localhost')`
returned `(uri, http://localhost:9494, <per-startup random token>)`.
Clients were separate OS processes (`CREATE SECRET (TYPE quack …)` +
`ATTACH … (TYPE quack)`).

| Probe | Result |
|---|---|
| ATTACH | OK, 52.8 ms |
| `COUNT(*)` over the wire | 2000 |
| KNN `ORDER BY array_distance … LIMIT 5` | correct top-5 (self-match first); cold 414.9 ms, **warm 54.9 ms/query** (50 runs) |
| Tiny autocommit INSERT ×100 | 3.5 ms/write |
| `CREATE`/`DROP TABLE` remote | both OK |

Transaction anomaly (confirmed broken on 1.5.5): a `BEGIN; INSERT;
ROLLBACK` sequence **grew the table** — remote writes autocommit
regardless of client transaction state; `UPDATE`/`DELETE` were entirely
unsupported (`BinderException: Can only delete from base table`), and
`INSERT OR IGNORE` — the statement our embed cache runs on every
rebuild — raised `NotImplementedException: GetStorageInfo not
implemented yet`. Write surface was **insert + DDL only**.

Concurrency headline (the advisory-crash class): two writer processes
(200 single-row inserts each) + one reader polling every 20 ms for 8 s
→ writer A 165/s, writer B 184/s, errors=0 both; reader 310 reads,
0 errors, observed committed growth 37× mid-stream. No lock exclusion,
no flock. Aggregate ≈350 writes/s vs ~290/s single-writer.

Verdict at the time: process-model blocker genuinely solved, but write
semantics could not host the embed cache or any mutable index, and
55 ms/query gave back the sub-ms local KNN.

## 4. Quack re-pilot — DuckDB 2.0.0 (2026-10-21)

Trigger #4 fired (Quack ships production with v2.0). Slice A of
`duckdb_20_quack.md` re-ran the exact battery against 2.0.0 (throwaway
DB in `$TMPDIR`; production `memory/` untouched; driver
`$TMPDIR/opencode/quack_trial/run_trial.py` — server child holding the
connection + `quack_serve`, clients as separate OS processes).

**Verdict: Slice A PASSES** — all four 1.5.5 write-parity gaps are
fixed; Slice B (integration) unblocked per the proposal gate, and
landed as completed.md #428.

| Probe | 1.5.5 (2026-08-27) | 2.0.0 (2026-10-21) |
|---|---|---|
| ATTACH | OK, 52.8 ms | OK, 48.8–117.4 ms (5 procs) |
| remote UPDATE | silent no-op | **OK** (`tag='upd'` persisted) |
| remote DELETE | BinderException (base table only) | **OK** (count 0 verified) |
| INSERT OR IGNORE | NotImplementedException (GetStorageInfo) | **OK** (no-throw, count 1) |
| BEGIN/INSERT/ROLLBACK | autocommit anyway (+1 leak) | **genuinely rolled** (2000→2000) |
| warm KNN p50 (50 runs) | 54.9 ms | **24.9 ms** (min 24.1, mean 25.1) — 2.2× faster |
| concurrency | 2W+1R, ~350 w/s agg, 0 errors | 2W×200 + reader: **400/400 OK, 0 reader errs**, observed 2000→2170 |
| kill -9 mid-burst | reopen clean | **reopen clean**, no recovery errors |

New 2.0.0 constraint found (and since designed away in Slice B): the
quack wire does **not carry prepared-statement parameters** —
parameterized remote DML raises `InvalidInputException: Values were not
provided for the following parameters: 1`. Remote statements must ship
literal SQL; the literal-emission layer is
`helpers/core/duckdb_shared.py::inline_params` + the `WireConnection`
proxy (completed.md #428).

Latency note: wire KNN stays RTT-dominated, so the local
read path remains the right split — the server is for concurrency, not
for making single queries faster.

## 5. VARIANT re-check — DuckDB 2.0.0 (2026-10-22)

### 5.1 What was proposed

Evaluate `VARIANT` against our JSON-as-text surfaces; migrate a column
only where a clean columnar win exists. At our row counts (≤139k rows
corpus-wide) "evaluated, not warranted — recorded" is a legitimate
outcome.

**Candidates survey**

- `graph_edges.properties` — SQLite (`memory/research.db`), not DuckDB
  → **out of scope** (VARIANT is a DuckDB type); a migration there is a
  SQLite JSON re-design, owned by `duckdb_20_sqlite_reentry.md`.
- DuckDB-side JSON-as-VARCHAR: `rebuild_convo_search` staging columns,
  snapshot `_build_meta`-adjacent payloads, `agent_traces` /
  `trace_contracts` event payloads, `helpers/graph/analytics.py`
  materialised tables.

**Method** — on the largest real payload table: (a) storage size
text-JSON vs shredded VARIANT; (b) path-query latency `json_extract` vs
variant extraction; (c) Parquet round-trip. Migrate only if ≥2× on the
metric that matters for the reader AND no query-visible roster/semantic
change — otherwise the column ships with an eval-gate bullet
(`helpers/misc/ontology_eval_gate.py`, dry-run vs post-apply).

### 5.2 Measurements (2.0.0, this repo)

Payload: `SELECT context FROM atr.fact_event WHERE context IS NOT NULL
LIMIT 50000` from `memory/data/agent_traces.duckdb` (a DuckDB file —
attach with `(READ_ONLY)`, not `TYPE sqlite`; `at` is a reserved-word
alias → use `atr`). Bench: `$TMPDIR/variant_bench.py`; storage probe
`$TMPDIR/variant_size.py` (20k rows into two scratch DBs, CHECKPOINT
then `os.path.getsize`).

| Metric | JSON as TEXT | VARIANT | Verdict |
|---|---|---|---|
| Path query (`json_extract`) | 28.0 ms | 21.8 ms | VARIANT 1.28× faster |
| Parquet file size | 1,216,240 B | 1,216,834 B | equal |
| Parquet **read** | 40.1 ms | 250.0 ms | VARIANT **6.2× slower** |
| Storage, 20k rows | 5,517,312 B | 9,449,472 B | VARIANT **1.71× larger** |

**2.0 compatibility — the 1.5.4 objection is dissolved.**
`json_extract_string(j, '$.k')`, `json_extract(j, '$.k')`, `j.k` and
`j['k']` all run without error against a VARIANT column on 2.0.0 (they
return NULL for absent keys). On 1.5.4 the same call raised
`InvalidInputException` — that was the breaking change recorded in
§5.3, and it no longer applies. Compatibility is therefore **not** the
reason to decline; storage and Parquet read are.

### 5.3 The recorded 1.5.x verdict (BUNDLE N / N1, 2026-07-27)

`duckdb_improvs.md` "BUNDLE N — New DuckDB 1.5.x features worth
evaluating" and completed.md #72-family entry (**N1**, mirrored at
completed.md:874): evaluated 2026-07-27 on 1.5.4 — VARIANT was
2.6–3.1× **slower** than TEXT + `json_extract_string` across 100k–500k
rows (venture projection 18.5 ms vs 49.0 ms; edition over a full scan
14.5 ms vs 45.7 ms), the raw scan alone was 5.6× slower (0.9 vs 4.9 ms)
because per-value type metadata costs more than extracting from TEXT,
and `json_extract_string` failed on VARIANT columns. Verdict: NOT
ADOPTED, revisit only if a future version closed the perf gap AND a
read-heavy long-tail key case appeared.

The 2.0.0 re-check above closes half of that: compatibility is fixed,
path extraction is modestly faster, but storage (+71%) and Parquet
read (6.2×) still fail the ≥2× bar — and see §5.4 for why the
addressable surface is small.

### 5.4 Why the surface is tiny post-L2

- The per-read `json_extract_string` surface shrank to **one key**
  (`venture`, in the `jv_partners` + `JvWith` arms) over 25 `jv_with`
  edges, of which 2 carry the key.
- `year` already moved to a typed column (L2); `edition` (38.9% of rows)
  is write-only provenance never read via `json_extract`.
- Net: even a zero-cost migration would optimise ≈2 reads out of 3,507
  edges.
- The TEXT path is depended on in code — `helpers/graph/analytics.py:200`
  and `:227` (`json_extract_string(ge.properties, '$.n_quotes')`),
  `helpers/graph/query.py:2569` (`year`), `:3426` / `:3666`
  (`venture`) — and F4 doctrine pins JSON extraction DuckDB-side
  (`tests/test_graph.py:928-1079`).

### 5.5 Decision

**Not adopted.** Keep JSON-as-TEXT (`VARCHAR` + `json_extract_string`)
on every DuckDB-side column; no schema change, no migration, no
eval-gate bullet needed. Resolved as "evaluated, not warranted —
recorded" per the proposal's §4.

Addendum (2026-10-23, after reading the upstream blog line): VARIANT
descends from the same shredding work as `read_json` (2023-03-03
"Shredding Deeply Nested JSON, One Vector at a Time") — typed columnar
access to semi-structured data. But the 2026-10-16 follow-up "From JSON
Strings to Typed Columns" recommends, for our exact shape (JSON-as-text
column, few stable hot keys), the same move our own L2 already made:
materialize the hot keys ONCE as real typed columns
(`json_group_structure` → `json_merge_patch` type overrides →
`json_transform` → `unnest`), raw document left in place for
provenance. Its words: "the type of the raw column matters much less
than whether you extract the fields at all... Turning it into typed
columns once does that work a single time", and the `JSON` type "is
stored exactly like a `VARCHAR`, so there is no difference in size or
scan speed". Typed columns beat VARIANT on every metric we measured —
zero storage overhead (VARIANT cost 1.71×), no Parquet read penalty
(6.2×), full compression, no 2.0 dependency. VARIANT is the right tool
only where the key set is too volatile to materialize; our long-tail
keys are write-only provenance we never read, so there is nothing for
it to accelerate. Reopen trigger refined: a hot key set too unstable
for L2 materialization AND at a scale where repeated `json_extract` is
visible (>100k-row read-heavy payloads, e.g. a future hot path over
`fact_event.context`).

## 6. Decision & revisit triggers

Keep the engine split: **SQLite (FTS5 + sqlite-vec sidecar) owns
search; DuckDB owns set-SQL analytics over materialised caches** —
consolidation target remains the single shared SQLite embed store
(`memory/embed_store.db`).

Re-evaluate only if:

1. the corpus reaches O(100k+) indexed notes (brute force stops being
   ms-scale) AND upstream `vss` ships a working COSINE opclass on our
   pin; or
2. a filtered-ANN join need appears that SQLite-side Python scoring
   can't express; or
3. DuckDB `fts` gains FTS5-parity features (snippet/highlight, weighted
   bm25, phrase syntax); or

   > **Re-checked 2026-10-30 (DuckDB 2.0.0, `INSTALL fts; LOAD fts;`)** —
   > the parity gap NARROWED but trigger 3 still does not fire. Now
   > present: `stemmer='porter'`/'`english'` (probe: unstemmed
   > `foxes→[4]` becomes `foxes→[1,4]`; stemming is live), per-column
   > `fields:='title'`/`'title,body'` scoping, `match_bm25(k:=1.2,
   > b:=0.5)` weights, `stopwords='english'`, `strip_accents=1`,
   > `lower=0`, `cluster_terms=1`, `layered_search=1`, tokenizers
   > `regex`/`opensearch_standard` only (`'simple'` → Invalid Input
   > Error). Still missing: `snippet`/`highlight` (Catalog Error: no
   > such scalar function), phrase and boolean operators are SILENTLY
   > IGNORED (`"world peace"`, `world AND peace`, `world OR peace`
   > return identical results to bare `world peace`), prefix wildcard
   > `wor*` returns nothing, and `incremental=1` is refused while an
   > index exists ("a FTS index already exists") so rebuilds stay
   > full-overwrite. FTS5 on the same corpus returns a true phrase
   > (`"world peace"` → row 1 only), distinguishes `OR`, and matches
   > `wor*`. Verdict: keep the split; next re-check when `fts` ships
   > phrase syntax or snippet.
4. **Quack** gains search-grade latency or the split's own read path
   moves server-side — note trigger #4 of the 2026-08-27 note ("Quack
   ships production") **already fired** and was executed as the
   server-per-store integration (#428); the search-store question was
   re-examined in §4 and stayed SQLite; or
5. a **read-heavy, large VARIANT-shaped column** appears: single-pass
   path extraction over a payload where the 1.28× extraction win and
   path pushdown dominate, and the 1.71× storage / 6.2× Parquet-read
   costs are acceptable for that table; or
6. a DuckDB release closes the VARIANT storage and Parquet-read gaps
   (re-run §5.2 before believing it).

## 7. Provenance

- 2026-08-27 note: engine assessment + 1.5.5 Quack pilot (pilot scripts
  were ephemeral under `/tmp/quack_pilot/`; all numbers inline above).
- 2026-10-21: `duckdb_20_variant.md` filed as part of the duckdb_20_*
  split; Slice A Quack re-pilot against 2.0.0 (proposal sibling
  `duckdb_20_quack.md`).
- 2026-10-22: VARIANT re-check (§5.2) on 2.0.0; both documents merged
  here and the proposal resolved as deferred (completed.md #430).
  Measurements reproducible from `$TMPDIR/variant_bench.py` and
  `$TMPDIR/variant_size.py`; the live quack re-pilot driver is
  `$TMPDIR/opencode/quack_trial/run_trial.py`.
