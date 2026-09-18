---
title: "Review kit — journaled sitting workflow for every findata/Misc triage queue"
status: executed
filed: "2026-09-19"
executed: "2026-09-19"
completed_md: "252"
area: "helpers/core (new review kit), helpers/graph (triage/suggest/derive surfaces), findata/Misc queues"
---

# Review kit — journaled sitting workflow for every findata/Misc triage queue

**Date:** 2026-09-19 · **Status:** EXECUTED (completed.md #252) ·
**Area:** helpers/core (new `review_kit.py`), helpers/graph
(`triage_pending_relations.py`, `triage_pending_quotes.py`,
`suggest_relations.py`), findata/Misc queue files

**Follows:** `nic2008_seed_table.md` (completed.md #246) — its `review()`
keypress workflow (journaled sittings, batch confirm as the only write
gate, skip-parking via journal read-back, lane partition, idempotent
batch prepopulation) proved itself on 85 labels / 101 lanes and is
currently private to one tool. The graph-side triage flows still run on
hand-edited decision files.

## 1. Motivation

Three manual-review surfaces share the same shape and the same weakness:

- `triage_pending_relations.py` — consumes
  `findata/Misc/_pending_relations.txt` + `_pending_suggestions.txt`
  (link-prediction output of `suggest_relations.py`); the operator
  hand-edits `_pending_triage_decisions.jsonl`, then `apply_decisions`
  validates, writes accepted edges, merges noise, drops decided rows.
- `triage_pending_quotes.py` — the
  `findata/Misc/quote_entity_worklist.json` queue (quote_capture_coverage
  S7): bucketed decisions file, then alias application with target
  validation.
- The `findata/Misc/*_worklist.json` family (counterparty, country,
  subsector, retro_resolution, quote backlogs) — export/report surfaces
  with no sitting UI at all.

Hand-edited decision files are the exact pattern #246 retired for NIC
coding: no journal, no parking (decided rows can resurface), no single
write gate, typo surface in hand-authored JSON, and no audit trail of
WHEN a decision was made. The NIC workflow solved all five; this proposal
extracts it into a shared kit and rehomes the triage flows onto it.

## 2. Evidence (measured 2026-09-19)

- Queues are cyclical — they drain on triage and refill every derive
  cycle (relations queue measured empty post-triage;
  `_pending_triage_decisions.jsonl` carries 10 decision lines; the
  worklist family is small but constant). The cost is per-cycle
  operator friction, not backlog size.
- `seed_nic2008.py review` carried 85 labels / 101 lanes to completion
  through sittings + one 37-sitting scripted batch (idempotent re-run
  verified: 101 rows = 101 distinct keys), guarded by 51 tests — proof
  the pattern scales past ad-hoc triage.
- The triage apply machinery is already plan-then-apply and
  target-validated (`_validate_decisions`, alias validation in S7) —
  only the DECISION INPUT surface is manual JSON.

## 2b. Producer map — every derive_* that defers to a human

| Producer | Queue it emits | Review slice |
|---|---|---|
| `extract_relations.py` (make derive-relations) | `_pending_relations.txt` | S2 |
| `suggest_relations.py` (link prediction) | `_pending_suggestions.txt` | S2 |
| `derive_insights.py` (fuzzy entity resolution, "never auto-applied") | quote entity worklist + backlogs | S3 |
| `derive_countries.py` (`unmapped_suffix` / no-ticker hints) | `country_worklist.json` | S4 |
| `triage_pending_quotes.py` S7 queue | `quote_entity_worklist.json` | S3 |
| `subsector_worklist.json`, `counterparty_worklist.json`, `retro_resolution_worklist.json` | export-only today | S4 |

Rule the kit enforces on producers: queue item ids must be STABLE
(relations already key `_row_id(edge_type, source, target)`; countries
key on company name) — parking is only as good as the id, so a refilled
queue never re-asks a decided item.

`derive_themes.py` needs no queue: it records matching aliases in edge
`properties` for post-hoc audit. `derive_events.py` preserves
hand-seeded (`manual:`) rows and has no deferral.

## 3. Design — the kit, not a rewrite

`helpers/core/review_kit.py` extracts the workflow from
`seed_nic2008.review` as a generic `ReviewSession`:

- items + lane partition (suggested / parked / promoted / no-signal),
  journal read-back decides parking (latest action per item wins);
- per-domain decision verbs as callbacks (`1/2/3` style ranked picks,
  `c CODE` overrides, bucket picks) — the kit owns the loop, journal
  format (session-bounded JSONL, append-only), evidence rendering hook,
  `--labels/--redecide/--skipped/--limit/--dry-run` flags;
- batch confirm stays the only write gate; application always delegates
  to the domain's EXISTING machinery (relations `apply_decisions`, S7
  alias application, NIC promote lane) — the kit never writes domain
  data itself.

Non-goals: no Textual TUI (search_tui remains the rich-UI lane; a TUI
adapter can come later), no model/API in any ranking, no schema change
to the findata/Misc interchange files beyond what apply already reads,
writer-owned vault untouched.

## 4. Slices

- **S1 kit extraction**: `review_kit.py` + rehome `seed_nic2008.review`
  onto it — zero behavior change, the 51 existing tests are the
  regression harness. Make `review-kit` doc entry in
  `doc/procedures/` covering the journal convention.
- **S2 relations sitting**: `triage_pending_relations.py review` —
  walks the queue that `extract_relations.py` (derive-relations) and
  `suggest_relations.py` refill; verbs: accept target (entity-name
  validated) / reject -> noise / alias / park / quit; apply via existing
  `apply_decisions`; `_pending_triage_decisions.jsonl` becomes
  tool-WRITTEN (still the machine record, no longer hand-edited).
- **S3 quotes sitting**: `triage_pending_quotes.py review` — bucket
  picks as keypresses; alias application with target validation via the
  existing S7 machinery.
- **S4 worklist convergence**: `derive_countries.py`'s
  `country_worklist.json` (unmapped-suffix / no-ticker decisions),
  counterparty, subsector and retro_resolution worklists get the same
  lane + parking semantics on export (small files; the win is one
  uniform workflow and "decided once, never re-asked" everywhere).

Gate-bullet rule (proposal template §4) applies: each slice names its
gates — per-slice pytest suites (51 NIC tests, S7 quote tests, triage
tests), live-vs-live self-checks where the apply lane touches the DB,
and full `make qa` at arc close.

## 5. Risks

- `triage_pending_relations.py` is the largest apply surface (noise
  merge + accepted-edge writer); S2 must not fork its semantics — the
  sitting only PRODUCES the decisions structure the existing validator
  already consumes.
- Journal read-back parking must be per-domain (separate journal files
  per queue), or decisions in one lane would park another.
