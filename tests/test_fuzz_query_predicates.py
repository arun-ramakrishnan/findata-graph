#!/usr/bin/env python3
"""Fuzz tests for the query-layer predicates
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B2).

`_normalise_as_of` (only 8 fixed unit cases today) and `_lit`
(injection-adjacent SQL literal rendering) see attacker-adjacent free
text through the API; `notes_like_text` guards (k clamping, min_sim
monotonicity) had no properties at all. The DuckDB-backed properties run
against an in-memory stand-in ``v_note_embeddings`` table with an
injectable embedder — no production DB touch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import query as gq  # noqa: E402

duckdb = pytest.importorskip("duckdb")

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=120,
)
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
_ISO_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- #
# _normalise_as_of                                                               #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(_TEXT)
def test_normalise_as_of_total_and_shaped(s):
    """Arbitrary input: None (empty-ish) or a YYYY-MM-DD string or
    ValueError — never any other exception, never a non-canonical shape."""
    try:
        out = gq._normalise_as_of(s)
    except ValueError:
        return
    assert out is None or _ISO_SHAPE.match(out)


@_SETTINGS
@given(_TEXT)
def test_normalise_as_of_idempotent(s):
    try:
        out = gq._normalise_as_of(s)
    except ValueError:
        return
    if out is not None:
        assert gq._normalise_as_of(out) == out


@_SETTINGS
@given(st.integers(min_value=1000, max_value=9999))
def test_normalise_as_of_year_canonicalises(year):
    assert gq._normalise_as_of(str(year)) == f"{year}-01-01"


@_SETTINGS
@given(st.integers(min_value=1000, max_value=9999), st.integers(min_value=1, max_value=12))
def test_normalise_as_of_year_month_canonicalises(year, month):
    assert gq._normalise_as_of(f"{year}-{month:02d}") == f"{year}-{month:02d}-01"


@_SETTINGS
@given(st.integers(min_value=1, max_value=12), st.integers(min_value=1, max_value=28))
def test_normalise_as_of_full_date_is_identity(month, day):
    s = f"2024-{month:02d}-{day:02d}"
    assert gq._normalise_as_of(s) == s


# --------------------------------------------------------------------------- #
# _lit — SQL literal rendering                                                   #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(_TEXT)
def test_lit_round_trips_through_duckdb(s):
    """`SELECT <lit>` returns the original string with control characters
    stripped (they are removed, not escaped) — the injection guard holds:
    the literal never breaks out of quoting, whatever the input."""
    con = duckdb.connect()
    try:
        row = con.execute(f"SELECT {gq._lit(s)}").fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[0] == _CONTROLS.sub("", s)


@_SETTINGS
@given(_TEXT)
def test_lit_never_contains_bare_quote(s):
    """Inside the literal, every ' is doubled (the classic escape rule)."""
    lit = gq._lit(s)
    body = lit[1:-1]
    assert lit.startswith("'") and lit.endswith("'")
    # No lone quote inside: every ' in body is part of a '' pair.
    stripped = body.replace("''", "")
    assert "'" not in stripped


# --------------------------------------------------------------------------- #
# notes_like_text guards                                                         #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def emb_con():
    """In-memory v_note_embeddings stand-in with four 2-D company vectors."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE v_note_embeddings (file_path VARCHAR, doc_type VARCHAR, "
        "title VARCHAR, emb FLOAT[2])"
    )
    rows = [
        ("a.md", "company", "A", [1.0, 0.0]),
        ("b.md", "company", "B", [0.9, 0.4358898943540673]),  # cos to A = 0.9
        ("c.md", "company", "C", [0.0, 1.0]),
        ("x.md", "sector", "X", [1.0, 0.0]),  # wrong doc_type
    ]
    for fp, dt, t, v in rows:
        con.execute("INSERT INTO v_note_embeddings VALUES (?, ?, ?, ?)", [fp, dt, t, v])
    return con


@_SETTINGS
@given(st.integers(min_value=-10, max_value=0))
def test_notes_like_text_k_clamped_non_negative(emb_con, k):
    res = gq.notes_like_text(emb_con, "text", k=k, min_sim=-1.0, embed_fn=lambda t: [1.0, 0.0])
    assert res == []


@_SETTINGS
@given(st.floats(min_value=-1.0, max_value=1.0), st.floats(min_value=-1.0, max_value=1.0))
def test_notes_like_text_min_sim_monotone(emb_con, lo, hi):
    """Raising min_sim can only REMOVE results (same k, same embedder)."""
    a, b = min(lo, hi), max(lo, hi)
    res_lo = gq.notes_like_text(emb_con, "text", k=10, min_sim=a, embed_fn=lambda t: [1.0, 0.0])
    res_hi = gq.notes_like_text(emb_con, "text", k=10, min_sim=b, embed_fn=lambda t: [1.0, 0.0])
    assert res_lo is not None and res_hi is not None
    paths_lo = {r[0] for r in res_lo}
    paths_hi = {r[0] for r in res_hi}
    assert paths_hi <= paths_lo
    assert all(r[2] >= b - 1e-9 for r in res_hi)
    assert all(r[2] >= a - 1e-9 for r in res_lo)


@_SETTINGS
@given(_TEXT)
def test_notes_like_text_dim_mismatch_returns_none(emb_con, text):
    """An embedder returning the wrong dimensionality degrades to None —
    never a SQL cast error."""
    res = gq.notes_like_text(emb_con, text, k=5, embed_fn=lambda t: [1.0, 2.0, 3.0])
    assert res is None
