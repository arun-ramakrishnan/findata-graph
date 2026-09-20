# Experiment C — SVM / zero-copy shared memory vs device buffers, REAL matrix.
# zeMemAllocShared buffer: file is read DIRECTLY into it (no host staging, no
# H2D memcpy), the kernel reads it like device memory, and partials are read
# back by the host directly (no D2H). Compared in the SAME session against the
# B-style path: read host buffer + memcpy_htod into device memory, per-pass
# D2H of partials. Kernel = tune_f4 @ group 64 (B's optimum), 4096 groups.
#
# Run: mojo run svm_real.mojo <matrix.f32> [passes]

from std.sys import argv
from std.os import SEEK_END
from std.time import perf_counter_ns
from mojo_intel_gpu import IntelGPUContext, Kernel, ZeGroupCount


def main() raises:
    var cli = argv()
    if len(cli) < 2:
        print("usage: mojo run svm_real.mojo <matrix.f32> [passes]")
        return
    var path = cli[1]
    var passes: Int = 100
    if len(cli) >= 3:
        passes = atol(cli[2])

    var fh = open(path, "r")
    var nbytes = Int(UInt64(fh.seek(0, SEEK_END)))
    var n = nbytes // 4
    fh.close()
    if n % 4 != 0:
        print("ERROR: needs n % 4 == 0")
        return
    var groups = 4096
    var part_bytes = UInt64(groups * 4)
    var bytes = UInt64(nbytes)
    print("matrix:", path, "floats:", n, "(", Float64(nbytes) / 1e6, "MB )")

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

    # ---------- upload phase: zero-copy shared vs staged device ----------
    var t0 = perf_counter_ns()
    var s_in = ctx.allocate_shared(bytes)  # host+device, one pointer
    var sp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=s_in)
    var fh1 = open(path, "r")
    _ = fh1.read(Span(unsafe_ptr=sp, length=n))  # DIRECT into shared
    fh1.close()
    var t1 = perf_counter_ns()
    var svm_upload_ms = Float64(t1 - t0) / 1e6

    var t2 = perf_counter_ns()
    var h_in = ctx.allocate_host(bytes)
    var hp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_in)
    var fh2 = open(path, "r")
    _ = fh2.read(Span(unsafe_ptr=hp, length=n))
    fh2.close()
    var d_in = ctx.allocate_device(bytes)
    ctx.memcpy_htod(d_in, h_in, bytes)
    var t3 = perf_counter_ns()
    var staged_upload_ms = Float64(t3 - t2) / 1e6
    print(
        t"upload: shared-direct {round(svm_upload_ms, 3)} ms | "
        t"staged(host+H2D) {round(staged_upload_ms, 3)} ms"
    )

    var s_sum = ctx.allocate_shared(part_bytes)
    var s_sq = ctx.allocate_shared(part_bytes)
    var ssp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=s_sum)
    var d_sum = ctx.allocate_device(part_bytes)
    var d_sq = ctx.allocate_device(part_bytes)
    var h_sum = ctx.allocate_host(part_bytes)
    var hsp = Pointer[Float32, MutUntrackedOrigin](unsafe_from_address=h_sum)

    # ---------- kernel phase: same kernel over both memory types ----------
    var nfloat = UInt64(n)  # kernel arg n = FLOAT count (B convention)

    # warmup both
    kernel.set_arg_pointer(0, s_in)
    kernel.set_arg_pointer(1, s_sum)
    kernel.set_arg_pointer(2, s_sq)
    kernel.set_arg_value(3, 4, nfloat)
    kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
    ctx.synchronize()
    kernel.set_arg_pointer(0, d_in)
    kernel.set_arg_pointer(1, d_sum)
    kernel.set_arg_pointer(2, d_sq)
    kernel.set_arg_value(3, 4, nfloat)
    kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
    ctx.synchronize()

    var svm_times = List[Float64]()
    var dev_times = List[Float64]()
    var s_sum_val: Float64 = 0.0
    var d_sum_val: Float64 = 0.0
    for _ in range(passes):
        # device leg (B-style: staged D2H readback)
        kernel.set_arg_pointer(0, d_in)
        kernel.set_arg_pointer(1, d_sum)
        kernel.set_arg_pointer(2, d_sq)
        kernel.set_arg_value(3, 4, nfloat)
        var ta = perf_counter_ns()
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
        ctx.synchronize()
        ctx.memcpy_dtoh(h_sum, d_sum, part_bytes)
        var s1: Float64 = 0.0
        for i in range(groups):
            s1 += Float64(hsp.unsafe_offset(i)[])
        var tb = perf_counter_ns()
        d_sum_val = s1
        dev_times.append(Float64(tb - ta) / 1e6)
        # shared leg (zero-copy: direct host read, NO D2H)
        kernel.set_arg_pointer(0, s_in)
        kernel.set_arg_pointer(1, s_sum)
        kernel.set_arg_pointer(2, s_sq)
        kernel.set_arg_value(3, 4, nfloat)
        var tc = perf_counter_ns()
        kernel.launch(ZeGroupCount(UInt32(groups), 1, 1))
        ctx.synchronize()
        var s2: Float64 = 0.0
        for i in range(groups):
            s2 += Float64(ssp.unsafe_offset(i)[])
        var td = perf_counter_ns()
        s_sum_val = s2
        svm_times.append(Float64(td - tc) / 1e6)

    def stats(ts: List[Float64]) -> Tuple[Float64, Float64, Float64]:
        var mn = 1e18
        var mx = 0.0
        var tot = 0.0
        for t in ts:
            tot += t
            if t < mn:
                mn = t
            if t > mx:
                mx = t
        return (mn, tot / Float64(len(ts)), mx)

    var sstats = stats(svm_times)
    var dstats = stats(dev_times)
    var smin = sstats[0]
    var savg = sstats[1]
    var smax = sstats[2]
    var dmin = dstats[0]
    var davg = dstats[1]
    var dmax = dstats[2]
    print()
    print(
        t"SVM/shared:  min {round(smin, 4)}  avg {round(savg, 4)}  max"
        t" {round(smax, 4)} ms/pass (no D2H)"
    )
    print(
        t"device+D2H:  min {round(dmin, 4)}  avg {round(davg, 4)}  max"
        t" {round(dmax, 4)} ms/pass (B-style)"
    )
    print(t"kernel-wall delta shared vs device: {round(savg - davg, 4)} ms")
    print(
        t"upload saved per session:"
        t" {round(staged_upload_ms - svm_upload_ms, 3)} ms"
        t" ({round(staged_upload_ms / max(svm_upload_ms, 0.001), 2)}x)"
    )
    print(
        "sums: shared",
        round(s_sum_val, 6),
        "device",
        round(d_sum_val, 6),
        "(full = -4049.255781 )",
    )

    ctx.free_shared(s_in)
    ctx.free_shared(s_sum)
    ctx.free_shared(s_sq)
    ctx.free_device(d_in)
    ctx.free_device(d_sum)
    ctx.free_device(d_sq)
    ctx.free_host(h_in)
    ctx.free_host(h_sum)
    ctx.close()
