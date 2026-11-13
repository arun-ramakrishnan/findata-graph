---
title: "Gate-acceptance growth watch: block on curated-group signal erosion"
status: executed
filed: "2026-11-13"
executed: "2026-11-13"
completed_md: "518"
area: "tooling (gate battery; helpers/maintenance/group_discovery_growth_monitor.py)"
---

# Gate-acceptance growth watch: block on curated-group signal erosion

**Date:** 2026-11-13 · **Status:** EXECUTED 2026-11-13 (completed.md #518) ·
**Area:** gate battery (qa) — growth-monitor `--check` policy + tracked reference

## 1. Motivation

The group-discovery lane now curates 39 family seeds over 657 same_group
components (`group_discovery_scale_lane.md` #514). The S4 growth monitor
(`group_discovery_growth_monitor.py`) diffs inventory reports, but exits 0
always — it is an advisory lens. Nothing blocks gate acceptance when the
curated-group signal erodes: a relabeled `vigil:rpt:*` source, a pruned
seed, or a dyad-correction replay can silently drop family members from
components or collapse a component that carried curated families, and the
battery still goes green. This proposal makes erosion a gating failure,
with a deliberate refresh path for intended structural change.

## 2. Design

### 2.1 Monitor gains a `--check` policy

`group_discovery_growth_monitor.py` gains:

- `gate_check(reference, current) -> (ok: bool, reasons: list[str])` —
  runs `compute()` and applies the failure policy below;
- CLI flags: `--check` (exit 1 on policy failure), `--refresh-reference`
  (rewrite the tracked reference from the current run — the deliberate
  change path, analogous to the questions rebaseline), `--db`
  (run the inventory inline against a live DB instead of a `--current`
  file).

Failure policy (regressions only — growth and new components are allowed
and reported):

1. `family_hit_delta < 0` — curated-family signal eroded (fewer family
   members appear in components than the reference).
2. `merge_flags_removed` non-empty — a previously adjudicated multi-family
   merge flag disappeared from the topology.
3. Any gone component that carried `family_members` — a component hosting
   curated family members vanished entirely.
4. Empty current dyad set — the store lost company-company `same_group`
   edges (a store problem, not a growth outcome).

### 2.2 Tracked reference

New file `helpers/maintenance/group_discovery_reference.json`: the
post-v7 inventory report (657 components, sampled coverage 5,037,
family hits 2,088) committed as the gate baseline. Refresh is a
deliberate act: `--refresh-reference` overwrites it and must land in the
same change as the family-seed edit that caused the shift (same doctrine
as the ontology-questions rebaseline).

### 2.3 Gate wiring

New blocking step `group-growth-check` in the `qa` gate
(`tests/run_gate_report.py`), after `schema-check`:

```bash
python3 helpers/maintenance/group_discovery_growth_monitor.py \
    --check --db memory/research.db \
    --reference helpers/maintenance/group_discovery_reference.json
```

It runs the inventory inline (read-only, seconds), diffs against the
tracked reference, prints the drift report, and fails the gate on any
policy condition. The same machinery stays usable manually
(`--check --reference ... --current ...`).

### 2.4 Docs

`doc/procedures/gates.md`: the qa battery section documents the new leg,
its failure conditions, and the refresh path.

## 3. Acceptance criteria

1. `gate_check` policy tests: each failure condition fires alone (delta
   loss, flag removal, family-bearing gone component, empty dyads);
   growth-only diffs pass; missing/empty reference fails with guidance.
   **VERIFIED 2026-11-13** — 6 policy tests (growth passes, hit-loss,
   flag-removal, family-component-gone, empty-current) + wiring
   introspection; 11/11 in tests/test_group_discovery_growth_monitor.py.
2. Wiring introspection test: `group-growth-check` present among the qa
   gate steps.
   **VERIFIED 2026-11-13** — qa gate includes the step after `schema-check`;
   test asserts label + `--check` args.
3. Live run on the current store: `--check` exits 0 (reference == current
   state) with a rendered drift report.
   **VERIFIED 2026-11-13** — PASS, 657→657 components, family-hit delta
   +0, size bands flat; sampled coverage 5,037→5,050 (growth, allowed).
4. `--refresh-reference` writes the tracked reference from a live
   `--db` run.
   **VERIFIED 2026-11-13** — writes from inline inventory; manual run
   confirmed (reference rewritten from the live store).
5. ruff + py_compile clean; the existing monitor suite (4 tests) plus new
   policy tests pass.
   **VERIFIED 2026-11-13** — ruff clean, py_compile clean, 42 passed across
   the monitor/inventory/lens/gate-runner suites.

## 4. Risks

- **False fails on intended family changes**: the fix is the refresh path
  — the reference moves in the same change as the seed edit. Documented
  in gates.md.
- **DB availability**: the leg reads `memory/research.db`, same as the
  existing `integrity_check`/`schema-check` legs — no new dependency
  class.
- **Battery cost**: inventory + diff ≈ seconds; negligible against the
  ~348 s qa wall.
- **Noise from legit corpus growth**: new components, grown components,
  and added merge flags are reported, never failed — the policy only
  fails on erosion of curated signal.

## 5. Non-goals

- No auto-refresh of the reference — every move is deliberate.
- No new gate name; the leg rides the existing qa battery (the gate_query
  surface is unchanged).
- No change to the advisory `--json`/render behavior (backward
  compatible: default invocation still prints and exits 0).
