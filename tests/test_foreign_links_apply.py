"""Tests for foreign_links_apply.py (S3 registry apply).

Covers: link loading, entity validation (unknown dropped), proposed-row
building (alias + isin), conflict scanning (existing different owner), and
idempotent apply (INSERT OR IGNORE semantics).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from helpers.maintenance.foreign_links_apply import (
    apply_candidates,
    build_proposed,
    load_links,
    scan_conflicts,
    validate_entities,
)


@pytest.fixture()
def conn():
    con = sqlite3.connect(":memory:")
    con.execute(
        """
        CREATE TABLE entities (name TEXT PRIMARY KEY)
        """
    )
    con.execute(
        """
        CREATE TABLE entity_identifiers (
            entity_name TEXT NOT NULL REFERENCES entities(name),
            identifier_type TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            namespace TEXT,
            source_ref TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_updated DATETIME,
            PRIMARY KEY (entity_name, identifier_type, identifier_value),
            UNIQUE (identifier_type, identifier_value)
        )
        """
    )
    con.execute("INSERT INTO entities (name) VALUES ('Tencent')")
    con.execute("INSERT INTO entities (name) VALUES ('Samsung Electronics')")
    con.execute("INSERT INTO entities (name) VALUES ('Reliance Industries')")
    con.commit()
    yield con
    con.close()


LINKS = {
    "Tencent": [
        {
            "exchange": "HKEX",
            "symbol": "00700",
            "isin": "KYG875721634",
            "source": "foreign_links_lane",
        },
    ],
    "Samsung Electronics": [
        {"exchange": "KRX", "symbol": "005930.KS", "source": "foreign_links_lane"},
    ],
    "No Such Entity": [
        {"exchange": "HKEX", "symbol": "99999", "source": "foreign_links_lane"},
    ],
}


def test_load_links(tmp_path):
    p = tmp_path / "links.json"
    p.write_text(json.dumps(LINKS))
    assert load_links(p) == LINKS


def test_load_links_rejects_non_object(tmp_path):
    p = tmp_path / "links.json"
    p.write_text("[1,2]")
    with pytest.raises(ValueError):
        load_links(p)


def test_validate_entities_drops_unknown(conn):
    valid, unknown = validate_entities(conn, LINKS)
    assert "No Such Entity" in unknown
    assert "Tencent" in valid
    assert "Samsung Electronics" in valid


def test_build_proposed_types_namespaces():
    proposed = build_proposed(LINKS)
    assert ("Tencent", "alias", "HKEX") in proposed
    assert ("Tencent", "isin", "HKEX") in proposed
    assert ("Samsung Electronics", "alias", "KRX") in proposed
    assert proposed[("Tencent", "alias", "HKEX")] == {"00700"}
    assert proposed[("Tencent", "isin", "HKEX")] == {"KYG875721634"}


def test_scan_conflicts_reports_foreign_owner(conn):
    proposed = build_proposed(LINKS)
    # existing 'isin' row owned by another entity
    conn.execute(
        "INSERT INTO entity_identifiers (entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('Reliance Industries', 'isin', 'KYG875721634', 'HKEX', 'exchange_sync')"
    )
    conn.commit()
    assert scan_conflicts(conn, proposed) == 1


def test_scan_conflicts_zero_when_owned_by_same(conn):
    proposed = build_proposed(LINKS)
    conn.execute(
        "INSERT INTO entity_identifiers (entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('Tencent', 'isin', 'KYG875721634', 'HKEX', 'exchange_sync')"
    )
    conn.commit()
    assert scan_conflicts(conn, proposed) == 0


def test_apply_is_idempotent(conn):
    valid, _unknown = validate_entities(conn, LINKS)
    proposed = build_proposed(valid)
    first = apply_candidates(conn, proposed)
    assert first == 3  # 2 alias + 1 isin
    second = apply_candidates(conn, proposed)
    assert second == 0
    rows = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]
    assert rows == 3
