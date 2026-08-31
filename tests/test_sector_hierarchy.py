#!/usr/bin/env python3
"""Tests for helpers/maintenance/build_sector_hierarchy.py (Bundle M4).

Covers the curated taxonomy's integrity guarantees:
  - Coverage: all 42 live sectors map to exactly one super-sector (no
    orphans, no collisions).
  - Name-collision guard: a super-sector name that equals an existing
    sector name is rejected (would silently mis-type the sector row via
    INSERT OR IGNORE on the shared PK).
  - Idempotency: re-running --apply is a no-op (INSERT OR IGNORE).
  - --check mode validates without writing.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance import build_sector_hierarchy as bsh  # noqa: E402

import pytest  # noqa: E402

pytestmark = [pytest.mark.integration]


# Minimal schema mirroring the production entities + graph_edges tables
# (only the columns the taxonomy script touches).
_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    file_path TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
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
    UNIQUE(source, target, edge_type),
    CHECK (source != target),
    CHECK (json_valid(properties))
);
"""

# The 42 live sectors — what the script must fully cover.
LIVE_SECTORS = [
    "Agriculture",
    "Automotive",
    "Aviation",
    "Banking",
    "Building_Materials",
    "Capital_Markets",
    "Chemicals",
    "Consumer",
    "Defense",
    "Diagnostics",
    "Diversified",
    "EMS_Manufacturing",
    "Education_Training",
    "Electronics",
    "Energy",
    "Engineering_Capital_Goods",
    "FMCG",
    "Fertilizer",
    "Financial_Services",
    "Fintech_Payments",
    "Healthcare",
    "Hospitals",
    "Housing_Finance",
    "Infrastructure",
    "Insurance",
    "International",
    "Logistics",
    "Media_Entertainment",
    "Metals",
    "Mining",
    "NBFC",
    "Packaging",
    "Pharma",
    "Railways",
    "Real_Estate",
    "Renewables",
    "Retail",
    "Semiconductors",
    "Technology",
    "Telecommunications",
    "Textiles",
    "Travel",
]


def _build_db(tmp_path: Path) -> Path:
    """Build a tmp DB seeded with the 42 live sectors."""
    db_path = tmp_path / "test.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for s in LIVE_SECTORS:
        con.execute(
            "INSERT INTO entities (name, entity_type) VALUES (?, 'sector')",
            (s,),
        )
    con.commit()
    con.close()
    return db_path


def _run_build(db_path: Path, mode: str, tmp_path: Path | None = None) -> int:
    """Invoke build(write=...) against a tmp DB via monkeypatch.

    When tmp_path is given, VAULT_ROOT/SUPER_SECTORS_DIR are redirected there
    so build(write=True) writes notes under the tmp dir, NOT the real
    findata/ vault. Without this, the test silently overwrites real
    Super_Sector/Sector markdown files (byte-identical today, but a template
    change would corrupt them).
    """
    orig_db = bsh.DB_PATH
    orig_vault = bsh.VAULT_ROOT
    orig_ss_dir = bsh.SUPER_SECTORS_DIR
    try:
        bsh.DB_PATH = db_path
        if tmp_path is not None:
            bsh.VAULT_ROOT = tmp_path / "findata"
            bsh.SUPER_SECTORS_DIR = bsh.VAULT_ROOT / "Super_Sectors"
        return bsh.build(write=(mode == "apply"))
    finally:
        bsh.DB_PATH = orig_db
        bsh.VAULT_ROOT = orig_vault
        bsh.SUPER_SECTORS_DIR = orig_ss_dir


# --------------------------------------------------------------------------- #
# Coverage validation                                                         #
# --------------------------------------------------------------------------- #
class TestCoverage:
    def test_validate_coverage_passes_on_full_mapping(self):
        # The curated SUPER_SECTORS must cover all 42 live sectors exactly once.
        errors = bsh._validate_coverage(set(LIVE_SECTORS))
        assert errors == [], f"taxonomy has coverage errors: {errors}"

    def test_detects_orphan_sector(self):
        # Add a sector not in any super-sector -> should error.
        errors = bsh._validate_coverage(set(LIVE_SECTORS) | {"PhantomSector"})
        assert any("PhantomSector" in e for e in errors)

    def test_detects_super_sector_name_collision(self):
        # A super-sector named identically to a live sector -> collision error.
        # (Healthcare/Energy collided before the _Super rename.)
        bad_taxonomy = {"Healthcare": ["Healthcare"]}
        orig = bsh.SUPER_SECTORS
        try:
            bsh.SUPER_SECTORS = bad_taxonomy
            errors = bsh._validate_coverage({"Healthcare"})
        finally:
            bsh.SUPER_SECTORS = orig
        assert any("collides" in e for e in errors)


# --------------------------------------------------------------------------- #
# Build / apply                                                               #
# --------------------------------------------------------------------------- #
class TestBuild:
    def test_apply_creates_expected_entities_and_edges(self, tmp_path):
        db = _build_db(tmp_path)
        rc = _run_build(db, "apply", tmp_path)
        assert rc == 0
        con = sqlite3.connect(db)
        # 9 super_sector + 78 sub_sector = 87 new entities. The 78 sub_sectors
        # are the merged Level 3 (19 sectors' subsector/* tags + 5 sectors'
        # ### headings, minus 2 degenerate self-named entries).
        n_ss = con.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='super_sector'"
        ).fetchone()[0]
        n_sub = con.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='sub_sector'"
        ).fetchone()[0]
        assert n_ss == 9
        assert n_sub == 78
        # 42 sector->super + 78 sub->sector = 120 belongs_to edges
        n_bt = con.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='belongs_to'"
        ).fetchone()[0]
        assert n_bt == 120
        con.close()

    def test_apply_is_idempotent(self, tmp_path):
        db = _build_db(tmp_path)
        _run_build(db, "apply", tmp_path)
        con1 = sqlite3.connect(db)
        e1 = con1.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        bt1 = con1.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='belongs_to'"
        ).fetchone()[0]
        con1.close()
        # Re-run — should be a no-op (INSERT OR IGNORE).
        _run_build(db, "apply", tmp_path)
        con2 = sqlite3.connect(db)
        e2 = con2.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        bt2 = con2.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='belongs_to'"
        ).fetchone()[0]
        con2.close()
        assert e2 == e1
        assert bt2 == bt1

    def test_check_mode_does_not_write(self, tmp_path):
        db = _build_db(tmp_path)
        rc = _run_build(db, "check")
        assert rc == 0
        con = sqlite3.connect(db)
        # No new entities / edges should exist.
        n_ss = con.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_type='super_sector'"
        ).fetchone()[0]
        n_bt = con.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='belongs_to'"
        ).fetchone()[0]
        con.close()
        assert n_ss == 0
        assert n_bt == 0


# --------------------------------------------------------------------------- #
# Taxonomy shape                                                              #
# --------------------------------------------------------------------------- #
class TestTaxonomyShape:
    def test_nine_super_sectors(self):
        assert len(bsh.SUPER_SECTORS) == 9

    def test_no_super_sector_name_collides_with_child(self):
        # The _Super rename resolved Healthcare/Energy; guard against
        # regression — no super-sector name (normalized) may equal a child.
        for ss, members in bsh.SUPER_SECTORS.items():
            stem = bsh._normalize(ss)
            assert stem not in members, f"super-sector {ss!r} ({stem!r}) equals a child sector name"

    def test_sub_category_parents_are_real_sectors(self):
        for parent in bsh.SUB_CATEGORIES:
            assert parent in LIVE_SECTORS, f"sub-category parent {parent!r} is not a live sector"


# ---------------------------------------------------------------------------
# _normalize — pure unit tests
# ---------------------------------------------------------------------------
def test_normalize_single_word():
    assert bsh._normalize("Financials") == "Financials"


def test_normalize_multi_word():
    assert bsh._normalize("Consumer Discretionary") == "Consumer_Discretionary"


# ---------------------------------------------------------------------------
# _note_path
# ---------------------------------------------------------------------------
def test_note_path():
    path = bsh._note_path("super_sector", "Information Technology")
    assert path == "findata/Super_Sectors/Information_Technology.md"


# ---------------------------------------------------------------------------
# _super_sector_note
# ---------------------------------------------------------------------------
def test_super_sector_note_structure():
    note = bsh._super_sector_note("Financials", ["Banks", "Insurance"])
    assert "title: Financials" in note
    assert "type: super_sector" in note
    assert "[[Banks]]" in note
    assert "[[Insurance]]" in note
    assert "BEGIN auto child sectors" in note


def test_super_sector_note_empty_children():
    note = bsh._super_sector_note("Financials", [])
    assert "title: Financials" in note
    assert "## Child Sectors (auto)" in note


# ---------------------------------------------------------------------------
# _inject_uplink
# ---------------------------------------------------------------------------
def test_inject_uplink_adds_section():
    content = "---\ntitle: Banks\ntype: sector\n---\n\n# Banks\n\nSome content"
    result = bsh._inject_uplink(content, "Financials")
    assert "super_sector: Financials" in result
    assert "[[Financials]]" in result
    assert "BEGIN auto super-sector uplink" in result


def test_inject_uplink_idempotent():
    content = "---\ntitle: Banks\ntype: sector\n---\n\n# Banks\n\nSome content"
    first = bsh._inject_uplink(content, "Financials")
    second = bsh._inject_uplink(first, "Financials")
    # Count uplink sections — should be exactly 1
    assert second.count("BEGIN auto super-sector uplink") == 1
    assert second.count("super_sector: Financials") == 1


def test_inject_uplink_no_h1_appends():
    content = "---\ntitle: X\ntype: sector\n---\n\nNo heading here"
    result = bsh._inject_uplink(content, "Financials")
    assert "[[Financials]]" in result


# --------------------------------------------------------------------------- #
# --check drift gate (2026-08-19): taxonomy maps may be fine while the       #
# NOTES lag a missed --apply. Region-scoped: other writers (OKF backfill)    #
# legitimately extend these notes, so only the sentinel regions decide.      #
# --------------------------------------------------------------------------- #
class TestCheckDrift:
    def test_check_fresh_after_apply(self, tmp_path):
        db = _build_db(tmp_path)
        assert _run_build(db, "apply", tmp_path) == 0
        assert _run_build(db, "check", tmp_path) == 0

    def test_check_writes_nothing(self, tmp_path):
        db = _build_db(tmp_path)
        _run_build(db, "apply", tmp_path)
        notes = sorted((tmp_path / "findata" / "Super_Sectors").glob("*.md"))
        before = [p.read_text() for p in notes]
        assert _run_build(db, "check", tmp_path) == 0
        assert [p.read_text() for p in notes] == before

    def test_drifted_child_sector_region_fails_check(self, tmp_path):
        db = _build_db(tmp_path)
        assert _run_build(db, "apply", tmp_path) == 0
        note = tmp_path / "findata" / "Super_Sectors" / "Industrials.md"
        note.write_text(note.read_text().replace("[[Defense]]", "[[DefenseX]]"))
        assert _run_build(db, "check", tmp_path) == 1

    def test_okf_frontmatter_additions_are_not_drift(self, tmp_path):
        # The OKF backfill adds generated/stale_after frontmatter to
        # super-sector notes — a full-file compare would false-positive
        # (and --apply would clobber the keys). The gate must stay
        # region-scoped.
        db = _build_db(tmp_path)
        _run_build(db, "apply", tmp_path)
        note = next((tmp_path / "findata" / "Super_Sectors").glob("*.md"))
        note.write_text(
            note.read_text().replace(
                "---\n",
                "---\ngenerated:\n  by: process:okf_backfill\n  at: '2026-08-19T00:00:00Z'\n",
                1,
            )
        )
        assert _run_build(db, "check", tmp_path) == 0

    def test_drifted_uplink_fails_check(self, tmp_path):
        db = _build_db(tmp_path)
        _run_build(db, "apply", tmp_path)
        sectors_dir = tmp_path / "findata" / "Sectors"
        sectors_dir.mkdir(parents=True)
        # Defense belongs to Industrials (curated taxonomy).
        defense = sectors_dir / "Defense.md"
        defense.write_text("# Defense\n", encoding="utf-8")
        _run_build(db, "apply", tmp_path)  # writes the uplink
        assert "Industrials" in defense.read_text()
        defense.write_text(defense.read_text().replace("Industrials", "Wrong_Super"))
        assert _run_build(db, "check", tmp_path) == 1
