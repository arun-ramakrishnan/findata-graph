"""Unit tests for helpers/maintenance/rename_entity.py."""

from __future__ import annotations
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance.rename_entity import replace_field  # noqa: E402


# ---------------------------------------------------------------------------
# replace_field — pure string function
# ---------------------------------------------------------------------------
def test_replace_field_basic():
    yaml = "---\ntitle: Old Name\nnormalized_name: Old_Name\n---"
    result = replace_field(yaml, "normalized_name", "New_Name")
    assert "New_Name" in result
    assert "Old_Name" not in result


def test_replace_field_title():
    yaml = "---\ntitle: Old Name\n---"
    result = replace_field(yaml, "title", "New Name")
    assert "title: New Name" in result


def test_replace_field_absent_adds():
    yaml = "---\ntitle: Foo\n---"
    result = replace_field(yaml, "ticker", "ABC.NS")
    assert "ticker: ABC.NS" in result


def test_replace_field_with_spaces_in_field():
    yaml = "---\n  permalink: old/link\n---"
    result = replace_field(yaml, "permalink", "new/link")
    assert "new/link" in result


def test_replace_field_preserves_other_fields():
    yaml = "---\ntitle: Foo\nsector: Healthcare\nticker: ABC.NS\n---"
    result = replace_field(yaml, "sector", "Consumer")
    assert "title: Foo" in result
    assert "ticker: ABC.NS" in result
    assert "sector: Consumer" in result


def test_replace_field_only_replaces_first_match():
    yaml = "---\ntitle: A\ntitle: B\n---"
    result = replace_field(yaml, "title", "X")
    assert result.count("title: X") == 1


# ===========================================================================
# Integration test for main() — DB + filesystem
# ===========================================================================
import sqlite3 as _re_sqlite3


def _make_rename_test_db(tmp_path):
    """Create a test DB + markdown file for rename_entity tests."""
    db_path = tmp_path / "test.db"
    conn = _re_sqlite3.connect(str(db_path))
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
            FOREIGN KEY (source) REFERENCES entities(name) ON UPDATE CASCADE,
            FOREIGN KEY (target) REFERENCES entities(name) ON UPDATE CASCADE,
            UNIQUE(source, target, edge_type)
        )
    """)
    conn.execute("""
        CREATE TABLE entity_tags (
            entity_name TEXT, tag TEXT,
            FOREIGN KEY (entity_name) REFERENCES entities(name) ON UPDATE CASCADE,
            UNIQUE(entity_name, tag)
        )
    """)
    # Create the markdown file
    sector = "Healthcare"
    sector_dir = tmp_path / "findata" / "Companies" / sector
    sector_dir.mkdir(parents=True)
    note_path = sector_dir / "Old_Name.md"
    note_path.write_text(
        "---\n"
        "title: Old Name\n"
        "type: company\n"
        "normalized_name: Old_Name\n"
        "sector: Healthcare\n"
        "file_path: findata/Companies/Healthcare/Old_Name.md\n"
        "permalink: companies/healthcare/old_name\n"
        "last_modified: '2025-01-01'\n"
        "---\n\n"
        "# Old Name\n\nBody text."
    )
    conn.execute(
        "INSERT INTO entities VALUES ('Old Name', 'company', '2025-01-01', "
        "'findata/Companies/Healthcare/Old_Name.md', '2025-01-01', 'Old_Name', 'Healthcare', NULL)"
    )
    conn.execute(
        "INSERT INTO entities VALUES ('Healthcare', 'sector', '2025-01-01', "
        "'findata/Sectors/Healthcare.md', '2025-01-01', 'Healthcare', NULL, NULL)"
    )
    # Add an edge referencing the old name (cascade should update it)
    conn.execute("INSERT INTO graph_edges VALUES ('Old Name', 'Healthcare', 'part_of', 'test')")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    return db_path


class TestRenameEntityMain:
    def test_rename_succeeds(self, tmp_path, monkeypatch):
        import helpers.maintenance.rename_entity as re_mod

        monkeypatch.setattr(re_mod, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(re_mod, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr("sys.argv", ["rename_entity.py", "Old Name", "New Name"])

        db_path = _make_rename_test_db(tmp_path)

        rc = re_mod.main()
        assert rc == 0

        # Verify entity renamed
        conn = _re_sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        row = conn.execute("SELECT name, normalized_name, file_path FROM entities").fetchone()
        assert row[0] == "New Name"
        assert row[1] == "New_Name"
        assert "New_Name.md" in row[2]
        conn.close()

        # Verify file moved
        assert (tmp_path / "findata" / "Companies" / "Healthcare" / "New_Name.md").exists()
        assert not (tmp_path / "findata" / "Companies" / "Healthcare" / "Old_Name.md").exists()

    def test_rename_with_ticker_override(self, tmp_path, monkeypatch):
        import helpers.maintenance.rename_entity as re_mod

        monkeypatch.setattr(re_mod, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(re_mod, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            "sys.argv", ["rename_entity.py", "Old Name", "New Name", "--ticker", "NEW.NS"]
        )

        db_path = _make_rename_test_db(tmp_path)

        rc = re_mod.main()
        assert rc == 0

        conn = _re_sqlite3.connect(str(db_path))
        row = conn.execute("SELECT ticker FROM entities WHERE name='New Name'").fetchone()
        assert row[0] == "NEW.NS"
        conn.close()

    def test_rename_entity_not_found(self, tmp_path, monkeypatch):
        import helpers.maintenance.rename_entity as re_mod

        monkeypatch.setattr(re_mod, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(re_mod, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr("sys.argv", ["rename_entity.py", "Ghost", "New Name"])

        _make_rename_test_db(tmp_path)

        rc = re_mod.main()
        assert rc == 1

    def test_rename_no_args(self, monkeypatch):
        import helpers.maintenance.rename_entity as re_mod

        monkeypatch.setattr("sys.argv", ["rename_entity.py"])
        rc = re_mod.main()
        assert rc == 2

    def test_rename_cascades_edges(self, tmp_path, monkeypatch):
        import helpers.maintenance.rename_entity as re_mod

        monkeypatch.setattr(re_mod, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(re_mod, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr("sys.argv", ["rename_entity.py", "Old Name", "New Name"])

        db_path = _make_rename_test_db(tmp_path)

        rc = re_mod.main()
        assert rc == 0

        # Verify edge cascaded
        conn = _re_sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        edges = conn.execute("SELECT source FROM graph_edges WHERE edge_type='part_of'").fetchall()
        assert all(e[0] == "New Name" for e in edges)
        assert len(edges) == 1
        conn.close()


# ---------------------------------------------------------------------------
# Slice D — transactional hardening: rename to an EXISTING name rolls back
# ---------------------------------------------------------------------------
def test_rename_to_existing_name_rolls_back(tmp_path, monkeypatch):
    """rename_entity must roll back the whole transaction (DB + file) when the
    new name collides with an existing PK — nothing is partially applied."""
    import helpers.maintenance.rename_entity as re_mod

    monkeypatch.setattr(re_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(re_mod, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("sys.argv", ["rename_entity.py", "Old Name", "Existing Name"])

    db_path = _make_rename_test_db(tmp_path)

    # Add a second, pre-existing entity the rename would collide with.
    conn = _re_sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    sector_dir = tmp_path / "findata" / "Companies" / "Healthcare"
    (sector_dir / "Existing_Name.md").write_text(
        "---\ntitle: Existing Name\nnormalized_name: Existing_Name\n---\n\n# Existing Name\n"
    )
    conn.execute(
        "INSERT INTO entities VALUES ('Existing Name','company','2025-01-01',"
        "'findata/Companies/Healthcare/Existing_Name.md','2025-01-01','Existing_Name','Healthcare',NULL)"
    )
    conn.commit()
    conn.close()

    rc = re_mod.main()
    assert rc == 1

    # DB unchanged: both names present, no rename applied.
    conn = _re_sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    conn.close()
    assert "Old Name" in names
    assert "Existing Name" in names

    # Markdown file unchanged (the failed UPDATE aborts before the move step).
    assert (tmp_path / "findata" / "Companies" / "Healthcare" / "Old_Name.md").exists()
    assert not (tmp_path / "findata" / "Companies" / "Healthcare" / "New_Name.md").exists()
