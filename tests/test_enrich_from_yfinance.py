"""Unit tests for helpers/maintenance/enrich_from_yfinance.py."""

from __future__ import annotations
import sys
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance.enrich_from_yfinance import (  # noqa: E402
    _auto_region_spans,
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
