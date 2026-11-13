#!/usr/bin/env python3
"""Generate foreign-link mappings (entity ↔ xchanges master) — read-only.

Tier policy (see doc/improvements/archive/graph/foreign_links_mapping_lane.md §3):
- **strict** (auto-curated candidates): exact normalized-name match, or
  fuzzy Jaccard ≥ 0.80;
- **review**: 0.66 ≤ fuzzy < 0.80 — adjudication only, never auto-applied.

Target universe is restricted to international entities (geography/
internationalational tag) OR foreign-tickered entities (non-Indian ticker
venue), so Indian homonyms cannot leak into the mapping.

Outputs (no store writes):
- bench_data/foreign_probe/links_strict.json
- bench_data/foreign_probe/links_review.json
- report printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.foreign_name_isin_probe import (  # noqa: E402
    _normalize,
    _token_set,
    load_foreign_masters,
)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


STRICT_FUZZY = 0.80
REVIEW_LOWER = 0.66

_FOREIGN_TICKER_WHERE = """
    e.ticker IS NOT NULL AND e.ticker <> ''
    AND NOT (e.ticker LIKE '%.NS' OR e.ticker LIKE '%.BO')
"""

# Static query string composed once at module scope (bandit S608-clean).
_TARGET_UNIVERSE_SQL = (
    "SELECT DISTINCT e.name, "  # noqa: S608  # module-level constants spliced; no user input
    "COALESCE((SELECT MAX(t.ticker) FROM entities t WHERE t.name = e.name), '') "
    "FROM entities e "
    "WHERE e.entity_type = 'company' "
    "AND ("
    "e.name IN (SELECT entity_name FROM entity_tags WHERE tag = 'geography/international') "
    "OR (" + _FOREIGN_TICKER_WHERE.strip() + ")"
    ") "
    "ORDER BY e.name"
)

# US depositary-heavy venues: a NASDAQ/NYSE line for a foreign company with
# an existing home-market primary is treated as an ADR-style path (alias),
# not as the canonical listing — see _is_adr_name / primary-preference.
_US_ADR_HEAVY_EXCHANGES = {"NASDAQ", "NYSE", "NYSEAMER"}

_ADR_MARKERS = (
    "american depositary",
    "american depository",
    "american deposited",
    " each representing ",
    "ads, each",
    "adr",
)


def _is_adr_name(name: str) -> bool:
    """True when the master listing name carries a depositary-share marker.

    ADRs/GDRs are a *listing path*, not the canonical home listing: a foreign
    company may also list directly (ordinary shares) on a US exchange or hold
    a primary listing at home (HKEX, TSE, KRX, LSE, …). Primary listings are
    preferred for mapping; ADR lines are demoted to aliases.
    """
    low = name.lower()
    return any(marker in low for marker in _ADR_MARKERS)


def target_universe(conn) -> dict[str, str]:
    """International-eligible entities → ticker ('' when none)."""
    rows = conn.execute(_TARGET_UNIVERSE_SQL).fetchall()
    return {r[0]: (r[1] or "") for r in rows}


def generate(entities_conn, sources_conn) -> dict:  # noqa: C901  # one cohesive primary/alias pipeline (strict+review+ADR demotion+ISIN); splitting would scatter the matching state machine
    universe = target_universe(entities_conn)
    masters = load_foreign_masters(sources_conn)

    uni_norm = {_normalize(n): n for n in universe}
    uni_toks = {n: _token_set(n) for n in universe}

    tok_to_ents: dict[str, list[str]] = {}
    for n, toks in uni_toks.items():
        if len(toks) < 2:
            continue
        for t in toks:
            tok_to_ents.setdefault(t, []).append(n)

    strict: list[dict] = []
    review: list[dict] = []
    us_rows: list[dict] = []
    matched: set[str] = set()

    for isin, name, exchange, symbol, segment in masters:
        nm = _normalize(name)
        if not nm:
            continue
        adr = _is_adr_name(name)
        target = uni_norm.get(nm)
        if target is not None:
            strict.append(
                {
                    "entity": target,
                    "exchange": exchange,
                    "symbol": symbol,
                    "isin": isin,
                    "master_name": name,
                    "tier": "exact",
                    "adr": adr,
                }
            )
            matched.add(target)
            continue
        toks = nm.split()
        if len(toks) < 2:
            continue
        cand = set()
        for t in toks:
            cand.update(tok_to_ents.get(t, ()))
            if len(cand) > 500:
                break
        if not cand:
            if exchange in _US_ADR_HEAVY_EXCHANGES:
                us_rows.append(
                    {
                        "exchange": exchange,
                        "symbol": symbol,
                        "isin": isin,
                        "master_name": name,
                    }
                )
            continue
        best_score = 0.0
        best_ent = None
        master_set = set(toks)
        for n in cand:
            s = len(master_set & uni_toks[n]) / len(master_set | uni_toks[n])
            if s > best_score:
                best_score = s
                best_ent = n
        if best_ent is None:
            if exchange in _US_ADR_HEAVY_EXCHANGES:
                # US-venue row with no exact/fuzzy-eligible match: keep raw for
                # the post-primary ADR pass (checked against home-market
                # primaries only — never the whole universe).
                us_rows.append(
                    {
                        "exchange": exchange,
                        "symbol": symbol,
                        "isin": isin,
                        "master_name": name,
                    }
                )
            continue
        if best_score >= STRICT_FUZZY:
            strict.append(
                {
                    "entity": best_ent,
                    "exchange": exchange,
                    "symbol": symbol,
                    "isin": isin,
                    "master_name": name,
                    "tier": "fuzzy",
                    "score": round(best_score, 3),
                    "adr": adr,
                }
            )
            matched.add(best_ent)
        elif best_score >= REVIEW_LOWER:
            review.append(
                {
                    "entity": best_ent,
                    "exchange": exchange,
                    "symbol": symbol,
                    "isin": isin,
                    "master_name": name,
                    "score": round(best_score, 3),
                }
            )

    strict.sort(key=lambda r: (r["entity"], r["exchange"], r["symbol"]))

    # Primary-listing preference: when an entity matched both a primary
    # (non-ADR) line and ADR lines, keep the primary in `strict` and demote
    # the ADR lines into `aliases` (still emitted in the JSON for reference,
    # never applied as the canonical link).
    primary: list[dict] = []
    aliases: list[dict] = []
    by_entity: dict[str, list[dict]] = {}
    for m in strict:
        by_entity.setdefault(m["entity"], []).append(m)
    for entity, rows in by_entity.items():
        # A US venue row counts as an ADR-style path whenever a home-market
        # primary exists; only when *every* matched venue is a US one do we
        # keep them as primaries (direct US-listed foreign issuers).
        non_us = [r for r in rows if r["exchange"] not in _US_ADR_HEAVY_EXCHANGES]
        if non_us:
            primary.extend(non_us)
            aliases.extend(r for r in rows if r["exchange"] in _US_ADR_HEAVY_EXCHANGES or r["adr"])
        else:
            primary.extend(rows)

    # Loose US candidates: alias when the entity has any home-market primary,
    # otherwise drop (no evidence they are the same commercial entity).
    us_primaries = {m["entity"] for m in primary}
    home_toks: dict[str, list[str]] = {}
    for n in us_primaries:
        for t in _token_set(n):
            home_toks.setdefault(t, []).append(n)
    for u in us_rows:
        ts = _token_set(u["master_name"])
        best_ent, best_score = None, 0.0
        for t in ts:
            for cand in home_toks.get(t, ()):
                s = _jaccard(ts, _token_set(cand))
                if s > best_score:
                    best_score, best_ent = s, cand
        if best_ent and best_score >= 0.10:
            aliases.append(
                {
                    "entity": best_ent,
                    "exchange": u["exchange"],
                    "symbol": u["symbol"],
                    "isin": u["isin"],
                    "master_name": u["master_name"],
                    "score": round(best_score, 3),
                    "adr": True,
                }
            )
    primary.sort(key=lambda r: (r["entity"], r["exchange"], r["symbol"]))
    aliases.sort(key=lambda r: (r["entity"], r["exchange"], r["symbol"]))

    review.sort(key=lambda r: (-r["score"], r["entity"]))

    return {
        "universe_size": len(universe),
        "strict_count": len(primary),
        "adr_alias_count": len(aliases),
        "review_count": len(review),
        "entities_matched": len({m["entity"] for m in primary}),
        "strict": primary,
        "aliases": aliases,
        "review": review,
    }


def _render(result) -> str:
    lines = [
        "# Foreign-link mapping (entity ↔ xchanges master)",
        "",
        f"Target universe (international/foreign-tickered entities): {result['universe_size']}.",
        f"Primary listings (exact + fuzzy ≥0.80, non-ADR): {result['strict_count']} "
        f"(entities reached: {result['entities_matched']}).",
        f"ADR/depositary aliases demoted: {result['adr_alias_count']} (kept in links_aliases.json).",
        f"Review tier (0.66–0.79): {result['review_count']} — adjudication only.",
        "",
        "## Primary listings",
        "",
        "| entity | exchange | symbol | isin | tier | master name |",
        "|---|---|---|---|---|---|",
    ]
    for m in result["strict"]:
        lines.append(
            f"| {m['entity']} | {m['exchange']} | {m['symbol']} | "
            f"{m['isin'] or '—'} | {m['tier']} | {m['master_name']} |"
        )
    lines += [
        "",
        "## Review tier (0.66–0.79)",
        "",
        "| score | entity | exchange | symbol | master name |",
        "|---|---|---|---|---|",
    ]
    for m in result["review"]:
        lines.append(
            f"| {m['score']} | {m['entity']} | {m['exchange']} | {m['symbol']} | {m['master_name']} |"
        )
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="memory/research.db")
    ap.add_argument("--json-dir", default="bench_data/foreign_probe")
    args = ap.parse_args(argv)

    import duckdb

    ent = duckdb.connect(args.db, **memory_cap_config())
    src = duckdb.connect(
        str(_PROJECT_ROOT / "memory" / "data" / "sources.duckdb"), **memory_cap_config()
    )
    result = generate(ent, src)
    ent.close()
    src.close()

    outdir = Path(args.json_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "links_strict.json").write_text(json.dumps(result["strict"], indent=2) + "\n")
    (outdir / "links_aliases.json").write_text(json.dumps(result["aliases"], indent=2) + "\n")
    (outdir / "links_review.json").write_text(json.dumps(result["review"], indent=2) + "\n")
    print(_render(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
