#!/usr/bin/env python3
"""S18(b/c/e) — canonical Arrow incidence loader (architecture.md §10).

The one loader every hypergraph consumer goes through: long-form incidence
(``edge_type, label, node, weight`` — weight nullable) as a pyarrow Table,
straight off whichever at-rest source is authoritative:

- ``source="live"`` — SQLite via DuckDB's sqlite scanner (ATTACH READ_ONLY,
  ``to_arrow_table`` zero-copy). SQLite stays the live source of truth.
- ``source="snapshot"`` — the zstd parquet snapshots under
  ``snapshots/parquet/sqlite/`` via pyarrow, joined IN FLIGHT with DuckDB
  ``from_arrow`` (no h_* materialization, no SQLite touched).

Consumers (§10 in-flight rule — Arrow until the last boundary):
- ``incidence_query`` — SQL over the incidence table in flight
  (DuckDB registered Arrow; the table never lands in any store).
- ``hypergraph_from_arrow`` — HGX boundary: ``to_pylist()`` exactly once,
  then ``Hypergraph`` construction (hypergraphx is the only tier that
  wants Python objects by design).

The legacy ``load_incidence`` dict (hyper_communities) is a thin wrapper
over this module (same prefixed-label contract, deterministic order).

Usage::

    from helpers.graph.hyper_arrow import load_incidence_arrow

    tbl = load_incidence_arrow(["sector", "sub_sector"])              # live
    tbl = load_incidence_arrow(["edition"], source="snapshot")        # parquet
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import DEFAULT_DB_PATH  # noqa: E402

SNAPSHOT_DIR = PROJECT_ROOT / "snapshots" / "parquet" / "sqlite"
HYPER_EDGES_PARQUET = "hyper_edges.parquet"
HYPER_INCIDENCES_PARQUET = "hyper_incidences.parquet"

_COLUMNS = "he.edge_type, he.label, hi.entity_name AS node, hi.weight"


def _in_clause(sources: list[str]) -> str:
    return "(" + ", ".join("?" for _ in sources) + ")"


def _from_live(sources: list[str], db_path: Path | str) -> pa.Table:
    """SQLite (read-only) through DuckDB's sqlite scanner → Arrow."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{db_path}' AS src (TYPE SQLITE, READ_ONLY)")
        rel = con.execute(
            f"SELECT {_COLUMNS} "
            "FROM src.hyper_incidences hi JOIN src.hyper_edges he ON he.id = hi.edge_id "
            f"WHERE he.edge_type IN {_in_clause(sources)} "
            "ORDER BY he.edge_type, he.label, hi.entity_name",
            sources,
        )
        return rel.to_arrow_table()
    finally:
        con.close()


def _from_snapshot(sources: list[str], parquet_dir: Path) -> pa.Table:
    """zstd parquet snapshots via pyarrow, joined in flight (zero SQLite)."""
    import duckdb
    import pyarrow.parquet as pq

    edges = pq.read_table(parquet_dir / HYPER_EDGES_PARQUET)
    incs = pq.read_table(parquet_dir / HYPER_INCIDENCES_PARQUET)
    con = duckdb.connect()
    try:
        con.register("e", edges)
        con.register("i", incs)
        rel = con.execute(
            f"SELECT {_COLUMNS} "
            "FROM i AS hi JOIN e AS he ON he.id = hi.edge_id "
            f"WHERE he.edge_type IN {_in_clause(sources)} "
            "ORDER BY he.edge_type, he.label, hi.entity_name",
            sources,
        )
        return rel.to_arrow_table()
    finally:
        con.close()


def load_incidence_arrow(
    sources: list[str],
    *,
    source: str = "live",
    db_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
) -> pa.Table:
    """Long-form incidence table: ``edge_type, label, node, weight``.

    ``weight`` is nullable (NULL = unweighted incidence; today only the S8
    edition lane sets quote-count weights). Rows are deterministic:
    ordered by (edge_type, label, node).
    """
    if not sources:
        return pa.table(
            {
                "edge_type": pa.array([], pa.string()),
                "label": pa.array([], pa.string()),
                "node": pa.array([], pa.string()),
                "weight": pa.array([], pa.float64()),
            }
        )
    if source == "live":
        return _from_live(sources, Path(db_path) if db_path else DEFAULT_DB_PATH)
    if source == "snapshot":
        return _from_snapshot(sources, Path(parquet_dir) if parquet_dir else SNAPSHOT_DIR)
    msg = f"source must be 'live' or 'snapshot', got {source!r}"
    raise ValueError(msg)


def incidence_query(table: pa.Table, sql: str, params: list | None = None) -> pa.Table:
    """SQL over the incidence Arrow table, in flight (nothing lands anywhere).

    The table is registered as ``incidence``; DuckDB's registered-Arrow
    scan reads the caller's buffers zero-copy (§10 in-flight rule).
    """
    import duckdb

    con = duckdb.connect()
    try:
        con.register("incidence", table)
        rel = con.execute(sql, params or [])
        return rel.to_arrow_table()
    finally:
        con.close()


def hypergraph_from_arrow(table: pa.Table):
    """HGX ``Hypergraph`` from the incidence table — the to_pylist boundary.

    hypergraphx wants Python objects; this is the single sanctioned
    conversion point (§10: Arrow in flight, ``to_pylist()`` at the
    boundary). Unweighted by construction, mirroring the S1-S13 lanes —
    per-incidence weights are per (edge, member), not per edge, so the
    S14 weighted walk composes them itself after loading node sets.

    Returns ``(hypergraph, labels)`` where ``labels`` maps each edge's
    frozen member tuple to its ``"edge_type:label"`` prefixed name (the
    legacy load_incidence contract).
    """
    from hypergraphx import Hypergraph

    grouped: dict[str, set[str]] = {}
    for r in table.to_pylist():
        grouped.setdefault(f"{r['edge_type']}:{r['label']}", set()).add(r["node"])
    edge_list = [tuple(sorted(m)) for m in grouped.values()]
    labels = {tuple(sorted(m)): p for p, m in grouped.items()}
    return Hypergraph(edge_list=edge_list), labels
