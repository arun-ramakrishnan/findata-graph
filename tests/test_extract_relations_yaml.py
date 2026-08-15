#!/usr/bin/env python3
"""Tests for helpers/graph/extract_relations.py — split from the
original test_extract_relations.py for navigability.

YAML parsing, doc-type detection, company-note properties.
"""
from __future__ import annotations


from helpers.graph.extract_relations import (  # noqa: E402
    EntityResolver,
    _detect_doc_type,
    _make_properties,
    _parse_yaml_field,
    _resolve_h1_title,
    _strip_yaml_front_matter,
    extract_relations,
)


# --------------------------------------------------------------------------- #
# Document-type detection & company-note helpers                              #
# --------------------------------------------------------------------------- #
class TestDocTypeDetection:
    """Tests for `_detect_doc_type`, `_strip_yaml_front_matter`, and the
    `_parse_yaml_field` helper."""

    def test_newsletter_has_no_yaml(self):
        # Newsletter source files start with `# Edition Title` directly.
        content = "# The Chatter: Foo\n\n## Foo Limited | Large Cap\nBody."
        assert _detect_doc_type(content) == "newsletter"

    def test_company_yaml_detected(self):
        content = (
            "---\n"
            "title: Foo Limited\n"
            "type: company\n"
            "normalized_name: Foo\n"
            "---\n\n"
            "# Foo Limited\n\nBody."
        )
        assert _detect_doc_type(content) == "company"

    def test_sector_yaml_detected(self):
        content = (
            "---\n"
            "title: Automotive\n"
            "type: sector\n"
            "---\n\n"
            "# Automotive\n\nOverview."
        )
        assert _detect_doc_type(content) == "sector"

    def test_other_type_falls_back_to_newsletter(self):
        content = (
            "---\n"
            "type: research\n"
            "---\n\n"
            "# Some Doc\n"
        )
        assert _detect_doc_type(content) == "newsletter"

    def test_strip_yaml_front_matter(self):
        content = (
            "---\n"
            "title: Foo\n"
            "type: company\n"
            "---\n\n"
            "# Foo\n\nBody text."
        )
        stripped = _strip_yaml_front_matter(content)
        assert stripped.startswith("# Foo")
        assert "title:" not in stripped

    def test_strip_yaml_noop_when_absent(self):
        content = "# Just a heading and body.\nNo YAML here."
        assert _strip_yaml_front_matter(content) == content

    def test_parse_yaml_field_simple(self):
        content = (
            "---\n"
            "title: Foo\n"
            "type: company\n"
            "normalized_name: Foo_Limited\n"
            "---\n\n"
            "# Foo"
        )
        assert _parse_yaml_field(content, "type") == "company"
        assert _parse_yaml_field(content, "title") == "Foo"
        assert _parse_yaml_field(content, "normalized_name") == "Foo_Limited"

    def test_parse_yaml_field_quoted(self):
        content = (
            "---\n"
            'title: "Foo Limited"\n'
            "---\n\n"
            "# Foo"
        )
        assert _parse_yaml_field(content, "title") == "Foo Limited"

    def test_parse_yaml_field_absent(self):
        content = "---\ntitle: Foo\n---\n\n# Foo"
        assert _parse_yaml_field(content, "missing") is None

    def test_parse_yaml_field_no_front_matter(self):
        content = "# Foo\nNo YAML."
        assert _parse_yaml_field(content, "type") is None


class TestCompanyNoteExtraction:
    """Integration tests for doc_type='company' extraction."""

    def test_resolve_h1_title_strips_suffix(self):
        resolver = EntityResolver(["Foo", "Bar"])
        assert _resolve_h1_title("# Foo Limited\nBody.", resolver) == "Foo"
        assert _resolve_h1_title("# Bar\nBody.", resolver) == "Bar"
        assert _resolve_h1_title("# Unknown Company\nBody.", resolver) is None

    def test_resolve_h1_title_returns_none_when_no_h1(self):
        resolver = EntityResolver(["Foo"])
        assert _resolve_h1_title("No heading here.", resolver) is None

    def test_company_note_subsidiary_of(self):
        resolver = EntityResolver(["HDB Financial Services", "HDFC Bank"])
        content = (
            "---\n"
            "title: HDB Financial Services\n"
            "type: company\n"
            "normalized_name: HDB_Financial_Services\n"
            "---\n\n"
            "# HDB Financial Services\n\n"
            "## Overview\n\n"
            "HDB Financial Services Ltd., a subsidiary of HDFC Bank, is India's "
            "7th largest retail-focused NBFC.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="HDB_Financial_Services",
            newsletter_type="The_Chatter", resolver=resolver,
            doc_type="company",
            source_entity_override="HDB Financial Services",
        )
        assert "subsidiary_of" in by_type
        edge = by_type["subsidiary_of"][0]
        assert edge.source == "HDB Financial Services"
        assert edge.target == "HDFC Bank"
        # source_ref should reflect the company-note provenance.
        assert edge.source_ref == (
            "derive:relations:company_note:HDB Financial Services"
        )
        # properties.doc_type must be 'company' and quote carried.
        assert edge.properties["doc_type"] == "company"
        assert edge.properties["note"] == "HDB_Financial_Services"
        assert "subsidiary" in edge.properties["quote"]

    def test_company_note_properties_omit_newsletter_key(self):
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "---\ntype: company\nnormalized_name: Foo\n---\n\n"
            "# Foo\n\nFoo, a subsidiary of Bar."
        )
        by_type, _ = extract_relations(
            content, edition_title="Foo", newsletter_type="The_Chatter",
            resolver=resolver, doc_type="company",
            source_entity_override="Foo",
        )
        edge = by_type["subsidiary_of"][0]
        # Company-note properties must NOT carry the `newsletter` key
        # (only `note`, `doc_type`, `quote`).
        assert "newsletter" not in edge.properties
        assert edge.properties["doc_type"] == "company"

    def test_company_note_h1_fallback_when_override_none(self):
        # If source_entity_override is None, fall back to resolving the H1.
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "---\ntype: company\nnormalized_name: Foo\n---\n\n"
            "# Foo\n\nFoo is a subsidiary of Bar."
        )
        by_type, _ = extract_relations(
            content, edition_title="Foo", newsletter_type="The_Chatter",
            resolver=resolver, doc_type="company",
            source_entity_override=None,
        )
        assert by_type["subsidiary_of"][0].source == "Foo"

    def test_company_note_unresolvable_h1_returns_empty(self):
        resolver = EntityResolver(["Bar"])  # Foo not in DB
        content = (
            "---\ntype: company\nnormalized_name: Some_Unknown\n---\n\n"
            "# Some Unknown\n\nFoo, a subsidiary of Bar."
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Some_Unknown",
            newsletter_type="The_Chatter", resolver=resolver,
            doc_type="company", source_entity_override=None,
        )
        assert by_type == {}
        assert unresolved == []

    def test_company_note_does_not_derive_same_group(self):
        # Company notes are single-section; there's nothing to cluster.
        resolver = EntityResolver(["UltraTech Cement"])
        content = (
            "---\ntype: company\nnormalized_name: UltraTech_Cement\n---\n\n"
            "# UltraTech Cement\n\n"
            "UltraTech, part of the Aditya Birla Group, is a leading cement maker."
        )
        by_type, _ = extract_relations(
            content, edition_title="UltraTech_Cement",
            newsletter_type="The_Chatter", resolver=resolver,
            doc_type="company",
            source_entity_override="UltraTech Cement",
        )
        # same_group requires >=2 resolved entities in the same group; a
        # single-section company note can't produce that.
        assert "same_group" not in by_type


class TestNewsletterVsCompanyAuditTrail:
    """Verify the doc_type dispatch produces distinct audit trails."""

    def test_newsletter_edge_carries_newsletter_key(self):
        resolver = EntityResolver(["Jio Financial Services", "BlackRock"])
        content = (
            "## Jio Financial Services Limited | Large Cap | Financial Services\n\n"
            "The asset management joint venture with BlackRock is growing.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
            doc_type="newsletter",
        )
        edge = by_type["jv_with"][0]
        assert edge.properties["doc_type"] == "newsletter"
        assert edge.properties["newsletter"] == "The_Chatter"
        assert edge.properties["edition"] == "Test Edition"
        assert edge.source_ref == "derive:relations:The_Chatter"

    def test_company_edge_uses_distinct_source_ref(self):
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "---\ntype: company\nnormalized_name: Foo\n---\n\n"
            "# Foo\n\nFoo, a subsidiary of Bar."
        )
        by_type, _ = extract_relations(
            content, edition_title="Foo", newsletter_type="The_Chatter",
            resolver=resolver, doc_type="company",
            source_entity_override="Foo",
        )
        edge = by_type["subsidiary_of"][0]
        assert edge.source_ref.startswith("derive:relations:company_note:")

    def test_make_properties_company_vs_newsletter(self):
        p_company = _make_properties("Note", "The_Chatter", "company", "q")
        p_news = _make_properties("Edition", "The_Chatter", "newsletter", "q")
        assert p_company == {"note": "Note", "doc_type": "company", "quote": "q"}
        assert p_news == {
            "edition": "Edition", "newsletter": "The_Chatter",
            "doc_type": "newsletter", "quote": "q",
        }

    def test_make_properties_with_year(self):
        # When year is provided, it's added as `properties.year`.
        p = _make_properties("Ed", "The_Chatter", "newsletter", "q", year=2025)
        assert p["year"] == 2025
        # Without year, the key is absent.
        p_no = _make_properties("Ed", "The_Chatter", "newsletter", "q")
        assert "year" not in p_no




# ===========================================================================
# Additional unit tests for pure helper functions
# ===========================================================================

from helpers.graph.extract_relations import (  # noqa: E811
    _tokens,
    _looks_like_speaker,
    _split_sections,
)


# ---------------------------------------------------------------------------
# _tokens
# ---------------------------------------------------------------------------
def test_tokens_strips_punctuation():
    result = _tokens("Infosys, Ltd.")
    assert isinstance(result, frozenset)
    assert "infosys" in result
    assert "ltd" not in result  # stopword


def test_tokens_stops_empty():
    result = _tokens("")
    assert result == frozenset()


def test_tokens_normalizes_case():
    result = _tokens("TATA Motors")
    assert "tata" in result
    assert "motors" in result


def test_tokens_strips_non_alnum():
    result = _tokens("360-ONE")
    assert "360" in result
    assert "one" in result


# ---------------------------------------------------------------------------
# _looks_like_speaker
# ---------------------------------------------------------------------------
def test_looks_like_speaker_ceo():
    assert _looks_like_speaker("John Smith, CEO") is True


def test_looks_like_speaker_cfo():
    assert _looks_like_speaker("Vineet Agrawal, Group CFO") is True


def test_looks_like_speaker_managing_director():
    assert _looks_like_speaker("Hitesh Sethia, Managing Director") is True


def test_looks_like_speaker_no_comma():
    assert _looks_like_speaker("Infosys Limited") is False


def test_looks_like_speaker_no_role():
    assert _looks_like_speaker("John Smith, Random Title") is False


# ---------------------------------------------------------------------------
# _parse_yaml_field
# ---------------------------------------------------------------------------
def test_parse_yaml_field_basic():
    content = "---\ntitle: Test Co\ntype: company\n---\n\n# Body"
    assert _parse_yaml_field(content, "title") == "Test Co"


def test_parse_yaml_field_quoted():
    content = '---\ntitle: "Test Co"\n---\nBody'
    assert _parse_yaml_field(content, "title") == "Test Co"


def test_parse_yaml_field_absent():
    content = "---\ntitle: Test Co\n---\nBody"
    assert _parse_yaml_field(content, "sector") is None


def test_parse_yaml_field_no_frontmatter():
    content = "# Just a heading"
    assert _parse_yaml_field(content, "title") is None


def test_parse_yaml_field_single_quoted():
    content = "---\ntitle: 'Test Co'\n---\nBody"
    assert _parse_yaml_field(content, "title") == "Test Co"


# ---------------------------------------------------------------------------
# _detect_doc_type
# ---------------------------------------------------------------------------
def test_detect_doc_type_company():
    content = "---\ntype: company\n---\n\n# Body"
    assert _detect_doc_type(content) == "company"


def test_detect_doc_type_sector():
    content = "---\ntype: sector\n---\n\n# Body"
    assert _detect_doc_type(content) == "sector"


def test_detect_doc_type_newsletter():
    content = "# Newsletter without YAML"
    assert _detect_doc_type(content) == "newsletter"


def test_detect_doc_type_unknown_type():
    content = "---\ntype: super_sector\n---\nBody"
    assert _detect_doc_type(content) == "newsletter"


# ---------------------------------------------------------------------------
# _make_properties
# ---------------------------------------------------------------------------
def test_make_properties_company():
    props = _make_properties("Test_Co", "rn", "company", "A quote", 2026)
    assert props["doc_type"] == "company"
    assert props["note"] == "Test_Co"
    assert props["quote"] == "A quote"
    assert props["year"] == 2026


def test_make_properties_newsletter():
    props = _make_properties("RNS #123", "rns", "newsletter", "A quote")
    assert props["doc_type"] == "newsletter"
    assert props["edition"] == "RNS #123"
    assert props["newsletter"] == "rns"
    assert "year" not in props


def test_make_properties_no_year():
    props = _make_properties("X", "rn", "newsletter", "q")
    assert "year" not in props


# ---------------------------------------------------------------------------
# _split_sections — basic structure
# ---------------------------------------------------------------------------
def test_split_sections_single_company():
    content = (
        "## Company A | Large Cap | Energy\n\n"
        "Some body text about A.\n\n"
        "## Company B | Mid Cap | Tech\n\n"
        "Body about B.\n"
    )
    sections = _split_sections(content)
    assert len(sections) >= 2
    names = [s[0] for s in sections]
    assert "Company A" in names
    assert "Company B" in names


def test_split_sections_strips_suffix():
    content = "## Tata Motors Ltd | Large Cap | Auto\n\nBody.\n"
    sections = _split_sections(content)
    assert sections[0][0] == "Tata Motors"


def test_split_sections_filters_speakers():
    content = (
        "## Reliance | Large Cap | Energy\n\n"
        "Some text.\n\n"
        "## John Smith, CEO\n\n"
        "Speaker section.\n"
    )
    sections = _split_sections(content)
    names = [s[0] for s in sections]
    assert "Reliance" in names
    assert "John Smith, CEO" not in names
