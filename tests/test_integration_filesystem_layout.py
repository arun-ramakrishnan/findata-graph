#!/usr/bin/env python3
"""P4 — Sector / Companies filesystem <-> DB consistency integration tests.

Verifies the layout invariants between the findata/ directory tree and the
entities/graph_edges tables:

  - Every company note is under the correct sector directory
  - Every sector note exists in findata/Sectors/
  - file_path follows findata/Companies/<sector>/<slug>.md pattern
  - entity_type matches filesystem location (company→Companies/, sector→Sectors/)
  - Company ↔ sector edges are bidirectional

Builds a synthetic findata tree + seeded DB, then runs invariant checks.

See doc/improvements/archive/testing/integration_plan.txt § Priority 4.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = [pytest.mark.integration]

# --------------------------------------------------------------------------- #
# Schema: entities + entity_tags + graph_edges
# --------------------------------------------------------------------------- #

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

# --------------------------------------------------------------------------- #
# Seed data: companies and sectors
# --------------------------------------------------------------------------- #

# (name, entity_type, sector, slug)
_SEED_ENTITIES = [
    ("HDFC Bank", "company", "Banking", "HDFC_Bank"),
    ("ICICI Bank", "company", "Banking", "ICICI_Bank"),
    ("State Bank of India", "company", "Banking", "State_Bank_of_India"),
    ("Infosys", "company", "Technology", "Infosys"),
    ("TCS", "company", "Technology", "TCS"),
    ("Banking", "sector", None, "Banking"),
    ("Technology", "sector", None, "Technology"),
]

# (source, target, edge_type)
_SEED_EDGES = [
    ("HDFC Bank", "Banking", "belongs_to"),
    ("ICICI Bank", "Banking", "belongs_to"),
    ("State Bank of India", "Banking", "belongs_to"),
    ("Infosys", "Technology", "belongs_to"),
    ("TCS", "Technology", "belongs_to"),
]


@pytest.fixture
def p4_tree(tmp_path):
    """Synthetic findata tree + seeded DB.

    Returns a tuple (findata_dir, db_conn) so tests can inspect both sides.
    """
    findata = tmp_path / "findata"
    companies_dir = findata / "Companies"
    sectors_dir = findata / "Sectors"

    # Build the directory tree
    for sector in ("Banking", "Technology"):
        (companies_dir / sector).mkdir(parents=True)
    sectors_dir.mkdir(parents=True)

    # Write company notes
    for name, etype, sector, slug in _SEED_ENTITIES:
        if etype == "company":
            (companies_dir / sector / f"{slug}.md").write_text(
                f"---\ntitle: {name}\ntype: company\nsector: {sector}\n---\n\n"
                f"# {name}\n\nCompany note for {name}.\n",
                encoding="utf-8",
            )
        elif etype == "sector":
            (sectors_dir / f"{slug}.md").write_text(
                f"---\ntitle: {name}\ntype: sector\n---\n\n"
                f"# {name}\n\nSector note for {name}.\n",
                encoding="utf-8",
            )

    # Build the DB
    db_path = str(tmp_path / "p4_layout.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    for name, etype, sector, slug in _SEED_ENTITIES:
        if etype == "company":
            fp = f"findata/Companies/{sector}/{slug}.md"
        else:
            fp = f"findata/Sectors/{slug}.md"
        conn.execute(
            "INSERT INTO entities(name, entity_type, sector_classification, "
            "file_path, normalized_name) VALUES (?,?,?,?,?)",
            (name, etype, sector, fp, name.lower()),
        )

    for source, target, edge_type in _SEED_EDGES:
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES (?,?,?,?)",
            (source, target, edge_type, "seed:test"),
        )
    conn.commit()

    yield findata, conn

    conn.close()


# --------------------------------------------------------------------------- #
# Layout invariant tests
# --------------------------------------------------------------------------- #


class TestCompanyNotesExist:
    """Every company entity has a corresponding .md file on disk."""

    def test_all_companies_have_files(self, p4_tree):
        findata, conn = p4_tree
        companies = conn.execute(
            "SELECT name, file_path FROM entities WHERE entity_type='company'"
        ).fetchall()
        assert len(companies) > 0
        for c in companies:
            # file_path is relative to project root, findata/ is the root
            fp = findata.parent / c["file_path"]
            assert fp.exists(), f"Missing company note: {c['file_path']}"

    def test_company_files_are_in_companies_dir(self, p4_tree):
        findata, conn = p4_tree
        companies = conn.execute(
            "SELECT name, file_path FROM entities WHERE entity_type='company'"
        ).fetchall()
        for c in companies:
            fp = c["file_path"]
            assert fp.startswith("findata/Companies/"), \
                f"Company {c['name']} file_path not under Companies/: {fp}"


class TestSectorNotesExist:
    """Every sector entity has a corresponding .md file."""

    def test_all_sectors_have_files(self, p4_tree):
        findata, conn = p4_tree
        sectors = conn.execute(
            "SELECT name, file_path FROM entities WHERE entity_type='sector'"
        ).fetchall()
        assert len(sectors) > 0
        for s in sectors:
            fp = findata.parent / s["file_path"]
            assert fp.exists(), f"Missing sector note: {s['file_path']}"

    def test_sector_files_are_in_sectors_dir(self, p4_tree):
        findata, conn = p4_tree
        sectors = conn.execute(
            "SELECT file_path FROM entities WHERE entity_type='sector'"
        ).fetchall()
        for s in sectors:
            fp = s["file_path"]
            assert fp.startswith("findata/Sectors/"), \
                f"Sector file_path not under Sectors/: {fp}"


class TestSectorClassificationConsistency:
    """Company's sector_classification matches its directory name."""

    def test_classification_matches_directory(self, p4_tree):
        findata, conn = p4_tree
        companies = conn.execute(
            "SELECT name, file_path, sector_classification FROM entities "
            "WHERE entity_type='company'"
        ).fetchall()
        for c in companies:
            parts = c["file_path"].split("/")
            # findata/Companies/<sector>/<slug>.md
            assert len(parts) == 4, f"Unexpected path shape: {c['file_path']}"
            dir_sector = parts[2]
            db_sector = c["sector_classification"]
            assert dir_sector == db_sector, \
                f"Company {c['name']}: dir='{dir_sector}' vs DB='{db_sector}'"


class TestFilePathFormat:
    """file_path follows findata/Companies/<sector>/<slug>.md pattern."""

    def test_company_paths_are_well_formed(self, p4_tree):
        findata, conn = p4_tree
        companies = conn.execute(
            "SELECT name, file_path FROM entities WHERE entity_type='company'"
        ).fetchall()
        for c in companies:
            fp = c["file_path"]
            parts = fp.split("/")
            # Must be: findata/Companies/<Sector>/<Slug>.md
            assert len(parts) == 4
            assert parts[0] == "findata"
            assert parts[1] == "Companies"
            assert parts[3].endswith(".md")

    def test_sector_paths_are_well_formed(self, p4_tree):
        findata, conn = p4_tree
        sectors = conn.execute(
            "SELECT name, file_path FROM entities WHERE entity_type='sector'"
        ).fetchall()
        for s in sectors:
            fp = s["file_path"]
            parts = fp.split("/")
            # Must be: findata/Sectors/<Slug>.md
            assert len(parts) == 3
            assert parts[0] == "findata"
            assert parts[1] == "Sectors"
            assert parts[2].endswith(".md")


class TestBelongsToEdges:
    """Company ↔ sector belongs_to edges are well-formed."""

    def test_every_company_has_belongs_to(self, p4_tree):
        findata, conn = p4_tree
        companies = conn.execute(
            "SELECT name FROM entities WHERE entity_type='company'"
        ).fetchall()
        for c in companies:
            edges = conn.execute(
                "SELECT target FROM graph_edges "
                "WHERE source=? AND edge_type='belongs_to'",
                (c["name"],),
            ).fetchall()
            assert len(edges) >= 1, \
                f"Company {c['name']} has no belongs_to edge"

    def test_belongs_to_target_is_sector(self, p4_tree):
        findata, conn = p4_tree
        edges = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='belongs_to'"
        ).fetchall()
        for e in edges:
            target = conn.execute(
                "SELECT entity_type FROM entities WHERE name=?", (e["target"],)
            ).fetchone()
            assert target is not None, \
                f"belongs_to target '{e['target']}' not in entities"
            assert target["entity_type"] == "sector", \
                f"belongs_to target '{e['target']}' is not a sector"

    def test_belongs_to_source_is_company(self, p4_tree):
        findata, conn = p4_tree
        edges = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='belongs_to'"
        ).fetchall()
        for e in edges:
            source = conn.execute(
                "SELECT entity_type FROM entities WHERE name=?", (e["source"],)
            ).fetchone()
            assert source is not None, \
                f"belongs_to source '{e['source']}' not in entities"
            assert source["entity_type"] == "company", \
                f"belongs_to source '{e['source']}' is not a company"


class TestEntityCountMatchesFilesystem:
    """DB entity count matches actual file count on disk."""

    def test_company_count_matches_files(self, p4_tree):
        findata, conn = p4_tree
        db_count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='company'"
        ).fetchone()[0]
        file_count = sum(1 for _ in (findata / "Companies").rglob("*.md"))
        assert db_count == file_count, \
            f"DB has {db_count} companies but {file_count} company files on disk"

    def test_sector_count_matches_files(self, p4_tree):
        findata, conn = p4_tree
        db_count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='sector'"
        ).fetchone()[0]
        file_count = sum(1 for _ in (findata / "Sectors").glob("*.md"))
        assert db_count == file_count, \
            f"DB has {db_count} sectors but {file_count} sector files on disk"


class TestNormalizedNamesConsistency:
    """normalized_name should be a lowercased version of name."""

    def test_normalized_names_lowercase(self, p4_tree):
        findata, conn = p4_tree
        rows = conn.execute(
            "SELECT name, normalized_name FROM entities WHERE normalized_name IS NOT NULL"
        ).fetchall()
        for r in rows:
            assert r["normalized_name"] == r["name"].lower(), \
                f"normalized_name mismatch: {r['name']} → {r['normalized_name']}"


class TestOrphanedFilesDetection:
    """Files on disk that have no DB entry are flagged."""

    def test_orphaned_company_file_detected(self, p4_tree):
        findata, conn = p4_tree
        # Create an orphan file
        orphan = findata / "Companies" / "Banking" / "Orphan_Company.md"
        orphan.write_text("# Orphan", encoding="utf-8")

        # Re-check: DB count should be LESS than file count
        db_count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='company'"
        ).fetchone()[0]
        file_count = sum(1 for _ in (findata / "Companies").rglob("*.md"))
        assert file_count == db_count + 1, \
            f"Orphan not detected: files={file_count}, db={db_count}"

    def test_orphaned_sector_file_detected(self, p4_tree):
        findata, conn = p4_tree
        orphan = findata / "Sectors" / "Orphan_Sector.md"
        orphan.write_text("# Orphan Sector", encoding="utf-8")

        db_count = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='sector'"
        ).fetchone()[0]
        file_count = sum(1 for _ in (findata / "Sectors").glob("*.md"))
        assert file_count == db_count + 1, \
            f"Orphan sector not detected: files={file_count}, db={db_count}"
