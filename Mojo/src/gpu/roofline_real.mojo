# Experiment A: roofline compute-intensity sweep on the REAL corpus matrix.
# Escalates ALU-per-byte on the same resident buffer (the 16560x384 f32
# matrix) through three intensities and measures CPU simd32 vs GPU for each:
#   L1 sum + sum-of-squares       (memory-bound baseline ~2 FLOP/elem)
#   L2 x^4 + x^2                  (~6 FLOP/elem, FMA pressure)
#   L3 exp(sin(x)) + sqrt(|x|)    (transcendentals, ALU-bound; Gen9 native
#                                  transcendental units are the slow path)
# The point per the runbook: if the GPU win grows as compute intensity rises
# past the L1 memory-bound ~1.4x, the roofline thesis holds (2-6x honest
# ceiling). If L2/L3 stay ~= L1, the dispatch floor found in D caps the win
# regardless of intensity.
#
# Run from THIS directory (mojo_intel_gpu.mojoc + roofline_real.spv adjacent):
#   mojo run roofline_real.mojo <path/to/embed_matrix.f32> [passes_per_intensity]
#
# Default: 100 passes per intensity, one H2D transfer (excluded from timings).

from std.math import abs, max, exp, sin, sqrt
from std.sys import simd_width_of
from std.os import SEEK_END
from std.sys import argv
from std.algorithm.functional import vectorize
from std.time import perf_counter_ns
from std.memory.alloc import alloc, Layout
from mojo_intel_gpu import IntelGPUContext, Kernel, ZeGroupCount

comptime W = simd_width_of[DType.float32]()


def simd_l1[
    origin: Origin[mut=True], //
](p: Pointer[Float32, origin], n: Int) -> Tuple[Float64, Float64]:
    var total: Float64 = 0.0
    var total_sq: Float64 = 0.0

    def acc[width: Int](i: Int) {mut total, mut total_sq, imm p}:
        var v = p.unsafe_load[width=width](i)
        total += Float64(v.reduce_add())
        total_sq += Float64((v * v).reduce_add())

    vectorize[W](n, acc)
    return (total, total_sq)


def simd_l2[
    origin: Origin[mut=True], //
](p: Pointer[Float32, origin], n: Int) -> Tuple[Float64, Float64]:
    var total: Float64 = 0.0
    var total_sq: Float64 = 0.0

    def acc[width: Int](i: Int) {mut total, mut total_sq, imm p}:
        var v = p.unsafe_load[width=width](i)
        var v2 = v * v
        total += Float64((v2 * v2).reduce_add())
        total_sq += Float64(v2.reduce_add())

    vectorize[W](n, acc)
    return (total, total_sq)


def simd_l3[
    origin: Origin[mut=True], //
](p: Pointer[Float32, origin], n: Int) -> Tuple[Float64, Float64]:
    var total: Float64 = 0.0
    var total_sqrt: Float64 = 0.0

    def acc[width: Int](i: Int) {mut total, mut total_sqrt, imm p}:
        var v = p.unsafe_load[width=width](i)
        var e = exp(sin(v))
        var r = sqrt(abs(v))
        total += Float64(e.reduce_add())
        total_sqrt += Float64(r.reduce_add())

    vectorize[W](n, acc)
    return (total, total_sqrt)


def stats(
    cp: Pointer[Float32, MutUntrackedOrigin], n: Int, intensity: Int
) -> Tuple[Float64, Float64]:
    if intensity == 1:
        return simd_l1(cp, n)
    if intensity == 2:
        return simd_l2(cp, n)
    return simd_l3(cp, n)


def run_sweep(
    mut kernel: Kernel,
    cp: Pointer[Float32, MutUntrackedOrigin],
    hp: Pointer[Float32, MutUntrackedOrigin],
    n: Int,
    groups: Int,
    passes: Int,
    intensity: Int,
    d_in: Int,
    d_a: Int,
    d_b: Int,
    h_a: Int,
    h_b: Int,
    part_bytes: UInt64,
    mut ctx: IntelGPUContext,
) raises:
    var hp_a = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_a)
    var hp_b = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_b)

    kernel.set_arg_pointer(0, d_in)
    kernel.set_arg_pointer(1, d_a)
    kernel.set_arg_pointer(2, d_b)
    kernel.set_arg_value(3, 4, UInt64(n))

    kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
    ctx.synchronize()

    var cpu_times = List[Float64]()
    var cpu_ref: Float64 = 0.0
    var cpu_ref2: Float64 = 0.0
    for _ in range(passes):
        var t0 = perf_counter_ns()
        (cpu_ref, cpu_ref2) = stats(cp, n, intensity)
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

    var gpu_times = List[Float64]()
    var ls_times = List[Float64]()
    var rd_times = List[Float64]()
    var gpu_ref: Float64 = 0.0
    var gpu_ref2: Float64 = 0.0
    for _ in range(passes):
        var t0 = perf_counter_ns()
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
        ctx.synchronize()
        var t_ls = perf_counter_ns()
        ctx.memcpy_dtoh(h_a, d_a, part_bytes)
        ctx.memcpy_dtoh(h_b, d_b, part_bytes)
        var s: Float64 = 0.0
        var q: Float64 = 0.0
        for i in range(groups):
            s += Float64(hp_a.unsafe_offset(i)[])
            q += Float64(hp_b.unsafe_offset(i)[])
        gpu_ref = s
        gpu_ref2 = q
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
    var mb = Float64(n * 4) / 1e6
    var rel_err = abs(cpu_ref - gpu_ref) / max(abs(gpu_ref), 1.0)

    var label = "L" + String(intensity)
    print()
    print(
        "=== ",
        label,
        " passes:",
        passes,
        "| floats:",
        n,
        "|",
        round(mb, 3),
        "MB ===",
    )
    print(
        t"CPU (simd32):  min {round(cpu_min, 4)}  avg {round(cpu_avg, 4)}  "
        t"max {round(cpu_max, 4)} ms/iter"
    )
    print(
        t"GPU (gs32):    min {round(gpu_min, 4)}  avg {round(gpu_avg, 4)}  "
        t"max {round(gpu_max, 4)} ms/iter"
    )
    print(
        t"  launch+sync: avg {round(ls_avg, 4)}  max {round(ls_max, 4)}  |  "
        t"D2H+reduce:   avg {round(rd_avg, 4)}  max {round(rd_max, 4)} ms"
    )
    print(
        t"avgs: {round(cpu_avg / gpu_avg, 2)}x  min-spread GPU"
        t" {round(gpu_max / gpu_min, 3)}x CPU {round(cpu_max / cpu_min, 3)}x "
        t" rel err {round(rel_err * 100, 8)}%"
    )
    print(
        "   cpu:",
        round(cpu_ref, 6),
        round(cpu_ref2, 6),
        "| gpu:",
        round(gpu_ref, 6),
        round(gpu_ref2, 6),
    )


def main() raises:
    var cli = argv()
    if len(cli) < 2:
        print("usage: mojo run roofline_real.mojo <matrix.f32> [passes]")
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

    comptime K = 4
    var groups = 4096
    var part_bytes = UInt64(groups * 4)

    var cp = alloc(Layout[Float32](count=n)).unsafe_leak()
    var fh1 = open(path, "r")
    var got = fh1.read(Span(unsafe_ptr=cp, length=n))
    fh1.close()

    var ctx = IntelGPUContext()
    var k_l1 = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./roofline_real.spv",
        "roofline_l1",
    )
    var k_l2 = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./roofline_real.spv",
        "roofline_l2",
    )
    var k_l3 = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./roofline_real.spv",
        "roofline_l3",
    )
    k_l1.set_group_size(256, 1, 1)
    k_l2.set_group_size(256, 1, 1)
    k_l3.set_group_size(256, 1, 1)

    var bytes = UInt64(nbytes)
    var h_in = ctx.allocate_host(bytes)
    var hp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_in)
    var fh2 = open(path, "r")
    var hgot = fh2.read(Span(unsafe_ptr=hp, length=n))
    fh2.close()
    if hgot != got:
        print("ERROR: host/GPU buffer byte mismatch", hgot, got)

    var d_in = ctx.allocate_device(bytes)
    var d_a = ctx.allocate_device(part_bytes)
    var d_b = ctx.allocate_device(part_bytes)
    var h_a = ctx.allocate_host(part_bytes)
    var h_b = ctx.allocate_host(part_bytes)

    var h2d0 = perf_counter_ns()
    ctx.memcpy_htod(d_in, h_in, bytes)
    var h2d1 = perf_counter_ns()
    print(
        "H2D one-time (excluded):", round(Float64(h2d1 - h2d0) / 1e6, 3), "ms"
    )

    run_sweep(
        k_l1,
        cp,
        hp,
        n,
        groups,
        passes,
        1,
        d_in,
        d_a,
        d_b,
        h_a,
        h_b,
        part_bytes,
        ctx,
    )
    run_sweep(
        k_l2,
        cp,
        hp,
        n,
        groups,
        passes,
        2,
        d_in,
        d_a,
        d_b,
        h_a,
        h_b,
        part_bytes,
        ctx,
    )
    run_sweep(
        k_l3,
        cp,
        hp,
        n,
        groups,
        passes,
        3,
        d_in,
        d_a,
        d_b,
        h_a,
        h_b,
        part_bytes,
        ctx,
    )
    print()

    ctx.free_device(d_in)
    ctx.free_device(d_a)
    ctx.free_device(d_b)
    ctx.free_host(h_in)
    ctx.free_host(h_a)
    ctx.free_host(h_b)
    cp.unsafe_free()
    ctx.close()
