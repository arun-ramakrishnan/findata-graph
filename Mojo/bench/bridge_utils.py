"""Shared scaffolding for the Mojo/bench Python drivers (consolidation).

Single source of truth for the repo-root bootstrap, the read-only sqlite
connectors, and the ``sum_rows`` checksum oracle previously re-rolled in
mojo_graph_algos.py, mojo_db_access.py, and mojo_db_integrity.py (shared
idiom, not copies — DB paths/flags differ per file, hence the parameters).

Import path: every importer (Mojo probes, leg scripts run with the bench
dir as ``sys.path[0]``) has ``Mojo/bench`` on ``sys.path``, so drivers do
a plain ``from bridge_utils import ...`` — no per-file bootstrap.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

RESEARCH_DB = REPO / "memory" / "research.db"
GRAPH_DUCKDB = REPO / "memory" / "graph.duckdb"


def ensure_repo_on_path() -> None:
    """Add the repo root to sys.path so ``helpers.*`` is importable."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))


def sum_rows(rows) -> int:
    """Deterministic row checksum, computable identically on both sides:
    UTF-8 BYTES of repr(row) — the Mojo side measures byte_length() of
    the same repr string, and codepoint counts would differ on non-ASCII
    quote text (one multi-byte char = +1 byte vs +1 char)."""
    return sum(len(repr(r).encode("utf-8")) for r in rows)


def connect_sqlite_ro(
    db_path: pathlib.Path | str, *, row_factory=None, query_only: bool = False
) -> sqlite3.Connection:
    """Read-only sqlite connect via the shared helper (P0 static check).

    ``row_factory=None`` (the default) keeps raw tuples: ``sum_rows``
    hashes ``repr(row)`` and the Mojo side mirrors Python's tuple repr
    byte-for-byte — the ``sqlite3.Row`` default would change every
    checksum and break parity. Pass ``row_factory=sqlite3.Row`` only
    where the caller consumes ``Row`` objects (never the checksum path).
    """
    ensure_repo_on_path()
    from helpers.core.db import connect

    con = connect(db_path, read_only=True, row_factory=row_factory)
    if query_only:
        con.execute("PRAGMA query_only=ON")
    return con


def connect_duckdb_ro(db_path: pathlib.Path | str | None = None):
    """Read-only DuckDB connect for the graph cache."""
    import duckdb

    return duckdb.connect(str(db_path or GRAPH_DUCKDB), read_only=True)
