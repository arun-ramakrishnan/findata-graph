# Temperature Analyzer (full 4-tier, MACHINE-LOCAL variant) — compute tiers:
#   1. scalar CPU loop (baseline)
#   2. SIMD-vectorized CPU loop (vectorize + hardware register width)
#   3. Intel GPU reduction kernel via the mojo_intel_gpu Level Zero shim
#      (Float32 — Gen9 FP64 is 1/64 rate; kernel: stats_reduce.cl -> .spv)
#   4. official MAX GPU path (CUDA/ROCm/Metal) — compile-time eliminated
#      unless has_accelerator()
#
# Machine-local companion of the repo's publishable 3-tier analyzer
# (findata Mojo/analyzer.mojo — scalar/SIMD/MAX only, no shim dependency).
# Findings log: findata doc/local/mojo_pilot.md.
#
# Run from THIS directory (mojo_intel_gpu.mojoc + stats_reduce.spv sit
# next to this file; venv on PATH via direnv for the numpy cross-check):
#   mojo run analyzer_intel.mojo
# Rebuild the shim package after changing the clone:
#   cd mojo-intel-gpu && mojo precompile mojo_intel_gpu -o ../mojo_intel_gpu.mojoc

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
from max.gpu import global_idx, thread_idx, block_idx
from max.gpu.sync import barrier
from max.gpu.memory import AddressSpace
from max.gpu.host import DeviceContext
from layout import TileTensor, row_major, stack_allocation
from std.python import Python, PythonObject
from std.python.numpy import copy_to_numpy_array
from mojo_intel_gpu import IntelGPUContext, Kernel, ZeGroupCount


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
    var sums = stack_allocation[dtype, address_space=AddressSpace.SHARED](
        row_major[BLOCK]()
    )
    var sumsqs = stack_allocation[dtype, address_space=AddressSpace.SHARED](
        row_major[BLOCK]()
    )
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
        "  NVIDIA/CUDA:",
        has_nvidia_gpu_accelerator(),
        " AMD/ROCm:",
        has_amd_gpu_accelerator(),
        " Apple/Metal:",
        has_apple_gpu_accelerator(),
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
    print(
        t"Benchmark: {N} Float64 samples ({round(Float64(N) / 1e6, 1)}M"
        t" synthetic temps)"
    )

    var t0 = perf_counter_ns()
    var scalar_stats = stats_scalar(data)
    var t1 = perf_counter_ns()
    var simd_stats = stats_simd(data)
    var t2 = perf_counter_ns()

    var scalar_ms = Float64(t1 - t0) / 1e6
    var simd_ms = Float64(t2 - t1) / 1e6
    print(
        t"  scalar loop  : {round(scalar_ms, 3)} ms "
        t" mean={round(scalar_stats.mean, 4)} "
        t" std={round(scalar_stats.std_dev, 4)}"
    )
    print(
        t"  SIMD (x{width})    : {round(simd_ms, 3)} ms "
        t" mean={round(simd_stats.mean, 4)} "
        t" std={round(simd_stats.std_dev, 4)}  speedup"
        t" {round(scalar_ms / simd_ms, 2)}x"
    )

    # --- Intel GPU tier (mojo_intel_gpu shim, Level Zero) -------------------

    var intel_ms: Float64 = -1.0
    var intel_stats = Stats(0.0, 0.0)
    try:
        var ctx = IntelGPUContext()
        comptime groups = N >> 8  # 256 threads/group, N is a power of two
        var gsize = UInt32(256)
        var in_bytes = UInt64(N * 4)
        var part_bytes = UInt64(groups * 4)

        var h_in = ctx.allocate_host(in_bytes)
        var h_sum = ctx.allocate_host(part_bytes)
        var h_sq = ctx.allocate_host(part_bytes)
        var in_ptr = Pointer[Float32, MutUntrackedOrigin](
            unsafe_from_address=h_in
        )
        var sum_ptr = Pointer[Float32, MutUntrackedOrigin](
            unsafe_from_address=h_sum
        )
        var sq_ptr = Pointer[Float32, MutUntrackedOrigin](
            unsafe_from_address=h_sq
        )
        for i in range(N):
            in_ptr.unsafe_offset(i)[] = Float32(data[i])
        for i in range(groups):
            sum_ptr.unsafe_offset(i)[] = 0.0
            sq_ptr.unsafe_offset(i)[] = 0.0

        var d_in = ctx.allocate_device(in_bytes)
        var d_sum = ctx.allocate_device(part_bytes)
        var d_sq = ctx.allocate_device(part_bytes)
        # Kernel construction JIT-compiles the SPIR-V module (one-time setup)
        var kernel = Kernel(
            ctx.library(),
            ctx.context(),
            ctx.device(),
            ctx.command_list(),
            "./stats_reduce.spv",
            "reduce_stats",
        )

        var g0 = perf_counter_ns()
        ctx.memcpy_htod(d_in, h_in, in_bytes)
        kernel.set_arg_pointer(0, d_in)
        kernel.set_arg_pointer(1, d_sum)
        kernel.set_arg_pointer(2, d_sq)
        kernel.set_arg_value(3, 4, UInt64(N))
        kernel.set_group_size(gsize, 1, 1)
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
        ctx.synchronize()
        ctx.memcpy_dtoh(h_sum, d_sum, part_bytes)
        ctx.memcpy_dtoh(h_sq, d_sq, part_bytes)
        var g1 = perf_counter_ns()

        var total: Float64 = 0.0
        var total_sq: Float64 = 0.0
        for i in range(groups):
            total += Float64(sum_ptr.unsafe_offset(i)[])
            total_sq += Float64(sq_ptr.unsafe_offset(i)[])

        intel_stats = make_stats(total, total_sq, N)
        intel_ms = Float64(g1 - g0) / 1e6
        print(
            t"  Intel GPU    : {round(intel_ms, 3)} ms end-to-end incl."
            t" transfers  [{ctx.device_info().name}] "
            t" mean={round(intel_stats.mean, 4)} "
            t" std={round(intel_stats.std_dev, 4)}  speedup"
            t" {round(scalar_ms / intel_ms, 2)}x"
        )

        ctx.free_device(d_in)
        ctx.free_device(d_sum)
        ctx.free_device(d_sq)
        ctx.free_host(h_in)
        ctx.free_host(h_sum)
        ctx.free_host(h_sq)
        ctx.close()
    except e:
        print("  Intel GPU    : skipped —", e)

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
        print(
            t"  GPU ({BLOCKS} blocks x {BLOCK} threads): {round(gpu_ms, 3)} ms"
            t" end-to-end incl. transfers  mean={round(gpu_stats.mean, 4)} "
            t" std={round(gpu_stats.std_dev, 4)}"
        )
    else:
        print(
            "  MAX accel.   : skipped — no CUDA/ROCm/Metal device (Intel path"
            " used instead)"
        )

    # --- cross-check against numpy -----------------------------------------

    var np_std_obj = np.std(copy_to_numpy_array(data))
    var delta = np.abs(np_std_obj - PythonObject(simd_stats.std_dev))
    print(
        t"Validation vs numpy std:"
        t" mojo={round(simd_stats.std_dev, 6)} numpy={np_std_obj} delta={delta}"
    )
    if Bool(delta < 1e-6):
        print("  OK — SIMD (Float64) results match")
    else:
        print("  MISMATCH beyond 1e-6")

    if intel_ms > 0:
        var gpu_delta = np.abs(np_std_obj - PythonObject(intel_stats.std_dev))
        print(
            t"Intel GPU (Float32) std: {round(intel_stats.std_dev, 6)} delta vs"
            t" numpy={gpu_delta}"
        )
        if Bool(gpu_delta < 0.01):
            print("  OK — GPU results match within Float32 tolerance")
        else:
            print("  MISMATCH beyond Float32 tolerance")
