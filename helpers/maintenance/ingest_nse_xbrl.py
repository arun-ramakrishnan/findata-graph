#!/usr/bin/env python3
"""NSE Integrated Filing XBRL -> company_metrics (D20).

Producer for the filings lane (doc/local/evaluations/xchange_filings.md
§10.1): fetch Integrated Filing XBRL per NSE-listed entity via
``nse_xbrl`` (installed for the 2026-09-16 trial, §7.5.1), map the
FilingResult dataclass fields to ``company_metrics`` rows with the NSE
archive URL as ``source_ref`` (append-only filing anchor).

Scope (§7.5.2 trap): authored entities with .NS tickers only — the
main-listing symbol IS the ticker prefix; F&O series and subsidiaries
never match this shape.

Conventions:
- metric_label = the dataclass field name (q_revenue, q_pat, ...) —
  stable, greppable, parser-accurate
- value_num normalized to crore_inr for ₹ magnitudes, percent for EPS
  is NOT converted (EPS is per-share: unit 'inr_per_share')
- period = FY{yy}Q{q} from period_end (Indian FY Apr-Mar)
- properties JSON carries consolidated/audited flags
- idempotent on (entity, metric_label, period, source_ref)

The nse_xbrl client uses cookie auth (fragile); failures return []
with a warning, never block other entities.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "memory" / "research.db"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from helpers.core.db import connect  # noqa: E402  (post-bootstrap: entry point, lazy-import convention)

# FilingResult fields worth persisting (see nse_xbrl docs; §7.5.1)
Q_FIELDS = (
    "q_revenue",
    "q_total_income",
    "q_total_expenses",
    "q_ebit",
    "q_ebitda",
    "q_pbt",
    "q_total_tax",
    "q_pat",
    "q_pat_owners",
    "q_pat_nci",
    "q_diluted_eps",
    "q_basic_eps",
    "q_finance_costs",
    "q_depreciation",
    "q_other_income",
    "q_exceptional_items",
)
BS_FIELDS = (
    "bs_total_assets",
    "bs_total_liabilities",
    "bs_equity",
    "bs_equity_owners",
    "bs_equity_share_capital",
    "bs_nci",
    "bs_goodwill",
)
CF_FIELDS = ("cf_capex", "cf_dividends_paid", "cf_net_change_in_cash")
META_FIELDS = ("paid_up_equity", "face_value", "book_value_per_share", "debt_equity_ratio")
PER_SHARE = {"q_diluted_eps", "q_basic_eps", "book_value_per_share", "face_value"}
RATIO = {"debt_equity_ratio"}

_NUM = re.compile(r"[-+]?[\d,]+(?:\.\d+)?")


def parse_value(v) -> tuple[str, float | None, str | None]:
    """(value_raw, value_num, unit) from FilingResult field values.

    Tolerates '3,11,850', '₹ 3,11,850 Cr', 311850.0, '-', '' and None.
    Unit is decided by the caller's field class, not the raw text.
    """
    if v is None:
        return "", None, None
    raw = str(v).strip()
    if not raw or raw in {"-", "–", "NA", "None"}:
        return raw, None, None
    m = _NUM.search(raw.replace("\u20b9", "").replace("Rs.", ""))
    num = float(m.group(0).replace(",", "")) if m else None
    return raw, num, None


def fy_quarter(period_end: str) -> str:
    """'2026-06-30' -> 'FY27Q1' (Indian FY Apr-Mar)."""
    try:
        d = dt.date.fromisoformat(str(period_end)[:10])
    except ValueError:
        return str(period_end or "")
    if d.month in (4, 5, 6):
        q = 1
    elif d.month in (7, 8, 9):
        q = 2
    elif d.month in (10, 11, 12):
        q = 3
    else:
        q = 4
    fy = d.year + 1 if d.month >= 4 else d.year
    return f"FY{str(fy)[-2:]}Q{q}"


def unit_for(field: str) -> str:
    if field in PER_SHARE:
        return "inr_per_share"
    if field in RATIO:
        return "ratio"
    return "crore_inr"


def filing_to_rows(f, entity: str) -> list[dict]:
    """FilingResult (duck-typed) -> company_metrics row dicts."""
    period = fy_quarter(getattr(f, "period_end", "") or "")
    props = json.dumps(
        {
            "consolidated": bool(getattr(f, "is_consolidated", False)),
            "audited": bool(getattr(f, "is_audited", False)),
            "period_start": str(getattr(f, "period_start", None) or ""),
            "period_end": str(getattr(f, "period_end", None) or ""),
            "company_name": getattr(f, "company_name", None),
            "via": "nse-xbrl",
        }
    )
    rows = []
    for field in Q_FIELDS + BS_FIELDS + CF_FIELDS + META_FIELDS:
        raw, num, _ = parse_value(getattr(f, field, None))
        if num is None:
            continue
        # money fields arrive as RAW RUPEES (verified §7.5.1: q_revenue
        # 3118500000000 == ₹3,11,850 Cr); store crore_inr as num/1e7
        if unit_for(field) == "crore_inr":
            num = num / 1e7
        rows.append(
            {
                "entity": entity,
                "metric_label": field,
                "value_raw": raw,
                "value_num": num,
                "unit": unit_for(field),
                "period": period,
                "source_ref": getattr(f, "xbrl_url", None)
                or getattr(f, "url", None)
                or "nse-integrated-filing",
                "source_tier": "external",
                "properties": props,
            }
        )
    return rows


def existing_keys(conn: sqlite3.Connection) -> set[tuple]:
    return set(
        conn.execute(
            "select entity, metric_label, period, source_ref from company_metrics"
        ).fetchall()
    )


def apply_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    keys = existing_keys(conn)
    n = 0
    for r in rows:
        k = (r["entity"], r["metric_label"], r["period"], r["source_ref"])
        if k in keys:
            continue
        conn.execute(
            "insert into company_metrics (entity, metric_label, value_raw,"
            " value_num, unit, period, source_ref, source_tier, properties)"
            " values (?,?,?,?,?,?,?,?,?)",
            (
                r["entity"],
                r["metric_label"],
                r["value_raw"],
                r["value_num"],
                r["unit"],
                r["period"],
                r["source_ref"],
                r["source_tier"],
                r["properties"],
            ),
        )
        keys.add(k)
        n += 1
    conn.commit()
    return n


def targets(conn: sqlite3.Connection, limit: int | None = None, offset: int = 0) -> list[tuple]:
    sql = (
        "select name, ticker from entities where entity_type='company'"
        " and file_path is not null and ticker like '%.NS'"
        " order by name"
    )
    rows = conn.execute(sql).fetchall()[offset:]
    return rows[:limit] if limit else rows


_CLIENT = None


def client():
    """One shared NSEClient per run (cookie reuse; no per-entity spam)."""
    global _CLIENT
    if _CLIENT is None:
        from nse_xbrl import NSEClient

        _CLIENT = NSEClient()
    return _CLIENT


def fetch_filings(
    symbol: str,
    issuer: str,
    max_filings: int = 4,
    known_refs: set[str] | None = None,
    full: bool = False,
):
    """LIST-FIRST INCREMENTAL (default; operator ruling 2026-09-16 — bulk
    re-fetching risks NSE blocks): fetch the filing LIST (one cheap
    request), skip filings whose XBRL URL is already stored, download
    and parse only unseen documents. ``full=True`` re-downloads."""
    known = known_refs or set()
    try:
        from nse_xbrl import FilingResult

        c = client()
    except ImportError:
        print("[nse-xbrl] package missing: uv pip install nse-xbrl", file=sys.stderr)
        return []
    try:
        listing = c.get_integrated_filings(symbol, issuer) or {}
        rows = (listing.get("data") or [])[:max_filings]
        results = []
        for row in rows:
            url = row.get("xbrl") or row.get("xbrlFile") or row.get("attachmentFile") or ""
            if not url:
                continue
            if not full and url in known:
                continue  # already stored — no document download
            xml = c.get_integrated_xbrl(url)
            if not xml:
                continue
            results.append(
                FilingResult.from_xbrl(
                    xml,
                    symbol=symbol,
                    company_name=row.get("companyName", issuer),
                    seq_id=str(row.get("seq_Id", "")),
                    is_audited=str(row.get("audited", "")).upper() == "AUDITED",
                    is_consolidated=str(row.get("consolidated", "")).upper() == "CONSOLIDATED",
                    xbrl_url=url,
                )
            )
        return results
    except Exception as e:  # noqa: BLE001 - per-entity isolation
        print(f"[nse-xbrl] {symbol}: fetch failed {str(e)[:80]}", file=sys.stderr)
        return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entity", help="single entity name (else all targets)")
    ap.add_argument("--limit", type=int, help="cap number of entities (smoke tests)")
    ap.add_argument("--offset", type=int, default=0, help="skip first N targets (chunked runs)")
    ap.add_argument("--max-filings", type=int, default=4)
    ap.add_argument(
        "--new",
        action="store_true",
        help="only entities with zero stored XBRL rows (refresh hook)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="re-download filings already stored (default: incremental skip)",
    )
    ap.add_argument("--apply", action="store_true", help="write rows (default: dry-run)")
    args = ap.parse_args()

    conn = connect(DB)
    if args.entity:
        row = conn.execute("select ticker from entities where name = ?", [args.entity]).fetchone()
        if not row or not row[0]:
            sys.exit(f"entity not found (or no ticker): {args.entity!r}")
        tgts = [(args.entity, row[0])]
    else:
        tgts = targets(conn, args.limit, args.offset)
        if args.new:
            have = {
                r[0]
                for r in conn.execute(
                    "select distinct entity from company_metrics where properties like '%nse-xbrl%'"
                )
            }
            tgts = [t for t in tgts if t[0] not in have]
    # URLs already in the store — the incremental skip set (one query)
    known_refs = set()
    if not args.full:
        known_refs = {
            r[0]
            for r in conn.execute(
                "select distinct source_ref from company_metrics where properties like '%nse-xbrl%'"
            )
        }
        print(f"known filings: {len(known_refs)} (incremental skip on)", flush=True)
    conn.close()
    print(f"targets: {len(tgts)}", flush=True)

    total_rows = written = entities_hit = 0
    for i, (name, ticker) in enumerate(tgts, 1):
        symbol = ticker[:-3]  # strip .NS — main-listing scope
        filings = fetch_filings(
            symbol, name, args.max_filings, known_refs=known_refs, full=args.full
        )
        rows: list[dict] = []
        for f in filings:
            rows += filing_to_rows(f, name)
        total_rows += len(rows)
        if rows:
            entities_hit += 1
        if args.apply and rows:
            conn = connect(DB)
            written += apply_rows(conn, rows)
            conn.close()
        if i % 10 == 0:
            print(f"{i}/{len(tgts)} rows={total_rows} written={written}", flush=True)
        time.sleep(1.0)  # polite pacing (cookie-authed client)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"{mode}: entities={len(tgts)} with_filings={entities_hit}"
        f" rows={total_rows} written={written}"
    )


if __name__ == "__main__":
    main()
