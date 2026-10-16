#!/usr/bin/env python3
"""Human time ranges — `30d`, `6m`, `1y`, `90` — for retention and display.

Shared by `bili_sessions.py` (`--since`) and `harvest_conversations.py`
(`--keep`), because a range that means different things in two retention flags
is worse than no flag at all.

Two decisions worth stating up front:

**A bare `m` is REJECTED, not guessed.** `m` reads as months in a retention
context and minutes everywhere else, and the two differ by a factor of 43,200.
On a flag that *purges*, guessing is the one failure mode that cannot be undone,
so `parse_range("6m")` raises and names the two spellings. Write `6mo` or
`6min`.

**Months and years use calendar arithmetic, not 30/365-day approximations.**
`cutoff_from("6mo")` from 2026-10-31 lands on 2026-04-30, not 2026-05-01 —
approximation drifts across short months and the drift is silent. (Implemented
here rather than with `dateutil.relativedelta`, which is present in the venv but
only transitively; `deptry` would flag the import.)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

__all__ = ["parse_range", "cutoff_from", "format_range", "RangeError"]

_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$")

# Longest-first inside each tuple so `months` never matches `mon`'s prefix
# rule, and so `min` is tested before the bare `m` rejection below.
_UNITS: dict[str, str] = {}
for _names, _canon in (
    (("seconds", "second", "secs", "sec", "s"), "s"),
    (("minutes", "minute", "mins", "min", "mi"), "min"),
    (("months", "month", "mons", "mon", "mo"), "mo"),
    (("weeks", "week", "wks", "wk", "w"), "w"),
    (("days", "day", "d"), "d"),
    (("hours", "hour", "hrs", "hr", "h"), "h"),
    (("years", "year", "yrs", "yr", "y"), "y"),
):
    for _n in _names:
        _UNITS[_n] = _canon

# `m` alone is the one spelling with no canonical unit.
_AMBIGUOUS = {"m"}


class RangeError(ValueError):
    """Raised for an unparsable range. The message names the fix."""


def parse_range(text) -> tuple[float, str]:
    """Parse `30d` / `6mo` / `1y` / `90` into `(amount, canonical_unit)`.

    A bare number means **days**, which is what `--since 7` already accepted.

    Raises `RangeError` on anything else, including the ambiguous bare `m`.
    """
    if text is None:
        raise RangeError("missing range; try 30d, 6mo or 1y")
    if isinstance(text, (int, float)):
        return float(text), "d"
    raw = str(text).strip()
    m = _RANGE_RE.match(raw)
    if not m:
        raise RangeError(f"cannot parse range {raw!r}; expected <number><unit> e.g. 30d, 6mo, 1y")
    amount = float(m.group(1))
    suffix = m.group(2).lower()
    if not suffix:
        return amount, "d"
    if suffix in _AMBIGUOUS:
        raise RangeError(
            f"ambiguous range {raw!r}: 'm' means months in a retention window and "
            f"minutes in an interval. Write '6mo' for months or '6min' for minutes."
        )
    unit = _UNITS.get(suffix)
    if unit is None:
        known = "d w h min s mo y (or a bare number of days)"
        raise RangeError(f"unknown range unit {suffix!r} in {raw!r}; known units: {known}")
    return amount, unit


def cutoff_from(text, *, now: datetime | None = None) -> datetime:
    """The instant `text` ago. Timezone-aware UTC unless `now` is naive."""
    amount, unit = parse_range(text)
    ref = now or datetime.now(UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)

    if unit == "mo":
        return _shift_months(ref, -amount)
    if unit == "y":
        return _shift_months(ref, -amount * 12)
    seconds = {
        "s": 1,
        "min": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }[unit]
    return ref - timedelta(seconds=amount * seconds)


def _shift_months(ref: datetime, months: float) -> datetime:
    """Calendar-correct month shift, clamping to the target month's last day.

    2026-10-31 minus 6 months is 2026-04-30, not 2026-05-01. `relativedelta`
    would do this, but it is only a transitive dependency here.
    """
    whole, frac = int(months), months - int(months)
    total = ref.year * 12 + (ref.month - 1) + whole
    year, month = divmod(total, 12)
    month += 1
    last = _days_in_month(year, month)
    day = min(ref.day, last)
    shifted = ref.replace(year=year, month=month, day=day)
    if frac:
        shifted -= timedelta(days=frac * _days_in_month(shifted.year, shifted.month))
    return shifted


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (datetime(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day


def format_range(delta: timedelta) -> str:
    """Render a `timedelta` the way `parse_range` reads it, for echo-back."""
    seconds = delta.total_seconds()
    if seconds <= 0:
        return "0d"
    for unit, size in (
        ("y", 31536000),
        ("mo", 2592000),
        ("w", 604800),
        ("d", 86400),
        ("h", 3600),
        ("min", 60),
    ):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"
