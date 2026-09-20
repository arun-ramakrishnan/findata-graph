#!/usr/bin/env python3
"""L-quick: batched-KNN CPU-leg numbers on the REAL 16560x384 matrix.

Three configs, run as SEPARATE subprocesses (--leg) so OpenBLAS thread state
is clean per config (config 2 must see OMP_NUM_THREADS=1 before import):
  1 --leg serial    : em.top_k per query, default threads (4T OpenBLAS)
  2 --leg omp1pool  : OMP_NUM_THREADS=1 + ProcessPoolExecutor over queries
  3 --leg flatknn   : single FlatKNN(MAX) session, loop top_k (compile ~2.8s once)

Sweep N = 1, 8, 64; reps = 3 (min per query batch, solo run). For the E
design this fixes the true CPU-leg per-query number at real corpus scale.
Controlled ablation: queries are seeded random 384-d (inputs only; the
matrix is the real embed_matrix.f32).

Run:
  OMP_NUM_THREADS=1 .venv/bin/python3 -m bench_lquick_knn --leg omp1pool
Other legs run with default OMP; the matrix-load env is irrelevant, only
the matmul thread count matters. All legs solo.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers.core.embed_matrix import EmbedMatrixStore  # noqa: E402

DIMS = 384
K = 5
SEED = 0
N_SWEEP = (1, 8, 64)
REPS = 3


def make_queries(n: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    return rng.standard_normal((n, DIMS), dtype=np.float32)


def bench_serial(em, queries: np.ndarray, reps: int) -> list[float]:
    """Per-query numpy gemv, default OpenBLAS threads."""
    times = []
    for q in queries:
        best = float("inf")
        for _ in range(reps):
            t0 = time.perf_counter()
            em.top_k(q, K)
            dt = time.perf_counter() - t0
            best = min(best, dt)
        times.append(best)
    return times


_WORKER_MAT: np.ndarray | None = None


def _worker_init() -> None:
    global _WORKER_MAT
    _WORKER_MAT = np.fromfile(_REPO_ROOT / "memory" / "embed_matrix.f32", dtype=np.float32).reshape(
        -1, DIMS
    )


def _worker(qidx: int, q: np.ndarray) -> float:
    import time

    mat = _WORKER_MAT
    if mat is None:
        raise RuntimeError("worker matrix not loaded — pool spawn missed _worker_init()")
    q = np.asarray(q, dtype=np.float32).ravel()
    n = float(np.linalg.norm(q))
    t0 = time.perf_counter()
    scores = mat @ (q / n)
    idx = np.argpartition(-scores, K - 1)[:K]
    _ = idx[np.argsort(-scores[idx], kind="stable")]
    dt = time.perf_counter() - t0
    return dt


def bench_omp1_pool(em, queries: np.ndarray, reps: int) -> list[float]:
    """Pool the N QUERIES across 4 pinned workers (not 4 copies of one)."""
    perq_accum = [0.0] * len(queries)
    workers = 4
    for _ in range(reps):
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=__import__("multiprocessing").get_context("spawn"),
            initializer=_worker_init,
        ) as ex:
            futs = {ex.submit(_worker, i, q): i for i, q in enumerate(queries)}
            for fu in as_completed(futs):
                perq_accum[futs[fu]] += fu.result()
    reps_f = float(reps)
    return [t / reps_f for t in perq_accum]


def bench_flatknn(em, queries: np.ndarray, reps: int) -> list[float]:
    """Single MAX session; loop top_k per query."""
    from flat_knn import FlatKNN

    t0 = time.perf_counter()
    fk = FlatKNN(em)
    compile_s = time.perf_counter() - t0
    print(f"FlatKNN compile: {compile_s:.2f}s", flush=True)
    times = []
    for q in queries:
        best = float("inf")
        for _ in range(reps):
            t0 = time.perf_counter()
            fk.top_k(q, K)
            dt = time.perf_counter() - t0
            best = min(best, dt)
        times.append(best)
    return times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=("serial", "omp1pool", "flatknn"), required=True)
    ap.add_argument(
        "--n", type=int, nargs="*", default=None, help="override N sweep (default: 1 8 64)"
    )
    ap.add_argument("--reps", type=int, default=REPS)
    args = ap.parse_args()

    n_sweep = tuple(args.n) if args.n else N_SWEEP

    em = EmbedMatrixStore().load()
    nrows = int(em.matrix.shape[0])
    print(f"matrix: {nrows}x{em.matrix.shape[1]}, aligned={em.aligned}", flush=True)
    print(f"openblas threads: {os.environ.get('OMP_NUM_THREADS', 'default')}", flush=True)

    for n in n_sweep:
        queries = make_queries(n)
        if args.leg == "serial":
            times = bench_serial(em, queries, args.reps)
        elif args.leg == "omp1pool":
            times = bench_omp1_pool(em, queries, args.reps)
        else:
            if n == 1:
                sys.stdout.write("FlatKNN compile ~2.8s...\n")
                sys.stdout.flush()
            times = bench_flatknn(em, queries, args.reps)
        avg = sum(times) / len(times)
        perq = avg  # single query min already
        print(
            f"N={n:3d}  per-query avg {perq * 1000:7.3f} ms  "
            f"total(one sweep) {avg * n:8.3f} ms  "
            f"band {min(times) * 1000:6.3f}–{max(times) * 1000:6.3f} ms",
            flush=True,
        )


if __name__ == "__main__":
    main()
