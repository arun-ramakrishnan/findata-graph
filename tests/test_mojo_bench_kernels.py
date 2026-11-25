"""S7 differentials: Mojo bench kernels vs Python equivalents.

Four untested ``Mojo/src/bench/*`` kernels, checked against their Python
oracles where one exists (skips when the Mojo binary is not built —
``make mojo-build``):

- ``a2_topk_batch`` (dense shared-count top-K) vs a plain Python
  sorted top-K over the same matrix;
- ``bfs_bipartite_all_batch`` (batched bipartite BFS trails) vs
  ``helpers.graph.hyper_trails.trail_source_stats``;
- ``motifs_null_batch`` (configuration-null census) vs
  ``helpers.graph.hyper_motifs.configuration_null`` (same RNG stream,
  so draws are identical and numbers must agree closely);
- ``window_shingles`` (golden-fixture parity leg built into the
  binary) — exit 0/``0 failed``.

Hermetic: synthetic data only; no live stores. Topic: coverage-gap
sweep S7 (doc/improvements/proposals/test_coverage_gap_sweep_nov24.md).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.graph import hyper_motifs as hm  # noqa: E402
from helpers.graph import hyper_trails as ht  # noqa: E402
from helpers.graph.hyper_trails import (  # noqa: E402
    bipartite_from_pairs,
    trail_source_stats,
)

_MOJO_BIN = _REPO / "Mojo" / "bin"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{_REPO / '.venv' / 'bin'}:{env.get('PATH', '')}"
    return env


def _run_bin(binary: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(_REPO),
        env=_env(),
    )


# ----------------------------------------------------------------------
# a2_topk_batch
# ----------------------------------------------------------------------


class TestA2TopkDifferential:
    BINARY = _MOJO_BIN / "a2_topk_batch"

    @staticmethod
    def _expected_topk(matrix: np.ndarray, k: int) -> list[tuple[int, int, int]]:
        n = matrix.shape[0]
        entries = [
            (int(matrix[i, j]), i, j) for i in range(n) for j in range(i + 1, n) if matrix[i, j] > 0
        ]
        entries.sort(key=lambda t: (-t[0], t[1], t[2]))
        return entries[:k]

    def test_topk_toy(self, tmp_path: Path):
        if not self.BINARY.exists():
            pytest.skip("a2_topk_batch not built (make mojo-build)")
        n, k = 5, 6
        rng = np.random.default_rng(11)
        mat = rng.integers(0, 5, size=(n, n), dtype=np.int64)
        mat = np.triu(mat, 1) + np.triu(mat, 1).T  # symmetric, zero diagonal
        bin_path = tmp_path / "shared.bin"
        bin_path.write_bytes(np.ascontiguousarray(mat, dtype=np.int64).tobytes())

        proc = _run_bin(self.BINARY, [bin_path, n, k, 1])
        assert proc.returncode == 0, proc.stderr
        got = [
            tuple(int(x) for x in line.split())
            for line in proc.stdout.splitlines()
            if line.strip() and line.split()[0].lstrip("-").isdigit()
        ]
        assert got == self._expected_topk(mat, k), (got, self._expected_topk(mat, k))

    def test_topk_sparse(self, tmp_path: Path):
        if not self.BINARY.exists():
            pytest.skip("a2_topk_batch not built (make mojo-build)")
        n, k = 4, 2
        mat = np.zeros((n, n), dtype=np.int64)
        mat[0, 1] = mat[1, 0] = 3
        mat[2, 3] = mat[3, 2] = 7
        bin_path = tmp_path / "shared_sparse.bin"
        bin_path.write_bytes(np.ascontiguousarray(mat, dtype=np.int64).tobytes())

        proc = _run_bin(self.BINARY, [bin_path, n, k, 1])
        assert proc.returncode == 0, proc.stderr
        got = [
            tuple(int(x) for x in line.split()) for line in proc.stdout.splitlines() if line.strip()
        ]
        assert got == [(7, 2, 3), (3, 0, 1)]


# ----------------------------------------------------------------------
# bfs_bipartite_all_batch
# ----------------------------------------------------------------------


class TestBfsBatchDifferential:
    BINARY = _MOJO_BIN / "bfs_bipartite_all_batch"

    def _run(self, g: dict, limit: int, workers: int = 1):
        out = ht._mojo_bins(g)
        src_idx = list(range(g["n_entities"]))
        return ht._mojo_batch_invoke(g, out, src_idx, limit=limit, workers=workers)

    def test_batch_matches_python_oracle(self):
        if not self.BINARY.exists():
            pytest.skip("bfs_bipartite_all_batch not built (make mojo-build)")
        pairs = [
            ("e:1", "a"),
            ("e:1", "b"),
            ("e:2", "b"),
            ("e:2", "c"),
            ("e:3", "c"),
            ("e:3", "d"),
            ("e:9", "a"),
            ("e:9", "b"),
            ("e:9", "c"),
        ]
        g = bipartite_from_pairs(pairs)
        limit = 6
        got = self._run(g, limit)

        ents = g["labels"][: g["n_entities"]]
        for sid, lbl in enumerate(ents):
            oracle = trail_source_stats(g, lbl, limit=limit)
            assert got[sid][0] == oracle["reachable"], (lbl, got[sid], oracle)
            assert got[sid][1] == oracle["hop_histogram"], (lbl, got[sid], oracle)


# ----------------------------------------------------------------------
# motifs_null_batch
# ----------------------------------------------------------------------


class TestMotifsNullDifferential:
    BINARY = _MOJO_BIN / "motifs_null_batch"

    def test_null_kernel_matches_python_oracle(self, monkeypatch):
        if not self.BINARY.exists():
            pytest.skip("motifs_null_batch not built (make mojo-build)")
        edges = [
            ["a", "b", "c"],
            ["b", "c", "d"],
            ["c", "d", "e"],
            ["a", "x", "y"],
            ["x", "y", "z"],
        ]
        orders = (2, 3)
        runs, seed = 4, 7

        # Force the Python oracle (exact same RNG stream as the kernel).
        monkeypatch.setenv("MOTIF_NULL_MOJO", "0")
        oracle = hm.configuration_null(edges, orders=orders, runs=runs, seed=seed)
        monkeypatch.delenv("MOTIF_NULL_MOJO", raising=False)
        kernel = hm._mojo_null(edges, orders=orders, runs=runs, seed=seed)
        assert kernel is not None, "mojo null lane unexpectedly unavailable"

        for k in orders:
            mean_o = float(oracle[f"pairs_ge_{k}_null_mean"])
            std_o = float(oracle[f"pairs_ge_{k}_null_std"])
            mean_k = float(kernel[f"pairs_ge_{k}_null_mean"])
            std_k = float(kernel[f"pairs_ge_{k}_null_std"])
            assert math.isclose(mean_k, mean_o, rel_tol=1e-9, abs_tol=1e-9), (
                k,
                mean_k,
                mean_o,
            )
            assert math.isclose(std_k, std_o, rel_tol=1e-9, abs_tol=1e-9), (
                k,
                std_k,
                std_o,
            )


# ----------------------------------------------------------------------
# window_shingles — golden-fixture parity built into the binary
# ----------------------------------------------------------------------


class TestWindowShinglesParity:
    BINARY = _MOJO_BIN / "window_shingles"

    def test_parity_leg_green(self):
        if not self.BINARY.exists():
            pytest.skip("window_shingles not built (make mojo-build)")
        proc = _run_bin(self.BINARY, [])
        assert proc.returncode == 0, proc.stderr
        assert "( 0  failed)" in proc.stdout, proc.stdout
        assert "parity:  210 / 210  passed" in proc.stdout, proc.stdout
