#!/usr/bin/env python3
"""Derive ``index`` entities + ``listed_on_index`` edges (company -> index).

Index-membership fill S2 (doc/improvements/archive/graph/index_membership_fill.md).
Reads the raw constituent sidecar (``sources.duckdb::vw_index_constituent``,
written by ``helpers/maintenance/index_sync.py``) and projects it into the
canonical SQLite graph:

- one fileless ``index`` entity per index name (idempotent INSERT OR IGNORE),
- one dyadic ``listed_on_index`` edge per (company, index),
- converged ``sector_classification`` on resolved companies whose field is
  still empty, via an explicit NSE-industry -> canonical-sector map (the
  raw exchange label is NOT the house taxonomy and stays sidecar-side).

Resolution precedence (never fuzzy name matching — #218 misfires four ways):
ISIN via ``exchange_listings.isin`` -> NSE symbol via ``entities.ticker``
(``SYMBOL`` or ``SYMBOL.NS``) -> worklist.

Idempotent: INSERT OR IGNORE for entities, apply_typed_edges
(UNIQUE(source, target, edge_type)) for edges. Re-running is safe.

Usage:
    python3 helpers/graph/derive_indices.py            # dry-run summary
    python3 helpers/graph/derive_indices.py --apply    # entities + edges + worklist
    python3 helpers/graph/derive_indices.py --verbose  # list every edge
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import duckdb

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect, utc_now  # noqa: E402
from helpers.graph import derive_cli as dcli  # noqa: E402  # S6 shared CLI scaffold
from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402

EDGE_TYPE = "listed_on_index"
SOURCE_REF = "derive:indices:nse-constituents"
SOURCES_DB = _REPO_ROOT / "memory" / "data" / "sources.duckdb"
WORKLIST = _REPO_ROOT / "findata" / "Misc" / "index_worklist.json"

# Explicit NSE-Indices `Industry` -> house `sector_classification` map. The
# exchange vocabulary is coarser/different (raw values stay in the sidecar);
# this only fills companies whose sector_classification is still empty, and
# unmapped labels are parked in the worklist, never silently dropped.
INDUSTRY_TO_SECTOR: dict[str, str] = {
    "Automobile and Auto Components": "Automotive",
    "Capital Goods": "Engineering_Capital_Goods",
    "Chemicals": "Chemicals",
    "Construction": "Infrastructure",
    "Construction Materials": "Building_Materials",
    "Consumer Durables": "Consumer",
    "Consumer Services": "Consumer",
    "Diversified": "Diversified",
    "Fast Moving Consumer Goods": "FMCG",
    "Financial Services": "Financial_Services",
    "Forest Materials": "Packaging",
    "Healthcare": "Healthcare",
    "Information Technology": "Technology",
    "Media Entertainment & Publication": "Media_Entertainment",
    "Metals & Mining": "Metals",
    "Oil Gas & Consumable Fuels": "Energy",
    "Oil, Gas & Consumable Fuels": "Energy",
    "Power": "Energy",
    "Realty": "Real_Estate",
    "Telecommunication": "Telecommunications",
    "Textiles": "Textiles",
}


def load_constituents(db: Path = SOURCES_DB) -> list[dict]:
    """Read the latest constituent vintage per index from the sidecar.

    Returns [] when the sidecar/table/view is absent (fresh clone) so the
    derive reports cleanly instead of crashing.
    """
    if not db.exists():
        return []
    con = duckdb.connect(str(db), read_only=True)
    try:
        present = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'index_constituents'"
        ).fetchone()
        if not present:
            return []
        rows = con.execute(
            "SELECT index_name, index_slug, symbol, isin, company_name, industry, as_of "
            "FROM vw_index_constituent ORDER BY index_name, symbol"
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "index_name": r[0],
            "index_slug": r[1],
            "symbol": r[2],
            "isin": r[3],
            "company_name": r[4],
            "industry": r[5],
            "as_of": r[6].isoformat() if r[6] is not None else None,
        }
        for r in rows
    ]


def _symbol_key(ticker: str | None) -> str | None:
    """``RELIANCE.NS`` / ``RELIANCE.BO`` -> ``RELIANCE`` (the NSE symbol)."""
    if not ticker:
        return None
    return ticker.split(".", 1)[0].strip().upper() or None


def _build_resolver(entities: list[tuple], exchange_rows: list[tuple]):
    """Build a ``(symbol, isin) -> company name`` resolver.

    ``entities``: ``(name, entity_type, ticker, sector_classification)``;
    ``exchange_rows``: ``(isin, symbol, exchange)``. Precedence: ISIN via
    ``exchange_listings`` (NSE row wins) -> NSE symbol via ``entities.ticker``
    (``SYMBOL`` or ``SYMBOL.NS``). Never fuzzy name matching (#218).
    """
    by_ticker: dict[str, str] = {}
    for name, etype, ticker, _sector in entities:
        if etype != "company" or not ticker:
            continue
        by_ticker[ticker.strip().upper()] = name
        key = _symbol_key(ticker)
        if key:
            by_ticker.setdefault(key, name)
    isin_to_symbol: dict[str, str] = {}
    for isin, symbol, exchange in exchange_rows:
        if not isin or not symbol:
            continue
        if isin not in isin_to_symbol or (exchange or "").upper() == "NSE":
            isin_to_symbol[isin.strip().upper()] = symbol.strip().upper()

    def company_for(symbol: str, isin: str) -> str | None:
        if isin:
            via_isin = isin_to_symbol.get(isin.strip().upper())
            if via_isin and via_isin in by_ticker:
                return by_ticker[via_isin]
        key = symbol.strip().upper()
        return by_ticker.get(key)

    return company_for


def resolve(
    constituents: list[dict], entities: list[tuple], exchange_rows: list[tuple]
) -> tuple[list[tuple], list[str], list[dict], dict[str, list[str]]]:
    """Resolve constituent rows to companies.

    ``entities``: ``(name, entity_type, ticker, sector_classification)``.
    ``exchange_rows``: ``(isin, symbol, exchange)``.

    Returns ``(edges, index_names, worklist, industry_map)`` where edges are
    ``(company, index, properties, source_ref)`` tuples for apply_typed_edges
    and ``industry_map`` maps industry label -> resolved company names.
    """
    company_for = _build_resolver(entities, exchange_rows)

    edges: list[tuple] = []
    index_names: set[str] = set()
    worklist: list[dict] = []
    industry_map: dict[str, list[str]] = {}
    for c in constituents:
        index = c["index_name"]
        if not index:
            continue
        index_names.add(index)
        company = company_for(c["symbol"] or "", c["isin"] or "")
        props = {
            "symbol": c["symbol"],
            "isin": c["isin"],
            "as_of": c["as_of"],
            "index_slug": c["index_slug"],
        }
        if company is None:
            worklist.append(
                {
                    "symbol": c["symbol"],
                    "isin": c["isin"],
                    "company_name": c["company_name"],
                    "index": index,
                    "industry": c["industry"],
                    "reason": "no_entity",
                }
            )
            continue
        edges.append((company, index, props, SOURCE_REF))
        if c["industry"]:
            industry_map.setdefault(c["industry"], []).append(company)
    return edges, sorted(index_names), worklist, industry_map


def normalized_name(name: str) -> str:
    """Vault ``normalized_name`` contract (``^[A-Za-z0-9][A-Za-z0-9_]*$``):
    runs of non-alphanumerics collapse to a single ``_``; edge underscores
    trimmed. Same rule as ``exchange_sync.normalized_name`` (index display
    names carry spaces, ``&`` and ``/``)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_")


def create_index_entities(conn, names, *, apply: bool = True) -> int:
    """Insert a fileless ``index`` entity row per index name (idempotent).

    Bare structural rows like countries/themes — no file_path, no note.
    Existing index rows get their ``normalized_name`` repaired (a first run
    stamped the raw display name). Returns rows inserted (dry-run count when
    apply=False).
    """
    if not apply:
        return len(names)
    now = utc_now()
    inserted = 0
    with conn:
        for name in names:
            nn = normalized_name(name)
            cur = conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(name, entity_type, normalized_name, last_updated) "
                "VALUES (?, 'index', ?, ?)",
                (name, nn, now),
            )
            inserted += cur.rowcount
            conn.execute(
                "UPDATE entities SET normalized_name = ? "
                "WHERE name = ? AND entity_type = 'index' AND IFNULL(normalized_name, '') <> ?",
                (nn, name, nn),
            )
    return inserted


def converge_industry(conn, industry_map: dict[str, list[str]], *, apply: bool = True) -> tuple:
    """Fill empty ``sector_classification`` from the explicit map.

    Only NULL/empty fields are written (authored sectors never clobbered).
    Returns ``(filled, unmapped_labels)`` where unmapped_labels is a sorted
    dict of label -> [company, ...] for the worklist.
    """
    unmapped: dict[str, list[str]] = {}
    planned = 0
    filled = 0
    with conn:
        for label, companies in sorted(industry_map.items()):
            sector = INDUSTRY_TO_SECTOR.get(label)
            if sector is None:
                unmapped[label] = sorted(set(companies))
                continue
            for name in sorted(set(companies)):
                row = conn.execute(
                    "SELECT sector_classification FROM entities WHERE name = ?", (name,)
                ).fetchone()
                if row is None or row[0]:
                    continue  # absent or already classified — never clobber
                planned += 1
                if not apply:
                    continue
                cur = conn.execute(
                    "UPDATE entities SET sector_classification = ?, last_updated = ? "
                    "WHERE name = ? AND (sector_classification IS NULL OR sector_classification = '')",
                    (sector, utc_now(), name),
                )
                filled += cur.rowcount
    return (filled if apply else planned), unmapped


def write_worklist(worklist: list, unmapped: dict[str, list[str]], path: Path = WORKLIST) -> int:
    """Persist unresolved constituents + unmapped industry labels.

    Same git-tracked operator surface as ``country_worklist.json``.
    """
    payload = {
        "generated": utc_now(),
        "count": len(worklist),
        "constituents": sorted(worklist, key=lambda w: (w["index"], w["symbol"] or "")),
        "unmapped_industries": {
            label: sorted(set(companies)) for label, companies in sorted(unmapped.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return len(worklist)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
_ARGS = dcli.DeriveArgsSpec(
    apply_help="Write index entities + listed_on_index edges + converged sectors + worklist "
    "(default: dry-run summary).",
    stale_help="No-op for indices: the source is the sidecar (index_sync), "
    "so there is no note-corpus watch path to gate on.",
    corpus=False,
)


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Derive listed_on_index (company -> index) edges from NSE constituents.",
    )
    dcli.add_derive_args(p, _ARGS)
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite target (default: live research.db; use for the ontology-gate parent/candidate copies)",
    )
    args = p.parse_args(argv)

    constituents = load_constituents()
    if not constituents:
        print("index_constituents empty/absent — run `make refresh-indices --apply` first")
        return 0

    conn = connect(args.db or DEFAULT_DB_PATH)
    try:
        entities = conn.execute(
            "SELECT name, entity_type, ticker, sector_classification FROM entities ORDER BY name"
        ).fetchall()
    except Exception:  # noqa: BLE001 — minimal schemas may lack sector_classification
        entities = [
            (r[0], r[1], r[2], None)
            for r in conn.execute(
                "SELECT name, entity_type, ticker FROM entities ORDER BY name"
            ).fetchall()
        ]
    con = duckdb.connect(str(SOURCES_DB), read_only=True)
    try:
        exchange_rows = con.execute(
            "SELECT isin, symbol, exchange FROM exchange_listings WHERE isin IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    edges, index_names, worklist, industry_map = resolve(constituents, entities, exchange_rows)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"constituents={len(constituents)} indices={len(index_names)} "
        f"resolved={len(edges)} worklist={len(worklist)} ({mode})",
        file=sys.stderr,
    )
    try:
        ent_inserted = create_index_entities(conn, index_names, apply=args.apply)
        edge_inserted = apply_typed_edges(
            edges, edge_type=EDGE_TYPE, symmetric=0, conn=conn, dry_run=not args.apply
        )
        conv, unmapped = converge_industry(conn, industry_map, apply=args.apply)
        action = "inserted" if args.apply else "would insert"
        print(
            f"{ent_inserted} index entities {action}; {edge_inserted} {EDGE_TYPE} edges {action}; "
            f"{conv} sector_classification {'filled' if args.apply else 'would fill'}.",
            file=sys.stderr,
        )
        if unmapped:
            print(
                "  WARNING unmapped industry labels parked: "
                + ", ".join(f"{k} ({len(v)})" for k, v in sorted(unmapped.items())),
                file=sys.stderr,
            )
        if args.apply and args.db is None:
            write_worklist(worklist, unmapped)
            print(f"worklist written: {WORKLIST} ({len(worklist)})", file=sys.stderr)
        elif args.apply:
            print("  --db target: live worklist NOT written (disposable copy run)", file=sys.stderr)
        if args.verbose:
            dcli.dump_edges_verbose(edges)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli())
