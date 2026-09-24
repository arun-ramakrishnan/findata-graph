---
title: "SciPy s-t lanes (Yen / max-flow) — ROUTING switch + wall-clock budget guard"
status: executed
filed: "2026-09-24"
area: "helpers/graph"
executed: "2026-09-24"
completed_md: "287"
---

# SciPy s-t lanes (Yen / max-flow) — ROUTING switch + wall-clock budget guard

**Date:** 2026-09-24 · **Status:** EXECUTED

Follows `scipy_graph_bridge.md` (S3 children), `scipy_routing_dispatch.md`
(the ROUTING switch), and `scipy_all_pair_reuse.md`. Un-defers exactly two
of the five `scipy_algos` children:
`doc/improvements/archive/graph/scipy_yen_k_shortest.md` (#275) and
`doc/improvements/archive/graph/scipy_maximum_flow.md` (#273). The other three stay
deferred (see §7).

## 1. TL;DR

Two s-t lanes that neither SQL nor Onager can express, landed in the
existing scipy bridge (`helpers/graph/scipy_bridge.py`) and registered in
the **ROUTING switch** as SCIPY-owned capabilities with a **wall-clock
budget guard**. They are direct opt-in CLI lanes, not node-keyed metric
dispatches. The switch already owns *which engine* runs a metric lane
(routing closeness/harmonic to SCIPY is what stops the 156 s Onager leg
from running); this proposal makes it own lane *TIME* too: a lane that
overruns its budget raises instead of silently returning a slow result.
Measured live (22,046 nodes / 57,581 edges, scipy 1.18.1): Yen K=5
**0.03 s**, max-flow **0.016 s** unit /
**0.010 s** weighted — the guard is not shaving milliseconds, it catches
super-linear decay at future scale.

The engine is NOT the change: scipy is already the second lane
(`load_projection` / `load_centrality_projection` / `spsolve` / `eigsh` /
`write_analytics` seam all exist). The delta is **capability + lane
registration**: two functions, two CLI commands, two ROUTING entries, one
budget table, tests. No new dependency, no venv split.

## 2. Why these two (and not the others)

The five `scipy_algos` children were deferred 2026-09-23 with triggers. On
re-evaluation (benchmarked 2026-09-24):

| Child | Verdict | Reason |
|---|---|---|
| `scipy_yen_k_shortest` | **un-defer** | cheap (0.03 s), new capability, opt-in |
| `scipy_maximum_flow` | **un-defer** | cheap (0.016 s), replaces the retired igraph seat |
| `scipy_personalized_pagerank` | stay deferred | **structural**: leaf-heavy graph makes PPR a 1-hop ego rank (archive §7, now recorded) |
| `scipy_minimum_spanning_tree` | stay deferred | no named consumer (viz-only use not yet ordered) |
| `scipy_link_prediction_kernel` | stay deferred | trigger not met (SQL L1c exact at 0.65 s) |

## 3. The switch + budget principle

`ROUTING` (`scipy_bridge.py`) is the single source of truth for lane
ownership. The dispatch (`algorithms._run_*`) consults it for node-keyed
metrics and refuses any silent engine fallback (a failed fast leg must
fail, never
degrade into a silent 156 s Onager run). `ROUTING` is an ownership and
dispatch table, not a complete inventory of SciPy public names. The
pair-valued s-t lanes have no per-entity metric consumer, so their
function-level ownership guard is the switch integration; they remain
opt-in CLI commands. This proposal extends the same fail-loud discipline
to the s-t lanes:

- **Ownership:** `ROUTING[YEN_LANE] = ROUTING[MAXFLOW_LANE] = "SCIPY"`.
  `_assert_scipy_lane()` refuses to run a lane the switch does not own.
- **Time:** `LANE_BUDGETS[lane]` (seconds). `_assert_budget()` raises
  `RuntimeError` when a lane overruns — the caller never absorbs a
  minutes-scale blowup unnoticed. Budgets are re-baselined only with a
  measurement.
- **Pre-flight (fail loud, no fallback):** Yen `k` bounded
  (`YEN_K_MAX = 25`; cost is ~K Dijkstras), both endpoints must resolve,
  source ≠ sink, projection non-empty.

This mirrors the PPR switch principle: the switch is what stops the wrong
(slow) engine from running; here it also stops the lane's own runaway.

## 4. Design

- **Yen** (`yen_paths(A, names, source, sink, k, *, directed=False, unweighted=True)`):
  `scipy.sparse.csgraph.yen` over the existing all-edges unweighted CSR;
  returns `[(cost, [name, ...]), ...]`, first path == the Dijkstra answer.
- **max-flow** (`max_flow(cap, names, source, sink)`): `maximum_flow`
  (Dinic) over an integer directed capacity CSR; the min-cut is the
  certificate — residual-reachable set from the source gives the crossing
  arcs, listed with names. `load_capacity_projection()` models each
  undirected edge as two opposite arcs (deduped first), capacities
  `max(1, round(weight))` or 1 (`--unweighted`).
- **CLI:** `yen --source --sink [--k] [--directed]` and
  `max-flow --source --sink [--unweighted]`; read-only (s-t output has no
  per-entity metric shape — no `--apply`, same disposition as the igraph
  pilot and the exact-solve lanes).
- **No dispatch flip:** both stay opt-in CLI lanes (like Katz/eigenvector);
  `ROUTING` declares ownership, not activation.

## 5. Slices

- **S1 — Yen lane:** `yen_paths` + `_run_yen_lane` + CLI command + ROUTING
  entry + budget entry.
- **S2 — max-flow lane:** `max_flow` + `load_capacity_projection` +
  `_run_max_flow_lane` + CLI command + ROUTING entry + budget entry.
- **S3 — switch/budget framework:** `_assert_scipy_lane` +
  `_assert_budget` + `LANE_BUDGETS`; document the "switch owns lane TIME"
  rule in the module docstring.
- **S4 — tests** (`tests/test_scipy_bridge.py`): Yen toy oracle (the
  proposal's 0→4 example: costs 4/5/7, simple, nondecreasing); max-flow toy
  oracle (CLRS: value 23, min-cut {s,v1,v2,v4} vs {v3,t}); ROUTING contains
  both lanes and both s-t lanes have budgets; CLI smoke tests cover both
  commands and capacity projection covers reverse-edge dedupe plus weighted
  and unit modes; `_assert_budget` raises on an overrun, including through
  the CLI; `_assert_scipy_lane` raises on a non-SCIPY owner; live
  under-budget smoke (`@pytest.mark.live`).

## 6. Acceptance

1. Toy oracles above green (hand-checked, no new dependency).
2. Live: `yen` and `max-flow` on one company pair, under budget, with
   named output.
3. `ROUTING`/`LANE_BUDGETS` invariant test green: the two s-t lanes are
   SCIPY-owned and budgeted, and remain outside the node-keyed metric
   dispatch; the existing `test_routing_table_covers_known_metrics` updated
   to admit the two s-t lane keys.
4. `ruff` + `tests/test_scipy_bridge.py` green; full `make qa` once at the
   arc end (house rule).

## 7. Non-goals / risks

- **No consumer wiring.** These are opt-in lanes; the shortest-path
  route/UI consumer is a follow-up with its own acceptance.
- **Full-surface accounting is not a scaffold queue:** converters,
  `test`, and `NegativeCycleError` are non-algorithm support entries;
  no project wrappers or tests are added without a named consumer.
- **Hub-detour caveat (measured, recorded):** on the current graph the
  Yen "alternatives" for a connected pair are often hub detours
  (`Infosys → india → Wipro`) because the graph is hub-dominated; the lane
  is honest about this, and a company-only/sector projection is the
  follow-up if a consumer needs meaningful alternatives.
- **PPR stays deferred** — the structural finding is recorded in
  `doc/improvements/archive/graph/scipy_personalized_pagerank.md` §7 so it is not
  re-litigated on solver grounds.
- **MST / A² kernel stay deferred** (no consumer / trigger not met).
- Budget is a **regression guard, not a preemptive kill**: it fires after
  the lane returns; the gate fails rather than the runtime silently
  growing. A hard timeout is out of scope.

## 8. References

- `doc/improvements/archive/graph/scipy_graph_bridge.md` (§3 S3, §7 surface).
- `doc/improvements/archive/graph/scipy_routing_dispatch.md` (the switch).
- `doc/improvements/archive/graph/scipy_yen_k_shortest.md` (#275),
  `doc/improvements/archive/graph/scipy_maximum_flow.md` (#273).
- `doc/improvements/archive/graph/scipy_personalized_pagerank.md` §7
  (structural blocker).
- Lane: `helpers/graph/scipy_bridge.py`; tests: `tests/test_scipy_bridge.py`.
