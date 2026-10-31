#!/usr/bin/env python3
"""Tests for the event_participants n-ary store (person_ontology_spine S4).

Layers:
- Store contract: DDL + PK idempotence, FK cascade from events, per-type
  role vocabulary, counterparty-less types, unknown types never
  confabulate.
- Lane contract: dry-run default, apply idempotence (writer OWNS the
  table — role re-mappings converge, dropped events are purged).
- Hyperedge contract: the S9 event family reads THIS store, so adding a
  participant row grows the hyperedge (k>=3 arrives with data, not SQL);
  guidance singletons stay out (zero co-membership signal); the
  fallback pair path survives when the store is absent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from helpers.maintenance.migrate_to_graph_edges import (  # noqa: E402
    ENTITIES_DDL,
    EVENTS_DDL,
    EVENTS_INDEXES,
    GRAPH_EDGES_DDL,
    GRAPH_EDGES_INDEXES,
    HYPER_EDGES_DDL,
    HYPER_EDGES_INDEXES,
    HYPER_INCIDENCES_DDL,
    HYPER_INCIDENCES_INDEXES,
)
from helpers.graph import derive_event_participants as dep  # noqa: E402
from helpers.graph import derive_hyperedges as dh  # noqa: E402

CO = ["Alpha", "Beta", "Gamma", "Delta"]


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    """Canonical schema + 4 events, one per role shape, + a guid event."""
    c = sqlite3.connect(tmp_path / "events.db")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(ENTITIES_DDL)
    c.execute(GRAPH_EDGES_DDL)
    for idx in GRAPH_EDGES_INDEXES:
        c.execute(idx)
    c.execute(EVENTS_DDL)
    for idx in EVENTS_INDEXES:
        c.execute(idx)
    c.execute(HYPER_EDGES_DDL)
    for idx in HYPER_EDGES_INDEXES:
        c.execute(idx)
    c.execute(HYPER_INCIDENCES_DDL)
    for idx in HYPER_INCIDENCES_INDEXES:
        c.execute(idx)
    c.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [(n,) for n in CO],
    )
    # id 1 acquisition, 2 jv, 3 guidance (no counterparty), 4 unknown type
    c.executemany(
        "INSERT INTO events (event_type, entity, counterparty_entity, source_ref) "
        "VALUES (?, ?, ?, 'test')",
        [
            ("acquisition", "Alpha", "Beta"),
            ("jv", "Alpha", "Gamma"),
            ("guidance", "Delta", None),
            ("webinar", "Alpha", "Beta"),
        ],
    )
    c.commit()
    return c


# --------------------------------------------------------------------------- #
# Store contract
# --------------------------------------------------------------------------- #
def test_ensure_schema_creates_table_and_index(conn):
    dep.ensure_schema(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "event_participants" in names
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_event_participants_participant" in idx
    dep.ensure_schema(conn)  # idempotent


def test_collect_roles_per_event_type(conn):
    got = dep.collect(conn)
    assert got[1] == [("Alpha", "acquirer"), ("Beta", "target")]
    assert got[2] == [("Alpha", "partner"), ("Gamma", "partner")]
    assert got[3] == [("Delta", "issuer")]  # counterparty-less: entity only
    assert 4 not in got  # unknown type: never confabulate


def test_pk_idempotent_per_event_participant_role(conn):
    dep.ensure_schema(conn)
    conn.execute(
        "INSERT INTO event_participants (event_id, participant, role, source_ref) "
        "VALUES (1, 'Alpha', 'acquirer', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_participants (event_id, participant, role, source_ref) "
            "VALUES (1, 'Alpha', 'acquirer', 'y')"
        )


def test_cascade_from_events(conn):
    dep.ensure_schema(conn)
    conn.execute(
        "INSERT INTO event_participants (event_id, participant, role, source_ref) "
        "VALUES (1, 'Alpha', 'acquirer', 't')"
    )
    conn.commit()
    conn.execute("DELETE FROM events WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM event_participants").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Lane contract
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(conn):
    dep.ensure_schema(conn)
    stats = dep.apply_participants(conn, dep.collect(conn), dry_run=True)
    assert stats["events"] == 3 and stats["rows"] == 5
    assert conn.execute("SELECT COUNT(*) FROM event_participants").fetchone()[0] == 0


def test_apply_idempotent_and_converging(conn):
    dep.ensure_schema(conn)
    first = dep.apply_participants(conn, dep.collect(conn), dry_run=False)
    assert first["rows"] == 5
    again = dep.apply_participants(conn, dep.collect(conn), dry_run=False)
    assert again["new_events"] == 0 and again["new_rows"] == 0
    # role re-mapping converges: same event, changed roles
    remapped = {
        1: [("Alpha", "buyer"), ("Beta", "bought")],
        2: dep.collect(conn)[2],
        3: dep.collect(conn)[3],
    }
    final = dep.apply_participants(conn, remapped, dry_run=False)
    assert final["changed_events"] == 1
    roles = {r[0] for r in conn.execute("SELECT role FROM event_participants WHERE event_id = 1")}
    assert roles == {"buyer", "bought"}


def test_dropped_event_rows_purged(conn):
    dep.ensure_schema(conn)
    dep.apply_participants(conn, dep.collect(conn), dry_run=False)
    # Simulate an orphan the FK cascade cannot cover (legacy row whose
    # event vanished under PRAGMA foreign_keys=OFF).
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO event_participants (event_id, participant, role, source_ref) "
        "VALUES (999, 'Alpha', 'acquirer', 'legacy')"
    )
    conn.commit()
    shrunk = dep.apply_participants(conn, dep.collect(conn), dry_run=False)
    assert 999 in shrunk["dropped_events"]
    assert (
        conn.execute("SELECT COUNT(*) FROM event_participants WHERE event_id = 999").fetchone()[0]
        == 0
    )


def test_derive_end_to_end(tmp_path, capsys):
    db = tmp_path / "e2e.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(ENTITIES_DDL)
    conn.execute(EVENTS_DDL)
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [("Alpha",), ("Beta",)],
    )
    conn.execute(
        "INSERT INTO events (event_type, entity, counterparty_entity, source_ref) "
        "VALUES ('acquisition', 'Alpha', 'Beta', 'test')"
    )
    conn.commit()
    conn.close()
    dep.derive(db)  # dry-run default
    assert "[dry-run]" in capsys.readouterr().out
    dep.derive(db, apply=True)
    out = capsys.readouterr().out
    assert "[apply]" in out and "rows=2" in out
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT participant, role FROM event_participants ORDER BY role").fetchall()
    conn.close()
    assert rows == [("Alpha", "acquirer"), ("Beta", "target")]


# --------------------------------------------------------------------------- #
# Hyperedge contract (S9 reads the store)
# --------------------------------------------------------------------------- #
def test_event_family_reads_the_store_and_grows(conn):
    dep.ensure_schema(conn)
    dep.apply_participants(conn, dep.collect(conn), dry_run=False)
    hyper, _w = dh.collect_hyperedges(conn)
    # acquisition + jv are k=2; guidance (issuer only) and the unmapped
    # type stay OUT — a one-member event carries zero co-membership signal.
    assert hyper["event"] == {
        "acquisition:1": {"Alpha", "Beta"},
        "jv:2": {"Alpha", "Gamma"},
    }
    # THE S4 POINT: a future writer adds a third participant and the
    # hyperedge grows — no flattening step to re-run.
    conn.execute(
        "INSERT INTO event_participants (event_id, participant, role, source_ref) "
        "VALUES (2, 'Delta', 'partner', 'announcements')"
    )
    conn.commit()
    hyper, _w = dh.collect_hyperedges(conn)
    assert hyper["event"]["jv:2"] == {"Alpha", "Gamma", "Delta"}


def test_event_family_fallback_when_store_absent(conn):
    # Pre-v16 restore posture: no store, old pair query still serves.
    hyper, _w = dh.collect_hyperedges(conn)
    assert hyper["event"] == {
        "acquisition:1": {"Alpha", "Beta"},
        "jv:2": {"Alpha", "Gamma"},
    }


def test_fallback_and_store_paths_agree_on_live_shape(conn):
    dep.ensure_schema(conn)
    dep.apply_participants(conn, dep.collect(conn), dry_run=False)
    with_store, _ = dh.collect_hyperedges(conn)
    conn.execute("DROP TABLE event_participants")
    fallback, _ = dh.collect_hyperedges(conn)
    assert with_store["event"] == fallback["event"]
