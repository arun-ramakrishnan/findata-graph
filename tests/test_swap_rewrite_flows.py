"""Step 3 dual-engine equivalence tests: SQLite cascade vs DuckDB ordered
rewrites (helpers/misc/swap_rewrite_flows.py), against hermetic fixtures."""

import sqlite3

import duckdb
import pytest

from helpers.misc.swap_rewrite_flows import drop_entity, rename_entity

# --- fixture DDL ------------------------------------------------------------

SQLITE_DDL = """
CREATE TABLE entities (name TEXT PRIMARY KEY);
CREATE TABLE entity_tags (
  tag TEXT,
  entity_name TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  entity TEXT NOT NULL REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  counterparty_entity TEXT REFERENCES entities(name) ON DELETE SET NULL ON UPDATE CASCADE
);
CREATE TABLE event_participants (
  participant TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE quotes (
  id INTEGER PRIMARY KEY,
  entity TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE graph_edges (
  id INTEGER PRIMARY KEY,
  source TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  target TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE hyper_edges (
  id INTEGER PRIMARY KEY,
  source TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
  target TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE hyper_incidences (
  edge_id INTEGER REFERENCES hyper_edges(id) ON DELETE CASCADE ON UPDATE CASCADE,
  entity_name TEXT REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE speaker_identity (
  person_name TEXT REFERENCES entities(name) ON DELETE SET NULL ON UPDATE CASCADE
);
"""

DUCKDB_DDL = """
CREATE TABLE entities (name VARCHAR PRIMARY KEY);
CREATE TABLE entity_tags (
  tag VARCHAR,
  entity_name VARCHAR,
  FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE events (
  id BIGINT PRIMARY KEY,
  entity VARCHAR NOT NULL,
  counterparty_entity VARCHAR,
  FOREIGN KEY (entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (counterparty_entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE event_participants (
  participant VARCHAR,
  event_id BIGINT,
  FOREIGN KEY (participant) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE quotes (
  id BIGINT PRIMARY KEY,
  entity VARCHAR,
  FOREIGN KEY (entity) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE graph_edges (
  id BIGINT PRIMARY KEY,
  source VARCHAR,
  target VARCHAR,
  FOREIGN KEY (source) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (target) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE hyper_edges (
  id BIGINT PRIMARY KEY,
  source VARCHAR,
  target VARCHAR,
  FOREIGN KEY (source) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (target) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE hyper_incidences (
  edge_id BIGINT,
  entity_name VARCHAR,
  FOREIGN KEY (edge_id) REFERENCES hyper_edges(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE speaker_identity (
  person_name VARCHAR,
  FOREIGN KEY (person_name) REFERENCES entities(name) ON DELETE RESTRICT ON UPDATE RESTRICT
);
"""

TABLES = [
    "entities",
    "entity_tags",
    "events",
    "event_participants",
    "quotes",
    "graph_edges",
    "hyper_edges",
    "hyper_incidences",
    "speaker_identity",
]

SEED = {
    "entities": [("A",), ("B",)],
    "entity_tags": [("x", "A")],
    "events": [(1, "A", None), (2, "A", "B"), (3, "B", None)],
    "event_participants": [("A", 1), ("B", 2), ("B", 3)],
    "quotes": [(1, "A"), (2, "B")],
    "graph_edges": [(1, "A", "B"), (2, "B", "A"), (3, "B", "B")],
    "hyper_edges": [(1, "A", "B")],
    "hyper_incidences": [(1, "A")],
    "speaker_identity": [("A",)],
}


def _seed_sqlite(conn: sqlite3.Connection) -> None:
    conn.executescript(SQLITE_DDL)
    for table, rows in SEED.items():
        cols = ",".join(c for c in _cols_sqlite(conn, table))
        marks = ",".join("?" for _ in rows[0])
        conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})", rows)
    conn.commit()


def _cols_sqlite(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _seed_duckdb(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DUCKDB_DDL)
    for table, rows in SEED.items():
        cols = ",".join(c for c in _cols_duckdb(con, table))
        marks = ",".join("?" for _ in rows[0])
        con.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})", rows)


def _cols_duckdb(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]


def _snapshot_sqlite(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    snap = {}
    for t in TABLES:
        snap[t] = sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall())
    return snap


def _snapshot_duckdb(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple]]:
    snap = {}
    for t in TABLES:
        snap[t] = sorted(tuple(r) for r in con.execute(f"SELECT * FROM {t}").fetchall())
    return snap


@pytest.fixture
def engines(tmp_path):
    sq = sqlite3.connect(tmp_path / "fx.db")
    _seed_sqlite(sq)
    dk = duckdb.connect(str(tmp_path / "fx.duckdb"))
    _seed_duckdb(dk)
    yield sq, dk
    sq.close()
    dk.close()


def test_drop_entity_full_equivalence(engines):
    sq, dk = engines

    sq.execute("PRAGMA foreign_keys = ON")
    sq.execute("DELETE FROM entities WHERE name = 'A'")
    sq.commit()

    drop_entity(dk, "A")
    assert _snapshot_duckdb(dk) == _snapshot_sqlite(sq)


def test_drop_entity_chain_effects(engines):
    sq, dk = engines
    drop_entity(dk, "A")
    # chain: entities -> events -> event_participants
    assert (
        dk.execute("SELECT count(*) FROM event_participants WHERE event_id IN (1,2)").fetchone()[0]
        == 0
    )
    # chain: entities -> hyper_edges -> hyper_incidences
    assert dk.execute("SELECT count(*) FROM hyper_incidences WHERE edge_id = 1").fetchone()[0] == 0
    assert (
        dk.execute(
            "SELECT count(*) FROM hyper_edges WHERE source = 'A' OR target = 'A'"
        ).fetchone()[0]
        == 0
    )
    # SET NULL: speaker_identity row remains, person_name -> NULL
    row = dk.execute("SELECT person_name FROM speaker_identity").fetchall()
    assert row == [(None,)]


def test_drop_entity_preserves_untouched(engines):
    _, dk = engines
    drop_entity(dk, "A")
    # B-owned rows untouched
    assert dk.execute("SELECT name FROM entities").fetchall() == [("B",)]
    assert dk.execute("SELECT id FROM events WHERE entity = 'B'").fetchall() == [(3,)]
    assert dk.execute("SELECT COUNT(*) FROM quotes WHERE entity = 'B'").fetchone()[0] == 1
    assert (
        dk.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source = 'B' OR target = 'B'"
        ).fetchone()[0]
        == 1
    )


def test_rename_entity_full_equivalence(engines):
    sq, dk = engines

    sq.execute("PRAGMA foreign_keys = ON")
    sq.execute("UPDATE entities SET name = 'AZ' WHERE name = 'A'")
    sq.commit()

    rename_entity(dk, "A", "AZ")
    assert _snapshot_duckdb(dk) == _snapshot_sqlite(sq)


def test_rename_entity_propagates_all_columns(engines):
    _, dk = engines
    rename_entity(dk, "A", "AZ")
    assert dk.execute("SELECT name FROM entities ORDER BY name").fetchall() == [("AZ",), ("B",)]
    assert dk.execute("SELECT entity_name FROM entity_tags").fetchall() == [("AZ",)]
    assert dk.execute("SELECT entity FROM events ORDER BY id").fetchall() == [
        ("AZ",),
        ("AZ",),
        ("B",),
    ]
    assert dk.execute("SELECT entity FROM quotes ORDER BY id").fetchall() == [("AZ",), ("B",)]
    assert dk.execute("SELECT source, target FROM graph_edges ORDER BY id").fetchall() == [
        ("AZ", "B"),
        ("B", "AZ"),
        ("B", "B"),
    ]
    assert dk.execute("SELECT source, target FROM hyper_edges").fetchall() == [("AZ", "B")]
    assert dk.execute("SELECT entity_name FROM hyper_incidences").fetchall() == [("AZ",)]
    assert dk.execute("SELECT person_name FROM speaker_identity").fetchall() == [("AZ",)]


def test_drop_entity_statement_order(engines):
    _, dk = engines
    stmts = drop_entity(dk, "A")
    # SET NULL updates come first, the entity's own delete last
    assert stmts[0].startswith('UPDATE "events" SET "counterparty_entity"')
    assert stmts[-1] == "DELETE FROM entities WHERE name = ?"
    assert any("DELETE FROM events WHERE entity = ?" in s for s in stmts)
    assert any('DELETE FROM "hyper_incidences" WHERE "edge_id" IN' in s for s in stmts)
