---
title: "Ownership edges from NSE shareholding-pattern XBRL"
status: executed
filed: "2026-09-22"
executed: "2026-09-22"
completed_md: "267"
area: "helpers/graph"
---

# Ownership edges from NSE shareholding-pattern XBRL

## 1. TL;DR

The graph has no promoter-ownership edges and its corporate-structure
tail is thin (`subsidiary_of` 68, `same_group` 37, `acquired` 42 across
6,203 companies), with `valid_from` missing on every edge family except
`invested_in`. NSE's shareholding-pattern filings — verified end to end
2026-09-22 — provide named holders with PAN identity, stake percentages,
and native filing periods on an open archives host. This proposal lands
the fetch → parse → materialize pipeline for `promoter_of` ownership
edges (weight = stake %) with real temporal validity, feeding the
economic projection in `pagerank_graph_enhancements.md` S1.

## 2. Sources — verified 2026-09-22

- **Discovery**: `nsearchives.nseindia.com/content/RSS/
  Shareholding_Pattern.xml` — open RSS (200 application/xml, no bot
  wall), same-day items. Each item: company name, XBRL link, and
  scalars `PR_AND_PRGRP` (promoter & promoter group %), `PUBLIC_VAL`,
  `EMPTR`, `AS ON DATE`, `SUBMISSION_DT`, `REVISION_DT`.
- **Data**: `nsearchives.nseindia.com/corporate/xbrl/
  SHP_<company>_<ts>_WEB.xml` — joint BSE+NSE `in-bse-shp` XBRL
  taxonomy (SHP V1.2, 2026-08-31). Sample parsed (463 KB,
  SHP_1726099…): 119 holder rows, each with `NameOfTheShareholder`,
  `PermanentAccountNumberOfShareholder` (PAN — durable holder
  identity), share counts, `ShareholdingAsAPercentageOfTotalNumberOf
  Shares`, voting-rights %, pledge/encumbrance counts, category
  contexts with period start/end.
- **Access**: archives host serves openly; main-site pages need a
  browser-like session (root 403s plain clients) — the pipeline never
  touches them. Filing cadence: quarterly + event-driven revisions
  (`NDS_REVISED_STATUS`, `REVISION_DT` — revised filings supersede).
- **BSE** corporate group repository (reachable; exact endpoint pinned
  at execution) as a later supplement for explicit same_group lists.

## 3. Slices

- S1 **Fetcher** — RSS poll → per-filing XBRL download from the
  archives host (browser-like UA, low frequency; RSS is the incremental
  index). Raw XML cached content-addressed by filing id; provenance
  stamp per fetch (source URL, UTC timestamp, tool+version) per house
  provenance pattern. No logins, no main-site scraping.
- S2 **Parser** — `in-bse-shp` XBRL → normalized holder rows (holder
  name, PAN, category, shares, stake %, voting %, pledged shares,
  period start/end, filing/revision dates). Validation: category
  percentages sum to ~100 per filing; revised filings supersede the
  prior one; company keyed by the XBRL Symbol identifier.
- S3 **Graph materialization** — holder nodes keyed by PAN (person vs
  entity from category; entity holders reconciled to existing company
  nodes by PAN/symbol match so ownership lands between real nodes);
  new `promoter_of` edge type (holder → company, weight = stake %,
  valid_from/valid_to from filing period — the second edge family with
  temporal validity after invested_in); `same_group` derivation for
  companies sharing a promoter-group holder ≥ threshold. New edge
  types added to the ontology doc rosters (static_checks guards this).
- S4 **Consumer wiring** — economic projection (pagerank enhancements
  S1) includes `promoter_of`; as-of pagerank (S3 there) inherits
  native periods. Coverage metric added to the integrity report
  (companies with current-quarter promoter ownership, universe-joined).
- Tests: trimmed fixture from the real sample into `tests/data/`
  (hermetic, no network); parser contract tests (categories sum, PAN
  normalization, revision supersede); materialization tests
  (weight/period/rounding); provenance stamp assertions.

## 4. Acceptance criteria

1. ≥95% of NSE-listed companies in the repo universe carry
   current-quarter promoter ownership edges with valid_from filled.
2. Fixture tests green offline; one live-fetch integration test
   marked/mocked per hermeticity rules (`_no_local_embedder` style).
3. `same_group` derived for ≥10 groups from shared promoter-group
   holders, spot-checked against a known group.
4. `make static-checks` (incl. ontology rosters) + `make secret-scan`
   clean.

## 5. Non-goals

- No beneficial-ownership beyond filing categories; no investor-level
  analytics beyond the graph.
- No BSE-only listings (NSE covers the repo universe); BSE group
  repository is a follow-on supplement.
- No live/streaming ingestion — quarterly + revision cadence.
- No changes to pagerank itself here (consumer side lives in
  `pagerank_graph_enhancements.md`).

## 6. Execution addendum (2026-09-22 — implemented)

Executed as `helpers/maintenance/shareholding_sync.py` + tests
(`tests/test_shareholding_sync.py`, 8 tests) + `person` kind wiring in
`query.py` (`v_node` entity_type list + the `invested_in` mixed-kind
JOIN) + the `entity_types` roster in `doc/design/ontology.md` and the
stability-register entry in `doc/design/data_sources.md`.

- **Data-shape discovery**: the PUBLIC feed masks
  `PermanentAccountNumberOfShareholder` and
  `TypeOfPromoterShareholding` (every row `******`, verified across
  three filings) — named holders are NOT flagged as promoter members
  and promoter ownership arrives aggregated only. PAN-keyed identity is
  therefore unavailable; holder identity is name-based with
  case-insensitive normalized-name dedup.
- **Landed semantics** (replaces the `promoter_of` plan): named holders
  with exact stakes + native periods → `invested_in` edges
  (weight = stake percent, `source_tier='regulator'`,
  `source_ref=nse:shp:<filing_id>`); holder kinds person / institution
  / reconciled-company (e.g. 'TSF INVESTMENTS LIMITED' → the existing
  'TSF Investments' entity); `same_group` pairs derived from shared
  corporate holders ≥ threshold (reconciliation-based — body-corporate
  holders file under public categories, so the XBRL category axis is
  not a company-ness signal). `graph_edges` holds the latest interval
  per pair (UNIQUE constraint); period history lives in
  `shp_filings`/`shp_holders`.
- **Live state after first apply**: 4 filings (RSS window), 38 holder
  entities (30 person — first person-kind entities in the store), 40
  `invested_in` edges; top holdings TSF Investments 27.81% → Wheels
  India, T.S. Santhanam family 27.42%, Gandhi family → Gandhi Special
  Tubes, Punglia family → VPRPL. Units normalized to percent (XBRL
  stores fractions; root category = 1.0).
- **Deferred — bulk backfill**: coverage is 3/8508 NSE symbols (0.04%,
  RSS window only). The ≥95% universe acceptance needs a bulk
  filings-search lane; the main-site search is bot-walled. `--xml`
  ingests known filing URLs directly in the meantime; quarterly RSS
  polling accumulates ~full coverage over a quarter.

## 7. References

- `pagerank_graph_enhancements.md` S1/S3/S4 — the consumer; census
  behind the ownership-gap claim.
- pending.md N5-5/N5-6 — brute-KNN and pagerank-adjacent verdicts.
- `helpers/core/local_embedder.py` provenance pattern; ontology doc
  rosters (static_checks).
