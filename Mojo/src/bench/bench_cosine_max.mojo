# bench_cosine_max — MAX CPU kernel port (serial) of pilot bench_cosine.
# Port of Mojo/src/bench/bench_cosine.mojo to MAX CPU kernel pattern:
# max/kernels/src/linalg/matmul/cpu/default.mojo Inner_matmul_default (scalar FMA fallback)
# + impl.mojo TiledMatmul trait InnerMatmulKernel, SKL AVX2 4c → default 8-wide FMA
# (not VNNI/NEON/I8MM, like bench_scale proxy). Serial MAX CPU kernel = your sample
# vectorize[8] + _Accumulator, not parallel sync_parallelize.
# Run: mojo run Mojo/src/bench/bench_cosine_max.mojo <matrix.f32> <query.f32> <rows> <dims> <reps>

"""
 Cosine-KNN benchmark — the Mojo leg of the Mojo/tests/bench_cosine_knn.py harness.

 Times a SIMD-vectorized whole-corpus brute-force cosine scan (dot product
 + per-row norm per row — the same math as the Python cosine fallback in
 app.py._scored_rows._cosine) over a real note-embedding corpus dumped to
 a raw float32 file by the Python driver. Results + verdict:
 doc/local/mojo_pilot.md.

 File format is headerless little-endian float32; rows/dims/reps arrive on
 the command line:
   mojo run Mojo/src/bench/bench_cosine.mojo <matrix.f32> <query.f32> <rows> <dims> <reps>
 (or build once with `mojo build` and run the binary directly — the
 Python driver does exactly that so process startup is measurable too).
"""


from std.sys import argv, simd_width_of
from std.time import perf_counter_ns
from std.math import sqrt

from cosine import load_f32, row_cosine


def main() raises:
    var args = argv()
    if len(args) < 6:
        print(
            "usage: bench_cosine <matrix.f32> <query.f32> <rows> <dims> <reps>"
        )
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

    def scan() {
        imm rows, imm dims, imm matrix, imm query, imm query_norm
    } -> Tuple[Int, Float64]:
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
