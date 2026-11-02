---
title: "HGX shelf spectral + random walk measurements (lanes 3, 5, 6) — wrapped + wired"
status: executed
filed: "2026-11-02"
executed: "2026-11-02"
completed_md: "472"
area: "graph"
---
# Proposal — HGX shelf spectral + random walk measurements (lanes 3, 5, 6) (2026-11-02)
Status: EXECUTED — lanes 3+5 wired same day (#472, helpers/graph/hyper_spectral.py); lane 6 deferred upstream (operator).
Parent records: completed.md #451 (hgx_unlock_shelf_probe.md); superseded restatements pending.md:359 / 414 (2026-11-02); shelf archive +135 lines (addendum + Lane 6 post-install); proposal file created 2026-11-02.

## What was done (this session, in order)
- Lane 1–6 shelf re-probe (6.8× trigger fired); all measurements recorded to archive/graph/hgx_unlock_shelf_probe.md (Re-probe 2026-11-02 section, +135 lines).
- Lane 3 first multi-order Laplacian measured (orders to max_k; commute false ed/ind/org, true sub_sector); .any() broken at linalg.py:475 noted.
- Lane 5 RW density measured on LC (edition 771/771 single component; industry/sub_sector/org_membership 63/51/41 of 1004/999/277 — 6%/5%/15%).
- Lane 6: sklearn installed (user go m00250); HySC + HypergraphMT import OK; hyperlink_communities blocked upstream; core_periphery cost unchanged.
- Proposal hgx_shelf_spectral_rw_measurements.md written; shelf archive + proposal preserved; no commit/stg (user stages); approval never requested (disabled); absorb used for long results.

## Lane 3 production spec (advisory — wire when user stages)
Target: helpers/graph/query.py spectral-consumer slot.
Fix: linalg.py:475 .any() missing on scipy csr_array. Replace with (np.abs(A-B)>0).sum()>0 or eq. Gate-targeted test required (not full make pytest).
Baseline (from shelf addendum): edition orders 1–34, industry 1–51, sub_sector 1–50, org_membership 1–31; commute false on ed/ind/org, true sub_sector.

## Lane 5 production spec (advisory — wire when user stages)
Target: query.py diffusion lane; expose component-annotation.
Baselines (LC): edition 771/771 RW 0.006035 (L2 0.001297→0.002239); industry 63/1004 RW 0.024935; sub_sector 51/999 RW 0.019608; org_membership 41/277 RW 0.047194.
Structural finding: only edition is single dominant component; others fragmented — component-handling must be visible in output.

## Gate reference (user's arc at end)
doc/procedures/gates.md: advisory (search-fresh) at start; targeted tests; user stages/commits; full gate (qa + integration + perf + advisory) ONCE per arc on user's go. This proposal is advisory until then.

## Production wire (2026-11-02, lanes 3+5 implemented — #472)

`helpers/graph/hyper_spectral.py` (+ tests/test_hyper_spectral.py): Lane 3
`--lane spectral` (per-order Laplacians, nnz/tolerance commutativity check
productionized, opt-in --spectra algebraic connectivity) and Lane 5
`--lane rw` (component annotation FIRST — count/sizes/LC fraction — then
random_walk_density on the LC). Wired as a standalone hyper_* lane CLI, not
the proposal's nominal query.py slot (no spectral slot exists in query.py,
and linalg.py:475 turned out to be hypergraphx's own linalg — the .any() gap
breaks HGX are_commuting on csr_matrix too, not just csr_array).

CORRECTION to this proposal's Lane 3 verdict: the re-probe's "sub_sector
COMMUTES across orders 1-50" is NOT reproducible. Boundary max|L_i - L_i+1|
grows as O(order) (2.0, 3.0, 4.0, ...) — structurally non-commuting, not
float dust (tolerance 1e-9 still reports NOT). The production lane reports
NOT for all four default families on the live store. Reconcile with the
probe author before trusting any order-decomposition-independence claim.

Live wire verdicts (2026-11-02 store): edition 34 orders NOT, industry 51
NOT, sub_sector 50 NOT, org_membership 31 NOT; component annotations match
the re-probe (edition 771/771 single, industry 63/984, sub_sector 51/983,
org_membership 41/273).

## Reopen triggers (carried, unchanged unless production wire fires)
Lane 1: NO-GO (norm_delta 0.0). Lane 2: unchanged verdict. Lane 3: new trigger — wire when spectral consumer exists. Lane 4: upstream dict_to_df fix. Lane 5: wire when component-annotation reproducible. Lane 6: wire when cost <10s/1000 nodes or sklearn + upstream fixes land.
