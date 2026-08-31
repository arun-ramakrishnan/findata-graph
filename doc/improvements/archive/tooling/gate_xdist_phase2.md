---
title: Gate parallelism phase 2 — live-invariants xdist safety and the advisory floor
status: executed
filed: '2026-08-31'
executed: '2026-08-31'
completed_md: '189'
area: tests/run_gate_report.py
---

# Gate parallelism phase 2 — live-invariants xdist safety and the advisory floor

**Date:** 2026-08-31 · **Status:** EXECUTED 2026-08-31 (completed.md #189) ·
**Area:** `tests/run_gate_report.py` · `helpers/graph/query.py` (locking) ·
live-test fixtures (`tests/test_graph*.py`, `tests/test_db_maint_duckdb.py`,
`tests/test_graph_stats.py`, `tests/conftest.py`)

> **Measured outcome (2026-08-31):** live-invariants is xdist-clean —
> standalone `pytest -m live -n auto` 218/218 across 5 runs (43.8–51.2s;
> serial was 72.5s); in-gate 5/5 green at 76–87s (serial in-gate was
> 89–96s). `make advisory` wall ~94s → **76–87s**, 11/11. Slice B: serial
> live suite 72.5s → **57.4s**. Execution found a second collision the
> design missed: the advisory gate runs TWO concurrent pytest invocations
> (live-invariants AND integration) whose workers are BOTH named gw0..gw3
> — worker-index keying shared files across invocations and failed 4/5
> advisory runs until the redirect key became `worker-pid` (details in
> §4.1). Integration's betweenness scaling guard flaked 2/5 under the
> 8-thread overlap; replaced independent-minima ratios with paired
> rounds (min per-round ratio) — 5/5 green after.
>
> **Final state (2026-08-31, post-archival addendum — see Appendix 2):**
> Slice C was resolved ADVISORY-SIDE (user directive — the reverse of
> §7's qa-side option): advisory dropped its integration step entirely;
> the 551 integration tests gate via qa's `-m "not live"` only (or
> `make integration` standalone). Removing integration's 4 workers
> un-stretched live-invariants back to its solo shape, so the final
> advisory floor is **45.5s** (10/10), live-invariants in-gate **45.4s**.
> Full-arc: `make qa` 114 → ~65–76s, `make advisory` 89–94 → **45.5s**,
> both gates back-to-back **~203s → ~110s**.

Phase 1 (xdist wiring, EXECUTED same day, no completed.md entry yet — this
proposal's §2 documents it; on joint archival, one entry can cover both) cut
`make qa` from 114s to 65s. What remains is the **advisory floor**:
`live-invariants` is the last serial suite, it bounds `make advisory` at
~94s, and it cannot run under pytest-xdist today because of a DuckDB
locking-model race in `query.connect()`. This proposal files the measured
case, the design options, and the shakedown plan for closing that gap.

## 1. Diagnosis recap (measured 2026-08-31, 4C/4T box)

The "massive slowdown" report resolved to **no regression** — the gate
reports append a per-step timing table on every run
(`qa_report.txt` / `advisory_report.txt`, written by
`tests/run_gate_report.py`; mine those FIRST for slowness questions) and
per-step times are FLAT since records began 08-28. What grew is what the
steps contain:

| Finding | Number |
|---|---|
| qa's pytest step = qa's critical path | 114s of a 114s wall (serial `-j1` total: 141s) |
| Suite growth (def test_ counts via git) | 1,944 (Aug 20) → 2,543 (Aug 31) = +31% / 11 days |
| Per-test floor | 2,423 tests × ~47ms avg; top-20 durations sum ≈ 30s (no single offender) |
| Collection overhead | 2.4s (irrelevant) |
| `-j 8` vs `-j 4` pre-xdist | 114s vs 136s — 8 won only by starting pytest at t≈0 |
| `-j 8` vs `-j 4` post-xdist | 66.5s vs 65.2s (qa); 95.7s vs 94.0s (advisory) — **wash** |

Decision embedded: **`-j` defaults to 4 (`_DEFAULT_JOBS`), use it bare**;
suite-internal `-n auto` is decoupled from `-j` on purpose (step
concurrency ≠ core count; `-j 8` would put 8 xdist workers inside a suite
on 4 cores).

## 2. Phase 1 — executed 2026-08-31 (context for reviewers)

| Change | File |
|---|---|
| `-n auto` on the qa pytest step + the integration gate step | `tests/run_gate_report.py` |
| `-n auto` on the `test` / `fuzz` make targets | `Makefile` |
| `con` fixture opens the REAL graph.duckdb **read-only** when it exists (was RW — an exclusive lock held per test, racing live-invariants and its own xdist workers) | `tests/test_integration_perf.py` |
| Both time-RATIO guards hardened: summed bursts (above the 1ms skip threshold, same factor both sizes) + best-of-3 (single samples flaked 3.6× vs a 3.0 budget under xdist CPU contention) | `tests/test_performance.py` |
| `live-invariants` stays serial, with the reason documented at both call sites | `Makefile`, `run_gate_report.py` |

| Result | Before | After |
|---|---|---|
| `make qa` wall | 114.0s | **65.2s** (1.75×) |
| qa CPU utilization | ~1.4 cores | ~3.1 cores |
| integration suite standalone (551 tests) | 60–79s | 30.0s (~2×) |
| integration gate step | 61.6s | 40.3s (contended) |
| `make advisory` wall | 89–96s | 94–96s (**unchanged — live-bound**) |

Shakedown evidence: 2,423 passed under `-n 4` (no shared-state breakage in
the default suite); the two failures the shakedown exposed (the RW fixture,
the flaky guard) are fixed, not suppressed.

## 3. The advisory floor, measured

`live-invariants` = 218 live tests, one pytest process:

| Measurement | Number |
|---|---|
| Solo serial wall | **72.5s** (`--durations` run, 2026-08-31) |
| In-gate wall (overlapped with integration's 4 workers) | 94–96s (+21s contention) |
| Avg per test | ~330ms (7× the main suite — real-DB fixtures) |
| Top-20 durations | ≈38s: BFS CTE-oracle tests ~8s (`test_graph.py`), DuckDB backup tests ~7s (`test_db_maint_duckdb.py`), 8 `graph_stats` print tests ~10s, `test_graph_disk` rebuild/ro-serialize ~6s |

Scheduling facts (why reordering is REJECTED, measured not guessed):

- integration starts at t≈3s (a worker frees when ty-tests ends) and
  finishes at t≈44s — entirely hidden under live-invariants' span.
- Serializing in either order = 72s + 30s ≈ **102–105s > today's 94s**.
  The overlap costs ~31s of mutual contention but hides ~44s; it is the
  optimal arrangement available while live stays serial.
- `-j 4` vs `-j 8` makes no difference post-xdist (both ~94–96s).

## 4. Root cause: `query.connect()`'s lock covers openings, not lifetimes

`helpers/graph/query.py` already serializes cold/stale **builds** through
an flock on a `<duckdb>.build.lock` sidecar (2026-08-26, for the advisory
gate-steps case). But:

1. The flock is released in the `finally` of `connect()` — while the RW
   connection it returns **stays open** in the caller. DuckDB file locks
   are held for the connection's lifetime: an open RW connection excludes
   every subsequent open in other processes, **including read-only ones**.
2. The warm fast-path (`read_only and not needs_build`, line ~440) opens
   OUTSIDE the flock — a builder in another process can acquire RW between
   the staleness probe and the open.

Under `pytest -m live -n 4`: 67–72 errors at setup, all
`IOException: Could not set lock on file .../memory/graph.duckdb:
Conflicting lock is held`. Serial: 218/218 green. Any test whose fixture
builds/rebuilds/opens RW (graph rebuild, backup, api-refresh tests) creates
a window every other worker can stumble into for the connection's whole
life.

## 5. Slice A (main): make the live suite xdist-safe

Design options, to be decided by a half-day spike:

- **Option B — per-worker cache files (test-only, RECOMMENDED first).**
  When `PYTEST_XDIST_WORKER` is set, a `conftest.py` fixture/patch points
  `query.DUCKDB_PATH` at `graph.xdist-<worker>.duckdb` (and redirects the
  `.build.lock` sibling). Each worker builds its own cache once (~2–4s,
  vs the current 72s suite — amortized noise), then all opens are RO.
  Zero production-code change, perfect isolation, no new lock semantics.
  Risk: tests asserting cross-process cache invalidation semantics
  (generation bumps vs `_is_warm`) must keep using the REAL path — those
  need an explicit opt-out marker or `loadgroup` pinning.
- **Option A — lifetime locks (production change).** Tie the flock to the
  connection's lifetime (e.g. a `connect(..., serialized=True)` wrapper
  that releases on close, or an RO fast-path moved under the lock).
  Correct for the general case (two human-run maintenance scripts also
  race today), but it changes runtime locking semantics for `app.py` and
  every helper — bigger blast radius, needs its own shakedown.
- **Option C — mixed scheduling (`--dist loadgroup`).** Pin RW-holding
  test files to one xdist group, RO tests spread freely. Cheapest, but
  cements the special cases into markers; prefer B unless the spike finds
  more than ~5 RW tests.

Whichever option: `pytest -m live -n auto` goes into the gate runner's
`live-invariants` step (replacing the documented-serial make invocation)
only after the acceptance shakedown in §8. `make live-invariants` itself
gains `-n auto` in the same change.

## 6. Slice B (independent): slim the live suite serially

From the durations table (§3): cache the materialised graph across the 8
`test_graph_stats` print tests (module-scope fixture, ~10s → ~2s), run
`test_db_maint_duckdb` backup cases against one pre-built tmp DuckDB
(~7s → ~3s), and consider trimming BFS oracle parameter grids (~8s → ~4s).
Target: 72.5s → ~55s serial. Worth doing regardless of Slice A — it
shrinks the post-xdist residual too. Est. effort: half a day.

## 7. Slice C (optional lever, default OFF): split integration+fuzz out of the default gate

qa's `-m "not live"` currently includes the 551 integration tests and the
fuzz modules, which advisory's integration step then re-runs. Excluding
`-m "not integration and not fuzz"` from qa's pytest would take ~65s →
~48s. RECOMMENDED AGAINST for now (the double-run is cheap post-xdist, and
the default dev invocation silently skipping integration tests is a
footgun) — recorded as the lever to pull only if qa must drop below 50s.

## 8. Acceptance criteria & shakedown

1. `pytest -m live -n auto` green twice consecutively (218/218, zero
   `IOException`/lock errors), then **5 consecutive `make advisory -j 4`
   PASSes** before the serial fallback is removed.
2. Advisory wall ≤ 60s (projected: live ~25–35s + integration ~40s
   overlapping; both contend at 4+4 workers on 4 cores — total CPU
   ~175s / 4 cores ≈ 45–60s floor).
3. `make qa` stays 8/8 and ≤ 70s; `snapshot-check` green in every run.
4. No new flakes over a 5-run window: step times within the report
   history's existing noise band (pytest 97–154s historical spread says
   single runs don't diagnose — the report tables do).

| Projected outcome | Today | After Slices A+B |
|---|---|---|
| `make advisory` | ~94s | **~50–60s** |
| `make qa` | 65s | 65s (48s if Slice C pulled) |
| Both gates back-to-back | ~160s | **~110–125s** |

## 9. Risks

- **Silent semantic drift (Option B):** a worker-local cache could mask a
  real cross-process invalidation bug the live suite exists to catch.
  Mitigation: keep the cache-invalidation tests on the real path (explicit
  real-path marker), and keep `make live-invariants` serial as the
  documented deep-check.
- **Flake budget:** the live suite was the gate's stability anchor; any
  new lock contention pattern shows up as sparse errors, not clean fails.
  Mitigation: the 5-green shakedown plus the report-history comparison.
- **Slice C footgun:** covered in §7 — default OFF.

## 10. Non-goals

xdiscing `run_perf_benchmarks.py` (wall-clock budgets must stay
single-sample-serial), the frontend/graph-algos/analytics gate steps
(seconds each), embeddings paths (separate arc, [[parallel_cold_embed]],
done), and any change to what the live suite asserts.

## Appendix — raw measurement log (2026-08-31)

| Run | Command | Wall | Notes |
|---|---|---|---|
| baseline | `make qa -j 8` | 114.0s | pytest step 114.0 |
| baseline | `make qa -j 4` | 136.0s | |
| baseline | `make qa -j 1` | 141.1s | fully serial |
| baseline | `make advisory -j 8` | 89.2s | live 89.1 / integration 61.6 |
| shakedown | `pytest -m "not live" -n 4` | 64.9s | 2423 passed |
| shakedown | `pytest -m live -n 4` | 41–46s | 146–151 passed, 67–72 lock ERRORS |
| shakedown | `pytest -m integration -n 4` | 31.2s | 551 passed |
| phase 1 | `make qa -j 8` | 65.2s | 8/8 (first run 71s before guard fix) |
| phase 1 | `make advisory -j 8` | 94.0s | 11/11 (was 9/11 pre-fixture-fix) |
| phase 1 | `pytest -m live --durations` | 72.5s | top-20 ≈ 38s |
| -j check | `make qa -j 4` | 66.5s | post-xdist wash |
| -j check | `make advisory -j 4` | 95.7s | post-xdist wash |

## Appendix 2 — final timings and the advisory-side Slice C resolution (2026-08-31, post-archival)

Slice C was resolved on the ADVISORY side (user directive — the reverse of
§7's qa-side option): advisory dropped its integration step entirely; the
551 integration tests gate via qa's `-m "not live"` only, or standalone
`make integration` (which remains the only writer of
`integration_report.txt`). Advisory is 10 steps. This avoids §7's footgun
(bare `pytest -m "not live"` keeps meaning exactly what it meant) AND
places the integration tests under the GATING gate, and — the measured
dividend — removing integration's 4 workers from the gate un-stretched
live-invariants back to its solo cost, beating both §8 projections.

| metric | baseline (pre-xdist) | phase 1 (xdist wiring) | phase 2 (Slices A+B) | FINAL (advisory-side C) |
|---|---|---|---|---|
| `make qa` wall | 114.0s (serial `-j1` 141.1s) | **65.2s** | 76.3s latest (65–76s noise band) | unchanged (no qa change) |
| `make advisory` wall | 89–94s | 94.0s | 76–87s (median ~80s) | **45.5s** (10/10) |
| live-invariants in-gate | 89–96s serial | serial by design | 76.4–86.6s | **45.4s** |
| live `-n auto` standalone | n/a (67–72 lock errors) | not run in gate | 43.8–51.2s, 218/218 ×5 | same |
| live serial | 72.5s | 72.5s | **57.4s** (Slice B) | 57.4s |
| integration step in advisory | 61.6s | 40.3s | 67.5–72.9s | **removed** |
| integration coverage | qa + advisory (double-run) | qa + advisory | qa + advisory | **qa only (gating) + `make integration`** |
| both gates back-to-back | ~203s | ~159s | ~157s | **~110s** |

Two findings worth keeping, measured not assumed:

1. **Worker indices are NOT unique identifiers across pytest invocations.**
   The advisory gate's live-invariants and integration steps ran as two
   concurrent `pytest -n auto` processes, both naming workers gw0..gw3 —
   worker-index-keyed per-worker caches collided across the invocations
   (4/5 advisory runs FAILed; a single-suite shakedown could never see
   this). Any per-worker artifact keying must include the PID.
2. **Contended overlap can cost more than it hides.** With both suites at
   4 workers on 4 cores, integration's share stretched live-invariants
   ~50s → 76–87s (and itself 30 → 67–73s): removing the duplicate was
   worth ~30s of wall, twice the saving projected for the qa-side Slice C
   variant — because the contention tax disappeared along with the
   duplicate work, not just the duplication.
