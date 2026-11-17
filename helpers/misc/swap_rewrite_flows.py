#!/usr/bin/env python3
"""Cascade rewrite flows for a DuckDB research-store copy (Step 3).

SQLite enforces FK CASCADE/SET NULL inside the engine; DuckDB parse-rejects
those actions (Step 2 manifest), so the rewrite must be re-enacted in code on
a DuckDB copy. These helpers implement the ordered sequences for the
entities-rooted flows, matching SQLite cascade outcomes for identical input.

Each helper takes a DuckDB connection to a *copy* and runs explicit
DELETE/UPDATE statements (ordered, dependency-safe). They are intended for
the swap program fixtures/tests; production rewrite wiring is a later phase.

Flows:
  - drop_entity:   delete an entity, propagating deletes to all FK children
                   (compound chains included) and SET NULL where the edge
                   declares it.
  - rename_entity: update entities.name and propagate to every referencing
                   child column.
"""

from __future__ import annotations

from typing import Any

# Child tables of entities.name, with the action SQLite applies on a delete
# of the parent row (from Step 2 manifest).
ENTITY_CHILDREN: dict[str, tuple[str, str]] = {
    "entity_tags": ("entity_name", "CASCADE"),
    "entity_identifiers": ("entity_name", "CASCADE"),
    "co_mention_editions": ("entity_name", "CASCADE"),
    "events": ("entity", "CASCADE"),
    "quotes": ("entity", "CASCADE"),
    "company_metrics": ("entity", "CASCADE"),
    "ppr_results": ("entity", "CASCADE"),
    "graph_analytics": ("entity_name", "CASCADE"),
    "graph_analytics_contract": ("entity_name", "CASCADE"),
    "hyper_incidences": ("entity_name", "CASCADE"),
}
# children whose delete-cascade is triggered through events.id
EVENT_CHILDREN: dict[str, tuple[str, str]] = {
    "event_participants": ("event_id", "CASCADE"),
}
# children whose delete-cascade is triggered through hyper_edges.id
HYPEREDGE_CHILDREN: dict[str, tuple[str, str]] = {
    "hyper_incidences": ("edge_id", "CASCADE"),
}
# entities-parented SET NULL edges (child table -> (column, parent table))
SET_NULL_ON_DELETE: dict[str, tuple[str, str]] = {
    "events": ("counterparty_entity", "entities"),
    "speaker_identity": ("person_name", "entities"),
}
# graph/hyper edge tables with two entity FK columns each
EDGE_TABLES: dict[str, tuple[str, str]] = {
    "graph_edges": ("source", "target"),
    "hyper_edges": ("source", "target"),
}
# tables whose rows are REFERENCED by children (parent side of an FK).
# DuckDB refuses in-place UPDATE of any FK-participating column on such a
# row (it validates via delete+insert, tripping RESTRICT), so renames must
# clone the row, repoint the children, then delete the original.
REPOINT_CHILDREN: dict[str, list[tuple[str, str]]] = {
    "events": [("event_participants", "event_id")],
    "hyper_edges": [("hyper_incidences", "edge_id")],
}


def _has_column(con, table: str, column: str) -> bool:
    try:
        rows = con.execute(f'DESCRIBE "{table}"').fetchall()
    except Exception:
        return False
    return any(r[0] == column for r in rows)


def _run(con, stmts: list[str], sql: str, params: tuple[Any, ...] = ()) -> None:
    stmts.append(sql)
    con.execute(sql, params)


def _rotate_rows(
    con,
    stmts: list[str],
    table: str,
    rows: list[Any],
    upd_cols: list[str],
    old: str,
    new: str,
) -> None:
    """Update rows of a table that is itself referenced by children (parent
    side of an FK). DuckDB refuses an in-place UPDATE of an FK-participating
    column on a referenced row (validation via delete+insert trips
    RESTRICT). We therefore rotate each affected row: insert a temporary
    clone (per-row unique negative id), move every child's FK onto the
    clone, delete the original, re-insert the original row under its
    ORIGINAL id with the updated columns, move children back, and delete
    the clone. Final ids are exactly preserved (matches SQLite's in-place
    UPDATE result).

    `rows` is the list of parent ids to update. `upd_cols` are the parent
    columns that change value from `old` to `new` (only columns currently
    equal to `old` are rewritten).
    """
    cols = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
    colset = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" for _ in cols)
    id_idx = cols.index("id")
    for idx, pid in enumerate(rows):
        row = con.execute(f'SELECT {colset} FROM "{table}" WHERE "id" = ?', (pid,)).fetchone()
        tid = -(idx + 1)
        temp_vals = list(row)
        temp_vals[id_idx] = tid
        for i, c in enumerate(cols):
            if c in upd_cols:
                temp_vals[i] = new
        _run(con, stmts, f'INSERT INTO "{table}" ({colset}) VALUES ({marks})', tuple(temp_vals))
        for ctab, ccol in REPOINT_CHILDREN.get(table, []):
            _run(con, stmts, f'UPDATE "{ctab}" SET "{ccol}" = ? WHERE "{ccol}" = ?', (tid, pid))
        _run(con, stmts, f'DELETE FROM "{table}" WHERE "id" = ?', (pid,))
        final_vals = list(row)
        for i, c in enumerate(cols):
            if c in upd_cols and final_vals[i] == old:
                final_vals[i] = new
        _run(con, stmts, f'INSERT INTO "{table}" ({colset}) VALUES ({marks})', tuple(final_vals))
        for ctab, ccol in REPOINT_CHILDREN.get(table, []):
            _run(con, stmts, f'UPDATE "{ctab}" SET "{ccol}" = ? WHERE "{ccol}" = ?', (pid, tid))
        _run(con, stmts, f'DELETE FROM "{table}" WHERE "id" = ?', (tid,))


def drop_entity(con, name: str) -> list[str]:
    """Delete entity `name` propagating deletes/set-nulls as SQLite would.

    Returns the ordered list of executed statements (for tests/logging).
    """
    stmts: list[str] = []
    # 1) SET NULL on entities-parented children that declare it
    for table, (col, parent) in SET_NULL_ON_DELETE.items():
        if parent == "entities" and _has_column(con, table, col):
            _run(con, stmts, f'UPDATE "{table}" SET "{col}" = NULL WHERE "{col}" = ?', (name,))
    # 2) edge tables first (they cascade to their own children)
    for table, (scol, tcol) in EDGE_TABLES.items():
        if not _has_column(con, table, scol):
            continue
        for ctab, (ccol, _action) in HYPEREDGE_CHILDREN.items():
            if _has_column(con, ctab, ccol):
                _run(
                    con,
                    stmts,
                    f'DELETE FROM "{ctab}" WHERE "{ccol}" IN '
                    f'(SELECT id FROM "{table}" WHERE "{scol}" = ? OR "{tcol}" = ?)',
                    (name, name),
                )
        _run(con, stmts, f'DELETE FROM "{table}" WHERE "{scol}" = ? OR "{tcol}" = ?', (name, name))
    # 3) event children (participants referencing the entity either directly
    #    or through a deleted event; then the events themselves)
    if _has_column(con, "event_participants", "event_id") and _has_column(
        con, "event_participants", "participant"
    ):
        _run(
            con,
            stmts,
            "DELETE FROM event_participants WHERE participant = ? OR event_id IN "
            "(SELECT id FROM events WHERE entity = ?)",
            (name, name),
        )
    if _has_column(con, "events", "entity"):
        _run(con, stmts, "DELETE FROM events WHERE entity = ?", (name,))
    # 4) remaining direct children (events + hyper_incidences already handled)
    for table, (col, action) in ENTITY_CHILDREN.items():
        if table in ("events", "hyper_incidences"):
            continue
        if action == "CASCADE" and _has_column(con, table, col):
            _run(con, stmts, f'DELETE FROM "{table}" WHERE "{col}" = ?', (name,))
    # 5) the entity row itself
    _run(con, stmts, "DELETE FROM entities WHERE name = ?", (name,))
    return stmts


def rename_entity(con, old: str, new: str) -> list[str]:
    """Rename entity `old` -> `new`, propagating to every FK child column.

    DuckDB enforces FKs at DML time (RESTRICT) and has no deferred/off
    switch, so a parent `UPDATE` is impossible while children still
    reference the old value — and reparenting children to the new value is
    impossible before it exists. The ordered sequence is therefore a
    clone-move-delete:
      1. INSERT the new entity row,
      2. update every child FK column to the new value (edge tables before
         their own children; hyper_incidences via edge ids are unaffected),
      3. DELETE the old entity row.
    This yields the same final state as SQLite's UPDATE CASCADE. Returns
    ordered statements.
    """
    stmts: list[str] = []
    _run(con, stmts, "INSERT INTO entities (name) VALUES (?)", (new,))
    # Tables whose rows are NOT referenced by children can be updated in
    # place (graph_edges, entity_tags, quotes, ...).
    for table, cols in EDGE_TABLES.items():
        if not _has_column(con, table, cols[0]):
            continue
        if table in REPOINT_CHILDREN:
            rows = [
                r[0]
                for r in con.execute(
                    f'SELECT id FROM "{table}" WHERE "{cols[0]}" = ? OR "{cols[1]}" = ?',
                    (old, old),
                ).fetchall()
            ]
            _rotate_rows(con, stmts, table, rows, list(cols), old, new)
            continue
        for col in cols:
            _run(con, stmts, f'UPDATE "{table}" SET "{col}" = ? WHERE "{col}" = ?', (new, old))
    for table, (col, _action) in ENTITY_CHILDREN.items():
        if not _has_column(con, table, col):
            continue
        if table in REPOINT_CHILDREN:
            rows = [
                r[0]
                for r in con.execute(
                    f'SELECT id FROM "{table}" WHERE "{col}" = ?', (old,)
                ).fetchall()
            ]
            _rotate_rows(con, stmts, table, rows, [col], old, new)
            continue
        _run(con, stmts, f'UPDATE "{table}" SET "{col}" = ? WHERE "{col}" = ?', (new, old))
    # participant + speaker_identity carry entity FK columns too
    # (UPDATE CASCADE per manifest), so propagate there as well
    if _has_column(con, "event_participants", "participant"):
        _run(
            con,
            stmts,
            "UPDATE event_participants SET participant = ? WHERE participant = ?",
            (new, old),
        )
    if _has_column(con, "speaker_identity", "person_name"):
        _run(
            con,
            stmts,
            "UPDATE speaker_identity SET person_name = ? WHERE person_name = ?",
            (new, old),
        )
    _run(con, stmts, "DELETE FROM entities WHERE name = ?", (old,))
    return stmts
