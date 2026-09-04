"""
 bench_pool.mojo — process-pool scaling of the SIMD cosine-KNN kernel.

 Mojo equivalent of the Python embed-pool bench (2026-08-29): N worker
 processes, each computing top-1 cosine for its slice of the query rows
 against the full matrix. Mojo 1.0 stdlib has NO host-threads API
 (parallelize/Thread/spawn absent, verified via mojo-mcp docs), so the
 honest equivalent of the Python spawn-Pool is N compiled processes —
 which also isolates whether the unpinned Python-pool collapse was
 ggml-specific (these workers are plain SIMD, no llama).

 usage: bench_pool <matrix.f32> <rows> <dims> <nqueries> <worker> <nworkers> <reps>
 matrix doubles as query bank: query i = matrix row i (pilot convention).
 Prints: worker=<w> queries=<k> compute_ms=<best> score_sum=<S> last_top=<i>:<s>
"""


from std.sys import argv
from std.time import perf_counter_ns
from std.math import sqrt

from cosine import load_f32, row_cosine


def main() raises:
    var args = argv()
    if len(args) < 8:
        print(
            "usage: bench_pool <matrix.f32> <rows> <dims> <nqueries> "
            "<worker> <nworkers> <reps>"
        )
        raise Error("expected 7 arguments")
    var matrix_path = String(args[1])
    var rows = Int(String(args[2]))
    var dims = Int(String(args[3]))
    var nqueries = Int(String(args[4]))
    var worker = Int(String(args[5]))
    var nworkers = Int(String(args[6]))
    var reps = Int(String(args[7]))

    var matrix = load_f32(matrix_path, rows * dims)

    # this worker's queries: contiguous strides q % nworkers == worker
    var mine: Int = 0
    for q in range(nqueries):
        if q % nworkers == worker:
            mine += 1

    def work() {
        imm matrix, imm rows, imm dims, imm nqueries, imm worker, imm nworkers
    } -> Tuple[Int, Float64, Float64]:
        var score_sum: Float64 = 0.0
        var best_idx: Int = -1
        var best_score: Float64 = -2.0
        for q in range(nqueries):
            if q % nworkers != worker:
                continue
            # query = matrix row q, pre-normalized divisor
            var qsq: Float64 = 0.0
            for d in range(dims):
                var v = Float64(matrix.unsafe_offset(q * dims + d)[])
                qsq += v * v
            var qnorm = sqrt(qsq)
            best_idx = -1
            best_score = -2.0
            for r in range(rows):
                var s = row_cosine(
                    matrix.unsafe_offset(r * dims),
                    matrix.unsafe_offset(q * dims),
                    qnorm,
                    dims,
                )
                if s > best_score:
                    best_score = s
                    best_idx = r
            score_sum += best_score
        return (best_idx, best_score, score_sum)

    _ = work()  # warm-up (page cache), unmeasured

    var best_ms: Float64 = -1.0
    var last_top: Int = -1
    var last_score: Float64 = -2.0
    var score_sum: Float64 = 0.0
    for _ in range(reps):
        var t0 = perf_counter_ns()
        var result = work()
        var t1 = perf_counter_ns()
        last_top = result[0]
        last_score = result[1]
        score_sum = result[2]
        var ms = Float64(t1 - t0) / 1e6
        if best_ms < 0 or ms < best_ms:
            best_ms = ms

    print(
        t"worker={worker} queries={mine} compute_ms={round(best_ms, 3)} "
        t"score_sum={round(score_sum, 6)} last_top={last_top}:{round(last_score, 6)}"
    )
    matrix.unsafe_free()
