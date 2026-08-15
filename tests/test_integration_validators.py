#!/usr/bin/env python3
"""P8 — parse_newsletter → validators round-trip integration tests.

Verifies that entities created by parse_newsletter pass NotesValidator
checks, and that malformed entities are caught.

See doc/improvements/archive/integration_plan.txt § Nice-to-have 5.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = [pytest.mark.integration]

_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path TEXT,
    last_updated DATETIME,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
);
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""


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
        note = render_stub("HDFC Bank", normalized, "Banking", "HDFCBANK",
                           f"/companies/banking/{normalized.lower()}")
        (tmp / file_path).parent.mkdir(parents=True, exist_ok=True)
        (tmp / file_path).write_text(note)

        # Run validator on the company note
        nv = NotesVerifier(project_root=tmp)
        nv.process_directory(
            tmp / "findata" / "Companies" / "Banking", "company"
        )
        # Should have 0 issues for this file
        issues_for_file = [
            issue for bucket_issues in nv.issues.values()
            for issue in bucket_issues
            if normalized in str(issue)
        ]
        assert len(issues_for_file) == 0, f"Unexpected issues: {issues_for_file}"

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
            fp.write_text(render_stub(name, norm, sector, ticker,
                                      f"/companies/{sector.lower()}/{norm.lower()}"))

        nv = NotesVerifier(project_root=tmp)
        nv.verify_all()
        # No issues at all
        total_issues = sum(len(v) for v in nv.issues.values())
        assert total_issues == 0, f"Expected 0 issues, got {total_issues}"


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
        conn.execute(
            "INSERT INTO entities(name, entity_type) VALUES ('Banking', 'sector')"
        )
        conn.commit()

        sector_entities = get_sector_entities(conn)
        normalized, file_path = create_entity(
            conn, "HDFC Bank", "Banking", "HDFCBANK", apply=True,
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

        # Bidirectional edges
        edges = conn.execute(
            "SELECT edge_type FROM graph_edges WHERE source='HDFC Bank' OR target='HDFC Bank'"
        ).fetchall()
        edge_types = [e["edge_type"] for e in edges]
        assert "part_of" in edge_types
        assert "has_company" in edge_types

    def test_create_entity_idempotent(self, p8_env):
        from helpers.core.parse_newsletter import create_entity, get_sector_entities

        tmp, findata, conn, db_path = p8_env
        conn.execute(
            "INSERT INTO entities(name, entity_type) VALUES ('Banking', 'sector')"
        )
        conn.commit()
        sector_entities = get_sector_entities(conn)

        create_entity(conn, "ICICI Bank", "Banking", "ICICIBANK", apply=True,
                      sector_entities=sector_entities)
        conn.commit()

        # Second call should be a no-op
        create_entity(conn, "ICICI Bank", "Banking", "ICICIBANK", apply=True,
                      sector_entities=sector_entities)
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) FROM entities WHERE name='ICICI Bank'").fetchone()[0]
        assert rows == 1
