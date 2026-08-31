#!/usr/bin/env python3
"""Google Finance thin client - quote-page fetcher and parser (F1).

Part of the GF ticker-fallback proposal
(doc/improvements/proposals/google_finance_ticker_fallback.md). Google
Finance has NO public API (shut down 2012) and no maintained python
library; the beta quote pages embed their data in AF_initDataCallback
payloads plus rendered label/value text. This module fetches a slug page
and parses that content into a typed dict - nothing more.

Design constraints (proposal sections 2 and 6):
  - Dead tickers return HTTP 200 shell pages, so validity is decided by
    CONTENT ("About <name>" + stat rows present), never status codes.
  - Parsing uses structural checks (label->value adjacency), not
    positional indexes into Google's internal payloads.
  - The parser never writes anything; callers own persistence.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

GF_BETA_URL = "https://www.google.com/finance/beta/quote/{slug}"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

_STAT_LABELS = {
    "Mkt. cap": "mkt_cap",
    "P/E ratio": "pe_ratio",
    "EPS": "eps",
    "52-wk high": "wk52_high",
    "52-wk low": "wk52_low",
    "Volume": "volume",
    "Avg. vol.": "avg_vol",
    "Prev Close": "prev_close",
}

_PROFILE_LABELS = {
    "CEO": "ceo",
    "Employees": "employees",
    "No. of employees": "employees",
    "Founded": "founded",
    "Headquarters": "headquarters",
    "Sector": "sector",
    "Website": "website",
}


def fetch_quote(slug: str, *, timeout: int = 30) -> str:
    """Fetch a beta quote page. Raises on network errors (caller decides)."""
    # https-only by construction: GF_BETA_URL is a constant https template
    # and the slug is percent-encoded above (S310 audited, per house
    # precedent in capture_newsletter_images.py).
    url = GF_BETA_URL.format(slug=urllib.parse.quote(slug, safe=":.%"))
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def _visible_lines(html: str) -> list[str]:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "\n", text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_number(raw: str) -> float | None:
    """'79.10' -> 79.10 ; '65.78B' -> 65.78e9 ; '501.78M' -> 501.78e6."""
    m = re.match(r"^[^0-9]*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([BMTK]?)\s*$", raw)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    mult = {"B": 1e9, "M": 1e6, "T": 1e12, "K": 1e3}.get(m.group(2), 1)
    return value * mult


def parse_stats(lines: list[str]) -> dict[str, float]:
    """Label -> next-line pairs from the visible stat region."""
    stats: dict[str, float] = {}
    for i, line in enumerate(lines):
        key = _STAT_LABELS.get(line)
        if key is None or i + 1 >= len(lines):
            continue
        number = _parse_number(lines[i + 1])
        if number is not None and key not in stats:
            stats[key] = number
    return stats


def parse_profile(lines: list[str]) -> dict[str, str]:
    """About-region fields: CEO / Employees / Sector / Website ..."""
    profile: dict[str, str] = {}
    about_idx = next((i for i, line in enumerate(lines) if line.startswith("About ")), None)
    if about_idx is None:
        return profile
    for i in range(about_idx, min(len(lines), about_idx + 30)):
        key = _PROFILE_LABELS.get(lines[i])
        if key and i + 1 < len(lines):
            profile.setdefault(key, lines[i + 1])
    return profile


def parse_price(lines: list[str]) -> float | None:
    """Current price from the quote header — NOT a label row.

    The header block renders the live price as the first rupee-prefixed
    bare number line ('₹575.00' right under the company name); the stat
    rows only carry Prev Close etc.
    """
    for line in lines:
        m = re.match(r"^₹\s*([0-9][0-9,]*(?:\.[0-9]+)?)$", line)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def parse_quote(html: str) -> dict | None:
    """Parse a beta quote page. Returns None for shells/dead slugs.

    Validity contract: BOTH an 'About <name>' heading AND at least one
    recognised stat row must be present (bogus slugs serve 200-shell pages).
    """
    lines = _visible_lines(html)
    about_idx = next((i for i, line in enumerate(lines) if line.startswith("About ")), None)
    stats = parse_stats(lines)
    if about_idx is None or not stats:
        return None
    profile = parse_profile(lines)
    company_name = lines[about_idx][len("About ") :].strip()
    return {
        "company_name": company_name,
        "stats": stats,
        "profile": profile,
        "price": parse_price(lines),
    }


def name_match_score(parsed_name: str, expected_name: str) -> float:
    """0..1 fuzzy similarity used for verification (Tier-C discipline).

    Case-folded SequenceMatcher on token-sorted names so 'Srigee DLM Ltd'
    matches 'Srigee DLM Limited' strongly while unrelated companies fail.
    """

    def norm(name: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", name.lower())
        tokens = [t for t in tokens if t not in {"ltd", "limited", "inc", "corp", "co", "company"}]
        return " ".join(sorted(tokens))

    a, b = norm(parsed_name), norm(expected_name)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# Slug grammar (F2 tier 1, proposal §4.1)                                      #
# --------------------------------------------------------------------------- #
_GF_SUFFIX_TO_YAHOO = {":NSE": ".NS", ":BOM": ".BO"}


def slug_candidates(yahoo_ticker: str) -> list[str]:
    """Tier-1 GF slug variants for a FAILING Yahoo symbol (proposal §4.1).

    '<SYM>:NSE' / '<SYM>:BOM' swaps of the failing symbol, native exchange
    first. Bare unsuffixed tickers (US/foreign, e.g. HBI) get no tier-1
    candidates — GF India slugs are symbol- or scrip-code-based, so those
    need tier 2 (F3) or a curated override.
    """
    t = yahoo_ticker.strip().upper()
    if t.endswith(".NS"):
        return [f"{t[:-3]}:NSE", f"{t[:-3]}:BOM"]
    if t.endswith(".BO"):
        return [f"{t[:-3]}:BOM", f"{t[:-3]}:NSE"]
    return []


def yahoo_symbol_for_slug(gf_slug: str) -> str | None:
    """Inverse slug -> Yahoo mapping ('544399:BOM' -> '544399.BO').

    Non-Indian slugs and bare symbols return None — nothing to map back
    to a Yahoo-format ticker (G4 writeback candidates, consumed in F4).
    """
    stem, sep, suffix = gf_slug.rpartition(":")
    if not sep:
        return None
    mapped = _GF_SUFFIX_TO_YAHOO.get(f":{suffix}")
    return f"{stem}{mapped}" if mapped else None


def load_or_fetch(slug: str, cache_dir: Path, *, timeout: int = 30) -> tuple[str, bool]:
    """Fetch a slug page with a simple per-slug HTML cache (debug aid).

    Returns (html, from_cache). Sweep callers should use this so repeated
    runs do not re-hit Google; cache lives under gitignored memory/.
    """
    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", slug)
    cache_file = cache_dir / f"{safe}.html"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8"), True
    html = fetch_quote(slug, timeout=timeout)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(html, encoding="utf-8")
    return html, False


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: googlefinance.py <SLUG>", file=sys.stderr)
        raise SystemExit(2)
    parsed = parse_quote(fetch_quote(sys.argv[1]))
    if parsed is None:
        print(f"{sys.argv[1]}: no valid quote page (shell/dead)", file=sys.stderr)
        raise SystemExit(1)
    import json

    print(json.dumps(parsed, indent=2, ensure_ascii=False))
