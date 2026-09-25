---
title: "Edition coverage tags — quotes→company/ note YAML converger (newsletter S5)"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "290"
area: "helpers/maintenance"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Edition coverage tags — quotes→company/ note YAML converger (newsletter S5)

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/maintenance (new `sync_coverage_tags.py`), maint chain
+ Makefile; `findata/The_Chatter/**` frontmatter (writer-owned surface)

Un-defers S5 of the newsletter notes adoption arc (§4.5, deferred per
Q2 2026-08-19: "execute only after S1–S4 have landed and stabilized" —
five weeks stable). Tier-1 item 1 of the 2026-09-25 pending-items
survey. The trigger is met and the schema question is already answered
(§2).

## 1. TL;RA

Tag each edition note with `company/<entity-slug>` for every company
quoted in that edition, derived from `quotes` — the "which editions
discussed X" index the adoption arc promised. Two consumers are
already live: Obsidian tag navigation and `note_tags` SQL joins
(`sync-tags` rebuilds `entity_tags` from note YAML on every maint run;
the app's tag filter reads `entity_tags`). Measured yield: **98
quote-bearing editions (of 119), 1,286 (edition, entity) pairs, 727
distinct entities**, 1–35 per edition (avg 13.1) → `note_tags`
238 → ~1.5k.

## 2. Evidence (measured 2026-09-25, this box)

- The newsletter frontmatter schema **already reserves the vocabulary**:
  `frontmatter.newsletter.v1.json` tags items are
  `^[a-z0-9_]+/[a-z0-9_]+$` with source vocabulary "series/<tree-slug>,
  publisher/<slug>, **company/<entity-slug>** (newsletter_notes_adoption
  SS3)" — no schema change, no validator relaxation.
- `quotes.as_of_edition` values ARE The_Chatter note stems
  (sample-verified: `Sharp_Takes`, `Jio_Financial_Wipro_Polycab`, …) —
  edition matching is a direct stem lookup, no fuzzy layer.
- Company notes live under `findata/Companies/<sector>/<Stem>.md`; the
  entity→slug step is the normalized-name match onto those stems.

## 3. Design

Standalone converger `helpers/maintenance/sync_coverage_tags.py`,
mirroring the sector-links sync pattern (dry-run default, `--apply`
writes, `--check` reports drift):

1. **S1 — converger core**: `SELECT DISTINCT as_of_edition, entity FROM
   quotes` → filter to entities with a company note stem (company-kind
   only; non-company quote entities — regulators, persons — are
   skipped, count reported) → merge `company/<stem>` into each edition
   note's frontmatter tag list (sorted, deduped, existing
   series/publisher tags untouched). Idempotent by construction;
   re-runs stable.
2. **S2 — wiring**: maint-full TIER2 step (converge after
   `derive-insights`, before `sync-tags`) + bare make target; `--check`
   mode for future gate use.
3. **S3 — shakedown record + archival** per the proposals checklist.

## 4. Acceptance criteria & shakedown

1. All 98 touched notes still validate against the newsletter
   frontmatter schema (static_checks Frontmatter schema + verify_notes
   green).
2. `note_tags` moves 238 → the measured pair count minus non-company
   exclusions (exact number in the S3 record); re-run converges to 0
   changes (idempotency, 3 consecutive runs).
3. Eval gate (`ontology_eval_gate.py`, frozen question set) between
   dry-run and apply: zero regressions — tags flow into `entity_tags`,
   a query-visible roster surface.
4. `make qa` 11/11.

| Projected outcome | Today | After |
|---|---|---|
| edition notes with coverage tags | 0 | 98 |
| note_tags rows | 238 | ~1.5k (measured at S3) |
| "editions discussing X" query | manual quotes scan | tag filter / SQL join |

## 5. Risks

- **Vault churn** (98 generated notes re-frontmattered) — the notes are
  machine-written (`generated.by: process:okf_backfill`), tags are
  additive and sorted; diff noise is one-time then convergent.
- **Slug grammar edge cases** (entity stems with characters outside
  `[a-z0-9_]`) — converger skips and reports any stem failing the
  pattern rather than coercing; count measured in shakedown.
- **Non-company quote entities** — excluded by design (see S1), count
  reported so the delta from 1,286 is explained.

## 6. Non-goals

- No new tags on company notes (edition notes only), no tag changes to
  `series/`/`publisher/`, no quote/metrics capture changes (that is
  `derive-insights`), no cap policy (all qualifying entities — the
  original "capped, e.g. all entities" resolved to ALL; revisit only if
  Obsidian tag-pane noise becomes a real complaint).

## 7. Implementation log (2026-09-25)

S1 and S2 are implemented in the current tree:

- Added `helpers/maintenance/sync_coverage_tags.py` with dry-run default,
  `--apply`, `--check`, deterministic company-slug filtering, and idempotent
  sorted/deduplicated frontmatter tag merging.
- Added the `sync-coverage-tags` Make target and the maint-full ordering
  `derive-insights → sync-coverage-tags → sync-tags`, so the SQL note-tag
  mirror sees the newly written edition YAML in the same run.
- Added focused unit coverage for tag preservation, company/non-company
  filtering, missing editions, and repeated-run convergence.

Shakedown against the live corpus:

- Input pairs: `1,286`; qualifying company pairs: `1,142`; skipped
  non-company pairs: `83`; missing entity mappings: `45`; missing edition
  notes: `16`; invalid slugs: `0`.
- Applied to `91` The Chatter edition notes; `note_tags` now contains `1,380`
  rows across `119` source notes.
- Three consecutive post-apply dry-runs reported `changed=0`.
- The frozen ontology gate accepted all `164` questions with zero
  regressions; `verify_notes`, frontmatter/static checks, Markdown lint,
  Ruff, and ty all pass.

The proposal is executed under the operator's no-perf QA disposition. The
full QA run was `10/11` because the parallel timing-budget test exceeded its
3x allowance; its isolated rerun passed, and no performance test was changed.

## Appendix — raw measurement log (2026-09-25, this box)

| Probe | Result | Notes |
|---|---|---|
| distinct `as_of_edition` in quotes | 98 | of 119 editions |
| (edition, entity) pairs | 1,286 | distinct |
| distinct quoted entities | 727 | type filter at S1 |
| entities per edition | min 1 · avg 13.1 · max 35 | |
| newsletter tag schema | `company/<entity-slug>` pre-allowed | frontmatter.newsletter.v1.json |
| `as_of_edition` ↔ note stems | direct match, sample-verified | |
| note_tags today | 238 rows | research.db |
