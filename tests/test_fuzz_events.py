"""Phase 5 fuzz tests — event extraction invariants.

Property-based tests (via Hypothesis) for `helpers/graph/derive_events.py`.
"""
from __future__ import annotations

from hypothesis import given, strategies as st, settings

from helpers.graph.derive_events import (
    _capture_period_token,
    _parse_event_date,
)


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=100))
def test_fuzz_capture_period_token(text: str):
    """Invariant 1: _capture_period_token handles arbitrary strings safely."""
    try:
        token = _capture_period_token(text)
        assert token is None or isinstance(token, str)
    except Exception as e:
        raise e


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=100))
def test_fuzz_parse_event_date(text: str):
    """Invariant 2: _parse_event_date handles arbitrary strings safely."""
    try:
        res = _parse_event_date(text)
        assert isinstance(res, tuple)
        assert len(res) == 3
    except Exception as e:
        raise e
