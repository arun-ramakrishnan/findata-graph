#!/usr/bin/env python3
"""BSE company-name search — tier 2 of the GF ticker-fallback proposal
(doc/improvements/proposals/google_finance_ticker_fallback.md §4.1).

Resolves company NAME -> (scrip code, NSE symbol, listed name) via BSE's
own PeerSmartSearch service (the autocomplete behind bseindia.com's search
box; endpoint + shape verified 2026-08-25, fixtures under
tests/fixtures/exchange_search/). NSE's official search API was probed the
same day and is Akamai-hard-blocked from this network (403 + bot-manager
on warmup) — BSE rows carry the NSE symbol anyway (the TMPV case), so BSE
covers both exchanges.

Design constraints (same as googlefinance.py):
  - Thin: one GET, one regex parse, no dependency on unofficial wrappers
    (the BseIndiaApi recipe just pointed at the endpoint/params).
  - The caller owns verification and persistence; matches are evidence,
    never auto-applied (Tier-C discipline).
  - Raw responses cache under gitignored memory/ so sweeps never re-query.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BSE_SEARCH_URL = ("https://api.bseindia.com/BseIndiaAPI/api/"
                  "PeerSmartSearch/w")
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")


@dataclass(frozen=True)
class BseMatch:
    scrip: str      # '544399'  -> GF slug '544399:BOM'
    symbol: str     # 'TMPV'    -> GF slug 'TMPV:NSE' / Yahoo 'TMPV.NS'
    name: str       # 'SRIGEE DLM LTD' (display only — verification always
                    #  re-checks the quote page against OUR entity name)
    isin: str = ""


# Each result row: liclick('scrip','NAME') plus a <span> of
# 'SYMBOL&nbsp;&nbsp;&nbsp;ISIN&nbsp;&nbsp;&nbsp;scrip' (symbol always
# present in equity rows; ISIN may be 'NA').
_LI_RE = re.compile(
    r"liclick\('(?P<scrip>\d+)','(?P<name>[^']*)'\)"
    r".*?<span>(?P<span>.*?)</span>",
    re.DOTALL,
)


def _decode_body(body: str) -> str:
    """BSE returns the HTML fragment JSON-string-encoded."""
    body = body.strip()
    if body.startswith('"'):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def parse_bse_response(text: str) -> list[BseMatch]:
    """Parse a (decoded) PeerSmartSearch response into matches.

    'No Match Found' -> []. Rows without a liclick handler are skipped.
    """
    if "No Match Found" in text:
        return []
    matches: list[BseMatch] = []
    for m in _LI_RE.finditer(text):
        # The match-highlight <strong> tags can wrap the symbol (and the
        # query text elsewhere) — strip tags before tokenising.
        span = re.sub(r"<[^>]+>", " ", m.group("span"))
        tokens = [t for t in span.replace("&nbsp;", " ").split() if t]
        symbol = tokens[0] if tokens else ""
        isin = tokens[1] if len(tokens) > 1 and tokens[1].startswith("IN") else ""
        matches.append(BseMatch(scrip=m.group("scrip"), symbol=symbol,
                                name=m.group("name"), isin=isin))
    return matches


def bse_search(query: str, *, timeout: int = 20) -> str:
    """Query PeerSmartSearch by company name; returns the decoded response.

    Raises on network errors — callers decide (the resolution driver
    treats any raise as 'tier 2 unavailable' for that target).
    """
    url = f"{BSE_SEARCH_URL}?Type=SS&text={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={  # noqa: S310  # https-only constant URL + quoted param
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return _decode_body(resp.read().decode("utf-8", "replace"))


def bse_search_cached(
    query: str, cache_dir: Path, *, timeout: int = 20,
) -> tuple[list[BseMatch], bool]:
    """bse_search with a per-query text cache (same contract as
    googlefinance.load_or_fetch: returns (matches, from_cache))."""
    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", query)[:80]
    cache_file = cache_dir / f"bse_search_{safe}.txt"
    if cache_file.exists():
        return parse_bse_response(
            cache_file.read_text(encoding="utf-8")), True
    text = bse_search(query, timeout=timeout)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(text, encoding="utf-8")
    return parse_bse_response(text), False
