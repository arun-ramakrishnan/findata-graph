"""
A² supplier-peer top-K kernel — Mojo scaling spike (proposal
``doc/improvements/archive/graph/mojo_a2_kernel_scaling.md``).

Reads the DENSE shared-count matrix (int64, row-major, n×n) from a bin,
and finds the global top-K pairs (i<j) by shared count. workers=1 is the
sequential baseline; workers>1 fans row-blocks across a std.runtime
TaskGroup with DISJOINT per-worker top-K buffers (no locks; each worker
owns a distinct range of i rows, so each i<j pair is visited once).

    a2_topk_batch <shared.bin> <n> <K> <workers>

Output: one ``SHARED I J`` line per top-K entry, sorted by
(-shared, i, j) — the exact tie-break of a2_kernel's stable sort.

Mojo 1.2: def-only, alloc(Layout[...]).unsafe_leak(), unsafe_offset(i)[].
"""

from std.sys import argv
from std.memory.alloc import alloc, Layout
from std.runtime._asyncrt import TaskGroup


def read_i64(
    path: String, count: Int
) raises -> Pointer[Int64, MutUntrackedOrigin]:
    var p = alloc(Layout[Int64](count=count)).unsafe_leak()
    var f = open(path, "r")
    var nbytes = f.read(
        Span[UInt8](unsafe_ptr=p.unsafe_bitcast[UInt8](), length=count * 8)
    )
    f.close()
    if nbytes != count * 8:
        raise Error("short read: " + path)
    return p


def key_before(
    av: Int64,
    ai: Int64,
    aj: Int64,
    bv: Int64,
    bi: Int64,
    bj: Int64,
) -> Bool:
    """True if (av,ai,aj) ranks before (bv,bi,bj): higher value, then lower i, then lower j.
    """
    if av != bv:
        return av > bv
    if ai != bi:
        return ai < bi
    return aj < bj


async def a2_worker(
    mat: Pointer[Int64, MutUntrackedOrigin],
    kval: Pointer[Int64, MutUntrackedOrigin],
    ki: Pointer[Int64, MutUntrackedOrigin],
    kj: Pointer[Int64, MutUntrackedOrigin],
    kcnt: Pointer[Int32, MutUntrackedOrigin],
    n: Int,
    K: Int,
    start: Int,
    end: Int,
    slot: Int,
) -> None:
    var vb = kval.unsafe_offset(slot * K)
    var ib = ki.unsafe_offset(slot * K)
    var jb = kj.unsafe_offset(slot * K)
    var cnt = 0
    for i in range(start, end):
        var row = mat.unsafe_offset(i * n)
        var ii = Int64(i)
        for j in range(i + 1, n):
            var val = row.unsafe_offset(j)[]
            if val > 0:
                var jj = Int64(j)
                var pos = 0
                var keep_going = True
                while pos < cnt and keep_going:
                    if key_before(
                        val,
                        ii,
                        jj,
                        vb.unsafe_offset(pos)[],
                        ib.unsafe_offset(pos)[],
                        jb.unsafe_offset(pos)[],
                    ):
                        keep_going = False
                    else:
                        pos += 1
                if pos < K:
                    if cnt < K:
                        cnt += 1
                    var t = cnt - 1
                    while t > pos:
                        vb.unsafe_offset(t)[] = vb.unsafe_offset(t - 1)[]
                        ib.unsafe_offset(t)[] = ib.unsafe_offset(t - 1)[]
                        jb.unsafe_offset(t)[] = jb.unsafe_offset(t - 1)[]
                        t -= 1
                    vb.unsafe_offset(pos)[] = val
                    ib.unsafe_offset(pos)[] = ii
                    jb.unsafe_offset(pos)[] = jj
    kcnt.unsafe_offset(slot)[] = Int32(cnt)


def main() raises:
    var args = argv()
    if len(args) < 5:
        raise Error("usage: a2_topk_batch <shared.bin> <n> <K> <workers>")
    var n = Int(args[2])
    var K = Int(args[3])
    var workers = Int(args[4])
    if workers < 1:
        workers = 1
    if workers > n:
        workers = n

    var mat = read_i64(String(args[1]), n * n)

    var kval = alloc(Layout[Int64](count=workers * K)).unsafe_leak()
    var ki = alloc(Layout[Int64](count=workers * K)).unsafe_leak()
    var kj = alloc(Layout[Int64](count=workers * K)).unsafe_leak()
    var kcnt = alloc(Layout[Int32](count=workers)).unsafe_leak()

    var chunk = (n + workers - 1) // workers
    var tg = TaskGroup()
    for w in range(workers):
        var start = w * chunk
        var end = start + chunk
        if end > n:
            end = n
        if start >= end:
            continue
        tg.create_task(a2_worker(mat, kval, ki, kj, kcnt, n, K, start, end, w))
    tg.wait()

    # merge per-worker top-K into global top-K
    var gv = alloc(Layout[Int64](count=K)).unsafe_leak()
    var gi = alloc(Layout[Int64](count=K)).unsafe_leak()
    var gj = alloc(Layout[Int64](count=K)).unsafe_leak()
    var gcnt = 0
    for w in range(workers):
        var cnt = Int(kcnt.unsafe_offset(w)[])
        var vb = kval.unsafe_offset(w * K)
        var ib = ki.unsafe_offset(w * K)
        var jb = kj.unsafe_offset(w * K)
        for t in range(cnt):
            var val = vb.unsafe_offset(t)[]
            var ii = ib.unsafe_offset(t)[]
            var jj = jb.unsafe_offset(t)[]
            var pos = 0
            var keep_going = True
            while pos < gcnt and keep_going:
                if key_before(
                    val,
                    ii,
                    jj,
                    gv.unsafe_offset(pos)[],
                    gi.unsafe_offset(pos)[],
                    gj.unsafe_offset(pos)[],
                ):
                    keep_going = False
                else:
                    pos += 1
            if pos < K:
                if gcnt < K:
                    gcnt += 1
                var s = gcnt - 1
                while s > pos:
                    gv.unsafe_offset(s)[] = gv.unsafe_offset(s - 1)[]
                    gi.unsafe_offset(s)[] = gi.unsafe_offset(s - 1)[]
                    gj.unsafe_offset(s)[] = gj.unsafe_offset(s - 1)[]
                    s -= 1
                gv.unsafe_offset(pos)[] = val
                gi.unsafe_offset(pos)[] = ii
                gj.unsafe_offset(pos)[] = jj

    for t in range(gcnt):
        print(
            String(gv.unsafe_offset(t)[])
            + " "
            + String(gi.unsafe_offset(t)[])
            + " "
            + String(gj.unsafe_offset(t)[])
        )
