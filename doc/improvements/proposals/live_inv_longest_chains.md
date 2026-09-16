---
title: "live-invariants: longest_chains edge-touched universe — cut the n^2 isolate blowup"
status: proposed
filed: "2026-09-16"
executed: null
completed_md: null
area: "helpers/graph/stats.py (longest_chains), tests/test_graph_stats.py (xdist dedup + docstring), tests/test_hyper_incidence.py (regression pin)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# live-invariants: longest_chains edge-touched universe — cut the n^2 isolate blowup

**Date:** 2026-09-16 · **Status:** PROPOSED ·
**Area:** `helpers/graph/stats.py` (longest_chains),
`tests/test_graph_stats.py`, `tests/test_hyper_incidence.py` — perf
fix, no schema change, output semantics preserved for the connected
universe.

## 1. Motivation — measured

The advisory `live-invariants` leg went 59-97s (09-14 median ~71s) to
234.3s / 267.4s (09-16) = **3.3-3.8x** after the D19 exchange seeding.
Every other leg in `make qa` and `make advisory` is flat. Attribution
chain (all measured 2026-09-16):

| Step | Measurement |
|---|---|
| leg wall (advisory_report.txt) | 234.3s -> 267.4s vs 59-97s pre-D19 |
| per-test durations (`-m live -n auto --durations=15`) | 3x `test_graph_stats.py` tests at **156-169s SETUP each** |
| render profile (section timers) | one `print_stats()` = 139.5s, **`longest_chains` = 136.2s (97%)** |
| store probe | entities 6,748; only **1,722 touched by any graph_edge**; 5,026 isolated (74%) — D19 pathless stubs |
| complexity | `csr_matrix(shape=(n, n))` + `shortest_path` dense n^2 output, run TWICE (ALL + ACTIVITY views): cells 2.84M (n=1,685 pre-D19) -> 45.5M (n=6,748) = **16.0x** |
| duplication | module-scoped `stats_render` fixture rebuilt per xdist worker: 3 parallel renders |

Isolated entities contribute nothing to chains, diameter, distant
pairs, or component structure — they are pure dead weight in the
matrix. The fixture docstring still claims "~1s per render" (stale
long before D19; the leg was 51-122s on 09-11/12).

## 2. Design

- **S1 edge-touched universe**: `longest_chains` builds `names` from
  entities touched by >=1 graph_edge (`WHERE name IN (SELECT source
  UNION SELECT target)`), preserving rowid order. n: 6,748 -> 1,722,
  restoring the pre-D19 universe exactly (pre-D19 all entities were
  mention-born and edge-touched). Empty-universe guard returns the
  existing "no edges" lines.
- **S2 render dedup + doc truth**: module-level
  `pytest.mark.xdist_group("graph_stats_render")` on
  test_graph_stats.py so all its tests share ONE xdist worker -> one
  render per run instead of three; fixture docstring rewritten with
  the honest cost model (O(n^2) in the edge-touched universe).
- **S3 per-component shortest paths** (DEFERRED): replace the single
  n x n matrix with per-connected-component computation. Only worth
  it if the connected universe grows ~10x; revisit trigger recorded.

## 3. Slices

- S1 `stats.py` universe query + empty guard; regression pin in
  test_hyper_incidence.py: identical `longest_chains` output with and
  without isolated entities in `entities`.
- S2 `test_graph_stats.py` xdist_group + docstring.
- Gates: targeted tests (test_graph_stats, test_hyper_incidence),
  ruff; measured render + advisory leg; full `make qa` + `make
  advisory` once at arc end.

## 4. Acceptance

- `longest_chains` render measured single-digit-to-tens of seconds
  (n=1,722 restores the pre-D19 matrix size); advisory live-invariants
  leg back under ~120s.
- Output for the connected universe unchanged (regression pin green;
  existing view/chain/degrade tests green).
- `make qa` 9/9 + `make advisory` green at arc end.

## 5. Deferred

- **S3 per-component paths** — reopen when edge-touched n grows
  ~10x above 1,722 or the render again dominates the leg.
