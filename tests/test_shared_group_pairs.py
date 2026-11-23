"""Pin ``hyper_arrow.shared_group_pairs_arrow`` (test_coverage_gap_sweep_nov24 S3 item 3).

Hermetic: hand-built incidence tables, no store, no HGX import.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from helpers.graph.hyper_arrow import shared_group_pairs_arrow


def _tbl(rows: list[tuple[str, str, str]]) -> pa.Table:
    return pa.table(
        {
            "edge_type": pa.array([r[0] for r in rows], pa.string()),
            "label": pa.array([r[1] for r in rows], pa.string()),
            "node": pa.array([r[2] for r in rows], pa.string()),
            "weight": pa.array([None] * len(rows), pa.float64()),
        }
    )


@pytest.fixture
def table() -> pa.Table:
    # hyperedge sector:a holds A,B; sector:b holds A,B  -> pair (A,B): 2 shared, mass 1.0
    # hyperedge sector:c holds C; sector:d holds D      -> no pair (distinct edges)
    # hyperedge sector:e holds C,D                      -> pair (C,D): 1 shared, mass 0.5
    # hyperedge sector:big holds x0..x204               -> excluded at the default cap
    # hyperedge industry:a holds twin alone             -> no pair anywhere
    rows = [
        ("sector", "a", "A"),
        ("sector", "a", "B"),
        ("sector", "b", "A"),
        ("sector", "b", "B"),
        ("sector", "c", "C"),
        ("sector", "d", "D"),
        ("sector", "e", "C"),
        ("sector", "e", "D"),
        ("industry", "a", "twin"),
    ]
    rows += [("sector", "big", f"x{i}") for i in range(205)]
    return _tbl(rows)


def _pairs(tbl: pa.Table) -> dict[tuple[str, str], tuple[int, float]]:
    return {
        (r["node_a"], r["node_b"]): (r["shared_hyperedges"], r["shared_inverse_size_mass"])
        for r in tbl.to_pylist()
    }


def test_shared_count_and_mass(table: pa.Table):
    got = _pairs(shared_group_pairs_arrow(table, max_edge_members=10))
    assert got[("A", "B")] == (2, pytest.approx(1.0))
    assert got[("C", "D")] == (1, pytest.approx(0.5))
    # each pair listed once, node_a < node_b
    assert all(a < b for a, b in got)


def test_giant_edges_are_background_at_the_default_cap(table: pa.Table):
    got = _pairs(shared_group_pairs_arrow(table))
    # 205-member edge: excluded entirely, so its pairs are absent
    assert not any("x1" in pair for pair in got)
    assert set(got) == {("A", "B"), ("C", "D")}


def test_low_cap_changes_which_pairs_survive(table: pa.Table):
    got = _pairs(shared_group_pairs_arrow(table, max_edge_members=4))
    assert ("A", "B") in got and ("C", "D") in got
    assert len(got) == 2


def test_min_shared_filters_accidental_comembership(table: pa.Table):
    got = _pairs(shared_group_pairs_arrow(table, max_edge_members=10, min_shared=2))
    assert list(got) == [("A", "B")]


def test_same_label_in_another_family_never_merges(table: pa.Table):
    got = _pairs(shared_group_pairs_arrow(table, max_edge_members=10))
    # sector:a and industry:a are different hyperedges: A/twin share nothing
    assert ("A", "twin") not in got


def test_empty_table_gives_empty_result_with_the_contract_columns():
    tbl = _tbl([])
    out = shared_group_pairs_arrow(tbl)
    assert out.num_rows == 0
    assert out.column_names == [
        "node_a",
        "node_b",
        "shared_hyperedges",
        "shared_inverse_size_mass",
    ]
