---
title: "MST/MSF Backbone Wiring (Lane 1)"
status: executed
filed: "2026-10-30"
executed: "2026-10-30"
completed_md: "458"
area: "helpers/graph/stats.py, Makefile"
---

# MST/MSF Backbone Wiring (Lane 1)

**Date:** 2026-10-31
**Status:** EXECUTED (#458)
**Area:** Graph reporting / consumer wiring

---

## 1. Motivation

The SciPy `minimum_spanning_tree` lane (`helpers/graph/l1_mst.py`) was unlocked in `scipy_minimum_spanning_tree.md` (2.9 ms full-graph MST on live data, supply-backbone use case). It is implemented, tested (4 tests pass), and trial-ready. However, it is currently only accessible via direct CLI invocation:

```bash
python3 helpers/graph/l1_mst.py mst-backbone --format summary
```

To make it a first-class graph capability, it needs to be wired into the existing consumer surfaces:
1. `make graph-stats` (via `helpers/graph/stats.py`) — the canonical whole-graph structural report
2. A dedicated `make graph-mst` target for direct access
3. Gate/report registration so it appears in the evaluation pipeline

This proposal covers the wiring only. No algorithmic changes, no new dependencies, no `--apply` writes.

---

## 2. Evidence (Measured)

| Metric | Value (live DB) | Notes |
|--------|----------------|-------|
| Nodes | 19,053 | companies + sectors |
| Components | 114 | connected components in projection |
| Forest edges | 18,939 | = V − C (spanning forest) |
| Total weight | 21,022.13 | min-collapse per parallel edge pair |
| Elapsed | ~5 ms scipy leg | `minimum_spanning_tree` call ONLY (`mst_forest()`'s `elapsed_s`; timer at l1_mst.py:76-78, key at :101); projection + CSR build are NOT in this number. Whole `mst_forest()` call incl. projection + CSR: ~360-390 ms (measured 2026-10-31, live DB) |
| Test suite | 4/4 pass | triangle oracle, square+diagonal, disconnected, live replay |

Source: `bench_data/mst_matching_trials/` artifacts (`mst_backbone_trial.json`, `mst_backbone_full.json`).

Lane 1 is the S1 SciPy lane from `scipy_csgraph_scale_lanes.md` (executed 2026-10-15; registered T1-unlocked in the SciPy scale register, `doc/local/evaluations/graph_layer.md:901`). 2.9 ms was the register's scipy leg at the 2026-09-24 graph shape; the live scipy leg today is ~5 ms. The whole `mst_forest()` call INCLUDING projection + CSR is ~360-390 ms — a leg the register never timed.

---

## 3. Design

### S1 — stats.py section (consumer wire)

Add `_print_mst_backbone()` to `helpers/graph/stats.py`:

- Called from `print_stats()` after hypergraph structure, before distributions
- Follows existing pattern: lazy import inside try/except, advisory (never fails `graph-stats`)
- Prints: nodes, components, forest_edges (= V − C), total_weight, elapsed_s, top-5 edges by weight
- Uses existing `mst_forest()` from `helpers.graph.l1_mst` (returns dict with all needed fields)
- **Timer:** the section times the whole `mst_forest()` call itself (`t0 = perf_counter()` around it) and prints that as `elapsed_s`. Do NOT reuse the dict's `elapsed_s` field — it covers only the scipy `minimum_spanning_tree` call, not projection + CSR build, and would mislabel the number in the canonical report.

### S2 — Makefile target (direct access)

Add `graph-mst` target to `Makefile` near `graph-stats`:

```make
graph-mst:      ## Print the MST/MSF backbone (S1 SciPy lane) whole-graph spanning forest
> python3 helpers/graph/l1_mst.py mst-backbone --format summary
```

This is a **3-line change, not 2**: besides the target itself, the
`name: ## desc` annotation requires (a) an `@echo "  graph-mst ..."` help
line inserted in ALPHABETICAL position (ASCII sort: between `graph-algos` and
`graph-rebuild` — after `graph-matching` once Lane 2 lands; NOT between
`graph-rebuild` and `graph-rebuild-bench`, which would fail the sort test),
and (b) `graph-mst` added to the `.PHONY` list. Both are
enforced by `TestMakefileHelpCompleteness` (`tests/test_static_checks.py:1261`)
— a bare target goes qa-red on exactly the help-drift that gate exists for.

### S3 — Gate/report registration (future, out of scope for this proposal)

- Register MST metrics in the evaluation gate (`helpers/misc/ontology_eval_gate.py`) if ontology questions cover supply-backbone topology
- Add MST snapshot to `outputs/perf_report.md` timing runs
- This is deferred until consumer demand is proven; S1+S2 are sufficient for immediate use.

---

## 4. Acceptance Criteria & Shakedown

- [ ] `make graph-stats` prints an "MST/MSF backbone (S1 SciPy lane)" section with live numbers
- [ ] `make graph-mst` works and matches direct CLI output
- [ ] Existing tests unaffected: `pytest tests/test_l1_mst.py tests/test_scipy_matching.py -v` → 14 passed
- [ ] Makefile help gate green: `pytest tests/test_static_checks.py -k MakefileHelp` (echo line present + alphabetical, .PHONY updated)
- [ ] Static checks pass: `make static-checks` (ruff, md-lint, types)
- [ ] No new dependencies; SciPy already in lockfile for this lane
- [ ] Advisory: the section's whole-call `elapsed_s` < 500 ms on live DB (measured ~360-390 ms — projection + CSR dominate; the scipy `minimum_spanning_tree` leg alone is ~5 ms and keeps l1_mst's own `elapsed_s` meaningful)

**Eval gate bullet:** MST backbone section must appear in `make graph-stats` output with non-zero forest edges and finite total weight on live DB.

---

## 5. Risks

| Risk | Mitigation |
|------|------------|
| SciPy import overhead in stats.py | Lazy import inside function; only loads when section runs |
| Projection changes break MST | `l1_mst.mst_forest()` is the single source of truth; stats only consumes its dict |
| Advisory section failure masks real errors | Follows existing stats.py pattern: try/except prints "(unavailable: ...)" and continues |
| Gate registration premature | S3 explicitly deferred; no gate changes in this proposal |

---

## 6. Non-Goals

- No `--apply` / persistence of MST edges (read-only lane)
- No changes to `l1_mst.py` algorithm or CLI
- No new hypergraph / matching integration (Lane 2+)
- No modification to `graph_analytics` tables or DuckDB schema
- No web / API exposure

---

## Execution record (2026-10-30)

S1+S2 executed same-day; S3 (gate registration) deferred per §3.

- `make graph-stats` prints the section with live numbers: nodes 19,051 |
  components 114 | forest_edges 18,937 (= V − C) | total_weight
  20,993.370 | whole-call **417 ms** (incl. projection + CSR; scipy leg
  alone 5.1 ms) — inside the <500 ms advisory budget. (Node count is
  19,051, not the proposal's 19,053: two person stubs and a stray were
  pruned from the store earlier the same day.)
- `make graph-mst` target + alphabetical help echo + `.PHONY` entry;
  `TestMakefileHelpCompleteness` green.
- Lane tests untouched: `tests/test_l1_mst.py` 4/4.

## Appendix A — Raw Measurement Log (from trial run)

```text
$ python3 helpers/graph/l1_mst.py mst-backbone --format summary
nodes 19053 | components 114 | forest_edges 18939 | total_weight 21022.130 | 0.005s

(Note: the CLI's trailing `0.005s` is `elapsed_s` = the scipy MST call only,
same mislabel corrected in §2/S1 above; the stats section will time the full
`mst_forest()` call instead. l1_mst.py itself is untouched per Non-Goals.)

$ python3 -m pytest tests/test_l1_mst.py -v
tests/test_l1_mst.py::test_triangle_oracle PASSED
tests/test_l1_mst.py::test_square_diagonal PASSED
tests/test_l1_mst.py::test_disconnected_components PASSED
tests/test_l1_mst.py::test_live_replay PASSED
```

---

## Appendix B — File Index (this proposal only touches)

| File | Change | Type |
|------|--------|------|
| `helpers/graph/stats.py` | Add `_print_mst_backbone()` + call in `print_stats()` | +35 lines |
| `Makefile` | Add `graph-mst` target + alphabetical help echo line + `.PHONY` entry | +4 lines |

Total diff: ~39 lines added, 0 removed.
