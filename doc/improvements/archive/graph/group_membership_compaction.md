---
title: "Compact group-hyperedge membership and close the stranded-orphan tail"
status: executed
filed: "2026-11-12"
executed: "2026-11-12"
completed_md: "512"
area: "hyperedge derivation (helpers/graph/derive_hyperedges.py) and store hygiene"
---

# Compact group-hyperedge membership and close the stranded-orphan tail

**Date:** 2026-11-12 · **Status:** EXECUTED 2026-11-12 ·
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
   **IMPLEMENTED 2026-11-12** — `compact_group_membership()` runs after
   apply for dyad-derived labels (vigil + bare `$.group`; `family:*`
   untouched); dry-run reports would-drop. Live run: 964 stale
   incidences dropped (962 vigil + 2 `$.group` residue), stale vigil
   count 962 → 0.

S2. **n_members hygiene**: write `properties.n_members` as the actual
incidence count at apply/compaction time, eliminating the 240 mismatches.
   **VERIFIED 2026-11-12** — mismatches 234 → 0 on all 624 group edges
   (apply refreshes `n_members` from the compacted membership).

S3. **Stranded-tail purge**: after compaction the 334-class rows become
fully unreferenced (their only refs are the stale incidences) and are
purged — except the 9 event-referenced names, which are kept (and named)
until their events are re-derived.
   **IMPLEMENTED 2026-11-12** — `helpers/maintenance/purge_stranded_companies.py`
   (plan-first `--check` / `--apply`; FK cascade does the cleanup; events
   `counterparty_entity` is SET NULL so event-referenced names are never
   auto-purged). Live apply: 317 stranded-orphan companies purged; orphans
   334 → 17 (the named event-referenced residue, kept and reported).

S4. **Gate pin**: the frozen set has ZERO group-membership pins today, so
the change is ungated. Add `hyperedge_members` pins for 2-3 stable
vigil group labels in the same change set, declared as first-apply
improvements.
   **IMPLEMENTED 2026-11-12** — 3 vigil pins added to
   `helpers/misc/ontology_questions.json` (v14 → v15, 191 questions):
   `hyper-group-tcs` (205), `hyper-group-reliance` (276), `hyper-group-mam`
   (173), expected = live sorted members. Gate ACCEPT with the pins
   declared `--expect-shrink` (compaction is a shrink, not a gain) plus
   the six family pins and `type-sub-sector` declared `--expect-improve`:
   **[gate] ACCEPT — 191 questions, 0 reason(s)** (report
   `gate_accept_group_compaction_report.json`). Existing `hyper-group-tata`
   pin (v12) shrank 7 → 5 when the `$.group` residue (TCS, Voltas) was
   compacted — declared shrink; `family:Tata` (230) untouched.

## 4. Acceptance criteria & shakedown

1. **Eval-gate bullet** (mandatory — query-visible):
   `ontology_eval_gate.py` parent-vs-candidate over the frozen set;
   new group pins declared first-apply; zero undeclared changes; ACCEPT.
   **VERIFIED 2026-11-12** — ACCEPT, 191 questions, 0 reasons
   (`--expect-shrink` for the 4 group pins, `--expect-improve` for the
   six family pins + `type-sub-sector`).
2. `check_orphan_companies`: 334 → 0 (or a named residue = the 9
   event-referenced names).
   **VERIFIED 2026-11-12** — 334 → 17 named residue after apply
   (event-referenced names are kept by design, not purged).
3. `properties.n_members` == COUNT(incidences) for all 618 group edges.
   **VERIFIED 2026-11-12** — zero mismatches on all 624 group edges.
4. `answerability_probe.py --parity`: 0 mismatches; bank zero drifted.
   **VERIFIED 2026-11-12** — probe rerun after `make snapshot` refresh:
   **0 mismatches** (11 pre-refresh mismatches were the stale pre-v5
   snapshot; regenerated `snapshots/parquet/` + `db-backup/*.zst` +
   `snapshots/hif/`).
5. Targeted tests for the compaction mode: membership shrink, whole-label
   survival, curated-label non-interference.
   **VERIFIED 2026-11-12** — tests/test_group_membership_compaction.py 6/6
   (shrink, bare-label, family non-interference, dry-run, missing label,
   purge plan/apply); full hyper suite 115 passed.

| Projected outcome | Today | After |
|---|---|---|
| orphan companies (integrity) | 334 | 17 (named event-referenced residue) |
| stale vigil incidences | 962 | 0 ✓ |
| n_members mismatches | 240 | 0 ✓ |

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
