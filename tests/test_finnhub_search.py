#!/usr/bin/env python3
"""S1 tests — finnhub_search.py symbol-lookup client.

Fixtures: real /search responses saved 2026-08-25 under
tests/fixtures/finnhub_search/. No network: fh_search is monkeypatched
or the cache is pre-seeded; the token file is never read by these tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers.maintenance import finnhub_search as fh  # noqa: E402
from helpers.maintenance.finnhub_search import (  # noqa: E402
    FhMatch,
    fh_search_cached,
    parse_search_response,
    trim_query,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "finnhub_search"


def _load(fname: str) -> str:
    return (FIXTURES / fname).read_text(encoding="utf-8")


class TestParseSearchResponse:
    def test_srigee_exact_hit(self):
        # The motivating case: name -> Yahoo-format scrip ticker.
        matches = parse_search_response(_load("fh_Srigee.json"))
        assert matches == [FhMatch(symbol="544399.BO", description="Srigee DLM Ltd")]

    def test_demerger_twins_both_found(self):
        matches = parse_search_response(_load("fh_Tata_Motors.json"))
        symbols = {m.symbol: m.description for m in matches}
        assert "TMPV.NS" in symbols and "TMCV.NS" in symbols
        assert "Passenger" in symbols["TMPV.NS"]

    def test_pel_fixes_stale_ticker(self):
        matches = parse_search_response(_load("fh_Piramal_Enterprises.json"))
        assert matches[0].symbol == "PEL.NS"

    def test_gati_substring_noise(self):
        # 'Gati' returns navigation companies worldwide — the reason
        # every candidate must pass name verification downstream.
        matches = parse_search_response(_load("fh_Gati.json"))
        assert len(matches) >= 5
        assert all(".NS" not in m.symbol and ".BO" not in m.symbol for m in matches)

    def test_no_results_is_empty(self):
        assert parse_search_response(_load("fh_zzznosuchco.json")) == []
        assert parse_search_response(json.dumps({"result": []})) == []

    def test_rows_without_symbol_skipped(self):
        text = json.dumps({"result": [{"description": "no symbol"}, {"symbol": "X.NS"}]})
        assert parse_search_response(text) == [FhMatch("X.NS", "")]


class TestTrimQuery:
    def test_short_query_unchanged(self):
        assert trim_query("Srigee") == "Srigee"

    def test_long_query_trims_to_word_boundary(self):
        # 'Tata Motors Passenger' (21 chars) 422s at FinnHub; the trim
        # keeps 'Tata Motors' — which still finds both demerger twins.
        assert trim_query("Tata Motors Passenger Vehicles") == "Tata Motors"

    def test_no_space_truncates_hard(self):
        assert trim_query("abcdefghijklmnopqrstuvwxyz") == "abcdefghijklmnopqrst"


class TestCacheAndToken:
    def test_cache_hit_avoids_network(self, tmp_path, monkeypatch):
        (tmp_path / "fh_search_Srigee.txt").write_text(_load("fh_Srigee.json"), encoding="utf-8")

        def exploding(query, **_kw):
            raise AssertionError("network hit despite warm cache")

        monkeypatch.setattr(fh, "fh_search", exploding)
        matches, from_cache = fh_search_cached("Srigee", tmp_path)
        assert from_cache is True
        assert matches[0].symbol == "544399.BO"

    def test_cache_miss_fetches_and_writes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fh, "fh_search", lambda query, **_kw: _load("fh_Tata_Motors.json"))
        matches, from_cache = fh_search_cached("Tata Motors", tmp_path)
        assert from_cache is False
        assert {m.symbol for m in matches} == {"TMPV.NS", "TMCV.NS"}
        assert (tmp_path / "fh_search_Tata_Motors.txt").exists()

    def test_token_from_env_file(self, tmp_path, monkeypatch):
        # Token resolves from a .env file (memory/.env form). Fake token —
        # _resolve_token only reads env/files, it never dials out.
        fake = "fake0token" + "a1b2c3d4" * 3
        env = tmp_path / ".env"
        env.write_text(f'FINNHUB_API_KEY="{fake}"\n')
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        assert fh._resolve_token(env) == fake

    def test_token_missing_raises(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("UNRELATED=yes\n")
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="no finnhub token"):
            fh._resolve_token(env)

    def test_exported_env_wins_over_file(self, tmp_path, monkeypatch):
        file_fake = "file0token" + "a1b2c3d4" * 3
        env_fake = "envir0token" + "a1b2c3d4" * 3
        env = tmp_path / ".env"
        env.write_text(f'FINNHUB_API_KEY="{file_fake}"\n')
        monkeypatch.setenv("FINNHUB_API_KEY", env_fake)
        assert fh._resolve_token(env) == env_fake


class TestProbeQueries:
    def test_single_word_single_probe(self):
        assert fh.probe_queries("Srigee") == ["Srigee"]

    def test_two_word_adds_first_word(self):
        # The Piramal shape: full name fits under the q limit; the
        # shorter stem is the fallback when the full form comes back
        # empty.
        assert fh.probe_queries("Piramal Enterprises") == ["Piramal Enterprises", "Piramal"]

    def test_long_name_trims_then_adds_stems(self):
        # 'Bajaj Allianz General Insurance' trims to 'Bajaj Allianz'
        # (word-boundary cut at 20 chars); probes are the trimmed form
        # then its first word.
        assert fh.probe_queries("Bajaj Allianz General Insurance") == ["Bajaj Allianz", "Bajaj"]

    def test_three_word_name_gets_intermediate_probe(self):
        assert fh.probe_queries("Ab Cd Ef Gh") == ["Ab Cd Ef Gh", "Ab Cd", "Ab"]


class TestFhSearchMulti:
    @staticmethod
    def _fake(results_by_query, cached_misses=False):
        """search_fn stub keyed by query -> list[FhMatch]; records calls.
        Misses report from_cache=cached_misses."""
        calls: list[str] = []

        def fn(query, _cache_dir, **_kw):
            calls.append(query)
            hit = query in results_by_query
            return (results_by_query.get(query, []), True if hit else cached_misses)

        return fn, calls

    def test_stops_at_first_nonempty_probe(self, tmp_path):
        fn, calls = self._fake({"Ab Cd": [FhMatch("X.NS", "Ab Cd Ltd")]})
        matches, from_cache = fh.fh_search_multi(
            "Ab Cd Ef Gh", tmp_path, search_fn=fn, sleeper=lambda _s: None
        )
        assert [m.symbol for m in matches] == ["X.NS"]
        assert from_cache is True
        assert calls == ["Ab Cd Ef Gh", "Ab Cd"]  # third probe never runs

    def test_all_empty_all_cached_no_sleep(self, tmp_path):
        fn, _calls = self._fake({}, cached_misses=True)
        slept: list[float] = []
        matches, from_cache = fh.fh_search_multi(
            "Ab Cd Ef Gh", tmp_path, search_fn=fn, sleeper=slept.append
        )
        assert matches == []
        assert from_cache is True  # caller skips its own politeness sleep
        assert slept == []

    def test_real_misses_sleep_and_report_uncached(self, tmp_path):
        fn, _calls = self._fake({})  # every probe reports a real fetch
        slept: list[float] = []
        matches, from_cache = fh.fh_search_multi(
            "Ab Cd Ef Gh", tmp_path, search_fn=fn, sleeper=slept.append, delay=2.0
        )
        assert matches == []
        assert from_cache is False
        assert slept == [2.0] * 3  # paced after each real empty probe
