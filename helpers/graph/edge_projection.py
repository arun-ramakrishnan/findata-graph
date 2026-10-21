#!/usr/bin/env python3
"""The SQLite edge projection: stored dyads UNION the retired sector pair.

part_of/has_company retirement (2026-10-19, completed.md #418): the pair
no longer has ``graph_edges`` rows. Membership is CANONICAL in
``hyper_edges``/``hyper_incidences`` (edge_type ``'sector'``), and every
reader that wants the pair must project it back out.

``helpers/graph/query.py`` does that for the DuckDB lane
(``_materialise_sector_membership``) and ``helpers/graph/onager.py`` for
Onager (``_edge_source_sql``). The SQLite lanes each built their own
adjacency from the raw table, so each one that skipped the projection
silently LOST sector-only companies — the failure mode is not a wrong
number, it is a ``KeyError: contract names missing from the projection``
in the centrality pre-flight, or a shortest path that cannot hop sectors.
Measured: Katz parity vs fresh Onager fell 0.9999 -> 0.9949 while only
one side of the comparison projected the pair.

This module is the single SQLite-side source. Callers get
``(source, target, edge_type, weight)`` rows and decide their own
filtering (index noise, self-loops, dedupe).

Two tolerances, both deliberate:

* **Absent incidence store** — a pre-retirement restore (or a fixture
  that models only the dyads) degrades to the plain read. Probe
  ``sqlite_master``; do NOT assume the store exists.
* **Narrow ``graph_edges``** — several fixtures declare only
  ``(source, target)`` or ``(source, target, weight)``. A missing
  ``edge_type`` reads as ``''`` (never ``'listed_on_index'``, so the
  centrality exclusion stays a no-op there) and a missing weight as
  ``1.0``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

#: The two labels the projection synthesises, in the order readers expect.
PROJECTED_TYPES: tuple[str, str] = ("part_of", "has_company")


def has_incidence_store(con: sqlite3.Connection) -> bool:
    """True when this store carries the canonical sector membership tables."""
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='hyper_incidences' AND type='table'"
        ).fetchone()
        is not None
    )


def edge_rows(con: sqlite3.Connection) -> list[tuple[str, str, str, float]]:
    """``(source, target, edge_type, weight)`` — stored dyads + projected pair.

    The projected rows carry the incidence weight when set, else the
    hyperedge's, so a weighted lane sees one consistent number per
    membership. Both directions are emitted (``company -> sector`` as
    ``part_of``, ``sector -> company`` as ``has_company``), matching what
    ``helpers/graph/query.py`` serves to the DuckDB consumers.
    """
    cols = {r[1] for r in con.execute("PRAGMA table_info('graph_edges')").fetchall()}
    et_col = "edge_type" if "edge_type" in cols else "''"
    w_col = "weight" if "weight" in cols else "1.0"
    if not has_incidence_store(con):
        return con.execute(
            f"SELECT source, target, {et_col}, COALESCE({w_col}, 1.0) FROM graph_edges"  # noqa: S608
        ).fetchall()
    return con.execute(
        f"SELECT source, target, {et_col}, COALESCE({w_col}, 1.0) FROM graph_edges "  # noqa: S608
        "UNION ALL "
        "SELECT hi.entity_name, he.label, 'part_of', "
        "COALESCE(hi.weight, he.weight, 1.0) "
        "FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.edge_type = 'sector' "
        "UNION ALL "
        "SELECT he.label, hi.entity_name, 'has_company', "
        "COALESCE(hi.weight, he.weight, 1.0) "
        "FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.edge_type = 'sector'"
    ).fetchall()


def pairs(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Just the endpoints, for lanes that ignore type and weight."""
    return [(s, t) for s, t, _et, _w in edge_rows(con)]


def read(db_path: Any) -> list[tuple[str, str, str, float]]:
    """Convenience: open ``db_path`` read-only, read, close."""
    from helpers.core.db import connect

    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        return edge_rows(con)
    finally:
        con.close()
