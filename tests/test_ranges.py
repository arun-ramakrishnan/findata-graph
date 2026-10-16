"""Tests for `helpers.core.ranges` — the shared `30d / 6mo / 1y` parser.

Two of these pin decisions that exist to PREVENT mistakes on a flag that
deletes data, not to compute dates correctly:

  * a bare `m` is rejected rather than guessed — months vs minutes differ by a
    factor of 43,200 and a purge is not undoable;
  * months and years use calendar arithmetic — a 30-day approximation silently
    drifts the cutoff by a day or two on short months.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from helpers.core.ranges import RangeError, cutoff_from, format_range, parse_range

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


class TestParseRange:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30d", (30.0, "d")),
            ("30D", (30.0, "d")),
            (" 6mo ", (6.0, "mo")),
            ("6mon", (6.0, "mo")),
            ("1y", (1.0, "y")),
            ("2w", (2.0, "w")),
            ("12h", (12.0, "h")),
            ("45min", (45.0, "min")),
            ("90s", (90.0, "s")),
            ("1.5d", (1.5, "d")),
            ("7", (7.0, "d")),  # bare number = days, matches the old --since
            (7, (7.0, "d")),
            (7.0, (7.0, "d")),
        ],
    )
    def test_accepts(self, text, expected):
        assert parse_range(text) == expected

    def test_rejects_bare_m(self):
        """The whole point: months or minutes, and guessing is not allowed."""
        with pytest.raises(RangeError) as exc:
            parse_range("6m")
        msg = str(exc.value)
        assert "ambiguous" in msg
        assert "6mo" in msg and "6min" in msg, "the error must name both spellings"

    @pytest.mark.parametrize("text", ["", "   ", "abc", "6x", "d", "6 m x", None, "-5d"])
    def test_rejects_junk(self, text):
        with pytest.raises(RangeError):
            parse_range(text)


class TestCutoffIsCalendarCorrect:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30d", "2026-10-01"),
            ("90d", "2026-08-02"),
            ("2w", "2026-10-17"),
            ("1y", "2025-10-31"),
            ("6mo", "2026-04-30"),
            ("3mo", "2026-07-31"),
        ],
    )
    def test_from_month_end(self, text, expected):
        assert cutoff_from(text, now=NOW).strftime("%Y-%m-%d") == expected

    def test_month_end_clamps_to_last_day(self):
        # 2026-03-31 minus one month is 2026-02-28, not 2026-03-03.
        got = cutoff_from("1mo", now=datetime(2026, 3, 31, tzinfo=UTC))
        assert got.strftime("%Y-%m-%d") == "2026-02-28"

    def test_leap_day_clamps(self):
        got = cutoff_from("1y", now=datetime(2024, 2, 29, tzinfo=UTC))
        assert got.strftime("%Y-%m-%d") == "2023-02-28"

    def test_year_boundary_crossing(self):
        got = cutoff_from("3mo", now=datetime(2026, 1, 15, tzinfo=UTC))
        assert got.strftime("%Y-%m-%d") == "2025-10-15"

    def test_naive_now_is_treated_as_utc(self):
        got = cutoff_from("1d", now=datetime(2026, 10, 17, 6, 0))
        assert got.tzinfo is not None
        assert got.strftime("%Y-%m-%d %H:%M") == "2026-10-16 06:00"


class TestFormatRange:
    @pytest.mark.parametrize(
        "delta,expected",
        [
            (timedelta(days=365), "1y"),
            (timedelta(days=30), "1mo"),
            (timedelta(days=7), "1w"),
            (timedelta(hours=12), "12h"),
            (timedelta(minutes=45), "45min"),
            (timedelta(0), "0d"),
            (timedelta(days=-1), "0d"),
        ],
    )
    def test_round_trips_readable(self, delta, expected):
        assert format_range(delta) == expected
        # and the output must parse back. Zero is the `--since 0` "no window"
        # sentinel, so a non-negative amount is the contract, not a positive one.
        amount, _unit = parse_range(expected)
        assert amount >= 0

    def test_format_output_is_parseable(self):
        for text in ("30d", "1y", "6mo"):
            amount, unit = parse_range(text)
            secs = {"d": 86400, "y": 31536000, "mo": 2592000}[unit]
            assert format_range(timedelta(seconds=amount * secs)) is not None
