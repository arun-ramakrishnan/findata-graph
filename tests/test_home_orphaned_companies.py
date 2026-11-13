#!/usr/bin/env python3
"""Tests for helpers/maintenance/home_orphaned_companies.py.

Covers: sector-home plan detection, idempotent apply (sector incidences +
person re-typing), unknown-sector refusal, and no-write dry-run.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance import home_orphaned_companies as hoc  # noqa: E402


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "research.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT,
            ticker TEXT,
            file_path TEXT
        );
        CREATE TABLE hyper_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edge_type TEXT,
            label TEXT,
            weight REAL,
            valid_from DATE,
            valid_to DATE,
            source_ref TEXT,
            properties TEXT,
            created_at DATETIME,
            agent_id TEXT,
            source_tier TEXT
        );
        CREATE TABLE hyper_incidences (
            edge_id INTEGER,
            entity_name TEXT,
            weight REAL,
            direction TEXT,
            role TEXT,
            valid_from DATE,
            valid_to DATE,
            PRIMARY KEY (edge_id, entity_name)
        );
        """
    )
    conn.execute("INSERT INTO hyper_edges (edge_type, label) VALUES ('sector', 'Automotive')")
    conn.executemany(
        "INSERT INTO hyper_edges (edge_type, label) VALUES ('sector', ?)",
        [(label,) for label in sorted({v for v in hoc.SECTOR_HOMES.values() if v != "Automotive"})],
    )
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        [(name, "company") for name in hoc.SECTOR_HOMES]
        + [
            ("Achyut Dhadphale", "company"),
            ("G Sekhar - Non Executive Director - EGF", "company"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_plan_detects_homeless_and_missing_sectors(db):
    conn = hoc._connect(db)
    missing, to_add = hoc._plan(conn)
    conn.close()
    assert missing == []  # fixture seeds every SECTOR_HOMES label
    assert set(to_add) == set(hoc.SECTOR_HOMES)


def test_refuses_unknown_sector(db):
    original = hoc.SECTOR_HOMES
    hoc.SECTOR_HOMES = {"Tata AutoComp Systems": "Not_A_Sector"}
    try:
        with pytest.raises(SystemExit, match="fatal"):
            hoc.apply(db)
    finally:
        hoc.SECTOR_HOMES = original


def test_apply_homes_and_retypes_idempotently(db):
    result = hoc.apply(db)
    assert result["companies_homed"] == 12
    assert result["sector_incidences_inserted"] == 12
    assert result["persons_retyped"] == 2

    conn = hoc._connect(db)
    row = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences hi "
        "JOIN hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.edge_type='sector'",
    ).fetchone()[0]
    assert row == 12
    n_person = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE entity_type='person' AND name IN (?, ?)",
        ("Achyut Dhadphale", "G Sekhar - Non Executive Director - EGF"),
    ).fetchone()[0]
    assert n_person == 2
    conn.close()

    # Idempotent.
    again = hoc.apply(db)
    assert again["companies_homed"] == 0
    assert again["sector_incidences_inserted"] == 0
    assert again["persons_retyped"] == 0
