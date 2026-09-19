---
title: "Persistent per-generation centrality cache — stamp v_centrality_* into graph.duckdb at rebuild"
status: proposed
filed: "2026-09-19"
executed: null
completed_md: null
area: "helpers/graph/query.py (build path + schema v17), helpers/graph/algorithms.py (read path), tests, doc/design (db_schema.md)"
---

# Persistent per-generation centrality cache — stamp `v_centrality_*` into `graph.duckdb` at rebuild

**Date:** 2026-09-19 · **Status:** PROPOSED ·
**Area:** `helpers/graph/query.py` (rebuild path, `_SCHEMA_VERSION` 16 →
17), `helpers/graph/algorithms.py` (nine centrality wrappers' read
path), `tests/test_centrality_cache.py` (new), `doc/design/db_schema.md`

## 1. Motivation

The centrality scores are a pure function of the edge set; the edge set
only changes when `research.db`'s generation counter bumps. Two cache
layers exist today and neither persists across processes:

- `_QUERY_CACHE` (P2.3, in-process dict keyed `(metric, generation,
  schema_version)`) — only the long-lived Flask app benefits;
- `graph.duckdb` (disk, rebuilt per generation) — materialises
  *tables* (`v_node`, `e_*`), not metric *scores*.

Every CLI, benchmark, and fresh worker therefore pays full cold compute
per invocation. Post-`graph_centrality_index_noise` the family computes
in ~2.4s total (closeness 1.33s + betweenness 0.92s + seven cheap
metrics) on the relationship projection — inside budget, but paid
repeatedly, and the payment grows with the conversation graph.

## 2. Design — mirror the app.py contract on disk

app.py semantics: one long-lived connection, results cached per
`(metric, generation)`, cleared when the generation moves. This
proposal moves exactly that contract into the graph cache file, whose
generation stamp (`_build_meta.generation`, already maintained and
already the `_is_warm` invalidation token) becomes the cache key. No
new invalidation machinery: the file is only read warm, and it is only
warm when its generation matches the SQLite source.

### 2.1 Storage (one table per metric, no per-row keys)

```sql
v_centrality_degree(name VARCHAR, score DOUBLE)
v_centrality_closeness(name VARCHAR, score DOUBLE)
v_centrality_betweenness(name VARCHAR, score DOUBLE)
v_centrality_eigenvector(name VARCHAR, score DOUBLE)
v_centrality_harmonic(name VARCHAR, score DOUBLE)
v_centrality_katz(name VARCHAR, score DOUBLE)
v_centrality_laplacian(name VARCHAR, score DOUBLE)
v_centrality_local_reaching(name VARCHAR, score DOUBLE)
v_centrality_voterank(name VARCHAR, score DOUBLE)
```

No `generation` column: the whole file lives or dies by its
`_build_meta` stamp — per-row keys would duplicate the file-level
contract (the same reasoning that keeps `v_node` unkeyed).

### 2.2 Build (eager, at rebuild — the operator's call)

The rebuild path (after the `v_*`/`e_*` CTAS runs) computes the nine
metrics via the existing wrappers on the build connection and CTASes
the results. Cost: ~+2.4s per rebuild at current sizes (measured;
post-exclusion projection). Rebuilds happen per generation bump (i.e.
after every mutating `--apply`), so the cost is paid per mutation, not
per consumer — accepted for simplicity (the lazy write-through
alternative is a non-goal, §5).

`_SCHEMA_VERSION` 16 → 17 (new build shape forces one fresh rebuild;
same mechanism idx_fill used for `v_index`/`e_listed_on_index`).

### 2.3 Read (readers never write)

The nine wrappers' DB path gains a fast path: read the
`v_centrality_<metric>` table on the caller's connection (a warm
`query.connect()` con already has it); non-empty → serve; empty or
missing (cold/stale/older cache) → compute as today. Readers stay
read-only; the single-writer (rebuild, flock-serialized) contract is
untouched. The in-process P2.3 layer stays in front — first read
loads the table into the dict, subsequent reads hit the dict.

### 2.4 Surfaces checklist (misses cause silent empties or gate reds)

| # | Surface | Change |
|---|---|---|
| 1 | `query.py::_SCHEMA_VERSION` | `"16"` → `"17"` |
| 2 | rebuild path | compute + CTAS the nine tables after the e_* pass |
| 3 | materialised-table registry | register the nine (whatever enumerates v_*) |
| 4 | `db_schema.md` | new table rows + object count |
| 5 | algorithms CLI | `--compute` flag: bypass the cache read (§2.5) |
| 6 | `tests/run_perf_benchmarks.py` | point the centrality legs at `--compute` so budgets keep measuring COMPUTE, not cache I/O |

### 2.5 Benchmark honesty

With the cache in place, a plain CLI run serves from disk (~0.4–0.5s)
and the perf budgets stop guarding the compute path. The benchmarks
therefore pass `--compute` (bypass cache read, force onager) — the
budgets keep their current meaning, and the cache path gets its own
cheap smoke assertion in tests instead.

## 3. Expected effect (measured baseline)

- CLI `closeness --top 10`: 1.51s → ≈0.45s (startup + connect + SQL
  read). Same family shape for the other eight.
- App restart: zero centrality compute (today: one per restart per
  generation).
- Wave-2 exposure for the family: eliminated — cache read is O(rows)
  regardless of graph growth; only the rebuild cost grows.

## 4. Verification

- Eval gate: no research.db mutation — identical-DB pass, recorded per
  the house rule for query-visible changes.
- New `tests/test_centrality_cache.py`: (a) warm rebuild → wrappers
  serve table contents equal to fresh compute; (b) generation bump →
  rebuild re-stamps (scores track the new edge set); (c) read-only
  reader never writes (no `.wal` appears); (d) `--compute` bypass
  recomputes and does not read the table.
- Existing: `test_centrality_projection.py` (exclusion semantics
  unchanged), `test_graph_disk.py`, `test_api_graph_unit/live.py`,
  `test_note_embeddings.py`.
- `make perf` with the §2.5 wiring: same budgets, compute still
  measured.

## 5. Non-goals

- **Lazy write-through** (first reader computes and persists under the
  build flock) — pays only on demand but needs a reader→writer
  transition; rejected for complexity while rebuild frequency is low.
  Revisit only if rebuilds become very frequent relative to reads.
- **`graph_metrics` persistence** (~300ms, already P2.3-cached
  in-process for `/api/graph/stats`) — add the same pattern later if
  that route ever serves cross-process.
- **Louvain** — the §9.7 decision is taken (excluded, same rule);
  louvain joins this cache as a tenth table (`v_centrality_louvain`:
  name, community_id) when this proposal executes.

## 6. Risks

- Rebuild cost +~2.4s per mutation apply (accepted; §2.2). Mitigation
  if it ever bites: a `--skip-centrality` maint escape hatch — not
  built now.
- Stale-serve risk is the existing `_is_warm` contract's risk — no new
  failure mode introduced (the tables cannot outlive their generation:
  the file rebuilds as a unit).
- Two-layer caching (dict + table) doubles the places a bug can hide;
  the equality test (§4a) is the guard.

## 7. References

- `../archive/graph/graph_centrality_index_noise.md` (#254) — §3 deferred this cache
  with triggers; this proposal is the filed design. Triggers stand:
  build when cold compute approaches budget or centrality enters hot
  cross-process paths.
- `helpers/graph/query.py` — `_QUERY_CACHE` (P2.3), `_is_warm`,
  `_mark_warm`, the flock-serialized build path.
- Measured baselines 2026-09-19, generation 112773: closeness 1.33s /
  betweenness 0.92s compute; connect 0.16s; CLI end-to-end 1.51s /
  1.17s.

**Follows:** `../archive/graph/graph_centrality_index_noise.md` (#254, same arc). **Precedes:**
none yet.
