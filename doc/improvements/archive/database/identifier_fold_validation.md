---
title: "Bulk identifier fold — exchange ISIN/CIK into entity_identifiers + validation ladder"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "292"
area: "helpers/maintenance"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Bulk identifier fold — exchange ISIN/CIK into entity_identifiers + validation ladder

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/maintenance (new `fold_identifiers.py`),
helpers/misc (`database_integrity_check.py` check extensions),
maint chain + Makefile

Un-defers two rows of the 2026-09-15 endpoint evaluation
(`doc/local/evaluations/xchange_filings.md` §5, Pending): the
**CIN↔CIK/ISIN crosswalk fill** and the **validation extension** —
they are one arc, not two: the checker has nothing to check until a
bulk producer runs, and the bulk ISIN fold IS the crosswalk fill.
Closes Tier-2 item 5 of the 2026-09-25 pending-items survey
(`doc/local/notes/pending_items.md`).

## 1. TL;RA

The `entity_identifiers` registry is **empty — 0 rows** — while every
producer it was designed for already sits in the store: the
exchange-listings snapshot carries ~20k official ISINs, 6,063 entities
carry yfinance tickers, and the sec.gov CIK dump (10.4k rows) is a
verified endpoint. The validation side is further along than the
pending item implies — `check_identifiers`
(database_integrity_check.py:1423, WARNING-tier) already runs a
CIN-format/facet-drift/cross-check ladder plus registry dangling-refs
and window inversion. What does not exist: any bulk writer
(`backfill_identifiers.py` is manual `--set-id` only) and three
checker blocks (per-type format, window overlap, NULL hygiene).

Measured join yield (ticker suffix → exchange symbol match,
2026-09-25): **NSE 1,612 + BSE 4,161 = 5,773 direct entity→ISIN
matches** today, with 90 cross-suffix name hits and 100 unsuffixed
tickers left to the ambiguity guard. HKEX (2,779 ISINs) and TWSE (982)
rows exist for the non-India tail; CIK matches only existing
US-mentioned entities (US folds are store-only — no stubs).

## 2. Design decisions (operator-accepted 2026-09-25)

- **Stubs get identifiers.** The fold matches ANY existing entity by
  ticker, authored or stub. Rationale: stubs carry tickers as their
  seed identity, ISINs from official exchange listings are
  provenance-backed (regulator-tier source), and the D14 universe seed
  was designated the expected-entity cross-check set. The alternative —
  authored-only — strands the ticker-bearing stub population and
  defeats the cross-check.
- **Store-only.** The fold NEVER creates an entity; unmatched listings
  rows are logged, not seeded.
- **Uniqueness stays schema-owned.** The registry DDL already declares
  `UNIQUE (identifier_type, identifier_value)` (one value → one
  entity, globally) + PK `(entity_name, identifier_type,
  identifier_value)`. The checker does not re-check what the schema
  forbids; it adds format, overlap, and NULL hygiene instead.
- **No schema change, no version bump.** Rows only; the table,
  checker tier (WARNING), and snapshot export set are untouched.

## 3. Slices

1. **S1 — fold lane** (`helpers/maintenance/fold_identifiers.py`):
   read `exchange_listings` (sources.duckdb) ISINs by
   `(exchange, symbol)`; map entity tickers via suffix→exchange
   (`.NS`→NSE, `.BO`→BSE, …); direct-match within exchange; the 90
   cross-suffix + 100 unsuffixed cases resolve only when unique across
   exchanges, else logged-skip (ambiguity guard). Upsert
   `INSERT OR IGNORE` keyed on the registry PK → idempotent;
   `namespace` = exchange, `validity` NULL, `source_ref`
   `exchange_sync`. CIK sub-lane: same run over the sec.gov dump
   (`cik` type, namespace `sec.gov`) — store-only match, yield
   measured in shakedown. Dry-run default, `--apply` to write,
   `--check` reports drift.
2. **S2 — checker blocks** (extend `check_identifiers`): per-type
   format (ISIN ISO 6166 check digit; CIK digit-run ≤10; LEI
   length/charset; LLPIN per the cin.py conventions — implement only
   for types present), window overlap per `(entity_name,
   identifier_type)`, NULL-hygiene (empty `identifier_value`).
   Existing dangling/inversion blocks unchanged.
3. **S3 — wiring**: maint-full TIER2 step `fold-identifiers`
   (idempotent converger, after the sources-side steps) + a bare
   `make fold-identifiers` target mirroring the sync-tags pattern.
4. **S4 — shakedown record + archival** per the proposals checklist.

## 4. Acceptance criteria & shakedown

1. Dry-run report reproduces the measured projection (5,773 direct);
   after `--apply` the registry moves 0 → ≥5,700 rows; an immediate
   second run writes 0 new rows (idempotency, 3 consecutive runs).
2. Format checker: 0 malformed ISINs among folded rows; a unit test
   seeds a bad-check-digit ISIN and trips the WARNING.
3. Eval gate (`ontology_eval_gate.py` over the frozen question set)
   between dry-run and canonical apply: zero regressions — the
   registry feeds `/api/resolve`, a crosswalk surface, so the gate is
   mandatory even though only additive rows land.
4. `make qa` 11/11; snapshot round-trip covers `entity_identifiers`
   (existing tracked table — verify in the S4 record).

| Projected outcome | Today | After |
|---|---|---|
| entity_identifiers rows | 0 | ≥ 5,700 (ISIN) + measured CIK tail |
| checker blocks (format/overlap/NULL) | absent | 3, WARNING-tier |
| maint-full | — | +1 idempotent converger step |

## 5. Risks

- **Source-side check-digit failures** (a real ISIN failing ISO 6166
  on a quirky listing row) — first run is report-only for format
  findings; gate nothing until the false-alarm rate is measured.
- **Suffix drift** (`.BSE` legacy spellings, exchange renames) — the
  suffix→exchange map is data, easy to extend; unmatched tickers log.
- **Cross-exchange symbol reuse** (the 90 cross-suffix hits) —
  ambiguity guard skips non-unique cases; never guesses.
- **Delisted/stale ISINs** — validity stays NULL with namespace +
  source_ref provenance; window semantics arrive with a real
  validity-producing source (none exists — D-O3 confidence columns
  stay dropped).

## 6. Non-goals

- No `confidence` columns (deferred until a calibrated producer
  exists — unchanged).
- No MCA OGD lane, no LEI producer (no source), no US stub seeding
  (US folds stay store-only), no CIN sidecar changes, no entity
  creation, no schema/version bumps.

## 7. Implementation log (2026-09-25)

S1–S3 are implemented in the current tree:

- Added `fold_identifiers.py` with dry-run, `--apply`, and `--check` modes,
  exchange-suffix resolution, unsuffixed ambiguity guards, optional
  `--cik-json` support, and store-only `INSERT OR IGNORE` writes.
- Added `make fold-identifiers` and the maint-full step after tag
  convergence; the step is idempotent and does not create entities.
- Added registry format validation for ISIN check digits, CIK, LEI, and
  LLPIN, plus validity-window overlap and empty-value hygiene checks.
  Existing dangling-entity and window-inversion checks remain unchanged.
- Added focused fold and checker regression tests.

Shakedown against the live sources:

- Exchange source rows: `20,270`; projected direct candidates: `5,778`;
  ambiguous ticker rows: `0`; unmatched ticker rows: `299`; two values
  already owned by other entities were skipped rather than reassigned.
- Canonical apply added `5,776` rows; the next three applies wrote zero and
  `--check` reported no candidates.
- No local SEC CIK dump exists, so the optional CIK lane measured zero; no
  network fetch was attempted.
- The frozen ontology gate accepted `164/164` questions with zero
  regressions; database integrity, focused tests, static checks, Markdown
  lint, Ruff, ty, and search freshness pass.

The operator has accepted the no-perf/full-QA disposition for this arc. The
full QA run was `10/11` because the parallel timing-budget test exceeded its
3x allowance; its isolated rerun passed, and no performance test was changed.

## Appendix — raw measurement log (2026-09-25, this box)

| Run | Command/probe | Result | Notes |
|---|---|---|---|
| 2026-09-25 | registry count | 0 rows | `entity_identifiers` |
| 2026-09-25 | DDL inspect | PK + UNIQUE(type,value) | uniqueness schema-owned |
| 2026-09-25 | ticker census | 6,063 entities | 100 unsuffixed |
| 2026-09-25 | suffix→symbol join | NSE 1,612 / BSE 4,161 | +90 cross-suffix hits |
| 2026-09-25 | listings ISIN census | BSE 13,387 / NSE 3,122 / HKEX 2,779 / TWSE 982 | snapshots/parquet/sources |
| 2026-09-25 | CIK dump | 10.4k rows | sec.gov, verified endpoint |
| 2026-09-25 | CIN facets | 728 entities | maint `identifiers` converger |
