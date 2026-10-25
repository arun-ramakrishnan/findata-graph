#!/usr/bin/env python3
"""Shareholding-pattern ingestion lane (ownership_ingestion_nse_shp.md,
bse_shareholding_rss.md): NSE RSS + XBRL, BSE RSS + HTML →
``sources.duckdb::shp_filings`` / ``shp_holders`` → holder entities +
``invested_in`` edges (and ``same_group`` from shared corporate holders)
in the research store.

Execution-shape note (2026-09-22, verified on three live filings): the
PUBLIC XBRL masks ``PermanentAccountNumberOfShareholder`` and
``TypeOfPromoterShareholding`` (all rows ``******``) — named rows sit
under SEBI public categories (IndividualsOrHUF, BodiesCorporate,
MutualFundsOrUTI, ...) and promoter membership is aggregated only. So
member-level ``promoter_of`` edges are NOT derivable from this feed;
the accessible ownership mass is:

- named holders with exact stakes + native filing periods →
  ``invested_in`` edges (person / institution / reconciled company
  holders), weight = stake percent, ``source_tier='regulator'``;
- bodies-corporate holders ≥ ``--same-group-min`` (default 10%) shared
  by two companies → ``same_group`` edges (group/holdco structure);
- promoter aggregate percent per filing → ``shp_filings.promoter_pct``
  (company attribute; PR_AND_PRGRP cross-check vs the RSS scalars).

Lanes (stability register in doc/design/data_sources.md):
- nsearchives RSS + XBRL: open host, no bot wall (browser UA, low
  frequency; quarterly + revision cadence).

Cadence discipline: RSS poll is incremental; bulk backfill beyond the
RSS window needs a filings-search lane (main-site is bot-walled) — see
``--xml`` for direct ingestion of known filing URLs in the meantime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, UTC
from pathlib import Path

import defusedxml.ElementTree as ET
import duckdb

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SOURCES_DB = REPO / "memory" / "data" / "sources.duckdb"
RAW_DIR = REPO / "memory" / "data" / "shp_raw"
WORKLIST = REPO / "memory" / "data" / "shp_worklist.csv"
FETCH_SCRATCH = (
    Path(os.environ.get("TMPDIR", "/mnt/data/tmp/opencode")) / "findata-graph" / "shp_fetch_scratch"
)
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml"
BSE_RSS_URL = "https://www.bseindia.com/Data/XML/ShareholdingPattern_Feed.aspx"
UA = {"User-Agent": "Mozilla/5.0 research", "Referer": "https://nsearchives.nseindia.com/"}
SOURCE_REF_PREFIX = "nse:shp"

XBRLI = "{http://www.xbrl.org/2003/instance}"
_PCT = "ShareholdingAsAPercentageOfTotalNumberOfShares"
_SHARES = "NumberOfFullyPaidUpEquityShares"
_VOTING = "PercentageOfTotalVotingRights"
_NAME = "NameOfTheShareholder"
_PAN = "PermanentAccountNumberOfShareholder"
_PLEDGED = "NumberOfSharesEncumberedUnderPledged"
_PERSON_CATEGORIES = (
    "IndividualsOrHuf",
    "IndividualsOrHUF",
    "ResidentIndividual",
    "KeyManagerialPersonnel",
)
_CORPORATE_CATEGORY = "BodiesCorporate"

# R6b (graph_relational_depth, 2026-10-25): shareholding-page STRUCTURAL
# rows are category holders, not investors. The NSE XBRL path filters them
# by category axis; the BSE HTML path mints them (t3 subtotal/equation rows
# carry subcategory headers like 'Any Other (specify)' as their category),
# so detection is NAME-based and every invested_in mint path marks matches
# with properties.category_holder=true. Majority promotion and participation
# consumers filter on the marker / name test — never on the mixed-kind
# weight column.
_CATEGORY_HOLDER_NAMES = frozenset(
    {
        "office bearers",
        "foreign portfolio investor (individual)",
        "foreign portfolio investors",
        "mutual funds",
        "banks",
        "financial institutions",
        "insurance companies",
        "central government",
        "state government",
        "venture capital funds",
        "foreign collaborators",
        "non-resident indians",
        "bodies corporate",
        "employee welfare trusts",
        "clearing members",
        "stock lenders",
        "any other (specify)",
    }
)
_CATEGORY_HOLDER_RE = re.compile(
    r"^\(?\s*sub[-\s]?total\b"  # Sub-Total (A)(1) … promoter/public aggregates
    # paren/shareholding forms only — 'Total Gas Limited' is a NAMED holder
    r"|^\(?\s*total\s*\("  # Total (A+B+C), Total ( A+B+C2 )
    r"|^\(?\s*total shareholding\b"  # Total Shareholding of Promoter …
    r"|^\(?\s*(grand|net)\s+total\b"
    r"|^\([a-z0-9]+\)\s*="  # (B)=(B)(1)+(B)(2)+… equation rows
)


def is_category_holder(name: str) -> bool:
    """True for shareholding-page structural rows — subtotal/total/equation
    lines and standard subcategory labels (aggregates, not investors)."""
    n = " ".join((name or "").casefold().split())
    return bool(_CATEGORY_HOLDER_RE.match(n)) or n in _CATEGORY_HOLDER_NAMES


def stake_pct_of(properties_json: str | None) -> float | None:
    """Participation weight accessor — the semantic stake in PERCENT.

    ``pctHeld`` (yfinance holders leg) is a fraction → ×100; ``stake_pct``
    (shp filings leg) is already percent. The weight COLUMN is mixed-kind
    across legs (stake on shp, 1.0/3.0 ranking on yfinance) — influence
    queries read this, never the column.
    """
    try:
        d = json.loads(properties_json or "{}")
    except TypeError, ValueError:
        return None
    v = d.get("pctHeld")
    if v is not None:
        try:
            return round(float(v) * 100.0, 4)
        except TypeError, ValueError:
            return None
    v = d.get("stake_pct")
    if v is not None:
        try:
            return round(float(v), 4)
        except TypeError, ValueError:
            return None
    return None


_DDL = [
    """CREATE TABLE IF NOT EXISTS shp_filings (
        filing_id VARCHAR PRIMARY KEY,
        symbol VARCHAR, company_name VARCHAR, url VARCHAR,
        period_start DATE, period_end DATE,
        submission_dt VARCHAR, revision_dt VARCHAR, revised_status VARCHAR,
        promoter_pct DOUBLE, public_pct DOUBLE, emptr DOUBLE,
        fetched_at TIMESTAMP, content_sha VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS shp_holders (
        filing_id VARCHAR, ctx VARCHAR, named INTEGER,
        category VARCHAR, holder_name VARCHAR, holder_pan VARCHAR,
        shares BIGINT, stake_pct DOUBLE, voting_pct DOUBLE,
        pledged_shares BIGINT
    )""",
]


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def _get_raw(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers=dict(UA))  # noqa: S310  # https-only archives lane
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # https-only archives lane
        return r.read()


def _get_text(url: str, timeout: int = 45) -> str:
    return _get_raw(url, timeout).decode("utf-8", "replace")


def _filing_id(url: str) -> str:
    """``.../SHP_1726099_21092026064713_WEB.xml`` → ``SHP_1726099_21092026064713_WEB``."""
    return url.rsplit("/", 1)[-1].removesuffix(".xml")


def _cache_path(filing_id: str) -> Path:
    return RAW_DIR / f"{filing_id}.xml.zst"


def fetch_xbrl_cached(url: str, *, refetch: bool = False) -> bytes:
    """Fetch an XBRL filing, zstd-cached content-addressed by filing id."""
    from helpers.core.zstd_io import compress_file, decompress_file

    fid = _filing_id(url)
    cpath = _cache_path(fid)
    if cpath.is_file() and not refetch:
        tmp = cpath.with_suffix(".tmp")
        decompress_file(cpath, tmp)
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data
    data = _get_raw(url)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cpath.with_suffix(".tmp")
    tmp.write_bytes(data)
    compress_file(tmp, cpath)
    tmp.unlink(missing_ok=True)
    return data


def parse_rss(xml_text: str) -> list[dict]:
    """RSS items → [{company, url, promoter_pct, public_pct, emptr,
    as_on, submission_dt, revision_dt, revised_status}] (scalars are
    cross-checks; the XBRL is the source of truth)."""
    # https-only nsearchives lane (stability register) — same trust
    # level as the CSV lane's S310 exemption. Parsed with defusedxml:
    # DTD/entity expansion is forbidden at the parser (enforced), not
    # assumed away about the feed content (review b28991d54..c777fa9b1,
    # operator-accepted 2026-10-11).
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if "/xbrl/SHP_" not in link:
            continue
        desc = item.findtext("description") or ""
        scalars = {
            k.strip(): v.strip() for k, v in re.findall(r"([A-Z][A-Z_ ]*?)\s*:\s*([^|]+)", desc)
        }

        def _num(key: str) -> float | None:
            v = scalars.get(key, "").strip()
            try:
                return float(v) if v not in ("", "-") else None
            except ValueError:
                return None

        out.append(
            {
                "company": (item.findtext("title") or "").strip(),
                "url": link,
                "promoter_pct": _num("PR_AND_PRGRP"),
                "public_pct": _num("PUBLIC_VAL"),
                "emptr": _num("EMPTR"),
                "as_on": scalars.get("AS ON DATE", "").strip(),
                "submission_dt": scalars.get("SUBMISSION_DT", "").strip(),
                "revision_dt": scalars.get("REVISION_DT", "").strip(),
                "revised_status": scalars.get("NDS_REVISED_STATUS", "").strip(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# parse — joint in-bse-shp XBRL
# --------------------------------------------------------------------------- #
def _ctx_category(ctx_id: str) -> str:
    """``D_IndividualsOrHUF_Context15`` → ``IndividualsOrHUF``."""
    cid = ctx_id[2:] if ctx_id.startswith("D_") else ctx_id
    return cid.rsplit("_Context", 1)[0]


def parse_shp(xml_bytes: bytes) -> dict:
    """Parse one ``in-bse-shp`` filing into filing meta + holder rows.

    Structure (verified on three live filings, SHP V1.2):
    - ``MainD``/``MainI`` contexts carry the Symbol identifier and the
      filing period (duration + instant);
    - ``<Category>_ContextI`` contexts carry category AGGREGATE numerics
      (48 in the sample; ``ShareholdingOfPromoterAndPromoterGroup`` is
      the promoter total);
    - ``D_<Category>_Context<N>`` contexts carry the named-holder
      identity facts (name, PAN, promoter-type — masked in the public
      feed), pairing with ``<Category>_Context<N>`` for the numerics.
    """
    # https-only nsearchives lane (see parse_rss note)
    root = ET.fromstring(xml_bytes)
    ctxs: dict[str, dict] = {}
    for c in root.iter(XBRLI + "context"):
        cid = c.get("id") or ""
        ident = c.find(".//" + XBRLI + "identifier")
        per = c.find(XBRLI + "period")
        instant = per.findtext(XBRLI + "instant") if per is not None else None
        start = per.findtext(XBRLI + "startDate") if per is not None else None
        end = per.findtext(XBRLI + "endDate") if per is not None else None
        ctxs[cid] = {
            "symbol": ident.text.strip() if ident is not None and ident.text else None,
            "instant": instant,
            "start": start,
            "end": end,
        }
    facts: dict[str, dict[str, str]] = {}
    for el in root:
        if not isinstance(el.tag, str) or not el.tag.startswith("{"):
            continue
        cr = el.get("contextRef")
        if not cr or el.text is None or not el.text.strip():
            continue
        ln = el.tag.rsplit("}", 1)[-1]
        facts.setdefault(cr, {})[ln] = el.text.strip()

    main = ctxs.get("MainI") or ctxs.get("MainD") or {}
    main_d = ctxs.get("MainD") or main
    symbol = main.get("symbol")
    period_end = main.get("instant") or main_d.get("end")
    period_start = main_d.get("start")

    named: list[dict] = []
    aggregates: dict[str, float] = {}
    for cr, fdict in facts.items():
        cat = _ctx_category(cr)
        if cr.startswith("D_"):
            num = facts.get(cr[2:], {})
            if _NAME not in fdict:
                continue
            named.append(
                {
                    "ctx": cr,
                    "category": cat,
                    "holder_name": fdict.get(_NAME, ""),
                    "holder_pan": fdict.get(_PAN) if (fdict.get(_PAN) or "").strip("*") else None,
                    "shares": _to_int(num.get(_SHARES) or num.get("NumberOfShares")),
                    # XBRL stores fractions (root ShareholdingPattern = 1.0);
                    # normalize to PERCENT to match the RSS scalars and the
                    # "weight = stake %" convention.
                    "stake_pct": _pct100(num.get(_PCT)),
                    "voting_pct": _pct100(num.get(_VOTING)),
                    "pledged_shares": _to_int(num.get(_PLEDGED)),
                }
            )
        elif cr.endswith("_ContextI"):
            pct = _pct100(fdict.get(_PCT))
            if pct is not None:
                aggregates[cat] = pct

    total = sum(aggregates.values())  # informational: axes form a
    # hierarchy (root/promoter/public/...) so the naive sum double-counts;
    # the validation anchors are root==100 and promoter+public==100.
    promoter_pct = aggregates.get("ShareholdingOfPromoterAndPromoterGroup")
    return {
        "symbol": symbol,
        "period_start": period_start,
        "period_end": period_end,
        "promoter_pct": promoter_pct,
        "category_total_pct": round(total, 4),
        "aggregates": aggregates,
        "named": named,
    }


def _to_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    """COUNT-style scalar: 0 when the table is empty/missing."""
    row = con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _pct100(v: str | None) -> float | None:
    """XBRL fraction (0..1) → percent, rounded to 4dp; None-safe."""
    f = _to_float(v)
    return round(f * 100.0, 4) if f is not None else None


def _to_int(v: str | None) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


# --------------------------------------------------------------------------- #
# store — sources.duckdb
# --------------------------------------------------------------------------- #
def store_filing(con: duckdb.DuckDBPyConnection, url: str, parsed: dict, rss: dict | None) -> None:
    fid = _filing_id(url)
    import hashlib

    con.execute(
        "INSERT OR REPLACE INTO shp_filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'NSE')",
        [
            fid,
            parsed["symbol"],
            (rss or {}).get("company"),
            url,
            parsed["period_start"],
            parsed["period_end"],
            (rss or {}).get("submission_dt"),
            (rss or {}).get("revision_dt") or None,
            (rss or {}).get("revised_status") or None,
            parsed["promoter_pct"],
            (rss or {}).get("public_pct"),
            (rss or {}).get("emptr"),
            datetime.now(UTC).isoformat(timespec="seconds"),
            hashlib.sha256(str(parsed["aggregates"]).encode()).hexdigest()[:16],
        ],
    )
    con.execute("DELETE FROM shp_holders WHERE filing_id = ?", [fid])
    rows = [
        (
            fid,
            h["ctx"],
            1,
            h["category"],
            h["holder_name"],
            h["holder_pan"],
            h["shares"],
            h["stake_pct"],
            h["voting_pct"],
            h["pledged_shares"],
        )
        for h in parsed["named"]
    ] + [
        (fid, cat + "_ContextI", 0, cat, None, None, None, pct, None, None)
        for cat, pct in parsed["aggregates"].items()
    ]
    if rows:
        con.executemany("INSERT INTO shp_holders VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def ensure_sources_schema(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in _DDL:
        con.execute(ddl)
    # bse_shareholding_rss S3: exchange provenance on the shared store
    con.execute("ALTER TABLE shp_filings ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'NSE'")


# --------------------------------------------------------------------------- #
# BSE lane (bse_shareholding_rss.md): RSS mirror + per-filing HTML pages
# --------------------------------------------------------------------------- #
import html as _html  # noqa: E402  (parser-local)

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S)


def _cells(row_html: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
        for c in _TD.findall(row_html)
    ]


def _filing_id_bse(url: str) -> str:
    """``.../513108_229202617213_SP.html`` → ``BSE_513108_229202617213``."""
    stem = url.rsplit("/", 1)[-1].removesuffix("_SP.html")
    return f"BSE_{stem}"


def fetch_bse_html_cached(url: str, *, refetch: bool = False) -> str:
    """Per-filing HTML download, zstd-cached (bse_ prefix beside the NSE cache)."""
    from helpers.core.zstd_io import compress_file, decompress_file

    fid = _filing_id_bse(url)
    cpath = RAW_DIR / f"{fid}.html.zst"
    if cpath.is_file() and not refetch:
        tmp = cpath.with_name(fid + ".tmp.html")
        decompress_file(cpath, tmp)
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data.decode("utf-8", "replace")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    data = _get_raw(url)
    tmp = cpath.with_name(fid + ".tmp.html")
    tmp.write_bytes(data)
    compress_file(tmp, cpath)
    tmp.unlink(missing_ok=True)
    return data.decode("utf-8", "replace")


def parse_bse_rss(xml_text: str) -> list[dict]:
    """BSE RSS items → [{company, scrip, url, scalars...}]."""
    root = ET.fromstring(xml_text)  # https-only bseindia lane (see parse_rss note)
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if "SHPXBRLDataXML" not in link:
            continue
        desc = item.findtext("description") or ""
        scalars = {
            k.strip(): v.strip() for k, v in re.findall(r"([A-Z][A-Z_ ]*?)\s*:\s*([^|]+)", desc)
        }
        m = re.search(r"\((\d{6})\)\s*$", (item.findtext("title") or "").strip())

        def _num(key: str) -> float | None:
            v = scalars.get(key, "").strip().rstrip("%")
            try:
                return float(v) if v not in ("", "-") else None
            except ValueError:
                return None

        out.append(
            {
                "company": re.sub(r"\s*\(\d{6}\)\s*$", "", (item.findtext("title") or "").strip()),
                "scrip": m.group(1) if m else None,
                "url": link if link.startswith("http") else "https://www.bseindia.com" + link,
                "promoter_pct": _num("PR_AND_PRGRP"),
                "public_pct": _num("PUBLIC_VAL"),
                "as_on": scalars.get("AS ON DATE", ""),
                "submission_dt": scalars.get("SUBMISSION_DT", ""),
                "revision_dt": scalars.get("REVISED FILING DATE", ""),
                "revised_status": scalars.get("STATUS", ""),
            }
        )
    return out


def _iso_date_bse(v: str) -> str | None:
    """'22-09-2026' → '2026-09-22' (BSE dd-mm-yyyy; ISO passthrough)."""
    v = (v or "").strip()
    if not v or v == "-":
        return None
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})$", v)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return v if re.match(r"\d{4}-\d{2}-\d{2}", v) else None


def parse_shp_html(html_text: str) -> dict:  # noqa: C901  # per-table parse ladder
    """BSE per-filing HTML → the same shape as ``parse_shp`` (XBRL).

    Table layout (verified 2026-09-22, Gandhi Special Tubes filing):
    - t0: 2-col header pairs — scrip, **NSE Symbol** cross-ref, ISIN,
      report type, as-on date;
    - t2: category aggregates — col0 code, col1 category, col6 shares,
      col7 stake%;
    - t3: named holders (36 cols) — col1 name, col5 shareholder count,
      col6 shares, col10 stake%, **col35 category tag** (e.g. 'Promoter
      Group' — the membership signal NSE's XBRL masks).
    """
    tables = _TABLE.findall(html_text)
    header: dict[str, str] = {}
    if tables:
        for row in _TR.findall(tables[0]):
            c = _cells(row)
            if len(c) >= 2 and c[0]:
                header[c[0]] = c[1]
    aggregates: dict[str, float] = {}
    if len(tables) > 2:
        for row in _TR.findall(tables[2]):
            c = _cells(row)
            if len(c) > 7 and c[1]:
                pct = _to_float(c[7].rstrip("%"))
                if pct is not None:
                    aggregates[re.sub(r"^\((\w+)\)\s*", "", c[1])] = pct
    named: list[dict] = []
    if len(tables) > 3:
        current_category = ""
        for row in _TR.findall(tables[3]):
            c = _cells(row)
            if len(c) < 11 or not c[1]:
                continue
            if c[0] and re.match(r"^\([a-z0-9]+\)$", c[0]):  # sub-category header
                current_category = c[1]
                continue
            shares = _to_int(c[6]) if len(c) > 6 else None
            stake = _to_float(c[10].rstrip("%")) if len(c) > 10 else None
            if c[0] == "" and c[1] and shares and stake is not None:
                named.append(
                    {
                        "ctx": c[1],
                        "category": (c[35] if len(c) > 35 and c[35] else current_category),
                        "holder_name": c[1],
                        "holder_pan": None,
                        "shares": shares,
                        "stake_pct": stake,
                        "voting_pct": _to_float(c[14].rstrip("%")) if len(c) > 14 else None,
                        "pledged_shares": None,
                    }
                )
    symbol = header.get("NSE Symbol")
    scrip = header.get("Scrip code")
    return {
        "symbol": symbol if symbol and symbol != "NA" else scrip,
        "scrip_code": scrip,
        "isin": header.get("ISIN"),
        "period_start": None,
        "period_end": _iso_date_bse(
            header.get(
                "Quarter Ended / Half year ended/Date of Report (For Prelisting / Allotment)", ""
            )
            or ""
        ),
        "promoter_pct": _to_float((header.get("Promoter") or "").rstrip("%")),
        "aggregates": aggregates,
        "named": named,
        "category_total_pct": round(sum(aggregates.values()), 4),
    }


def store_bse_filing(
    con: duckdb.DuckDBPyConnection, url: str, parsed: dict, rss: dict | None
) -> None:
    """Fold a BSE filing into the shared store (source='BSE')."""
    fid = _filing_id_bse(url)
    con.execute(
        "INSERT OR REPLACE INTO shp_filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            fid,
            parsed["symbol"],
            (rss or {}).get("company"),
            url,
            parsed["period_start"],
            parsed["period_end"],
            (rss or {}).get("submission_dt"),
            (rss or {}).get("revision_dt") or None,
            (rss or {}).get("revised_status") or None,
            parsed.get("promoter_pct"),
            (rss or {}).get("public_pct"),
            (rss or {}).get("emptr"),
            datetime.now(UTC).isoformat(timespec="seconds"),
            hashlib.sha256(str(parsed["aggregates"]).encode()).hexdigest()[:16],
            "BSE",
        ],
    )
    con.execute("DELETE FROM shp_holders WHERE filing_id = ?", [fid])
    rows = [
        (
            fid,
            h["ctx"],
            1,
            h["category"],
            h["holder_name"],
            h["holder_pan"],
            h["shares"],
            h["stake_pct"],
            h["voting_pct"],
            h["pledged_shares"],
        )
        for h in parsed["named"]
    ] + [
        (fid, cat + "_CtxI", 0, cat, None, None, None, pct, None, None)
        for cat, pct in parsed["aggregates"].items()
    ]
    if rows:
        con.executemany("INSERT INTO shp_holders VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


# --------------------------------------------------------------------------- #
# derive — holder entities + invested_in / same_group edges
# --------------------------------------------------------------------------- #
def _normalize(name: str) -> str:
    """Listing name → entities.normalized_name key: exchange_sync.clean_name
    (strips Ltd/Limited/Pvt tails — listing names carry them, entity names
    don't) then parse_newsletter.normalize_name (PascalCase underscores)."""
    try:
        from helpers.core.parse_newsletter import normalize_name as _norm  # noqa: PLC0415
        from helpers.maintenance.exchange_sync import clean_name  # noqa: PLC0415

        return _norm(clean_name(name))
    except Exception:
        import re as _re

        n = _re.sub(r"\b(Pvt|Private)\b", " ", name)
        while True:
            n2 = _re.sub(r"[\s.]*(Ltd|Limited)[.]?\s*$", "", n.strip(), flags=_re.I)
            if n2 == n:
                break
            n = n2
        n = _re.sub(r"[&()\-]", " ", n)
        n = _re.sub(r"[^A-Za-z0-9 _]", "", n)
        return "_".join(" ".join(n.split()).split())


def classify_holder_category(category: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", category.casefold()).strip()
    if any(token in value for token in ("shareholdingpattern", "detailsofsharesheldby", "total")):
        return "artifact"
    if "promoter" in value:
        return "promoter_group" if "group" in value else "promoter"
    if any(token in value for token in ("individual", "huf", "person", "managerial")):
        return "person"
    if any(token in value for token in ("corporate", "company", "body")):
        return "company"
    if "public" in value:
        return "public"
    return "institution"


def _holder_kind(category: str) -> str:
    holder_class = classify_holder_category(category)
    if holder_class in {"person", "promoter", "promoter_group"}:
        return "person"
    if holder_class == "company":
        return "company"
    if holder_class == "artifact":
        return "artifact"
    return "institution"


def build_candidates(
    src: duckdb.DuckDBPyConnection,
    companies: dict[str, str],
    normalized: set[str],
    *,
    min_stake: float,
) -> tuple[list[dict], list[str]]:
    """Latest filing per symbol → holder-edge candidates + unresolved symbols.

    ``companies``: symbol → resolved entity name (from exchange_listings ×
    entities.normalized_name). ``normalized``: the entities' normalized-name
    universe for holder reconciliation (UPPERCASED keys — holder names
    arrive in mixed case across filings).
    """
    latest = src.execute(
        """SELECT DISTINCT ON (symbol) symbol, company_name, filing_id, period_end,
                  promoter_pct
           FROM shp_filings WHERE symbol IS NOT NULL
           ORDER BY symbol, period_end DESC, revision_dt DESC NULLS LAST"""
    ).fetchall()
    holders = src.execute(
        """SELECT h.filing_id, h.category, h.holder_name, h.stake_pct, h.shares,
                  h.voting_pct, h.pledged_shares, f.symbol, f.period_end
           FROM shp_holders h JOIN shp_filings f USING (filing_id)
           WHERE h.named = 1 AND h.stake_pct >= ?""",
        [float(min_stake)],
    ).fetchall()
    by_filing: dict[str, list] = {}
    for row in holders:
        by_filing.setdefault(row[0], []).append(row)
    candidates: list[dict] = []
    unresolved: list[str] = []
    for symbol, company_name, filing_id, period_end, _prom in latest:
        target = companies.get(symbol)
        if target is None:
            unresolved.append(symbol)
            continue
        for _fid, category, name, pct, shares, voting, pledged, _sym, pend in by_filing.get(
            filing_id, []
        ):
            norm = _normalize(name)
            key = norm.upper()  # filings mix 'MANOJ B GANDHI' and
            # 'Manoj B Gandhi' — dedup case-insensitively, keep display
            kind = _holder_kind(category)
            if kind == "artifact":
                continue
            # corporate holders reconcile to existing company entities
            if category == _CORPORATE_CATEGORY or key in normalized:
                kind = "company" if key in normalized else kind
            candidates.append(
                {
                    "holder": name,
                    "holder_norm": key,
                    "holder_kind": kind,
                    "holder_class": classify_holder_category(category),
                    "category": category,
                    "target": target,
                    "stake_pct": pct,
                    "shares": shares,
                    "voting_pct": voting,
                    "pledged": pledged,
                    "filing_id": filing_id,
                    # ISO string: duckdb DATE reads back as datetime.date
                    "valid_from": pend.isoformat()
                    if hasattr(pend, "isoformat")
                    else str(pend or ""),
                }
            )
    return candidates, unresolved


def apply_candidates(  # noqa: C901
    conn: sqlite3.Connection,
    candidates: list[dict],
    norm_to_name: dict[str, str] | None = None,
    *,
    dry_run: bool = True,
) -> tuple[int, int, int]:
    """Create holder entities + invested_in edges; supersede prior validity.

    ``norm_to_name``: UPPERCASED normalized_name → canonical entity name.
    A holder that reconciles to an existing entity (e.g. the corporate
    holder 'TSF INVESTMENTS LIMITED' → 'TSF Investments') MUST get the
    canonical name as the edge source — the raw filing display form has
    no entity row and would fail the graph_edges FK.

    Returns (n_new_entities, n_fresh_edges, n_written). Dry-run reports
    the parity numbers without touching the store.
    """
    norm_to_name = norm_to_name or {}
    from helpers.core.person_names import (
        classify_person_name,
        person_fold_registry,
        resolve_person,
    )

    existing_entities = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    existing_people = [
        (r[0], classify_person_name(r[0]))
        for r in conn.execute("SELECT name FROM entities WHERE entity_type='person'")
    ]
    resolved_people = {}
    person_folds = person_fold_registry()
    for c in candidates:
        if c["holder_kind"] == "person":
            match, _score = resolve_person(c["holder"], existing_people)
            if match:
                resolved_people[c["holder_norm"]] = match
                continue
            # entity_name_dedup/person arc: a spelling folded by the
            # signed person plan resolves to its canonical — the dedup
            # and this resolver cannot drift (one artifact, one map).
            from helpers.core.entity_identity import name_key  # noqa: PLC0415

            hit = person_folds.get(name_key(c["holder"]))
            if hit:
                resolved_people[c["holder_norm"]] = hit
    existing_edges = {
        (r[0], r[1])
        for r in conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='invested_in'"
        ).fetchall()
    }
    new_entities = {
        c["holder_norm"]: (c["holder"], c["holder_kind"])
        for c in candidates
        if c["holder_kind"] != "company"
        and c["holder"] not in existing_entities
        and c["holder_norm"] not in resolved_people
    }
    # canonical entity name when reconciled, display name when new
    resolved_source: dict[str, str] = {}
    skipped: list[str] = []
    for c in candidates:
        hit = resolved_people.get(c["holder_norm"]) or norm_to_name.get(c["holder_norm"])
        if hit is None:
            # Raw display form only when it IS a registry row — or will be
            # within this same transaction (new_entities mints non-company
            # holders before the edge inserts). #400 SHP incident: a
            # post-merge variant key drops out of norm_to_name while the
            # canonical row survives — the old unconditional raw fallback
            # built a ghost source and died on the graph_edges FK
            # mid-chain. Anything neither registered nor mint-destined
            # skips + reports; no ghost, no noise mint.
            if c["holder"] in existing_entities or c["holder_norm"] in new_entities:
                hit = c["holder"]
            else:
                skipped.append(c["holder"])
                continue
        resolved_source[c["holder_norm"]] = hit
    if skipped:
        print(
            f"shareholding_sync: skipped {len(skipped)} unreconciled holder(s) "
            f"(variant forms with no registry row — not minted): "
            f"{sorted(set(skipped))[:5]}"
        )
    fresh = sum(
        1
        for c in candidates
        if c["holder_norm"] in resolved_source
        and (resolved_source[c["holder_norm"]], c["target"]) not in existing_edges
    )
    if dry_run:
        return len(new_entities), fresh, fresh

    written = 0
    with conn:
        for norm, (name, kind) in new_entities.items():
            conn.execute(
                "INSERT OR IGNORE INTO entities (name, entity_type, normalized_name, file_path) "
                "VALUES (?, ?, ?, NULL)",
                (name, kind, norm),
            )
        for c in candidates:
            if c["holder_norm"] not in resolved_source:
                continue  # unreconciled variant form — skipped above
            src_name = resolved_source[c["holder_norm"]]
            props = {
                "category": c["category"],
                "stake_pct": c["stake_pct"],
                "shares": c["shares"],
                "voting_pct": c["voting_pct"],
                "pledged_shares": c["pledged"],
                "filing_id": c["filing_id"],
            }
            if is_category_holder(c["holder"]):
                props["category_holder"] = True
            # graph_edges holds ONE interval per (source, target, type)
            # (UNIQUE constraint) — the interval history lives in
            # shp_filings/shp_holders. A newer filing UPSERTS the pair in
            # place: latest weight/period/properties, valid_to re-opened.
            cur = conn.execute(
                "INSERT INTO graph_edges "
                "(source, target, edge_type, weight, properties, valid_from, "
                " source_ref, symmetric, source_tier) "
                "VALUES (?, ?, 'invested_in', ?, ?, ?, ?, 0, 'regulator') "
                "ON CONFLICT(source, target, edge_type) DO UPDATE SET "
                "weight=excluded.weight, properties=excluded.properties, "
                "valid_from=excluded.valid_from, valid_to=NULL, "
                "source_ref=excluded.source_ref, source_tier=excluded.source_tier",
                (
                    src_name,
                    c["target"],
                    float(c["stake_pct"] or 0.0),
                    json.dumps(props, sort_keys=True),
                    c["valid_from"],
                    f"{SOURCE_REF_PREFIX}:{c['filing_id']}",
                ),
            )
            written += cur.rowcount
    return len(new_entities), fresh, written


def build_same_group(
    src: duckdb.DuckDBPyConnection,
    companies: dict[str, str],
    corporate_norms: set[str],
    *,
    min_stake: float,
) -> list[tuple[str, str, str]]:
    """Companies sharing a ≥min-stake CORPORATE holder → same_group pairs.

    Corporate-ness is reconciliation-based, not category-based: live data
    shows body-corporate holders filing under 'OthersIndianShareholders'
    (TSF Investments → Wheels India) — the XBRL category axis is not a
    reliable company-ness signal. A holder counts when its normalized
    name matches an existing company entity (``corporate_norms``,
    UPPERCASED).
    """
    rows = src.execute(
        """SELECT h.holder_name, h.stake_pct, f.symbol
            FROM shp_holders h JOIN shp_filings f USING (filing_id)
            WHERE h.named = 1 AND h.stake_pct >= ?
              AND f.symbol IS NOT NULL""",
        [float(min_stake)],
    ).fetchall()
    by_holder: dict[str, set[str]] = {}
    for holder, _pct, symbol in rows:
        if _normalize(holder).upper() not in corporate_norms:
            continue
        target = companies.get(symbol)
        if target:
            by_holder.setdefault(holder, set()).add(target)
    pairs = []
    for holder, targets in sorted(by_holder.items()):
        if len(targets) < 2:
            continue
        for a in sorted(targets):
            for b in sorted(targets):
                if a < b:
                    pairs.append((a, b, holder))
    return pairs


# --------------------------------------------------------------------------- #
# R6b (graph_relational_depth) — category-holder marker + majority promotion
# --------------------------------------------------------------------------- #
MAJORITY_PROMOTE_PREFIX = "derive:shareholding_majority"
MAJORITY_PCT = 50.0


def mark_category_holders(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """One-time backfill: stamp ``properties.category_holder=true`` on live
    invested_in rows whose holder is a structural shareholding-page row.

    Standing behavior lives at mint (apply_candidates / the yfinance
    holders leg); this brings pre-marker rows to parity so consumers can
    filter structurally.
    """
    rows = conn.execute(
        "SELECT id, source, properties FROM graph_edges WHERE edge_type='invested_in'"
    ).fetchall()
    to_mark: list[tuple[int, str, str]] = []  # (id, holder, new_props)
    for rid, holder, props_json in rows:
        try:
            d = json.loads(props_json or "{}")
        except ValueError:
            continue
        if d.get("category_holder") or not is_category_holder(holder):
            continue
        d["category_holder"] = True
        to_mark.append((rid, holder, json.dumps(d, sort_keys=True)))
    counts = {
        "scanned": len(rows),
        "to_mark": len(to_mark),
        "marked": 0,
        "samples": sorted({holder for _rid, holder, _p in to_mark})[:6],
    }
    if not apply or not to_mark:
        return counts
    with conn:
        conn.executemany(
            "UPDATE graph_edges SET properties=? WHERE id=?",
            [(props, rid) for rid, _holder, props in to_mark],
        )
    counts["marked"] = len(to_mark)
    return counts


def reconcile_majority_ownership(  # noqa: C901
    conn: sqlite3.Connection, *, apply: bool = False
) -> dict:
    """Clean-majority promotion into the ownership semantic (R6b / I1).

    Every ``invested_in`` row whose holder is a NAMED investor (not a
    category holder — name test or marker) at >= ``MAJORITY_PCT`` percent
    promotes as ``subsidiary_of`` child=investee → parent=holder with
    ``source_ref='derive:shareholding_majority:<date>'``,
    ``source_tier='derive'``, weight 1.0 — the stake rides in
    ``properties.pct`` via :func:`stake_pct_of` (the participation weight;
    the column is mixed-kind and never a stake).

    Store-wide idempotent reconcile: promote-ref edges whose dyad is no
    longer clean-majority retract (control lost / source row deleted); a
    foreign ``subsidiary_of`` row on a desired dyad (VIGIL, manual, other
    derive lanes) is never clobbered — counted as already-claimed.
    Identical re-stamps skip the write so the generation trigger does not
    churn on every sync run.
    """
    today = datetime.now(UTC).date().isoformat()
    counts = {
        "invested_scanned": 0,
        "category_skipped": 0,
        "no_stake_skipped": 0,
        "minority_skipped": 0,
        "majority_eligible": 0,
        "to_insert": 0,
        "to_update": 0,
        "to_retract": 0,
        "already_claimed_foreign": 0,
        "inserted": 0,
        "updated": 0,
        "retracted": 0,
    }
    desired: dict[tuple[str, str], dict] = {}
    for holder, investee, props_json, ref, valid_from in conn.execute(
        "SELECT source, target, properties, source_ref, valid_from "
        "FROM graph_edges WHERE edge_type='invested_in'"
    ).fetchall():
        counts["invested_scanned"] += 1
        try:
            d = json.loads(props_json or "{}")
        except ValueError:
            d = {}
        if is_category_holder(holder) or d.get("category_holder"):
            counts["category_skipped"] += 1
            continue
        pct = stake_pct_of(props_json)
        if pct is None:
            counts["no_stake_skipped"] += 1
            continue
        if pct < MAJORITY_PCT:
            counts["minority_skipped"] += 1
            continue
        # subsidiary_of orientation: child (investee) → parent (holder)
        desired[(investee, holder)] = {
            "pct": pct,
            "promoted_from": ref,
            "valid_from": valid_from,
        }
    counts["majority_eligible"] = len(desired)

    existing: dict[tuple[str, str], tuple[int, str, str, str | None]] = {}
    for eid, src, tgt, ref, props, valid_from in conn.execute(
        "SELECT id, source, target, source_ref, properties, valid_from "
        "FROM graph_edges WHERE edge_type='subsidiary_of'"
    ).fetchall():
        existing[(src, tgt)] = (eid, ref, props or "{}", valid_from)

    inserts: list[tuple[str, str, str, str | None, str]] = []
    updates: list[tuple[str, str | None, str, int]] = []
    retracts: list[int] = []
    samples: list[tuple[str, str, float]] = []
    retract_samples: list[tuple[str, str]] = []
    claimed_samples: list[tuple[str, str, str]] = []
    for child, parent in sorted(desired):
        info = desired[(child, parent)]
        props_json = json.dumps(
            {"pct": info["pct"], "promoted_from": info["promoted_from"]}, sort_keys=True
        )
        ref = f"{MAJORITY_PROMOTE_PREFIX}:{today}"
        hit = existing.get((child, parent))
        if hit is None:
            inserts.append((child, parent, props_json, info["valid_from"], ref))
            if len(samples) < 8:
                samples.append((child, parent, info["pct"]))
        elif hit[1].startswith(MAJORITY_PROMOTE_PREFIX):
            if hit[2] != props_json or hit[3] != info["valid_from"]:
                updates.append((props_json, info["valid_from"], ref, hit[0]))
        else:
            counts["already_claimed_foreign"] += 1
            if len(claimed_samples) < 5:
                claimed_samples.append((child, parent, hit[1]))
    for dyad in sorted(existing):
        eid, ref, _props, _vf = existing[dyad]
        if ref.startswith(MAJORITY_PROMOTE_PREFIX) and dyad not in desired:
            retracts.append(eid)
            if len(retract_samples) < 5:
                retract_samples.append(dyad)

    counts["to_insert"] = len(inserts)
    counts["to_update"] = len(updates)
    counts["to_retract"] = len(retracts)
    counts["samples"] = samples
    counts["retract_samples"] = retract_samples
    counts["claimed_samples"] = claimed_samples
    if not apply or not (inserts or updates or retracts):
        return counts
    with conn:
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, weight, properties, "
            "valid_from, source_ref, symmetric, source_tier) "
            "VALUES (?, ?, 'subsidiary_of', 1.0, ?, ?, ?, 0, 'derive')",
            inserts,
        )
        conn.executemany(
            "UPDATE graph_edges SET properties=?, valid_from=?, source_ref=? WHERE id=?",
            updates,
        )
        conn.executemany("DELETE FROM graph_edges WHERE id=?", [(r,) for r in retracts])
    counts["inserted"] = len(inserts)
    counts["updated"] = len(updates)
    counts["retracted"] = len(retracts)
    return counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve_companies(src: duckdb.DuckDBPyConnection, conn: sqlite3.Connection) -> dict[str, str]:
    # the XBRL Symbol identifier carries either an NSE trading symbol or
    # a BSE scrip code (both seen live) — resolve against both exchanges.
    # A symbol can carry several listing-name variants (NSE canonical,
    # BSE truncated forms); NSE-preferred, first normalization hit wins.
    listings = src.execute(
        "SELECT symbol, name FROM exchange_listings "
        "WHERE exchange IN ('NSE', 'BSE') AND asset_type='equity' "
        "AND name IS NOT NULL "
        "ORDER BY symbol, CASE exchange WHEN 'NSE' THEN 0 ELSE 1 END, segment NULLS LAST"
    ).fetchall()
    entities = {
        r[0]: r[1]
        for r in conn.execute("SELECT normalized_name, name FROM entities").fetchall()
        if r[0]
    }
    out = {}
    for symbol, name in listings:
        if symbol in out:
            continue
        ent = entities.get(_normalize(name or ""))
        if ent:
            out[symbol] = ent
    return out


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--rss", action="store_true", help="poll the NSE RSS index and ingest new filings"
    )
    ap.add_argument(
        "--bse-rss", action="store_true", help="poll the BSE RSS mirror and ingest new filings"
    )
    ap.add_argument("--xml", metavar="URL|PATH", help="ingest one filing directly")
    ap.add_argument("--max", type=int, default=0, help="cap filings per run (0 = no cap)")
    ap.add_argument("--apply", action="store_true", help="write edges (default: dry-run)")
    ap.add_argument(
        "--fetch-only",
        action="store_true",
        help="fetch + parse filings, stage to scratch (no store writes); pairs with --apply-only",
    )
    ap.add_argument(
        "--apply-only",
        action="store_true",
        help="apply staged filings (no fetch); pairs with --fetch-only",
    )
    ap.add_argument(
        "--min-stake", type=float, default=0.1, help="named-holder stake %% floor (default 0.1)"
    )
    ap.add_argument(
        "--same-group-min",
        type=float,
        default=10.0,
        help="corporate-holder %% floor for same_group (default 10)",
    )
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between archive fetches")
    ap.add_argument(
        "--cure-participation",
        action="store_true",
        help="R6b backfill: stamp category_holder on structural rows + reconcile "
        "clean-majority promotion into subsidiary_of (default dry-run; --apply writes)",
    )
    args = ap.parse_args(argv)

    FETCH_SCRATCH.mkdir(parents=True, exist_ok=True)

    if args.fetch_only and args.apply_only:
        print("[shp] --fetch-only and --apply-only are mutually exclusive")
        return 2

    if args.cure_participation:
        from helpers.core.db import connect as sqlite_connect, DEFAULT_DB_PATH

        conn = sqlite_connect(DEFAULT_DB_PATH)
        marked = mark_category_holders(conn, apply=args.apply)
        rec = reconcile_majority_ownership(conn, apply=args.apply)
        mode = "APPLIED" if args.apply else "dry-run"
        print(f"participation cure ({mode}):")
        print(
            f"  category-holder marker: {marked['to_mark']} of {marked['scanned']} "
            f"invested_in rows" + (f" — e.g. {marked['samples'][:3]}" if marked["samples"] else "")
        )
        print(
            f"  majority reconcile: eligible={rec['majority_eligible']} "
            f"insert={rec['to_insert']} update={rec['to_update']} "
            f"retract={rec['to_retract']} already_claimed={rec['already_claimed_foreign']}"
        )
        for child, parent, pct in rec["samples"]:
            print(f"    promote {child} -> {parent} ({pct}%)")
        for child, parent in rec["retract_samples"]:
            print(f"    retract {child} -> {parent}")
        conn.close()
        return 0

    # ------------------------------------------------------------------ #
    # Fetch phase — network-bound, no research.db / sources.duckdb writes #
    # ------------------------------------------------------------------ #
    if not args.apply_only:
        src = duckdb.connect(str(SOURCES_DB))
        ensure_sources_schema(src)

        ingested = 0
        if args.rss:
            items = parse_rss(_get_text(RSS_URL))
            known = {r[0] for r in src.execute("SELECT filing_id FROM shp_filings").fetchall()}
            for item in items:
                fid = _filing_id(item["url"])
                if fid in known:
                    continue
                if args.max and ingested >= args.max:
                    break
                try:
                    data = fetch_xbrl_cached(item["url"])
                except (urllib.error.URLError, OSError) as exc:
                    # one dead filing URL must not kill the lane — #401
                    print(f"[shp] FETCH-FAILED: {exc} — skipping {item['url']}")
                    continue
                parsed = parse_shp(data)
                if args.fetch_only:
                    scratch = FETCH_SCRATCH / f"{fid}.json"
                    scratch.write_text(
                        json.dumps(
                            {
                                "fid": fid,
                                "url": item["url"],
                                "parsed": parsed,
                                "rss": item,
                                "source": "nse",
                            }
                        )
                    )
                    ingested += 1
                    print(
                        f"staged {fid} symbol={parsed['symbol']} named={len(parsed['named'])} total_pct={parsed['category_total_pct']}"
                    )
                else:
                    store_filing(src, item["url"], parsed, item)
                    ingested += 1
                    print(
                        f"ingested {fid} symbol={parsed['symbol']} named={len(parsed['named'])} total_pct={parsed['category_total_pct']}"
                    )
                time.sleep(args.sleep)

        if args.xml:
            p = Path(args.xml)
            data = p.read_bytes() if p.is_file() else fetch_xbrl_cached(args.xml)
            parsed = parse_shp(data)
            if args.fetch_only:
                fid = _filing_id(args.xml)
                scratch = FETCH_SCRATCH / f"{fid}.json"
                scratch.write_text(
                    json.dumps(
                        {
                            "fid": fid,
                            "url": args.xml,
                            "parsed": parsed,
                            "rss": None,
                            "source": "nse",
                        }
                    )
                )
                ingested += 1
                print(f"staged {fid} symbol={parsed['symbol']} named={len(parsed['named'])}")
            else:
                store_filing(src, args.xml, parsed, None)
                ingested += 1
                print(
                    f"ingested {_filing_id(args.xml)} symbol={parsed['symbol']} named={len(parsed['named'])}"
                )

        if args.bse_rss:
            bse_items = parse_bse_rss(_get_text(BSE_RSS_URL))
            known = {r[0] for r in src.execute("SELECT filing_id FROM shp_filings").fetchall()}
            for item in bse_items:
                fid = _filing_id_bse(item["url"])
                if fid in known:
                    continue
                if args.max and ingested >= args.max:
                    break
                page = fetch_bse_html_cached(item["url"])
                parsed = parse_shp_html(page)
                if args.fetch_only:
                    scratch = FETCH_SCRATCH / f"{fid}.json"
                    scratch.write_text(
                        json.dumps(
                            {
                                "fid": fid,
                                "url": item["url"],
                                "parsed": parsed,
                                "rss": item,
                                "source": "bse",
                            }
                        )
                    )
                    ingested += 1
                    print(
                        f"staged {fid} symbol={parsed['symbol']} named={len(parsed['named'])} "
                        f"total_pct={parsed['category_total_pct']}"
                    )
                else:
                    store_bse_filing(src, item["url"], parsed, item)
                    ingested += 1
                    print(
                        f"ingested {fid} symbol={parsed['symbol']} named={len(parsed['named'])} "
                        f"total_pct={parsed['category_total_pct']}"
                    )
                time.sleep(args.sleep)

        src.close()

    if args.fetch_only:
        n_staged = len(list(FETCH_SCRATCH.glob("*.json")))
        print(f"[FETCH-ONLY] staged={n_staged} filings -> {FETCH_SCRATCH}")
        return 0

    # --------------------------------------------------------------- #
    # Apply phase — store writes (sources.duckdb + research.db)       #
    # --------------------------------------------------------------- #
    src = duckdb.connect(str(SOURCES_DB))
    ensure_sources_schema(src)
    from helpers.core.db import connect as sqlite_connect, DEFAULT_DB_PATH

    if args.apply_only:
        for scratch in sorted(FETCH_SCRATCH.glob("*.json")):
            data = json.loads(scratch.read_text())
            fid, url, parsed, rss, source = (
                data["fid"],
                data["url"],
                data["parsed"],
                data.get("rss"),
                data.get("source", "nse"),
            )
            if source == "bse":
                store_bse_filing(src, url, parsed, rss)
            else:
                store_filing(src, url, parsed, rss)
            print(f"applied {fid} from scratch")

    conn = sqlite_connect(DEFAULT_DB_PATH)
    companies = _resolve_companies(src, conn)
    norm_to_name = {
        (r[0] or "").upper(): r[1]
        for r in conn.execute("SELECT normalized_name, name FROM entities").fetchall()
        if r[0]
    }
    candidates, unresolved = build_candidates(
        src, companies, set(norm_to_name), min_stake=args.min_stake
    )
    n_ent, n_fresh, n_written = apply_candidates(
        conn, candidates, norm_to_name, dry_run=not args.apply
    )
    if args.apply:
        # R6b standing reconcile: majority promotion follows every apply
        rec = reconcile_majority_ownership(conn, apply=True)
        print(
            f"majority reconcile: eligible={rec['majority_eligible']} "
            f"inserted={rec['inserted']} updated={rec['updated']} "
            f"retracted={rec['retracted']} already_claimed={rec['already_claimed_foreign']}"
        )
    mode = "APPLY" if args.apply else "dry-run"
    n_filings = _scalar(src, "SELECT COUNT(*) FROM shp_filings")
    n_universe = _scalar(
        src,
        "SELECT COUNT(DISTINCT symbol) FROM exchange_listings "
        "WHERE exchange='NSE' AND asset_type='equity'",
    )
    n_resolved = len(
        {
            f[0]
            for f in src.execute(
                "SELECT DISTINCT symbol FROM shp_filings WHERE symbol IS NOT NULL"
            ).fetchall()
        }
        & set(companies)
    )
    print(
        f"[{mode}] filings={n_filings} candidates={len(candidates)} "
        f"new_entities={n_ent} fresh_edges={n_fresh} written={n_written} | "
        f"coverage: {n_resolved}/{n_universe} NSE universe "
        f"({(100.0 * n_resolved / n_universe if n_universe else 0):.2f}%)"
    )
    if unresolved:
        print(f"unresolved symbols ({len(unresolved)}): {', '.join(sorted(unresolved)[:10])}")
        WORKLIST.write_text("symbol\n" + "\n".join(sorted(unresolved)) + "\n")
    corporate_norms = {
        (r[0] or "").upper()
        for r in conn.execute(
            "SELECT normalized_name FROM entities WHERE entity_type='company'"
        ).fetchall()
    }
    pairs = build_same_group(src, companies, corporate_norms, min_stake=args.same_group_min)
    print(f"same_group pairs available: {len(pairs)}")
    if args.apply:
        from helpers.graph.query import rebuild as duckdb_rebuild

        duckdb_rebuild()
        print("duckdb property graph rebuilt")
    src.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
