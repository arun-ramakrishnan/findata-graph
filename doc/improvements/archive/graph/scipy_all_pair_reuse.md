---
title: "Reuse one SciPy dijkstra pass across routed centrality metrics"
status: executed
filed: "2026-09-24"
executed: "2026-09-24"
completed_md: "283"
area: "helpers/graph"
---

# Reuse one SciPy dijkstra pass across routed centrality metrics

**Date:** 2026-09-24 · **Status:** EXECUTED ·
**Area:** `helpers/graph/algorithms.py`, SciPy routing tests

**Follows:** `doc/improvements/archive/graph/scipy_routing_dispatch.md`

## 1. Motivation

`ROUTING` assigns both closeness and harmonic centrality to the SciPy
bridge because it is substantially faster than fresh Onager at the current
graph scale. The bridge already computes both metrics from one dijkstra
distance matrix, but the original dispatch called that bridge once per
metric. Consequently, `--all` repeated the same projection load and dijkstra
pass even though the second result was already available.

The optimization had to preserve the routing switch, the persisted-contract
population, fail-loud pre-flight checks, and standalone `compute()` behavior.
A persistent process-global cache was unnecessary because the duplicate work
is confined to one `--all` invocation.

## 2. Evidence (measured 2026-09-24, this box)

The live graph had 22,046 projection nodes and a 1,734-company contract.

| Configuration | Measurements | Median | Verdict |
|---|---|---:|---|
| Full routed closeness call | 7.241 s, 7.250 s, 6.951 s | 7.082 s | baseline half |
| Full routed harmonic call | 6.793 s, 6.922 s, 7.213 s | 7.082 s | duplicate half |
| Baseline `--all` routed total | two full calls | 14.164 s | redundant dijkstra |
| Shared pair after implementation | 7.275 s, 7.261 s, 6.940 s | 7.261 s | adopt |

The shared pair returned 1,734 closeness rows and 1,734 harmonic rows. The
median saving was 6.903 s, or 48.7% of the routed pair's former wall time.
The isolated dijkstra kernel itself measured a 7.052 s median, confirming
that projection loading was not the source of the removable work.

## 3. Design

`_run_scipy_lane_pair()` now owns the existing pre-flight checks, projection
load, contract resolution, source-position resolution, and one SciPy compute.
It returns both named result dictionaries. `_run_scipy_lane()` selects one
result for standalone metric calls, preserving that API.

The `--all` loop owns an invocation-scoped `scipy_results` dictionary. On the
first metric whose `ROUTING` value is `SCIPY`, it fills the dictionary from
`_run_scipy_lane_pair()`; the second routed metric reuses its entry. The cache
is local to one CLI run and disappears on return. No module-level memo,
generation key, or persistent cache was introduced.

The optimization remains wired through the routing switch. Changing either
closeness or harmonic to `ONAGER_DEFAULT` bypasses pair reuse automatically.

## 4. Acceptance criteria and shakedown

1. Both routing values remain `SCIPY`; no engine or formula changes.
2. One live SciPy kernel call produces both 1,734-row outputs.
3. The `--all` CLI invokes `_run_scipy_lane_pair()` exactly once.
4. Empty projections, empty contracts, import failures, and contract drift
   retain their existing fail-loud behavior before compute.
5. Standalone closeness, harmonic, and `compute(..., db_path=...)` behavior
   remains covered.
6. Targeted Ruff, format, `ty`, and test checks pass: 93 tests passed.

| Outcome | Before | After |
|---|---:|---:|
| SciPy dijkstra passes in `--all` | 2 | 1 |
| Routed pair wall time | 14.164 s | 7.261 s |
| Time saved | — | 6.903 s |
| Persistent cache surfaces | 0 | 0 |

## 5. Risks

- **Mid-run graph replacement** — invocation-scoped reuse gives both metrics
  one coherent snapshot rather than allowing the second metric to observe a
  different generation.
- **Routing drift** — the cache is populated only for metrics whose current
  `ROUTING` value is `SCIPY`; flipping the switch bypasses reuse.
- **Result aliasing** — callers receive ordinary dictionaries and the CLI
  treats them as read-only; persistence and display paths do not mutate them.
- **Failure duplication** — if pair computation fails before cache population,
  the later routed metric may retry and fail independently, matching the CLI's
  existing continue-after-leg-failure behavior.

## 6. Non-goals

- No persistent or cross-invocation result cache.
- No routing-policy change; SciPy remains selected for performance.
- No formula, projection, contract, persistence, or centrality-universe change.
- No work on the served-versus-stamp convention fork.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-24 | three timed `_run_scipy_lane(closeness)` calls | 7.241 / 7.250 / 6.951 s | live contract |
| 2026-09-24 | three timed `_run_scipy_lane(harmonic)` calls | 6.793 / 6.922 / 7.213 s | live contract |
| 2026-09-24 | three timed `scipy_bridge.compute()` calls | 7.052 / 7.079 / 7.049 s | isolated kernel |
| 2026-09-24 | three timed `_run_scipy_lane_pair()` calls | 7.275 / 7.261 / 6.940 s | 1,734 rows each |
| 2026-09-24 | targeted pytest over SciPy and graph-algorithm integration suites | 93 passed | two fork deprecation warnings |
