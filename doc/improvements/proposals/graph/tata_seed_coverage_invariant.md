---
title: "Tata family-seed coverage invariant: filer-label maximality guard"
status: proposed
filed: "2026-11-13"
executed: null
completed_md: null
area: "graph (family-seed integrity guard; helpers/maintenance/family_label_coverage_check.py)"
---

# Tata family-seed coverage invariant: filer-label maximality guard

**Date:** 2026-11-13 · **Status:** PROPOSED ·
**Area:** family-seed integrity (read-only census + qa gate step)

## 1. Motivation

The group-discovery arc (#514) closes with `family:Tata` at 230 curated
members. A v8 expansion was considered from the C1 dyad blob (678 members
across 15 `vigil:rpt:TATA*` labels) — but live measurement refuted the
candidate pool:

- all 21 labels touching the seed are fully contained in it: union of
  label members = 230 exactly (TCS 136/136, TMPV 133/133, VOLTAS
  119/119, TTML 86/86, INDHOTEL 80/80, TATASTEEL 72/72,
  TATAPOWER 71/71, TITAN 63/63, TATAELXSI 37/37, TATACONSUM 31/31,
  TATACOMM 22/22, TATAINVEST 22/22, TATATECH 21/21, TATACHEM 20/20,
  TEJASNET 19/19, TRENT 5/5 …);
- core rule (≥2 RPT-label memberships, outside seed): **0 candidates**;
  name-token "tata" corroboration: **0 candidates**.

The remaining ~448 C1 members are dyad-connected *without* any filer-label
membership — exactly the weak, #507-inflation-prone evidence class the
family lane screens out. So the 230 is **maximal w.r.t. labeled
evidence**, and an expansion is not justified. What is missing is a guard
that keeps it that way: filer relabels or future dyad edits must not be
able to desynchronize the seed from the `vigil:rpt:TATA*` evidence sets
without a loud gate failure.

## 2. Design

New read-only helper `helpers/maintenance/family_label_coverage_check.py`:

- loads curated families from `helpers/misc/family_seeds.json` (reusing
  the inventory's loader) and group hyperedges (`hyper_edges` +
  `hyper_incidences`) from the live DB;
- evaluates only **filer labels** (`vigil:rpt:*` — dyad-derived evidence
  sets; `family:*`/`$.group` labels are excluded from the invariant);
- for each family marked **complete** (constant `COMPLETE_FAMILIES =
  {"Tata"}`; overridable via `--families`, `all` for every family), for
  each filer label that intersects the family: require the label to be
  **fully contained** (`label ⊆ family`);
- reports per-label coverage (FULL / PARTIAL / NONE) and exits 1 under
  `--check` when a complete-family filer label is only partial;
- `--db` for the store path; no writes; skip-safe on empty label sets.

Wire as a blocking `qa` gate step `family-label-coverage` (after
`group-growth-check`), documented in `doc/procedures/gates.md`. A future
expansion is still possible — it must move the seed and the gate stays
green because the invariant is then satisfied again.

## 3. Acceptance criteria

1. Unit tests: FULL label passes; PARTIAL label fires under `--check`
   for a complete family; non-filer labels never fire; `--families`
   override scopes the check; empty store skips rc 0.
2. Live run on the current store: `--check` exits 0 (all 20
   `vigil:rpt:TATA*` labels FULL; the `family:TVS` intersection is
   excluded as non-filer).
3. qa gate step present; gates.md documents the leg.
4. ruff + py_compile clean; helper + round suites pass.

## 4. Risks

- **False fail on a legitimate family edit**: by design — editing
  `family:Tata` (or any complete family) without keeping every filer label
  contained is a curation error; the deliberate path is to change the seed
  and ship it in the same change (the gate then passes).
- **Partial filer labels for non-complete families**: informational only
  (they appear in the report, never fail) until a family is declared
  complete.
- **No new dependency class**: same live-DB read pattern as
  `integrity_check`/`schema-check`/`group-growth-check`.

## 5. Non-goals

- No seed edits (the evidence shows none are justified).
- No auto-expansion; no change to the curated 230.
- No gate-name surface change (rides the qa battery).
