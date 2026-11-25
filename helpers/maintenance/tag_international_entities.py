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

The store is opened through the house door (:func:`helpers.core.db.connect`),
never a raw ``duckdb.connect``: the research store resolves to its DuckDB
file (``memory/research.duckdb``; ``FINDATA_RESEARCH_DB_PATH`` is honoured)
and the retired ``memory/research.db`` path is never (re)created as an
empty store. Before P4 (2026-11-24) the default ``--db`` was that retired
path, so a default run opened a brand-new empty database there and died
with ``Catalog Error: Table with name entities does not exist``.

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

from helpers.core import db  # noqa: E402
from helpers.core.vec_search import _is_duckdb_conn  # noqa: E402

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

_TAGGED_AMONG_PREFIX = "SELECT entity_name FROM entity_tags WHERE tag = ? AND entity_name IN ("

# DuckDB write shape (proposal sync_tags_duckdb_setbased P4): one statement
# for every candidate still missing the tag. ``executemany`` on DuckDB is one
# round trip per row — measured 2026-11-24 on a copy of the live store:
# 150 rows ≈ 0.84–1.06 s (≈6 ms/row) versus ≈0.016 s here, same 150 rows.
# NOT EXISTS keeps it idempotent and additive (other ``geography/*`` tags
# are never touched).
_INSERT_MISSING_SQL = (
    "INSERT INTO entity_tags (entity_name, tag) "
    "SELECT DISTINCT u.name, ? FROM (SELECT unnest(?) AS name) u "
    "WHERE NOT EXISTS ("
    "SELECT 1 FROM entity_tags t WHERE t.entity_name = u.name AND t.tag = ?"
    ")"
)


def _in_marks(n: int) -> str:
    """``?,?,…`` for ``n`` bound values.

    ``IN (?,?,…)`` is the one list shape both DuckDB and the SQLite rollback
    store understand — ``unnest(?)`` list binding is DuckDB-only (until P4 the
    lookup here was, so the helpers could not run on a SQLite connection).
    Placeholders only: every value is bound, nothing is spliced.
    """
    return ",".join(["?"] * n)


def collect_international_candidates(conn) -> list[str]:
    """Entity names with a non-Indian ticker venue (sorted)."""
    return [r[0] for r in conn.execute(_INTERNATIONAL_CANDIDATES_SQL).fetchall()]


def _tagged_among(conn, names: list[str]) -> set[str]:
    """The subset of ``names`` that already carries ``INTERNATIONAL_TAG``."""
    sql = _TAGGED_AMONG_PREFIX + _in_marks(len(names)) + ")"
    return {r[0] for r in conn.execute(sql, [INTERNATIONAL_TAG, *names]).fetchall()}


def already_tagged(conn, names: list[str]) -> int:
    if not names:
        return 0
    return len(_tagged_among(conn, names))


def apply_tags(conn, names: list[str]) -> int:
    """Insert the international tag for every candidate missing it; return added."""
    if not names:
        return 0
    existing = _tagged_among(conn, names)
    missing = [n for n in names if n not in existing]
    if not missing:
        return 0
    if _is_duckdb_conn(conn):
        conn.execute(_INSERT_MISSING_SQL, [INTERNATIONAL_TAG, missing, INTERNATIONAL_TAG])
    else:
        conn.executemany(
            "INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)",
            [(n, INTERNATIONAL_TAG) for n in missing],
        )
    conn.commit()
    return len(missing)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--db",
        default=None,
        help=(
            "research store; default = the canonical store via helpers.core.db "
            "(memory/research.duckdb, FINDATA_RESEARCH_DB_PATH honoured)"
        ),
    )
    ap.add_argument("--apply", action="store_true", help="write the tags")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else None
    if db_path is not None and not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path
    # House door, read-only unless applying: the selector resolves the
    # research store to its DuckDB file (sibling redirect), so the retired
    # ``memory/research.db`` path is never opened — let alone created.
    conn = db.connect(db_path, read_only=not args.apply)
    try:
        names = collect_international_candidates(conn)
        have = already_tagged(conn, names)
        missing = len(names) - have
        print(f"international candidates: {len(names)} (already tagged {have}, missing {missing})")
        if args.apply:
            added = apply_tags(conn, names)
            print(f"applied geography/international to {added} entit(ies)")
        else:
            print(f"dry-run: {missing} would be tagged (rerun with --apply)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
