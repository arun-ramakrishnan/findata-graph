# Experiment E — heterogeneous co-execution on the REAL embedding matrix.
# CPU (simd32) reduces HALF the buffer while the GPU kernel reduces the other
# HALF concurrently (L0: launch is async-append, host runs CPU leg, synchronize
# blocks at the end). Wall-time = max(GPU half, CPU half) — the only path to
# > 1.89x (single-device best, B) since both devices work at once.
#
# Use the group-64 / float4 config from B which hit 1.307 ms (25.4 MB).
# The CPU half of the reduced work is the same sumsq/sum shape; its ~2.47 ms
# full-buffer time halves to ~1.2 ms — near the GPU's, hence overlap.
#
# Run: mojo run hetero_real.mojo <matrix.f32> [passes]
#   passes default 100.

from std.math import abs, max
from std.sys import simd_width_of
from std.os import SEEK_END
from std.sys import argv
from std.algorithm.functional import vectorize
from std.time import perf_counter_ns
from std.memory.alloc import alloc, Layout
from mojo_intel_gpu import IntelGPUContext, Kernel, ZeGroupCount


def simd_stats32[
    origin: Origin[mut=True], //
](p: Pointer[Float32, origin], n0: Int, n1: Int) -> Tuple[Float64, Float64]:
    """Reduce p[n0:n1] (half of the buffer) with SIMD."""
    comptime W = simd_width_of[DType.float32]()
    var total: Float64 = 0.0
    var total_sq: Float64 = 0.0

    def acc[width: Int](i: Int) {mut total, mut total_sq, imm p, imm n0}:
        var v = p.unsafe_load[width=width](n0 + i)
        total += Float64(v.reduce_add())
        total_sq += Float64((v * v).reduce_add())

    vectorize[W](n1 - n0, acc)
    return (total, total_sq)


def main() raises:
    var cli = argv()
    if len(cli) < 2:
        print("usage: mojo run hetero_real.mojo <matrix.f32> [passes]")
        return
    var path = cli[1]
    var passes: Int = 100
    if len(cli) >= 3:
        passes = atol(cli[2])
    var gpu_pct: Int = 50
    if len(cli) >= 4:
        gpu_pct = atol(cli[3])

    var fh = open(path, "r")
    var size = UInt64(fh.seek(0, SEEK_END))
    var nbytes = Int(size)
    var n = nbytes // 4
    _ = fh
    print("matrix:", path, "floats:", n, "(", Float64(nbytes) / 1e6, "MB )")
    if n % 4 != 0:
        print("ERROR: needs n % 4 == 0")
        return

    var groups = 4096
    var part_bytes = UInt64(groups * 4)

    var cp = alloc(Layout[Float32](count=n)).unsafe_leak()
    var fh1 = open(path, "r")
    var got = fh1.read(Span(unsafe_ptr=cp, length=n))
    fh1.close()

    var ctx = IntelGPUContext()
    var kernel = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./tune_real.spv",
        "tune_f4",
    )
    kernel.set_group_size(64, 1, 1)

    var bytes = UInt64(nbytes)
    var h_in = ctx.allocate_host(bytes)
    var hp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_in)
    var fh2 = open(path, "r")
    var hgot = fh2.read(Span(unsafe_ptr=hp, length=n))
    fh2.close()
    if hgot != got:
        print("ERROR: host/GPU buffer byte mismatch")

    var d_in = ctx.allocate_device(bytes)
    var d_sum = ctx.allocate_device(part_bytes)
    var d_sq = ctx.allocate_device(part_bytes)
    var h_sum = ctx.allocate_host(part_bytes)
    var h_sq = ctx.allocate_host(part_bytes)
    var hp_sum = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_sum)
    var hp_sq = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_sq)

    var h2d0 = perf_counter_ns()
    ctx.memcpy_htod(d_in, h_in, bytes)
    var h2d1 = perf_counter_ns()
    print("H2D (excluded):", round(Float64(h2d1 - h2d0) / 1e6, 3), "ms")

    # GPU reduces first `split` floats, CPU the remainder. Kernel reads n4 =
    # split/4 (grid-stride over float4). CPU leg reads cp[split:n].
    var split = (n * gpu_pct) // 100  # gpu_pct% to GPU, rest to CPU
    if split % 4 != 0:
        split -= split % 4
    var n_gpu = split
    var n_cpu = n - split

    kernel.set_arg_pointer(0, d_in)
    kernel.set_arg_pointer(1, d_sum)
    kernel.set_arg_pointer(2, d_sq)
    kernel.set_arg_value(3, 4, UInt64(n_gpu))

    # warmup
    kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
    ctx.synchronize()

    var wall_times = List[Float64]()
    var gpu_times = List[Float64]()
    var la_times = List[Float64]()
    var cpu_times = List[Float64]()
    var gpu_sum: Float64 = 0.0
    var cpu_sum: Float64 = 0.0
    for _ in range(passes):
        var t0 = perf_counter_ns()
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))  # async append
        var t_launch_done = perf_counter_ns()
        (cpu_sum, _) = simd_stats32(cp, split, n)  # CPU leg while GPU runs
        var t_cpu_done = perf_counter_ns()
        ctx.synchronize()  # blocks until GPU leg done
        ctx.memcpy_dtoh(h_sum, d_sum, part_bytes)
        var s: Float64 = 0.0
        for i in range(groups):
            s += Float64(hp_sum.unsafe_offset(i)[])
        gpu_sum = s  # GPU leg partial (first half)
        var t1 = perf_counter_ns()

        wall_times.append(Float64(t1 - t0) / 1e6)  # launch..readback
        la_times.append(Float64(t_launch_done - t0) / 1e6)  # append overhead
        cpu_times.append(Float64(t_cpu_done - t0) / 1e6)  # CPU leg elapsed
        gpu_times.append(Float64(t1 - t_cpu_done) / 1e6)  # sync+readback tail

    var w_min = 1e18
    var w_max = 0.0
    var w_total = 0.0
    for t in wall_times:
        w_total += t
        if t < w_min:
            w_min = t
        if t > w_max:
            w_max = t
    var w_avg = w_total / Float64(passes)

    var c_avg = 0.0
    for t in cpu_times:
        c_avg += t
    c_avg /= Float64(passes)
    var g_avg = 0.0
    for t in gpu_times:
        g_avg += t
    g_avg /= Float64(passes)
    var la_avg = 0.0
    for t in la_times:
        la_avg += t
    la_avg /= Float64(passes)

    var mb = Float64(nbytes) / 1e6
    # Baselines from B/D/A on the SAME full buffer:
    #   CPU simd32 full ~2.47 ms; GPU f4@g64 full ~1.31 ms.
    # Honest E yardstick = best single device (GPU 1.31 ms) — beat that.
    print()
    print(
        "passes:",
        passes,
        "| split",
        gpu_pct,
        "/",
        100 - gpu_pct,
        "gpu/cpu @",
        n_gpu,
        "/",
        n_cpu,
        "floats (",
        round(mb, 3),
        "MB total )",
    )
    print(
        t"HETERO wall: min {round(w_min, 4)}  avg {round(w_avg, 4)}  "
        t"max {round(w_max, 4)} ms/pass"
    )
    print(
        t"  breakdown: launch-append {round(la_avg, 4)} | CPU leg (to its end) "
        t"{round(c_avg, 4)} | sync+readback tail {round(g_avg, 4)} ms"
    )
    print(
        t"  vs CPU full 2.47ms = {round(2.47 / w_avg, 2)}x  |  "
        t"vs GPU-only full ~1.31ms = {round(1.31 / w_avg, 2)}x"
    )
    print(
        "  gpu partial sum:",
        round(gpu_sum, 6),
        " cpu partial sum:",
        round(cpu_sum, 6),
        " total ~",
        round(gpu_sum + cpu_sum, 6),
        "(full = -4049.255781 )",
    )

    ctx.free_device(d_in)
    ctx.free_device(d_sum)
    ctx.free_device(d_sq)
    ctx.free_host(h_in)
    ctx.free_host(h_sum)
    ctx.free_host(h_sq)
    cp.unsafe_free()
    ctx.close()
