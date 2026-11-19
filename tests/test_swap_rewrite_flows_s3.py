"""S3 dual-engine equivalence tests for the cascade rewrite flows.

Covers the flows added/extended in the S3 phase of the SQLite→DuckDB swap:
``drop_event`` (manifest edge 6), ``drop_agent`` (edges 1/7/12/15/19),
the DELETE-CASCADE-only ``ppr_results`` edge (18), and a coverage assertion
that all 22 manifest edges resolve to a concrete ``rewrite_flow_owner``.
The six pre-existing tests in ``test_swap_rewrite_flows.py`` stay untouched.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from helpers.misc.swap_rewrite_flows import (
    NO_UPDATE_CASCADE,
    drop_agent,
    drop_entity,
    drop_event,
    rename_entity,
)

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "findata" / "Misc" / "duckdb_research_swap_cascade_manifest.json"

SQLITE_DDL = """
CREATE TABLE entities (name TEXT PRIMARY KEY);
CREATE TABLE provenance_agents (agent_id TEXT PRIMARY KEY);
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  entity TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  agent_id TEXT REFERENCES provenance_agents(agent_id) ON DELETE SET NULL
);
CREATE TABLE event_participants (
  participant TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE quotes (
  id INTEGER PRIMARY KEY,
  entity TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  agent_id TEXT REFERENCES provenance_agents(agent_id) ON DELETE SET NULL
);
CREATE TABLE graph_edges (
  id INTEGER PRIMARY KEY,
  source TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  target TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  agent_id TEXT REFERENCES provenance_agents(agent_id) ON DELETE SET NULL
);
CREATE TABLE hyper_edges (
  id INTEGER PRIMARY KEY,
  source TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  target TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  agent_id TEXT REFERENCES provenance_agents(agent_id) ON DELETE SET NULL
);
CREATE TABLE hyper_incidences (
  edge_id INTEGER REFERENCES hyper_edges(id) ON DELETE CASCADE ON UPDATE CASCADE,
  entity_name TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE company_metrics (
  id INTEGER PRIMARY KEY,
  entity TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  agent_id TEXT REFERENCES provenance_agents(agent_id) ON DELETE SET NULL
);
CREATE TABLE ppr_results (
  seed TEXT,
  entity TEXT REFERENCES entities(name) ON DELETE CASCADE,
  PRIMARY KEY (seed, entity)
);
CREATE TABLE graph_analytics (
  metric TEXT,
  entity_name TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  PRIMARY KEY (metric, entity_name)
);
CREATE TABLE graph_analytics_contract (
  metric TEXT,
  entity_name TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  PRIMARY KEY (metric, entity_name)
);
CREATE TABLE speaker_identity (
  person_name TEXT REFERENCES entities(name) ON DELETE SET NULL ON UPDATE CASCADE
);
"""

DUCKDB_DDL = """
CREATE TABLE entities (name VARCHAR PRIMARY KEY);
CREATE TABLE provenance_agents (agent_id VARCHAR PRIMARY KEY);
CREATE TABLE events (
  id BIGINT PRIMARY KEY,
  entity VARCHAR,
  agent_id VARCHAR,
  FOREIGN KEY (entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (agent_id) REFERENCES provenance_agents(agent_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE event_participants (
  participant VARCHAR,
  event_id BIGINT,
  FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE quotes (
  id BIGINT PRIMARY KEY,
  entity VARCHAR,
  agent_id VARCHAR,
  FOREIGN KEY (entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (agent_id) REFERENCES provenance_agents(agent_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE graph_edges (
  id BIGINT PRIMARY KEY,
  source VARCHAR,
  target VARCHAR,
  agent_id VARCHAR,
  FOREIGN KEY (source) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (target) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (agent_id) REFERENCES provenance_agents(agent_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE hyper_edges (
  id BIGINT PRIMARY KEY,
  source VARCHAR,
  target VARCHAR,
  agent_id VARCHAR,
  FOREIGN KEY (source) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (target) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (agent_id) REFERENCES provenance_agents(agent_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE hyper_incidences (
  edge_id BIGINT,
  entity_name VARCHAR,
  FOREIGN KEY (edge_id) REFERENCES hyper_edges(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE company_metrics (
  id BIGINT PRIMARY KEY,
  entity VARCHAR,
  agent_id VARCHAR,
  FOREIGN KEY (entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (agent_id) REFERENCES provenance_agents(agent_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE ppr_results (
  seed VARCHAR,
  entity VARCHAR,
  PRIMARY KEY (seed, entity),
  FOREIGN KEY (entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE graph_analytics (
  metric VARCHAR,
  entity_name VARCHAR,
  PRIMARY KEY (metric, entity_name),
  FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE graph_analytics_contract (
  metric VARCHAR,
  entity_name VARCHAR,
  PRIMARY KEY (metric, entity_name),
  FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE speaker_identity (
  person_name VARCHAR,
  FOREIGN KEY (person_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
"""

TABLES = [
    "entities",
    "provenance_agents",
    "events",
    "event_participants",
    "quotes",
    "graph_edges",
    "hyper_edges",
    "hyper_incidences",
    "company_metrics",
    "ppr_results",
    "graph_analytics",
    "graph_analytics_contract",
    "speaker_identity",
]

SEED = {
    "entities": [("A",), ("B",)],
    "provenance_agents": [("ag1",), ("ag2",)],
    "events": [(1, "A", "ag1"), (2, "A", "ag2"), (3, "B", None)],
    "event_participants": [("A", 1), ("B", 2), ("B", 3)],
    "quotes": [(1, "A", "ag1"), (2, "B", "ag2")],
    "graph_edges": [(1, "A", "B", "ag1"), (2, "B", "A", "ag2")],
    "hyper_edges": [(1, "A", "B", "ag1")],
    "hyper_incidences": [(1, "A")],
    "company_metrics": [(1, "A", "ag1"), (2, "B", "ag2")],
    "ppr_results": [("s1", "A"), ("s2", "B")],
    "graph_analytics": [("m1", "A"), ("m2", "B")],
    "graph_analytics_contract": [("m1", "A")],
    "speaker_identity": [("A",)],
}


def _cols(con, table: str, duck: bool) -> list[str]:
    if duck:
        return [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _seed(con, duck: bool) -> None:
    # duckdb 2.0 has no executescript(); a single .execute() runs the whole
    # multi-statement DDL string. sqlite3 uses executescript().
    if duck:
        con.execute(DUCKDB_DDL)
    else:
        con.executescript(SQLITE_DDL)
    for table, rows in SEED.items():
        cols = ",".join(_cols(con, table, duck))
        marks = ",".join("?" for _ in rows[0])
        con.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})", rows)
    if not duck:
        con.commit()


def _snapshot(con, duck: bool) -> dict[str, list[tuple]]:
    return {
        t: sorted(tuple(r) for r in con.execute(f"SELECT * FROM {t}").fetchall()) for t in TABLES
    }


@pytest.fixture
def engines(tmp_path):
    sq = sqlite3.connect(tmp_path / "s3.db")
    sq.execute("PRAGMA foreign_keys = ON")
    _seed(sq, duck=False)
    dk = duckdb.connect(str(tmp_path / "s3.duckdb"))
    _seed(dk, duck=True)
    yield sq, dk
    sq.close()
    dk.close()


def test_drop_event_equivalence(engines):
    sq, dk = engines
    sq.execute("DELETE FROM event_participants WHERE event_id = 1")
    sq.execute("DELETE FROM events WHERE id = 1")
    sq.commit()
    drop_event(dk, 1)
    assert _snapshot(dk, True) == _snapshot(sq, False)


def test_drop_agent_equivalence(engines):
    sq, dk = engines
    sq.execute("DELETE FROM provenance_agents WHERE agent_id = 'ag1'")
    sq.commit()
    drop_agent(dk, "ag1")
    assert _snapshot(dk, True) == _snapshot(sq, False)
    # all five fact tables SET NULL, agent gone
    for table in ("events", "quotes", "graph_edges", "hyper_edges", "company_metrics"):
        assert dk.execute(f"SELECT COUNT(*) FROM {table} WHERE agent_id = 'ag1'").fetchone()[0] == 0
    assert dk.execute("SELECT COUNT(*) FROM provenance_agents").fetchone()[0] == 1


def test_drop_entity_covers_marked_cascade_edges(engines):
    sq, dk = engines
    sq.execute("DELETE FROM entities WHERE name = 'A'")
    sq.commit()
    drop_entity(dk, "A")
    assert _snapshot(dk, True) == _snapshot(sq, False)
    # ppr_results / graph_analytics / graph_analytics_contract DELETE CASCADE
    for table, col in (
        ("ppr_results", "entity"),
        ("graph_analytics", "entity_name"),
        ("graph_analytics_contract", "entity_name"),
    ):
        assert dk.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = 'A'").fetchone()[0] == 0


def test_rename_entity_skips_no_update_cascade_edges(engines):
    # SQLite rejects a rename when a DELETE-CASCADE-only child (ppr_results)
    # references the target; DuckDB must leave it untouched rather than follow.
    # Point ppr_results at B so the A→AZ rename is legal on both engines and
    # confirm the rewrite does not drag the no-update-cascade table along.
    sq, dk = engines
    sq.execute("UPDATE ppr_results SET entity = 'B' WHERE entity = 'A'")
    dk.execute("UPDATE ppr_results SET entity = 'B' WHERE entity = 'A'")
    sq.execute("PRAGMA foreign_keys = ON")
    sq.execute("UPDATE entities SET name = 'AZ' WHERE name = 'A'")
    sq.commit()
    rename_entity(dk, "A", "AZ")
    assert _snapshot(dk, True) == _snapshot(sq, False)
    assert "ppr_results" in NO_UPDATE_CASCADE


def test_all_manifest_edges_have_concrete_owner():
    edges = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(edges) == 22
    bad = [e for e in edges if e["rewrite_flow_owner"].startswith("TBD")]
    assert bad == [], f"unassigned manifest edges: {bad}"
    # Every owner is one of the known flow/module names from the proposal S3.
    known = {
        "rename_entity",
        "name_dedup",
        "migrate_to_graph_edges",
        "store_sweep",
        "drop_entity",
        "drop_event",
        "drop_agent",
    }
    unknown = sorted({e["rewrite_flow_owner"] for e in edges} - known)
    assert unknown == [], f"unknown owners: {unknown}"
