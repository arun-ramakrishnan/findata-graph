"""DB access drivers for the Mojo bridge bench: SQLite (research.db —
FTS5 + relational) and DuckDB (graph.duckdb — vertices/edges/embeddings).

Same shape as the regex battery: cases() exposes (name, callable) pairs;
the Mojo probe (db_access_probe.mojo) runs every case BOTH natively in
CPython and from Mojo through the bridge, consuming rows on the Mojo
side (repr checksum) — that marshaling cost is the thing being measured.
Connections are read-only and module-lazy.
"""

from __future__ import annotations

import sqlite3
import time

import duckdb

from bridge_utils import (  # Mojo/bench is on sys.path for every importer
    RESEARCH_DB,
    connect_duckdb_ro,
    connect_sqlite_ro,
    sum_rows,
)

_sq: sqlite3.Connection | None = None
_dk: duckdb.DuckDBPyConnection | None = None


def _sqlite() -> sqlite3.Connection:
    global _sq
    if _sq is None:
        _sq = connect_sqlite_ro(RESEARCH_DB, row_factory=sqlite3.Row, query_only=True)
    return _sq


def _duck() -> duckdb.DuckDBPyConnection:
    global _dk
    if _dk is None:
        _dk = connect_duckdb_ro()
    return _dk


def fts5_search(limit=20):
    """The production hybrid-search path: BM25-ranked FTS5 MATCH."""
    return (
        _sqlite()
        .execute(
            "SELECT file_path, bm25(note_search) FROM note_search "
            "WHERE note_search MATCH ? ORDER BY rank LIMIT ?",
            ("revenue growth", limit),
        )
        .fetchall()
    )


def graph_edges(limit=200):
    """Relational slice: one edge type with its JSON properties column."""
    return (
        _sqlite()
        .execute(
            "SELECT source, target, edge_type, weight, properties "
            "FROM graph_edges WHERE edge_type='competes_with' LIMIT ?",
            (limit,),
        )
        .fetchall()
    )


def quotes(limit=200):
    """Text-heavy rows (quote_text + paraphrase)."""
    return (
        _sqlite()
        .execute(
            "SELECT entity, quote_text, paraphrase, speaker_name FROM quotes LIMIT ?", (limit,)
        )
        .fetchall()
    )


def company_metrics(limit=500):
    """Numeric/label rows."""
    return (
        _sqlite()
        .execute(
            "SELECT entity, metric_label, value_num, unit, period FROM company_metrics LIMIT ?",
            (limit,),
        )
        .fetchall()
    )


def duckdb_edges(limit=200):
    """DuckDB undirected-edge scan."""
    return (
        _duck()
        .execute("SELECT a_id, b_id, edge_type, valid_from FROM e_all_und LIMIT ?", [limit])
        .fetchall()
    )


def duckdb_wide_embeddings(limit=200):
    """WIDE rows — 384-float embedding as a Python list (~8 KB/row): the
    marshaling sensitivity case."""
    return (
        _duck()
        .execute("SELECT company_name, embedding FROM v_embeddings LIMIT ?", [limit])
        .fetchall()
    )


def cases():
    return [
        ("fts5_search", fts5_search),
        ("graph_edges", graph_edges),
        ("quotes", quotes),
        ("company_metrics", company_metrics),
        ("duckdb_edges", duckdb_edges),
        ("duckdb_wide_embeddings", duckdb_wide_embeddings),
    ]


def ncases():
    return len(cases())


def bench_native(reps=50):
    """name -> (rows, checksum, elapsed_s) computed natively in CPython."""
    out = {}
    for name, fn in cases():
        t0 = time.perf_counter()
        checksum = 0
        nrows = 0
        for _ in range(reps):
            rows = fn()
            nrows = len(rows)
            checksum = sum_rows(rows)
        out[name] = (nrows, checksum, time.perf_counter() - t0)
    return out


_NATIVE: dict | None = None


def _native(reps=50):
    """Cached bench_native(reps) — accessors below feed the Mojo probe."""
    global _NATIVE
    if _NATIVE is None:
        _NATIVE = bench_native(reps)
    return _NATIVE


def checksum_of(name):
    return _native()[name][1]


def elapsed_of(name):
    return _native()[name][2]


def bench_report(reps=50):
    lines = [f"python native, {reps} reps/case:"]
    for name, (nrows, checksum, dt) in bench_native(reps).items():
        lines.append(
            f"  {name:24s} rows={nrows:4d} checksum={checksum:>9d} "
            f"elapsed={dt:.3f}s ({reps * nrows / dt:,.0f} rows/s)"
        )
    return "\n".join(lines)
