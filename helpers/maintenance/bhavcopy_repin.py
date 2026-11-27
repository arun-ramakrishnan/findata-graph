#!/usr/bin/env python3
"""Last-3-trading-days BhavCopy repin probe (India, 3-month window).

There is no single monthly BhavCopy file: NSE and BSE publish daily
UDiFF files. This probe walks backwards over the requested months and
takes only the last three *available* trading days per exchange per
month, then aggregates them into a staged report. It never writes to the
live stores; new/absent_from_sample symbols are candidates for the operator-gated
``exchange_sync`` / ``fold_identifiers`` lanes.

Current UDiFF endpoints (old standardized files died 2024-07-08):
- NSE: https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
- BSE: https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{YYYYMMDD}_F_0000.CSV
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, overload

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import duckdb  # noqa: E402  (after the REPO sys.path insert, house pattern)
from helpers.core.db import utc_now  # noqa: E402
from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402

DB = REPO / "memory" / "data" / "sources.duckdb"
TRADING_DAYS = 3
MONTHS = 3
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

BhavRow = dict  # exchange, symbol, name, isin, ticker, series, trade_date

BHAVCOPY_SAMPLE_DDL = """
CREATE TABLE IF NOT EXISTS bhavcopy_sample (
    exchange VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    isin VARCHAR,
    name VARCHAR,
    series VARCHAR,
    quarter VARCHAR,
    source VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (exchange, trade_date, ticker)
)
"""


@overload
def _get(url: str, referer: str | None = ..., binary: Literal[False] = ...) -> str: ...
@overload
def _get(url: str, referer: str | None = ..., binary: Literal[True] = ...) -> bytes: ...
def _get(url: str, referer: str | None = None, binary: bool = False) -> bytes | str:
    hdr = dict(UA)
    if referer:
        hdr["Referer"] = referer
    req = urllib.request.Request(url, headers=hdr)  # noqa: S310  # https-only official endpoints
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310  # https-only official endpoints
        raw = r.read()
    return raw if binary else raw.decode("utf-8", "replace")


def nse_url(day: date) -> str:
    return (
        "https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    )


def bse_url(day: date) -> str:
    return (
        "https://www.bseindia.com/download/BhavCopy/Equity/"
        f"BhavCopy_BSE_CM_0_0_0_{day:%Y%m%d}_F_0000.CSV"
    )


def parse_udiff_csv(text: str, exchange: str, day: date) -> list[BhavRow]:
    """Parse a NSE/BSE UDiFF BhavCopy CSV into listing-shaped rows.

    UDiFF carries no company name; the ticker/ISIN are the identity. The
    name field is left empty so ``enrich_relations``' identity_ok can be
    the authority before any writeback.
    """
    out: list[BhavRow] = []
    for raw in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        row = {(k or "").strip().upper(): (v or "").strip() for k, v in raw.items()}
        sym = row.get("TCKRSYMB", "")
        isin = row.get("ISIN", "")
        if not sym or not isin.startswith("IN"):
            continue
        if exchange == "BSE":
            code = row.get("FININSTRMID", sym)
            if not code.isdigit():
                continue
            series = row.get("SERIES") or row.get("SCTYSRS") or row.get("SGMT", "")
            sym, suffix = code, ".BO"
        else:
            series = row.get("SCTYSRS", "").upper()
            if series and series not in ("EQ", "BE", "BZ", "SM", "YM", "ST", "SX"):
                continue
            suffix = ".NS"
        out.append(
            {
                "exchange": exchange,
                "symbol": sym,
                "name": "",
                "isin": isin,
                "ticker": sym + suffix,
                "series": series,
                "trade_date": day.isoformat(),
            }
        )
    return out


def _nse_rows(day: date) -> list[BhavRow]:
    raw = _get(nse_url(day), referer="https://www.nseindia.com/", binary=True)
    if not isinstance(raw, bytes):
        raise TypeError("NSE BhavCopy fetch must return bytes")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("NSE BhavCopy zip has no CSV member")
        text = z.read(names[0]).decode("utf-8-sig", "replace")
    return parse_udiff_csv(text, "NSE", day)


def _bse_rows(day: date) -> list[BhavRow]:
    text = _get(bse_url(day), referer="https://www.bseindia.com/")
    if not isinstance(text, str):
        raise TypeError("BSE BhavCopy fetch must return text")
    return parse_udiff_csv(text, "BSE", day)


def _days_back(year: int, month: int, today: date) -> list[date]:
    last = date(year, month, calendar.monthrange(year, month)[1])
    end = min(last, today)
    days = []
    cur = end
    while cur.month == month:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return days


def collect_month(year: int, month: int, today: date) -> dict:
    """Walk backwards over a month until each exchange has 3 good days."""
    found: dict[str, list[str]] = {"NSE": [], "BSE": []}
    by_day: dict[str, list[BhavRow]] = defaultdict(list)
    errors: list[str] = []
    for day in _days_back(year, month, today):
        if all(len(v) >= TRADING_DAYS for v in found.values()):
            break
        for exchange, fetch in (("NSE", _nse_rows), ("BSE", _bse_rows)):
            if len(found[exchange]) >= TRADING_DAYS:
                continue
            try:
                rows = fetch(day)
            except Exception as exc:  # noqa: BLE001  # one bad day is normal (holiday/outage)
                errors.append(f"{exchange} {day.isoformat()}: {type(exc).__name__}: {exc}")
                continue
            found[exchange].append(day.isoformat())
            by_day[exchange].extend(rows)
    agg: dict[str, dict[str, BhavRow]] = {}
    for exchange, rows in by_day.items():
        seen: dict[str, BhavRow] = {}
        for row in rows:
            seen.setdefault(row["ticker"], row)
        agg[exchange] = seen
    return {
        "month": f"{year:04d}-{month:02d}",
        "days": found,
        "errors": errors,
        "rows": dict(by_day),
        "aggregate": {k: list(v.values()) for k, v in agg.items()},
    }


def _previous_months(today: date, months: int) -> list[tuple[int, int]]:
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _quarter_months(today: date) -> tuple[str, list[tuple[int, int]]]:
    """Return the previous completed calendar quarter and its three months.

    The quarterly cadence deliberately uses a closed quarter, not a rolling
    three-month window: a run on 2027-01-05 covers Oct–Dec 2026.
    """
    q = (today.month - 1) // 3 + 1
    if q == 1:
        year, prev_q = today.year - 1, 4
    else:
        year, prev_q = today.year, q - 1
    start = 3 * (prev_q - 1) + 1
    return f"{year}-Q{prev_q}", [(year, start + i) for i in range(3)]


def _load_known() -> dict[str, set[str]]:
    import duckdb

    con = duckdb.connect(str(DB), read_only=True, **memory_cap_config())
    try:
        rows = con.execute(
            "select exchange, symbol from exchange_listings where exchange in ('NSE','BSE')"
        ).fetchall()
    finally:
        con.close()
    known: dict[str, set[str]] = defaultdict(set)
    for exchange, symbol in rows:
        known[exchange].add(str(symbol))
    return dict(known)


def persist_sample(staged: list[dict], quarter: str | None, path: Path = DB) -> int:
    """Persist daily BhavCopy rows into sources.duckdb::bhavcopy_sample."""
    rows: list[tuple[str, str, str, str, str, str, str, str, str, str]] = []
    now = utc_now()
    for entry in staged:
        q = quarter or str(entry.get("month") or "")
        for exchange, day_rows in entry.get("rows", {}).items():
            for row in day_rows:
                rows.append(
                    (
                        str(row["exchange"]),
                        str(row["trade_date"]),
                        str(row["symbol"]),
                        str(row["ticker"]),
                        str(row.get("isin") or ""),
                        str(row.get("name") or ""),
                        str(row.get("series") or ""),
                        q,
                        f"{str(exchange).lower()}_bhavcopy",
                        now,
                    )
                )
    if not rows:
        return 0
    con = duckdb.connect(str(path), **memory_cap_config())
    try:
        con.execute(BHAVCOPY_SAMPLE_DDL)
        con.executemany(
            "INSERT OR REPLACE INTO bhavcopy_sample "
            "(exchange, trade_date, symbol, ticker, isin, name, series, quarter, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()
    return len(rows)


def _summarize(staged: list[dict]) -> dict:
    known = _load_known()
    summary = {"months": [], "new": {}, "absent_from_sample": {}}
    for entry in staged:
        month = entry["month"]
        summary["months"].append(
            {
                "month": month,
                "nse_days": entry["days"]["NSE"],
                "bse_days": entry["days"]["BSE"],
                "nse_unique": len(entry["aggregate"].get("NSE", [])),
                "bse_unique": len(entry["aggregate"].get("BSE", [])),
            }
        )
        for exchange in ("NSE", "BSE"):
            syms = {r["symbol"] for r in entry["aggregate"].get(exchange, [])}
            known_syms = known.get(exchange, set())
            new = syms - known_syms
            absent = known_syms - syms
            summary["new"].setdefault(exchange, set()).update(new)
            summary["absent_from_sample"].setdefault(exchange, set()).update(absent)
    summary["new"] = {k: sorted(v) for k, v in summary["new"].items()}
    summary["absent_from_sample"] = {k: sorted(v) for k, v in summary["absent_from_sample"].items()}
    return summary


def main() -> int:
    global TRADING_DAYS
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--months", type=int, default=MONTHS, help="lookback months (default: 3)")
    p.add_argument("--trading-days", type=int, default=TRADING_DAYS)
    p.add_argument("--today", default=date.today().isoformat())
    p.add_argument("--month", help="single month YYYY-MM (overrides --months)")
    p.add_argument(
        "--quarter",
        action="store_true",
        help="use the previous completed calendar quarter (quarterly cadence)",
    )
    p.add_argument(
        "--no-db",
        action="store_true",
        help="report only; do not persist rows to sources.duckdb::bhavcopy_sample",
    )
    args = p.parse_args()

    TRADING_DAYS = args.trading_days
    today = date.fromisoformat(args.today)
    quarter = None
    if args.month:
        months = [tuple(int(x) for x in args.month.split("-"))]
    elif args.quarter:
        quarter, months = _quarter_months(today)
    else:
        months = _previous_months(today, args.months)
    staged = [collect_month(y, m, today) for y, m in months]
    summary = _summarize(staged)
    db_rows = 0 if args.no_db else persist_sample(staged, quarter)
    print(
        json.dumps(
            {
                "db_rows": db_rows,
                "cadence": "quarterly" if quarter else "rolling",
                "quarter": quarter,
                "summary": summary,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
