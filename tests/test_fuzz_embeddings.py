"""Fuzz tests — embedding generation invariants.

Property-based tests (via Hypothesis) for `helpers/graph/embeddings.py`.

Invariants pinned:
  1. _pseudo_embedding() is deterministic, returns exactly `dims` floats,
     all finite and within [-1, 1], L2-normalised to unit length, for ANY
     text and any positive dims.
  2. dims < 1 raises ValueError (regression: dims=0/-1 silently returned
     an empty vector; _ensure_schema(dims=-3) silently created a broken
     table whose CHECK could never pass).
  3. _ensure_schema() on a scratch SQLite DB always yields a table whose
     CHECK constraint matches the requested dims (round-trips via
     sqlite_master SQL text).
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import given, settings, strategies as st

from helpers.graph.embeddings import _ensure_schema, _pseudo_embedding


@settings(max_examples=200, deadline=None)
@given(st.text(min_size=0, max_size=300), st.integers(min_value=1, max_value=64))
def test_fuzz_pseudo_embedding_shape_and_norm(text: str, dims: int):
    """Invariant 1: unit-length vector of exactly `dims` finite floats."""
    vec = _pseudo_embedding(text, dims)
    assert len(vec) == dims
    assert all(math.isfinite(x) for x in vec)
    assert all(-1.0 <= x <= 1.0 for x in vec)
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm == pytest.approx(1.0, abs=1e-6)


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=300), st.integers(min_value=1, max_value=64))
def test_fuzz_pseudo_embedding_deterministic(text: str, dims: int):
    """Invariant 2: same input → identical vector (hash-derived, no RNG)."""
    a = _pseudo_embedding(text, dims)
    b = _pseudo_embedding(text, dims)
    assert a == b


@settings(max_examples=50, deadline=None)
@given(st.integers(min_value=-50, max_value=0))
def test_fuzz_pseudo_embedding_rejects_non_positive_dims(dims: int):
    """Invariant 3: dims < 1 must raise, not silently return an empty vector."""
    try:
        _pseudo_embedding("CEAT", dims)
    except ValueError:
        return
    raise AssertionError(f"_pseudo_embedding(dims={dims}) did not raise ValueError")


@settings(max_examples=50, deadline=None)
@given(st.integers(min_value=-50, max_value=0))
def test_fuzz_ensure_schema_rejects_non_positive_dims(dims: int):
    """Invariant 4: _ensure_schema(dims<1) must raise (no broken tables)."""
    conn = sqlite3.connect(":memory:")
    try:
        try:
            _ensure_schema(conn, dims)
        except ValueError:
            return
        raise AssertionError(f"_ensure_schema(dims={dims}) did not raise ValueError")
    finally:
        conn.close()


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=1, max_value=32))
def test_fuzz_ensure_schema_check_matches_dims(dims: int):
    """Invariant 5: table DDL CHECK always matches the requested dims."""
    conn = sqlite3.connect(":memory:")
    try:
        _ensure_schema(conn, dims)
        r = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='company_embeddings'"
        ).fetchone()
        assert r is not None
        assert f"= {dims}" in r[0] or f"={dims}" in r[0].replace(" ", "")
        assert f"FLOAT[{dims}]" in r[0]
    finally:
        conn.close()
