"""Tests for helpers/graph/context_pack.py (C1 GraphRAG-lite packs).

Unit tests build a minimal synthetic graph.duckdb (v_node + the e_* star
tables from _EDGE_SPECS) so they run without the live DB; one live test
exercises the real memory/graph.duckdb when present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

duckdb = pytest.importorskip("duckdb")

from helpers.graph import context_pack as CP  # noqa: E402
from helpers.graph import query  # noqa: E402

DDL = """
CREATE TABLE v_node(id BIGINT, name VARCHAR, kind VARCHAR,
                    sector_classification VARCHAR, market_cap VARCHAR,
                    ticker VARCHAR);
CREATE TABLE v_company(id BIGINT, name VARCHAR, sector_classification VARCHAR,
                       market_cap VARCHAR, ticker VARCHAR);
"""


@pytest.fixture()
def con(tmp_path: Path):
    """Synthetic graph: Acme (seed) + partners across every edge type."""
    c = duckdb.connect(str(tmp_path / "graph.duckdb"))
    c.execute(DDL)
    nodes = [
        (1, "Acme", "company", "Aerospace", "large_cap", "ACME.NS"),
        (2, "Sub One", "company", "Aerospace", "small_cap", None),
        (3, "Old Target", "company", "Aerospace", None, None),
        (4, "JV Friend", "company", "Aerospace", None, None),
        (5, "Competitor", "company", "Aerospace", None, "COMP.NS"),
        (6, "Acme Supplier", "company", "Industrials", None, None),
        (7, "Sector_A", "sector", "Aerospace", None, None),
        (8, "Theme_X", "theme", None, None, None),
        (9, "Far Co", "company", "FMCG", None, None),
    ]
    c.executemany("INSERT INTO v_node VALUES (?, ?, ?, ?, ?, ?)", nodes)
    c.executemany(
        "INSERT INTO v_company VALUES (?, ?, ?, ?, ?)",
        [(1, "Acme", "Aerospace", "large_cap", "ACME.NS")],
    )
    edges = [
        ("e_subsidiary", 2, 1, 1.0, "note:Sub One"),
        ("e_acquired", 1, 3, 1.0, "note:Acme"),
        ("e_jv", 1, 4, 0.7, None),
        ("e_competes", 1, 5, 0.5, None),
        ("e_supplier", 6, 1, 0.9, None),
        ("e_belongs_to", 1, 7, 1.0, None),
        ("e_exposed_to", 1, 8, 0.3, None),
    ]
    for tbl in (
        "e_subsidiary",
        "e_acquired",
        "e_jv",
        "e_supplier",
        "e_customer",
        "e_group",
        "e_competes",
        "e_belongs_to",
        "e_exposed_to",
        "e_cited_in",
        "e_comention",
    ):
        cols = {
            "e_subsidiary": ("subsidiary_name", "parent_name"),
            "e_acquired": ("acquirer_name", "target_name"),
            "e_jv": ("a_name", "b_name"),
            "e_supplier": ("supplier_name", "customer_name"),
            "e_customer": ("customer_name", "supplier_name"),
            "e_group": ("a_name", "b_name"),
            "e_competes": ("a_name", "b_name"),
            "e_belongs_to": ("child_id", "parent_id"),
            "e_exposed_to": ("company_id", "theme_id"),
            "e_cited_in": ("company_id", "edition_id"),
            "e_comention": ("a_name", "b_name"),
        }[tbl]
        c.execute(
            f"CREATE TABLE {tbl}({cols[0]} BIGINT, {cols[1]} BIGINT, weight DOUBLE,"
            " properties VARCHAR, source_ref VARCHAR, valid_from DATE, valid_to DATE"
            + (", year VARCHAR" if tbl == "e_acquired" else "")
            + ")"
        )
        for _t, s, o, w, src in edges:
            if _t == tbl:
                year = "2023" if tbl == "e_acquired" else None
                c.execute(
                    f"INSERT INTO {tbl} VALUES (?, ?, ?, NULL, ?, NULL, NULL"  # noqa: S608  # fixture-local table constant
                    + (", ?" if tbl == "e_acquired" else "")
                    + ")",
                    [s, o, w, src] + ([year] if tbl == "e_acquired" else []),
                )
    # extra comention + a far comention NOT touching Acme (must not appear)
    c.execute("INSERT INTO e_comention VALUES (1, 9, 2.0, NULL, 'chatter', NULL, NULL)")
    c.execute("INSERT INTO e_comention VALUES (5, 9, 5.0, NULL, 'chatter', NULL, NULL)")
    yield c
    c.close()


class TestBuildContextPack:
    def test_profile_section(self, con):
        pack = CP.build_context_pack(con, "Acme")
        assert "# Context pack — Acme" in pack
        for bit in (
            "kind: company",
            "sector: Aerospace",
            "market_cap: large_cap",
            "ticker: ACME.NS",
        ):
            assert bit in pack

    def test_name_resolution_case_insensitive_and_ticker(self, con):
        assert "— Acme" in CP.build_context_pack(con, "acme")
        assert "# Context pack — Acme" in CP.build_context_pack(con, "ACME.NS")

    def test_unknown_entity_raises(self, con):
        with pytest.raises(ValueError, match="not found"):
            CP.build_context_pack(con, "No Such Co")

    def test_every_edge_type_directionalized(self, con):
        pack = CP.build_context_pack(con, "Acme", budget=50)
        expected = (
            "Sub One —subsidiary_of→ Acme",
            "Acme —acquired→ Old Target (w=1, 2023, src=note:Acme)",
            "Acme —joint_venture_with→ JV Friend",
            "Acme —competes_with→ Competitor",
            "Acme Supplier —supplies_to→ Acme",
            "Acme —belongs_to→ Sector_A",
            "Acme —exposed_to→ Theme_X",
            "Acme —co_mentioned_with→ Far Co",
        )
        for bit in expected:
            assert bit in pack, bit

    def test_far_comention_edge_not_in_ego_pack(self, con):
        # Competitor<->Far Co does not touch Acme -> excluded at hops=1
        pack = CP.build_context_pack(con, "Acme", budget=50)
        assert "Competitor —co_mentioned_with→ Far Co" not in pack

    def test_cited_in_fact_renders_but_never_expands_hops(self, con):
        # okf_activation P: cited_in (company -> edition) renders as a
        # display fact ranked LAST (trims with the firehose), and hop
        # expansion never runs through editions — Far Co citing the same
        # edition must NOT enter Acme's pack via the edition.
        con.execute("INSERT INTO v_node VALUES (10, 'Edition_Q1', 'edition', NULL, NULL, NULL)")
        con.execute("INSERT INTO e_cited_in VALUES (1, 10, 1.0, NULL, 'okf', NULL, NULL)")
        con.execute("INSERT INTO e_cited_in VALUES (9, 10, 1.0, NULL, 'okf', NULL, NULL)")
        pack = CP.build_context_pack(con, "Acme", budget=50)
        assert "Acme —cited_in→ Edition_Q1" in pack
        # Far Co enters via the co-mention edge, never via the shared edition:
        # exactly ONE fact mentions Far Co, and the Far Co->edition edge (no
        # endpoint in the pack's id set) is never collected.
        assert pack.count("Far Co") == 1
        assert "Far Co —cited_in→ Edition_Q1" not in pack

    def test_budget_trims_comention_first(self, con):
        full = CP.build_context_pack(con, "Acme", budget=50)
        small = CP.build_context_pack(con, "Acme", budget=5)
        # structural facts survive; the lowest-priority tail drops
        assert "subsidiary_of" in small and "acquired" in small
        assert "co_mentioned_with" not in small
        assert "of 8 available" in full and "of 8 available" in small

    def test_footer_reports_fact_budget_and_char_estimate(self, con):
        pack = CP.build_context_pack(con, "Acme", budget=5)
        assert "_budget: 5/5 relation facts kept of 8 available" in pack
        assert "chars ≈" in pack and "tokens_" in pack

    def test_sector_rollup_counts_pack_entities(self, con):
        pack = CP.build_context_pack(con, "Acme", budget=50)
        assert "## Sector rollup" in pack
        assert "| Aerospace |" in pack and "| Industrials |" in pack

    def test_hops_2_expands_structured_only(self, con):
        # hops=2: Sub One's own edges enter the pack
        pack = CP.build_context_pack(con, "Acme", hops=2, budget=50)
        assert "of 9 available" in pack  # +Competitor<->FarCo comention (+1)

    def test_deterministic_output(self, con):
        a = CP.build_context_pack(con, "Acme", budget=7)
        b = CP.build_context_pack(con, "Acme", budget=7)
        assert a == b

    def test_no_semantic_section_without_embeddings(self, con):
        pack = CP.build_context_pack(con, "Acme")
        assert "## Semantic neighbors" not in pack


R3_DDL = """
CREATE TABLE ultimate_parent (
    company TEXT PRIMARY KEY, ultimate_parent TEXT, depth INTEGER,
    path TEXT, root_kind TEXT, coalition_pct REAL, computed_at DATETIME);
CREATE TABLE hidden_connections (
    a TEXT, b TEXT, distance INTEGER, shared_count INTEGER,
    intermediaries TEXT, computed_at DATETIME, PRIMARY KEY (a, b));
CREATE TABLE supply_chokepoints (
    supplier TEXT PRIMARY KEY, competitors_served INTEGER,
    competitor_dyads INTEGER, companies TEXT, computed_at DATETIME);
CREATE TABLE competitive_overlap (
    a TEXT, b TEXT, c TEXT, computed_at DATETIME, PRIMARY KEY (a, b, c));
"""


@pytest.fixture()
def r3db(tmp_path: Path):
    """R2 deep-relations fixture: the four R3 insight tables for the Acme
    universe (self-root with coalition, 2-company descendant forest)."""
    import json
    import sqlite3

    p = tmp_path / "research.db"
    conn = sqlite3.connect(p)
    conn.executescript(R3_DDL)
    conn.executemany(
        "INSERT INTO ultimate_parent VALUES (?,?,?,?,?,?, '2026-10-25')",
        [
            ("Acme", "Acme", 0, json.dumps(["Acme"]), "self-root", 64.1),
            ("Sub One", "Acme", 1, json.dumps(["Sub One", "Acme"]), "chain-root", None),
            (
                "SubSub Co",
                "Acme",
                2,
                json.dumps(["SubSub Co", "Sub One", "Acme"]),
                "chain-root",
                None,
            ),
            (
                "JV Friend",
                "HoldX",
                1,
                json.dumps(["JV Friend", "HoldX"]),
                "chain-root",
                None,
            ),
        ],
    )
    conn.execute(
        "INSERT INTO hidden_connections VALUES "
        "('Acme','Rival Co',2,2,'[\"NicheSup\",\"JvMid\"]','2026-10-25')"
    )
    conn.execute(
        "INSERT INTO competitive_overlap VALUES ('Acme','Competitor','Comp C','2026-10-25')"
    )
    conn.execute(
        "INSERT INTO supply_chokepoints VALUES "
        "('BigSup',3,2,'[\"Acme\",\"Competitor\",\"Comp C\"]','2026-10-25')"
    )
    conn.commit()
    yield p
    conn.close()


class TestDeepRelations:
    def test_control_self_root_with_descendant_forest(self, con, r3db):
        pack = CP.build_context_pack(con, "Acme", budget=50, research_db=r3db)
        assert "## Deep relations" in pack
        assert "### Control" in pack
        assert "- control: self-root (no trusted parent) — promoter coalition 64.1%" in pack
        assert (
            "- descendant forest: 2 companies roll up to this root (1 direct); "
            "nearest: Sub One (1 hop), SubSub Co (2 hops)" in pack
        )

    def test_chained_control_line(self, con, r3db):
        pack = CP.build_context_pack(con, "Sub One", budget=50, research_db=r3db)
        assert "- ultimate parent: Acme (depth 1; chain: Sub One → Acme)" in pack
        assert (
            "- descendant forest: 1 companies roll up to this root (1 direct); nearest: SubSub Co (1 hop)"
            in pack
        )

    def test_insight_rollups(self, con, r3db):
        pack = CP.build_context_pack(con, "Acme", budget=50, research_db=r3db)
        assert "### Insight rollups" in pack
        assert "- hidden connections (1): Rival Co" in pack
        assert "- competitor-of-competitor set (2): Comp C, Competitor" in pack
        assert "- chokepoint suppliers (1 — shared with rivals): BigSup" in pack

    def test_two_step_supply_with_corroboration_floor(self, con, r3db):
        con.execute(
            "INSERT INTO v_node VALUES (20, 'Rival Co', 'company', 'Aerospace', NULL, NULL)"
        )
        con.execute(
            "INSERT INTO v_node VALUES (21, 'NicheSup', 'company', 'Industrials', NULL, NULL)"
        )
        con.execute(
            "INSERT INTO v_node VALUES (22, 'Weak Rival', 'company', 'Aerospace', NULL, NULL)"
        )
        # NicheSup supplies both Acme and Acme Supplier (my supplier -> 2nd tier);
        # Rival Co is reached from BOTH of Acme's suppliers (2 shared -> kept);
        # Weak Rival from one only (below the floor -> dropped).
        con.execute("INSERT INTO e_supplier VALUES (21, 1, 1.0, NULL, NULL, NULL, NULL)")
        con.execute("INSERT INTO e_supplier VALUES (21, 6, 1.0, NULL, NULL, NULL, NULL)")
        con.execute("INSERT INTO e_supplier VALUES (6, 20, 1.0, NULL, NULL, NULL, NULL)")
        con.execute("INSERT INTO e_supplier VALUES (21, 20, 1.0, NULL, NULL, NULL, NULL)")
        con.execute("INSERT INTO e_supplier VALUES (6, 22, 1.0, NULL, NULL, NULL, NULL)")
        pack = CP.build_context_pack(con, "Acme", budget=50, research_db=r3db)
        assert "### Two-step supply" in pack
        assert "- shared-supply rivals (Rival Co (2))" in pack
        assert "Weak Rival" not in pack
        assert "- second-tier suppliers: NicheSup" in pack

    def test_groups_and_editions_hyperedge_families(self, con, r3db):
        con.execute(
            "CREATE TABLE h_edge(id BIGINT, edge_type VARCHAR, label VARCHAR, "
            "weight DOUBLE, valid_from DATE, valid_to DATE, source_ref VARCHAR)"
        )
        con.execute(
            "CREATE TABLE h_incidence(edge_id BIGINT, entity_name VARCHAR, "
            "weight DOUBLE, direction VARCHAR, role VARCHAR, "
            "valid_from DATE, valid_to DATE)"
        )
        con.execute(
            "INSERT INTO h_edge VALUES (1, 'group', 'Aerospace Group', 1.0, NULL, NULL, NULL)"
        )
        con.execute("INSERT INTO h_edge VALUES (2, 'edition', 'Q1 Roundup', 1.0, NULL, NULL, NULL)")
        con.execute("INSERT INTO h_incidence VALUES (1, 'Acme', 1.0, NULL, NULL, NULL, NULL)")
        con.execute("INSERT INTO h_incidence VALUES (2, 'Acme', 1.0, NULL, NULL, NULL, NULL)")
        pack = CP.build_context_pack(con, "Acme", budget=50, research_db=r3db)
        assert "### Groups & editions" in pack
        assert "- group (1): Aerospace Group" in pack
        assert "- edition (1): Q1 Roundup" in pack

    def test_absent_research_db_omits_section(self, con):
        pack = CP.build_context_pack(con, "Acme", budget=50)
        assert "## Deep relations" not in pack

    def test_pre_r3_research_db_tolerated(self, con, tmp_path):
        import sqlite3

        p = tmp_path / "pre_r3.db"
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE entities (name TEXT)")
        c.commit()
        c.close()
        pack = CP.build_context_pack(con, "Acme", budget=50, research_db=p)
        assert "## Deep relations" not in pack  # nothing rolls up on a pre-R3 store


@pytest.mark.live
class TestLiveDeepRelations:
    LIVE = REPO_ROOT / "memory" / "graph.duckdb"

    @pytest.fixture(autouse=True)
    def _need_live(self):
        if not self.LIVE.exists():
            pytest.skip("live graph.duckdb not present")

    def test_live_deep_relations_for_tata_steel(self):
        con = query.connect_read_only(self.LIVE)
        try:
            pack = CP.build_context_pack(
                con,
                "Tata Steel",
                budget=15,
                research_db=REPO_ROOT / "memory" / "research.db",
            )
        finally:
            con.close()
        assert "## Deep relations" in pack
        assert "### Control" in pack
        assert "descendant forest" in pack


@pytest.mark.live
class TestLiveGraph:
    LIVE = REPO_ROOT / "memory" / "graph.duckdb"

    @pytest.fixture(autouse=True)
    def _need_live(self):
        if not self.LIVE.exists():
            pytest.skip("live graph.duckdb not present")

    def test_live_pack_for_mm(self):
        # Shared reader contract: routes over the quack wire when the store
        # is served (a local read-only open is refused by the server's
        # read-write lock) and keeps the file path when it is not.
        con = query.connect_read_only(self.LIVE)
        try:
            pack = CP.build_context_pack(con, "Mahindra & Mahindra", budget=15)
        finally:
            con.close()
        assert "# Context pack — Mahindra & Mahindra" in pack
        assert "subsidiary_of" in pack
        assert "## Sector rollup" in pack
        assert "_budget:" in pack
