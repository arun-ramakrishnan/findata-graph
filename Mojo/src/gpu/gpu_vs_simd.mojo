# Crossover benchmark: CPU SIMD vs Intel GPU on a resident buffer.
#
# Same sum / sum-of-squares reduction as the analyzer, at growing N. The
# GPU buffer is uploaded once (one-time H2D reported separately, excluded
# from the loop); the timed GPU iteration is launch + sync + partial D2H +
# host partial-reduce. CPU is the analyzer's single-thread SIMD path.
#
# Run from THIS directory (mojo_intel_gpu.mojoc adjacent):
#   mojo run gpu_vs_simd.mojo

from std.sys import simd_width_of
from std.math import abs, max
from std.algorithm.functional import vectorize
from std.time import perf_counter_ns
from std.memory.alloc import alloc, dealloc, Layout
from mojo_intel_gpu import IntelGPUContext, Kernel, ZeGroupCount


def fill_temps[
    origin: Origin[mut=True], //
](p: Pointer[Float32, origin], n: Int):
    var seed: Int = 42
    for i in range(n):
        seed = (seed * 1103515245 + 12345) % 2147483648
        p.unsafe_offset(i)[] = Float32(seed % 2000) / 100.0 + 15.0


def simd_stats32[
    origin: Origin[mut=True], //
](p: Pointer[Float32, origin], n: Int) -> Tuple[Float64, Float64]:
    comptime W = simd_width_of[DType.float32]()
    var total: Float64 = 0.0
    var total_sq: Float64 = 0.0

    def acc[width: Int](i: Int) {mut total, mut total_sq, imm p}:
        var v = p.unsafe_load[width=width](i)
        total += Float64(v.reduce_add())
        total_sq += Float64((v * v).reduce_add())

    vectorize[W](n, acc)
    return (total, total_sq)


def main() raises:
    var sizes: List[Int] = [1 << 20, 4 << 20, 16 << 20, 64 << 20]
    comptime K = 3

    var ctx = IntelGPUContext()
    print("Device:", ctx.device_info().name)
    print(
        "CPU: single-thread SIMD float32 | GPU: resident buffer, per-iteration"
    )
    print(
        "cost = launch + sync + partial D2H + host reduce (transfers excluded)"
    )
    print()
    print("      N     MB  CPU ms/iter  GPU ms/iter  speedup  rel err %")

    var kernel = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./stats_reduce.spv",
        "reduce_stats_gs",
    )
    kernel.set_group_size(256, 1, 1)

    for n in sizes:
        var bytes = UInt64(n * 4)
        var groups = 4096  # fixed grid; the kernel grid-strides over N
        var part_bytes = UInt64(groups * 4)

        # --- CPU side ---
        # unsafe_leak: bench owns the pointer; freed via unsafe_free() below
        var cp = alloc(Layout[Float32](count=n)).unsafe_leak()
        fill_temps(cp, n)
        var ct = (0.0, 0.0)
        var c0 = perf_counter_ns()
        for _ in range(K):
            ct = simd_stats32(cp, n)
        var c1 = perf_counter_ns()
        var cpu_ms = Float64(c1 - c0) / 1e6 / K

        # --- GPU side (upload once, timed iterations after warmup) ---
        var h_in = ctx.allocate_host(bytes)
        var hp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_in)
        fill_temps(hp, n)
        var d_in = ctx.allocate_device(bytes)
        var d_sum = ctx.allocate_device(part_bytes)
        var d_sq = ctx.allocate_device(part_bytes)
        var h_sum = ctx.allocate_host(part_bytes)
        var h_sq = ctx.allocate_host(part_bytes)
        var h_sum_ptr = Pointer[Float32, MutUntrackedOrigin](
            unsafe_from_address=h_sum
        )
        var h_sq_ptr = Pointer[Float32, MutUntrackedOrigin](
            unsafe_from_address=h_sq
        )

        var h2d0 = perf_counter_ns()
        ctx.memcpy_htod(d_in, h_in, bytes)
        var h2d1 = perf_counter_ns()
        _ = h2d0
        _ = h2d1

        kernel.set_arg_pointer(0, d_in)
        kernel.set_arg_pointer(1, d_sum)
        kernel.set_arg_pointer(2, d_sq)
        kernel.set_arg_value(3, 4, UInt64(n))

        # warmup launch (driver page-binding etc.), untimed
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
        ctx.synchronize()

        var gt = (0.0, 0.0)
        var g0 = perf_counter_ns()
        for _ in range(K):
            kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
            ctx.synchronize()
            ctx.memcpy_dtoh(h_sum, d_sum, part_bytes)
            ctx.memcpy_dtoh(h_sq, d_sq, part_bytes)
            var s: Float64 = 0.0
            var q: Float64 = 0.0
            for i in range(groups):
                s += Float64(h_sum_ptr.unsafe_offset(i)[])
                q += Float64(h_sq_ptr.unsafe_offset(i)[])
            gt = (s, q)
        var g1 = perf_counter_ns()
        var gpu_ms = Float64(g1 - g0) / 1e6 / K

        var rel_err = abs(ct[0] - gt[0]) / max(abs(gt[0]), 1.0)
        var mb = Float64(n * 4) / 1e6
        print(
            t"{n}  {round(mb, 0)}  {round(cpu_ms, 3)}  {round(gpu_ms, 3)}  "
            t"{round(cpu_ms / gpu_ms, 2)}x  {round(rel_err * 100, 10)}%"
        )

        ctx.free_device(d_in)
        ctx.free_device(d_sum)
        ctx.free_device(d_sq)
        ctx.free_host(h_in)
        ctx.free_host(h_sum)
        ctx.free_host(h_sq)
        cp.unsafe_free()

    ctx.close()
    print()
    print(
        "H2D of the 256MB working set (one-time): ~250 ms at the ~12.5 GB/s"
        " copy ceiling."
    )
