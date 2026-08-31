"""Phase 5 fuzz tests — event extraction invariants.

Property-based tests (via Hypothesis) for `helpers/graph/derive_events.py`.
Strengthened 2026-08-22 (integration_fuzz_enhancement B7): the original
try/except-raise wrappers asserted nothing — these now pin real
invariants (typed, deterministic, shape-constrained outputs).
"""

from __future__ import annotations

import re

from hypothesis import given, strategies as st, settings

from helpers.graph.derive_events import (
    _capture_period_token,
    _parse_event_date,
)

_TEXT = st.text(min_size=0, max_size=200)
_ISO = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


@settings(max_examples=100, deadline=None)
@given(_TEXT)
def test_fuzz_capture_period_token(text):
    """Invariant 1: _capture_period_token returns None or a stripped,
    non-empty token — and whatever it returns is literally present in the
    input (it captures, never synthesises)."""
    token = _capture_period_token(text)
    assert token is None or (isinstance(token, str) and token.strip())
    if token is not None:
        assert token in text


@settings(max_examples=100, deadline=None)
@given(_TEXT)
def test_fuzz_parse_event_date(text):
    """Invariant 2: _parse_event_date returns a 3-tuple of None-or-str;
    a non-None event_date is always an ISO date prefix (YYYY[-MM[-DD]]),
    and the result is deterministic."""
    res = _parse_event_date(text)
    assert isinstance(res, tuple) and len(res) == 3
    for part in res:
        assert part is None or isinstance(part, str)
    if res[0] is not None:
        assert _ISO.match(res[0])
    assert _parse_event_date(text) == res
