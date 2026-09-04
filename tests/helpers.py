"""Shared test scaffolding (consolidation: single source of truth).

Plain module functions (importable without pytest fixture injection) for
the three mechanical clusters: production-DB copy-then-prune, Flask
test_client monkeypatching, and Row-factory connections. Each helper
reproduces the exact statements the per-file copies used — same table
list, same row factory, same restore order.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterator

# Derived tables wiped by the copy-production-DB pattern (the 9-table
# tuple shared verbatim, S608-suppressed at the DELETE site below).
DERIVED_TABLES = (
    "graph_edges",
    "entity_tags",
    "graph_analytics",
    "events",
    "quotes",
    "company_metrics",
    "company_embeddings",
    "note_search",
    "note_search_meta",
)

# snapshot_cycle's 8-table subset (drops note_search_meta for size).
DERIVED_TABLES_NO_FTS_META = tuple(t for t in DERIVED_TABLES if t != "note_search_meta")


def copy_production_db(
    src_db: Path | str,
    dst_path: Path | str,
    *,
    tables: tuple[str, ...] = DERIVED_TABLES,
    vacuum: bool = False,
    keep_all: bool = False,
) -> Path:
    """Backup the production DB, prune derived tables + entities.

    Args:
        src_db: live DB file (callers pass their ``DB_PATH``).
        dst_path: destination file.
        tables: derived-table list to DELETE (snapshot_cycle drops
            ``note_search_meta`` — pass the 8-table subset).
        vacuum: compact freed pages (snapshot_cycle's ~100KB goal).
        keep_all: pure copy — skip all DELETEs (query_plans,
            rebuild_schema fixtures need the full live corpus).
    """
    dst = Path(dst_path)
    src = sqlite3.connect(str(src_db))
    dst_conn = sqlite3.connect(str(dst))
    src.backup(dst_conn)
    src.close()
    try:
        if keep_all:
            return dst
        for t in tables:
            dst_conn.execute(f"DELETE FROM {t}")  # noqa: S608  # schema-constant identifiers
        dst_conn.execute("DELETE FROM entities")
        dst_conn.commit()
        if vacuum:
            dst_conn.execute("VACUUM")
            dst_conn.commit()
    finally:
        dst_conn.close()
    return dst


def open_conn(db_path: Path | str) -> sqlite3.Connection:
    """Fresh Row-factory connection (matches production)."""
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def response_count(resp) -> int:
    """``total_count`` from a paginated API response."""
    return resp.get_json()["total_count"]


def response_names(resp, collection: str, key: str) -> list:
    """Sorted ``[item[key] for item in get_json()[collection]]``."""
    return sorted(item[key] for item in resp.get_json()[collection])


@contextmanager
def flask_test_client(
    db_path: Path | str,
    *,
    connect_fn: Callable[[], sqlite3.Connection] | None = None,
    track_conns: bool = False,
) -> Iterator[Any]:
    """Yield a Flask test_client with get_db_connection patched to db_path.

    Args:
        db_path: temp DB file.
        connect_fn: custom opener (default: Row-factory ``open_conn``).
            The one Row-less site passes its bare connect explicitly —
            centralizing it to Row would be a semantic change.
        track_conns: close every opened connection on teardown (the
            ts_contract/graph_algorithms/conftest discipline).
    """
    import app as A  # lazy: avoid Flask-app startup at collection time

    opener = connect_fn or (lambda: open_conn(db_path))
    _open_conns: list[sqlite3.Connection] = []

    def _open() -> sqlite3.Connection:
        c = opener()
        if track_conns:
            _open_conns.append(c)
        return c

    saved = A.get_db_connection
    A.get_db_connection = _open  # ty: ignore[invalid-assignment]
    try:
        yield A.app.test_client()
    finally:
        A.get_db_connection = saved
        for c in _open_conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
