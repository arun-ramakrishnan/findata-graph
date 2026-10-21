#!/usr/bin/env python3
"""derive_org_membership (person_ontology_spine S3) — the ORG-Membership
convention lane.

Pins the contract measured live 2026-10-19 (34 companies / 287 members /
promoter 205 + holder 82): role is a per-row property read, the latest
filing wins per (company, person), the lane OWNS its family (shrinkage
converges), institutions and non-company targets are out of scope, and
the provenance FK gates unregistered agents.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from helpers.core.db import connect
from helpers.graph.derive_org_membership import (
    AGENT_ID,
    MEMBERSHIP_HYPER_TYPE,
    SOURCE_REF,
    _role_for_category,
    apply_derivation,
    collect,
    derive,
)

_DDL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT,
    last_updated DATETIME
);
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type)
);
CREATE TABLE hyper_edges (
    id INTEGER PRIMARY KEY,
    edge_type TEXT NOT NULL,
    label TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    agent_id TEXT REFERENCES provenance_agents(agent_id),
    source_tier TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(edge_type, label),
    CHECK (json_valid(properties))
);
CREATE TABLE hyper_incidences (
    edge_id INTEGER NOT NULL REFERENCES hyper_edges(id) ON DELETE CASCADE,
    entity_name TEXT NOT NULL REFERENCES entities(name),
    weight REAL,
    direction TEXT,
    role TEXT,
    valid_from DATE,
    valid_to DATE,
    PRIMARY KEY (edge_id, entity_name)
);
CREATE TABLE provenance_agents (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    repo_ref TEXT,
    script TEXT,
    command TEXT,
    as_of TEXT NOT NULL
);
"""


@pytest.fixture
def db(tmp_path):
    """Two persons at one company (one promoter, one holder) + one
    institution row that must stay out of scope. graph_edges UNIQUEs
    (source, target, edge_type), so filings collapse at ingest — the
    fixture mirrors that shape (one row per pair)."""
    path = tmp_path / "research.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.execute(
            "INSERT INTO provenance_agents (agent_id, name, version, as_of) "
            "VALUES (?, 'lane', 't', '2026-01-01')",
            (AGENT_ID,),
        )
        con.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            [
                ("Alpha Ltd", "company"),
                ("Beta Ltd", "company"),
                ("Ravi Kumar", "person"),
                ("Sana Sheikh", "person"),
                ("Big Inst", "institution"),
            ],
        )

        def edge(src, tgt, cat, stake, vf, ref):
            con.execute(
                "INSERT INTO graph_edges (source, target, edge_type, properties, "
                "valid_from, source_ref) VALUES (?, ?, 'invested_in', ?, ?, ?)",
                (src, tgt, json.dumps({"category": cat, "stake_pct": stake}), vf, ref),
            )

        edge("Ravi Kumar", "Alpha Ltd", "Promoter Group", 6.0, "2026-09-30", "nse:shp:new")
        edge(
            "Sana Sheikh",
            "Alpha Ltd",
            "Resident Individuals holding nominal",
            1.2,
            "2026-09-30",
            "nse:shp:s2",
        )
        edge("Ravi Kumar", "Beta Ltd", "Promoter", 51.0, "2026-09-30", "nse:shp:s3")
        edge("Big Inst", "Alpha Ltd", "BodiesCorporate", 2.0, "2026-09-30", "nse:shp:s4")
        con.commit()
    finally:
        con.close()
    return path


def test_role_for_category():
    assert _role_for_category("Promoter Group") == "promoter"
    assert _role_for_category("Promoter") == "promoter"
    assert _role_for_category("Resident Individuals holding nominal") == "holder"
    assert _role_for_category("IndividualsOrHUF") == "holder"
    assert _role_for_category(None) == "holder"
    assert _role_for_category("") == "holder"


def test_collect_folds_pairs_and_skips_institutions(db):
    con = connect(db, read_only=True)
    try:
        members = collect(con)
    finally:
        con.close()
    assert set(members) == {"Alpha Ltd", "Beta Ltd"}
    assert set(members["Alpha Ltd"]) == {"Ravi Kumar", "Sana Sheikh"}
    ravi = members["Alpha Ltd"]["Ravi Kumar"]
    assert ravi["role"] == "promoter"
    assert ravi["weight"] == 6.0
    assert ravi["valid_from"] == "2026-09-30"
    assert members["Beta Ltd"]["Ravi Kumar"]["role"] == "promoter"


def test_dry_run_writes_nothing(db):
    con = connect(db)
    try:
        members = collect(con)
        stats = apply_derivation(con, members, dry_run=True)
        assert stats["new_edges"] == 2
        assert stats["new_incidences"] == 3
        assert con.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 0
    finally:
        con.close()


def test_apply_stamps_family_rows(db):
    con = connect(db)
    try:
        apply_derivation(con, collect(con), dry_run=False)
        con.commit()
        rows = con.execute(
            "SELECT label, source_ref, agent_id, source_tier, properties "
            "FROM hyper_edges WHERE edge_type = ? ORDER BY label",
            (MEMBERSHIP_HYPER_TYPE,),
        ).fetchall()
        assert [r[0] for r in rows] == ["Alpha Ltd", "Beta Ltd"]
        for _label, ref, agent, tier, props in rows:
            assert ref == SOURCE_REF
            assert agent == AGENT_ID
            assert tier == "derive"
            assert json.loads(props)["n_members"] >= 1
        inc = con.execute(
            "SELECT hi.entity_name, hi.role, hi.weight, hi.valid_from FROM "
            "hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
            "WHERE he.edge_type = ? ORDER BY he.label, hi.entity_name",
            (MEMBERSHIP_HYPER_TYPE,),
        ).fetchall()
        assert [tuple(r) for r in inc] == [
            ("Ravi Kumar", "promoter", 6.0, "2026-09-30"),
            ("Sana Sheikh", "holder", 1.2, "2026-09-30"),
            ("Ravi Kumar", "promoter", 51.0, "2026-09-30"),
        ]
    finally:
        con.close()


def test_apply_is_idempotent(db):
    con = connect(db)
    try:
        apply_derivation(con, collect(con), dry_run=False)
        con.commit()
        stats = apply_derivation(con, collect(con), dry_run=False)
        assert stats["new_edges"] == 0
        assert stats["new_incidences"] == 0
        assert stats["dropped_edges"] == []
        assert (
            con.execute(
                "SELECT COUNT(*) FROM hyper_incidences hi JOIN hyper_edges he "
                "ON he.id = hi.edge_id WHERE he.edge_type = ?",
                (MEMBERSHIP_HYPER_TYPE,),
            ).fetchone()[0]
            == 3
        )
    finally:
        con.close()


def test_shrinkage_converges(db):
    """The lane OWNS the family: a member gone from invested_in is gone
    from the family, and a memberless company's hyperedge is dropped."""
    con = connect(db)
    try:
        apply_derivation(con, collect(con), dry_run=False)
        con.commit()
        con.execute("DELETE FROM graph_edges WHERE source='Sana Sheikh'")
        con.commit()
        stats = apply_derivation(con, collect(con), dry_run=False)
        assert "Alpha Ltd" not in stats["dropped_edges"]
        names = {
            r[0]
            for r in con.execute(
                "SELECT hi.entity_name FROM hyper_incidences hi JOIN hyper_edges he "
                "ON he.id = hi.edge_id WHERE he.edge_type = ?",
                (MEMBERSHIP_HYPER_TYPE,),
            )
        }
        assert names == {"Ravi Kumar"}
        con.execute("DELETE FROM graph_edges WHERE edge_type='invested_in'")
        con.commit()
        stats = apply_derivation(con, collect(con), dry_run=False)
        assert stats["dropped_edges"] == ["Alpha Ltd", "Beta Ltd"]
        assert (
            con.execute(
                "SELECT COUNT(*) FROM hyper_edges WHERE edge_type = ?",
                (MEMBERSHIP_HYPER_TYPE,),
            ).fetchone()[0]
            == 0
        )
    finally:
        con.close()


def test_unregistered_agent_is_refused(tmp_path):
    """The provenance FK is the gate: no provenance_agents row, no write."""
    path = tmp_path / "gate.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.execute("INSERT INTO entities (name, entity_type) VALUES ('C1', 'company')")
        con.execute("INSERT INTO entities (name, entity_type) VALUES ('P1', 'person')")
        con.execute(
            "INSERT INTO graph_edges (source, target, edge_type, properties, "
            "valid_from, source_ref) VALUES ('P1', 'C1', 'invested_in', ?, '2026-01-01', 'x')",
            (json.dumps({"category": "Promoter", "stake_pct": 9.0}),),
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            apply_derivation(con, collect(con), dry_run=False)
    finally:
        con.close()


def test_derive_end_to_end_reports(db, capsys):
    stats = derive(db)  # dry-run path, report printed
    assert stats["companies"] == 2
    out = capsys.readouterr().out
    assert "companies=2 members=3" in out
    assert "[dry-run]" in out
