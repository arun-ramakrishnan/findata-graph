# Experiment D: multi-pass data residency on the REAL corpus embedding matrix.
# Same shape as gpu_vs_simd.mojo, but the GPU buffer is loaded from the real
# 16560x384 f32 matrix (memory/embed_matrix.f32, 6359040 floats, 25.44 MB)
# instead of synthetic temps, and K passes are timed per-iteration so we get
# min/avg/max (the "stable GPU vs noisy CPU" residency claim lives here).
#
# Run from THIS directory (mojo_intel_gpu.mojoc + stats_reduce.spv adjacent):
#   mojo run gpu_vs_simd_real.mojo <path/to/embed_matrix.f32> [passes]
#
# Default: 100 passes over the resident buffer (transfer excluded from timings).

from std.math import abs, max
from std.sys import simd_width_of
from std.os import SEEK_END
from std.sys import argv
from std.algorithm.functional import vectorize
from std.time import perf_counter_ns
from std.memory.alloc import alloc, dealloc, Layout
from mojo_intel_gpu import IntelGPUContext, Kernel, ZeGroupCount


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
    var cli = argv()
    if len(cli) < 2:
        print("usage: mojo run gpu_vs_simd_real.mojo <matrix.f32> [passes]")
        return
    var path = cli[1]
    var passes: Int = 100
    if len(cli) >= 3:
        passes = atol(cli[2])

    var fh = open(path, "r")
    var size = UInt64(fh.seek(0, SEEK_END))
    var nbytes = Int(size)
    var n = nbytes // 4
    _ = fh
    print("matrix:", path, "floats:", n, "(", Float64(nbytes) / 1e6, "MB )")
    if n % 4 != 0:
        print("ERROR: kernel needs n % 4 == 0 (float4 loads)")
        return

    comptime K = 4  # group count as in samples (4096 fixed grid handled below)
    var groups = 4096
    var part_bytes = UInt64(groups * 4)

    # --- CPU host buffer (read the real file once) ---
    var cp = alloc(Layout[Float32](count=n)).unsafe_leak()
    var fh1 = open(path, "r")
    var got = fh1.read(Span(unsafe_ptr=cp, length=n))
    fh1.close()

    # --- GPU side ---
    var ctx = IntelGPUContext()
    var kernel = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./stats_reduce.spv",
        "reduce_stats_gs",
    )
    kernel.set_group_size(256, 1, 1)

    var bytes = UInt64(nbytes)
    var h_in = ctx.allocate_host(bytes)
    var hp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_in)
    var fh2 = open(path, "r")
    var hgot = fh2.read(Span(unsafe_ptr=hp, length=n))
    fh2.close()
    if hgot != got:
        print("ERROR: host/GPU buffer byte mismatch", hgot, got)

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
    print(
        "H2D one-time (excluded):", round(Float64(h2d1 - h2d0) / 1e6, 3), "ms"
    )

    kernel.set_arg_pointer(0, d_in)
    kernel.set_arg_pointer(1, d_sum)
    kernel.set_arg_pointer(2, d_sq)
    kernel.set_arg_value(3, 4, UInt64(n))

    # warmup launch (un-timed)
    kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
    ctx.synchronize()

    # --- CPU: passes timed per-iteration, min/avg/max ---
    var cpu_times = List[Float64]()
    var cpu_sum: Float64 = 0.0
    var cpu_sq: Float64 = 0.0
    for _ in range(passes):
        var t0 = perf_counter_ns()
        (cpu_sum, cpu_sq) = simd_stats32(cp, n)
        var t1 = perf_counter_ns()
        cpu_times.append(Float64(t1 - t0) / 1e6)
    var cpu_min = 1e18
    var cpu_max = 0.0
    var cpu_total = 0.0
    for t in cpu_times:
        cpu_total += t
        if t < cpu_min:
            cpu_min = t
        if t > cpu_max:
            cpu_max = t

    # --- GPU: passes timed per-iteration, min/avg/max ---
    var gpu_times = List[Float64]()
    var ls_times = List[Float64]()
    var rd_times = List[Float64]()
    var gpu_sum: Float64 = 0.0
    var gpu_sq: Float64 = 0.0
    for _ in range(passes):
        var t0 = perf_counter_ns()
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
        ctx.synchronize()
        var t_ls = perf_counter_ns()
        ctx.memcpy_dtoh(h_sum, d_sum, part_bytes)
        ctx.memcpy_dtoh(h_sq, d_sq, part_bytes)
        var s: Float64 = 0.0
        var q: Float64 = 0.0
        for i in range(groups):
            s += Float64(h_sum_ptr.unsafe_offset(i)[])
            q += Float64(h_sq_ptr.unsafe_offset(i)[])
        gpu_sum = s
        gpu_sq = q
        var t1 = perf_counter_ns()
        gpu_times.append(Float64(t1 - t0) / 1e6)
        ls_times.append(Float64(t_ls - t0) / 1e6)
        rd_times.append(Float64(t1 - t_ls) / 1e6)
    var gpu_min = 1e18
    var gpu_max = 0.0
    var gpu_total = 0.0
    for t in gpu_times:
        gpu_total += t
        if t < gpu_min:
            gpu_min = t
        if t > gpu_max:
            gpu_max = t

    var cpu_avg = cpu_total / Float64(passes)
    var gpu_avg = gpu_total / Float64(passes)
    var ls_total = 0.0
    var rd_total = 0.0
    var ls_max = 0.0
    var rd_max = 0.0
    for i in range(len(ls_times)):
        ls_total += ls_times[i]
        rd_total += rd_times[i]
        if ls_times[i] > ls_max:
            ls_max = ls_times[i]
        if rd_times[i] > rd_max:
            rd_max = rd_times[i]
    var ls_avg = ls_total / Float64(passes)
    var rd_avg = rd_total / Float64(passes)
    var rel_err = abs(cpu_sum - gpu_sum) / max(abs(gpu_sum), 1.0)
    var mb = Float64(nbytes) / 1e6

    print()
    print("passes:", passes, "| floats:", n, "|", round(mb, 3), "MB")
    print(
        t"CPU (simd32):  min {round(cpu_min, 4)}  avg {round(cpu_avg, 4)}  "
        t"max {round(cpu_max, 4)} ms/iter  {round(mb / cpu_avg, 2)} GB/s"
    )
    print(
        t"GPU (gs32):    min {round(gpu_min, 4)}  avg {round(gpu_avg, 4)}  "
        t"max {round(gpu_max, 4)} ms/iter  {round(mb / gpu_avg, 2)} GB/s"
    )
    print(
        t"  launch+sync: avg {round(ls_avg, 4)}  max {round(ls_max, 4)}  |  "
        t"D2H+reduce:   avg {round(rd_avg, 4)}  max {round(rd_max, 4)} ms"
    )
    print(
        t"avgs: {round(cpu_avg / gpu_avg, 2)}x  min-spread GPU"
        t" {round(gpu_max / gpu_min, 3)}x CPU {round(cpu_max / cpu_min, 3)}x "
        t" rel err {round(rel_err * 100, 10)}%"
    )

    ctx.free_device(d_in)
    ctx.free_device(d_sum)
    ctx.free_device(d_sq)
    ctx.free_host(h_in)
    ctx.free_host(h_sum)
    ctx.free_host(h_sq)
    cp.unsafe_free()
    ctx.close()
