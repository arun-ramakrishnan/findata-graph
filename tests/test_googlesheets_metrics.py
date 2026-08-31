#!/usr/bin/env python3
"""S3 tests — googlesheets_metrics.py batch client (no network, no
gspread import: client_factory is always faked)."""

from __future__ import annotations

import pytest

from helpers.maintenance.googlesheets_metrics import (  # noqa: E402
    fetch_gf_metrics,
    parse_cell,
    to_sheets_ticker,
)


class TestToSheetsTicker:
    def test_exchange_first(self):
        assert to_sheets_ticker("544399:BOM") == "BOM:544399"
        assert to_sheets_ticker("AJAXENGG:NSE") == "NSE:AJAXENGG"

    def test_bare_slug_passthrough(self):
        assert to_sheets_ticker("HBI") == "HBI"


class TestParseCell:
    def test_numbers(self):
        assert parse_cell(84) == 84.0
        assert parse_cell(501782400) == 501782400.0

    def test_numeric_strings_cleaned(self):
        assert parse_cell("1,234.5") == 1234.5
        assert parse_cell(" 7.3 ") == 7.3

    def test_error_text_is_none(self):
        assert (
            parse_cell(
                "#N/A (When evaluating GOOGLEFINANCE, the query "
                "for the symbol: 'SRIGEE' returned no data.)"
            )
            is None
        )
        assert parse_cell("") is None
        assert parse_cell(None) is None
        assert parse_cell(True) is None


class _FakeSheet:
    """Records the batch write; serves computed values after one poll."""

    def __init__(self, computed: dict):
        self.computed = computed
        self.ws = _FakeWS(self)

    @property
    def sheet1(self):
        return self.ws


class _FakeWS:
    def __init__(self, sheet):
        self.sheet = sheet
        self.update_calls: list[dict] = []
        self.polls = 0

    def clear(self):
        pass

    def update(self, *, values, range_name, raw=True):
        self.update_calls.append({"values": values, "range": range_name, "raw": raw})

    def get_values(self, _rng, value_render_option=None):
        self.polls += 1
        if self.polls == 1:
            return [["t", "a", ""], ["x", "y", ""]]  # not yet evaluated
        rows = [["ticker", "attribute", "value"]]
        for ticker, attr, v in self.sheet.computed[("grid", self.polls - 2)]:
            rows.append([ticker, attr, v])
        return rows


class _FakeClient:
    def __init__(self, sheet):
        self._sheet = sheet

    def open(self, _title):
        return self._sheet


class TestFetchGfMetrics:
    @staticmethod
    def _factory(results):
        sheet = _FakeSheet({})
        grid = []
        for (slug, attr), v in results.items():
            grid.append((to_sheets_ticker(slug), attr, v))
        sheet.computed[("grid", 0)] = grid
        return lambda: _FakeClient(sheet)

    def test_batch_round_trip(self):
        results = {
            ("544399:BOM", "pe"): 7.3,
            ("544399:BOM", "marketcap"): 501782400,
            ("NSE:SRIGEE", "price"): "#N/A no data",
        }
        out = fetch_gf_metrics(
            list(results), client_factory=self._factory(results), poll_seconds=0, attempts=3
        )
        assert out[("544399:BOM", "pe")] == pytest.approx(7.3)
        assert out[("544399:BOM", "marketcap")] == pytest.approx(501782400)
        assert out[("NSE:SRIGEE", "price")] is None  # #N/A -> None
        assert len(out) == 3

    def test_single_batch_write_with_user_entered(self):
        sheet = _FakeSheet({})
        sheet.computed[("grid", 0)] = [("BOM:544399", "price", 84)]

        fetch_gf_metrics(
            [("544399:BOM", "price")],
            client_factory=lambda: _FakeClient(sheet),
            poll_seconds=0,
            attempts=2,
        )
        ws = sheet.ws
        assert len(ws.update_calls) == 1  # ONE batch, not per-cell
        call = ws.update_calls[0]
        assert call["raw"] is False  # USER_ENTERED trap
        assert call["values"][1] == ["BOM:544399", "price", '=GOOGLEFINANCE("BOM:544399","price")']

    def test_empty_requests_short_circuit(self):
        assert fetch_gf_metrics([], client_factory=None) == {}
