"""Pin ``hyper_arrow.core_mass_arrow`` (test_coverage_gap_sweep_nov24 S10).

Hermetic: a hand-built incidence table, no store, no HGX import.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from helpers.graph.hyper_arrow import core_mass_arrow

_COLS = ["edge_type", "label", "node", "weight"]


def _tbl(rows: list[tuple[str, str, str]]) -> pa.Table:
    return pa.table(
        {c: pa.array([r[i] for r in rows]) for i, c in enumerate(_COLS[:-1])}
        | {"weight": pa.array([None] * len(rows), pa.float64())}
    )


@pytest.fixture
def table() -> pa.Table:
    # core: 2 members in three hyperedges (a, b, c) -> mass 3 * 0.5 = 1.5
    # periph: 1 of 4 in one hyperedge (d)            -> mass 0.25
    # wide:   in a and b only                      -> mass 0.5 + 0.5 = 1.0
    # twin:   same label "a" under another family    -> must not merge with sector:a
    return _tbl(
        [
            ("sector", "a", "core"),
            ("sector", "a", "wide"),
            ("sector", "b", "core"),
            ("sector", "b", "wide"),
            ("sector", "c", "core"),
            ("sector", "c", "other"),
            ("sector", "d", "periph"),
            ("sector", "d", "x1"),
            ("sector", "d", "x2"),
            ("sector", "d", "x3"),
            ("industry", "a", "twin"),
        ]
    )


def _rows(tbl: pa.Table) -> dict[str, dict[str, float]]:
    return {r["node"]: r for r in tbl.to_pylist()}


def test_inverse_size_mass_counts_each_hyperedge_by_its_own_size(table: pa.Table):
    got = _rows(core_mass_arrow(table))
    assert got["core"]["inverse_size_mass"] == pytest.approx(1.5)
    assert got["periph"]["inverse_size_mass"] == pytest.approx(0.25)
    assert got["wide"]["inverse_size_mass"] == pytest.approx(1.0)


def test_companion_columns_and_sort(table: pa.Table):
    got = _rows(core_mass_arrow(table))
    assert list(got) == sorted(got)
    assert got["core"]["n_hyperedges"] == 3
    assert got["core"]["mean_edge_size"] == pytest.approx(2.0)
    assert got["wide"]["max_edge_size"] == 2
    assert got["periph"]["max_edge_size"] == 4


def test_same_label_in_another_family_is_a_different_hyperedge(table: pa.Table):
    got = _rows(core_mass_arrow(table))
    # twin sits alone in industry:a: one hyperedge of size 1 -> mass exactly 1
    assert got["twin"]["n_hyperedges"] == 1
    assert got["twin"]["inverse_size_mass"] == pytest.approx(1.0)


def test_empty_table_gives_empty_result_with_the_contract_columns():
    tbl = pa.table(
        {
            "edge_type": pa.array([], pa.string()),
            "label": pa.array([], pa.string()),
            "node": pa.array([], pa.string()),
            "weight": pa.array([], pa.float64()),
        }
    )
    out = core_mass_arrow(tbl)
    assert out.num_rows == 0
    assert out.column_names == [
        "node",
        "n_hyperedges",
        "mean_edge_size",
        "inverse_size_mass",
        "max_edge_size",
    ]
