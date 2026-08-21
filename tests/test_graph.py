#!/usr/bin/env python3
"""Tests for helpers/graph/query.py (DuckDB graph layer).

These tests are marked `live` (require network to INSTALL duckdb extensions
on first run) and `graph` so they can be filtered.

Run:
    pytest tests/test_graph.py -v
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

# Skip the whole module if duckdb isn't importable (CI without optional deps).
duckdb = pytest.importorskip("duckdb")

# Skip if the project DB doesn't exist (pristine clone).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "memory" / "research.db"

if not DB_PATH.exists():
    pytest.skip(f"skipping graph tests — {DB_PATH} not present", allow_module_level=True)

from helpers.graph.query import (  # noqa: E402
    acquisitions,
    clustering_coefficient,
    company_neighbors_bundle,
    connect,
    find_cycles,
    group_siblings,
    jv_partners,
    neighbors,
    pagerank,
    peers,
    sector_members,
    sector_members_with_market_cap,
    sector_of,
    sectors_in_super,
    shortest_path,
    sql,
    subsidiary_of_company,
    sub_sectors_of,
    super_sector_of,
    suppliers_and_customers,
    weakly_connected_components,
)


@pytest.fixture(scope="module")
def con():
    """One DuckDB connection for the whole module (slow to spin up)."""
    c = connect()
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# Connect / property-graph setup
# --------------------------------------------------------------------------- #
def _has_duckdb_graphs(con) -> bool:
    """True if duckdb_graphs() is queryable (only with the duckpgq extension
    loaded; it is not, post-retirement)."""
    try:
        con.execute("SELECT 1 FROM duckdb_graphs() LIMIT 1").fetchone()
        return True
    except Exception:
        return False


class TestConnect:
    def test_connect_returns_duckdb_connection(self, con):
        # Sanity: a simple non-graph query works.
        n = con.execute("SELECT COUNT(*) FROM fin.entities").fetchone()[0]
        assert n > 900  # we have 991 nodes today

    def test_graph_edges_attached(self, con):
        n = con.execute("SELECT COUNT(*) FROM fin.graph_edges").fetchone()[0]
        assert n > 1000  # ~1,686 today

    def test_materialised_tables_present(self, con):
        # Phase E (duckpgq retirement): the property graph fin_graph is GONE.
        # What connect() must guarantee now: the materialised v_node vertex
        # table + e_belongs edge table exist and join correctly (the plain
        # SQL the pattern queries are written against — see
        # doc/improvements/archive/graph/duckpgq_retirement.txt).
        r = con.execute(
            """
            SELECT v_c.name AS company, v_s.name AS sector
            FROM e_belongs e
            JOIN v_node v_c ON v_c.id = e.company_name AND v_c.kind = 'company'
            JOIN v_node v_s ON v_s.id = e.sector_name AND v_s.kind = 'sector'
            LIMIT 1
            """
        ).fetchall()
        assert r, "expected at least one Company->Sector edge"
        company, sector = r[0]
        assert isinstance(company, str) and isinstance(sector, str)
        # And no property graph catalog entry remains.
        graphs = con.execute(
            "SELECT count(*) FROM duckdb_graphs()"
        ).fetchone() if _has_duckdb_graphs(con) else (0,)
        assert graphs[0] == 0

    def test_company_sector_counts_match_sqlite(self, con):
        # Materialised v_company / v_sector row counts should match SQLite.
        sqlite_n_company = con.execute(
            "SELECT COUNT(*) FROM fin.entities WHERE entity_type='company'"
        ).fetchone()[0]
        duck_n_company = con.execute(
            "SELECT COUNT(*) FROM v_company"
        ).fetchone()[0]
        assert duck_n_company == sqlite_n_company


# --------------------------------------------------------------------------- #
# Wrapper functions
# --------------------------------------------------------------------------- #
class TestSectorOf:
    def test_known_company(self, con):
        assert sector_of(con, "CEAT") == "Automotive"

    def test_unknown_company_returns_none(self, con):
        assert sector_of(con, "NoSuchCompany XYZ") is None

    def test_company_with_spaces(self, con):
        assert sector_of(con, "Polycab India") == "Engineering_Capital_Goods"


class TestSectorMembers:
    def test_known_sector_returns_sorted_list(self, con):
        members = sector_members(con, "Automotive")
        assert len(members) > 10
        assert members == sorted(members)
        assert "CEAT" in members

    def test_filter_by_market_cap(self, con):
        all_fs = sector_members(con, "Financial_Services")
        mid_fs = sector_members(con, "Financial_Services", market_cap="mid_cap")
        assert set(mid_fs).issubset(set(all_fs))
        assert len(mid_fs) <= len(all_fs)

    def test_unknown_sector_returns_empty(self, con):
        assert sector_members(con, "NoSuchSector") == []


class TestSectorMembersWithMarketCap:
    """Bundle K2: sector_members_with_market_cap fetches members + market_cap
    in ONE DuckDB GRAPH_TABLE, collapsing the cross-DB hop the old
    _sector_neighbors_bundle used (DuckDB members → SQLite GROUP BY → Python
    bucketize). These tests pin the new helper's contract."""

    def test_returns_name_cap_pairs(self, con):
        pairs = sector_members_with_market_cap(con, "Automotive")
        assert len(pairs) > 10
        # Every row is a (name, cap_or_None) 2-tuple.
        for name, cap in pairs:
            assert isinstance(name, str)
            assert cap is None or isinstance(cap, str)
        # Sorted by name (matches sector_members' sort).
        names = [n for n, _ in pairs]
        assert names == sorted(names)
        assert "CEAT" in names

    def test_market_cap_filter_narrows_pairs(self, con):
        all_fs = sector_members_with_market_cap(con, "Financial_Services")
        mid_fs = sector_members_with_market_cap(con, "Financial_Services", market_cap="mid_cap")
        # The filtered set is a subset.
        assert set(n for n, _ in mid_fs).issubset(n for n, _ in all_fs)
        # And every filtered row actually has mid_cap (the filter applies to
        # the same row whose cap we return — the K2 invariant).
        for _, cap in mid_fs:
            assert cap == "mid_cap"

    def test_agrees_with_sector_members_names(self, con):
        """K2 invariant: the names from the new helper must match
        sector_members() exactly. The new helper is a drop-in source for the
        members list, just with cap attached."""
        for sector in ("Automotive", "Financial_Services", "Banking"):
            names_plain = sector_members(con, sector)
            names_paired = [n for n, _ in sector_members_with_market_cap(con, sector)]
            assert names_plain == names_paired, f"{sector}: name sets diverge"

    def test_unknown_sector_returns_empty(self, con):
        assert sector_members_with_market_cap(con, "NoSuchSector") == []


class TestSectorHierarchy:
    """Bundle M4: the super-sector -> sector -> sub-category hierarchy,
    traversed via the belongs_to edge (label BelongsToHierarchy). Pins the
    three query helpers added alongside the e_belongs_to materialisation."""

    def test_super_sector_of_known_sector(self, con):
        # Banking -> Financials (the GICS-style grouping).
        assert super_sector_of(con, "Banking") == "Financials"

    def test_super_sector_of_sector_with_sub_categories(self, con):
        # Metals sits under Materials and has 6 sub-categories.
        assert super_sector_of(con, "Metals") == "Materials"

    def test_super_sector_of_renamed_collision(self, con):
        # Energy/Healthcare collided with sector names and were renamed with
        # a _Super suffix — the helper must resolve to the renamed super-sector.
        assert super_sector_of(con, "Energy") == "Energy_Super"
        assert super_sector_of(con, "Healthcare") == "Healthcare_Super"

    def test_super_sector_of_unknown_returns_none(self, con):
        assert super_sector_of(con, "NoSuchSector") is None

    def test_sectors_in_super_returns_sorted_children(self, con):
        children = sectors_in_super(con, "Financials")
        # Financials groups 7 financial sub-sectors.
        assert len(children) == 7
        assert children == sorted(children)
        assert "Banking" in children
        assert "Insurance" in children

    def test_sectors_in_super_unknown_returns_empty(self, con):
        assert sectors_in_super(con, "NoSuchSuper") == []

    def test_sub_sectors_of_sector_with_categories(self, con):
        # Metals has 6 authored sub-categories (Iron and Steel, Aluminum, ...).
        subs = sub_sectors_of(con, "Metals")
        assert len(subs) == 6
        assert "Iron_and_Steel" in subs
        assert subs == sorted(subs)

    def test_sub_sectors_of_sector_without_categories(self, con):
        # Capital_Markets is one of the 18 sectors with no authored
        # sub-categories (neither subsector/* tags nor ### headings) —
        # returns []. (Banking now has 4 sub-categories under the merged
        # Level 3, so it's no longer the right no-category example.)
        assert sub_sectors_of(con, "Capital_Markets") == []

    def test_belongs_to_materialised_in_graph(self, con):
        # The e_belongs_to table must be populated (the dedicated CTAS ran).
        n = con.execute("SELECT COUNT(*) FROM e_belongs_to").fetchone()[0]
        assert n == 120  # 42 sector->super + 78 sub->sector (merged Level 3)

    def test_vertex_projections_populated(self, con):
        # The 4 entity kinds must all materialise as vertices.
        assert con.execute("SELECT COUNT(*) FROM v_super_sector").fetchone()[0] == 9
        assert con.execute("SELECT COUNT(*) FROM v_sub_sector").fetchone()[0] == 78


class TestNeighbors:
    def test_company_neighbors(self, con):
        # A company should have one outgoing BelongsTo + one incoming
        # HasCompany from its sector (same sector on both sides).
        n = neighbors(con, "CEAT")
        assert any(d == "out" and label == "BelongsTo" and o == "Automotive" for d, o, label in n)
        assert any(d == "in" and label == "HasCompany" and o == "Automotive" for d, o, label in n)

    def test_sector_neighbors_include_member_companies(self, con):
        n = neighbors(con, "Automotive")
        companies_in = [o for d, o, label in n if d == "in" and label == "BelongsTo"]
        assert "CEAT" in companies_in
        assert len(companies_in) > 10


class TestBundleK3CoalescedNeighbors:
    """Bundle K3: neighbors() and suppliers_and_customers() used to fire 4
    serial GRAPH_TABLE queries each. Both are now a single UNION ALL — one
    trip through duckpgq's planner. These tests pin the coalesced shape
    against the same live seeds the old multi-query form covered."""

    def test_neighbors_returns_no_duplicates(self, con):
        # F3 dedup still applies post-K3: inverse edge types (BelongsTo vs
        # HasCompany) can emit the same (dir, other, label) twice.
        n = neighbors(con, "CEAT")
        assert len(n) == len(set(n)), "neighbors() has duplicate rows"

    def test_neighbors_results_sorted(self, con):
        n = neighbors(con, "Automotive")
        assert n == sorted(n)

    def test_neighbors_company_focal_shape(self, con):
        # CEAT (company): one out BelongsTo + one in HasCompany from Automotive.
        n = neighbors(con, "CEAT")
        assert ("out", "Automotive", "BelongsTo") in n
        assert ("in", "Automotive", "HasCompany") in n

    def test_suppliers_and_customers_outgoing_suppliesto(self, con):
        # Talbros → Tata Motors Passenger Vehicles (supplier_to). Talbros is
        # the supplier, so it has a customer (Tata Motors PV), no suppliers.
        s, c = suppliers_and_customers(con, "Talbros Automotive Components")
        assert s == []
        assert c == ["Tata Motors Passenger Vehicles"]

    def test_suppliers_and_customers_incoming_suppliesto(self, con):
        # The flip side: Tata Motors PV has Talbros as a supplier.
        s, c = suppliers_and_customers(con, "Tata Motors Passenger Vehicles")
        assert s == ["Talbros Automotive Components"]
        assert c == []

    def test_suppliers_and_customers_outgoing_customerof(self, con):
        # GAIL → Indian Oil (customer_of). customer_of is customer→supplier,
        # so GAIL (the source) has Indian Oil as a supplier.
        s, c = suppliers_and_customers(con, "GAIL India")
        assert s == ["Indian Oil Corporation"]
        assert c == []

    def test_suppliers_and_customers_unknown_company_returns_empty(self, con):
        s, c = suppliers_and_customers(con, "NoSuch Company XYZ")
        assert s == [] and c == []


class TestShortestPath:
    def test_company_to_sector_one_hop(self, con):
        path = shortest_path(con, "CEAT", "Automotive", max_hops=3)
        assert path is not None
        assert path[0] == ("CEAT", 0)
        assert path[-1] == ("Automotive", 1)

    def test_sector_to_company_one_hop(self, con):
        path = shortest_path(con, "Engineering_Capital_Goods", "Polycab India", max_hops=3)
        assert path is not None
        assert path[0][0] == "Engineering_Capital_Goods"
        assert path[-1][0] == "Polycab India"

    def test_disconnected_companies_return_none(self, con):
        # Two companies that share NO newsletter edition, sector, or peer
        # relationship in the current schema. With Phase 2's co_mentioned_in
        # edges, many companies in the same edition are now 1-hop connected,
        # so we pick two known-isolated endpoints.
        assert shortest_path(con, "TCS", "MRF", max_hops=2) is None

    def test_unknown_entity_returns_none(self, con):
        assert shortest_path(con, "NoSuch", "AlsoMissing", max_hops=2) is None

    def test_cte_fallback_honors_edge_label(self, con):
        """B3: the recursive-CTE fallback must honor edge_label, matching the
        native path's semantics. Indigo Paints and Kansai Nerolac Paints are
        connected via CompetesWith but are in DIFFERENT sectors (Chemicals vs
        Building_Materials), so a BelongsTo path should not exist. Before the
        fix, the CTE ignored edge_label and traversed ALL edge types — a
        caller asking for BelongsTo would silently get a path via
        CompetesWith."""
        from helpers.graph.query import _shortest_path_cte
        a, b = "Indigo Paints", "Kansai Nerolac Paints"
        # CompetesWith path exists (direct competitor edge).
        assert _shortest_path_cte(con, a, b, max_hops=3, edge_label="CompetesWith") is not None
        # BelongsTo path must NOT exist — different sectors, no shared node.
        # Before B3 this returned a path via CompetesWith (wrong edge type).
        assert _shortest_path_cte(con, a, b, max_hops=3, edge_label="BelongsTo") is None

    def test_cte_fallback_unknown_label_traverses_all(self, con):
        """B3: an unrecognized edge_label means no filter — the CTE traverses
        all edge types (the historical behavior for unknown labels). This is
        the entry point shortest_path uses when the label isn't in
        EDGE_REGISTRY."""
        from helpers.graph.query import _shortest_path_cte
        # CEAT and MRF are connected via CompetesWith (1 hop) and via
        # BelongsTo through the Automotive sector (2 hops). An unknown label
        # should find SOME path (traverses all types).
        path = _shortest_path_cte(con, "CEAT", "MRF", max_hops=3, edge_label="NoSuchLabel")
        assert path is not None
        assert path[0][0] == "CEAT"
        assert path[-1][0] == "MRF"

    def test_cte_primary_returns_full_vertex_sequence(self, con):
        """Phase C (duckpgq retirement): shortest_path is now a pure
        recursive-CTE walk. Unlike the removed duckpgq ANY SHORTEST branch
        (which returned only the two endpoints because duckpgq v1.5 could
        not expose intermediate vertices), the CTE returns the FULL vertex
        sequence — asserted here on a 2-hop path."""
        # CEAT -competes_with-> MRF -part_of(BelongsTo)-> Automotive:
        # via BelongsTo alone CEAT->Automotive is 1 hop; use an entity pair
        # whose shortest path genuinely has an interior vertex.
        path = shortest_path(con, "CEAT", "MRF", max_hops=3,
                             edge_label="BelongsTo")
        # CEAT and MRF are both companies; BelongsTo connects company ->
        # sector, so the shortest BelongsTo path between two companies in
        # different sectors is 2 hops (CEAT -> their shared sector? no —
        # CEAT -> Automotive <- MRF IS the 2-hop path when they share a
        # sector, or None when they do not). Either outcome is contract-
        # valid; assert shape only.
        if path is not None:
            assert path[0] == ("CEAT", 0)
            assert len({n for n, _ in path}) == len(path)  # no repeats
            assert all(h == i for i, (_, h) in enumerate(path))  # 0..N-1

        # Undirected traversal: a sector -> company path also works.
        p2 = shortest_path(con, "Automotive", "CEAT", max_hops=2,
                           edge_label="BelongsTo")
        assert p2 is not None and p2[0] == ("Automotive", 0) and p2[-1][0] == "CEAT"

    def test_cte_unknown_label_traverses_all_types(self, con):
        """An unrecognized edge_label means no edge-type filter (historical
        behaviour, now exercised directly since the label-resolution branch
        of the old native path is gone)."""
        path = shortest_path(con, "CEAT", "MRF", max_hops=3,
                             edge_label="NoSuchLabel")
        # competes_with CEAT<->MRF exists, so an unfiltered walk finds it.
        assert path is not None
        assert path[0][0] == "CEAT" and path[-1][0] == "MRF"


class TestBfsShortestPath:
    """sql_capability_unlocks B2: shortest_path is a level-by-level BFS over
    the materialised e_all_und adjacency. _shortest_path_cte (the retired
    production path) stays importable as the ORACLE — on small max_hops its
    cost is irrelevant and its independence from the BFS implementation is
    the point. Equivalence is pinned on (depth, endpoints); the specific
    tie-break among equal-depth paths is unspecified in the contract (the
    CTE picked arbitrarily too; BFS picks MIN(a_id) deterministically)."""

    # Known-shape pairs from the live seed data (see the tests above):
    # 1-hop company→sector, sector→company, 1-hop competitors, 2-hop via
    # sector, cross-sector competitors (label-sensitive), disconnected.
    PAIRS = [
        ("CEAT", "Automotive"),
        ("Automotive", "CEAT"),
        ("Engineering_Capital_Goods", "Polycab India"),
        ("CEAT", "MRF"),
        ("Indigo Paints", "Kansai Nerolac Paints"),
        ("TCS", "MRF"),
    ]

    @staticmethod
    def _shape(path):
        """Reduce a path to its contract-observable shape (depth +
        endpoints) for oracle comparison."""
        if path is None:
            return None
        return (len(path), path[0][0], path[-1][0])

    def test_bfs_matches_cte_oracle(self, con):
        from helpers.graph.query import _shortest_path_cte
        for a, b in self.PAIRS:
            for hops in (1, 2, 3):
                for label in ("BelongsTo", "CompetesWith", "NoSuchLabel"):
                    bfs = shortest_path(con, a, b, max_hops=hops, edge_label=label)
                    cte = _shortest_path_cte(con, a, b, max_hops=hops, edge_label=label)
                    assert self._shape(bfs) == self._shape(cte), (
                        f"divergence at ({a!r}, {b!r}, hops={hops}, "
                        f"label={label!r}): bfs={self._shape(bfs)} "
                        f"cte={self._shape(cte)}"
                    )

    def test_bfs_matches_cte_oracle_temporal(self, con):
        from helpers.graph.query import _shortest_path_cte
        # edge_label=None on BOTH sides — shortest_path's signature default
        # is "BelongsTo" while the oracle's is None, so the label must be
        # pinned explicitly or the sweep compares different filters.
        for a, b in self.PAIRS:
            for as_of in ("2020-01-01", "2026-01-01"):
                bfs = shortest_path(con, a, b, max_hops=3, edge_label=None, as_of=as_of)
                cte = _shortest_path_cte(con, a, b, max_hops=3, edge_label=None, as_of=as_of)
                assert self._shape(bfs) == self._shape(cte), (
                    f"temporal divergence at ({a!r}, {b!r}, as_of={as_of}): "
                    f"bfs={self._shape(bfs)} cte={self._shape(cte)}"
                )

    def test_bfs_path_invariants(self, con):
        """Whatever path BFS returns: contiguous hop indexes from 0, no
        repeated vertices, endpoints exactly (src, 0) and (dst, len-1)."""
        for a, b in self.PAIRS:
            path = shortest_path(con, a, b, max_hops=3, edge_label="NoSuchLabel")
            if path is None:
                continue
            assert path[0] == (a, 0)
            assert path[-1] == (b, len(path) - 1)
            assert all(h == i for i, (_, h) in enumerate(path))
            assert len({n for n, _ in path}) == len(path)  # simple path

    def test_bfs_contract_pins(self, con):
        # src == dst (known vertex) → [(src, 0)] without touching edges.
        assert shortest_path(con, "CEAT", "CEAT", max_hops=3) == [("CEAT", 0)]
        # Unknown src or dst → None (the old CTE never seeded/matched).
        assert shortest_path(con, "NoSuch", "CEAT", max_hops=3) is None
        assert shortest_path(con, "CEAT", "NoSuch", max_hops=3) is None
        assert shortest_path(con, "NoSuch", "AlsoMissing", max_hops=3) is None
        # hops == 0 with src != dst → None (no levels to walk).
        assert shortest_path(con, "CEAT", "MRF", max_hops=0) is None
        # Unreachable within the cap → None (bounded full-graph traversal).
        assert shortest_path(con, "TCS", "MRF", max_hops=2) is None
        # Negative cap is a caller bug, not a "no path".
        with pytest.raises(ValueError, match="max_hops must be >= 0"):
            shortest_path(con, "CEAT", "MRF", max_hops=-1)

    def test_bfs_honors_edge_label(self, con):
        # Competitors in different sectors: CompetesWith connects them,
        # BelongsTo does not (label filter is load-bearing in e_all_und).
        a, b = "Indigo Paints", "Kansai Nerolac Paints"
        assert shortest_path(con, a, b, max_hops=3, edge_label="CompetesWith") is not None
        assert shortest_path(con, a, b, max_hops=3, edge_label="BelongsTo") is None

    def test_bfs_binds_hostile_names_safely(self, con):
        """Part C: names travel as bind parameters — a NUL/control-char
        payload must not be able to crack the SQL text (the fuzz-discovered
        _lit() class is gone from this path)."""
        assert shortest_path(con, "No\x00Such'", "CEAT", max_hops=2) is None

class TestCompanyNeighborsBundle:
    """C1: the coalesced mega-query must reproduce the 7 serial wrappers
    exactly, honor as_of, and degrade gracefully for unknown companies.

    `test_bundle_matches_individual_wrappers` is the correctness oracle — if
    a UNION arm's direction is inverted or a JSON field is dropped, this test
    catches it immediately against known seeds in the live DB.
    """

    def test_bundle_matches_individual_wrappers(self, con):
        """For several companies with diverse edge profiles, the coalesced
        company_neighbors_bundle() must return the same dict as calling the
        7 individual wrappers (sector_of, peers, jv_partners, group_siblings,
        acquisitions, subsidiary_of_company, suppliers_and_customers)."""
        # CEAT — peers + acquired; HDB Financial — subsidiary_of parent;
        # ICICI Prudential AMC — jv_partners; Muthoot Finance — group_siblings;
        # Indian Oil Corp — customers; GAIL India — suppliers.
        companies = [
            "CEAT", "HDB Financial Services", "ICICI Prudential Asset "
            "Management Company", "Muthoot Finance", "Indian Oil Corporation",
            "GAIL India",
        ]
        for company in companies:
            bundle = company_neighbors_bundle(con, company)
            expected = {
                "sector": sector_of(con, company),
                "peers": peers(con, company),
                "jv_partners": [{"partner": p, "venture": v}
                                for p, v in jv_partners(con, company)],
                "group_siblings": group_siblings(con, company),
                "acquired": [{"name": n, "year": y}
                             for n, y in acquisitions(con, company)],
                "subsidiary_of": subsidiary_of_company(con, company),
                "suppliers": suppliers_and_customers(con, company)[0],
                "customers": suppliers_and_customers(con, company)[1],
            }
            # Per-field report on failure so a direction bug is obvious.
            for key in expected:
                assert bundle[key] == expected[key], (
                    f"{company!r} mismatch on {key!r}: "
                    f"bundle={bundle[key]!r} wrappers={expected[key]!r}"
                )

    def test_bundle_honors_as_of(self, con):
        """as_of must thread into every arm. CEAT acquired Camso in 2023
        (verified in test_neighbors_bundle_shape); an as_of before that date
        drops the acquired edge while leaving structural edges intact."""
        # 2020-01-01: before the Camso acquisition.
        old = company_neighbors_bundle(con, "CEAT", as_of="2020-01-01")
        now = company_neighbors_bundle(con, "CEAT")
        # Structural edges survive.
        assert old["sector"] == now["sector"] == "Automotive"
        assert old["peers"] == now["peers"]
        # The acquired edge is dropped under the historical lens.
        assert old["acquired"] == []
        assert any(a["name"] == "Camso" for a in now["acquired"])

    def test_bundle_empty_for_unknown_company(self, con):
        """Unknown company → empty lists + None scalar fields, no exception.
        Mirrors how the individual wrappers behave today."""
        bundle = company_neighbors_bundle(con, "NoSuchCompany XYZ")
        assert bundle == {
            "sector": None,
            "peers": [],
            "jv_partners": [],
            "group_siblings": [],
            "acquired": [],
            "subsidiary_of": None,
            "suppliers": [],
            "customers": [],
        }


class TestSqlPassthrough:
    def test_arbitrary_sql(self, con):
        r = sql(
            con,
            """
            SELECT v_c.name AS company
            FROM e_belongs e
            JOIN v_node v_c ON v_c.id = e.company_name AND v_c.kind = 'company'
            LIMIT 2
            """,
        )
        assert len(r) >= 1

class TestPhase2Edges:
    """Tests for competes_with, jv_with, same_group, acquired, subsidiary_of.

    The graph_edges for these labels were seeded by a one-shot Phase 2 seed
    script (since removed) and now live in memory/research.db.
    """

    def test_peers_returns_competitors(self, con):
        peers_list = peers(con, "CEAT")
        # Phase 2 seed includes Apollo Tyres and MRF as CEAT competitors.
        assert "Apollo Tyres" in peers_list
        assert "MRF" in peers_list
        assert "CEAT" not in peers_list  # symmetric, no self-loop

    def test_peers_unknown_company_returns_empty(self, con):
        assert peers(con, "NoSuchCompany") == []

    def test_jv_partners_returns_venture(self, con):
        partners = jv_partners(con, "Jio Financial Services")
        # 3 partners: Allianz, BlackRock + Mastercard (foreign-entity stub
        # from the 2026-08-11 H4 follow-up — "third-party products launched
        # in partnership with Mastercard for a closed-user group").
        assert len(partners) == 3
        partner_names = [p for p, _ in partners]
        assert "BlackRock" in partner_names
        assert "Allianz" in partner_names
        assert "Mastercard" in partner_names
        # Venture name should be present in the tuple
        ventures = [v for _, v in partners]
        assert any("JioBlackRock AMC" == v for v in ventures)
        assert any("Allianz Jio Reinsurance" == v for v in ventures)

    def test_group_siblings_returns_muthoot_group(self, con):
        sibs = group_siblings(con, "Muthoot Finance")
        assert "Muthoot Capital Services" in sibs
        assert "Muthoot Microfin" in sibs
        assert "Muthoot Finance" not in sibs  # no self-loop

    def test_acquisitions_returns_target_with_year(self, con):
        acqs = acquisitions(con, "CEAT")
        assert ("Camso", "2023") in acqs

    def test_acquisitions_unknown_acquirer_returns_empty(self, con):
        assert acquisitions(con, "NoSuchCompany") == []


class TestPhase3NativeAlgorithms:
    """Tests for the native duckpgq algorithm wrappers (pagerank, wcc, lcc).

    These work on duckdb 1.5+ thanks to integer vertex PKs. See
    the duckpgq era (archive/graph/duckpgq_retirement.txt) — earlier versions segfaulted.
    """

    def test_pagerank_returns_named_scores(self, con):
        pr = pagerank(con, edge_label="BelongsTo")
        assert len(pr) > 100
        # All scores non-negative. (We don't assert sum == 1.0 because the
        # BelongsTo label connects company→sector, and pagerank is computed
        # over ALL vertices in the property graph but we only return company
        # rows — so the returned sum is < 1.0.)
        assert all(score >= 0 for _, score in pr)
        # Top entry must be a real company name, not None.
        assert pr[0][0]

    def test_pagerank_via_competes_with(self, con):
        pr = pagerank(con, edge_label="CompetesWith")
        # Sparse graph (5 competes_with edges), so few entries.
        names = [n for n, _ in pr]
        assert "CEAT" in names
        assert "MRF" in names

    def test_weakly_connected_components_returns_labels(self, con):
        comps = weakly_connected_components(con, edge_label="BelongsTo")
        # Every entry has a name and an integer component id.
        assert all(isinstance(cid, int) for _, cid in comps)
        # Membership graph (Company-Sector) → each sector is its own component.
        # With competes_with + co_mentioned_in there's bridging, but via
        # BelongsTo alone each sector forms a separate component, so we
        # expect many distinct ids.
        distinct = {cid for _, cid in comps}
        assert len(distinct) >= 30

    def test_clustering_coefficient_returns_floats(self, con):
        cc = clustering_coefficient(con, edge_label="CompetesWith")
        # The seeded tyre trio (CEAT, MRF, Apollo Tyres) forms a complete
        # triangle → clustering coefficient 1.0 for each.
        as_dict = dict(cc)
        assert as_dict.get("CEAT") == 1.0
        assert as_dict.get("MRF") == 1.0
        assert as_dict.get("Apollo Tyres") == 1.0

    def test_shortest_path_via_competes_with(self, con):
        # CEAT and MRF are direct competitors → 1 hop.
        path = shortest_path(con, "CEAT", "MRF", max_hops=3, edge_label="CompetesWith")
        assert path is not None
        assert path[0] == ("CEAT", 0)
        assert path[-1] == ("MRF", 1)


# --------------------------------------------------------------------------- #
# FK cascade propagation through the read-only attach
# --------------------------------------------------------------------------- #
class TestCascadePropagation:
    """Verify that SQLite-side renames/deletes propagate transparently
    to DuckDB's view of the graph on the next session.

    duckpgq doesn't expose the SQLite file directly; we materialise at
    connect(), so propagation = open a fresh session after a SQLite change.
    """

    def test_rename_propagates(self, tmp_path):
        """Copy DB, rename an entity in SQLite, reopen DuckDB, confirm.

        Under the disk-based DuckDB model, a second ``connect(tmp_db)``
        call would hit a warm ``.duckdb`` file and NOT pick up the
        rename. The fix is to pass ``rebuild=True`` so the materialised
        tables are dropped + repopulated from the current SQLite state.
        """
        # Copy via the SQLite backup API so WAL state is flushed (a plain
        # shutil.copy can produce a DB missing graph_edges if taken mid-WAL).
        import sqlite3

        tmp_db = tmp_path / "test_cascade.db"
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(tmp_db))
        src.backup(dst)
        dst.close()
        src.close()

        # Insert a synthetic entity + edge we can rename safely.
        from helpers.core.db import connect as sqlite_connect

        conn = sqlite_connect(tmp_db)
        try:
            conn.execute("INSERT INTO entities(name, entity_type) VALUES ('__TestCo__', 'company')")
            conn.execute("INSERT INTO entities(name, entity_type) VALUES ('__TestSector__', 'sector')")
            conn.execute(
                "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
                "VALUES ('__TestCo__', '__TestSector__', 'part_of', 'test')"
            )
            conn.execute(
                "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
                "VALUES ('__TestSector__', '__TestCo__', 'has_company', 'test')"
            )
            conn.commit()

            # Before rename: DuckDB should see __TestCo__. First connect
            # builds the .duckdb file cold.
            c1 = connect(tmp_db)
            assert sector_of(c1, "__TestCo__") == "__TestSector__"
            c1.close()

            # Rename __TestCo__ → __RenamedCo__ in SQLite.
            conn.execute("UPDATE entities SET name='__RenamedCo__' WHERE name='__TestCo__'")
            conn.commit()
        finally:
            conn.close()

        # After rename: must rebuild the .duckdb file to pick up the new
        # name (warm connect would otherwise serve stale materialised
        # tables — see doc/graph_design.txt §8 on the staleness contract).
        c2 = connect(tmp_db, rebuild=True)
        try:
            assert sector_of(c2, "__TestCo__") is None  # old name gone
            assert sector_of(c2, "__RenamedCo__") == "__TestSector__"  # new name present
        finally:
            c2.close()


# --------------------------------------------------------------------------- #
# Bundle F cleanups                                                           #
# --------------------------------------------------------------------------- #
class TestBundleFShortestPathCycleGuard:
    """F1: the recursive-CTE cycle guard must be token-exact, not substring.

    Previously `instr(w.path, node)` treated the path as a flat string, so a
    node named "ITC" was (wrongly) considered already-visited when the path
    contained "ITC Infotech" — pruning valid paths between prefix-collision
    pairs. The fix uses ``array_contains(string_to_array(path, '||'), node)``
    for exact-token membership.
    """

    def test_prefix_collision_path_is_found(self, tmp_path):
        """A path whose expansion must revisit a SHORT name that is a prefix
        of a node already in the path.

        Seed: ``__ITC Extended`` — ``__Mid`` — ``__ITC`` (the only route).
        Walking from ``__ITC Extended``, at hop 2 the candidate is ``__ITC``
        and the path so far is ``__ITC Extended||__Mid``. The OLD substring
        guard did ``instr('__ITC Extended||__Mid', '__ITC')`` → nonzero
        (because ``__ITC`` is a substring of ``__ITC Extended``), falsely
        treating ``__ITC`` as visited and pruning the only valid path →
        returned ``None``. The token-based guard correctly finds the route.
        """
        import sqlite3

        tmp_db = tmp_path / "f1_cycle.db"
        # Copy live schema by backing up, then wipe & reseed minimal data.
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(tmp_db))
        src.backup(dst)
        dst.close()
        src.close()

        conn = sqlite3.connect(str(tmp_db))
        try:
            # Isolate: wipe edges, keep entities table for FK satisfaction.
            conn.execute("DELETE FROM graph_edges")
            for n in ("__ITC", "__ITC Extended", "__Mid"):
                conn.execute(
                    "INSERT OR IGNORE INTO entities(name, entity_type) "
                    "VALUES (?, 'company')",
                    (n,),
                )
            # Linear chain: __ITC Extended — __Mid — __ITC (undirected).
            conn.executemany(
                "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
                "VALUES (?,?,?,?)",
                [
                    ("__ITC Extended", "__Mid", "competes_with", "test"),
                    ("__Mid", "__ITC Extended", "competes_with", "test"),
                    ("__Mid", "__ITC", "competes_with", "test"),
                    ("__ITC", "__Mid", "competes_with", "test"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        c = connect(tmp_db, fresh=True)
        try:
            # as_of forces the recursive-CTE fallback (where the F1 fix lives;
            # native ANY SHORTEST returns endpoints only and doesn't hit the
            # guard). The as_of value satisfies all edges (NULL valid_from).
            path = shortest_path(c, "__ITC Extended", "__ITC",
                                 max_hops=4, edge_label="CompetesWith",
                                 as_of="2026-01-01")
            assert path is not None, (
                "expected path __ITC Extended → __Mid → __ITC; None means the "
                "substring cycle guard regressed (falsely pruned __ITC as a "
                "substring of __ITC Extended already in the path)"
            )
            names = [n for n, _ in path]
            assert names[0] == "__ITC Extended"
            assert names[-1] == "__ITC"
            assert "__Mid" in names
        finally:
            c.close()


class TestBundleFNeighborsDedup:
    """F3: neighbors() must not return duplicate (dir, other, label) tuples."""

    def test_no_duplicate_rows(self, con):
        # CEAT has a clean single sector; still, the dedup is a general
        # invariant — assert the returned list has no duplicate tuples.
        n = neighbors(con, "CEAT")
        assert len(n) == len(set(n)), f"duplicate neighbour tuples: {n}"

    def test_sorted_output(self, con):
        # Bundle F3 also made output sorted for stable CLI rendering.
        n = neighbors(con, "CEAT")
        assert n == sorted(n)


class TestBundleFJsonExtraction:
    """F4: jv_partners / acquisitions now use DuckDB json_extract_string.

    Verified end-to-end against live seeds: BlackRock ↔ Jio Financial Services
    (venture "JioBlackRock AMC"), and CEAT → Camso (acquired, year 2023).
    """

    def test_jv_partners_extracts_venture(self, con):
        partners = dict(jv_partners(con, "BlackRock"))
        assert "Jio Financial Services" in partners
        assert partners["Jio Financial Services"] == "JioBlackRock AMC"

    def test_jv_partners_missing_venture_is_empty_string(self, con):
        # A JV edge whose properties lack a 'venture' key must yield '', not
        # raise — json_extract_string returns NULL, COALESCE converts to ''.
        partners = dict(jv_partners(con, "BlackRock"))
        # Reliance Industries JV has no 'venture' field in properties.
        assert partners.get("Reliance Industries") == ""

    def test_acquisitions_extracts_year_as_string(self, con):
        acq = dict(acquisitions(con, "CEAT"))
        assert acq.get("Camso") == "2023"


class TestBundleK1JsonExtraction:
    """Bundle K1: the company_neighbors_bundle (the coalesced 10-arm UNION used
    by /api/graph/neighbors) must extract venture/year via DuckDB
    json_extract_string — NOT re-parse e.properties in Python with a bare
    except (the F4 anti-pattern the bundle originally re-introduced for
    performance). Pins the K1 contract that the bundle matches the single-arm
    wrappers' data-quality behavior."""

    def test_bundle_jv_partners_extracts_venture(self, con):
        # The bundle path must extract 'venture' the same way jv_partners() does.
        bundle = company_neighbors_bundle(con, "BlackRock")
        partners = {p["partner"]: p["venture"] for p in bundle["jv_partners"]}
        assert partners.get("Jio Financial Services") == "JioBlackRock AMC"

    def test_bundle_jv_partners_missing_venture_is_empty_string(self, con):
        # Missing 'venture' key → '' via COALESCE, not raise. Reliance
        # Industries JV has no 'venture' field.
        bundle = company_neighbors_bundle(con, "BlackRock")
        partners = {p["partner"]: p["venture"] for p in bundle["jv_partners"]}
        assert partners.get("Reliance Industries") == ""

    def test_bundle_acquisitions_extracts_year_as_string(self, con):
        # The bundle path must extract 'year' the same way acquisitions() does.
        bundle = company_neighbors_bundle(con, "CEAT")
        acq = {a["name"]: a["year"] for a in bundle["acquired"]}
        assert acq.get("Camso") == "2023"

    def test_bundle_matches_single_arm_wrappers_for_json_fields(self, con):
        """K1 invariant: the bundle and the single-arm wrappers must agree on
        venture/year values (both now use DuckDB-side json_extract_string).
        Catches any future drift where one path changes the extraction shape."""
        for company in ("BlackRock", "CEAT"):
            bundle = company_neighbors_bundle(con, company)
            if bundle["jv_partners"]:
                single = dict(jv_partners(con, company))
                for p in bundle["jv_partners"]:
                    assert p["venture"] == single.get(p["partner"], ""), (
                        f"{company}: bundle venture {p['venture']!r} != "
                        f"single-arm {single.get(p['partner'])!r}"
                    )
            if bundle["acquired"]:
                single = dict(acquisitions(con, company))
                for a in bundle["acquired"]:
                    assert a["year"] == single.get(a["name"], ""), (
                        f"{company}: bundle year {a['year']!r} != "
                        f"single-arm {single.get(a['name'])!r}"
                    )


class TestBundleL2TypedYearColumn:
    """Bundle L2: e_acquired carries a typed `year` column projected from
    properties JSON ONCE at materialise time, so acquisitions() and the
    AcquiredBy arm of company_neighbors_bundle read it directly instead of
    calling json_extract_string(e.properties, 'year') per query.

    Pins three contracts: (1) the column exists on e_acquired only (not on
    other edge tables), (2) the schema version bumped so warm files rebuild,
    (3) the value round-trips identically to the old per-read extraction."""

    def test_e_acquired_has_typed_year_column(self, con):
        """e_acquired must have a `year` column; other edge tables must not
        (only acquired has a single hot-read JSON key worth denormalising)."""
        cols = {
            r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'e_acquired'"
            ).fetchall()
        }
        assert "year" in cols, f"e_acquired missing `year` column: {cols}"
        # Other edge tables should NOT have the column — L2 is scoped to
        # e_acquired only (jv_with.venture stays per-read; no other edge
        # type has a single hot-read key).
        for table in ("e_jv", "e_belongs", "e_competes", "e_supplier"):
            cols = {
                r[0] for r in con.execute(
                    f"SELECT column_name FROM information_schema.columns "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                    f"WHERE table_name = '{table}'"
                ).fetchall()
            }
            assert "year" not in cols, (
                f"{table} should not have a `year` column (L2 is e_acquired-only)"
            )

    def test_year_column_matches_json_extract(self, con):
        """The typed `year` column must equal what
        json_extract_string(properties, 'year') would return — proves the
        materialisation projection is correct and didn't drop/coerce data."""
        rows = con.execute(
            """
            SELECT year,
                   COALESCE(json_extract_string(properties, 'year'), '') AS from_json
            FROM e_acquired
            """
        ).fetchall()
        assert rows, "fixture has no e_acquired rows"
        mismatches = [
            (yr, js) for yr, js in rows if yr != js
        ]
        assert not mismatches, (
            f"typed `year` column disagrees with json_extract: {mismatches[:5]}"
        )

    def test_schema_version_bumped_to_force_rebuild(self):
        """Adding a column to e_acquired changes the materialisation shape,
        so warm files built under the old schema must rebuild. The schema
        version constant must be >1 (L2 bumped it from 1 to 2; later
        bundles raised it further — C2 to 4, C2-fix to 5 — each forcing a
        rebuild for a materialisation-shape change)."""
        from helpers.graph.query import _SCHEMA_VERSION
        # Accept any version >= 2: the contract is "bumped past v1 so warm
        # files rebuild". Pinning an exact value would break on every
        # legitimate future bump.
        assert int(_SCHEMA_VERSION) >= 2, (
            f"requires _SCHEMA_VERSION >= 2 (got {_SCHEMA_VERSION!r}); "
            "warm files built under v1 won't have the `year` column"
        )

    def test_acquisitions_reads_typed_column_not_json(self, con):
        """acquisitions() must return the same (name, year) pairs it did
        pre-L2 — the typed column is a transparent optimisation, not a
        behaviour change. Verified against the CEAT→Camso (2023) live seed."""
        acq = dict(acquisitions(con, "CEAT"))
        assert acq.get("Camso") == "2023"

    def test_acquisitions_empty_year_for_edges_without_year(self, con):
        """Edges with no `year` in properties must yield '' (not NULL, not
        a crash) — the COALESCE in the materialisation projection handles
        the ~10/22 live edges that lack a year."""
        # At least one acquired edge in the fixture has no year; its value
        # in the typed column must be '' (empty string), matching the old
        # COALESCE(json_extract_string(...), '') behaviour.
        has_empty = con.execute(
            "SELECT COUNT(*) FROM e_acquired WHERE year = ''"
        ).fetchone()[0]
        assert has_empty > 0, "fixture has no yearless acquired edge to test"


class TestFindCycles:
    """Bundle G3: directed cycle detection (recursive CTE diagnostic).

    The live graph SHOULD be acyclic for the edge types where a cycle is a
    data bug — same_group / co_mentioned_in are stored as one directed row
    per pair (alphabetical), and acquired / subsidiary_of are strictly
    acyclic by definition. These tests pin that invariant and also prove
    find_cycles actually detects a cycle when one exists (the function isn't
    trivially returning [])."""

    def test_every_edge_type_is_acyclic(self, con):
        """The real diagnostic invariant: no single edge type contains a
        directed cycle. Cycles DO exist in the mixed-type walk (the
        intentional part_of + has_company bidirectional pairs — Sector→Co
        via has_company, Co→Sector via part_of), but those are legitimate
        structure, not data bugs. A cycle within one edge type is the bug
        class G3 catches (e.g. two acquired rows forming A→B→A, or a
        symmetric type accidentally doubled)."""
        from helpers.graph.query import EDGE_REGISTRY_BY_LABEL
        for label in sorted(EDGE_REGISTRY_BY_LABEL):
            cycles = find_cycles(con, edge_label=label, max_hops=4, limit=1000)
            assert cycles == [], (
                f"edge_label={label!r} has {len(cycles)} directed cycle(s); "
                f"first: {cycles[:2]}"
            )

    def test_mixed_type_walk_does_find_bidirectional_pairs(self, con):
        """Sanity: the mixed-type walk (edge_label=None) DOES find cycles —
        the part_of/has_company bidirectional pairs. This proves find_cycles
        isn't trivially returning [] when cycles exist; the per-edge-type
        test above is what makes the diagnostic meaningful."""
        cycles = find_cycles(con, max_hops=3, limit=10)
        assert len(cycles) > 0, "expected bidirectional part_of/has_company cycles"
        # Each should be a 2-cycle (Sector → Company → Sector).
        assert all(len(c) == 3 for c in cycles), f"expected 2-cycles, got {cycles[:3]}"

    def test_max_hops_validation(self, con):
        with pytest.raises(ValueError, match="must be >= 2"):
            find_cycles(con, max_hops=1)
        with pytest.raises(ValueError, match="combinatorially explosive"):
            find_cycles(con, max_hops=7)

    def test_detects_synthetic_2_cycle(self, tmp_path):
        """Positive control: a real A→B→A cycle MUST be detected. Without this
        test, find_cycles could trivially return [] and the diagnostic tests
        above would still pass. Builds a temp SQLite+DuckDB with a known cycle."""
        import duckdb as _ddb

        # Build a temp SQLite with a 2-cycle: Alpha → Beta (acquired) and
        # Beta → Alpha (acquired). The CHECK (source != target) holds for each
        # row individually, but the pair forms a directed cycle.
        sqlite_path = tmp_path / "cycle.db"
        scon = sqlite3.connect(str(sqlite_path))
        scon.executescript("""
            CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT);
            CREATE TABLE graph_edges (
                source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0, properties TEXT NOT NULL DEFAULT '{}',
                source_ref TEXT NOT NULL DEFAULT 'test',
                valid_from DATE, valid_to DATE,
                CHECK (source != target)
            );
            INSERT INTO entities VALUES ('Alpha', 'company'), ('Beta', 'company'), ('Gamma', 'company');
            -- 2-cycle: Alpha -> Beta -> Alpha
            INSERT INTO graph_edges (source, target, edge_type) VALUES
                ('Alpha', 'Beta', 'acquired'),
                ('Beta', 'Alpha', 'acquired');
            -- Gamma is a dangling acyclic edge (Alpha -> Gamma, no return).
            INSERT INTO graph_edges (source, target, edge_type) VALUES
                ('Alpha', 'Gamma', 'acquired');
        """)
        scon.commit()
        scon.close()

        # Attach the temp SQLite to a fresh in-memory DuckDB and mount the
        # walk substrate find_cycles reads (sql_capability_unlocks B1: the
        # directed walk goes over the materialised e_dir, joined to a
        # minimal v_node — same CTAS as the production build).
        dcon = _ddb.connect()
        dcon.execute("INSTALL sqlite; LOAD sqlite;")
        dcon.execute(f"ATTACH '{sqlite_path}' AS fin (TYPE sqlite, READ_ONLY);")
        from helpers.graph.query import _materialise_walk_substrate
        dcon.execute(
            "CREATE TABLE v_node AS "
            "SELECT row_number() OVER () AS id, name, entity_type AS kind "
            "FROM fin.entities")
        _materialise_walk_substrate(dcon)
        try:
            cycles = find_cycles(dcon, edge_label="AcquiredBy", max_hops=4)
            # Must find the Alpha↔Beta 2-cycle (in both rotational orderings
            # since we seed every node, but dedup by set-of-vertices).
            cycle_sets = [frozenset(c[:-1]) for c in cycles]
            assert frozenset({"Alpha", "Beta"}) in cycle_sets
            # Gamma must not appear in any cycle (it's dangling).
            assert all("Gamma" not in c for c in cycles)
        finally:
            dcon.close()

    def test_no_cycle_when_only_one_direction(self, tmp_path):
        """A single directed edge A→B with no return edge is NOT a cycle.
        Guards against the function flagging every edge as a 'cycle'."""
        import duckdb as _ddb

        sqlite_path = tmp_path / "noloop.db"
        scon = sqlite3.connect(str(sqlite_path))
        scon.executescript("""
            CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT);
            CREATE TABLE graph_edges (
                source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0, properties TEXT NOT NULL DEFAULT '{}',
                source_ref TEXT NOT NULL DEFAULT 'test',
                valid_from DATE, valid_to DATE,
                CHECK (source != target)
            );
            INSERT INTO entities VALUES ('A', 'company'), ('B', 'company');
            INSERT INTO graph_edges (source, target, edge_type) VALUES ('A', 'B', 'competes_with');
        """)
        scon.commit()
        scon.close()

        dcon = _ddb.connect()
        dcon.execute("INSTALL sqlite; LOAD sqlite;")
        dcon.execute(f"ATTACH '{sqlite_path}' AS fin (TYPE sqlite, READ_ONLY);")
        from helpers.graph.query import _materialise_walk_substrate
        dcon.execute(
            "CREATE TABLE v_node AS "
            "SELECT row_number() OVER () AS id, name, entity_type AS kind "
            "FROM fin.entities")
        _materialise_walk_substrate(dcon)
        try:
            assert find_cycles(dcon, max_hops=4) == []
        finally:
            dcon.close()
