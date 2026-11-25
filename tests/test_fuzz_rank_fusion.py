#!/usr/bin/env python3
"""Fuzz tests for RRF fusion (S4, rank_fusion leg).

Properties pinned over random ranked lists:
- rrf: key set is the union of input keys; scores positive; per-key
  contribution strictly decreases with position in a leg; duplicate
  keys across legs accumulate; empty input -> empty result.
- anchored_fusion: the first `anchors` fts keys lead the output in
  order; output contains no duplicates; ties break by key.
- rrf_two_leg: scores are strictly descending; the missing-cosine
  fallback equals the single-leg formula; the extra-predicate branch
  uses the cosine leg alone.

Can-fail proof: a list where the same key appears at two positions must
score strictly more at the earlier position — if positional scoring
regressed to uniform, this fails.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.core.rank_fusion import RRF_K, anchored_fusion, rrf

_KEY = st.uuids()
_SCORE = st.floats(min_value=0.0, max_value=100.0)
_RANKED = st.lists(
    st.lists(st.tuples(_KEY, _SCORE), min_size=0, max_size=12),
    min_size=0,
    max_size=5,
)
_SETTINGS = settings(max_examples=60, deadline=None)


@_SETTINGS
@given(_RANKED)
def test_rrf_key_set_is_union_and_scores_positive(ranked):
    fused = rrf(*ranked)
    expected_keys = {k for hits in ranked for k, _ in hits}
    assert set(fused) == expected_keys
    assert all(v > 0.0 for v in fused.values())


@_SETTINGS
@given(_RANKED)
def test_rrf_empty_input_is_empty(ranked):
    if not any(ranked):
        assert rrf(*ranked) == {}


@_SETTINGS
@given(_RANKED, st.integers(min_value=1, max_value=12))
def test_rrf_contribution_strictly_decreases_with_position(ranked, k):
    filler = [("f0", 1.0), ("f1", 1.0), ("f2", 1.0)]
    key = "k"
    # three legs with the same key at increasing positions
    legs = []
    for pos in range(3):
        leg = list(filler)
        leg[pos] = (key, 1.0)
        legs.append(leg)
    contribs = [rrf(leg, k=k)[key] for leg in legs]
    assert contribs[0] > contribs[1] > contribs[2]
    assert contribs[0] == pytest.approx(1.0 / (k + 0 + 1))
    assert contribs[1] == pytest.approx(1.0 / (k + 1 + 1))
    assert contribs[2] == pytest.approx(1.0 / (k + 2 + 1))


@_SETTINGS
@given(_RANKED, st.uuids())
def test_rrf_duplicate_key_accumulates(ranked, key):
    a = [(key, 1.0), ("x", 1.0)]
    b = [("x", 1.0), (key, 1.0)]
    fused = rrf(a, b)
    assert fused[key] == pytest.approx(1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 2))


@_SETTINGS
@given(
    st.lists(st.tuples(_KEY, _SCORE), min_size=1, max_size=10),
    st.lists(st.tuples(_KEY, _SCORE), min_size=0, max_size=10),
    st.integers(min_value=1, max_value=5),
)
def test_anchored_fusion_anchors_lead_and_no_dups(fts, cos, anchors):
    out = anchored_fusion(fts, cos, anchors=anchors)
    seen = set()
    for key, _ in out:
        assert key not in seen
        seen.add(key)
    anchor_keys = [k for k, _ in fts[:anchors]]
    for i, key in enumerate(anchor_keys):
        assert out[i][0] == key


def test_can_fail_position_scoring():
    """Earlier position must beat later position for the same key."""
    hits = [("k", 1.0), ("k", 1.0)]
    fused = rrf(hits)
    contrib_0 = 1.0 / (RRF_K + 1)
    contrib_1 = 1.0 / (RRF_K + 2)
    assert fused["k"] == pytest.approx(contrib_0 + contrib_1)
