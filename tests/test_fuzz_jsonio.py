#!/usr/bin/env python3
"""Fuzz tests for helpers/core/jsonio.py — the orjson shim
(test_coverage_gap_sweep S2).

The shim's contract (module docstring §6.3): loads raises the DISTINCT
``jsonio.JSONDecodeError`` on malformed input; dumps is orjson-canonical
(compact, sorted when asked); special float / key-type edges are PINNED
measured 2026-11-01:

* ``loads('NaN')`` REJECTS (JSONDecodeError) — the docstring is right on
  the parse side,
* ``dumps(float('nan'))`` writes ``null`` — the docstring's "orjson
  rejects NaN" is WRONG on the serialize side; roundtrip maps NaN →
  None. Pinned as a property so a caller silently losing NaNs is a
  known, documented behavior rather than a surprise.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from helpers.core import jsonio  # noqa: E402

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# JSON roundtrip domain: no NaN (loads rejects it), no non-str dict keys
# (dumps raises TypeError), ints within orjson's 64-bit serializer.
_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**63), max_value=2**64 - 1),
    st.floats(allow_nan=False, allow_infinity=False, width=16),
    st.text(max_size=64),
)
_JSONISH = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=8),
        st.dictionaries(st.text(max_size=16), children, max_size=8),
    ),
    max_leaves=25,
)


@_SETTINGS
@given(_JSONISH)
def test_dumps_bytes_roundtrip(value):
    assert jsonio.loads(jsonio.dumps_bytes(value)) == value


@_SETTINGS
@given(_JSONISH)
def test_dumps_str_roundtrip(value):
    """dumps() returns str; loads() reads it back identically."""
    out = jsonio.dumps(value)
    assert isinstance(out, str)
    assert jsonio.loads(out) == value


@_SETTINGS
@given(st.dictionaries(st.text(max_size=16), _SCALARS, max_size=10))
def test_sort_keys_orders_every_level(obj):
    """sort_keys=True sorts recursively (OPT_SORT_KEYS); the parsed
    result's key order must be non-decreasing at every level."""
    out = jsonio.loads(jsonio.dumps_bytes(obj, sort_keys=True))

    def check(node):
        if isinstance(node, dict):
            keys = list(node)
            assert keys == sorted(keys)
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(out)


def _keys_sorted(node):
    if isinstance(node, dict):
        return list(node) == sorted(node) and all(_keys_sorted(v) for v in node.values())
    if isinstance(node, list):
        return all(_keys_sorted(v) for v in node)
    return True


@_SETTINGS
@given(st.dictionaries(st.text(max_size=16), st.integers(0, 99), max_size=10))
def test_indent_fallback_keeps_stdlib_shape(obj):
    """indent=2 → orjson OPT_INDENT_2 (sorted not implied); any other
    indent → stdlib json path (sorted honoured by stdlib when asked).
    Empty containers indent to '{}' — only the roundtrip is universal."""
    out2 = jsonio.dumps(obj, indent=2)
    assert jsonio.loads(out2) == obj
    out3 = jsonio.dumps(obj, indent=3, sort_keys=True)
    assert jsonio.loads(out3) == obj
    if obj:
        assert "\n" in out2 and "\n" in out3
        assert _keys_sorted(jsonio.loads(out3))


@_SETTINGS
@given(st.binary(max_size=256))
def test_malformed_bytes_raise_jsondecodeerror(raw):
    """The one catch point the converted call sites rely on: every parse
    failure is jsonio.JSONDecodeError (a ValueError subclass), never
    anything else."""
    try:
        jsonio.loads(raw)
    except jsonio.JSONDecodeError:
        pass


def test_nan_asymmetry_is_pinned():
    """MEASURED 2026-11-01: parse rejects NaN, serialize maps it to null —
    the docstring documents only half of this. A NaN entering the shim on
    the write side silently becomes None downstream."""
    with pytest.raises(jsonio.JSONDecodeError):
        jsonio.loads("NaN")
    assert jsonio.dumps(float("nan")) == "null"
    assert jsonio.loads(jsonio.dumps(float("nan"))) is None


def test_non_str_dict_keys_raise_typeerror():
    """orjson's key rule: str keys only — int and bytes keys raise
    TypeError (msgpackio accepts int keys; the two transports differ)."""
    with pytest.raises(TypeError):
        jsonio.dumps({1: "a"})
    with pytest.raises(TypeError):
        jsonio.dumps({b"k": 1})


def test_out_of_range_int_raises_typeerror():
    """2**64 exceeds orjson's serializer (TypeError), where msgpackio
    raises OverflowError — different failure classes at the two seams."""
    with pytest.raises(TypeError):
        jsonio.dumps(2**64)
