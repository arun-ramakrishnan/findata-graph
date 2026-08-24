# Proposal: Google-Finance-assisted ticker resolution & data fallback

**Date**: 2026-08-24
**Status**: CLOSED 2026-08-25 — F1–F3 landed; F4 absorbed and executed
inside `market_data_resolution.md` (S1–S4 + R1 hardening, all applied;
user ran manual steps incl. qa). Success criteria met or superseded:
Srigee-class companies carry metrics with `googlefinance:` provenance
(entity_gf_map), ticker_issues shrank from 33 to a classified residue,
terminal classifications stop re-probing dead ends. Archived to
archive/pipeline/ together with the absorbing proposal.
**Depends on**: `enrich_relations.py` ticker-hygiene report (E2, live);
`relations_report.txt` `[ticker_issues]`; the 147 deliberately-unlisted
entities. Independent of Relations-2.0 slices E3–E5.
**Context**: yfinance remains the primary market-data source (§6.0 of
relation_enrichment_sources.md). This proposal adds Google Finance (GF)
as a *fallback* resolver and data source for exactly those companies where
yfinance fails.

---

## 1. Problem statement

Every enrichment sweep leaves a residue that yfinance cannot serve:

- Measured 2026-08-24 (`relations_report.txt`): **33 dead tickers** out of
  916 (renames, demergers, numeric-BOM listings, foreign duals).
- Plus **147 deliberately-unlisted** entities (frontmatter
  ``ticker: null``) with no market data at all.

A subset of these EXISTS with rich data on Google Finance, which covers
NSE and BSE (incl. numeric-only BOM codes Yahoo lacks): verified today,
``google.com/finance/beta/quote/544399:BOM`` serves full stats for Srigee
DLM (₹79.10, Mkt cap 501.78M, P/E 7.30, 52-wk range) whose Yahoo tickers
(SRIGEE.NS and 544399.BO) both fail. Today these companies silently starve.

## 2. Measured capability assessment (probed 2026-08-24)

| Capability | Probe result | Verdict |
|---|---|---|
| Quote page data | beta quote pages embed parseable payloads (`AF_initDataCallback` blocks): price/OHLC, Mkt cap, P/E, EPS, 52-wk range, volume | **strong** |
| Company profile ("About") | sector (Google KG taxonomy), employees, founded, HQ, website | useful for note profiles |
| Financials | income/balance/cash-flow tables (quarterly+annual) in payload blocks | future use |
| Peers / competitors / ownership | **absent** on quote pages | zero value for relation edges |
| Dead-ticker signal | bogus slugs return HTTP 200 shell pages — must verify by parsing content ("About <name>" + P/E presence), not status codes | handled |
| Name -> slug discovery | old `finance/match` endpoint 404s; internal batchexecute RPCs reject guessed rpcids; web-search scraping consent-walled (0 finance URLs in 91KB HTML); wrong-exchange slugs are NOT redirected | **hard problem**, §5 tiers |
| Library | none maintained (official API shut down 2012); PyPI wrappers target dead endpoints | self-built thin parser |

ToS posture: same gray zone that rejected Screener.in scraping (§3 of
relation_enrichment_sources.md). User explicitly directs proceeding for
this narrow fallback use. Mitigations: low volume (only failures + opt-in
unlisted set, ~40-180 pages per sweep, not 900+), permanent local cache,
polite rate limiting, distinct provenance everywhere.

## 3. Goals / non-goals

### Goals
- G1. For every yfinance failure, attempt GF resolution automatically and
  record the outcome (resolved-slug / mapped-back-to-yahoo / still-dead).
- G2. Where GF has data but Yahoo doesn't (Srigee case): fetch core stats
  (mcap, P/E, EPS, price, 52-wk) into `company_metrics` with distinct
  provenance `source_ref='googlefinance:<slug>'`.
- G3. Persist resolved GF slugs durably (DB), so resolution happens ONCE
  per company, not every sweep.
- G4. Where GF resolves to a working Yahoo-format symbol (TMPV.NS case),
  write it back to `entities.ticker` + frontmatter (same contract as the
  2026-08-24 one-time uplift) — turning GF hits into permanent yfinance
  coverage.
- G5. House doctrine: read-only fetch, dry-run parity, prefix-scoped
  writes, report-file transparency.

### Non-goals
- No relation edges from GF (no peer/group/ownership data exists there).
- No replacement of yfinance as primary; GF is strictly fallback.
- No LLM/HTML-diff scraping beyond the structured payload blocks.

## 4. Design

### 4.1 Resolution tiers (per failed company)

1. **Slug variants from context**: try ``<SYM>:NSE``, ``<SYM>:BOM``,
   ``<stem>:BOM`` swaps of the failing Yahoo symbol; verify by content
   (page must contain "About <fuzzy-matched company name>" AND stat rows).
2. **Exchange autocomplete** (measured 2026-08-25): NSE's official
   search API is Akamai-hard-blocked from this network (403 +
   bot-manager on warmup, full browser headers don't help). BSE's own
   PeerSmartSearch (`api.bseindia.com/BseIndiaAPI/api/PeerSmartSearch/w`,
   the autocomplete behind bseindia.com) works and resolves company NAME
   -> scrip code + NSE symbol + ISIN + listed name in one row (the TMPV
   row carries both `500570` and `TMPV`) — so BSE covers both
   exchanges. Implemented as `helpers/maintenance/exchange_search.py`;
   substring noise ('gati' matches JAIN IRRIGATION rows) is exactly why
   the About-name verification stays mandatory.

   **FinnHub probe (2026-08-25, free tier)**: `symbol_lookup` is a
   legitimate-API name->Yahoo-ticker resolver that covers India —
   found `544399.BO` (Srigee), `TMPV.NS` + `TMCV.NS` (demerger twins),
   `PEL.NS` (fixes our failing PIEIL.NS), `HBI` — but Indian quotes/
   profiles/symbol-masters are premium-only (403), search q is capped
   ~20 chars, and name noise exists too ('Gati' -> navigation
   companies). Role: an additional tier-2 DISCOVERY source feeding the
   same verification+writeback machinery; NOT a metrics source.
   Token at gitignored `memory/finnhub_api.key`; `finnhub-python`
   installed venv-only.
3. **Curated overrides**: a small DB table (not a hand-edited workflow
   file) seeded from one-time human mappings; consulted first, extended
   only via explicit apply commands.

### 4.2 New module: `helpers/maintenance/googlefinance.py`

Thin client: slug page fetch -> parse `AF_initDataCallback` blocks ->
typed dict {price, mcap, pe, eps, wk52_high, wk52_low, about_name,
sector, employees, ...}. ~100 lines; payload keys are internal and may
drift — parser asserts expected shapes and degrades loudly to the report.

### 4.3 Storage

- ``entity_gf_map`` table: entity_name, gf_slug, kind ('yahoo_mapped_back'
  | 'gf_only'), resolved_at, verified_name. Prefix-scoped deletes N/A
  (no edges written).
- Metrics rows: existing ``company_metrics`` schema, ``source_ref =
  'googlefinance:<slug>:<metric>'``, properties carry fetched_at + slug.
- Yahoo mapped-back tickers: written like the one-time uplift (entities +
  frontmatter), reported explicitly.

### 4.4 Driver integration

    make relations-enrich ARGS="--source googlefinance --dry-run"
    # operates on: [ticker_issues] from last report + --include-unlisted
    # flow: load targets -> tiered resolution -> dry-run table
    #       (entity | outcome | slug | sample data) -> --apply writes

Runs AFTER the yfinance pass consumes its report; ordering documented in
Makefile help.

## 5. Implementation slices

| Slice | Content | Gate |
|---|---|---|
| F1 ✅ | `googlefinance.py` parser + fixture tests (cached HTML fixtures) | pytest |
| F2 ✅ | Resolution tiers 1 (+3 read) with verification; report section; `--source googlefinance` dry-run (`--apply` rejected until F4); tier-1 hits are gf-only by construction — yahoo-candidates need tier 2/curation | pytest + live dry-run |
| F3 ✅ | Tier 2 (BSE PeerSmartSearch; NSE blocked — see §4.1) behind `--tier2`, serves unlisted targets too. Live 2026-08-25: 10/33 resolved (Srigee 544399:BOM ✓, TMPV case ✓), Gati correctly unverified | pytest + live dry-run |
| F4 → S3 | Metrics write path (`--apply`, source_ref-provenanced) + mapped-back ticker writeback (verify yahoo-candidates against yfinance before writing). Discovered in F2: the current price is NOT a label row — header-block extraction must be added to the parser here (G2 needs it; mcap/P/E/EPS/52-wk already parse). Metrics source per user direction 2026-08-25: GOOGLEFINANCE via gspread service account — **mechanism live-proven 2026-08-25** on the 'Search Test' sheet (key at gitignored `memory/goog_svc_account.json`, principal `search@gen-lang-client-0978107453.iam.gserviceaccount.com`): batch formula write needs `raw=False` (USER_ENTERED — the gspread default stores formulas as literal text), evaluation is instant, and `BOM:544399` returns price/marketcap/PE **matching the GF web parser to the decimal** (501782400 / 7.3); `NSE:SRIGEE` is #N/A exactly as tier-1 predicted (scrip-only listing). API-key route is read-only (401 "API keys are not supported by this API") — service account is mandatory. **Scope absorbed 2026-08-25 into the combined proposal** `market_data_resolution.md` slice S3 (FinnHub discovery → yfinance bulk → GF fallback); see that doc | pytest + parity test |

## 6. Risks

- **Payload drift**: internal ds-N block shapes change without notice ->
  loud assertion failures + cache makes debugging cheap; parser pinned to
  structural checks, not positional indexes.
- **Rate limiting**: bounded target set + permanent cache + >=1s delay.
- **Wrong-entity attribution**: verification requires fuzzy name match;
  misses land in report, never auto-applied (Tier-C discipline).
- **ToS**: accepted by user decision for this narrow fallback; revisit if
  Google blocks (degrade gracefully: resolution simply reports unknown).

## 7. Success criteria

- Srigee DLM case: GF slug 544399:BOM discovered-or-confirmed, stats in
  company_metrics with googlefinance provenance.
- Of the current 33 ticker_issues: >=50% resolved (mapped back to working
  Yahoo symbols OR gf_only with metrics), remainder explicitly classified.
- Zero unverified auto-writes: every applied change traceable to a
  verified About-name match or curated override.
- Re-running sweeps shrinks the failure list monotonically (resolutions
  persist).

## 8. Open questions — RESOLVED by user review 2026-08-24

1. Unlisted entities: → **explicit ``--include-unlisted`` flag only**
   (never swept by default).
2. Metrics cadence: → **only-when-missing**; a ``--refresh-gf`` force flag
   can come later if needed.
3. Mapped-back Yahoo tickers: → **yes, invalidate/extend the persistent
   fetch cache** so new tickers enter the next yfinance sweep (same
   gotcha as the 2026-08-24 one-time uplift).

Green light for F1.
