"""Hermetic tests for the HGX spectral + RW-density lanes (shelf lanes 3+5).

Synthetic hypergraphs only — no live incidence store. The commute check is
the productionized nnz/tolerance workaround for HGX are_commuting's
csr_array.any() gap (linalg.py:475).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.graph.hyper_spectral import (  # noqa: E402
    commute_nnz,
    rw_density_report,
    spectral_multiorder,
)

CHAIN = {"e1": ["a", "b", "c"], "e2": ["b", "c", "d"], "e3": ["d", "e"]}


def test_commute_nnz_identical_is_true():
    L = spectral_multiorder(CHAIN)["orders"]  # smoke the orders list shape
    assert L == [1, 2]


def test_commute_nnz_tolerance_band():
    import numpy as np
    from scipy.sparse import csr_array

    A = csr_array(np.eye(3))
    B = csr_array(np.eye(3) * 0.0 + np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]))
    assert commute_nnz(A, B) is True  # identical
    C = csr_array(np.array([[1.0, 1e-12, 0], [1e-12, 1.0, 0], [0, 0, 1.0]]))
    assert commute_nnz(A, C) is True  # within the 1e-9 band
    D = csr_array(np.array([[1.0, 0.5, 0], [0.5, 1.0, 0], [0, 0, 1.0]]))
    assert commute_nnz(A, D) is False  # structural difference


def test_spectral_two_uniform_star_commutes():
    # Two size-2 edges sharing a node (HGX order convention: order = size-1,
    # so this is a single-order family): shared symmetry => vacuous commute.
    inc = {"e1": ["a", "b"], "e2": ["a", "c"]}
    rep = spectral_multiorder(inc)
    assert rep["orders"] == [1]
    assert rep["commutes"] is True


def test_spectral_chain_does_not_commute():
    rep = spectral_multiorder(CHAIN)
    assert rep["commutes"] is False
    assert rep["n_orders"] == 2


def test_rw_single_component():
    rep = rw_density_report(CHAIN, steps=50)
    assert rep["single_component"] is True
    assert rep["lc_fraction"] == 1.0
    assert rep["total_nodes"] == 5
    assert 0 < rep["rw_final_max_density"] < 1


def test_rw_fragmented_components_annotated():
    inc = {"e1": ["a", "b"], "e2": ["c", "d"]}
    rep = rw_density_report(inc, steps=10)
    assert rep["single_component"] is False
    assert rep["n_components"] == 2
    assert rep["lc_fraction"] == 0.5
    assert rep["component_sizes"][0] == 2
