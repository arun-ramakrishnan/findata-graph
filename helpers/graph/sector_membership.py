#!/usr/bin/env python3
"""Canonical sector membership writes (part_of/has_company retirement S2/S3).

Sector membership used to be stored twice: the bidirectional
``part_of``/``has_company`` dyad pair in ``graph_edges`` (with a roster
entry, a symmetric-pair integrity check, and three separate emitters) and
the sector hyperedges in the incidence store — where the hyperedges were
*derived from* the dyads. The derivation made the incidence store strictly
LESS expressive than the membership it copied: ``Kuantum Papers`` holds two
sector hyperedges (Agriculture, Packaging) while a single-valued
``sector_classification`` / ``part_of`` row can only record one.

The retirement made the incidence store CANONICAL and deleted the 2,414
dyad rows. Under that contract every writer that used to emit the pair
calls :func:`put_sector_membership` / :func:`remove_sector_membership`
instead — one write path, one uniqueness rule (``UNIQUE(edge_type,
label)`` + the ``(edge_id, entity_name)`` incidence PK give INSERT OR
IGNORE semantics for free), and no way for the two representations to
disagree again.

Readers never see this module: ``helpers/graph/query.py`` projects
``part_of``/``has_company`` back out of the incidence store
(``_materialise_sector_membership``), so ``e_belongs``/``e_has``,
onager, igraph, the walk substrate, snapshots and the API are unchanged.
"""

from __future__ import annotations

import sqlite3

SECTOR_HYPER_TYPE = "sector"


def _sector_edge_id(conn: sqlite3.Connection, sector: str, *, create: bool) -> int | None:
    row = conn.execute(
        "SELECT id FROM hyper_edges WHERE edge_type = ? AND label = ?",
        (SECTOR_HYPER_TYPE, sector),
    ).fetchone()
    if row is not None:
        return int(row[0])
    if not create:
        return None
    cur = conn.execute(
        "INSERT INTO hyper_edges (edge_type, label, source_ref) VALUES (?, ?, ?)",
        (SECTOR_HYPER_TYPE, sector, "sector_membership"),
    )
    assert cur.lastrowid is not None  # ty: narrow  # noqa: S101  # INSERT always sets it
    return int(cur.lastrowid)


def _refresh_member_count(conn: sqlite3.Connection, edge_id: int) -> None:
    """Keep hyper_edges.properties.n_members honest (derive writes it too)."""
    import json

    n = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ?", (edge_id,)
    ).fetchone()[0]
    row = conn.execute("SELECT properties FROM hyper_edges WHERE id = ?", (edge_id,)).fetchone()
    try:
        props = json.loads(row[0]) if row and row[0] else {}
    except TypeError, ValueError:
        props = {}
    props["n_members"] = int(n)
    conn.execute(
        "UPDATE hyper_edges SET properties = ? WHERE id = ?",
        (json.dumps(props, sort_keys=True), edge_id),
    )


def put_sector_membership(
    conn: sqlite3.Connection,
    company: str,
    sector: str,
    *,
    source_ref: str,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> bool:
    """Add (or refresh) company→sector membership; returns True when NEW.

    Idempotent: an existing incidence is left alone (the caller's
    transaction owns any richer convergence). The sector hyperedge is
    created on demand — a company note naming a sector that has no
    hyperedge yet must not vanish.
    """
    edge_id = _sector_edge_id(conn, sector, create=True)
    assert edge_id is not None  # noqa: S101  # create=True guarantees a row
    cur = conn.execute(
        "INSERT OR IGNORE INTO hyper_incidences "
        "(edge_id, entity_name, valid_from, valid_to) VALUES (?, ?, ?, ?)",
        (edge_id, company, valid_from, valid_to),
    )
    created = cur.rowcount > 0
    conn.execute(
        "UPDATE hyper_edges SET source_ref = ? WHERE id = ? AND source_ref != ?",
        (source_ref, edge_id, source_ref),
    )
    if created:
        _refresh_member_count(conn, edge_id)
    return created


def remove_sector_membership(conn: sqlite3.Connection, company: str, sector: str) -> bool:
    """Drop company→sector membership; returns True when a row was removed.

    The hyperedge survives an emptied membership set: other provenance
    (authored sector notes, derive) may still own it, so only the
    incidence goes and the member count is refreshed.
    """
    edge_id = _sector_edge_id(conn, sector, create=False)
    if edge_id is None:
        return False
    cur = conn.execute(
        "DELETE FROM hyper_incidences WHERE edge_id = ? AND entity_name = ?",
        (edge_id, company),
    )
    removed = cur.rowcount > 0
    if removed:
        _refresh_member_count(conn, edge_id)
    return removed


def sector_membership_rows(
    conn: sqlite3.Connection, company: str | None = None
) -> list[tuple[str, str]]:
    """``[(company, sector)]`` from the canonical store (all, or one company)."""
    sql = (
        "SELECT hi.entity_name, he.label FROM hyper_incidences hi "
        "JOIN hyper_edges he ON he.id = hi.edge_id WHERE he.edge_type = ?"
    )
    params: tuple = (SECTOR_HYPER_TYPE,)
    if company is not None:
        sql += " AND hi.entity_name = ?"
        params += (company,)
    return [(r[0], r[1]) for r in conn.execute(sql + " ORDER BY he.label, hi.entity_name", params)]
