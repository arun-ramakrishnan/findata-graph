#!/usr/bin/env python3
"""Tests for helpers/maintenance/enrich_from_yfinance.py."""

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance.enrich_from_yfinance import (
    _format_value,
    _convert_value,
    _update_frontmatter,
    _insert_profile_section,
    _PROFILE_BEGIN,
    _PROFILE_END,
    extract_metrics,
    extract_profile,
    render_profile_block,
    write_metrics,
    get_enriched_companies,
    SOURCE_REF,
)


# ---------------------------------------------------------------------------
# Value formatting / conversion
# ---------------------------------------------------------------------------


class TestFormatValue:
    def test_percent_decimal(self):
        assert _format_value(0.338, "operating_margin") == "33.8%"

    def test_percent_already_scaled(self):
        assert _format_value(33.8, "operating_margin") == "33.8%"

    def test_crore_inr(self):
        # yfinance returns raw INR; we convert to crore
        assert _format_value(4791300000000, "market_capitalization") == "₹479,130 Cr"

    def test_ratio(self):
        assert _format_value(24.0, "pe_ratio") == "24.0"

    def test_dimensionless(self):
        assert _format_value(0.9, "beta") == "0.90"


class TestConvertValue:
    def test_percent_decimal_to_scaled(self):
        assert _convert_value(0.338, "operating_margin") == pytest.approx(33.8)

    def test_percent_already_scaled(self):
        assert _convert_value(33.8, "operating_margin") == pytest.approx(33.8)

    def test_crore_inr_conversion(self):
        # ₹4,791,300,000,000 → ₹479,130 Cr
        assert _convert_value(4791300000000, "market_capitalization") == 479130.0

    def test_ratio_unchanged(self):
        assert _convert_value(24.0, "pe_ratio") == 24.0


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

SAMPLE_INFO = {
    "longName": "Test Company Ltd",
    "sector": "Technology",
    "industry": "Software - Application",
    "financialCurrency": "INR",
    "marketCap": 10000000000,  # ₹1,000 Cr
    "totalRevenue": 5000000000,  # ₹500 Cr
    "grossMargins": 0.40,
    "operatingMargins": 0.20,
    "profitMargins": 0.15,
    "debtToEquity": 10.5,
    "trailingPE": 25.0,
    "priceToBook": 3.5,
    "beta": 0.8,
    "revenueGrowth": 0.15,
    "earningsGrowth": 0.10,
    "heldPercentInsiders": 0.55,
    "heldPercentInstitutions": 0.25,
    "fullTimeEmployees": 5000,
    "longBusinessSummary": "A test company.",
}


class TestExtractMetrics:
    def test_extracts_all_fields(self):
        metrics = extract_metrics("Test Company", SAMPLE_INFO)
        labels = {m["metric_label"] for m in metrics}
        assert "market_capitalization" in labels
        assert "total_revenue" in labels
        assert "gross_margin" in labels
        assert "operating_margin" in labels
        assert "pe_ratio" in labels
        assert len(metrics) == 13

    def test_each_metric_has_source_ref(self):
        metrics = extract_metrics("Test Company", SAMPLE_INFO)
        for m in metrics:
            assert m["source_ref"] == SOURCE_REF

    def test_percent_conversion(self):
        metrics = extract_metrics("Test Company", SAMPLE_INFO)
        for m in metrics:
            if m["metric_label"] == "operating_margin":
                assert m["value_num"] == 20.0
                assert m["value_raw"] == "20.0%"

    def test_crore_conversion(self):
        metrics = extract_metrics("Test Company", SAMPLE_INFO)
        for m in metrics:
            if m["metric_label"] == "market_capitalization":
                assert m["value_num"] == 1000.0
                assert m["unit"] == "crore_inr"

    def test_usd_currency_conversion(self):
        info = dict(SAMPLE_INFO)
        info["financialCurrency"] = "USD"
        info["totalRevenue"] = 2000000000  # $2B
        metrics = extract_metrics("Test Company", info)
        for m in metrics:
            if m["metric_label"] == "total_revenue":
                # $2B × 83 = ₹166B → ₹16,600 Cr
                assert m["value_num"] == pytest.approx(16600.0, rel=0.01)

    def test_skips_missing_fields(self):
        info = {"longName": "Co", "financialCurrency": "INR"}
        metrics = extract_metrics("Co", info)
        assert len(metrics) == 0


# ---------------------------------------------------------------------------
# Profile extraction / rendering
# ---------------------------------------------------------------------------


class TestExtractProfile:
    def test_extracts_structural_data(self):
        profile = extract_profile("Test Co", SAMPLE_INFO)
        assert profile is not None
        assert profile["industry"] == "Software - Application"
        assert profile["employees"] == 5000
        assert profile["promoter_holding"] == 0.55
        assert profile["institutional_holding"] == 0.25

    def test_returns_none_without_industry(self):
        info = dict(SAMPLE_INFO)
        del info["industry"]
        assert extract_profile("Test Co", info) is None

    def test_truncates_long_summary(self):
        info = dict(SAMPLE_INFO)
        info["longBusinessSummary"] = "x" * 500
        profile = extract_profile("Test Co", info)
        assert profile is not None
        assert len(profile["business_summary"]) <= 300


class TestRenderProfileBlock:
    def test_contains_sentinel_markers(self):
        profile = {
            "industry": "IT",
            "employees": 100,
            "promoter_holding": 0.5,
            "institutional_holding": 0.3,
            "business_summary": "Summary",
        }
        block = render_profile_block(profile)
        assert _PROFILE_BEGIN in block
        assert _PROFILE_END in block
        assert "## Company Profile (yfinance)" in block

    def test_formats_percentages(self):
        profile = {"industry": "IT", "promoter_holding": 0.45}
        block = render_profile_block(profile)
        assert "45.0%" in block


# ---------------------------------------------------------------------------
# Note updates
# ---------------------------------------------------------------------------

SAMPLE_NOTE = """---
title: Test Company
type: company
ticker: TEST.NS
sector: Technology
market_cap: large_cap
normalized_name: Test_Company
---

# Test Company

## Overview

Some overview text.

## Key Insights

- Something
"""


class TestUpdateFrontmatter:
    def test_adds_industry_after_sector(self):
        text = _update_frontmatter(SAMPLE_NOTE, "Software - Application")
        lines = text.split("\n")
        # industry should be right after sector
        for i, ln in enumerate(lines):
            if ln.startswith("sector:"):
                assert lines[i + 1] == "industry: Software - Application"
                break

    def test_updates_existing_industry(self):
        note = SAMPLE_NOTE.replace(
            "sector: Technology\n",
            "sector: Technology\nindustry: Old Industry\n",
        )
        text = _update_frontmatter(note, "New Industry")
        assert "industry: New Industry" in text
        assert "Old Industry" not in text

    def test_no_frontmatter_unchanged(self):
        note = "No frontmatter here"
        assert _update_frontmatter(note, "IT") == note


class TestInsertProfileSection:
    def test_replaces_existing_sentinel_block(self):
        note = SAMPLE_NOTE + "\n" + _PROFILE_BEGIN + "\nOld block\n" + _PROFILE_END + "\n"
        new_block = render_profile_block({"industry": "IT"})
        result = _insert_profile_section(note, new_block)
        assert "Old block" not in result
        assert "IT" in result
        # Only one sentinel pair
        assert result.count(_PROFILE_BEGIN) == 1

    def test_inserts_after_overview(self):
        new_block = render_profile_block({"industry": "IT"})
        result = _insert_profile_section(SAMPLE_NOTE, new_block)
        # Profile section should be between Overview and Key Insights
        profile_idx = result.index("Company Profile (yfinance)")
        overview_idx = result.index("## Overview")
        insights_idx = result.index("## Key Insights")
        assert overview_idx < profile_idx < insights_idx


# ---------------------------------------------------------------------------
# DB writes (in-memory DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_db():
    """In-memory DB with company_metrics + graph_edges tables."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE company_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity TEXT, metric_label TEXT, value_raw TEXT, value_num REAL,
        unit TEXT, period TEXT, as_of_edition TEXT, source_quote TEXT,
        source_ref TEXT, properties TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE graph_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
        weight REAL NOT NULL, properties TEXT NOT NULL,
        valid_from DATE, valid_to DATE, source_ref TEXT NOT NULL,
        symmetric INTEGER NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source, target, edge_type)
    )""")
    yield conn
    conn.close()


class TestWriteMetrics:
    def test_inserts_metrics(self, mem_db):
        metrics = [
            {
                "entity": "Co A",
                "metric_label": "pe_ratio",
                "value_raw": "20.0",
                "value_num": 20.0,
                "unit": "ratio",
                "period": "latest",
                "as_of_edition": None,
                "source_quote": None,
                "source_ref": SOURCE_REF,
                "properties": "{}",
            },
        ]
        count = write_metrics(mem_db, metrics)
        assert count == 1
        rows = mem_db.execute("SELECT entity, metric_label FROM company_metrics").fetchall()
        assert len(rows) == 1

    def test_delete_before_insert_idempotent(self, mem_db):
        metrics = [
            {
                "entity": "Co A",
                "metric_label": "pe_ratio",
                "value_raw": "20.0",
                "value_num": 20.0,
                "unit": "ratio",
                "period": "latest",
                "as_of_edition": None,
                "source_quote": None,
                "source_ref": SOURCE_REF,
                "properties": "{}",
            },
        ]
        write_metrics(mem_db, metrics)
        write_metrics(mem_db, metrics)  # second run
        rows = mem_db.execute("SELECT * FROM company_metrics WHERE entity='Co A'").fetchall()
        assert len(rows) == 1  # no duplicates

    def test_preserves_non_yfinance_metrics(self, mem_db):
        mem_db.execute(
            "INSERT INTO company_metrics (entity, metric_label, value_num, unit, source_ref, properties) "
            "VALUES ('Co A', 'revenue', 1000, 'crore', 'derive:metrics:xyz', '{}')"
        )
        metrics = [
            {
                "entity": "Co A",
                "metric_label": "pe_ratio",
                "value_raw": "20.0",
                "value_num": 20.0,
                "unit": "ratio",
                "period": "latest",
                "as_of_edition": None,
                "source_quote": None,
                "source_ref": SOURCE_REF,
                "properties": "{}",
            },
        ]
        write_metrics(mem_db, metrics)
        rows = mem_db.execute(
            "SELECT metric_label FROM company_metrics WHERE entity='Co A'"
        ).fetchall()
        labels = {r[0] for r in rows}
        assert "revenue" in labels  # original metric preserved
        assert "pe_ratio" in labels  # new yfinance metric added


class TestWriteCompetitorEdges:
    """E2 (2026-08-24) retired the v1 industry-clique path from this
    module; competes_with is now written by the KNN pass in
    enrich_relations.py (covered by its own topology tests). Pinned so
    the import-level contract stays honest."""

    def test_clique_path_retired(self):
        from helpers.maintenance import enrich_from_yfinance as ef

        assert not hasattr(ef, "write_competitor_edges")


class TestGetEnrichedCompanies:
    def test_detects_industry_tag(self, tmp_path):
        """Company with industry: in frontmatter is detected as enriched."""
        note = tmp_path / "Co A.md"
        note.write_text("---\ntitle: Co A\ntype: company\nindustry: Software\n---\n\n# Co A\n")
        result = get_enriched_companies([("Co A", str(note))])
        assert "Co A" in result

    def test_no_industry_tag_not_enriched(self, tmp_path):
        """Company without industry: is not enriched."""
        note = tmp_path / "Co B.md"
        note.write_text("---\ntitle: Co B\ntype: company\nsector: Tech\n---\n\n# Co B\n")
        result = get_enriched_companies([("Co B", str(note))])
        assert "Co B" not in result

    def test_missing_file_not_enriched(self):
        result = get_enriched_companies([("Co C", "/nonexistent/path.md")])
        assert "Co C" not in result

    def test_no_file_path_skipped(self):
        result = get_enriched_companies([("Co D", None)])
        assert "Co D" not in result
