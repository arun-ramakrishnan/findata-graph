#!/usr/bin/env python3
"""enrich_from_yfinance.py — refresh company financials + industry edges from yfinance.

Fetches structured data from Yahoo Finance for all companies with exchange tickers
and writes:
  - company_metrics: clean financial snapshot (margins, P/E, market cap, etc.)
  - graph_edges: competes_with edges from shared yfinance industry classification
  - company notes: structural profile section (industry, employees, holdings) +
    frontmatter `industry` field

Standalone tool — run via `make metrics-rebuild`. Not part of maint / maint-full.

Usage:
    python3 helpers/maintenance/enrich_from_yfinance.py [OPTIONS]

Options:
    --dry-run          Fetch and display, don't write to DB or notes
    --workers N        ThreadPool parallelism (default: 2)
    --company NAME     Enrich only one company (by name or ticker)
    --max-age-days N   Skip companies refreshed within N days (default: 0 = all)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, UTC
from pathlib import Path

import yfinance as yf
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# --- project bootstrap -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402

# --- constants ---------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("enrich")

DB_PATH = PROJECT_ROOT / "memory" / "research.db"
COMPANIES_DIR = PROJECT_ROOT / "findata" / "Companies"

SOURCE_REF = "yfinance"
SOURCE_REF_PREFIX = "yfinance"

# Sentinel markers for the note body section (mirrors derive_insights.py pattern)
_PROFILE_BEGIN = "<!-- BEGIN auto company profile (enrich_from_yfinance.py) -->"
_PROFILE_END = "<!-- END auto company profile -->"
_PROFILE_HEADING = "## Company Profile (yfinance)"
_PROFILE_PATTERN = re.compile(
    re.escape(_PROFILE_BEGIN) + r".*?" + re.escape(_PROFILE_END) + r"\n?",
    re.DOTALL,
)

# Any `<!-- BEGIN auto ... --><!-- END auto ... -->` region. Used to keep the
# profile insertion point from landing inside a sibling auto-block (e.g. the
# derive_insights key-figures region) — the root cause of the historical
# profile-block-stripping collision, where a profile lodged between the KF
# BEGIN/END markers got destroyed on the next `derive_insights --apply`.
# Marker pairs are matched with a STACK walk, not a non-greedy regex: the
# regions nest (a chatter region can enclose the key-figures region), and a
# non-greedy `BEGIN.*?END` pairs the outer BEGIN with the FIRST inner END,
# misplacing the region boundary (2026-08-19: 10 of 58 restored profiles
# landed inside the true chatter region this way).
_AUTO_MARKER = re.compile(r"<!--\s*(BEGIN|END)\s+auto\b.*?-->", re.DOTALL)


def _auto_region_spans(text: str) -> list[tuple[int, int]]:
    """Maximal (start, end) spans of top-level auto-block regions."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for m in _AUTO_MARKER.finditer(text):
        if m.group(1) == "BEGIN":
            stack.append(m.start())
        elif stack:
            start = stack.pop()
            if not stack:  # outermost pair closed
                spans.append((start, m.end()))
        # END without BEGIN: corrupted note — ignore that marker
    return spans


def _outside_auto_region(text: str, pos: int) -> int:
    """If ``pos`` falls inside an auto-block region, return that region's start.

    Ensures the profile is placed before a sibling auto-block's BEGIN marker
    rather than nested inside it.
    """
    for start, end in _auto_region_spans(text):
        if start <= pos < end:
            return start
    return pos

# yfinance fields that go to company_metrics (volatile, DB-only)
_METRIC_FIELDS = {
    "market_capitalization": "marketCap",
    "total_revenue": "totalRevenue",
    "gross_margin": "grossMargins",
    "operating_margin": "operatingMargins",
    "net_profit_margin": "profitMargins",
    "debt_to_equity": "debtToEquity",
    "pe_ratio": "trailingPE",
    "price_to_book": "priceToBook",
    "beta": "beta",
    "revenue_growth_yoy": "revenueGrowth",
    "earnings_growth_yoy": "earningsGrowth",
    "held_percent_insiders": "heldPercentInsiders",
    "held_percent_institutions": "heldPercentInstitutions",
}

# Maps our metric_label → (unit, period) for company_metrics rows
_METRIC_META = {
    "market_capitalization": ("crore_inr", "latest"),
    "total_revenue": ("crore_inr", "TTM"),
    "gross_margin": ("percent", "TTM"),
    "operating_margin": ("percent", "TTM"),
    "net_profit_margin": ("percent", "TTM"),
    "debt_to_equity": ("ratio", "TTM"),
    "pe_ratio": ("ratio", "latest"),
    "price_to_book": ("ratio", "latest"),
    "beta": ("dimensionless", "latest"),
    "revenue_growth_yoy": ("percent", "TTM"),
    "earnings_growth_yoy": ("percent", "TTM"),
    "held_percent_insiders": ("percent", "latest"),
    "held_percent_institutions": ("percent", "latest"),
}

_INSERT_METRIC_SQL = """
INSERT INTO company_metrics
    (entity, metric_label, value_raw, value_num, unit, period,
     as_of_edition, source_quote, source_ref, properties)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# --- fetching ----------------------------------------------------------------

def _format_value(raw: float, metric_label: str) -> str:
    """Format a numeric value for the value_raw column (human-readable)."""
    unit = _METRIC_META.get(metric_label, ("", ""))[0]
    if unit == "percent":
        return f"{raw * 100:.1f}%" if raw < 1 else f"{raw:.1f}%"
    if unit == "crore_inr":
        return f"₹{raw / 1e7:,.0f} Cr"  # yfinance returns INR, convert to crore
    if unit == "ratio":
        return f"{raw:.1f}"
    return f"{raw:.2f}"


def _convert_value(raw: float, metric_label: str) -> float:
    """Convert yfinance values to our standard units."""
    unit = _METRIC_META.get(metric_label, ("", ""))[0]
    if unit == "percent" and raw < 1:
        return raw * 100  # 0.338 → 33.8
    if unit == "crore_inr":
        return raw / 1e7  # ₹ → ₹ crore
    return raw


def fetch_company(ticker: str, retries: int = 1) -> dict | None:
    """Fetch yfinance .info for a single ticker. Returns dict or None on failure.

    Retries once after a short delay to handle transient rate-limiting.
    """
    for attempt in range(1 + retries):
        try:
            info = yf.Ticker(ticker).get_info()
            # Some tickers only populate shortName (e.g. HYUNDAI.NS); accept
            # either as proof the instrument exists and has data.
            if not info or not (info.get("longName") or info.get("shortName")):
                return None
            return info
        except Exception:
            if attempt < retries:
                time.sleep(0.5)
                continue
            return None
    return None


def extract_metrics(name: str, info: dict) -> list[dict]:
    """Extract company_metrics rows from yfinance .info."""
    metrics = []
    now = datetime.now(UTC).isoformat(timespec="seconds")
    props = json.dumps({"fetched_at": now, "source": "yfinance"}, sort_keys=True)

    # Some companies (e.g. Infosys) report financials in USD even on .NS.
    # marketCap is always in local currency (INR); revenue/ebitda may be in
    # financialCurrency. Convert to INR for consistency.
    fin_currency = info.get("financialCurrency", "INR")
    usd_to_inr = 83.0  # approximate; good enough for cross-company comparison

    for metric_label, yf_field in _METRIC_FIELDS.items():
        raw = info.get(yf_field)
        if raw is None:
            continue
        try:
            raw_float = float(raw)
        except (ValueError, TypeError):
            continue
        # Convert USD revenue to INR for consistency
        if metric_label in ("total_revenue",) and fin_currency == "USD":
            raw_float *= usd_to_inr
        unit, period = _METRIC_META[metric_label]
        value_num = _convert_value(raw_float, metric_label)
        value_raw = _format_value(raw_float, metric_label)
        metrics.append({
            "entity": name,
            "metric_label": metric_label,
            "value_raw": value_raw,
            "value_num": value_num,
            "unit": unit,
            "period": period,
            "as_of_edition": None,
            "source_quote": None,
            "source_ref": SOURCE_REF,
            "properties": props,
        })
    return metrics


def extract_profile(name: str, info: dict) -> dict | None:
    """Extract structural profile data for the note body section."""
    industry = info.get("industry")
    if not industry:
        return None
    summary = info.get("longBusinessSummary", "")
    if len(summary) > 300:
        summary = summary[:297] + "…"
    return {
        "industry": industry,
        "employees": info.get("fullTimeEmployees"),
        "promoter_holding": info.get("heldPercentInsiders"),
        "institutional_holding": info.get("heldPercentInstitutions"),
        "business_summary": summary,
    }


def render_profile_block(profile: dict) -> str:
    """Render the sentinel-wrapped note body section."""
    lines = [_PROFILE_BEGIN, "", _PROFILE_HEADING, ""]

    industry = profile.get("industry")
    if industry:
        lines.append(f"- **Industry**: {industry}")

    employees = profile.get("employees")
    if employees is not None:
        lines.append(f"- **Employees**: {int(employees):,}")

    promoter = profile.get("promoter_holding")
    if promoter is not None:
        lines.append(f"- **Promoter Holding**: {promoter * 100:.1f}%")

    institutional = profile.get("institutional_holding")
    if institutional is not None:
        lines.append(f"- **Institutional Holding**: {institutional * 100:.1f}%")

    summary = profile.get("business_summary")
    if summary:
        lines.append(f"- **Business Summary**: {summary}")

    today = datetime.now().strftime("%Y-%m-%d")
    lines.append("")
    lines.append(f"_Source: yfinance | Refreshed: {today}_")
    lines.extend([_PROFILE_END, ""])
    return "\n".join(lines)


# --- note updates ------------------------------------------------------------

def _update_frontmatter(text: str, industry: str) -> str:
    """Add or update the `industry:` field in YAML frontmatter."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text

    # Find end of frontmatter
    fm_end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return text

    # Check if industry already exists
    industry_re = re.compile(r"^industry:\s*(.*)$")
    for i in range(1, fm_end):
        m = industry_re.match(lines[i])
        if m:
            # Update existing
            lines[i] = f"industry: {industry}"
            return "\n".join(lines)

    # Insert industry after the `sector:` line if present, else after ticker
    insert_at = 1  # fallback: right after opening ---
    for i in range(1, fm_end):
        if lines[i].startswith("sector:"):
            insert_at = i + 1
            break
        if lines[i].startswith("ticker:"):
            insert_at = i + 1

    lines.insert(insert_at, f"industry: {industry}")
    return "\n".join(lines)


def _insert_profile_section(text: str, block: str) -> str:
    """Replace or insert the sentinel-wrapped profile block."""
    if _PROFILE_PATTERN.search(text):
        return _PROFILE_PATTERN.sub(block, text)

    # Find insertion point: after Company Overview / first ## heading
    for heading_re in (r"^## Company Overview", r"^## Overview", r"^## "):
        m = re.search(heading_re, text, re.MULTILINE)
        if m:
            nxt = re.search(r"^## ", text[m.end():], re.MULTILINE)
            pos = m.end() + (nxt.start() if nxt else 0)
            # Don't insert inside a sibling auto-block region (e.g. the
            # derive_insights key-figures block): place before its BEGIN marker.
            pos = _outside_auto_region(text, pos)
            return text[:pos] + "\n" + block + text[pos:]

    return text + "\n" + block


def update_note(file_path: Path, industry: str | None, profile: dict | None) -> bool:
    """Update a company note with industry frontmatter + profile section.

    Returns True if the file was modified.
    """
    if not file_path.exists():
        return False

    text = file_path.read_text(encoding="utf-8")
    original = text

    if industry:
        text = _update_frontmatter(text, industry)

    if profile:
        block = render_profile_block(profile)
        text = _insert_profile_section(text, block)

    if text != original:
        file_path.write_text(text, encoding="utf-8")
        return True
    return False


# --- DB writes ---------------------------------------------------------------

def write_metrics(conn: sqlite3.Connection, metrics: list[dict]) -> int:
    """Delete old yfinance metrics for affected entities, then insert new ones."""
    if not metrics:
        return 0

    entities = list({m["entity"] for m in metrics})
    placeholders = ",".join("?" * len(entities))
    conn.execute(
        f"DELETE FROM company_metrics WHERE source_ref = ? AND entity IN ({placeholders})",  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        [SOURCE_REF] + entities,
    )

    inserted = 0
    for m in metrics:
        cur = conn.execute(
            _INSERT_METRIC_SQL,
            (m["entity"], m["metric_label"], m["value_raw"], m["value_num"],
             m["unit"], m["period"], m["as_of_edition"], m["source_quote"],
             m["source_ref"], m["properties"]),
        )
        inserted += cur.rowcount
    return inserted


# NOTE (E2, 2026-08-24): the v1 write_competitor_edges() clique path was
# RETIRED — it never effectively applied (zero 'yfinance:*' rows in the DB;
# see proposal relation_enrichment_sources.md §1) and is superseded by the
# bounded-KNN topology in helpers/maintenance/enrich_relations.py. This
# script is now metrics/notes-only.


# --- skip logic --------------------------------------------------------------

def get_stale_companies(conn: sqlite3.Connection,
                        all_names: list[str],
                        max_age_days: int) -> set[str]:
    """Return names of companies that have yfinance data newer than max_age_days."""
    if max_age_days <= 0:
        return set()
    cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT entity FROM company_metrics "
        "WHERE source_ref = ? AND created_at >= ?",
        (SOURCE_REF, cutoff),
    ).fetchall()
    return {r[0] for r in rows}


def get_enriched_companies(file_paths: list[tuple[str, str | None]]) -> set[str]:
    """Return names of companies whose notes already have ``industry:`` in frontmatter.

    Used to skip fetching when the note is already enriched and the user
    hasn't requested a forced full refresh (``--max-age-days 0``).
    """
    enriched: set[str] = set()
    for name, fp_str in file_paths:
        if not fp_str:
            continue
        fp = PROJECT_ROOT / fp_str
        if not fp.exists():
            continue
        text = fp.read_text()
        # Check frontmatter only (between first pair of ---)
        if text.startswith("---"):
            fm_end = text.find("---", 3)
            if fm_end > 0 and "\nindustry:" in text[:fm_end]:
                enriched.add(name)
    return enriched



# --- report -------------------------------------------------------------------

REPORT_PATH = PROJECT_ROOT / "metrics_report.txt"


def write_report(
    results: list[tuple[str, str, str, dict]],
    failures: list[tuple[str, str, str]],
    metrics_written: int,
    edges_written: int,
    notes_updated: int,
    notes_skipped: int,
    total_time: float,
    dry_run: bool,
) -> None:
    """Write a detailed enrichment report to metrics_report.txt."""
    mode_label = "DRY-RUN" if dry_run else "APPLIED"
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines_out: list[str] = []
    lines_out.append(f"# yfinance Enrichment Report — {mode_label}")
    lines_out.append(f"# Generated: {ts}")
    lines_out.append(f"# Workers: 2 | Duration: {total_time:.1f}s")
    lines_out.append("")

    # Summary
    lines_out.append("## Summary")
    lines_out.append(f"  Total companies attempted : {len(results) + len(failures)}")
    lines_out.append(f"  Successfully fetched      : {len(results)}")
    lines_out.append(f"  Failed (404 / no data)    : {len(failures)}")
    lines_out.append(f"  Skipped (already enriched): {notes_skipped}")
    if not dry_run:
        lines_out.append(f"  Metrics written to DB     : {metrics_written}")
        lines_out.append(f"  competes_with edges added : {edges_written}")
        lines_out.append(f"  Company notes updated     : {notes_updated}")
    lines_out.append("")

    # Success details — industry distribution
    if results:
        from collections import Counter
        industries = Counter()
        for _, _, _, info in results:
            ind = info.get("industry", "(unknown)")
            industries[ind] += 1

        lines_out.append("## Industries (fetched)")
        for ind, cnt in industries.most_common():
            lines_out.append(f"  {cnt:4d}  {ind}")
        lines_out.append("")

    # Failures — full list with file paths
    if failures:
        lines_out.append("## Failed Tickers (404 or no data)")
        lines_out.append("    # name | ticker | note_path")
        lines_out.append("    # ---- | ------ | ---------")
        for name, ticker, file_path in sorted(failures):
            fp = file_path or "(no file_path)"
            lines_out.append(f"    {name} | {ticker} | {fp}")
        lines_out.append("")

    lines_out.append("---")
    lines_out.append("")

    REPORT_PATH.write_text("\n".join(lines_out))
    log.info("report written to %s", REPORT_PATH.relative_to(PROJECT_ROOT))


# --- main --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="Enrich company data from yfinance."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and display without writing to DB or notes")
    parser.add_argument("--workers", type=int, default=2,
                        help="ThreadPool parallelism (default: 8)")
    parser.add_argument("--company", type=str, default=None,
                        help="Enrich only one company (by name or ticker)")
    parser.add_argument("--max-age-days", type=int, default=0,
                        help="Skip companies refreshed within N days (default: 0 = all)")
    args = parser.parse_args(argv)

    conn = connect(DB_PATH)

    # Load companies with tickers
    if args.company:
        like = f"%{args.company}%"
        rows = conn.execute(
            "SELECT name, ticker, file_path FROM entities "
            "WHERE entity_type = 'company' "
            "AND ticker IS NOT NULL AND ticker != '' "
            "AND (name LIKE ? OR ticker LIKE ?)",
            (like, like),
        ).fetchall()
        # Fallback: strip the exchange suffix (e.g. KSB.NS → KSB) so that a
        # ticker with a different suffix in the DB (KSB.SG) or a bare company
        # name still matches. This mirrors get_tickers.py's resolution, which
        # matches the yfinance longName — not the stored ticker string.
        if not rows:
            base = re.sub(r'\.[A-Za-z]{1,3}$', '', args.company.strip())
            if base != args.company:
                base_like = f"%{base}%"
                rows = conn.execute(
                    "SELECT name, ticker, file_path FROM entities "
                    "WHERE entity_type = 'company' "
                    "AND ticker IS NOT NULL AND ticker != '' "
                    "AND (name LIKE ? OR ticker LIKE ?)",
                    (base_like, base_like),
                ).fetchall()
    else:
        rows = conn.execute(
            "SELECT name, ticker, file_path FROM entities "
            "WHERE entity_type = 'company' "
            "AND ticker IS NOT NULL AND ticker != ''",
        ).fetchall()

    if not rows:
        log.error("no companies with tickers found")
        conn.close()
        return 1

    # Apply skip-logic
    #   max_age_days=0  → full refresh (fetch everything)
    #   max_age_days>0  → incremental: skip companies that already have
    #                     industry: in their note frontmatter AND have yfinance
    #                     metrics newer than max_age_days
    all_names = [r[0] for r in rows]
    fresh = get_stale_companies(conn, all_names, args.max_age_days)
    industry_enriched: set[str] = set()
    if args.max_age_days > 0:
        enriched_pairs: list[tuple[str, str | None]] = [(str(r[0]), str(r[2])) for r in rows]
        industry_enriched = get_enriched_companies(enriched_pairs)
    skip_set = fresh | industry_enriched
    notes_skipped = len(skip_set)
    if skip_set:
        log.info("skipping %d companies (%d fresh metrics, %d already enriched)",
                 notes_skipped, len(fresh), len(industry_enriched))

    todo = [(r[0], r[1], r[2]) for r in rows if r[0] not in skip_set]
    log.info("enriching %d/%d companies (workers=%d, dry_run=%s)",
             len(todo), len(rows), args.workers, args.dry_run)

    # Fetch in parallel
    results: list[tuple[str, str, str, dict]] = []  # (name, ticker, file_path, info)
    t0 = time.perf_counter()

    def _fetch_task(name_ticker_path):
        name, ticker, file_path = name_ticker_path
        info = fetch_company(ticker)
        return name, ticker, file_path, info

    failures: list[tuple[str, str, str]] = []  # (name, ticker, file_path)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_fetch_task, todo_item): todo_item for todo_item in todo}
        completed = 0
        for f in as_completed(futures):
            name, ticker, file_path, info = f.result()
            completed += 1
            if info is None:
                failures.append((name, ticker, file_path))
                continue
            results.append((name, ticker, file_path, info))
            if completed % 100 == 0:
                log.info("  fetched %d/%d...", completed, len(todo))

    fetch_time = time.perf_counter() - t0
    log.info("fetched %d/%d companies in %.1fs (%d failed)",
             len(results), len(todo), fetch_time, len(failures))

    if failures:
        log.info("failed tickers (%d): %s",
                 len(failures),
                 ", ".join(f"{n}({t})" for n, t, _ in failures))

    if args.dry_run:
        # Display sample results
        for name, ticker, _, info in results[:5]:
            metrics = extract_metrics(name, info)
            profile = extract_profile(name, info)
            print(f"\n{'='*60}")
            print(f"  {name} ({ticker})")
            print(f"{'='*60}")
            if profile:
                print(f"  Industry: {profile['industry']}")
                print(f"  Employees: {profile.get('employees')}")
            print(f"  Metrics ({len(metrics)}):")
            for m in metrics:
                print(f"    {m['metric_label']:25s} {m['value_raw']:>15s}  [{m['unit']}]")
        if len(results) > 5:
            print(f"\n  ... and {len(results) - 5} more (use --company NAME for details)")

        # E2 (2026-08-24): the dry-run "Projected competes_with edges" clique
        # projection was RETIRED — its n*(n-1)/2 number (~5900) never matched
        # what any apply path could write (proposal §1 confusion). Edge
        # projections now live ONLY in `enrich_relations.py --dry-run`.

        conn.close()
        total_time = time.perf_counter() - t0
        write_report(results, failures, 0, 0, 0, notes_skipped, total_time, dry_run=True)
        return 0

    # Write to DB
    all_metrics = []
    name_to_industry = {}
    notes_updated = 0

    with conn:
        for name, ticker, file_path, info in results:
            # company_metrics
            metrics = extract_metrics(name, info)
            all_metrics.extend(metrics)

            # industry for competitor edges
            industry = info.get("industry")
            if industry:
                name_to_industry[name] = industry

            # note update
            fp = PROJECT_ROOT / file_path if file_path else None
            if fp and fp.exists():
                profile = extract_profile(name, info)
                if update_note(fp, industry, profile):
                    notes_updated += 1

        # Write metrics
        metrics_written = write_metrics(conn, all_metrics)
        log.info("wrote %d metric rows for %d companies",
                 metrics_written, len({m["entity"] for m in all_metrics}))


    log.info("updated %d company notes", notes_updated)

    conn.close()

    total_time = time.perf_counter() - t0
    # E2: edges_written is gone — competes_with moved to enrich_relations.py.
    log.info("✓ enrichment complete in %.1fs (%d fetched, %d metrics, %d notes, %d skipped)",
             total_time, len(results), metrics_written, notes_updated, notes_skipped)
    write_report(results, failures, metrics_written, 0, notes_updated,
                 notes_skipped, total_time, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
