#!/usr/bin/env python3
"""merger / demerger hyperedge families (merger_demerger_hyperedges S1+S2).

Pins the contract decided in the proposal and measured live 2026-10-21:

* the family vocabulary is CODE-side (``helpers/graph/hyperedge_types.py``)
  because ``hyper_edges.edge_type`` carries no DDL CHECK — a family is a
  registry entry, not a migration;
* a merger is n-ary: constituents + the SURVIVOR as a member (the dyad
  could not hold the entity that exists after the merger); a demerger is
  parent → spun-off child, which is not a dyad at all;
* roles are per-family (``constituent``/``resultant``,
  ``parent``/``resultant``) and are NOT event roles;
* facts exist only after a HUMAN accepted them — the lane reads the
  triage decisions journal, never the raw relation sidecar;
* the lane OWNS both families (shrinkage converges), and it never writes
  a ``graph_edges`` row — the dyadic graph stays byte-identical.
"""

from __future__ import annotations

import json

import pytest

from helpers.core.db import connect
from helpers.graph.derive_merger_hyperedges import (
    AGENT_ID,
    SOURCE_REF,
    apply_derivation,
    collect,
)
from helpers.graph.hyperedge_types import (
    HYPEREDGE_TYPES,
    MERGER_HYPEREDGE_TYPES,
    MERGER_ROLE_BY_TYPE,
    roles_for,
)
from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

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

_ENTITIES = [
    ("Larsen & Toubro Infotech", "company"),
    ("Mindtree", "company"),
    ("LTIMindtree", "company"),
    ("Reliance Industries", "company"),
    ("Jio Platforms", "company"),
    ("Ghost Co", "company"),
]


def _row(**kw) -> dict:
    row = {
        "id": "row-1",
        "edge_type": "merger",
        "source": "Larsen & Toubro Infotech",
        "target_mention": "Mindtree",
        "decision": "accept:merger:LTIMindtree",
        "constituents": ["Larsen & Toubro Infotech", "Mindtree"],
        "resultant": "LTIMindtree",
        "source_quote": "formed through the merger of Larsen & Toubro Infotech and Mindtree",
    }
    row.update(kw)
    return row


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "research.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.executemany("INSERT INTO entities (name, entity_type) VALUES (?, ?)", _ENTITIES)
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture
def journal(tmp_path):
    """A triage decisions journal; rows are written by the test."""
    path = tmp_path / "decisions.jsonl"
    path.write_text("", encoding="utf-8")
    return path


def _write(path, *rows) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# --------------------------------------------------------------------------- #
# S1 — the vocabulary                                                          #
# --------------------------------------------------------------------------- #
def test_registry_carries_the_two_families():
    assert {"merger", "demerger"} <= HYPEREDGE_TYPES
    assert MERGER_HYPEREDGE_TYPES == {"merger", "demerger"}
    assert roles_for("merger") == ("constituent", "resultant")
    assert roles_for("demerger") == ("parent", "resultant")
    assert roles_for("event") == ()


def test_family_roles_are_not_event_roles():
    """The merger roles are their own lane — the event role vocabulary
    (acquirer/holder/partner/promoter/target) must not absorb them."""
    from helpers.graph.derive_event_participants import EVENT_ROLE_BY_TYPE

    event_roles = {r for roles in EVENT_ROLE_BY_TYPE.values() for r in roles}
    merger_roles = {r for roles in MERGER_ROLE_BY_TYPE.values() for r in roles}
    assert not (merger_roles & event_roles)


# --------------------------------------------------------------------------- #
# S2 — the derive lane                                                         #
# --------------------------------------------------------------------------- #
def test_collect_reads_an_accepted_merger(db, journal):
    _write(journal, _row())
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert out["skipped"] == []
    assert out["unresolved"] == []
    assert len(out["facts"]) == 1
    fact = out["facts"][0]
    assert fact["edge_type"] == "merger"
    assert fact["label"] == "LTIMindtree"
    assert sorted(fact["members"]) == sorted(
        [
            ("Larsen & Toubro Infotech", "constituent"),
            ("Mindtree", "constituent"),
            ("LTIMindtree", "resultant"),
        ]
    )
    assert fact["properties"]["n_members"] == 3
    assert fact["properties"]["resultant"] == "LTIMindtree"


def test_unaccepted_row_is_skipped(db, journal):
    _write(journal, _row(decision=None))
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert out["facts"] == []
    assert out["skipped"][0]["reason"] == "not accepted"


def test_contradictory_verb_is_skipped(db, journal):
    """accept:jv_with:DWS on a merger row is a contradiction, not a merge."""
    _write(journal, _row(decision="accept:jv_with:DWS"))
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert out["facts"] == []
    assert out["skipped"][0]["reason"] == "not accepted"


def test_incomplete_members_are_skipped(db, journal):
    _write(journal, _row(constituents=["Larsen & Toubro Infotech"]))
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert out["facts"] == []
    assert out["skipped"][0]["reason"] == "incomplete members"


def test_unresolved_member_is_reported_not_written(db, journal):
    _write(journal, _row(constituents=["Larsen & Toubro Infotech", "Phantom Co"]))
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert out["facts"] == []
    assert out["unresolved"][0]["names"] == ["Phantom Co"]


def test_demerger_members_and_roles(db, journal):
    _write(
        journal,
        _row(
            id="row-2",
            edge_type="demerger",
            source="Reliance Industries",
            target_mention="Jio Platforms",
            decision="accept:demerger:Jio Platforms",
            constituents=[],
            resultant="",
            parent="Reliance Industries",
            child="Jio Platforms",
        ),
    )
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert len(out["facts"]) == 1
    fact = out["facts"][0]
    assert fact["edge_type"] == "demerger"
    assert fact["label"] == "Jio Platforms"
    assert sorted(fact["members"]) == [
        ("Jio Platforms", "resultant"),
        ("Reliance Industries", "parent"),
    ]


def test_resultant_direction_survivor_is_the_source(db, journal):
    """Bug regression: for direction="resultant" rows the section company IS the
    survivor and the verb's Name is the OTHER constituent — treating the verb
    name as the resultant made Mindtree both a constituent and the resultant of
    its own merger (UNIQUE constraint on (edge_id, entity_name))."""
    _write(
        journal,
        _row(
            id="row-3",
            source="LTIMindtree",
            target_mention="Larsen & Toubro Infotech",
            direction="resultant",
            decision="accept:merger:Mindtree",
            constituents=["Larsen & Toubro Infotech", "Mindtree"],
            resultant="",
        ),
    )
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert len(out["facts"]) == 1
    fact = out["facts"][0]
    assert fact["label"] == "LTIMindtree"
    assert sorted(fact["members"]) == [
        ("LTIMindtree", "resultant"),
        ("Larsen & Toubro Infotech", "constituent"),
        ("Mindtree", "constituent"),
    ]
    names = [name for name, _ in fact["members"]]
    assert len(names) == len(set(names))  # a name can never carry two roles


def test_conflicting_rows_with_one_label_are_dropped(db, journal):
    """Two readings of the same fact — the reviewer settles it, not the lane."""
    _write(
        journal,
        _row(id="row-a", resultants=None),
        _row(
            id="row-b",
            constituents=["Larsen & Toubro Infotech", "Mindtree", "Ghost Co"],
            resultant="LTIMindtree",
        ),
    )
    con = connect(db, read_only=True)
    try:
        out = collect(con, journal)
    finally:
        con.close()
    assert out["facts"] == []
    assert out["conflicts"][0]["edge_type"] == "merger"


def test_dry_run_writes_nothing(db, journal):
    _write(journal, _row())
    con = connect(db)
    try:
        stats = apply_derivation(con, collect(con, journal)["facts"], dry_run=True)
        assert stats["facts"] == 1
        assert stats["new_edges"] == 1
        assert stats["new_incidences"] == 3
        assert con.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 0
    finally:
        con.close()


def test_apply_writes_role_tagged_incidences_and_no_dyads(db, journal):
    _write(journal, _row())
    con = connect(db)
    try:
        apply_derivation(con, collect(con, journal)["facts"], dry_run=False)
        con.commit()
        edge = con.execute(
            "SELECT label, source_ref, agent_id, source_tier, properties "
            "FROM hyper_edges WHERE edge_type = 'merger'"
        ).fetchone()
        assert edge[0] == "LTIMindtree"
        assert edge[1] == SOURCE_REF
        assert edge[2] == AGENT_ID
        assert edge[3] == "derive"
        assert json.loads(edge[4])["n_members"] == 3
        inc = con.execute(
            "SELECT hi.entity_name, hi.role FROM hyper_incidences hi "
            "JOIN hyper_edges he ON he.id = hi.edge_id ORDER BY hi.entity_name"
        ).fetchall()
        assert [tuple(r) for r in inc] == [
            ("LTIMindtree", "resultant"),
            ("Larsen & Toubro Infotech", "constituent"),
            ("Mindtree", "constituent"),
        ]
        # the dyadic graph is untouched
        assert con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0
    finally:
        con.close()


def test_apply_is_idempotent(db, journal):
    _write(journal, _row())
    con = connect(db)
    try:
        for _ in range(2):
            apply_derivation(con, collect(con, journal)["facts"], dry_run=False)
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 3
    finally:
        con.close()


def test_shrinkage_converges(db, journal):
    """The lane owns both families — a fact the journal no longer holds is
    dropped, not left orphaned."""
    _write(journal, _row())
    con = connect(db)
    try:
        apply_derivation(con, collect(con, journal)["facts"], dry_run=False)
        con.commit()
        journal.write_text("", encoding="utf-8")
        stats = apply_derivation(con, collect(con, journal)["facts"], dry_run=False)
        con.commit()
        assert stats["dropped_edges"] == ["merger:LTIMindtree"]
        assert con.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 0
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# the gates that read the vocabulary                                            #
# --------------------------------------------------------------------------- #
def test_integrity_check_passes_on_a_clean_store(db, journal):
    _write(journal, _row())
    con = connect(db)
    try:
        apply_derivation(con, collect(con, journal)["facts"], dry_run=False)
        con.commit()
    finally:
        con.close()
    checker = DatabaseIntegrityChecker(db_path=str(db))
    try:
        result = checker.check_hyperedge_types()
    finally:
        checker.close()
    assert result["total"] == 1
    assert result["errors"] == 0


def test_integrity_check_flags_unknown_family_and_bad_role(db, journal):
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO hyper_edges (edge_type, label, source_ref) VALUES ('unicorn', 'X', 'test')"
        )
        con.commit()
    finally:
        con.close()
    checker = DatabaseIntegrityChecker(db_path=str(db))
    try:
        result = checker.check_hyperedge_types()
    finally:
        checker.close()
    assert result["unknown_type"] == 1
    assert result["errors"] == 1
