---
title: "Price lane + graph alpha research — as-of substrate, OHLCV ingest, event studies, one measured hypothesis"
status: executed
filed: "2026-10-31"
executed: "2026-10-31"
completed_md: "461"
area: "helpers/graph (as-of projection) + helpers/maintenance/price_sync.py + memory/data/price_history.parquet + doc/design/data_sources.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Price lane + graph alpha research — as-of substrate, OHLCV ingest, event studies, one measured hypothesis

**Date:** 2026-10-31 · **Status:** EXECUTED (#461) ·
**Area:** `helpers/graph/` (as-of projection), `helpers/maintenance/price_sync.py`,
`memory/data/price_history.parquet`, `doc/design/data_sources.md`

## 1. Motivation

The graph is descriptive-only. 13 hyperedge families, 19+ served metrics,
8,414 dated edges — and **zero market data anywhere in the estate** to test
any of it against. The identifiers are fully solved (6,728 ISINs: 4,128 BSE
+ 2,600 NSE via `entity_identifiers`), the data-source register documents
both price paths (Yahoo lane; Kite Connect v3 with historical-candles as the
exchange-grade successor), and `memory/data/*.parquet` is the established
sidecar convention — but no price-history lane was ever built. Consequence:
every "needs a consumer" deferral in the graph register (link prediction,
supplier concentration, RPT-disclosure density, backbone fragility) has no
way to ever pay for itself.

This proposal adds the market dimension in four slices: the as-of substrate
that makes the graph point-in-time queryable (S0), the OHLCV ingest lane
(S1), the entity-day join that connects the two (S2), and — the point of it
all — an event-study harness plus ONE graph hypothesis measured end-to-end
against realized prices (S3/S4). Siblings B (multilingual transcript ingest)
and D (MCP server) are parked to `pending.md`, out of scope here.

## 2. Evidence (measured 2026-10-31, live store)

| Fact | Value | Source |
|---|---|---|
| Dated edges | 8,414 of 69,831 carry `valid_from` | `graph_edges` census |
| Listed-universe identifiers | 6,728 ISINs (4,128 BSE, 2,600 NSE; overlap expected) | `entity_identifiers` GROUP BY |
| Company entities | 22,344 (listed subset is the price-joinable one) | `entities` census |
| Price history in estate | **none** — `memory/data/` holds no OHLCV; yfinance touched only for enrichment (market cap etc.) | data_sources.md + file census |
| Documented price sources | Yahoo lane (unauthed, daily); Kite Connect v3 historical-candles (keys in `memory/.env`, access token rotates daily) | data_sources.md:149 |
| `points_and_figures` | a Zerodha *newsletter* series in the vault, not a charting lane — the register's "Yahoo price lane (PnF)" note names the future source, no store exists | vault census |
| Event substrate for S3 | merger/demerger hyperedges (valid_from stamped), `acquired` edges, guidance events, triage decisions — all dated | hyper_edges / graph_edges |
| Graph timestamp discipline | `graph_analytics.computed_at`, hyper `valid_from`, S3 as_of pagerank lane already exist (as_of = pagerank only) | algorithms.py / query.py |

## 3. Design

Smallest-risk first; S0–S2 are substrate, S3–S4 are the research payoff.

### S0 — graph-as-of substrate (read-only)

`helpers/graph/as_of.py`: point-in-time projections over `graph_edges` —
an edge is visible at date D iff (`valid_from` IS NULL OR `valid_from` <= D)
AND (`valid_to` IS NULL OR `valid_to` > D). Provenance-bound edges (RPT
filings carry filing-period dates) follow the same rule. CLI:
`--date D --edge-type T --summary` (counts per type) + a Python API
returning the filtered edge list, mirroring `edge_projection.edge_rows`'
contract (ex-index noise filter reusable). Tests: hand-built tmp DB where
an edge expires mid-range (visible before, invisible after), plus an
as-of vs live differential on the real store (expect 8,414 − expired).
NOT in S0: metric re-computation as-of (S3 consumes the substrate
directly); DuckDB stamp integration; hyper_edges as-of (they carry
`valid_from` too — include the same filter, no consumer yet).

### S1 — weekly OHLCV backfill (one-shot, no recurring sync)

`helpers/maintenance/price_sync.py`: WEEKLY bars (open/high/low/close/
volume) over a 5-year window — the operator's call (2026-10-31): weekly
signals, then long-horizon analysis; **no daily feed, no charting, no
recurring sync lane**. Symbols come ready-made: 6,081 entities carry
Yahoo-suffixed tickers (`SBIN.NS`, `MRF.NS`, `.BO` for BSE) written by
the enrichment lane — no ISIN gymnastics. Output:
`memory/data/price_history_weekly.parquet` (zstd, git-managed house
sidecar; ~6.1k symbols × ~260 weeks ≈ 1.6M rows). Modes: `build`
(one-shot bulk backfill, batched `yf.download` with threads, jitter,
per-batch retry, failures listed) / `coverage` (per-ticker row census).
A `--since` incremental top-up exists for when a study needs newer
weeks — refresh is a research action, never a scheduled lane. The
register gains the endpoint row (D15).

### S2 — entity-day join + coverage census

A DuckDB view/SQL recipe joining `price_history` → `entities` via
ISIN, plus a coverage report: how many graph companies are price-joinable,
per-exchange split, history depth distribution, and the survivorship
caveat written down (delisted names absent from yfinance's chart API —
the join is survivor-biased; recorded in the report, not fixed —
delisted-data sourcing is a separate lane if a study needs it). Surfaced
as a `stats.py` advisory line + a `data_sources.md` census snapshot.

### S3 — long-horizon outcome windows (report-only)

`helpers/research/event_study.py`: dated graph events (S0 substrate:
`acquired` edges, merger/demerger hyperedges, guidance events — each
carrying `valid_from`) → LONG-HORIZON windows against weekly bars:
[T−4w, T+26w] style horizons, outcome = forward 1–2 quarter relative
return vs sector peers (sector from the canonical incidence store).
Report-only, no served metric, no gate. The harness is the reusable
shape; the event list is data, not code.

### S4 — ONE graph hypothesis measured end-to-end

Pre-registered (house bank discipline): **link-prediction precision vs
realized `acquired` edges** — the persisted `link_prediction` candidate
lists (jaccard, dated generations exist since 2026-09-24) checked
against `acquired` edges whose `valid_from` post-dates each generation's
`computed_at`: did top-K candidates realize? This needs NO new data
beyond S1–S2 (both sides already stored), gives link prediction its
first precision-against-ground-truth number, and establishes the
backtest pattern (as-of correctness: candidates must be scored on
pre-event data only — S0's substrate enforces it). Alternative
hypotheses (supplier-concentration → returns; RPT-density → drawdowns)
are recorded in the proposal as follow-ons, not run here. GO/NO-GO on
the number itself is a product question — the slice delivers the
measurement with a frozen pre-registration note.

## 4. Acceptance criteria & shakedown

1. **S0**: tmp-DB expiry tests green; live differential prints the
   as-of vs live edge delta; `--summary` counts match a hand SQL.
2. **S1**: `price_history_weekly.parquet` builds for ≥90% of the
   6,081 tickered entities (Yahoo coverage is the honest ceiling;
   per-symbol failures listed, not fatal); weekly frequency, 5y window;
   data_sources.md D15 row written; NO recurring sync scheduled.
3. **S2**: coverage census printed with per-exchange split + survivorship
   caveat; stats advisory line green on a DB without the parquet
   (clean skip, never fail).
4. **S3**: event-study table for ≥2 event types with N ≥ 10 each on the
   live store; windows respect the as-of substrate (a post-dated edge
   never leaks into an earlier window — pinned by test).
5. **S4**: pre-registration note committed BEFORE the score runs (bank
   discipline); precision number delivered with N and CI; no served
   surface changes.
6. `make static_checks` + md-lint green; `make search-fresh APPLY=1`
   (doc/ changes) then plain rc=0; NO `make qa`-scale runs mid-arc
   (targeted only, per gates doctrine).

## 5. Risks

| Risk | Mitigation |
|---|---|
| yfinance rate limits / bulk flakiness | batched download, jitter, resume; failures listed per-symbol; Kite Connect documented as the exchange-grade successor if Yahoo proves inadequate |
| Survivorship bias in every study | written into the S2 census and every S3/S4 report header; delisted sourcing is a separate lane |
| Lookahead leakage (the classic backtest bug) | S0's as-of substrate is the only event-source; a test pins that post-dated edges never enter earlier windows |
| Parquet growth (git-managed) | daily-delta update mode; ~1.2M rows/yr ≈ tens of MB zstd; compaction = rewrite, same as other sidecars |
| Scope creep into "quant platform" | S4 delivers ONE hypothesis; follow-ons recorded, not built |

## 6. Non-goals

- No served API metric, no `graph_analytics` writes, no UI surface
  (report lanes only; serving is a product decision for later).
- No Kite Connect integration (documented successor, keys exist; Yahoo
  first — unauthed and sufficient for daily bars).
- No intraday/order-book data; no derivatives.
- No multilingual transcript ingest (sibling B, parked) and no MCP
  surface (sibling D, parked).
- No portfolio construction, execution, or live-trading anything.

## 7. Execution record (2026-10-31)

All four slices executed the day the proposal was filed; S1 re-scoped
mid-arc per operator ruling (weekly bars, 5y, no daily feed, no
recurring sync — "I don't intend to do any daily charting work").

| Slice | Result (measured) |
|---|---|
| S0 as-of substrate | `helpers/graph/as_of.py` — point-in-time edge visibility (`valid_from <= D < valid_to`), narrow-schema tolerances mirroring edge_projection; live differential: 62,570 of 69,831 edges visible at 2026-06-30, 7,261 not-yet-born, 0 expired. 5/5 tests incl. boundary exclusivity |
| S1 weekly OHLCV | `helpers/maintenance/price_sync.py` → `memory/data/price_history_weekly.parquet`: **1,172,762 rows, 5,486 symbols (90.2% of the 6,080 tickered entities), 2021-10-25..2026-10-30**, median 261 weeks/symbol. `--fill-missing` recovery mode added after Yahoo 429 bursts (3 passes: 3,393 → 4,323 → 5,486); the persistent 594 are Yahoo-absent legacy numeric `.BO` codes (survivorship caveat, repeat-verified) |
| S2 join + census | `helpers/research/price_join.py` — 5,487/6,081 price-joinable (2,922 BSE / 2,565 NSE; 3,857 full-5y); advisory line in `make graph-stats`; D15 register entry in `data_sources.md` |
| S3 event studies | `helpers/research/event_study.py` — as-of-guarded, sector-baseline abnormal returns. Live: **acquirers −7.2% median abnormal at 4w** (N=13, 31% positive; volume −30%), converging to +1.3% by 26w — the classic deal-friction signature; `invested_in` filings ≈ no signal (−0.3..−4.5%, ~50% positive — anticipated disclosures). `listed_on_index` dates proven ingest-stamps (4,419 on one day) and excluded — the lookahead trap named and avoided |
| S4 pre-registration | `doc/local/evaluations/linkpred_precision_prereg.md` + frozen snapshot (`bench_data/alpha_research/linkpred_snapshot.parquet`, 10,630 rows) + `helpers/research/linkpred_precision.py`. Registration run: **0 measurable deals** (24 circular-excluded by construction, 6 no-acquirer-row); accumulates per recompute cycle under the pre-registered decision rule |

10 targeted tests green; static_checks + md-lint clean; search-fresh
converged. Siblings B (transcript ingest) and D (MCP) remain parked in
`pending.md`.
