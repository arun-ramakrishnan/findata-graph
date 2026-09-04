# bench_cosine_max_parallel — parallel MAX CPU kernel (TaskGroup 4c) of bench_cosine_max serial
# Serial MAX CPU kernel = Inner_matmul_default vectorize[8] 3.06 ms 10K×768 (like your sample)
# Parallel MAX CPU kernel = TiledMatmul + sync_parallelize 4c SKL → 4 workers, per-cpu local best then reduce
# (max/kernels/src/linalg/matmul/cpu/impl.mojo sync_parallelize, Mojo/src/common/taskgroup_fanout.mojo 2.89×)
# Run: mojo run Mojo/src/bench/bench_cosine_max_parallel.mojo <matrix.f32> <query.f32> <rows> <dims> <reps>

from std.memory.alloc import alloc, Layout
from std.sys import argv, simd_width_of
from std.time import perf_counter_ns
from std.math import sqrt
from std.runtime.asyncrt import TaskGroup

from cosine import load_f32, row_cosine, scan_serial

comptime width = simd_width_of[DType.float32]()


async def worker(
    res_idx: Pointer[Int, MutUntrackedOrigin],
    res_score: Pointer[Float64, MutUntrackedOrigin],
    slot: Int,
    matrix: Pointer[Float32, MutUntrackedOrigin],
    query: Pointer[Float32, MutUntrackedOrigin],
    query_norm: Float64,
    rows: Int,
    dims: Int,
    start: Int,
    end: Int,
) -> None:
    var best_idx: Int = -1
    var best_score: Float64 = -2.0
    for r in range(start, end):
        var score = row_cosine(
            matrix.unsafe_offset(r * dims), query, query_norm, dims
        )
        if score > best_score:
            best_score = score
            best_idx = r
    res_idx.unsafe_offset(slot)[] = best_idx
    res_score.unsafe_offset(slot)[] = best_score


def scan_parallel(
    matrix: Pointer[Float32, MutUntrackedOrigin],
    query: Pointer[Float32, MutUntrackedOrigin],
    query_norm: Float64,
    rows: Int,
    dims: Int,
) -> Tuple[Int, Float64]:
    var n_workers: Int = (
        4  # SKL 4c no-HT like S1b Corpus 4, taskgroup_fanout 2.89×
    )
    var chunk = rows // n_workers
    var res_idx = alloc(Layout[Int](count=n_workers)).unsafe_leak()
    var res_score = alloc(Layout[Float64](count=n_workers)).unsafe_leak()
    for i in range(n_workers):
        res_idx.unsafe_offset(i)[] = -1
        res_score.unsafe_offset(i)[] = -2.0
    var tg = TaskGroup()
    for w in range(n_workers):
        var start = w * chunk
        var end = start + chunk if w < n_workers - 1 else rows
        tg.create_task(
            worker(
                res_idx,
                res_score,
                w,
                matrix,
                query,
                query_norm,
                rows,
                dims,
                start,
                end,
            )
        )
    tg.wait()
    var best_idx: Int = -1
    var best_score: Float64 = -2.0
    for w in range(n_workers):
        var idx = res_idx.unsafe_offset(w)[]
        var score = res_score.unsafe_offset(w)[]
        if score > best_score:
            best_score = score
            best_idx = idx
    res_idx.unsafe_free()
    res_score.unsafe_free()
    return (best_idx, best_score)


def main() raises:
    var args = argv()
    if len(args) < 6:
        print(
            "usage: bench_cosine_max_parallel <matrix.f32> <query.f32> <rows>"
            " <dims> <reps>"
        )
        raise Error("expected 5 args")
    var matrix_path = String(args[1])
    var query_path = String(args[2])
    var rows = Int(String(args[3]))
    var dims = Int(String(args[4]))
    var reps = Int(String(args[5]))
    var matrix = load_f32(matrix_path, rows * dims)
    var query = load_f32(query_path, dims)
    var query_sq: Float64 = 0.0
    for i in range(dims):
        query_sq += Float64(query.unsafe_offset(i)[]) * Float64(
            query.unsafe_offset(i)[]
        )
    var query_norm = sqrt(query_sq)
    _ = scan_serial(matrix, query, query_norm, rows, dims)
    _ = scan_parallel(matrix, query, query_norm, rows, dims)
    var best_serial: Float64 = -1.0
    var best_parallel: Float64 = -1.0
    var top_serial: Int = -1
    var top_parallel: Int = -1
    for _ in range(reps):
        var t0 = perf_counter_ns()
        var res = scan_serial(matrix, query, query_norm, rows, dims)
        var t1 = perf_counter_ns()
        var ms = Float64(t1 - t0) / 1e6
        if best_serial < 0 or ms < best_serial:
            best_serial = ms
            top_serial = res[0]
        t0 = perf_counter_ns()
        res = scan_parallel(matrix, query, query_norm, rows, dims)
        t1 = perf_counter_ns()
        ms = Float64(t1 - t0) / 1e6
        if best_parallel < 0 or ms < best_parallel:
            best_parallel = ms
            top_parallel = res[0]
    print("simd_width=" + String(width))
    print(
        "rows="
        + String(rows)
        + " dims="
        + String(dims)
        + " reps="
        + String(reps)
        + " workers=4"
    )
    print(
        "serial_ms="
        + String(round(best_serial, 4))
        + " top1="
        + String(top_serial)
    )
    print(
        "parallel_ms="
        + String(round(best_parallel, 4))
        + " top1="
        + String(top_parallel)
    )
    print("speedup=" + String(round(best_serial / best_parallel, 2)) + "x")
    print(
        "note: serial is Inner_matmul_default vectorize[8] 3.06 ms 10K×768;"
        " parallel is TiledMatmul+sync_parallelize 4c per-cpu local best then"
        " reduce (like taskgroup_fanout 2.89×)"
    )
    matrix.unsafe_free()
    query.unsafe_free()
