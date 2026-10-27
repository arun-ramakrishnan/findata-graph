#!/usr/bin/env python3
"""Perf leg: the production cosine kernel must still EARN its place.

Why this exists (2026-10-27): `make perf` had no Rust leg at all. The
`+19%` in `cecf2eddf` ("real target-cpu tuning") came from a one-off
back-to-back A/B on a loaded box, and nothing in the tree could re-verify
it. It does not reproduce on a quiet box today — native (`vmulps` in
`topk`) and generic (`mulss`) builds measure within noise on both the
4096x384 batch shape (432-448 q/s each) and the 717x512 production shape
(0.50-0.55 ms/query each), and the pyo3 0.22 -> 0.30 bump is likewise
invisible here. The kernel is issue/bandwidth-bound at these sizes, not
FLOP-bound, so target-cpu tuning buys nothing measurable — a claim this
leg now keeps honest instead of a commit message that cannot.

WHAT IT GUARDS. Not an absolute q/s number (that would encode today's
CPU and penalise a faster box for being different). The ratio between the
Rust leg and the numpy leg, measured interleaved in the same process at
the LIVE production shape:

  * rust/numpy throughput >= 1.3x — the reason the Rust path exists at
    all. When the abi3 module goes missing the selector silently degrades
    to numpy (the documented "hybrid must degrade, never 500" contract),
    and both halves then time the SAME code: the ratio collapses to ~1.0
    and this leg goes red. That silent-degradation class is otherwise
    invisible — the ranking stays correct, only the reason for the crate
    evaporates.
  * rust/pure >= 5x — the pure reference loop is the semantic fallback
    (stable sort, zero-norm rows score 0.0); a regression that routes
    production traffic there would be a 40x cliff.

Measured 2026-10-27, i5-6500, at the live script-search shape (717 rows x
512 dim, `embeddinggemma-2-q8_512`), interleaved rounds on a quiet box:
rust 1.11 ms/query (~900 q/s), numpy 2.88 ms (~350 q/s) = 2.59x, pure
43.8 ms (~23 q/s) = 40.8x; per-round spread rust/numpy 2.36-2.86 and
rust/pure 37.3-42.4. A separate non-interleaved study through the same
entry point landed at 2.38x with rust at 1.07 ms, and its numpy-half
penalty is mostly RETURN SHAPE (all-717 list+dict 0.91 ms vs k=10 0.59
ms) plus `np.linalg.norm(M, axis=1)` (0.39 ms) rather than the matvec
(0.03 ms) — so read the ratio as "the production path is ~2.4-2.6x
faster", not as a pure kernel multiplier. The 1.3x bar sits well outside
that spread.

Shape note: the matrix is SYNTHETIC (deterministic rng), not read from
the store — a perf leg must not read a live DB that a concurrent gate
lane may be rewriting. 717x512 mirrors the production shape, which is
what the branch decision keys on.

A machine without the build (no `cosine_knn` abi3 module) SKIPs with
exit 0, mirroring `make rust-clippy`/`rust-test`. The hard pin lives in
qa: `SCRIPT_KNN_STRICT=1` turns the importorskip in
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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

ROWS = 717  # memory/script_search.db row count at the 512-d re-embed
DIM = 512  # embeddinggemma-2-q8_512
QUERIES = 60  # enough to beat timer noise, cheap enough for a perf budget
ROUNDS = 5  # odd; the median round is the reported one

# rust/numpy throughput floor. Live interleaved spread 2.36-2.86x (repeat
# runs 2.4-2.6x) — 1.3 leaves room for a contended box while still
# collapsing to ~1.0 if the selector ever times the same path on both sides.
MIN_VS_NUMPY = 1.3
# rust/pure floor: a routing regression to the reference loop is ~40x.
MIN_VS_PURE = 5.0
PURE_QUERIES = 5  # the pure loop is ~40x slower; 5 is plenty to time it


def _sentinel() -> tuple[list[int], list[bytes], list[list[float]], np.ndarray]:
    """Deterministic stand-in for the live script-search index."""
    rng = np.random.default_rng(20261027)
    matrix = rng.standard_normal((ROWS, DIM), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    rids = list(range(ROWS))
    blobs = [row.tobytes() for row in matrix]
    vecs = [row.tolist() for row in matrix]
    return rids, blobs, vecs, matrix


def _timed(fn, n: int) -> float:
    """Per-query ms for n back-to-back calls of fn."""
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1000.0


def main() -> int:
    try:
        importlib.import_module("cosine_knn")
    except ImportError:
        print(
            "SKIP cosine_gate: the cosine_knn abi3 module is not built on this "
            "box (make rust-install); qa's SCRIPT_KNN_STRICT=1 is the hard pin",
            file=sys.stderr,
        )
        return 0

    from helpers.maintenance.rebuild_script_search import _cosine_rank_rows

    rids, blobs, vecs, matrix = _sentinel()
    q_list = matrix[0].tolist()

    def leg(backend: str):
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

    # INTERLEAVED rounds, not one block per backend: a governor or cache
    # drift across the run then hits all three legs alike and cancels in
    # the ratio instead of masquerading as a kernel difference. The env is
    # set OUTSIDE the timed loop — `_cosine_rank_rows` reads it per call,
    # so setting it per call would charge the fast leg a ~1 µs write.
    numpy_ratios, pure_ratios = [], []
    rust_samples, numpy_samples, pure_samples = [], [], []
    for _ in range(ROUNDS):
        os.environ["SCRIPT_KNN"] = "rust"
        r = _timed(rust, QUERIES)
        os.environ["SCRIPT_KNN"] = "numpy"
        n_ = _timed(numpy, QUERIES)
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
    print(
        f"shape          {ROWS} rows x {DIM} dim, interleaved, "
        f"{QUERIES} queries/round ({PURE_QUERIES} for pure), median of {ROUNDS}"
    )
    print(f"rust           {rust_ms:8.3f} ms/query  ({1000 / rust_ms:6.0f} q/s)")
    print(f"numpy          {numpy_ms:8.3f} ms/query  ({1000 / numpy_ms:6.0f} q/s)")
    print(f"pure           {pure_ms:8.3f} ms/query  ({1000 / pure_ms:6.0f} q/s)")
    print(
        f"rust vs numpy  {vs_numpy:8.2f}x   bar >= {MIN_VS_NUMPY}x"
        f"   (rounds {min(numpy_ratios):.2f}-{max(numpy_ratios):.2f})"
    )
    print(
        f"rust vs pure   {vs_pure:8.1f}x   bar >= {MIN_VS_PURE}x"
        f"   (rounds {min(pure_ratios):.1f}-{max(pure_ratios):.1f})"
    )

    failures = []
    if vs_numpy < MIN_VS_NUMPY:
        failures.append(
            f"rust/numpy {vs_numpy:.2f}x below the {MIN_VS_NUMPY}x bar — the "
            "Rust kernel no longer earns its place (silent numpy fallback, or "
            "a kernel regression)"
        )
    if vs_pure < MIN_VS_PURE:
        failures.append(f"rust/pure {vs_pure:.1f}x below the {MIN_VS_PURE}x bar")
    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
