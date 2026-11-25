#!/usr/bin/env python3
"""Integration: the foreign-links apply chain (test_coverage_gap_sweep S5).

link JSON → load_links → validate_entities → build_proposed →
scan_conflicts → apply_candidates → idempotency, all against one
hermetic store. No live entities/registry reads.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import EDGES_12COL, ENTITIES_8COL  # noqa: E402

from helpers.maintenance.foreign_links_apply import (  # noqa: E402
    apply_candidates,
    build_proposed,
    load_links,
    scan_conflicts,
    validate_entities,
)

ENTITIES = ("Alpha Ltd", "Beta Ltd", "Gamma Ltd")
UNKNOWN = "Ghost Ltd"

IDENTIFIERS_DDL = """
CREATE TABLE entity_identifiers (
    entity_name      TEXT NOT NULL REFERENCES entities(name),
    identifier_type  TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    namespace        TEXT,
    source_ref       TEXT NOT NULL,
    last_updated     TEXT,
    PRIMARY KEY (entity_name, identifier_type, identifier_value)
)
"""


def _seed(conn: sqlite3.Connection) -> None:
    conn.executescript(ENTITIES_8COL)
    conn.executescript(EDGES_12COL)
    conn.executescript(IDENTIFIERS_DDL)
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [(e,) for e in ENTITIES],
    )
    conn.commit()


def _links_json(tmp_path: Path) -> Path:
    path = tmp_path / "foreign_links.json"
    path.write_text(
        json.dumps(
            {
                "Alpha Ltd": [
                    {"exchange": "HKEX", "symbol": "00700", "isin": "HK0000000070"},
                    {"exchange": "TWSE", "symbol": "2330"},
                ],
                UNKNOWN: [{"exchange": "HKEX", "symbol": "99999"}],
            }
        )
    )
    return path


@pytest.fixture()
def chain_store(tmp_path: Path):
    db = tmp_path / "chain.db"
    conn = sqlite3.connect(str(db))
    _seed(conn)
    yield db, conn
    conn.close()


def test_chain_validate_propose_apply_idempotent(chain_store, tmp_path: Path):
    _db, conn = chain_store
    links = load_links(_links_json(tmp_path))
    assert UNKNOWN in links

    valid, unknown = validate_entities(conn, links)
    assert unknown == [UNKNOWN]
    assert set(valid) == {"Alpha Ltd"}  # only entities present in the links doc

    proposed = build_proposed(valid)
    assert ("Alpha Ltd", "alias", "HKEX") in proposed
    assert ("Alpha Ltd", "isin", "HKEX") in proposed
    assert ("Alpha Ltd", "alias", "TWSE") in proposed
    assert proposed[("Alpha Ltd", "alias", "HKEX")] == {"00700"}

    assert scan_conflicts(conn, proposed) == 0
    added = apply_candidates(conn, proposed)
    assert added == 3  # HKEX alias + HKEX isin + TWSE alias

    rows = set(
        conn.execute(
            "SELECT entity_name, identifier_type, identifier_value, namespace "
            "FROM entity_identifiers"
        )
    )
    assert ("Alpha Ltd", "alias", "00700", "HKEX") in rows
    assert ("Alpha Ltd", "isin", "HK0000000070", "HKEX") in rows
    assert ("Alpha Ltd", "alias", "2330", "TWSE") in rows

    # idempotent: re-apply adds nothing
    assert apply_candidates(conn, proposed) == 0


def test_chain_conflicts_reported_never_overwritten(chain_store, tmp_path: Path):
    """A value owned by a different entity is reported and not applied."""
    _db, conn = chain_store
    conn.execute(
        "INSERT INTO entity_identifiers "
        "(entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('Beta Ltd', 'alias', '00700', 'HKEX', 'seed')"
    )
    conn.commit()

    links = load_links(_links_json(tmp_path))
    valid, _unknown = validate_entities(conn, links)
    proposed = build_proposed(valid)
    assert scan_conflicts(conn, proposed) == 1

    added = apply_candidates(conn, proposed)
    # INSERT OR IGNORE keys on (entity_name, type, value): the pre-seeded
    # Beta row has a different entity => Alpha's alias still lands; the
    # owner row is never overwritten.
    assert added == 3
    owner = conn.execute(
        "SELECT entity_name FROM entity_identifiers "
        "WHERE identifier_type='alias' AND identifier_value='00700' AND namespace='HKEX' "
        "ORDER BY entity_name"
    ).fetchall()
    assert owner == [("Alpha Ltd",), ("Beta Ltd",)]
