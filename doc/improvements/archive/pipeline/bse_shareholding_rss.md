---
title: "BSE shareholding-pattern RSS — second discovery stream + HTML parser"
status: executed
filed: "2026-09-22"
executed: "2026-09-22"
completed_md: "269"
area: "helpers/maintenance"
---

# BSE shareholding-pattern RSS — second discovery stream + HTML parser

## 1. TL;DR

The SHP lane currently discovers filings only through NSE's RSS. BSE
runs a mirror feed plus per-filing HTML pages with the COMPLETE pattern
— verified live 2026-09-22 — and the filing header carries the **NSE
Symbol cross-reference** natively. This proposal adds the BSE stream to
`shareholding_sync` as a second discovery source (dual-exchange
coverage: whichever exchange a company files through, we catch it),
folding into the same `shp_filings` / `shp_holders` store and the same
`invested_in` derivation. Scope is the incremental stream — the
historical backfill stays closed (no per-scrip history endpoint exists;
coverage completes with the quarterly filing wave).

## 2. Source — verified 2026-09-22 (A.1 probe)

- **RSS**: `https://www.bseindia.com/Data/XML/ShareholdingPattern_Feed.aspx`
  — open (browser UA + Referer lane, no bot wall),
  `application/rss+xml`, latest ~5 filings, same-day `lastBuildDate`.
  Item description carries `AS ON DATE`, `PR_AND_PRGRP`, `PUBLIC_VAL`,
  `STATUS` (New/Revised), submission/revision dates.
- **Per-filing page**: `/XBRLFILES/SHPXBRLDataXML/<scrip>_<ts>_SP.html`
  — server-rendered, ~600 KB, 5 tables:
  - t0 header: Scrip code, **NSE Symbol**, MSEI Symbol, ISIN, SME flag,
    report type, as-on date, regulation (Reg 31(1)(c));
  - t1 yes/no SHP flags;
  - t2 category summary (the aggregates, ~10 rows);
  - t3 **named-holder table** (~92 rows: Table II promoter section,
    sub-categories, holder rows with class-wise share and % columns);
  - t4 approved/encumbrance limits.
- No history: `.xml` variant 404s; per-scrip history API endpoints
  absent; the search page is SPA-gated. Latest-only stream — same
  incremental contract as NSE's RSS.

## 3. Slices

- S1 **Fetcher** — BSE RSS poll → per-filing HTML download
  (zstd-cached beside the NSE cache in `memory/data/shp_raw/`,
  `bse_<scrip>_<ts>.html.zst`), skip known filing ids.
- S2 **Parser** — the 5-table HTML: t0 header (symbol = NSE Symbol
  when present, else the scrip code; ISIN; as-on date; report type),
  t2 aggregates → `shp_holders` named=0 rows, t3 holder rows → named=1
  (name, category, shares, stake %, voting %). Column positions pinned
  from the header row; section-header rows skipped.
- S3 **Store + derive** — same `shp_filings` / `shp_holders` tables
  with `source='BSE'` provenance (column added, `NSE` default for
  existing rows); `build_candidates` / `apply_candidates` unchanged —
  the NSE-symbol cross-ref means company resolution needs no new
  machinery. Dedup across exchanges: a company filing on both lands
  once per filing id; the latest `period_end` wins per symbol.
- S4 **Tests** — fixture HTML (trimmed from the real Gandhi Special
  Tubes page), header/parser/store contract tests; dual-exchange dedup
  case.

## 4. Acceptance criteria

1. Live dry-run: BSE RSS items ingest with named holders extracted;
   symbol resolution rate reported; `--apply` lands edges, re-run is a
   no-op.
2. Targeted tests green; lint-audit/ty/ruff/md-lint/search-fresh clean.

## 4a. Execution addendum (2026-09-22 — implemented + applied)

`--bse-rss` in `shareholding_sync` + 4 tests (BSE RSS scalars, HTML
header/holders/store roundtrip, id-namespace). Live first run: 5 BSE
filings ingested (9 total with NSE), 44 fresh `invested_in` edges
applied, 29 new holder entities; one BSE-only listing correctly lands
as `NOTLISTED` → worklist. **Bonus finding**: the t3 named-holder table
carries a category tag column (col 35) — 21 promoter-tagged holders in
the first batch — the promoter-membership signal NSE's public XBRL
masks now flows in via BSE as a holder `category` property.

## 5. Non-goals

- No historical backfill (no endpoint; quarterly wave completes
  coverage on cadence).
- No BSE-only scrip → company stub creation; unresolved symbols go to
  the worklist like NSE's.
- No change to the NSE lane beyond the shared-store provenance column.

## 6. References

- `ownership_ingestion_nse_shp.md` (the lane this extends; execution
  addendum documents the masked-PAN/name-dedup reality).
- `data_sources.md` §BSE (Referer lane) + §VIGIL (the group-structure
  bulk that makes SHP enrichment, not foundation).
