---
title: "Convo search — pointer-indexed search over all harness histories"
status: executed
filed: "2026-09-27"
executed: "2026-09-27"
completed_md: "304"
area: "helpers/maintenance (harvest+rebuild+gc), helpers/misc (convo_query+note_query), memory/data/harness/<harness>/conversations (corpus parquet zstd), memory/convo_search.duckdb + _fts.db (index)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Convo search — pointer-indexed search over all harness histories

**Date:** 2026-09-27 · **Status:** EXECUTED 2026-09-27 (completed.md #304 —
S1-S4 landed, S5 recall augmentation left consumer-gated) ·
**Area:** corpus under `memory/data/harness/<harness>/conversations/`
(parquet zstd), index `memory/convo_search.duckdb` + `convo_search_fts.db`, machinery in
tracked `helpers/` (naming in tune with the doc_search family).

## 1. Motivation (operator directive, 2026-09-26/27)

Conversation history across the three harnesses (opencode, prime-rlm,
zcode) is the most valuable piece of memory: user inputs, agent/model
reasoning, decisions, and context that exists nowhere else. The
sources are UNRELIABLE BY DESIGN — opencode deletes old messages (the
db shrank 825→216 MB between snapshots), prime purges sessions after
upload, zcode logs rotate. **We therefore own an extracted corpus and
never rely on live db rows at query time.** The one-time union
survives in the timeshift snapshots + the pre-redaction backup; the
durable fix is a canonical corpus + a search surface like
`doc_search`/`note_search`, so recall questions get instant answers.

**Hard constraints (operator, all incorporated):**
1. Separate store from agent_traces (own db, own lifecycle).
2. The index db holds POINTERS, not payloads — GBs stay in files.
3. Corpus splits across the existing `memory/data/harness/<harness>/`
   namespace (opencode/prime-rlm/zcode).
4. **Corpus = parquet (zstd)** — DuckDB opens them directly, no
   extraction-copy step between corpus and query.
5. **Arrow everywhere in-memory** (house mandate): the pipeline runs
   Arrow tables end-to-end; DuckDB↔Arrow zero-copy; parquet written
   via the Arrow writer.
6. **Every stage incremental** — harvest, index, and embed all
   watermark-delta like `make search-fresh` (check mode + APPLY=1).
7. Machinery named in tune with the search family:
   `helpers/maintenance/harvest_conversations.py` (sources → corpus),
   `helpers/maintenance/rebuild_convo_search.py` (corpus → index,
   `--check`/APPLY semantics), `helpers/misc/convo_query.py`
   (query CLI, the doc_query/script_query/gate_query pattern).

## 2. Source inventory (measured 2026-09-26/27)

| Harness | Sources | Notes |
|---|---|---|
| opencode | live opencode.db (message+part) + 7 timeshift copies + pre-redaction agent_traces fact_event snapshots | snapshots recover deleted conversations |
| prime-rlm | live sessions/*.jsonl (16) + snapshot copies + logs | purged sessions recoverable from the union |
| zcode | rollout/model-io-*.jsonl (109 MB full trajectories) + snapshots + db.sqlite | fullest-request-per-session wins (earlier contexts are subsets) |

Load order: oldest snapshot → newest → backup duckdb → live; dedup on
(harness, part_id), newest wins. Post-restore snapshot re-harvest is
manual.

## 3. Design

### 3.1 Corpus layer — parquet zstd, per session, immutable

```text
memory/data/harness/<harness>/conversations/<session_id>.parquet   (zstd)
```

Arrow schema (one row per part, columns):

| column | type | notes |
|---|---|---|
| part_id | string | PK with harness; dedup anchor |
| message_id, session_id | string | linkage |
| ts | timestamp | parsed from source |
| role, agent, model | string | |
| part_type | string | text/reasoning/tool/patch/diffs/req-msg/response |
| text | string | FULL payload, uncapped — the archive of record |
| meta | string (json) | tool state, truncation flags, source-line info |
| source | string | snap:…/backup:…/live + watermark line refs |

Written by the Arrow parquet writer (zstd 3); text compresses ~5-10×
(the 15.5 MB diff rows shrink accordingly). DuckDB reads the corpus
directly via glob — the index never copies payloads.

### 3.2 Index layer — pointers + digests + vectors

`memory/convo_search.duckdb`, table `convo_search`:

```sql
CREATE TABLE convo_search (
  harness TEXT, session_id TEXT, part_id TEXT,
  file_path TEXT, row_no BIGINT,         -- THE POINTER
  ts TIMESTAMP, role TEXT, agent TEXT, model TEXT, part_type TEXT,
  text_len BIGINT,
  snippet TEXT,                          -- capped digest (CONVO_SNIPPET_CAP, 8 KiB default)
  embedding FLOAT[],                     -- 384-d granite lane (3.3)
  PRIMARY KEY (harness, part_id)
);
CREATE TABLE convo_meta (key TEXT PRIMARY KEY, value TEXT);
-- file:<relpath> → blake2b(content) per corpus file, plus embed_model/embed_dims
```

`FLOAT[]` (not a fixed `FLOAT[384]`) so the degraded pseudo-embedder path
can store its own width; `embed_dims` in `convo_meta` records the width
actually stored and the query leg casts to THAT width — a hardcoded 384
breaks pseudo mode. File hashes live in `convo_meta`, one row per corpus
file, rather than per index row: the same staleness answer with 140 rows
instead of 52k.

`part_id` is only unique WITHIN a harness, so every key path — FTS
delete, row delete, row fetch, RRF fusion — is the composite
`(harness, part_id)`. `harness` is an UNINDEXED column in the FTS
table, present purely to rebuild the composite key.

Rebuildable from corpus alone (payloads never enter the db).

### 3.3 Embeddings — model, cost, speed (TRIAL-measured 2026-09-27)

- **Model: the house granite-embedding-97m-r2, 384-d** — same lane as
  note_search. Deliberate: SAME VECTOR SPACE = conversations and notes
  are cross-searchable, and `embed_cache (text_hash, model)` reuse
  comes for free.
- **Embedding unit**: the snippet digest capped at 8 KiB; trial
  corpus avg 1.0 KiB/part.
- **Speed (trial-measured on real conversation parts, n=3.8k)**:
  - per-text and batched CONVERGE at ~3-5 texts/s on 1 KiB texts —
    the llama.cpp batch advantage (4.5x on small note sections)
    vanishes at conversation sizes; either lane is fine, per-text is
    simpler and is what the worker pool already runs.
  - **Cold full corpus (est. 100-150k parts)**: single-thread ≈ 6-10 h
    → run through the pinned 4-worker pool lane (house-measured
    47.7/s on note texts; conversation-sized texts project to
    **~1.5-3 h one-time**), resumable via embed_cache checkpoints —
    an overnight job.
  - **Incremental: seconds-class, trial-verified** — live deltas of
    2-26 parts embedded in 2-15 s including model load.
  - **Retrieval latency (trial)**: FTS5 ~1 ms; cosine linear scan
    6-17 ms @ 3.8k rows → sub-second brute-force at 150k rows; no
    ANN needed at this scale.
- If the cold window ever matters, the fallback is a MiniLM-class
  faster model in a SIDE table — rejected by default for vector-space
  parity.

### 3.4 Incrementality — the search-fresh contract everywhere

- **Harvest**: per-source watermarks in the index meta table —
  opencode `part.time_updated` high-water mark per db (parts are the
  indexed unit), prime session-file mtime+size, zcode rollout
  mtime+size per session. Watermarks and the part registry are STAGED
  in duckdb and committed only after the parquet flush succeeds: a crash
  must never mark a source quiet while its rows were never written.
  `--check` opens duckdb READ-ONLY (a check that CREATEs tables takes
  the write lock and collides with a running rebuild). Unchanged
  sources are not re-read; changed sessions produce only their delta
  rows.
- **Index**: per-parquet-file `mtime + blake2b` (the corpus.py /
  note_search_meta pattern); unchanged files skipped; changed files
  re-index their delta by part_id already present.
- **Embed**: (text_hash, model) cache; only new digests hit the model.
- **Entry point**: `make convo-fresh` mirroring `make search-fresh`
  (`--check` default exits 1 on drift; `APPLY=1` refreshes). Joins the
  advisory search-fresh sweep once S4 lands.

### 3.5 Query surface

- `helpers/misc/convo_query.py` — hybrid rank (SQLite FTS5 BM25 over
  snippet + cosine over embedding in convo_search.duckdb, RRF fuse —
  the exact trial-verified split; DuckDB's own FTS stays out, it is
  experimental and not in-stack); snippet-first results, full body
  read from the parquet pointer on expand.
- `search_tui` lane (S3): convo is the FOURTH index lane (keys 1-7:
  docs, scripts, notes, **convo**, code, rg, reports) — index lanes
  stay grouped, so code/rg/reports shift 4→5, 5→6, 6→7. Hits are
  pointers, not text files: the results row shows
  `harness/role/ts`, the preview pane expands the parquet row to the
  full body, and `enter` COPIES the pointer (there is no editor for a
  parquet). The index monitor's auto-rebuild list deliberately does NOT
  include convo — a cold rebuild is an hours-long embed, and
  freshness is the advisory gate's `convo-fresh` job, not the
  keystroke's.
- Recall augmentation (top-k context injection) stays consumer-gated
  (S5).
- **FTS5 implementation notes (trial-hardened)**: queries must be
  token-quoted — `"near" "dup"` — bare hyphens parse as FTS5
  column-filter syntax (`no such column: dup`); FTS5 `rank` is
  lower-is-better (bm25), so `ORDER BY rank` asc. A full rebuild must
  `DELETE FROM convo_fts` before re-inserting — the FTS sidecar has no
  uniqueness constraint, so a second full rebuild would otherwise
  duplicate every lexical row.
- **Namespace**: the corpus directory IS the harness namespace
  (`memory/data/harness/<harness>/conversations/`), never a column —
  one file cannot span harnesses, and renaming a lane is an `mv`.
- **Rank prior + `--kinds` (added after the hypergraph demo)**: raw
  RRF put a library import above the assessment memo it fed, because
  tool traffic is ~30k of 53,423 rows — and it is NOT one part_type:
  assistant tool CALLS are `part_type='tool'` (17,049) while their
  OUTPUT arrives as `role='toolResult', part_type='text'` (12,622).
  A prior keyed on `part_type` alone therefore demoted nothing (caught
  by running it against the live index, not by the tests). Both columns
  now decide: tool-ish rows × 0.45, everything else 1.0. It is a nudge
  because a tool row is often the best answer for a query *about* code
  (a grep hit found by meaning) — `--kinds` is the hard lever, and it
  accepts both part_types and roles, so
  `--kinds user,assistant,reasoning` is "conversation only, no tool
  traffic". Effect on `? hypergraph`: rank 1 went from a
  `BashResult(import hypergraphx…)` dump to the reasoning trace, and
  the operator's own question moved to rank 2.

### 3.6 Trial evidence (2026-09-27, live sample)

Setup: throwaway trial (`/tmp/opencode/convo_trial.py`, not repo
code) harvesting 3.8k parts / 3.9 MB text from the LIVE sources
(3.4k opencode message×part, 296 prime, 153 zcode rollout) — avg
1.0 KiB/part; granite embeds; SQLite FTS5 (porter) sidecar + DuckDB
cosine over registered Arrow table; six known-answer queries drawn
from this session's own history (D17/AGPL, near-dup ceiling, CSR
gate, MIN-claimant parity, anchored layout, WF scaling).

| Leg | Measured | Verdict |
|---|---|---|
| Embed speed (1 KiB texts) | per-text ≈ batch ≈ 3-5/s | batch advantage vanishes on conversation sizes (it was a small-section-text effect); per-text is simpler and matches the pool shape |
| Cold corpus projection | single 6-10 h → pool lane ~1.5-3 h | overnight job, resumable |
| Incremental delta | 2-26 live-new parts embedded in 2-15 s | seconds-class, verified against sources growing DURING the trial |
| FTS5 latency | ~1 ms | free |
| Cosine latency | 6-17 ms @ 3.8k rows, linear scan | sub-second brute-force at 150k — no ANN needed |
| Quality | cosine exact known-answer top-1 on 5/6 (cos 0.82-0.90); 6/6 topically right | viable |

Findings worth keeping:

- **Hybrid earns its keep — the lanes are complementary.** Q1
  (igraph/AGPL): cosine retrieved the user's original license note and
  the reasoning that echoed it; FTS retrieved the D17 decision-log
  execution blocks. RRF fuse surfaced both families.
- **Cosine carries semantic recall** ("generation gate freshness
  fallback" ↔ "0.17s build, checksum verified, generation-fresh");
  **FTS carries lexical precision** (exact ids/terms like MIN-claimant
  pull the procedural blocks). Neither subsumes the other.
- **Self-referential retrieval confirmed incrementality end-to-end**:
  the live session kept appending parts during the trial; delta-embed
  picked them up (2-15 s) and they ranked — including this very
  conversation's replies appearing in results as they were written.
- FTS5 gotchas above (token-quoting, rank direction) cost two reruns —
  design notes now, not rediscoveries.

### 3.7 Cold-run evidence (first full build, 2026-09-27)

The first real run replaced the trial's projections with measurements.

| quantity | measured |
|---|---|
| corpus | 141 files, 96,917 rows, ~35 MB parquet (opencode 110, prime-rlm 15, zcode 16) |
| indexable rows (text, `step-*` excluded) | 68,155 → **52,706** after T2 (53,423 by the end of the session — the live opencode session indexes its own transcript, so the count only rises) |
| indexable rows by part type | text 27,977 · tool 16,649 · step-finish 15,449 · reasoning 7,411 · req-msg 669 |
| distinct 8 KiB digests | **46,976** — i.e. 21,179 duplicate rows; the top digest (`tool-calls`) repeated **14,375×** |
| embed work, cold | 15,488 cache hits + **52,667 misses**, 2h10m at ~6.7 texts/s through the 4-worker pool |
| embed work, after T1+T2 | **454 unique texts** for the same corpus (everything else cached, repeats collapsed) |
| artifacts | `convo_search.duckdb` 335 MiB, `convo_search_fts.db` 88 MiB |

The 15,488 + 52,667 = 68,155 identity is the answer to "why isn't the
miss count the row count": the bulk path counts model calls, not rows,
and the cache is shared with the note/doc/script cohorts — 15,488
conversation snippets were already embedded by an earlier index.

The duplicate structure is the more useful finding: a quarter of the
indexable corpus is protocol bookkeeping, and the single most repeated
"text" in a year of agent history is the word pair `tool-calls`. Both T1
and T2 exist because of it, and both are shared-code wins — the same
dedup applies to `rebuild_note_search` and `helpers/graph/embeddings.py`.

### 4.6 Performance forensics — three defects, one question

T6 and T8 both came from the same operator question: *"`make convo-fresh
APPLY=1` is taking 4+ minutes — is that normal?"* It was not. The
method is worth keeping: **measure the phase, then bisect it on a
rollback**, because every one of these looked like "the index is just
slow" and none of them were where the time was going.

| defect | symptom | cause | cost | after |
|---|---|---|---|---|
| T6 | 45–82 min *after* the last vector, 85% CPU, 1.8 GB RSS | 52.7k row-by-row `executemany` binds, each a 384-float list | ~45 min | **2 min** (Arrow bulk load) |
| T8a | 137 s of a 3m24s incremental run | FTS `DELETE` per key; `harness`/`part_id` are UNINDEXED, so every statement scanned 53k rows | 68.8 ms/key | one statement per 400-key chunk |
| T8b | the rest of the 3m24s | re-inserting ~6.9k *unchanged* rows: FTS de-tokenizing text that had not changed | 11 s | untouched; delta-only |

The measurement that settled it (on a rolled-back transaction, so the
live index was never touched):

```text
DELETE per (harness, part_id) x2000 :   7.16s  (3.6 ms/key)
DELETE by file_path (1 statement)    :   0.04s
FTS DELETE per key x2000             : 137.67s  (68.8 ms/key)
FTS bulk INSERT of 6878 rows         :   0.41s  (0.1 ms/row)
DuckDB bulk INSERT of 6878 rows      :   1.11s
```

**The compaction catch** (operator, 2026-09-27): delta-only rests on
"the writer only appends". A harness *compaction* breaks that — parts
get dropped or reordered, so every subsequent part keeps its id and text
but **moves to a different `row_no`**, and the pointers are row numbers.
Comparing only `(snippet, text_len)` would skip those rows and leave
pointers resolving to the *wrong part*: silent corruption, invisible to
every count-based check. The fingerprint therefore includes `row_no`, so
a compaction flags the tail of that file as changed and re-indexes it.
Correct, and cheap because compaction is rare — tolerated skew, on
purpose. `test_compaction_reshifts_row_no_and_pointers_stay_correct`
pins it, and it was verified to FAIL when `row_no` is removed from the
fingerprint (a guard test that cannot fail is worthless).

**Timings are now first-class output** (T9). A single total hides
exactly this class of defect, so both freshness targets report per-phase
elapsed and any slowdown is visible without a profiler:

- `rebuild_convo_search` — `phases: hash+read=… embed=… duckdb_delete=…
  duckdb_insert=… fts_sync=… meta=… total=…`
- `harvest_conversations` — `lanes: opencode=… prime-rlm=… zcode=… flush=…`
- `make convo-fresh` / `make search-fresh` — per-command `took NNNms`

A healthy incremental run looks like this, and any regression stands out
against it:

```text
convo_search: 24 parts indexed (incremental, total 53784)
  phases: hash+read=0.49s embed=8.03s duckdb_delete=0.0s
          duckdb_insert=0.08s fts_sync=0.02s meta=0.67s
    took 10216ms
```

Embed is now the dominant term, which is the correct place for it to be:
it is the only phase that does real work per new text.

## 4. Slices

- **S1 — Harvester**: `harvest_conversations.py` — union sources →
  per-session parquet corpus (Arrow writer, zstd). Fixes v1 spike's
  `info.time`-dict bug; no index yet.
- **S2 — Index builder**: `rebuild_convo_search.py` — pointer table +
  snippets + FTS + embeddings, watermarked incremental, `--check`
  mode; the one-time cold embed runs here (~1.5-3 h via the pool
  lane, resumable via cache).
- **S3 — Query**: `convo_query.py` + search_tui lane.
- **S4 — Fresh gate**: `make convo-fresh` (check + APPLY=1) wired into
  the advisory sweep.
- **S5 — Recall augmentation**: consumer-gated context injection.

### 4.1 Tasks — index hygiene (measured on the first cold run, 2026-09-27)

The first full build surfaced three hygiene defects. All three are
wasted model calls or wasted bytes, and all three would recur on every
rebuild, so they are tasks, not one-off patches.

| # | Task | Why (measured) | Done when |
|---|---|---|---|
| T1 | **Dedup model calls WITHIN one `cached_embed_batch` call** — `helpers/core/embed_cache.py` | the bulk path only saw rows committed by EARLIER calls, so identical texts in the SAME call each paid a model call. The convo corpus has 21,179 duplicate rows over 46,976 distinct 8 KiB digests; one digest ("tool-calls") repeated **14,375 times**. Cold build paid ~52.7k calls where ~47k distinct (or fewer) suffice — roughly 20 min of the 2 h run | `stats["unique_misses"] < stats["misses"]` on a corpus with duplicates; progress meter reads `N/M unique`; one model call per distinct digest |
| T2 | **Skip protocol-marker parts in the INDEX** (`SKIP_PART_PREFIXES = ("step-",)` in `rebuild_convo_search.py`) — corpus unchanged | `step-finish` parts are 15,449 opencode rows whose entire text is `tool-calls` or `stop` (0.1 MiB) — indexing them embeds the word "stop" 1,044 times and adds pure noise to lexical recall. The corpus KEEPS them (archive of record); only the index filters | indexable rows drop 68,155 → ~52.7k with no loss of real text; `step-finish` count in `convo_search` is 0 while the parquet still has all 15,449 |
| T4 | **Commit the embed cache per chunk, not once at the tail** — `helpers/core/embed_cache.py` | `cached_embed_batch` ran every chunk, then did ONE `INSERT OR REPLACE` + commit after the loop. The first cold run held 52,667 vectors in RAM for 2h10m and wrote them in a single transaction at the end: a crash (machine or pool) at minute 119 loses every vector, so the "resumable via cache checkpoints" claim was false WITHIN a build (still true across builds) | each 512-text chunk commits as it completes; a crash costs ≤1 chunk; progress meter and `stats["dirty"]` stay per-chunk |
| T5 | **Register the convo artifacts in `db-backup/`** — `db_maint.py` `_backup_convo_search` (index pair) + `_backup_convo_corpus` (tar.zst) + `_backup_memory_sidecars` (catch-all sweep), all called from `run()` beside `_backup_corpus` | a rebuild of the index pair re-inserts 50k rows and re-tokenizes 35 MB of FTS text, and a CLEAN rebuild re-embeds for 2h10m (measured). 2h+ runs deserve a recovery point. The corpus is a different class again: no rebuild path at all, since the sources it archives are exactly what the harnesses delete. And the audit behind it found ~44 MB of `memory/`+`memory/data/` artifacts with NO registration at all — a coverage gap, not a judgement | `make maint` writes `convo_search_backup.duckdb.zst`, `convo_search_fts_backup.db.zst`, `convo_corpus_backup.tar.zst`, `memory_sidecars_backup.tar.zst`; the restored duckdb opens and each tar lists its members; secrets/transient/oversize are skipped WITH a log line; a locked/absent source logs and skips (never fails the run). Extracted `_duckdb_zstd_backup` so the graph.duckdb backup and this one share the checkpoint-then-copy routine. **DONE (2026-09-27):** exercised on live data — `convo_search_backup.duckdb.zst` 400.3 MiB (restores 824 MiB, 53,423 rows, 53,423 vectors, 3 harnesses — byte-size identical to live), `convo_search_fts_backup.db.zst` 41.6 MiB, `convo_corpus_backup.tar.zst` 33.2 MiB (141 parquet), `memory_sidecars_backup.tar.zst` 35.3 MiB (34 files incl. the prime-rlm `memory_trail.md` + pre-consolidation tarball). Cost worth knowing: the index backup is 400 MiB per run because it carries the 53k×384 vectors, and it is rewritten every `make maint` |
| T6 | **Bulk-load the index instead of 52k `executemany` row binds** — `rebuild_convo_search.py` | the write leg is the real cost of a rebuild, not the embed: the first cold run spent ~45 min AFTER the last vector was computed, compute-bound at 85% CPU and 1.8 GB RSS, inserting 52.7k rows one Python list at a time (384 floats each). The 8 KiB snippets make the payload ~35 MB, so this is a conversion cost, not an IO cost | rows go in as Arrow/parquet via `con.register` + `INSERT … SELECT` (or a single `append`); the write leg drops from tens of minutes to seconds, and the RAM high-water mark stops scaling with corpus size |
| T8 | **Incremental cost must scale with the DELTA, and the pointer fingerprint must include `row_no`** — `rebuild_convo_search.py` | an incremental rebuild re-indexed every row of every CHANGED corpus file. The corpus writer rewrites whole session files (only ever appending parts), so the unchanged majority was deleted and reinserted for nothing. Measured on the live index: **3m24s** for one changed file (2,372 rows, **0** embeds). Two stacked O(n²) loops: FTS `DELETE` per key at **68.8 ms/key** (harness + part_id are UNINDEXED in FTS5, so each statement full-scanned 53k rows — 137 s for 2,000 keys), and DuckDB delete per key at 3.6 ms/key against 0.04 s for one by-`file_path` statement | one OR-ed statement per 400-key chunk for both sides (SQLite caps expression depth at 1000); a changed file inserts only parts whose key is new **or whose (snippet, text_len, row_no) fingerprint moved**; deleted files still drop wholesale. Same workload now **8.6-10.2s**, and the cost tracks the delta, not the file |
| T3 | **Evict dead cache rows** — new `helpers/maintenance/gc_embed_cache.py` + `make embed-gc` (check default, `APPLY=1` deletes + VACUUMs) | the shared cache is keyed `(text_hash, model)` with a `source` label, so rows nothing references any more are indistinguishable from live ones and never expire. It is a PURE function of (text, model), so deleting a row can only cost a recompute, never correctness | `--check` reports per-source live/dead and exits 1 when dead rows exist; `APPLY=1` deletes + VACUUMs; a reference index that cannot be read ABORTS instead of emptying the cache. **Applied 2026-09-27: 2,503 of 71,025 rows deleted, 181.2 → 168.5 MiB.** The dead set is index-shrink staleness (doc 1,403 · note 658 · script 402 · convo 4 · company 36) — NOT a big abandoned cohort, which the first survey wrongly suggested |
| T3a | **Reproduce each builder's COMPOSED embed text in the GC** (the bug the first survey exposed) | every legacy index embeds `title\nsection\nbody[:4000]` (doc/script) or `title\nsector\nsection\nbody[:cap]` (note) — NOT its stored column, and the company lane embeds the note text via `_get_company_text(conn, name)`. Hashing the raw `content` column made **23,595 LIVE rows look dead** and would have forced a re-embed of every note/doc/script vector | each `Ref` carries its own text basis (imported from the builder, never re-derived by hand); `test_gc_reference_text_basis_matches_the_builders` pins the compositions; a per-source coverage gate marks any source UNVERIFIED — reported, never deleted — when its references cover <25% of its own rows or no reference index claims its label |
| T7 | **Notes query core moves OUT of the TUI** — `helpers/misc/note_query.py` is the canonical surface; `search_tui` lane 3 is a thin adapter | notes were the one index with no CLI: both query legs lived in `search_tui.py` as lane adapters, so an agent session could not reach them at all and neither could a script. TUI is a usability shell — the core (legs + fusion) belongs outside it, or the next consumer forks it again. **DONE (2026-09-27):** the staleness-gated matrix loader, both legs and the fusion now live in `note_query`; `search_tui` keeps only `_note_hit` mapping and shed 100 lines (and its private sqlite connection, `lru_cache`, `threading` and `numpy` imports). The one real semantic difference is a flag, not a fork: the CLI fuses per SECTION (default, exact `path:line`) and `--per-note` collapses to the best section per note, which is what the lane displays | 14 `tests/test_note_query.py` cases (fts_safe degradation, bm25 ranking, pointer↔anchor correctness, per-section vs per-note fusion, prose inheritance, stale-matrix fallback, CLI shapes); 75 lane tests + live lane output unchanged |

Guard rails for T3: the GC deletes only from `embed_cache` (never
`note_search_vec*`, which hold the real vectors), and it aborts if any
reference index (doc/script/note/convo) is missing or unreadable —
a half-visible reference set would otherwise delete live rows.

### 4.2 Wrap-up tasks (from the same run)

| # | Task | Done when |
|---|---|---|
| W1 | `mv memory/data/harness/prime → prime-rlm` (the lane was renamed; the corpus dir was not, and an empty `prime-rlm/` now exists from a post-rename harvest) | no `prime/` dir; `convo_search.harness` has no `prime` rows; `--harness prime-rlm` returns hits |
| W2 | One full rebuild on the fixed builder (T1+T2) — the embed cache makes it minutes, not hours | `convo_search` row count == the new indexable count; `embed_cache` gains one row per distinct digest only |
| W3 | Validate on real data (synthetic tests cannot): 6 known-answer queries, pointer expansion on a real session, `--check` clean, `make convo-fresh APPLY=1` idempotent (second run: 0 new) | recorded in this proposal's evidence section |
| W4 | `make qa` once, then flip proposal status | green gate; status no longer `proposed` |

### 4.3 Backup surface — what is actually covered, and by what

Audited 2026-09-27 while wiring T5. The name "snapshot" means three
different mechanisms in this repo, and only two of them are ours:

| mechanism | captures | git |
|---|---|---|
| `snapshots/parquet/**`, `snapshots/hif/**` (`make snapshot`) | per-table parquet exports of `memory/research.db` + `memory/graph.duckdb` ONLY | tracked |
| `db-backup/*.zst` (`db_maint.py`, `snapshot_db.py`, `rebuild_common.backup_last_good_index`) | byte-exact zstd recovery copies | ignored |
| `/mnt/store/timeshift/snapshots/` | whole-home filesystem snapshots | external, out of our domain |

`memory/` and `memory/data/` inventory, by recovery class:

| artifact | size | `db-backup` | `snapshots/parquet` | if lost |
|---|---|---|---|---|
| `research.db` — analytics + `note_search` + all graph tables | 307 MB | yes | yes (every table) | restorable from parquet |
| `embed_store.db` — `note_search_vec` + `embed_cache` | 180 MB | yes | vec tables only | note vec: rebuild; **cache: re-embed cost** |
| `convo_search.duckdb` (+ `.wal`) | ~100 MB | **T5** | no | rebuild from corpus (2h if the cache is gone too) |
| `corpus.db` — vault frontmatter cache | 53 MB | yes | no | one `findata` walk |
| `graph.duckdb` | 39 MB | yes | yes | restorable |
| `embed_matrix.f32` / `.json` — note semantic matrix | 26 MB | no (derived) | no | rebuilt lazily on first semantic note query |
| `doc_search.db` | 23 MB | yes (last-good) | no | rebuild |
| `data/sources.duckdb` | 42 MB | yes | no | **no** — fetched, not derivable |
| `data/agent_traces.duckdb` | 18 MB | yes | no | **no** — pre-redaction event log |
| `data/harness/*/conversations/*.parquet` — convo corpus | 35 MB | **T5** | no | **no** — the harnesses deleted the sources |
| `convo_search_fts.db` | sidecar | **T5** | no | re-tokenized from the index |
| `script_search.db` | 3.3 MB | yes (last-good) | no | rebuild |
| `data/model_usage.duckdb` | 1.8 MB | yes | no | **no** |
| `md_lint_cache.db`, `graph_layout.json`, `*_cache.json` | ~8 MB | no (derived) | no | rebuild |

Two findings worth keeping, because they are not obvious from the file
names:

- **There is no `memory/note_search.db`.** Notes are a TABLE inside
  `memory/research.db` (`note_search`, 17,237 rows + its FTS5 shadows),
  and the note VECTORS live in `memory/embed_store.db` as the vec0
  table `note_search_vec` with `note_search_vec_rowids` mapping back.
  That split is the only reason `embed_store.db` is a separate 180 MB
  backup: a `research.db` restore alone brings back the text and not the
  vectors.
- **The three irreplaceable artifacts in this neighbourhood are
  `data/agent_traces.duckdb`, `data/sources.duckdb` and the convo
  corpus** — all under `memory/data/`, all already carrying history the
  producing system no longer has. Two of the three were already backed
  up; the corpus is what T5 adds.

### 4.4 The catch-all, and what it must refuse

The thesis is coverage, not a curated list: **everything under
`memory/` and `memory/data/` belongs in `db-backup/`.** The named
routines above exist because their engines need WAL-consistent
(SQLite online backup) or checkpointed (DuckDB) copies; the long tail
does not, so `db_maint._backup_memory_sidecars()` sweeps it into one
`memory_sidecars_backup.tar.zst` (~44 MB raw: embed matrix, layout
JSON, fetch caches, worklists, raw ingest drops, CSR artifacts).

A catch-all is only as good as its refusals, so the deny-list is
explicit, coded, and LOGGED every run (a skip that is invisible is
indistinguishable from a gap):

| denied | patterns | why |
|---|---|---|
| **secrets** | `.env`, `*svc_account*`, `*credential*`, `*secret*`, `*token*`, `*.key`, `*.pem` | `memory/` holds live GCP credentials. A backup is not a vault: the sidecar tar is one opaque blob that gets `zstd -dc`'d and copied around, so multiplying secrets into it is strictly worse hygiene than leaving a discrete file where the operator put it. Recovery value of a service-account key in a backup ≈ 0 |
| **transient** | `*-wal`, `*-shm`, `*.lock`, `*.tmp` | not state. A WAL without its database is inert, and each artifact that owns a WAL already has a routine that merges it (online backup / CHECKPOINT). A restored `*.lock` can even block the next writer |
| **duplicates of a named routine** | research/graph/embed_store/corpus/doc/script/convo index+fts, `data/{sources,agent_traces,model_usage}`, `data/harness/` | two recovery points for one artifact can disagree, and restoring the sweep would overwrite a newer dedicated copy with an older one. The corpus keeps its own per-file tar for the same reason (35 MB is not worth storing twice) |
| **oversize** | any single file > 512 MiB | the sweep must not become the way a data lake gets tarred. Skipped WITH a log line, so it reads as a coverage gap rather than passing unnoticed — and never a backup failure |

Everything else is included on purpose, including things that are
merely "derived, rebuildable": `embed_matrix.f32`/`.json` (26 MB, needs
the embedder to regenerate), `graph_layout.json`, `md_lint_cache.db`,
`csr/`, and the rate-limited fetch caches
(`yf_relations_fetch_cache.json`, `wikidata_qids_cache.json`) whose
re-fetch costs quota, not just time. Cheap-and-derivable is not a
reason to leave a gap; that judgement belongs to the daily `make maint`
run, not to a list that quietly rots.

### 4.5 Restore: which class each artifact recovers through

There is an automated restore, and it covers exactly one class:

- **`make snapshot-restore`** (`snapshot_db.py --restore --force`)
  rebuilds `memory/research.db` + `memory/graph.duckdb` from the
  git-tracked parquet in `snapshots/parquet/`. That is the versioned,
  clone-portable path — and it covers NOTHING convo, by design: the
  corpus and the conversation text are the private-content class the
  git snapshot deliberately excludes.
- **`db-backup/*.zst`** is the local byte-exact class, and its recovery
  is a documented one-liner, not automation. `db_maint` is
  backup-only by construction; there is no `restore` step anywhere in
  it, and adding one would be a false promise (a restore that clobbers
  a live DB needs the same `--force` + clobber-warning care as
  `snapshot-restore`).

Consequences worth stating plainly, because they decide what you do when
the machine dies:

- Losing the index costs a rebuild, and the corpus makes that cheap —
  but ONLY if `embed_store.db` came back too, otherwise it is 2h10m of
  re-embedding (which is the whole reason T5 exists).
- Losing the corpus on a machine that also loses `db-backup/` is
  **unrecoverable**, and no automation can save it: the sources it
  archives are the histories the harnesses deleted. `data/agent_traces.duckdb`
  and `data/sources.duckdb` are in the same class today — backed up,
  restore-by-hand, and not covered by `snapshot-restore`.

Recovery one-liners for the new artifacts (arcnames are relative to
`memory/` for the sweep, `memory/data/` for the corpus tar):

```bash
zstd -dc db-backup/memory_sidecars_backup.tar.zst | tar -x -C memory/
zstd -dc db-backup/convo_corpus_backup.tar.zst   | tar -x -C memory/data
zstd -dc db-backup/convo_search_backup.duckdb.zst > memory/convo_search.duckdb
zstd -dc db-backup/convo_search_fts_backup.db.zst > memory/convo_search_fts.db
```

Order matters if you restore all four: embed_store → corpus → index
(its pointers are corpus row numbers).

## 5. Risks

- **Cold embed wall (~1.5-3 h via the pool lane)**: one-time,
  resumable (cache checkpoints); incremental runs never pay it again
  (trial-verified seconds-class deltas).
- **Corpus size**: parquet zstd shrinks the ~1-3 GB raw union to an
  estimated 200-500 MB on disk; per-harness tar.zst sidecars remain
  available if pressure appears.
- **Sensitivity**: conversation text is local-only — corpus and index
  live under gitignored `memory/`; the tracked code never embeds
  content; no cloud embedder (house CPU lane).
- **Dedup**: newest-source-wins is idempotent for identical parts;
  divergent rewrites across snapshots collapse to the newest variant
  (rare, accepted — same part_id). NOTE: that is part-level dedup;
  TEXT-level duplicates across parts are a separate matter — 21,179 of
  the 68,155 indexable rows share a digest with another row (T1/T2).
- **Small-file count**: session-grain parquet = tens of files (not
  thousands) — glob-friendly now; re-shard by row-groups if session
  counts ever explode.

## 6. Non-goals

Editing/curating conversations · cross-harness session stitching ·
summarization lanes (own proposal if ever) · API endpoints before a
consumer asks (D15) · relying on live harness dbs at query time
(anti-goal by mandate).
