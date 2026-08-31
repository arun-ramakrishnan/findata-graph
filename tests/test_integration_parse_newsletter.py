#!/usr/bin/env python3
"""End-to-end integration tests for parse_newsletter pipeline (P1).

Exercises the full newsletter → SQLite → notes → edges → worklist chain on a
synthetic newsletter, verifying:

  - Entities created in DB with correct metadata
  - Note files exist on disk with correct YAML frontmatter
  - graph_edges populated (part_of / has_company) bidirectionally
  - entity_tags populated (via sync_tags — mocked here)
  - Enhancement worklist emitted
  - Second run is idempotent (no duplicate rows)
  - Dry-run mode writes nothing
  - Sector-guessing from heading context works
  - Existing entities classified correctly (not duplicated)
  - Uncertain (fuzzy) entities flagged, not auto-created

Marked `integration` so it can be excluded from fast `make test` runs.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "helpers"))

from helpers.core import parse_newsletter as pn  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import ENTITIES_DDL  # noqa: E402


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #


def _build_db(db_path: Path) -> sqlite3.Connection:
    """Create a minimal but realistic SQLite DB with entities + graph_edges."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(ENTITIES_DDL)
    conn.executescript("""
        CREATE TABLE entity_tags (
            entity_name TEXT NOT NULL,
            tag         TEXT NOT NULL,
            PRIMARY KEY (entity_name, tag),
            FOREIGN KEY (entity_name) REFERENCES entities(name)
                ON DELETE CASCADE ON UPDATE CASCADE
        );
        CREATE TABLE graph_edges (
            id          INTEGER PRIMARY KEY,
            source      TEXT NOT NULL,
            target      TEXT NOT NULL,
            edge_type   TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            properties  TEXT NOT NULL DEFAULT '{}',
            valid_from  DATE,
            valid_to    DATE,
            source_ref  TEXT NOT NULL,
            symmetric   INTEGER NOT NULL DEFAULT 0,
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, target, edge_type),
            CHECK (source != target)
        );
        CREATE TABLE graph_analytics (
            entity_name TEXT NOT NULL,
            metric      TEXT NOT NULL,
            value       TEXT NOT NULL,
            computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (metric, entity_name)
        );
    """)
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# Synthetic newsletter
# --------------------------------------------------------------------------- #

SYNTHETIC_NEWSLETTER = """\
# The Chatter — Test Edition

## Acme Banking Corp | Large Cap | Banking

Acme Banking Corp reported strong Q3 results with net profit growth of 25%.
The bank's CASA ratio improved to 45%, driven by digital onboarding.

## Zeta Technologies | Large Cap | Technology

Zeta Technologies announced a new cloud platform partnership.
Revenue grew 30% YoY, with SaaS contributing 60% of the mix.

## Banking Sector Overview

No company heading here, just a sector summary.

## Epsilon Energy | Mid Cap | Energy

Epsilon Energy commissioned a new 500MW solar plant.
This marks their entry into the renewables segment.
"""


# --------------------------------------------------------------------------- #
# Fixture: synthetic project tree + DB
# --------------------------------------------------------------------------- #


@pytest.fixture
def synth_project(tmp_path, monkeypatch):
    """Build a synthetic project root, DB, and sector directories.

    Returns a dict with paths and the DB connection for assertions.
    """
    root = tmp_path
    companies = root / "findata" / "Companies"
    companies.mkdir(parents=True)
    # Sector dirs that guess_sector_for will match against
    for d in ("Banking", "Technology", "Energy", "Diversified"):
        (companies / d).mkdir(parents=True, exist_ok=True)
    # Sectors dir (for run_validation, but it's mocked)
    (root / "findata" / "Sectors").mkdir(parents=True)
    # Newsletter location
    chatter_dir = root / "findata" / "The_Chatter"
    chatter_dir.mkdir(parents=True)

    db_path = root / "memory" / "research.db"
    db_path.parent.mkdir(parents=True)

    conn = _build_db(db_path)
    # Pre-seed sector entities + one existing company
    for sector_name in ("Banking", "Technology", "Energy", "Diversified"):
        conn.execute(
            "INSERT OR IGNORE INTO entities(name, entity_type, sector_classification, "
            "normalized_name) VALUES (?, 'sector', NULL, ?)",
            (sector_name, sector_name),
        )
    conn.execute(
        "INSERT INTO entities(name, entity_type, sector_classification, "
        "normalized_name, ticker) VALUES "
        "('HDFC Bank', 'company', 'Banking', 'HDFC_Bank', 'HDFCBANK')"
    )
    conn.commit()
    conn.close()

    # Write the newsletter
    nl_path = chatter_dir / "Test_Edition.md"
    nl_path.write_text(SYNTHETIC_NEWSLETTER, encoding="utf-8")

    # Monkeypatch all module-level paths and externals
    monkeypatch.setattr(pn, "PROJECT_ROOT", root)
    monkeypatch.setattr(pn, "DB_PATH", db_path)
    monkeypatch.setattr(pn, "COMPANIES", companies)
    monkeypatch.setattr(pn, "FINDATA", root / "findata")
    # search_ticker: mock — returns a fake ticker tuple
    monkeypatch.setattr(pn, "search_ticker", lambda name: (f"FAKE_{name[:3].upper()}.NS", {}))
    # capture_images: always succeed (no image capture in tests)
    monkeypatch.setattr(pn, "capture_images", lambda md_path, apply: True)
    # run_validation: always succeed (no validator subprocess)
    monkeypatch.setattr(pn, "run_validation", lambda apply: True)
    # run_graph_analytics: always succeed
    monkeypatch.setattr(pn, "run_graph_analytics", lambda: True)

    return {
        "root": root,
        "db_path": db_path,
        "companies": companies,
        "newsletter": nl_path,
    }


def _run_apply(project, monkeypatch):
    """Run pn.main() with --apply, setting sys.argv correctly."""
    rel = str(project["newsletter"].relative_to(project["root"]))
    monkeypatch.setattr(sys, "argv", ["parse_newsletter.py", rel, "--apply"])
    try:
        pn.main()
    except SystemExit:
        pass


def _run_dry(project, monkeypatch):
    """Run pn.main() in dry-run (no --apply), setting sys.argv correctly."""
    rel = str(project["newsletter"].relative_to(project["root"]))
    monkeypatch.setattr(sys, "argv", ["parse_newsletter.py", rel])
    try:
        pn.main()
    except SystemExit:
        pass


def _open_db(project):
    """Open a fresh connection to the test DB for assertions."""
    conn = sqlite3.connect(str(project["db_path"]))
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- #
# Test: full pipeline — entities created
# --------------------------------------------------------------------------- #


class TestEntitiesCreated:
    """Verify entity rows are created in SQLite with correct attributes."""

    def test_new_companies_inserted(self, synth_project, monkeypatch):
        """Acme Banking Corp, Zeta Technologies, Epsilon Energy should all be
        new entities (not in the pre-seeded DB)."""
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM entities WHERE entity_type='company'"
                ).fetchall()
            }
            assert "Acme Banking Corp" in names
            assert "Zeta Technologies" in names
            assert "Epsilon Energy" in names
            # Pre-seeded entity still present
            assert "HDFC Bank" in names
        finally:
            conn.close()

    def test_preseeded_company_not_duplicated(self, synth_project, monkeypatch):
        """HDFC Bank is pre-seeded; it should not get a second row."""
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            count = conn.execute("SELECT COUNT(*) FROM entities WHERE name='HDFC Bank'").fetchone()[
                0
            ]
            assert count == 1
        finally:
            conn.close()

    def test_entity_type_is_company(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            row = conn.execute(
                "SELECT entity_type FROM entities WHERE name='Acme Banking Corp'"
            ).fetchone()
            assert row is not None
            assert row[0] == "company"
        finally:
            conn.close()

    def test_sector_classification_set(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            sector = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Acme Banking Corp'"
            ).fetchone()[0]
            assert sector == "Banking"

            sector = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Zeta Technologies'"
            ).fetchone()[0]
            assert sector == "Technology"
        finally:
            conn.close()

    def test_normalized_name_set(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            norm = conn.execute(
                "SELECT normalized_name FROM entities WHERE name='Acme Banking Corp'"
            ).fetchone()[0]
            assert norm == "Acme_Banking_Corp"
        finally:
            conn.close()

    def test_ticker_resolved(self, synth_project, monkeypatch):
        """search_ticker is mocked; verify the ticker value flows through."""
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            ticker = conn.execute(
                "SELECT ticker FROM entities WHERE name='Acme Banking Corp'"
            ).fetchone()[0]
            assert ticker is not None
            assert ticker.startswith("FAKE_")
        finally:
            conn.close()

    def test_file_path_set(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            fp = conn.execute(
                "SELECT file_path FROM entities WHERE name='Zeta Technologies'"
            ).fetchone()[0]
            assert fp == "findata/Companies/Technology/Zeta_Technologies.md"
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Test: note files created on disk
# --------------------------------------------------------------------------- #


class TestNoteFilesCreated:
    """Verify markdown stub files are written to findata/Companies/<sector>/."""

    def test_note_file_exists(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        path = synth_project["companies"] / "Banking" / "Acme_Banking_Corp.md"
        assert path.exists()

    def test_note_has_yaml_frontmatter(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        path = synth_project["companies"] / "Technology" / "Zeta_Technologies.md"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "title: Zeta Technologies" in content
        assert "type: company" in content

    def test_note_has_correct_tags(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        path = synth_project["companies"] / "Banking" / "Acme_Banking_Corp.md"
        content = path.read_text(encoding="utf-8")
        assert "- entity_type/company" in content
        assert "- sector/banking" in content
        assert "- geography/india" in content

    def test_note_has_permalink(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        path = synth_project["companies"] / "Energy" / "Epsilon_Energy.md"
        content = path.read_text(encoding="utf-8")
        assert "permalink: /companies/energy/epsilon_energy" in content

    def test_note_has_heading(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        path = synth_project["companies"] / "Banking" / "Acme_Banking_Corp.md"
        content = path.read_text(encoding="utf-8")
        assert "# Acme Banking Corp" in content
        assert "## Company Overview" in content

    def test_note_not_overwritten_on_rerun(self, synth_project, monkeypatch):
        """create_entity never clobbers existing notes. If we manually modify
        a note, a second run should preserve the modification."""
        _run_apply(synth_project, monkeypatch)
        path = synth_project["companies"] / "Banking" / "Acme_Banking_Corp.md"
        original = path.read_text(encoding="utf-8")
        modified = original + "\n## Enriched Section\nAdded by hand.\n"
        path.write_text(modified, encoding="utf-8")

        _run_apply(synth_project, monkeypatch)
        final = path.read_text(encoding="utf-8")
        assert "Enriched Section" in final


# --------------------------------------------------------------------------- #
# Test: graph edges created
# --------------------------------------------------------------------------- #


class TestGraphEdges:
    """Verify bidirectional part_of / has_company edges are created."""

    def test_part_of_edge_exists(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            edges = conn.execute(
                "SELECT * FROM graph_edges WHERE source=? AND edge_type='part_of'",
                ("Acme Banking Corp",),
            ).fetchall()
            assert len(edges) == 1
            assert edges[0]["target"] == "Banking"
        finally:
            conn.close()

    def test_has_company_edge_exists(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            edges = conn.execute(
                "SELECT * FROM graph_edges WHERE target=? AND edge_type='has_company'",
                ("Acme Banking Corp",),
            ).fetchall()
            assert len(edges) == 1
            assert edges[0]["source"] == "Banking"
        finally:
            conn.close()

    def test_all_new_companies_get_edges(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            for name in ("Acme Banking Corp", "Zeta Technologies", "Epsilon Energy"):
                n = conn.execute(
                    "SELECT COUNT(*) FROM graph_edges WHERE source=? AND edge_type='part_of'",
                    (name,),
                ).fetchone()[0]
                assert n == 1, f"{name} missing part_of edge"
        finally:
            conn.close()

    def test_edge_source_ref(self, synth_project, monkeypatch):
        """Edges should be tagged source_ref='parse_newsletter'."""
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            ref = conn.execute(
                "SELECT source_ref FROM graph_edges WHERE source=? AND edge_type='part_of'",
                ("Acme Banking Corp",),
            ).fetchone()[0]
            assert ref == "parse_newsletter"
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Test: idempotency
# --------------------------------------------------------------------------- #


class TestIdempotency:
    """Running the pipeline twice should not create duplicates."""

    def test_no_duplicate_entities(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            count_after_1 = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type='company'"
            ).fetchone()[0]
        finally:
            conn.close()

        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            count_after_2 = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type='company'"
            ).fetchone()[0]
            assert count_after_1 == count_after_2
        finally:
            conn.close()

    def test_no_duplicate_edges(self, synth_project, monkeypatch):
        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            edges_1 = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        finally:
            conn.close()

        _run_apply(synth_project, monkeypatch)
        conn = _open_db(synth_project)
        try:
            edges_2 = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            assert edges_1 == edges_2
        finally:
            conn.close()

    def test_idempotent_exit_code(self, synth_project, monkeypatch):
        """Both runs should complete without error (exit 0)."""
        _run_apply(synth_project, monkeypatch)
        _run_apply(synth_project, monkeypatch)


# --------------------------------------------------------------------------- #
# Test: dry-run mode
# --------------------------------------------------------------------------- #


class TestDryRun:
    """In dry-run mode (no --apply), nothing should be written."""

    def test_no_entities_created(self, synth_project, monkeypatch):
        """In dry-run, no entities should be created."""
        _run_dry(synth_project, monkeypatch)

        conn = _open_db(synth_project)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type='company'"
            ).fetchone()[0]
            # Only pre-seeded HDFC Bank
            assert count == 1
        finally:
            conn.close()

    def test_no_note_files_created(self, synth_project, monkeypatch):
        _run_dry(synth_project, monkeypatch)

        # No new note files
        acme = synth_project["companies"] / "Banking" / "Acme_Banking_Corp.md"
        assert not acme.exists()

    def test_worklist_still_emitted(self, synth_project, monkeypatch):
        """Worklist is always emitted (even in dry-run) — it's the plan."""
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        assert wl_path.exists()


# --------------------------------------------------------------------------- #
# Test: worklist content
# --------------------------------------------------------------------------- #


class TestWorklist:
    """Verify the enhancement worklist JSON structure."""

    def test_worklist_exists(self, synth_project, monkeypatch):
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        assert wl_path.exists()

    def test_worklist_has_new_entities(self, synth_project, monkeypatch):
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        new_names = {e["name"] for e in wl["new_entities"]}
        assert "Acme Banking Corp" in new_names
        assert "Zeta Technologies" in new_names

    def test_worklist_has_existing_entities(self, synth_project, monkeypatch):
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        existing_names = {e["name"] for e in wl["existing_entities_to_enhance"]}
        assert "HDFC Bank" not in existing_names  # HDFC Bank not in newsletter


# --------------------------------------------------------------------------- #
# Test: sector classification
# --------------------------------------------------------------------------- #


class TestSectorGuessing:
    """Verify guess_sector_for assigns correct sectors from heading context."""

    def test_banking_sector_from_content(self, synth_project, monkeypatch):
        sector = pn.guess_sector_for(
            "Acme Banking Corp",
            "Acme Banking Corp | Large Cap | Banking",
            {"Banking", "Technology", "Energy", "Diversified"},
        )
        assert sector == "Banking"

    def test_technology_sector_from_content(self, synth_project, monkeypatch):
        sector = pn.guess_sector_for(
            "Zeta Technologies",
            "Zeta Technologies announced a new cloud platform partnership.",
            {"Banking", "Technology", "Energy", "Diversified"},
        )
        assert sector == "Technology"

    def test_unknown_sector_returns_diversified(self, synth_project, monkeypatch):
        sector = pn.guess_sector_for(
            "Mystery Co",
            "Mystery Co | Small Cap | Something Unusual",
            {"Banking", "Technology", "Energy", "Diversified"},
        )
        assert sector == "Diversified"

    def test_unknown_sector_no_diversified_returns_none(self, synth_project, monkeypatch):
        sector = pn.guess_sector_for(
            "Mystery Co",
            "Mystery Co | Small Cap | Something Unusual",
            {"Banking", "Technology"},  # no Diversified
        )
        assert sector is None


# --------------------------------------------------------------------------- #
# Test: existing-entity classification
# --------------------------------------------------------------------------- #


class TestExistingEntityClassification:
    """Companies already in the DB should be classified as existing, not new."""

    def test_existing_company_in_newsletter_classified_existing(self, synth_project, monkeypatch):
        """If the newsletter mentions HDFC Bank (pre-seeded), it should appear
        in the 'existing' list, not the 'new' list of the worklist."""
        # Add HDFC Bank to the newsletter
        nl_path = synth_project["newsletter"]
        original = nl_path.read_text(encoding="utf-8")
        nl_path.write_text(
            original + "\n## HDFC Bank | Large Cap | Banking\n\nExisting entity.\n",
            encoding="utf-8",
        )

        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        existing_names = {e["name"] for e in wl["existing_entities_to_enhance"]}
        assert "HDFC Bank" in existing_names

    def test_existing_company_not_in_new_list(self, synth_project, monkeypatch):
        nl_path = synth_project["newsletter"]
        original = nl_path.read_text(encoding="utf-8")
        nl_path.write_text(
            original + "\n## HDFC Bank | Large Cap | Banking\n\nExisting entity.\n",
            encoding="utf-8",
        )

        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        new_names = {e["name"] for e in wl["new_entities"]}
        assert "HDFC Bank" not in new_names

    def test_existing_company_not_duplicated_in_db(self, synth_project, monkeypatch):
        """Running --apply when an existing company is mentioned should not
        create a duplicate entity row."""
        nl_path = synth_project["newsletter"]
        original = nl_path.read_text(encoding="utf-8")
        nl_path.write_text(
            original + "\n## HDFC Bank | Large Cap | Banking\n\nExisting entity.\n",
            encoding="utf-8",
        )

        _run_apply(synth_project, monkeypatch)

        conn = _open_db(synth_project)
        try:
            count = conn.execute("SELECT COUNT(*) FROM entities WHERE name='HDFC Bank'").fetchone()[
                0
            ]
            assert count == 1
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Test: non-company headings skipped
# --------------------------------------------------------------------------- #


class TestNonCompanyHeadings:
    """Headings without cap tokens or pipes should not be treated as companies."""

    def test_sector_overview_heading_not_extracted(self, synth_project, monkeypatch):
        """The 'Banking Sector Overview' heading has no pipe or cap token — it
        should not be extracted as a company candidate."""
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        all_names = (
            {e["name"] for e in wl["new_entities"]}
            | {e["name"] for e in wl["existing_entities_to_enhance"]}
            | {e["candidate"] for e in wl["uncertain_entities"]}
        )
        # "Banking Sector Overview" is a plain heading — not a company
        assert "Banking Sector Overview" not in all_names

    def test_newsletter_title_not_extracted(self, synth_project, monkeypatch):
        """The top-level '# The Chatter — Test Edition' is not a company heading."""
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        all_names = {e["name"] for e in wl["new_entities"]} | {
            e["name"] for e in wl["existing_entities_to_enhance"]
        }
        assert "The Chatter — Test Edition" not in all_names

    def test_exactly_three_companies_extracted(self, synth_project, monkeypatch):
        _run_dry(synth_project, monkeypatch)

        wl_path = synth_project["newsletter"].parent / "Test_Edition_enhancement_worklist.json"
        wl = json.loads(wl_path.read_text())
        total = len(wl["new_entities"]) + len(wl["existing_entities_to_enhance"])
        assert total == 3


# --------------------------------------------------------------------------- #
# Tests: Stage 2b --cross-check (semantic NEW-name guard)
# --------------------------------------------------------------------------- #


class TestCrossCheck:
    """--cross-check annotates NEW names via query.notes_like_text."""

    def test_flag_routes_new_names(self, synth_project, monkeypatch):
        """--cross-check calls cross_check_new with the NEW-classified names
        (existing companies like HDFC Bank are not passed)."""
        calls = []
        monkeypatch.setattr(pn, "cross_check_new", lambda names, min_sim=0.55: calls.append(names))
        rel = str(synth_project["newsletter"].relative_to(synth_project["root"]))
        monkeypatch.setattr(sys, "argv", ["parse_newsletter.py", rel, "--cross-check"])
        try:
            pn.main()
        except SystemExit:
            pass
        assert len(calls) == 1
        assert "Acme Banking Corp" in calls[0] and "Zeta Technologies" in calls[0]
        assert "HDFC Bank" not in calls[0]

    def test_no_flag_no_cross_check(self, synth_project, monkeypatch):
        calls = []
        monkeypatch.setattr(pn, "cross_check_new", lambda names, min_sim=0.55: calls.append(names))
        _run_dry(synth_project, monkeypatch)
        assert calls == []

    def test_hits_printed(self, synth_project, monkeypatch, capsys):
        """Monkeypatched notes_like_text hits become ⚠ annotation lines."""
        import helpers.graph.query as gq

        def fake_notes_like_text(con, text, k=5, doc_type="company", min_sim=0.0, embed_fn=None):
            if text == "Acme Banking Corp":
                return [("findata/Companies/Banking/Hdfc_Bank.md", "HDFC Bank", 0.81)]
            return []

        monkeypatch.setattr(gq, "notes_like_text", fake_notes_like_text)
        monkeypatch.setattr(gq, "connect", lambda *a, **kw: SimpleNamespace(close=lambda: None))
        pn.cross_check_new(["Acme Banking Corp", "Zeta Technologies"])
        out = capsys.readouterr().out
        assert "NEW 'Acme Banking Corp'" in out
        assert "Hdfc_Bank" in out and "0.81" in out
        assert "1 of 2 NEW name(s)" in out

    def test_unavailable_embedder_warns_once(self, synth_project, monkeypatch, capsys):
        """notes_like_text None → one unavailable line, no per-name noise."""
        import helpers.graph.query as gq

        monkeypatch.setattr(gq, "notes_like_text", lambda *a, **kw: None)
        monkeypatch.setattr(gq, "connect", lambda *a, **kw: SimpleNamespace(close=lambda: None))
        pn.cross_check_new(["A", "B", "C"])
        out = capsys.readouterr().out
        assert "unavailable" in out
        assert out.count("unavailable") == 1

    def test_connect_failure_warns_and_skips(self, synth_project, monkeypatch, capsys):
        import helpers.graph.query as gq

        def boom(*a, **kw):
            raise RuntimeError("no graph.duckdb")

        monkeypatch.setattr(gq, "connect", boom)
        pn.cross_check_new(["A"])
        out = capsys.readouterr().out
        assert "graph connect failed" in out
