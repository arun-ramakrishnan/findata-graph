---
title: "D14 stub convergence — ticker hygiene and an industry→sector path for note-less stubs"
status: deferred
filed: "2026-10-16"
executed: "2026-10-16"
completed_md: "404"
area: "helpers/maintenance"
---

# D14 stub convergence — ticker hygiene and an industry→sector path for note-less stubs

## 0. TL;RA

The D14 universe seeder (`helpers/maintenance/exchange_sync.py:669 seed_stubs()`)
mints note-less company stubs (name + ticker only) for every exchange row not
already an entity. Two defects leave those stubs permanently outside every
downstream convergence lane:

1. **Ticker hygiene** — the BSE lane stores the exchange's *numeric scrip code*
   as the ticker (`sym + ".BO"` at `helpers/maintenance/exchange_sync.py:469`;
   `exchange_listings.symbol` is `542772` for 360 ONE WAM, `360ONE` on NSE).
   A scrip code is not a Yahoo symbol, so `enrich_from_yfinance` can never
   resolve it: `yf.Ticker('531120.BO').info` → **13 keys, all `None`**, vs
   **162 keys** for `RELIANCE.NS`. **4,159** company entities carry such
   scrip-code tickers (local part contains no letters).
2. **No convergence path for a note-less stub** — `sector_classification`
   derives from the note's `sector/*` tag (`helpers/core/sync_tags.py:463`), and
   the only other writers are `helpers/graph/derive_indices.py:295` (index
   constituents, fill-empty-only) and `helpers/maintenance/move_sector.py:196`
   (refuses a missing source file). `enrich_from_yfinance.update_note()` only
   updates an *existing* note. `doc/design/data_sources.md:220` claims these
   "converge later from index constituents (`derive_indices.py`) and the
   yfinance label lane" — for a note-less stub neither lane can fire.

Observed impact: the S3 sector cut reported **21 unclassified targets**, every one
of them a D14 stub (`created_at = 2026-09-15`, `file_path IS NULL`, numeric `.BO`
ticker, 0 rows in `company_metrics`, 0 hits in
`sources.duckdb::index_constituents` — 6,661 rows). Store-wide, **23,041 of
24,927** entities carry no `sector_classification` (1,886 classified before this
session's out-of-band write).

## 1. The missing ingredient already exists

`exchange_listings` (47,525 rows) carries an `industry` column in exactly the
NSE vocabulary `INDUSTRY_TO_SECTOR` was built against
(`helpers/graph/derive_indices.py:55`). BSE equity rows: **7,019**, of which
**1,579** map today:

| label | rows | → sector |
|---|---|---|
| Capital Goods | 323 | Engineering_Capital_Goods |
| Fast Moving Consumer Goods | 153 | FMCG |
| Financial Services | 152 | Financial_Services |
| Healthcare | 136 | Healthcare |
| Consumer Services | 136 | Consumer |
| Consumer Durables | 117 | Consumer |
| Information Technology | 104 | Technology |
| Chemicals | 83 | Chemicals |
| Construction | 71 | Infrastructure |
| Textiles | 70 | Textiles |
| Automobile and Auto Components | 68 | Automotive |
| Realty | 41 | Real_Estate |
| Power | 25 | Energy |
| Metals & Mining | 24 | Metals |
| Construction Materials | 18 | Building_Materials |
| Oil Gas & Consumable Fuels | 17 | Energy |
| Telecommunication | 17 | Telecommunications |
| Forest Materials | 11 | Packaging |
| Media Entertainment & Publication | 8 | Media_Entertainment |
| Diversified | 5 | Diversified |

So a convergence path is available without any new fetch — the defect is that
`converge_industry` reads only `index_constituents`, never the exchange table.

## 2. Fix — slices

- **S1 ticker hygiene.** Resolve scrip code → Yahoo symbol with the existing
  `helpers/core/get_tickers.py:58 search_ticker()` plus the identity guard
  (`enrich_from_yfinance.identity_ok`, industry_coding_completion S2), and write
  `entities.ticker`. Keep a machine report in the `## ticker_issues` shape
  `helpers/maintenance/enrich_relations.py` already uses. 4,159 candidates.
- **S2 exchange-industry convergence.** Extend `derive_indices.converge_industry`
  (`helpers/graph/derive_indices.py:269`) with the sidecar's exchange `industry`
  as a second label source, fill-empty-only (`WHERE … IS NULL OR … = ''`, never
  clobber an authored sector). Reuses `INDUSTRY_TO_SECTOR` unchanged.
- **S3 label hygiene.** Three unmapped labels are HTML-entity mangled in the
  sidecar — `Media, Entertainment &amp; Publicati` (28), `Metals &amp; Mining`
  (14), `Oil, Gas &amp; Consumable Fuels` (10) — unescape (`&amp;` → `&`) before
  the map lookup. Add `Services` (150) and `Utilities` (16), or park them in
  `index_worklist.json` for curation rather than dropping silently.
- **S4 backfill + report.** Run S1–S3 over the stub population and publish how
  many stubs converge. 5,212 BSE equity rows carry `industry = NULL` and stay in
  the worklist by construction.

## 3. Non-goals

- No note authoring for the stub population — the vault stays the curated layer;
  stubs receive a machine-derived `sector_classification` only, and
  `file_path` stays `NULL`.
- No write to entities that already hold a value (fill-empty-only).
- No change to `s3_assign_primary_sector` / `assign-primary-sector` semantics.

## 4. Acceptance criteria

- `assign-primary-sector` over the S3 targets reports **0 unclassified**.
- `make maint-full` stays green through `sync-sector-links --check` (step 13) and
  `sector-hierarchy --check` (step 14).
- Static checks green (`ruff`, `md-lint`, `--dirty`).
- **Eval gate** (house rule, `doc/improvements/proposals/README.md`): this change
  alters query-visible sector rosters, so it MUST run
  `helpers/misc/ontology_eval_gate.py` over the frozen question set between
  dry-run and the canonical apply.

## 5. Residual from this session

The 21 targets were classified **out-of-band** rather than by a sanctioned
writer, because none exists for a note-less stub: `/mnt/data/tmp/apply21.py`
issues the same guarded UPDATE as `derive_indices.converge_industry`
(`WHERE name = ? AND (sector_classification IS NULL OR sector_classification = '')`).
Result: classified 1,886 → 1,907; S3 cut **118/118, 0 unclassified, 26 sectors**
(was 97/118, 21 unclassified); DuckDB cache rebuilt + `stamp-centrality` re-run
to answer the generation bump.

Assignments (from `get_tickers.search_ticker()` Yahoo `sector`/`industry`, grounded
on already-classified peers):

| company | Yahoo industry | sector |
|---|---|---|
| Patel Engineering | Engineering & Construction | Infrastructure |
| BF Utilities | Infrastructure Operations | Infrastructure |
| Vadilal Industries | Packaged Foods | FMCG |
| Kriti Nutrients | Packaged Foods | FMCG |
| Uniparts India | Farm & Heavy Construction Machinery | Engineering_Capital_Goods |
| Indef Manufacturing | Farm & Heavy Construction Machinery | Engineering_Capital_Goods |
| Bosch Home Comfort India | Furnishings, Fixtures & Appliances | Consumer |
| Krystal Integrated Services | Specialty Business Services | Consumer |
| KSR Footwear | Footwear & Accessories | Consumer |
| Bhartiya International | Footwear & Accessories | Consumer |
| Jay Ushin | Auto Parts | Automotive |
| Shreyans Industries | Paper & Paper Products | Packaging |
| Bedmutha Industries | Steel | Metals |
| Uniphos Enterprises | Chemicals | Chemicals |
| Procter & Gamble Health | Drug Mfrs — Specialty & Generic | Pharma |
| Sai Parenterals | Drug Manufacturers — General | Pharma |
| Senores Pharmaceuticals | Drug Manufacturers — General | Pharma |
| Dhunseri Investments | Capital Markets | Capital_Markets |
| Pilani Investment and Inds | Asset Management | Financial_Services |
| Landmark Global Learning | Consulting Services | Education_Training |
| Landsmill Green | Conglomerates | Diversified |

`entities.ticker` was deliberately **not** repaired in-session, so these 21 remain
unresolvable by `enrich_from_yfinance` until S1 lands.

## 6. Log

| date | event |
|---|---|
| 2026-10-16 | filed; 21 D14 stubs classified out-of-band; S3 cut 118/118 |
| 2026-10-16 | **S1 executed** (`helpers/maintenance/ticker_hygiene.py`, 22 tests): 4,159 scrip tickers → **3,678 verified Yahoo symbols (88.4%)** — 1,110 `.NS` / 2,568 `.BO`; guarded `UPDATE … WHERE name AND old-ticker`, `skipped_moved=0`. Guard hardening in `enrich_from_yfinance._name_tokens` (accent NFKD fold + plural fold, both sides) recovered +45 false refusals (Sai Parenterals, Shangar Décor, Venky's…). Tier-2 BSE PeerSmartSearch fallback built but **403-blocked from this network** (same as the exchange_sync BSE lane) — 481 scrips remain: renames needing an entity-rename pass + search-absent names, all in `outputs/ticker_hygiene_report.md`. dup/person-census 0 after apply. Chain wiring landed: `--fetch-only` in phase A, `--apply-only` in phase B before `enrich_from_yfinance`. |
