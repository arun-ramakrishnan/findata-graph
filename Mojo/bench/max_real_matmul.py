#!/usr/bin/env python3
"""Real MAX CPU kernel — matmul via max.graph + max.engine (CPU DeviceRef).

Uses MAX's production CPU matmul kernel (the same `max/kernels/src/linalg/matmul/cpu`
`Inner_matmul_default`/`VNNI`/`NEON` dispatched via `TiledMatmul` that `max_bench`
would use) — not a simulated Python loop. Input is the same float32 matmul
as Mojo bench_scale: matrix (Rows x Dims) @ query (Dims x 1) = Rows x 1,
which is the batched dot per row that bench_cosine does as cosine dot.

Data: generated LOCALLY by default — np.random.seed(0) filled in row-order
chunks. The MATRIX stream is byte-identical to the old examples/data fixtures
(verified: gen randn(7812,128) == matrix_7812_128.f32); the fixture QUERY
files were session-position-dependent draws, so gen mode uses a canonical
fresh seed-0 query instead. Chunked fill bounds the float64 intermediate so
250M/500M-element runs don't blow up RAM. --data/--query loads .f32 files
instead when given.

Run: .venv/bin/python Mojo/bench/max_real_matmul.py --elements 100000000 --dims 128 --reps 3
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

CHUNK_ROWS = 131072  # 128K rows: caps the float64 fill intermediate (~128 MB at dims=128)


def aligned_array(shape, dtype=np.float32, alignment=64):
    """numpy array whose data pointer is exactly `alignment`-byte aligned.

    The MAX CPU matmul kernel issues vmovaps (32B-required) loads directly off
    the host input, so a 16-mod-32 numpy buffer segfaults on execute. We
    over-allocate and slice to a 64B boundary, keeping the base alive so the
    view stays valid for the zero-copy handoff.
    """
    itemsize = np.dtype(dtype).itemsize
    n = int(np.prod(shape))
    buf = np.empty(n + alignment // itemsize, dtype=dtype)
    off = (-buf.ctypes.data) % alignment
    view = buf[off // itemsize : off // itemsize + n].reshape(shape)
    return view, buf


def fill_random(mat, qry, rows, dims):
    """Seed-0 standard normal; row-order chunks == one big randn(rows, dims)."""
    np.random.seed(0)
    for start in range(0, rows, CHUNK_ROWS):
        stop = min(start + CHUNK_ROWS, rows)
        mat[start:stop] = np.random.randn(stop - start, dims)
    np.random.seed(0)  # canonical query: fresh seed-0 draw, independent of rows
    qry[:] = np.random.randn(dims, 1)
    return "gen(seed 0, local)"


def load_inputs(rows: int, dims: int, data: str | None, query: str | None):
    """Return (mat, qry) 64B-aligned float32 arrays + source label."""
    mat_path = Path(data) if data else None
    qry_path = Path(query) if query else None

    mat, _ = aligned_array((rows, dims))
    qry, _ = aligned_array((dims, 1))
    if mat_path and qry_path and mat_path.exists() and qry_path.exists():
        mat[:] = np.fromfile(mat_path, dtype=np.float32, count=rows * dims).reshape(rows, dims)
        qry[:] = np.fromfile(qry_path, dtype=np.float32, count=dims).reshape(dims, 1)
        src = f"data:{mat_path.name}"
    else:
        src = fill_random(mat, qry, rows, dims)
    if mat.ctypes.data % 32 or qry.ctypes.data % 32:
        raise RuntimeError("aligned_array failed: inputs not 32B-aligned (vmovaps SEGV contract)")
    return mat, qry, src


def build_matmul_graph(rows: int, dims: int):
    device = DeviceRef.CPU()
    # Graph takes two inputs: lhs (Rows x Dims), rhs (Dims x 1), output Rows x 1
    lhs_type = TensorType(DType.float32, [rows, dims], device)
    rhs_type = TensorType(DType.float32, [dims, 1], device)
    with Graph("matmul_real", input_types=[lhs_type, rhs_type]) as g:
        lhs, rhs = g.inputs[0].tensor, g.inputs[1].tensor
        out = ops.matmul(lhs, rhs)  # Rows x 1, MAX dispatches to Inner_matmul_default/VNNI/NEON
        g.output(out)
        return g


def bench_once(
    rows: int, dims: int, reps: int = 5, data: str | None = None, query: str | None = None
):
    t0 = time.perf_counter()
    mat, qry, src = load_inputs(rows, dims, data, query)
    data_s = time.perf_counter() - t0
    g = build_matmul_graph(rows, dims)
    # Compile once (like mojo build)
    t0 = time.perf_counter()
    model = InferenceSession().load(g)
    compile_ms = (time.perf_counter() - t0) * 1000
    # Warm-up
    _ = model.execute(mat, qry)[0].to_numpy()
    best_ms = float("inf")
    best_top = -1
    for _ in range(reps):
        t0 = time.perf_counter()
        out = model.execute(mat, qry)[0].to_numpy()  # Rows x 1
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000
        if ms < best_ms:
            best_ms = ms
            best_top = int(np.argmax(out))
    top1_numpy = int(np.argmax(mat @ qry[:, 0]))  # correctness check vs numpy
    size = rows * dims * 4 / 1024**3
    unit = f"{size:.2f} GiB" if size >= 1 else f"{size * 1024:.1f} MiB"
    return {
        "rows": rows,
        "dims": dims,
        "elements": rows * dims,
        "size": unit,
        "src": src,
        "data_s": round(data_s, 2),
        "compile_ms": round(compile_ms, 2),
        "compute_ms": round(best_ms, 4),
        "top1": best_top,
        "top1_ok": best_top == top1_numpy,
    }


def main():
    ap = argparse.ArgumentParser(description="Real MAX CPU matmul — Rows x Dims @ Dims x 1")
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
    r = bench_once(args.rows, args.dims, args.reps, args.data, args.query)
    print(
        f"MAX CPU matmul real: rows={r['rows']} dims={r['dims']} elements={r['elements']} {r['size']} src={r['src']} data_s={r['data_s']} compile_ms={r['compile_ms']} compute_ms={r['compute_ms']} top1={r['top1']} top1_ok={r['top1_ok']}"
    )
    print(
        "Note: MAX dispatches Inner_matmul_default scalar FMA 8-wide SKL→default (like bench_cosine vectorize 8) — not VNNI/NEON — so MAX = your sample on SKL, parallel MAX is 2.69-3.01x via sync_parallelize 4c, see bench_cosine_max_parallel.mojo"
    )


if __name__ == "__main__":
    main()
