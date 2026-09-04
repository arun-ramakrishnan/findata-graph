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


@pytest.mark.live
class TestLiveGraph:
    LIVE = REPO_ROOT / "memory" / "graph.duckdb"

    @pytest.fixture(autouse=True)
    def _need_live(self):
        if not self.LIVE.exists():
            pytest.skip("live graph.duckdb not present")

    def test_live_pack_for_mm(self):
        con = duckdb.connect(str(self.LIVE), read_only=True)
        try:
            pack = CP.build_context_pack(con, "Mahindra & Mahindra", budget=15)
        finally:
            con.close()
        assert "# Context pack — Mahindra & Mahindra" in pack
        assert "subsidiary_of" in pack
        assert "## Sector rollup" in pack
        assert "_budget:" in pack
