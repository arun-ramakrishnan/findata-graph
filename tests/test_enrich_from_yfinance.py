"""Unit tests for helpers/maintenance/enrich_from_yfinance.py."""

from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

import helpers.maintenance.enrich_from_yfinance as enf  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from helpers.maintenance.enrich_from_yfinance import (  # noqa: E402
    _auto_region_spans,
    _name_tokens,
    identity_ok,
    load_aliases,
    _format_value,
    _convert_value,
    _outside_auto_region,
    extract_metrics,
    extract_profile,
    render_profile_block,
    _update_frontmatter,
    _insert_profile_section,
    write_metrics,
    get_stale_companies,
    get_enriched_companies,
    SOURCE_REF,
)


# ---------------------------------------------------------------------------
# _format_value
# ---------------------------------------------------------------------------
def test_format_value_percent_below_1():
    assert _format_value(0.338, "gross_margin") == "33.8%"


def test_format_value_percent_above_1():
    assert _format_value(33.8, "gross_margin") == "33.8%"


def test_format_value_crore_inr():
    result = _format_value(3_38_00_00_000, "total_revenue")
    assert "Cr" in result


def test_format_value_ratio():
    result = _format_value(25.5, "pe_ratio")
    assert result == "25.5"


def test_format_value_dimensionless():
    result = _format_value(1.15, "beta")
    assert result == "1.15"


# ---------------------------------------------------------------------------
# _convert_value
# ---------------------------------------------------------------------------
def test_convert_value_percent():
    assert _convert_value(0.338, "gross_margin") == pytest.approx(33.8)


def test_convert_value_crore_inr():
    assert _convert_value(1e7, "total_revenue") == pytest.approx(1.0)  # 1e7 INR = 1 Cr


def test_convert_value_ratio_unchanged():
    assert _convert_value(25.5, "pe_ratio") == 25.5


def test_convert_value_dimensionless_unchanged():
    assert _convert_value(1.15, "beta") == 1.15


# ---------------------------------------------------------------------------
# _outside_auto_region
# ---------------------------------------------------------------------------
def test_outside_auto_region_outside():
    text = "Hello world\n## Heading\nMore text"
    assert _outside_auto_region(text, 5) == 5


def test_outside_auto_region_inside():
    text = "<!-- BEGIN auto foo -->\ncontent\n<!-- END auto foo -->\nrest"
    # pos points inside the auto region — should return the region start
    start = text.find("<!-- BEGIN auto foo -->")
    result = _outside_auto_region(text, start + 10)
    assert result == start


def test_outside_auto_region_nested_regions():
    """2026-08-19 regression: regions nest (a chatter region encloses the
    key-figures region). A non-greedy BEGIN.*?END pairs the outer BEGIN with
    the FIRST inner END, so a pos between the inner END and the true outer
    END was considered 'outside' — profiles landed inside the chatter region
    (10 of 58 restored notes). The stack walk pairs markers correctly."""
    text = (
        "<!-- BEGIN auto chatter block (derive_insights.py) -->\n"
        "<!-- BEGIN auto key figures -->\n- figures\n<!-- END auto key figures -->\n"
        "## The Chatter — Ed\ncontent\n"
        "<!-- END auto chatter block -->\nrest"
    )
    outer_start = text.find("<!-- BEGIN auto chatter block")
    # pos sits AFTER the inner KF END but INSIDE the true chatter region.
    inner_end = (
        text.find("<!-- END auto key figures -->") + len("<!-- END auto key figures -->") + 2
    )
    assert _outside_auto_region(text, inner_end) == outer_start


def test_auto_region_spans_nested_maximal():
    text = (
        "<!-- BEGIN auto chatter block -->x"
        "<!-- BEGIN auto key figures -->y<!-- END auto key figures -->"
        "z<!-- END auto chatter block -->tail"
    )
    spans = _auto_region_spans(text)
    assert len(spans) == 1  # one maximal region, not a mis-paired partial
    s, e = spans[0]
    assert text[s:e].startswith("<!-- BEGIN auto chatter block -->")
    assert text[s:e].endswith("<!-- END auto chatter block -->")


# ---------------------------------------------------------------------------
# extract_metrics
# ---------------------------------------------------------------------------
def test_extract_metrics_basic():
    info = {"grossMargins": 0.45, "trailingPE": 30.0, "marketCap": 1e11}
    metrics = extract_metrics("Test Co", info)
    labels = {m["metric_label"] for m in metrics}
    assert "gross_margin" in labels
    assert "pe_ratio" in labels
    assert "market_capitalization" in labels


def test_extract_metrics_empty_info():
    assert extract_metrics("Test Co", {}) == []


def test_extract_metrics_none_values_skipped():
    info = {"grossMargins": None, "trailingPE": 30.0}
    metrics = extract_metrics("Test Co", info)
    labels = {m["metric_label"] for m in metrics}
    assert "gross_margin" not in labels
    assert "pe_ratio" in labels


def test_extract_metrics_usd_conversion():
    info = {"totalRevenue": 1e9, "financialCurrency": "USD"}
    metrics = extract_metrics("Test Co", info)
    rev = [m for m in metrics if m["metric_label"] == "total_revenue"][0]
    # USD 1e9 * 83 = 83e9, / 1e7 = 8300 Cr
    assert rev["value_num"] == pytest.approx(8300.0)


# ---------------------------------------------------------------------------
# extract_profile
# ---------------------------------------------------------------------------
def test_extract_profile_with_data():
    info = {
        "industry": "Software",
        "longBusinessSummary": "A great company.",
        "fullTimeEmployees": 5000,
        "heldPercentInsiders": 0.5,
        "heldPercentInstitutions": 0.3,
    }
    profile = extract_profile("Test Co", info)
    assert profile is not None
    assert profile["industry"] == "Software"
    assert profile["employees"] == 5000


def test_extract_profile_no_industry():
    assert extract_profile("Test Co", {}) is None


def test_extract_profile_truncates_long_summary():
    info = {"industry": "Tech", "longBusinessSummary": "x" * 500}
    profile = extract_profile("Test Co", info)
    assert profile is not None
    assert len(profile["business_summary"]) <= 300


# ---------------------------------------------------------------------------
# render_profile_block
# ---------------------------------------------------------------------------
def test_render_profile_block_full():
    profile = {
        "industry": "Banking",
        "employees": 10000,
        "promoter_holding": 0.6,
        "institutional_holding": 0.25,
        "business_summary": "A bank.",
    }
    block = render_profile_block(profile)
    assert "Industry**:** Banking" in block or "**Industry**: Banking" in block
    assert "10,000" in block
    assert "60.0%" in block
    assert "25.0%" in block


def test_render_profile_block_minimal():
    profile = {"industry": "Tech"}
    block = render_profile_block(profile)
    assert "**Industry**: Tech" in block
    assert "Employees" not in block


# ---------------------------------------------------------------------------
# _update_frontmatter
# ---------------------------------------------------------------------------
def test_update_frontmatter_add_industry():
    text = "---\ntitle: Foo\nticker: BAR.NS\nsector: Tech\n---\n\n# Foo"
    result = _update_frontmatter(text, "Software")
    assert "industry: Software" in result


def test_update_frontmatter_replace_industry():
    text = "---\ntitle: Foo\nindustry: Old\nsector: Tech\n---\n\n# Foo"
    result = _update_frontmatter(text, "New")
    assert "industry: New" in result
    assert "industry: Old" not in result


def test_update_frontmatter_no_frontmatter():
    text = "# Just a heading\nNo frontmatter"
    result = _update_frontmatter(text, "Software")
    assert result == text  # unchanged


# ---------------------------------------------------------------------------
# _insert_profile_section
# ---------------------------------------------------------------------------
def test_insert_profile_section_new():
    text = "---\ntitle: Foo\n---\n\n# Foo\n\n## Company Overview\n\nText.\n\n## Other\n\nMore."
    block = "BLOCK_CONTENT\n"
    result = _insert_profile_section(text, block)
    assert "BLOCK_CONTENT" in result


def test_insert_profile_section_replace():
    text = "---\ntitle: Foo\n---\n\n<!-- BEGIN auto company profile (enrich_from_yfinance.py) -->\nOld block\n<!-- END auto company profile -->\n"
    block = "NEW_BLOCK\n"
    result = _insert_profile_section(text, block)
    assert "NEW_BLOCK" in result
    assert "Old block" not in result


def test_insert_profile_section_no_heading_appends():
    text = "---\ntitle: Foo\n---\n\n# Foo\n\nNo headings here."
    block = "BLOCK_CONTENT\n"
    result = _insert_profile_section(text, block)
    assert "BLOCK_CONTENT" in result


# ---------------------------------------------------------------------------
# write_metrics — in-memory DB
# ---------------------------------------------------------------------------
def _make_metrics_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE company_metrics (
            entity TEXT, metric_label TEXT, value_raw TEXT, value_num REAL,
            unit TEXT, period TEXT, as_of_edition TEXT, source_quote TEXT,
            source_ref TEXT, properties TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    return conn


def test_write_metrics_inserts():
    conn = _make_metrics_db()
    metrics = [
        {
            "entity": "Co A",
            "metric_label": "pe_ratio",
            "value_raw": "25.0",
            "value_num": 25.0,
            "unit": "ratio",
            "period": "latest",
            "as_of_edition": None,
            "source_quote": None,
            "source_ref": SOURCE_REF,
            "properties": "{}",
        }
    ]
    inserted = write_metrics(conn, metrics)
    assert inserted == 1
    rows = conn.execute("SELECT * FROM company_metrics").fetchall()
    assert len(rows) == 1


def test_write_metrics_empty_returns_zero():
    conn = _make_metrics_db()
    assert write_metrics(conn, []) == 0


def test_write_metrics_replaces_old():
    conn = _make_metrics_db()
    metrics = [
        {
            "entity": "Co A",
            "metric_label": "pe_ratio",
            "value_raw": "25.0",
            "value_num": 25.0,
            "unit": "ratio",
            "period": "latest",
            "as_of_edition": None,
            "source_quote": None,
            "source_ref": SOURCE_REF,
            "properties": "{}",
        }
    ]
    write_metrics(conn, metrics)
    # Second write should replace
    metrics[0]["value_num"] = 30.0
    write_metrics(conn, metrics)
    rows = conn.execute("SELECT value_num FROM company_metrics").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 30.0


# ---------------------------------------------------------------------------
# write_competitor_edges was RETIRED in E2 (2026-08-24) — the dead clique
# path never effectively applied and is superseded by the bounded-KNN
# topology in helpers/maintenance/enrich_relations.py (see
# tests/test_enrich_relations.py for its coverage).


# ---------------------------------------------------------------------------
# get_stale_companies
# ---------------------------------------------------------------------------
def test_get_stale_companies_zero_returns_empty():
    conn = _make_metrics_db()
    assert get_stale_companies(conn, [], 0) == set()


# ---------------------------------------------------------------------------
# get_enriched_companies
# ---------------------------------------------------------------------------
def test_get_enriched_companies(tmp_path, monkeypatch):
    import helpers.maintenance.enrich_from_yfinance as mod

    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    # Create a note with industry
    note = tmp_path / "Co_A.md"
    note.write_text("---\ntitle: Co A\nindustry: Banking\n---\n\n# Co A")
    result = get_enriched_companies([("Co A", "Co_A.md")])
    assert "Co A" in result


def test_get_enriched_companies_none(tmp_path, monkeypatch):
    import helpers.maintenance.enrich_from_yfinance as mod

    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    note = tmp_path / "Co_B.md"
    note.write_text("---\ntitle: Co B\n---\n\n# Co B")
    result = get_enriched_companies([("Co B", "Co_B.md")])
    assert "Co B" not in result


# ---------------------------------------------------------------------------
# identity guard (industry_coding_completion S2)
# ---------------------------------------------------------------------------
def test_name_tokens_strips_corporate_suffixes():
    assert _name_tokens("DCB Bank Limited") == {"dcb", "bank"}
    assert _name_tokens("Power Grid Corporation of India") == {"power", "grid", "india"}


def test_identity_ok_short_exchange_spelling_passes():
    assert identity_ok("DCB Bank", {"longName": "DCB Bank Limited"})
    assert identity_ok("Power Grid Corporation of India", {"shortName": "Power Grid Corp"})


def test_name_tokens_folds_accents_and_plurals():
    # S1 hygiene pass: live Yahoo longNames differ from note names exactly here
    assert _name_tokens("Shangar Decor") == _name_tokens("Shangar Décor Limited")
    assert _name_tokens("Lambodhara Textile") == _name_tokens("Lambodhara Textiles Limited")


def test_identity_ok_accent_apostrophe_plural_passes():
    assert identity_ok("Sai Parenterals", {"longName": "Sai Parenteral's Limited"})
    assert identity_ok("Shangar Decor", {"longName": "Shangar Décor Limited"})
    assert identity_ok("Lambodhara Textile", {"longName": "Lambodhara Textiles Limited"})
    assert identity_ok("Venkys (India)", {"longName": "Venky's (India) Limited"})


def test_identity_ok_still_blocks_different_stems():
    # plural folding widens matching symmetrically — distinct stems never meet
    assert not identity_ok("Felix Industries", {"longName": "Felix Gold Limited"})
    assert not identity_ok(
        "IND Renewable Energy",
        {"longName": "Indian Renewable Energy Development Agency Limited"},
    )


def test_identity_ok_blocks_wrong_entity():
    # FELIX.NS resolved to the Australian explorer — the proven Gold poisoning
    assert not identity_ok("Felix Industries", {"longName": "Felix Gold Limited"})
    assert not identity_ok("Shriram Pistons", {"longName": "SPR Auto Technologies Limited"})


def test_identity_ok_alias_rescues_renames():
    aliases = {"Shriram Pistons": ["SPR Auto Technologies Limited"]}
    assert identity_ok("Shriram Pistons", {"longName": "SPR Auto Technologies Limited"}, aliases)


def test_identity_ok_unanchorable_name_refuses():
    assert not identity_ok("Felix Industries", {"longName": ""})
    assert not identity_ok("", {"longName": "Felix Industries"})


def test_load_aliases_missing_file_is_empty(tmp_path):
    assert load_aliases(tmp_path / "nope.json") == {}


class TestManifestEntities:
    """thin_tail S1b: --from-manifest selection (entity names + note paths)."""

    @pytest.fixture()
    def conn(self, tmp_path):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute(
            "CREATE TABLE entities(name TEXT PRIMARY KEY, entity_type TEXT,"
            " normalized_name TEXT, file_path TEXT, ticker TEXT)"
        )
        c.executemany(
            "INSERT INTO entities(name, entity_type, normalized_name, file_path, ticker)"
            " VALUES (?,?,?,?,?)",
            [
                ("MRF Ltd", "company", "MRF_Ltd", "findata/Companies/Auto/MRF.md", "MRF.NS"),
                ("KIOCL", "company", "KIOCL", "findata/Companies/Metals/KIOCL.md", "KIOCL.NS"),
                (
                    "Forbes Enviro",
                    "company",
                    "Forbes_Enviro",
                    "findata/Companies/Misc/Forbes_Enviro.md",
                    None,
                ),
            ],
        )
        c.commit()
        return c

    def _manifest(self, tmp_path, body, monkeypatch):
        mf = tmp_path / "mf.txt"
        mf.write_text(body, encoding="utf-8")
        monkeypatch.setattr(enf, "PROJECT_ROOT", tmp_path)
        return mf

    def test_names_paths_and_comments(self, tmp_path, monkeypatch, conn):
        mf = self._manifest(
            tmp_path,
            "# s1b manifest\n\nMRF Ltd\nfindata/Companies/Metals/KIOCL.md\n",
            monkeypatch,
        )
        assert enf._manifest_entities(conn, str(mf)) == ["MRF Ltd", "KIOCL"]

    def test_dropped_unresolvable_path(self, tmp_path, monkeypatch, conn, caplog):
        mf = self._manifest(
            tmp_path,
            "findata/Companies/Ghost/Ghost.md\nMRF Ltd\n",
            monkeypatch,
        )
        assert enf._manifest_entities(conn, str(mf)) == ["MRF Ltd"]
        assert "no entity row" in caplog.text

    def test_untickered_entity_still_listed(self, tmp_path, monkeypatch, conn):
        # Selection filter (ticker IS NOT NULL) lives in main(); the manifest
        # reader itself must pass the name through untouched.
        mf = self._manifest(tmp_path, "Forbes Enviro\n", monkeypatch)
        assert enf._manifest_entities(conn, str(mf)) == ["Forbes Enviro"]


class TestIdentityOkMinSubsetTokens:
    """thin_tail S1c: one-word note names are too weak an anchor for the
    ours<=theirs subset direction — 'Take' matched Take-Two, 'GEE' matched
    Geely under the default rule."""

    def test_one_word_subset_refused_at_two(self):
        assert not identity_ok(
            "Take",
            {"longName": "Take-Two Interactive Software, Inc."},
            aliases={},
            min_subset_tokens=2,
        )
        assert not identity_ok(
            "GEE",
            {"longName": "Geely Automobile Holdings Limited"},
            aliases={},
            min_subset_tokens=2,
        )

    def test_exact_single_token_equality_passes_at_two(self):
        # corporate suffixes are stripped by _name_tokens on both sides
        assert identity_ok(
            "PepsiCo", {"longName": "PepsiCo, Inc."}, aliases={}, min_subset_tokens=2
        )

    def test_multi_token_subset_still_passes_at_two(self):
        assert identity_ok(
            "HDFC Bank", {"longName": "HDFC Bank Limited"}, aliases={}, min_subset_tokens=2
        )

    def test_default_rule_unchanged(self):
        # legacy scrip-lane semantics: 1-token subset still passes
        assert identity_ok("Take", {"longName": "Take-Two Interactive Software, Inc."}, aliases={})

    def test_exchange_shorter_side_ungated(self):
        # theirs <= ours is the safe direction regardless of the knob
        assert identity_ok(
            "Power Grid Corporation of India",
            {"shortName": "Power Grid"},
            aliases={},
            min_subset_tokens=2,
        )
