"""
Shared SIMD cosine-KNN kernels (consolidation: single source of truth).

Canonical home for the `row_cosine` / `load_f32` / `scan_serial` copies
previously hand-propagated across bench_cosine, bench_cosine_max,
bench_cosine_max_parallel, bench_scale, and bench_pool (pairs were
byte-identical; the quintet only logic-/parameter-identical — docstring
and inner `comptime width` placement canonicalized here).

Flat import (Makefile.mojo passes -I per package dir):
  from cosine import row_cosine, load_f32, scan_serial
"""

from std.algorithm.functional import vectorize
from std.io.file import open
from std.math import sqrt
from std.memory.alloc import alloc, Layout
from std.sys import simd_width_of


def row_cosine(
    row: Pointer[Float32, MutUntrackedOrigin],
    query: Pointer[Float32, MutUntrackedOrigin],
    query_norm: Float64,
    dims: Int,
) -> Float64:
    """Cosine(query, row) — SIMD over dims, Float64 accumulators, mirrors
    the Python fallback: dot / (|query| * |row|), 0.0 on zero norms."""
    comptime width = simd_width_of[DType.float32]()
    var dot: Float64 = 0.0
    var row_sq: Float64 = 0.0

    def chunk[width: Int](i: Int) {mut dot, mut row_sq, imm row, imm query}:
        var v = row.unsafe_load[width=width](i)
        var w = query.unsafe_load[width=width](i)
        dot += Float64((v * w).reduce_add())
        row_sq += Float64((v * v).reduce_add())

    vectorize[width](dims, chunk)
    var row_norm = sqrt(row_sq)
    if row_norm == 0 or query_norm == 0:
        return 0.0
    return dot / (query_norm * row_norm)


def load_f32(
    path: String, count: Int
) raises -> Pointer[Float32, MutUntrackedOrigin]:
    """Typed-read `count` float32 values from a headerless binary dump.

    read_bytes() is deliberately NOT used: on this toolchain (Mojo 1.0.0)
    it clobbers the first 8 bytes of the returned buffer (observed zeros
    and heap-pointer values; reproduced standalone) — the typed
    FileHandle.read into an alloc'd Span reads correctly.
    """
    var p = alloc(Layout[Float32](count=count)).unsafe_leak()
    var f = open(path, "r")
    var nbytes = f.read(Span(unsafe_ptr=p, length=count))
    f.close()
    if nbytes != count * 4:
        p.unsafe_free()
        raise Error(
            "expected "
            + String(count * 4)
            + " bytes from "
            + path
            + ", got "
            + String(nbytes)
        )
    return p


def scan_serial(
    matrix: Pointer[Float32, MutUntrackedOrigin],
    query: Pointer[Float32, MutUntrackedOrigin],
    query_norm: Float64,
    rows: Int,
    dims: Int,
) -> Tuple[Int, Float64]:
    var best_idx: Int = -1
    var best_score: Float64 = -2.0
    for r in range(rows):
        var score = row_cosine(
            matrix.unsafe_offset(r * dims), query, query_norm, dims
        )
        if score > best_score:
            best_score = score
            best_idx = r
    return (best_idx, best_score)


def main():
    # Smoke: every src/<pkg>/*.mojo file must carry a main() or
    # `mojo build` refuses it ("module does not contain a 'main'
    # function") — the Makefile builds ALL of src/ into Mojo/bin/.
    # Synthetic 8-wide vectors exercise row_cosine + scan_serial.
    var layout = Layout[Float32](count=8)
    var a = alloc(layout).unsafe_leak()
    var b = alloc(layout).unsafe_leak()
    for i in range(8):
        a.unsafe_offset(i)[] = 1.0
        b.unsafe_offset(i)[] = 1.0
    var s = row_cosine(a, b, sqrt(8.0), 8)
    b.unsafe_offset(0)[] = 0.0
    var z = row_cosine(a, b, sqrt(7.0), 8)
    var res = scan_serial(a, b, sqrt(7.0), 1, 8)
    a.unsafe_free()
    b.unsafe_free()
    print("cosine smoke: self=", s, " partial=", z, " scan=", res)
