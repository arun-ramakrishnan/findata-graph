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
import re
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, UTC
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SOURCES_DB = REPO / "memory" / "data" / "sources.duckdb"
RAW_DIR = REPO / "memory" / "data" / "shp_raw"
WORKLIST = REPO / "memory" / "data" / "shp_worklist.csv"
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
    # level as the CSV lane's S310 exemption; no DTD/entity expansion
    # in these feeds and no local file URIs are followed.
    root = ET.fromstring(xml_text)  # noqa: S314
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
    root = ET.fromstring(xml_bytes)  # noqa: S314
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
    root = ET.fromstring(xml_text)  # noqa: S314  # https-only bseindia lane (see parse_rss note)
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


def _holder_kind(category: str) -> str:
    if any(p in category for p in _PERSON_CATEGORIES):
        return "person"
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
           WHERE h.named = 1 AND h.stake_pct >= ?""".replace("?", str(float(min_stake)))
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
            # corporate holders reconcile to existing company entities
            if category == _CORPORATE_CATEGORY or key in normalized:
                kind = "company" if key in normalized else kind
            candidates.append(
                {
                    "holder": name,
                    "holder_norm": key,
                    "holder_kind": kind,
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


def apply_candidates(
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
    existing_entities = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    existing_edges = {
        (r[0], r[1])
        for r in conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='invested_in'"
        ).fetchall()
    }
    new_entities = {
        c["holder_norm"]: (c["holder"], c["holder_kind"])
        for c in candidates
        if c["holder_kind"] != "company" and c["holder"] not in existing_entities
    }
    # canonical entity name when reconciled, display name when new
    resolved_source = {
        c["holder_norm"]: norm_to_name.get(c["holder_norm"], c["holder"]) for c in candidates
    }
    fresh = sum(
        1
        for c in candidates
        if (resolved_source[c["holder_norm"]], c["target"]) not in existing_edges
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
            src_name = resolved_source[c["holder_norm"]]
            props = {
                "category": c["category"],
                "stake_pct": c["stake_pct"],
                "shares": c["shares"],
                "voting_pct": c["voting_pct"],
                "pledged_shares": c["pledged"],
                "filing_id": c["filing_id"],
            }
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
        "--min-stake", type=float, default=0.1, help="named-holder stake %% floor (default 0.1)"
    )
    ap.add_argument(
        "--same-group-min",
        type=float,
        default=10.0,
        help="corporate-holder %% floor for same_group (default 10)",
    )
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between archive fetches")
    args = ap.parse_args(argv)

    src = duckdb.connect(str(SOURCES_DB))
    ensure_sources_schema(src)
    from helpers.core.db import connect as sqlite_connect, DEFAULT_DB_PATH

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
            data = fetch_xbrl_cached(item["url"])
            parsed = parse_shp(data)
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
            store_bse_filing(src, item["url"], parsed, item)
            ingested += 1
            print(
                f"ingested {fid} symbol={parsed['symbol']} named={len(parsed['named'])} "
                f"total_pct={parsed['category_total_pct']}"
            )
            time.sleep(args.sleep)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
