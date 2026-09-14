#!/usr/bin/env python3
"""Tests for the S4 n-ary event facets (ontology_convention_stack):
hyper_incidences role/valid_from/valid_to, the event role vocabulary +
magnitude parse, facet convergence in apply_hyperedges, the pre-apply
reconciliation report, and the role round-trip through the Parquet
snapshot path (proposal acceptance: "role-tagged incidences round-trip
through snapshot").
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import derive_hyperedges as dh  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import (  # noqa: E402
    ENTITIES_DDL,
    EVENTS_DDL,
    GRAPH_EDGES_DDL,
    HYPER_EDGES_DDL,
    HYPER_INCIDENCES_DDL,
)
from helpers.maintenance.snapshot_db import (  # noqa: E402
    export_parquet_sqlite,
    restore_sqlite_from_parquet,
)

CO = ["Alpha", "Beta", "Gamma", "Delta"]


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    """Canonical schema + counterparty-carrying events of each shape.

    - acquisition 1: mapped role pair + numeric magnitude + date
    - jv 2: symmetric partner roles, no magnitude
    - guidance 3: counterparty present but UNMAPPED type (roles stay NULL)
    - acquisition 4 + 5: identical (type, pair, date) — a duplicate group
    - jv 6: counterparty TEXT unresolved (no matching entity)
    """
    c = sqlite3.connect(tmp_path / "roles.db")
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(ENTITIES_DDL)
    c.execute(GRAPH_EDGES_DDL)
    c.execute(HYPER_EDGES_DDL)
    c.execute(HYPER_INCIDENCES_DDL)
    c.execute(EVENTS_DDL)
    c.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [(n,) for n in CO],
    )
    c.executemany(
        "INSERT INTO events (id, entity, event_type, event_date, period, "
        "date_precision, magnitude, counterparty, counterparty_entity, source_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'test')",
        [
            (
                1,
                "Alpha",
                "acquisition",
                "2026-01-15",
                "Q4FY26",
                "day",
                "USD 1,234.5 crore",
                "Beta",
                "Beta",
            ),
            (2, "Gamma", "jv", "2026-02-01", None, "day", None, "Delta", "Delta"),
            (3, "Alpha", "guidance", "2026-02-10", None, "day", "10-12%", "Beta", "Beta"),
            (4, "Gamma", "acquisition", "2026-03-01", None, "day", None, "Delta", "Delta"),
            (5, "Delta", "acquisition", "2026-03-01", None, "day", None, "Gamma", "Gamma"),
            (6, "Beta", "jv", None, None, None, None, "Ghost Co", None),
        ],
    )
    c.commit()
    return c


def _event_label(conn, eid: int) -> str:
    etype = conn.execute("SELECT event_type FROM events WHERE id = ?", (eid,)).fetchone()[0]
    return f"{etype}:{eid}"


def _role_of(conn, label: str, member: str) -> str | None:
    return conn.execute(
        "SELECT i.role FROM hyper_incidences i JOIN hyper_edges h ON h.id = i.edge_id "
        "WHERE h.edge_type = 'event' AND h.label = ? AND i.entity_name = ?",
        (label, member),
    ).fetchone()[0]


# --- schema + parse ---------------------------------------------------------


class TestEnsureIncidenceFacets:
    def test_adds_columns_idempotently(self, conn):
        # fresh-canonical DDL already has them; strip via a legacy-shaped DB
        legacy = sqlite3.connect(":memory:")
        legacy.execute(ENTITIES_DDL)
        legacy.execute(
            "CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY, edge_type TEXT, "
            "label TEXT, weight REAL DEFAULT 1.0, valid_from DATE, valid_to DATE, "
            "source_ref TEXT NOT NULL, properties TEXT DEFAULT '{}', created_at DATETIME)"
        )
        legacy.execute(
            "CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT, "
            "weight REAL, direction TEXT, PRIMARY KEY (edge_id, entity_name))"
        )
        dh.ensure_incidence_facets(legacy)
        dh.ensure_incidence_facets(legacy)  # idempotent
        cols = [r[1] for r in legacy.execute("PRAGMA table_info(hyper_incidences)")]
        assert cols[-3:] == ["role", "valid_from", "valid_to"]
        legacy.close()

    def test_noop_without_table(self, tmp_path):
        bare = sqlite3.connect(tmp_path / "bare.db")
        dh.ensure_incidence_facets(bare)  # must not raise
        bare.close()


class TestParseMagnitude:
    def test_numeric_with_unit(self):
        assert dh._parse_magnitude("USD 1,234.5 crore") == (1234.5, "USD crore")

    def test_bare_number(self):
        assert dh._parse_magnitude("708") == (708.0, None)

    def test_non_numeric(self):
        assert dh._parse_magnitude("10-12%") == (None, None)

    def test_empty_and_none(self):
        assert dh._parse_magnitude("") == (None, None)
        assert dh._parse_magnitude(None) == (None, None)


# --- collect + apply --------------------------------------------------------


class TestCollectEventFacets:
    def test_roles_props_windows(self, conn):
        f = dh.collect_event_facets(conn)
        assert f["roles"][("acquisition:1", "Alpha")] == "acquirer"
        assert f["roles"][("acquisition:1", "Beta")] == "target"
        assert f["roles"][("jv:2", "Gamma")] == "partner"
        assert f["roles"][("jv:2", "Delta")] == "partner"
        # unmapped type: NO role entries (participants stay unlabeled)
        assert not any(k[0] == "guidance:3" for k in f["roles"])
        assert f["valid_from"]["acquisition:1"] == "2026-01-15"
        assert f["props"]["acquisition:1"] == {
            "period": "Q4FY26",
            "date_precision": "day",
            "magnitude_raw": "USD 1,234.5 crore",
            "magnitude_numeric": 1234.5,
            "magnitude_unit": "USD crore",
        }
        # jv:2 has no magnitude/period — only date_precision rides along
        assert f["props"]["jv:2"] == {"date_precision": "day"}
        # guidance:3's "10-12%" is a range — no numeric key (Nones are
        # dropped), raw preserved for audit
        assert "magnitude_numeric" not in f["props"]["guidance:3"]
        assert f["props"]["guidance:3"]["magnitude_raw"] == "10-12%"

    def test_absent_events_table(self, tmp_path):
        bare = sqlite3.connect(tmp_path / "bare.db")
        assert dh.collect_event_facets(bare) == {"roles": {}, "props": {}, "valid_from": {}}
        bare.close()


class TestApplyWithRoles:
    def _run(self, conn, apply=True):
        hyper, weights = dh.collect_hyperedges(conn)
        facets = dh.collect_event_facets(conn)
        return dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=not apply, facets=facets)

    def test_roles_written_and_idempotent(self, conn):
        self._run(conn)
        assert _role_of(conn, "acquisition:1", "Alpha") == "acquirer"
        assert _role_of(conn, "acquisition:1", "Beta") == "target"
        assert _role_of(conn, "jv:2", "Gamma") == "partner"
        # unmapped type: participants recorded with NULL roles
        assert _role_of(conn, "guidance:3", "Alpha") is None
        stats = self._run(conn)
        assert stats["event"] == (0, 0)  # idempotent re-run

    def test_event_props_and_valid_from(self, conn):
        self._run(conn)
        props = json.loads(
            conn.execute(
                "SELECT properties FROM hyper_edges WHERE edge_type='event' "
                "AND label='acquisition:1'"
            ).fetchone()[0]
        )
        assert props["magnitude_numeric"] == 1234.5
        assert props["magnitude_raw"] == "USD 1,234.5 crore"
        assert props["period"] == "Q4FY26"
        vf = conn.execute(
            "SELECT valid_from FROM hyper_edges WHERE edge_type='event' AND label='acquisition:1'"
        ).fetchone()[0]
        assert vf == "2026-01-15"

    def test_role_convergence(self, conn):
        self._run(conn)
        # stale stored role (e.g. hand-fix or older vocabulary)
        conn.execute(
            "UPDATE hyper_incidences SET role='buyer' WHERE entity_name='Alpha' "
            "AND edge_id = (SELECT id FROM hyper_edges WHERE edge_type='event' "
            "AND label='acquisition:1')"
        )
        conn.commit()
        self._run(conn)
        assert _role_of(conn, "acquisition:1", "Alpha") == "acquirer"

    def test_stale_role_converges_to_null_for_untagged(self, conn):
        self._run(conn)
        # a guidance participant carrying a stale role from a pre-S4 world
        conn.execute(
            "UPDATE hyper_incidences SET role='subject' WHERE entity_name='Alpha' "
            "AND edge_id = (SELECT id FROM hyper_edges WHERE edge_type='event' "
            "AND label='guidance:3')"
        )
        conn.commit()
        self._run(conn)
        assert _role_of(conn, "guidance:3", "Alpha") is None

    def test_no_facets_leaves_roles_untouched(self, conn):
        self._run(conn)
        conn.execute(
            "UPDATE hyper_incidences SET role='hand-curated' WHERE entity_name='Alpha' "
            "AND edge_id = (SELECT id FROM hyper_edges WHERE edge_type='event' "
            "AND label='acquisition:1')"
        )
        conn.commit()
        hyper, weights = dh.collect_hyperedges(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False, facets=None)
        assert _role_of(conn, "acquisition:1", "Alpha") == "hand-curated"


# --- reconciliation ---------------------------------------------------------


class TestReconcileEvents:
    def test_full_report_shape(self, conn):
        rec = dh.reconcile_events(conn)
        assert rec["observations"] == 5  # resolved-counterparty events only
        assert rec["unresolved"] == 1
        assert rec["unresolved_names"] == ["Ghost Co"]
        # acquisition 4+5: same type/pair/date across two rows -> 1 group
        assert rec["duplicate_groups"] == 1
        assert any("acquisition" in d and "×2" in d for d in rec["duplicate_rows"])
        assert rec["untagged"] == {"guidance": 1}
        assert rec["conflicts"] == []

    def test_conflict_detected(self, conn):
        hyper, weights = dh.collect_hyperedges(conn)
        facets = dh.collect_event_facets(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False, facets=facets)
        # tamper: swap a stored member set away from {Alpha, Beta}
        conn.execute(
            "UPDATE hyper_incidences SET entity_name='Delta' WHERE entity_name='Beta' "
            "AND edge_id = (SELECT id FROM hyper_edges WHERE edge_type='event' "
            "AND label='acquisition:1')"
        )
        conn.commit()
        rec = dh.reconcile_events(conn)
        assert "acquisition:1" in rec["conflicts"]

    def test_absent_events_table(self, tmp_path):
        bare = sqlite3.connect(tmp_path / "bare.db")
        rec = dh.reconcile_events(bare)
        assert rec["observations"] == 0
        bare.close()


# --- snapshot round-trip ----------------------------------------------------


class TestSnapshotRoundTrip:
    def test_role_tagged_incidences_survive_parquet_cycle(self, conn, tmp_path):
        hyper, weights = dh.collect_hyperedges(conn)
        facets = dh.collect_event_facets(conn)
        dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False, facets=facets)
        conn.commit()
        conn.close()

        src = tmp_path / "roles.db"
        log = logging.getLogger("test-s4-snapshot")
        pq = tmp_path / "pq"
        export_parquet_sqlite(src, pq, log)
        restored = tmp_path / "restored.db"
        restore_sqlite_from_parquet(pq, restored, log)

        got = {
            (r[0], r[1]): r[2]
            for r in sqlite3.connect(restored).execute(
                "SELECT h.label, i.entity_name, i.role FROM hyper_incidences i "
                "JOIN hyper_edges h ON h.id = i.edge_id WHERE h.edge_type='event'"
            )
        }
        assert got[("acquisition:1", "Alpha")] == "acquirer"
        assert got[("acquisition:1", "Beta")] == "target"
        assert got[("jv:2", "Gamma")] == "partner"
        assert got[("guidance:3", "Alpha")] is None
        # the validity window column rides along too
        vf = (
            sqlite3.connect(restored)
            .execute("SELECT valid_from FROM hyper_incidences LIMIT 1")
            .fetchone()
        )
        assert vf is not None  # column exists on the restored table
