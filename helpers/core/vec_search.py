#!/usr/bin/env python3
"""
sqlite-vec KNN support for the note_search hybrid ranking (A1).

The FTS5 ``note_search`` table stores per-doc embeddings as JSON text and
historically ranked them by computing cosine similarity row-by-row in Python
over the BM25 candidate page. This module adds a ``vec0`` virtual table
(``note_search_vec``) that mirrors those embeddings as native float32 blobs
and answers global top-K nearest-neighbour queries in C.

Why a mirror table (not a migration): the JSON column stays the source of
truth (snapshot export, rebuild incremental diffing, and the Python fallback
path all keep working untouched); the vec table is a derived index, exactly
like the FTS5 shadow tables. It is excluded from Parquet snapshots for the
same reason — it is rebuilt, not shipped.

The mirror lives in a SIDECAR SQLite database (``<main>_vec.db``, ATTACHed
as ``vecdb``), never in research.db itself: DuckDB's SQLite scanner cannot
catalog-scan a database containing vec0 virtual tables ("no such module:
vec0" on ATTACH — the same regression class as spellfix1 tables). The
sidecar is derived state: safe to delete, rebuilt by rebuild_note_search
or lazily backfilled on the first hybrid search.

Three entry points:

- ``vec_available(conn)``     — extension load + table present (read path gate)
- ``knn_similarities(...)``   — global top-K, returns {file_path: similarity}
- ``sync_vec_table(conn, ...)`` — write path for rebuild_note_search (full
  refresh, incremental upsert/delete, or lazy backfill from the JSON column)

Everything here is best-effort: any failure (missing package, missing
extension, older sqlite) returns the "unavailable" value so callers fall
back to the pre-A1 Python cosine path. Hybrid search must never 500 because
a vector index is absent.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# Repo root: helpers/core/vec_search.py -> parents[2]. Must be on sys.path
# BEFORE any `from helpers.core.db import ...` so the script works as a
# subprocess (python3 helpers/core/vec_search.py) the same way it works
# under pytest. (Mirrors the rebuild_note_search.py bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import struct  # noqa: E402  # after sys.path bootstrap
from collections.abc import Iterable, Sequence  # noqa: E402

VEC_TABLE = "note_search_vec"
# The vec0 virtual table must NOT live in research.db itself: DuckDB's
# SQLite scanner chokes on extension virtual tables during ATTACH catalog
# scans ("no such module: vec0" — same failure class as spellfix1, see
# memory: spellfix1_tables_must_not_live_in_research_db). It lives in a
# sidecar database attached to the same connection as schema ``vecdb``.
VEC_SCHEMA = "vecdb"

# Dims of an existing vec0 table, parsed from its DDL ("... FLOAT[384] ...").
_DIM_RE = re.compile(r"FLOAT\[(\d+)\]")


def qualified() -> str:
    """Schema-qualified vec table name (``vecdb.note_search_vec``)."""
    return f"{VEC_SCHEMA}.{VEC_TABLE}"


def _sidecar_path(conn: sqlite3.Connection) -> str:
    """Sidecar DB path derived from the connection's main file.

    ``memory/research.db`` -> ``memory/research.db_vec.db``. In-memory or
    temporary connections get an anonymous in-memory sidecar so tests and
    throwaway conns stay isolated.
    """
    for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
        if name == "main" and file:
            return str(Path(file).with_name(Path(file).name + "_vec.db"))
    return ":memory:"


def _attach_vec_db(conn: sqlite3.Connection) -> None:
    """Idempotently ATTACH the sidecar DB as ``vecdb`` on this connection."""
    have = conn.execute(
        "SELECT 1 FROM pragma_database_list WHERE name = ?", (VEC_SCHEMA,)
    ).fetchone()
    if have:
        return
    path = _sidecar_path(conn)
    if path == ":memory:":
        conn.execute(f"ATTACH DATABASE ':memory:' AS {VEC_SCHEMA}")
    else:
        conn.execute(f"ATTACH DATABASE ? AS {VEC_SCHEMA}", (path,))

# Keep module importable (and unit-testable) without the package installed.
try:  # pragma: no cover - exercised implicitly via vec_available()
    import sqlite_vec

    _EXTENSION_PATH: str | None = sqlite_vec.loadable_path()
except ImportError:  # pragma: no cover
    _EXTENSION_PATH = None


def _load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension onto ``conn``; False when impossible."""
    if _EXTENSION_PATH is None:
        return False
    try:
        conn.enable_load_extension(True)  # type: ignore[attr-defined]
        conn.load_extension(_EXTENSION_PATH)
        conn.enable_load_extension(False)  # type: ignore[attr-defined]
    except (sqlite3.Error, AttributeError):
        return False
    return True


def _pack(vec: Sequence[float]) -> bytes:
    """Serialize a float vector to the little-endian float32 blob vec0 wants."""
    return struct.pack(f"{len(vec)}f", *vec)


def _attach_ok(conn: sqlite3.Connection) -> bool:
    """Best-effort sidecar attach; False when the main DB is unusable."""
    try:
        _attach_vec_db(conn)
    except sqlite3.Error:
        return False
    return True


def _table_exists(conn: sqlite3.Connection) -> bool:
    _attach_vec_db(conn)
    row = conn.execute(
        f"SELECT 1 FROM {VEC_SCHEMA}.sqlite_master WHERE type='table' AND name = ?",  # noqa: S608  # schema/name constants
        (VEC_TABLE,),
    ).fetchone()
    return row is not None


def _create_table(conn: sqlite3.Connection, dims: int) -> bool:
    """Create the vec0 table; False when the extension is unavailable."""
    if not _load_vec_extension(conn):
        return False
    _attach_vec_db(conn)
    try:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {qualified()} "  # noqa: S608  # identifier is the qualified() constant above
            "USING vec0(file_path TEXT PRIMARY KEY, "
            f"embedding FLOAT[{int(dims)}] distance_metric=cosine)"
        )
    except sqlite3.Error:
        return False
    return True


def stored_dims(conn: sqlite3.Connection) -> int | None:
    """Dims of the existing vec0 table, parsed from its DDL (None if absent).

    Read-path gate for hybrid search: the query vector must live in the same
    vector space as the mirrored rows. An embedding-model swap (pseudo 64 →
    bge 384) changes the DDL; callers compare this against len(q_vec) and
    degrade to BM25-only on mismatch instead of computing garbage cosine.
    """
    if not _attach_ok(conn):
        return None
    try:
        row = conn.execute(
            f"SELECT sql FROM {VEC_SCHEMA}.sqlite_master "  # noqa: S608  # schema/name constants
            "WHERE type='table' AND name = ?",
            (VEC_TABLE,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    m = _DIM_RE.search(row[0])
    return int(m.group(1)) if m else None


def backfill_from_fts(conn: sqlite3.Connection, dims: int) -> int:
    """Populate the vec table from note_search's JSON embedding column.

    Returns the number of rows written (0 when nothing to do or on failure).
    Used both by the rebuild write path and lazily on first hybrid search
    after a snapshot restore (the vec table is snapshot-excluded by design).
    """
    if not _attach_ok(conn):
        return 0
    try:
        rows = conn.execute(
            "SELECT file_path, embedding FROM note_search "
            "WHERE embedding IS NOT NULL AND embedding != ''"
        ).fetchall()
    except sqlite3.Error:
        return 0
    written = 0
    for file_path, embedding_json in rows:
        try:
            vec = json.loads(embedding_json)
            if not isinstance(vec, list) or len(vec) != dims:
                continue
        except (TypeError, ValueError):
            continue
        try:
            conn.execute(f"DELETE FROM {qualified()} WHERE file_path = ?", (file_path,))  # noqa: S608  # qualified() constant
            conn.execute(
                f"INSERT INTO {qualified()} (file_path, embedding) VALUES (?, ?)",  # noqa: S608  # qualified() constant
                (file_path, _pack(vec)),
            )
            written += 1
        except sqlite3.Error:
            continue
    return written


def vec_available(conn: sqlite3.Connection, dims: int, *, lazy_backfill: bool = False) -> bool:
    """True when the KNN path can serve queries on this connection.

    ``lazy_backfill=True`` (used by the search read path) creates the table
    and backfills it from the JSON column when absent — a one-time cost on
    the first hybrid query after a rebuild-from-scratch or snapshot restore.
    """
    if not _load_vec_extension(conn):
        return False
    if not _table_exists(conn):
        if not lazy_backfill:
            return False
        if not _create_table(conn, dims):
            return False
        conn.commit()
        backfill_from_fts(conn, dims)
        conn.commit()
    n = conn.execute(f"SELECT COUNT(*) FROM {qualified()}").fetchone()[0]  # noqa: S608  # qualified() constant
    return n > 0


def knn_similarities(
    conn: sqlite3.Connection,
    q_vec: Sequence[float],
    k: int | None,
    dims: int,
) -> dict[str, float] | None:
    """Cosine neighbours of ``q_vec``; ``k=None`` covers the whole corpus.

    Returns ``{file_path: similarity}`` (similarity = 1 - cosine distance,
    the raw cosine — negative for anti-correlated docs, matching the Python
    path's contract), best first, or ``None`` when the KNN path is
    unavailable — the caller
    then falls back to the Python cosine loop.

    ``k=None`` (the hybrid-search usage) sizes KNN to the table so EVERY
    indexed doc carries its exact similarity: the re-ranked BM25 page is a
    subset of the corpus, and a bounded k would zero out page docs that
    fall outside the global top-k, silently changing their scores. At
    corpus scale (~1k docs) the full scan is still a sub-millisecond C
    loop. An explicit int k is clamped to at least 1; asking for more rows
    than exist returns all of them (vec0 semantics).
    """
    if len(q_vec) != dims:
        return None
    if not vec_available(conn, dims, lazy_backfill=True):
        return None
    if k is None:
        k = conn.execute(f"SELECT COUNT(*) FROM {qualified()}").fetchone()[0]  # noqa: S608  # qualified() constant
    if k < 1:
        return None
    try:
        hits = conn.execute(
            f"SELECT file_path, distance FROM {qualified()} "  # noqa: S608  # qualified() constant
            "WHERE embedding MATCH ? AND k = ?",
            (_pack(q_vec), int(k)),
        ).fetchall()
    except sqlite3.Error:
        return None
    out: dict[str, float] = {}
    for file_path, distance in hits:
        try:
            out[str(file_path)] = 1.0 - float(distance)  # raw cosine, no clamp
        except (TypeError, ValueError):
            continue
    return out


def _upsert_vec_rows(
    conn: sqlite3.Connection,
    dims: int,
    upsert_rows: Iterable[tuple[str, str | None]],
) -> int:
    """Apply (file_path, embedding_json) deltas; a None/invalid embedding
    removes the vec row (mirrors the "searchable but unembeddable" FTS
    behaviour). Returns rows written."""
    written = 0
    for file_path, embedding_json in upsert_rows:
        conn.execute(
            f"DELETE FROM {qualified()} WHERE file_path = ?", (file_path,)  # noqa: S608  # qualified() constant
        )
        if not embedding_json:
            continue
        try:
            vec = json.loads(embedding_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(vec, list) or len(vec) != dims:
            continue
        conn.execute(
            f"INSERT INTO {qualified()} (file_path, embedding) VALUES (?, ?)",  # noqa: S608  # qualified() constant
            (file_path, _pack(vec)),
        )
        written += 1
    return written


def sync_vec_table(
    conn: sqlite3.Connection,
    dims: int,
    *,
    upsert_rows: Iterable[tuple[str, str | None]] = (),
    delete_paths: Iterable[str] = (),
    full: bool = False,
) -> int:
    """Write path for rebuild_note_search: keep the vec table in sync.

    - ``full=True``      — drop everything and re-mirror the FTS embedding
      column (used by the non-incremental rebuild).
    - ``upsert_rows``    — (file_path, embedding_json) pairs whose FTS row
      changed; a None/invalid embedding removes the vec row (mirrors the
      "searchable but unembeddable" FTS behaviour).
    - ``delete_paths``   — files that disappeared from disk.

    Returns the number of vec rows written. Best-effort: any extension or
    schema failure returns 0 without raising — the JSON column path in
    rebuild/app remains the source of truth.
    """
    if not _create_table(conn, dims):
        return 0
    # Embedding-model swap (pseudo 64 -> bge 384): the existing vec0 table
    # pins the OLD dims (CREATE ... IF NOT EXISTS is a no-op), so every new
    # insert would fail its dimension check per-row and the stale vectors
    # would keep serving KNN. Detect the mismatch and rebuild the derived
    # index at the new dims — it is a mirror, never a source of truth.
    existing = stored_dims(conn)
    if existing is not None and existing != dims:
        with conn:
            conn.execute(f"DROP TABLE {qualified()}")  # noqa: S608  # qualified() constant
        if not _create_table(conn, dims):
            return 0
        with conn:
            written = backfill_from_fts(conn, dims)
        return written
    written = 0
    try:
        with conn:
            if full:
                conn.execute(f"DELETE FROM {qualified()}")  # noqa: S608  # qualified() constant
                written = backfill_from_fts(conn, dims)
            for file_path in delete_paths:
                conn.execute(
                    f"DELETE FROM {qualified()} WHERE file_path = ?", (file_path,)  # noqa: S608  # qualified() constant
                )
            written += _upsert_vec_rows(conn, dims, upsert_rows)
    except sqlite3.Error:
        return written
    if not full and written == 0:
        # Incremental with an empty delta (first run after adding A1, or
        # right after a snapshot restore) leaves a bare table — mirror the
        # whole FTS embedding column once so stats report real coverage.
        n = conn.execute(f"SELECT COUNT(*) FROM {qualified()}").fetchone()[0]  # noqa: S608  # qualified() constant
        if n == 0:
            with conn:
                written = backfill_from_fts(conn, dims)
    return written


def main() -> int:  # pragma: no cover - manual diagnostic
    """CLI: report vec-table health for a research.db path (default memory/)."""
    import argparse

    p = argparse.ArgumentParser(description="sqlite-vec note_search index status")
    p.add_argument("db", nargs="?", default=str(Path("memory") / "research.db"))
    p.add_argument("--dims", type=int, default=64)
    args = p.parse_args()
    from helpers.core.db import connect

    conn = connect(args.db)
    try:
        ok = vec_available(conn, args.dims, lazy_backfill=False)
        n = conn.execute(
            f"SELECT COUNT(*) FROM {qualified()}"  # noqa: S608  # identifier is the qualified() constant
        ).fetchone()[0] if ok else 0
        fts = conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
        print(f"extension+table: {'ok' if ok else 'unavailable'}")
        print(f"vec rows: {n}  (fts rows: {fts})")
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
