#!/usr/bin/env python3
"""Tests for group-membership compaction (S1) and stranded-orphan purge (S3)
    proposal group_membership_compaction.md.

- compact_group_membership: dyad-derived group labels (vigil:rpt:*, bare
  $.group) converge their incidences to the current collected membership;
  family:* seed labels are untouched; dry-run reports without writing.
- purge_stranded_companies: only zero-reference orphan companies are
  purgeable; event-referenced (counterparty_entity) names are kept; plan
  mode never writes.

Run: pytest tests/test_group_membership_compaction.py -v
"""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance.migrate_to_graph_edges import (  # noqa: E402
    ENTITIES_DDL,
    GRAPH_EDGES_DDL,
    GRAPH_EDGES_INDEXES,
    HYPER_EDGES_DDL,
    HYPER_EDGES_INDEXES,
    HYPER_INCIDENCES_DDL,
    HYPER_INCIDENCES_INDEXES,
    EVENTS_DDL,
    EVENTS_INDEXES,
)
from helpers.graph import derive_hyperedges as dh  # noqa: E402
from helpers.maintenance import purge_stranded_companies as ps  # noqa: E402

CO = ["Alpha", "Beta", "Gamma", "Delta"]


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "hyper.db")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(ENTITIES_DDL)
    c.execute(GRAPH_EDGES_DDL)
    for idx in GRAPH_EDGES_INDEXES:
        c.execute(idx)
    c.execute(HYPER_EDGES_DDL)
    for idx in HYPER_EDGES_INDEXES:
        c.execute(idx)
    c.execute(HYPER_INCIDENCES_DDL)
    for idx in HYPER_INCIDENCES_INDEXES:
        c.execute(idx)
    c.execute(EVENTS_DDL)
    for idx in EVENTS_INDEXES:
        c.execute(idx)
    # The orphan predicate references the relations table (or pre-retirement
    # fallback); an empty table satisfies it in tests.
    c.execute(
        "CREATE TABLE IF NOT EXISTS relations ("
        " id INTEGER PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,"
        " relation_type TEXT NOT NULL, source_ref TEXT, properties TEXT,"
        " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS company_metrics ("
        " id INTEGER PRIMARY KEY, entity TEXT NOT NULL, metric_label TEXT,"
        " value_raw TEXT, value_num REAL, unit TEXT, period TEXT,"
        " as_of_edition TEXT, source_quote TEXT, source_ref TEXT, properties TEXT,"
        " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " consolidated INTEGER)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS quotes ("
        " id INTEGER PRIMARY KEY, entity TEXT NOT NULL, quote_text TEXT,"
        " paraphrase TEXT, speaker_name TEXT, speaker_title TEXT,"
        " as_of_edition TEXT, source_ref TEXT, properties TEXT,"
        " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    c.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        [(n, "company") for n in CO] + [("Automotive", "sector")],
    )
    c.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            # vigil disclosure set: Alpha/Beta dyad under source_ref vigil:rpt:TCS
            ("Alpha", "Beta", "same_group", "{}", "vigil:rpt:TCS"),
            # bare $.group dyad
            ("Gamma", "Delta", "same_group", '{"group": "M Group"}', "test"),
        ],
    )
    c.commit()
    return c


def _seed_group_edge(c, label, members):
    cur = c.execute(
        "INSERT INTO hyper_edges (edge_type, label, source_ref) VALUES ('group', ?, 'test')",
        (label,),
    )
    c.executemany(
        "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?, ?)",
        [(cur.lastrowid, m) for m in members],
    )
    return cur.lastrowid


def test_compact_vigil_label_shrinks_membership(conn):
    # Stale incidence: Gamma is in the stored vigil:rpt:TCS hyperedge but
    # has no same_group dyad under that source_ref anymore.
    _seed_group_edge(conn, "vigil:rpt:TCS", ["Alpha", "Beta", "Gamma"])
    groups = {"vigil:rpt:TCS": {"Alpha", "Beta"}}
    pruned = dh.compact_group_membership(conn, groups, dry_run=False)
    assert pruned == 1
    members = {
        r[0]
        for r in conn.execute(
            "SELECT entity_name FROM hyper_incidences WHERE edge_id = "
            "(SELECT id FROM hyper_edges WHERE label='vigil:rpt:TCS')"
        )
    }
    assert members == {"Alpha", "Beta"}


def test_compact_bare_group_label_shrinks(conn):
    _seed_group_edge(conn, "M Group", ["Gamma", "Delta", "Alpha"])
    pruned = dh.compact_group_membership(conn, {"M Group": {"Gamma", "Delta"}}, dry_run=False)
    assert pruned == 1
    members = {
        r[0]
        for r in conn.execute(
            "SELECT entity_name FROM hyper_incidences WHERE edge_id = "
            "(SELECT id FROM hyper_edges WHERE label='M Group')"
        )
    }
    assert members == {"Gamma", "Delta"}


def test_compact_family_label_untouched(conn):
    # family:* seeds are NOT dyad-derived — never compacted even if the
    # collected groups omit a stored member.
    _seed_group_edge(conn, "family:Seeded", ["Alpha", "Beta", "Gamma"])
    pruned = dh.compact_group_membership(conn, {"family:Seeded": {"Alpha", "Beta"}}, dry_run=False)
    assert pruned == 0
    n = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences WHERE edge_id = "
        "(SELECT id FROM hyper_edges WHERE label='family:Seeded')"
    ).fetchone()[0]
    assert n == 3


def test_compact_dry_run_reports_without_writing(conn):
    _seed_group_edge(conn, "vigil:rpt:TCS", ["Alpha", "Beta", "Gamma"])
    would = dh.compact_group_membership(conn, {"vigil:rpt:TCS": {"Alpha", "Beta"}}, dry_run=True)
    assert would == 1
    n = conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0]
    assert n == 3  # unchanged


def test_compact_missing_label_untouched(conn):
    _seed_group_edge(conn, "vigil:rpt:TCS", ["Alpha", "Beta", "Gamma"])
    pruned = dh.compact_group_membership(conn, {}, dry_run=False)
    assert pruned == 0
    n = conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0]
    assert n == 3


def test_purge_plan_and_apply(conn):
    # Sector membership for Alpha/Beta (non-orphan), none for others.
    cur = conn.execute(
        "INSERT INTO hyper_edges (edge_type, label, source_ref) "
        "VALUES ('sector', 'Automotive', 'test')"
    )
    conn.executemany(
        "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?, ?)",
        [(cur.lastrowid, n) for n in ("Alpha", "Beta")],
    )
    # Eps: no refs at all -> purgeable. Zeta: an event references it as
    # counterparty_entity -> kept (event-referenced).
    conn.execute("INSERT INTO entities (name, entity_type) VALUES ('Eps', 'company')")
    conn.execute("INSERT INTO entities (name, entity_type) VALUES ('Zeta', 'company')")
    conn.execute(
        "INSERT INTO events (entity, event_type, counterparty_entity, source_ref) "
        "VALUES ('Alpha', 'jv', 'Zeta', 'test')"
    )
    conn.commit()

    purgeable, kept = ps.collect_stranded(conn)
    assert {n for n, _, _ in purgeable} == {"Eps"}
    assert {n for n, _, _ in kept} == {"Zeta"}

    # Plan mode writes nothing.
    ps.purge(write=False, con=conn)
    assert conn.execute("SELECT COUNT(*) FROM entities WHERE name='Eps'").fetchone()[0] == 1

    # Apply removes Eps, keeps Zeta.
    ps.purge(write=True, con=conn)
    assert conn.execute("SELECT COUNT(*) FROM entities WHERE name='Eps'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM entities WHERE name='Zeta'").fetchone()[0] == 1
    # Zeta's event survived and still points at it.
    assert (
        conn.execute("SELECT COUNT(*) FROM events WHERE counterparty_entity='Zeta'").fetchone()[0]
        == 1
    )
