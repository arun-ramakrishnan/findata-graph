#!/usr/bin/env python3
"""Set-based replacement of ``hyper_incidences`` on the DuckDB store.

The derive-* family rebuilt one edge at a time: one ``DELETE`` and one
``executemany`` INSERT per edge. DuckDB's python ``executemany`` executes
the prepared statement once per row (~1.9 ms on a PK-indexed table —
measured in the sync_tags sweep, doc/improvements/proposals/
sync_tags_duckdb_setbased.md), so a family with hundreds of incidences
spends most of its time in statement overhead.

This helper stages every edge's rows in one Arrow relation and replaces
them with one DELETE (over all staged edges) + one INSERT SELECT. SQLite
callers keep their own per-edge path — per-statement cost there is
microseconds and the multiset/PK semantics are already pinned by tests.

Callers must have created the target ``hyper_edges`` rows first (with
stable ``(edge_type, label)`` keys); the staging join is by that key.
"""

from __future__ import annotations

from helpers.core.vec_search import _is_duckdb_conn  # noqa: E402


def bulk_replace_hyper_incidences(conn, rows: list[tuple]) -> None:
    """Replace the incidences of every staged (edge_type, label) edge.

    ``rows`` are ``(edge_type, label, entity_name, weight, role,
    valid_from)`` tuples; ``weight``/``valid_from`` may be None. Callers
    branch on ``_is_duckdb_conn`` themselves so SQLite keeps its path.
    No SELECT DISTINCT: the old per-row INSERT let the
    ``PRIMARY KEY (edge_id, entity_name)`` reject duplicate members, and
    this keeps that contract.
    """
    if not rows:
        return
    import pyarrow as pa  # lazy: only the DuckDB research store needs it

    rel = pa.table(
        {
            "edge_type": pa.array([str(r[0]) for r in rows]),
            "label": pa.array([str(r[1]) for r in rows]),
            "entity_name": pa.array([str(r[2]) for r in rows]),
            "weight": pa.array(
                [float(r[3]) if r[3] is not None else None for r in rows],
                type=pa.float64(),
            ),
            "role": pa.array([None if r[4] is None else str(r[4]) for r in rows]),
            # DuckDB casts the staged VARCHAR to the target DATE on INSERT,
            # exactly as the old bound-parameter executemany did.
            "valid_from": pa.array([None if r[5] is None else str(r[5]) for r in rows]),
        }
    )
    # helpers.core.db's _DuckDBConn PROXY has no register(); the raw
    # connection does, and registration is session-scoped.
    raw = getattr(conn, "raw", conn)
    raw.register("_incidence_stage", rel)
    try:
        conn.execute(
            "DELETE FROM hyper_incidences WHERE edge_id IN "
            "(SELECT e.id FROM hyper_edges e "
            "JOIN _incidence_stage s "
            "ON e.edge_type = s.edge_type AND e.label = s.label)"
        )
        conn.execute(
            "INSERT INTO hyper_incidences "
            "(edge_id, entity_name, weight, role, valid_from) "
            "SELECT e.id, s.entity_name, s.weight, s.role, s.valid_from "
            "FROM _incidence_stage s "
            "JOIN hyper_edges e "
            "ON e.edge_type = s.edge_type AND e.label = s.label"
        )
    finally:
        raw.unregister("_incidence_stage")


__all__ = ["_is_duckdb_conn", "bulk_replace_hyper_incidences"]
