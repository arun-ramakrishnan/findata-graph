#!/usr/bin/env python3
"""P9 — SQLite mutation → graph rebuild → DuckDB integration tests.

Verifies the critical "write to SQLite, rebuild graph, query via DuckDB"
workflow:
  - Seed SQLite → rebuild DuckDB → query DuckDB → verify correct
  - Mutate SQLite (add entity/edge) → rebuild → verify new data visible
  - Mutate SQLite (delete entity) → rebuild → verify removal reflected

See doc/improvements/archive/testing/integration_plan.txt § Nice-to-have 6.
"""

from __future__ import annotations

import sqlite3

import pytest

pytestmark = [pytest.mark.integration]

from tests.schema import EDGES_12COL, ENTITIES_8COL, ENTITY_TAGS, EVENTS  # noqa: E402

# Local deviation: FK-guarded graph_analytics (differs from the shared
# tests/schema.py GRAPH_ANALYTICS) — kept local, not silently unified.
_GRAPH_ANALYTICS = """
CREATE TABLE graph_analytics (
    metric TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (metric, entity_name),
    FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE CASCADE
);
"""

_SCHEMA = "".join([ENTITIES_8COL, ENTITY_TAGS, EDGES_12COL, _GRAPH_ANALYTICS, EVENTS])


def _seed_sqlite(conn, companies, sectors, edges):
    """Populate the SQLite DB with entities + edges."""
    for name, etype, sector in companies + sectors:
        conn.execute(
            "INSERT INTO entities(name, entity_type, sector_classification) VALUES (?,?,?)",
            (name, etype, sector),
        )
    for source, target, etype in edges:
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) VALUES (?,?,?,'seed')",
            (source, target, etype),
        )
    conn.commit()


@pytest.fixture
def p9_db(tmp_path):
    """SQLite DB with seeded entities + edges for DuckDB rebuild testing."""
    db_path = str(tmp_path / "p9_rebuild.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    companies = [
        ("HDFC Bank", "company", "Banking"),
        ("ICICI Bank", "company", "Banking"),
        ("Infosys", "company", "Technology"),
        ("TCS", "company", "Technology"),
    ]
    sectors = [
        ("Banking", "sector", None),
        ("Technology", "sector", None),
    ]
    edges = [
        ("HDFC Bank", "Banking", "part_of"),
        ("ICICI Bank", "Banking", "part_of"),
        ("Infosys", "Technology", "part_of"),
        ("TCS", "Technology", "part_of"),
        ("Banking", "HDFC Bank", "has_company"),
        ("Banking", "ICICI Bank", "has_company"),
        ("Technology", "Infosys", "has_company"),
        ("Technology", "TCS", "has_company"),
    ]
    _seed_sqlite(conn, companies, sectors, edges)

    yield conn, db_path
    conn.close()


# --------------------------------------------------------------------------- #
# Helpers — query DuckDB after rebuild
# --------------------------------------------------------------------------- #


def _get_duckdb_conn(db_path):
    """Open a DuckDB connection with rebuilt property graph."""
    from helpers.graph.query import connect

    return connect(db_path=db_path, fresh=True)


def _count_nodes(con):
    """Count v_node rows (total entities)."""
    return con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0]


def _count_companies(con):
    """Count v_company rows."""
    return con.execute("SELECT COUNT(*) FROM v_company").fetchone()[0]


def _count_sectors(con):
    """Count v_sector rows."""
    return con.execute("SELECT COUNT(*) FROM v_sector").fetchone()[0]


def _sector_members(con, sector_name):
    """Get companies in a sector via GRAPH_TABLE query."""
    from helpers.graph.query import sector_members

    return sector_members(con, sector_name)


# --------------------------------------------------------------------------- #
# 1. Initial rebuild reflects SQLite state
# --------------------------------------------------------------------------- #


class TestInitialRebuild:
    """SQLite → rebuild DuckDB → query verifies correct reflection."""

    def test_node_count_matches_sqlite(self, p9_db):
        conn, db_path = p9_db
        sqlite_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        con = _get_duckdb_conn(db_path)
        try:
            assert _count_nodes(con) == sqlite_count
        finally:
            con.close()

    def test_company_count_matches(self, p9_db):
        conn, db_path = p9_db
        sqlite_co = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='company'"
        ).fetchone()[0]
        con = _get_duckdb_conn(db_path)
        try:
            assert _count_companies(con) == sqlite_co
        finally:
            con.close()

    def test_sector_count_matches(self, p9_db):
        conn, db_path = p9_db
        sqlite_sec = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='sector'"
        ).fetchone()[0]
        con = _get_duckdb_conn(db_path)
        try:
            assert _count_sectors(con) == sqlite_sec
        finally:
            con.close()

    def test_sector_members_correct(self, p9_db):
        conn, db_path = p9_db
        con = _get_duckdb_conn(db_path)
        try:
            banking = _sector_members(con, "Banking")
            assert "HDFC Bank" in banking
            assert "ICICI Bank" in banking
            assert len(banking) == 2

            tech = _sector_members(con, "Technology")
            assert "Infosys" in tech
            assert "TCS" in tech
            assert len(tech) == 2
        finally:
            con.close()

    def test_edge_count_matches(self, p9_db):
        conn, db_path = p9_db
        sqlite_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        con = _get_duckdb_conn(db_path)
        try:
            # Count total edges across all edge tables
            duckdb_edges = con.execute("SELECT COUNT(*) FROM fin.graph_edges").fetchone()[0]
            assert duckdb_edges == sqlite_edges
        finally:
            con.close()


# --------------------------------------------------------------------------- #
# 2. Mutation: add entity → rebuild → verify visible in DuckDB
# --------------------------------------------------------------------------- #


class TestAddEntityRebuild:
    """Add an entity to SQLite → rebuild DuckDB → verify it's visible."""

    def test_new_company_visible_after_rebuild(self, p9_db):
        conn, db_path = p9_db
        # Initial state: 4 companies
        con = _get_duckdb_conn(db_path)
        initial = _count_companies(con)
        con.close()

        # Add a new company + edges
        conn.execute(
            "INSERT INTO entities(name, entity_type, sector_classification) "
            "VALUES ('Axis Bank', 'company', 'Banking')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('Axis Bank', 'Banking', 'part_of', 'test')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('Banking', 'Axis Bank', 'has_company', 'test')"
        )
        conn.commit()

        # Rebuild
        con = _get_duckdb_conn(db_path)
        try:
            after = _count_companies(con)
            assert after == initial + 1

            # Axis Bank should be in Banking sector
            banking = _sector_members(con, "Banking")
            assert "Axis Bank" in banking
            assert len(banking) == 3
        finally:
            con.close()

    def test_new_sector_visible_after_rebuild(self, p9_db):
        conn, db_path = p9_db
        con = _get_duckdb_conn(db_path)
        initial_sectors = _count_sectors(con)
        con.close()

        # Add a new sector + company
        conn.execute("INSERT INTO entities(name, entity_type) VALUES ('Pharma', 'sector')")
        conn.execute(
            "INSERT INTO entities(name, entity_type, sector_classification) "
            "VALUES ('Sun Pharma', 'company', 'Pharma')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('Sun Pharma', 'Pharma', 'part_of', 'test')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('Pharma', 'Sun Pharma', 'has_company', 'test')"
        )
        conn.commit()

        con = _get_duckdb_conn(db_path)
        try:
            assert _count_sectors(con) == initial_sectors + 1
            pharma = _sector_members(con, "Pharma")
            assert "Sun Pharma" in pharma
        finally:
            con.close()


# --------------------------------------------------------------------------- #
# 3. Mutation: delete entity → rebuild → verify removal
# --------------------------------------------------------------------------- #


class TestDeleteEntityRebuild:
    """Remove an entity from SQLite → rebuild → verify it's gone from DuckDB."""

    def test_deleted_company_gone_after_rebuild(self, p9_db):
        conn, db_path = p9_db
        con = _get_duckdb_conn(db_path)
        initial = _count_companies(con)
        banking_before = _sector_members(con, "Banking")
        con.close()
        assert "HDFC Bank" in banking_before

        # Delete HDFC Bank + its edges
        conn.execute("DELETE FROM graph_edges WHERE source='HDFC Bank' OR target='HDFC Bank'")
        conn.execute("DELETE FROM entities WHERE name='HDFC Bank'")
        conn.commit()

        con = _get_duckdb_conn(db_path)
        try:
            after = _count_companies(con)
            assert after == initial - 1

            banking_after = _sector_members(con, "Banking")
            assert "HDFC Bank" not in banking_after
            assert "ICICI Bank" in banking_after  # Other companies remain
        finally:
            con.close()


# --------------------------------------------------------------------------- #
# 4. Edge mutation → rebuild → verify graph topology changes
# --------------------------------------------------------------------------- #


class TestEdgeMutationRebuild:
    """Add/remove edges → rebuild → verify graph topology reflects changes."""

    def test_new_edge_visible_after_rebuild(self, p9_db):
        conn, db_path = p9_db
        # Add a competes_with edge between HDFC and ICICI
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('HDFC Bank', 'ICICI Bank', 'competes_with', 'test')"
        )
        conn.commit()

        con = _get_duckdb_conn(db_path)
        try:
            # The edge should appear in DuckDB's raw graph_edges
            r = con.execute(
                "SELECT COUNT(*) FROM fin.graph_edges WHERE edge_type='competes_with'"
            ).fetchone()[0]
            assert r >= 1
        finally:
            con.close()

    def test_edge_removal_reflected(self, p9_db):
        conn, db_path = p9_db
        # Remove all part_of edges
        conn.execute("DELETE FROM graph_edges WHERE edge_type='part_of'")
        conn.commit()

        con = _get_duckdb_conn(db_path)
        try:
            # When the BelongsTo edge table is empty, duckpgq skips it from
            # the property graph declaration (CSR construction fails on empty
            # tables). sector_members raises BinderException — that's correct
            # behavior, not a bug. Verify via raw edge count instead.
            r = con.execute(
                "SELECT COUNT(*) FROM fin.graph_edges WHERE edge_type='part_of'"
            ).fetchone()[0]
            assert r == 0
            # The has_company edges are still there
            r2 = con.execute(
                "SELECT COUNT(*) FROM fin.graph_edges WHERE edge_type='has_company'"
            ).fetchone()[0]
            assert r2 >= 4
        finally:
            con.close()


# --------------------------------------------------------------------------- #
# 5. Idempotency: multiple rebuilds produce the same result
# --------------------------------------------------------------------------- #


class TestRebuildIdempotency:
    """Rebuilding twice produces identical DuckDB state."""

    def test_double_rebuild_same_counts(self, p9_db):
        conn, db_path = p9_db
        con1 = _get_duckdb_conn(db_path)
        c1_nodes = _count_nodes(con1)
        c1_companies = _count_companies(con1)
        c1_sectors = _count_sectors(con1)
        con1.close()

        con2 = _get_duckdb_conn(db_path)
        c2_nodes = _count_nodes(con2)
        c2_companies = _count_companies(con2)
        c2_sectors = _count_sectors(con2)
        con2.close()

        assert c1_nodes == c2_nodes
        assert c1_companies == c2_companies
        assert c1_sectors == c2_sectors

    def test_rebuild_preserves_sector_membership(self, p9_db):
        conn, db_path = p9_db
        con1 = _get_duckdb_conn(db_path)
        b1 = sorted(_sector_members(con1, "Banking"))
        con1.close()

        con2 = _get_duckdb_conn(db_path)
        b2 = sorted(_sector_members(con2, "Banking"))
        con2.close()

        assert b1 == b2
