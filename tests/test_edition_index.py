"""F0 (okf_activation proposal): shared canonical edition-key machinery.

Behavior here was lifted verbatim from backfill_okf_provenance (its 14
tests pin the consumer side); these tests pin the module itself,
including resolve_edition_string — the single-string entry point the
coverage report (C1) will use for the quotes.as_of_edition bridge.
"""

from pathlib import Path


from helpers.core.edition_index import (  # noqa: E402
    norm_key,
    note_title,
    resolve_edition_string,
    resolve_editions,
    source_note_index,
)


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    chatter = vault / "The_Chatter"
    chatter.mkdir(parents=True)
    (chatter / "Note_Alpha.md").write_text(
        "---\ntitle: The Chatter: Note Alpha\ntype: newsletter\n---\n# Note Alpha\n\nbody\n",
        encoding="utf-8",
    )
    (chatter / "Plain_Stem.md").write_text("# Plain Stem\n\nbody\n", encoding="utf-8")
    (chatter / "image_map.md").write_text("chrome\n", encoding="utf-8")
    (chatter / "images").mkdir()
    (chatter / "images" / "x.md").write_text("img chrome\n", encoding="utf-8")
    companies = vault / "Companies" / "Agri"
    companies.mkdir(parents=True)
    (companies / "Some_Company.md").write_text("# Some Company\n", encoding="utf-8")
    return vault


def test_norm_key_collapses_to_fuzzy_form():
    assert norm_key("The Chatter — Note #Alpha!") == "the chatter note alpha"
    assert norm_key("  Points &   Figures ") == "points figures"
    assert norm_key("…") == ""


def test_note_title_strips_yaml_quoting():
    # yaml.safe_load scalars: quotes are delimiters, not content — a raw
    # line grab used to keep them (they then landed in sources[].title
    # as '''…''' soup once merged_sources started converging entries).
    assert note_title("---\ntitle: 'The Chatter: Quoted'\n---\n", "stem") == "The Chatter: Quoted"
    assert note_title('---\ntitle: "Dq Title"\n---\n', "stem") == "Dq Title"
    # unquoted-with-colon is invalid YAML mapping syntax — raw fallback
    assert note_title("---\ntitle: The Chatter: Raw\n---\n", "stem") == "The Chatter: Raw"
    # heading / stem fallbacks unchanged
    assert note_title("---\n---\n# Heading Title\n", "stem") == "Heading Title"
    assert note_title("no frontmatter, no heading", "Stem_Fallback") == "Stem_Fallback"


def test_source_note_index_keys_stem_title_and_colon_tail(tmp_path):
    index = source_note_index(_make_vault(tmp_path))
    chatter = tmp_path / "vault" / "The_Chatter"
    assert index[norm_key("Note_Alpha")] == chatter / "Note_Alpha.md"
    # full title and post-colon tail keys hit the same note
    assert index[norm_key("The Chatter: Note Alpha")] == chatter / "Note_Alpha.md"
    assert index[norm_key("Note Alpha")] == chatter / "Note_Alpha.md"
    # chrome + images skipped; derived trees never indexed
    assert not any("image_map" in str(p) or "images" in p.parts for p in index.values())
    assert not any("Companies" in p.parts for p in index.values())


def test_resolve_edition_string_variant_forms(tmp_path):
    index = source_note_index(_make_vault(tmp_path))
    chatter = tmp_path / "vault" / "The_Chatter"
    assert resolve_edition_string("Note_Alpha", index) == chatter / "Note_Alpha.md"
    assert resolve_edition_string("The Chatter — Note Alpha", index) == (chatter / "Note_Alpha.md")
    assert resolve_edition_string("Note Alpha, Edition #3", index) == (chatter / "Note_Alpha.md")
    assert resolve_edition_string("Note Alpha, Zerodha", index) == (chatter / "Note_Alpha.md")
    assert resolve_edition_string("Note Alpha, Aug 2026", index) == (chatter / "Note_Alpha.md")
    # containment fallback for a long candidate against a shorter key
    assert resolve_edition_string("Plain Stem special extended weekend edition digest", index) == (
        chatter / "Plain_Stem.md"
    )


def test_resolve_edition_string_misses_resolve_to_none(tmp_path):
    index = source_note_index(_make_vault(tmp_path))
    assert resolve_edition_string("Yahoo Finance", index) is None
    assert resolve_edition_string("yfinance data", index) is None
    assert resolve_edition_string("", index) is None


def test_resolve_editions_headings_and_footers(tmp_path):
    index = source_note_index(_make_vault(tmp_path))
    body = (
        "prose\n\n"
        "## The Chatter — Note Alpha\n\nquote\n\n"
        "## The Chatter — Yahoo Finance\n\nother\n\n"
        "*Source: Plain Stem*\n"
    )
    resolved = resolve_editions(body, index)
    chatter = tmp_path / "vault" / "The_Chatter"
    assert resolved == [chatter / "Note_Alpha.md", chatter / "Plain_Stem.md"]


def test_source_note_index_warms_title_memo(tmp_path: Path) -> None:
    """graph_db_optimization Issue 4 A: the index build already reads +
    titles every source note; warming _TITLE_MEMO there means
    edition_source_entry never re-reads them (~1,400 reads per
    derive_insights run eliminated)."""
    import helpers.core.edition_index as ei

    vault = _make_vault(tmp_path)
    saved = dict(ei._TITLE_MEMO)
    ei._TITLE_MEMO.clear()
    try:
        ei.source_note_index(vault)
        alpha = vault / "The_Chatter" / "Note_Alpha.md"
        assert str(alpha) in ei._TITLE_MEMO
        assert ei._TITLE_MEMO[str(alpha)] == "The Chatter: Note Alpha"
        entry = ei.edition_source_entry(alpha, vault)
        assert entry["title"] == "The Chatter: Note Alpha"
        assert entry["id"] == "Note_Alpha"
    finally:
        ei._TITLE_MEMO.clear()
        ei._TITLE_MEMO.update(saved)
