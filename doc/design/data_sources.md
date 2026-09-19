# Data Sources — External Endpoint Register

**Status:** LIVE reference (D12/D14 execution, 2026-09-15). Every external
data endpoint this repo pulls, with auth quirks, verified shapes, and the
sidecar files that cache the results. Re-discovery is expensive — this
register exists so the blind search never repeats. Companion filings:
D12 (CIN/NIC) and D14 (universe seed) in
`doc/improvements/archive/graph/hyper_lane_wiring.md` §5.

## Conventions

- **Final data lives in `memory/data/*.parquet`** (zstd), git-managed,
  DuckDB-queried: `SELECT … FROM read_parquet('memory/<file>.parquet')`.
  Arrow (pyarrow) for ETL, no pandas middleman (bulk_data_lanes rule).
- **Personal keys in `memory/.env`** (gitignored contents, file
  tracked): `GOV_API_KEY` for data.gov.in — strip surrounding quotes
  when reading.
- CINs are immutable ⇒ identifier sidecars are append-only; exchange
  master lists are re-pullable snapshots.

## OGD India / MCA company master (D12)

| What | Value |
|---|---|
| Catalog (singular `/catalog`; `/catalogs/<uuid>` 404s) | `https://api.data.gov.in/catalog/ec58dab7-d891-4abb-936e-d5d274a6ce9b?api-key=…` |
| Resource lookup | `https://api.data.gov.in/resource/ec58dab7-d891-4abb-936e-d5d274a6ce9b?api-key=…&format=json&limit=10&filters[company_name]=<NAME>` |
| Fields | CIN, name, status, class, category, capitals, state, RoC, PBA, address, sub-category |
| Auth | personal key (`GOV_API_KEY`); the public demo key is 429-saturated |
| Limits | `q=` unsupported; `filters[]` is EXACT match; offset capped ~10k (Elasticsearch result window) ⇒ no bulk pagination — per-name lookup only |
| PBA caveat | `principal_business_activity` is NA throughout; NIC comes only from the CIN, and 88% of CINs are pre-2008 ⇒ legacy NIC codes (cin.py warns) |
| MCA portal | hard 403 (Akamai) — the master-data zip is NOT bulk-downloadable |
| Dead ends | `/catalogs?q=`, `/meta/*`, backend SPA routes — all 404/empty |

Sidecar: `memory/data/mca_cin.parquet` via
`helpers/maintenance/mca_cin_sync.py` (`build` / `apply` / `resolve`).
Manual curation rows: `memory/data/mca_cin_manual.csv` (wins on merge).
Statutory corporations (SBI, NABARD, …) are absent from the master by
design — not Companies-Act companies.

## NSE (static CSVs on nsearchives — the reliable lane)

Needs browser `User-Agent` + `Referer: https://www.nseindia.com/`.

| File | Rows | Notes |
|---|---|---|
| `nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | ~2.6k | main board, slim — NO industry; header fields carry leading spaces |
| `nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` | 500 | Company Name, **Industry**, Symbol, ISIN |
| `nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv` | ~750 | same columns (underscore before `list` — take the href from the index page) |
| `nsearchives.nseindia.com/content/indices/ind_niftysmelist.csv` | 529 | SME EMERGE index, Series `SM` |

`www.nseindia.com/api/*` routes need cookie handshakes and mostly 404
(`/api/live-analysis-sme-scrips` died even with a warmed session) — avoid.
Full Emerge board list: NOT pinned (EQUITY_SME.csv 404s); SME coverage
rides the EMERGE index list until pinned.

## NSE Indices (niftyindices.com — per-index constituent CSVs)

`helpers/maintenance/index_sync.py` (`make refresh-indices`) folds NSE
Indices equity-index constituents into
`sources.duckdb::index_constituents` (append-only,
`PRIMARY KEY (index_name, symbol, as_of)`; `as_of` = CSV `Last-Modified`,
the semi-annual reconstitution vintage). Wave 1 = broad-based + sectoral:
detail pages are discovered from
`niftyindices.com/indices/equity/{broad-based,sectoral}-indices` and each
is scraped for its `IndexConstituent/<stem>.csv` href (stems are
inconsistent — never guessed). Columns: `Company Name, Industry, Symbol,
Series, ISIN Code`. The three nsearchives feeds above are the no-scrape
lane for their indices (`index_sync.py` skips the discovered duplicate).
Consumed by `helpers/graph/derive_indices.py` → `index` entities +
`listed_on_index` edges (`doc/improvements/archive/graph/index_membership_fill.md`).

## BSE (api.bseindia.com needs `Referer: https://www.bseindia.com/`)

| What | Value |
|---|---|
| Full instrument master | `https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Industry=&Segment=Equity&status=Active&Pageno=1&pagesize=5000&strSearch=` — JSON |
| Shape | 12,855 Active rows: equity 5.2k + debentures/bonds 5.4k + CP 868 + MF 258 + pref; `Segment` param is IGNORED (returns all segments — filter client-side); `INDUSTRY` field is null |
| SME board | NOT in this API (`Group=SME` → 0 rows; `MT` group 128 = migrated-to-main) |
| Official SME list | `https://www.bsesme.com/corpoaratefilings/ScripsList.aspx?expandable=0` — SSR HTML table, 582 scrips: code \| ticker \| name \| status \| group \| face value \| ISIN \| **industry** (the `corpoaratefilings` typo in the path is real) |
| Dead | `bseindia.com/sensex/IndicesWatch_Weight.aspx?iname=SMEIPO` → redirects to the SPA homepage |

## Zerodha / Kite (cross-check + optional candles)

| What | Value |
|---|---|
| Public instruments dump (no auth) | `https://api.kite.trade/instruments` — daily CSV, ~112k rows |
| Columns | instrument_token, exchange_token, tradingsymbol, name, tick/lot, instrument_type, segment, exchange — **no ISIN** |
| Coverage | NSE + BSE cash equity (~23k EQ rows) AND SME boards (NSE Emerge + BSE SME symbols verified present) + full F&O/CDS/MCX complex |
| Kite Connect v3 (docs: `kite.trade/docs/connect/v3`) | free for individual use; historical-candles add-on free since 2025-02-08. API key held by the operator (`KITEC_API_KEY` + `KITEC_API_SECRET` in `memory/.env`). Root `https://api.kite.trade`, header `X-Kite-Version: 3` on every call. Endpoints: `GET /instruments` (dump — NO auth), `GET /quote?i=NSE:INFY` (full quote: token, LTP, volume, OHLC, circuit limits, depth), `GET /ltp`, `GET /ohlc`, `GET /instruments/historical/:instrument_token/:interval?from=&to=`. Authed calls: `Authorization: token <api_key>:<access_token>`; access_token from the daily request_token login exchange (`/session/token`, checksum = SHA256(api_key+request_token+api_secret)), valid until ~6 AM next day. Envelope: `{status: "success", data: {...}}` / `{status: "error", message, error_type}`. Official Python client: `kiteconnect` (PyPI). Relevant when exchange-grade OHLC replaces the Yahoo price lane (PnF) |
| Value here | third cross-check for symbols/names (universe refresh); NOT a source for industry/fundamentals or ISINs |
| STATUS 2026-09-15 (final) | DECIDED: keep Kite for the public instruments dump ONLY — no login flow. All market-data routes return 403 `PermissionException` on the free tier and the operator dropped the pursuit; Yahoo remains the price/label lane |

## Global exchanges (D14b — international lane, probed 2026-09-15)

| Market | Universe feed | Sector labels | Status |
|---|---|---|---|
| US — Nasdaq | `nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt` (pipe-table) | — | ✅ verified |
| US — NYSE/AMEX etc | `nasdaqtrader.com/dynamic/SymDir/otherlisted.txt` | — | ✅ verified |
| US — ticker+CIK | `sec.gov/files/company_tickers.json` (10.4k; UA must carry contact email) | — | ✅ verified |
| US — S&P 500 | Wikipedia *List of S&P 500 companies* (id=constituents table) | **GICS Sector + Sub-Industry** | ✅ verified (503 parsed) |
| Japan | JPX publishes monthly listed-issue xlsx under `jpx.co.jp/markets/statistics-equities/misc/` (attachment paths rotate; the misc/04 page hosts PBR stats — the Tōyō list file needs pinning) | — | ⏳ endpoint to pin |
| Japan — labels | Nikkei 225 Wikipedia constituents table lacks a ticker-headed header (no parse) — REDUNDANT anyway: the TSE master (data_j.xlsx, 4,441 issues with 33-sector labels) covers Japan | — | ⏹ skipped as redundant |
| Hong Kong — HSI labels | Wikipedia *Hang Seng Index* constituents parse fine (ticker/name/sub-index, 85 rows) but are REDUNDANT for listings: the official HKEX ListOfSecurities covers them (sub-index would be a taxonomy label, not a listing fact) | sub-index | ⏹ skipped as redundant |
| Korea | **FOLDED** from Wikipedia *KOSPI 200* components table (`en.wikipedia.org/wiki/KOSPI_200#Components`), operator-curated to `/tmp/krx_listings.json` (200 rows; fields company/krx_code/yf_ticker/gics_sector; tickers suffixed `.KS`, `.KQ` for KOSDAQ — operator conventions). TRAP: that table's header is company-first — my first parse inverted symbol/name (purged). Full universe: `openapi.krx.co.kr` (operator pointer) needs an API key | GICS sector | ✅ 200 (kospi) / ⏳ openapi key for full universe |
| China — SSE | **FOLDED**: `query.sse.com.cn/sseQuery/commonQuery.do?jsonCallBack=...&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L&STOCK_TYPE=1&pageHelp...` (JSONP, Referer `www.sse.com.cn`, 25/page; fields A_STOCK_CODE / FULL_NAME_IN_ENGLISH / CSRC_CODE_DESC industry / LIST_BOARD 1=main 2=STAR; throttles mid-run but retries land it) | CSRC industry (zh) | ✅ 1,844 A-shares (main/star, suffix `.SS`) |
| Taiwan | **VERIFIED**: `isin.twse.com.tw/isin/e_class_main.jsp?market=1&Page=N&Language=en` (paginated; columns No./ISIN/Code/Name/Market/Type; ISIN regex `^TW[A-Z0-9]{10}$`) | — | 🟡 scrape in flight |
| Toronto | **FOLDED**: operator-supplied official TMX xlsx (tsx.com issuer-directory download, hand-delivered 2026-09-15 — JS-rendered link, DevTools only). Sheets TSX/TSXV × domestic/international; header at row 10 (r1-3 disclaimer, r6-8 summary); column offsets shift between boards (TSXV carries PO ID); filter Sector ∈ ETP/Closed-End/Fund/Structured/Trust | **TMX Sub-Sector** (own taxonomy, richer than GICS) | ✅ 2,248 equities (747 tsx `.TO` + 1,501 tsxv `.V`) + 60 tsx60 index rows |
| Europe — FTSE/DAX/CAC | **VERIFIED+FOLDED**: Wikipedia constituent tables — pick the table whose header row contains ticker/symbol/code (the year-history tables are decoys): FTSE 100 = company/ticker/ICB; DAX 40 = ticker/company/sector; CAC 40 = company/GICS/ticker | ICB / sector / GICS | ✅ 180 rows (LSE ftse100, XETRA dax40, EURONEXT cac40) |
| Toronto | **FOLDED**: operator-supplied official TMX xlsx (tsx.com issuer-directory download, hand-delivered 2026-09-15 — JS-rendered link, DevTools only). Sheets TSX/TSXV × domestic/international; header at row 10 (r1-3 disclaimer, r6-8 summary); column offsets shift between boards (TSXV carries PO ID); filter Sector ∈ ETP/Closed-End/Fund/Structured/Trust | **TMX Sub-Sector** (own taxonomy, richer than GICS) | ✅ 2,248 equities (747 tsx `.TO` + 1,501 tsxv `.V`) + 60 tsx60 index rows |
| China — Shenzhen (SZSE) | **VERIFIED**: `szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1110&TABKEY=tab1&PAGENO=N` (Referer szse.cn; 20 rows/page; fields bk board / agdm code / agjc name-with-HTML / agssrq listing date) — throttles aggressively from a repeat-hitting IP (read timeouts mid-scrape) | — | 🟡 scrape grinding |
| Korea | **Index lane folded**: Wikipedia KOSPI 200 (company/symbol/GICS sector, suffix `.KS`; `.KQ` for KOSDAQ — operator pointer 2026-09-15). Full universe: `openapi.krx.co.kr` (operator pointer) needs an API key; the old `data.krx.co.kr/getJsonData.cmd` bld set 400s | GICS sector | ✅ 200 (kospi200) / ⏳ openapi key for full universe |
| HK | HKEX daily quotation CSVs | — | unprobed |

Design note: international entities need (a) Yahoo-style tickers
(`AAPL`, `SHEL.L`, `SAP.DE`, `005930.KS`, `0700.HK`, `2330.TW`, `7203.T`)
to enter the yfinance label lane, and (b) sector labels from the index
tables. US is fully free-end-to-end; Europe is covered by index tables;
Asia varies (SSE/TSE/TWSE verified; TSX/KRX ride operator research).

## Sidecars (queryable, git-managed)

| Store (DuckDB, one file) | Tables/Views | Content |
|---|---|---|
| `memory/data/sources.duckdb` | `exchange_listings` (24,305) | LISTING-level long format: isin, name, industry, asset_type (equity/debt/cp/mf/pref), exchange (NSE/BSE), segment (main/sme), symbol, kite_tradingsymbol/token/tick/lot |
|  | `mca_cin` (1,020) | entity_name → CIN + MCA name/status/class/PBA/state + provenance |
|  | `index_constituents` (append-only) | index_name, index_slug, asset_class, symbol, isin, company_name, industry, series, as_of, fetched_at — NSE index membership per reconstitution vintage |
|  | `vw_equity` / `vw_sme` / `vw_instruments` / `vw_index_constituent` | convenience views (equity; SME listings; kite-tokened instruments; latest constituent vintage per index) |

Query directly — no load step:

```sql
duckdb memory/data/sources.duckdb
SELECT * FROM vw_sme;                      -- all SME listings, both boards
SELECT * FROM vw_instruments WHERE symbol LIKE 'INFY%';
```

Export for external consumers (arrow/json/csv, same as snapshot exports):

```sql
COPY (SELECT * FROM vw_equity) TO 'eq.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
COPY (SELECT * FROM mca_cin) TO 'mca.csv' (FORMAT CSV, HEADER);
COPY (SELECT * FROM exchange_listings) TO 'listings.json';
```

Snapshot integration (D15, executed 2026-09-15): every `make snapshot`
exports these tables to git-tracked `snapshots/parquet/sources/` (own
`SOURCES_TABLES` manifest + `_schema.sources.sql` — deliberately NOT the
graph `MATERIALISED_TABLES`, whose drop pass must never touch this store);
`snapshot_db.py --check` verifies row counts and `--restore` rebuilds the
file (views replay from the DDL).

Query pattern:

```sql
-- see vw_sme / vw_equity in memory/data/sources.duckdb
```

## Consumers

- **D12** — CIN/NIC: sidecar → `entities.cin` + facets via
  `backfill_identifiers`; NIC seed table maps ONLY nic2008-era CINs.
- **D14** — universe seed: `exchange_universe.parquet` equity rows minus
  existing entities ⇒ stub companies (name + ticker only). The D14 seed
  does NOT carry `industry`/`sector_classification` — those converge later
  from index constituents (`derive_indices.py`) and the yfinance label
  lane. ~6.6k new entities measured 2026-09-15.
- **Index-membership fill** — `index_sync.py` →
  `sources.duckdb::index_constituents` → `derive_indices.py` → `index`
  entities + `listed_on_index` edges (company → index) + converged
  `sector_classification` (`doc/improvements/archive/graph/index_membership_fill.md`).
- **yfinance lane** (pre-existing): tickers → industry labels via
  `helpers/core/get_tickers.py` + `helpers/maintenance/enrich_from_yfinance.py`
  — the classification-label workhorse for exchange stubs.
