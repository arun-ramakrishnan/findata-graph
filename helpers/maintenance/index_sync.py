#!/usr/bin/env python3
"""NSE index-constituent sidecar sync (index-membership fill).

Fetches the constituent lists of every NSE Indices equity index in scope
(Wave 1 = broad-based + sectoral) and folds them into
``sources.duckdb::index_constituents`` — the raw, append-only sidecar
that ``helpers/graph/derive_indices.py`` then projects into ``index``
entities + ``listed_on_index`` edges.

Plan-of-record: doc/improvements/archive/graph/index_membership_fill.md;
design memo doc/local/evaluations/index_fill.md §3/§5.

Two lanes:
- ``nsearchives`` — the three feeds already in the data-source register
  (no scraping; needs the browser User-Agent + NSE Referer).
- ``niftyindices`` — the long tail. Detail pages are discovered from the
  two listing pages, never guessed: CSV stems are inconsistent
  (``ind_nifty50list.csv`` vs ``ind_niftyindiadefence_list.csv``), so
  each detail page is scraped for its ``IndexConstituent/*.csv`` href.

Idempotent on ``PRIMARY KEY (index_name, symbol, as_of)`` where ``as_of``
is the CSV ``Last-Modified`` (the semi-annual reconstitution vintage):
re-running a stale fetch is a no-op. ``--apply`` writes; default dry-run.

    python3 helpers/maintenance/index_sync.py            # dry-run summary
    python3 helpers/maintenance/index_sync.py --apply    # write sidecar
    python3 helpers/maintenance/index_sync.py --apply --verbose
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DB = REPO / "memory" / "data" / "sources.duckdb"
DATA_DIR = DB.parent
UA = {"User-Agent": "Mozilla/5.0 research"}
NSE_REFERER = "https://www.nseindia.com/"
NIFTYINDICES = "https://www.niftyindices.com"

# Wave 1 listing pages: each link under the page's OWN category prefix is
# a detail page. Discovery-scraped, never a hardcoded slug list.
LISTING_PAGES = (
    "broad-based-indices",
    "sectoral-indices",
)

# The three documented nsearchives feeds (data_sources.md §NSE). Kept as
# the no-scrape lane for the highest-value indices; the niftyindices
# discovery skips any stem already covered here.
NSEARCHIVES_FEEDS: tuple[tuple[str, str], ...] = (
    ("NIFTY 500", "ind_nifty500list.csv"),
    ("NIFTY TOTAL MARKET", "ind_niftytotalmarket_list.csv"),
    ("NIFTY SME EMERGE", "ind_niftysmelist.csv"),
)
NSEARCHIVES_BASE = "https://nsearchives.nseindia.com/content/indices/"

_COLUMN_ALIASES = {
    "company name": "company_name",
    "industry": "industry",
    "symbol": "symbol",
    "series": "series",
    "isin code": "isin",
    "isin": "isin",
}
_CSV_HREF_RE = re.compile(r"IndexConstituent/([^\"']+\.csv)", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS index_constituents (
    index_name   VARCHAR NOT NULL,
    index_slug   VARCHAR,
    asset_class  VARCHAR NOT NULL DEFAULT 'equity',
    symbol       VARCHAR,
    isin         VARCHAR,
    company_name VARCHAR,
    industry     VARCHAR,
    series       VARCHAR,
    as_of        DATE,
    fetched_at   DATE NOT NULL,
    PRIMARY KEY (index_name, symbol, as_of)
)
"""
_VIEW_DDL = """
CREATE OR REPLACE VIEW vw_index_constituent AS
SELECT * FROM index_constituents c
WHERE c.as_of = (SELECT MAX(as_of) FROM index_constituents
                 WHERE index_name = c.index_name)
"""


@dataclass
class IndexBatch:
    """One index's constituent list at one reconstitution vintage."""

    index_name: str
    index_slug: str
    as_of: str
    rows: list[dict] = field(default_factory=list)


def _get(url: str, *, referer: str | None = None, timeout: int = 60) -> tuple[bytes, dict]:
    """Fetch ``url`` -> (body_bytes, response_headers). Browser UA always;
    the NSE archive lane additionally requires the nseindia Referer."""
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)  # noqa: S310  # https-only lane endpoints
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # https-only lane endpoints
        return r.read(), dict(r.headers)


def _as_of_from_headers(headers: dict) -> str:
    """CSV Last-Modified -> ISO date (the reconstitution vintage). Falls
    back to today when the header is absent."""
    raw = headers.get("Last-Modified") or headers.get("last-modified")
    if raw:
        try:
            return parsedate_to_datetime(raw).date().isoformat()
        except TypeError, ValueError:
            pass
    return date.today().isoformat()


def parse_constituent_csv(text: str) -> list[dict]:
    """Parse a constituent CSV (either lane) -> row dicts.

    Header variants seen live: ``Company Name,Industry,Symbol,Series,ISIN
    Code`` (both lanes). Rows without a symbol are dropped (index rows
    with no company constituents, e.g. India VIX).
    """
    rows: list[dict] = []
    for raw in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        mapped: dict[str, str] = {}
        for key, value in norm.items():
            out = _COLUMN_ALIASES.get(key)
            if out:
                mapped[out] = value
        symbol = mapped.get("symbol", "")
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "isin": mapped.get("isin", ""),
                "company_name": mapped.get("company_name", ""),
                "industry": mapped.get("industry", ""),
                "series": mapped.get("series", ""),
            }
        )
    return rows


def _detail_paths(html: str, category: str) -> list[str]:
    """Detail-page hrefs under ``/indices/equity/<category>/<slug>``."""
    pat = re.compile(r'href="(/indices/equity/' + re.escape(category) + r'/([^"#/]+))"')
    return sorted({m.group(1) for m in pat.finditer(html)})


def _index_name(html_text: str, fallback: str) -> str:
    """Canonical display name from the detail page <title> (HTML-unescaped)."""
    m = _TITLE_RE.search(html_text)
    if not m:
        return fallback
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()


def _csv_url(html: str) -> str | None:
    m = _CSV_HREF_RE.search(html)
    if not m:
        return None
    return f"{NIFTYINDICES}/IndexConstituent/{m.group(1)}"


def fetch_nsearchives_feeds() -> list[IndexBatch]:
    batches: list[IndexBatch] = []
    for index_name, stem in NSEARCHIVES_FEEDS:
        url = NSEARCHIVES_BASE + stem
        try:
            body, headers = _get(url, referer=NSE_REFERER)
        except (OSError, urllib.error.URLError) as e:
            print(f"[nsearchives] FETCH-FAILED {stem}: {str(e)[:80]}", file=sys.stderr)
            continue
        batches.append(
            IndexBatch(
                index_name=index_name,
                index_slug=stem,
                as_of=_as_of_from_headers(headers),
                rows=parse_constituent_csv(body.decode("utf-8-sig", "replace")),
            )
        )
    return batches


def discover_niftyindices(covered_stems: set[str]) -> list[IndexBatch]:
    """Scrape the listing pages -> detail pages -> CSV hrefs, then fetch."""
    detail_paths: list[str] = []
    for category in LISTING_PAGES:
        try:
            body, _ = _get(f"{NIFTYINDICES}/indices/equity/{category}")
        except (OSError, urllib.error.URLError) as e:
            print(f"[niftyindices] listing {category} FAILED: {str(e)[:80]}", file=sys.stderr)
            continue
        detail_paths += _detail_paths(body.decode("utf-8", "replace"), category)

    batches: list[IndexBatch] = []
    seen = set(covered_stems)
    for path in sorted(set(detail_paths)):
        fallback = path.rsplit("/", 1)[-1]
        try:
            page, _ = _get(NIFTYINDICES + path)
            html = page.decode("utf-8", "replace")
            csv_url = _csv_url(html)
            if csv_url is None:
                continue  # no constituent feed (India VIX)
            stem = csv_url.rsplit("/", 1)[-1]
            if stem in seen:
                continue  # nsearchives lane or another detail page already owns it
            seen.add(stem)
            name = _index_name(html, fallback)
            body, headers = _get(csv_url, referer=NIFTYINDICES + path)
            batches.append(
                IndexBatch(
                    index_name=name,
                    index_slug=stem,
                    as_of=_as_of_from_headers(headers),
                    rows=parse_constituent_csv(body.decode("utf-8-sig", "replace")),
                )
            )
            time.sleep(0.3)  # polite crawl
        except (OSError, urllib.error.URLError) as e:
            print(f"[niftyindices] FETCH-FAILED {path}: {str(e)[:80]}", file=sys.stderr)
            continue
    return batches


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SCHEMA_DDL)
    con.execute(_VIEW_DDL)


def _table_exists(con: duckdb.DuckDBPyConnection) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'index_constituents'"
    ).fetchone()
    return row is not None


def fold(
    con: duckdb.DuckDBPyConnection | None,
    batches: list[IndexBatch],
    run_date: str,
    *,
    apply: bool,
) -> dict:
    """Insert batches idempotently; returns per-index + total counts.

    ``con`` may be None when ``apply`` is False (a dry-run touches no store).
    """
    stats: dict = {"indices": 0, "rows": 0, "inserted": 0, "per_index": {}}
    for b in batches:
        stats["indices"] += 1
        stats["rows"] += len(b.rows)
        stats["per_index"][b.index_name] = len(b.rows)
    if not apply:
        return stats
    if con is None:
        raise RuntimeError("apply=True requires a DuckDB connection")
    ensure_schema(con)
    existing: set[tuple] = set()
    if _table_exists(con):
        existing = {
            (r[0], r[1], r[2])
            for r in con.execute(
                "SELECT index_name, symbol, CAST(as_of AS VARCHAR) FROM index_constituents"
            ).fetchall()
        }
    for b in batches:
        for r in b.rows:
            key = (b.index_name, r["symbol"], b.as_of)
            if key in existing:
                continue
            con.execute(
                "INSERT INTO index_constituents "
                "(index_name, index_slug, asset_class, symbol, isin, company_name,"
                " industry, series, as_of, fetched_at) "
                "VALUES (?, ?, 'equity', ?, ?, ?, ?, ?, ?, ?)",
                (
                    b.index_name,
                    b.index_slug,
                    r["symbol"],
                    r["isin"],
                    r["company_name"],
                    r["industry"],
                    r["series"],
                    b.as_of,
                    run_date,
                ),
            )
            existing.add(key)
            stats["inserted"] += 1
    return stats


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync NSE index constituents into sources.duckdb.")
    p.add_argument("--apply", action="store_true", help="write the sidecar (default: dry-run)")
    p.add_argument("--verbose", "-v", action="store_true", help="print every index + industry")
    p.add_argument(
        "--max-age-days",
        type=int,
        default=0,
        help="skip an index whose newest fetched_at is within N days (0 = always fetch)",
    )
    a = p.parse_args(argv)

    run_date = date.today().isoformat()
    batches = fetch_nsearchives_feeds()
    covered = {b.index_slug for b in batches}
    batches += discover_niftyindices(covered)

    mode = "apply" if a.apply else "dry-run"
    if a.apply and a.max_age_days > 0 and DB.exists():
        con = duckdb.connect(str(DB))
        try:
            if _table_exists(con):
                cutoff = date.today().toordinal() - a.max_age_days
                fresh = {
                    r[0]
                    for r in con.execute(
                        "SELECT index_name FROM index_constituents "
                        "GROUP BY index_name "
                        "HAVING MAX(fetched_at) >= CAST(? AS DATE)",
                        [date.fromordinal(cutoff).isoformat()],
                    ).fetchall()
                }
                batches = [b for b in batches if b.index_name not in fresh]
        finally:
            con.close()

    if a.apply:
        con = duckdb.connect(str(DB))
        try:
            stats = fold(con, batches, run_date, apply=True)
        finally:
            con.close()
    else:
        # Dry-run never opens/creates the store (a fresh clone must not get
        # a side effect from a preview).
        stats = fold(None, batches, run_date, apply=False)

    print(
        f"indices={stats['indices']} constituent_rows={stats['rows']} "
        f"inserted={stats['inserted']} ({mode})",
        file=sys.stderr,
    )
    for name, n in sorted(stats["per_index"].items()):
        print(f"  {n:4d}  {name}", file=sys.stderr)
    if a.verbose:
        industries = sorted({r["industry"] for b in batches for r in b.rows if r["industry"]})
        print("industries: " + "; ".join(industries), file=sys.stderr)
    if not a.apply:
        print("dry-run — pass --apply to write sources.duckdb", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
