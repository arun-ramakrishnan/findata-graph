#!/usr/bin/env python3
"""Exchange listing sync + IPO intake lane (D19, hyper_lane_wiring.md).

One idempotent command per lane: fetch the exchange master, fold rows
into ``sources.duckdb::exchange_listings`` (deduped on exchange/symbol/
segment), diff new symbols against the store (the IPO detector), seed
stubs for confirmed rows, and write a review worklist. Renames and
delistings (symbols that vanished from the master) are flagged, never
acted on automatically.

Lanes (stability register in doc/design/data_sources.md):
- hkex/szse/sse/twse/wiki: stable endpoints, scriptable
- tmx/krx: operator-delivered masters (ingest-file style, see --tmx-file/--krx-file)
- jpx: monthly xlsx with rotating attachment id — manual for now
- India (BSE/NSE): repin pending (BhavCopy monthly is the candidate)

The first run stamps every pre-existing row fetched_at=2026-09-15 (the
D16 fold date); later runs stamp only rows they fold.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, overload

import duckdb

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from helpers.core.db import connect  # noqa: E402  (post-bootstrap: entry point, lazy-import convention)

DB = REPO / "memory" / "data" / "sources.duckdb"
WORKLIST = REPO / "memory" / "data" / "ipo_worklist.csv"
BACKFILL_DATE = "2026-09-15"  # D16 fold date for pre-existing rows
UA = {"User-Agent": "Mozilla/5.0 research"}

Row = dict  # keys: symbol, name, segment, isin?, industry?, exchange


@overload
def _get(
    url: str, referer: str | None = ..., binary: Literal[False] = ..., timeout: int = ...
) -> str: ...
@overload
def _get(
    url: str, referer: str | None = ..., binary: Literal[True] = ..., timeout: int = ...
) -> bytes: ...
def _get(
    url: str, referer: str | None = None, binary: bool = False, timeout: int = 60
) -> bytes | str:
    hdr = dict(UA)
    if referer:
        hdr["Referer"] = referer
    req = urllib.request.Request(url, headers=hdr)  # noqa: S310  # https-only lane endpoints (no file:/custom schemes)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # https-only lane endpoints (no file:/custom schemes)
        raw = r.read()
    return raw if binary else raw.decode("utf-8", "replace")


def clean_name(nm: str) -> str:
    """entities CHECK-safe name (no Pvt/Private/Ltd/Limited tails)."""
    nm = re.sub(r"\bPvt\.?\b", " ", nm or "", flags=re.I)
    nm = re.sub(r"\bPrivate\b", " ", nm, flags=re.I)
    while True:
        nm2 = re.sub(r"[\s.]*(Ltd|Limited)[.]?\s*$", "", nm.strip(), flags=re.I)
        if nm2 == nm:
            break
        nm = nm2
    return re.sub(r"\s{2,}", " ", nm).strip(" .,-")


def normalized_name(nm: str) -> str:
    """Vault convention: underscore form, case preserved, [A-Za-z0-9_]
    only — punctuation runs collapse to a single '_' and edge underscores
    are trimmed (the integrity check's normalization contract; listing
    names with "(ITP)"/"&"/"-" must not leak punctuation, 2026-09-16)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", (nm or "").strip()).strip("_")


# ---------------------------------------------------------------- parsers


def _xlsx_rows_from_bytes(data: bytes) -> list[list[str]]:
    """Minimal xlsx reader (HKEX): sharedStrings + sheet1, namespace-safe."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        z = zipfile.ZipFile(tmp)
        ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        shared = [
            re.sub(r"<[^>]+>", "", m)
            for m in re.findall(r"<(?:\w+:)?si>(.*?)</(?:\w+:)?si>", ss, re.S)
        ]
        s = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")
        rows: list[list[str]] = []
        for rr in re.findall(r"<(?:\w+:)?row[^>]*>(.*?)</(?:\w+:)?row>", s, re.S):
            vals = []
            for cell in re.findall(r"<(?:\w+:)?c\b[^>]*?(?:/>|>.*?</(?:\w+:)?c>)", rr, re.S):
                t = re.search(r't="(\w+)"', cell)
                v = re.search(r"<(?:\w+:)?v>(.*?)</(?:\w+:)?v>", cell, re.S)
                if v is None:
                    vals.append("")
                elif t and t.group(1) == "s":
                    vals.append(shared[int(v.group(1))])
                else:
                    vals.append(v.group(1))
            rows.append(vals)
        return rows
    finally:
        tmp.unlink(missing_ok=True)


def parse_hkex_xlsx(data: bytes) -> list[Row]:
    """HKEX ListOfSecurities: equities only (Category == 'Equity', col 2)."""
    out: list[Row] = []
    for r in _xlsx_rows_from_bytes(data)[1:]:
        if len(r) < 3 or (r[2] or "").strip().lower() != "equity":
            continue
        sym, nm = (r[0] or "").strip(), (r[1] or "").strip()
        sub = (r[3] if len(r) > 3 else "") or ""
        seg = "gem" if "gem" in sub.lower() else "main"
        if sym and nm:
            out.append(
                {
                    "exchange": "HKEX",
                    "symbol": sym,
                    "name": nm,
                    "segment": seg,
                    "asset_type": "equity",
                }
            )
    return out


def fetch_hkex() -> list[Row]:
    url = (
        "https://www.hkex.com.hk/eng/services/trading/securities/"
        "securitieslists/ListOfSecurities.xlsx"
    )
    return parse_hkex_xlsx(_get(url, binary=True))


def fetch_szse() -> list[Row]:
    """SZSE JSON API, paged; board Chinese label -> main/sme/chinext."""
    rows: list[Row] = []
    page = 1
    while page <= 200:
        j = json.loads(
            _get(
                "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON"
                "&CATALOGID=1110&TABKEY=tab1&PAGENO=" + str(page) + "&random=0.1",
                referer="https://www.szse.cn/markets/stock/list/index.html",
            )
        )
        data = (j[0].get("data") or []) if j else []
        if not data:
            break
        for r in data:
            # API fields: agdm=code, agjc=name (HTML <a><u>…</u></a>), bk=board
            code = (r.get("agdm") or "").strip()
            nm = re.sub(r"<[^>]+>", "", r.get("agjc") or "").strip()
            board = r.get("bk") or ""
            if not code or not nm:
                continue
            seg = "sme" if "中小板" in board else "chinext" if "创业板" in board else "main"
            rows.append(
                {
                    "exchange": "SZSE",
                    "symbol": code,
                    "name": nm,
                    "segment": seg,
                    "asset_type": "equity",
                }
            )
        page += 1
        time.sleep(0.4)
    return rows


def fetch_sse() -> list[Row]:
    """SSE JSONP query, paged; main board + star (科创) board. SLOW MODE:
    2s pacing + per-page retry with backoff — the endpoint throttles
    aggressive clients into 60s+ stalls (verified 2026-09-16)."""
    base = (
        "http://query.sse.com.cn/sseQuery/commonQuery.do?"
        "jsonCallBack=jsonpCallback123&isPagination=true"
        "&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L"
        "&STOCK_TYPE=1&pageHelp.cacheSize=1&pageHelp.beginPage={p}"
        "&pageHelp.pageSize=25&pageHelp.pageNo={p}&pageHelp.endPage={p}"
    )
    rows: list[Row] = []
    for p in range(1, 201):
        t = ""
        for attempt in (1, 2, 3):
            try:
                t = _get(base.format(p=p), referer="http://www.sse.com.cn/", timeout=120)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(10 * attempt)  # backoff: 10s, 20s
        m = re.search(r"jsonpCallback123\((.*)\)\s*$", t, re.S)
        j = json.loads(m.group(1)) if m else {}
        res = j.get("result") or []
        if not res:
            break
        for r in res:
            # fields: A_STOCK_CODE / FULL_NAME_IN_ENGLISH / COMPANY_ABBR_EN /
            # LIST_BOARD ('1' = main board, other values = STAR 科创板).
            # D16 convention: symbols carry .SS; full English names preferred
            code = (
                r.get("A_STOCK_CODE") or r.get("SECURITY_CODE_A") or r.get("SECURITY_CODE") or ""
            ).strip()
            nm = (
                r.get("FULL_NAME_IN_ENGLISH")
                or r.get("COMPANY_ABBR_EN")
                or r.get("COMPANY_ABBR")
                or ""
            ).strip()
            if not code or not nm:
                continue
            seg = "main" if (r.get("LIST_BOARD") or "1") == "1" else "star"
            rows.append(
                {
                    "exchange": "SSE",
                    "symbol": code + ".SS",
                    "name": nm,
                    "segment": seg,
                    "asset_type": "equity",
                }
            )
        if p % 10 == 0:
            print(f"[sse] page {p} rows {len(rows)}", flush=True)
        time.sleep(2.0)
    return rows


def fetch_twse() -> list[Row]:
    """TWSE ISIN page: Page param is IGNORED (whole universe per fetch)."""
    url = (
        "https://isin.twse.com.tw/isin/e_class_main.jsp?owncode=&stockname="
        "&code3=&code=&market=1&industry=&Page=1&Language=en"
    )
    t = _get(url)
    keep = ("Stocks", "Real Estate Investment Trust (REIT)", "TDR")
    out: list[Row] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        c = [
            re.sub(r"<[^>]+>", "", x).strip()
            for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        ]
        if len(c) >= 6 and re.match(r"^TW[A-Z0-9]{10}$", c[1] or "") and c[5] in keep:
            seg = "tdr" if c[5] == "TDR" else ("reit" if "REIT" in c[5] else "main")
            out.append(
                {
                    "exchange": "TWSE",
                    "symbol": (c[2] or "").strip(),
                    "name": (c[3] or "").strip(),
                    "isin": c[1],
                    "segment": seg,
                    "asset_type": "equity",
                }
            )
    return out


def pick_wiki_table(html: str) -> tuple[list[str], list[str]] | None:
    """Pick wikitables whose header row contains ticker/symbol/code —
    year-history tables are decoys."""
    for tb in re.findall(r"<table[^>]*wikitable[^>]*>(.*?)</table>", html, re.S):
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S)
        if not trs:
            continue
        hdr = [
            re.sub(r"<[^>]+>", "", x).strip().lower()
            for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", trs[0], re.S)
        ]
        if any(h in ("ticker", "symbol", "code", "stock code") for h in hdr):
            return hdr, trs
    return None


def parse_wiki_table(html: str) -> list[tuple[str, str]]:
    """(ticker, company) pairs: ticker column by header name, company
    column by first header containing company/name."""
    picked = pick_wiki_table(html)
    if not picked:
        return []
    hdr, trs = picked
    ti = next((i for i, h in enumerate(hdr) if h in ("ticker", "symbol", "code", "stock code")), 0)
    ni = next((i for i, h in enumerate(hdr) if "company" in h or "name" in h), None)
    out = []
    for r in trs[1:]:
        c = [
            re.sub(r"<[^>]+>", "", x).strip()
            for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
        ]
        if len(c) <= max(ti, ni or 0) or not c[ti]:
            continue
        out.append((c[ti], c[ni] if ni is not None else c[ti]))
    return out


WIKI_LANES = {
    "FTSE_100_Index": ("LSE", "ftse100"),
    "DAX": ("XETRA", "dax40"),
    "CAC_40": ("EURONEXT", "cac40"),
    "S&P/TSX_60": ("TSX", "tsx60"),
}


def fetch_wiki() -> list[Row]:
    rows: list[Row] = []
    for page, (exch, seg) in WIKI_LANES.items():
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page, safe="/")
        try:
            html = _get(url)
        except Exception as e:  # noqa: BLE001 - lane isolation
            print(f"[wiki] {page}: fetch failed {str(e)[:60]}", flush=True)
            continue
        for tick, name in parse_wiki_table(html):
            suffix = ".TO" if exch == "TSX" else ""
            rows.append(
                {
                    "exchange": exch,
                    "symbol": tick + suffix,
                    "name": name,
                    "segment": seg,
                    "asset_type": "equity",
                }
            )
    return rows


def ingest_tmx(paths: list[Path]) -> list[Row]:
    """TMX operator xlsx: header at row 10, columns shift per board sheet;
    ETP/Closed-End/Fund/Structured/Trust sectors filtered (purity)."""
    import openpyxl

    sheets = [(0, 2, 3, 6, 7, "tsx", ".TO"), (1, 3, 4, 8, 9, "tsxv", ".V")]
    drop = ("ETP", "Closed-End", "Fund", "Structured", "Trust")
    out: list[Row] = []
    for path in paths:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for si, nc, tc, sc, _uc, seg, sfx in sheets:
            if si >= len(wb.sheetnames):
                continue
            ws = wb[wb.sheetnames[si]]
            for r in ws.iter_rows(min_row=11, values_only=True):
                name = (r[nc] or "").strip() if len(r) > nc else ""
                tick = (r[tc] or "").strip() if len(r) > tc else ""
                sector = (r[sc] or "").strip() if len(r) > sc else ""
                if not name or not tick:
                    continue
                if any(d.lower() in sector.lower() for d in drop):
                    continue
                out.append(
                    {
                        "exchange": "TSX",
                        "symbol": tick + sfx,
                        "name": name,
                        "industry": sector,
                        "segment": seg,
                        "asset_type": "equity",
                    }
                )
        wb.close()
    return out


def ingest_krx(path: Path) -> list[Row]:
    """Operator-curated KRX json ([{symbol|ticker, name|company, sector?}])."""
    return [
        {
            "exchange": "KRX",
            "symbol": (r.get("symbol") or r.get("ticker") or "").strip(),
            "name": (r.get("name") or r.get("company") or "").strip(),
            "industry": r.get("sector"),
            "segment": "kospi",
            "asset_type": "equity",
        }
        for r in json.loads(path.read_text())
    ]


def fetch_nse() -> list[Row]:
    """NSE EQUITY_L.csv master: SYMBOL/NAME/SERIES/ISIN. Series EQ/BE/BZ
    -> main, SM/YM/ST -> sme. Names keep their Ltd/Limited form (store
    convention)."""
    import csv as _csv
    import io

    b = _get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", binary=True)
    text = b.decode("utf-8-sig", "replace") if isinstance(b, bytes) else b
    out: list[Row] = []
    for _r in _csv.DictReader(io.StringIO(text)):
        r = {k.strip(): v for k, v in _r.items()}  # header has stray spaces
        sym = (r.get("SYMBOL") or "").strip()
        nm = (r.get("NAME OF COMPANY") or "").strip()
        series = (r.get("SERIES") or "").strip().upper()
        isin = (r.get("ISIN NUMBER") or "").strip()
        if not sym or not nm or not isin.startswith("IN"):
            continue
        seg = "sme" if series in ("SM", "YM", "ST", "SX") else "main"
        out.append(
            {
                "exchange": "NSE",
                "symbol": sym,
                "name": nm,
                "isin": isin,
                "segment": seg,
                "asset_type": "equity",
                "ticker": sym + ".NS",
            }
        )
    return out


def fetch_bse() -> list[Row]:
    """BSE ListofScripData JSON, Equity segment, ALL statuses (store
    breadth). SME scrips are not distinguishable in this API — the store
    keeps its D14 sme rows and new SME listings fold as main (the NSE
    lane carries series-accurate sme). Delisted/suspended rows fold but
    never flag gone (active flag)."""
    b = _get(
        "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?"
        "Group=&Scripcode=&industry=&segment=Equity",
        binary=True,
        referer="https://www.bseindia.com/",
    )
    data = json.loads(b if isinstance(b, str) else b.decode("utf-8", "replace"))
    out: list[Row] = []
    for r in data:
        if str(r.get("Segment") or "") not in ("Equity", ""):
            continue  # skip PreferenceShares etc.
        if str(r.get("Status") or "") != "Active":
            continue  # delisted/suspended history adds noise, never listings
        sym = str(r.get("SCRIP_CD") or "").strip()
        nm = (r.get("Scrip_Name") or "").strip()
        isin = (r.get("ISIN_NUMBER") or "").strip()
        if not sym or not nm or not isin.startswith("IN"):
            continue
        out.append(
            {
                "exchange": "BSE",
                "symbol": sym,
                "name": nm,
                "isin": isin,
                "segment": "main",
                "asset_type": "equity",
                "ticker": sym + ".BO",
                "active": str(r.get("Status") or "") == "Active",
            }
        )
    return out


_OTHER_EXCH = {"A": "NYSEAMER", "N": "NYSE", "P": "NYSEARCA", "Z": "BATS", "V": "IEX"}


def fetch_us() -> list[Row]:
    """US masters via the nasdaqtrader FTP symboldirectory (the HTTP
    symdir paths are walled): nasdaqlisted.txt + otherlisted.txt. Test
    issues and ETFs excluded (universe purity, TSX-rule parity)."""
    out: list[Row] = []

    def _pipe(data: bytes) -> list[dict]:
        lines = data.decode("utf-8", "replace").splitlines()
        hdr = [h.strip() for h in lines[0].split("|")]
        return [
            dict(zip(hdr, ln.split("|")))
            for ln in lines[1:]
            if ln.strip() and not ln.startswith("File Creation Time")
        ]

    with urllib.request.urlopen(  # noqa: S310  # https-only archive/file downloads
        "ftp://ftp.nasdaqtrader.com/symboldirectory/nasdaqlisted.txt", timeout=120
    ) as r:
        nas = _pipe(r.read())
    for r in nas:
        if (r.get("Test Issue") or "").strip() != "N":
            continue
        if (r.get("ETF") or "").strip() == "Y" or (r.get("NextShares") or "").strip() == "Y":
            continue
        sym = (r.get("Symbol") or "").strip()
        nm = (r.get("Security Name") or "").strip()
        if not sym or not nm:
            continue
        out.append(
            {
                "exchange": "NASDAQ",
                "symbol": sym,
                "name": nm,
                "segment": "main",
                "asset_type": "equity",
                "ticker": sym,
            }
        )
    with urllib.request.urlopen(  # noqa: S310  # https-only archive/file downloads
        "ftp://ftp.nasdaqtrader.com/symboldirectory/otherlisted.txt", timeout=120
    ) as r:
        oth = _pipe(r.read())
    for r in oth:
        if (r.get("Test Issue") or "").strip() != "N":
            continue
        if (r.get("ETF") or "").strip() == "Y":
            continue
        sym = (r.get("ACT Symbol") or r.get("Symbol") or "").strip()
        nm = (r.get("Security Name") or "").strip()
        exch = _OTHER_EXCH.get((r.get("Exchange") or "").strip())
        if not sym or not nm or not exch:
            continue
        out.append(
            {
                "exchange": exch,
                "symbol": sym,
                "name": nm,
                "segment": "main",
                "asset_type": "equity",
                "ticker": sym,
            }
        )
    return out


LANES: dict[str, Callable[[], list[Row]]] = {
    "hkex": fetch_hkex,
    "szse": fetch_szse,
    "sse": fetch_sse,
    "twse": fetch_twse,
    "wiki": fetch_wiki,
    "nse": fetch_nse,
    "bse": fetch_bse,
    "us": fetch_us,
}

# Incremental-by-default (operator ruling 2026-09-16: bulk re-fetching
# risks upstream blocks). A lane whose rows were all fetched within its
# max age is skipped entirely; --force refetches, --max-age-days N
# overrides the default for every lane in one run.
LANE_MAX_AGE_DAYS = {"nse": 25, "bse": 25}
DEFAULT_MAX_AGE_DAYS = 80
LANE_EXCHANGES = {
    "hkex": ["HKEX"],
    "szse": ["SZSE"],
    "sse": ["SSE"],
    "twse": ["TWSE"],
    "nse": ["NSE"],
    "bse": ["BSE"],
    "us": ["NASDAQ", "NYSE", "NYSEAMER", "NYSEARCA", "BATS"],
}
LANE_SEGMENTS = {  # wiki spans 4 exchanges; TSX also fed by tmx lane
    "wiki": [("LSE", "ftse100"), ("XETRA", "dax40"), ("EURONEXT", "cac40"), ("TSX", "tsx60")],
}


def lane_fresh(con: duckdb.DuckDBPyConnection, lane: str, max_age: int) -> bool:
    """True when every exchange(/segment) the lane writes has a
    ``fetched_at`` within ``max_age`` days (missing data = not fresh)."""
    cutoff = date.today() - timedelta(days=max_age)
    pairs = LANE_SEGMENTS.get(lane)
    fallback = LANE_EXCHANGES.get(lane, [lane.upper()])
    probes: list[tuple[str, str | None]] = (
        [(e, seg) for e, seg in pairs] if pairs else [(e, None) for e in fallback]
    )
    for exch, seg in probes:
        if seg is None:
            row = con.execute(
                "select max(fetched_at) from exchange_listings where exchange = ?",
                [exch],
            ).fetchone()
        else:
            row = con.execute(
                "select max(fetched_at) from exchange_listings where exchange = ? and segment = ?",
                [exch, seg],
            ).fetchone()
        if not row or not row[0] or row[0] < cutoff:
            return False
    return True


# ------------------------------------------------------------------ store


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB))
    cols = [r[0] for r in con.execute("describe exchange_listings").fetchall()]
    if "fetched_at" not in cols:
        con.execute("alter table exchange_listings add column fetched_at date")
        con.execute(
            "update exchange_listings set fetched_at = ? where fetched_at is null", [BACKFILL_DATE]
        )
    return con


def fold(
    con: duckdb.DuckDBPyConnection, rows: list[Row], run_date: str, apply: bool
) -> tuple[list[Row], list[Row]]:
    """Diff vs store, then insert new rows idempotently. Returns
    (new_rows, gone_rows) — gone = store symbols absent from the master
    (rename/delisting flags, review-only)."""
    if not rows:
        return [], []
    new_rows: list[Row] = []
    gone_rows: list[Row] = []
    by_exch: dict[str, list[Row]] = {}
    for r in rows:
        by_exch.setdefault(r["exchange"], []).append(r)
    for exch, grp in by_exch.items():
        sub_new, sub_gone = _fold_one(con, grp, run_date, apply)
        new_rows += sub_new
        gone_rows += sub_gone
    return new_rows, gone_rows


def _fold_one(
    con: duckdb.DuckDBPyConnection, rows: list[Row], run_date: str, apply: bool
) -> tuple[list[Row], list[Row]]:
    exch = rows[0]["exchange"]
    segs = sorted({r["segment"] for r in rows})
    store = con.execute(
        "select symbol, segment from exchange_listings where exchange = ?"  # noqa: S608  # placeholders only — constant SQL + ? segments
        " and segment in (" + ",".join("?" * len(segs)) + ")",
        [exch, *segs],
    ).fetchall()
    store_keys = {(exch, s, g) for s, g in store}
    fetched = {(r["exchange"], r["symbol"], r["segment"]) for r in rows}
    new_rows = [r for r in rows if (r["exchange"], r["symbol"], r["segment"]) not in store_keys]
    gone = sorted(store_keys - fetched)
    if apply and new_rows:
        for r in new_rows:
            con.execute(
                "insert into exchange_listings (isin, name, industry, asset_type,"
                " exchange, segment, symbol, fetched_at) values (?,?,?,?,?,?,?,?)",
                [
                    r.get("isin"),
                    r.get("name"),
                    r.get("industry"),
                    r.get("asset_type", "equity"),
                    r["exchange"],
                    r["segment"],
                    r["symbol"],
                    run_date,
                ],
            )
    return new_rows, [{"exchange": e, "symbol": s, "segment": g} for e, s, g in gone]


def seed_stubs(
    new_rows: list[Row], apply: bool, con: sqlite3.Connection | None = None
) -> list[tuple[str, str]]:
    """D14 seeder (promoted): CHECK-safe entity stubs, INSERT OR IGNORE.
    Rejects (CHECK/normalization) are skipped to the worklist, not fatal."""
    made: list[tuple[str, str]] = []
    if not new_rows:
        return made
    own = con is None
    con = con or connect(REPO / "memory" / "research.db")
    cur = con.cursor()
    for r in new_rows:
        nm = clean_name(r.get("name") or "")
        if not nm or len(nm) < 3:
            continue
        try:
            # lanes without an explicit ticker (wiki/hkex/szse/twse/sse)
            # seed the exchange symbol as ticker — never NULL (NULL-ticker
            # stubs collided with the SSE junk-cleanup signature 2026-09-16)
            tk = r.get("ticker") or r["symbol"]
            # D19 dedupe guard: an entity already holding this ticker is the
            # same listed company (NSE/BSE name variants, note rows) — never
            # seed a second stub for it (duplicate-ticker bug class, 2026-09-16).
            if (
                tk
                and cur.execute(
                    "select 1 from entities where ticker = ? and entity_type = 'company'",
                    (tk,),
                ).fetchone()
            ):
                continue
            cur.execute(
                "insert or ignore into entities (name, ticker, normalized_name,"
                " entity_type) values (?,?,?,?)",
                (nm, tk, normalized_name(nm), "company"),
            )
            if cur.rowcount:
                made.append((nm, r.get("ticker") or r["symbol"]))
        except sqlite3.IntegrityError:
            continue
    con.commit()
    if own:
        con.close()
    return made


def write_worklist(new_rows: list[Row], gone: list[Row], seeded: list[tuple[str, str]]) -> None:
    """Merge with the previous worklist — a per-run overwrite clobbered
    the TSX60 evidence during the 2026-09-16 SSE re-run. Old rows whose
    key is not superseded by this run stay (review continuity)."""
    WORKLIST.parent.mkdir(parents=True, exist_ok=True)
    seed_map = {n: t for n, t in seeded}
    fresh = []
    for r in new_rows:
        nm = clean_name(r.get("name") or "")
        fresh.append(
            [
                r["exchange"],
                r["symbol"],
                r.get("name", ""),
                r["segment"],
                "new",
                seed_map.get(nm, ""),
            ]
        )
    for r in gone:
        fresh.append([r["exchange"], r["symbol"], "", r["segment"], "gone-review", ""])
    fresh_keys = {(r[0], r[1], r[3], r[4]) for r in fresh}
    kept = []
    if WORKLIST.exists():
        with WORKLIST.open(newline="") as fh:
            rd = csv.reader(fh)
            next(rd, None)
            for row in rd:
                if len(row) == 6 and (row[0], row[1], row[3], row[4]) not in fresh_keys:
                    kept.append(row)
    with WORKLIST.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exchange", "symbol", "name", "segment", "status", "seeded_entity"])
        w.writerows(fresh + kept)


def main() -> None:  # noqa: C901  # arg-dispatch main (flag-per-lane CLI)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "lanes",
        nargs="*",
        choices=sorted(LANES) + ["tmx", "krx"],
        help="lanes to run (default: all scriptable)",
    )
    ap.add_argument(
        "--apply", action="store_true", help="write store + seed stubs (default: dry-run)"
    )
    ap.add_argument(
        "--tmx-file", type=Path, action="append", default=[], help="TMX xlsx path (tmx lane)"
    )
    ap.add_argument("--krx-file", type=Path, help="KRX json path (krx lane)")
    ap.add_argument("--no-seed", action="store_true", help="skip stub seeding (store refresh only)")
    ap.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="freshness override for every lane (default: nse/bse 25d, rest 80d)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="refetch even when a lane is fresh (bypass incremental default)",
    )
    args = ap.parse_args()
    lanes = args.lanes or sorted(LANES)
    run_date = time.strftime("%Y-%m-%d")
    # fetch phase: NO db connection held (a slow lane must never lock out
    # readers — operator directive 2026-09-16); fold connects briefly.
    all_new: list[Row] = []
    all_gone: list[Row] = []
    seeded: list[tuple[str, str]] = []
    for lane in lanes:
        if lane not in ("tmx", "krx") and not args.force:
            max_age = (
                args.max_age_days
                if args.max_age_days is not None
                else LANE_MAX_AGE_DAYS.get(lane, DEFAULT_MAX_AGE_DAYS)
            )
            con = _connect()
            try:
                fresh = lane_fresh(con, lane, max_age)
            finally:
                con.close()
            if fresh:
                print(
                    f"[{lane}] skipped (fetched within {max_age}d — incremental default, --force refetches)",
                    flush=True,
                )
                continue
        try:
            if lane == "tmx":
                rows = ingest_tmx(args.tmx_file) if args.tmx_file else []
            elif lane == "krx":
                rows = ingest_krx(args.krx_file) if args.krx_file else []
            else:
                rows = LANES[lane]()
        except OSError as e:
            print(f"[{lane}] FETCH-FAILED: {str(e)[:80]}", flush=True)
            rows = []
        con = _connect()
        try:
            new, gone = fold(con, rows, run_date, args.apply)
        except OSError as e:  # endpoint/store failures never block other lanes
            print(f"[{lane}] FAILED: {str(e)[:80]}")
            con.close()
            all_gone.append(
                {"exchange": lane, "symbol": "", "segment": "", "status": "lane-failed"}
            )
            continue
        con.close()
        all_new += new
        all_gone += gone
        print(f"[{lane}] fetched={len(rows)} new={len(new)} gone={len(gone)}")
    if args.apply and not args.no_seed:
        seeded = seed_stubs(all_new, apply=True)
        print(f"stubs seeded: {len(seeded)}")
        for n, t in seeded[:10]:
            print(f"  + {n} ({t})")
    elif all_new and not args.apply:
        print(f"DRY-RUN: {len(all_new)} new rows not written (use --apply)")
    elif all_new and args.no_seed:
        print(f"store-only: {len(all_new)} rows folded, stub seeding skipped")
    write_worklist(all_new, all_gone, seeded)
    if seeded:
        print("hooks: mca_cin_resolve.py ogd (Indian stubs) | enrich_from_yfinance (labels)")


if __name__ == "__main__":
    main()
