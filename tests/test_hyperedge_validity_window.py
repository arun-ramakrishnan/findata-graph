#!/usr/bin/env python3
"""Hyperedge regroup honors the dyad validity window (regression).

Why this exists: `derive_indices --prune-absent` closes a departed
membership by stamping `graph_edges.valid_to` instead of deleting the row,
but `collect_hyperedges` used to regroup EVERY historical dyad — so the
closed member stayed inside its hyperedge forever and the index family was
a union of ever-membership. Measured live 2026-11-14 before the fix:
NIFTY 50 held 51 members (Wipro, closed 2026-11-13, still counted beside
its replacement BSE), 281 stale incidences across 36 index hyperedges.

The fix has two halves, both pinned here:
  1. `collect_hyperedges` filters on the validity window
     (`valid_to IS NULL OR valid_to > date('now')`), matching
     `helpers/graph/as_of.py`;
  2. `compact_membership` deletes incidences the collect no longer
     produces, because `apply_hyperedges` is INSERT OR IGNORE (additive).

The test uses an always-open row and a closed row under the SAME label, so
a regression that drops the filter shows up as an extra member rather than
as a missing hyperedge.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import EDGES_12COL, ENTITIES_8COL, HYPER_STORE  # noqa: E402

from helpers.graph import derive_hyperedges as dh  # noqa: E402

_STAYS = "Stays Ltd"
_LEFT = "Left Ltd"
_NEW = "Newco Ltd"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(ENTITIES_8COL)
    c.executescript(EDGES_12COL)
    c.executescript(HYPER_STORE)
    c.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [(_STAYS,), (_LEFT,), (_NEW,)],
    )
    rows = [
        # Still a member: open window.
        (_STAYS, "NIFTY 50", "listed_on_index", "{}", "2026-09-18", None),
        # Departed: closed by the prune lane (valid_to set, row retained).
        (_LEFT, "NIFTY 50", "listed_on_index", "{}", "2026-09-18", "2026-11-13"),
        # Replacement, arrived in the new vintage.
        (_NEW, "NIFTY 50", "listed_on_index", "{}", "2026-11-13", None),
    ]
    c.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, properties, valid_from, valid_to, source_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, 'test')",
        rows,
    )
    # A symmetric family with the same shape: the departed peer must not
    # rejoin its group hyperedge through the target-side pass either.
    c.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, properties, valid_from, valid_to, source_ref) "
        "VALUES (?, ?, 'same_group', ?, '2026-09-18', ?, 'test')",
        [
            (_STAYS, _NEW, '{"group": "Test Group"}', None),
            (_STAYS, _LEFT, '{"group": "Test Group"}', "2026-11-13"),
        ],
    )
    yield c
    c.close()


def test_closed_dyad_does_not_join_the_hyperedge(conn):
    hyper, _weights = dh.collect_hyperedges(conn)
    members = hyper["index"]["NIFTY 50"]
    assert members == {_STAYS, _NEW}, f"closed member leaked into the index family: {members}"
    assert _LEFT not in members
    group = hyper["group"]["Test Group"]
    assert _LEFT not in group, f"closed peer leaked into the symmetric family: {group}"


def test_compact_membership_drops_the_stale_incidence(conn):
    hyper, weights = dh.collect_hyperedges(conn)
    dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
    # Simulate the pre-fix store: the departed member is still an incidence.
    eid = conn.execute(
        "SELECT id FROM hyper_edges WHERE edge_type = 'index' AND label = 'NIFTY 50'"
    ).fetchone()[0]
    conn.execute("INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?, ?)", (eid, _LEFT))

    would = dh.compact_membership(conn, "index", hyper["index"], dry_run=True)
    assert would == 1, "dry-run must report the stale incidence without writing"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ? AND entity_name = ?",
            (eid, _LEFT),
        ).fetchone()[0]
        == 1
    )

    gone = dh.compact_membership(conn, "index", hyper["index"], dry_run=False)
    assert gone == 1
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = ? AND entity_name = ?",
            (eid, _LEFT),
        ).fetchone()[0]
        == 0
    )


def test_open_rows_are_untouched(conn):
    hyper, _weights = dh.collect_hyperedges(conn)
    eid_hyper = hyper["index"]
    dh.apply_hyperedges(hyper, {}, conn=conn, dry_run=False)
    before = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.edge_type = 'index'"
    ).fetchone()[0]
    assert dh.compact_membership(conn, "index", eid_hyper, dry_run=False) == 0
    after = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.edge_type = 'index'"
    ).fetchone()[0]
    assert before == after == 2
