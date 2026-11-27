---
title: "Update exchange and ticker data sources"
status: executed
filed: "2026-11-27"
executed: "2026-11-27"
completed_md: "576"
area: "helpers/maintenance/exchange_sync.py + helpers/maintenance/ticker_hygiene.py + helpers/maintenance/fold_identifiers.py + helpers/maintenance/price_sync.py + helpers/maintenance/enrich_relations.py + helpers/maintenance/googlefinance.py + helpers/research/price_join.py + memory/data/sources.duckdb + memory/research.duckdb + doc/design/data_sources.md + doc/improvements/pending.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Update exchange and ticker data sources

**Date:** 2026-11-27 · **Status:** EXECUTED ·
**Area:** `helpers/maintenance/exchange_sync.py`, `helpers/maintenance/ticker_hygiene.py`, `helpers/maintenance/fold_identifiers.py`, `helpers/maintenance/price_sync.py`, `helpers/maintenance/enrich_relations.py`, `helpers/research/price_join.py`, `memory/data/sources.duckdb`, `memory/research.duckdb`

## 1. Motivation

The earlier master-query/ripwire investigation (m01841, block `b19`) found that
this repo already has a broad exchange-universe and ticker-fallback stack, but
Yahoo failure residue and a few source-specific gaps remain. The ask is to turn
those findings into a data-update proposal and proceed with safe slices where
possible, fixing pending gaps where source evidence exists, and adding slices
for gaps that need external keys, operator files, or separate source pinning.

## 2. Evidence (measured 2026-11-27, this box)

| Metric | Result |
|---|---|
| `helpers/maintenance/exchange_sync.py` lanes | `hkex`, `szse`, `sse`, `twse`, `wiki`, `nse`, `bse`, `us`; `tmx`/`krx` operator-delivered; `jpx` added this slice |
| `memory/research.duckdb::entities` | 25,201 companies; 6,073 with ticker; 6,072 distinct tickers; 19,128 no ticker |
| ticker suffixes | `.BO` 3,122; `.NS` 2,801; no-suffix 105; other 45; Indian `.NS+.BO` 5,923; numeric `.BO` 378; numeric `.NS` 0 |
| `memory/research.duckdb::entity_identifiers` | `isin` 6,734; `alias` 72; no `cik` type |
| `entity_ticker_status` | `delisted` 5; `amalgamated` 2 |
| `entity_gf_map` | `yahoo_mapped_back` 1; `gf_only` 1 |
| `helpers/research/price_join.py census` | 6,073 → 5,480 price-joinable / 593 yahoo-absent; BSE 2,919 + NSE 2,561; full-5y 3,857; thin <1y 508; 1,172,762 rows; 2021-10-25..2026-10-30 |
| `memory/data/sources.duckdb::exchange_listings` | 47,525 rows; 45,067 symbols; 18,997 ISINs; no-ISIN 27,219; `equity` 39,888; `debt` 5,365; `other` 1,134; `cp` 868; `mf` 258; `pref` 12 |
| `memory/data/sources.duckdb::exchange_listings` equity | 39,888 rows; 37,430 symbols; 11,360 ISINs; no-ISIN 27,219 |
| JPX official `data_e.xlsx` (2026-11-27 probe) | 4,444 rows / 4,444 codes; no ISIN; `fetch_jpx()` returns 4,444 rows: `equity` 3,891 + `other` 553; `Effective Date` 2026-10-31; official attachment `https://www.jpx.co.jp/english/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_e.xlsx`; source page `https://www.jpx.co.jp/english/markets/statistics-equities/misc/01.html`; old `.xls` URL 404 |
| `make maint` | PASS 8 ok + 2 skipped (`graph-rebuild`, `stamp-centrality` skipped because `memory/graph.duckdb` is served by quack) |
| `make snapshot-check` | rc=0; integrity OK; DuckDB generation OK; Parquet verify OK (73 sqlite + 7 sources + 3 note); `note_search_content` absent on DuckDB live side is contract-legal |

## 3. Existing mechanisms to reuse

- `helpers/maintenance/exchange_sync.py:427-496` `fetch_nse` / `fetch_bse`; `:569-594` `LANES` / `LANE_EXCHANGES`; `:804-810` `--tmx-file` / `--krx-file`; `fetch-only` / `apply-only`; `lane_fresh`; `--force`; `--max-age-days`; renames/delistings flagged never auto-applied; `memory/data/ipo_worklist.csv`.
- `doc/design/data_sources.md:40-61` NSE static CSVs and UA/Referer; `:160-185` D14b global exchanges; `:187-253` `memory/data/sources.duckdb`, `exchange_listings`, `index_constituents`, `entity_identifiers`, `mca_cin`, snapshots, yfinance, Kite.
- `helpers/maintenance/ticker_hygiene.py`: numeric Indian scrip `.NS`/`.BO`; `--untickered --notes-only`; `--apply`; `--fetch-only` + `--apply-only`; `--tier2`; `--tier3`; `outputs/ticker_hygiene_report.md`; operator-gated.
- `helpers/core/get_tickers.py::_yf` per-property guard; `helpers/maintenance/enrich_from_yfinance.py::identity_ok`; `helpers/maintenance/exchange_search.py` BSE PeerSmartSearch; `helpers/maintenance/googlefinance.py`; `helpers/maintenance/googlesheets_metrics.py`; `enrich_relations.py --source googlefinance`; `enrich_relations.py --classify ENTITY delisted|amalgamated [SUCCESSOR]`; `entity_ticker_status`; `entity_gf_map`.
- `helpers/maintenance/fold_identifiers.py`: fold exchange ISINs + optional SEC CIKs; `.NS`/`.BO`/`.BSE`; `memory/data/sources.duckdb`; `memory/research.duckdb`.
- `helpers/maintenance/price_sync.py`: Yahoo `yf.download`, `PERIOD=5y`, `BATCH=200`, 2 attempts, `90s` cooldown on `Rate limited`/`429`, `5s`/`10s`, `fill_missing`, atomic `os.replace`, flat-frame handling; `price_join.py census`.
- `helpers/maintenance/enrich_from_yfinance.py::fetch_todo` progress + `STALLED`; per-company failures/mismatches; scratch cache.
- Finnhub `symbol_lookup` + `_apply_finnhub_writebacks`; `FINNHUB_API_KEY`.
- Kite Connect v3 `GET /instruments` no auth; market-data 403 on free tier; operator dropped; public instruments dump only.
- `helpers/maintenance/snapshot_db.py`; `make snapshot`; `make snapshot-check`; `make snapshot-restore`.

## 4. Scope

### In scope
- Safe, repeatable data refresh and identifier folding.
- Source-pinning where official source evidence exists.
- Ticker-hygiene reconciliation with backup + dry-run before apply.
- Yahoo-failure classification and fallback residue reduction.
- Documentation reconciliation for known stale comments and pending gaps.

### Non-goals
- No full gate rerun; no `stg` patch/commit lifecycle ops; no history rewrite; no writer-owned `findata/**` edits unless an explicit operator-gated slice says so.
- No blind `ticker_hygiene.py --apply`; no blind `price_sync.py build` on the full 5,923 Indian set; no auto-applying renames/delistings.
- No Kite market-data purchase; no Finnhub premium purchase; no GoogleFinance public API; no web-scraping GF/Screener.in; no NSE official-API revival.
- No claiming no-ISIN or Yahoo-absent equals delisted; no terminal `still-dead` writes without an explicit classifier.

## 5. Slices

| Slice | Status | Work | Fixes / gap | Validation |
|---|---|---|---|---|
| **S0 — proposal + baseline** | **done** | This document; measured current state; no full gate. | Concrete slices recorded. | `make md-lint`; `make static-checks`. |
| **S1 — safe exchange refresh** | **done** | `make maint`; full scriptable-lane `exchange_sync.py --force hkex szse sse twse wiki jpx nse bse us --apply` executed after backup `/mnt/data/tmp/opencode/exchange_ticker_backup_20261127T123132/`, rc=0; `make snapshot` + `make snapshot-check` clean. `tmx`/`krx` stayed out (no operator file); JPX safe slice is S3. | Repeatable data update landed; `exchange_listings=52353`. | `exchange_sync.py --force --apply` rc=0; `make snapshot-check` rc=0; `git diff --stat` reviewed. |
| **S2 — `ticker_hygiene` reconciliation** | **done (fresh staged review; no note writes)** | Fresh detached staging on 2026-11-27 with scratch JSONLs: numeric `--tier2 --tier3 --fetch-only` → `candidates=454`, canonical stage `resolved=68`, `tier3_rename=7`, `tier3_no_kite=268`, `tier3_dead=111`; note-backed untickered `--untickered --notes-only --tier2 --tier3 --fetch-only` → `candidates=135`, canonical `resolved=2`, `tier3_no_kite=133`. Backup `/mnt/data/tmp/opencode/ticker_hygiene_s2_apply_20261127/`, then `--apply-only --apply` on numeric stage → `applied=68 skipped_moved=0 note_tickers=0/0`. Untickered stage left review-only (no `findata/**` note frontmatter writes). | numeric `.BO` 463→386, numeric `.NS` 32→0; two untickered candidates (`Hitachi Energy`, `HKC`) staged but not applied by operator choice. | `outputs/ticker_hygiene_report.md`; fresh stage JSONLs under `/mnt/data/tmp/opencode/`; `make snapshot-check` clean. |
| **S3 — JPX lane** | **done** | Added `fetch_jpx` + `JPX_XLSX_URL` pinned `data_e.xlsx`; `LANES["jpx"]`; `LANE_EXCHANGES["jpx"]=["TSE"]`; raw `Local Code` as string; `ticker=<code>.T`; ETF/ETN/REIT/fund/equity-contribution `asset_type='other'`; non-equity never seeds company stubs; `seed_stubs` filters `asset_type=='equity'`. | `data_sources.md:168` JPX attachment not pinned; official source verified. | `tests/test_exchange_sync.py::test_fetch_jpx_pinned_monthly_master`; 15 passed; real `fetch_jpx()` → 4,444 (`equity` 3,891 + `other` 553). |
| **S4 — terminal-class persistence** | **done for staged promotion + exclusion**; delisted/amalgamated successor still proposed | `helpers/core/vocab.py:TERMINAL_TICKER_STATUS_VALUES` now the single roster: `amalgamated, delisted, no-candidates, still-dead, tier2_refused, tier3_dead, tier3_rename, unverified`. `helpers/maintenance/enrich_relations.py` `entity_ticker_status` gains `detail TEXT` + current CHECK; `_promote_staged_statuses()` promotes JSONL terminal rows (`--promote-status STAGED_JSONL`, later row wins, non-terminal rows skipped, successor from `new_ticker`, detail from `yf_name`/`error`); `load_companies()` excludes terminal names so no re-probe; `load_terminal_statuses()` tolerates a missing table on read-only sweeps. `conflict`/`ambiguous`/`unmatched`/`same-issuer` stay non-terminal triage per operator ruling m04969. | `delisted`/`amalgamated` successor writes still need operator-classified input; `[terminal]` report section not rewired to the new detail column. | `pytest tests/test_enrich_relations.py::TestTerminalClassifications` — `test_promote_staged_statuses_persists_terminal_only` + `test_terminal_classifications_leave_the_sweep_universe`; 139 targeted tests passed. |
| **S5 — identifier folding** | **done; all live conflicts cleared** | `fold_identifiers.py` classifies same-underlying-issuer collisions as `same-issuer` (`_normalize_company_name`, `_rights_entitlement_name`, `_same_issuer_name`, `_is_isin`); unknown exchange suffixes cannot borrow another exchange's listing; bare foreign tickers (e.g. `DeFi Capital Markets, Inc. Common Stock` / `DCM`) cannot borrow an Indian listing; `ensure_resolution_status_vocab()` migrates the DuckDB CHECK. Operator ruling 2026-11-27: ISIN is authoritative and `Dalmia Bharat Sugar and Industries` is a distinct issuer from cement maker `Dalmia Bharat`. `apply_operator_overrides()` reassigned 4 identifiers (`INE0QNI01012`→Boss Packaging Solutions, `INE499A01024`→DCM Shriram, `NSE:DCMSRIND`→DCM Shriram Industries, `NSE:DALMIASUG`→Dalmia Bharat Sugar and Industries), cleared 5 stale conflict statuses, and remaining real-ISIN collisions became `same-issuer`. Live final: `conflicts=0`, `same_issuer=17`, `unmatched=7375`. | 17 same-issuer rows remain triage-only (rename/duplicate-listing variants), never re-probe bans. No genuine collisions left; do not reassign a real ISIN without source verification. | `pytest tests/test_fold_identifiers.py`; dry-run after overrides `conflicts=0 same_issuer=19`, live apply `conflicts=0 same_issuer=17 statuses=7392`; live `entity_resolution_status` groups `same-issuer=17, unmatched=7375`. |
| **S6 — Yahoo-absent classification** | **deferred (operator choice 2026-11-27)** | `price_sync.py build --fill-missing` + `price_join.py census` already re-run once: `yahoo-absent=928` after full refresh; remaining gap is not a blocker for DuckDB capture. | Needs operator decision on a permanent `no-market-data` terminal status; re-running fills costs time without changing identity resolution. | Reopen when `market_data_resolution.md` defines the permanent conversion rule or operator supplies a fresh cooldown window. |
| **S7 — fallback residue** | **deferred (operator choice 2026-11-27)** | GF/Finnhub/Kite/exchange-search fallbacks are broad, low-yield, and mostly need new credentials or curated files; bulk yfinance path stays authoritative. | GF `gf_only`/`still-dead`; Finnhub discovery; Kite dump; no public GF API. | Reopen for a specific named entity set or when operator supplies a Kite/Finnhub artifact. |
| **S8 — external source gaps** | **deferred (operator choice 2026-11-27)** | India/NSE/BSE/SZSE/JPX/KOSPI-200 lanes applied; remaining KRX full universe, HKEX daily CSV, NSE Emerge, TMX need API keys/operator files/source verification. | External inputs, not code; no verified public source for several. | Reopen per-source only after URL/API key/operator file is verified; no bulk sweep. |
| **S9 — identifier-aware `fold_identifiers` extras** | **done (cleanup landed)** | `.T` → TSE mapping; SEC `company_tickers.json` / `company_tickers_exchange.json` adapter; `_cik10` 10-digit padding; `_base_symbol` `-` normalization; stale unused `DEFAULT_DB` constant removed (CLI already defaults to canonical `memory/research.duckdb` via `helpers.core.db`); `load_exchange_listings()` now skips non-equity (`other`/`pref`/`mf`/`cp`/`debt`) so ETFs/debt cannot borrow equity identifiers. | `.NS/.BO/.BSE/.T`; TSE no ISIN; `cik` no longer missing; non-equity rows no longer pollute the ISIN fold. | `pytest tests/test_fold_identifiers.py` — 12 passed incl `test_load_exchange_listings_skips_non_equity`; `--check` → `listings=12842 direct_isin=3095 conflicts=0 same_issuer=17 unmatched=7375`; `--apply --persist-status` → `statuses=7392`. |
| **S10 — docs/stale-status reconciliation** | **deferred (operator choice 2026-11-27)** | Cosmetic/stale-comment sweep (`data_sources.md` JPX pin, `pending.md` D2 re-measure, commit-message template references, `snapshot_untrack_large`, `.gitignore`, `memory/research.db`→`.duckdb` mentions). | No runtime impact; `make md-lint`/`make static-checks` currently green. | Reopen only when a stale reference blocks a gate or an operator wants a docs-only cleanup arc. |
| **S12 — OpenFIGI/FIGI** | **done; metadata in DuckDB** | `POST https://api.openfigi.com/v3/mapping`, no basic key; `idType=TICKER`, `exchCode=US/IN`; do not pass `marketSector`; FIGI string in `memory/research.duckdb::entity_identifiers`, full metadata in `memory/data/sources.duckdb::figi_instruments`; no `uniqueID` ISIN reliance. | no `figi` before; OpenFIGI yields `BBG000B9XRY4`, `BBG000GQ5749`, `BBG000BKVP93`; `marketSector='Equity'` invalid input. | probe `AAPL`, `SBIN`, `RELIANCE`; `apply_candidates(... identifier_type='figi' ...)`; `make snapshot-check`. |
| **S13 — unresolved/unmatched/conflict persistence** | **done** | `fold_identifiers.py --persist-status` writes non-terminal `unmatched`/`conflict`/`ambiguous` rows to `entity_resolution_status`; `enrich_relations.py` gains `--resolution-classify` + `[resolution]` report loader; statuses never enter terminal re-probe exclusions. | `entity_ticker_status` only held terminal `delisted`/`amalgamated`/`still-dead`; `conflicts`/`unmatched` were console-only. | `--persist-status` → `statuses=7427`; `enrich_relations --resolution-classify` CLI; 127 targeted tests. |
| **S14 — SZSE mirror + stale fallback** | **done** | `SZSE_APIS` tries `www.szse.cn`, www-less `szse.cn`, then http mirror with short backoff; `main()` falls back to last staged `szse.json` when all mirrors fail. | `FETCH-FAILED ... Connection reset by peer` on 2026-11-27 full refresh. | `--force szse --fetch-only` → `staged=2905`; apply → `applied=2905 new=7 gone=3`. |
| **S15 — KOSPI 200 lane** | **done** | `fetch_krx200()` parses the Wikipedia KOSPI 200 components table (company-first header trap avoided by `parse_wiki_table`); six-digit and alphanumeric local codes kept; `ticker=<code>.KS`, `segment='kospi200'`; `LANES['krx200']`, `LANE_EXCHANGES`, `LANE_SEGMENTS`. | KOSPI 200 was operator-curated JSON only; full KRX universe still needs an API key. | `--force krx200 --fetch-only` → `staged=200`; apply → `applied=200 new=200`; 195 stubs seeded. |
| **S16 — quarterly BhavCopy repin probe** | **done (DB-native)** | `helpers/maintenance/bhavcopy_repin.py` samples the **last 3 available UDiFF trading days per month** over the **previous completed calendar quarter**, aggregates ticker/ISIN rows, and upserts them into `memory/data/sources.duckdb::bhavcopy_sample` (PK exchange/trade_date/ticker). `--no-db` keeps the old report-only JSON escape hatch; `new`/`absent_from_sample` remain candidates, never auto-deletes. | BhavCopy repin has been “pending” since `exchange_sync.py:15`; bulk month download is far more data than needed. | 2026-Q3 dry run: NSE new 51 / absent 5,402; BSE new 74 / absent 9,416; DB write path added. |
| **S17 — FIGI instrument metadata in DuckDB** | **done; batched** | `fold_identifiers.openfigi_lookup_many()` chunks `/v3/mapping` jobs (10 per POST; 100 returns HTTP 413) and keeps the full `FigiRecord` (`name`,`ticker`,`exchCode`,`marketSector`,`securityType`,`shareClassFIGI`,`compositeFIGI`); `openfigi_lookup()` remains a one-ticker wrapper; `--figi --apply` collects unmatched tickers, issues batch POSTs, upserts `sources.duckdb::figi_instruments`, and still writes the FIGI identifier to `entity_identifiers`. `helpers/core/vocab.py` and the SQLite snapshot CHECK now admit `figi`. | FIGI metadata was discarded, only the string was kept; no `figi_instruments` table; sequential per-ticker POSTs were too slow for ~7k unmatched tickers. | `pytest tests/test_fold_identifiers.py`; live `--figi --apply`; `make snapshot`. |
| **S18 — resolution/status ontology alignment** | **done** | `RESOLUTION_STATUS_VALUES` is shared by `fold_identifiers.py` and `enrich_relations.py`; `entity_resolution_status` is documented in `doc/design/ontology.md` and `doc/design/db_schema.md`; `entity_ticker_status` CHECK carries `still-dead`. | statuses were code-only and absent from the ontology roster. | `make md-lint`; `make static-checks`; targeted tests. |
| **S11 — no-go/external** | documented | Kite free market-data no; Finnhub premium no; GoogleFinance public API no; Screener.in scraping no; NSE official API Akamai no; KRX without key no; full restore of untracked large snapshots no. | hard stops. | not implemented. |

### S16 — last-3-trading-day BhavCopy repin probe (detailed)

**Why.** There is no single monthly BhavCopy file; NSE and BSE publish
daily UDiFF files. Pulling every trading day of a month is unnecessary for
the repin decision, so this slice samples only the **last three available
trading days per month**, for the **last three months only**. A symbol that
traded on any of those days is a live candidate; a master symbol absent on
all three is an `absent_from_sample` candidate, never an auto-deletion.

**Sources (official, no key).**
- NSE: `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` — browser UA + `Referer: https://www.nseindia.com/`; zip contains one CSV.
- BSE: `https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{YYYYMMDD}_F_0000.CSV` — plain CSV, `Referer: https://www.bseindia.com/`.
- Legacy standardized formats were discontinued 2024-07-08 (BSE notice 20240610-33); no fallback to `EQ{DDMMYY}_CSV.ZIP`.
- No file on weekends/holidays; a 404/outage is recorded and the walk continues backwards.

**Shape.** UDiFF columns used: `TckrSymb`, `SctySrs` (NSE), `FinInstrmId`, `Sgmt`, `ISIN` (BSE). NSE rows keep the ticker symbol → `<symbol>.NS`; BSE rows use the numeric `FinInstrmId` → `<scrip_code>.BO`, which is exactly the existing numeric-`.BO` convention. UDiFF carries no company name, so `name` is left empty and `enrich_relations.identity_ok` remains the authority before any writeback.

**Code / CLI.**
- `helpers/maintenance/bhavcopy_repin.py`
  - `nse_url(day)`, `bse_url(day)`, `parse_udiff_csv(text, exchange, day)`.
  - `collect_month(year, month, today)` walks `_days_back()` in reverse, skips weekends, stops when each exchange has 3 successful days, and aggregates unique tickers per exchange.
  - `_previous_months(today, 3)` defaults to `(M-2, M-1, M)`; `_quarter_months(today)` returns the previous completed calendar quarter.
  - `--months` (default 3), `--month YYYY-MM`, `--quarter` (quarterly cadence), `--trading-days` (default 3), `--today` (test determinism).
  - DB-native by default: upserts all sampled rows into `memory/data/sources.duckdb::bhavcopy_sample` (PK exchange/trade_date/ticker); prints per-month day lists, unique counts, and `new`/`absent_from_sample` symbol candidates vs `exchange_listings`. `--no-db` retains the report-only `$TMPDIR/findata-graph/bhavcopy_scratch/bhavcopy_repin_<YYYY-Qn>.json` escape hatch.
- `tests/test_bhavcopy_repin.py` covers NSE/BSE UDiFF parsing, numeric scrip-code handling, weekend/future-day skipping, the 3-month window, and the previous-completed-quarter boundary.

**Quarterly cadence architecture.**
- **Trigger:** once per quarter, on the first business day after quarter close (or an operator-run `make` target). Do not run intra-quarter; the point is a closed reference window, not a rolling sample.
- **Window:** the previous completed calendar quarter, e.g. a run on 2027-01-05 covers Oct–Dec 2026. `--quarter` selects this window; `--month` and `--months` remain debugging/backfill escape hatches.
- **Sampling:** for each of the three months, walk backwards from month-end and take the **last three available trading days** per exchange. Weekends are skipped; exchange holidays and transient 404/outages are recorded per day and do not abort the month.
- **Idempotency:** one DB sample per quarter; reruns upsert the same PK rows. `--no-db` writes `bhavcopy_repin_<YYYY-Qn>.json` atomically for inspection. No partial quarter state.
- **Diff semantics:** `new` = sampled symbol absent from `exchange_listings`; `absent_from_sample` = master symbol not seen in the three-day sample. `absent_from_sample` is **not** a delisting verdict: the master carries historical and non-equity rows, so these are triage candidates, never auto-deletes. Persist reviewed outcomes in `entity_resolution_status` (`unmatched`/`still-dead`), not by deleting master rows.
- **Handoff:** review the report → `fold_identifiers.py` for ISINs → `exchange_sync.py`/stub seeding for reviewed new listings only after `identity_ok` → `make snapshot` + `make snapshot-check`.
- **Failure policy:** if an exchange is unreachable for a month, the report records the errors and the run is incomplete; rerun after the next trading day. Do not substitute stale data silently.
- **Cadence boundary:** quarterly runs make the three-month window a closed reference period; monthly runs are explicitly out of scope for S16.
- **2026-Q3 dry run (2026-11-27):** `--quarter --today 2026-11-27` → `bhavcopy_repin_2026-Q3.json`; Jul 29–31, Aug 27–28/31, Sep 28–30; unique NSE 3,233/3,442/3,490 and BSE 5,450/5,622/5,747; aggregate `new` NSE 51 / BSE 74; `absent_from_sample` NSE 5,402 / BSE 9,416. The large absent count is the master-history effect described above, not a delisting list.

**Acceptance.**
- `.venv/bin/python3 -m pytest tests/test_bhavcopy_repin.py -q` green.
- `ruff format` / `ruff check --select S,UP,C901` clean on the new files.
- A real run with `--today 2026-11-27` produces three dated files per exchange (or records explicit 404/outage errors) and upserts `bhavcopy_sample`; `--no-db` produces the staged JSON report instead.
- No full qa/perf rerun; no patch lifecycle ops; no auto-apply of `new`/`absent_from_sample`.

**Follow-through (operator-gated).** Review `bhavcopy_sample` (or the `--no-db` JSON); fold ISINs into `entity_identifiers` with `fold_identifiers.py`; seed new listings only after `identity_ok`; persist `absent_from_sample` candidates as `entity_resolution_status` rows (`unmatched`/`still-dead`) rather than deleting rows. NSE EOD archives older than two years are chargeable, so this probe is deliberately limited to the last 3 months.

## 6. Concrete next commands (not all require new code)

```bash
# Baseline + safe exchange refresh (backup live DBs first; operator-owned snapshots)
make snapshot
cp memory/data/sources.duckdb "$TMPDIR/sources.duckdb.bak"
cp memory/research.duckdb "$TMPDIR/research.duckdb.bak"

make maint
.venv/bin/python3 helpers/maintenance/exchange_sync.py --force hkex szse sse twse wiki jpx nse bse us --apply
.venv/bin/python3 helpers/maintenance/exchange_sync.py --force tmx --tmx-file /path/to/operator.xlsx --apply
.venv/bin/python3 helpers/maintenance/exchange_sync.py --force krx --krx-file /path/to/operator.json --apply
make snapshot
make snapshot-check

# Ticker hygiene: dry-run / stage before any apply
.venv/bin/python3 helpers/maintenance/ticker_hygiene.py --tier2 --tier3
.venv/bin/python3 helpers/maintenance/ticker_hygiene.py --untickered --notes-only --tier2 --tier3
.venv/bin/python3 helpers/maintenance/ticker_hygiene.py --untickered --notes-only --fetch-only
# review staged JSONL; then, only with backup + explicit operator go:
.venv/bin/python3 helpers/maintenance/ticker_hygiene.py --apply-only --apply

# Identifiers + Yahoo residue
.venv/bin/python3 helpers/maintenance/fold_identifiers.py --apply
.venv/bin/python3 helpers/maintenance/price_sync.py build --fill-missing
.venv/bin/python3 helpers/research/price_join.py census
```

## 7. Validation for this slice

- `.venv/bin/python3 -m pytest tests/test_exchange_sync.py -q` → 15 passed.
- `.venv/bin/ruff format ...` / `.venv/bin/ruff check ...` clean.
- Real `fetch_jpx()` probe → 4,444 rows.
- `make maint` PASS; `make snapshot-check` rc=0.
- No full gate rerun; no patch/commit lifecycle ops.

## 8. Open questions / not done

- **Explicit live-apply slice executed 2026-11-27** after backup `/mnt/data/tmp/opencode/exchange_ticker_backup_20261127T123132/`:
  - `.venv/bin/python3 helpers/maintenance/exchange_sync.py --force jpx --apply` → `[jpx] applied=4444 new=4444 gone=0`, `stubs seeded: 3879`; `memory/research.duckdb` entities 27,754→31,633; tickered 6,073→9,952; `memory/data/sources.duckdb::exchange_listings` now includes JPX; `snapshots/parquet/sources/exchange_listings.parquet` regenerated.
  - `.venv/bin/python3 helpers/maintenance/ticker_hygiene.py --apply` → `candidates=378`, `todo=0`, `resolved=3779`, `applied=0 skipped_moved=3779`, `note_tickers=0/0`; no numeric `.BO` reduction; `applied=0` because staged old tickers did not match current rows; no `findata/**` note edits.
  - `.venv/bin/python3 helpers/maintenance/fold_identifiers.py --apply` → `listings=20306 ciks=0 direct_isin=2989 cik=0 ambiguous=0 unmatched=6979 conflicts=6 candidates=0 written=0`.
  - `make snapshot` → `make snapshot-check` rc=0; `make md-lint`, `make static-checks`, `make lint-audit`, `make search-fresh` APPLY+check, `make snapshot-check` clean.
- `ticker_hygiene.py` remains operator-gated; backup + staged review still needed for any future `--apply`.
- KRX API key / operator master; TMX operator master; BhavCopy monthly; HKEX daily CSV; Korea/Taiwan/SZSE/JPX-adjacent full-universe details need source verification.
- `entity_ticker_status` currently only `delisted`/`amalgamated`; `still-dead` persistence is a proposed slice, not implemented.
- `pending.md` exchange gaps should be swept after S1/S3/S8.
- **US + India alternate slice executed 2026-11-27** with backup `/mnt/data/tmp/opencode/sec_cik/`:
  - SEC official `https://www.sec.gov/files/company_tickers.json` downloaded; `load_cik_rows` adapter added; `.venv/bin/python3 helpers/maintenance/fold_identifiers.py --apply --cik-json /mnt/data/tmp/opencode/sec_cik/company_tickers.json` → `ciks=10400 direct_isin=2999 cik=192 ambiguous=1 conflicts=8 candidates=207 written=207`; `memory/research.duckdb::entity_identifiers` `cik=192 namespace='sec.gov'` + 10-digit `_cik10`.
  - `.venv/bin/python3 helpers/maintenance/exchange_sync.py --force nse bse --apply` → `nse applied=2636 new=40 gone=33`, `bse applied=5131 new=129 gone=7855`, `stubs seeded=94`; `gone` flagged not auto-removed; `memory/data/ipo_worklist.csv`; `memory/data/sources.duckdb::exchange_listings` `BSE/NSE` refreshed.
  - Re-run `fold_identifiers.py --apply --cik-json ...` → `listings=20475 ciks=10400 direct_isin=3095 cik=192 ambiguous=1 unmatched=6967 conflicts=20 candidates=85 written=85`; `entity_identifiers` `isin=6834` (`BSE=4195 NSE=2635 HKEX=2 TWSE=2`) + `cik=192` + `alias=72` + `figi=3`.
  - OpenFIGI `POST /v3/mapping` probe (`AAPL`, `SBIN`, `RELIANCE`, `exchCode=US/IN`, no `marketSector`) → `figi BBG000B9XRY4 / BBG000GQ5749 / BBG000BKVP93`; 3 `identifier_type='figi' namespace='openfigi'` rows added.
  - Remaining: `conflicts`, `ambiguous`, `unmatched`, US `exchange_listings` no-ISIN, `still-dead`, `price_join`, `mca_cin`, BhavCopy/KRX/HKEX/SZSE/etc not completed.
  - **Pending-task slice 2026-11-27**: `still-dead` added to `entity_ticker_status` statuses + `--classify ... still-dead`; `price_sync.py build --fill-missing` + `price_join.py census` rerun (`yahoo-absent=4566` before, `714` after; `price-joinable=9332`; `2,104,939` rows; span `2021-10-25..2026-11-27`); `ticker_hygiene.py --apply` on current candidates (`applied=0`; `numeric_bo=463`; `numeric_ns=32`); `mca_cin_sync.py build --apply` (`mca_cin=1020`) then `apply --apply` blocked by 3 not-found sidecar entities (`Amara Raja Batteries`, `Kinetic Engg`, `Oil and Natural Gas`); full `exchange_sync.py --force --apply` rc=0; `exchange_listings=52353`; `mca_cin=1020`; `fold_identifiers` with SEC CIK `unmatched=7176 conflicts=24 candidates=141 written=141 cik=332`; `price_join` after full refresh `entities=10260 price-joinable=9332 yahoo-absent=928`.
  - **3–5 follow-through 2026-11-27**: backup `/mnt/data/tmp/opencode/pending_345_20261127T150137`; `mca_cin_sync.ENTITY_ALIASES` applied (`cin_set=1020`) + alias rows; `fold_identifiers --persist-status` wrote `statuses=7427` non-terminal resolution rows (`unmatched=7371 conflicts=55 ambiguous=1`); `SZSE_APIS` mirror/retry + stale-scratch fallback → `staged=2905` then `applied=2905 new=7 gone=3`; new `krx200` Wikipedia KOSPI 200 lane → `staged=200 applied=200 new=200`, 195 stubs seeded; `entity_resolution_status` added to `snapshot_db.SQLITE_PARQUET_TABLES` and `snapshots/parquet/_schema.sqlite.sql` (with `still-dead` added to `entity_ticker_status` CHECK) so the triage table is snapshotted — `make snapshot` exports 30 research parquet tables, `make snapshot-check` verifies 74+7+3 all OK; `166` targeted tests passed. Remaining non-goals: KRX full universe (API key), BhavCopy monthly, HKEX daily CSV, KRX API key, TMX operator file.
  - **S4/S5 close-out 2026-11-27** (backup `/mnt/data/tmp/opencode/duckdb_s5_s4_20261127/`): `fold_identifiers --apply --persist-status` → `listings=20479 direct_isin=3096 conflicts=11 same_issuer=11 unmatched=7375 statuses=7397 written=0` (after DuckDB CHECK migration admitted `same-issuer`). Live `entity_resolution_status`: `conflict=11, same-issuer=11, unmatched=7375`. `enrich_relations --promote-status` tested for terminal promotion + re-probe exclusion. `make static-checks`, `make lint-audit`, `make md-lint` clean; 139 targeted tests passed.
  - **Conflict-resolution close-out 2026-11-27** (backup `/mnt/data/tmp/opencode/duckdb_s5_isin_20261127/`): operator ruling (m05395/m05411) made real ISINs authoritative, excluded bare foreign tickers (`DeFi Capital Markets, Inc. Common Stock` / `DCM`), and confirmed `Dalmia Bharat Sugar and Industries` is distinct from cement maker `Dalmia Bharat`. `apply_operator_overrides()` reassigned 4 identifiers (`INE0QNI01012`, `INE499A01024`, `NSE:DCMSRIND`, `NSE:DALMIASUG`), cleared 5 stale conflict statuses; real-ISIN collisions reclassified `same-issuer`. Final live: `conflicts=0`, `same-issuer=17`, `unmatched=7375`, `statuses=7392`; 11 `test_fold_identifiers.py` tests passed; `ruff format/check` clean.
  - **S2 close-out 2026-11-27**: fresh detached staging with scratch JSONLs (no store writes): numeric `candidates=454`, canonical `resolved=68 / tier3_rename=7 / tier3_no_kite=268 / tier3_dead=111`; note-backed untickered `candidates=135`, canonical `resolved=2 / tier3_no_kite=133`. Backup `/mnt/data/tmp/opencode/ticker_hygiene_s2_apply_20261127/`, then `--apply-only --apply` on numeric stage → `applied=68 skipped_moved=0 note_tickers=0/0`; numeric `.BO` 463→386 and `.NS` 32→0. Untickered stage left review-only; no `findata/**` note frontmatter edits.
  - **S1/S9 close-out 2026-11-27**: S1 recorded as done from the already-executed full scriptable-lane refresh (backup `/mnt/data/tmp/opencode/exchange_ticker_backup_20261127T123132/`, `exchange_sync.py --force ... --apply` rc=0, `exchange_listings=52353`, `make snapshot-check` rc=0). S9 cleanup landed: removed unused stale `DEFAULT_DB` (`memory/research.db`) from `helpers/maintenance/fold_identifiers.py` and filtered non-equity rows in `load_exchange_listings()`; 12 `test_fold_identifiers.py` tests passed; live `--check` → `listings=12842 direct_isin=3095 conflicts=0 same_issuer=17 unmatched=7375`, `--apply --persist-status` → `statuses=7392`.
- **Scope decision 2026-11-27**: S6-S8 and S10 deferred by operator (too much low-yield work); reopen only on the named triggers above. S1/S2/S9 are now done; S4/S5/S12/S13/S14-S18 are done or substantially done. S11 remains documented no-go/external.
