---
title: "Maximum Bipartite Matching Wiring (Lane 2)"
status: executed
filed: "2026-10-30"
executed: "2026-10-30"
completed_md: "459"
area: "helpers/graph/stats.py, Makefile"
---

# Maximum Bipartite Matching Wiring (Lane 2)

**Date:** 2026-10-31
**Status:** EXECUTED (#459)
**Area:** Graph reporting / consumer wiring

---

## 1. Motivation

The SciPy `maximum_bipartite_matching` lane (`helpers/graph/scipy_matching.py`) was unlocked in `scipy_csgraph_scale_lanes.md` (executed 2026-10-15; registered T2-unlocked in the SciPy scale register, `doc/local/evaluations/graph_layer.md:902` — 0.8 ms, 958 matches = 10.9% coverage at the 2026-09-24 graph shape). There is no `maximum_bipartite_matching.md`. It is implemented, tested (10 test functions pass), and trial-ready with four subcommands:

- `match-suppliers` — max bipartite matching over supplier_to (suppliers × consumers)
- `assign-feasibility` — min-weight full bipartite matching feasibility (Hall condition)
- `assign-primary` — Hall-violator exclusion loop + min-stake assignment
- `assign-primary-sector` — per-sector cuts (live trial: 139/139 assigned, 0 exclusions across 28 sectors)

Currently only accessible via direct CLI. To make it a first-class capability, it needs wiring into consumer surfaces:
1. `make graph-stats` (via `helpers/graph/stats.py`) — canonical whole-graph report
2. Dedicated `make graph-matching` target for direct access
3. Gate/report registration (deferred)

This proposal covers wiring only. No algorithmic changes, no new deps, no `--apply`.

---

## 2. Evidence (Measured)

| Metric | Value (live DB) | Notes |
|--------|----------------|-------|
| Suppliers (supplier_to source) | 6,083 | companies with outgoing supplier_to edges |
| Consumers (supplier_to target) | 8,733 | companies with incoming supplier_to edges |
| Matching size | 958 | max bipartite matching (`maximum_bipartite_matching`, NOT max_flow) |
| Coverage | 10.97% | matching_size / **consumers** (s2_matching divides by m = 8,733, scipy_matching.py:86) |
| Unmatched consumers | 7,775 | consumers left unmatched (8,733 − 958); NOT suppliers |
| Zero-degree consumers | 0 | separate census; `zero_degree_consumers` |
| Elapsed | not recorded | trial artifacts carry NO timing key — measure at execution |
| Sector assignment (assign-primary-sector) | 139/139 assigned | 28 sectors, 0 exclusions |
| Test suite | 10/10 pass | 10 test functions in tests/test_scipy_matching.py |

Source: `bench_data/mst_matching_trials/` artifacts (`match_suppliers_trial.json`, `assign_feasibility_trial.json`, `assign_primary_sector_trial.json`).

Lane 2 is the S2 SciPy lane from `scipy_csgraph_scale_lanes.md` (executed 2026-10-15; register row `doc/local/evaluations/graph_layer.md:902`). The 0.8 ms figure is the register's 2026-09-24 run — no timing exists in this proposal's trial artifacts; measure at execution.

**Known quirk (out of scope, flagged):** `s3_feasibility`'s `max_matching_size` is `len()` of the permutation array = column count (8,733), NOT the matched count (958 on the same graph) — scipy_matching.py:112; in scipy 1.18.1 `maximum_bipartite_matching(mat, perm_type="row")` returns an array of length #columns (verified empirically: a 2x3 matrix yields a len-3 array), so `len()` can never be the matching size. The printed "Hall collisions: max matching 8733/6083" is meaningless as a matching size. Non-Goals forbid touching scipy_matching.py in this wiring proposal; fix belongs to a lane-2 algorithmic pass.

---

## 3. Design

### S1 — stats.py section (consumer wire)

Add `_print_supplier_matching()` to `helpers/graph/stats.py`:

- Called from `print_stats()` after MST backbone (S1 of Lane 1 proposal), before distributions
- Follows existing pattern: lazy import inside try/except, advisory (never fails `graph-stats`)
- Runs `s2_matching(load_supplier_incidence(db))` from `helpers.graph.scipy_matching` — note there is NO `match_suppliers()` function; that is the CLI subcommand name. Returns dict with keys: `matching_size`, `coverage_pct`, `unmatched_consumers` (list), `zero_degree_consumers` (list), `n_suppliers`, `n_consumers`
- **Timer:** there is NO `elapsed_s` in the dict — the section times the whole `s2_matching(load_supplier_incidence(db))` path and prints that as its own `elapsed_s` (same rule as Lane 1's S1). Measured split (2026-10-31, live DB): `load_supplier_incidence` ~880 ms + `s2_matching` ~3 ms — label the printed number "incl. load" so it is not misread as the scipy leg
- Prints: suppliers, consumers, matching_size, coverage %, unmatched consumers, zero-degree consumers, elapsed_s
- Optionally: if coverage < 15%, print "(sparse — typical for supplier_to topology)"

### S2 — Makefile target (direct access)

Add `graph-matching` target to `Makefile` near `graph-stats`/`graph-mst`:

```make
graph-matching: ## Print supplier→consumer max bipartite matching (S2 SciPy lane)
> python3 helpers/graph/scipy_matching.py match-suppliers
```

Same **3-line change** rule as Lane 1's `graph-mst`: the `##` annotation
requires an `@echo "  graph-matching ..."` help line in ALPHABETICAL position
(`graph-matching` sorts BEFORE `graph-mst`) plus a `.PHONY` entry — enforced
by `TestMakefileHelpCompleteness` (`tests/test_static_checks.py:1261`).

### S3 — Extended matching subcommands (future, out of scope)

- `make graph-matching-feasibility` → `assign-feasibility`
- `make graph-matching-sector` → `assign-primary-sector`
- Gate registration for matching metrics in evaluation gate
- Deferred until consumer demand proven; S1+S2 sufficient for immediate use.

---

## 4. Acceptance Criteria & Shakedown

- [ ] `make graph-stats` prints a "Supplier→Consumer max bipartite matching (S2 SciPy lane)" section with live numbers
- [ ] `make graph-matching` works and matches direct CLI output
- [ ] Existing tests unaffected: `pytest tests/test_scipy_matching.py -v` → 10 passed
- [ ] Makefile help gate green: `pytest tests/test_static_checks.py -k MakefileHelp` (echo line present + alphabetical, .PHONY updated)
- [ ] Static checks pass: `make static-checks` (ruff, md-lint, types)
- [ ] No new dependencies; SciPy already in lockfile for this lane
- [ ] Advisory: scipy matching leg < 5 ms (measured ~3 ms); the timed whole path incl. `load_supplier_incidence` budgets < 1.2 s (measured ~881 ms — load dominates: the incidence is rebuilt per call, with a `suppliers.index(s)` O(n) lookup in the edge loop at scipy_matching.py:59; a dict there is a lane-2 algorithmic-pass candidate)

**Eval gate bullet:** Matching section must appear in `make graph-stats` output with matching_size > 0 and coverage % printed on live DB.

---

## 5. Risks

| Risk | Mitigation |
|------|------------|
| SciPy import overhead in stats.py | Lazy import inside function; only loads when section runs |
| Projection changes break matching | `scipy_matching.s2_matching(load_supplier_incidence(db))` is the single source of truth (`match_suppliers` is only the CLI subcommand name); stats only consumes its dict |
| Advisory section failure masks real errors | Follows existing stats.py pattern: try/except prints "(unavailable: ...)" and continues |
| Matching coverage naturally low | Documented as expected; supplier_to is inherently sparse (10–15% coverage) |
| Gate registration premature | S3 explicitly deferred; no gate changes in this proposal |

---

## 6. Non-Goals

- No `--apply` / persistence of matching results (read-only lane)
- No changes to `scipy_matching.py` algorithm or CLI subcommands
- No new hypergraph integration
- No modification to `graph_analytics` tables or DuckDB schema
- No web / API exposure
- No feasibility/sector subcommands in stats.py (only `match-suppliers` summary)

---

## Execution record (2026-10-30)

S1+S2 executed same-day; S3 (feasibility/sector targets, gate
registration) deferred per §3; the flagged `max_matching_size` quirk
stays out of scope per Non-Goals.

- `make graph-stats` prints the section with live numbers: suppliers
  6,083 | consumers 8,733 | matching_size 958 | coverage 10.97% |
  unmatched 7,775 | zero-degree 0 | elapsed **920 ms (incl. load)** —
  inside the <1.2 s advisory budget; the sparse note prints (coverage
  < 15%).
- `make graph-matching` target + alphabetical help echo (sorts before
  `graph-mst`) + `.PHONY` entry; `TestMakefileHelpCompleteness` green.
- Lane tests untouched: `tests/test_scipy_matching.py` 10/10 (14 across
  both lanes with test_l1_mst).

## Appendix A — Raw Measurement Log (from trial run)

```text
$ python3 helpers/graph/scipy_matching.py match-suppliers
suppliers=6083 consumers=8733
matching_size=958 coverage=10.97%
unmatched=7775 zero_degree=0
Done.

$ python3 helpers/graph/scipy_matching.py assign-feasibility
investors=6083 targets=8733
max_matching_size=8733 full_matching=False
admissible=False
reason: Hall collisions: max matching 8733/6083
  deficient (zero_degree_consumers): 0
Done.

(Note: `max_matching_size=8733` is the permutation-array length = column
count, NOT the matched count — see the flagged quirk in §2.)

$ python3 helpers/graph/scipy_matching.py assign-primary-sector
targets=161 classified=139 sectors=28
assigned=139 excluded=0 unclassified=22
Done.

$ python3 -m pytest tests/test_scipy_matching.py -v
tests/test_scipy_matching.py::test_s2_simple PASSED
tests/test_scipy_matching.py::test_s2_no_overlap PASSED
tests/test_scipy_matching.py::test_s3_n_greater_than_m PASSED
tests/test_scipy_matching.py::test_s3_possible PASSED
tests/test_scipy_matching.py::test_s3_hall_collision PASSED
tests/test_scipy_matching.py::test_s3b_covering_assignment PASSED
tests/test_scipy_matching.py::test_s3b_already_admissible_passthrough PASSED
tests/test_scipy_matching.py::test_live PASSED
tests/test_scipy_matching.py::test_s3_sector_toy PASSED
tests/test_scipy_matching.py::test_s3_sector_live PASSED
```

---

## Appendix B — File Index (this proposal only touches)

| File | Change | Type |
|------|--------|------|
| `helpers/graph/stats.py` | Add `_print_supplier_matching()` + call in `print_stats()` | +30 lines |
| `Makefile` | Add `graph-matching` target + alphabetical help echo line + `.PHONY` entry | +4 lines |

Total diff: ~34 lines added, 0 removed.
