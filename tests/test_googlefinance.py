#!/usr/bin/env python3
"""F1 tests - googlefinance.py quote-page parser (fixture-based, no network).

Fixtures: real saved pages under tests/fixtures/googlefinance/ (slimmed:
style blocks stripped). The BOGUS fixture is a dead-slug shell that still
returns HTTP 200 - parse_quote must reject it on content.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from helpers.maintenance.googlefinance import (  # noqa: E402
    _parse_number,
    name_match_score,
    parse_price,
    parse_quote,
    parse_stats,
    slug_candidates,
    yahoo_symbol_for_slug,
)

FIXTURES = (Path(__file__).resolve().parents[1] / "tests"
            / "fixtures" / "googlefinance")


def _load(fname: str) -> str:
    return (FIXTURES / fname).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ajax():
    return parse_quote(_load("quote_AJAXENGG_NSE.html"))


@pytest.fixture(scope="module")
def srigee():
    return parse_quote(_load("quote_544399_BOM.html"))


class TestParseQuote:
    def test_valid_page_yields_company_name(self, ajax):
        assert ajax is not None
        assert ajax["company_name"] == "AJAX Engineering Ltd"

    def test_stats_parsed_with_suffixes(self, ajax):
        stats = ajax["stats"]
        # Mkt. cap rendered as '65.78B' -> 65.78e9
        assert stats["mkt_cap"] == pytest.approx(65.78e9)
        assert stats["pe_ratio"] == pytest.approx(29.01)
        assert stats["wk52_high"] == pytest.approx(728.50)
        assert stats["wk52_low"] == pytest.approx(394.80)

    def test_profile_fields(self, ajax):
        profile = ajax["profile"]
        assert profile["employees"] == "487"
        assert profile["founded"] == "1992"
        assert profile["website"].endswith(".com")

    def test_srigee_bom_numeric_slug(self, srigee):
        # The motivating case: Yahoo-dead numeric BOM listing.
        assert srigee is not None
        assert srigee["company_name"] == "Srigee DLM Ltd"
        assert srigee["stats"]["pe_ratio"] == pytest.approx(7.30)
        assert srigee["stats"]["mkt_cap"] == pytest.approx(501.78e6)

    def test_dead_shell_rejected_on_content(self):
        # BOGUS:NSE serves HTTP 200 but has no About heading / stat rows.
        assert parse_quote(_load("quote_BOGUS_NSE.html")) is None


class TestParseNumber:
    @pytest.mark.parametrize("raw,expected", [
        ("79.10", 79.10),
        ("65.78B", 65.78e9),
        ("501.78M", 501.78e6),
        ("1,234.5", 1234.5),
        ("19.20K", 19_200.0),
        ("-", None),
        ("srigee.com", None),
    ])
    def test_number_forms(self, raw, expected):
        if expected is None:
            assert _parse_number(raw) is None
        else:
            assert _parse_number(raw) == pytest.approx(expected)

    def test_stat_pairs_from_lines(self):
        lines = ["P/E ratio", "29.01", "EPS", "-"]
        stats = parse_stats(lines)
        assert stats == {"pe_ratio": 29.01}


class TestNameMatchScore:
    def test_same_company_variants_score_high(self):
        assert name_match_score(
            "Srigee DLM Ltd", "Srigee DLM Limited") > 0.9
        assert name_match_score(
            "AJAX Engineering Ltd", "Ajax Engineering") > 0.75

    def test_unrelated_companies_score_low(self):
        assert name_match_score(
            "Bosch Limited", "Srigee DLM Ltd") < 0.4


class TestParsePrice:
    """S3: the current price lives in the quote header, not the stat rows."""

    def test_ajax_header_price(self, ajax):
        assert ajax["price"] == pytest.approx(575.00)

    def test_srigee_header_price(self, srigee):
        # ₹84 at fixture-save time (2026-08-25) — and the Sheets
        # GOOGLEFINANCE probe read exactly 84 the same day (cross-source
        # agreement); the proposal's ₹79.10 was the prior day's price.
        assert srigee["price"] == pytest.approx(84.0)

    def test_absent_price_is_none(self):
        assert parse_price(["₹", "About X", "575.00"]) is None


class TestSlugGrammar:
    """F2 tier-1 candidate generation + the G4 slug->Yahoo inverse."""

    def test_ns_ticker_native_exchange_first(self):
        assert slug_candidates("SRIGEE.NS") == ["SRIGEE:NSE", "SRIGEE:BOM"]

    def test_bo_ticker_native_exchange_first(self):
        # Numeric BOM scrip codes are the Srigee-class case GF serves and
        # Yahoo lacks (proposal §1).
        assert slug_candidates("544442.BO") == ["544442:BOM", "544442:NSE"]

    def test_bare_foreign_ticker_has_no_candidates(self):
        # US/foreign symbols (HBI, HYMTF) are out of GF-India scope; they
        # need tier 2 (F3) or a curated override.
        assert slug_candidates("HBI") == []

    def test_whitespace_and_case_normalised(self):
        assert slug_candidates(" srigee.ns ") == ["SRIGEE:NSE", "SRIGEE:BOM"]

    def test_yahoo_symbol_inverse(self):
        assert yahoo_symbol_for_slug("544399:BOM") == "544399.BO"
        assert yahoo_symbol_for_slug("AJAXENGG:NSE") == "AJAXENGG.NS"

    def test_yahoo_symbol_inverse_non_indian(self):
        assert yahoo_symbol_for_slug("HBI:NYSE") is None
        assert yahoo_symbol_for_slug("HBI") is None
