"""Tests for helpers/maintenance/sync_sector_wikilinks.py (Bundle H3).

The script regenerates an auto company index in each sector note from the
SQLite source of truth. These tests pin the core contract:
  - the section is complete (every company in the sector appears)
  - links resolve (target is the filename stem, so no phantoms even when
    the YAML title diverges from the stem)
  - the section is idempotent (re-running is a no-op)
  - curated content is preserved (the auto section is additive)
  - --check detects drift without writing
"""

import re
import sqlite3

import pytest


from maintenance.sync_sector_wikilinks import (  # noqa: E402
    _BEGIN,
    _END,
    _render_section,
    _replace_or_insert,
    sync_sector,
)


pytestmark = [pytest.mark.integration]


# --------------------------------------------------------------------------- #
# _render_section + _replace_or_insert (pure-function unit tests)             #
# --------------------------------------------------------------------------- #
def test_render_section_lists_all_companies_as_stem_targeted_links():
    # (name, stem, title) — stem is the Obsidian link TARGET, title display.
    companies = [
        ("HDFC Bank", "HDFC_Bank", "HDFC Bank"),
        ("ICICI Bank", "ICICI_Bank", "ICICI Bank"),
        ("State Bank of India", "State_Bank_of_India", "State Bank of India"),
    ]
    section = _render_section(companies, "Banking")
    assert _BEGIN in section
    assert _END in section
    # Each link's TARGET (before any |) is the filename stem.
    for _name, stem, title in companies:
        assert f"[[{stem}" in section
    assert "3 company note(s) in Banking" in section


def test_render_section_uses_pipe_alias_when_stem_and_title_differ():
    """When stem != title, the link must be [[stem|title]] so the TARGET
    is the resolvable filename stem while the display text stays readable.
    This is the core fix for the 36 phantom links (issue 01)."""
    companies = [("Hindustan Aeronautics", "Hindustan_Aeronautics", "HAL")]
    section = _render_section(companies, "Defense")
    # Target is the stem (resolves), display is the title.
    assert "[[Hindustan_Aeronautics|HAL]]" in section
    # The bare title must NOT be the link target (that was the bug).
    assert "[[HAL]]" not in section


def test_render_section_plain_form_when_stem_equals_title():
    """No unnecessary pipe alias when the two are identical — keeps the
    section readable for the majority of companies."""
    companies = [("Acme Corp", "Acme Corp", "Acme Corp")]
    section = _render_section(companies, "Test")
    assert "[[Acme Corp]]" in section
    assert "|" not in section.split("[[")[1]


def test_render_section_empty_sector_message():
    section = _render_section([], "Empty")
    assert "No companies tracked in this sector yet" in section


def test_replace_or_insert_adds_section_when_absent():
    text = "# Some Sector\n\nIntro.\n\n## Newsletter synthesis — Some Sector\n\nbody.\n"
    new_section = _render_section([("Acme Co", "Acme Co", "Acme Co")], "Some Sector")
    result, changed = _replace_or_insert(text, new_section)
    assert changed
    assert _BEGIN in result and _END in result
    # The auto section must come BEFORE the Newsletter synthesis heading.
    assert result.index(_BEGIN) < result.index("## Newsletter synthesis")


def test_replace_or_insert_is_idempotent_when_section_exists():
    text = "# S\n\n## Newsletter synthesis — S\n\nbody.\n"
    section = _render_section([("Acme Co", "Acme Co", "Acme Co")], "S")
    # First insert.
    text_v1, _ = _replace_or_insert(text, section)
    # Re-run with the SAME section content -> no change.
    text_v2, changed = _replace_or_insert(text_v1, section)
    assert not changed
    assert text_v1 == text_v2


def test_replace_or_insert_detects_staleness_when_membership_changes():
    """If the company set changes, re-syncing produces a different section."""
    text = "# S\n\n## Newsletter synthesis — S\n\nbody.\n"
    section_v1 = _render_section([("Acme Co", "Acme Co", "Acme Co")], "S")
    text_v1, _ = _replace_or_insert(text, section_v1)
    # A new company joined.
    section_v2 = _render_section(
        [("Acme Co", "Acme Co", "Acme Co"), ("Beta Co", "Beta Co", "Beta Co")], "S"
    )
    text_v2, changed = _replace_or_insert(text_v1, section_v2)
    assert changed
    assert "[[Beta Co]]" in text_v2


def test_replace_or_insert_preserves_curated_content():
    """The auto section must not disturb hand-written editorial content."""
    text = (
        "# Banking\n\n"
        "## Major Companies\n\n"
        "### Public Sector Banks\n"
        "- [[SBI]] - hand-curated highlight\n\n"
        "## Newsletter synthesis — Banking\n\nbody.\n"
    )
    section = _render_section([("HDFC Bank", "HDFC_Bank", "HDFC Bank")], "Banking")
    result, _ = _replace_or_insert(text, section)
    # Curated content survives verbatim.
    assert "### Public Sector Banks" in result
    assert "[[SBI]] - hand-curated highlight" in result
    # Auto section is additive.
    assert "## All Companies (auto)" in result


# --------------------------------------------------------------------------- #
# sync_sector (integration against a tmp DB + tmp sector file)                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_sector_db(tmp_path):
    """Build a minimal DB with 2 sectors and 3 companies, plus sector notes."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT,
            file_path TEXT,
            sector_classification TEXT
        );
        """
    )
    # Company notes (with title: frontmatter) + DB rows.
    companies = [
        ("Acme Corp", "Acme_Corp", "Test", "Acme Corp"),
        ("Beta Inc", "Beta_Inc", "Test", "Beta Inc"),
        ("Gamma Ltd", "Gamma_Ltd", "Other", "Gamma Ltd"),
    ]
    for name, stem, sector, title in companies:
        fp = f"findata/Companies/{sector}/{stem}.md"
        note_dir = tmp_path / "findata" / "Companies" / sector
        note_dir.mkdir(parents=True, exist_ok=True)
        (note_dir / f"{stem}.md").write_text(
            f"---\ntitle: {title}\ntype: company\n---\n# {title}\n",
            encoding="utf-8",
        )
        conn.execute(
            "INSERT INTO entities (name, entity_type, file_path, sector_classification) "
            "VALUES (?,?,?,?)",
            (name, "company", fp, sector),
        )
    conn.commit()
    # A sector note for "Test".
    sectors_dir = tmp_path / "findata" / "Sectors"
    sectors_dir.mkdir(parents=True, exist_ok=True)
    (sectors_dir / "Test.md").write_text(
        "---\ntitle: Test\ntype: sector\n---\n# Test Sector\n\n## Newsletter synthesis — Test\n\nbody.\n",
        encoding="utf-8",
    )
    # Monkeypatch PROJECT_ROOT so _company_title resolves against tmp_path.
    import maintenance.sync_sector_wikilinks as m

    orig_root = m.PROJECT_ROOT
    m.PROJECT_ROOT = tmp_path
    try:
        yield db_path, sectors_dir / "Test.md"
    finally:
        m.PROJECT_ROOT = orig_root
        conn.close()


def test_sync_sector_writes_complete_section(tmp_sector_db):
    db_path, sector_file = tmp_sector_db
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    changed, n = sync_sector(conn, sector_file, "Test", dry_run=False)
    assert changed
    assert n == 2  # Acme + Beta are in "Test"; Gamma is in "Other".
    text = sector_file.read_text(encoding="utf-8")
    # Links target the filename STEM (with pipe-aliased display title).
    assert "[[Acme_Corp|Acme Corp]]" in text
    assert "[[Beta_Inc|Beta Inc]]" in text
    assert "[[Gamma_Ltd" not in text  # different sector
    conn.close()


def test_sync_sector_is_idempotent(tmp_sector_db):
    db_path, sector_file = tmp_sector_db
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sync_sector(conn, sector_file, "Test", dry_run=False)
    # Second run: no change.
    changed, n = sync_sector(conn, sector_file, "Test", dry_run=False)
    assert not changed
    assert n == 2
    conn.close()


def test_sync_sector_check_mode_does_not_write(tmp_sector_db):
    db_path, sector_file = tmp_sector_db
    original = sector_file.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    changed, n = sync_sector(conn, sector_file, "Test", dry_run=True)
    assert changed  # reports stale
    assert n == 2
    # File untouched.
    assert sector_file.read_text(encoding="utf-8") == original
    conn.close()


def test_main_dry_run_default_writes_only_with_apply(tmp_sector_db, monkeypatch):
    """Guard unification (shared_routines_cli_guards W2): bare main() is a
    dry-run report; writes require --apply. --check keeps its advisory-gate
    contract (dry-run + rc 1 on stale)."""
    import maintenance.sync_sector_wikilinks as m

    db_path, sector_file = tmp_sector_db
    monkeypatch.setattr(m, "DB_PATH", db_path)
    monkeypatch.setattr(m, "SECTORS_DIR", sector_file.parent)
    original = sector_file.read_text(encoding="utf-8")

    assert m.main([]) == 0  # bare: dry-run default
    assert sector_file.read_text(encoding="utf-8") == original

    assert m.main(["--apply"]) == 0
    assert "[[Acme_Corp|Acme Corp]]" in sector_file.read_text(encoding="utf-8")


def test_sync_sector_link_targets_are_filename_stems(tmp_sector_db):
    """Every link TARGET (the part before ``|``, or the whole link if no pipe)
    must equal a real .md filename stem on disk — that's what Obsidian
    resolves against. This is the regression guard for issue 01: the old code
    used the YAML title as the target, producing 36 phantoms where title and
    stem diverge (HAL/Hindustan_Aeronautics, 3M India/Three_M_India, ...)."""
    db_path, sector_file = tmp_sector_db
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sync_sector(conn, sector_file, "Test", dry_run=False)
    text = sector_file.read_text(encoding="utf-8")
    _m = re.search(rf"{re.escape(_BEGIN)}.*?{re.escape(_END)}", text, re.DOTALL)
    assert _m is not None, "section not found in sector file"
    section = _m.group(0)
    links = re.findall(r"\[\[([^\]]+)\]\]", section)
    # The TARGET is the text before '|'.
    stems_on_disk = {
        p.stem for p in (tmp_sector_db[0].parent / "findata" / "Companies").rglob("*.md")
    }
    for link in links:
        target = link.split("|")[0]
        assert target in stems_on_disk, f"phantom link target: [[{target}]]"
    conn.close()


# ---------------------------------------------------------------------------
# Pure-function edge cases for _replace_or_insert
# ---------------------------------------------------------------------------
def test_replace_or_insert_adds_section_with_no_newline_prefix():
    """When text doesn't end with newline, one is added (line 170-173)."""
    import maintenance.sync_sector_wikilinks as m

    text = "# Some heading"  # no trailing newline, no Newsletter heading
    section = m._render_section([], "Test")
    new_text, changed = m._replace_or_insert(text, section)
    assert changed
    assert m._BEGIN in new_text


def test_replace_or_insert_adds_section_with_single_newline_prefix():
    """When text ends with single newline, add one more (line 170-171)."""
    import maintenance.sync_sector_wikilinks as m

    text = "# Some heading\n"
    section = m._render_section([], "Test")
    new_text, changed = m._replace_or_insert(text, section)
    assert changed
    assert "\n\n" + m._BEGIN in new_text


def test_find_insertion_point_no_newsletter_heading():
    """Falls back to end of file when Newsletter heading is absent (line 149)."""
    import maintenance.sync_sector_wikilinks as m

    text = "# No newsletter here\n\nSome content"
    idx = m._find_insertion_point(text)
    assert idx == len(text)


def test_find_insertion_point_at_newsletter_heading():
    """Returns offset of Newsletter synthesis heading."""
    import maintenance.sync_sector_wikilinks as m

    text = "# Title\n\nIntro\n\n## Newsletter synthesis\n\nStuff"
    idx = m._find_insertion_point(text)
    # Points to the start of "## Newsletter synthesis"
    assert text[idx:].startswith("## Newsletter synthesis")


# ---------------------------------------------------------------------------
# _company_title fallback
# ---------------------------------------------------------------------------
def test_company_title_missing_file():
    """Returns stem with spaces when file doesn't exist (line 81)."""
    import maintenance.sync_sector_wikilinks as m

    result = m._company_title("findata/Companies/F/Fake_Company.md")
    assert result == "Fake Company"


def test_company_title_no_title_in_yaml(tmp_path, monkeypatch):
    """Returns stem when YAML has no title field (line 86)."""
    import maintenance.sync_sector_wikilinks as m

    note = tmp_path / "Test_Co.md"
    note.write_text("---\ntype: company\n---\n\n# Body")
    monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
    result = m._company_title("Test_Co.md")
    assert result == "Test Co"
