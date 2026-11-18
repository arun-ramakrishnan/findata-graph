#!/usr/bin/env python3
"""Perf leg: the Rust cosine kernel must still earn its place.

This gate covers two shape classes:

* the live production script-search shape (717 rows x 512 dimensions), where
  the Rust backend must remain at least 1.3x faster than the numpy backend and
  at least 5x faster than the pure-Python reference; and
* the live-note shape (19,356 rows x 512 dimensions) plus scale shapes at
  250,000 / 1,000,000 rows x 384 dimensions, where Rust loses to BLAS.
  Those shapes carry a 0.14x floor: the point is to pin today's honest
  gap and catch a regression, not to pretend Rust wins them. The 500,000
  middle point was dropped 2026-11-19: the measured ratio is flat
  (0.18-0.20x) across scales, so 250k and 1M bracket the regime and the
  middle row added ~1.7s of leg time with no additional signal.

WHY THE SCALE FLOOR IS 0.14 AND NOT 0.40. A regression gate needs the floor to
sit inside a non-empty window: it must exceed half the widest healthy ratio (so
a deliberate 2x Rust slowdown lands below it) and stay under the narrowest
healthy ratio (so a healthy build never trips it). The window is
(max_healthy / 2, min_healthy], and what makes it usable is a NARROW healthy
band, not a low floor.

Rayon thread count is what widens or narrows that band. Measured on the 4-core
powersave reference box, medians across fresh processes:

  Rayon 1:  live-note 0.170-0.218, 250k 0.187-0.201, 1000k 0.185-0.243
            -> window (0.122, 0.170], usable.
  Rayon 4:  live-note 0.422-0.615, 250k 0.521-0.768, 1000k 0.557-0.737
            -> window (0.384, 0.422], ~9% wide, one noisy round from empty.

With more Rayon workers the Rust lane gains speed but the band widens enough
that a floor passing an idle box fails a busy one, and one catching a 2x
slowdown fails an idle one: the earlier 0.40x floor sat inside that band and
failed healthy builds on 5 of 5 runs. Pinning Rayon to one thread makes the
measurement a KERNEL comparison on a single core, which is both the honest
question ("is the Rust inner loop competitive?") and the stable one.

Each shape reports the MEDIAN of five interleaved rounds rather than the best
of five. A min-of-N estimator tracks a transient stall on the fast lane and
inflates the ratio; the median is what a ratio gate should assert.

TOP-1 PARITY IS COMPARED BY SCORE, NOT ID. The cheap scale fixture tiles a
4,096-row base and rotates each tile to keep rows distinct. Similarities still
cluster within a few float32 ulps at the top, so row ids can legitimately swap
between a Rust and a BLAS accumulation order. Asserting id equality there would
make the gate flaky without testing anything: what matters is that the kernel
returns a row carrying the maximal similarity, which the score check pins at
1e-5 (the production shape, where the top row is unique, keeps the exact
full-ranking comparison through the production entry point).

A machine without the build (no `cosine_knn` abi3 module) SKIPs with exit 0,
mirroring `make rust-clippy`/`rust-test`. The hard pin lives in qa:
`SCRIPT_KNN_STRICT=1` turns the importorskip in
`tests/test_cosine_knn_backends.py` into a failure, so a vanished `.so`
still fails the gate on boxes that carry the production backend.

Usage: .venv/bin/python3 Rust/bench/cosine_gate.py
Exit codes: 0 within bars (or SKIP), 1 a bar was breached.
"""

from __future__ import annotations

import importlib
import os
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Pin before numpy imports its BLAS backend and Rust initialises Rayon. This
# script is its own perf-leg process, so the setting cannot leak into unrelated
# tests or benchmark legs. One BLAS thread and one Rayon worker put both lanes
# on a single core: that is the regime whose healthy band is narrow enough to
# hold a feasible floor (see the docstring).
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["RAYON_NUM_THREADS"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

PROD_ROWS = 717  # memory/script_search.db row count at the 512-d re-embed
PROD_DIM = 512  # embeddinggemma-2-q8_512
PROD_QUERIES = 60  # enough to beat timer noise, cheap enough for a perf budget
PROD_ROUNDS = 5  # odd; the median round is the reported one
PURE_QUERIES = 5  # the pure loop is ~40x slower; 5 is plenty to time it

# Production bars: Rust must beat the production alternatives there.
MIN_VS_NUMPY = 1.3
MIN_VS_PURE = 5.0

# Scale/live bars: Rust currently loses, so pin the lower bound of that loss.
# 0.14 sits inside the feasible window (0.122, 0.170] for the measured healthy
# band under the single-worker pin; see the module docstring for the algebra.
MIN_SCALE_VS_NUMPY = 0.14
SCALE_ROUNDS = 5
# Two similarities within this distance are the same answer on the scale
# shapes (float32 accumulation order differs between Rust and BLAS).
SCALE_SCORE_TOL = 1e-5

LIVE_ROWS = 19_356
LIVE_DIM = 512
SCALE_DIM = 384
# 250k and 1M bracket the flat loss regime; the 500k middle point added
# leg time without new signal (see module docstring).
SCALE_ROWS = (250_000, 1_000_000)


@dataclass
class ShapeResult:
    label: str
    rows: int
    dim: int
    rust_ms: float
    numpy_ms: float
    ratio: float
    bar: float
    ok: bool
    detail: str = ""


def _sentinel() -> tuple[list[int], list[bytes], list[list[float]], np.ndarray]:
    """Deterministic stand-in for the live script-search index."""
    rng = np.random.default_rng(20261027)
    matrix = rng.standard_normal((PROD_ROWS, PROD_DIM), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    rids = list(range(PROD_ROWS))
    blobs = [row.tobytes() for row in matrix]
    vecs = [row.tolist() for row in matrix]
    return rids, blobs, vecs, matrix


def _timed(fn: Callable[[], object], n: int) -> float:
    """Per-query ms for n back-to-back calls of fn."""
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1000.0


def _production_shape() -> tuple[ShapeResult, float, list[str]]:
    """Measure the production full-ranking entry point with all three backends."""
    from helpers.maintenance.rebuild_script_search import _cosine_rank_rows

    rids, blobs, vecs, matrix = _sentinel()
    q_list = matrix[0].tolist()

    def leg(backend: str) -> Callable[[], object]:
        """One query through the production entry point, as this backend sees it."""
        if backend == "pure":  # the reference loop takes lists, not blobs
            return lambda: _cosine_rank_rows(q_list, rids, vecs)
        return lambda: _cosine_rank_rows(q_list, rids, vecs, blobs=blobs)

    rust, numpy, pure = leg("rust"), leg("numpy"), leg("pure")
    # Warm each backend once (lazy backend init / first-touch page faults land
    # here, not in the measured rounds).
    for backend, fn in (("rust", rust), ("numpy", numpy), ("pure", pure)):
        os.environ["SCRIPT_KNN"] = backend
        fn()

    # Interleave rounds so a governor or cache drift hits all backends alike.
    numpy_ratios, pure_ratios = [], []
    rust_samples, numpy_samples, pure_samples = [], [], []
    for _ in range(PROD_ROUNDS):
        os.environ["SCRIPT_KNN"] = "rust"
        r = _timed(rust, PROD_QUERIES)
        os.environ["SCRIPT_KNN"] = "numpy"
        n_ = _timed(numpy, PROD_QUERIES)
        os.environ["SCRIPT_KNN"] = "pure"
        p = _timed(pure, PURE_QUERIES)
        rust_samples.append(r)
        numpy_samples.append(n_)
        pure_samples.append(p)
        numpy_ratios.append(n_ / r)
        pure_ratios.append(p / r)
    os.environ.pop("SCRIPT_KNN", None)

    rust_ms = statistics.median(rust_samples)
    numpy_ms = statistics.median(numpy_samples)
    pure_ms = statistics.median(pure_samples)
    vs_numpy = statistics.median(numpy_ratios)
    vs_pure = statistics.median(pure_ratios)
    ok = vs_numpy >= MIN_VS_NUMPY and vs_pure >= MIN_VS_PURE
    result = ShapeResult(
        label="production",
        rows=PROD_ROWS,
        dim=PROD_DIM,
        rust_ms=rust_ms,
        numpy_ms=numpy_ms,
        ratio=vs_numpy,
        bar=MIN_VS_NUMPY,
        ok=ok,
    )
    detail = [
        f"production pure {pure_ms:.3f} ms/query; rust/pure {vs_pure:.2f}x "
        f"(bar >= {MIN_VS_PURE:.1f}x, rounds "
        f"{min(pure_ratios):.2f}-{max(pure_ratios):.2f})"
    ]
    if vs_pure < MIN_VS_PURE:
        detail.append("FAIL rust/pure below its production bar")
    return result, vs_pure, detail


def _normalised_random(rows: int, dim: int, seed: int) -> np.ndarray:
    """Build a deterministic normalised matrix without a costly Python loop."""
    rng = np.random.default_rng(seed)
    matrix = rng.standard_normal((rows, dim), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.ascontiguousarray(matrix)


def _measure_scale_shape(
    cosine_knn: object,
    label: str,
    matrix: np.ndarray,
) -> ShapeResult:
    """Compare Rust top-1 with numpy matrix @ query on one normalised matrix."""
    # An INDEPENDENT query, not matrix[0]. The scale matrices are a tiled base
    # rotated per row, so querying with row 0 puts several rows at similarity
    # 1.0 within float32 ulps and the winning id stops being well defined.
    query = _normalised_random(1, matrix.shape[1], 20261120)[0].copy()
    rust_knn = cosine_knn.knn  # type: ignore[attr-defined]

    # k=10 matches the measurement that established the loss regime. Only the
    # first result is compared with numpy, but the kernel pays the same top-k
    # work as that benchmark.
    rust_top = rust_knn(query, matrix, 10)[0]
    rust_top1 = int(rust_top[0])
    scores = matrix @ query
    numpy_top1 = int(np.argmax(scores))

    # Compare SCORES, not ids: near-ties mean the winning id depends on the
    # accumulation order. What must hold is that Rust returns a row carrying
    # the maximal similarity.
    score_delta = abs(float(rust_top[1]) - float(scores[numpy_top1]))
    top1_match = score_delta <= SCALE_SCORE_TOL

    round_ratios, rust_samples, numpy_samples = [], [], []
    for _ in range(SCALE_ROUNDS):
        t0 = time.perf_counter()
        rust_knn(query, matrix, 10)
        rust_ms = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        matrix @ query
        numpy_ms = (time.perf_counter() - t0) * 1000.0
        rust_samples.append(rust_ms)
        numpy_samples.append(numpy_ms)
        round_ratios.append(numpy_ms / rust_ms)

    # MEDIAN of the per-round ratios, not best-of-N. A min-of-N estimator
    # latches onto a transient stall on the fast lane and inflates the ratio,
    # which is what made the previous floor flaky on a healthy build.
    rust_ms = statistics.median(rust_samples)
    numpy_ms = statistics.median(numpy_samples)
    ratio = statistics.median(round_ratios)
    ok = top1_match and ratio >= MIN_SCALE_VS_NUMPY
    detail = (
        f"top1 rust={rust_top1} numpy={numpy_top1} "
        f"score_delta={score_delta:.1e} match={top1_match}; "
        f"rounds {min(round_ratios):.2f}-{max(round_ratios):.2f}"
    )
    if not top1_match:
        detail = "FAIL " + detail
    return ShapeResult(
        label=label,
        rows=matrix.shape[0],
        dim=matrix.shape[1],
        rust_ms=rust_ms,
        numpy_ms=numpy_ms,
        ratio=ratio,
        bar=MIN_SCALE_VS_NUMPY,
        ok=ok,
        detail=detail,
    )


def _scale_shapes(cosine_knn: object) -> list[ShapeResult]:
    """Measure the live-note shape and the three loss-regime scale shapes."""
    results: list[ShapeResult] = []

    live = _normalised_random(LIVE_ROWS, LIVE_DIM, 20261118)
    results.append(_measure_scale_shape(cosine_knn, "live-note", live))
    del live

    # One large contiguous allocation supplies all three prefixes. A tiled
    # random base keeps construction to a few seconds and remains deterministic.
    # Rotate the first two coordinates by a unique tiny angle for every row so
    # repeated base rows cannot tie at score 1.0. The rotation preserves each
    # row's norm, so matrix @ query remains the same cosine operation.
    base = _normalised_random(4096, SCALE_DIM, 20261119)
    large = np.ascontiguousarray(np.tile(base, (245, 1)))
    angles = np.arange(large.shape[0], dtype=np.float32) * 1e-6
    first = large[:, 0].copy()
    second = large[:, 1].copy()
    large[:, 0] = first * np.cos(angles) - second * np.sin(angles)
    large[:, 1] = first * np.sin(angles) + second * np.cos(angles)
    for rows in SCALE_ROWS:
        results.append(_measure_scale_shape(cosine_knn, f"scale-{rows // 1000}k", large[:rows]))
    return results


def _print_table(results: list[ShapeResult], details: list[str]) -> None:
    print(
        f"{'shape':<18} {'rows':>10} {'dim':>5} "
        f"{'rust ms':>10} {'numpy ms':>10} {'rust/numpy':>12} "
        f"{'bar':>10} {'result':>7}"
    )
    for result in results:
        print(
            f"{result.label:<18} {result.rows:>10,} {result.dim:>5} "
            f"{result.rust_ms:>10.3f} {result.numpy_ms:>10.3f} "
            f"{result.ratio:>11.2f}x {result.bar:>9.2f}x "
            f"{'PASS' if result.ok else 'FAIL':>7}"
        )
    for detail in details:
        print(f"  {detail}")


def main() -> int:
    try:
        cosine_knn = importlib.import_module("cosine_knn")
    except ImportError:
        print(
            "SKIP cosine_gate: the cosine_knn abi3 module is not built on this "
            "box (make rust-install); qa's SCRIPT_KNN_STRICT=1 is the hard pin",
            file=sys.stderr,
        )
        return 0

    production, _vs_pure, details = _production_shape()
    results = [production]
    results.extend(_scale_shapes(cosine_knn))
    _print_table(results, details)

    failures = [result for result in results if not result.ok]
    for result in failures:
        if result.label == "production":
            reason = "rust/numpy below the production bar"
        else:
            reason = "rust/numpy below the scale floor or top-1 mismatch"
        print(
            f"FAIL {result.label}: {reason} ({result.ratio:.2f}x, bar >= {result.bar:.2f}x)",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
