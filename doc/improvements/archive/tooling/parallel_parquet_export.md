---
title: "Parallel parquet mirror export — thread the per-table COPY loop under duckdb 2.0"
status: executed
filed: "2026-10-24"
executed: "2026-10-24"
completed_md: "435"
area: "helpers/maintenance/snapshot_db.py"
---

# Parallel parquet mirror export — thread the per-table COPY loop under duckdb 2.0

**Date:** 2026-10-24 · **Status:** EXECUTED same day — archived (completed.md #435)

## 1. Motivation

`export_parquet_duckdb` (snapshot_db.py:760) writes the DuckDB parquet
mirror one table at a time — a serial Python loop of
`COPY (SELECT * FROM <t> ORDER BY ALL) TO … (FORMAT PARQUET, COMPRESSION
ZSTD)` statements, 58+ tables per `make snapshot`. DuckDB 2.0 ("Cyanoptera",
upgraded 2026-10-21, completed.md #427) ships engine-level async I/O
(`async_threads`, default 16) — but that pool parallelizes *within* one
statement; the loop still pays per-statement serialisation for 58 table
writes. The 2.0 changeset offers no Python API to batch them, so the wall
clock is ours to reclaim.

Measured shape (2026-10-24): maint's `snapshot duckdb parquet mirror`
step ran 3.02 s; `make snapshot` 16.04 s total (parquet export + sqlite
side + db-backup zst + HIF). The export loop is the parallelisable core;
the sqlite-side loop (snapshot_db.py:909) is the same shape.

## 2. Design

Thread-pool the per-table COPY with one **cursor** per worker
(`con.cursor()` duplicates the connection cheaply and execute() releases
the GIL — no file-level contention: each worker writes a distinct
`<t>.parquet`).

- `MAX_EXPORT_WORKERS = min(4, os.cpu_count())` (the box is 4c/4t; PSI
  budget discipline from run_perf_benchmarks applies — the snapshot leg
  already sits at 6.6 s of a 9.0 budget, so the gain must come WITHOUT
  raising peak load on the perf leg, or the leg budget needs the +1 s
  the pre-parallel numbers justify).
- Keep `ORDER BY ALL` + ZSTD per table (the 2026-08-22 churn fix and
  #174 D2 codec standardisation are load-bearing).
- Aggregate `results`/`total_bytes` after the pool; logging batched to
  preserve the per-table lines.
- Same treatment for the sqlite-side loop (909) in a second slice.
- The schema DDL export stays first and serial (it is the canonical
  ordering surface).

Acceptance: `make snapshot` wall ≤ 60% of the 2026-10-24 baseline on a
quiet box; `make snapshot-check` byte-stable (the manifest is
ORDER-BY-ALL canonical, so worker order must not affect content);
perf `snapshot_check` leg still within budget.

## 3. Risks → measured outcomes (2026-10-24 execution)

- **Cursor semantics (the risk section's verify item) — three 2.0 traps
  found and fixed**: (1) `con.cursor()` does NOT inherit the active
  catalog (the wire attach's `USE graph`) — every worker re-issues USE;
  (2) cursor() creation itself races ("connection is busy with another
  call") — cursors are built serially in the parent and handed to
  workers through a queue; (3) 4 concurrent COPYs over quack-attach
  cursors break the wire result stream ("Result has been closed") — the
  wire-served graph mirror keeps the SERIAL loop; the pool is proven on
  file-mode connections only.
- **Acceptance revised by measurement**: the projection ("≤60% of make
  snapshot wall") mis-attributed the payoff — `make snapshot`'s 16 s is
  dominated by backup compression + verify + HIF (~12 s), not the
  export loop. Measured per-leg: sqlite export 4.7 s → **0.85 s (5.5×)**
  pooled; sources (7 tables) pooled green; the wire-served graph mirror
  (43 tables, 2.4 s) stays serial by the guard. Whole-run 16.0 → 15.6 s
  (~3%) on a loaded box. `make snapshot-check` clean — artifacts
  byte-stable (worker order cannot churn ORDER-BY-ALL blobs). Suites:
  integration snapshot cycle + centrality cache 20, snapshot-select 66 —
  all green.
- Writer-order nondeterminism in logs only; artifacts are content-stable
  by ORDER BY ALL.

## 4. Read-side trial — negative (2026-10-24, operator question: "async I/O for reads?")

The 2.0 changeset's read-side freebies (async prefetch, expression-stats
pruning #23264, exact rowgroup stats #23557, all-NULL fast path #23464)
were trialled A/B on the real mirror corpus (74 files, 111.7 MB, 1.35 M
rows — the `--check` verify shape plus a full scan of the 41.7 MB
note_search_content):

| config | count-sweep median |
|---|---|
| defaults | 56 ms |
| `parquet_metadata_cache=true` | 47 ms |
| `prefetch_all_parquet_files=true` | 48 ms |
| `disable_parquet_prefetching=true` | 49 ms |
| `async_threads` 1 / 16 / 64 | 52 / 56 / 53 ms |

Every config lands in a ±15% band around 50 ms — **reads are already
sub-second and knob-insensitive at this corpus scale**; the full-scan
probe agrees (0.20-0.22 s regardless of `async_threads`, 0.26 s with the
compute pool pinned to 1 — compute, not I/O, is the visible axis).

Where `make snapshot-check`'s ~6.4 s actually goes: the S3 **zstd
write-roundtrip** (read → rewrite → count per file) measured 3.25 s in
isolation — a deliberate integrity cost, not a read cost — plus
live-side count queries. No read-side knob is worth a change; the trial
closes the "async I/O could help reads" question with numbers.
