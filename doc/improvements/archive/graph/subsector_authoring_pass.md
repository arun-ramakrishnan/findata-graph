---
title: "Sub-sector authoring pass — resolve the 16 parked buckets via alias additions + per-note authored subsector:"
status: executed
filed: "2026-09-19"
executed: "2026-09-19"
completed_md: "258"
area: "findata/Misc (SUB_SECTOR_ALIASES), company-note frontmatter (subsector:), derive_hyperedges, ontology_eval_gate"
---

# Sub-sector authoring pass

**Date:** 2026-09-19 · **Status:** EXECUTED 2026-09-19 ·
**Area:** `helpers/graph/derive_hyperedges.py` (SUB_SECTOR_ALIASES),
company-note YAML frontmatter, `helpers/core/sync_tags.py` (mirror),
`helpers/misc/ontology_eval_gate.py` (gate)

## Problem — measured state

The D11 residue (16 unmapped Yahoo `industry:` labels, ~89 member
companies) sat in `findata/Misc/subsector_worklist.json` as standing
nags; on 2026-09-19 they were parked (export-side parking, review_kit
S4) and the worklist converged to `unmapped: 0, parked: 16`. The
companies behind them have **no sub_sector membership at all** —
membership today derives only from the Yahoo label via the 68-entry
`SUB_SECTOR_ALIASES` map, and the authored escape hatch (#238, live
since 2026-09-16) has never been used: 0 of 1,165 company notes carry
`subsector:`.

This pass resolves each parked bucket by ONE of three levers, then
re-derives under the eval gate.

## The decision matrix (operator input = the S1 deliverable)

| Parked label | members | worklist suggestion | lever |
|---|---|---|---|
| Banks - Regional | 38 | new sub_sector Banks, or Private/Public split per company | S2 author (split) or S1 alias→Banks |
| Packaging & Containers | 11 | no canonical target — taxonomy decision | S1 (new sub_sector) or stay parked |
| Capital Markets | 10 | new sub_sector Capital_Markets | S1 alias→Capital_Markets |
| Metal Fabrication | 10 | no canonical target — taxonomy decision | S1 or stay parked |
| Semiconductors | 6 | new sub_sector Semiconductors | S1 alias→Semiconductors |
| Utilities - Renewable | 5 | Solar/Wind split per company | S2 author (split) |
| Oil & Gas Integrated | 4 | no canonical target — taxonomy decision | S1/S2 or stay parked |
| Department Stores | 3 | no canonical target — taxonomy decision | S1 or stay parked |
| Publishing | 3 | no canonical target — taxonomy decision | S1 or stay parked |
| Tools & Accessories | 2 | no canonical target — taxonomy decision | S1 or stay parked |
| Banks - Diversified | 1 | new sub_sector Banks | S1 alias→Banks |
| 6 single-member labels | 1 each | no canonical target | S2 author / stay parked |

Staying parked is a legitimate resolution for genuinely-deferred
labels — the deliverable is that every bucket is either RESOLVED or
DELIBERATELY parked with a recorded reason, not silently re-asked.

## Slices

- **S1 decision matrix + alias additions**: the operator fills the
  lever column above; every "S1" row becomes a `SUB_SECTOR_ALIASES`
  entry (new sub_sector targets materialize as hyperedges on the next
  derive, same as the existing 68 mappings).
- **S2 per-note authoring sweep**: split/precision rows author
  `subsector:` in each member note's YAML frontmatter via the surgical
  frontmatter writer (the `enrich_from_yfinance` `_update_frontmatter`
  pattern — never a whole-file rewrite). Authored values carry
  canonical precedence over the alias table (#238) and mirror into
  `entity_tags` via sync_tags.
- **S3 eval gate (MANDATORY — query-visible roster semantics)**:
  `ontology_eval_gate.py` over the frozen question set, dry-run vs
  canonical apply, before any `derive_hyperedges --apply` lands. A
  membership regression (count or identity) fails the arc.
- **S4 derive + reconciliation**: `derive_hyperedges --apply` →
  A/B membership diff vs baseline (no involuntary losses) →
  graph-rebuild → full `snapshot_db.py` → `--check` green →
  parking-journal reopen (remove the block, per the review-kit runbook)
  for every label S1/S2 resolved, so future label drift re-asks it.

## Acceptance criteria

1. Every parked bucket is resolved-or-deliberately-parked (recorded in
   the parking journal, not just absent).
2. Eval-gate green between dry-run and apply (S3).
3. A/B membership diff shows additions only (or justified moves).
4. Worklist: `unmapped: 0`; `parked` counts only deliberate deferrals.
5. `verify_notes`, `integrity`, snapshot `--check` green.
