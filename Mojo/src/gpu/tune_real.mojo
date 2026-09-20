# Experiment B: work-group size / vectorization tuning (Gen9 OpenCL guide).
# SWEEPS the resident reduce over 4 kernels (float4 baseline, float8, float16,
# float4+uint+unroll playbook) x 3 work-group sizes (64/128/256, clinfo max)
# on the REAL 16560x384 matrix. Per-iteration timing (min/avg/max) per config,
# one shared H2D. The question: does any config beat the D-baseline (float4,
# group 256, 21.4 GB/s resident / ~1.72 ms at 25 MB)? Gen9: 24 EUs / 3
# subslices of 8; guide prefers group sizes that tile the EU count.
#
# Run from THIS directory (mojo_intel_gpu.mojoc + tune_real.spv adjacent):
#   mojo run tune_real.mojo <path/to/embed_matrix.f32> [passes]

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


def run_config(
    mut kernel: Kernel,
    cp: Pointer[Float32, MutUntrackedOrigin],
    n: Int,
    grp: Int,
    passes: Int,
    d_in: Int,
    d_sum: Int,
    d_sq: Int,
    h_sum: Int,
    h_sq: Int,
    part_bytes: UInt64,
    mut ctx: IntelGPUContext,
    label: String,
) raises:
    var hp_sum = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_sum)
    var hp_sq = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_sq)
    var groups = 4096

    kernel.set_arg_pointer(0, d_in)
    kernel.set_arg_pointer(1, d_sum)
    kernel.set_arg_pointer(2, d_sq)
    kernel.set_arg_value(3, 4, UInt64(n))

    kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
    ctx.synchronize()

    var gpu_times = List[Float64]()
    var ls_times = List[Float64]()
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
            s += Float64(hp_sum.unsafe_offset(i)[])
            q += Float64(hp_sq.unsafe_offset(i)[])
        gpu_sum = s
        gpu_sq = q
        var t1 = perf_counter_ns()
        gpu_times.append(Float64(t1 - t0) / 1e6)
        ls_times.append(Float64(t_ls - t0) / 1e6)
    var gpu_min = 1e18
    var gpu_max = 0.0
    var gpu_total = 0.0
    for t in gpu_times:
        gpu_total += t
        if t < gpu_min:
            gpu_min = t
        if t > gpu_max:
            gpu_max = t
    var gpu_avg = gpu_total / Float64(passes)
    var ls_avg = 0.0
    for t in ls_times:
        ls_avg += t
    ls_avg /= Float64(passes)
    var err = abs(Float64(gpu_sum) + 4049.255781) / 4049.255781
    if err > 1e-12:
        print(
            t"{label}group {grp}:  min {round(gpu_min, 4)}  "
            t"avg {round(gpu_avg, 4)}  max {round(gpu_max, 4)}  "
            t"ls {round(ls_avg, 4)}  ERR {round(err * 100, 8)}%"
        )
    else:
        print(
            t"{label}group {grp}:  min {round(gpu_min, 4)}  "
            t"avg {round(gpu_avg, 4)}  max {round(gpu_max, 4)}  "
            t"ls {round(ls_avg, 4)}"
        )


def main() raises:
    var cli = argv()
    if len(cli) < 2:
        print("usage: mojo run tune_real.mojo <matrix.f32> [passes]")
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
    if n % 16 != 0:
        print("ERROR: f16 kernel needs n % 16 == 0")
        return

    var cp = alloc(Layout[Float32](count=n)).unsafe_leak()
    var fh1 = open(path, "r")
    var got = fh1.read(Span(unsafe_ptr=cp, length=n))
    fh1.close()

    var ctx = IntelGPUContext()

    var k_f4 = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./tune_real.spv",
        "tune_f4",
    )
    var k_f8 = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./tune_real.spv",
        "tune_f8",
    )
    var k_f16 = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./tune_real.spv",
        "tune_f16",
    )
    var k_f4u = Kernel(
        ctx.library(),
        ctx.context(),
        ctx.device(),
        ctx.command_list(),
        "./tune_real.spv",
        "tune_f4_unroll",
    )
    k_f4.set_group_size(256, 1, 1)
    k_f8.set_group_size(256, 1, 1)
    k_f16.set_group_size(256, 1, 1)
    k_f4u.set_group_size(256, 1, 1)

    var bytes = UInt64(nbytes)
    var h_in = ctx.allocate_host(bytes)
    var hp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_in)
    var fh2 = open(path, "r")
    var hgot = fh2.read(Span(unsafe_ptr=hp, length=n))
    fh2.close()
    if hgot != got:
        print("ERROR: host/GPU buffer byte mismatch")

    var d_in = ctx.allocate_device(bytes)
    var groups = 4096
    var part_bytes = UInt64(groups * 4)
    var d_sum = ctx.allocate_device(part_bytes)
    var d_sq = ctx.allocate_device(part_bytes)
    var h_sum = ctx.allocate_host(part_bytes)
    var h_sq = ctx.allocate_host(part_bytes)

    var h2d0 = perf_counter_ns()
    ctx.memcpy_htod(d_in, h_in, bytes)
    var h2d1 = perf_counter_ns()
    print(
        "H2D (excluded):",
        round(Float64(h2d1 - h2d0) / 1e6, 3),
        "ms | passes:",
        passes,
    )
    print()
    print(
        "kernel                         group    min     avg     max "
        " launch/sync"
    )
    print(
        "----------------------------------------------------------------------"
    )

    var sizes = [64, 128, 256]

    for grp in sizes:
        k_f4.set_group_size(UInt32(grp), 1, 1)
        run_config(
            k_f4,
            cp,
            n,
            grp,
            passes,
            d_in,
            d_sum,
            d_sq,
            h_sum,
            h_sq,
            part_bytes,
            ctx,
            "f4        ",
        )
        k_f8.set_group_size(UInt32(grp), 1, 1)
        run_config(
            k_f8,
            cp,
            n,
            grp,
            passes,
            d_in,
            d_sum,
            d_sq,
            h_sum,
            h_sq,
            part_bytes,
            ctx,
            "f8        ",
        )
        k_f16.set_group_size(UInt32(grp), 1, 1)
        run_config(
            k_f16,
            cp,
            n,
            grp,
            passes,
            d_in,
            d_sum,
            d_sq,
            h_sum,
            h_sq,
            part_bytes,
            ctx,
            "f16       ",
        )
        k_f4u.set_group_size(UInt32(grp), 1, 1)
        run_config(
            k_f4u,
            cp,
            n,
            grp,
            passes,
            d_in,
            d_sum,
            d_sq,
            h_sum,
            h_sq,
            part_bytes,
            ctx,
            "f4+unroll ",
        )

    ctx.free_device(d_in)
    ctx.free_device(d_sum)
    ctx.free_device(d_sq)
    ctx.free_host(h_in)
    ctx.free_host(h_sum)
    ctx.free_host(h_sq)
    cp.unsafe_free()
    ctx.close()
