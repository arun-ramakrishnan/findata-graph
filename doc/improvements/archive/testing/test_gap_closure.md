---
title: "Test-gap closure — date-rot class, availability route budgets, perf HTTP legs, worker fallback"
status: executed
filed: "2026-09-20"
executed: "2026-09-20"
completed_md: "256"
area: "tests/ (test_analytics, test_snapshot, test_graph_stats, test_verify_notes, run_perf_benchmarks, test_fuzz_regex, new test_extract_relations fallback), helpers/graph/analytics.py (optional clock seam)"
---

# Test-gap closure — date rot, availability budgets, perf HTTP legs, worker fallback

**Date:** 2026-09-20 · **Status:** EXECUTED (completed.md #256) ·
**Basis:** full-suite branch-coverage run (3,522 passed, 74% over
`helpers/`) cross-checked against the 38-route table, the 24 fuzz
modules, the 16 perf legs, and the 3 advisory tests. Every gap below is
measured, not guessed. Follows the security-arc close
(completed.md #247b, #249–#251) and Addendum 6 §F, which named the
availability sweep as the arc's real inheritance.

## 1. Motivation — four gaps, one live failure

The suite is large (184 modules, ~3.5k tests) and the gates are green,
but the coverage measurement surfaced five gaps. Ranked by live damage:

### G1 — date rot (LIVE FAILURE, a whole class)

`tests/test_analytics.py::TestTemporal::test_staleness_percentiles_and_buckets`
**fails on `make qa` today.** The fixture pins `last_updated` to
`2026-08-20`; `helpers/graph/analytics.py:376` computes
`DATE_DIFF('day', TRY_CAST(last_updated AS TIMESTAMP), now())` and the
assertion expects "S1 fresh, stale>30d == 0". The fixture turned 31 days
old on **2026-09-19** — one day after its own date — so the sector now
reports stale. Reproduced exactly: `2026-09-20 - 2026-08-20 = 31 > 30`.

This is a **class**, not one test. Four files pin late-2026 dates and
assert freshness against a source that reads the wall clock:

| File | Pinned dates | Risk |
|---|---|---|
| `test_analytics.py` | 8 | **broken now** |
| `test_verify_notes.py` | 4 | rots next |
| `test_snapshot.py` | 2 | rots later |
| `test_graph_stats.py` | 2 | rots later |

None of these tests call `now()` themselves — the rot is structural:
fixture absolute, source relative. Every one carries a self-destruct
timer set at authoring.

### G2 — availability has no systematic route coverage

The security arc established availability as its most consequential
class (2 of 3 confirmed findings: AVAIL-1 53s GET, AVAIL-2 cubic regex).
Today the only availability tripwires are the three single-finding
regression tests — `503` appears in exactly 3 test files, all
near-duplicates. Across all API tests: `perf_counter` **0**, `timeout`
**0**, `max_rows` **0**. So one route has an availability guard and 37
have none. The 38 routes *are* functionally covered (verified:
`/api/graph/positions` is tested in `test_graph_layout.py`, not the
`test_api_*` files) — but none are **budget**-covered. This is the exact
class that produced the arc's most expensive finding, and it has no
systematic regression net.

### G3 — perf suite is CLI-only, zero HTTP legs

All 16 `run_perf_benchmarks.py` legs invoke helper scripts directly.
`client.get`, `test_client`, `flask`, and `/api/` each appear **0
times**. The gate times algorithm CLIs but never a Flask request path —
so it structurally cannot catch the AVAIL-1 family (an unauthenticated
GET doing 53s of compute). `test_integration_perf.py` is the same shape:
23 tests, all centrality-write validity, zero route timing.

### G4 — worker coverage artifact + the untested serial fallback

Coverage reports `_extract_worker.py` and `_insights_worker.py` at **0%**.
That is a *measurement artifact*: both run in `ProcessPoolExecutor`
children (spawned at `extract_relations.py:2684-2697`), invisible to the
parent process's coverage. Two real gaps sit behind it:

1. The `BrokenProcessPool` **serial fallback** (`extract_relations.py:2699`)
   is never exercised. It is the OOM-recovery path — the one that runs
   when a child dies under memory pressure — and it has no test.
2. The workers have no direct unit test, so a pickle-path or return-shape
   regression only surfaces at full-corpus scale.

Also genuinely untested (not an artifact): `backfill_quotes_attribution.py`
(0%, 167 lines, a graph-writing path), `embed_eval.py` (8%),
`mca_cin_sync.py` (16%), `fts_duckdb_parity.py` (20%).

### G5 — one mild quadratic regex outside the fuzz guard

Scanning all 67 `re.compile` literals for the AVAIL-2 shape flagged two.
`parse_newsletter.py:89` is safe (flat at all sizes).
`pdf_local.py:70` `^(?:\*\*.+?\*\*\s*)+$` is **quadratic**: 0.02 ms
→ 71 ms across a 5-doubling sweep (repeated bold-line input). Bounded and
low-severity — a 6.5 MB single line is unlikely from a PDF — but it sits
in the exact class `test_fuzz_regex.py` was built to guard and is not
covered by it.

## 2. Design — five slices, each independently shippable

### S1. Fix the date rot (kills the live failure + the class)

> **RESOLVED before implementation (2026-09-20):** the operator had
> already repaired `test_analytics.py` in commit `abeda1d7` — the
> fixture now derives `fresh`/`stale` from `date.today()`, with an
> inline comment naming the day-bomb that bit it. Verified: the
> previously-failing test passes, and the other three files
> (`test_verify_notes.py`, `test_snapshot.py`, `test_graph_stats.py`)
> turned out to be **date-labels, not date-rot** — their hardcoded
> 2026 strings are historical comments and fixture labels, none
> asserted against `now()`. So the class was smaller than the scan
> suggested: one real instance, already fixed. No work needed here.

Make every freshness fixture **relative**. In the four files above,
replace absolute timestamps with a helper that derives from
`datetime.now()`:

```python
# tests/conftest.py or a small tests/_clock.py
def fresh(days: int = 5) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
```

Then `("Co1", "company", "2026-01-01 09:00:00", fresh(5), "Co1", "S1")`
etc. The assertion "stale>30d == 0" now holds on any run date, and the
30-day boundary test straddles the line deliberately
(`fresh(5)` vs `fresh(200)`).

Deliberately **not** done: freezing the clock in `analytics.py`. A clock
seam in production code to serve a test is the wrong trade here — the
fixtures are the thing that moved, so fix the fixtures. If a future test
needs determinism across a DST or timezone edge, revisit; the source
already stores UTC-ish strings and the class is date-only.

**Side benefit:** this also fixes the same latent rot in the three files
that have not broken yet, before each fails on its own schedule.

### S2. Availability route budgets (closes G2 for the hot routes)

> **LANDED (2026-09-20):** `tests/test_api_availability_budgets.py` — 10
> tests, all green. Budgets are the measured **cold first call** per
> route (the parametrized cases get a fresh `unit_client`, so the
> one-shot cost is what matters — for `/api/graph/stats` that is ~766 ms
> while the graph layer builds, vs ~190 ms warm) with a **x5 ceiling
> floored at 1 s**. Tightened from an initial x50 after measurement
> showed that would let a route slow 48x and still pass. Three
> malicious-shape tests (huge `limit`, deep `<path:>` converter, unknown
> analytics name) assert fast failure rather than hang.

A new `tests/test_api_availability_budgets.py` in the house
route-integration style, asserting a **wall-clock budget per route** with
a generous-but-finite ceiling:

- `/api/graph/near-duplicates` — already 503-guarded; assert the cap fires
  under a synthetic-large corpus (the AVAIL-1 regression already does
  this; keep it, add the budget twin).
- `/api/graph/similar/<path>` , `/api/graph/neighbors`, `/api/analytics/<name>`,
  `/api/graph/positions` — assert each returns 200 within e.g. 3s on the
  test fixture, and that a *malicious* parameter shape (deep path,
  huge `limit`, many `tickers`) stays bounded rather than hanging.

The budgets are chosen per route from measured baseline + margin, not
invented — same discipline as the perf legs. A budget test that fails
intermittently on CI load is worse than no test, so the ceiling is set
at ~3× the measured p50 and the test uses a hard `pytest` timeout marker
rather than a flaky tight bound.

**Scope is deliberately the hot routes, not all 38.** The point is to
stand up the *pattern* and cover the class that produced AVAIL-1; a
full 38-route sweep is the Addendum 6 §F follow-up, not this proposal.

### S3. Perf HTTP legs (closes G3)

> **LANDED (2026-09-20):** `tests/bench_routes.py` (a new perf-gate script
> in the `bench_shortest_path.py` idiom: best-of-3 per route, unmeasured
> warm-up call, exit 1 on violation) wired into `run_perf_benchmarks.py`
> as the `route_graph_stats` leg. Four hot routes through the Flask test
> client, each against a 0.5 s bar; the outer process budget is 5.0 s
> (measured end-to-end 3.3 s stable; the outer budget only guards the
> harness, the route-level bars do the work). The script builds its
> fixture DB in a tempdir, so it needs nothing stashed in `bench_data/`.
>
> Two calibration notes. First, the route budgets had to be re-measured
> once running: the documented steady-state estimates (~3-4 ms) were
> wrong — the real numbers are 137-180 ms, because the warm-up call does
> not fully amortise the graph layer on this fixture. Second, the first
> outer budget (2.0 s) missed that the leg is a subprocess, so its cost
> is interpreter + Flask app + graph build + routes, not just the routes.
> **Mutation-verified:** a 2 s stall on the sector route fails the
> route-level bar (2,143 ms > 500 ms) and propagates through the runner
> (21/23).

Add 2–3 legs to `PERF_LEGS` in `tests/run_perf_benchmarks.py` that drive
the Flask test client instead of a CLI:

```python
("route_near_duplicates", [...client path...], 2.0),
("route_graph_similar",   [...], 2.0),
("route_analytics",       [...], 2.0),
```

This needs a small harness shim (the runner execs a script; a route leg
needs an app + fixture DB). Cheapest honest wiring: a `tests/bench_routes.py`
that builds the test client and times the routes, invoked the same way
as the other legs so the runner's budget/timing machinery is reused
unchanged. Budgets from measured baselines.

### S4. Worker + fallback tests (closes G4)

> **LANDED (2026-09-20):** `tests/test_workers_and_fallback.py` — 6
> tests, all green. Direct unit tests for both worker shims (importable
> with no side effects, delegation to the real `_extract_batch` is
> verified by monkeypatch, the insights worker emits plain dicts not
> dataclasses). The serial fallback is exercised by a `_BoomPool` that
> raises `BrokenProcessPool` on construction, with `_PARALLEL_THRESHOLD`
> patched to 0 so the parallel branch runs over one newsletter file.
> **Mutation-verified:** removing the `except BrokenProcessPool` fallback
> from `extract_relations.py` makes the test fail.

- A direct unit test for `_extract_batch_arg` / `_scan_chunk_arg` that
  imports the worker module and calls it in-process — this also makes
  the 0% number honest by giving coverage a path to the code.
- A test for the `BrokenProcessPool` serial fallback by monkeypatching
  `ProcessPoolExecutor` to raise it, then asserting the serial path
  produces identical results.
- A smoke test for `backfill_quotes_attribution.py`'s non-writing paths
  (`_norm`, `EntityIndex`) — the 167-line 0% module that writes edges.

### S5. Fuzz guard for the bold-line regex (closes G5)

> **LANDED (2026-09-20):** extended `tests/test_fuzz_regex.py` with a
> Hypothesis no-catastrophic-backtracking case (matching the existing
> AVAIL-2 guards) plus a deterministic subquadratic-scaling assertion.
> The threshold took three iterations to calibrate: an initial x50 was
> meaninglessly loose, x5 admitted the regression at small sizes because
> a 0.05 ms first sample is noise-dominated, and the settled form uses
> **min-of-3 samples at sizes 160-1280 with a 6.0 growth ceiling** —
> the good pattern measures 4.26 max, a nested-quantifier regression
> `(?:.+?\s*)+` measures 12.2. **Mutation-verified:** the nested form
> fails the test; the correct pattern passes 3/3.

Extend `tests/test_fuzz_regex.py` with a generator for repeated
`**bold**` segments, asserting subquadratic scaling on
`pdf_local.py:70`. Same structure as the existing AVAIL-2 guard: a
scaling assertion, not a fixed timeout.

## 3. What this does NOT do

- **No production code change except the optional test helper.** G1 is
  fixture-only; G2–G5 are test-only. `analytics.py` is untouched.
- **No coverage-target gate.** 74% branch is a measurement, not a
  threshold; chasing a number would produce low-value tests. The gaps
  this targets are *behavioural* (a live failure, an untested recovery
  path, an unbudgeted class), which is why they were chosen over the
  long tail of missing lines.
- **Not the full 38-route availability sweep.** S2 covers the hot routes
  and establishes the pattern; the sweep is its own follow-up.
- **No flaky budget choices.** Every ceiling is measured-baseline × margin.

## 4. Success criteria

1. `make qa` is 10/10 with **zero deselections** — the date-rot test
   passes on any run date, verified by running it with a fake `now()`
   (or by reasoning: the fixture is `now()-5d`, so it cannot rot).
2. A new route-budget test exists for ≥4 hot routes and passes; each
   budget is documented as `measured p50 X ms, ceiling 3×`.
3. `run_perf_benchmarks.py` runs ≥2 HTTP legs within budget.
4. The serial fallback has a test that fails if the fallback is removed;
   the worker modules report >0% coverage.
5. The bold-line regex scaling assertion fails if the pattern reverts to
   an ambiguous form.

## 5. Order and risk

**Status after the first pass: S1 resolved by the operator before
implementation; S2, S4, S5 landed and mutation-verified. S3 remains
deferred to the end** (the runner assumes a CLI, so route legs need a
`tests/bench_routes.py` shim; it blocks nothing).

Risk: low. All slices are additive tests. The judgement calls that
actually mattered during implementation were the two thresholds — the
S2 route ceilings and the S5 growth bound — and both were calibrated by
measuring the good pattern against a deliberate regression rather than
by intuition. Both tests have teeth: removing the guarded code makes
them fail.
