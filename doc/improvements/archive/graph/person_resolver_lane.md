---
title: "Person nodes (D6) first slices — name resolver, prose FP fixes, trigger-gated SHP diff"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "293"
area: "helpers/core"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Person nodes (D6) first slices — name resolver, prose FP fixes, trigger-gated SHP diff

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/core (new `person_names.py`), helpers/maintenance
(`shareholding_sync.py` guard), helpers/graph (`derive_events.py` ARM 2
filters), helpers/validators (`CANONICAL_EVENT_TYPES`)

Opens the hierarchy-ledger D6 row ("person nodes — DEFERRED, D7 DONE —
now unblocked") with the three slices that have data today, and
trigger-gates the fourth. Closes Tier-2 item 6 of the 2026-09-25
pending-items survey (`doc/local/notes/pending_items.md`). The
deferred-item gates recorded elsewhere stand: the SHP-holder lane
already created person entities + `invested_in` edges (#267, 2026-09-22
— 30 persons, 30 of 799 `invested_in` edges live), so this arc adds
**resolution quality and deterministic capture**, not a new edge type.
DIN/directorships stay blocked (MCA bulk 403; no appointments source).

## 1. TL;RA

The named gate — a person resolver — is real and measurable today:
all 30 person entities are SHP holder stubs, and they exhibit the
exact disambiguation hazard (`"MANHAR GORDHANDAS GANDHI"` beside
`"MANHAR G GANDHI (SMALL )HUF"` — an HUF is a distinct legal person;
merging across that line is forbidden, merging within it is required).
The prose capture arm is thin AND noisy: 5 `management_change` rows,
of which ≥2 are false positives ("TCS takes over Porsche's automotive
contract" — an acquisition; "IndiGo is evolving … into a hybrid model"
— a mode change) and the Infosys succession is double-counted (board
quote + summary bullet both firing). The structured alternative is
under-accumulated: `shp_filings` holds 9 filings, **1 symbol with ≥2
filings** — too young to diff today, so the holder-change diff slice
ships trigger-gated, not speculative.

Holder data needs a normalization map before any of this:
`shp_holders` carries **60+ raw category spellings** across 333 rows,
including artifact/aggregate rows (`Total`, `ShareholdingPattern`,
`DetailsOfSharesHeldBy*`) that are not holders and must be excluded;
51 rows are person-class by a broad filter.

## 2. Slices

1. **S1 — resolver** (`helpers/core/person_names.py`):
   `classify_person_name` (person | huf | trust/family-office via
   suffix tokens), `person_name_key` (casefold, token-sort,
   initial-fold), `resolve_person(name, candidates)` built on
   `word_overlap_match` (`fuzzy_match.py`) under a hard
   classification constraint — never merge across person↔HUF↔trust.
   Ships with a dedupe report over the 30 existing persons;
   `--apply` performs merges via the owned `rename_entity.py`,
   operator-reviewed before apply. Conservative default: a missed
   merge is recoverable, a wrong merge is costly.
2. **S2 — ingest guard + category map** (`shareholding_sync.py`):
   resolve before stub INSERT (no new near-dup persons); normalize
   the 60+ raw `category` spellings to
   `{promoter, promoter_group, person, institution, company, public,
   artifact}` with artifact rows skipped (measured count in the
   shakedown record).
3. **S3 — prose FP fixes** (`derive_events.py` ARM 2): sense filters
   for the two measured FP classes (acquisition-sense "takes over
   ⟨non-role noun⟩"; mode-sense "evolving from/into") + dedupe key
   `(entity, person_name_key, as_of quarter)` — collapses the Infosys
   double-count. Audit target on the live set: **5 → 3 events**.
4. **S4 — TRIGGER-GATED: SHP holder-change diff → `holding_change`
   events.** Per `(symbol, holder)` across consecutive filings:
   category flip, stake Δ ≥ 1pp, or material pledge Δ → one
   deterministic event (counterparty resolved via S1 where person).
   Adds one `CANONICAL_EVENT_TYPES` member
   (static_checks.py:643 — single enumeration, checker reads it).
   **Trigger: ≥10 symbols with ≥2 filings (today: 1).** Not built
   before the trigger fires — no speculative machinery.

## 3. Acceptance criteria & shakedown

1. Seeded resolver tests: the HUF pair is NOT merged; known variant
   pairs merge; the 30-entity dedupe report proposes N merges and the
   operator signs off before `--apply`.
2. Prose audit: on the live 5-row set, exactly the 2 known FPs drop
   and the dup merges (5 → 3); a corpus-wide ARM-2 re-run reports the
   full delta.
3. Category map classifies all 333 holder rows; artifact rows
   excluded (count reported); no holder row maps to more than one
   class.
4. Eval gate over the frozen question set between dry-run and apply:
   zero regressions (events and edges are query-visible surfaces).
5. `make qa` 11/11. S4 acceptance is authored when its trigger fires;
   nothing in S1–S3 depends on it.
6. Repeat-count: resolver parity over the seeded set ×3 runs
   (deterministic); prose re-run idempotent on second pass.

| Projected outcome | Today | After |
|---|---|---|
| person entities | 30 (unresolved) | 30 − N dupes, resolver-guarded ingest |
| management_change rows | 5 (≥2 FP, 1 dup) | 3 (audited) |
| holder rows classified | 0 | 333 (artifact rows excluded) |
| holding_change events | — (type absent) | trigger-gated, deterministic |

## 4. Risks

- **Over-merge** (the HUF hazard and beyond) — hard classification
  constraint + operator-reviewed merge report before any `--apply`;
  merges run through the owned rename tool with its cascades.
- **Under-merge** (conservative default) — accepted by design;
  residual dupes surface in the next dedupe report, recoverable.
- **Category mis-map** (60+ spellings, XBRL ctx variance) — map ships
  report-first; unmapped values log loudly instead of defaulting.
- **Young SHP lane** — S4 gated on ≥10 diffable symbols; if the lane
  never accrues, no machinery ships (the deferral discipline).
- **FP filters over-tightening** (dropping a true succession) — audit
  against the 5-row set plus corpus delta; filters are narrow
  sense-checks, not new triggers.

## 5. Non-goals

- Quote speakers stay string attributes — the D6-for-quotes deferral
  STANDS (`quote_capture_coverage` §7); this arc touches no quote
  surface.
- No DIN lane, no directorships/appointments (MCA bulk 403; no
  source). No new membership/director edge types — `invested_in`
  already carries person→company holdings.
- No person notes in the findata vault, no person embeddings, no
  person UI surfaces.

## 4. Implementation log (2026-09-25)

S1 and S2 are implemented in the current tree:

- Added `helpers/core/person_names.py` with person/HUF/trust classification,
  deterministic token-sorted keys, hard-class fuzzy resolution, and dedupe
  reporting. `apply_candidates` now resolves new person holders against
  existing person entities before inserting a stub; it never merges across
  person/HUF/trust classes.
- Added `classify_holder_category` to the SHP lane, mapping the 65 observed
  raw category spellings to person, company, institution, promoter,
  promoter_group, public, or artifact; artifact rows are skipped.
- Added seeded resolver and holder-category regression tests.

Live shakedown:

- Existing person entities: `30`; same-class dedupe groups: `0`.
- SHP holder rows: `333` across `65` raw categories; `20` artifact rows were
  excluded. Mapped classes: institution `183`, company `12`, person `61`,
  promoter `16`, promoter_group `32`, public `9`.
- S1/S2 focused tests, Ruff, and the live census pass. No entity merge or
  SHP holder-change event was applied.
- S3 added narrow acquisition-sense and mode-sense rejection plus a
  person-quarter dedupe key. The live ARM-2 audit moved from `5` to `3`
  `management_change` events; the surviving rows are Walmart and the two
  Infosys succession rows. Canonical apply removed `2` stale derived rows
  and retained `1,440` derived events.
- S3 focused tests and the live extract audit pass. S4 remains
  trigger-gated and unimplemented; only one symbol currently has at least
  two filings.

The operator accepted the no-perf/full-QA disposition for S1–S3. S4 remains
explicitly deferred until the measured trigger of at least 10 symbols with
repeat filings is met.

## Appendix — raw measurement log (2026-09-25, this box)

| Run | Probe | Result | Notes |
|---|---|---|---|
| 2026-09-25 | person census | 30, all SHP stubs | #267; HUF variant pair present |
| 2026-09-25 | invested_in edges | 30 person-sourced / 799 total | graph_edges join |
| 2026-09-25 | management_change audit | 5 rows; 2 FP, 1 dup | names cited in §1 |
| 2026-09-25 | shp_filings | 9 filings; 1 symbol ≥2 | memory/data/sources.duckdb |
| 2026-09-25 | shp_holders categories | 60+ spellings / 333 rows | incl. artifact rows |
| 2026-09-25 | person-class holders | 51 rows | broad ILIKE filter |
| 2026-09-25 | event type enum | frozenset, 4 members | static_checks.py:643 |
