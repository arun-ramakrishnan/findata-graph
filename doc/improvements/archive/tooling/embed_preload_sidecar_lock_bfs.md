---
title: "Sidecar reader lock queueing, embed-cache dict preload, and a de-pathologized bench probe — the 2026-11-19 perf sweep"
status: executed
filed: "2026-11-19"
executed: "2026-11-19"
completed_md: "546"
area: "helpers/maintenance + helpers/core + tests/bench"
---

# Sidecar reader lock queueing, embed-cache dict preload, and a de-pathologized bench probe

**Date:** 2026-11-19 · **Status:** EXECUTED 2026-11-19 (archived 2026-11-19, completed.md #546) ·
**Area:** `helpers/maintenance/gc_embed_cache.py`, `helpers/maintenance/rebuild_*.py`,
`helpers/maintenance/rebuild_common.py`, `helpers/core/embed_cache.py`,
`tests/bench_shortest_path.py`, `tests/run_perf_benchmarks.py`

## 1. Where this proposal comes from

The 2026-11-19 perf-unlock survey (prompted by "anything in mojo/rust/sqlite/duckdb
or perf we can unlock") walked `doc/improvements/pending.md`, perf run 4302, and the
gate indexes, and produced three actionable items plus one crash already in hand:

1. `make embed-gc` (report mode) crashed on a held DuckDB lock — reader had no
   queueing, while every query CLI already had it (`duckdb_lock`).
2. The `CachedEmbed` point-lookup preload, deferred candidate (2) of
   `duckdb_rebuild_incremental_carry` (#528 §6): 4,576 lookups × 1.7 ms ≈ 7.8 s
   of every doc-apply wall. (The adjacent derive-insights serial-default fix
   and its budget retune are recorded in #545's execution record, not re-filed
   here.)
3. `shortest_path_bfs` sitting at 11.86–12.0 s against a 12.0 s budget (99%,
   growth 8.1–10.2 s → 11.9 s with the inner per-route asserts healthy at
   8–13 ms — i.e. the wall was somewhere OUTSIDE the measured cases).

Two doctrine corrections rode along and are recorded in §5.

No query-visible semantics changed anywhere in this sweep: the cache hit path is
byte-identical by construction (content-addressed rows), the bench fix preserves
the same worst-case traversal, and lock queueing only delays opens. No eval-gate
bullet is required.

## 2. S1 — sidecar readers queue on a held lock

**Bug (operator-reported):** `make embed-gc` crashed:
`_duckdb.IOException: IO Error: Could not set lock on file
"/home/arun/Research/findata-graph/memory/embed_store.duckdb": Conflicting lock is
held in …/python3.14 (PID 678232)` at `helpers/maintenance/gc_embed_cache.py:60`
(`_open_store`, trace `main:667` → `survey:473` → `_classify:430` →
`_open_store:381`). The holder was a background `convo-fresh` APPLY.

**Fix:** `_open_store` routes through
`helpers.misc.duckdb_lock.connect_with_lock_retry(..., delays=_EXTENDED_RETRY_DELAYS_S)`
— the same 2/10/48 s = 60 s long-holder ladder the query CLIs use; both read-only
(report) and read-write (gc/retire) modes queue; non-lock errors still fail fast on
attempt 1.

**Audit ("check for other readers with similar bug"):** 63 files touch
`duckdb.connect`, 28 already use `duckdb_lock`. Seven raw `.duckdb` openers fixed,
all wrapped in the same ladder:

- `helpers/maintenance/rebuild_doc_search.py:152` `connect_doc_db` (serves
  `doc_query` + rebuild APPLY)
- `helpers/maintenance/rebuild_git_search.py:229` `connect_git_db`
- `helpers/maintenance/rebuild_script_search.py:211` `connect_script_db`
- `helpers/maintenance/rebuild_memory_search.py:165` `connect_memory_db`
- `helpers/maintenance/rebuild_note_search.py:100` `connect_note_db`
- `helpers/maintenance/rebuild_convo_search.py` — five opens wrapped
  (`:144`/`:155` provenance migration probe+write, `:181`/`:192` bili migration
  probe+write, `:1070` `_drop_ann_indexes`)
- `helpers/maintenance/rebuild_common.py:117` `_open_stamp_db` (APPLY decision
  input — a held lock previously read as "no stamp", i.e. a spurious full rebuild)

**The nastiest subclass:** the two convo migration PROBES swallowed
`except duckdb.Error: return False` — a held lock was indistinguishable from
"table absent", sentencing the next APPLY to a pointless ~45-min re-embed. They
now queue first (60 s ladder), then fall back to the absent reading on a fresh
store. Excluded as not-bugs: `:memory:` scratch opens, one-shot bench/research
probes, `quack_server_child` (holder by design).

**Tests:** `tests/test_sidecar_lock_queue.py` (parametrized over the five
dispatch openers + `_open_stamp_db` + `_provenance_migration`: transient lock
retried, EXTENDED schedule selected, probe still reports absence on a fresh
store) and `tests/test_embed_gc.py::TestOpenStoreQueuesOnALongHolder`. Test
lessons: `connect_with_lock_retry` binds `sleep=time.sleep` at def time — stub
`lock_retry_delay`, not `time.sleep`; `_provenance_migration` gates on
`db_path.exists()` — pre-create the file with a REAL connection before
monkeypatching. 190 tests green across the touched suites; ruff clean.

## 3. S2 — CachedEmbed dict preload (#528 deferred candidate, closed)

**Design:** `CachedEmbed.preload()` (helpers/core/embed_cache.py) loads every
`(text_hash, embedding)` row for the model with ONE scan into an in-memory dict;
`__call__` serves hits from the dict and a dict miss **is** a cache miss (the
dict is model-complete — no SQL probe on that path). The per-call point SELECT
runs only when preload was never attempted or failed. `_store_vec` and the
donor-hit branch update the dict, so mid-pass writes stay coherent.
Content-addressed rows are immutable — the same doctrine that makes the S4b
donor map safe — so the snapshot cannot go stale for the hashes it holds.
`status_line()` surfaces `preloaded=N`. Opt-in per construction site; both doc
rebuild sites (`rebuild_doc_search.py:627` incremental, `:1623` resolver) call
it; note/script/memory sites unchanged.

**Measured (live `memory/doc_search.duckdb`, read-only):** 4,807 rows —
preload scan **44.2 ms** vs point-lookup median **0.91 ms** (≈4.4 s projected
per apply pass; #528 measured 1.7 ms/lookup on a busier box ≈ 7.8 s). The scan
is ~100× cheaper than the lookups it replaces; warm doc-apply drops from
~17 s toward ~9–12 s. (Derive-insights context: the same session set
`derive_insights` workers default to serial — 30.8 s pooled vs 7.9 s wall,
byte-identical output — and re-benched the leg at **7.99 s**; budget
9.0 → 12.0 in `tests/run_perf_benchmarks.py`.)

**Tests:** `tests/test_embed_cache.py::TestCachedEmbedPreload` ×3 — dict hit
without SQL (proven structurally: drop the table post-preload, the hit still
returns), dict miss → embed → repeat served from dict, failure → SQL degrade.
Test lesson: DuckDB connections reject attribute patching (`execute` is
read-only), so no-SQL is proven by removing the table instead of spying on
`execute`; and the cache DDL only exists after the constructor's `_try_init`,
so seed rows AFTER construction. 71 + 49 tests green; ruff clean.

## 4. S3 — shortest_path_bfs: the wall was the probe, not the BFS

The leg ran 11.86–12.0 s against a 12.0 s budget while its measured cases were
healthy (default 15.9 ms, unreachable 93.2 ms best-of-3). Decomposition on the
live box:

- `connect()` 0.6–0.7 s · warm-ups 0.03/0.11 s · measured cases ~0.1 s
- `_unreachable_dst()` **10.65 s** — a recursive CTE walking CEAT's whole
  component via per-iteration UNION anti-joins over `e_all_und`.

`e_all_und` itself is cheap — 201,534 rows, full scan 0.07 s, fully symmetric
(both orientations per dyad), and the BFS traverses the same component in
<100 ms — so the CTE was pure planner pathology (a correlated `NOT EXISTS`
over the same relation costs 13.7 s too).

**Fix:** `tests/bench_shortest_path.py::_unreachable_dst` now fetches the edge
pairs once (0.07 s), builds an adjacency dict, runs a plain Python BFS from
CEAT, and returns the first `v_node` name outside the component (`ORDER BY
name` semantics preserved; isolated nodes qualify exactly as before). The
returned destination still forces the full source-component traversal in the
measured unreachable case — worst-case semantics unchanged.

**Measured:** leg wall **11.9 s → 1.21 s**; same destination picked
(21st Century Management Services); default 0.7 ms, unreachable 80.5 ms.
Budget stays 12.0 (operator-owned; ~10× headroom now). Calibration comment in
`tests/run_perf_benchmarks.py` updated.

## 5. Doctrine corrections recorded

- **psi is not a baseline.** The `psi avg10 16.6%` fragment in perf FAIL tags
  is Linux Pressure Stall Information (/proc/pressure/cpu) at leg time —
  contention ATTRIBUTION, not a drift reference. The ceilings
  (`_PSI_SOME_CEIL=10.0` start guard, `_PSI_SUSTAINED_CEIL=15.0` attribution
  ceiling, `tests/run_perf_benchmarks.py:119-120`) are diagnostic guards; there
  is nothing to re-seat.
- **Mojo is not retired** (operator, 2026-11-19). Only the goal of replacing
  Python with Mojo was retired (#437 — the std.python bridge holds the GIL, so
  Mojo cannot follow the PyO3 model for in-process production kernels). All
  other Mojo perf work continues.

## 6. Verification

- `tests/test_embed_cache.py` 22 · `tests/test_embed_gc.py` 32 ·
  `tests/test_gemma_adoption.py` (batch 71 total) · `tests/test_rebuild_doc_search.py` 49 ·
  sidecar-lock batch 190 · ruff check + format clean on every touched file.
- Live wall checks: embed-gc report mode reads 242,854 cache rows through the
  ladder; doc sidecar preload scan 44.2 ms/4,807 rows; bench leg 1.21 s.
- Derive-insights output byte-identical across the workers change
  (quotes=9036, metrics=4999→4085 apply rows).

## 7. Left open (not this sweep)

1. Delta-apply threshold fallback — 100%-changed corpus 26.9 s delta vs 17.0 s
   full (#528 candidate 1, still OPEN in pending.md).
2. `--allow-stale` stamped legs applying as full rebuilds (#528 candidate 3).
3. `cosine_knn_ratios` budget 15 → 25 s — operator gate-tuning call (S4 arc
   already uses 25.0 in the harness).
