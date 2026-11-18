"""Rust-vs-numpy parity for the two L1 fold kernels.

`helpers/misc/parity_harness.py` byte-compares rendered text, which cannot
judge a float lane that legitimately reorders reductions — so the kernel
agreement is pinned HERE, numerically, at a documented epsilon:

- betweenness: the two folds accumulate the same f64 sums in a different
  order (numpy `np.bincount` scatter vs the kernel's reverse-level walk;
  rayon parts vs `fork_map` chunks). Agreement is asserted at 1e-9
  RELATIVE, ~4 orders tighter than the smallest digit either lane can
  shift (measured max 4.6e-13 on the live store) and ~8 orders tighter
  than any rank or score the lane publishes (values are normalized and
  rendered at 1e-12 against the stamped contract).
- closeness: distances are INTEGERS, so the full pipeline must agree
  EXACTLY — no epsilon, no tolerance.

Both kernels are opt-in (`L1B_RUST` / `L1C_RUST`); the selector semantics
themselves are covered too, because a lane that silently falls back to
numpy would make every parity test above vacuous.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from helpers.graph import l1_betweenness as lb
from helpers.graph import l1_closeness as lc
from helpers.graph.fixture_graph_db import build_graph_fixture_db

try:  # the kernels are optional; without them the selector must fall back
    import graph_kernels
except ImportError:  # pragma: no cover - exercised on a machine without cargo
    graph_kernels = None

_HAVE_BRANDES = graph_kernels is not None and hasattr(graph_kernels, "brandes_fold_sources")
_HAVE_CORE_BFS = graph_kernels is not None and hasattr(graph_kernels, "core_bfs_sources_batch")
_NEEDS_BRANDES = pytest.mark.skipif(not _HAVE_BRANDES, reason="graph_kernels brandes lane absent")
_NEEDS_CORE_BFS = pytest.mark.skipif(
    not _HAVE_CORE_BFS, reason="graph_kernels core-bfs lane absent"
)

#: Relative agreement required of the betweenness folds (see module docstring).
BETWEENNESS_EPS = 1e-9


def _rel(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    assert a.shape == b.shape, f"shape {a.shape} vs {b.shape}"
    denom = np.maximum(np.maximum(np.abs(a), np.abs(b)), 1.0)
    return float(np.max(np.abs(a - b) / denom)) if a.size else 0.0


@pytest.fixture()
def fixture_db() -> Path:
    with tempfile.TemporaryDirectory(prefix="l1-kernel-") as tmp:
        yield build_graph_fixture_db(Path(tmp) / "fixture.db")


def _betweenness_inputs(db_path: Path):
    names, edges = lb.load_projection(db_path)
    n = len(names)
    indptr, indices = lb.build_adjacency(n, edges)
    mask, removed = lb.peel_two_core(indptr, indices, n)
    core = np.flatnonzero(mask)
    core_set = set(core.tolist())
    adj_sets = lc.build_adj_sets(edges)
    folded, _specials = lb.tree_components(removed, adj_sets, core_set)
    kw, _att = lb._compute_attachment_weights(folded, adj_sets, core_set, n)
    return indptr, indices, np.array(core, dtype=np.int64), kw, mask


class TestLaneSelector:
    """`_rust_lane` is the only place the switch lives; pin its vocabulary.

    House convention: the kernel is the DEFAULT (unset => auto), and the
    env var opts OUT. A flipped default would silently drop a 13x/2.4x
    win on every call, so the default itself is asserted here.
    """

    @pytest.mark.parametrize("value", ["0", "off", "false", "no", "numpy", " OFF "])
    def test_off_values(self, monkeypatch, value):
        monkeypatch.setenv("L1B_RUST", value)
        assert lb._rust_lane("L1B_RUST") == "off"
        monkeypatch.setenv("L1C_RUST", value)
        assert lc._rust_lane("L1C_RUST") == "off"

    @pytest.mark.parametrize("value", [None, "", "auto"])
    def test_unset_and_auto_mean_default(self, monkeypatch, value):
        """Unset must NOT mean "off" — the kernel is the default lane."""
        if value is None:
            monkeypatch.delenv("L1B_RUST", raising=False)
            monkeypatch.delenv("L1C_RUST", raising=False)
        else:
            monkeypatch.setenv("L1B_RUST", value)
            monkeypatch.setenv("L1C_RUST", value)
        assert lb._rust_lane("L1B_RUST") == "auto"
        assert lc._rust_lane("L1C_RUST") == "auto"

    def test_kernel_is_taken_at_rest(self, fixture_db, monkeypatch):
        """With no env var set, the batched kernel lane must actually run."""
        monkeypatch.delenv("L1B_RUST", raising=False)
        monkeypatch.delenv("L1C_RUST", raising=False)
        indptr, indices, sources, kw, is_core = _betweenness_inputs(fixture_db)
        if _HAVE_BRANDES:
            assert (
                lb._kernel_brandes_core(indptr, indices, sources, kw, is_core, force_parallel=False)
                is not None
            )
        if _HAVE_CORE_BFS:
            assert (
                lc._kernel_core_bfs(
                    indptr,
                    indices,
                    sources,
                )
                is not None
            )

    def test_forced(self, monkeypatch):
        monkeypatch.setenv("L1B_RUST", "1")
        assert lb._rust_lane("L1B_RUST") == "force"
        monkeypatch.setenv("L1C_RUST", "rust")
        assert lc._rust_lane("L1C_RUST") == "force"

    def test_off_never_touches_the_kernel(self, monkeypatch):
        """Lane off must return None even though the symbol exists."""
        if _HAVE_BRANDES:
            monkeypatch.setenv("L1B_RUST", "0")
            assert (
                lb._kernel_brandes_core(
                    np.array([0, 1], dtype=np.int64),
                    np.array([0], dtype=np.int64),
                    np.array([0], dtype=np.int64),
                    np.zeros(1),
                    np.ones(1, dtype=bool),
                    force_parallel=False,
                )
                is None
            )
        if _HAVE_CORE_BFS:
            monkeypatch.setenv("L1C_RUST", "0")
            assert (
                lc._kernel_core_bfs(
                    np.array([0, 0], dtype=np.int64),
                    np.array([], dtype=np.int64),
                    np.array([0], dtype=np.int64),
                )
                is None
            )


@_NEEDS_BRANDES
class TestBrandesKernelParity:
    def test_fold_matches_the_numpy_lane(self, fixture_db, monkeypatch):
        indptr, indices, sources, kw, is_core = _betweenness_inputs(fixture_db)

        monkeypatch.setenv("L1B_RUST", "0")
        ref = lb.brandes_core(indptr, indices, sources, kw, jobs=1, is_core=is_core)

        monkeypatch.setenv("L1B_RUST", "1")
        got = lb.brandes_core(indptr, indices, sources, kw, jobs=1, is_core=is_core)

        for name, a, b in zip(("U", "TU", "KK"), ref, got):
            rel = _rel(np.asarray(a), np.asarray(b))
            assert rel <= BETWEENNESS_EPS, f"{name} drift {rel:g} > {BETWEENNESS_EPS:g}"

    def test_kernel_is_actually_used(self, fixture_db, monkeypatch):
        """A silent fallback would make the parity test vacuous."""
        monkeypatch.setenv("L1B_RUST", "1")
        indptr, indices, sources, kw, is_core = _betweenness_inputs(fixture_db)
        assert (
            lb._kernel_brandes_core(indptr, indices, sources, kw, is_core, force_parallel=False)
            is not None
        )

    def test_forced_lane_fails_loud_without_the_symbol(self, monkeypatch):
        monkeypatch.setenv("L1B_RUST", "1")
        # Simulate a stale build: the module imports but lacks the symbol.
        import types

        stub = types.SimpleNamespace()
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def fake_import(name, *args, **kwargs):
            if name == "graph_kernels":
                return stub
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        with pytest.raises(RuntimeError, match="lacks brandes_fold_sources"):
            lb._kernel_brandes_core(
                np.array([0, 1], dtype=np.int64),
                np.array([0], dtype=np.int64),
                np.array([0], dtype=np.int64),
                np.zeros(1),
                np.ones(1, dtype=bool),
                force_parallel=False,
            )


@_NEEDS_CORE_BFS
class TestCoreBfsKernelParity:
    def test_batch_matches_single_source_bfs(self, fixture_db, monkeypatch):
        names, edges = lc.load_projection(fixture_db)
        n = len(names)
        indptr, indices = lc.build_adjacency(n, edges)
        mask, _removed = lc.peel_two_core(indptr, indices, n)
        is_core = np.zeros(n, dtype=bool)
        is_core[mask] = True
        core_indptr, core_indices = lc.build_core_adjacency(indptr, indices, is_core)
        sources = [int(s) for s in np.flatnonzero(is_core)]

        monkeypatch.setenv("L1C_RUST", "1")
        got = lc.core_bfs_batch(sources, core_indptr, core_indices)
        assert set(got) == set(sources)

        for u in sources:
            ref = lc.core_bfs(u, core_indptr, core_indices)
            np.testing.assert_array_equal(got[u], ref)

    def test_batch_cap_splits_without_changing_results(self, fixture_db, monkeypatch):
        """Sources beyond `_SOURCE_BATCH` take a second kernel call."""
        names, edges = lc.load_projection(fixture_db)
        n = len(names)
        indptr, indices = lc.build_adjacency(n, edges)
        is_core = np.ones(n, dtype=bool)
        core_indptr, core_indices = lc.build_core_adjacency(indptr, indices, is_core)

        monkeypatch.setenv("L1C_RUST", "1")
        one = lc.core_bfs_batch([0, 1], core_indptr, core_indices)
        monkeypatch.setattr(lc, "_SOURCE_BATCH", 1)
        capped = lc.core_bfs_batch([0, 1, 0], core_indptr, core_indices)

        assert set(capped) == {0, 1}  # duplicates collapse
        for u in capped:
            np.testing.assert_array_equal(capped[u], one[u])


@_NEEDS_CORE_BFS
def test_full_pipeline_is_identical(fixture_db, monkeypatch):
    """Integers in, integers out: the whole closeness fold must match exactly."""
    monkeypatch.setenv("L1C_RUST", "0")
    ref, ref_info = lc.compute(fixture_db, jobs=1)
    monkeypatch.setenv("L1C_RUST", "1")
    got, got_info = lc.compute(fixture_db, jobs=1)

    assert ref_info == got_info
    assert sorted(ref) == sorted(got)
    assert ref == got  # exact float equality is correct here
