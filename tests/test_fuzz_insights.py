"""Phase 5 fuzz tests — quote and metric extraction invariants.

Property-based tests (via Hypothesis) for `helpers/graph/derive_insights.py`.
Strengthened 2026-08-22 (integration_fuzz_enhancement B7): the original
try/except-raise wrappers asserted nothing — these now pin real
invariants (typed, bounded numeric output, deterministic attribution).
"""

from __future__ import annotations

import math

from hypothesis import given, strategies as st, settings

from helpers.graph.derive_insights import (
    _parse_attribution,
    _parse_value_num,
)

_TEXT = st.text(min_size=0, max_size=100)


@settings(max_examples=100, deadline=None)
@given(_TEXT)
def test_fuzz_parse_attribution(attribution_text):
    """Invariant 1: _parse_attribution returns None or a (name, title)
    pair of None-or-str — and is deterministic."""
    res = _parse_attribution(attribution_text)
    assert res is None or (isinstance(res, tuple) and len(res) == 2)
    if res is not None:
        assert all(p is None or isinstance(p, str) for p in res)
    assert _parse_attribution(attribution_text) == res


@settings(max_examples=100, deadline=None)
@given(_TEXT)
def test_fuzz_parse_value_num(val_str):
    """Invariant 2: _parse_value_num returns None or a finite float in a
    sane magnitude range (financial magnitudes; never inf/nan from
    arbitrary text)."""
    num = _parse_value_num(val_str, None)
    assert num is None or isinstance(num, (int, float))
    if num is not None:
        assert math.isfinite(num)
        assert -1e15 <= num <= 1e15
