"""part_of/has_company retirement — the canonical store and its projection.

Two contracts under test:

1. ``helpers/graph/sector_membership.py`` — the ONE write path
   (put/remove/idempotence/dual membership/member-count upkeep).
2. ``helpers/graph/query.py::_materialise_sector_membership`` — the
   compatibility projection that keeps ``e_belongs``/``e_has`` (and every
   consumer: onager, igraph, walk substrate, snapshots, API) alive with
   zero changes, now sourced from the incidence store.

The dual-membership case is the load-bearing one: it is the information the
retired dyad pair could not carry (Kuantum Papers in live data).
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers.core.db import connect
from helpers.graph import query
from helpers.graph.sector_membership import (
    put_sector_membership,
    remove_sector_membership,
    sector_membership_rows,
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
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
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
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
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
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(edge_type, label)
);
CREATE TABLE hyper_incidences (
    edge_id INTEGER NOT NULL,
    entity_name TEXT NOT NULL,
    weight REAL,
    direction TEXT,
    role TEXT,
    valid_from DATE,
    valid_to DATE,
    PRIMARY KEY (edge_id, entity_name)
);
"""


@pytest.fixture
def db(tmp_path):
    """Entities (2 sectors, 2 companies) + one supplier edge for the substrate."""
    path = tmp_path / "research.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            [
                ("Agriculture", "sector"),
                ("Packaging", "sector"),
                ("Kuantum Papers", "company"),
                ("Avanti Feeds", "company"),
            ],
        )
        con.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('Avanti Feeds', 'Kuantum Papers', 'supplier_to', 'test')"
        )
        con.commit()
    finally:
        con.close()
    return path


# --------------------------------------------------------------------------- #
# The write path
# --------------------------------------------------------------------------- #
def test_put_creates_hyperedge_and_incidence(db):
    con = connect(db)
    try:
        assert put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="test")
        con.commit()
        rows = sector_membership_rows(con, "Kuantum Papers")
        assert rows == [("Kuantum Papers", "Agriculture")]
        n = con.execute(
            "SELECT properties FROM hyper_edges WHERE edge_type='sector' AND label='Agriculture'"
        ).fetchone()[0]
        assert '"n_members": 1' in n
    finally:
        con.close()


def test_put_is_idempotent(db):
    con = connect(db)
    try:
        assert put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="a")
        assert not put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="b")
        con.commit()
        assert len(sector_membership_rows(con, "Kuantum Papers")) == 1
        # source_ref converges to the latest writer
        ref = con.execute(
            "SELECT source_ref FROM hyper_edges WHERE edge_type='sector' AND label='Agriculture'"
        ).fetchone()[0]
        assert ref == "b"
    finally:
        con.close()


def test_put_preserve_source_ref_skips_edge_convergence(db):
    """Bulk writers (sync_tags traverses every sector roster) must record
    the incidence WITHOUT restamping the shared edge's provenance — the
    explicit escape hatch from the latest-writer-wins contract above."""
    con = connect(db)
    try:
        assert put_sector_membership(
            con, "Kuantum Papers", "Agriculture", source_ref="derive:hyperedges:part_of"
        )
        # Second call: same incidence exists, but a bulk-sweep write with
        # preserve=True must not touch the edge's source_ref.
        assert not put_sector_membership(
            con,
            "Kuantum Papers",
            "Agriculture",
            source_ref="sync_tags",
            preserve_source_ref=True,
        )
        con.commit()
        ref = con.execute(
            "SELECT source_ref FROM hyper_edges WHERE edge_type='sector' AND label='Agriculture'"
        ).fetchone()[0]
        assert ref == "derive:hyperedges:part_of"
        assert sector_membership_rows(con, "Kuantum Papers") == [("Kuantum Papers", "Agriculture")]
    finally:
        con.close()


def test_cli_put_and_remove_lands_membership(db, capsys):
    """The D17-facing CLI: an agent-authored stub note lands its membership
    through `python3 -m helpers.graph.sector_membership put` (and removes
    it via `remove`)."""
    from helpers.graph.sector_membership import _main

    assert _main(["put", "Kuantum Papers", "Agriculture", "--db", str(db)]) == 0
    con = connect(db)
    try:
        assert sector_membership_rows(con, "Kuantum Papers") == [("Kuantum Papers", "Agriculture")]
    finally:
        con.close()

    assert _main(["remove", "Kuantum Papers", "Agriculture", "--db", str(db)]) == 0
    con = connect(db)
    try:
        assert sector_membership_rows(con, "Kuantum Papers") == []
    finally:
        con.close()
    capsys.readouterr()


def test_dual_membership_is_representable(db):
    """THE retirement argument: one company, two sectors — impossible with a
    single-valued ``part_of`` row, natural here."""
    con = connect(db)
    try:
        put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="test")
        put_sector_membership(con, "Kuantum Papers", "Packaging", source_ref="test")
        con.commit()
        assert [r[1] for r in sector_membership_rows(con, "Kuantum Papers")] == [
            "Agriculture",
            "Packaging",
        ]
    finally:
        con.close()


def test_remove_drops_only_that_membership(db):
    con = connect(db)
    try:
        put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="test")
        put_sector_membership(con, "Kuantum Papers", "Packaging", source_ref="test")
        con.commit()
        assert remove_sector_membership(con, "Kuantum Papers", "Agriculture")
        con.commit()
        assert [r[1] for r in sector_membership_rows(con, "Kuantum Papers")] == ["Packaging"]
        assert not remove_sector_membership(con, "Kuantum Papers", "Agriculture")
    finally:
        con.close()


def test_remove_unknown_sector_is_a_noop(db):
    con = connect(db)
    try:
        assert not remove_sector_membership(con, "Kuantum Papers", "Ghost Sector")
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# The projection (query.py) — every dyadic consumer rides this
# --------------------------------------------------------------------------- #
def _build(db):
    con = query.connect(db, rebuild=True)
    return con


def test_projection_serves_both_directions(db):
    con = connect(db)
    try:
        put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="test")
        con.commit()
    finally:
        con.close()
    con = _build(db)
    try:
        # e_belongs: company -> sector ; e_has: sector -> company
        assert con.execute("SELECT COUNT(*) FROM e_belongs").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM e_has").fetchone()[0] == 1
        # and NO stored dyads (the retirement contract)
        assert (
            con.execute(
                "SELECT COUNT(*) FROM fin.graph_edges WHERE edge_type IN ('part_of','has_company')"
            ).fetchone()[0]
            == 0
        )
    finally:
        con.close()


def test_projection_dual_membership_and_sector_of(db):
    con = connect(db)
    try:
        put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="test")
        put_sector_membership(con, "Kuantum Papers", "Packaging", source_ref="test")
        con.commit()
    finally:
        con.close()
    con = _build(db)
    try:
        sectors = sorted(
            r[0]
            for r in con.execute(
                "SELECT v_s.name FROM e_belongs e "
                "JOIN v_node v_c ON v_c.id = e.company_name "
                "JOIN v_node v_s ON v_s.id = e.sector_name "
                "WHERE v_c.name = 'Kuantum Papers'"
            ).fetchall()
        )
        assert sectors == ["Agriculture", "Packaging"]
        assert len(query.sector_members(con, "Agriculture")) == 1
        assert len(query.sector_members(con, "Packaging")) == 1
    finally:
        con.close()


def test_projection_reaches_the_walk_substrate(db):
    """e_dir/e_all_und must still cross sector boundaries after the
    retirement — otherwise shortest_path/diameter silently change."""
    con = connect(db)
    try:
        put_sector_membership(con, "Kuantum Papers", "Agriculture", source_ref="test")
        con.commit()
    finally:
        con.close()
    con = _build(db)
    try:
        types = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT edge_type FROM e_dir WHERE edge_type IN "
                "('part_of','has_company','supplier_to')"
            ).fetchall()
        }
        assert types == {"part_of", "has_company", "supplier_to"}
    finally:
        con.close()


def test_projection_empty_store_is_safe(tmp_path):
    """No incidences at all: the projection is empty, the build still works
    (pristine-clone posture — never block on absent data)."""
    db = tmp_path / "empty.db"
    seed = connect(db)
    try:
        seed.executescript(_DDL)
        seed.commit()
    finally:
        seed.close()
    con = _build(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM e_belongs").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM e_has").fetchone()[0] == 0
    finally:
        con.close()
        assert db.exists()


def test_projection_ordering_is_stable(db):
    """Repeated builds must not reshuffle membership (id stability is the
    contract every reader's exclusivity assumption rests on)."""
    con = connect(db)
    try:
        for sector in ("Agriculture", "Packaging"):
            for company in ("Kuantum Papers", "Avanti Feeds"):
                put_sector_membership(con, company, sector, source_ref="test")
        con.commit()
    finally:
        con.close()
    first = _build(db)
    try:
        a = first.execute(
            "SELECT company_name, sector_name FROM e_belongs ORDER BY 1, 2"
        ).fetchall()
    finally:
        first.close()
    second = _build(db)
    try:
        b = second.execute(
            "SELECT company_name, sector_name FROM e_belongs ORDER BY 1, 2"
        ).fetchall()
    finally:
        second.close()
    assert a == b
    assert len(a) == 4


def test_retire_migration_refuses_unabsorbed(tmp_path):
    """The apply gate: a part_of row with no incidence must STOP the
    migration — losing membership is worse than a failed run."""
    from helpers.maintenance.retire_sector_dyads import retire

    path = tmp_path / "gate.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.execute("INSERT INTO entities (name, entity_type) VALUES ('Banking', 'sector')")
        con.execute("INSERT INTO entities (name, entity_type) VALUES ('Lone Co', 'company')")
        con.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('Lone Co', 'Banking', 'part_of', 'test')"
        )
        con.commit()
    finally:
        con.close()
    stats = retire(path, apply=True)
    assert stats["unabsorbed_part_of"] == 1
    # nothing deleted
    con = sqlite3.connect(str(path))
    try:
        assert (
            con.execute("SELECT COUNT(*) FROM graph_edges WHERE edge_type='part_of'").fetchone()[0]
            == 1
        )
    finally:
        con.close()
