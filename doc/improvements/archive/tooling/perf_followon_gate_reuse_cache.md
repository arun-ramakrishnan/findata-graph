---
title: "Four perf follow-ons: live-lane scheduling, wire-connection lease pool, cross-run template cache, perf-harness SKIP visibility"
status: executed
filed: "2026-11-24"
executed: "2026-11-24"
completed_md: "568"
area: "app.py + tests/_tmp_hygiene.py + tests/run_perf_benchmarks.py"
---

# Perf follow-ons: gate orchestration, wire-connection reuse, template cache, SKIP visibility

Follow-on work recorded when the 2026-11-24 perf regression sweep
(`test_coverage_gap_sweep_nov24.md`, Perf follow-on sections) hit
scope/doctrine boundaries. Each item below is a standing, measured
candidate — deliberately not implemented in the sweep, with the reason.

## 1. Gate orchestration: live-invariants writer/reader split

- **What:** under `pytest -m live -n auto --dist=loadscope`, the
  graph-writer module (`tests/test_graph_disk.py`) and the
  graph-reader modules (`tests/test_api_graph_live.py`,
  `tests/test_scipy_bridge.py`, `tests/test_l1_betweenness.py`,
  `tests/test_graph_stats.py`) share an xdist window. Parallel
  wire-readers queue on the writer's `build.lock` — the measured
  effect is `test_refresh_resets_connection` at 186.9s under advisory
  gate 4777 vs ~3.1s isolated (the per-request connect cost itself is
  ~0.5s).
- **Reason parked:** changes gate scheduling, not code; the sweep's
  fix (served-open takes no flock, protocol (c) warm RO path) already
  removed the lock queue for served stores, so the remaining win is
  wall-clock hygiene under xdist, not a functional defect.
- **Candidate:** a `--dist=loadscope` group key that pins
  graph-writers and graph-readers to different workers (`xdist_group`
  markers exist but are inert on pytest 9.2.0 + xdist 3.8.0 — see
  `tests/test_graph_stats.py` header; needs a loadscope-compatible
  mechanism).
- **Acceptance:** advisory live-invariants leg wall returns toward the
  pre-2026-11-22 ~219s baseline (was 359.7s in 4777).
- **Executed 2026-11-24 — resolved by verification, no code:** the
  exact gate lane shape (`.venv/bin/python3 -m pytest -m live -n auto
  --dist=loadscope`) ran at **203.5s wall, 271 passed / 6 skipped**
  — below the ~219s baseline with MORE tests collected (277 vs ~186
  in 4777: the retired-path sweep re-enabled `test_graph.py`'s 85 and
  `test_db_maint_duckdb`'s 6), and `test_refresh_resets_connection`
  no longer appears in the slowest 15. Protocol (c) (served-open takes
  no flock) had already removed the writer/reader queue; the slowest
  entries are now real work (donor setup 59.1s, acyclicity 33.4s,
  serving-stack pins 14–26s). The `xdist_group` gap stays recorded
  for the day lane walls grow again.

## 2. Per-request wire-connection reuse under a read-only lease

- **What:** every Flask request re-opens the served graph store over
  the quack wire (`_attach_sqlite`: `CREATE OR REPLACE SECRET
  fin_quack` + `ATTACH … TYPE quack, READ_WRITE` per request), plus
  the `_is_warm`/staleness probe chain (`_check_generation_staleness`,
  `_check_duckdb_version_drift`, `_probe_note_embed_state`).
- **Reason parked:** reusing one wire connection across requests within
  a worker touches the CONC-1 per-request-connection contract — a
  design change, not a local fix; and it must stay read-only (no
  cross-request write state).
- **Candidate:** per-worker pool under a read-only lease (flask.g
  lifetime + RO guarantee), with the staleness probe retained at a
  lower frequency.
- **Acceptance:** peer-request latency median unchanged or lower while
  the refresh test's re-attach cost disappears; no write-visible
  behavior change.
- **Executed 2026-11-24:** per-worker lease pool in `app.py` —
  `_GRAPH_POOL_IDLE_TTL = 5.0`, `_GRAPH_POOL_CAP = 4`, `_pool_borrow`
  (pop newest, drop entries idle past the TTL, liveness probe
  `SELECT 1`, closes outside the lock), `_pool_return` (park with a
  timestamp; cap overflow closes the oldest), `_pool_drain` (refresh
  and fixtures). CONC-1 is preserved exactly: a lease is exclusive —
  a checked-out connection is never loaned twice, and a connection
  from an errored request is closed, never re-served; only sequential
  reuse is amortized. `_reset_graph_connection` drains the pool before
  the rebuild's read-write reopen. Pins: `TestGraphConnectionPool` in
  `tests/test_api_graph_unit.py` (second borrow reuses, concurrent
  borrows never share, idle-TTL eviction, dead-entry skip, refresh
  drain, cap overflow) plus two rewritten teardown/distinct pins —
  **103 passed**. Measured: `tests/bench_routes.py` best-of-3 three
  runs = 2.71/2.80/2.77s vs 4.07–5.62s pool-less history.

## 3. Persistent cross-run shared-template cache

- **What:** the once-per-run fixture build (`_tmp_hygiene.py`
  `_shared_template_locked`) restores the 604,622-row legacy donor via
  `restore_sqlite_from_parquet` (~16.3s) even when the schema-only
  fast path could serve a consumer; trimmed/full templates must always
  pay it.
- **Reason parked:** caching templates across runs changes the
  once-per-run freshness contract (a template could serve a stale
  snapshot after the store evolves) — a semantic change, not a pure
  win.
- **Candidate:** persist the three shared templates keyed by a
  parquet-dir fingerprint (size/mtime of the 30 parquet + DDL) under a
  gitignored cache dir; cross-run setup tax → ~0 after the first run.
- **Acceptance:** identical template bytes for an unchanged fingerprint;
  a changed fingerprint rebuilds; suite setup wall reflects the cache
  hit.
- **Executed 2026-11-24:** `tests/_tmp_hygiene.py` persists both
  templates under `outputs/.template_cache/` (gitignored), keyed by
  `TEMPLATE_CACHE_VERSION` + a fingerprint over the axis (sorted
  path/size/mtime_ns of every parquet + the DDL). Publish is atomic
  (`.part.` build → `os.replace`) under a per-kind flock with a size
  sidecar as the completeness marker; `_prune_template_cache` keeps
  the newest two fingerprints per kind. Consumers still get a fresh
  per-run copy (`reflink_or_copy` from the cache), so per-run
  isolation and the read-only donor contract are unchanged. Pins:
  `TestTemplateCache` in `tests/test_tmp_hygiene.py` (axis moves the
  fingerprint, hit serves bytes without rebuilding, miss builds once
  and a second run reuses identical bytes, changed axis rebuilds,
  prune keeps two) — 28 passed. Measured on the real axis: schema
  template 1.50s first run → **0.01s** on cache hits; the next gate
  run's donor restore drops out of the critical path entirely.

## 4. Perf harness: a SKIP is not an OK

- **What:** `tests/run_perf_benchmarks.py` `run_one` scores
  `ok = rc == 0 and dt < budget`. Two legs deliberately exit 0 with a
  `SKIP:` note when the live store or an extension is absent, so an
  absent store reads as a fast pass: from 2026-11-22 (the research-store
  cutover deleted the file their gate named) `l1_kernels` scored ✓ OK at
  0.19–0.27 s against a 240 s budget and `ppr_edge_attribution` at
  0.37–0.51 s against 90 s — seven runs each, where the same legs took
  67.6–78.8 s and 5.5–7.6 s on 2026-11-21. Forced perf run 4760 reported
  "32/35 within budget" with both vacuous passes counted.
- **Reason parked:** exit-0-with-SKIP is the documented contract for
  optional modules and other legs rely on it; changing how the harness
  scores is a gate-semantics change. The two legs' gates are fixed
  regardless — they resolve the store through `tests/_live_store.py`
  (coverage proposal, "Live legs keyed to the retired store path").
- **Candidate:** a leg declares its skip (a `SKIP:` line on stdout, or a
  dedicated exit code) and the harness records SKIPPED — neither OK nor
  FAIL — with the report footer counting it on its own.
- **Acceptance:** a leg that skips never appears under the passed count;
  the footer's passed number equals the legs that actually ran.
- **Executed 2026-11-24:** `tests/run_perf_benchmarks.py` grew a SKIP
  contract: `run_one` returns `(dt, status, ok, tail, skipped)` (a
  declared skip = exit 0 + a `SKIP:` stdout line) and legs carry a
  7-tuple with `skipped`; skipped legs print `⏸ SKIPPED`, are excluded
  from the FAILED section, never tag as load-fragmented (contention
  still forces attribution), and the footer reads
  `N/M passed, K skipped` with exit 0 only when `passed + skipped ==
  total`. `perf.json` rows record `"status": "warn"` +
  `"raw_status": "SKIPPED"` (the `gate_query` adapter accepts
  pass/fail/warn/info — never emit an unknown status); the report
  gains `## <leg> (SKIPPED)` sections. Parsed consumers stay
  compatible (`search_tui` skips unparsable footer rows;
  `gate_query` maps SKIP→info). Pins: `tests/test_run_perf_benchmarks.py`
  rewritten for the 7-tuple shapes — **37 passed**.

## Status

**EXECUTED 2026-11-24** (archival rides the gate arc; `completed_md`
filled at move time). Item 1 resolved by verification (203.5s lane,
below baseline, more tests); items 2–4 implemented with pins and
measurements as recorded per item. The sweep's own fixes (served
no-flock connect, schema fast path, fused_derive, harvest dry-run,
static-checks DuckDB user_version) stand on their own in the coverage
proposal. Items 1–3 were recorded on the operator's instruction ("the
3 follow ons"); item 4 was added the same day when the retired-path
sweep showed two perf legs passing vacuously.
