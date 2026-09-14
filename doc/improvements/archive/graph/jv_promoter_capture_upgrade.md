---
title: "JV venture + promoter-group capture upgrade — name-before-marker patterns + cross-note group accumulation"
status: executed
filed: "2026-09-15"
executed: "2026-09-16"
completed_md: "237"
area: "helpers/graph/extract_relations.py (venture patterns, batch group pass, props-converge), tests, doc/design/db_schema.md — capture arc, no schema change"
---

# JV venture + promoter-group capture upgrade — name-before-marker
patterns + cross-note group accumulation

**Date:** 2026-09-15 · **Status:** EXECUTED ·
**Area:** `helpers/graph/extract_relations.py`, `tests/`, doc — D5+D6 of
the hyper_lane_wiring §5 backlog (one extract_relations arc per the
agreed working order; the regroup keys `$.venture`/`$.group` already
landed in derive_hyperedges S10/S11 — this arc feeds them).

## 1. Problem (measured 2026-09-15, read-only)

The hyperedge regroup keys exist but capture starves them:

- **D5 — venture names: 2/69** `jv_with` rows carry
  `properties.venture`, both Phase-2 seeds. The S10 hook
  (`capture_venture_name`, 3 conservative patterns) exists but the
  measured corpus yield is 2 — the dominant prose shape puts the
  venture name BEFORE the marker, which no pattern covers:
  - "AllyGram **JV with** Grammer AG" (via X JV with Y)
  - "VE Commercial Vehicles (VECV), **a joint venture with** the Volvo
    Group" (name + abbr before the marker)
  - "ASHVINI, **out joint venture with** NPCIL" (name + OCR-typo
    article before the marker)
- **D6 — promoter groups: 1 seed group** (3 Muthoot rows, "Phase 2
  seed"). Five group regexes (`GROUP_RES`) + `_derive_same_group` exist
  and match 27 times over The_Chatter (16 distinct groups), but
  derivation is **per file**: `group_to_companies` is built inside each
  scan and discarded. A group mentioned in several company notes — the
  common case ("a Tata Group company" across notes) — never reaches
  ≥2 members in ONE file, so no edge is ever derived.

The self-venture shape ("ICICI Prudential AMC, a joint venture between
ICICI Bank and Prudential") names no venture — out of scope (guessing
violates the honest-capture doctrine).

## 2. Design

- **E1 name-before-marker venture patterns** (2 families appended to
  `_VENTURE_PATTERNS`, order after the existing three):
  1. `X JV with` — `AllyGram JV with Grammer AG`, `(via AllyGram JV
     with Y)`.
  2. `X (ABBR), a|an|our|the|its|out joint venture with` — covers the
     VECV shape and the measured "out" OCR-typo article; the optional
     `(ABBR)` group is skipped, the leading name captured.
  Still conservative: capitalized-name tokens only, length-capped,
  first hit wins; measured yield is reported honestly, never padded.
- **E2 cross-note group accumulation**: the per-file scan returns its
  `group_to_companies` map (company-note path included — it already
  populates the map, then discards it); the CLI accumulates across all
  files and derives `same_group` edges ONCE after the reduce loop.
  Per-file (per-edition) derivation stays — its edges carry edition
  provenance; the batch pass adds cross-note edges with
  `properties.found_in` (contributing-note count) and
  `source_ref: derive:relations:cross_note`. Existing-triple dedup
  prevents double-insert when both passes see the same pair.
- **E3 props-converge + live apply**: `apply_edges` skips existing
  triples, so enrichment of the 67 live venture-less rows needs an
  explicit converge: after the reduce loop, rows whose re-extraction
  yields `venture` while the stored JSON lacks it get a merge-UPDATE
  (missing keys only — never overwrite; S5 precedent). Then the
  standard `--apply` corpus pass + `make derive-hyperedges` regroup;
  jv/same_group hyperedge counts measured before/after.
- **E4 tests + docs**: unit tests for both new venture families (the
  three live shapes above), batch group accumulation (company-note maps
  merge; ≥2-member derivation; dedup vs per-edition edges), the
  converge-UPDATE (adds missing venture, never overwrites); db_schema.md
  `jv_with`/`same_group` property notes.

## 3. Acceptance

- Venture yield honestly reported (measured before/after on the live
  69); every captured name is prose-stated — no inference.
- Cross-note groups produce real `same_group` edges (target: the Tata /
  Aditya Birla shapes measured 6/5 editions); re-run idempotent
  (existing triples skipped; converge is a no-op second run).
- `jv`/`group` hyperedges grow in derive_hyperedges; store guard clean.
- Gates: targeted per slice; full qa/advisory parked to arc end with
  the operator's go.

## 4. Non-goals

- Self-venture inference (no prose-stated name), venture abbreviations
  as separate properties, stake/ownership parsing.
- Group→company resolution beyond section-company membership; group
  entities as first-class rows (groups stay string properties, the
  regroup handles identity).
- The Muthoot seed rows (operator-curated; untouched).
- extract_relations prose-shape changes beyond the two families.

## 5b. Execution Results

- **E1 EXECUTED (2026-09-15)** — two name-before-marker families in
  `_VENTURE_PATTERNS` (`X JV with` incl. the "(via …)" form;
  `X (ABBR), a|an|our|the|its|out joint venture with|between`) +
  `_VENTURE_NAME_STOPWORDS` post-filter (articles for empty name slots;
  "India" for the measured self-venture FP). Live yield over stored
  quotes: 4 newly extractable.
- **E2 EXECUTED (2026-09-15)** — `extract_relations(return_groups=True)`
  (3-tuple opt-in; default arity pinned); `_extract_batch` threads the
  per-file group map (company notes included — previously built then
  discarded); `_derive_cross_note_groups` derives pairs from the CLI's
  cross-file accumulation with `found_in` note counts +
  `source_ref: derive:relations:cross_note`.
- **E3 EXECUTED (2026-09-15, live, pre-backed-up)** — full-corpus
  `--apply` over `findata` (1,348 files incl. 1,165 company notes):
  - **same_group 3 → 34**: 31 cross-note pairs, all audited clean
    (Tata 8 members, Aditya Birla, Hinduja, Bajaj, RP Sanjiv Goenka,
    TVS, Welspun, Muthoot-Pappachan prose pair deduped against the
    seed edge — no duplicate group).
  - **venture 2 → 6/81** via the props-converge UPDATE (missing-key
    merge only; extracted props first, the row's own stored quote as
    fallback — one VECV row converged only from its stored quote
    because the note was rewritten after capture). New ventures: VE
    Commercial Vehicles, Titan Watches, AllyGram, ASHVINI.
  - **Hyperedge regroup** (`make derive-hyperedges`): `group`
    hyperedges 1 → 8, `jv` 2 → 6; store 480/5,688. Second run 0/0 —
    idempotent; third extraction pass applied=0.
  - **Honest side effect, flagged for the operator**: scanning company
    notes also extracted 48 real non-D5/D6 edges from company prose
    (acquired +11, jv_with +12, regulated_by +12, approved_by +7,
    rated_by +3, supplier_to +2, subsidiary_of +1) — all
    provenance-tagged (`doc_type: company`), FK-safe, deduped.
- **E4 EXECUTED (2026-09-15)** — 8 new tests
  (`TestVentureNameBeforeMarker` 4 shapes + 2 FP rejects,
  `TestReturnGroups` 2, `TestCrossNoteGroups` 2); extraction suite
  50/50; the five relations suites + hyper wiring 188/188 green;
  graph_design.md edge table refreshed (counts + the two capture
  notes).

## 5c. Triage of the company-prose side edges (operator-requested)

- **Root cause of the mis-attributed set**: `findata/Super_Sectors/Quotes.md`
  (the 7.4MB derived catch-all, `type: super_sector`) classified as a
  **newsletter** — `_detect_doc_type` only knew company/sector — and its
  multi-company quote blocks split into sections whose headings resolved
  to stub entities (Harley-Davidson ×19, Adani Green ×6, Hyundai ×2,
  Amazon/General Atomics/Reliance/Standard Chartered). All 31 such edges
  carried `edition: "Quotes"` — the discriminator. Zero pre-existed.
- **Fix**: `super_sector` now classifies as `sector` → skipped
  (`TestSuperSectorNotesSkipped`, 2 tests). Re-run over `findata`:
  extracted 312 (was 343), applied=0, 0 Quotes-edition edges — the fix
  holds.
- **Deleted 31** (backup: `/tmp/research_pre_d56.db` + the delete is
  scoped by `source_ref='derive:relations:The_Chatter' AND
  edition='Quotes'`): acquired 11, jv_with 12, subsidiary_of 1,
  supplier_to 2, regulated_by 1, approved_by 2, rated_by 1. None fed a
  hyperedge (regroup after delete: 0/0; store stays 480/5,688).
- **Kept 17 real company-note edges** (all `source_ref=
  company_note:<company>`, correct own-note attribution):
  regulated_by ×11 (banks/NBFC/Pine Labs/CRISIL → RBI),
  approved_by ×5 (AU SFB + IRCTC + LVB → RBI; HDFC AMC + Torrent →
  SEBI), rated_by ×1 (Motilal Oswal → CRISIL).
- **Post-triage state**: jv_with 69 (ventures 6/69 — none of the
  deleted carried venture), same_group 34 (all 31 cross-note pairs
  unaffected — they came from real company notes via the
  normalized_name override, correct attribution).
