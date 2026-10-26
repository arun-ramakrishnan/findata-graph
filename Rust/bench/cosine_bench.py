#!/usr/bin/env python3
"""Python baseline for rust_trials Trial B (cosine similarity / KNN).

Mirrors helpers/graph/query.py::similar_notes / rebuild_script_search.py::_cosine_leg:
cosine similarity between a query f32 vector (384-d) and every row of an
N x 384 matrix, returning top-K.

Same deterministic matrix sizes as the Rust trial:
N queries x N rows in {1024, 4096, 16384}, DIM=384.

Usage: .venv/bin/python3 helpers/misc/cosine_bench.py
"""

from __future__ import annotations

import math
import time

try:
    import numpy as np

    HAVE_NUMPY = True
except ImportError:  # pragma: no cover - baseline fallback
    HAVE_NUMPY = False

DIM = 384


def gen_matrix(n: int) -> list[list[float]]:
    return [
        [math.sin(i * 31 + j * 7) * math.cos(i * 31 + j * 7) for j in range(DIM)]
        for i in range(n)
    ]


def gen_queries(n: int) -> list[list[float]]:
    return [
        [math.cos(i * 13 + j * 3) * math.sin(i * 13 + j * 3) for j in range(DIM)]
        for i in range(n)
    ]


def norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


def topk_python(query, qnorm, rows, norms):
    best_idx = 0
    best = -math.inf
    for i, row in enumerate(rows):
        denom = qnorm * norms[i]
        c = sum(a * b for a, b in zip(query, row)) / denom if denom else -math.inf
        if c > best:
            best = c
            best_idx = i
    return best_idx, best


def topk_numpy(query, qnorm, rows_arr, norms):
    dots = rows_arr @ query
    denom = qnorm * norms
    denom[denom == 0] = math.inf
    coss = dots / denom
    return int(np.argmax(coss)), float(coss.max())


def main() -> None:
    print("Python baseline — cosine similarity / KNN (Trial B)")
    print(f"dim={DIM} top-k=1 (best match, mirrors query.py similar_notes)")
    print(f"numpy available: {HAVE_NUMPY}")
    print()
    for n_queries in (1, 4, 16):
        for n_rows in (1024, 4096, 16384):
            matrix = gen_matrix(n_rows)
            queries = gen_queries(n_queries)
            norms = [norm(v) for v in matrix]
            qnorms = [norm(v) for v in queries]

            # pure python scalar
            t0 = time.perf_counter()
            for qi in range(n_queries):
                topk_python(queries[qi], qnorms[qi], matrix, norms)
            t_scalar = time.perf_counter() - t0
            qps_scalar = n_queries / t_scalar

            # numpy vectorized
            qps_numpy = None
            if HAVE_NUMPY:
                rows_arr = np.array(matrix, dtype=np.float32)
                norms_arr = np.array(norms, dtype=np.float32)
                t0 = time.perf_counter()
                for qi in range(n_queries):
                    topk_numpy(queries[qi], qnorms[qi], rows_arr, norms_arr)
                t_numpy = time.perf_counter() - t0
                qps_numpy = n_queries / t_numpy

            print(
                f"queries={n_queries:<3} rows={n_rows:<6}  "
                f"python={qps_scalar:>9.1} q/s  "
                f"numpy={qps_numpy:>9.1} q/s"
                if qps_numpy
                else f"python={qps_scalar:>9.1} q/s"
            )
        print()


if __name__ == "__main__":
    main()
