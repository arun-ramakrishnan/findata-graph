---
title: "convo_search: drop HNSW, use numpy exact KNN + COPY-compaction; remove --swap from maint path"
status: executed
filed: "2026-11-09"
executed: "2026-11-09"
completed_md: "495"
area: "helpers/maintenance/rebuild_convo_search.py + helpers/misc/convo_query.py + helpers/maintenance/maint.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# convo_search — drop HNSW, numpy exact KNN, COPY-compaction, no swap in maint cycle

**Date:** 2026-11-09 · **Status:** EXECUTED 2026-11-09 ·
**Area:** `helpers/maintenance/rebuild_convo_search.py` · `helpers/misc/convo_query.py` ·
`helpers/maintenance/maint.py` · `helpers/maintenance/db_maint.py` ·
`helpers/maintenance/tmp_sweep.py` · `tests/test_maint.py` · `tests/test_convo_search.py`

## 1. TL;DR

The 2026-11-06/07 swap-rebuild doctrine (`convo_search_dead_block_compaction`,
S1–S4 EXECUTED, completed.md #485) solved the **symptom** but kept the
**disease**: every incremental refresh strands dead blocks (DuckDB never
returns them to the OS), and the cure — a 1596 s `--swap` rebuild per
maintenance cycle — is **measured** to take 30–40 min on today's corpus
because the bulk of that runtime is `hnsw_build` (1638 s at 191k).

The arc's decisive finding inverted the initial design: **the copy itself
was never slow — the HNSW blob was.** Measured 2026-11-09 on the live
4.40 GB store:

| recipe | time | result |
|---|---|---|
| `COPY FROM DATABASE` with the HNSW index present | **158.0 s** | 1.32 GB (the ~370 MB graph blob dominates) |
| `DROP INDEX` + `CHECKPOINT`, then `COPY` | **1.9 s + 12.8–14.2 s ≈ 15 s** | **0.977 GB**, dead 2.8% |
| old `--swap` rebuild (re-embed + HNSW build + FTS recreate + COPY) | 1596 s | 1.32 GB |

So: drop HNSW, and compaction drops from 26 minutes to 15 seconds — a
~100× improvement, with the bloat *source* removed at the same time.

## 2. Why now (not "the doctrine already shipped")

- The 2026-11-06 doctrine's law ("VACUUM reclaims 0 bytes", #485) is
  **conditional** — proven wrong once HNSW is removed (`reclaim.py`:
  DROP INDEX + CHECKPOINT reclaimed 700+ MB to the OS in 0.9 s). The
  unconditional version — "CHECKPOINT reclaims freed blocks iff no HNSW
  blob pins them" — is the new law.
- HNSW was the **bloat generator**, not merely a slow index: `attr2.py`
  over three 250-row delta waves with HNSW → dead 28.1% → 35.6% → 44.9%
  → 51.9%, file 1.32 → 2.705 GB; identical waves **without** HNSW → dead
  flat at 2.1%, file flat at 1.32 GB. DuckDB 2.0.0 rewrites the whole
  HNSW blob on every checkpoint, stranding the old one.
- HNSW's quality did not justify carrying it: on 2.0.0 (where the 1.5.5
  cosine binder defect is gone) recall@10 vs exact cosine was mean
  **0.887** / min **0.30**, saturating at ~94% even at `ef=800`.
- The corpus is append-only in practice (§S4 of the doctrine), so the
  swap path's re-embed paid 1596 s to re-derive ~99% cached vectors.
- Only two stores ever carried HNSW (`q1.py`): this one and
  `note_vectors.duckdb` (0.16 GB, REPLACE-only, does not bloat).

## 3. Slices — as built

### S1 — exact-KNN leg in `convo_query` (numpy is OPT-IN, DuckDB scan is the default)

**Why the original "numpy replaces HNSW" plan was reversed.** The first
implementation made numpy the default leg. Adversarial review finding F1
predicted a per-query regression, and direct A/B measurement confirmed
it: `search_tui` runs `convo_query.py` as a **subprocess per query**, so
a per-process LRU never warms. Loading a 313 MB matrix to answer one
query is strictly worse than DuckDB's in-database scan:

| leg (one-shot CLI invocation) | live 4.40 GB store | clean 1.32 GB store |
|---|---|---|
| numpy default (old) | 3303–5199 ms | 3661–4528 ms |
| `array_cosine_distance` SQL | **1304–1979 ms** | **1341–2225 ms** |

Decomposition: matrix load 2983 ms cold (864 ms of it the combined
`harness, part_id, provenance, embedding` Arrow fetch) vs 38–76 ms for
the warm numpy query and 110–131 ms for the warm SQL scan. Splitting the
fetch into two queries did not help — the Arrow transfer dominates.

**As built** in `helpers/misc/convo_query.py`:

- `_cos_hits` keeps its original signature and dispatches
  `_cos_hits_numpy(..., cache_only=True)` **first**; that returns `None`
  unless the matrix is already resident, then falls through to
  `_cos_hits_sql`. A cache miss is the normal path, not a degradation —
  the old fallback warning was deleted.
- `_load_matrix(con, dims, *, cache_only=False)` — one Arrow fetch of
  `harness, part_id, provenance, embedding` (falls back to the
  pre-provenance shape on `CatalogException`), `.combine_chunks()
  .flatten()` → `(N, dims)` float32 zero-copy; the Arrow table is kept
  alive so `mat` stays zero-copy. Width comes from
  `pa.types.is_fixed_size_list(emb.type)`.
- `_MATRIX_CACHE` — LRU, capacity 2, keyed `(str(path), st_mtime_ns,
  st_size, dims)`, so any rebuild/compact/replace invalidates it;
  returns `None` when the key is unavailable (in-memory or served attach).
- `_row_mask` applies `harness` / `provenance` filtering in Python via
  `pyarrow.compute` (`match_substring` for a source, `*` = any non-empty),
  so the source filter the review (R3) flagged as inexpressible works.
- NEW public `warm_matrix(con) -> bool` for long-lived callers that want
  the numpy path (TUI in-process lanes, bench harnesses): 0.9–1.7 s load
  buys 67–105 ms queries afterwards.
- `connect()` drops `LOAD vss` and `SET hnsw_enable_experimental_
  persistence` (they only existed for HNSW; a store with a persisted
  index now opens fine without them). `LOAD fts` stays.
- `ConvoConnections` gained `db_path: Path | None = None` (the cache key).

**Verified:** SQL and numpy legs return **identical ordered keys** with
max score delta **6.55e-08**; filters (`harness=`, `source=`, `source="*"`)
behave identically; CLI latency back to 1398–2051 ms.

### S2 — `compact_via_copy` replaces `rebuild_via_swap` (no re-embed, no HNSW, no FTS rebuild)

**As built** in `helpers/maintenance/rebuild_convo_search.py`:

- `COMPACT_GUARD = "Table swap in progress — do not interrupt"`, printed
  to stderr **twice**: at start, and immediately before `os.replace`.
- `_drop_ann_indexes(db_path)` runs FIRST under
  `io_lock(db_path, exclusive=True)` (~2 s) — this is the only write the
  function performs on the live store. Without it the persisted HNSW blob
  is copied and the copy costs 158 s instead of 14 s.
- Then an **in-memory** connection attaches `src` `READ_ONLY` and `dst`
  read-write, runs `COPY FROM DATABASE src TO dst` + `CHECKPOINT dst`.
  `COPY` (not table-level `CREATE TABLE AS`) is required: the FTS
  scoring is a persisted **macro** (`fts_main_convo_search.match_bm25`),
  and a raw table copy does not carry it — `match_bm25` then fails with
  `CatalogException: Scalar Function with name match_bm25 does not exist`.
  `EXPORT/IMPORT DATABASE` was rejected too: it loses indexes entirely.
- Parity gate on the four tables before any swap; a mismatch raises and
  the temp is discarded. The live store is never touched on failure.
- Post-compaction anatomy is measured with `anatomy(tmp)` on its OWN
  connection — see the bug note in §4.
- `os.replace(tmp, db_path)` under `io_lock(exclusive=True)`.
- `--compact` added to the CLI (`RebuildCliSpec.compact_help`);
  `rebuild_via_swap`, `--swap`, `HNSW_EF_CONSTRUCTION`, and the
  swap-only table DDL helpers (`_carry_harvest_state`, `HARVEST_META_DDL`,
  `PART_INDEX_DDL`) were deleted — `COPY` carries every table.
- The lock contract is deliberate: a `READ_ONLY` attach keeps concurrent
  `convo_query` readers working for the whole copy, while a would-be
  writer fails fast with DuckDB's `Conflicting lock` — the desired
  behaviour during the window the guard names.

### S3 — the maint tripwire now APPENDS `--compact` (it does not replace `--incremental`)

The original slice said "delete the tripwire". Review of the question
"do we still have the bloat guard?" changed that: the operator wants the
**watch kept** for unrelated reasons, only the *destination* changed.
A compaction does not refresh the corpus, so replacing
`--incremental` would silently drop the refresh.

**As built:** `maint._maybe_upgrade_convo_swap` → `_maybe_compact_convo`,
which keeps `--incremental` and **appends** `--compact` when
`duckdb_block_anatomy.above_swap_trigger` fires (dead ≥ max(2 GB, 60%)).
It is idempotent (a human-passed `--compact` is not doubled) and a probe
failure leaves the step alone. `db_maint`'s F4 alert (40% / 1 GB, all
DuckDB sidecars) had its text retargeted from `--swap` to `--compact`;
its thresholds are unchanged. `duckdb_block_anatomy` itself is untouched
(`db_maint` still uses it).

### S4 — `tmp_sweep` globs for the copy temp

`REPO_RESIDUE_GLOBS` gained `*.duckdb.copy-*.tmp` and
`*.duckdb.copy-*.tmp.wal`. Deliberately **no** `.build.lock` entry —
compaction never takes a build lock, so a glob for one would advertise a
file this path cannot produce.

### S5 — one-off live compaction (EXECUTED 2026-11-09)

```text
pre:  4,398,526,464 B, 70.3% dead, 203,921 rows, persisted HNSW
run:  python3 helpers/maintenance/rebuild_convo_search.py --compact
post: 976,760,832 B (0.977 GB), dead 2.8%, 203,921 rows, indexes []
```

Guard printed twice; index drop 4.40 → 3.71 GB; COPY 3.71 → 0.977 GB;
~15 s total. Verified after: dims 384, `duckdb_indexes()` empty, native
FTS `match_bm25(part_id,'hypergraph')` still returns 1899 hits, and
`convo_query.search()` returns `mode=hybrid` with 5 results.

## 4. Bugs found and fixed during execution

- **`anatomy_from_con` closes the caller's connection.** Its docstring
  promises the opposite ("for callers that hold their own lock/window")
  but it ends in `finally: con.close()`. The first live compaction died
  at 14.2 s with `Connection Error: Connection already closed!` when the
  following `DETACH dst` ran. The store survived intact (the failure is
  before the swap). Fixed by measuring the temp with `anatomy(tmp)` on
  its own connection; `db_maint`'s usage is unaffected.
- **DuckDB `.fetchone()` leaves an unfinished result** on the same
  connection, so a following `.sql(...).to_arrow_table()` raises
  `ResourceInUseException: ... connection has an unfinished result from
  execute()`. All three meta readers now use `.fetchall()`.
- **`COPY FROM DATABASE` needs the current database alias**, not the file
  stem (`BinderException: Catalog "work" does not exist`).
- **`VACUUM <attached-alias>`** fails with
  `CatalogException: Table with name <alias> does not exist`.

## 5. Risks & rollback

- **Risk: a future ANN index silently re-inflates the store.** Mitigation:
  the maint tripwire is kept as a *regression* tripwire (it now appends
  `--compact`), and `db_maint`'s F4 alert still watches every DuckDB
  sidecar at 40% / 1 GB.
- **Risk: numpy latency on cold load is high for a one-shot CLI.** This
  materialised (F1) and is why numpy is now opt-in via `warm_matrix`
  instead of the default leg.
- **Risk: the compaction loses something the copy does not carry.**
  Mitigation: row-count parity gate on the four tables before the swap,
  plus a hermetic test asserting identical table counts, identical row
  identity *and ordering*, and unchanged FTS scoring.
- **Risk: `--swap` is still wanted for shape-changing DDL.** It can be
  restored by setting `RebuildCliSpec.swap_help`; for compaction the
  COPY path is strictly stronger.
- **Rollback:** the old file survives until `os.replace`; a failure at
  any earlier point leaves the live store byte-identical (asserted by
  test). After the swap, rollback means rebuilding from the corpus.

## 6. Tests added

`tests/test_convo_search.py`:

- `test_compact_via_copy_parity_and_index_drop` — per-table counts, row
  identity *and order*, FTS scoring equality before/after, empty
  `duckdb_indexes()`, guard printed ≥ 2×, file did not grow.
- `test_compact_failure_leaves_the_live_store_intact` — a failure in the
  pre-copy step propagates, the live store stays **byte-identical**, and
  no `*.copy-*.tmp*` residue remains.

`tests/test_maint.py`: `TestConvoSwapTripwire` →
`TestConvoCompactTripwire` (7 tests: `--compact` appended while
`--incremental` survives; below-trigger / unavailable / raising probes
leave the step alone; no double-append; other steps and `TIER1_STEPS`
untouched).

Targeted suite: **94 passed** across `test_convo_search`,
`test_maint`, `test_duckdb_block_anatomy`, `test_db_maint_convo`,
`test_tmp_sweep`, `test_rebuild_cli_flags`; `ruff check` and
`ruff format --check` clean.

## 7. Open questions

1. `note_vectors.duckdb` HNSW in scope? **Out of scope** — 0.16 GB,
   REPLACE-only, does not bloat. Revisit only on a note-lane ticket.
2. Should the exact-KNN live in `convo_query.py` or a new
   `helpers/core/vec_knn.py`? **Keep it here** until a second caller
   exists (YAGNI).
3. `anatomy_from_con`'s docstring still contradicts its behaviour. Its
   only remaining caller wraps it in a `finally: close()`, so nothing is
   broken today — but the docstring should be corrected or the function
   renamed when someone next touches that module.
