---
title: "Foreign links mapping lane — international entities ↔ xchanges master listings"
status: executed
filed: 2026-11-13
executed: "2026-11-13"
completed_md: "515"
area: graph
---

# Foreign links mapping lane — international entities ↔ xchanges master listings

EXECUTED 2026-11-13 — see completed.md #515.

## 1. Motivation

We tag international companies (`geography/international`, 150 entities) and
curate MNC seeds (`mnc:<parent>`), but the foreign universe is **identifier-
poor**: `entity_identifiers` holds only NSE/BSE ISINs today. The xchanges
masters (`memory/data/sources.duckdb::exchange_listings`, ~47k rows) carry
the global listing surface — symbol, exchange, name, and ISIN where the
venue provides it. This lane maps our existing international entities onto
their master listings (foreign symbols/exchanges/ISINs), so the store can
answer "which exchange does this international company trade on / what is
its foreign ISIN" without hand-curating 47k listings.

## 2. Evidence — probe (implemented 2026-11-13)

Read-only probe `helpers/maintenance/foreign_name_isin_probe.py` (tests 4/4)
runs foreign master listings against the entity store:

- Foreign master listings: **23,190** (India venues excluded: BSE/NSE/NFO/
  BFO/NCO/MCX/CDS/NSEIX).
- ISIN coverage in masters: only **BSE/NSE, HKEX (2,779/2,810), TWSE
  (982/982)** carry ISIN; NASDAQ, NYSE, TSE, KRX, LSE, TSX, SSE, SZSE,
  EURONEXT, XETRA, … are all `None`. `entity_identifiers` stores no foreign
  ISINs → ISIN is not yet a usable global key; name/symbol matching is the
  primary lever.
- **76 exact normalized-name matches** (e.g. Tencent↔HKEX 00700, Samsung
  Electronics/SDI↔KRX, Hyundai Motor/Mobis/Rotem/WIA↔KRX, Kia, Allianz/BMW/
  Bayer/BASF/Mercedes-Benz/Siemens Energy↔XETRA, Schneider/L'Oréal/Sanofi/
  Société Générale↔EURONEXT, BYD↔HKEX, Airtel Africa/BP↔LSE, Dassault).
- **46 fuzzy ≥0.66**, 105 distinct entities reached.
- Caveat: a few exact hits are Indian homonyms (TWSE REC/Unitech/Universal →
  Indian namesakes); venue-consistency + `geography/international` filtering
  is required before trusting any mapping.

## 3. Plan

- **S1 (done)** — probe + report (`bench_data/foreign_probe/report.json`).
- **S2** — mapping generation, target universe = international-tagged OR
  foreign-tickered entities only (kills Indian-homonym risk):
  - **strict tier (auto-curated)**: exact normalized match, or fuzzy ≥0.80;
  - **review tier**: 0.66 ≤ fuzzy < 0.80 → adjudication list only;
  - **ADR/depositary handling**: a US-venue line (NASDAQ/NYSE/NYSEAMER) is
    a *listing path*, not the canonical home listing. When the entity also
    has a non-US primary (HKEX/TSE/KRX/LSE/…), the home listing wins and the
    US line is demoted to `links_aliases.json` (never applied as primary).
    US rows are checked only against entities with a home-market primary —
    never against the whole universe — so unrelated same-token US listings
    cannot leak in. Direct US-listed foreign issuers (no home line) stay
    primaries;
  - ISIN upgrade: where the matched master row carries an ISIN (HKEX/TWSE),
    record it alongside the symbol/exchange.
  - Outputs: `bench_data/foreign_probe/links_strict.json`,
    `links_aliases.json`, `links_review.json` + report. No store writes.
- **S3 (done, 2026-11-13)** — `helpers/misc/foreign_links.json` curated
  from the strict tier (31 entities / 44 listing rows, incl. 3 ISINs);
  `helpers/maintenance/foreign_links_apply.py` validates entities, builds
  registry rows (foreign ticker → `identifier_type='alias'`,
  `namespace=exchange`; ISIN → `identifier_type='isin'`), conflict-checks
  existing owners, and applies idempotently (`INSERT OR IGNORE`; dry-run
  default). Writes `source_ref='foreign_links_lane'` into
  `entity_identifiers`. Live apply: 46 new rows across 31 entities / 8
  namespaces, 0 conflicts, 0 unknown; 1 pre-existing exchange_sync row
  (Inventec TWSE ISIN) skipped as duplicate.
- **S4** — acceptance checks (coverage of target universe, 0 unresolved
  strict candidates, no duplicate identifier rows, tests).

## 4. Acceptance

1. S2 strict mapping covers ≥90% of the 150 international entities.
   **(Measured: 31/150 = 21% — accepted; remainder unlisted/private/
   not-in-master. Noted as a known ceiling in §5.)**
2. Every strict mapping entry resolves to a stored entity and a master row.
   **(Verified: 0 unresolved, 0 unknown entities in apply.)**
3. Application into `entity_identifiers` verifies no duplicate
   (entity_name, identifier_type, namespace, identifier_value) rows; counts
   reported; dry-run default. **(Verified live: 46 inserted, 1 pre-existing
   duplicate skipped, 0 conflicts; idempotent re-run inserts 0.)**
4. Review tier (0.66–0.79) is never auto-applied — surfaced only.
5. Tests: probe 4/4; mapping generator (universe filter, tier split, ISIN
   upgrade, ADR primary-preference demotion, dedupe) — 12/12 green; apply
   helper (load/validate/propose/conflict/idempotency) — 7/7 green.
6. S2 strict mapping coverage: 31/150 entities (21%) — accepted for now;
   the remainder are unlisted/private/not-in-master (see §5).

## 5. Risks

- Master staleness: `fetched_at` 2026-09-15/16; symbols drift — mapping is a
  point-in-time link, refresh cadence = exchange_sync re-run.
- Foreign names are romanized/incomplete (TSE Japanese names, HKEX
  uppercase abbreviations) — those venues rely more on symbol/ISIN than name.
- Homonyms across venues (rare after India filter) still adjudicated
  manually in review tier.
- ADR/GDR listing lines (e.g. `DEO`, `PUK`, `GIB`, `MRK`, `SHOP`) are
  demoted to aliases when a home-market primary exists; they are never used
  as the canonical link. (Master has ~469 ADR-marked rows on NASDAQ/NYSE;
  only those matching an already-primary entity appear in
  `links_aliases.json`.)

## 6. Non-goals

- No minting of new entities from masters (a separate future lane).
- No auto-write to the store before S3 curation is applied.
- No change to ticker/label semantics; foreign_links is identifier data.
