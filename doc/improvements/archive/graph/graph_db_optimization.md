---
title: Performance Optimization Proposal
status: executed
filed: '2026-09-10'
executed: '2026-09-10'
completed_md: '222'
area: helpers/graph/onager.py, helpers/graph/algorithms.py, helpers/graph/derive_insights.py
---

# Performance Optimization Proposal

**Date**: 2026-09-10
**Machine**: 4 physical cores (no HT), 13GB RAM, Python 3.14.4
**Baseline**: `make perf` 2026-09-10 → 21/22 passed (snapshot_check FAIL(rc) due to DuckDB generation MISMATCH)

---

## Current State Summary

The prior session (2026-08-17) fixed four items (static_checks -35%, snapshot_check -53%, rebuild_note_search -65%, fuzzy_duplicate_names -30%) and refactored `closeness_centrality` from NetworkX (`nx.closeness_centrality(target, distance="weight")`, 8.8s) to Onager DuckDB extension (`onager_ctr_closeness`, 1.7s). The `perf_improvs.txt` P0 item is resolved; remaining targets are discussed below.

| Benchmark | Latest | Budget | Status |
|---|---|---|---|
| `graph_pagerank` | 0.61s | 3.0s | OK |
| `graph_closeness` | 2.23s | 4.0s | OK |
| `graph_betweenness` | 1.57s | 4.0s | OK |
| `graph_metrics` | 1.85s | — | not in perf gate |
| `closeness_centrality` (standalone) | 1.70s | — | not in perf gate |
| `derive_insights` (dry-run) | 3.98s | 12.0s | OK |
| `graph_rebuild` | 3.99s | 5.0s | OK |

---

## Issue 1 — `closeness_centrality` Onager overhead (1.7s)

### Problem

`closeness_centrality()` in `helpers/graph/algorithms.py:178` delegates to `onager_closeness()` in `helpers/graph/onager.py:245`, which:

1. Creates a **fresh DuckDB connection** every call (no connection reuse)
2. Calls `_materialize_from_db()` which creates **two temp tables** (`_onager_int`, `_onager_e`) from `fin.graph_edges` via SQL
3. Calls `_onager_named()` which runs `onager_ctr_closeness((SELECT src, dst, weight FROM _onager_e))` — a DuckDB extension function

The Onager extension function internally makes **~13 additional `_duckdb.execute` calls** (16 total, 1.66s). The graph has 19,261 edges across 1,491 distinct sources and 1,364 distinct targets.

### Root Cause

Every call path (CLI `--closeness`, `graph_metrics` when it includes closeness, `api_graph_stats`) creates a new connection. The `_prepare(None)` in `onager.py:54` does `duckdb.connect()` + `LOAD sqlite;` + `LOAD onager;` on every invocation. Even `connect(read_only=True)` in `query.py:424` creates a new connection to `memory/graph.duckdb`.

The temp tables `_onager_int` and `_onager_e` are **deterministic functions of the edge set** (which is static between data changes). They are recreated on every call even when the graph hasn't changed.

### Proposed Fix: Shared Onager Connection + Cached Materialization

**Approach A: Connection caching** (small win, ~0.2s)

- Cache the DuckDB connection object in a module-level singleton or `functools.lru_cache`
- Reuse the connection across calls so `_prepare` skips `duckdb.connect()` and extension loading
- Risk: connection state (temp tables) persists across calls; must invalidate on data change

**Approach B: Cached materialization** (medium win, ~0.5s)

- Add a **generation-based cache key** for the materialized temp tables
- `_materialize_from_db` checks if the current SQLite generation matches the cached one
- If matched, skip `CREATE OR REPLACE TEMP TABLE` and reuse existing temp tables
- On data change (`clear_graph_cache()`), invalidate the cache
- This avoids both the connection overhead and the temp-table creation

**Approach C: Batched metric calls** (largest win, ~1.2s)

- Add a `with_onager_connection()` context manager that creates one connection and yields it
- `closeness_centrality`, `betweenness_centrality`, `pagerank`, `degree_centrality` all share the same connection and same `_onager_int`/`_onager_e` temp tables
- The temp tables are **identical** for all Onager centrality functions (same edge set)
- This eliminates redundant materialization when multiple metrics are computed in sequence (e.g., `make graph-stats` or `algorithms --all`)

**Recommended**: Combine B and C. Cache materialization by generation (B) and share connections across batched calls (C). This targets 1.7s → ~0.5s.

### Files to Modify

- `helpers/graph/onager.py`: Add connection cache + generation-keyed temp-table cache
- `helpers/graph/algorithms.py`: Batch calls through shared connection context
- `helpers/graph/query.py`: Expose generation for cache key

### Verification

- `python3 -c "from helpers.graph.algorithms import closeness_centrality; closeness_centrality()"` should drop from 1.7s to ~0.5s
- `make perf` should show `graph_closeness` within budget with margin

---

## Issue 4 — `_splice_sources` at 1.45s in `derive_insights`

### Problem

`derive_insights` dry-run takes 3.98s total. Profiling shows:

- `render_notes`: 2.11s (1187 calls to `_splice_sources`)
- `_splice_sources` → `merged_sources` → `edition_source_entry`: 1.45s (1187 calls)
- `edition_source_entry` reads source note files and resolves metadata: 1404 calls
- `resolve_editions` → `resolve_edition_string` → `_resolve_variants`: 0.94s (8955 calls)

### Root Cause

`edition_source_entry(src, vault)` at `helpers/core/edition_index.py:283` is called **once per unique source path**, but each call does:

1. `src.read_text()` — reads the full source note file
2. `note_title(...)` — parses the title from the file content
3. `git_add_date(src)` — runs `git log --diff-filter=A` (memoized via `_GIT_LOG_DATES` batch)
4. `_TITLE_MEMO` caches titles, `_GIT_DATE_MEMO` caches dates — but **the file is still read** on first access

There are ~1,404 unique source notes referenced by derived notes. On the first `render_notes` call, all 1,404 files are read and parsed. The `_TITLE_MEMO` and `_GIT_DATE_MEMO` caches persist across calls, so subsequent `render_notes` calls are fast.

The `_resolve_variants` function does regex splits and containment checks against the index. With 8,955 calls, each checking ~3 variant forms against the full index, this is O(calls × variants × index_size).

### Proposed Fix: Pre-built Source Metadata Cache

**Approach A: Batch source metadata preload** (medium win, ~0.7s)

- At the start of `derive_insights` `_cli()`, do a **single pass** over all source notes in the vault
- Build a `dict[str, dict]` mapping source stem → `{id, resource, title, last_modified}`
- `edition_source_entry` becomes a dict lookup instead of file I/O
- This replaces 1,404 file reads + YAML parses with one batch operation

**Approach B: Memoize `merged_sources` results** (small win, ~0.3s)

- `merged_sources` is called 1187 times with the same `(fm, text, index, vault)` combinations
- Many notes reference the same editions, so `merged_sources` returns identical results
- Add an LRU cache keyed on `(id(fm), text, vault)` or `(id(fm), hash(text))`
- This avoids re-resolving edition strings for notes that share the same body text

**Approach C: Optimize `_resolve_variants`** (small win, ~0.2s)

- `_resolve_variants` does containment checks (`k in key or key in k`) against the full index
- Pre-index the source notes by length or use a trie-like structure
- Or add a `memo` parameter to `_resolve_variants` (like `resolve_edition_string` already has)

**Recommended**: Combine A and B. Pre-build source metadata at `derive_insights` startup (A), and memoize `merged_sources` results (B). This targets 1.45s → ~0.5s.

### Files to Modify

- `helpers/graph/derive_insights.py`: Add pre-built source metadata cache in `_cli()`; pass to `render_notes`
- `helpers/core/edition_index.py`: Add `build_source_metadata_cache(vault)` function; modify `edition_source_entry` to accept pre-built cache
- `helpers/graph/derive_insights.py:_splice_sources`: Accept and use memoized `merged_sources` results

### Verification

- `python3 helpers/graph/derive_insights.py findata --stale-only` should drop from ~4s to ~2.5s
- `make perf` `derive_insights` should be comfortably within 12s budget

---

## Issue 5 — `graph_metrics` 1.85s / 16 DuckDB execute calls

### Problem

`graph_metrics()` in `helpers/graph/algorithms.py:337` calls `onager_graph_metrics()` which:

1. Calls `_materialize_from_db()` (2 `_duckdb.execute` calls)
2. Runs `_GRAPH_METRIC_SQL` — a single query with **8 scalar subqueries**, each calling an Onager metric function (`onager_mtr_density`, `onager_mtr_diameter`, `onager_mtr_radius`, `onager_mtr_avg_path_length`, `onager_mtr_transitivity`, `onager_mtr_avg_clustering`, `onager_mtr_assortativity`, `onager_mtr_triangles`)
3. The Onager extension functions internally make ~13 additional `_duckdb.execute` calls

Total: 16 `_duckdb.execute` calls, 1.81s. **Notably, `graph_metrics` does NOT include `closeness_centrality`** — it computes density, diameter, path length, transitivity, clustering, assortativity, triangles only.

### Root Cause

Each Onager metric function (`onager_mtr_*`) is an independent DuckDB extension function. The SQL query uses **8 scalar subqueries** (one per metric), each invoking the extension. The extension internally re-materializes the graph for each metric, causing redundant computation.

Additionally, `_materialize_from_db` creates temp tables that are **not shared** with `closeness_centrality` or other Onager calls. Each function call is isolated.

### Proposed Fix: Reduce Onager Round-Trips

**Approach A: Single Onager metric call** (large win, ~0.8s)

- Check if Onager exposes a **multi-metric** function (e.g., `onager_mtr_all`) that computes all metrics in one call
- If not, the 8 scalar subqueries could be consolidated into a **single SQL query** using a CTE that computes all metrics in parallel
- This eliminates the per-function overhead of 8 separate extension invocations

**Approach B: Shared materialization** (medium win, ~0.5s)

- Same as Issue 1, Approach C — share `_onager_int`/`_onager_e` temp tables between `graph_metrics` and `closeness_centrality`
- If `graph_metrics` and `closeness_centrality` are called in sequence, the temp tables are identical and should not be recreated

**Approach C: Connection caching** (small win, ~0.2s)

- Same as Issue 1, Approach A — cache the DuckDB connection so `_prepare` skips connection creation

**Recommended**: Combine A, B, and C. The biggest win is reducing the 8 separate Onager invocations (A). This targets 1.85s → ~0.6s.

### Files to Modify

- `helpers/graph/onager.py`: Check for multi-metric Onager function; optimize `_GRAPH_METRIC_SQL`
- `helpers/graph/algorithms.py`: Share connection across graph metric calls
- `helpers/graph/query.py`: Expose generation for cache key

### Verification

- `python3 -c "from helpers.graph.algorithms import graph_metrics; graph_metrics()"` should drop from 1.85s to ~0.6s
- `make perf` should show `graph_rebuild` within budget with margin

---

## Priority Ranking

| Priority | Issue | Current | Est. After | Effort | Win |
|---|---|---|---|---|---|
| 1 | Issue 5 (`graph_metrics`) | 1.85s | ~0.6s | Medium | High (impacts `graph_rebuild` budget) |
| 2 | Issue 1 (`closeness_centrality`) | 1.70s | ~0.5s | Medium | High (impacts graph algorithms) |
| 3 | Issue 4 (`_splice_sources`) | 1.45s | ~0.5s | Small-Medium | Medium (impacts `derive_insights`) |

All three share a common optimization: **connection/materialization reuse** across Onager calls. This suggests a unified fix (shared connection + cached temp tables) would address Issues 1 and 5 simultaneously.

---

## Implementation

_Revised 2026-09-10 after a correctness review of a first attempt (reverted)._
_The attempt cached the DuckDB connection + `_onager_*` temp tables in
module-level state in `onager.py`. It broke the `query.connect()` test seam
(integration tests silently computed against the production DB — caught by
`test_full_projection_disconnected`), risked serving stale mixed-projection
temp tables when alternating `edge_types`, and its measured win (~0.1–0.2 s
per call) was noise next to the added complexity. Note the appendix caps the
connection+materialization overhead at ~0.23 s/call; the B/C estimates above
were inconsistent with it._

### Phase 1 — `closeness_centrality` result cache (algorithms.py only)
Mirror the P2.3 query-result-cache pattern `graph_metrics()` already uses:
a generation-keyed result cache invalidated by `clear_graph_cache()`. No
connection changes, no temp-table changes — the `query.connect()` seam and
test isolation stay intact. Repeat calls (API surface) become free.

### Phase 2 — Issue 5 Approach A: fewer Onager round-trips in `graph_metrics`
_Outcome (2026-09-10, evidence-based): true consolidation is NOT available
upstream; the implementable residue is a connectivity short-circuit._

Per-metric profiling (1,648 nodes / 19,261 edges): the all-pairs trio
`diameter` + `radius` + `avg_path_length` is ~95% of the metrics cost
(~1.0 s apiece — each internally repeats the same all-pairs BFS); the other
five metrics total ~0.16 s. Consolidation candidates, all measured:

* Multi-metric function (`onager_mtr_all`): does not exist — all 64
  extension functions enumerated.
* One `onager_pth_floyd_warshall` call deriving all three: REJECTED —
  O(n³), 24.4 s measured (vs 3.2 s for the trio), and it computes
  WEIGHTED distances (semantics differ from the unweighted trio).
* Multi-source `onager_par_bfs` (`sources BIGINT[]`): process aborts
  inside the extension. Risk #2 (cannot patch Onager) materialized.

Two levers landed instead:

1. **Connectivity short-circuit** — split `_GRAPH_METRIC_SQL` into a
   cheap-local query (density, transitivity, avg_clustering,
   assortativity, triangles — always run) and a path query (diameter,
   radius, avg_path_length) gated on an `onager_cmm_components` check
   (~10 ms; 1 component == connected). Disconnected projections return
   `None` for the trio without the ~3 s of all-pairs work — identical
   semantics (the trio is NULL on disconnected graphs by contract,
   asserted by `test_full_projection_disconnected`).

2. **Thread-parallel trio — implemented, measured, REVERTED** (revisit,
   2026-09-10). Running the three all-pairs functions concurrently on
   thread-private connections over a snapshot of `_onager_e` measured
   "3.77 s → 1.51 s" against a hand-built serial baseline — but that
   baseline was wrong: it used three private connections, each paying
   connection + LOAD + 19k-row insert setup, which is NOT what production
   runs. The shipped form is ONE statement with three packed scalar
   subqueries, and DuckDB executes independent subqueries within a
   statement CONCURRENTLY (threads=4 here): the packed statement measures
   **0.95 s** — already ~max(one metric), not the sum — while the
   thread-pool measured **1.50 s** (per-connection setup dominates).
   The thread-pool was a ~0.55 s regression and was reverted the same
   day. The packed-subquery form stays, with a comment in
   `onager_graph_metrics` documenting why. Lesson recorded below:
   benchmark against the shipped code path, not a reconstructed
   equivalent.

### Phase 3 — batched calls within one invocation (implemented)
`with_onager_connection(con)` in `onager.py`, now wiring the `--all` CLI
run. Successive computes inside the block share ONE materialisation: a
signature (graph generation + edge types for the DB path; order-sensitive
content hash for synthetic edge lists — louvain is edge-order sensitive)
is kept in the connection's TEMP schema, and `_ensure_materialized()`
skips the ~34 ms rebuild on a match. Contract, as planned:

* scoped to a single CLI/API invocation, never spanning the
  `query.connect()` seam — `con` is the caller's (honoured, never
  cached, never closed here);
* no state outlives the invocation — the active flag is thread-local
  and reset on exit, and the signature table is dropped on exit
  (`test_with_onager_connection_no_sig_leak`);
* a projection change re-materialises (signature mismatch), and calls
  OUTSIDE a block materialise afresh exactly as before
  (`test_with_onager_connection_batches_materialization`).

All 30 materialisation call sites in `onager.py` route through
`_ensure_materialized()`, so every `onager_*` function benefits with no
per-call changes. Measured A/B on `--all` (14 commands, warm process,
2026-09-10): **14 → 7 materialisations, 4.42 s → 4.30 s**. Seven is the
floor for the current command order — the loop interleaves three
projections (full / membership for pagerank-wcc-clustering /
non-membership for link-predict); grouping commands by projection would
reach ~3 but is not worth the churn. Cold `--all` process: 6.2 s wall,
dominated by extension load and the computes themselves.

## Post-implementation measurements (2026-09-10)

Production graph: 19,261 edges / 1,648 nodes, connected as a whole;
`competes_with`-only projection disconnected. In-process timings unless
noted; "cold" = first call in a fresh process with the result cache
cleared, "warm" = immediate repeat.

### Pre / post

| Operation | Pre (recorded) | Post (2026-09-10) | Lever |
|---|---|---|---|
| `closeness_centrality`, 1st call | 1.70 s (appendix cProfile) | 1.62 s | unchanged — compute-bound |
| `closeness_centrality`, repeat | 1.70 s (full recompute) | **0.001 s** | **Phase 1 result cache** |
| `graph_metrics`, 1st call (full graph) | 1.85 s (make perf) / ~1.3 s in-proc | 1.29 s in-proc | ~unchanged — trio already internally parallel (below) |
| `graph_metrics`, repeat | 0.001 s (P2.3, pre-existing) | 0.0009 s | — |
| `graph_metrics`, disconnected projection | ~1.2 s (trio computed, then NULL) | **0.21 s** | **Phase 2 connectivity short-circuit** |
| `--all` CLI, 14 commands (warm) | 14 materialisations / 4.42 s | 7 / 4.30 s | Phase 3 batching |
| `--all` CLI, cold process | ~6.6 s | 6.2 s | Phase 3 (est. pre: +7 skips) |

### All-pairs trio, isolated (same connection, warm)

| Form | Time |
|---|---|
| one metric alone (`mtr_diameter`) | 0.89–1.06 s |
| three separate `execute()` statements | 2.78 s |
| **one statement, three packed subqueries (shipped)** | **0.95–0.97 s** |
| thread-pool on private connections, 3 workers | 1.46–1.54 s (reverted) |
| thread-pool on private connections, 1 worker | 3.76 s (worst of all worlds) |

Keeping the pool with `max_workers=1` "for future scaling" was considered
and rejected: it serialises the three metrics AND pays per-connection
setup three times (3.76 s, a 3.9x regression), and each private
connection defaults to its own `threads=4` — a 3-worker pool already
oversubscribes 12 DuckDB threads onto 4 cores. The packed statement's
parallelism comes from DuckDB's own executor, whose thread count follows
the core count automatically, so it scales with hardware for free. If a
future multi-core/multi-node design ever justifies private-connection
parallelism, the recipe (and the bar it must beat) is recorded here; git
history holds the implementation.

DuckDB (threads=4) executes independent scalar subqueries within one
statement concurrently — the packed-subquery form the code has always
shipped was already near-optimal; it costs ~max(one metric), not the sum.

### Issue 4 — `_splice_sources` wrap-up (executed 2026-09-10)

Measured slice of the same-day wrap-up (Approaches A + C; B skipped):

| Change | Effect |
|---|---|
| `source_note_index` warms `_TITLE_MEMO` (A) | the index build already reads + titles every source note; populating the memo as a side effect means `edition_source_entry` never re-reads — ~1,400 reads/run eliminated |
| `_resolve_variants` guard hoisted (C) | containment-length check moved out of the per-key loop (2,597 distinct candidates x ~1,200 keys) |
| Approach B (merged_sources memo) | SKIPPED — `(id(fm), hash(text))` module state is a staleness hazard; residual per-call cost after A is ~0.05s |

Measured: derive_insights CLI 3.33s -> 2.75s (-17%); `_splice_sources`
path 1.345s -> ~0.8s. Regression test:
`test_source_note_index_warms_title_memo`.

### Lessons

1. **Benchmark the shipped code path, not a reconstructed equivalent.**
   The thread-pool's "3.77 s → 1.51 s win" evaporated against the real
   baseline (0.95 s) — the hand-built serial run paid per-connection
   setup that production never pays. The regression was caught only by
   the follow-up timing suite and reverted same-day.
2. **Packing subqueries IS the parallelism mechanism** in DuckDB;
   application-level thread pools over private connections add setup
   cost that dwarfs any residual win at this scale.
3. **Extension internals are a hard wall** (Risk #2 confirmed with
   evidence): no multi-metric function exists (64 functions enumerated);
   `floyd_warshall` is O(n³) — 24.4 s — and computes weighted distances;
   multi-source `par_bfs` aborts the process.
4. **The real wins were the boring ones**: a repeat-call result cache
   (1.62 s → 0.001 s), skipping work whose result is contractually NULL
   (1.2 s → 0.21 s on disconnected projections), and deduplicating
   materialisation within an invocation (14 → 7). Threshold-guarded
   cleverness (the thread pool) returned negative and was removed.
5. **General DuckDB concurrency rule** (extends beyond this proposal):
   a single connection SERIALISES `execute()` calls — three separate
   statements cost the sum (2.78 s), while the same three as scalar
   subqueries of ONE statement cost ~max (0.95 s) because DuckDB's
   executor runs independent subqueries concurrently and its `threads`
   setting follows core count. So for independent SELECTs, the default
   lever is _packing into one statement_ (zero setup, shared catalog);
   per-thread private connections are the fallback for isolation,
   non-packable statements (multi-statement DDL, differing settings), or
   a measured case where one executor is saturated — and they must be
   thread-partitioned (`SET threads=cores/N`) or they oversubscribe.

## Dependencies and Risks

### Common Infrastructure Needed

A **shared Onager connection pool** with generation-based cache invalidation is needed for Issues 1 and 5. This should be implemented once in `helpers/graph/onager.py` and consumed by both `algorithms.py` and `query.py`.

### Risks

1. **Connection state**: Temp tables persist on a cached connection. If a data change occurs without invalidating the cache, stale results will be returned. Must tie cache invalidation to `clear_graph_cache()` and generation checks.
2. **Onager extension internals**: We cannot modify the Onager DuckDB extension. If it internally re-materializes the graph per function call, we cannot eliminate that overhead without a multi-metric API.
3. **Concurrent access**: If multiple `derive_insights` or `graph_metrics` calls run in parallel (via `xdist`), shared connections could cause race conditions. Must use `threading.Lock` or `concurrent.futures` isolation.
4. **`_resolve_variants` containment check**: The O(index) containment fallback in `_resolve_variants` could be slow with 1,400+ source notes. If pre-building the cache doesn't fix this, a separate optimization may be needed.

---

## Appendix: Profiling Data

### `closeness_centrality` (cProfile, direct call)

```text

1.697s total | 414 function calls | 16× _duckdb.execute @ 1.659s
  └─ onager_closeness: 1.497s
     └─ _onager_named: 1.432s
     └─ _materialize_from_db: 0.034s (2 SQL creates)
  └─ duckdb_connect: 0.196s (connect to graph.duckdb + _prep_graph_connection)

```text

### `graph_metrics` (cProfile, direct call)

```text

1.847s total | 519 function calls | 16× _duckdb.execute @ 1.810s
  └─ onager_graph_metrics: 1.661s
     └─ _materialize_from_db: 0.034s (2 SQL creates)
  └─ duckdb_connect: 0.182s

```text

### `derive_insights` dry-run (cProfile, `_cli()`)

```text

3.979s total | 3,742,334 function calls
  └─ render_notes: 2.109s (1187× _splice_sources)
     └─ _splice_sources → merged_sources: 1.449s (1404× edition_source_entry)
        └─ resolve_editions: 0.941s (8955× resolve_edition_string)
           └─ _resolve_variants: 0.585s (2597 calls)
     └─ scan (file I/O): 1.081s (134× select.poll)
  └─ render_metrics_notes: 0.579s

```text

### `v_note_embeddings` (DuckDB)

```text

16,479 rows | 7 doc_types: company(9205), points_and_figures, misc, sector, super_sector, chatter, plotlines

```text

### `graph_edges` (SQLite, attached to DuckDB as `fin`)

```text

19,261 edges | 1,491 distinct sources | 1,364 distinct targets

```text

### `note_search` (SQLite)

```text

16,479 rows

```text
