"""Unit tests for helpers/maintenance/move_sector.py."""

from __future__ import annotations
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

from helpers.maintenance.move_sector import (  # noqa: E402
    err,
    ok,
    normalize_sector_tag_value,
    update_yaml_field,
    update_yaml_sector_tag,
    update_yaml_permalink,
    update_yaml_file_path,
    update_yaml_sector_field,
    bump_last_modified,
    TODAY,
)


# ---------------------------------------------------------------------------
# normalize_sector_tag_value
# ---------------------------------------------------------------------------
def test_normalize_lowercases():
    assert normalize_sector_tag_value("Healthcare") == "healthcare"
    assert normalize_sector_tag_value("CONSUMER") == "consumer"
    assert normalize_sector_tag_value("fintech_payments") == "fintech_payments"


# ---------------------------------------------------------------------------
# update_yaml_field — replace existing
# ---------------------------------------------------------------------------
def test_update_yaml_field_replace():
    yaml = "---\ntitle: Foo\nsector: Old\n---"
    result = update_yaml_field(yaml, "sector", "New")
    assert "sector: New" in result
    assert "sector: Old" not in result


def test_update_yaml_field_add_absent():
    yaml = "---\ntitle: Foo\n---"
    result = update_yaml_field(yaml, "sector", "New")
    assert "sector: New" in result


def test_update_yaml_field_quoted_value():
    yaml = '---\ntitle: Foo\npermalink: "companies/old/X"\n---'
    result = update_yaml_field(yaml, "permalink", "companies/new/X")
    assert "companies/new/X" in result


# ---------------------------------------------------------------------------
# update_yaml_sector_tag
# ---------------------------------------------------------------------------
def test_update_sector_tag_replace():
    yaml = "---\ntags:\n- sector/Healthcare\n- entity_type/company\n---"
    result = update_yaml_sector_tag(yaml, "Healthcare", "Consumer")
    assert "sector/consumer" in result
    assert "sector/Healthcare" not in result


def test_update_sector_tag_case_insensitive():
    yaml = "---\ntags:\n- sector/healthcare\n---"
    result = update_yaml_sector_tag(yaml, "Healthcare", "Consumer")
    assert "sector/consumer" in result


def test_update_sector_tag_no_existing():
    yaml = "---\ntags:\n- entity_type/company\n---"
    result = update_yaml_sector_tag(yaml, "Healthcare", "Consumer")
    # Should return unchanged when no sector tag exists
    assert result == yaml


# ---------------------------------------------------------------------------
# update_yaml_permalink
# ---------------------------------------------------------------------------
def test_update_permalink():
    yaml = "---\npermalink: companies/old/Company_X\n---"
    result = update_yaml_permalink(yaml, "NewSector", "Company_X")
    assert "companies/newsector/company_x" in result


def test_update_permalink_add_absent():
    yaml = "---\ntitle: Foo\n---"
    result = update_yaml_permalink(yaml, "NewSector", "Company_X")
    assert "companies/newsector/company_x" in result


# ---------------------------------------------------------------------------
# update_yaml_file_path
# ---------------------------------------------------------------------------
def test_update_file_path():
    yaml = "---\nfile_path: findata/Companies/Old/Company_X.md\n---"
    result = update_yaml_file_path(yaml, "NewSector", "Company_X")
    assert "findata/Companies/NewSector/Company_X.md" in result


def test_update_file_path_add_absent():
    yaml = "---\ntitle: Foo\n---"
    result = update_yaml_file_path(yaml, "NewSector", "Company_X")
    assert "findata/Companies/NewSector/Company_X.md" in result


# ---------------------------------------------------------------------------
# update_yaml_sector_field
# ---------------------------------------------------------------------------
def test_update_sector_field():
    yaml = "---\nsector: Old\n---"
    result = update_yaml_sector_field(yaml, "NewSector")
    assert "sector: NewSector" in result


def test_update_sector_field_add():
    yaml = "---\ntitle: Foo\n---"
    result = update_yaml_sector_field(yaml, "NewSector")
    assert "sector: NewSector" in result


# ---------------------------------------------------------------------------
# bump_last_modified
# ---------------------------------------------------------------------------
def test_bump_last_modified_replace():
    yaml = "---\nlast_modified: 2025-01-01\n---"
    result = bump_last_modified(yaml)
    assert f"last_modified: {TODAY}" in result
    assert "2025-01-01" not in result


def test_bump_last_modified_add():
    yaml = "---\ntitle: Foo\n---"
    result = bump_last_modified(yaml)
    assert f"last_modified: {TODAY}" in result


# ---------------------------------------------------------------------------
# err / ok — smoke tests (capture stdout/stderr)
# ---------------------------------------------------------------------------
def test_err_prints_to_stderr(capsys):
    err("something wrong")
    captured = capsys.readouterr()
    assert "✗" in captured.err
    assert "something wrong" in captured.err


def test_ok_prints_to_stdout(capsys):
    ok("all good")
    captured = capsys.readouterr()
    assert "✓" in captured.out
    assert "all good" in captured.out


# ===========================================================================
# Integration test for move_entity — DB + filesystem
# ===========================================================================
import sqlite3 as _sqlite3
from helpers.core.frontmatter import split_frontmatter as _split_fm

from helpers.validators.static_checks import CANONICAL_SECTORS  # noqa: E402


def _make_move_test_db(tmp_path, sector="Healthcare"):
    """Create a test DB + markdown file for move_entity tests."""
    db_path = tmp_path / "test.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT,
            created_at TEXT,
            file_path TEXT,
            last_updated TEXT,
            normalized_name TEXT,
            sector_classification TEXT,
            ticker TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE graph_edges (
            source TEXT, target TEXT, edge_type TEXT,
            source_ref TEXT,
            UNIQUE(source, target, edge_type)
        )
    """)
    # Create the markdown file
    sector_dir = tmp_path / "findata" / "Companies" / sector
    sector_dir.mkdir(parents=True)
    note_path = sector_dir / "Test_Co.md"
    note_path.write_text(
        "---\n"
        "title: Test Co\n"
        "type: company\n"
        "normalized_name: Test_Co\n"
        "sector: " + sector + "\n"
        "file_path: findata/Companies/" + sector + "/Test_Co.md\n"
        "permalink: companies/" + sector.lower() + "/test_co\n"
        "tags:\n"
        "- entity_type/company\n"
        "- sector/" + sector.lower() + "\n"
        "created: '2025-01-01'\n"
        "last_modified: '2025-01-01'\n"
        "---\n\n"
        "# Test Co\n\nBody text."
    )
    conn.execute(
        "INSERT INTO entities VALUES ('Test Co', 'company', '2025-01-01', "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        "'findata/Companies/" + sector + "/Test_Co.md', '2025-01-01', 'Test_Co', ?, NULL)",
        (sector,),
    )
    conn.execute("INSERT INTO graph_edges VALUES ('Test Co', ?, 'part_of', 'test')", (sector,))
    conn.execute("INSERT INTO graph_edges VALUES (?, 'Test Co', 'has_company', 'test')", (sector,))
    conn.commit()
    conn.close()
    return db_path


class TestMoveEntity:
    def test_move_to_new_sector(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path, "Healthcare")
        conn = _sqlite3.connect(str(db_path))
        # Move from Healthcare to Consumer (both in CANONICAL_SECTORS)
        assert "Consumer" in CANONICAL_SECTORS
        result = ms_mod.move_entity(conn, "Test Co", "Consumer", dry_run=False)
        assert result is True
        # Check DB updated
        row = conn.execute(
            "SELECT sector_classification, file_path FROM entities WHERE name='Test Co'"
        ).fetchone()
        assert row[0] == "Consumer"
        assert "Consumer" in row[1]
        # Check new file exists
        assert (tmp_path / "findata" / "Companies" / "Consumer" / "Test_Co.md").exists()
        # Check old file is gone
        assert not (tmp_path / "findata" / "Companies" / "Healthcare" / "Test_Co.md").exists()
        conn.close()

    def test_move_dry_run(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path, "Healthcare")
        conn = _sqlite3.connect(str(db_path))
        result = ms_mod.move_entity(conn, "Test Co", "Consumer", dry_run=True)
        assert result is True
        # Nothing should have changed
        old_path = tmp_path / "findata" / "Companies" / "Healthcare" / "Test_Co.md"
        assert old_path.exists()
        row = conn.execute(
            "SELECT sector_classification FROM entities WHERE name='Test Co'"
        ).fetchone()
        assert row[0] == "Healthcare"
        conn.close()

    def test_move_invalid_sector(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path)
        conn = _sqlite3.connect(str(db_path))
        result = ms_mod.move_entity(conn, "Test Co", "NonExistent_Sector", dry_run=False)
        assert result is False
        conn.close()

    def test_move_entity_not_found(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path)
        conn = _sqlite3.connect(str(db_path))
        result = ms_mod.move_entity(conn, "Ghost Co", "Consumer", dry_run=False)
        assert result is False
        conn.close()

    def test_move_same_sector(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path, "Healthcare")
        conn = _sqlite3.connect(str(db_path))
        result = ms_mod.move_entity(conn, "Test Co", "Healthcare", dry_run=False)
        assert result is True  # idempotent — already there
        conn.close()

    def test_move_updates_yaml(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path, "Healthcare")
        conn = _sqlite3.connect(str(db_path))
        ms_mod.move_entity(conn, "Test Co", "Consumer", dry_run=False)
        new_file = tmp_path / "findata" / "Companies" / "Consumer" / "Test_Co.md"
        text = new_file.read_text()
        opener, yaml_body, rest = _split_fm(text)
        assert "sector: Consumer" in yaml_body
        assert "sector/consumer" in yaml_body
        assert "companies/consumer/test_co" in yaml_body
        conn.close()

    def test_move_updates_edges(self, tmp_path, monkeypatch):
        import helpers.maintenance.move_sector as ms_mod

        monkeypatch.setattr(ms_mod, "PROJECT_ROOT", tmp_path)
        db_path = _make_move_test_db(tmp_path, "Healthcare")
        conn = _sqlite3.connect(str(db_path))
        ms_mod.move_entity(conn, "Test Co", "Consumer", dry_run=False)
        # Old edges should be gone
        old = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE target='Healthcare' OR source='Healthcare'"
        ).fetchone()[0]
        assert old == 0
        # New edges should exist
        new_part_of = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source='Test Co' AND target='Consumer' AND edge_type='part_of'"
        ).fetchone()[0]
        assert new_part_of == 1
        new_has = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source='Consumer' AND target='Test Co' AND edge_type='has_company'"
        ).fetchone()[0]
        assert new_has == 1
        conn.close()
