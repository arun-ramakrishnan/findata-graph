---
title: "Compact group-hyperedge membership and close the stranded-orphan tail"
status: proposed
filed: "2026-11-12"
executed: null
completed_md: null
area: "hyperedge derivation (helpers/graph/derive_hyperedges.py) and store hygiene"
---

# Compact group-hyperedge membership and close the stranded-orphan tail

**Date:** 2026-11-12 · **Status:** PROPOSED ·
**Area:** helpers/graph/derive_hyperedges.py (apply_hyperedges, prune_stale_group_edges); store-hygiene tail

## 1. Motivation

The RPT attribution arc (#507) corrected filer-subject attribution, and 524
pre-existing counter-party companies lost dyads that were artifacts of the
old loader. But `apply_hyperedges` is additive-only — its docstring
says "Membership shrinkage (a company leaving a category) is NOT compacted
here — the backfill is additive; compaction belongs to a future
full-refresh mode" — so those companies kept their group-hyperedge
incidences. The 2026-11-12 bounded cleanup (operator go: 400 case-variant
merges, 143 zero-ref purges, 80 person retypes, 1 file_path fix) removed
every other orphan class; what remains is exactly the class this proposal
exists to close:

- 334 companies orphaned in `check_orphan_companies`, every one homed
  ONLY by a stale group incidence (measured: 334 of 334).
- 962 stale incidences under the 602 `vigil:rpt:<SYMBOL>` group labels
  (member has no current `same_group` dyad under that label).
- 240 of 618 group hyperedges carry `properties.n_members` != their
  actual incidence count.
- 10 `jv` events still point at 9 stranded names.

## 2. Evidence (measured 2026-11-12, this box)

| Measurement | Value | Source |
|---|---|---|
| residual orphans after the bounded cleanup | 334 | check_orphan_companies |
| ... homed only by stale group incidences | 334 of 334 | orph_remeasure.py |
| stale incidences under vigil labels | 962 | same |
| group edges with n_members mismatch | 240 of 618 | same |
| pre-replay orphans that HAD dyads | 524 of 524 | research.db.prerpt comparison |
| events on stranded names | 10 events / 9 names | same |
| pins touching group/vigil labels | 0 | ontology_questions.json v12 + bank v8 |

All 524 pre-existing orphans had a dyad before the #507 replay and lost it
to the corrected attribution — the incidences are the residue of
filer-subject artifacts, not live group facts. Raw logs:
bench_data/orphan_cleanup/ (plan.json, apply_log.json).

## 3. Design

S1. **Compaction mode in apply_hyperedges** (helpers/graph/derive_hyperedges.py):
for `vigil:rpt:<SYMBOL>` group labels, membership is rebuilt from the
current `same_group` dyads carrying that source_ref; incidences whose
member has no current dyad under the label are deleted. Curated family
labels (`family:*`, seed-driven via family_seeds.json) are NOT
dyad-derived and are untouched; every other family (events, supply,
subsidiary, ...) is untouched.
S2. **n_members hygiene**: write `properties.n_members` as the actual
incidence count at apply/compaction time, eliminating the 240 mismatches.
S3. **Stranded-tail purge**: after compaction the 334-class rows become
fully unreferenced (their only refs are the stale incidences) and are
purged — except the 9 event-referenced names, which are kept (and named)
until their events are re-derived.
S4. **Gate pin**: the frozen set has ZERO group-membership pins today, so
the change is ungated. Add `hyperedge_members` pins for 2-3 stable
vigil group labels in the same change set, declared as first-apply
improvements.

## 4. Acceptance criteria & shakedown

1. **Eval-gate bullet** (mandatory — query-visible):
   `ontology_eval_gate.py` parent-vs-candidate over the frozen set;
   new group pins declared first-apply; zero undeclared changes; ACCEPT.
2. `check_orphan_companies`: 334 → 0 (or a named residue = the 9
   event-referenced names).
3. `properties.n_members` == COUNT(incidences) for all 618 group edges.
4. `answerability_probe.py --parity`: 0 mismatches; bank zero drifted.
5. Targeted tests for the compaction mode: membership shrink, whole-label
   survival, curated-label non-interference.

| Projected outcome | Today | After |
|---|---|---|
| orphan companies (integrity) | 334 | 0 (9 named if events kept) |
| stale vigil incidences | 962 | 0 |
| n_members mismatches | 240 | 0 |

## 5. Risks

- **Honest shrinkage** — compaction deletes memberships for companies that
  genuinely left a filer's disclosure set; a filer re-disclosing re-adds
  them on the next refresh (additive apply still runs first).
- **Whole-label loss** — a vigil label whose dyads all vanished loses every
  member; `prune_stale_group_edges` already drops whole labels absent
  from the collect output — unchanged behavior.
- **Event-dangling** — purging the 9 event-referenced names would dangle 10
  `jv` events; they are kept and named instead.

## 6. Non-goals

- No change to the orphan check's exemptions (the stale incidences are the
  disease, not the exemption list).
- No further person/company re-typing beyond the 80 already ruled and
  applied (2026-11-12).
- No compaction of non-group families.
- No new note files for stranded names.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-12 | bench_data/orphan_cleanup/ plan+apply | 958 → 334 | 1 fix + 400 merges (9 repointed / 320 dup-dropped) + 143 purge + 80 retype |
| 2026-11-12 | orph_remeasure.py | 962 stale / 240 mismatch | post-cleanup |
| 2026-11-12 | orphan_pre.py (prerpt) | 524/524 had dyads | pre-replay comparison |
