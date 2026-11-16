---
title: "Restore incremental carry to the DuckDB rebuild legs: doc_search delta-apply + Arrow bulk writes, git-leg scoping, FTS-shadow delta refresh"
status: executed
filed: "2026-11-16"
executed: "2026-11-16"
completed_md: "528"
area: "helpers/maintenance/rebuild_doc_search.py + rebuild_git_search.py + rebuild_{script,memory,note}_search.py + tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Restore incremental carry to the DuckDB rebuild legs: doc_search delta-apply + Arrow bulk writes, git-leg scoping, FTS-shadow delta refresh

**Date:** 2026-11-16 · **Status:** EXECUTED (implemented + measured 2026-11-16) · **Area:**
`helpers/maintenance/rebuild_doc_search.py` + `rebuild_git_search.py` +
`rebuild_{script,memory,note}_search.py` + `tests/test_doc_query_duckdb.py`
(+ `tests/test_fts_topk_parity.py`).

## 0. Status — IMPLEMENTED 2026-11-16 (all slices, measured)

- **S1 doc carry**: rebuild() duckdb dispatch forwards incremental;
  mtime fast path + hash fallback, stable hash row ids, Arrow batch writes.
  Warm full rebuild 81.7 s -> **16.98 s**; no-op incremental carry **4.68 s**
  (495 files carried, 0 embed calls); live APPLY no-op **6.5-6.9 s**.
- **S2 git**: carry + Arrow batch. Live incremental delta (2 new + 1
  dropped) **13.9 s** vs the measured 40 s; no-op carry **7.4 s**; full
  replacement **25.5 s** (Arrow batch, for cold stores / basis bumps).
- **S3 script/memory**: duckdb legs wired to row-keyed carry +
  refresh_fts_shadow (the audit table below said 'yes' — stale; both were
  replacement-only). Memory diff is **content-keyed** (_memory_row_key),
  not title-keyed: a history snapshot sharing a title masked live deltas.
  Note-leg latent bug fixed: delta rows carried NULL id/searchtext
  (first non-empty delta would have been invisible to FTS).
- **S4 compaction**: live memory/doc_search.duckdb
  **89,665,536 -> 70,266,880 B** (seeded-cache fresh rebuild + os.replace;
  meta multiset parity verified, doc --check rc 0). The 30-35 MB estimate
  below was stale: the store now legitimately carries the HNSW mirror +
  native FTS shadow + consolidated embed cache (post-2026-11-08 folds).
- **S5**: doc carry tests (no-op/delta/deleted/mtime-drift/forced-full),
  git/script/memory carry tests; fts_topk parity green; targeted suites
  **104 passed**, ruff clean.
- **Deferred (measured)**: delta path on a 100%-changed corpus ran **26.9 s
  vs 17.0 s full** (root-swap probe) — threshold-fallback candidate;
  CachedEmbed point-lookup preload; the --allow-stale guard escape
  applies stamped legs as full rebuilds (script one-off **84 s** mid-arc).
**Follows:** the `duckdb_sidecar_fold` arc (#524, LANDED 2026-11-15) which
moved the doc leg to a DuckDB-native store as a **replacement** rebuild;
`duckdb_fts_sql_topk_regression` (fts_topk over shadow posting tables);
`duckdb_watchlist_hardening` (doc_vectors HNSW mirror); the convo-leg T6
Arrow-insert precedent (2026-09-27: `per-row executemany was the real cost
of a rebuild, not the embed`).

## 1. Motivation / scope

After the DuckDB vectors/embeddings migration, `make search-fresh` regressed
5×, concentrated in the `rebuild_doc_search` leg: a **warm** doc rebuild
(embed cache 100% hits, zero embed calls) measured **81.7 s** where the
sqlite-carry era applied the same corpus in seconds. The root cause is not
DuckDB-the-engine — it is that the doc leg's CLI contract (`--incremental`,
default **True**) is **silently dropped** at the DuckDB dispatch, so every
`make search-fresh` APPLY runs a full replacement rebuild through per-row
`executemany`. The same regression class (duckdb path never scopes writes)
also afflicts the **git leg**, and the leg audit surfaced a separate
**correctness** bug: the script/memory/note incremental paths write rows
without refreshing the native FTS shadow, so lexical search silently misses
the delta.

In scope: (S1) restore incremental carry to the doc leg with Arrow bulk
writes; (S2) scope the git leg's duckdb apply; (S3) refresh the FTS shadow
on the script/memory/note delta paths; (S4) one-off compaction of the
bloated live `doc_search.duckdb`; (S5) tests + measured validation.
Out of scope: query legs (perf_smoke shows doc queries at 12.8–17.8 ms
topk — healthy); convo + analytics legs (audit-verified healthy); embed
model or DDL changes.

## 2. Current state (verified 2026-11-16, this box)

**Doc leg profile** (warm rebuild, scratch copy of `memory/doc_search.duckdb`,
corpus 494 files / 4,576 section rows, cache hits 100%, embeds 0):

- Total **81.7 s**; cProfile: `_write_doc_store` **71.5 s (87%)**,
  `_file_rows` 8.7 s, `CachedEmbed.__call__` 7.8 s tottime (**4,576
  point-lookups × 1.7 ms even at 100% hits**).
- Sub-stages (doc_stage.py instrumentation): walk+chunk+embed(cache)
  8.46 s; **executemany insert doc_search 35.32 s**; **drop+insert
  doc_vectors mirror 34.15 s**; hnsw_create 1.34 s; meta executemany
  4.09 s; fts drop+create 2.46 s. Per-row executemany = **69.5 s = 80%**
  of the rebuild (`7.7 ms/row).
- Store bloat: `memory/doc_search.duckdb` **90,976,256 bytes** vs the
  **30.19 MiB** 2026-11-08 census filing (pending.md:246) — `3× growth
  from delete-all churn (DuckDB never returns freed space to the OS).
- `doc --check` is **not** the problem: 601 ms (hash-compare only).

**Leg audit** (all `make search-fresh` legs + convo-fresh/analytics-fresh,
live check timings 2026-11-16):

| leg | store/shape | incremental? | --check | verdict |
|---|---|---|---|---|
| doc | duckdb native FTS + HNSW mirror | **NO — kwarg dropped** | 0.6 s | **this proposal S1** |
| script | duckdb, `185 units, row-keyed diff | yes (writes only changed units) | 7.8 s | healthy (perf) |
| memory | duckdb, source_path keyed | yes | 2.8 s | healthy (perf) |
| note | duckdb carve-out (2026-11-16) | yes (per-file carry, DELETE WHERE + executemany) | 2.1 s | healthy (perf) |
| git | duckdb `rebuild_git_duckdb` :686 | **NO — passes incremental=False** (:744); `_write_git_store` :860 = DELETE-all + executemany + create_fts **every apply**; measured **scratch apply 40 s incremental == 40 s full** | 8.6 s | **S2** |
| convo | duckdb, `_bulk_insert` = ONE Arrow batch (:673) | yes (INSERT OR REPLACE; T6) | 1.2 s | healthy — the precedent |
| analytics | research.db aggregates | n/a (day-aggregates, small) | 0.7 s | healthy |
| harvest | live-store bound | pre-existing | 20.4 s | out of scope |

**Correctness finding (S3):** probe on a `script_search.duckdb` copy —
`fts_main_script_search.docs` count **848 → 848** after a plain INSERT of a
new row: DuckDB 2.0's native FTS shadow is a **one-shot structure**; it is
NOT auto-updated by DML. The script/memory/note incremental paths do
DELETE+INSERT **without** `drop_fts_index`/`create_fts_index`, so lexical
search misses every delta until the next `--full`. (Same law documented for
the doc leg at duckdb_sidecar_fold.md:74.) The doc leg does not have this
bug — it rebuilds the shadow every apply — and query-side `fts_topk`/
`match_bm25` consumers are unaffected for stores written by a full rebuild.

## 3. Root cause: how incremental went missing

The CLI contract never left: rebuild_common.py:370 defines `--incremental`
`store_true` **default=True** for all legs (no flag = incremental; `--full`
wins; :625-648 forwards the kwarg into `rebuild()`). The doc leg's DuckDB
dispatch dropped it on the floor:

```python
# rebuild_doc_search.py:562-563 (pre-fix)
if db_path.suffix.lower() == ".duckdb":
    return rebuild_duckdb(db_path, write=write, embed_fn=embed_fn, root=root)
```

— no `incremental=`, and `rebuild_duckdb` (:1239) has no such parameter;
its docstring premise `the corpus is 4.5k rows; full rebuild is seconds`
(:1252) measured **false** (86 s). The archived fold proposal anticipated
exactly this: the FTS one-shot law (:74) and `legacy per-file mtime carry
… remains a later incremental-carry slice **IF REBUILD LATENCY DEMANDS
IT**` (:126-127). Latency now demands it. The git leg repeats the pattern
in its own duckdb path (sqlite path kept :594/:601 scoping; the duckdb
port — 4045e41af, 2026-11-15 — did not).

## 4. Design

### S1 — doc leg: restore `incremental` + carry + Arrow bulk write
`helpers/maintenance/rebuild_doc_search.py`

1. Dispatch (:563) forwards `incremental`; `rebuild_duckdb` gains
   `incremental: bool = True` (matching the CLI default, sqlite-path shape).
2. **Carry pass** (mtime fast path + hash fallback, the sqlite-path
   doctrine): per file, a stored-mtime match carries the file verbatim —
   no read, no chunk, no embed, no id churn. Mtime drift falls back to a
   content-hash compare (mtime is only a hint — the shared-index/worktree
   lesson); only changed/new files go through `_file_rows` (which embeds
   through the cache). Deleted files' rows are removed. Carry is gated on
   the stored embed-model stamp matching (the meta basis is model-agnostic,
   the vectors are not); a cold store or stamp mismatch forces the full
   replacement.
3. **Stable section ids**: `_section_id(rel, idx)` = 47 blake2b bits of the
   file path `<< 16 | idx`, collision-guarded (salt bump) against the live
   id set. Positional ids (legacy `enumerate`) make every row after an edit
   point rewrite; stable ids keep carried rows byte-identical. Ids are
   opaque join keys (FTS shadow `docs.name`, the mirror) — legacy
   positional and new hash ids coexist harmlessly.
4. **Delta write**: DELETE WHERE file_path for changed/deleted files, then
   **one Arrow batch insert** (`pa.table` + `register`/`unregister`, the
   convo-leg T6 pattern that replaced 52k row binds) — projected `0.3–0.5 s
   vs the measured 69.5 s executemany.
5. **Derived structures rebuilt wholesale every apply** (unchanged
   doctrine): `doc_vectors` mirror + HNSW (the persisted vss index cannot
   absorb inserts; INSTALL/LOAD vss BEFORE the mirror DDL — the bind law)
   and the native FTS shadow (one-shot). Measured fixed cost `1.3 s HNSW +
   `2.5 s FTS + `0.3 s mirror reload at 4.6k rows.
6. Stats additions (`mode`, `carried_files`, `changed_files`,
   `deleted_files`, `sections_carried`) keep every existing key.

Expected timings: typical incremental apply **7 s** (mtime-matched corpus
skips reads entirely; 8.5 s walk+chunk worst case only when all mtimes
drifted) vs **86 s** today; forced `--full` **13 s** (Arrow insert
replaces the 69.5 s binds; everything else unchanged).

### S2 — git leg: scope the duckdb apply
`helpers/maintenance/rebuild_git_search.py`

Mirror the sqlite path's existing scoping (:594/:601: `embed_shas` =
touched commits when incremental) into the duckdb path: pass
`incremental` through (:744), delete only touched SHAs' rows, Arrow-batch
the insert, `drop`/`create_fts_index` after a non-empty delta
(`_write_git_store`'s delete-all + full create_fts every apply measured
**40 s incremental == 40 s full**). Same false-premise docstring at :702
corrected in passing.

### S3 — FTS-shadow delta refresh (correctness)
`helpers/maintenance/rebuild_{script,memory,note}_search.py`

After a non-empty incremental delta, `PRAGMA drop_fts_index` +
`PRAGMA create_fts_index` (one-shot law; `2.5 s at 4.6k doc rows, cheaper
at these stores' sizes). This restores the documented semantics — lexical
search finds the delta — and is query-visible **as a bugfix**. Guard on
delta non-empty so no-op applies stay fast.

### S4 — one-off live-store compaction
`memory/doc_search.duckdb`

After S1's first APPLY: rebuild the store into a fresh file and swap
(`os.replace`) — reclaims the 3× delete-all bloat (90,976,256 → 30–35
MB) and re-mints all ids onto the hash scheme in one 13 s pass. DuckDB
never returns space to the OS; fresh-file rebuild is the only compaction
(duckdb-pure-stack memory law). Single-writer discipline: no concurrent
`search-fresh`/`doc_query` write windows during the swap.

### S5 — tests + validation

- `tests/test_doc_query_duckdb.py` additions (existing `seeded`/
  `fake_local` fixtures): incremental no-op apply carries (embed call count
  0, `mode`/`carried_files` stats, id set identical); delta apply embeds
  only the new file and total grows by its sections; deleted-file rows
  removed; **FTS shadow sees the delta** (`fts_topk` over a token unique to
  the new file); `--full` replacement mode intact.
- `tests/test_fts_topk_parity.py` stays green (query semantics unchanged).
- Scratch-copy A/B timing on the live corpus (incremental vs full vs
  pre-fix), `perf_smoke` doc query legs unchanged; store size before/after
  S4.
- Targeted tests only — no `make pytest` (gate doctrine).

## 5. Costs / risks

- **Id-scheme migration**: live store keeps positional ids on carried
  rows; new rows mint hash ids. Mixed scheme is invisible to consumers
  (ids are opaque, never persisted outside the store's own shadow
  structures). S4 re-mints uniformly if uniformity is wanted.
- **mtime fast path** could carry a same-mtime/different-content file;
  mitigated by the hash fallback on drift, and check-mode remains
  hash-identity (a false carry surfaces as a stale verdict at the next
  check, which forces a hash pass).
- **Model-stamp gate**: carry across an embedder swap is refused → full
  rebuild (correct; vectors are model-bound).
- **pyarrow dependency**: already installed (26.0.1) and load-bearing for
  the convo leg — no new dependency.
- **FTS refresh cost on delta applies** (S1/S3): `2.5 s at doc-corpus
  scale, smaller elsewhere — the documented price of the one-shot shadow;
  revisit only if the corpus grows 10×.
- **Arrow batch memory**: one 4.6k-row batch is MB-scale; the convo leg
  batches 52k rows the same way.

## 6. Deferred / out of scope

- `CachedEmbed` point-lookup cost (1.7 ms × N per apply) — next-slice
  candidate (in-memory dict preload) once carry makes it the dominant
  remaining cost on changed-file-heavy applies.
- `harvest --check` 20.4 s (live-store bound, pre-existing, separate
  surface).
- HNSW incremental insert support if/when the vss extension grows one
  (pending.md deferred N5 item 5 tracks the index macros).
