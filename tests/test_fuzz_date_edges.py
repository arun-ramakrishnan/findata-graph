#!/usr/bin/env python3
"""Fuzz tests for helpers/maintenance/date_edges.py
(test_coverage_gap_sweep S2) — the edge-dating backfill whose whole
purpose is leak-free as-of studies, so its parsers must be total and
its plan must never invent a date or touch a pre-dated row.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.maintenance import date_edges as de  # noqa: E402

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
)

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATES = st.dates(min_value=date(1900, 1, 1), max_value=date(2099, 12, 31))
_ALL_FAMILIES = (*de.CLASS_A_FAMILIES, *de.CLASS_B_FAMILIES)


# --------------------------------------------------------------------------- #
# _parse_period_end — Class A: '31-MAR-2026' -> ISO                              #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(_DATES)
def test_period_end_roundtrip(day):
    """Any real date, rendered the way RPT filings carry it, parses back
    to exactly that date."""
    assert de._parse_period_end(day.strftime("%d-%b-%Y")) == day.isoformat()


@_SETTINGS
@given(st.text(max_size=40))
def test_period_end_is_total(raw):
    """Arbitrary text → None or ISO shape. Never any other exception —
    a malformed filing label must leave the row UNDATED, not crash the
    backfill (fabricating precision is the failure mode)."""
    out = de._parse_period_end(raw)
    assert out is None or _ISO.match(out)


# --------------------------------------------------------------------------- #
# _ref_date — Class B: first ISO date embedded in the source_ref                 #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(
    st.text(st.characters(blacklist_characters="-"), max_size=20),
    _DATES,
    st.text(st.characters(blacklist_characters="-"), max_size=20),
)
def test_ref_date_finds_first_embedded_iso_date(prefix, day, suffix):
    """The first embedded ISO date in a ref is the evidence date."""
    assert de._ref_date(f"{prefix}{day.isoformat()}{suffix}") == day.isoformat()


@_SETTINGS
@given(st.text(max_size=60))
def test_ref_date_is_total_and_canonical(source_ref):
    """Arbitrary refs → None or an ISO date; when several are embedded the
    FIRST match wins (the conservative earliest-evidence rule)."""
    out = de._ref_date(source_ref)
    if out is None:
        return
    assert _ISO.match(out)
    assert out == re.findall(r"\d{4}-\d{2}-\d{2}", source_ref)[0]


def test_ref_date_none_input():
    assert de._ref_date(None) is None


# --------------------------------------------------------------------------- #
# plan() — model-based oracle: the plan equals the evidence recomputed            #
# --------------------------------------------------------------------------- #
def _expected_date(edge_type: str, source_ref: str | None, properties: str | None) -> str | None:
    """The evidence rule plan() implements, recomputed independently."""
    if edge_type in de.CLASS_A_FAMILIES:
        try:
            pe = json.loads(properties or "{}").get("period_end")
        except ValueError, TypeError:
            pe = None
        return de._parse_period_end(pe) if pe else None
    return de._ref_date(source_ref)


@_SETTINGS
@given(
    st.lists(
        st.tuples(
            st.sampled_from(_ALL_FAMILIES),
            st.one_of(st.none(), _DATES.map(lambda d: d.isoformat())),
            st.one_of(st.none(), st.text(max_size=40)),
            st.one_of(st.none(), st.text(max_size=40)),
        ),
        max_size=20,
    )
)
def test_plan_matches_independent_evidence_model(rows):
    """For arbitrary edge mixes the plan is EXACTLY the set of undated
    rows whose own evidence resolves to an ISO date — never a pre-dated
    row, never a fabricated date, and the per-family census closes."""
    conn = sqlite3.connect(":memory:")
    try:
        db_rows = [
            (f"S{i + 1}", f"T{i + 1}", et, 1.0, vf, None, sr, pr)
            for i, (et, vf, sr, pr) in enumerate(rows)
        ]
        conn.execute(
            "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, "
            "weight REAL, valid_from DATE, valid_to DATE, source_ref TEXT, properties TEXT)"
        )
        conn.executemany("INSERT INTO graph_edges VALUES (?,?,?,?,?,?,?,?)", db_rows)
        plan_rows, census = de.plan(conn)
        # the plan equals the independently recomputed evidence model
        # (rowids are 1-based in a fresh sqlite table)
        expected = {
            (i + 1, expected_date)
            for i, (et, _vf, sr, pr) in enumerate(rows)
            if _vf is None and (expected_date := _expected_date(et, sr, pr)) is not None
        }
        assert set(plan_rows) == expected
        # census arithmetic closes per family
        for et, c in census.items():
            assert c["undated"] == c["datable"] + c["unresolvable"]
            total = sum(1 for r in db_rows if r[2] == et)
            assert c["already_dated"] + c["undated"] == total
    finally:
        conn.close()


@_SETTINGS
@given(st.lists(st.dates().map(lambda d: d.isoformat()), max_size=30))
def test_histogram_buckets_close_and_sort(dates):
    """Buckets are YYYY-MM prefixes, sorted, and sum to the input."""
    hist = de._histogram(dates)
    assert list(hist) == sorted(hist)
    assert sum(hist.values()) == len(dates)
    assert all(len(k) == 7 and _ISO.match(k + "-01") for k in hist)
