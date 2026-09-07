---
title: "get_ticker_fixes — per-property Yahoo guards + deprecated endpoint replacements"
status: executed
filed: "2026-09-06"
executed: "2026-09-07"
completed_md: "212"
area: "helpers/core/get_tickers.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# get_ticker_fixes

**Date:** 2026-09-06 · **Status:** PROPOSED · **Mode:** filed first,
implement later (operator decision 2026-09-06).
**Area:** get_tickers Yahoo fetch (`get_comprehensive_company_data`) +
deprecated yfinance endpoints.
**Trigger:** `--detailed HDFCBANK.NS` printed `HTTP Error 404:
{"quoteSummary": ... "No fundamentals data found for symbol:
HDFCBANK.NS"}` plus a yfinance `DeprecationWarning: 'Ticker.earnings'
is deprecated` — and rendered no fundamentals sections at all.

## 1. Motivation

Two independent defects, one visible failure:

- **All-or-nothing fetch.** `get_comprehensive_company_data`
  (`get_tickers.py:119`) pulls ~15 yfinance properties, but only six
  are guarded (`earnings`, `quarterly_earnings`,
  `recommendations_summary`, `calendar`, `isin`, `options`). The core
  nine — `info`, `history`, all six financial statements,
  `recommendations`, both holders frames, `sustainability` — are
  unguarded: one 404 anywhere aborts the whole dict via the outer
  `except` (`:208`), and `--detailed` silently drops every section.
- **Dead endpoints.** yfinance 1.7.0 (pinned in `.venv`):
  `Ticker.earnings` warns and returns `None` unconditionally
  (`scrapers/fundamentals.py:32` — "not available via API"); and the
  code reads `fund_holders`, which does not exist in 1.7.0 (the
  property is `mutualfund_holders`, `ticker.py:121`) — so the
  `getattr(..., "fund_holders", None)` fallback resolves to `None`
  on every run, always.

404 verdict (operator-confirmed, recorded so it is not re-litigated):
this is Yahoo data absence, not rate limiting — no 429/crumb error;
the quoteSummary body names the symbol. Retrying later may succeed,
but the client must still degrade per-property.

## 2. Evidence (2026-09-06, yfinance 1.7.0 in `.venv`)

| Access in code | yfinance 1.7.0 status | Today |
|---|---|---|
| `info`, `history`, 6× statements, `recommendations`, 2× holders, `sustainability` | live | UNGUARDED — one failure kills all 15 |
| `earnings` | hard-deprecated: warns + returns None always | guarded, but warning noise on every `--detailed` run |
| `quarterly_earnings` | DEAD too (corrected 2026-09-07 after live smoke): `ticker.py:197` routes to `base.get_earnings`, which reads the same deprecated `_fundamentals.earnings` property (`base.py:386`) — warns, returns None, always | derive both from Net Income rows |
| `recommendations_summary`, `calendar`, `isin`, `options` | live | guarded — keep |
| `fund_holders` (getattr) | does not exist → always None | replace with `mutualfund_holders` (+ old-name fallback) |

## 3. Design

Three slices, independently landable (S3 needs S1's guard helper).

**S1 — per-property guards, partial data renders.**
A `_yf(ticker_obj, name, default=None)` helper: `getattr` + call
inside try/except, one stderr warning per failed property (names the
property + symbol, so a 404 stays visible instead of silent), returns
the default otherwise. Apply to all nine unguarded accesses; the six
already-guarded ones route through the same helper for one uniform
shape. The returned dict keeps today's keys (all present, `None`
where unavailable). Verify each `_print_*_section` is already
None-tolerant before wiring (they check `is not None`/`empty` —
confirm, don't assume).

**S2 — endpoint replacements.**
- `earnings` + `quarterly_earnings` → derive locally from
  `income_stmt` / `quarterly_income_stmt` (`_net_income_series`:
  the "Net Income" row, the exact replacement the deprecation
  message names). Kills the DeprecationWarning at the source. Note
  the trap the live smoke caught: `quarterly_earnings` LOOKS live
  (`ticker.py:197`) but funnels into the dead property one level
  down (`base.py:386`) — never call either. Statements themselves
  stay S1-guarded, so absence still yields None, not a crash.
- yfinance's own logger (`'yfinance'`, ERROR) prints every swallowed
  HTTP error raw (the bare 404 line in the smoke) with no property
  context. Set to CRITICAL at get_tickers import: our `_yf()`
  warnings already report real failures with property + symbol,
  and empties are normal. stdlib-only, safe under test stubs.
- `fund_holders` → `getattr` chain: `mutualfund_holders` first,
  `fund_holders` fallback (older yfinance), None last.
- `recommendations_summary`, `calendar`, `isin`, `options`:
  no change (live + guarded).

**S3 — capture dividends + splits.**
`get_actions` (`scrapers/history.py:728`) merges the `Dividends` +
`Stock Splits` (+ `Capital Gains`, `Dividends FX`) columns of the
same history cache — so for NSE names `actions` is a strict superset
of the two Series plus fund-only columns. Fetch `dividends` +
`splits` only (lighter Series, no dup), skip `actions`. Both route
through S1's `_yf()` guard (a symbol with no payouts returns empty,
never an error) plus one `_print_dividends_splits_section`
(recent-N rows, tail format matching the recommendations section).
Estimate-family fields (`analyst_price_targets`, `earnings_dates`,
`upgrades_downgrades`, `*_estimate`, `eps_*`, `growth_estimates`)
deliberately excluded: Yahoo coverage for Indian names is thin —
revisit only with a coverage survey, not on assumption.

## 4. Acceptance criteria & shakedown

1. Stubbed-ticker tests (no network): a FakeTicker whose one
   property raises `urllib.error.HTTPError(404)` renders the other
   fourteen sections; the stderr warning names property + symbol.
2. `earnings` derivation test: income_stmt fixture with a Net Income
   row → `_net_income_series` equals that row (annual + quarterly);
   statements-None → earnings None, no warning raised.
3. `fund_holders` test: resolves via `mutualfund_holders` on 1.7.0;
   DeprecationWarning filter set to error — full `--detailed` stub
   run emits none.
4. Live smoke (operator-run, network): concise + detailed for one
   NSE symbol; a 404 on any module degrades to that section, never
   to empty output.
5. S3: stubbed dividends/splits Series render recent-N rows;
   empty Series → section skipped silently (no-payout symbols are
   normal, not warnings).
6. Existing suites green: test_get_tickers (+ new), fuzz unchanged.
7. Full gate sequence (qa / advisory / perf / search-fresh) ONCE at
   arc end, with the operator's go — house directive 2026-09-04.

## 5. Risks

- **Warning spam on chronic-404 symbols** — one line per failed
  property per run; accepted (visibility beats silence; `--detailed`
  is interactive, not a loop).
- **Net Income row label drift** — Yahoo renames statement rows
  occasionally; `_net_income_series` tries "Net Income" then common
  variants, else None. Pinned by the derivation test on a fixture.
- **yfinance major-version drift** — replacements target the pinned
  1.7.0; the getattr chains degrade gracefully on both sides.

## 6. Non-goals

- Retries/backoff for Yahoo 404s (data absence, not transient —
  retrying a "no fundamentals" answer is wasted traffic).
- yfinance upgrade (maintenance call, separate arc).
- **DuckDB / sqlite-vec in the ticker CLI — considered and
  rejected:** (a) nothing to gain — the S3 run index serves the
  1079×384 scan in 0.7ms numpy; KNN would be equally fast but
  heavier; (b) the CLI is deliberately import-light for minimal
  envs — DuckDB adds cold-start weight and a hard native dep;
  (c) sqlite-vec needs a loadable `.so` and fails in some
  embeddings (the spellfix1/DuckDB-catalog precedent cited in
  `main()`); a best-effort fallback must not depend on one;
  (d) a vec table cannot live in research.db anyway — DuckDB's
  SQLite scanner chokes on extension virtual tables on ATTACH
  (`vec_search.py` module docstring) — so the CLI would need
  two-database handling for zero benefit. Do not re-litigate.
