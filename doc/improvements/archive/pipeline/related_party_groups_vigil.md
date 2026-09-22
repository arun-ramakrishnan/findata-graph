---
title: "Related-party group structure from the VIGIL bulk RPT dataset"
status: executed
filed: "2026-09-22"
executed: "2026-09-22"
completed_md: "268"
area: "helpers/maintenance"
---

# Related-party group structure from the VIGIL bulk RPT dataset

## 1. TL;DR

The "BSE corporate group repository" from the earlier research note does
not exist as such (BSE `GROUP` = trading segments A/B/X/T/Z — verified
against `ListofScripData`, 5,042 equity rows; company pages carry no
corporate-group row). The real equivalent is VIGIL's bulk related-party
dataset: LODR Reg 23 half-yearly XBRL RPT disclosures, republished as
`rpt_transactions` — **356,473 transactions across 744 filers**, free,
no auth, CC0 compilation license, one bulk CSV
(`api.tigzig.com/vigil/v1/download/rpt_transactions?format=csv.gz`,
9.0 MB). This proposal lands the lane: bulk download →
`sources.duckdb::rpt_transactions` → relationship classification →
`subsidiary_of` / `same_group` / `jv_with` edges + subsidiary entities.

## 2. Source — verified 2026-09-22

- API base `https://api.tigzig.com/vigil/v1` (found in the site bundle);
  named-query REST `POST vigil.tigzig.com/api/data {query, params}`
  (e.g. `rpt_section` `{"symbol": "TVSMOTOR"}` — 10-row display feed),
  bulk via `/download/{table}?format=csv.gz` + `/downloads/manifest`.
- Table: 356,473 rows; columns include `symbol`, `company_name`,
  `entity_name`, `counter_party`, `relationship` (free text),
  `rel_group` (their 12 standardized categories), `transaction_type`,
  amounts, PANs (masked), `period_end_date`, and `xbrl_url` pointing at
  the source filing on nsearchives (verification trail per row).
- `rel_group` distribution: Group Companies 244,955; KMP 33,087; Other
  23,079; Relatives 19,892; Promoter Group 12,748; Common Control 8,229;
  rest <4K each.
- Provenance stance: republished regulator data — edges land
  `source_tier='external'` with the `xbrl_url` carried in properties
  (unlike the direct-from-nsearchives SHP lane, which stays
  `'regulator'`). `source_ref` prefix `vigil:rpt:<symbol>`.

## 3. Classification (raw free-text relationship → edge class)

SQL CASE over the raw string, sized on the live table (distinct
filer-resolvable pairs; counter-parties resolved against the entity
universe via the house normalizer):

| class | rule (lowercased contains) | pairs | cp resolved | cp new |
|---|---|---|---|---|
| `subsidiary_of` (forward) | subsidiary / wholly owned | 22,107 | 474 | 21,521 |
| `subsidiary_of` (reverse) | holding company | 3,563 | 526 | 3,003 |
| `same_group` | fellow subsidiary / subsidiary of listed-or-ultimate / associate / common control / promoter group / Group Companies bucket | 24,377 | 1,167 | 23,026 |
| `jv_with` | joint venture | 1,680 | 70 | 1,559 |

~51.7K edges; ~49K counter-parties are real but unlisted (foreign
subsidiaries, holdcos) — the corporate tree the ownership arc wants.
Name hygiene: pure digits, sub-3-char, address-like strings rejected;
"formerly known as" parentheticals stripped for identity, kept in
properties.

## 4. Slices

- S1 **Fetcher** — bulk CSV download (zstd-cached under
  `memory/data/rpt_raw/`), typed load into
  `sources.duckdb::rpt_transactions` (raw strings; lazy casts).
- S2 **Classifier + pairs** — the SQL CASE above; `rpt_pairs` = distinct
  (filer symbol, counter_party, class) with latest period per pair.
- S3 **Derive** — filer resolved via exchange_listings (NSE+BSE) ×
  entities (the cross-ref the SHP lane already uses); counter-parties
  resolved against the entity universe; unresolved structural
  counter-parties created as company entities (`clean_name` for the
  CHECK-safe form, capped by `--max-new-entities`, default 60k).
  Edges: `subsidiary_of` directional (symmetric=0; reverse rule flips
  endpoints), `same_group` + `jv_with` symmetric=1, weight 1.0,
  `source_tier='external'`, properties {relationship, rel_group,
  txn_type, amount, period_end, xbrl_url}. Upsert-latest semantics
  (UNIQUE pair per edge type), worklist for unresolved filers.
- S4 **Tests** — classification direction cases, hygiene filter,
  resolution + entity creation, symmetric flags, dry-run parity,
  idempotent re-apply.

## 5. Acceptance criteria

1. Dry-run parity on the live db: pair counts by class match §3 within
   noise; apply lands edges + entities, second run is a no-op.
2. Spot-checks: TVS Motor's Singapore subsidiary resolves to a
   `subsidiary_of` edge; a fellow-subsidiary pair lands `same_group`.
3. Targeted tests green; lint-audit/ty/ruff/md-lint/search-fresh clean.

## 5a. Execution addendum (2026-09-22 — implemented + applied)

`helpers/maintenance/related_party_sync.py` + 8 tests
(`tests/test_related_party_sync.py`) + `make refresh-vigil` /
`make refresh-shp` targets. Three passes per run (`--group`,
`--supply-chain`, `--ratings`), all idempotent upserts; converged after
three waves (entity creation unlocks further pair resolutions each
pass — final dry-run delta zero).

Landed on the live store (edge counts before → after):

| edge type | before | after | notes |
|---|---|---|---|
| `subsidiary_of` | 68 | 11,348 | incl. reverse (holding-company) rule |
| `same_group` | 37 | 9,781 | fellow-subs / associates / promoter group |
| `jv_with` | 73 | 994 | |
| `supplier_to` | 8 | 15,533+ | two-way trading lands both directions; amounts in properties |
| `rated_by` | 3 | 216 | 6 rating-agency institution entities created |
| graph total | 18,291 | 56,014+ | companies 6,203 → 25,206 |

- Entity creation: 19,003 company entities from unresolved structural
  counter-parties (the corporate tree; `clean_name` + a CHECK-safe
  scrubber for mangled filings text — glued 'Solutionspvt',
  'Privated limited' tails survive clean_name and the entities CHECK is
  a substring LIKE, so unsafe displays are repaired or dropped before
  insert; 3 broken names found this way).
- Filer resolution: exchange_listings NSE+BSE × entities (the SHP-lane
  cross-ref); 116 filers unresolved → worklist
  (`memory/data/rpt_worklist.csv`).
- Supply-chain pass added beyond the original scope (same source,
  `transaction_type` sale/purchase rows → directional `supplier_to`
  edges with amounts) — the S4(b) supply-chain ladder item, landed
  early. Ratings pass likewise (`credit_ratings` 13,746 rows → latest
  rating per (company, agency) → `rated_by`).
- Pair counts run lower than the §3 sizing because `build_pairs` keeps
  the LATEST filing per (symbol, counter_party) and dedupes there; §3
  counted every filing.

## 6. Non-goals

- No KMP/Relatives/Trust/Employee-Benefit edges (transactional people
  data — the SHP lane already carries person holders with stakes).
- No transaction-type edges (sales/purchases → supplier_to/customer_of
  is a natural follow-on once group structure lands; amounts are kept
  in properties meanwhile).
- No credit-ratings/pledges/insider tables this arc (separate filings;
  `rated_by` is the obvious next).
- No scheduling — operator-run command like the other lanes.

## 7. References

- `ownership_ingestion_nse_shp.md` (S1 SHP lane; the
  `_resolve_companies` / `_normalize` machinery reused here).
- `data_sources.md` §BSE (trading-group falsification),
  §Shareholding (the archives lane).
- VIGIL docs: vigil.tigzig.com/data-api (API catalog
  api.tigzig.com/vigil/v1/docs; terms CC0 for the compilation).
