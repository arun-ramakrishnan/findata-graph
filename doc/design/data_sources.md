# Data Sources — External Endpoint Register

**Status:** LIVE reference (D12/D14 execution, 2026-09-15). Every external
data endpoint this repo pulls, with auth quirks, verified shapes, and the
sidecar files that cache the results. Re-discovery is expensive — this
register exists so the blind search never repeats. Companion filings:
D12 (CIN/NIC) and D14 (universe seed) in
`doc/improvements/proposals/hyper_lane_wiring.md` §5.

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
| Japan — labels | Wikipedia *Nikkei 225* constituents (per-page parser needed — table shape differs from S&P 500; generic parser picks up index-stats rows) | sector per constituent | ⏳ parser at execution |
| Hong Kong | Wikipedia *Hang Seng Index* constituents (same caveat) | sub-index | ⏳ parser at execution |
| Korea | KRX data portal (`data.krx.co.kr`, POST-based) | — | ⏳ to pin |
| China — SSE | `query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L` (JSON; ignores paging → full list; needs `Referer: sse.com.cn`) | — | ✅ verified |
| Taiwan | TWSE ISIN list (`isin.twse.com.tw/isin/C_public.jsp`) returned 500 — params/encoding to pin | — | ⏳ to pin |
| Toronto | TSX/TSXV listed-directory xlsx (resource URL 404 — find via tsx.com listing pages) | — | ⏳ to pin |
| Europe — FTSE/DAX/CAC | Wikipedia index-constituent tables (ICB sector per constituent — per-page parsers, same pattern as S&P 500; generic parse proved the route but caught stats rows) | ICB sector | parser at execution |
| Toronto | TSX/TSXV listed-directory xlsx (resource URL 404 — find via tsx.com listing pages) | — | ⏳ to pin |
| China — Shenzhen (SZSE) | pair of the SSE API (szse.cn equity list) | — | unprobed |
| Korea | KRX data portal (`data.krx.co.kr`, POST-based) | — | ⏳ to pin |
| HK | HKEX daily quotation CSVs | — | unprobed |

Design note: international entities need (a) Yahoo-style tickers
(`AAPL`, `SHEL.L`, `SAP.DE`, `005930.KS`, `0700.HK`, `2330.TW`, `7203.T`)
to enter the yfinance label lane, and (b) sector labels from the index
tables. US is fully free-end-to-end; Europe is covered by index tables;
Asia varies (SSE verified; JPX/TWSE/TSX/KRX need endpoint pinning).

## Sidecars (queryable, git-managed)

| Store (DuckDB, one file) | Tables/Views | Content |
|---|---|---|
| `memory/data/sources.duckdb` | `exchange_listings` (24,305) | LISTING-level long format: isin, name, industry, asset_type (equity/debt/cp/mf/pref), exchange (NSE/BSE), segment (main/sme), symbol, kite_tradingsymbol/token/tick/lot |
|  | `mca_cin` (730) | entity_name → CIN + MCA name/status/class/PBA/state + provenance |
|  | `vw_equity` / `vw_sme` / `vw_instruments` | convenience views (equity; SME listings; kite-tokened instruments) |

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
  existing entities ⇒ stub companies (ticker + industry +
  sector_classification from exchange data); ~6.6k new entities measured
  2026-09-15.
- **yfinance lane** (pre-existing): tickers → industry labels via
  `helpers/core/get_tickers.py` + `helpers/maintenance/enrich_from_yfinance.py`
  — the classification-label workhorse for exchange stubs.
