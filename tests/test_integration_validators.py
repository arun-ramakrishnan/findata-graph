#!/usr/bin/env python3
"""P8 — parse_newsletter → validators round-trip integration tests.

Verifies that entities created by parse_newsletter pass NotesValidator
checks, and that malformed entities are caught.

See doc/improvements/archive/testing/integration_plan.md § Nice-to-have 5.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.schema import EDGES_12COL, ENTITIES_8COL, ENTITY_TAGS, HYPER_STORE  # noqa: E402

pytestmark = [pytest.mark.integration]

_SCHEMA = "".join([ENTITIES_8COL, ENTITY_TAGS, EDGES_12COL, HYPER_STORE])


@pytest.fixture
def p8_env(tmp_path, monkeypatch):
    """Create a synthetic findata tree + DB, patch PROJECT_ROOT."""
    findata = tmp_path / "findata"
    companies = findata / "Companies"
    sectors = findata / "Sectors"
    for s in ("Banking", "Technology"):
        (companies / s).mkdir(parents=True)
    sectors.mkdir(parents=True)

    # DB
    db_path = str(tmp_path / "p8_val.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Patch parse_newsletter's PROJECT_ROOT so create_entity writes to tmp
    import helpers.core.parse_newsletter as pn

    monkeypatch.setattr(pn, "PROJECT_ROOT", tmp_path)
    # NotesVerifier takes project_root as a constructor arg — no patch needed.

    yield tmp_path, findata, conn, db_path
    conn.close()


class TestParseNewsletterValidatesClean:
    """Entities created by parse_newsletter pass the validator."""

    def test_clean_entity_note_validates(self, p8_env):
        from helpers.core.parse_newsletter import render_stub
        from helpers.validators.verify_notes import NotesVerifier

        tmp, findata, conn, db_path = p8_env

        # Create a clean entity
        normalized = "HDFC_Bank"
        file_path = f"findata/Companies/Banking/{normalized}.md"
        note = render_stub(
            "HDFC Bank",
            normalized,
            "Banking",
            "HDFCBANK",
            f"/companies/banking/{normalized.lower()}",
        )
        (tmp / file_path).parent.mkdir(parents=True, exist_ok=True)
        (tmp / file_path).write_text(note)

        # Run validator on the company note
        nv = NotesVerifier(project_root=tmp)
        nv.process_directory(tmp / "findata" / "Companies" / "Banking", "company")
        # The fresh stub IS minimal by design (S4 removed the chatter
        # block): content_minimal is the expected finding — the enhancer's
        # cue. Any OTHER issue would be a real defect.
        issues_for_file = [
            issue
            for bucket_issues in nv.issues.values()
            for issue in bucket_issues
            if normalized in str(issue)
        ]
        unexpected = [i for i in issues_for_file if "Minimal meaningful content" not in str(i)]
        assert unexpected == [], f"Unexpected issues: {unexpected}"

    def test_multiple_clean_entities_validate(self, p8_env):
        from helpers.core.parse_newsletter import render_stub
        from helpers.validators.verify_notes import NotesVerifier

        tmp, findata, conn, db_path = p8_env
        # Create several stubs
        entities = [
            ("HDFC Bank", "HDFC_Bank", "Banking", "HDFCBANK"),
            ("ICICI Bank", "ICICI_Bank", "Banking", "ICICIBANK"),
            ("Infosys", "Infosys", "Technology", "INFY"),
        ]
        for name, norm, sector, ticker in entities:
            fp = tmp / "findata" / "Companies" / sector / f"{norm}.md"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                render_stub(
                    name, norm, sector, ticker, f"/companies/{sector.lower()}/{norm.lower()}"
                )
            )

        nv = NotesVerifier(project_root=tmp)
        nv.verify_all()
        # Fresh stubs ARE minimal by design (S4 removed the chatter block):
        # the verifier WARNS for machine-born stubs (generated stamp) — the
        # enhancer's cue, not a corpus defect. Any ERROR issue would be a
        # real defect.
        unexpected = [
            i
            for bucket in nv.issues.values()
            for i in bucket
            if "Minimal meaningful content" not in str(i)
        ]
        assert unexpected == [], f"Unexpected issues: {unexpected}"
        warned = sum(len(v) for v in getattr(nv, "warnings", {}).values())
        assert warned >= len(entities)  # one awaiting-enrichment cue per stub


class TestValidatorCatchesProblems:
    """Malformed entities created by bad paths are caught by the validator."""

    def test_bad_filename_caught(self, p8_env):
        from helpers.validators.verify_notes import NotesVerifier

        tmp, findata, conn, db_path = p8_env
        # Write a file with a bad name (lowercase, not PascalCase)
        bad_dir = tmp / "findata" / "Companies" / "Banking"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "lowercase_name.md").write_text(
            "---\ntitle: Test\ntype: company\n---\n\n# Test\n"
        )
        nv = NotesVerifier(project_root=tmp)
        nv.process_directory(bad_dir, "company")
        # Should flag the filename
        total_issues = sum(len(v) for v in nv.issues.values())
        assert total_issues > 0

    def test_missing_normalized_name_caught(self, p8_env):
        from helpers.validators.verify_notes import NotesVerifier

        tmp, findata, conn, db_path = p8_env
        bad_dir = tmp / "findata" / "Companies" / "Banking"
        bad_dir.mkdir(parents=True, exist_ok=True)
        # Write a note missing normalized_name
        (bad_dir / "Test_Company.md").write_text(
            "---\ntitle: Test Company\ntype: company\n---\n\n# Test Company\n"
        )
        nv = NotesVerifier(project_root=tmp)
        nv.process_directory(bad_dir, "company")
        total_issues = sum(len(v) for v in nv.issues.values())
        assert total_issues > 0

    def test_name_mismatch_caught(self, p8_env):
        from helpers.validators.verify_notes import NotesVerifier

        tmp, findata, conn, db_path = p8_env
        bad_dir = tmp / "findata" / "Companies" / "Banking"
        bad_dir.mkdir(parents=True, exist_ok=True)
        # normalized_name doesn't match filename
        (bad_dir / "Good_Bank.md").write_text(
            "---\ntitle: Good Bank\ntype: company\nnormalized_name: Bad_Bank\n---\n\n# Good Bank\n"
        )
        nv = NotesVerifier(project_root=tmp)
        nv.process_directory(bad_dir, "company")
        total_issues = sum(len(v) for v in nv.issues.values())
        assert total_issues > 0

    def test_consecutive_underscores_caught(self, p8_env):
        from helpers.validators.verify_notes import NotesVerifier

        tmp, findata, conn, db_path = p8_env
        bad_dir = tmp / "findata" / "Companies" / "Banking"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "Bad__Name.md").write_text(
            "---\ntitle: Bad Name\ntype: company\nnormalized_name: Bad__Name\n---\n\n# Bad Name\n"
        )
        nv = NotesVerifier(project_root=tmp)
        nv.process_directory(bad_dir, "company")
        total_issues = sum(len(v) for v in nv.issues.values())
        assert total_issues > 0


class TestParseNewsletterDbConsistency:
    """create_entity inserts the right DB row matching the note on disk."""

    def test_create_entity_writes_row_and_note(self, p8_env):
        from helpers.core.parse_newsletter import create_entity, get_sector_entities

        tmp, findata, conn, db_path = p8_env

        # Seed a sector entity so belongs_to edges can be created
        conn.execute("INSERT INTO entities(name, entity_type) VALUES ('Banking', 'sector')")
        conn.commit()

        sector_entities = get_sector_entities(conn)
        normalized, file_path = create_entity(
            conn,
            "HDFC Bank",
            "Banking",
            "HDFCBANK",
            apply=True,
            sector_entities=sector_entities,
        )
        conn.commit()

        # DB row exists
        row = conn.execute(
            "SELECT name, entity_type, normalized_name, sector_classification, ticker FROM entities WHERE name=?",
            ("HDFC Bank",),
        ).fetchone()
        assert row is not None
        assert row["entity_type"] == "company"
        assert row["normalized_name"] == "HDFC_Bank"
        assert row["sector_classification"] == "Banking"
        assert row["ticker"] == "HDFCBANK"

        # Note file on disk
        assert (tmp / file_path).exists()

        # Sector membership is canonical in the incidence store (2026-10-19
        # part_of/has_company retirement); no dyads are written.
        members = conn.execute(
            "SELECT he.label FROM hyper_incidences hi "
            "JOIN hyper_edges he ON he.id = hi.edge_id "
            "WHERE hi.entity_name='HDFC Bank' AND he.edge_type='sector'"
        ).fetchall()
        assert [m["label"] for m in members] == ["Banking"]
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM graph_edges WHERE edge_type IN ('part_of','has_company')"
            ).fetchone()["n"]
            == 0
        )

    def test_create_entity_idempotent(self, p8_env):
        from helpers.core.parse_newsletter import create_entity, get_sector_entities

        tmp, findata, conn, db_path = p8_env
        conn.execute("INSERT INTO entities(name, entity_type) VALUES ('Banking', 'sector')")
        conn.commit()
        sector_entities = get_sector_entities(conn)

        create_entity(
            conn, "ICICI Bank", "Banking", "ICICIBANK", apply=True, sector_entities=sector_entities
        )
        conn.commit()

        # Second call should be a no-op
        create_entity(
            conn, "ICICI Bank", "Banking", "ICICIBANK", apply=True, sector_entities=sector_entities
        )
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) FROM entities WHERE name='ICICI Bank'").fetchone()[0]
        assert rows == 1
