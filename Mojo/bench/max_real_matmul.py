#!/usr/bin/env python3
"""Real MAX CPU kernels — gemv/gemm via max.graph + max.engine (CPU DeviceRef).

Dispatch (per upstream `max/kernels/src/linalg`, read 2026-09-02 — the wheel's
.mojoc packages are symbol-stripped, the repo is the readable source):
- n == 1 (our historical shape, Rows x Dims @ Dims x 1): the CPU matmul
  dispatcher routes to the dedicated CPU GEMV — `gemv[parallelize=True]`
  built on `max.algorithm.reduction._reduce_generator` (SIMD-width
  `load_linear` row-dots) — NOT the tiled matmul microkernel. Same primitive
  shape as bench_cosine's vectorize[8] row-dots, fanned out over rows, which
  is why the engine lands at Mojo-*parallel* numbers (~2.4x over scalar).
- n > 1 (--n, true GEMM: Rows x Dims @ Dims x n): `TiledMatmul` — M/N/K tile
  loops, `BTileGenerator` packing, inner microkernel dispatch
  (`Inner_matmul_default` on SKL AVX2; vnni/avx512, neon, i8mm elsewhere),
  fanout via sync_parallelize. B-packing amortizes the A-stream across the n
  columns, so per-query cost should drop until compute-bound.
Both CPU entry points also accept an elementwise_lambda_fn epilogue fused
into the kernel (elementwise only — top_k cannot ride along).

Uses MAX's production CPU kernels — not a simulated Python loop. Data is
generated LOCALLY by default — np.random.seed(0) filled in row-order
chunks. The MATRIX stream is byte-identical to the old examples/data fixtures
(verified: gen randn(7812,128) == matrix_7812_128.f32); the fixture QUERY
files were session-position-dependent draws, so gen mode uses a canonical
fresh seed-0 query instead (n>1: one fresh seed-0 (dims, n) draw). Chunked
fill bounds the float64 intermediate so 250M/500M-element runs don't blow up
RAM. --data/--query loads .f32 files instead when given (--query then reads
dims*n values row-major).

Run: .venv/bin/python3 Mojo/bench/max_real_matmul.py --elements 100000000 --dims 128 --reps 3
     .venv/bin/python3 Mojo/bench/max_real_matmul.py --elements 12800000 --dims 128 --n 8
     (--elements derives rows = elements/dims; the standing scale ladder is
     1M / 7.6M / 10M / 12.8M / 100M with 100M as the MAX scale — 781250x128,
     381 MiB. 250M/500M were one-off linearity probes, 2026-09-02; see the
     pilot § MAX evaluation run log before going past 100M: DRAM-bound.)
Requires: max 26.5.0 via pip (already in .venv), no discrete GPU — CPU DeviceRef.

SEGV ROOT CAUSE (debugged 2026-09-02, see doc/local/mojo/mojo_pilot.md § "MAX
evaluation" → "SEGV root cause"): the JIT'd CPU kernel reads the host input
zero-copy with `vmovaps` ymm loads that require 32-byte alignment. numpy only
guarantees 16-byte alignment, and Python 3.14's mimalloc places ~3 MB
allocations at region+0x10 (16 mod 32) almost always — hence the flaky-to-
deterministic "segfault >100 rows" the earlier examples/ version hit.
Fix: allocate inputs 64-byte aligned (aligned_array below). Verified: aligned
inputs 8/8 runs pass; inputs forced to 16 mod 32 reproduce 8/8 segfaults.
"""

import argparse
import time
from pathlib import Path

import numpy as np
from max.dtype import DType
from max.engine import InferenceSession
from max.graph import DeviceRef, Graph, ops
from max.graph.type import TensorType

from aligned_array import aligned_array  # shared 64B-alignment helper (same dir)

CHUNK_ROWS = 131072  # 128K rows: caps the float64 fill intermediate (~128 MB at dims=128)


def fill_random(mat, qry, rows, dims):
    """Seed-0 standard normal; row-order chunks == one big randn(rows, dims);
    query is one fresh seed-0 draw of qry's full (dims, n) shape."""
    np.random.seed(0)
    for start in range(0, rows, CHUNK_ROWS):
        stop = min(start + CHUNK_ROWS, rows)
        mat[start:stop] = np.random.randn(stop - start, dims)
    np.random.seed(0)  # canonical query: fresh seed-0 draw, independent of rows/n
    qry[:] = np.random.randn(*qry.shape)
    return "gen(seed 0, local)"


def load_inputs(rows: int, dims: int, n: int, data: str | None, query: str | None):
    """Return (mat, qry) 64B-aligned float32 arrays + source label."""
    mat_path = Path(data) if data else None
    qry_path = Path(query) if query else None

    mat, _ = aligned_array((rows, dims))
    qry, _ = aligned_array((dims, n))
    if mat_path and qry_path and mat_path.exists() and qry_path.exists():
        mat[:] = np.fromfile(mat_path, dtype=np.float32, count=rows * dims).reshape(rows, dims)
        qry[:] = np.fromfile(qry_path, dtype=np.float32, count=dims * n).reshape(dims, n)
        src = f"data:{mat_path.name}"
    else:
        src = fill_random(mat, qry, rows, dims)
    if mat.ctypes.data % 32 or qry.ctypes.data % 32:
        raise RuntimeError("aligned_array failed: inputs not 32B-aligned (vmovaps SEGV contract)")
    return mat, qry, src


def build_matmul_graph(rows: int, dims: int, n: int):
    device = DeviceRef.CPU()
    # Graph takes two inputs: lhs (Rows x Dims), rhs (Dims x n), output Rows x n
    lhs_type = TensorType(DType.float32, [rows, dims], device)
    rhs_type = TensorType(DType.float32, [dims, n], device)
    with Graph("matmul_real", input_types=[lhs_type, rhs_type]) as g:
        lhs, rhs = g.inputs[0].tensor, g.inputs[1].tensor
        # n==1 dispatches to CPU gemv (reduce-generator row-dots); n>1 to
        # TiledMatmul + BTileGenerator packing + Inner_matmul_* + sync_parallelize
        out = ops.matmul(lhs, rhs)  # Rows x n
        g.output(out)
        return g


def bench_once(
    rows: int,
    dims: int,
    n: int = 1,
    reps: int = 5,
    data: str | None = None,
    query: str | None = None,
):
    t0 = time.perf_counter()
    mat, qry, src = load_inputs(rows, dims, n, data, query)
    data_s = time.perf_counter() - t0
    # NOTE: InferenceSession(num_threads=...) CRASHES in max 26.5.0 for any
    # value ("AsyncRT::getOrCreateCPUDevice ... different options" — the CPU
    # device is created process-globally at import with default options; LLVM
    # abort, not catchable). Engine fanout is observed via threads_avg below.
    session = InferenceSession()
    g = build_matmul_graph(rows, dims, n)
    # Compile once (like mojo build)
    t0 = time.perf_counter()
    model = session.load(g)
    compile_ms = (time.perf_counter() - t0) * 1000
    # Warm-up
    _ = model.execute(mat, qry)[0].to_numpy()
    best_ms = float("inf")
    best_out = None
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for _ in range(reps):
        t0 = time.perf_counter()
        out = model.execute(mat, qry)[0].to_numpy()  # Rows x n
        ms = (time.perf_counter() - t0) * 1000
        if ms < best_ms:
            best_ms = ms
            best_out = out
    # threads engaged by the engine during the reps loop (1.0 = single core)
    threads_avg = (time.process_time() - cpu0) / (time.perf_counter() - wall0)
    # numpy BLAS baseline: best of the same passes (also the correctness oracle)
    numpy_ms = float("inf")
    ref = None
    for _ in range(max(3, min(reps, 5))):
        t0 = time.perf_counter()
        ref = mat @ qry
        numpy_ms = min(numpy_ms, (time.perf_counter() - t0) * 1000)
    top1_numpy = np.argmax(ref, axis=0)
    top1_max = np.argmax(best_out, axis=0)
    bytes_moved = (rows * dims + dims * n + rows * n) * 4
    flops = 2.0 * rows * dims * n
    return {
        "rows": rows,
        "dims": dims,
        "n": n,
        "elements": rows * dims,
        "src": src,
        "data_s": round(data_s, 2),
        "compile_ms": round(compile_ms, 2),
        "compute_ms": round(best_ms, 4),
        "per_query_ms": round(best_ms / n, 4),
        "gb_s": round(bytes_moved / best_ms / 1e6, 2),
        "gflops": round(flops / best_ms / 1e6, 2),
        "threads_avg": round(threads_avg, 2),
        "numpy_ms": round(numpy_ms, 4),
        "top1": int(top1_max[0]),
        "top1_ok": bool(np.array_equal(top1_max, top1_numpy)),
    }


def main():
    ap = argparse.ArgumentParser(description="Real MAX CPU gemv/gemm — Rows x Dims @ Dims x n")
    ap.add_argument(
        "--elements",
        type=int,
        default=None,
        help="Total matrix elements; derives rows = elements/dims "
        "(ladder: 999936/7680000/10000000/12800000/100000000 = max scale)",
    )
    ap.add_argument(
        "--rows",
        type=int,
        default=None,
        help="Rows directly (mutually exclusive with --elements; default 10000)",
    )
    ap.add_argument(
        "--dims", type=int, default=768, help="Dims (768 for 10K, 128 for the 1M-100M family)"
    )
    ap.add_argument(
        "--n", type=int, default=1, help="rhs columns: 1 = gemv path, >1 = TiledMatmul GEMM path"
    )
    ap.add_argument("--reps", type=int, default=5, help="Reps best-of")
    ap.add_argument(
        "--data", default=None, help="Optional matrix .f32 path (default: generate locally, seed 0)"
    )
    ap.add_argument(
        "--query", default=None, help="Optional query .f32 path (default: generate locally)"
    )
    args = ap.parse_args()
    if args.elements is None and args.rows is None:
        args.rows = 10000
    if args.elements is not None:
        if args.rows is not None:
            ap.error("--elements and --rows are mutually exclusive")
        if args.elements % args.dims:
            ap.error(f"--elements {args.elements} not divisible by --dims {args.dims}")
        args.rows = args.elements // args.dims
    r = bench_once(args.rows, args.dims, args.n, args.reps, args.data, args.query)
    print(
        f"MAX CPU matmul real: rows={r['rows']} dims={r['dims']} n={r['n']} "
        f"elements={r['elements']} src={r['src']} "
        f"data_s={r['data_s']} compile_ms={r['compile_ms']} compute_ms={r['compute_ms']} "
        f"per_query_ms={r['per_query_ms']} gb_s={r['gb_s']} gflops={r['gflops']} "
        f"threads_avg={r['threads_avg']} numpy_ms={r['numpy_ms']} top1={r['top1']} "
        f"top1_ok={r['top1_ok']}"
    )
    print(
        "Note: n=1 dispatches to CPU gemv[parallelize=True] (_reduce_generator SIMD "
        "row-dots, same shape as bench_cosine vectorize[8]); n>1 takes TiledMatmul + "
        "BTileGenerator packing + Inner_matmul_default (SKL AVX2, not VNNI/NEON) with "
        "sync_parallelize fanout — per upstream max/kernels/src/linalg, see "
        "doc/local/mojo/mojo_pilot.md § MAX evaluation"
    )


if __name__ == "__main__":
    main()
