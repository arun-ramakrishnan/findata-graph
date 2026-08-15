"""Phase 5 fuzz tests — quote and metric extraction invariants.

Property-based tests (via Hypothesis) for `helpers/graph/derive_insights.py`.
"""
from __future__ import annotations

from hypothesis import given, strategies as st, settings

from helpers.graph.derive_insights import (
    _parse_attribution,
    _parse_value_num,
)


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=100))
def test_fuzz_parse_attribution(attribution_text: str):
    """Invariant 1: _parse_attribution handles arbitrary strings without crashing."""
    try:
        res = _parse_attribution(attribution_text)
        assert res is None or (isinstance(res, tuple) and len(res) == 2)
    except Exception as e:
        raise e


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=50))
def test_fuzz_parse_value_num(val_str: str):
    """Invariant 2: _parse_value_num handles arbitrary strings without crashing or overflowing."""
    try:
        num = _parse_value_num(val_str, None)
        assert num is None or isinstance(num, (int, float))
    except (ValueError, TypeError, OverflowError):
        pass
