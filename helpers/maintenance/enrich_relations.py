#!/usr/bin/env python3
"""Relations-enrichment driver — "Relations 2.0" (E2 slice).

One driver per source (proposal
doc/improvements/proposals/relation_enrichment_sources.md §6.1), each source
independently runnable and independently idempotent:

    python3 helpers/maintenance/enrich_relations.py --source yfinance --dry-run

House doctrine (§3 G2):
  - read-only fetch -> explicit apply (--apply; default OFF = dry-run),
  - dry-run prints projected counts (parity asserted in tests),
  - idempotent DELETE-by-source_ref-prefix + INSERT OR IGNORE,
  - full provenance in source_ref/properties,
  - ticker-first endpoint resolution off entities.ticker (§6.0); the ~149
    ticker-less companies are deliberately unlisted and are SKIPPED, never
    fuzz-matched.

E2 scope: the yfinance industry pass with KNN-by-market-cap topology
(§6.2) plus the ticker-hygiene report (§6.4). The remaining sources land
in later slices:
    prose      -> E1 (done: helpers/graph/extract_relations.py)
    embeddings -> E3     coinfer -> E4     holders -> E5     wikidata -> deferred

Google-Finance fallback (F2/F3,
doc/improvements/proposals/google_finance_ticker_fallback.md):
``--source googlefinance`` re-attacks exactly the yfinance failures
(``[ticker_issues]`` of the last report) plus an opt-in unlisted set:
curated overrides first (entity_gf_map, read-only until F4), then tier-1
slug variants, then — with ``--tier2`` — BSE name-search discovery
(exchange_search.py). Every hit is verified by fuzzy About-name match.
``--apply`` (S3) persists resolutions to entity_gf_map and writes
company_metrics rows for gf_only entities via one Sheets
GOOGLEFINANCE batch (googlesheets_metrics.py). Runs AFTER the yfinance
pass (it consumes that pass's report).

FinnHub stage 1 (market_data_resolution.md S1/S2):
``--source finnhub`` resolves each failure via FinnHub name search
(finnhub_search.py — Yahoo-format candidates, exchange-class guarded)
and verifies candidates with a SINGLE-ticker yfinance fetch; verified
hits become ticker writebacks on ``--apply`` (entities + note
frontmatter + fetch-cache extension — the fix enters the next bulk
sweep). Bulk doctrine: yfinance stays the only bulk source; every other
network call here is per-target over the bounded failure residue and
cached permanently under memory/.

Per-sweep network budgets (S4 audit 2026-08-25, 33-target residue):
yfinance bulk is the only bulk leg (cache-first); FinnHub lookup 1
call/target until its query cache fills (then 0); BSE search likewise;
GF pages <=4/target until the page cache fills (then 0); Sheets
metrics = 2 calls per sweep batch; single-ticker yfinance verifies are
success-cached in memory/fh_verify_cache.json (None results retry by
design so transient flakes heal). Warm re-sweep measured: GF+tier2
2.1s, finnhub 4.4s — zero non-yfinance network.

Ticker hygiene (§6.4): every 404/no-data ticker is reported (name, ticker,
error class) — stale tickers silently starving enrichment is the failure
mode this pass exists to surface. The tool reports; it never guesses a
replacement ticker.

See `make relations-enrich` (not wired into maint/maint-full: network-bound).
Run BEFORE `make graph-rebuild` so DuckDB picks the edges up.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path

# Bootstrap so this module is importable both as a script and as a package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.maintenance.enrich_from_yfinance import (  # noqa: E402
    DB_PATH,
    PROJECT_ROOT,
    fetch_company,
)
from helpers.maintenance.googlefinance import (  # noqa: E402
    load_or_fetch,
    name_match_score,
    parse_quote,
    slug_candidates,
    yahoo_symbol_for_slug,
)
from helpers.maintenance.exchange_search import bse_search_cached  # noqa: E402
from helpers.maintenance.finnhub_search import (  # noqa: E402
    fh_search_multi,
)
from helpers.maintenance.googlesheets_metrics import GF_ATTRIBUTES  # noqa: E402

log = logging.getLogger("enrich_relations")

REPORT_PATH = PROJECT_ROOT / "relations_report.txt"
# Persistent fetch cache — the ~931-ticker yfinance sweep costs minutes, so
# the .info payloads are retained across runs (topology/K experiments then
# cost zero network). Lives under gitignored memory/ next to research.db.
FETCH_CACHE_PATH = PROJECT_ROOT / "memory" / "yf_relations_fetch_cache.json"

SOURCE_REF_PREFIX = "yfinance"
SOURCES = ("prose", "yfinance", "googlefinance", "finnhub", "embeddings",
           "coinfer", "holders", "wikidata")
# Sources owned by other slices (E1 done in extract_relations.py; rest TBD;
# googlefinance handled by the F2+ fallback pass in this driver).
_NOT_IMPLEMENTED = {
    "wikidata": "deferred by proposal §12.4",
}

# --- Google-Finance fallback (google_finance_ticker_fallback.md, F2) -------- #
# Tier-1 pages cached under gitignored memory/ so re-runs never re-hit Google.
GF_PAGE_CACHE_DIR = PROJECT_ROOT / "memory" / "gf_page_cache"
# Fuzzy About-name verification floor (Tier-C discipline: misses land in the
# report for human curation, never auto-applied). Measured 2026-08-24:
# same-company variants (abbreviations, Ltd/Limited) score >=0.7, unrelated
# companies <0.4 — 0.6 splits the two with margin.
GF_NAME_MATCH_THRESHOLD = 0.6
# Politeness floor between real network fetches (cache hits don't sleep).
GF_MIN_DELAY_S = 1.0
# Tier 2 (F3): BSE name-search rows considered per target, top rows only
# (relevance-ordered; the 'gati' probe returned 7 substring-noise rows).
GF_T2_MATCH_LIMIT = 2
# Curated overrides + resolved-slug persistence (§4.3). F2 reads; the
# --apply write path (S3) persists resolutions and gf_only metrics.
# Seeded one-time by human mappings.
ENTITY_GF_MAP_DDL = """
CREATE TABLE IF NOT EXISTS entity_gf_map (
    entity_name TEXT PRIMARY KEY,
    gf_slug TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('yahoo_mapped_back', 'gf_only')),
    resolved_at TEXT NOT NULL,
    verified_name TEXT NOT NULL
)
"""
# Terminal ticker classifications (--classify, market_data_resolution.md
# §5): dead ends a human has ruled out — never re-probed by either
# resolution pass; reported under [terminal] (they satisfy the §7
# "remainder explicitly classified" clause). Plain PK like
# entity_gf_map (no FK — test DBs stay light); rows are written ONLY via
# --classify / removed via --unclassify.
TERMINAL_STATUSES = ("delisted", "amalgamated")
ENTITY_TICKER_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS entity_ticker_status (
    entity_name TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('delisted', 'amalgamated')),
    successor TEXT,
    decided_at TEXT NOT NULL
)
"""
# GOOGLEFINANCE attribute -> company_metrics (label, unit, converter).
# market_capitalization stays in ₹ CRORE (the stored-metric contract the
# KNN mcap loader reads); Sheets returns absolute INR, hence /1e7.
_Conv = Callable[[float], float]
GF_METRIC_SPEC: dict[str, tuple[str, str, _Conv | None]] = {
    "price": ("price", "inr", None),
    "marketcap": ("market_capitalization", "crore",
                  lambda v: v / 1e7),
    "pe": ("pe_ratio", "ratio", None),
    "eps": ("eps", "inr", None),
    "high52": ("wk52_high", "inr", None),
    "low52": ("wk52_low", "inr", None),
}


def _convert_metric(value: float, conv: _Conv | None) -> float:
    """Apply a GF_METRIC_SPEC converter (None = identity)."""
    return conv(value) if conv is not None else value

# Coarse frontmatter market_cap bucket -> representative size in ₹ crore.
# Only used when company_metrics.market_capitalization is absent (§6.0).
_BUCKET_CRORE = {
    "mega": 300_000.0, "large": 75_000.0, "mid": 25_000.0,
    "small": 5_000.0, "micro": 1_000.0, "nano": 200.0,
}
_BUCKET_RE = re.compile(r"^market_cap:\s*(\w+)", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Company universe (ticker-first resolution, §6.0)                            #
# --------------------------------------------------------------------------- #
def load_companies(
    conn: sqlite3.Connection,
) -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """Return (tickered_companies, deliberately_unlisted_names).

    Tickered companies are (name, ticker, file_path) rows from entities.
    Ticker-less companies are recorded for the report but NEVER looked up
    or fuzz-matched (frontmatter contract: ``ticker: null`` = known-unlisted).
    """
    rows = conn.execute(
        "SELECT name, ticker, file_path FROM entities "
        "WHERE entity_type = 'company' AND ticker IS NOT NULL "
        "AND ticker != '' ORDER BY name"
    ).fetchall()
    unlisted = [
        r[0] for r in conn.execute(
            "SELECT name FROM entities WHERE entity_type = 'company' "
            "AND (ticker IS NULL OR ticker = '') ORDER BY name"
        ).fetchall()
    ]
    return [(r[0], r[1], r[2]) for r in rows], unlisted


def fetch_all(
    companies: list[tuple[str, str, str | None]],
    *,
    fetch_fn=fetch_company,
    workers: int = 2,
) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Fetch .info for every tickered company.

    Returns (info_by_name, failures) where failures are (name, ticker)
    pairs classified as 404/no-data — the ticker-hygiene list.
    """
    info_by_name: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_fn, ticker): (name, ticker)
            for name, ticker, _fp in companies
        }
        for fut in as_completed(futures):
            name, ticker = futures[fut]
            try:
                info = fut.result()
            except Exception:  # defensive: fetch_fn contract says None, not raise
                info = None
            if info is None:
                failures.append((name, ticker))
            else:
                info_by_name[name] = info
    return info_by_name, sorted(failures)


# --------------------------------------------------------------------------- #
# Market-cap proximity data (§6.0: stored metric first, bucket fallback)      #
# --------------------------------------------------------------------------- #
def load_log_mcaps(
    conn: sqlite3.Connection,
    names: list[str],
) -> dict[str, float]:
    """log10(market cap in ₹ crore) per company, best-effort.

    Primary: latest company_metrics.market_capitalization value_num.
    Fallback: coarse market_cap bucket tag parsed from the note's YAML
    frontmatter (_BUCKET_CRORE representative points). Companies with
    neither signal get no entry; _knn_pairs treats them via the industry
    median so they still connect to their peers.
    """
    out: dict[str, float] = {}
    if names:
        placeholders = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT entity, value_num FROM company_metrics "  # noqa: S608
            f"WHERE metric_label = 'market_capitalization' AND value_num "
            f"IS NOT NULL AND entity IN ({placeholders})",
            names,
        ).fetchall()
        for entity, value_num in rows:
            if value_num and value_num > 0:
                out[entity] = math.log10(value_num)
    return out


def bucket_fallback_mcap(file_path: str | None) -> float | None:
    """log10(representative crore) from a note's market_cap bucket tag."""
    if not file_path:
        return None
    note = PROJECT_ROOT / file_path
    if not note.exists():
        return None
    m = _BUCKET_RE.search(note.read_text(encoding="utf-8")[:2000])
    if not m:
        return None
    # frontmatter value is "mid_cap"; _BUCKET_CRORE keys drop the suffix.
    bucket = m.group(1).lower().removesuffix("_cap")
    crore = _BUCKET_CRORE.get(bucket)
    return math.log10(crore) if crore else None


# --------------------------------------------------------------------------- #
# Topology (§6.2): bounded peers, not raw cliques                             #
# --------------------------------------------------------------------------- #
def _pair(a: str, b: str) -> tuple[str, str]:
    """Canonical symmetric pair (source <= target, decision D4)."""
    return (a, b) if a <= b else (b, a)


def knn_industry_pairs(
    names: list[str],
    log_mcaps: dict[str, float],
    k: int,
    *,
    mutual: bool = False,
) -> dict[tuple[str, str], int]:
    """Symmetric union of per-company K-nearest neighbours within an industry.

    Distance is |log10 mcap_a - log10 mcap_b| (size proximity, NOT price).
    Companies without an mcap signal inherit the industry median so they
    remain connectable. Returns canonical pairs -> min rank distance seen
    (rank 1 = nearest). Deterministic: ties broken alphabetically.

    With ``mutual=True`` a pair survives only when EACH endpoint picked the
    other among its own K nearest (reciprocity precision filter). Measured
    2026-08-24 on the live corpus: coarse Yahoo industry pools make many
    one-sided picks ("17 small caps all point at one large cap"); mutuality
    drops those asymmetric arcs (4015 -> ~2.3k pairs).
    """
    if len(names) < 2:
        return {}
    present = sorted(
        v for v in (log_mcaps.get(n) for n in names) if v is not None)
    # Industry median anchors companies with no mcap signal at all so they
    # stay connectable; a fully mcap-less industry collapses to distance 0
    # and falls back to alphabetical neighbour choice.
    median = present[len(present) // 2] if present else 0.0
    caps = {n: log_mcaps.get(n, median) for n in names}
    ordered = sorted(names)
    pairs: dict[tuple[str, str], int] = {}
    picks: dict[str, set[str]] = {}
    for i, a in enumerate(ordered):
        others = [
            (abs(caps[a] - caps[b]), b)
            for b in ordered if b != a
        ]
        others.sort()  # (distance, name) — alphabetical tie-break
        neighbours = [b for _dist, b in others[:k]]
        picks[a] = set(neighbours)
        for rank_0, b in enumerate(neighbours):
            pair = _pair(a, b)
            rank = rank_0 + 1
            if pair not in pairs or rank < pairs[pair]:
                pairs[pair] = rank
    if mutual:
        pairs = {
            p: r for p, r in pairs.items()
            if p[1] in picks[p[0]] and p[0] in picks[p[1]]
        }
    return pairs


def clique_industry_pairs(names: list[str]) -> dict[tuple[str, str], int]:
    """Full industry clique (the retired v1 topology; behind --topology clique)."""
    ordered = sorted(names)
    return {_pair(a, b): 1 for i, a in enumerate(ordered) for b in ordered[i + 1:]}


def weight_for_rank(rank: int, k: int) -> float:
    """Rank-distance weight decay: 1.0 at rank 1 -> 0.4 at rank k."""
    k = max(int(k), 2)
    return round(max(0.4, 1.0 - 0.6 * (rank - 1) / (k - 1)), 3)


# --------------------------------------------------------------------------- #
# The yfinance pass                                                           #
# --------------------------------------------------------------------------- #
def build_candidate_edges(
    info_by_name: dict[str, dict],
    log_mcaps: dict[str, float],
    *,
    topology: str = "knn",
    k: int = 8,
    mutual: bool = False,
) -> list[tuple[str, str, float, str, dict]]:
    """Group fetched companies by Yahoo industry and build competes_with edges.

    Returns (source, target, weight, source_ref, properties) tuples with
    canonical symmetric ordering. source_ref prefix 'yfinance:' keeps the
    delete-scoped-by-prefix idempotence contract.
    """
    by_industry: dict[str, list[str]] = defaultdict(list)
    for name, info in info_by_name.items():
        industry = info.get("industry") or ""
        if industry:
            by_industry[industry].append(name)

    today = datetime.now(UTC).date().isoformat()
    source_ref = f"{SOURCE_REF_PREFIX}:industry:{today}"
    edges: list[tuple[str, str, float, str, dict]] = []
    for industry, members in by_industry.items():
        if len(members) < 2:
            continue
        if topology == "clique":
            pairs = clique_industry_pairs(members)
        else:
            pairs = knn_industry_pairs(members, log_mcaps, k,
                                       mutual=mutual)
        for (a, b), rank in pairs.items():
            edges.append((
                a, b, weight_for_rank(rank, k), source_ref,
                {"industry": industry, "rank": rank, "fetched_at": today},
            ))
    edges.sort()
    return edges


def apply_edges(
    conn: sqlite3.Connection,
    edges: list[tuple[str, str, float, str, dict]],
    *,
    dry_run: bool = True,
) -> int:
    """DELETE-by-prefix + INSERT OR IGNORE in one transaction.

    Dry-run counts how many of the candidate edges are NOT already present
    under the same (source, target, edge_type) — the parity number printed
    before --apply. Never touches the read-only relations VIEW.
    """
    existing: set[tuple[str, str]] = set()
    if dry_run:
        existing = {
            (r[0], r[1]) for r in conn.execute(
                "SELECT source, target FROM graph_edges "
                "WHERE edge_type = 'competes_with'"
            ).fetchall()
        }
        fresh = [(s, t) for s, t, *_ in edges if (s, t) not in existing]
        return len(fresh)

    with conn:
        conn.execute(
            "DELETE FROM graph_edges WHERE edge_type = 'competes_with' "
            "AND source_ref LIKE ?",
            (f"{SOURCE_REF_PREFIX}:%",),
        )
        inserted = 0
        for source, target, weight, source_ref, props in edges:
            cur = conn.execute(
                "INSERT OR IGNORE INTO graph_edges "
                "(source, target, edge_type, weight, properties, source_ref, "
                " symmetric) VALUES (?, ?, 'competes_with', ?, ?, ?, 1)",
                (source, target, weight, json.dumps(props, sort_keys=True),
                 source_ref),
            )
            inserted += cur.rowcount
    return inserted


def write_report(
    path: Path,
    *,
    mode: str,
    n_tickered: int,
    unlisted_names: list[str],
    n_fetched: int,
    failures: list[tuple[str, str]],
    n_edges: list[tuple[str, str, float, str, dict]],
    industries: dict[str, int],
    applied: int | None,
) -> None:
    """metrics_report.txt-style run report with the ticker-hygiene section."""
    lines = [
        "relations_report.txt — enrich_relations.py",
        f"generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"mode: {mode}",
        "",
        "[universe]",
        f"  tickered companies: {n_tickered}",
        f"  deliberately unlisted (skipped, never fuzz-matched): {len(unlisted_names)}",
        f"  fetched OK: {n_fetched}",
    ]
    if unlisted_names:
        lines += [f"  (unlisted) {n}" for n in unlisted_names]
    lines += [
        "",
        "[ticker_issues]  # 404 / no-data — enrichment silently starves on these",
        "    # name | ticker",
    ]
    if failures:
        lines += [f"    {name} | {ticker}" for name, ticker in failures]
    else:
        lines.append("    (none)")
    lines += [
        "",
        "[industries]",
    ]
    for ind, n in sorted(industries.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:4d}  {ind}")
    lines += ["", f"[competes_with candidates] total={len(n_edges)}"]
    if applied is not None:
        lines.append(f"[apply result] {'would-insert' if mode == 'dry-run' else 'inserted'}={applied}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve_log_mcaps(
    conn: sqlite3.Connection,
    companies: list[tuple[str, str, str | None]],
    info_by_name: dict[str, dict],
) -> dict[str, float]:
    """log10(market cap, ₹ crore) per company — three sources by priority.

    1. The fresh ``info['marketCap']`` we already fetched for this pass
       (INR; converted to crore). Measured 2026-08-24: covers 860/883
       fetched companies, while company_metrics.market_capitalization has
       only 30 rows — the stored metric alone made KNN degenerate to
       alphabetical ordering.
    2. Stored company_metrics.market_capitalization (already crore).
    3. Coarse market_cap bucket tag from note frontmatter.
    """
    caps: dict[str, float] = {}
    for name, info in info_by_name.items():
        mc = info.get("marketCap")
        if mc and mc > 0:
            caps[name] = math.log10(mc / 1e7)  # INR -> ₹ crore
    stored = load_log_mcaps(conn, [n for n, *_ in companies])
    for name, value in stored.items():
        caps.setdefault(name, value)
    for name, _t, fp in companies:
        if name not in caps:
            fb = bucket_fallback_mcap(fp)
            if fb is not None:
                caps[name] = fb
    return caps


def run_yfinance_pass(
    conn: sqlite3.Connection,
    *,
    topology: str = "knn",
    k: int = 8,
    workers: int = 2,
    dry_run: bool = True,
    check_only: bool = False,
    fetch_fn=fetch_company,
    fetch_cache: Path | None = FETCH_CACHE_PATH,
    refresh_cache: bool = False,
    mutual: bool = False,
) -> int:
    """The full yfinance pass: fetch -> hygiene -> edges -> report."""
    companies, unlisted = load_companies(conn)
    log.info("universe: %d tickered, %d deliberately unlisted (skipped)",
             len(companies), len(unlisted))

    t0 = time.perf_counter()
    cache: Path | None = fetch_cache
    if cache is not None and cache.exists() and not refresh_cache:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        info_by_name = payload["info_by_name"]
        failures = [tuple(f) for f in payload["failures"]]
        fetched_at = payload.get("fetched_at", "unknown")
        log.info("loaded %d fetched infos (+%d failures) from cache %s "
                 "(fetched_at: %s)", len(info_by_name), len(failures),
                 cache, fetched_at)
    else:
        info_by_name, failures = fetch_all(companies, fetch_fn=fetch_fn,
                                           workers=workers)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "info_by_name": info_by_name,
                "failures": failures,
            }), encoding="utf-8")
            log.info("fetch cache written to %s", cache)
    log.info("fetched %d/%d in %.1fs (%d failed)",
             len(info_by_name), len(companies), time.perf_counter() - t0,
             len(failures))
    if failures:
        for name, ticker in failures[:20]:
            log.warning("ticker issue: %s (%s)", name, ticker)
        if len(failures) > 20:
            log.warning("... and %d more (see report)", len(failures) - 20)

    edges: list[tuple[str, str, float, str, dict]] = []
    industries: dict[str, int] = {}
    if not check_only:
        caps = _resolve_log_mcaps(conn, companies, info_by_name)
        edges = build_candidate_edges(info_by_name, caps,
                                      topology=topology, k=k,
                                      mutual=mutual)
        industries = {}
        for _s, _t, _w, _r, props in edges:
            industries[props["industry"]] = industries.get(
                props["industry"], 0) + 1

    applied: int | None = None
    if not check_only:
        applied = apply_edges(conn, edges, dry_run=dry_run)
        mode = "dry-run" if dry_run else "apply"
        log.info("%s: %d candidate edges, %d %s",
                 mode, len(edges), applied,
                 "would insert" if dry_run else "inserted")

    write_report(
        REPORT_PATH, mode="check-only" if check_only else
        ("dry-run" if dry_run else "apply"),
        n_tickered=len(companies), unlisted_names=unlisted,
        n_fetched=len(info_by_name), failures=failures,
        n_edges=edges, industries=industries, applied=applied,
    )
    log.info("report written to %s", REPORT_PATH)
    if not check_only and not dry_run:
        print("reminder: run `make graph-rebuild` so DuckDB picks up the new "
              "edges (qa's cache-consistency check fails otherwise)")
    return 0


# --------------------------------------------------------------------------- #
# E3: semantic_peer from DuckDB VSS (bge-small-en-v1.5, 384d)                 #
# --------------------------------------------------------------------------- #
EMBEDDINGS_SOURCE_REF_PREFIX = "embeddings:bge-small:v1"
EMBEDDINGS_REPORT_PATH = PROJECT_ROOT / "relations_report.txt"


def _semantic_pair_for_company(
    company: str,
    gq: object,
    dcon: object,
    k: int,
    threshold: float,
    today: str,
) -> list[tuple[tuple[str, str], dict]]:
    """Fetch top-k neighbours for one company and return canonical pairs."""
    try:
        neigh = gq.semantic_neighbors(dcon, company, k=k)  # type: ignore[attr-defined]
    except Exception as e:
        log.warning("semantic_neighbors failed for %s: %s", company, e)
        return []
    out: list[tuple[tuple[str, str], dict]] = []
    for rank, (other, _sector, score) in enumerate(neigh, start=1):
        if score < threshold:
            continue
        a, b = _pair(company, other)
        out.append(((a, b), {
            "cosine": round(float(score), 4),
            "rank": rank,
            "fetched_at": today,
        }))
    return out


def build_semantic_peer_edges(  # noqa
    conn: sqlite3.Connection,
    *,
    k: int = 10,
    threshold: float = 0.0,
) -> list[tuple[str, str, float, str, dict]]:
    """Build semantic_peer candidates from company_embeddings via DuckDB VSS.

    For each company, take its top-k cosine neighbours (semantic_neighbors),
    filter by threshold, then canonicalise (a <= b) and de-dupe symmetrically
    keeping the highest cosine per unordered pair. Weight is fixed 0.5 per
    proposal §5; source_ref is embeddings:bge-small:v1:<date>.
    """
    try:
        n = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    if n == 0:
        log.warning("company_embeddings is empty — run helpers/graph/embeddings.py first")
        return []
    try:
        models = [r[0] for r in conn.execute("SELECT DISTINCT model FROM company_embeddings").fetchall()]
        log.info("company_embeddings: %d rows, models=%s", n, models)
    except Exception as e:
        log.debug("failed to fetch embedding models: %s", e)
    today = datetime.now(UTC).date().isoformat()
    source_ref = f"{EMBEDDINGS_SOURCE_REF_PREFIX}:{today}"
    try:
        import helpers.graph.query as gq
    except Exception as e:
        log.error("failed to import helpers.graph.query: %s", e)
        return []
    try:
        dcon = gq.connect()  # type: ignore[attr-defined]
    except Exception as e:
        log.error("failed to connect DuckDB for VSS: %s", e)
        return []
    companies = [r[0] for r in conn.execute(
        "SELECT name FROM entities WHERE entity_type='company' ORDER BY name"
    ).fetchall()]
    pair_to_props: dict[tuple[str, str], dict] = {}
    for company in companies:
        for pair, props in _semantic_pair_for_company(company, gq, dcon, k, threshold, today):
            prev = pair_to_props.get(pair)
            if prev is None or props["cosine"] > prev["cosine"]:
                pair_to_props[pair] = props
    edges: list[tuple[str, str, float, str, dict]] = []
    for (a, b), props in sorted(pair_to_props.items()):
        edges.append((a, b, 0.5, source_ref, props))
    try:
        dcon.close()  # type: ignore[attr-defined]
    except Exception as e:
        log.debug("failed to close DuckDB connection: %s", e)
    log.info("semantic_peer candidates: %d pairs (k=%d, threshold=%.3f, %d companies)",
             len(edges), k, threshold, len(companies))
    return edges


def apply_semantic_peer_edges(
    conn: sqlite3.Connection,
    edges: list[tuple[str, str, float, str, dict]],
    *,
    dry_run: bool = True,
) -> int:
    """DELETE-by-prefix + INSERT OR IGNORE for semantic_peer (idempotent).

    Dry-run counts fresh pairs not already present; apply deletes the
    previous embeddings:* batch then inserts the new one.
    """
    if dry_run:
        existing = {
            (r[0], r[1]) for r in conn.execute(
                "SELECT source, target FROM graph_edges WHERE edge_type='semantic_peer'"
            ).fetchall()
        }
        fresh = [(s, t) for s, t, *_ in edges if (s, t) not in existing]
        return len(fresh)
    with conn:
        conn.execute(
            "DELETE FROM graph_edges WHERE edge_type='semantic_peer' AND source_ref LIKE ?",
            (f"{EMBEDDINGS_SOURCE_REF_PREFIX}:%",),
        )
        inserted = 0
        for source, target, weight, source_ref, props in edges:
            cur = conn.execute(
                "INSERT OR IGNORE INTO graph_edges "
                "(source, target, edge_type, weight, properties, source_ref, symmetric) "
                "VALUES (?, ?, 'semantic_peer', ?, ?, ?, 1)",
                (source, target, weight, json.dumps(props, sort_keys=True), source_ref),
            )
            inserted += cur.rowcount
    return inserted


def run_embeddings_pass(
    conn: sqlite3.Connection,
    *,
    k: int = 10,
    threshold: float = 0.0,
    dry_run: bool = True,
) -> int:
    """E3 driver: VSS → semantic_peer edges → report.

    Mirrors the yfinance pass skeleton: build candidates, apply (or
    dry-run), write a report section, remind about graph-rebuild.
    """
    edges = build_semantic_peer_edges(conn, k=k, threshold=threshold)
    applied = apply_semantic_peer_edges(conn, edges, dry_run=dry_run)
    mode = "dry-run" if dry_run else "apply"
    log.info("%s: %d semantic_peer candidates, %d %s",
             mode, len(edges), applied, "would insert" if dry_run else "inserted")
    # Append a small report section to the shared relations_report.txt
    try:
        today = datetime.now(UTC).date().isoformat()
        lines = [
            "",
            f"[semantic_peer]  # E3 embeddings:bge-small:v1 (k={k}, threshold={threshold})",
            f"  generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
            f"  mode: {mode}",
            f"  candidates: {len(edges)}",
            f"  {'would_insert' if dry_run else 'inserted'}: {applied}",
            f"  source_ref prefix: {EMBEDDINGS_SOURCE_REF_PREFIX}:{today}",
        ]
        # Sample 5 highest-cosine pairs for spot-check
        if edges:
            # edges are sorted by (a,b); re-sort by cosine desc for sample
            sample = sorted(edges, key=lambda e: -e[4].get("cosine", 0))[:5]
            lines.append("  sample (a | b | cosine | rank):")
            for a, b, _w, _ref, props in sample:
                lines.append(f"    {a} | {b} | {props.get('cosine')} | {props.get('rank')}")
        with open(EMBEDDINGS_REPORT_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        log.info("report appended to %s", EMBEDDINGS_REPORT_PATH)
    except Exception as e:
        log.warning("failed to append embeddings report: %s", e)
    if not dry_run:
        print("reminder: run `make graph-rebuild` so DuckDB picks up the new semantic_peer edges")
    return 0


# --------------------------------------------------------------------------- #
# E4: Co-mention inference scorer + pending-sidecar append (Relations 2.0 §6.3)#
# --------------------------------------------------------------------------- #
# Score(s,t) = co_mention_weight(s,t) * idf_boost * sector_match_boost *
#              (1 - existing_edge_penalty)
# Simplified per task: co_mention_weight = edge weight (count), idf_boost = 1.0,
# sector_match_boost = 1.5 if same sector_classification else 1.0,
# existing_edge_penalty = 1 if any typed edge already exists between s,t else 0.
# Emit top-N per company above threshold into findata/_pending_relations.txt
# (sidecar) with origin coinfer; idempotent; NO writes to graph_edges.

COINFER_ORIGIN = "coinfer"
COINFER_METHOD = "co_mention"
# Reuse extract_relations SIDECAR_PATH for format-compat; fallback if import fails.
try:
    from helpers.graph.extract_relations import SIDECAR_PATH as _COINFER_SIDECAR  # noqa: F401
    COINFER_SIDECAR_PATH = _COINFER_SIDECAR
except Exception:  # pragma: no cover
    COINFER_SIDECAR_PATH = PROJECT_ROOT / "findata" / "_pending_relations.txt"


def coinfer_sector_map(conn: sqlite3.Connection) -> dict[str, str | None]:
    """Company name -> sector_classification (or None)."""
    rows = conn.execute(
        "SELECT name, sector_classification FROM entities "
        "WHERE entity_type = 'company'"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def coinfer_existing_pairs(conn: sqlite3.Connection) -> set[frozenset[str]]:
    """All unordered pairs already linked by ANY edge_type (for penalty)."""
    rows = conn.execute("SELECT source, target FROM graph_edges").fetchall()
    return {frozenset((r[0], r[1])) for r in rows}


def coinfer_prior_pairs(path: Path = COINFER_SIDECAR_PATH) -> set[frozenset[str]]:
    """Pairs already suggested with origin=coinfer in the sidecar."""
    pairs: set[frozenset[str]] = set()
    if not path.exists():
        return pairs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("origin") == COINFER_ORIGIN:
            pairs.add(frozenset((row.get("source", ""), row.get("target_mention", ""))))
    return pairs


def score_coinfer(
    weight: float,
    same_sector: bool,
    has_existing_edge: bool,
) -> float:
    """Deterministic §6.3 simplified scorer."""
    if has_existing_edge:
        return 0.0
    sector_boost = 1.5 if same_sector else 1.0
    # idf_boost = 1.0 by simplification
    return round(float(weight) * sector_boost, 4)


def build_coinfer_suggestions(  # noqa
    conn: sqlite3.Connection,
    *,
    per_company: int = 3,
    threshold: float = 0.0,
) -> list[dict]:
    """Rank co_mentioned_in pairs via score_coinfer and take top-N per company.

    Returns sorted list of dicts with keys source, target, score, weight,
    same_sector, existing (bool). Candidates with existing edges get score 0
    and are dropped. Only pairs with score >= threshold (strictly >0 when
    threshold==0) survive. Per-company cap is enforced before sym-dedup; the
    final list is then deduplicated on unordered pair and sorted by
    (-score, source, target) deterministically.
    """
    sector_map = coinfer_sector_map(conn)
    existing = coinfer_existing_pairs(conn)
    # For penalty we must not penalize the co_mentioned_in edge itself:
    # existing includes co_mentioned, but that is the signal itself.
    # So build a filtered existing that excludes co_mentioned_in edges.
    # However task simplification says penalty 0 if edge exists else 1 — unclear.
    # We interpret as ANY non-co_mentioned business edge suppresses the
    # suggestion; co_mentioned alone should not suppress.
    try:
        non_comention_existing = {
            frozenset((r[0], r[1]))
            for r in conn.execute(
                "SELECT source, target FROM graph_edges "
                "WHERE edge_type != 'co_mentioned_in'"
            ).fetchall()
        }
    except Exception:
        non_comention_existing = existing

    rows = conn.execute(
        "SELECT source, target, weight FROM graph_edges "
        "WHERE edge_type = 'co_mentioned_in'"
    ).fetchall()
    # Aggregate weight per unordered pair (in case of split rows; normally 1 row)
    pair_weight: dict[tuple[str, str], float] = {}
    for s, t, w in rows:
        a, b = _pair(s, t)
        pair_weight[(a, b)] = pair_weight.get((a, b), 0.0) + float(w or 1.0)

    # Score each pair
    scored: list[tuple[str, str, float]] = []
    for (a, b), w in pair_weight.items():
        # has_existing means a business edge already links them (not just co_mention)
        has_existing = frozenset((a, b)) in non_comention_existing
        same = (
            sector_map.get(a) is not None
            and sector_map.get(a) == sector_map.get(b)
        )
        sc = score_coinfer(w, same, has_existing)
        if sc <= 0:
            continue
        if sc < threshold:
            continue
        scored.append((a, b, sc))

    if not scored:
        return []

    # Per-company top-N: build adjacency with scores
    from collections import defaultdict as _dd
    adj: dict[str, list[tuple[str, float]]] = _dd(list)
    for a, b, sc in scored:
        adj[a].append((b, sc))
        adj[b].append((a, sc))

    # For each company take top per_company neighbours deterministically
    # sorted by (-score, neighbour name)
    keep_pairs: set[tuple[str, str]] = set()
    for company in sorted(adj):
        neigh = sorted(adj[company], key=lambda x: (-x[1], x[0]))
        for other, sc in neigh[:per_company]:
            keep_pairs.add(_pair(company, other))

    # Filter scored to kept pairs and sort globally deterministically
    out: list[dict] = []
    for a, b, sc in scored:
        if _pair(a, b) not in keep_pairs:
            continue
        out.append({
            "source": a,
            "target": b,
            "score": sc,
            "weight": pair_weight[(a, b)],
            "same_sector": sector_map.get(a) is not None and sector_map.get(a) == sector_map.get(b),
        })
    out.sort(key=lambda d: (-d["score"], d["source"], d["target"]))
    return out


def _coinfer_edition() -> str:
    return f"coinfer/{datetime.now(UTC).date().isoformat()}"


def coinfer_to_row(suggestion: dict, *, edition: str | None = None) -> dict:
    """One coinfer suggestion -> sidecar JSONL row (Unresolved-compatible)."""
    if edition is None:
        edition = _coinfer_edition()
    return {
        "edge_type": "suggested",
        "source": suggestion["source"],
        "target_mention": suggestion["target"],
        "quote": "",
        "edition": edition,
        "origin": COINFER_ORIGIN,
        "score": round(float(suggestion["score"]), 4),
        "method": COINFER_METHOD,
    }


def coinfer_business_existing_pairs(conn: sqlite3.Connection) -> set[frozenset[str]]:
    """All unordered pairs linked by a business edge (excludes co_mentioned_in).

    The co_mentioned signal is the source of scores, not a penalty; only
    non-co_mentioned edges suppress suggestions.
    """
    rows = conn.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type != 'co_mentioned_in'"
    ).fetchall()
    return {frozenset((r[0], r[1])) for r in rows}


def append_coinfer_suggestions(  # noqa
    suggestions: list[dict],
    path: Path = COINFER_SIDECAR_PATH,
    *,
    existing_pairs: set[frozenset[str]] | None = None,
    prior_pairs: set[frozenset[str]] | None = None,
    edition: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Append coinfer suggestions to sidecar, deduplicated. Returns n written.

    Dedupes against existing graph edges and prior coinfer sidecar rows.
    If conn is given, existing graph pairs are refreshed from it.
    """
    if not suggestions:
        return 0
    if existing_pairs is None:
        if conn is not None:
            existing_pairs = coinfer_business_existing_pairs(conn)
        else:
            existing_pairs = set()
    if prior_pairs is None:
        prior_pairs = coinfer_prior_pairs(path)
    if edition is None:
        edition = _coinfer_edition()
    fresh: list[dict] = []
    seen: set[frozenset[str]] = set()
    for s in suggestions:
        key = frozenset((s["source"], s["target"]))
        if key in seen:
            continue
        seen.add(key)
        if key in existing_pairs or key in prior_pairs:
            continue
        fresh.append(s)
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for s in fresh:
            f.write(json.dumps(coinfer_to_row(s, edition=edition), ensure_ascii=False) + "\n")
    return len(fresh)


def render_coinfer(suggestions: list[dict]) -> str:
    """Human-readable dry-run table for coinfer suggestions."""
    if not suggestions:
        return "no coinfer suggestions passed the filters"
    lines = ["score  weight  sector  pair", "-" * 70]
    for s in suggestions:
        flag = "same" if s["same_sector"] else "diff"
        lines.append(f"{s['score']:<6.2f} {s['weight']:<6.1f}  {flag:<5}  {s['source']} <-> {s['target']}")
    lines.append(f"({len(suggestions)} suggestions · sidecar: --apply to append)")
    return "\n".join(lines)


def run_coinfer_pass(
    conn: sqlite3.Connection,
    *,
    per_company: int = 3,
    threshold: float = 0.0,
    dry_run: bool = True,
    sidecar_path: Path | None = None,
) -> int:
    """E4 driver: score co-mentions -> dry-run table or sidecar append.

    No writes to graph_edges; only appends to findata/_pending_relations.txt
    when --apply is given. Deterministic and idempotent (re-apply does not
    duplicate sidecar rows).
    """
    path = COINFER_SIDECAR_PATH if sidecar_path is None else sidecar_path
    suggestions = build_coinfer_suggestions(
        conn, per_company=per_company, threshold=threshold
    )
    mode = "dry-run" if dry_run else "apply"
    log.info("%s: %d coinfer candidates (per_company=%d, threshold=%.3f)",
             mode, len(suggestions), per_company, threshold)
    if dry_run:
        print(render_coinfer(suggestions))
        # Also surface a small summary of idempotency state
        if suggestions:
            prior = coinfer_prior_pairs(path)
            existing = coinfer_business_existing_pairs(conn)
            fresh = [s for s in suggestions if frozenset((s["source"], s["target"])) not in prior and frozenset((s["source"], s["target"])) not in existing]
            print(f"fresh (not in graph/sidecar): {len(fresh)} of {len(suggestions)}")
        return 0
    # Apply: append to sidecar (idempotent)
    n = append_coinfer_suggestions(suggestions, path=path, conn=conn)
    log.info("coinfer: appended %d suggestions to %s (of %d candidates; rest deduped)", n, path, len(suggestions))
    print(f"appended {n} coinfer suggestions to {path}" + ("" if n == len(suggestions) else f" (of {len(suggestions)}; rest deduped)"))
    return 0


# --------------------------------------------------------------------------- #
# E5: invested_in holders pass (Relations 2.0 §4.1, §5)                      #
# --------------------------------------------------------------------------- #
# yfinance institutional_holders: US-listed tickers only (AAPL ✓, RELIANCE.NS
# empty). Expected yield ≤140 rows (~14 companies). For each tickered company,
# fetch Ticker.institutional_holders DataFrame (Holder, Shares, Date Reported,
# % Out, Value). Create institution entities (entity_type='institution',
# file_path=NULL, dedup by normalized_name) and invested_in edges
# (institution -> company, weight 3.0 if pctHeld >=5% else 1.0),
# source_ref='yfinance:holders:<date>', properties {pctHeld, shares, value,
# fetched_at}, valid_from=dateReported (temporal convention, proposal §5).

HOLDERS_SOURCE_REF_PREFIX = "yfinance:holders"


def _holder_normalized(name: str) -> str:
    """normalized_name for an institution (dedup key)."""
    try:
        from helpers.core.parse_newsletter import normalize_name as _norm  # noqa: WPS433
        return _norm(name)
    except Exception:
        # fallback: same contract as normalize_name (PascalCase underscored)
        import re as _re
        n = _re.sub(r"[&\(\)\-]", " ", name)
        n = _re.sub(r"[^A-Za-z0-9 _]", "", n)
        parts = [p for p in n.split() if p]
        return "_".join(parts)


def _parse_pct_holder(val) -> float | None:
    """Parse % Out into fraction 0-1. Handles 0.0797, '7.97%', '7.97'."""
    if val is None:
        return None
    try:
        # pandas NA
        import math as _math
        if isinstance(val, float) and _math.isnan(val):
            return None
    except Exception:  # noqa: S110
        pass
    s = str(val).strip().replace("%", "").replace(",", "")
    if not s or s.lower() == "nan":
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    # If value looks like percent (e.g. 7.97) rather than fraction (0.0797),
    # heuristic: values >1 are percents.
    if f > 1.0:
        f = f / 100.0
    return f


def _parse_holder_date(val) -> str | None:
    """Normalize Date Reported to YYYY-MM-DD or None."""
    if val is None:
        return None
    try:
        import math as _math
        if isinstance(val, float) and _math.isnan(val):
            return None
    except Exception:  # noqa: S110
        pass
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    # Try pandas Timestamp
    try:
        import pandas as _pd
        if isinstance(val, _pd.Timestamp):
            return val.date().isoformat()
    except Exception:  # noqa: S110
        pass
    # Try ISO-ish
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # Last resort: first 10 chars if ISO
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _parse_holders_dataframe(df) -> list[dict]:  # noqa
    """DataFrame -> list of holder dicts {holder, shares, pct, value, date}."""
    if df is None:
        return []
    try:
        # pandas DataFrame
        import pandas as _pd  # noqa: F401
        if hasattr(df, "empty") and df.empty:
            return []
        if hasattr(df, "columns"):
            cols = {str(c).lower().strip(): c for c in df.columns}
            # Map flexible column names
            holder_col = cols.get("holder") or cols.get("holder ") or next((v for k, v in cols.items() if "holder" in k), None)
            shares_col = next((v for k, v in cols.items() if "share" in k), None)
            date_col = next((v for k, v in cols.items() if "date" in k), None)
            pct_col = next((v for k, v in cols.items() if "%" in k or "pct" in k or "out" in k), None)
            value_col = next((v for k, v in cols.items() if "value" in k), None)
            rows: list[dict] = []
            # Iterate rows
            try:
                for _, row in df.iterrows():
                    holder = str(row[holder_col]).strip() if holder_col is not None and holder_col in row else ""
                    if not holder or holder.lower() == "nan":
                        continue
                    shares = row[shares_col] if shares_col is not None and shares_col in row else None
                    pct_raw = row[pct_col] if pct_col is not None and pct_col in row else None
                    value = row[value_col] if value_col is not None and value_col in row else None
                    date_raw = row[date_col] if date_col is not None and date_col in row else None
                    # Normalize shares/value to int/float where possible
                    try:
                        shares_n = int(float(str(shares).replace(",", ""))) if shares not in (None, "") and str(shares).lower() != "nan" else None
                    except Exception:
                        shares_n = None
                    try:
                        value_n = float(str(value).replace(",", "").replace("$", "")) if value not in (None, "") and str(value).lower() != "nan" else None
                    except Exception:
                        value_n = None
                    pct = _parse_pct_holder(pct_raw)
                    date_s = _parse_holder_date(date_raw)
                    rows.append({"holder": holder, "shares": shares_n, "pct": pct, "value": value_n, "date": date_s})
                return rows
            except Exception:
                return []
        # list-like fallback
        if isinstance(df, list):
            return df
    except Exception:
        return []
    return []


def fetch_holders_for_one(ticker: str, fetch_fn=None) -> list[dict]:
    """Fetch institutional holders for a single ticker (test-injectable)."""
    if fetch_fn is not None:
        try:
            df = fetch_fn(ticker)
        except Exception:
            return []
        # fetch_fn may return list[dict] directly (test fixtures)
        if isinstance(df, list):
            return df
        return _parse_holders_dataframe(df)
    try:
        import yfinance as yf  # noqa: WPS433
        t = yf.Ticker(ticker)
        df = getattr(t, "institutional_holders", None)
        # yfinance 1.6.0: institutional_holders is a property returning DataFrame
        if callable(df):
            df = df()
        return _parse_holders_dataframe(df)
    except Exception:
        return []


def fetch_holders_for_all(
    companies: list[tuple[str, str, str | None]],
    *,
    fetch_fn=None,
    workers: int = 2,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Fetch holders for every tickered company.

    Returns (holders_by_company, companies_with_holders) where holders_by_company
    maps company name -> list of holder dicts (empty list if none).
    """
    holders_by_company: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for name, ticker, _fp in companies:
            if fetch_fn is not None:
                fut = pool.submit(fetch_holders_for_one, ticker, fetch_fn)
            else:
                fut = pool.submit(fetch_holders_for_one, ticker)
            futures[fut] = name
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                rows = fut.result()
            except Exception:
                rows = []
            holders_by_company[name] = rows or []
    return holders_by_company, [n for n, rows in holders_by_company.items() if rows]


def build_holders_edges(  # noqa
    holders_by_company: dict[str, list[dict]],
    *,
    source_ref: str | None = None,
) -> tuple[list[tuple[str, str, float, str, dict, str | None]], dict[str, str]]:
    """Build invested_in candidate edges + institution dedup map.

    Returns (edges, institution_normalized_map) where edges are
    (institution_name, company_name, weight, source_ref, properties, valid_from)
    and institution_normalized_map is holder_name -> normalized_name (for entity insert).
    """
    today = datetime.now(UTC).date().isoformat()
    ref = source_ref or f"{HOLDERS_SOURCE_REF_PREFIX}:{today}"
    edges: list[tuple[str, str, float, str, dict, str | None]] = []
    inst_norm: dict[str, str] = {}
    for company, holders in holders_by_company.items():
        for h in holders:
            holder = (h.get("holder") or "").strip()
            if not holder:
                continue
            pct = h.get("pct")
            try:
                pct_f = float(pct) if pct is not None else None
            except Exception:
                pct_f = None
            weight = 3.0 if (pct_f is not None and pct_f >= 0.05) else 1.0
            props: dict = {"fetched_at": today}
            if pct_f is not None:
                props["pctHeld"] = round(float(pct_f), 6)
            if h.get("shares") is not None:
                try:
                    props["shares"] = int(h["shares"])
                except Exception:
                    props["shares"] = h["shares"]
            if h.get("value") is not None:
                try:
                    props["value"] = float(h["value"])
                except Exception:
                    props["value"] = h["value"]
            valid_from = h.get("date")
            edges.append((holder, company, weight, ref, props, valid_from))
            if holder not in inst_norm:
                inst_norm[holder] = _holder_normalized(holder)
    # Deterministic sort
    edges.sort(key=lambda e: (e[0], e[1]))
    return edges, inst_norm


def apply_holders_edges(
    conn: sqlite3.Connection,
    edges: list[tuple[str, str, float, str, dict, str | None]],
    inst_norm: dict[str, str],
    *,
    dry_run: bool = True,
) -> tuple[int, int, int]:
    """Insert institution entities (INSERT OR IGNORE) + invested_in edges.

    Returns (n_institutions_would_or_did, n_fresh_edges, n_inserted_edges).
    Dry-run counts fresh edges not already present and new institutions not yet present.
    Apply does DELETE-by-prefix + INSERT OR IGNORE for edges and INSERT OR IGNORE for institutions.
    """
    # Count fresh vs existing for dry-run parity
    existing_edges: set[tuple[str, str]] = set()
    existing_insts: set[str] = set()
    try:
        existing_edges = {(r[0], r[1]) for r in conn.execute("SELECT source, target FROM graph_edges WHERE edge_type='invested_in'").fetchall()}
    except sqlite3.OperationalError:
        existing_edges = set()
    try:
        existing_insts = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    except sqlite3.OperationalError:
        existing_insts = set()

    n_fresh = sum(1 for s, t, *_ in edges if (s, t) not in existing_edges)
    n_new_insts = sum(1 for h in inst_norm if h not in existing_insts)

    if dry_run:
        return n_new_insts, n_fresh, n_fresh

    # Apply: insert institutions first (FK target must exist)
    with conn:
        for holder, norm in inst_norm.items():
            conn.execute(
                "INSERT OR IGNORE INTO entities (name, entity_type, normalized_name, file_path, sector_classification, ticker) VALUES (?, 'institution', ?, NULL, NULL, NULL)",
                (holder, norm),
            )
        # Idempotent prefix delete
        conn.execute(
            "DELETE FROM graph_edges WHERE edge_type='invested_in' AND source_ref LIKE ?",
            (f"{HOLDERS_SOURCE_REF_PREFIX}:%",),
        )
        inserted = 0
        for src, dst, weight, source_ref, props, valid_from in edges:
            cur = conn.execute(
                "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, weight, properties, source_ref, valid_from, symmetric) VALUES (?, ?, 'invested_in', ?, ?, ?, ?, 0)",
                (src, dst, weight, json.dumps(props, sort_keys=True), source_ref, valid_from),
            )
            inserted += cur.rowcount
    return n_new_insts, n_fresh, inserted


def run_holders_pass(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    workers: int = 2,
    fetch_fn=None,
) -> int:
    """E5 driver: fetch institutional holders -> institutions + invested_in edges."""
    companies, unlisted = load_companies(conn)
    log.info("holders pass: %d tickered, %d unlisted skipped", len(companies), len(unlisted))
    holders_by_company, with_holders = fetch_holders_for_all(companies, fetch_fn=fetch_fn, workers=workers)
    log.info("holders pass: %d companies with holder data (of %d fetched)", len(with_holders), len(companies))
    edges, inst_norm = build_holders_edges(holders_by_company)
    n_new_insts, n_fresh, n_inserted = apply_holders_edges(conn, edges, inst_norm, dry_run=dry_run)
    mode = "dry-run" if dry_run else "apply"
    log.info("%s: %d invested_in candidates, %d would insert (%d new institutions)", mode, len(edges), n_fresh, n_new_insts)
    if not dry_run:
        log.info("apply: inserted %d invested_in edges, %d institutions", n_inserted, n_new_insts)
    # Report appendix
    try:
        today = datetime.now(UTC).date().isoformat()
        lines = [
            "",
            f"[invested_in]  # E5 {HOLDERS_SOURCE_REF_PREFIX} (yfinance institutional holders)",
            f"  generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
            f"  mode: {mode}",
            f"  tickered companies: {len(companies)}",
            f"  companies with holders: {len(with_holders)}",
            f"  candidates: {len(edges)}",
            f"  {'would_insert' if dry_run else 'inserted'}: {n_inserted if not dry_run else n_fresh}",
            f"  institutions: {len(inst_norm)} unique ({n_new_insts} new)",
            f"  source_ref prefix: {HOLDERS_SOURCE_REF_PREFIX}:{today}",
        ]
        if edges:
            # Sample 5 highest pct
            sample = sorted(edges, key=lambda e: -e[4].get("pctHeld", 0))[:5]
            lines.append("  sample (holder -> company | pct | weight | date):")
            for src, dst, w, _ref, props, vf in sample:
                pct = props.get("pctHeld", "-")
                lines.append(f"    {src} -> {dst} | {pct} | {w} | {vf or '-'}")
        with open(REPORT_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        log.info("report appended to %s", REPORT_PATH)
    except Exception as e:
        log.warning("failed to append holders report: %s", e)
    if not dry_run:
        print("reminder: run `make graph-rebuild` so DuckDB picks up the new invested_in edges")
    # Human dry-run summary
    if dry_run:
        print(f"[holders dry-run] {len(edges)} candidates, {n_fresh} fresh, {len(inst_norm)} institutions ({n_new_insts} new)")
        if with_holders:
            print(f"companies with holders: {', '.join(sorted(with_holders)[:10])}" + (f" (+{len(with_holders)-10} more)" if len(with_holders) > 10 else ""))
        else:
            print("no holders found (expected for .NS-only universe; US tickers only)")
    return 0

# --------------------------------------------------------------------------- #
# The Google-Finance fallback pass (F2: tier 1 + curated read)                #
# --------------------------------------------------------------------------- #
@dataclass
class GfOutcome:
    """Resolution result for one target; becomes one report row."""

    entity: str
    ticker: str | None              # None for deliberately-unlisted targets
    outcome: str                    # curated (...)/curated-broken/resolved
                                    # gf-only/resolved yahoo-candidate/
                                    # unverified/still-dead/no-candidates
    slug: str | None = None
    score: float | None = None
    parsed_name: str | None = None  # the page's own About-name
    stats: dict = field(default_factory=dict)
    source_tier: int = 0            # 1/2/3 for report annotation [tN]


def _stats_with_price(parsed: dict) -> dict:
    """Stats dict + the header price (fallback-path metric, S3)."""
    stats = dict(parsed["stats"])
    if parsed.get("price") is not None:
        stats.setdefault("price", parsed["price"])
    return stats


def _verified_outcome(
    entity: str, ticker: str | None, slug: str,
    parsed: dict, score: float, tier: int,
) -> GfOutcome:
    """Classify a verified (>= threshold) quote-page hit.

    gf-only: the slug's stem is the failing Yahoo symbol's own stem —
    Yahoo already knows that symbol and failed on it, so GF is the only
    source (F4 metrics-only path). yahoo-candidate: a different stem
    (tier-2 discovery, or curation) maps back to a working Yahoo-format
    symbol — the G4 writeback candidate.
    """
    stem = slug.rsplit(":", 1)[0]
    yahoo = yahoo_symbol_for_slug(slug)
    different = yahoo and (ticker is None or stem != ticker.rsplit(".", 1)[0])
    outcome = ("resolved yahoo-candidate" if different else "resolved gf-only")
    return GfOutcome(entity, ticker, outcome, slug, score,
                     parsed["company_name"], _stats_with_price(parsed),
                     source_tier=tier)


def load_gf_targets(
    report_path: Path, *, include_unlisted: bool,
) -> list[tuple[str, str | None]]:
    """GF pass targets (§4.4): [ticker_issues] rows of the last yfinance
    report as (name, ticker); with --include-unlisted also the [universe]
    ``(unlisted)`` rows as (name, None)."""
    issues: list[tuple[str, str]] = []
    unlisted: list[str] = []
    section = None
    for line in report_path.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^\[(\w+)\]", line)
        if header:
            section = header.group(1)
            continue
        if section == "ticker_issues":
            m = re.match(r"^    (.+?) \| (\S+)$", line)
            if m and not line.lstrip().startswith("#"):
                issues.append((m.group(1).strip(), m.group(2)))
        elif section == "universe" and include_unlisted:
            m = re.match(r"^  \(unlisted\) (.+)$", line)
            if m:
                unlisted.append(m.group(1).strip())
    return [*issues, *[(name, None) for name in unlisted]]


def load_curated(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """entity_name -> (gf_slug, kind) from entity_gf_map (§4.1 tier 3).

    The table is consulted FIRST and trusted (human-seeded); a row whose
    page has since rotted is reported as curated-broken, never silently
    re-resolved — curation stays an explicit human act.
    """
    rows = conn.execute(
        "SELECT entity_name, gf_slug, kind FROM entity_gf_map").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def load_terminal_statuses(
    conn: sqlite3.Connection,
) -> dict[str, tuple[str, str | None]]:
    """entity_name -> (status, successor) from entity_ticker_status.

    Caller creates the table first (both passes run the DDL alongside
    ENTITY_GF_MAP_DDL); an empty mapping is the normal state.
    """
    rows = conn.execute(
        "SELECT entity_name, status, successor FROM entity_ticker_status"
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def append_terminal_report_section(
    path: Path,
    statuses: dict[str, tuple[str, str | None]],
) -> None:
    """Append the [terminal] section: classified dead ends (--classify).

    These entities never enter resolution sweeps again; they are the
    proposal §7 'remainder explicitly classified' half of the success
    criterion, so they stay visible here rather than as forever-`still-
    dead` rows.
    """
    lines = [
        "",
        "[terminal]  # classified dead ends (--classify); excluded from "
        "all resolution sweeps",
        f"  generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"  classified: {len(statuses)}",
        "  # entity | status | successor",
    ]
    for name in sorted(statuses):
        status, successor = statuses[name]
        lines.append(f"    {name} | {status} | {successor or '-'}")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def _fetch_parse(slug: str, cache_dir: Path, fetch_fn, delay: float) -> dict | None:
    """Fetch one slug page (through the cache) and parse it."""
    html, from_cache = fetch_fn(slug, cache_dir)
    if not from_cache and delay > 0:
        time.sleep(delay)  # politeness floor between real fetches (§6)
    return parse_quote(html)


def _scan_slugs(
    entity: str,
    ticker: str | None,
    slugs: list[str],
    *,
    cache_dir: Path,
    fetch_fn,
    delay: float,
    tier: int,
    tried: set[str] | None = None,
) -> tuple[GfOutcome | None, GfOutcome | None]:
    """Fetch+verify slugs in order. Returns (verified_hit, best_unverified).

    Slugs already in ``tried`` are skipped (and new ones recorded).
    verified_hit short-circuits the caller; best_unverified is the
    highest-scoring below-threshold live page, kept for curation.
    """
    best: GfOutcome | None = None
    for slug in slugs:
        if tried is not None:
            if slug in tried:
                continue
            tried.add(slug)
        parsed = _fetch_parse(slug, cache_dir, fetch_fn, delay)
        if parsed is None:
            continue
        score = name_match_score(parsed["company_name"], entity)
        if score >= GF_NAME_MATCH_THRESHOLD:
            return (_verified_outcome(entity, ticker, slug, parsed, score,
                                      tier=tier), best)
        candidate = GfOutcome(entity, ticker, "unverified", slug, score,
                              parsed["company_name"],
                              _stats_with_price(parsed), source_tier=tier)
        if best is None or (best.score or 0) < score:
            best = candidate
    return None, best


def _resolve_curated(
    entity: str,
    ticker: str | None,
    curated: tuple[str, str],
    *,
    cache_dir: Path,
    fetch_fn,
    delay: float,
) -> GfOutcome:
    """Tier 3: the trusted, human-seeded override (§4.1). Consulted FIRST.

    A row whose page has since rotted is reported as curated-broken,
    never silently re-resolved — curation stays an explicit human act.
    """
    slug, kind = curated
    parsed = _fetch_parse(slug, cache_dir, fetch_fn, delay)
    if parsed is None:
        return GfOutcome(entity, ticker, "curated-broken", slug,
                         source_tier=3)
    return GfOutcome(
        entity, ticker, f"curated ({kind})", slug,
        score=name_match_score(parsed["company_name"], entity),
        parsed_name=parsed["company_name"],
        stats=_stats_with_price(parsed), source_tier=3,
    )


def _resolve_tier1(
    entity: str,
    ticker: str,
    *,
    cache_dir: Path,
    fetch_fn,
    delay: float,
) -> GfOutcome:
    """Tier 1: stem-preserving slug variants of the failing Yahoo symbol.

    First verified candidate wins (native exchange ordered first); the
    best below-threshold page is kept as ``unverified`` for human
    curation, never applied. Stem-preserving by construction, so hits
    classify gf-only (the classifier's yahoo-candidate branch is
    tier-2/curation territory).
    """
    hit, best = _scan_slugs(entity, ticker, slug_candidates(ticker),
                            cache_dir=cache_dir, fetch_fn=fetch_fn,
                            delay=delay, tier=1)
    return hit if hit is not None else (
        best if best is not None else GfOutcome(entity, ticker, "still-dead"))


def _resolve_tier2(
    entity: str,
    ticker: str | None,
    *,
    tried: set[str],
    cache_dir: Path,
    fetch_fn,
    delay: float,
    search_fn,
) -> GfOutcome | None:
    """Tier 2 (F3): BSE name search -> GF slug candidates (§4.1).

    Returns None when the search yields no rows (or fails — caller
    reports no-candidates); otherwise the best outcome across the
    discovered slugs (scrip:BOM then symbol:NSE, top matches only).
    """
    try:
        matches, from_cache = search_fn(entity, cache_dir)
    except Exception:  # network/BSE hiccup: tier 2 unavailable, not fatal
        log.warning("tier-2 search failed for %s — skipping",
                    entity, exc_info=True)
        return None
    if not from_cache and delay > 0:
        time.sleep(delay)
    if not matches:
        return None
    best: GfOutcome | None = None
    for m in matches[:GF_T2_MATCH_LIMIT]:
        slugs = [f"{m.scrip}:BOM"]
        if m.symbol:
            slugs.append(f"{m.symbol}:NSE")
        hit, candidate = _scan_slugs(
            entity, ticker, slugs, cache_dir=cache_dir, fetch_fn=fetch_fn,
            delay=delay, tier=2, tried=tried)
        if hit is not None:
            return hit
        if (candidate is not None
                and (best is None or (best.score or 0) < (candidate.score or 0))):
            best = candidate
    return best if best is not None else GfOutcome(
        entity, ticker, "still-dead", source_tier=2)


def resolve_gf_target(
    entity: str,
    ticker: str | None,
    *,
    curated: tuple[str, str] | None,
    cache_dir: Path,
    fetch_fn=load_or_fetch,
    delay: float = GF_MIN_DELAY_S,
    tier2: bool = False,
    search_fn=bse_search_cached,
) -> GfOutcome:
    """Resolve one failed/unlisted entity through the GF tiers (§4.1).

    Tier 3 (curated override) first, then tier-1 slug variants, then —
    only with ``tier2=True`` (F3) — BSE name-search candidates. Every
    auto-discovered hit must pass the fuzzy About-name verification
    (>= GF_NAME_MATCH_THRESHOLD); the best below-threshold page is kept
    as ``unverified`` for human curation, never applied.
    """
    if curated is not None:
        return _resolve_curated(entity, ticker, curated,
                                cache_dir=cache_dir, fetch_fn=fetch_fn,
                                delay=delay)

    tried: set[str] = set()
    fallback: GfOutcome | None = None  # best below-threshold evidence
    if ticker is not None and slug_candidates(ticker):
        tried.update(slug_candidates(ticker))
        out = _resolve_tier1(entity, ticker, cache_dir=cache_dir,
                             fetch_fn=fetch_fn, delay=delay)
        if out.outcome.startswith("resolved"):
            return out
        if out.outcome == "unverified":
            fallback = out

    if tier2:
        out2 = _resolve_tier2(entity, ticker, tried=tried,
                              cache_dir=cache_dir, fetch_fn=fetch_fn,
                              delay=delay, search_fn=search_fn)
        if out2 is not None:
            # A tier-2 hit must not mask better tier-1 evidence:
            # still-dead loses to any unverified page (even score 0 —
            # a live wrong-name page is curatable, a shell is not);
            # unverified-vs-unverified keeps the higher-scoring page.
            if (fallback is not None
                    and (out2.outcome == "still-dead"
                         or (out2.outcome == "unverified"
                             and (fallback.score or 0) > (out2.score or 0)))):
                return fallback
            return out2

    if fallback is not None:
        return fallback
    if ticker is None or not slug_candidates(ticker):
        # Bare/foreign ticker or unlisted entity with nothing found.
        return GfOutcome(entity, ticker, "no-candidates")
    return GfOutcome(entity, ticker, "still-dead")


def _fmt_gf_num(value: float | None) -> str:
    if value is None:
        return "-"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= div:
            return f"{value / div:.2f}{suffix}"
    return f"{value:.2f}"


def append_gf_report_section(
    path: Path,
    outcomes: list[GfOutcome],
    *,
    include_unlisted: bool,
    tier2: bool = False,
    apply_info: str = "",
) -> None:
    """Append the [google_finance] section to the existing report.

    Append-only: the yfinance pass regenerates the file wholesale, so a
    fresh GF section simply follows the latest yfinance run (§4.4 ordering).
    """
    def count(prefix: str) -> int:
        return sum(1 for o in outcomes if o.outcome.startswith(prefix))

    resolved = [o for o in outcomes if o.outcome.startswith("resolved")]
    yahoo_candidates = sum(1 for o in resolved
                           if o.outcome == "resolved yahoo-candidate")
    lines = [
        "",
        "[google_finance]  # fallback resolution pass (curated + tier 1"
        + (" + tier 2" if tier2 else "") + ", dry-run)",
        f"  generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"  unlisted opt-in: {'yes' if include_unlisted else 'no'}"
        f" · tier2 (BSE name search): {'on' if tier2 else 'off'}",
        (f"  outcomes: {count('curated (')} curated ({count('curated-broken')} broken)"
         f" · {len(resolved)} resolved ({yahoo_candidates} yahoo-candidates,"
         f" {len(resolved) - yahoo_candidates} gf-only)"
         f" · {count('unverified')} unverified · {count('still-dead')} still-dead"
         f" · {count('no-candidates')} no-candidates"),
        "  # entity | outcome | gf_slug | name_match | sample (px / mkt_cap / P/E)",
    ]
    if apply_info:
        lines.append(f"  {apply_info}")
    for o in outcomes:
        score = f"{o.score:.2f}" if o.score is not None else "-"
        outcome = o.outcome
        if outcome == "resolved yahoo-candidate" and o.slug:
            outcome += f" ({yahoo_symbol_for_slug(o.slug)})"
        if o.source_tier:
            outcome += f" [t{o.source_tier}]"
        if outcome == "unverified":
            sample = f"page says: {o.parsed_name}"
        else:
            s = o.stats
            px = s.get("price", s.get("prev_close"))
            sample = " / ".join(_fmt_gf_num(x) for x in
                                (px, s.get("mkt_cap"), s.get("pe_ratio")))
        slug = o.slug or "-"
        lines.append(f"    {o.entity} | {outcome} | {slug} | {score} | {sample}")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def _persist_gf_resolutions(
    conn: sqlite3.Connection,
    outcomes: list[GfOutcome],
    *,
    metrics_fn=None,
) -> tuple[int, int]:
    """S3 --apply: persist entity_gf_map rows + gf_only metrics.

    Every resolved outcome (tier 1/2/curated) persists its slug —
    resolution happens ONCE. gf_only entities additionally get
    company_metrics rows via one Sheets GOOGLEFINANCE batch
    (metrics_fn injectable; default = the real googlesheets_metrics
    client, imported lazily since gspread is venv-only). yahoo_mapped_
    back rows persist for stage-1 consumption but take no metrics (they
    must first prove themselves on yfinance, per §3).
    Returns (n_map_rows, n_metric_rows).
    """
    resolved = [o for o in outcomes if o.outcome.startswith("resolved")]
    gf_only = [o for o in resolved
               if o.outcome != "resolved yahoo-candidate"]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with conn:
        for o in resolved:
            kind = ("yahoo_mapped_back"
                    if o.outcome == "resolved yahoo-candidate" else "gf_only")
            conn.execute(
                "INSERT OR REPLACE INTO entity_gf_map "
                "(entity_name, gf_slug, kind, resolved_at, verified_name) "
                "VALUES (?, ?, ?, ?, ?)",
                (o.entity, o.slug or "", kind, now, o.parsed_name or ""))

    n_metrics = 0
    if not gf_only:
        return len(resolved), 0
    if metrics_fn is None:
        from helpers.maintenance.googlesheets_metrics import (  # noqa: E402  # lazy: gspread is venv-only
            fetch_gf_metrics,
        )
        metrics_fn = fetch_gf_metrics
    requests = [(o.slug or "", attr) for o in gf_only
                for attr in GF_ATTRIBUTES]
    values = metrics_fn(requests)
    with conn:
        for o in gf_only:
            conn.execute(
                "DELETE FROM company_metrics WHERE source_ref LIKE ?",
                (f"googlefinance:{o.slug}:%",))
            for attr in GF_ATTRIBUTES:
                v = values.get((o.slug or "", attr))
                if v is None:
                    continue
                label, unit, conv = GF_METRIC_SPEC[attr]
                v = _convert_metric(v, conv)
                conn.execute(
                    "INSERT INTO company_metrics "
                    "(entity, metric_label, value_raw, value_num, unit,"
                    " source_quote, source_ref, properties) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (o.entity, label, f"{v:g}", v, unit,
                     f'GOOGLEFINANCE("{o.slug}","{attr}")',
                     f"googlefinance:{o.slug}:{attr}",
                     json.dumps({"fetched_at": now, "slug": o.slug,
                                 "via": "sheets"}, sort_keys=True)))
                n_metrics += 1
    return len(resolved), n_metrics


def run_googlefinance_pass(
    conn: sqlite3.Connection,
    *,
    include_unlisted: bool = False,
    tier2: bool = False,
    apply_resolutions: bool = False,
    report_path: Path | None = None,
    cache_dir: Path | None = None,
    fetch_fn=load_or_fetch,
    search_fn=bse_search_cached,
    metrics_fn=None,
    delay: float = GF_MIN_DELAY_S,
) -> int:
    """GF fallback pass (F2/F3 + S3 apply): tiers -> report -> persist.

    Dry-run by default. ``--apply`` (S3) persists every resolved slug to
    entity_gf_map and writes company_metrics rows for gf_only entities
    via one Sheets GOOGLEFINANCE batch — resolution happens ONCE per
    company. Creating the empty entity_gf_map when missing is infra on
    the same precedent as the fetch cache being written during dry-run
    yfinance runs. Paths default to the module constants at call time
    (monkeypatch-friendly, same as REPORT_PATH in the pass above).
    """
    report_path = REPORT_PATH if report_path is None else report_path
    cache_dir = GF_PAGE_CACHE_DIR if cache_dir is None else cache_dir
    if not report_path.exists():
        log.error("no relations report at %s — run the yfinance pass first "
                  "(--source yfinance [--check-only]) to produce it",
                  report_path)
        return 2
    conn.execute(ENTITY_GF_MAP_DDL)
    conn.execute(ENTITY_TICKER_STATUS_DDL)
    curated = load_curated(conn)
    terminal = load_terminal_statuses(conn)
    current_ticker = dict(conn.execute(
        "SELECT name, ticker FROM entities WHERE entity_type = 'company'"
    ).fetchall())
    stale = [n for n, t in load_gf_targets(report_path,
                                           include_unlisted=include_unlisted)
             if t is not None and current_ticker.get(n) not in (None, t)]
    targets = [t for t in load_gf_targets(report_path,
                                          include_unlisted=include_unlisted)
               if t[0] not in terminal
               and current_ticker.get(t[0]) in (None, t[1])]
    if stale:
        log.info("skipping %d already-resolved targets (report predates "
                 "writebacks): %s", len(stale), ", ".join(sorted(stale)))
    n_issues = sum(1 for _n, t in targets if t is not None)
    log.info("GF pass: %d ticker_issues (+%d unlisted opt-in), %d curated "
             "overrides, tier2 %s, %s", n_issues, len(targets) - n_issues,
             len(curated), "on" if tier2 else "off",
             "APPLY" if apply_resolutions else "dry-run")

    outcomes = [
        resolve_gf_target(
            name, ticker, curated=curated.get(name),
            cache_dir=cache_dir, fetch_fn=fetch_fn, delay=delay,
            tier2=tier2, search_fn=search_fn,
        )
        for name, ticker in targets
    ]
    for o in outcomes:
        log.info("  %s: %s%s", o.entity, o.outcome,
                 f" [{o.slug}]" if o.slug else "")

    applied_info = ""
    if apply_resolutions:
        n_map, n_metrics = _persist_gf_resolutions(
            conn, outcomes, metrics_fn=metrics_fn)
        applied_info = (f"apply: {n_map} entity_gf_map rows · "
                        f"{n_metrics} company_metrics rows")
        log.info("%s", applied_info)

    append_gf_report_section(report_path, outcomes,
                             include_unlisted=include_unlisted, tier2=tier2,
                             apply_info=applied_info)
    if terminal:
        append_terminal_report_section(report_path, terminal)
    log.info("GF section appended to %s", report_path)
    return 0


# --------------------------------------------------------------------------- #
# The FinnHub stage-1 pass (market_data_resolution.md S1/S2):                 #
# name -> Yahoo ticker discovery, yfinance-verified writeback                 #
# --------------------------------------------------------------------------- #
@dataclass
class FhOutcome:
    """Stage-1 result for one target; becomes one report row."""

    entity: str
    old_ticker: str | None
    outcome: str    # writeback-candidate / unverified / still-dead /
                    # no-candidates
    new_ticker: str | None = None
    score: float | None = None
    sample: str = ""                # yfinance longName · industry
    info: dict = field(default_factory=dict)  # verified payload (cache ext)


def _candidate_allowed(old_ticker: str | None, candidate: str,
                       taken: dict[str, str] | None = None) -> bool:
    """Exchange-class + ownership guards (proposal §5 traps).

    FinnHub returns global matches — for an India-domiciled entity (or
    an unlisted one, corpus default India) only .NS/.BO candidates may
    write, so the near-identical Dutch parent (AKZA.AS) can never
    replace Akzo Nobel India. Bare-US entities accept bare symbols.

    ``taken`` (ticker -> owning entity, from entities) adds the sibling
    guard the fuzzy name check cannot provide: a candidate another
    entity ALREADY owns is never eligible — 'Kotak Mahindra Life
    Insurance' scored 0.71 against Kotak Mahindra BANK's payload and
    would otherwise take over KOTAKBANK.NS (measured live 2026-08-25).
    """
    if candidate == old_ticker:
        return False
    if taken is not None:
        owner = taken.get(candidate)
        if owner is not None:
            return False
    indian_entity = old_ticker is None or old_ticker.endswith((".NS", ".BO"))
    if indian_entity:
        return candidate.endswith((".NS", ".BO"))
    return "." not in candidate


def _verify_candidate(
    entity: str, candidate: str, verify_fn,
) -> tuple[bool, dict | None]:
    """Single-ticker yfinance fetch + fuzzy name check.

    An info payload for the WRONG company passes a bare fetch; the
    longName/shortName check (same matcher + threshold as the GF tiers)
    is what makes a candidate a writeback.
    """
    info = verify_fn(candidate)  # contract: dict or None, never raises
    if info is None:
        return False, None
    name = info.get("longName") or info.get("shortName") or ""
    return name_match_score(name, entity) >= GF_NAME_MATCH_THRESHOLD, info


def _set_frontmatter_ticker(note_path: Path, new_ticker: str) -> bool:
    """Rewrite the ``ticker:`` line inside a note's YAML frontmatter.

    Returns False when the file or the ticker line is absent (logged by
    the caller; the DB update still proceeds — the note is repairable
    by the next sync pass).
    """
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end < 0:
        return False
    head, tail = text[:end], text[end:]
    new_head, n = re.subn(
        r"(?m)^ticker:.*$", f"ticker: {new_ticker}", head)
    if n == 0:
        return False
    note_path.write_text(new_head + tail, encoding="utf-8")
    return True


def append_finhub_report_section(
    path: Path,
    outcomes: list[FhOutcome],
    *,
    mode: str,
) -> None:
    """Append the [finnhub] section to the existing report."""
    def count(name: str) -> int:
        return sum(1 for o in outcomes if o.outcome == name)

    lines = [
        "",
        "[finnhub]  # stage-1 discovery + yfinance verify (writebacks on --apply)",
        f"  generated: {datetime.now(UTC).isoformat(timespec='seconds')}"
        f"  mode: {mode}",
        (f"  outcomes: {count('writeback-candidate')} writeback-candidates"
         f" · {count('unverified')} unverified"
         f" · {count('still-dead')} still-dead"
         f" · {count('no-candidates')} no-candidates"),
        "  # entity | outcome | ticker | name_match | yfinance",
    ]
    for o in outcomes:
        change = (f"{o.old_ticker} -> {o.new_ticker}"
                  if o.new_ticker else (o.old_ticker or "-"))
        score = f"{o.score:.2f}" if o.score is not None else "-"
        sample = o.sample or "-"
        lines.append(
            f"    {o.entity} | {o.outcome} | {change} | {score} | {sample}")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


def _resolve_finnhub_target(
    entity: str,
    old_ticker: str | None,
    *,
    lookup_fn,
    verify_fn,
    cache_dir: Path,
    delay: float,
    taken: dict[str, str] | None = None,
) -> FhOutcome:
    """Stage-1 resolution for one target: search -> guard -> verify."""
    try:
        matches, from_cache = lookup_fn(entity, cache_dir)
    except Exception:  # network/token hiccup: source unavailable, not fatal
        log.warning("finnhub lookup failed for %s — skipping",
                    entity, exc_info=True)
        return FhOutcome(entity, old_ticker, "no-candidates")
    if not from_cache and delay > 0:
        time.sleep(delay)
    candidates = [m.symbol for m in matches
                  if _candidate_allowed(old_ticker, m.symbol, taken)][:3]
    best: FhOutcome | None = None
    for cand in candidates:
        ok, info = _verify_candidate(entity, cand, verify_fn)
        if info is None:
            continue
        name = info.get("longName") or info.get("shortName") or ""
        sample = " · ".join(x for x in (name, info.get("industry", "")) if x)
        score = name_match_score(name, entity)
        if ok:
            return FhOutcome(entity, old_ticker, "writeback-candidate",
                             new_ticker=cand, score=score,
                             sample=sample, info=info)
        if best is None or score > (best.score or 0):
            best = FhOutcome(entity, old_ticker, "unverified",
                             score=score, sample=sample)
    if best is not None:
        return best
    return FhOutcome(
        entity, old_ticker,
        "no-candidates" if not candidates else "still-dead")


def _apply_finnhub_writebacks(
    conn: sqlite3.Connection,
    writebacks: list[FhOutcome],
    *,
    file_path_of: dict[str, str | None],
    fetch_cache: Path | None,
) -> int:
    """Write entities.ticker + note frontmatter; extend the fetch cache
    with the verified info so the next bulk sweep sees the new tickers."""
    cache_payload = None
    if fetch_cache is not None and fetch_cache.exists():
        cache_payload = json.loads(fetch_cache.read_text(encoding="utf-8"))
    with conn:
        for o in writebacks:
            conn.execute(
                "UPDATE entities SET ticker = ? WHERE name = ?",
                (o.new_ticker, o.entity))
            fp = file_path_of.get(o.entity)
            if fp and _set_frontmatter_ticker(PROJECT_ROOT / fp,
                                              o.new_ticker or ""):
                log.info("frontmatter ticker updated: %s", fp)
            else:
                log.warning("no frontmatter ticker line for %s "
                            "(DB updated; note needs a manual look)",
                            o.entity)
            if cache_payload is not None:
                cache_payload["info_by_name"][o.entity] = o.info
                cache_payload["failures"] = [
                    f for f in cache_payload.get("failures", [])
                    if f[0] != o.entity]
    if cache_payload is not None and fetch_cache is not None:
        fetch_cache.write_text(json.dumps(cache_payload), encoding="utf-8")
        log.info("fetch cache extended; the next bulk sweep sees the new "
                 "tickers")
    return len(writebacks)


def _cached_verify(verify_fn, cache: dict):
    """Success-only verify wrapper (S4 warm-sweep doctrine).

    Verified info payloads cache permanently (slow-moving fundamentals);
    None results do NOT cache — transient yfinance flakes must retry
    cheaply on the next sweep (the Piramal case).
    """

    def verify(ticker: str) -> dict | None:
        if ticker in cache:
            return cache[ticker]
        info = verify_fn(ticker)
        if info is not None:
            cache[ticker] = info
        return info

    return verify


def _collect_finnhub_targets(
    conn: sqlite3.Connection,
    report_path: Path,
    include_unlisted: bool,
    terminal: set[str],
) -> tuple[dict[str, str], list[tuple[str, str | None]], dict[str, str], list[str]]:
    """Collect finnhub targets from the yfinance report + live DB state.

    Returns (file_path_of, targets, taken, stale) — file_path_of maps
    entity -> frontmatter path, targets are the unresolved (entity,
    old_ticker) pairs, taken maps ticker -> holder, stale are entities
    whose live ticker already diverged from the report (report predates
    writebacks).
    """
    rows = conn.execute(
        "SELECT name, ticker, file_path FROM entities "  # noqa: S608
        "WHERE entity_type = 'company'").fetchall()
    file_path_of = {r[0]: r[2] for r in rows}
    current_ticker = {r[0]: r[1] for r in rows}
    taken: dict[str, str] = {}
    for name, tick, _fp in rows:
        if tick:
            taken[tick] = name
    stale = [n for n, t in load_gf_targets(report_path,
                                           include_unlisted=include_unlisted)
             if t is not None and current_ticker.get(n) not in (None, t)]
    targets = [t for t in load_gf_targets(report_path,
                                          include_unlisted=include_unlisted)
               if t[0] not in terminal
               and current_ticker.get(t[0]) in (None, t[1])]
    return file_path_of, targets, taken, stale


def run_finnhub_pass(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    include_unlisted: bool = False,
    report_path: Path | None = None,
    cache_dir: Path | None = None,
    lookup_fn=fh_search_multi,
    verify_fn=fetch_company,
    delay: float = GF_MIN_DELAY_S,
    fetch_cache: Path | None = FETCH_CACHE_PATH,
    verify_cache: Path | None = None,
) -> int:
    """Stage 1 (S2): FinnHub discovery -> yfinance verify -> writeback.

    Targets = the last report's [ticker_issues] (+ opt-in unlisted).
    Each candidate must pass the exchange-class guard AND a
    single-ticker yfinance fetch with a fuzzy name match — only then is
    it a writeback-candidate. Dry-run reports; --apply writes
    entities.ticker + the note frontmatter and EXTENDS the persistent
    fetch cache with the verified info so the next bulk sweep sees the
    new ticker (doctrine: resolution is permanent, yfinance stays the
    only bulk source).

    ``verify_cache`` (main() passes memory/fh_verify_cache.json; None =
    in-memory only, what tests use) is the success-only info cache that
    keeps warm re-sweeps network-free outside genuinely new
    verifications.
    """
    verify_store: dict = {}
    if verify_cache is not None and verify_cache.exists():
        verify_store = json.loads(
            verify_cache.read_text(encoding="utf-8"))
    verify = _cached_verify(verify_fn, verify_store)
    report_path = REPORT_PATH if report_path is None else report_path
    cache_dir = GF_PAGE_CACHE_DIR if cache_dir is None else cache_dir
    if not report_path.exists():
        log.error("no relations report at %s — run the yfinance pass first "
                  "(--source yfinance [--check-only]) to produce it",
                  report_path)
        return 2
    conn.execute(ENTITY_TICKER_STATUS_DDL)
    terminal = load_terminal_statuses(conn)
    file_path_of, targets, taken, stale = _collect_finnhub_targets(
        conn, report_path, include_unlisted, terminal)
    if stale:
        log.info("skipping %d already-resolved targets (report predates "
                 "writebacks): %s", len(stale), ", ".join(sorted(stale)))
    log.info("finnhub pass: %d targets (%d unlisted opt-in), %s",
             len(targets),
             sum(1 for _n, t in targets if t is None),
             "dry-run" if dry_run else "APPLY")

    outcomes = [
        _resolve_finnhub_target(
            entity, old_ticker, lookup_fn=lookup_fn, verify_fn=verify,
            cache_dir=cache_dir, delay=delay, taken=taken)
        for entity, old_ticker in targets
    ]
    if verify_store and verify_cache is not None:
        verify_cache.parent.mkdir(parents=True, exist_ok=True)
        verify_cache.write_text(json.dumps(verify_store), encoding="utf-8")
    for o in outcomes:
        log.info("  %s: %s%s", o.entity, o.outcome,
                 f" [{o.old_ticker} -> {o.new_ticker}]"
                 if o.new_ticker else "")

    writebacks = [o for o in outcomes if o.outcome == "writeback-candidate"]
    applied = 0
    if not dry_run and writebacks:
        applied = _apply_finnhub_writebacks(
            conn, writebacks, file_path_of=file_path_of,
            fetch_cache=fetch_cache)

    append_finhub_report_section(
        report_path, outcomes, mode="dry-run" if dry_run else "apply")
    if terminal:
        append_terminal_report_section(report_path, terminal)
    log.info("finnhub section appended to %s", report_path)
    if dry_run:
        log.info("dry-run: %d writeback-candidates (re-run with --apply)",
                 len(writebacks))
    else:
        log.info("apply: %d tickers written", applied)
    return 0





# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def _run_classify(args) -> int:
    """--classify / --unclassify: the ONLY writer of entity_ticker_status.

    All-writes-explicit: these flags perform just this DB write and exit
    (a sweep never classifies on its own). Upserts are idempotent; the
    report picks the classification up on the next resolution pass.
    """
    conn = connect(DB_PATH)
    try:
        conn.execute(ENTITY_TICKER_STATUS_DDL)
        if args.unclassify:
            with conn:
                cur = conn.execute(
                    "DELETE FROM entity_ticker_status WHERE entity_name = ?",
                    (args.unclassify,))
            if cur.rowcount:
                log.info("%s: classification removed", args.unclassify)
            else:
                log.info("%s: no classification present", args.unclassify)
            return 0
        parts = args.classify
        entity, status = parts[0], parts[1]
        successor = " ".join(parts[2:]) or None
        if len(parts) < 2 or status not in TERMINAL_STATUSES:
            log.error("--classify expects ENTITY %s [SUCCESSOR] "
                      "(got %r)", "/".join(TERMINAL_STATUSES),
                      " ".join(parts))
            return 2
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO entity_ticker_status "
                "(entity_name, status, successor, decided_at) "
                "VALUES (?, ?, ?, ?)",
                (entity, status, successor, now))
        log.info("classified: %s -> %s%s (%s)", entity, status,
                 f" -> {successor}" if successor else "", now)
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Relations-enrichment driver (Relations 2.0).",
        epilog=(
            "Examples:\n"
            "  %(prog)s --source yfinance --dry-run\n"
            "  %(prog)s --source yfinance --topology clique --k 8\n"
            "  %(prog)s --source yfinance --check-only   # hygiene only\n"
            '  %(prog)s --classify "Akzo Nobel India" amalgamated '
            '"JSW Paints"\n'
            '  %(prog)s --unclassify "Akzo Nobel India"\n'
        ),
    )
    p.add_argument("--source", choices=SOURCES, default="yfinance")
    p.add_argument("--classify", nargs="+", metavar="ARG",
                   help="explicit terminal classification write: ENTITY "
                        "delisted | amalgamated [SUCCESSOR WORDS...] — "
                        "upserts entity_ticker_status; the entity leaves "
                        "all resolution sweeps and is reported under "
                        "[terminal]. Performs only this write.")
    p.add_argument("--unclassify", metavar="ENTITY",
                   help="remove ENTITY's terminal classification (re-opens "
                        "resolution). Performs only this write.")
    p.add_argument("--include-unlisted", action="store_true",
                   help="googlefinance only: also attempt the deliberately-"
                        "unlisted set (served by tier 2 / curation only)")
    p.add_argument("--tier2", action="store_true",
                   help="googlefinance only: enable tier-2 name->symbol "
                        "discovery via BSE's PeerSmartSearch (NSE's own API "
                        "is Akamai-blocked from this network, 2026-08-25; "
                        "BSE rows carry NSE symbols anyway)")
    p.add_argument("--topology", choices=("knn", "clique"), default="knn",
                   help="competes_with shape: knn (default, bounded) or "
                        "clique (full n*(n-1)/2, kept reproducible)")
    p.add_argument("--mutual", action="store_true",
                   help="keep a KNN pair only when BOTH endpoints picked each "
                        "other (reciprocity precision filter; measured "
                        "2026-08-24: 3385 -> 2341 pairs)")
    p.add_argument("--k", type=int, default=8,
                   help="K nearest neighbours per company for knn topology "
                        "(yfinance knn default 8; embeddings E3 default 10 when --source embeddings)")
    p.add_argument("--threshold", type=float, default=0.0,
                   help="cosine threshold for embeddings E3 (default 0.0 = no filter); "
                        "for coinfer E4 it is the minimum score to emit")
    p.add_argument("--per-company", type=int, default=3,
                   help="coinfer E4: max suggestions per company (default 3)")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--dry-run", action="store_true",
                   help="explicit dry-run (this is already the default; "
                        "accepted so '--dry-run' in docs/make targets works)")
    p.add_argument("--apply", action="store_true",
                   help="write edges (default OFF = dry-run)")
    p.add_argument("--check-only", action="store_true",
                   help="fetch + ticker-hygiene report only; no edge writes")
    default_cache_disp = str(FETCH_CACHE_PATH.relative_to(PROJECT_ROOT))
    p.add_argument("--fetch-cache", metavar="PATH", default=None,
                   help="PATH override for the persistent fetch cache "
                        f"(default: {default_cache_disp})")
    p.add_argument("--refresh-cache", action="store_true",
                   help="ignore any existing fetch cache and refetch all "
                        "tickers (rewrites the cache)")
    p.add_argument("--no-cache", action="store_true",
                   help="disable the fetch cache entirely (fetch and discard)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    if args.classify or args.unclassify:
        return _run_classify(args)

    if args.source == "googlefinance":
        conn = connect(DB_PATH)
        try:
            return run_googlefinance_pass(
                conn, include_unlisted=args.include_unlisted,
                tier2=args.tier2, apply_resolutions=args.apply)
        finally:
            conn.close()

    if args.source == "finnhub":
        conn = connect(DB_PATH)
        try:
            return run_finnhub_pass(
                conn, dry_run=not args.apply,
                include_unlisted=args.include_unlisted,
                verify_cache=PROJECT_ROOT / "memory"
                / "fh_verify_cache.json")
        finally:
            conn.close()

    if args.source == "embeddings":
        # E3: k=10 default for embeddings (yfinance default is 8)
        k = args.k if args.k != 8 else 10
        conn = connect(DB_PATH)
        try:
            return run_embeddings_pass(
                conn, k=k, threshold=args.threshold, dry_run=not args.apply)
        finally:
            conn.close()

    if args.source == "coinfer":
        conn = connect(DB_PATH)
        try:
            return run_coinfer_pass(
                conn, per_company=args.per_company, threshold=args.threshold,
                dry_run=not args.apply)
        finally:
            conn.close()

    if args.source == "holders":
        conn = connect(DB_PATH)
        try:
            return run_holders_pass(conn, dry_run=not args.apply, workers=args.workers)
        finally:
            conn.close()

    if args.source != "yfinance":
        owner = _NOT_IMPLEMENTED[args.source]
        print(f"source '{args.source}' is not implemented yet ({owner}); "
              f"E2 ships the yfinance pass only", file=sys.stderr)
        return 2

    conn = connect(DB_PATH)

    try:
        return run_yfinance_pass(
            conn, topology=args.topology, k=args.k, workers=args.workers,
            dry_run=not args.apply,
            check_only=args.check_only,
            fetch_cache=(None if args.no_cache else
                         (Path(args.fetch_cache)
                          if args.fetch_cache else FETCH_CACHE_PATH)),
            refresh_cache=args.refresh_cache,
            mutual=args.mutual,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
