#!/usr/bin/env python3
"""FinnHub symbol search — discovery source #1 of the combined
market-data resolution proposal
(doc/improvements/proposals/market_data_resolution.md §3 stage 1).

Resolves company NAME -> Yahoo-format ticker (``544399.BO``,
``TMPV.NS``) via finnhub.io's documented /search endpoint, free tier.
Deliberately raw-urllib like googlefinance.py / exchange_search.py —
the finnhub-python package stays an experiment tool, not a dependency.

Free-tier facts (probed 2026-08-25, fixtures under
tests/fixtures/finnhub_search/):
  - search q is capped ~20 chars ("q too long" 422 above that)
  - Indian QUOTES/profiles are premium-403 — this module only SEARCHES
  - name noise exists ('Gati' -> navigation companies); callers must
    verify candidates against the entity name (Tier-C discipline)
  - token comes from FINNHUB_API_KEY in gitignored memory/.env (see
    helpers/core/env.py); never printed or committed
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from helpers.core.env import load_memory_env

FINNHUB_SEARCH_URL = "https://finnhub.io/api/v1/search"
_QUERY_LIMIT = 20  # FinnHub 422s above ~20 chars (measured)


@dataclass(frozen=True)
class FhMatch:
    symbol: str       # '544399.BO' — exact Yahoo format, directly
    description: str  # 'Srigee DLM Ltd' (display; callers verify names)


def trim_query(name: str, limit: int = _QUERY_LIMIT) -> str:
    """Trim to the last full word within FinnHub's ~20-char q limit."""
    if len(name) <= limit:
        return name
    cut = name[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > 0 else cut


def parse_search_response(text: str) -> list[FhMatch]:
    """Parse a /search JSON body; empty/absent result -> []."""
    data = json.loads(text)
    return [
        FhMatch(symbol=r["symbol"], description=r.get("description", ""))
        for r in data.get("result", []) if r.get("symbol")
    ]


def _resolve_token(env_file: Path | None = None) -> str:
    """FINNHUB_API_KEY from the environment (loaded from memory/.env).

    ``env_file`` is a test seam: an explicit .env path instead of the
    repo's memory/.env.
    """
    load_memory_env(env_file)
    token = os.environ.get("FINNHUB_API_KEY")
    if not token:
        raise RuntimeError(
            "no finnhub token: set FINNHUB_API_KEY in memory/.env "
            "(gitignored) or export it")
    return token


def fh_search(query: str, *, timeout: int = 20) -> str:
    """Query the /search endpoint; returns the raw JSON text.

    Raises on network / auth (401 bad token, 403 premium, 429 rate
    limit) — callers decide (the resolution driver treats a raise as
    'source unavailable' for that target, non-fatal).
    """
    token = _resolve_token()
    url = (f"{FINNHUB_SEARCH_URL}?q={urllib.parse.quote(trim_query(query))}"
           f"&token={token}")
    req = urllib.request.Request(url, headers={  # noqa: S310  # https-only constant + quoted param
        "User-Agent": "findata-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def fh_search_cached(
    query: str, cache_dir: Path, *, timeout: int = 20,
) -> tuple[list[FhMatch], bool]:
    """fh_search with a per-query text cache (same contract as
    exchange_search.bse_search_cached / googlefinance.load_or_fetch)."""
    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", trim_query(query))[:80]
    cache_file = cache_dir / f"fh_search_{safe}.txt"
    if cache_file.exists():
        return parse_search_response(
            cache_file.read_text(encoding="utf-8")), True
    text = fh_search(query, timeout=timeout)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(text, encoding="utf-8")
    return parse_search_response(text), False


def probe_queries(name: str) -> list[str]:
    """Progressive FinnHub queries: full trimmed name, then first two
    words, then first word — deduped, order preserved.

    FinnHub's index is query-specificity-sensitive, and long names
    collapse under trim_query (``'Bajaj Allianz General Insurance'``
    trims to ``'Bajaj Allianz'``, which finds nothing while shorter
    stems may). Each probe goes through the per-query text cache, so
    every variant costs at most one real call ever.
    """
    full = trim_query(name)
    words = full.split()
    out = [full]
    if len(words) >= 3:
        out.append(" ".join(words[:2]))
    if len(words) >= 2:
        out.append(words[0])
    seen: set[str] = set()
    return [q for q in out if not (q in seen or seen.add(q))]


def fh_search_multi(
    name: str, cache_dir: Path, *, timeout: int = 20, delay: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    search_fn: Callable[..., tuple[list[FhMatch], bool]] =
    fh_search_cached,
) -> tuple[list[FhMatch], bool]:
    """Progressive-probe search — same ``(matches, from_cache)`` contract
    as :func:`fh_search_cached`, drop-in as the resolution driver's
    lookup_fn.

    Runs probe_queries(name) in order and returns the first probe that
    yields any match. Politeness: sleeps ``delay`` after each real
    (cache-miss) empty probe, pacing whatever fires next — including
    this function's own later probes and the caller's next target.
    ``from_cache`` is False iff at least one probe hit the network, so
    warm runs never sleep anywhere.
    """
    any_real = False
    for q in probe_queries(name):
        matches, from_cache = search_fn(q, cache_dir, timeout=timeout)
        if matches:
            return matches, from_cache
        if not from_cache:
            any_real = True
            sleeper(delay)
    return [], not any_real
