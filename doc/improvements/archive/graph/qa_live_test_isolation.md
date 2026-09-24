---
title: "Restore the QA critical path — isolate live graph checks from synthetic gate tests"
status: executed
filed: "2026-09-24"
executed: "2026-09-24"
completed_md: "284"
area: "tests"
---

# Restore the QA critical path — isolate live graph checks from synthetic gate tests

**Date:** 2026-09-24 · **Status:** EXECUTED ·
**Area:** QA test classification and synthetic CLI integration coverage

## 1. Motivation

QA pytest time rose from a normal 116.64s at run 34 to 155.62s at run 37
and remained around 153–156s. The transition occurred in the dirty-tree
window later committed as `92cbf6eb` (`[Findata] scipy analytics lanes:
bridge, dispatch, exact solves`).

The slowdown was not a generalized production-algorithm regression. Commit
`92cbf6eb` added high-value live parity coverage while the corporate-intake
refresh simultaneously increased `graph_edges` from 18,291 to 57,581. Three
new tests read the live database but lacked `@pytest.mark.live`, so the
blocking QA selection (`pytest -m "not live"`) still executed them. The same
commit's ROUTING change also redirected an existing synthetic `--all` CLI
test from its mocked Onager connection to the live SciPy and L1B lanes
because the test CLI supplied no routed-lane database path.

The result was a test-topology regression: live-data work entered the
blocking QA critical path and was charged to a synthetic persistence test.

## 2. Evidence

### QA transition from `gate_query`

| Run | Started | Tests | Pytest | QA wall |
|---|---|---:|---:|---:|
| 34 | 2026-09-23 00:48 | 3,402 | 116.64s | 117.0s |
| 35 | 2026-09-23 21:28 | 3,449 | 134.25s | 136.4s |
| 36 | 2026-09-23 21:32 | 3,451 | 140.12s | 141.7s |
| 37 | 2026-09-23 21:36 | 3,451 | 155.62s | 156.6s |
| 39 | 2026-09-24 09:29 | 3,468 | 153.58s | 154.8s |

Runs 36 and 37 had the same test count, proving that the final step was an
execution-path change rather than only test-count growth. Commit `92cbf6eb`
was checked in at 21:55, 19 minutes after run 37, consistent with testing the
dirty patch before refresh.

### Live work accidentally charged to QA

Latest run-39 JUnit durations:

| Test | Duration | Classification defect |
|---|---:|---|
| `test_dispatch_live_closeness_exact_vs_incumbents` | 21.78s | reads live DB, no `live` marker |
| `test_dispatch_live_betweenness_exact_vs_incumbents` | 13.07s | reads live DB, no `live` marker |
| `test_exact_lanes_live_parity_vs_fresh` | 1.99s | reads live DB, no `live` marker |
| `test_cli_all_applies_phase3_metrics` | 39.15s | synthetic test, routed lanes read live DB |

The SciPy pair-reuse follow-up reduced the last test from 39.15s to 16.68s,
confirming that duplicate live dijkstra work was real but was only a
secondary contributor. Correct test isolation remains necessary.

### Corpus expansion

| Snapshot table | Before `e3a23f97` | After | Ratio |
|---|---:|---:|---:|
| `graph_edges` | 18,291 | 57,581 | 3.15x |
| `entities` | 6,811 | 26,149 | 3.84x |
| `graph_analytics` | 24,757 | 65,180 | 2.63x |
| DuckDB doubled edges | 36,582 | 115,162 | 3.15x |

The algorithms remained fast at the larger scale: live SciPy closeness is
about 7s and current L1B betweenness is 3–13s in QA. The regression came
from placing this live work in the wrong gate and leaking it into a synthetic
test, amplified by the larger corpus.

## 3. Design

### S1 — Classify genuine live checks

Mark the three tests that open `memory/research.db` or compare against the
live production graph with `@pytest.mark.live`. They remain collected and
run under `make advisory` / `make live-invariants`; they leave blocking QA's
`-m "not live"` selection.

### S2 — Isolate the synthetic CLI persistence test

Keep `test_cli_all_applies_phase3_metrics` in blocking QA because it verifies
that `--all --apply` persists the expected metric families. Replace only its
routed-lane boundary with deterministic synthetic outputs for SciPy
closeness/harmonic and L1B betweenness. Routing correctness, formulas, live
parity, drift, and performance remain covered by their dedicated tests.

This test-level seam is preferable to changing the production CLI contract
solely for a test. The SciPy bridge has a dedicated persistence test and the
L1B lane has dedicated dispatch tests; the integration test's responsibility
is the aggregate persistence flow.

### S3 — Pin classification and isolation

Acceptance checks must prove:

1. `pytest -m "not live" --collect-only` excludes all three genuine live
   parity tests.
2. `pytest -m live --collect-only` includes them.
3. The synthetic `--all` test calls each stubbed routed boundary once and
   completes without opening the live routed database.
4. Targeted routing, persistence, and integration tests remain green.

## 4. Acceptance criteria and shakedown

1. Collection audit: the three live checks are absent from not-live QA and
   present in the live selection.
2. `test_cli_all_applies_phase3_metrics` remains in not-live QA, uses only
   synthetic route outputs, and persists all expected metrics.
3. Dedicated SciPy closeness, harmonic, L1B betweenness, exact-lane, drift,
   and CLI persistence tests remain green.
4. Targeted test wall improves materially from the pre-fix 39.15s synthetic
   CLI test; exact full-suite recovery is measured by the next QA run because
   xdist schedules the remaining critical path.
5. Ruff, format, `ty`, proposal lifecycle, Markdown lint, and search freshness
   pass. Full QA remains operator-owned before check-in.

## 5. Risks

- **Coverage accidentally removed from blocking QA** — the tests move to the
  existing advisory live lane rather than being deleted; collection checks pin
  both selections.
- **Synthetic route stubs diverge from real lane shapes** — return the exact
  `{metric: {entity: float}}` structures consumed by the CLI and keep route
  correctness covered by dedicated non-live unit tests.
- **Advisory live contention** — the moved tests retain their current read-only
  DB access and run under the existing xdist live-lane isolation.
- **Future recurrence through a new routed metric** — the synthetic CLI test
  pins every currently routed lane explicitly; new routed metrics require a
  matching test boundary.

## 6. Non-goals

- No production routing or algorithm change.
- No weakening of live parity tolerances.
- No deletion of slow correctness tests.
- No attempt to make the full pytest suite serial or redesign xdist scheduling.
- No claim that every QA timing increase is algorithmic; this arc fixes the
  specific live-test classification and synthetic-path leak found in run 37.

## 7. Execution result

- Marked the three live parity checks with `@pytest.mark.live`; the not-live
  QA selection now deselects them and the live selection collects them.
- Replaced the synthetic `--all` test's routed-lane calls with deterministic
  synthetic SciPy/L1B outputs. The test dropped from 16.68s after pair reuse
  (39.15s before pair reuse) to 0.46s.
- Added a regression assertion that all three live checks carry the `live`
  marker.
- Targeted not-live coverage: 100 passed, 3 deselected in 9.69s; Ruff and
  format checks passed. Full QA remains operator-owned after patch refresh.

## Appendix — investigation commands

| Command | Result |
|---|---|
| `gate_query recent --gate qa --last 10 --tests` | located runs 34–39 and intermediate failures |
| `gate_query timing --leg pytest --last 50` | 116.64s at run 34, 155.62s at run 37 |
| `git log` for the run-34→37 window | identified `92cbf6eb` and corporate-intake commits |
| historical Parquet row counts | quantified the 3.15x graph expansion |
| run-39 JUnit duration grouping | identified the three live tests and leaked synthetic CLI test |
| `pytest -m "not live" --collect-only` at `92c^` / `92c` | 3,415 → 3,454 tests |
