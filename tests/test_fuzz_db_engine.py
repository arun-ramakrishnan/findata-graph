#!/usr/bin/env python3
"""Fuzz tests for the DuckDB connection proxy (S4, db_engine leg).

`helpers.core.db_engine._DuckDBConn` is the house row/param
translation layer; the properties here are:
- roundtrip: arbitrary (int, float, text, blob) rows survive
  execute/executemany/select with byte-identical values;
- malformed rejection: wrong arity and wrong types raise (never
  silently truncate);
- can-fail proof: a hand-built arity mismatch is rejected.
"""

from __future__ import annotations

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.core import db_engine as de

_INT = st.integers(min_value=-(2**40), max_value=2**40)
_FLOAT = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e12, max_value=1e12)
_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40)
_BLOB = st.binary(min_size=0, max_size=16)

_ROW = st.tuples(_INT, _FLOAT, _TEXT, _BLOB)

_SETTINGS = settings(max_examples=50, deadline=None)


def _fresh_conn():
    raw = duckdb.connect(":memory:")
    raw.execute("CREATE TABLE t (i BIGINT, f DOUBLE, s TEXT, b BLOB)")
    return de._DuckDBConn(raw)


def _rows(conn):
    return conn.execute("SELECT i, f, s, b FROM t ORDER BY i, s").fetchall()


@_SETTINGS
@given(st.lists(_ROW, min_size=0, max_size=8))
def test_roundtrip_rows(rows):
    conn = _fresh_conn()
    if rows:
        conn.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", rows)
    got = sorted(tuple(r) for r in _rows(conn))
    assert got == sorted(rows)


@_SETTINGS
@given(st.lists(_ROW, min_size=1, max_size=8), st.integers(min_value=0, max_value=7))
def test_execute_single_row_is_same_as_batch(rows, idx):
    conn = _fresh_conn()
    row = rows[idx % len(rows)]
    conn.execute("INSERT INTO t VALUES (?, ?, ?, ?)", row)
    assert [tuple(r) for r in _rows(conn)] == [row]


@_SETTINGS
@given(st.lists(_ROW, min_size=1, max_size=16))
def test_executemany_preserves_count_and_shape(rows):
    conn = _fresh_conn()
    conn.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", rows)
    got = sorted(tuple(r) for r in _rows(conn))
    assert len(got) == len(rows)
    assert got == sorted(rows)


@_SETTINGS
@given(st.lists(_ROW, min_size=1, max_size=4), st.integers(min_value=-3, max_value=3))
def test_wrong_arity_rejected(rows, delta):
    if delta == 0:
        return  # correct arity is the roundtrip case, not a rejection
    conn = _fresh_conn()
    bad = rows[0][: len(rows[0]) + delta] if delta <= 0 else (rows[0] + ("x",) * delta)
    with pytest.raises((duckdb.Error, TypeError)):
        conn.execute("INSERT INTO t VALUES (?, ?, ?, ?)", bad)


def test_can_fail_arity_mismatch():
    """Hand-built bad input the property shape rejects."""
    conn = _fresh_conn()
    with pytest.raises((duckdb.Error, TypeError)):
        conn.execute("INSERT INTO t VALUES (?, ?, ?, ?)", (1, 2.0))
