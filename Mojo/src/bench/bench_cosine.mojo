# Cosine-KNN benchmark — the Mojo leg of the Mojo/tests/bench_cosine_knn.py harness.
#
# Times a SIMD-vectorized whole-corpus brute-force cosine scan (dot product
# + per-row norm per row — the same math as the Python cosine fallback in
# app.py._scored_rows._cosine) over a real note-embedding corpus dumped to
# a raw float32 file by the Python driver. Results + verdict:
# doc/local/mojo_pilot.md.
#
# File format is headerless little-endian float32; rows/dims/reps arrive on
# the command line:
#   mojo run Mojo/src/bench/bench_cosine.mojo <matrix.f32> <query.f32> <rows> <dims> <reps>
# (or build once with `mojo build` and run the binary directly — the
# Python driver does exactly that so process startup is measurable too).

from std.io.file import open
from std.memory.alloc import alloc, Layout
from std.sys import argv, simd_width_of
from std.algorithm.functional import vectorize
from std.time import perf_counter_ns
from std.math import sqrt


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


def load_f32(path: String, count: Int) raises -> Pointer[Float32, MutUntrackedOrigin]:
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
            "expected " + String(count * 4) + " bytes from " + path
            + ", got " + String(nbytes)
        )
    return p


def main() raises:
    var args = argv()
    if len(args) < 6:
        print("usage: bench_cosine <matrix.f32> <query.f32> <rows> <dims> <reps>")
        raise Error("expected 5 arguments: matrix, query, rows, dims, reps")
    var matrix_path = String(args[1])
    var query_path = String(args[2])
    var rows = Int(String(args[3]))
    var dims = Int(String(args[4]))
    var reps = Int(String(args[5]))

    comptime width = simd_width_of[DType.float32]()

    # Headerless float32 dumps read straight into owned allocations.
    var t_load0 = perf_counter_ns()
    var matrix = load_f32(matrix_path, rows * dims)
    var query = load_f32(query_path, dims)
    var t_load1 = perf_counter_ns()

    var query_sq: Float64 = 0.0
    for i in range(dims):
        var q = Float64(query.unsafe_offset(i)[])
        query_sq += q * q
    var query_norm = sqrt(query_sq)

    def scan() {imm rows, imm dims, imm matrix, imm query, imm query_norm} -> Tuple[Int, Float64]:
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

    _ = scan()  # warm-up (page cache + branch predictors), unmeasured

    var best_ms: Float64 = -1.0
    var top_idx: Int = -1
    var top_score: Float64 = -2.0
    for _ in range(reps):
        var t0 = perf_counter_ns()
        var result = scan()
        var t1 = perf_counter_ns()
        top_idx = result[0]
        top_score = result[1]
        var ms = Float64(t1 - t0) / 1e6
        if best_ms < 0 or ms < best_ms:
            best_ms = ms

    print(t"simd_width={width}")
    print(t"rows={rows} dims={dims}")
    print(t"load_ms={round(Float64(t_load1 - t_load0) / 1e6, 3)}")
    print(t"compute_ms={round(best_ms, 4)}")
    print(t"top1_idx={top_idx}")
    print(t"top1_score={top_score}")

    matrix.unsafe_free()
    query.unsafe_free()
