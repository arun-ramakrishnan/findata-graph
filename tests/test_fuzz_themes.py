"""Phase 5 fuzz tests — theme extraction and frontmatter stripping invariants.

Property-based tests (via Hypothesis) for `helpers/graph/derive_themes.py`.
"""

from __future__ import annotations

from hypothesis import given, strategies as st, settings

from helpers.graph.derive_themes import (
    _strip_frontmatter,
    derive_edges,
)


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=500))
def test_fuzz_strip_frontmatter(text: str):
    """Invariant 1: _strip_frontmatter handles arbitrary text without crashing or raising."""
    try:
        res = _strip_frontmatter(text)
        assert isinstance(res, str)
        # Stripping frontmatter should never make the text longer
        assert len(res) <= len(text)
    except Exception as e:
        raise e


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.text(min_size=1, max_size=20),
            st.text(min_size=1, max_size=20),
            st.lists(st.text(min_size=1, max_size=10), max_size=3),
        ),
        max_size=10,
    )
)
def test_fuzz_derive_edges(membership):
    """Invariant 2: derive_edges transforms arbitrary membership lists safely."""
    try:
        edges = derive_edges(membership)
        assert isinstance(edges, list)
        assert len(edges) == len(membership)
        for edge in edges:
            assert isinstance(edge, tuple)
            assert len(edge) == 4
    except Exception as e:
        raise e
