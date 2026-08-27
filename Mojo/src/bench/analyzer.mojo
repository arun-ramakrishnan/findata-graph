# Temperature Analyzer — compute tiers (publishable variant):
#   1. scalar CPU loop (baseline)
#   2. SIMD-vectorized CPU loop (vectorize + hardware register width)
#   3. official MAX GPU path (CUDA/ROCm/Metal) — compile-time eliminated
#      unless has_accelerator(); std+max only, no machine-specific deps.
#
# The full 4-tier variant (adds an Intel-GPU tier via the community
# mojo_intel_gpu Level Zero shim) lives outside the repo:
#   ~/Research/MCP/mojo-intel-gpu-demo/analyzer_intel.mojo
# Findings log: doc/local/mojo_pilot.md.
#
# Run from the repo root (venv on PATH via direnv):
#   .venv/bin/mojo run Mojo/src/bench/analyzer.mojo

from std.math import sqrt
from std.bit import log2_floor
from std.sys import has_accelerator, simd_width_of
from std.sys.info import (
    has_nvidia_gpu_accelerator,
    has_amd_gpu_accelerator,
    has_apple_gpu_accelerator,
    num_logical_cores,
)
from std.algorithm.functional import vectorize
from std.time import perf_counter_ns
from std.gpu import global_idx, thread_idx, block_idx
from max.gpu.sync import barrier
from max.gpu.memory import AddressSpace
from max.gpu.host import DeviceContext
from layout import TileTensor, row_major, stack_allocation
from std.python import Python, PythonObject
from std.python.numpy import copy_to_numpy_array


@fieldwise_init
struct Stats(Copyable, Movable, Writable):
    var mean: Float64
    var std_dev: Float64


def make_stats(total: Float64, total_sq: Float64, n: Int) -> Stats:
    var mean = total / Float64(n)
    var variance = total_sq / Float64(n) - mean * mean
    return Stats(mean, sqrt(variance))


def stats_scalar(data: List[Float64]) -> Stats:
    var total = 0.0
    var total_sq = 0.0
    for i in range(len(data)):
        var v = data[i]
        total += v
        total_sq += v * v
    return make_stats(total, total_sq, len(data))


def stats_simd(data: List[Float64]) -> Stats:
    comptime width = simd_width_of[DType.float64]()
    var p = data.unsafe_ptr()
    var total: Float64 = 0.0
    var total_sq: Float64 = 0.0

    def accumulate[width: Int](i: Int) {mut total, mut total_sq, imm p}:
        var v = p.unsafe_load[width=width](i)
        total += v.reduce_add()
        total_sq += (v * v).reduce_add()

    vectorize[width](len(data), accumulate)
    return make_stats(total, total_sq, len(data))


# --- GPU tier -------------------------------------------------------------

comptime dtype = DType.float64
comptime BLOCK = 256
comptime BLOCKS = 512
comptime data_layout = row_major[1 << 20]()
comptime partial_layout = row_major[BLOCKS]()


def stats_kernel(
    inp: TileTensor[dtype, type_of(data_layout), MutAnyOrigin],
    partial_sum: TileTensor[dtype, type_of(partial_layout), MutAnyOrigin],
    partial_sumsq: TileTensor[dtype, type_of(partial_layout), MutAnyOrigin],
    size: Int32,
    total_threads: Int32,
):
    comptime assert inp.flat_rank == 1
    var local: Scalar[dtype] = 0.0
    var local_sq: Scalar[dtype] = 0.0

    # grid-stride: each thread walks the array with a full-grid step
    var i = global_idx.x
    var n = Int(size)
    var stride = Int(total_threads)
    while i < n:
        var v = rebind[Scalar[dtype]](inp[i])
        local += v
        local_sq += v * v
        i += stride

    # block-level tree reduction in shared memory
    var sums = stack_allocation[dtype,
        address_space=AddressSpace.SHARED](row_major[BLOCK]())
    var sumsqs = stack_allocation[dtype,
        address_space=AddressSpace.SHARED](row_major[BLOCK]())
    sums[thread_idx.x] = local
    sumsqs[thread_idx.x] = local_sq
    barrier()

    var active = BLOCK
    comptime for _ in range(log2_floor(BLOCK)):
        active >>= 1
        if thread_idx.x < active:
            sums[thread_idx.x] += sums[thread_idx.x + active]
            sumsqs[thread_idx.x] += sumsqs[thread_idx.x + active]
        barrier()

    if thread_idx.x == 0:
        partial_sum[block_idx.x] = sums[0]
        partial_sumsq[block_idx.x] = sumsqs[0]


# --- original program ------------------------------------------------------


def calculate_average(temps: List[Float64]) raises -> Float64:
    if len(temps) == 0:
        raise Error("No temperature data")

    var total = 0.0
    for temp in temps:
        total += temp
    return total / Float64(len(temps))


def main() raises:
    print("Temperature Analyzer")
    var temps: List[Float64] = [20.5, 22.3, 19.8, 25.1]
    print("Recorded", len(temps), "temperatures")

    for index in range(len(temps)):
        print(t"  Day {index + 1}: {temps[index]}°C")

    var avg = calculate_average(temps)
    print(t"Average: {round(avg, 2)}°C")

    if avg > 25.0:
        print("Status: Hot week")
    elif avg > 20.0:
        print("Status: Comfortable week")
    else:
        print("Status: Cool week")

    var np = Python.import_module("numpy")
    var std_dev = np.std(copy_to_numpy_array(temps))
    print("Temperature standard deviation:", std_dev)

    # --- accelerator census ------------------------------------------------

    print()
    print("Accelerator census:")
    print("  logical cores:", num_logical_cores())
    print(
        "  NVIDIA/CUDA:", has_nvidia_gpu_accelerator(),
        " AMD/ROCm:", has_amd_gpu_accelerator(),
        " Apple/Metal:", has_apple_gpu_accelerator(),
    )

    # --- benchmark: 1M synthetic temperature samples -----------------------

    comptime N = 1 << 20
    comptime width = simd_width_of[DType.float64]()
    var data: List[Float64] = []
    var seed: Int = 42
    for _ in range(N):
        seed = (seed * 1103515245 + 12345) % 2147483648
        data.append(Float64(seed % 2000) / 100.0 + 15.0)

    print()
    print(t"Benchmark: {N} Float64 samples ({round(Float64(N) / 1e6, 1)}M synthetic temps)")

    var t0 = perf_counter_ns()
    var scalar_stats = stats_scalar(data)
    var t1 = perf_counter_ns()
    var simd_stats = stats_simd(data)
    var t2 = perf_counter_ns()

    var scalar_ms = Float64(t1 - t0) / 1e6
    var simd_ms = Float64(t2 - t1) / 1e6
    print(t"  scalar loop  : {round(scalar_ms, 3)} ms  mean={round(scalar_stats.mean, 4)}  std={round(scalar_stats.std_dev, 4)}")
    print(t"  SIMD (x{width})    : {round(simd_ms, 3)} ms  mean={round(simd_stats.mean, 4)}  std={round(simd_stats.std_dev, 4)}  speedup {round(scalar_ms / simd_ms, 2)}x")

    comptime if has_accelerator():
        var ctx = DeviceContext()
        var dev_in = ctx.enqueue_create_buffer[dtype](N)
        var partial_s = ctx.enqueue_create_buffer[dtype](BLOCKS)
        var partial_q = ctx.enqueue_create_buffer[dtype](BLOCKS)
        var host_in = ctx.enqueue_create_host_buffer[dtype](N)

        var g0 = perf_counter_ns()
        for i in range(N):
            host_in[i] = data[i]
        ctx.enqueue_copy(dst_buf=dev_in, src_buf=host_in)
        ctx.enqueue_function[stats_kernel](
            TileTensor(dev_in, data_layout),
            TileTensor(partial_s, partial_layout),
            TileTensor(partial_q, partial_layout),
            Int32(N),
            Int32(BLOCKS * BLOCK),
            grid_dim=BLOCKS,
            block_dim=BLOCK,
        )

        var total: Float64 = 0.0
        var total_sq: Float64 = 0.0
        with partial_s.map_to_host() as hs:
            with partial_q.map_to_host() as hq:
                var ts = TileTensor(hs, partial_layout)
                var tq = TileTensor(hq, partial_layout)
                comptime assert ts.flat_rank == 1 and tq.flat_rank == 1
                for b in range(BLOCKS):
                    total += rebind[Float64](ts[b])
                    total_sq += rebind[Float64](tq[b])
        var g1 = perf_counter_ns()

        var gpu_stats = make_stats(total, total_sq, N)
        var gpu_ms = Float64(g1 - g0) / 1e6
        print(t"  GPU ({BLOCKS} blocks x {BLOCK} threads): {round(gpu_ms, 3)} ms end-to-end incl. transfers  mean={round(gpu_stats.mean, 4)}  std={round(gpu_stats.std_dev, 4)}")
    else:
        print("  MAX accel.   : skipped — no CUDA/ROCm/Metal device on this machine")

    # --- cross-check against numpy -----------------------------------------

    var np_std_obj = np.std(copy_to_numpy_array(data))
    var delta = np.abs(np_std_obj - PythonObject(simd_stats.std_dev))
    print(t"Validation vs numpy std: mojo={round(simd_stats.std_dev, 6)} numpy={np_std_obj} delta={delta}")
    if Bool(delta < 1e-6):
        print("  OK — SIMD (Float64) results match")
    else:
        print("  MISMATCH beyond 1e-6")
