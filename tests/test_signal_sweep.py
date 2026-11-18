"""Tests for the signal-sweep forward-return cache + smoke check (perf-only, exact parity)."""

from __future__ import annotations

from helpers.research.signal_sweep import _abnormal, _bar_index, _forward_return, _smoke

PRICES = {
    "AAA": [
        ("2024-01-01", 10.0, 100),
        ("2024-01-08", 12.0, 100),
        ("2024-01-15", 15.0, 100),
        ("2024-01-22", 9.0, 100),
    ],
    "BBB": [
        ("2024-01-01", 100.0, 100),
        ("2024-01-08", 110.0, 100),
        ("2024-01-15", 121.0, 100),
        ("2024-01-22", 99.0, 100),
    ],
}


class TestForwardReturnCache:
    def test_cache_matches_uncached(self):
        idx = _bar_index(PRICES)
        cache: dict = {}
        for tkr, t, h in [
            ("AAA", "2024-01-01", 1),
            ("AAA", "2024-01-03", 1),  # anchor = last bar <= t
            ("BBB", "2024-01-01", 2),
            ("MISSING", "2024-01-01", 1),
            ("AAA", "2024-01-01", 99),  # out of window
        ]:
            uncached = _forward_return(PRICES, idx, tkr, t, h)
            cached = _forward_return(PRICES, idx, tkr, t, h, cache)
            assert cached == uncached

    def test_cache_populates_and_reuses(self):
        idx = _bar_index(PRICES)
        cache: dict = {}
        first = _forward_return(PRICES, idx, "AAA", "2024-01-01", 1, cache)
        assert ("AAA", "2024-01-01", 1) in cache
        # poison the source — a cache hit must still return the old value
        PRICES["AAA"][1] = ("2024-01-08", 999.0, 100)
        try:
            assert _forward_return(PRICES, idx, "AAA", "2024-01-01", 1, cache) == first
        finally:
            PRICES["AAA"][1] = ("2024-01-08", 12.0, 100)

    def test_abnormal_with_cache_matches_uncached(self):
        idx = _bar_index(PRICES)
        peers = ["BBB"]
        cache: dict = {}
        for t, h in [("2024-01-01", 1), ("2024-01-08", 1), ("2024-01-01", 2)]:
            uncached = _abnormal(PRICES, idx, "AAA", t, h, peers)
            cached = _abnormal(PRICES, idx, "AAA", t, h, peers, cache)
            assert cached == uncached

    def test_smoke_returns_closed_form_values(self):
        checks = _smoke()
        assert abs(checks["fwd_1bar"] - 0.2) < 1e-12
        assert abs(checks["fwd_2bar"] - 0.5) < 1e-12
        assert abs(checks["abnormal"] - 0.1) < 1e-12
        assert checks["spearman_perfect"] == 1.0
        assert checks["spearman_inverse"] == -1.0
        assert checks["spearman_short"] is None
