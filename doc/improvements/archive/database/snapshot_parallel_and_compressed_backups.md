---
title: Parallel per-DB snapshot + zstd-compressed recovery backups
status: executed
filed: '2026-08-29'
executed: '2026-08-29'
completed_md: '175'
area: helpers/maintenance/snapshot_db.py
---

# Parallel per-DB snapshot + zstd-compressed recovery backups

> **Measured outcome (2026-08-29):** db-backup/ is now 100 % compressed —
> 233 MB → **68 MB** (research 59.1→22.7 MB, embed 36.4→16.3 MB, duckdb
> 11.0→3.4 MB, doc_search 11.1→4.1 MB, script_search 3.3→0.002 MB);
> roundtrip sanity: decompressed research_backup passes integrity_check
> with live row counts. Snapshot 8.2 s steady-state with the per-DB
> threads (embed-gz reuse firing inside its worker). 178 tests green.
**Date:** 2026-08-29 · **Status:** EXECUTED 2026-08-29 (completed.md #175) ·
**Area:** `helpers/maintenance/snapshot_db.py` + `db_maint.py` +
`rebuild_doc_search.py` / `rebuild_script_search.py` (sidecar backups)

> Closes the "Parallel per-table Parquet export" row deferred in
> `../archive/tooling/parallel_cold_embed.md` §7 — at per-DB granularity
> per the user's "keep it simple: one thread per db for starters";
> per-table parallelism stays deferred behind the same trigger. Also
> extends the #174 zstd standard to the PLAIN recovery copies in
> `db-backup/` ("make everything a compressed copy").

## 1. Motivation

- After #174, one snapshot run is ~8 s: the gzip binary branch (~11 s
  cold / ~7 s with the embed-gz reused) runs its three per-DB backups
  strictly sequentially, and the parquet branch runs the SQLite (~2 s)
  and DuckDB (0.16 s) exports one after the other. The three DBs are
  fully independent — the parallelism is free.
- `db-backup/` holds ~110 MB of PLAIN, uncompressed recovery copies
  (`research_backup.db` 59–63 MB, `embed_store_backup.db` 36 MB,
  `graph_backup.duckdb` 11 MB, sidecar `doc_search_backup.db` /
  `script_search_backup.db` ~14 MB) beside the compressed `.gz`
  snapshots. With stdlib `compression.zstd` (PEP 784) there is no reason
  to keep raw copies of DBs that compress 2.7–3×.

## 2. Design

**D1 — `helpers/core/zstd_io.py`** (new, tiny):
`compress_file(src, dst)` / `decompress_file(src, dst)` — streaming,
chunked, stdlib zstd at library-default level (3, per the #174 level
policy: no explicit level switches); `zst_path(p)` = `p.with_name(p.name
+ ".zst")` (append, so `backup.db → backup.db.zst`).

**D2 — db_maint compressed recovery copies:** `_backup`,
`_backup_embed_store` (incl. the legacy `_vec` twin naming), and
`_backup_duckdb` stage the WAL-consistent plain copy in a temp file
exactly as today, then compress to `name.zst` and unlink the temp. The
artifact set becomes `research_backup.db.zst`, `embed_store_backup.db.zst`
(or `research_backup_vec.db.zst`), `graph_backup.duckdb.zst`. Manual
recovery is now `zstd -dc db-backup/research_backup.db.zst >
memory/research.db` (documented in the module docstring). Sidecar
backups (`_backup_file` in rebuild_doc_search, called by
rebuild_script_search) compress the same way →
`doc_search_backup.db.zst` / `script_search_backup.db.zst`.

**D3 — parallel snapshot, one thread per DB:** `_cmd_create` gains a
`ThreadPoolExecutor`:
- binary branch: 3 workers — research (create + verify), embed store
  (create + reuse rider), duckdb (create + verify). Fully independent
  files; C-level work (gzip/zlib, sqlite backup, duckdb CHECKPOINT)
  releases the GIL.
- parquet branch: 2 workers — (export_parquet_sqlite + sqlite-side
  verify) and (export_parquet_duckdb + duckdb-side verify).
  `verify_parquet_snapshot` is refactored into per-DB helpers + a thin
  wrapper (existing callers/tests unchanged). Determinism is untouched:
  exports remain pure functions of content.
- Thread exceptions are captured and fail the run's `ok` (no silent
  partial snapshots). Warm-cycle behavior unchanged (embed-gz reuse
  rider still fires inside its worker).

**D4 — housekeeping:** after a green run, the stale plain backups
(and the frozen pre-#166 `_vec` gz/db orphans) are deleted by hand —
one-time, listed in the completed.md entry.

## 3. Verification

- `test_db_maint.py` / `test_db_maint_duckdb.py`: backup assertions move
  to `.zst` names + a decompress→sqlite-open roundtrip (row counts
  match the source).
- `test_integration_maint_chain.py`: shim paths → `.zst`.
- `test_snapshot_db.py` / `test_snapshot.py`: existing `_cmd_create`
  branches now exercise the threaded paths unchanged; determinism pin
  green.
- Live: one `make maint` (backups land compressed), timed `make
  snapshot` before/after, `make snapshot-check` green; db-backup holds
  only compressed artifacts afterwards.

## 4. Costs & risks

- Effort: ~half a day (mechanical; the refactor of verify_parquet into
  per-DB helpers is the only structural bit).
- Threads on 4C/4T: three workers on three independent files — no
  contention; desktop interactivity dip during backup is unchanged in
  kind, shorter in duration.
- Recovery copies change extension — the manual restore command is
  documented in `db_maint`'s docstring; no programmatic reader exists
  (verified: only doc references).
