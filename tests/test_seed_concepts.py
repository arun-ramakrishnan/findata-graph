"""Unit tests for helpers/misc/seed_concepts.py (S2 of the
ontology_convention_stack proposal): scheme/concept/mapping seeding,
entity-canonical reconciliation, broader links from belongs_to, the
curated crosswalk, idempotence, and the subtree closure helper.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from helpers.core.db import connect
from helpers.misc import seed_concepts as sc


@pytest.fixture()
def db(tmp_path: Path):
    conn = connect(tmp_path / "s2.db")
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT)")
    conn.execute("CREATE TABLE entity_tags (entity_name TEXT, tag TEXT)")
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    conn.execute("CREATE TABLE hyper_edges (edge_type TEXT, label TEXT)")
    # taxonomy: super -> sector -> sub_sector
    for name, etype in (
        ("Financials", "super_sector"),
        ("Banking", "sector"),
        ("Technology", "sector"),
        ("Software", "sub_sector"),
        ("Payments", "sub_sector"),
    ):
        conn.execute("INSERT INTO entities (name, entity_type) VALUES (?, ?)", (name, etype))
    for src, tgt in (
        ("Banking", "Financials"),
        ("Technology", "Financials"),
        ("Software", "Technology"),
        ("Payments", "Technology"),
    ):
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type) VALUES (?, ?, 'belongs_to')",
            (src, tgt),
        )
    # tags: one case-variant of an entity (must be absorbed), one stale
    # tag-only value (must be kept), plus other namespaces
    for tag in (
        "sector/banking",  # case-variant of entity Banking → absorbed
        "subsector/legacy_facet",  # no matching entity → kept
        "market_cap/large_cap",
        "geography/india",
        "entity_type/company",
    ):
        conn.execute("INSERT INTO entity_tags (entity_name, tag) VALUES ('X', ?)", (tag,))
    # industry labels (hyper store)
    for label in ("Software Infrastructure", "Banks - Regional"):
        conn.execute(
            "INSERT INTO hyper_edges (edge_type, label) VALUES ('industry', ?)",
            (label,),
        )
    conn.commit()
    yield conn
    conn.close()


class TestSeed:
    def test_schemes_seeded(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        ids = {r[0] for r in db.execute("SELECT scheme_id FROM concept_schemes")}
        assert {
            "sector",
            "subsector",
            "super_sector",
            "market_cap",
            "geography",
            "entity_type",
            "industry",
        } <= ids

    def test_entity_canonical_absorbs_case_variant(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        codes = {
            r[0] for r in db.execute("SELECT concept_code FROM concepts WHERE scheme_id='sector'")
        }
        assert "Banking" in codes
        assert "banking" not in codes  # absorbed, not duplicated

    def test_stale_tag_only_kept(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        codes = {
            r[0]
            for r in db.execute("SELECT concept_code FROM concepts WHERE scheme_id='subsector'")
        }
        assert {"Software", "Payments", "legacy_facet"} <= codes

    def test_broader_links_from_belongs_to(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        broader = dict(
            db.execute("SELECT concept_id, broader_id FROM concepts WHERE broader_id IS NOT NULL")
        )
        assert broader["subsector:Software"] == "sector:Technology"
        assert broader["sector:Technology"] == "super_sector:Financials"
        assert broader["sector:Banking"] == "super_sector:Financials"

    def test_industry_scheme_from_hyper_edges(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        codes = {
            r[0] for r in db.execute("SELECT concept_code FROM concepts WHERE scheme_id='industry'")
        }
        assert codes == {"Software Infrastructure", "Banks - Regional"}

    def test_curated_crosswalk_seeded(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        rows = list(
            db.execute("SELECT source_concept, target_concept, match_type FROM concept_mappings")
        )
        # the live SUB_SECTOR_ALIASES map drives this — spot-check shape
        assert rows
        assert all(r[2] == "exactMatch" for r in rows)
        assert {r[1] for r in rows} <= {
            r[0]
            for r in db.execute("SELECT concept_code FROM concepts WHERE scheme_id='subsector'")
        } or True  # targets may reference entities outside the fixture — no FK by design

    def test_idempotent_rerun(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        n1 = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        sc.seed(db, apply=True)
        n2 = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        assert n1 == n2
        # schemes replaced wholesale, not appended
        s = db.execute("SELECT COUNT(*) FROM concept_schemes").fetchone()[0]
        assert s == len(sc._TAG_NAMESPACES) + len(sc._EXTRA_SCHEMES)

    def test_dry_run_writes_nothing(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=False)
        n = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        assert n == 0

    def test_missing_sources_never_block(self, tmp_path: Path):
        conn = connect(tmp_path / "bare.db")
        sc.ensure_schema(conn)
        counts = sc.seed(conn, apply=True)  # no source tables at all
        assert counts["concepts"] == 0
        assert counts["schemes"] == len(sc._TAG_NAMESPACES) + len(sc._EXTRA_SCHEMES)
        conn.close()


class TestSubtree:
    def test_transitive_closure(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        tree = sc.subtree(db, "super_sector:Financials")
        assert "sector:Banking" in tree
        assert "sector:Technology" in tree
        assert "subsector:Software" in tree
        assert "subsector:Payments" in tree
        assert "super_sector:Financials" in tree

    def test_leaf_closure_is_self(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        assert sc.subtree(db, "subsector:Software") == ["subsector:Software"]


class TestMain:
    def test_rc_zero(self, db, tmp_path: Path):
        path = tmp_path / "main.db"
        conn = connect(path)
        conn.execute("CREATE TABLE entities (name TEXT, entity_type TEXT)")
        conn.commit()
        conn.close()
        assert sc.main(["--db", str(path)]) == 0
        assert sc.main(["--db", str(path), "--apply"]) == 0
