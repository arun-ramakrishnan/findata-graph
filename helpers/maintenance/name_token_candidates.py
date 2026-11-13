#!/usr/bin/python3
"""Name-token candidate generator for the curated family lane (S1).

Proposal: ``doc/improvements/proposals/graph/name_token_family_seed_lane.md``.

READ-ONLY: this module never writes to any store. It emits a ranked
adjudication report of company pairs sharing a distinctive name token
(stopworded, len>2, document-frequency-capped), each annotated with an
existing-edge check (``same_group`` / ``subsidiary_of`` / ``jv_with``).
The report feeds operator curation of ``helpers/misc/family_seeds.json`` —
it never writes edges itself. Auto-apply is vetoed by the #507 lesson
(auto-derived group edges inflated ``same_group`` 10,373 → 33,270, 3.2×,
destroying real parent claims).

Measured signal (2026-11-12 store): shared distinctive name token = 76×
lift for true ``same_group`` pairs (38.5% vs 0.5% random) vs embedding cosine
4.8× — and vector space is blind on 99.4% of the hard ``subsidiary_of``
edges (≥1 endpoint unembedded). Core queue (df≤5): 554 candidates, 80 hits
(14.4% precision), 474 new.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core import jsonio  # noqa: E402

EDGE_TYPES = ("same_group", "subsidiary_of", "jv_with")
DEFAULT_MAX_DF = 5
EXTENDED_MAX_DF = 25


# Default stopword set — grammar-only, matching the list the 2026-11-12
# measurement ran (554 candidates / 80 hits / 14.4% at df≤5 over the 1,209
# embedded companies). Segment/suffix words are NOT stopped here: the df cap
# already excludes them (bank 49, finance 26, energy 24, power 22, ...) and
# stopping more would silently change the measured evidence.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "of",
        "for",
        "to",
        "in",
        "on",
        "with",
        "at",
        "by",
        "from",
        "&",
    }
)

# Second line of defense (proposal §5), applied only under ``--strict``:
# generic corporate suffixes, filing chrome, and segment words — measured as
# noise at any cap. 2026-11-12 census over the embedded universe: 50 such
# tokens carry 2≤df≤5 (laboratories 5, management 5, capital 5, rail 5,
# realty 5, logistics 5, agro 4, precision 4, development 4, packaging 4,
# solar 3, holdings 3, ...); stopping them tightens precision (18.9% instead
# of 14.4%) at the cost of diverging from the recorded evidence numbers.
NOISE_STOPWORDS = frozenset(
    {
        "ltd",
        "limited",
        "pvt",
        "private",
        "corp",
        "corporation",
        "inc",
        "company",
        "companies",
        "group",
        "co",
        "intl",
        "holdings",
        "holding",
        "enterprises",
        "enterprise",
        "ventures",
        "venture",
        "capital",
        "investment",
        "investments",
        "investor",
        "trading",
        "trade",
        "services",
        "service",
        "systems",
        "system",
        "solutions",
        "solution",
        "technologies",
        "technology",
        "tech",
        "global",
        "international",
        "world",
        "worldwide",
        "india",
        "indian",
        "bharat",
        "national",
        "industries",
        "industry",
        "energy",
        "power",
        "finance",
        "financial",
        "bank",
        "banks",
        "insurance",
        "chemicals",
        "chemical",
        "textiles",
        "textile",
        "electronics",
        "electronic",
        "telecom",
        "pharma",
        "pharmaceuticals",
        "health",
        "healthcare",
        "software",
        "network",
        "networks",
        "media",
        "food",
        "foods",
        "consumer",
        "retail",
        "logistics",
        "shipping",
        "transport",
        "transportation",
        "engineering",
        "construction",
        "realty",
        "estate",
        "property",
        "properties",
        "hotel",
        "hotels",
        "resorts",
        "agro",
        "agri",
        "agriculture",
        "mining",
        "minerals",
        "steel",
        "cement",
        "petroleum",
        "refinery",
        "refineries",
        "distribution",
        "distributors",
        "marketing",
        "exports",
        "imports",
        "export",
        "import",
        "mills",
        "mill",
        "fabrics",
        "fabric",
        "polyester",
        "cotton",
        "yarn",
        "paper",
        "packaging",
        "glass",
        "rubber",
        "tires",
        "tyres",
        "paints",
        "paint",
        "valves",
        "bearings",
        "forgings",
        "castings",
        "precision",
        "products",
        "product",
        "beverages",
        "beverage",
        "dairy",
        "sugar",
        "tea",
        "coffee",
        "spices",
        "oils",
        "fertilizers",
        "fertiliser",
        "fertilisers",
        "cables",
        "cable",
        "wires",
        "wire",
        "switchgear",
        "transformers",
        "generators",
        "generation",
        "solar",
        "wind",
        "renewables",
        "renewable",
        "digital",
        "online",
        "infotech",
        "information",
        "computers",
        "computer",
        "mobile",
        "wireless",
        "broadband",
        "satellite",
        "defence",
        "defense",
        "aerospace",
        "aviation",
        "airlines",
        "railways",
        "rail",
        "highway",
        "highways",
        "ports",
        "port",
        "terminals",
        "terminal",
        "shipbuilding",
        "ship",
        "machines",
        "machine",
        "machinery",
        "equipment",
        "components",
        "component",
        "tools",
        "tool",
        "fasteners",
        "hardware",
        "electrical",
        "electric",
        "appliances",
        "appliance",
        "furniture",
        "ceramics",
        "granite",
        "marble",
        "tiles",
        "tile",
        "sanitary",
        "plumbing",
        "urban",
        "overseas",
        "domestic",
        "development",
        "developers",
        "developer",
        "projects",
        "project",
        "assets",
        "asset",
        "management",
        "managers",
        "manager",
        "advisory",
        "consultancy",
        "consultants",
        "consultant",
        "consulting",
        "associates",
        "associate",
        "partners",
        "partner",
        "alliance",
        "alliances",
        "trust",
        "foundation",
        "foundations",
        "society",
        "institute",
        "institutes",
        "academy",
        "university",
        "school",
        "research",
        "laboratories",
        "laboratory",
        "labs",
        "centre",
        "center",
        "centres",
        "centers",
        "division",
        "divisions",
        "unit",
        "units",
        "works",
        "factory",
        "factories",
        "plant",
        "plants",
        "operations",
        "operation",
        "office",
        "offices",
        "branch",
        "branches",
        "subsidiary",
        "subsidiaries",
        "affiliates",
        "affiliate",
        "conglomerate",
        "house",
        "family",
        "families",
        "brand",
        "brands",
        "business",
        "businesses",
        "records",
        "reports",
        "report",
        "bureau",
        "associations",
        "association",
    }
)


def tokenize(name: str, *, strict: bool = False) -> list[str]:
    """Distinctive tokens of a company name (lowercased, len>2, stopworded).

    Punctuation runs become spaces; tokens must be alpha-only (legal
    identifiers/numbers are dropped — they are not family signals). Default
    stopword set is grammar-only (reproduces the recorded evidence);
    ``strict=True`` additionally applies :data:`NOISE_STOPWORDS`.
    """
    stop = STOPWORDS | (NOISE_STOPWORDS if strict else frozenset())
    cleaned = "".join(ch if ch.isalnum() else " " for ch in name.lower())
    seen: set[str] = set()
    out: list[str] = []
    for tok in cleaned.split():
        if len(tok) > 2 and tok.isalpha() and tok not in stop and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def load_company_names(conn, *, all_companies: bool = False) -> list[str]:
    """Distinct company names.

    Default universe = the 1,209 companies in ``company_embeddings`` — the
    exact population the 2026-11-12 measurements were taken over (token
    lift 76×, candidate yield 554/80/14.4% at df≤5). ``all_companies=True``
    widens to every ``entity_type='company'`` name (25.5k) — useful for
    exploring stub/unlisted parents, but the df calculus changes and the
    core queue is no longer bounded.
    """
    if all_companies:
        rows = conn.execute(
            "SELECT DISTINCT name FROM entities WHERE entity_type = 'company'"
        ).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT company_name FROM company_embeddings").fetchall()
    names: list[str] = []
    seen: set[str] = set()
    for (name,) in rows:
        key = name.strip().lower()
        if name.strip() and key not in seen:
            seen.add(key)
            names.append(name.strip())
    return names


def load_existing_pairs(conn) -> set[tuple[str, str]]:
    """Undirected pair set for same_group/subsidiary_of/jv_with edges."""
    pairs: set[tuple[str, str]] = set()
    for src, tgt in conn.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type IN (?, ?, ?)",
        EDGE_TYPES,
    ).fetchall():
        a, b = sorted((src.strip().lower(), tgt.strip().lower()))
        if a and b and a != b:
            pairs.add((a, b))
    return pairs


def compute_token_df(names: list[str], *, strict: bool = False) -> dict[str, int]:
    """Document frequency per token over the company-name corpus."""
    df: dict[str, int] = {}
    for name in names:
        for tok in set(tokenize(name, strict=strict)):
            df[tok] = df.get(tok, 0) + 1
    return df


def generate_candidates(
    conn,
    *,
    max_df: int = DEFAULT_MAX_DF,
    all_companies: bool = False,
    strict: bool = False,
) -> tuple[list[dict], dict[str, int | float | str | bool]]:
    """Read-only candidate generation.

    Returns ``(rows, stats)``: rows are ordered by (df, token, source,
    target); each row has source/target (display names), token (rarest
    shared distinctive token), df, and hit (bool — already connected by
    same_group/subsidiary_of/jv_with). Stats carries total/hits/new and
    precision. Unique pairs only (a pair sharing several tokens is listed
    once, under its rarest token).
    """
    names = load_company_names(conn, all_companies=all_companies)
    df = compute_token_df(names, strict=strict)
    existing = load_existing_pairs(conn)

    # token -> list of company names carrying it (df-capped)
    by_token: dict[str, list[str]] = defaultdict(list)
    for name in names:
        for tok in set(tokenize(name, strict=strict)):
            if 2 <= df.get(tok, 0) <= max_df:
                by_token[tok].append(name)

    rows: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for tok, members in by_token.items():
        members_sorted = sorted(members, key=str.lower)
        for i in range(len(members_sorted)):
            for j in range(i + 1, len(members_sorted)):
                a, b = members_sorted[i], members_sorted[j]
                akey, bkey = a.lower(), b.lower()
                key = (akey, bkey) if akey <= bkey else (bkey, akey)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                hit = key in existing
                rows.append(
                    {
                        "source": a,
                        "target": b,
                        "token": tok,
                        "df": df[tok],
                        "hit": hit,
                    }
                )

    rows.sort(key=lambda r: (r["df"], r["token"], r["source"].lower(), r["target"].lower()))
    total = len(rows)
    hits = sum(1 for r in rows if r["hit"])
    stats = {
        "companies": len(names),
        "tokens": len(by_token),
        "total": total,
        "hits": hits,
        "new": total - hits,
        "precision": round(hits / total, 4) if total else 0.0,
    }
    return rows, stats


def print_report(
    rows: list[dict], stats: dict[str, int | float | str | bool], *, limit: int = 60
) -> None:
    print(f"companies scanned       : {stats['companies']}")
    print(f"distinctive tokens (2≤df≤{stats.get('max_df', '')}) : {stats['tokens']}")
    print(f"candidate pairs        : {stats['total']}")
    print(f"  hits (already edge)  : {stats['hits']}  ({stats['precision'] * 100:.1f}%)")
    print(f"  new candidates       : {stats['new']}")
    print()
    print(f"{'#':>4}  {'token':<16} {'df':>3}  {'seed':<42} {'target':<42} existing")
    for i, r in enumerate(rows[:limit], 1):
        print(
            f"{i:>4}  {r['token']:<16} {r['df']:>3}  "
            f"{r['source'][:42]:<42} {r['target'][:42]:<42} "
            f"{'HIT' if r['hit'] else ''}"
        )
    if len(rows) > limit:
        print(f"... ({len(rows) - limit} more)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="name_token_candidates",
        description=(
            "Read-only name-token candidate generator for the family lane. "
            "Never writes to any store."
        ),
    )
    ap.add_argument("--db", default=str(_REPO_ROOT / "memory" / "research.db"))
    ap.add_argument("--max-df", type=int, default=DEFAULT_MAX_DF)
    ap.add_argument("--extended", action="store_true", help="df≤25 extended view")
    ap.add_argument(
        "--all-companies",
        action="store_true",
        help="widen universe to every entity_type='company' name (default: the 1,209 embedded companies)",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="also stop NOISE_STOPWORDS (corporate suffixes/segment words; raises precision, diverges from the recorded evidence counts)",
    )
    ap.add_argument("--json", metavar="FILE", help="write full ordered report JSON")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args(argv)

    max_df = EXTENDED_MAX_DF if args.extended else args.max_df
    import duckdb

    from helpers.core.duckdb_shared import memory_cap_config

    con = duckdb.connect(args.db, **memory_cap_config())
    try:
        rows, stats = generate_candidates(
            con,
            max_df=max_df,
            all_companies=args.all_companies,
            strict=args.strict,
        )
    finally:
        con.close()
    stats["max_df"] = max_df
    stats["universe"] = "all companies" if args.all_companies else "embedded (1,209)"
    stats["strict"] = args.strict

    print_report(rows, stats, limit=args.limit)
    if args.json:
        out = {"max_df": max_df, "stats": stats, "candidates": rows}
        Path(args.json).write_text(jsonio.dumps(out, indent=2))
        print(f"\nfull report -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
