#!/usr/bin/env python3
"""Tag international (foreign-domiciled) companies with ``geography/international``.

The store holds tickers from global exchanges (Nasdaq/NYSE, Xetra, KRX,
TSE, HKEX, TSX, Euronext, TWSE, SZSE/SSE, SGX, …) alongside the Indian
NSE/BSE universe. Those entities are the *international* part of the
universe — companies whose voting/ownership centre sits outside India.

This helper applies the ``geography/international`` tag to every entity
whose ticker venue is non-Indian (all other ``geography/*`` tags are left
untouched; the tag is additive and idempotent). Curation scope follows the
repo precedent for India itself: we tag what we know (notes + tickers),
not the whole exchange master universe.

Read-only by default; ``--apply`` writes. Exit 0 always.

Usage:
    python3 helpers/maintenance/tag_international_entities.py
    python3 helpers/maintenance/tag_international_entities.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402

INTERNATIONAL_TAG = "geography/international"

_FOREIGN_TICKER_WHERE = """
    e.ticker IS NOT NULL AND e.ticker <> ''
    AND NOT (e.ticker LIKE '%.NS' OR e.ticker LIKE '%.BO')
"""

_INTERNATIONAL_CANDIDATES_SQL = (
    "SELECT e.name FROM entities e WHERE "  # noqa: S608  # module-level constants spliced; no user input
    + _FOREIGN_TICKER_WHERE.strip()
    + " ORDER BY e.name"
)


def collect_international_candidates(conn) -> list[str]:
    """Entity names with a non-Indian ticker venue (sorted)."""
    return [r[0] for r in conn.execute(_INTERNATIONAL_CANDIDATES_SQL).fetchall()]


def already_tagged(conn, names: list[str]) -> int:
    if not names:
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM entity_tags WHERE tag = ? AND entity_name IN (SELECT unnest(?))",
        [INTERNATIONAL_TAG, names],
    ).fetchone()[0]


def apply_tags(conn, names: list[str]) -> int:
    """Insert the international tag for every candidate missing it; return added."""
    if not names:
        return 0
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT entity_name FROM entity_tags WHERE tag = ? AND entity_name IN (SELECT unnest(?))",
            [INTERNATIONAL_TAG, names],
        ).fetchall()
    }
    missing = [n for n in names if n not in existing]
    if not missing:
        return 0
    conn.executemany(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)",
        [(n, INTERNATIONAL_TAG) for n in missing],
    )
    conn.commit()
    return len(missing)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="memory/research.db")
    ap.add_argument("--apply", action="store_true", help="write the tags")
    args = ap.parse_args(argv)

    import duckdb

    conn = duckdb.connect(args.db, **memory_cap_config())
    names = collect_international_candidates(conn)
    have = already_tagged(conn, names)
    missing = len(names) - have
    print(f"international candidates: {len(names)} (already tagged {have}, missing {missing})")
    if args.apply:
        added = apply_tags(conn, names)
        print(f"applied geography/international to {added} entit(ies)")
    else:
        print(f"dry-run: {missing} would be tagged (rerun with --apply)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
