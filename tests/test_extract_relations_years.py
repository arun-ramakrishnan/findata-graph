#!/usr/bin/env python3
"""Tests for helpers/graph/extract_relations.py — split from the
original test_extract_relations.py for navigability.

Year extraction, valid_from plumbing, and extraction guards.
"""
from __future__ import annotations


from helpers.graph.extract_relations import (  # noqa: E402
    Edge,
    EntityResolver,
    _extract_year_from_context,
    extract_relations,
)


# --------------------------------------------------------------------------- #
# Temporal context extraction (`valid_from` for `acquired` edges)             #
# --------------------------------------------------------------------------- #
class TestExtractYearFromContext:
    """Tests for `_extract_year_from_context` — the year/date extractor used
    to populate `valid_from` on `acquired` edges."""

    def test_explicit_year_only(self):
        # "in 2024" → year-only; iso_date defaults to YYYY-01-01.
        y, d = _extract_year_from_context(
            "Acquired by Anupam Rasayan for backward integration in 2024."
        )
        assert y == 2024
        assert d == "2024-01-01"

    def test_month_year(self):
        # "Dec 2025" → month captured; iso_date is YYYY-MM-01.
        y, d = _extract_year_from_context(
            "Completed 67% stake acquisition in Dec 2025."
        )
        assert y == 2025
        assert d == "2025-12-01"

    def test_long_month_name(self):
        # "October 2021" → month name captured.
        y, d = _extract_year_from_context(
            "acquired by Reliance Retail Ventures; effective October 2021"
        )
        assert y == 2021
        assert d == "2021-10-01"

    def test_fy_quarter_q4(self):
        # Q4 FY26 starts Jan 2026 (calendar year 2026).
        y, d = _extract_year_from_context("completed Q4 FY26 consolidation")
        assert y == 2026
        assert d == "2026-01-01"

    def test_fy_quarter_q1(self):
        # Q1 FY27 starts Apr 2026 (calendar year 2026).
        y, d = _extract_year_from_context("launched in Q1 FY27")
        assert y == 2026
        assert d == "2026-04-01"

    def test_fy_quarter_q3(self):
        # Q3 FY26 starts Oct 2025 (calendar year 2025).
        y, d = _extract_year_from_context("results from Q3 FY26")
        assert y == 2025
        assert d == "2025-10-01"

    def test_strips_yahoo_finance_attribution(self):
        # "Yahoo Finance, Jun 2026" must not be picked as the acquisition year.
        y, d = _extract_year_from_context(
            "Yahoo Finance, Jun 2026. acquired X in 2023"
        )
        assert y == 2023
        assert d == "2023-01-01"

    def test_no_year_returns_none(self):
        # No temporal signal at all.
        y, d = _extract_year_from_context(
            "Acquired Fintellix, a Bangalore-based RegTech company"
        )
        assert y is None
        assert d is None

    def test_relative_year_unresolved_returns_none(self):
        # "last year" / "this year" need edition context — return None for now.
        y, d = _extract_year_from_context(
            "Acquired August Electronics in Canada last year"
        )
        assert y is None
        assert d is None

    def test_empty_quote_returns_none(self):
        assert _extract_year_from_context("") == (None, None)
        assert _extract_year_from_context(None) == (None, None)

    def test_multiple_years_picks_earliest_plausible(self):
        # Two years in quote: "2021" and "2026" (current). Should pick 2021.
        y, d = _extract_year_from_context(
            "Acquired Eureka Forbes in 2021. Yahoo Finance as of Jun 2026."
        )
        # The Yahoo Finance clause is stripped, leaving only 2021.
        assert y == 2021
        assert d == "2021-01-01"

    def test_future_year_filtered(self):
        # Standalone year 2030 is in the future; should not be picked.
        y, d = _extract_year_from_context(
            "planning to acquire by 2030"  # forward-looking, not past-tense
        )
        # No plausible past year; return None.
        assert y is None
        assert d is None

    def test_year_below_plausibility_window_filtered(self):
        # 2010 is too old (outside the 2018-current_year window).
        y, d = _extract_year_from_context("acquired back in 2010")
        assert y is None
        assert d is None

    def test_current_year_acquisition_is_kept(self):
        # H2 fix: a current-year acquisition is a legitimately completed,
        # past-tense event and must NOT be filtered out. Regression guard
        # for the `< current_year` bug that silently dropped ESTEC's
        # "Acquired by Tata Technologies in 2026" (written in Jul 2026).
        from datetime import date
        cy = date.today().year
        y, d = _extract_year_from_context(
            f"Acquired by Tata Technologies in {cy} for market access."
        )
        assert y == cy, f"current-year ({cy}) acquisition must be kept"
        assert d == f"{cy:04d}-01-01"

    def test_current_year_picked_when_only_candidate(self):
        # When the current year is the ONLY candidate, it must be used
        # (previously returned None — the ESTEC failure mode).
        from datetime import date
        cy = date.today().year
        y, d = _extract_year_from_context(
            f"German engineering company. Acquired in {cy}."
        )
        assert y == cy
        assert d == f"{cy:04d}-01-01"


class TestAcquiredEdgeValidFrom:
    """End-to-end test: `acquired` edges carry `valid_from` and
    `properties.year` when the prose has a year."""

    def test_acquired_edge_with_explicit_year(self):
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "## Foo Limited | Large Cap | Sector\n\n"
            "Foo acquired Bar in 2024 for $100M.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test", newsletter_type="The_Chatter",
            resolver=resolver,
        )
        edge = by_type["acquired"][0]
        assert edge.source == "Foo"
        assert edge.target == "Bar"
        assert edge.valid_from == "2024-01-01"
        assert edge.properties["year"] == 2024

    def test_acquired_edge_with_month_year(self):
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "## Foo Limited | Large Cap | Sector\n\n"
            "Foo acquired Bar, with the deal closing in Dec 2025.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test", newsletter_type="The_Chatter",
            resolver=resolver,
        )
        edge = by_type["acquired"][0]
        assert edge.valid_from == "2025-12-01"
        assert edge.properties["year"] == 2025

    def test_acquired_edge_without_year_has_none(self):
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "## Foo Limited | Large Cap | Sector\n\n"
            "Foo acquired Bar last quarter.\n"  # no year/month/FY
        )
        by_type, _ = extract_relations(
            content, edition_title="Test", newsletter_type="The_Chatter",
            resolver=resolver,
        )
        edge = by_type["acquired"][0]
        assert edge.valid_from is None
        assert "year" not in edge.properties

    def test_non_acquired_edges_have_no_valid_from(self):
        # `subsidiary_of`, `jv_with`, etc. don't get temporal extraction.
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "## Foo Limited | Large Cap | Sector\n\n"
            "Foo is a subsidiary of Bar.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test", newsletter_type="The_Chatter",
            resolver=resolver,
        )
        edge = by_type["subsidiary_of"][0]
        # Only `acquired` edges get valid_from populated.
        assert edge.valid_from is None


class TestProseYearExtractionGuard:
    """The set of edge types for which `_extract_year_from_context` runs at
    extraction time is governed by
    `_EDGE_TYPES_WITH_PROSE_YEAR_EXTRACTION`. Today this is `{"acquired"}`
    only: a manual audit (2026-07) of the 5 other relation edge types
    found that applying the helper to non-`acquired` prose produces
    ~80% false positives (financial-statement "as of" dates, rename
    events, cross-sentence bleed). The `valid_from` *plumbing* stays
    type-agnostic — it's just the *automatic prose-mining* that's gated.
    """

    def test_frozenset_currently_contains_only_acquired(self):
        # Pin the current decision. If this asserts, the prose-mining set
        # was deliberately widened — update the audit docstring in
        # extract_relations.py to explain why.
        from helpers.graph.extract_relations import (
            _EDGE_TYPES_WITH_PROSE_YEAR_EXTRACTION,
        )
        assert _EDGE_TYPES_WITH_PROSE_YEAR_EXTRACTION == frozenset({"acquired"})

    def test_jv_with_year_in_quote_not_extracted(self):
        # Regression guard: BlackRock→Reliance case from the live DB. The
        # quote mentions "in 2023" (Jio launch year) and "Q3 FY26" (a
        # concall heading) — `_extract_year_from_context` would return
        # 2025-10-01 if applied, which is a cross-sentence false positive.
        # The guard prevents that.
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "## Foo Limited | Large Cap | Sector\n\n"
            "Foo announced a joint venture with Bar in 2023.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test", newsletter_type="The_Chatter",
            resolver=resolver,
        )
        edge = by_type["jv_with"][0]
        assert edge.valid_from is None
        assert "year" not in edge.properties

    def test_subsidiary_year_in_quote_not_extracted(self):
        # Regression guard: HDB→HDFC Bank case. Quote contains "as of
        # Mar 31, 2025" — a financial-statement date, not when the
        # subsidiary relationship began. Must not be lifted to valid_from.
        resolver = EntityResolver(["Foo", "Bar"])
        content = (
            "## Foo Limited | Large Cap | Sector\n\n"
            "Foo, a subsidiary of Bar, has ₹100B loan book as of Mar 31, 2025.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test", newsletter_type="The_Chatter",
            resolver=resolver,
        )
        edge = by_type["subsidiary_of"][0]
        assert edge.valid_from is None

    def test_edge_dataclass_accepts_valid_from_on_any_type(self):
        # The plumbing is type-agnostic even though prose-mining is gated.
        # `apply_edges` and the backfill scripts can set valid_from on any
        # edge type via properties.since or explicit construction.
        e = Edge(
            source="A", target="B", edge_type="jv_with",
            properties={"since": "2023"}, source_ref="seed",
            symmetric=True, valid_from="2023-01-01",
        )
        assert e.valid_from == "2023-01-01"
        assert e.edge_type == "jv_with"
