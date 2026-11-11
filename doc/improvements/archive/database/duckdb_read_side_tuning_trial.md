---
title: "DuckDB read-side tuning trial (convo_search read legs)"
status: executed
filed: "2026-11-11"
executed: "2026-11-11"
completed_md: "505"
area: database
---

# DuckDB read-side tuning trial (convo_search read legs)

**EXECUTED 2026-11-11** · **Status:** EXECUTED (verdict: measured-not-adopted; repo-wide 5 GB memory cap implemented) ·
**Area:** database read path, helpers/misc/convo_query.py

## 1. Motivation

The `convo_search.duckdb` store (1.14 GB) is the largest DuckDB read surface
in the repo. #4 proposed trialing read-side knobs (threads, memory limit,
read-ahead/mmap) against the real read legs before any adoption claim. This
trial ran the actual production read path (`helpers/misc/convo_query.py`
connection + queries) under each knob.

## 2. Method

- Fresh read-only connection per config via `convo_query.connect()` (the
  production code path, `duckdb_lock.open_read_only` + `LOAD fts`).
- Knobs: baseline (default) · `SET threads=1/2/4` · `SET memory_limit='512MB'`
  · `SET memory_limit='1.5GB'`.
- Queries: FTS `match_bm25` hits (`embed cache` limit 25; `context` limit
  200), point read by `part_id`, `convo_meta` read.
- Warm OS page cache (prime pass before timing); best-of-5 wall per query per
  config. DuckDB 2.0.0 on i5-6500 (4 cores, no HT). Default `memory_limit`
  on this 16 GB box is **11.2 GiB** (DuckDB default, ~70% of RAM).

## 3. Results (best-of-5, ms)

| query | baseline | threads=1 | threads=2 | threads=4 | mem 512MB | mem 1.5GB |
|---|---|---|---|---|---|---|
| fts_embed_cache | 296.7 | 816.4 | 471.2 | 299.7 | 321.1 | 305.1 |
| fts_generic | 199.2 | 462.5 | 283.6 | 197.6 | 209.0 | 197.5 |
| point_read | 6.4 | 9.9 | 6.2 | 6.6 | 6.4 | 6.3 |
| meta_read | 0.44 | 0.36 | 0.48 | 0.46 | 0.42 | 0.46 |

## 4. Verdict: measured-not-adopted

- **threads=1 is 2.3–2.75× slower** on FTS legs — the default (threads=4)
  is already the optimum; explicit `SET threads=4` matches baseline exactly.
  No upside in lowering; nothing to set.
- **memory_limit=512MB and 1.5GB have no effect** on these legs (statistical
  noise); the query working sets are small. The default is already
  **11.2 GiB**, so a 1.5 GB cap (operator preference, 16 GB box) changes
  nothing for read performance — and even 512 MB does not degrade it. If the
  goal is *bounding* DuckDB memory to protect other workloads, a
  `SET memory_limit='1.5GB'` (or `DUCKDB_MEMORY_LIMIT`) is safe; it is a
  policy cap, not a perf lever.
- **mmap / read-ahead are not exposed** as DuckDB 2.0.0 connection knobs on
  this path; OS page-cache warm-up dominates and is already effective (point
  reads 6 ms, meta 0.4 ms).
- FTS legs (~200–350 ms warm) are engine-bound bm25 over 1.14 GB, not
  knob-tunable; their cost is already amortized into the query CLIs and is
  not a wall-clock blocker on search paths.

Same class as the earlier measured-not-adopted levers (FTS5 optimize,
`preserve_insertion_order=false`): no *tuning* changes to code or config.
Recommended follow-on, if FTS latency ever matters: examine the bm25 query
plan / index segmentation (a *write-side* concern), not read-side knobs.

**Implemented 2026-11-11 (policy cap):** repo-wide 5 GB memory cap via
`helpers.core.duckdb_shared.memory_cap_config()` (`{"memory_limit": "5GB"}`),
applied on every file-backed connection — the shared funnels
(`duckdb_shared.connect_shared`, `duckdb_lock.open_read_only`), the graph
build/onager/quack-server child connections (config merged with unsigned-ext
kwargs), and all raw file-backed `duckdb.connect(...)` sites in maintenance
sync/backup/rebuild, analytics, gate_query, vec_search, trace_quote, etc.
Pure `:memory:` scratch and benches stay exempt. Verified: fresh connection
reports `memory_limit = 4.6 GiB` (5 GB); py_compile + ruff clean across all
25 edited files.
