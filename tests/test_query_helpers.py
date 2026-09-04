"""Unit tests for helpers/graph/query.py — pure helper functions."""

from __future__ import annotations

import pytest


from helpers.graph import query as q  # noqa: E402


# ---------------------------------------------------------------------------
# _lit — SQL string literal escaping
# ---------------------------------------------------------------------------
class TestLit:
    def test_basic_string(self):
        assert q._lit("hello") == "'hello'"

    def test_empty_string(self):
        assert q._lit("") == "''"

    def test_single_quote_escaped(self):
        assert q._lit("O'Brien") == "'O''Brien'"

    def test_strips_control_chars(self):
        result = q._lit("test\x00abc")
        assert "\x00" not in result
        assert "testabc" in result

    def test_non_string_input(self):
        assert q._lit(42) == "'42'"


# ---------------------------------------------------------------------------
# _normalise_as_of — temporal date normalisation
# ---------------------------------------------------------------------------
class TestNormaliseAsOf:
    def test_none(self):
        assert q._normalise_as_of(None) is None

    def test_empty(self):
        assert q._normalise_as_of("") is None

    def test_whitespace(self):
        assert q._normalise_as_of("   ") is None

    def test_year_only(self):
        assert q._normalise_as_of("2024") == "2024-01-01"

    def test_year_month(self):
        assert q._normalise_as_of("2024-06") == "2024-06-01"

    def test_full_date(self):
        assert q._normalise_as_of("2024-06-15") == "2024-06-15"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            q._normalise_as_of("not-a-date")

    def test_invalid_partial_raises(self):
        with pytest.raises(ValueError):
            q._normalise_as_of("202")  # 3 chars, not a valid shape


# ---------------------------------------------------------------------------
# _as_of_predicate — temporal WHERE fragment builder
# ---------------------------------------------------------------------------
class TestAsOfPredicate:
    def test_none_returns_empty(self):
        assert q._as_of_predicate(None) == ""

    def test_empty_returns_empty(self):
        assert q._as_of_predicate("") == ""

    def test_year_builds_predicate(self):
        result = q._as_of_predicate("2024")
        assert "valid_from" in result
        assert "2024-01-01" in result

    def test_custom_edge_alias(self):
        result = q._as_of_predicate("2024", edge_alias="x")
        assert "x.valid_from" in result


# ---------------------------------------------------------------------------
# _label_to_table — edge label → table name lookup
# ---------------------------------------------------------------------------
class TestLabelToTable:
    def test_belongs_to(self):
        assert q._label_to_table("BelongsTo") == "e_belongs"

    def test_acquired_by(self):
        assert q._label_to_table("AcquiredBy") == "e_acquired"

    def test_unknown_returns_none(self):
        assert q._label_to_table("NonExistent") is None


# ---------------------------------------------------------------------------
# _query_cache — cache operations
# ---------------------------------------------------------------------------
class TestQueryCache:
    def test_set_and_get(self):
        q._query_cache_clear()
        q._query_cache_set(("test",), "value")
        assert q._query_cache_get(("test",)) == "value"

    def test_get_missing(self):
        q._query_cache_clear()
        assert q._query_cache_get(("missing",)) is None

    def test_clear(self):
        q._query_cache_set(("a",), 1)
        q._query_cache_clear()
        assert q._query_cache_get(("a",)) is None

    def test_fifo_eviction(self):
        q._query_cache_clear()
        # Fill to max
        original_max = q._QUERY_CACHE_MAX
        q._QUERY_CACHE_MAX = 3  # ty: ignore[invalid-assignment]
        try:
            q._query_cache_set(("a",), 1)
            q._query_cache_set(("b",), 2)
            q._query_cache_set(("c",), 3)
            # Next insert should evict ("a",)
            q._query_cache_set(("d",), 4)
            assert q._query_cache_get(("a",)) is None
            assert q._query_cache_get(("d",)) == 4
        finally:
            q._QUERY_CACHE_MAX = original_max


# ---------------------------------------------------------------------------
# clear_graph_cache — smoke test
# ---------------------------------------------------------------------------
class TestClearGraphCache:
    def test_does_not_crash(self):
        q._query_cache_set(("x",), "y")
        q.clear_graph_cache()
        assert q._query_cache_get(("x",)) is None


# ---------------------------------------------------------------------------
# EDGE_REGISTRY — constant validation
# ---------------------------------------------------------------------------
class TestEdgeRegistry:
    def test_all_have_table(self):
        for etype, spec in q.EDGE_REGISTRY.items():
            assert "table" in spec, f"{etype} missing table"

    def test_all_have_label(self):
        for etype, spec in q.EDGE_REGISTRY.items():
            assert "label" in spec, f"{etype} missing label"

    def test_all_have_src_dst(self):
        for etype, spec in q.EDGE_REGISTRY.items():
            assert "src" in spec, f"{etype} missing src"
            assert "dst" in spec, f"{etype} missing dst"

    def test_by_label_reverse_lookup(self):
        for etype, spec in q.EDGE_REGISTRY.items():
            label = spec["label"]
            assert label in q.EDGE_REGISTRY_BY_LABEL
            assert q.EDGE_REGISTRY_BY_LABEL[label]["edge_type"] == etype
