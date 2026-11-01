#!/usr/bin/env python3
"""The as-of leak property, as an executable Hypothesis property
(test_coverage_gap_sweep S2).

The alpha lane's doctrine (graph_alpha_lane #462: undated edges are
LOOKAHEAD for backtesting) as a generated-input invariant: for ANY edge
set and ANY probe date, ``edges_as_of`` returns exactly the rows whose
validity window covers the probe — never a row born after the probe,
never a row already dead at the probe. Soundness AND completeness, on
arbitrary inputs rather than the 4 fixed unit rows in test_as_of.py.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.graph import as_of as ao  # noqa: E402

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
)

_DATES = st.dates(min_value=date(2024, 1, 1), max_value=date(2027, 12, 31)).map(
    lambda d: d.isoformat()
)
# windows built FROM the probe pool so boundary hits (valid_from == probe,
# valid_to == probe) occur with meaningful probability
_BOUNDS = st.one_of(st.none(), _DATES)
_TYPES = st.sampled_from(["acquired", "supplier_to", "competes_with", "jv_with", ""])


def _seed(edges):
    """In-memory store over generated (source, target, type, from, to) rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, "
        "weight REAL DEFAULT 1.0, valid_from DATE, valid_to DATE, source_ref TEXT)"
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, valid_from, valid_to, source_ref) "
        "VALUES (?,?,?,?,?,?)",
        [(f"S{i}", f"T{i}", et, vf, vt, f"r{i}") for i, (et, vf, vt) in enumerate(edges)],
    )
    return conn


def _covers(row_from: str | None, row_to: str | None, probe: str) -> bool:
    """The docstring's visibility predicate, independent of the SQL."""
    return (row_from is None or row_from <= probe) and (row_to is None or row_to > probe)


@_SETTINGS
@given(
    st.lists(st.tuples(_TYPES, _BOUNDS, _BOUNDS), min_size=1, max_size=25),
    _DATES,
)
def test_as_of_never_leaks(edges, probe):
    """SOUNDNESS: every returned row's window covers the probe (no
    lookahead row, no expired row). COMPLETENESS: every covering row is
    returned — the visible set equals the predicate, exactly."""
    conn = _seed(edges)
    try:
        visible = ao.edges_as_of(conn, probe)
        stored = conn.execute(
            "SELECT source, target, valid_from, valid_to FROM graph_edges"
        ).fetchall()
        expected = {(s, t) for s, t, vf, vt in stored if _covers(vf, vt, probe)}
        got = {(r["source"], r["target"]) for r in visible}
        # no lookahead: nothing born after the probe is visible
        assert all(r["valid_from"] is None or r["valid_from"] <= probe for r in visible)
        # no resurrection: nothing dead at the probe is visible
        assert all(r["valid_to"] is None or r["valid_to"] > probe for r in visible)
        # and the two together are exactly the predicate (completeness)
        assert got == expected
    finally:
        conn.close()


@_SETTINGS
@given(
    st.lists(st.tuples(_TYPES, _BOUNDS, _BOUNDS), min_size=1, max_size=25),
    _DATES,
)
def test_summary_totals_match_edges_as_of(edges, probe):
    """The per-type summary is a partition of the visible set."""
    conn = _seed(edges)
    try:
        summary = ao.as_of_summary(conn, probe)
        assert sum(summary.values()) == len(ao.edges_as_of(conn, probe))
    finally:
        conn.close()


# differential()'s drift identity: FIXED 2026-11-01 (was a FINDING) —
# the expired class now covers from-NULL/to-dated rows, so the property
# holds on UNRESTRICTED windows (both bounds, either, or neither).
@_SETTINGS
@given(
    st.lists(st.tuples(_TYPES, _BOUNDS, _BOUNDS), min_size=1, max_size=25),
    _DATES,
)
def test_differential_drift_classes_partition(edges, probe):
    """expired + not-yet-born + as-of-visible == live total (every live
    row is in exactly one of the three states) — on arbitrary windows,
    including from-NULL/to-dated and born-after-expired rows."""
    conn = _seed(edges)
    try:
        d = ao.differential(conn, probe)
        assert d["asof_total"] + d["expired_at"] + d["not_yet_born"] == d["live_total"]
    finally:
        conn.close()


def test_differential_classifies_from_null_to_dated_as_expired():
    """FIXED 2026-11-01 (was FINDING): a row with valid_from=NULL but
    valid_to set is invisible at the probe AND now lands in expired_at —
    the census identity holds on that domain. edges_as_of was always
    leak-safe (the row is invisible); only the drift census under-reported."""
    conn = _seed([("acquired", None, "2026-01-01")])
    try:
        assert ao.edges_as_of(conn, "2026-06-30") == []  # leak-safe: invisible
        d = ao.differential(conn, "2026-06-30")
        assert d["live_total"] == 1
        assert d["asof_total"] == 0
        assert d["expired_at"] == 1  # classified, not vanished
        assert d["not_yet_born"] == 0
    finally:
        conn.close()


def test_differential_born_after_and_expired_counts_as_not_yet():
    """Disjointness edge the fix had to preserve: a row born AFTER the
    probe with an already-closed window counts as not_yet_born (birth
    wins), never in both classes."""
    conn = _seed([("acquired", "2026-07-01", "2026-07-15")])
    try:
        d = ao.differential(conn, "2026-06-30")
        assert d["asof_total"] == 0
        assert d["not_yet_born"] == 1
        assert d["expired_at"] == 0
    finally:
        conn.close()


def test_property_is_discriminating():
    """The can-fail proof (proposal acceptance 2): the leak check rejects
    a hand-built leaking row — a row born one day AFTER the probe must
    fail the soundness assertion the properties above rely on."""
    leak_probe = "2026-06-30"
    future = (date(2026, 6, 30) + timedelta(days=1)).isoformat()
    conn = _seed([("acquired", future, None)])  # born 2026-07-01
    try:
        rows = ao.edges_as_of(conn, leak_probe)
        assert rows == []  # the implementation refuses the future row
        # and the predicate the properties assert would flag it if it leaked
        assert not _covers(future, None, leak_probe)
    finally:
        conn.close()
