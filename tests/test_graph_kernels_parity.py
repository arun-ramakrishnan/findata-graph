"""Rust-vs-scipy parity for the personalized PageRank kernels.

S2 of test_coverage_gap_sweep_nov24. The L1 fold parity (test_l1_kernel_parity.py)
covers the BFS-based kernels; the PPR kernels are the other Rust surface,
and their agreement with the exact scipy solve is pinned here numerically.

- `personalized_pagerank_batch`: batched one-hot PPR ((k, n) float64,
  rayon across seeds). The bridge's fallback lane solves each seed with
  `spsolve`; the two lanes must agree at 1e-9 relative on the same
  binary CSR — including graphs with dangling nodes, where both lanes
  teleport the dangling mass to the seed.
- `personalized_pagerank_vec`: the SET-teleport kernel (S5,
  2026-11-14). It is exported but currently has no production caller —
  pinned as a surface: one-hot teleport must reduce to
  `personalized_pagerank`, arbitrary teleports must converge to a
  probability vector (sum 1), and the solution must be linear in the
  teleport on dangling-free graphs. Its input validation is pinned too,
  because a raw kernel call bypassing the bridge must still reject
  malformed graphs/teleports loudly.

The kernels are optional (`graph_kernels` extension); every test skips
when it is absent, matching the house fixture pattern.
"""

from __future__ import annotations

import os
import re
import types
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from typing import TYPE_CHECKING

from helpers.graph.scipy_bridge import _ppr_spsolve_lane

if TYPE_CHECKING:
    from scipy.sparse import csr_matrix

try:  # the kernels are optional; tests skip without them
    import graph_kernels
except ImportError:  # pragma: no cover - exercised on a machine without cargo
    graph_kernels = None

_HAVE_PPR_KERNEL = graph_kernels is not None and hasattr(graph_kernels, "personalized_pagerank")
_HAVE_PPR_BATCH = graph_kernels is not None and hasattr(
    graph_kernels, "personalized_pagerank_batch"
)
_HAVE_PPR_VEC = graph_kernels is not None and hasattr(graph_kernels, "personalized_pagerank_vec")
_NEEDS_PPR = pytest.mark.skipif(
    not (_HAVE_PPR_KERNEL and _HAVE_PPR_BATCH),
    reason="graph_kernels ppr lanes absent",
)
_NEEDS_PPR_VEC = pytest.mark.skipif(
    not _HAVE_PPR_VEC, reason="graph_kernels personalized_pagerank_vec absent"
)

PPR_REL_EPS = 1e-9
ALPHAS = (0.5, 0.85, 0.95)

# Every `#[pyfunction]` Rust/graph_kernels registers (pinned against the crate
# source below). A lane missing from the installed .so is a stale build.
EXPORTED_LANES = (
    "all_pairs_bfs_stats",
    "brandes_fold_sources",
    "core_bfs_sources_batch",
    "hop_hist_sources",
    "limited_bfs_sources",
    "personalized_pagerank",
    "personalized_pagerank_batch",
    "personalized_pagerank_vec",
)


def _missing_lanes(module: object) -> list[str]:
    return [lane for lane in EXPORTED_LANES if module is None or not hasattr(module, lane)]


def test_every_exported_lane_is_present_under_qa():
    """T1 of test_coverage_gap_sweep_nov24: one loud failure for a missing or
    stale `.so`.

    Measured 2026-11-24 with the import blocked on the 7 test files that name
    the extension (197 tests): 24 skip, 2 fail by accident, 170 pass on the
    Python fallback — a qa run stays green while every kernel pin vanishes.
    `GRAPH_KERNELS_STRICT=1` (set by the qa harness, the SCRIPT_KNN_STRICT
    pattern) turns that into this failure, naming the missing lanes.
    """
    missing = _missing_lanes(graph_kernels)
    if not missing:
        return
    detail = f"graph_kernels extension is missing lanes: {', '.join(missing)}"
    if os.environ.get("GRAPH_KERNELS_STRICT") == "1":
        pytest.fail(f"{detail} — qa requires the built extension (make rust-install)")
    pytest.skip(detail)


def test_lane_list_matches_the_crate_and_a_stale_build_is_named():
    """The strict pin is only as good as its lane list: it must equal what the
    crate exports, and a stale .so (importable, one lane short) must be told
    apart from a fresh one."""
    source = (
        Path(__file__).resolve().parents[1] / "Rust" / "graph_kernels" / "src" / "lib.rs"
    ).read_text()
    exported = set(re.findall(r"wrap_pyfunction!\((\w+),", source))
    assert exported == set(EXPORTED_LANES)

    stale = types.SimpleNamespace(**{lane: object() for lane in EXPORTED_LANES[:-1]})
    assert _missing_lanes(stale) == [EXPORTED_LANES[-1]]
    assert _missing_lanes(None) == list(EXPORTED_LANES)
    assert (
        _missing_lanes(types.SimpleNamespace(**{lane: object() for lane in EXPORTED_LANES})) == []
    )


def _symmetric_binary_csr(n: int, edges: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    """Build int32 (indptr, indices) for an undirected binary graph."""
    from scipy.sparse import csr_matrix

    rows: list[int] = []
    cols: list[int] = []
    seen: set[tuple[int, int]] = set()
    for i, j in edges:
        if i == j or i >= n or j >= n:
            continue
        for a, b in ((i, j), (j, i)):
            if (a, b) not in seen:
                seen.add((a, b))
                rows.append(a)
                cols.append(b)
    csr = csr_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    csr.sort_indices()
    indptr = np.ascontiguousarray(csr.indptr, dtype=np.int32)
    indices = np.ascontiguousarray(csr.indices, dtype=np.int32)
    return indptr, indices


def _csr_from_kernel_inputs(indptr: np.ndarray, indices: np.ndarray) -> csr_matrix:
    from scipy.sparse import csr_matrix

    n = len(indptr) - 1
    return csr_matrix(
        (np.ones(len(indices), dtype=np.int8), indices, indptr),
        shape=(n, n),
    )


def _rel(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    assert a.shape == b.shape, f"shape {a.shape} vs {b.shape}"
    denom = np.maximum(np.maximum(np.abs(a), np.abs(b)), 1.0)
    return float(np.max(np.abs(a - b) / denom)) if a.size else 0.0


#: graphs with 4..24 nodes, edge prob 0.2..0.7; deliberately allows
#: isolated (dangling) nodes to exercise the teleport semantics.
_graphs = st.builds(
    lambda n, p: (n, [(i, j) for i in range(n) for j in range(i + 1, n) if np.random.rand() < p]),
    n=st.integers(min_value=4, max_value=24),
    p=st.floats(min_value=0.2, max_value=0.7),
).map(lambda t: (t[0], t[1]))


@_NEEDS_PPR
class TestPprBatchParity:
    @given(_graphs, st.sampled_from(ALPHAS), st.integers(min_value=1, max_value=5))
    @settings(max_examples=30, deadline=None)
    def test_batch_matches_spsolve_per_seed(self, graph, alpha, n_seeds):
        assert graph_kernels is not None
        n, edges = graph
        if n_seeds > n:
            n_seeds = n
        indptr, indices = _symmetric_binary_csr(n, edges)
        seeds = list(range(n_seeds)) if n_seeds else []
        rows = graph_kernels.personalized_pagerank_batch(
            indptr, indices, np.asarray(seeds, dtype=np.int32), alpha, 1e-12, 1000
        )
        assert rows.shape == (len(seeds), n)
        assert np.all(np.isfinite(rows))
        assert np.all(rows >= -1e-12)
        assert np.allclose(rows.sum(axis=1), 1.0, atol=1e-9)
        A = _csr_from_kernel_inputs(indptr, indices)
        for k, seed in enumerate(seeds):
            ref = _ppr_spsolve_lane(A, seed, alpha)
            assert _rel(rows[k], ref) <= PPR_REL_EPS, (
                f"seed {seed} alpha {alpha}: kernel vs spsolve rel {_rel(rows[k], ref):.3e}"
            )

    @given(_graphs, st.sampled_from(ALPHAS), st.integers(min_value=2, max_value=5))
    @settings(max_examples=20, deadline=None)
    def test_batch_rows_are_one_hot_solutions(self, graph, alpha, n_seeds):
        """Batched row k equals the single-seed kernel called for seed k."""
        assert graph_kernels is not None
        n, edges = graph
        if n_seeds > n:
            n_seeds = n
        indptr, indices = _symmetric_binary_csr(n, edges)
        seeds = list(range(n_seeds)) if n_seeds else []
        rows = graph_kernels.personalized_pagerank_batch(
            indptr, indices, np.asarray(seeds, dtype=np.int32), alpha, 1e-12, 1000
        )
        for k, seed in enumerate(seeds):
            single = graph_kernels.personalized_pagerank(indptr, indices, seed, alpha, 1e-12, 1000)
            assert _rel(rows[k], single) <= PPR_REL_EPS


@_NEEDS_PPR_VEC
class TestPprVecParity:
    @given(_graphs, st.sampled_from(ALPHAS), st.integers(min_value=0, max_value=24))
    @settings(max_examples=25, deadline=None)
    def test_one_hot_teleport_reduces_to_personalized_pagerank(self, graph, alpha, seed):
        assert graph_kernels is not None
        n, edges = graph
        if seed >= n:
            seed %= n
        indptr, indices = _symmetric_binary_csr(n, edges)
        v = np.zeros(n, dtype=np.float64)
        v[seed] = 1.0
        x = graph_kernels.personalized_pagerank_vec(indptr, indices, v, alpha, 1e-12, 1000)
        ref = graph_kernels.personalized_pagerank(indptr, indices, seed, alpha, 1e-12, 1000)
        assert _rel(x, ref) <= PPR_REL_EPS

    @given(_graphs, st.sampled_from(ALPHAS))
    @settings(max_examples=15, deadline=None)
    def test_arbitrary_teleport_sums_to_one(self, graph, alpha):
        assert graph_kernels is not None
        n, edges = graph
        indptr, indices = _symmetric_binary_csr(n, edges)
        rng = np.random.default_rng(0)
        v = rng.random(n) + 0.01
        x = graph_kernels.personalized_pagerank_vec(indptr, indices, v, alpha, 1e-12, 1000)
        assert np.all(np.isfinite(x))
        assert np.all(x >= -1e-12)
        assert np.isclose(x.sum(), 1.0, atol=1e-9)

    @given(_graphs, st.sampled_from(ALPHAS))
    @settings(max_examples=15, deadline=None)
    def test_no_dangling_linear_in_teleport(self, graph, alpha):
        """On dangling-free graphs PPR is linear in v: x(v)=sum_i v_i x(e_i)."""
        assert graph_kernels is not None
        n, edges = graph
        # force every node to have degree >= 1
        all_nodes = set(range(n))
        incident: set[int] = set()
        for i, j in edges:
            incident.add(i)
            incident.add(j)
        if len(incident) < n:
            extra = all_nodes - incident
            # connect each missing node to node 0 (unless it is node 0)
            for node in extra:
                peer = 0 if node != 0 else 1
                edges.append((node, peer))
        indptr, indices = _symmetric_binary_csr(n, edges)
        rng = np.random.default_rng(1)
        v = rng.random(n) + 0.01
        v /= v.sum()
        x = graph_kernels.personalized_pagerank_vec(indptr, indices, v, alpha, 1e-12, 1000)
        combo = np.zeros(n, dtype=np.float64)
        for i in range(n):
            e = np.zeros(n, dtype=np.float64)
            e[i] = 1.0
            combo += v[i] * graph_kernels.personalized_pagerank_vec(
                indptr, indices, e, alpha, 1e-12, 1000
            )
        assert _rel(x, combo) <= PPR_REL_EPS


@_NEEDS_PPR_VEC
class TestPprVecGuards:
    """Validation must reject malformed graphs/teleports loudly.

    The bridge validates graph shapes before calling the kernel; these
    call the raw kernel to pin that the Rust-side guards exist for
    callers that do not pass through the bridge.
    """

    @pytest.mark.parametrize(
        ("indptr", "indices", "v", "alpha", "tol", "max_iter", "message"),
        [
            (
                np.array([], dtype=np.int32),
                np.array([], dtype=np.int32),
                np.array([0.0]),
                0.85,
                1e-12,
                1000,
                "indptr must carry at least the [0] terminator",
            ),
            (
                np.array([1, 0], dtype=np.int32),
                np.array([], dtype=np.int32),
                np.array([1.0, 0.0]),
                0.85,
                1e-12,
                1000,
                "indptr must be non-decreasing",
            ),
            (
                np.array([0, 2], dtype=np.int32),
                np.array([0], dtype=np.int32),
                np.array([1.0, 0.0]),
                0.85,
                1e-12,
                1000,
                "indptr[-1] must equal len(indices)",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([2, 0], dtype=np.int32),
                np.array([1.0, 0.0]),
                0.85,
                1e-12,
                1000,
                "indices out of range",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([1, 0], dtype=np.int32),
                np.array([1.0, 0.0]),
                1.5,
                1e-12,
                1000,
                "alpha must be in (0, 1)",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([1, 0], dtype=np.int32),
                np.array([1.0, 0.0]),
                0.85,
                0.0,
                1000,
                "tol must be positive",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([1, 0], dtype=np.int32),
                np.array([1.0, 0.0]),
                0.85,
                1e-12,
                0,
                "max_iter must be >= 1",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([1, 0], dtype=np.int32),
                np.array([1.0]),
                0.85,
                1e-12,
                1000,
                "teleport length must equal the node count",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([1, 0], dtype=np.int32),
                np.array([1.0, -0.5]),
                0.85,
                1e-12,
                1000,
                "teleport entries must be finite and non-negative",
            ),
            (
                np.array([0, 1, 2], dtype=np.int32),
                np.array([1, 0], dtype=np.int32),
                np.array([0.0, 0.0]),
                0.85,
                1e-12,
                1000,
                "teleport must have positive mass",
            ),
        ],
    )
    def test_rejects_malformed_inputs(self, indptr, indices, v, alpha, tol, max_iter, message):
        assert graph_kernels is not None
        with pytest.raises(ValueError, match=re.escape(message)):
            graph_kernels.personalized_pagerank_vec(indptr, indices, v, alpha, tol, max_iter)

    def test_rejects_non_finite_teleport(self):
        assert graph_kernels is not None
        indptr, indices = _symmetric_binary_csr(3, [(0, 1)])
        v = np.array([1.0, np.inf, 0.0])
        with pytest.raises(ValueError, match=re.escape("finite and non-negative")):
            graph_kernels.personalized_pagerank_vec(indptr, indices, v, 0.85, 1e-12, 1000)
