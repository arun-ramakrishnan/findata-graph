"""Tests for helpers/core/sync_tags.py — sector_classification derivation (E5a).

E5a makes the note's sector/* tag the single source of truth for sector:
sync_tags.py now also UPDATEs entities.sector_classification from the tag,
eliminating drift between the column and the entity_tags table.

These tests seed a minimal DB + note files under tmp_path, run sync_tags.main()
with --db, and assert the column is derived correctly.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from maintenance.migrate_to_graph_edges import ENTITIES_DDL  # noqa: E402
from helpers.validators.static_checks import CANONICAL_SECTORS  # noqa: E402
import helpers.core.sync_tags as sync_tags  # noqa: E402


pytestmark = [pytest.mark.integration]


def _note_with_sector(sector_tag: str, title: str = "Test Co") -> str:
    """A minimal note whose YAML front matter carries one sector/* tag."""
    return (
        "---\n"
        f"title: {title}\n"
        "type: company\n"
        "tags:\n"
        "- entity_type/company\n"
        f"- {sector_tag}\n"
        "---\n"
        f"# {title}\n\n"
        "Line one of real content.\n"
        "Line two of real content.\n"
    )


def _seed_db(db_path: Path, rows: list[tuple]) -> None:
    """Create entities table + insert (name, entity_type, file_path,
    sector_classification) rows."""
    conn = sqlite3.connect(db_path)
    conn.execute(ENTITIES_DDL)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entity_tags ("
        "  entity_name TEXT NOT NULL, tag TEXT NOT NULL, "
        "  PRIMARY KEY (entity_name, tag),"
        "  FOREIGN KEY (entity_name) REFERENCES entities(name)"
        "    ON DELETE CASCADE ON UPDATE CASCADE)"
    )
    conn.executemany(
        "INSERT INTO entities (name, entity_type, file_path, sector_classification) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _run_sync(db_path: Path) -> int:
    """Invoke sync_tags.main() with --db pointing at db_path (main() uses
    argparse on sys.argv, so we patch argv)."""
    old_argv = sys.argv
    sys.argv = ["sync_tags.py", "--db", str(db_path)]
    try:
        return sync_tags.main()
    finally:
        sys.argv = old_argv


def test_sync_tags_updates_sector_classification_from_note_tag(tmp_path, monkeypatch):
    """E5a: an entity whose sector_classification column is stale gets updated
    to match its note's sector/* tag."""
    # Repoint _REPO_ROOT so file_path resolves under tmp_path.
    monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)

    note_dir = tmp_path / "findata" / "Companies" / "Banking"
    note_dir.mkdir(parents=True)
    note_path = note_dir / "Stale_Co.md"
    note_path.write_text(_note_with_sector("sector/banking", "Stale Co"))

    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        [("Stale Co", "company", "findata/Companies/Banking/Stale_Co.md", "StaleValue")],
    )

    rc = _run_sync(db_path)
    assert rc == 0

    conn = sqlite3.connect(db_path)
    val = conn.execute(
        "SELECT sector_classification FROM entities WHERE name = ?", ("Stale Co",)
    ).fetchone()[0]
    conn.close()
    assert val == "Banking", f"expected Banking, got {val!r}"


def test_sync_tags_skips_unchanged_sectors(tmp_path, monkeypatch):
    """E5a: when the column already matches the note's tag, the UPDATE is a
    no-op (IS NOT ? skips matching rows). Verifies the non-destructive design."""
    monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)

    note_dir = tmp_path / "findata" / "Companies" / "Banking"
    note_dir.mkdir(parents=True)
    (note_dir / "Good_Co.md").write_text(_note_with_sector("sector/banking", "Good Co"))

    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        [("Good Co", "company", "findata/Companies/Banking/Good_Co.md", "Banking")],
    )

    rc = _run_sync(db_path)
    assert rc == 0

    # Value unchanged.
    conn = sqlite3.connect(db_path)
    val = conn.execute(
        "SELECT sector_classification FROM entities WHERE name = ?", ("Good Co",)
    ).fetchone()[0]
    conn.close()
    assert val == "Banking"


@pytest.mark.parametrize("canonical", sorted(CANONICAL_SECTORS))
def test_sync_tags_maps_all_canonical_sectors(canonical, tmp_path, monkeypatch):
    """E5a: every one of the 42 canonical sectors round-trips correctly:
    PascalCase -> sector/<lowercase> tag -> back to PascalCase column value.
    Guards against a missing or mistyped entry in the reverse map."""
    monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)

    slug = canonical.lower()
    note_dir = tmp_path / "findata" / "Companies" / canonical
    note_dir.mkdir(parents=True)
    safe_name = f"Co_{slug}"
    (note_dir / f"{safe_name}.md").write_text(
        _note_with_sector(f"sector/{slug}", safe_name.replace("_", " "))
    )

    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        [
            (
                safe_name.replace("_", " "),
                "company",
                f"findata/Companies/{canonical}/{safe_name}.md",
                None,
            )
        ],
    )

    rc = _run_sync(db_path)
    assert rc == 0

    conn = sqlite3.connect(db_path)
    val = conn.execute(
        "SELECT sector_classification FROM entities WHERE name = ?",
        (safe_name.replace("_", " "),),
    ).fetchone()[0]
    conn.close()
    assert val == canonical, f"slug {slug!r} mapped to {val!r}, expected {canonical!r}"


def test_sync_tags_leaves_entities_without_notes_unchanged(tmp_path, monkeypatch):
    """E5a: entities with no file_path (e.g. sector-entities themselves) keep
    their existing sector_classification — non-destructive on missing notes."""
    monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)

    db_path = tmp_path / "test.db"
    # A sector-entity with no file_path and a pre-existing column value.
    _seed_db(db_path, [("Banking", "sector", None, None)])

    rc = _run_sync(db_path)
    assert rc == 0

    conn = sqlite3.connect(db_path)
    val = conn.execute(
        "SELECT sector_classification FROM entities WHERE name = ?", ("Banking",)
    ).fetchone()[0]
    conn.close()
    # Unchanged — no note to derive from.
    assert val is None


def test_sync_tags_skips_sector_entities_with_self_referential_tag(tmp_path, monkeypatch):
    """E5a: sector_classification is only derived for COMPANIES. A sector
    entity whose note carries a sector/<self> tag (e.g. the Automotive sector
    note tags itself sector/automotive) must NOT get sector_classification
    populated — the field classifies companies into sectors, not sectors into
    themselves. Without this guard, every sector entity ends up with a self-
    referential sector_classification, inflating sector member counts."""
    monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)

    # A sector note that tags itself sector/automotive (as the real notes do).
    note_dir = tmp_path / "findata" / "Sectors"
    note_dir.mkdir(parents=True)
    (note_dir / "Automotive.md").write_text(
        "---\n"
        "title: Automotive\n"
        "type: sector\n"
        "tags:\n"
        "- entity_type/sector\n"
        "- sector/automotive\n"
        "---\n"
        "# Automotive\n\n"
        "Sector overview line one.\n"
        "Sector overview line two.\n"
    )

    db_path = tmp_path / "test.db"
    # Sector entity with a file_path and NULL sector_classification.
    _seed_db(
        db_path,
        [("Automotive", "sector", "findata/Sectors/Automotive.md", None)],
    )

    rc = _run_sync(db_path)
    assert rc == 0

    conn = sqlite3.connect(db_path)
    val = conn.execute(
        "SELECT sector_classification FROM entities WHERE name = ?",
        ("Automotive",),
    ).fetchone()[0]
    conn.close()
    # Still NULL — the company guard prevented self-referential population.
    assert val is None, f"sector entity got sector_classification={val!r}"


# --------------------------------------------------------------------------- #
# C1 regression guard (D3, 2026-07-30)                                        #
# --------------------------------------------------------------------------- #
# findata_corpus_audit.txt C1: the geography/business_model/risk_investment/
# investment_theme namespaces were read from YAML but silently DROPPED by
# sync_tags.py's ALLOWED_CATEGORIES allowlist — ~3,100 tags invisible to every
# entity_tags query. This test pins that all four namespaces are admitted, so a
# future narrowing of ALLOWED_CATEGORIES fails loudly here instead of recurring
# silently for months (as C1 did).

_DROPPED_NAMESPACE_TAGS = [
    "risk_investment/growth",
    "business_model/b2b",
    "geography/india",
    "investment_theme/renewable_energy",
]


def test_sync_tags_admits_previously_dropped_namespaces(tmp_path, monkeypatch):
    """D3/C1: tags in the four previously-dropped namespaces must be mirrored
    into entity_tags, not dropped by ALLOWED_CATEGORIES."""
    monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)

    note_dir = tmp_path / "findata" / "Companies" / "Renewables"
    note_dir.mkdir(parents=True)
    note_path = note_dir / "Tagged_Co.md"
    # Build a note carrying one tag from each previously-dropped namespace.
    tag_lines = "\n".join(f"- {t}" for t in _DROPPED_NAMESPACE_TAGS)
    note_path.write_text(
        "---\n"
        "title: Tagged Co\n"
        "type: company\n"
        "tags:\n"
        "- entity_type/company\n"
        "- sector/renewables\n"
        f"{tag_lines}\n"
        "---\n"
        "# Tagged Co\n\n"
        "Line one of real content.\n"
        "Line two of real content.\n"
    )

    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        [("Tagged Co", "company", "findata/Companies/Renewables/Tagged_Co.md", None)],
    )

    rc = _run_sync(db_path)
    assert rc == 0

    conn = sqlite3.connect(db_path)
    synced = {
        row[0]
        for row in conn.execute(
            "SELECT tag FROM entity_tags WHERE entity_name = ?", ("Tagged Co",)
        )
    }
    conn.close()

    missing = [t for t in _DROPPED_NAMESPACE_TAGS if t not in synced]
    assert not missing, (
        f"ALLOWED_CATEGORIES dropped these tags (C1 regression): {missing}. "
        f"Got: {sorted(synced)}"
    )


def test_sync_tags_allowed_categories_covers_all_four_d3_namespaces():
    """D3/C1: static guard. If someone narrows ALLOWED_CATEGORIES, this fails
    at import time of the test rather than after a silent data loss."""
    for ns in ("geography", "business_model", "risk_investment", "investment_theme"):
        assert ns in sync_tags.ALLOWED_CATEGORIES, (
            f"{ns!r} missing from ALLOWED_CATEGORIES — re-introduces C1 tag-drop"
        )


# ---------------------------------------------------------------------------
# allowed_tags — direct unit tests
# ---------------------------------------------------------------------------
def test_allowed_tags_filters_disallowed_category():
    """Tags without a known category prefix are excluded."""
    tags = ["entity_type/company", "random/thing", "sector/India"]
    result = sync_tags.allowed_tags(tags)
    assert "entity_type/company" in result
    assert "sector/India" in result
    assert "random/thing" not in result


def test_allowed_tags_requires_slash():
    """Tags without a slash are excluded even if category is known."""
    tags = ["entity_type", "sector/India"]
    result = sync_tags.allowed_tags(tags)
    assert "entity_type" not in result
    assert "sector/India" in result


def test_allowed_tags_strips_whitespace():
    """Leading/trailing whitespace is stripped."""
    tags = ["  entity_type/company  ", "  market_cap/Large"]
    result = sync_tags.allowed_tags(tags)
    assert "entity_type/company" in result
    assert "market_cap/Large" in result


def test_allowed_tags_empty_input():
    """Empty list returns empty list."""
    assert sync_tags.allowed_tags([]) == []


def test_allowed_tags_all_valid():
    """All valid tags pass through."""
    tags = ["entity_type/company", "sector/Banking", "market_cap/Large", "geography/India"]
    result = sync_tags.allowed_tags(tags)
    assert len(result) == 4


# --- note_tags (newsletter_notes_adoption S4) --------------------------------


def _newsletter_note(tags: list[str]) -> str:
    tag_lines = "\n".join(f"- {t}" for t in tags)
    return f"---\ntype: newsletter\ntags:\n{tag_lines}\n---\n# Ed\nbody\n"


class TestNoteTags:
    def test_rebuilt_from_source_trees(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)
        chatter = tmp_path / "findata" / "The_Chatter"
        chatter.mkdir(parents=True)
        (chatter / "Ed_One.md").write_text(
            _newsletter_note(["series/the_chatter", "publisher/zerodha",
                              "company/x_co"])
        )
        (chatter / "image_map.md").write_text("chrome\n")  # skipped

        db_path = tmp_path / "test.db"
        _seed_db(db_path, [])
        assert _run_sync(db_path) == 0

        rows = sqlite3.connect(db_path).execute(
            "SELECT tag FROM note_tags ORDER BY tag"
        ).fetchall()
        assert rows == [("company/x_co",), ("publisher/zerodha",),
                        ("series/the_chatter",)]

    def test_only_whitelisted_namespaces_mirrored(self, tmp_path, monkeypatch):
        # entity_type/company is valid YAML on any note, but note_tags
        # mirrors only the source vocabulary (series/publisher/company).
        monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)
        chatter = tmp_path / "findata" / "The_Chatter"
        chatter.mkdir(parents=True)
        (chatter / "Ed.md").write_text(
            _newsletter_note(["series/the_chatter", "entity_type/company",
                              "mystery/tag"])
        )
        db_path = tmp_path / "test.db"
        _seed_db(db_path, [])
        _run_sync(db_path)
        rows = sqlite3.connect(db_path).execute(
            "SELECT tag FROM note_tags"
        ).fetchall()
        assert rows == [("series/the_chatter",)]

    def test_full_rebuild_drops_stale_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sync_tags, "_REPO_ROOT", tmp_path)
        chatter = tmp_path / "findata" / "The_Chatter"
        chatter.mkdir(parents=True)
        note = chatter / "Ed.md"
        note.write_text(_newsletter_note(["series/the_chatter"]))
        db_path = tmp_path / "test.db"
        _seed_db(db_path, [])
        _run_sync(db_path)
        # Tag removed from the note -> next sync must drop the row.
        note.write_text(_newsletter_note(["publisher/zerodha"]))
        _run_sync(db_path)
        rows = sqlite3.connect(db_path).execute(
            "SELECT tag FROM note_tags"
        ).fetchall()
        assert rows == [("publisher/zerodha",)]
