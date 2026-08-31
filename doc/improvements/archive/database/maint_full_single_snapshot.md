---
title: maint-full single snapshot + zstd compression
status: executed
filed: '2026-08-29'
executed: '2026-08-29'
completed_md: '174'
area: helpers/maintenance/maint.py
---

# maint-full single snapshot + zstd compression

**Date:** 2026-08-29 · **Status:** EXECUTED 2026-08-29 (completed.md #174) ·
**Area:** `helpers/maintenance/maint.py` + `helpers/maintenance/snapshot_db.py`

> **Measured outcome (2026-08-29):** `make maint-full` 66 s → **32.3 s**
> (12/12 steps, single 13.0 s snapshot at the tail); standalone
> `make snapshot` 16.6–20.3 s → **7.7–8.0 s** (zstd parquet + embed-gz
> reuse on the 2nd run); `make snapshot-check` 1.1 s green end-to-end.
> §7 deferred row in parallel_cold_embed.md closed by this proposal.

> Closes the "Incremental 2nd snapshot in maint-full" row deferred in
> `doc/improvements/archive/tooling/parallel_cold_embed.md` §7
> (recorded estimate: saves ~19 s of the 66 s maint-full budget).

## 1. Motivation

`make maint-full` runs `snapshot_db.py` twice: TIER1 step 2 (after
`db_maint`) and TIER2 step 10 (final re-snapshot after analytics +
events + insights). `maint_report.txt` (2026-08-28) records
16.56–20.29 s and 15.69–21.38 s for the two legs — ~32–42 s of the
66 s budget. Between the two snapshot points, the TIER1 snapshot's
artifacts are **always discarded**: step 3 rebuilds the entire DuckDB
file, steps 4–9 mutate SQLite tables, and step 10 re-exports
everything. The first snapshot only earns its keep if TIER2 crashes
mid-run.

## 2. Measured sub-stage budget of one snapshot (2026-08-29, this box)

| Stage | Measured | Skippable in a 2nd snapshot? |
|---|---|---|
| gzip `research.db` (59 MB) | ~5.5 s | No — TIER2 steps change content |
| gzip `embed_store.db` (36 MB) | ~5.2 s | Yes — untouched in steady state |
| gzip `graph.duckdb` | 0.25 s | No — gen restamped |
| SQLite parquet export (13 tables) | ~5.5 s | Barely — heavy tables are the dirty ones |
| DuckDB parquet export (28 × `COPY … ORDER BY ALL`) | 0.16 s | Moot |
| verify (gzip roundtrip + counts + parquet footer) | ~1–3 s | Partially |

## 3. Design

**D1 — elide the TIER1 snapshot in `--full`** (replaces the literal
"incremental 2nd snapshot"): `maint.py --full` composes
TIER1-minus-snapshot + TIER2, so exactly one snapshot runs at the end.
Plain `make maint` is unchanged (3 steps, always-safe, still
snapshots). Rationale over a true dirty-table incremental: measured,
an incremental 2nd snapshot recovers only ~5–8 s (the gzip branch
dominates and most of it is mandatory) while a dirty-table registry
adds a stale-parquet drift hazard to a published artifact — skipping
the first snapshot recovers the full ~19 s for a step-list change.

Crash-safety trade: a mid-TIER2 failure leaves no fresh committed
snapshot this run — recovery is intact via `db_maint`'s step-1
pre-mutation `db-backup/*.gz` copies, `snapshot-restore`, and the
previous day's git-tracked parquet. maint-full is post-ingest
re-derivation; re-running after a fix is the normal flow.

**D2 — SQLite parquet codec gzip → zstd** (`export_parquet_sqlite`):
measured on the two heavy tables (pyarrow built-in codec, no new
dependency; parquet container unchanged so `--restore`, verify and all
readers are codec-transparent):

| Table | gzip write | zstd write | gzip size | zstd size |
|---|---|---|---|---|
| note_search_content | 3.17 s | 0.11 s | 8.54 MB | 8.50 MB |
| company_embeddings | 1.82 s | 0.04 s | 3.85 MB | 3.54 MB |

The "gzip for TEXT/BLOB-heavy tables" comment is superseded: zstd
matches or beats the ratio at ~30× the write speed and faster
read-back. One-time churn: the first post-landing snapshot rewrites
all 13 SQLite parquet files under the new codec (expected; snapshots
are same-day-refreshed artifacts).

**D3 — embed-store gzip skip rider** (`_cmd_create` binary branch):
gzip `embed_store.db` only when its mtime+size is newer than the
existing `embed_store.snapshot.db.gz` (missing gz → always gzip);
otherwise log "embed store unchanged — reusing gz". Safe because
`db_maint` never writes the embed store (backup is read-only copy;
VACUUM/CHECKPOINT hit `research.db`/`graph.duckdb` only) and
steady-state TIER2 embed writes are guarded upserts (no byte change ⇒
no write). Saves ~5 s on every standalone `make snapshot` in steady
state.

## 4. Measured not adopted

- **Binary backups `.gz` → `.zst` (zstd -9):** full 4-path bench
  (2026-08-29, raw → compressed bytes, gzip -9 = today's code):
  `research.db` 63.3 MB → 23.4 MB / 6.70 s (zstd -9: 20.8 MB / 2.35 s;
  zstd -3: 22.4 MB / 0.62 s); `embed_store.db` 36.6 MB → 15.8 MB /
  6.38 s (zstd -9: 15.4 MB / 2.03 s); `graph.duckdb` 11.0 MB → 3.4 MB /
  0.24 s (zstd -9: 3.3 MB / 0.10 s). Decompression ~2× faster on zstd.
  Deferred: changes the restore-artifact format (extension, `gzip.open`
  → stdlib `compression.zstd` (PEP 784, Python 3.14), the documented
  `gunzip -c` recovery command). zstd -19 / pzstd slower than gzip -9 at
  this file size — do not use.
- **DuckDB COPY compression:** snappy default, total 0.16 s — nothing
  to win (codec documented in source).
- **The 4th db-backup gzip is an orphan**: `research.snapshot.db_vec.db.gz`
  (10.5 MB, frozen 2026-08-26) is the pre-#166 legacy sidecar artifact —
  its source `memory/research.db_vec.db` no longer exists, so no code
  path refreshes it. Safe to delete by hand; #166's naming rule keeps
  only the three live artifacts fresh.

## 4b. Same-day follow-ups (2026-08-29, generation-audit session)

- **`rebuild_schema.py` trigger restore:** the rebuild's DROP+recreate of
  `entities`/`graph_edges` silently destroyed the six `trg_*_gen`
  generation triggers (SQLite drops a table's triggers with it) — every
  later write would have stopped bumping the epoch and `_is_warm` gone
  blind. `rebuild()` now calls idempotent `ensure_db_meta` post-commit
  and reports `generation_triggers=6`; two regression tests pin trigger
  survival + a post-rebuild write actually bumping.
- **`snapshot_db.py` live-source opens are read-only** (`_connect_ro`):
  the backup source, the snapshot verifier's source counts, and the
  parquet exporter/verifier used plain RW `sqlite3.connect` on the live
  DBs — SELECT-only, but it dirtied the file mtime via WAL bookkeeping.
  Verified: a full `make snapshot` run now leaves `memory/research.db`'s
  mtime untouched. (VACUUM/ANALYZE/OPTIMIZE deliberately do NOT bump the
  epoch — content-preserving; a bump would re-dirty db_meta.parquet every
  maint cycle, defeating #147 zero-churn.)

## 5. Verification

- `test_maint.py`: pin the `--full` composition (12 steps, single
  snapshot at the end; plain-maint 3-step order unchanged).
- `test_snapshot_db.py`: embed-store gz reuse on unchanged source
  (mtime backdating), re-gzip after touch; existing parquet roundtrip
  tests prove the zstd files transparently.
- Timing record: two back-to-back `make snapshot` (2nd logs the skip);
  one real `make maint-full` (safe by zero-churn guarantees) with
  before/after in maint_report.txt; `make snapshot-check` green.

## 6. Result (measured)

maint-full **66 s → 32.3 s** (−51%; −19 s snapshot #1, −~5 s zstd
parquet, −~2 s gzip branch elsewhere; the in-run snapshot step is
13.04 s). Standalone `make snapshot` **16.6–20.3 s → 7.7 s first run /
8.0 s with the embed gz reused**. `snapshot-check` green (41 parquet
tables, gen parity 58158). One-time churn: all 13 SQLite parquet files
re-encoded gzip → zstd (hidden behind the snapshots/ skip-worktree flag
until `git add`).
