---
title: "Complete the S9 counterparty and S11 subsector lanes (curated aliases + authored industry mapping)"
status: executed
filed: "2026-11-12"
executed: "2026-11-12"
completed_md: "511"
area: "graph lanes (S9 counterparty resolver alias tier; S11 subsector taxonomy + authored assignment)"
---

# Complete the S9 counterparty and S11 subsector lanes

**Date:** 2026-11-12 · **Status:** EXECUTED 2026-11-12 (completed.md #511) ·
**Area:** `resolve_counterparties` alias tier
(`helpers/misc/counterparty_aliases.json`); S11 authored subsector
assignments + taxonomy node (`build_sector_hierarchy.py`, `SUB_SECTOR_ALIASES`)

## 1. Motivation

The name-token/family-seed arc
(`name_token_family_seed_lane.md`) closed its gate at 188 questions / 0
reasons, but its surface revealed two sibling gaps in the *same* derive
pipeline, each with a cheap, curated closure:

- **S9 counterparty resolution** leaves unmatched `events.counterparty`
  rows unresolved. On the 2026-11-12 store, 8 raw counterparty strings
  failed all three existing tiers (exact name / NOCASE / normalized_name)
  — several are trivially curatable: parent-brand vs Indian leg
  ("Abbott" → Abbott India), a missing space, a known spelling variant.
- **S11 sub-sector mapping** had 12 industry labels with no
  `SUB_SECTOR_ALIASES` target. Several were singletons with an obvious
  canonical node (Jinkushal → Specialized_Logistics, Harley-Davidson →
  Automobiles, Indus Towers → Telecom_Services, Energy Infrastructure
  Trust → Infrastructure_Operations, Antony Waste → a brand-new
  Waste_Management node); the rest (banks, packaging, capital markets,
  metal fabrication, semiconductors, renewable utilities) are *split
  buckets* — no single label→node mapping is correct, so the companies
  in them must be authored to per-company sub-sectors instead.

This proposal records both closures, their measurements, and the gate
acceptance that lands them — as a separate document from the name-token
lane, which is about family *seed discovery* and is already archived as
its own story.

## 2. S9: curated counterparty alias tier

Design: a curated `counterparty_aliases.json` consulted by
`resolve_counterparties` **after** exact/NOCASE/normalized tiers and
**before** the unresolved bucket. Stale alias targets (not resolving to a
stored entity) are dropped + reported, never minted — same doctrine as
family seeds. Matched rows get `counterparty_entity` set in place.

**Implemented 2026-11-12** — `helpers/misc/counterparty_aliases.json`
(4 entries):

| raw counterparty | canonical entity |
|---|---|
| Abbott | Abbott India |
| Coal Lignite Urja Vikas | Coal LigniteUrja Vikas |
| L&T | Larsen & Toubro |
| STAR UNION DAI-ICHI LIFE INSURANCE CO | Star Union Dai-Ichi Life Insurance Company |

Measured (live store, `events.counterparty_entity`):

| state | unresolved | resolved |
|---|---|---|
| pre-alias | 8 | 0 |
| post-alias apply | **4** | 1,118 total |

The 4 remaining unresolved — `Adani Group`, `Reliance Group)`, `Renew`,
`Highbond` — are parked in
`outputs/worklist_parking/counterparty/journal.jsonl` (action `skip`,
approved 0); the worklist rewrite (`findata/Misc/counterparty_worklist.json`)
converges with park state on every apply.

## 3. S11: authored sub-sector mapping + taxonomy node

Two mechanisms:

1. **Aliases** (`SUB_SECTOR_ALIASES`, `derive_hyperedges.py`) for the
   singleton labels (5 new): `Recreational Vehicles` → Automobiles,
   `Telecom Tower Infrastructure` → Telecom_Services, `Oil & Gas
   Midstream` → Infrastructure_Operations, `Industrial Distribution` →
   Specialized_Logistics, `Waste Management` → Waste_Management. The
   `Waste_Management` node was added to `SUB_CATEGORIES` under
   Infrastructure in `build_sector_hierarchy.py` (Waste Management is an
   infrastructure sub-sector; the node is created by the idempotent
   `--apply`).
2. **Authored lane** — per-company `subsector:` frontmatter for the 6
   split-bucket labels: 93 notes (bank PSU/private split, flexible/rigid
   packaging, brokers/depositories/exchanges/etc., forge/castings,
   semiconductor design/memory/foundry, solar/hydro) written via
   `helpers/misc/author_subsector.py --map` from a generated candidate
   map (`bench_data/subsector_mapping_candidates/notes_map.json`, 93/93
   stems resolved, 0 missing). Label-level unmapped remains for split
   buckets *by design* — the membership is authored, so the label is not
   a single-node target.

Also fixed: `Cosmos_Co_operative_Bank.md` — dropped the wrong
`industry: Medical Distribution` label (it is a cooperative bank; kept
`sector: Banking` + `subsector: Cooperative_Banks`).

Measured (S11):

| metric | before | after |
|---|---|---|
| unmapped industry labels | 12 | **6** (all members authored) |
| aliases | 111 | 116 |
| sub_sector hyperedges | 108 | 111 |
| members | 999 | 1,011 |
| authored assignments (D7) | — | 203 (182 unique) |
| taxonomy | — | +Waste_Management node (153 entities / 185 edges applied) |

## 4. Acceptance criteria

1. **Eval-gate bullet**: `ontology_eval_gate.py` over the frozen parent
   (14:18 pre-apply snapshot `research_parent_pre_apply.db`) vs live
   candidate; declared first-apply improvements `--expect-improve`
   incl. `type-sub-sector` (Waste_Management node); **ACCEPT, 188
   questions, 0 reasons** (reports `gate_accept_v14_report.json` and
   `gate_final_subsector_proposal_report.json`).
   Un-declared run REJECTs with exactly the expected undeclared changes
   (type-sub-sector + the 6 hyper-family pins vs the pre-family parent) —
   the declaration set closes them.
2. Alias targets resolve to stored entities; stale alias → dropped +
   reported, never minted (validated in `_load_counterparty_aliases`
   + resolve-time check).
3. Worklist converges with park state on apply (journal `sitting-end`,
   approved 0; worklist count = 4, parked 4).
4. Authored notes: 93/93 carry the target `subsector:` value (verify
   pass: 0 missing) — frontmatter edit only, no DB writes by
   `author_subsector.py`.
5. Taxonomy apply idempotent (`INSERT OR IGNORE`; re-run reports 153
   entities / 185 edges unchanged).
6. Targeted tests: `tests/test_hyper_incidence.py` (S11 stale-alias
   path + node membership) green.

## 5. Risks

- **Split buckets stay label-unmapped** — intentional; if a future
  taxonomy wants `Banks - Regional` → one node, the authored lane
  override must be removed in the same change (D7 has precedence over
  aliases).
- **Alias rot** — aliases are curated; a renamed entity + a stale alias
  is dropped + reported (never silent).
- **Authored assignments are static** — new companies in split buckets
  need a mapping pass; the generator (notes_map.json) is regenerable.

## 6. Non-goals

- No auto-apply of unresolved counterparties beyond curated aliases.
- No single-node taxonomy for the 6 split buckets (per-company authored).
- No change to family-seed S1/S2/S3 (name_token_family_seed_lane.md).
- No change to `GROUP_RES` prose regexes.

## Appendix — raw evidence

| Run | Result |
|---|---|
| S9 pre-alias derive | 0 resolved / 8 unresolved |
| S9 post-alias apply | 4 unresolved (parked skips), 1,118 total resolved |
| S11 apply (post-taxonomy) | 116 aliases → 111 hyperedges / 1,011 members; D7 203 (182) |
| gate: un-declared | REJECT 7 reasons (type-sub-sector + 6 family pins) |
| gate: declared --expect-improve | **ACCEPT — 188 questions, 0 reasons** |
