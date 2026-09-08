#!/usr/bin/env python3
"""
Look up company tickers via Yahoo Finance and display company data.

Usage:
    get_tickers.py TCS.NS RELIANCE.NS INFY.NS
    get_tickers.py --detailed TCS.NS
"""

import yfinance as yf
import logging
import os
import sys
import argparse
import ast
from datetime import datetime
from pathlib import Path

# Ensure the repo root is importable when this script is run as a subprocess.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import fuzzy_match for entity resolution (package-qualified; the bare
# `from fuzzy_match import ...` used before only worked when CWD was
# helpers/core/, silently breaking word_overlap_match in search_ticker).
from helpers.core.fuzzy_match import fuzzy_match, build_spellfix_table, word_overlap_match
from helpers.core.db import connect

_FUZZY_AVAILABLE = True

# yfinance logs every swallowed HTTP error (404 data absence included) to
# its own 'yfinance' logger at ERROR (scrapers/quote.py _fetch) — raw
# one-liners with no property context. Our _yf() guard already reports
# real failures with property + symbol; empties are normal (skipped
# silently by the display layer). Keep the library logger quiet so
# --detailed stderr carries signal, not Yahoo's data gaps. stdlib-only,
# safe under test monkeypatching (no yf import involved).
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Name-matching helpers (pure, testable)
# ---------------------------------------------------------------------------


def extract_company_name_from_path(file_path):
    """Extract company name from file path by taking the filename without extension"""
    filename = os.path.basename(file_path)
    company_name = os.path.splitext(filename)[0]
    # Replace underscores with spaces for searching
    return company_name.replace("_", " ")


# Indian-exchange suffixes preferred for results ordering.
_INDIAN_SUFFIXES = (".NS", ".BO")


def search_ticker(company_name):
    """Search for a company ticker using Yahoo Finance.

    Single-pass ``yf.Search`` \u2192 validate via ``word_overlap_match`` \u2192 fetch
    ``.info`` once.  Returns ``(ticker, info_dict)`` or ``(None, None)``.

    The old implementation had three phases \u2014 a search phase (broken by an
    import bug), an exchange-suffix guessing phase (constructed wrong symbols
    like ``RELIANCEINDUSTRIES.NS`` \u2192 always 404), and a ``requests`` fallback
    (unreachable).  All three are replaced by this single-pass search.
    """
    try:
        search_obj = yf.Search(company_name, timeout=5)
        quotes = search_obj.quotes
    except Exception as e:
        print(f"Error searching for {company_name}: {e}")
        return None, None

    if not quotes:
        return None, None

    # Collect candidate matches that pass word-overlap validation.
    candidates = []
    for quote in quotes:
        sym = quote.get("symbol")
        if not sym:
            continue
        short_name = quote.get("shortname", "")
        long_name = quote.get("longname", "")
        wo_match, _ = word_overlap_match(company_name, [short_name, long_name])
        if wo_match:
            candidates.append(sym)

    if not candidates:
        return None, None

    # Prefer Indian exchanges (.NS/.BO); keep original search-result order otherwise.
    order = {s: i for i, s in enumerate(candidates)}
    candidates.sort(key=lambda s: (0 if s.endswith(_INDIAN_SUFFIXES) else 1, order[s]))

    # Fetch .info only for the top candidate (1 HTTP request instead of N).
    for sym in candidates:
        try:
            info = yf.Ticker(sym).info
            if info and "longName" in info:
                return sym, info
        except Exception:  # noqa: S112  # best-effort; skip item on failure
            continue

    return None, None


# ---------------------------------------------------------------------------
# Direct symbol lookup (new — command-line symbols)
# ---------------------------------------------------------------------------


def get_basic_info(symbol):
    """Return basic info dict for a ticker symbol, or None on failure."""
    try:
        info = yf.Ticker(symbol).info
        if not info or "longName" not in info:
            return None
        return info
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None


def _yf(ticker_obj, name, default=None, *args, **kwargs):
    """One guarded yfinance property/method read (get_ticker_fixes S1).

    Returns ``default`` on any failure — 404 data absence, removed
    endpoint, empty-payload raise — plus one stderr line naming the
    property + symbol, so a single dead module degrades instead of
    killing all fifteen sections. Methods are called with args/kwargs;
    plain attributes are returned as-is. Empty frames/Series are NOT
    failures (no-payout symbols are normal) — they pass through for
    the display layer to skip silently.
    """
    symbol = getattr(ticker_obj, "ticker", None) or getattr(ticker_obj, "symbol", "?")
    try:
        val = getattr(ticker_obj, name, None)
        if val is None:
            return default
        if callable(val):
            val = val(*args, **kwargs)
        return val
    except Exception as e:
        print(f"WARNING: yfinance {name} unavailable for {symbol}: {e}", file=sys.stderr)
        return default


_NET_INCOME_LABELS = ("Net Income", "NetIncome", "Net income")


def _net_income_series(ticker_obj, quarterly=False):
    """Net Income row from the income statement (get_ticker_fixes S2).

    Replaces the dead ``Ticker.earnings`` (yfinance 1.7.0 warns and
    returns None unconditionally) with the exact replacement the
    deprecation message names. None when the statements themselves are
    unavailable — absence still yields None, never a crash.
    """
    stmt = _yf(ticker_obj, "quarterly_income_stmt" if quarterly else "income_stmt")
    if stmt is None:
        return None
    try:
        for label in _NET_INCOME_LABELS:
            if label in stmt.index:
                return stmt.loc[label]
    except Exception:
        return None
    return None


def _fund_holders_frame(ticker_obj):
    """Mutual-fund holders across yfinance versions (get_ticker_fixes S2).

    1.7.0 renamed the property to ``mutualfund_holders`` — the old
    ``fund_holders`` getattr resolved to None on every run. New name
    first, old name as fallback, None last.
    """
    frame = _yf(ticker_obj, "mutualfund_holders")
    if frame is None:
        frame = _yf(ticker_obj, "fund_holders")
    return frame


def get_comprehensive_company_data(ticker):
    """Get comprehensive company data using yfinance.

    Per-property guarded (get_ticker_fixes S1): each module degrades to
    None independently — a 404 on one never kills the other fourteen.
    Only a failure to construct the Ticker itself is total (as before).
    """
    try:
        ticker_obj = yf.Ticker(ticker)
    except Exception as e:
        print(f"Error getting comprehensive data for {ticker}: {str(e)}")
        return None

    try:
        # Get historical market data (last 10 days)
        hist = _yf(ticker_obj, "history", period="10d")

        # Get financials
        financials = {
            "income_stmt": _yf(ticker_obj, "income_stmt"),
            "quarterly_income_stmt": _yf(ticker_obj, "quarterly_income_stmt"),
            "balance_sheet": _yf(ticker_obj, "balance_sheet"),
            "quarterly_balance_sheet": _yf(ticker_obj, "quarterly_balance_sheet"),
            "cashflow": _yf(ticker_obj, "cashflow"),
            "quarterly_cashflow": _yf(ticker_obj, "quarterly_cashflow"),
        }

        # Get recommendations
        recommendations = _yf(ticker_obj, "recommendations")

        # Get institutional holders
        institutional_holders = _yf(ticker_obj, "institutional_holders")

        # Get major holders
        major_holders = _yf(ticker_obj, "major_holders")

        # Get company sustainability info
        sustainability = _yf(ticker_obj, "sustainability")

        # Annual + quarterly net income, derived from the statements
        # (get_ticker_fixes S2): Ticker.earnings is dead in yfinance
        # 1.7.0 AND quarterly_earnings funnels through the same dead
        # property (base.get_earnings reads _fundamentals.earnings, which
        # warns and returns None) — both are derived, never called.
        earnings = _net_income_series(ticker_obj)
        quarterly_earnings = _net_income_series(ticker_obj, quarterly=True)

        # Get analysts recommendations
        recommendations_summary = _yf(ticker_obj, "recommendations_summary")

        # Get company calendar
        calendar = _yf(ticker_obj, "calendar")

        # Get company ISIN
        isin = _yf(ticker_obj, "isin", default="N/A")

        # Get company options
        options = _yf(ticker_obj, "options")

        # Corporate actions (get_ticker_fixes S3): dividends + splits only.
        # `actions` is a strict superset (same history cache plus
        # fund-only columns) — no dup fetch.
        dividends = _yf(ticker_obj, "dividends")
        splits = _yf(ticker_obj, "splits")

        # Get mutual fund holdings (property renamed in yfinance 1.7.0).
        fund_holders = _fund_holders_frame(ticker_obj)

        return {
            "info": _yf(ticker_obj, "info"),
            "history": hist,
            "financials": financials,
            "recommendations": recommendations,
            "institutional_holders": institutional_holders,
            "major_holders": major_holders,
            "sustainability": sustainability,
            "earnings": earnings,
            "quarterly_earnings": quarterly_earnings,
            "recommendations_summary": recommendations_summary,
            "calendar": calendar,
            "isin": isin,
            "options": options,
            "dividends": dividends,
            "splits": splits,
            "fund_holders": fund_holders,
        }
    except Exception as e:
        print(f"Error getting comprehensive data for {ticker}: {str(e)}")
        return None


# ---------------------------------------------------------------------------
# Entity resolution: match ticker/company-name to known entities
# ---------------------------------------------------------------------------


def _candidate_vec(emb_str, dims):
    """Parse a stored embedding string; None if unparsable or wrong dims."""
    try:
        vec = ast.literal_eval(emb_str)
    except ValueError, SyntaxError, TypeError:
        return None
    if len(vec) != dims:
        return None
    return vec


# derive_render_shared_note_grouping S3 (2026-09-08): the python VSS query
# core lives in helpers/core/vss_index.py — embedder picker, per-call
# decode cache + cosine, and the run-index tier. Re-exported here so the
# public module surface is unchanged (triage/embed_eval/tests and the
# `gt.*` attr access in test_get_tickers keep working).
from helpers.core.vss_index import (  # module-level re-export (E402 via per-file ignore)
    _best_vss_match,  # noqa: F401  # re-export: callers import from this module
    _decoded_vss_table,  # noqa: F401  # re-export
    _pick_embedder,
    _raw_vec,  # noqa: F401  # re-export
    _VssRunIndex,  # noqa: F401  # re-export
    _vss_match_with_index,
    build_vss_run_index,
    check_vss_run_index,
)


def vss_match(
    query,
    entities,
    conn=None,
    db_path=None,
    threshold=0.5,
    embed_fn=None,
    index=None,
):
    """Best-effort vector-similarity fallback for entity resolution.

    Matches a query string (e.g. a Yahoo ``longName``) against known
    entities via cosine similarity over the ``company_embeddings`` table in
    the SQLite source of truth. Returns ``(match_name, score)`` or
    ``(None, 0.0)``.

    The query is embedded with the same embedder that populated the table
    (dry-run pseudo-embeddings by default; the local bge-small model when
    company_embeddings carries its label — see _pick_embedder, which keeps
    query-side and index-side on the same model). For other (API) models no
    local embedder is available, so the stage returns no match unless
    ``embed_fn`` is supplied. The comparison is pure Python — no DuckDB
    dependency, so ``get_tickers`` stays a standalone CLI.

    ``conn``: optional open SQLite connection; if None, one is opened against
    ``db_path`` (default ``memory/research.db``) and closed before returning.
    ``embed_fn(query, dims) -> list[float]``: overrides the default
    pseudo-embedder (used by tests for deterministic control).
    ``index``: optional _VssRunIndex from build_vss_run_index (S3) — skips
    the per-call SELECT/digest/dots for one matvec; the per-call path
    below is unchanged for embed_eval/tests.
    """
    entity_set = set(entities) if entities is not None else None
    if index is not None:
        return _vss_match_with_index(index, query, entity_set, threshold)
    owns = False
    try:
        if conn is None:
            conn = connect(db_path, row_factory=None)
            owns = True

        rows = conn.execute(
            "SELECT company_name, embedding, model FROM company_embeddings"
        ).fetchall()
    except Exception:  # noqa: S110  # best-effort; absent table is a valid no-match
        if owns and conn is not None:
            conn.close()
        return None, 0.0

    if owns and conn is not None:
        conn.close()

    if not rows:
        return None, 0.0

    try:
        embed_fn, dims = _pick_embedder(rows, embed_fn)
    except Exception:  # noqa: S110  # bad first row -> no match
        return None, 0.0
    if embed_fn is None:
        return None, 0.0

    qvec = embed_fn(query, dims)

    best_name, best_score = _best_vss_match(qvec, rows, dims, entity_set)

    if best_name and best_score >= threshold:
        return best_name, best_score
    return None, 0.0


def resolve_entity(ticker, info, entities, spellfix_conn=None, vss_conn=None, index=None):
    """Match a Yahoo Finance result to a known entity via fuzzy_match, with a
    vector-similarity fallback (deferred N5 item — VSS stage)."""
    if not _FUZZY_AVAILABLE or not entities:
        return None, None
    long_name = info.get("longName", "")
    if long_name:
        match, method, score = fuzzy_match(long_name, entities, spellfix_conn=spellfix_conn)
        if match:
            return match, method
    short_name = info.get("shortName", "")
    if short_name:
        match, method, score = fuzzy_match(short_name, entities, spellfix_conn=spellfix_conn)
        if match:
            return match, method

    # VSS fallback (deferred N5 item): embed the Yahoo name and find the
    # nearest known entity above a cosine threshold. Only fires when the
    # heuristics above all miss.
    for candidate in (long_name, short_name):
        if not candidate:
            continue
        match, score = vss_match(candidate, entities, conn=vss_conn, index=index)
        if match:
            return match, "vss"
    return None, None


def load_entities(db_path="memory/research.db"):
    """Load entity names from the SQLite database."""
    try:
        conn = connect(db_path, row_factory=None)
        cursor = conn.execute("SELECT name FROM entities ORDER BY name")
        entities = [r[0] for r in cursor.fetchall()]
        conn.close()
        return entities
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Display helpers (format data for human-readable output)
# ---------------------------------------------------------------------------


def _fmt_number(val, prefix="", suffix=""):
    """Format a number with thousands separator, or return 'N/A'."""
    if val is None:
        return "N/A"
    try:
        return f"{prefix}{val:,.0f}{suffix}"
    except ValueError, TypeError:
        return str(val)


def _fmt_pct(val):
    """Format a decimal as percentage (e.g. 0.045 → 4.50%)."""
    if val is None:
        return "N/A"
    try:
        return f"{val * 100:.2f}%"
    except ValueError, TypeError:
        return str(val)


def _print_header(symbol, info):
    """Print the company header section."""
    exchange = info.get("exchange", "N/A")
    exchange_name = {"NSI": "NSE", "BSE": "BSE"}.get(exchange, exchange)
    currency = info.get("currency", "INR")
    print(f"\n{'=' * 60}")
    print(f" {symbol}  |  {info.get('longName', info.get('shortName', '?'))}")
    print(f" Exchange: {exchange_name} ({exchange})  |  Currency: {currency}")
    print(f"{'=' * 60}")


def _print_basic_section(info):
    """Print key company metrics (always shown)."""
    print("\n--- Basic Info ---")
    print(f"  Sector:        {info.get('sector', 'N/A')}")
    print(f"  Industry:      {info.get('industry', 'N/A')}")
    print(f"  Market Cap:    {_fmt_number(info.get('marketCap'), suffix=' cr')}")
    print(f"  Enterprise Val:{_fmt_number(info.get('enterpriseValue'), suffix=' cr')}")
    print(f"  Employees:     {_fmt_number(info.get('fullTimeEmployees'))}")
    print(f"  Country:       {info.get('country', 'N/A')}, {info.get('city', 'N/A')}")
    print(f"  Website:       {info.get('website', 'N/A')}")


def _print_valuation_section(info):
    """Print valuation / price metrics."""
    print("\n--- Valuation & Price ---")
    print(f"  Previous Close: {info.get('previousClose', 'N/A')}  Open: {info.get('open', 'N/A')}")
    print(f"  Day Range:      {info.get('dayLow', 'N/A')} – {info.get('dayHigh', 'N/A')}")
    print(
        f"  52W Range:      {info.get('fiftyTwoWeekLow', 'N/A')} – {info.get('fiftyTwoWeekHigh', 'N/A')}"
    )
    print(f"  P/E (trailing): {info.get('trailingPE', 'N/A')}")
    print(f"  P/E (forward):  {info.get('forwardPE', 'N/A')}")
    print(f"  P/B:            {info.get('priceToBook', 'N/A')}")
    print(f"  EV/EBITDA:      {info.get('enterpriseToEbitda', 'N/A')}")
    div_rate = info.get("dividendRate")
    div_yield = info.get("dividendYield")
    if div_rate and div_yield:
        print(f"  Dividend:       {div_rate}  ({_fmt_pct(div_yield)})")
    else:
        print("  Dividend:       N/A")


def _print_history_section(hist):
    """Print recent price history."""
    if hist is None or hist.empty:
        return
    print(f"\n--- Price History (last {len(hist)} trading days) ---")
    print(f"  {'Date':12s} {'Close':>10s} {'Volume':>14s} {'Change':>8s}")
    prev_close = None
    for date, row in hist.iterrows():
        close = row["Close"]
        vol = row["Volume"]
        change_str = ""
        if prev_close and prev_close != 0:
            change = (close - prev_close) / prev_close * 100
            change_str = f"{change:+.2f}%"
        print(f"  {str(date.date()):12s} {close:>10,.2f} {vol:>14,.0f} {change_str:>8s}")
        prev_close = close


def _print_financials_section(financials, info):  # noqa: C901
    """Print income statement / balance sheet / cashflow highlights."""

    def _col_label(col):
        """Return a human-readable column label (Timestamp → YYYY-MM-DD, str → as-is)."""
        if hasattr(col, "date"):
            return str(col.date())
        return str(col)

    # Income Statement
    income = financials.get("income_stmt")
    if income is not None and not income.empty:
        print("\n--- Income Statement (Annual) ---")
        latest_col = income.columns[0]
        col_label = _col_label(latest_col)
        for label in ["Total Revenue", "Net Income", "Operating Income", "EBITDA", "Basic EPS"]:
            matches = [idx for idx in income.index if label.lower() in idx.lower()]
            if matches:
                val = income.loc[matches[0], latest_col]
                if hasattr(val, "item"):
                    val = val.item()
                print(f"  {label:25s} {_fmt_number(val)}  ({col_label})")

    # Balance Sheet
    bs = financials.get("balance_sheet")
    if bs is not None and not bs.empty:
        print("\n--- Balance Sheet (Annual) ---")
        latest_col = bs.columns[0]
        col_label = _col_label(latest_col)
        for label in [
            "Total Assets",
            "Total Liabilities Net Minority Interest",
            "Stockholder Equity",
            "Cash And Cash Equivalents",
            "Total Debt",
        ]:
            matches = [idx for idx in bs.index if label.lower() in idx.lower()]
            if matches:
                val = bs.loc[matches[0], latest_col]
                if hasattr(val, "item"):
                    val = val.item()
                print(f"  {label:40s} {_fmt_number(val)}  ({col_label})")

    # Cashflow
    cf = financials.get("cashflow")
    if cf is not None and not cf.empty:
        print("\n--- Cashflow (Annual) ---")
        latest_col = cf.columns[0]
        col_label = _col_label(latest_col)
        for label in ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"]:
            matches = [idx for idx in cf.index if label.lower() in idx.lower()]
            if matches:
                val = cf.loc[matches[0], latest_col]
                if hasattr(val, "item"):
                    val = val.item()
                print(f"  {label:25s} {_fmt_number(val)}  ({col_label})")


def _print_holders_section(major_holders, institutional_holders, fund_holders):
    """Print major / institutional / mutual-fund holders."""
    if major_holders is not None and not major_holders.empty:
        print("\n--- Major Holders ---")
        for idx, row in major_holders.head(5).iterrows():
            # yfinance returns major_holders with a 'Value' column and
            # breakdown names ('insidersPercentHeld', etc.) as the index.
            if "Value" in row.index:
                val = row["Value"]
                if hasattr(val, "item"):
                    val = val.item()
                label = (
                    str(idx)
                    .replace("Percent", " %")
                    .replace("Held", " held")
                    .replace("Count", " count")
                )
                if isinstance(val, float) and val < 1.0:
                    print(f"  {label:35s} {_fmt_pct(val)}")
                else:
                    print(f"  {label:35s} {_fmt_number(val)}")
            else:
                # Fallback for unexpected column shape
                for col in row.index:
                    print(f"  {str(idx):35s} {row[col]}")

    if institutional_holders is not None and not institutional_holders.empty:
        print("\n--- Top Institutional Holders ---")
        for _, row in institutional_holders.head(5).iterrows():
            holder = row.get("Holder", row.get("holder", ""))
            shares = row.get("Shares", row.get("shares", ""))
            value = row.get("Value", row.get("value", ""))
            print(f"  {str(holder):35s} {_fmt_number(shares) if shares else ''}  {value}")


def _print_recommendations_section(recommendations, recommendations_summary):
    """Print analyst recommendations."""
    if recommendations_summary is not None and not recommendations_summary.empty:
        print("\n--- Analyst Recommendations (Summary) ---")
        print(recommendations_summary.to_string(index=False))

    if recommendations is not None and not recommendations.empty:
        recent = recommendations.tail(5)
        if not recent.empty:
            print("\n--- Recent Recommendations ---")
            print(recent.to_string())


def _print_sustainability_section(sustainability):
    """Print ESG / sustainability scores."""
    if sustainability is not None and not sustainability.empty:
        print("\n--- Sustainability (ESG) ---")
        print(sustainability.to_string())


def _print_calendar_section(calendar):
    """Print upcoming earnings / event calendar."""
    if calendar is None:
        return
    if isinstance(calendar, dict):
        print("\n--- Earnings Calendar ---")
        for k, v in calendar.items():
            print(f"  {k}: {v}")
    elif hasattr(calendar, "empty") and not calendar.empty:
        print("\n--- Earnings Calendar ---")
        print(calendar.to_string())


def _print_dividends_splits_section(dividends, splits):
    """Print recent payouts + splits, detailed mode only (get_ticker_fixes S3).

    Empty/missing Series are skipped silently — a no-payout symbol is
    normal, not a warning (the S1 guard already warns on real failures).
    """
    for label, series in (("Dividends", dividends), ("Stock Splits", splits)):
        if series is None:
            continue
        try:
            if hasattr(series, "empty") and series.empty:
                continue
            recent = series.tail(5)
            if hasattr(recent, "empty") and recent.empty:
                continue
        except Exception:  # noqa: S112  # exotic frame shape — skip section, data below still renders
            continue
        print(f"\n--- Recent {label} ---")
        try:
            print(recent.to_string())
        except Exception:
            print(f"  {recent}")


def display_ticker(symbol, detailed=False, entities=None, spellfix_conn=None, index=None):
    """Look up a ticker and print formatted info to stdout.

    With detailed=True, fetches comprehensive data (financials, holders,
    recommendations, etc.). Without it, prints a concise one-liner.
    """
    info = get_basic_info(symbol)
    if info is None:
        print(f"{symbol}: NOT FOUND or lookup failed")
        return None

    # Resolve to known entity in our knowledge graph
    entity_name, match_method = resolve_entity(symbol, info, entities, spellfix_conn, index=index)

    if not detailed:
        # Concise mode: one line
        exchange = info.get("exchange", "N/A")
        exchange_name = {"NSI": "NSE", "BSE": "BSE"}.get(exchange, exchange)
        currency = info.get("currency", "")
        line = (
            f"{symbol:15s} | {info.get('longName', '?'):40s} | {exchange_name:4s} | {currency} | "
        )
        line += f"MktCap: {_fmt_number(info.get('marketCap'))}"
        if entity_name:
            line += f" | Entity: {entity_name}"
        print(line)
        return info

    # Detailed mode
    _print_header(symbol, info)
    if entity_name:
        print(f"  KG Entity: {entity_name}  (matched via {match_method})")
    _print_basic_section(info)
    _print_valuation_section(info)

    # Fetch comprehensive data
    comp = get_comprehensive_company_data(symbol)
    if comp:
        _print_history_section(comp.get("history"))
        _print_financials_section(comp.get("financials", {}), info)
        _print_holders_section(
            comp.get("major_holders"), comp.get("institutional_holders"), comp.get("fund_holders")
        )
        _print_recommendations_section(
            comp.get("recommendations"), comp.get("recommendations_summary")
        )
        _print_sustainability_section(comp.get("sustainability"))
        _print_calendar_section(comp.get("calendar"))
        _print_dividends_splits_section(comp.get("dividends"), comp.get("splits"))

        if comp.get("options"):
            print("\n--- Options Expiry Dates ---")
            print(f"  {', '.join(comp['options'][:8])}")
    print()  # trailing newline
    return info


# ---------------------------------------------------------------------------
# Batch mode (legacy — write tickers into markdown files)
# ---------------------------------------------------------------------------


def add_ticker_to_markdown(file_path, ticker):
    """Add ticker information to the markdown file"""
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    # Split content to separate frontmatter and body
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2]
        else:
            frontmatter = ""
            body = content
    else:
        frontmatter = ""
        body = content

    # Add ticker to frontmatter if not already present
    if "ticker:" not in frontmatter and "stock_symbol:" not in frontmatter:
        if frontmatter:
            # Add ticker to existing frontmatter
            updated_frontmatter = frontmatter.strip() + f"\nticker: '{ticker}'"
            updated_content = f"---\n{updated_frontmatter}\n---{body}"
        else:
            # Create new frontmatter with ticker
            # Extract title from filename
            filename = os.path.basename(file_path)
            company_name = os.path.splitext(filename)[0].replace("_", " ")
            updated_content = f"""---
title: '{company_name}'
type: 'company'
ticker: '{ticker}'
tags:
  - entity_type/company
created: '{datetime.now().strftime("%Y-%m-%d")}'
normalized_name: '{os.path.splitext(filename)[0]}'
permalink: '/companies/{filename.lower()}'
---

{body.lstrip()}"""
    else:
        # Ticker already exists, just return original content
        return content

    # Write updated content back to file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated_content)

    return updated_content


def main(symbols=None, detailed=False):
    """Look up ticker symbols and display company data.

    Loads entities from the database for KG entity resolution.
    """
    if symbols:
        # Load entities for matching
        entities = load_entities() if _FUZZY_AVAILABLE else []
        spellfix_conn = None
        if entities:
            try:
                import sqlite_spellfix

                # Build the spellfix table in an in-memory DB: it must NOT
                # land in memory/research.db (the source of truth), because
                # DuckDB's sqlite-extension catalog scans (e.g.
                # information_schema.columns / duckdb_columns() in
                # tests/test_graph.py) fail to PRAGMA the spellfix1 virtual
                # table when the module isn't loadable in the embedding.
                spellfix_conn = connect(":memory:", row_factory=None)
                spellfix_conn.enable_load_extension(True)
                spellfix_conn.load_extension(sqlite_spellfix.extension_path())
                build_spellfix_table(spellfix_conn, entities)
            except Exception:
                spellfix_conn = None

        # S3: fetch + decode company_embeddings once for the whole run
        # (static within a run — writers are separate maint CLI runs).
        # None → per-call behavior inside resolve_entity; the tripwire
        # re-check after the loop warns if a writer committed mid-run.
        vss_index = build_vss_run_index()
        for sym in symbols:
            display_ticker(
                sym.upper().strip(),
                detailed=detailed,
                entities=entities,
                spellfix_conn=spellfix_conn,
                index=vss_index,
            )
        check_vss_run_index(vss_index)

        if spellfix_conn:
            spellfix_conn.close()


def cli(argv: list[str] | None = None) -> int:
    """CLI entry — house idiom (parser takes *argv*) so tests can drive flags.

    RawDescriptionHelpFormatter keeps the multi-line epilog from collapsing
    (only extract_relations set it before — shared_routines_cli_guards W3).
    """
    parser = argparse.ArgumentParser(
        description="Look up company tickers via Yahoo Finance and display company data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s TCS.NS RELIANCE.NS INFY.NS
  %(prog)s --detailed HDFCBANK.NS NYKAA.NS
""",
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        metavar="SYMBOL",
        help="Ticker symbol(s) to look up (e.g. RELIANCE.NS, TCS.BO)",
    )
    parser.add_argument(
        "-d",
        "--detailed",
        action="store_true",
        help="Fetch comprehensive data (financials, holders, recommendations)",
    )

    args = parser.parse_args(argv)
    main(symbols=args.symbols, detailed=args.detailed)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
