#!/usr/bin/env python3
"""F3 tests — exchange_search.py BSE PeerSmartSearch client.

Fixtures: real responses saved 2026-08-25 under tests/fixtures/
exchange_search/ (raw bodies, JSON-string-encoded HTML as BSE serves
them). No network: bse_search is monkeypatched or cache-seeded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.maintenance import exchange_search as es  # noqa: E402
from helpers.maintenance.exchange_search import (  # noqa: E402
    BseMatch,
    _decode_body,
    bse_search_cached,
    parse_bse_response,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "exchange_search"


def _load(fname: str) -> str:
    return (FIXTURES / fname).read_text(encoding="utf-8")


class TestParseBseResponse:
    def test_exact_hit_row(self):
        # The motivating case: name search -> scrip + symbol + ISIN.
        matches = parse_bse_response(_decode_body(_load("bse_srigee.json")))
        assert matches == [
            BseMatch(scrip="544399", symbol="SRIGEE", name="SRIGEE DLM LTD", isin="INE0RJ901010")
        ]

    def test_tmpv_row_carries_nse_symbol(self):
        # BSE rows carry the NSE symbol (TMPV) — the G4 writeback case.
        matches = parse_bse_response(_decode_body(_load("bse_tata_motors_pas.json")))
        assert matches[0].symbol == "TMPV"
        assert matches[0].scrip == "500570"
        assert "PASSENGER VEHICLES" in matches[0].name

    def test_substring_noise_rows_all_parsed(self):
        # 'gati' substring-matches 7 irrigation/navigation rows; order is
        # relevance (exact-ish first) and the caller caps what it probes.
        matches = parse_bse_response(_decode_body(_load("bse_gati.json")))
        assert len(matches) == 7
        assert matches[0].name == "JAIN IRRIGATION SYSTEMS LTD"
        assert matches[1].name.endswith("_DVR")
        assert matches[0].symbol == "JISLJALEQS"

    def test_na_isin_left_empty(self):
        matches = parse_bse_response(_decode_body(_load("bse_gati.json")))
        scindia = next(m for m in matches if m.scrip == "501887")
        assert scindia.isin == ""
        assert scindia.symbol == "SCINDIA"

    def test_no_match_found_is_empty(self):
        assert parse_bse_response(_decode_body(_load("bse_zzznosuchco.json"))) == []

    def test_decode_body_passthrough_for_plain_html(self):
        assert _decode_body("<li>x</li>") == "<li>x</li>"


class TestBseSearchCached:
    def test_cache_hit_avoids_network(self, tmp_path, monkeypatch):
        (tmp_path / "bse_search_Srigee_DLM.txt").write_text(
            _decode_body(_load("bse_srigee.json")), encoding="utf-8"
        )

        def exploding_search(query, **_kw):
            raise AssertionError("network hit despite warm cache")

        monkeypatch.setattr(es, "bse_search", exploding_search)
        matches, from_cache = bse_search_cached("Srigee DLM", tmp_path)
        assert from_cache is True
        assert matches[0].scrip == "544399"

    def test_cache_miss_fetches_and_writes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            es, "bse_search", lambda query, **_kw: _decode_body(_load("bse_srigee.json"))
        )
        matches, from_cache = bse_search_cached("Srigee DLM", tmp_path)
        assert from_cache is False
        assert matches[0].symbol == "SRIGEE"
        cache_file = tmp_path / "bse_search_Srigee_DLM.txt"
        assert cache_file.exists()
        assert parse_bse_response(cache_file.read_text(encoding="utf-8")) == matches

    def test_query_sanitised_for_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr(es, "bse_search", lambda query, **_kw: "")
        bse_search_cached("Tata Motors Passenger/Vehicles Ltd", tmp_path)
        assert list(tmp_path.iterdir()) == [
            tmp_path / "bse_search_Tata_Motors_Passenger_Vehicles_Ltd.txt"
        ]


class TestBseMatch:
    def test_frozen_and_defaulted(self):
        m = BseMatch(scrip="1", symbol="S", name="N")
        assert m.isin == ""
        with pytest.raises(Exception):
            m.scrip = "2"  # ty: ignore[invalid-assignment]
