#!/usr/bin/env python3
"""Foreign-universe name/ISIN probe against the xchanges masters (read-only).

The store's international companies (``geography/international`` tag, 150
today) come from curated notes/tickers. The xchanges masters in
``memory/data/sources.duckdb::exchange_listings`` hold the *whole* global
listing surface (~47k rows). This probe answers: which foreign master
listings can be mapped onto entities we already have (name similarity), and
how much of that can be keyed by ISIN at all (per-exchange ISIN coverage).

ISIN situation: only BSE/NSE (India), HKEX and TWSE carry ISINs in the
master; every other exchange (NASDAQ, NYSE, TSE, KRX, LSE, TSX, SSE,
SZSE, EURONEXT, XETRA, …) is empty. Our identifier table
(``entity_identifiers``) only stores NSE/BSE ISINs today — so ISIN is not
yet a usable global join key; name/symbol matching is the primary lever.

Read-only. Exit 0 always (advisory lane).

Usage:
    python3 helpers/maintenance/foreign_name_isin_probe.py
    python3 helpers/maintenance/foreign_name_isin_probe.py --json bench_data/foreign_probe/report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402

SOURCES_DB = _PROJECT_ROOT / "memory" / "data" / "sources.duckdb"
INDIA_EXCHANGES = {
    "BSE",
    "NSE",
    "NFO",
    "BFO",
    "NCO",
    "MCX",
    "CDS",
    "NSEIX",
}

# suffix tokens stripped before exact-name comparison (legal form words)
_LEGAL_SUFFIXES = {
    "ltd",
    "limited",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "plc",
    "sa",
    "ag",
    "gmbh",
    "co",
    "company",
    "group",
    "holdings",
    "holding",
    "corp.",
    "inc.",
    "ltd.",
    "plc.",
    "sa.",
    "ag.",
    "n.v",
    "nv",
    "llc",
    "s.a",
    "s.a.",
    "b.v",
    "bv",
    "kk",
    "oyj",
    "ab",
    "as",
    "l.p",
    "lp",
}


def _normalize(name: str) -> str:
    """Unicode-fold, lowercase, strip punctuation, drop legal-suffix tokens."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", s) if t]
    tokens = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def _token_set(name: str) -> set[str]:
    return set(_normalize(name).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_foreign_masters(conn, min_isin: bool = False):
    rows = conn.execute(
        """
        SELECT isin, name, exchange, symbol, segment FROM exchange_listings
        WHERE exchange NOT IN (SELECT unnest(?))
        """,
        [sorted(INDIA_EXCHANGES)],
    ).fetchall()
    if min_isin:
        rows = [r for r in rows if r[0]]
    return rows


def probe(entities_conn, sources_conn, min_isin: bool = False) -> dict:  # noqa: C901  # single-pass probe over the master list (exact + fuzzy tiers + per-entity memo); extraction would re-scan
    masters = load_foreign_masters(sources_conn, min_isin=min_isin)

    ent_rows = entities_conn.execute(
        "SELECT name FROM entities WHERE entity_type='company'"
    ).fetchall()
    entities = [r[0] for r in ent_rows]

    ent_norm = {_normalize(n): n for n in entities}

    # inverted token index: token -> entity indexes (only ≥2-token entities)
    ent_toks = [_token_set(n) for n in entities]
    tok_to_ents: dict[str, list[int]] = {}
    for i, ts in enumerate(ent_toks):
        if len(ts) < 2:
            continue
        for t in ts:
            tok_to_ents.setdefault(t, []).append(i)

    exact: list[dict] = []
    fuzzy: list[dict] = []
    matched_entities: set[str] = set()

    for isin, name, exchange, symbol, segment in masters:
        nm = _normalize(name)
        if not nm:
            continue
        target = ent_norm.get(nm)
        if target is not None:
            exact.append(
                {
                    "master_name": name,
                    "exchange": exchange,
                    "symbol": symbol,
                    "isin": isin,
                    "entity": target,
                }
            )
            matched_entities.add(target)
            continue
        toks = nm.split()
        if len(toks) < 2:
            continue
        # candidate pool = union of postings for the master's tokens
        cand = set()
        for t in toks:
            cand.update(tok_to_ents.get(t, ()))
            if len(cand) > 500:  # cap noise for very common tokens
                break
        if not cand:
            continue
        best_score = 0.0
        best_ent = None
        master_set = set(toks)
        for i in cand:
            s = len(master_set & ent_toks[i]) / len(master_set | ent_toks[i])
            if s > best_score:
                best_score = s
                best_ent = entities[i]
        if best_ent and best_score >= 0.66:
            fuzzy.append(
                {
                    "master_name": name,
                    "exchange": exchange,
                    "symbol": symbol,
                    "isin": isin,
                    "entity": best_ent,
                    "score": round(best_score, 3),
                }
            )
            matched_entities.add(best_ent)

    exact.sort(key=lambda r: (r["exchange"], r["entity"]))
    fuzzy.sort(key=lambda r: (-r["score"], r["entity"]))

    return {
        "masters_total": len(masters),
        "masters_with_isin": sum(1 for r in masters if r[0]),
        "exact_matches": len(exact),
        "fuzzy_matches_ge066": len(fuzzy),
        "distinct_entities_matched": len(matched_entities),
        "exact": exact,
        "fuzzy": fuzzy,
    }


def _render(result) -> str:
    lines = [
        "# Foreign-universe name/ISIN probe (xchanges masters vs entities)",
        "",
        f"Foreign master listings: {result['masters_total']:,} "
        f"(with ISIN: {result['masters_with_isin']:,}).",
        f"Exact normalized-name matches: {result['exact_matches']}.",
        f"Fuzzy (≥0.66) matches: {result['fuzzy_matches_ge066']}.",
        f"Distinct entities reached: {result['distinct_entities_matched']}.",
        "",
        "## Exact matches",
        "",
        "| exchange | symbol | master name | entity |",
        "|---|---|---|---|",
    ]
    for m in result["exact"][:80]:
        lines.append(f"| {m['exchange']} | {m['symbol']} | {m['master_name']} | {m['entity']} |")
    lines += [
        "",
        "## Fuzzy matches (≥0.66)",
        "",
        "| score | exchange | symbol | master name | entity |",
        "|---|---|---|---|---|",
    ]
    for m in result["fuzzy"][:60]:
        lines.append(
            f"| {m['score']} | {m['exchange']} | {m['symbol']} | {m['master_name']} | {m['entity']} |"
        )
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="memory/research.db")
    ap.add_argument("--isin-only", action="store_true", help="probe only masters that carry ISIN")
    ap.add_argument("--json", default=None, help="write report JSON")
    args = ap.parse_args(argv)

    import duckdb

    ent = duckdb.connect(args.db, **memory_cap_config())
    src = duckdb.connect(str(SOURCES_DB), **memory_cap_config())
    result = probe(ent, src, min_isin=args.isin_only)
    ent.close()
    src.close()

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {out}")
    print(_render(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
