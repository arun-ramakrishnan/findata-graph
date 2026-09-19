#!/usr/bin/env python3
"""Tests for helpers/misc/seed_nic2008.py (nic2008_seed_table S1).

Build-parse pins: the tracked seed JSON is itself the parse artifact — tests
pin its counts, attested rows, and parent-chain integrity, plus parser
behaviour on a synthetic PDF-text excerpt covering the primary-source defects
(collapsed header, note continuation masquerading as a division header, bare
code lines, no-space code+title rows). Converger pins: idempotence run twice
(the acceptance criterion), update/dropped semantics on an in-memory DB.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
HELPERS_MISC = REPO / "helpers" / "misc"
SPEC = importlib.util.spec_from_file_location("seed_nic2008", HELPERS_MISC / "seed_nic2008.py")
assert SPEC is not None and SPEC.loader is not None
sn = importlib.util.module_from_spec(SPEC)
sys.modules["seed_nic2008"] = sn
SPEC.loader.exec_module(sn)

SEED = json.loads((HELPERS_MISC / "nic2008_seed.json").read_text(encoding="utf-8"))


class TestSeedJson:
    """The tracked seed JSON is the vendored parse artifact — pin it."""

    def test_counts_match_expectation(self):
        assert SEED["counts"] == {
            "sections": 21,
            "divisions": 88,
            "groups": 238,
            "classes": 419,
            "subclasses": 1301,
        }

    def test_attested_rows_byte_equal(self):
        by_code = {r["code"]: r for r in SEED["subclasses"]}
        # NOTE: the proposal's "62090 IT services" pin was a misattestation —
        # 62090 does not exist in NIC-2008; 62099 is the real row.
        assert by_code["01111"]["description"] == "Growing of wheat"
        assert by_code["10728"]["description"] == "Manufacture of molasses"
        assert (
            by_code["62099"]["description"]
            == "Other information technology and computer service activities n.e.c"
        )

    def test_parent_chain_integrity(self):
        sections = {r["code"] for r in SEED["sections"]}
        divisions = {r["code"] for r in SEED["divisions"]}
        groups = {r["code"] for r in SEED["groups"]}
        classes = {r["code"] for r in SEED["classes"]}
        for d in SEED["divisions"]:
            assert d["section"] in sections, d
        for g in SEED["groups"]:
            assert g["division"] in divisions, g
            assert g["code"][:2] == g["division"], g
        for c in SEED["classes"]:
            assert c["group"] in groups, c
            assert c["code"][:3] == c["group"], c
        for s in SEED["subclasses"]:
            assert s["class"] in classes, s
            if s["code"] not in sn.PARENT_OVERRIDE:
                assert s["code"][:4] == s["class"], s

    def test_sections_a_through_u(self):
        assert [r["code"] for r in SEED["sections"]] == list("ABCDEFGHIJKLMNOPQRSTU")

    def test_prefix_quirk_parents(self):
        by_code = {r["code"]: r for r in SEED["subclasses"]}
        # printed by BOTH official sources under these parents (DGE's class
        # column is a mechanical prefix slice — it names nonexistent classes)
        assert by_code["20203"]["class"] == "2030"
        assert by_code["65020"]["class"] == "6520"
        assert by_code["96903"]["class"] == "9609"
        assert by_code["96908"]["class"] == "9609"

    def test_zero_ending_singles_present(self):
        # single-subclass classes the PDF prints and the DGE table drops
        codes = {r["code"] for r in SEED["subclasses"]}
        assert {"01420", "30120", "31001", "37001", "47640", "81100"} <= codes

    def test_defect_map_rows_normalized(self):
        codes = {r["code"] for r in SEED["subclasses"]}
        assert "84230" in codes and "88230" not in codes
        assert "85494" in codes and "95494" not in codes
        fixes = dict(SEED["defect_fixes"])
        assert fixes.get("88230") == "84230"

    def test_no_page_break_header_glue(self):
        # the PDF re-prints "Group Class Sub- Description" after every page
        # break — 56 descriptions + 29 notes were glued before the skip
        # regex landed; pin the three-way absence
        for r in SEED["subclasses"]:
            assert "Group   Class" not in r["description"], r["code"]
        for note in SEED["notes"].values():
            assert "Group   Class" not in note
        by_code = {r["code"]: r for r in SEED["subclasses"]}
        assert by_code["01139"]["description"] == "Growing of vegetables, n.e.c."

    def test_class_notes_captured(self):
        # scope notes from the PDF's This class includes/excludes statements
        assert len(SEED["notes"]) > 300
        wheat_class = SEED["notes"]["0111"]
        assert "growing of cereals" in wheat_class.lower()


MINI_PDF_TEXT = """
National Industrial Classification 2008

Group    Class    Sub-
                 class

Section A     Agriculture, forestry and fishing
Division 01   Crop and animal production, hunting
Division 16   Manufacture of wood and products of wood and cork

SECTION A : AGRICULTURE, FORESTY AND FISHING

Division 01 : Crop and animal production, hunting and related service activities
 011                    Growing of non-perennial crops
        0111            Growing of cereals (except rice), leguminous crops and oil seeds
                        This class includes all forms of growing of cereals in open fields
                        This class excludes:
                        - growing of maize for fodder, see 0119
                01111   Growing of wheat
Division16:Manufactureofwoodandofproductsofwoodandcork,except furniture
 161               Saw milling and planing of wood
       1610        Saw milling and planing of wood
                      This class includes repair and maintenance of fabricated metal products
                      division 25
               16101   Sawing and planing of wood
               16102Manufacture of card board boxes
"""


class TestParserMiniFixture:
    """Parser behaviour pinned on a synthetic excerpt (no PDF needed)."""

    @pytest.fixture()
    def parsed_mini(self):
        return sn.parse_pdf_text(MINI_PDF_TEXT)

    def test_section_header_with_colon_title(self, parsed_mini):
        assert parsed_mini["sections"] == [
            {"code": "A", "description": "Agriculture, Foresty And Fishing"}
        ]

    def test_division_with_wrapped_title(self, parsed_mini):
        by_code = {r["code"]: r for r in parsed_mini["divisions"]}
        assert "01" in by_code
        assert by_code["01"]["description"].startswith("Crop and animal production")

    def test_collapsed_division_header_repaired_from_summary(self, parsed_mini):
        by_code = {r["code"]: r for r in parsed_mini["divisions"]}
        # Division16:Manufactureofwood... — no spaces; summary part has the
        # properly spaced title
        assert "16" in by_code
        assert by_code["16"]["description"].startswith("Manufacture of wood")

    def test_note_continuation_not_a_division_header(self, parsed_mini):
        codes = {r["code"] for r in parsed_mini["divisions"]}
        assert "25" not in codes  # 'division 25' is a wrapped note, no colon/title

    def test_subclass_emitted_under_class(self, parsed_mini):
        by_code = {r["code"]: r for r in parsed_mini["subclasses"]}
        assert by_code["01111"]["class"] == "0111"
        assert by_code["01111"]["description"] == "Growing of wheat"

    def test_no_space_code_title_row(self, parsed_mini):
        by_code = {r["code"]: r for r in parsed_mini["subclasses"]}
        assert by_code["16102"]["description"].startswith("Manufacture of card")

    def test_note_attached_to_class(self, parsed_mini):
        assert "0111" in parsed_mini["notes"]


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


class TestConverge:
    """Converger: insert/update/report-dropped, idempotent twice."""

    def test_ensure_schema_creates_table(self):
        conn = _memory_conn()
        sn.ensure_schema(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(nic2008)")]
        assert cols == [
            "subclass",
            "class",
            "grp",
            "division",
            "section",
            "description",
            "isic4",
            "nace21",
            "gics",
            "wikidata",
            "scope_note",
            "version",
        ]

    def test_converge_inserts_then_idempotent_twice(self, tmp_path, monkeypatch):
        conn = _memory_conn()
        r1 = sn.converge(conn, apply=True)
        assert r1["inserted"] == 1301 and r1["table_rows"] == 1301
        rows = conn.execute("SELECT COUNT(*) FROM nic2008").fetchone()[0]
        assert rows == 1301
        # acceptance: second dry-run reports zero changes — repeated twice
        r2 = sn.converge(conn, apply=True)
        r3 = sn.converge(conn, apply=True)
        assert (r2["inserted"], r2["updated"]) == (0, 0)
        assert (r3["inserted"], r3["updated"]) == (0, 0)

    def test_converge_updates_changed_row(self):
        conn = _memory_conn()
        sn.converge(conn, apply=True)
        conn.execute("UPDATE nic2008 SET description='TAMPERED' WHERE subclass='01111'")
        conn.commit()
        rep = sn.converge(conn, apply=True)
        assert rep["updated"] == 1
        got = conn.execute("SELECT description FROM nic2008 WHERE subclass='01111'").fetchone()[0]
        assert got == "Growing of wheat"

    def test_converge_reports_dropped_never_deletes(self):
        conn = _memory_conn()
        sn.converge(conn, apply=True)
        conn.execute(
            "INSERT INTO nic2008 (subclass, class, grp, division, section,"
            " description, isic4, version) VALUES ('99999','9999','999','99','Z',"
            " 'ghost','9999','NIC-1998')"
        )
        conn.commit()
        rep = sn.converge(conn, apply=True)
        assert rep["dropped_reported"] == 1
        still = conn.execute("SELECT COUNT(*) FROM nic2008 WHERE subclass='99999'").fetchone()[0]
        assert still == 1  # #244 S1: never delete

    def test_scope_notes_denormalised_from_class(self):
        conn = _memory_conn()
        sn.converge(conn, apply=True)
        row = conn.execute("SELECT scope_note FROM nic2008 WHERE subclass='01111'").fetchone()
        assert row[0] and "growing of cereals" in row[0].lower()

    def test_chain_columns_of_quirk_rows(self):
        conn = _memory_conn()
        sn.converge(conn, apply=True)
        row = conn.execute(
            "SELECT class, grp, division, section FROM nic2008 WHERE subclass='65020'"
        ).fetchone()
        assert tuple(row) == ("6520", "652", "65", "K")


class TestSchemeProjection:
    """S2: the five-level concept scheme from the same seed JSON."""

    @pytest.fixture()
    def concept_rows(self):
        return sn.scheme_concepts(SEED)

    def test_concept_count_is_five_levels(self, concept_rows):
        assert len(concept_rows) == 21 + 88 + 238 + 419 + 1301

    def test_notation_is_code(self, concept_rows):
        for cid, row in concept_rows.items():
            assert row[5] == row[2]  # notation == concept_code, all levels

    def test_section_roots_have_no_broader(self, concept_rows):
        for sec in SEED["sections"]:
            row = concept_rows[f"nic2008:{sec['code']}"]
            assert row[6] is None

    def test_it_chain_five_levels(self, concept_rows):
        # 62099 (the corrected IT pin — 62090 doesn't exist) chains up
        # class→group→division→section exactly once per level
        cid = "nic2008:62099"
        chain = []
        while cid is not None:
            row = concept_rows[cid]
            chain.append(row[2])
            cid = row[6]
        assert chain == ["62099", "6209", "620", "62", "J"]

    def test_quirk_subclass_under_true_class(self, concept_rows):
        assert concept_rows["nic2008:65020"][6] == "nic2008:6520"
        assert concept_rows["nic2008:96903"][6] == "nic2008:9609"

    def test_scope_notes_on_classes_not_subclasses(self, concept_rows):
        assert concept_rows["nic2008:0111"][7]
        assert concept_rows["nic2008:01111"][7] is None  # inherited via subtree

    def test_source_ref_and_scheme(self, concept_rows):
        row = concept_rows["nic2008:01111"]
        assert row[1] == "nic2008" and row[8] == "seed:nic2008/NIC-2008"


class TestSchemeConverge:
    """Scheme converger: lifecycle idiom, idempotent, foreign rows safe."""

    def test_converge_projects_scheme_and_table(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s2.db")
        try:
            rep = sn.converge(conn, apply=True)
            assert rep["scheme_concepts"] == 2067 and rep["scheme_inserted"] == 2067
            assert rep["table_rows"] == 1301
            scheme = conn.execute(
                "SELECT scheme_id, scheme_type, version, active FROM concept_schemes"
                " WHERE scheme_id='nic2008'"
            ).fetchone()
            assert tuple(scheme) == ("nic2008", "classification", "NIC-2008", 1)
            n = conn.execute(
                "SELECT COUNT(*) FROM concepts WHERE scheme_id='nic2008' AND status='active'"
            ).fetchone()[0]
            assert n == 2067
        finally:
            conn.close()

    def test_scheme_idempotent_twice(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s2.db")
        try:
            sn.converge(conn, apply=True)
            for _ in range(2):
                rep = sn.converge(conn, apply=True)
                assert rep["scheme_inserted"] == 0
                assert rep["scheme_updated"] == 0
                assert rep["scheme_superseded"] == 0
        finally:
            conn.close()

    def test_subtree_closure_section_to_subclasses(self, tmp_path):
        from helpers.core.db import connect
        from helpers.misc.seed_concepts import subtree

        conn = connect(tmp_path / "s2.db")
        try:
            sn.converge(conn, apply=True)
            # acceptance: subtree() on a projected section returns the full
            # chain through to its subclasses
            st = subtree(conn, "nic2008:J")
            assert "nic2008:J" in st
            assert "nic2008:62" in st and "nic2008:6209" in st
            assert "nic2008:62099" in st
            assert "nic2008:62091" in st
            # and a division root too
            st62 = subtree(conn, "nic2008:62")
            assert "nic2008:62099" in st62
        finally:
            conn.close()

    def test_tampered_concept_restored(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s2.db")
        try:
            sn.converge(conn, apply=True)
            conn.execute(
                "UPDATE concepts SET pref_label='TAMPERED', notation=NULL"
                " WHERE concept_id='nic2008:01111'"
            )
            conn.commit()
            rep = sn.converge(conn, apply=True)
            assert rep["scheme_updated"] == 1
            row = conn.execute(
                "SELECT pref_label, notation FROM concepts WHERE concept_id='nic2008:01111'"
            ).fetchone()
            assert tuple(row) == ("Growing of wheat", "01111")
        finally:
            conn.close()

    def test_lapsed_seed_row_supersedes_never_deletes(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s2.db")
        try:
            sn.converge(conn, apply=True)
            conn.execute(
                "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label,"
                " notation, broader_id, source_ref, status)"
                " VALUES ('nic2008:99999','nic2008','99999','ghost','99999',NULL,"
                " 'seed:nic2008/NIC-1998','active')"
            )
            conn.commit()
            rep = sn.converge(conn, apply=True)
            assert rep["scheme_superseded"] == 1
            st = conn.execute(
                "SELECT status FROM concepts WHERE concept_id='nic2008:99999'"
            ).fetchone()[0]
            assert st == "superseded"
        finally:
            conn.close()

    def test_foreign_rows_untouched(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s2.db")
        try:
            sn.converge(conn, apply=True)
            conn.execute(
                "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label,"
                " notation, source_ref, status)"
                " VALUES ('nic2008:agentrow','nic2008','agentrow','agent-curated',NULL,"
                " 'agent:op/2026','active')"
            )
            conn.commit()
            sn.converge(conn, apply=True)
            st = conn.execute(
                "SELECT status FROM concepts WHERE concept_id='nic2008:agentrow'"
            ).fetchone()[0]
            assert st == "active"
        finally:
            conn.close()


class TestCoexistence:
    """seed_concepts and seed_nic2008 own disjoint rosters — the S2 contract.

    seed_concepts' supersede/delete passes are scoped to its own
    seed-owned rows (the seed:nic2008/% carve-out) and its scheme-table
    reinsert is scoped to its roster — an unscoped wipe would dangle the
    2k+ nic2008 concepts.
    """

    def test_seed_concepts_never_supersedes_nic2008(self, tmp_path):
        from helpers.core.db import connect
        from helpers.misc import seed_concepts as sc

        conn = connect(tmp_path / "co.db")
        try:
            sn.converge(conn, apply=True)
            for _ in range(2):  # idempotent on both sides
                rep = sc.seed(conn, apply=True)
                assert rep["superseded_concepts"] == 0
                n = conn.execute(
                    "SELECT COUNT(*) FROM concepts WHERE scheme_id='nic2008' AND status='active'"
                ).fetchone()[0]
                assert n == 2067
                scheme = conn.execute(
                    "SELECT 1 FROM concept_schemes WHERE scheme_id='nic2008'"
                ).fetchone()
                assert scheme is not None
        finally:
            conn.close()


class TestCandidates:
    """S3 coding lane: deterministic lexical suggestions -> candidate rows."""

    def test_scorer_orders_known_affinity(self):
        tops = sn.suggest_for_label("Software Infrastructure", SEED["subclasses"])
        assert tops, "expected suggestions"
        codes = [c for c, _s, _d in tops]
        assert "62091" in codes
        scores = [s for _c, s, _d in tops]
        assert scores == sorted(scores, reverse=True)

    def test_scorer_zero_signal_returns_empty(self):
        assert sn.suggest_for_label("Zzz Qqq Vvv", SEED["subclasses"]) == []

    def test_scorer_deterministic(self):
        a = sn.suggest_for_label("Airlines", SEED["subclasses"])
        b = sn.suggest_for_label("Airlines", SEED["subclasses"])
        assert a == b
        # prefix morphology: air-family codes surface, not filler
        assert any(c.startswith("51") for c, _s, _d in a)

    def _fixture(self, tmp_path, labels):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s3.db")
        conn.execute("CREATE TABLE hyper_edges (edge_type TEXT, label TEXT, member TEXT)")
        for lab, members in labels:
            for m in members:
                conn.execute("INSERT INTO hyper_edges VALUES ('industry', ?, ?)", (lab, m))
        conn.commit()
        return conn

    def test_candidates_land_and_are_idempotent(self, tmp_path):
        conn = self._fixture(
            tmp_path, [("Software Infrastructure", ["A", "B"]), ("Airlines", ["C"])]
        )
        try:
            sn.converge(conn, apply=True)
            rep = sn.converge_candidates(conn, apply=True)
            assert rep["labels"] == 2 and rep["inserted"] == 6
            rows = conn.execute(
                "SELECT source_scheme, target_scheme, match_type, source_ref,"
                " version, status FROM concept_mappings WHERE target_scheme='nic2008'"
            ).fetchall()
            assert len(rows) == 6
            assert all(
                tuple(r)[:5]
                == ("industry", "nic2008", "closeMatch", sn.CANDIDATE_SOURCE_REF, "NIC-2008")
                for r in rows
            )
            assert all(r[5] == "candidate" for r in rows)
            for _ in range(2):  # idempotent, twice
                rep2 = sn.converge_candidates(conn, apply=True)
                assert (rep2["inserted"], rep2["superseded"]) == (0, 0)
        finally:
            conn.close()

    def test_promoted_label_skipped_and_lapsed_superseded(self, tmp_path):
        conn = self._fixture(tmp_path, [("Software Infrastructure", ["A"]), ("Airlines", ["C"])])
        try:
            sn.converge(conn, apply=True)
            sn.converge_candidates(conn, apply=True)
            # operator promotes one mapping for the label
            conn.execute(
                "UPDATE concept_mappings SET status='active' WHERE source_concept='Airlines'"
                " AND target_concept=(SELECT target_concept FROM concept_mappings"
                " WHERE source_concept='Airlines' LIMIT 1)"
            )
            conn.commit()
            rep = sn.converge_candidates(conn, apply=True)
            assert rep["labels_promoted"] == 1
            # Airlines' leftover candidates supersede; label never re-suggested
            assert rep["suggested_pairs"] == 3  # only Software Infrastructure
            assert rep["superseded"] == 2  # Airlines' remaining candidates
            active_or_cand = conn.execute(
                "SELECT COUNT(*) FROM concept_mappings WHERE source_concept='Airlines'"
                " AND status IN ('active','candidate')"
            ).fetchone()[0]
            assert active_or_cand == 1
        finally:
            conn.close()

    def test_real_promote_lane_interop(self, tmp_path):
        from helpers.misc.seed_concepts import promote

        conn = self._fixture(tmp_path, [("Airlines", ["C"])])
        try:
            sn.converge(conn, apply=True)
            sn.converge_candidates(conn, apply=True)
            target = conn.execute(
                "SELECT target_concept FROM concept_mappings"
                " WHERE source_concept='Airlines' AND status='candidate'"
                " ORDER BY target_concept LIMIT 1"
            ).fetchone()[0]
            # candidate rows are closeMatch — the spec names it (the S4
            # worklist prints this exact command shape)
            applied, errors = promote(conn, [], [f"industry:Airlines->nic2008:{target}:closeMatch"])
            assert not errors and applied
            # next candidates run: label promoted, leftover candidates retire,
            # the PROMOTED row keeps its state whatever its source_ref
            rep = sn.converge_candidates(conn, apply=True)
            assert rep["labels_promoted"] == 1
            assert rep["superseded"] == 2
            states = conn.execute(
                "SELECT status FROM concept_mappings WHERE source_concept='Airlines'"
                " ORDER BY status"
            ).fetchall()
            assert sorted(s[0] for s in states) == ["active", "superseded", "superseded"]
        finally:
            conn.close()


class TestWorklist:
    """S4: the operator coding worklist export."""

    def _fixture(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "s4.db")
        conn.execute(
            "CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " edge_type TEXT, label TEXT)"
        )
        conn.execute("CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT)")
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, cin_nic5 TEXT, cin_year TEXT)")
        rows = [
            ("Advertising Agencies", "Signpost India", "74110", "2012"),
            ("Airlines", "FlyHigh", "61100", "1997"),  # pre-2008 legacy vintage
            ("Zzz Qqq", "Odd Corp", None, None),
        ]
        for label, name, nic5, yr in rows:
            conn.execute(
                "INSERT INTO hyper_edges (edge_type, label) VALUES ('industry', ?)", (label,)
            )
            eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO hyper_incidences VALUES (?, ?)", (eid, name))
            conn.execute("INSERT INTO entities VALUES (?, ?, ?)", (name, nic5, yr))
        conn.commit()
        return conn

    def test_worklist_sections_and_promote_command(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            out = tmp_path / "wl.json"
            wl = sn.export_worklist(conn, out_path=out, journal_path=tmp_path / "journal.jsonl")
            assert wl["counts"] == {
                "labels": 3,
                "suggested": 2,
                "skipped": 0,
                "no_signal": 1,
                "promoted": 0,
            }
            adv = next(x for x in wl["suggested"] if x["label"] == "Advertising Agencies")
            assert adv["members"] == 1
            top = adv["suggestions"][0]
            assert top["code"] == "73100" and top["description"] == "Advertising"
            assert (
                top["promote"] == "helpers/misc/seed_concepts.py --promote-map"
                " 'industry:Advertising Agencies->nic2008:73100:closeMatch' --apply"
            )
            # CIN second signal joined, vintage split
            cin = adv["cin_codes"]["74110"]
            assert cin == {
                "count": 1,
                "companies": ["Signpost India"],
                "nic2008": False,
                "vintage_counts": {"post-2008": 1, "pre-2008": 0},
            }
            air = next(x for x in wl["suggested"] if x["label"] == "Airlines")
            assert air["cin_codes"]["61100"]["vintage_counts"] == {"post-2008": 0, "pre-2008": 1}
            assert air["cin_codes"]["61100"]["nic2008"] is False
            assert [x["label"] for x in wl["no_signal"]] == ["Zzz Qqq"]
            assert json.loads(out.read_text())["counts"] == wl["counts"]
        finally:
            conn.close()

    def test_worklist_marks_promoted_done(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            sn.converge(conn, apply=True)
            sn.converge_candidates(conn, apply=True)
            conn.execute(
                "UPDATE concept_mappings SET status='active'"
                " WHERE source_concept='Airlines' AND status='candidate'"
                " AND target_concept=(SELECT MIN(target_concept) FROM concept_mappings"
                " WHERE source_concept='Airlines')"
            )
            conn.commit()
            wl = sn.export_worklist(conn, out_path=None)
            assert wl["counts"]["promoted"] == 1
            assert wl["counts"]["suggested"] == 1
            assert [d["label"] for d in wl["promoted"]] == ["Airlines"]
        finally:
            conn.close()


class TestReview:
    """S3 review tool: keypress decisions -> batched promote, journaled."""

    def _fixture(self, tmp_path):
        from helpers.core.db import connect

        conn = connect(tmp_path / "rev.db")
        conn.execute(
            "CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " edge_type TEXT, label TEXT)"
        )
        conn.execute("CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT)")
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, cin_nic5 TEXT, cin_year TEXT)")
        conn.execute("INSERT INTO hyper_edges (edge_type, label) VALUES ('industry', 'Airlines')")
        conn.execute(
            "INSERT INTO hyper_incidences VALUES"
            " ((SELECT id FROM hyper_edges WHERE label='Airlines'), 'FlyHigh')"
        )
        conn.execute("INSERT INTO entities VALUES ('FlyHigh', '61100', '1997')")
        conn.commit()
        sn.converge(conn, apply=True)
        sn.converge_candidates(conn, apply=True)
        return conn

    def test_review_applies_and_journals(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            answers = iter(["1", "y"])
            out = []
            rep = sn.review(
                conn, input_fn=lambda _p: next(answers), print_fn=out.append, journal_dir=tmp_path
            )
            assert rep["approved"] == 1 and rep["applied"] == 1 and not rep["errors"]
            active = conn.execute(
                "SELECT target_concept FROM concept_mappings"
                " WHERE source_concept='Airlines' AND status='active'"
            ).fetchall()
            assert len(active) == 1
            journal = Path(rep["journal"]).read_text()
            assert '"action": "approve"' in journal and "Airlines" in journal
            assert any("batch to promote" in ln for ln in out)
        finally:
            conn.close()

    def test_review_decline_applies_nothing(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            answers = iter(["1", "n"])
            rep = sn.review(
                conn,
                input_fn=lambda _p: next(answers),
                print_fn=lambda *_: None,
                journal_dir=tmp_path,
            )
            assert rep["approved"] == 1 and rep["applied"] == 0
            n = conn.execute(
                "SELECT COUNT(*) FROM concept_mappings WHERE status='active'"
            ).fetchone()[0]
            assert n == 0
        finally:
            conn.close()

    def test_review_skip_quit_abort(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            rep = sn.review(
                conn,
                input_fn=lambda _p: next(iter(["s"])),
                print_fn=lambda *_: None,
                limit=1,
                journal_dir=tmp_path,
            )
            assert rep["approved"] == 0 and rep["applied"] == 0
            rep2 = sn.review(
                conn,
                input_fn=lambda _p: next(iter(["q"])),
                print_fn=lambda *_: None,
                limit=1,
                journal_dir=tmp_path,
            )
            assert rep2["approved"] == 0
            rep3 = sn.review(
                conn,
                input_fn=lambda _p: next(iter(["x"])),
                print_fn=lambda *_: None,
                limit=1,
                journal_dir=tmp_path,
            )
            assert rep3["approved"] == 0
        finally:
            conn.close()

    def test_review_override_validates_code(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            answers = iter(["c 99999", "c 62099", "y"])
            rep = sn.review(
                conn,
                input_fn=lambda _p: next(answers),
                print_fn=lambda *_: None,
                journal_dir=tmp_path,
            )
            # invalid override rejected, valid one applied
            assert rep["approved"] == 1 and rep["applied"] == 1
        finally:
            conn.close()

    def test_review_labels_filter(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            rep = sn.review(
                conn,
                labels_filter={"Airlines"},
                input_fn=lambda _p: next(iter(["1"])),
                print_fn=lambda *_: None,
                apply=False,
                journal_dir=tmp_path,
            )
            assert rep["approved"] == 1
        finally:
            conn.close()

    def test_worklist_parks_journaled_skips(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            sn.converge(conn, apply=True)
            sn.converge_candidates(conn, apply=True)
            jp = tmp_path / "journal.jsonl"
            wl = sn.export_worklist(conn, out_path=None, journal_path=jp)
            target = wl["suggested"][0]["label"]  # fixture: single suggested label
            jp.write_text(json.dumps({"label": target, "action": "skip"}) + "\n")
            wl2 = sn.export_worklist(conn, out_path=None, journal_path=jp)
            assert target not in [e["label"] for e in wl2["suggested"]]
            assert target in [e["label"] for e in wl2["skipped"]]
            # default review walk avoids it; --skipped lane shows it
            seen = []
            out = []

            def inp(prompt):
                seen.append(prompt)
                return "q"

            rep = sn.review(conn, input_fn=inp, print_fn=lambda *_: None, journal_dir=jp.parent)
            # parked label is out of the default walk: nothing prompted, nothing decided
            assert seen == []
            assert rep["decisions"] == 0
            rep2 = sn.review(
                conn,
                input_fn=inp,
                print_fn=lambda *_a: out.append(_a),
                journal_dir=jp.parent,
                skipped=True,
            )
            # --skipped lane walks the parked label
            assert target in "".join(str(a) for a in out)
            assert rep2["decisions"] == 1
        finally:
            conn.close()

    def test_review_match_type_stacking(self, tmp_path):
        conn = self._fixture(tmp_path)
        try:
            # primary closeMatch first (51101 = scheduled air transport)
            answers = iter(["c 51101", "y"])
            sn.review(
                conn,
                input_fn=lambda _p: next(answers),
                print_fn=lambda *_: None,
                journal_dir=tmp_path,
            )
            # stack a narrowMatch lane on the SAME label — promoted labels
            # come back via explicit --labels (the stacking flow)
            answers = iter(["c 51201:narrowMatch", "y"])
            rep = sn.review(
                conn,
                input_fn=lambda _p: next(answers),
                print_fn=lambda *_: None,
                journal_dir=tmp_path,
                labels_filter={"Airlines"},
            )
            assert rep["applied"] == 1
            lanes = [
                tuple(r)
                for r in conn.execute(
                    "SELECT match_type FROM concept_mappings"
                    " WHERE source_concept='Airlines' AND status='active'"
                )
            ]
            assert ("closeMatch",) in lanes
            assert ("narrowMatch",) in lanes
            # re-deciding the narrowMatch lane must NOT touch closeMatch
            answers = iter(["c 51201:narrowMatch", "y"])
            sn.review(
                conn,
                input_fn=lambda _p: next(answers),
                print_fn=lambda *_: None,
                journal_dir=tmp_path,
                labels_filter={"Airlines"},
            )
            lanes = sorted(
                tuple(r)
                for r in conn.execute(
                    "SELECT match_type FROM concept_mappings"
                    " WHERE source_concept='Airlines' AND status='active'"
                )
            )
            assert lanes == [("closeMatch",), ("narrowMatch",)]
        finally:
            conn.close()


class TestStamp:
    """S3 per-company industry_code stamping (industry_coding_completion)."""

    def _conn_with_members(self, tmp_path, members):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY, edge_type TEXT, label TEXT)"
        )
        conn.execute("CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT)")
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, file_path TEXT)")
        conn.execute(
            "INSERT INTO hyper_edges (edge_type, label) VALUES ('industry', 'Conglomerates')"
        )
        for name in members:
            note = tmp_path / f"{name.replace(' ', '_')}.md"
            note.write_text(
                "---\n"
                "title: " + name + "\n"
                "type: company\n"
                "sector: Industrials\n"
                "industry: Conglomerates\n"
                "---\n"
                "# " + name + "\n",
                encoding="utf-8",
            )
            conn.execute("INSERT INTO hyper_incidences VALUES (1, ?)", (name,))
            conn.execute("INSERT INTO entities VALUES (?, ?)", (name, str(note)))
        conn.commit()
        return conn

    def _map(self, tmp_path, codes):
        p = tmp_path / "map.json"
        p.write_text(
            json.dumps({"flagged_labels": ["Conglomerates"], "codes": codes}), encoding="utf-8"
        )
        return p

    def test_stamp_dry_then_apply_then_idempotent(self, tmp_path):
        conn = self._conn_with_members(tmp_path, ["BEML", "SRF"])
        mp = self._map(tmp_path, {"BEML": {"code": "35202", "via": "cin"}})
        rep = sn.stamp_company_codes(conn, apply=False, map_path=mp)
        assert rep["stamped"] == ["BEML"]  # would stamp
        note = tmp_path / "BEML.md"
        assert "industry_code" not in note.read_text()  # dry-run wrote nothing
        rep2 = sn.stamp_company_codes(conn, apply=True, map_path=mp)
        assert rep2["stamped"] == ["BEML"]
        text = note.read_text()
        assert 'industry_code: "35202"' in text
        # sibling position: directly after industry:
        lines = text.splitlines()
        assert lines[lines.index("industry: Conglomerates") + 1] == 'industry_code: "35202"'
        rep3 = sn.stamp_company_codes(conn, apply=True, map_path=mp)
        assert rep3["stamped"] == [] and rep3["unchanged"] == ["BEML"]  # idempotent
        assert rep["pending_attestation"] == [["Conglomerates", "SRF"]]

    def test_stamp_refuses_out_of_vocabulary_code(self, tmp_path):
        conn = self._conn_with_members(tmp_path, ["Acme Ltd"])
        mp = self._map(tmp_path, {"Acme Ltd": {"code": "99999", "via": "op"}})
        rep = sn.stamp_company_codes(conn, apply=True, map_path=mp)
        assert rep["stamped"] == [] and rep["skipped"] == [["Conglomerates", "Acme Ltd", "99999"]]
        assert "industry_code" not in (tmp_path / "Acme_Ltd.md").read_text()

    def test_stamp_updates_diverged_field(self, tmp_path):
        conn = self._conn_with_members(tmp_path, ["BEML"])
        note = tmp_path / "BEML.md"
        note.write_text(
            note.read_text().replace(
                "industry: Conglomerates", "industry: Conglomerates\nindustry_code: 11111"
            )
        )
        mp = self._map(tmp_path, {"BEML": {"code": "35202", "via": "cin"}})
        rep = sn.stamp_company_codes(conn, apply=True, map_path=mp)
        assert rep["stamped"] == ["BEML"]
        assert 'industry_code: "35202"' in note.read_text()
        assert "11111" not in note.read_text()
