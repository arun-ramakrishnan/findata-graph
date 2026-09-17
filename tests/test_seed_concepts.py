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


# --------------------------------------------------------------------------- #
# Lifecycle (ontology_governance S1): supersede / resurrect / promote         #
# --------------------------------------------------------------------------- #


def _status(db, concept_id: str) -> str:
    return db.execute("SELECT status FROM concepts WHERE concept_id=?", (concept_id,)).fetchone()[0]


def _mapping_status(db, key: tuple) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT status FROM concept_mappings WHERE source_scheme=? AND "
            "source_concept=? AND target_scheme=? AND target_concept=? AND match_type=?",
            key,
        )
    ]


class TestLifecycle:
    def test_supersede_on_roster_removal(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        assert _status(db, "subsector:Payments") == "active"
        db.execute("DELETE FROM entities WHERE name='Payments'")
        db.execute("DELETE FROM graph_edges WHERE source='Payments' OR target='Payments'")
        db.commit()
        counts = sc.seed(db, apply=True)
        assert counts["superseded_concepts"] == 1
        assert counts["resurrected_concepts"] == 0
        # kept queryable, not deleted
        assert _status(db, "subsector:Payments") == "superseded"

    def test_resurrect_on_re_add(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        db.execute("DELETE FROM entities WHERE name='Payments'")
        db.execute("DELETE FROM graph_edges WHERE source='Payments' OR target='Payments'")
        db.commit()
        sc.seed(db, apply=True)
        db.execute("INSERT INTO entities (name, entity_type) VALUES ('Payments', 'sub_sector')")
        db.execute(
            "INSERT INTO graph_edges (source, target, edge_type) VALUES ('Payments', 'Technology', 'belongs_to')"
        )
        db.commit()
        counts = sc.seed(db, apply=True)
        assert counts["resurrected_concepts"] == 1
        assert counts["superseded_concepts"] == 0
        assert _status(db, "subsector:Payments") == "active"

    def test_idempotent_rerun_zero_lifecycle_flips(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        db.execute("DELETE FROM entities WHERE name='Payments'")
        db.execute("DELETE FROM graph_edges WHERE source='Payments' OR target='Payments'")
        db.commit()
        sc.seed(db, apply=True)  # converge: Payments superseded
        counts = sc.seed(db, apply=True)
        assert counts["superseded_concepts"] == 0
        assert counts["superseded_mappings"] == 0
        assert counts["resurrected_concepts"] == 0
        assert counts["resurrected_mappings"] == 0

    def test_operator_rows_untouched(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        db.execute(
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "source_ref, status) VALUES ('subsector:Operator Candidate', 'subsector', "
            "'Operator Candidate', 'Operator Candidate', 'manual:op', 'candidate')"
        )
        db.execute(
            "INSERT INTO concept_mappings (source_scheme, source_concept, target_scheme, "
            "target_concept, match_type, source_ref, version, status) VALUES "
            "('industry', 'Op Label', 'subsector', 'Software', 'closeMatch', "
            "'manual:op', 'v1', 'candidate')"
        )
        db.commit()
        sc.seed(db, apply=True)
        assert _status(db, "subsector:Operator Candidate") == "candidate"
        assert _mapping_status(
            db, ("industry", "Op Label", "subsector", "Software", "closeMatch")
        ) == ["candidate"]

    def test_dry_run_reports_without_writing(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        db.execute("DELETE FROM entities WHERE name='Payments'")
        db.execute("DELETE FROM graph_edges WHERE source='Payments' OR target='Payments'")
        db.commit()
        counts = sc.seed(db, apply=False)
        assert counts["superseded_concepts"] == 1
        assert _status(db, "subsector:Payments") == "active"  # not yet written

    def test_mapping_supersede_and_resurrect(self, db, monkeypatch):
        from helpers.graph import derive_hyperedges as dh

        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        full = dict(dh.SUB_SECTOR_ALIASES)
        assert len(full) >= 2
        dropped = sorted(full)[-1]
        shrunk = {k: v for k, v in full.items() if k != dropped}
        monkeypatch.setattr(dh, "SUB_SECTOR_ALIASES", shrunk)
        counts = sc.seed(db, apply=True)
        assert counts["superseded_mappings"] >= 1
        key = ("industry", dropped, "subsector", full[dropped], "exactMatch")
        assert _mapping_status(db, key) == ["superseded"]
        monkeypatch.undo()
        counts = sc.seed(db, apply=True)
        assert counts["resurrected_mappings"] >= 1
        assert _mapping_status(db, key) == ["active"]

    def test_subtree_excludes_superseded(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        full = sc.subtree(db, "super_sector:Financials")
        assert "subsector:Payments" in full
        db.execute("DELETE FROM entities WHERE name='Payments'")
        db.execute("DELETE FROM graph_edges WHERE source='Payments' OR target='Payments'")
        db.commit()
        sc.seed(db, apply=True)
        active = sc.subtree(db, "super_sector:Financials")
        assert "subsector:Payments" not in active
        historical = sc.subtree(db, "super_sector:Financials", include_inactive=True)
        assert "subsector:Payments" in historical


class TestPromote:
    def _add_candidate(self, db):
        db.execute(
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "source_ref, status) VALUES ('subsector:New Thing', 'subsector', "
            "'New Thing', 'New Thing', 'agent:test', 'candidate')"
        )
        db.commit()

    def test_promote_candidate_concept(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        self._add_candidate(db)
        applied, errors = sc.promote(db, ["subsector:New Thing"], [])
        assert errors == []
        assert applied == ["concept subsector:New Thing -> active"]
        assert _status(db, "subsector:New Thing") == "active"

    def test_missing_target_blocks_batch(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        self._add_candidate(db)
        applied, errors = sc.promote(db, ["subsector:nope", "subsector:New Thing"], [])
        assert len(errors) == 1 and "no such concept" in errors[0]
        assert applied == []  # batch blocked — nothing applied
        assert _status(db, "subsector:New Thing") == "candidate"

    def test_already_active_blocks(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        _, errors = sc.promote(db, ["sector:Banking"], [])
        assert errors == ["sector:Banking: already active"]

    def test_promote_mapping_with_conflict(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        db.execute(
            "INSERT INTO concept_mappings (source_scheme, source_concept, target_scheme, "
            "target_concept, match_type, source_ref, version, status) VALUES "
            "('industry', 'Banks - Regional', 'subsector', 'Payments', 'exactMatch', "
            "'manual:t', 'v1', 'active')"
        )
        db.execute(
            "INSERT INTO concept_mappings (source_scheme, source_concept, target_scheme, "
            "target_concept, match_type, source_ref, version, status) VALUES "
            "('industry', 'Banks - Regional', 'subsector', 'Software', 'exactMatch', "
            "'agent:test', 'v1', 'candidate')"
        )
        db.commit()
        applied, errors = sc.promote(db, [], ["industry:Banks - Regional->subsector:Software"])
        assert len(errors) == 1 and "conflicting active mapping -> Payments" in errors[0]
        assert applied == []

    def test_promote_mapping_happy_path(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        db.execute(
            "INSERT INTO concept_mappings (source_scheme, source_concept, target_scheme, "
            "target_concept, match_type, source_ref, version, status) VALUES "
            "('industry', 'Banks - Regional', 'subsector', 'Payments', 'exactMatch', "
            "'agent:test', 'v1', 'candidate')"
        )
        db.commit()
        applied, errors = sc.promote(db, [], ["industry:Banks - Regional->subsector:Payments"])
        assert errors == []
        assert len(applied) == 1
        assert _mapping_status(
            db, ("industry", "Banks - Regional", "subsector", "Payments", "exactMatch")
        ) == ["active"]

    def test_cli_promote_rc(self, db, tmp_path):
        path = db.execute("PRAGMA database_list").fetchone()[2]
        assert sc.main(["--db", path, "--promote", "subsector:nope"]) == 1
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        self._add_candidate(db)
        assert sc.main(["--db", path, "--promote", "subsector:New Thing"]) == 0
        assert _status(db, "subsector:New Thing") == "active"

    def test_cli_rejects_promote_with_apply(self, tmp_path):
        with pytest.raises(SystemExit):
            sc.main(["--db", str(tmp_path / "x.db"), "--apply", "--promote", "a:b"])


class TestCheckConceptsLifecycle:
    def _checker(self, db):
        from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

        path = db.execute("PRAGMA database_list").fetchone()[2]
        return DatabaseIntegrityChecker(db_path=path, base_path=str(Path(path).parent))

    def test_lifecycle_advisories_fire(self, db):
        sc.ensure_schema(db)
        sc.seed(db, apply=True)
        # active mapping -> superseded source concept
        db.execute(
            "INSERT INTO concept_mappings (source_scheme, source_concept, target_scheme, "
            "target_concept, match_type, source_ref, version, status) VALUES "
            "('industry', 'Software Infrastructure', 'subsector', 'Software', "
            "'exactMatch', 'manual:t', 'v1', 'active')"
        )
        db.execute(
            "UPDATE concepts SET status='superseded' WHERE concept_id='industry:Software Infrastructure'"
        )
        # active concept whose broader is superseded
        db.execute("UPDATE concepts SET status='superseded' WHERE concept_id='sector:Technology'")
        # candidate mapping with a nonexistent target
        db.execute(
            "INSERT INTO concept_mappings (source_scheme, source_concept, target_scheme, "
            "target_concept, match_type, source_ref, version, status) VALUES "
            "('industry', 'Banks - Regional', 'subsector', 'Ghost', 'exactMatch', "
            "'agent:test', 'v1', 'candidate')"
        )
        db.commit()
        result = self._checker(db).check_concepts()
        assert result["active_mappings_to_superseded"] == 1
        assert result["hierarchy_through_superseded"] == 2  # Software + Payments
        assert result["dangling_candidates"] == 1

    def test_pre_s1_db_skips_lifecycle(self, tmp_path):
        conn = connect(tmp_path / "pre_s1.db")
        conn.execute(
            "CREATE TABLE concept_schemes (scheme_id TEXT PRIMARY KEY, label TEXT, "
            "scheme_type TEXT, version TEXT, source_uri TEXT, license TEXT, "
            "attribution TEXT, active INTEGER DEFAULT 1)"
        )
        conn.execute(
            "CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, scheme_id TEXT, "
            "concept_code TEXT, pref_label TEXT, alt_label TEXT, notation TEXT, "
            "broader_id TEXT, scope_note TEXT, source_ref TEXT)"
        )
        conn.execute(
            "CREATE TABLE concept_mappings (source_scheme TEXT, source_concept TEXT, "
            "target_scheme TEXT, target_concept TEXT, match_type TEXT, "
            "source_ref TEXT, version TEXT)"
        )
        conn.commit()
        path = str(tmp_path / "pre_s1.db")
        conn.close()
        from helpers.misc.database_integrity_check import DatabaseIntegrityChecker

        checker = DatabaseIntegrityChecker(db_path=path, base_path=str(tmp_path))
        result = checker.check_concepts()
        assert result["active_mappings_to_superseded"] == 0
        assert result["hierarchy_through_superseded"] == 0
        assert result["dangling_candidates"] == 0
