"""
Batched single-source-to-all bipartite BFS with TaskGroup fan-out.

Wall 2 S3 Mojo batching experiment (proposal
``doc/improvements/archive/graph/mojo_trail_bfs_batch_taskgroup.md``).
Reads the S2 bipartite CSR bins ONCE, answers ALL sources in ONE process,
and emits one COMPACT line per source (no per-node text parse):

    SRC REACH H0 H1 ... Hn_hops-1

where Hk = entity count at bipartite distance 2k (k trail hops). Sources
are printed in ascending id order. Semantics match bfs_bipartite_all.mojo:
even bipartite distance only, self excluded, max_steps capped.

  bfs_bipartite_all_batch <offsets.bin> <neighbors.bin> <N> <M> \
      <n_entities> <max_steps> <workers> <src1> <src2> ...

workers=1 gives the sequential batch (S1 baseline); workers>1 fans sources
across DISJOINT per-worker frontier buffers via std.runtime TaskGroup
(S2). CSR read state is shared; per-worker dist/cur/nxt buffers are
disjoint, so no locks are needed. TaskGroup contract: non-raising async
def, no futures (results in pointer slots), wait() is the barrier.

Mojo 1.2: def-only (fn removed); positional __getitem__ is deprecated, so
pointer access uses unsafe_offset(i)[].
"""

from std.sys import argv
from std.memory.alloc import alloc, Layout
from std.runtime._asyncrt import TaskGroup


def read_i32(
    path: String, count: Int
) raises -> Pointer[Int32, MutUntrackedOrigin]:
    var p = alloc(Layout[Int32](count=count)).unsafe_leak()
    var f = open(path, "r")
    var nbytes = f.read(
        Span[UInt8](unsafe_ptr=p.unsafe_bitcast[UInt8](), length=count * 4)
    )
    f.close()
    if nbytes != count * 4:
        raise Error("short read: " + path)
    return p


async def bfs_worker(
    offsets: Pointer[Int32, MutUntrackedOrigin],
    neighbors: Pointer[Int32, MutUntrackedOrigin],
    srcs: Pointer[Int32, MutUntrackedOrigin],
    dist_base: Pointer[Int32, MutUntrackedOrigin],
    cur_base: Pointer[Int32, MutUntrackedOrigin],
    nxt_base: Pointer[Int32, MutUntrackedOrigin],
    reach_out: Pointer[Int32, MutUntrackedOrigin],
    hist_out: Pointer[Int32, MutUntrackedOrigin],
    n: Int,
    n_ent: Int,
    max_steps: Int,
    n_hops: Int,
    start: Int,
    end: Int,
    slot: Int,
) -> None:
    var dist = dist_base.unsafe_offset(slot * n)
    var cur = cur_base.unsafe_offset(slot * n)
    var nxt = nxt_base.unsafe_offset(slot * n)
    for si in range(start, end):
        var src = Int(srcs.unsafe_offset(si)[])
        for i in range(n):
            dist.unsafe_offset(i)[] = -1
        dist.unsafe_offset(src)[] = 0
        var cur_len = 1
        cur.unsafe_offset(0)[] = Int32(src)
        var hops = 0
        while cur_len > 0 and hops < max_steps:
            var nxt_len = 0
            for i in range(cur_len):
                var u = Int(cur.unsafe_offset(i)[])
                var s = Int(offsets.unsafe_offset(u)[])
                var e = Int(offsets.unsafe_offset(u + 1)[])
                for j in range(s, e):
                    var v = Int(neighbors.unsafe_offset(j)[])
                    if dist.unsafe_offset(v)[] == -1:
                        dist.unsafe_offset(v)[] = Int32(hops + 1)
                        nxt.unsafe_offset(nxt_len)[] = Int32(v)
                        nxt_len += 1
            var tmp = cur
            cur = nxt
            nxt = tmp
            cur_len = nxt_len
            hops += 1
        var hbase = si * n_hops
        for h in range(n_hops):
            hist_out.unsafe_offset(hbase + h)[] = 0
        var reach = 0
        for v in range(n_ent):
            var d = Int(dist.unsafe_offset(v)[])
            if d > 0 and d % 2 == 0 and d <= max_steps:
                reach += 1
                hist_out.unsafe_offset(hbase + d // 2)[] += 1
        reach_out.unsafe_offset(si)[] = Int32(reach)


def main() raises:
    var args = argv()
    if len(args) < 9:
        raise Error(
            "usage: bfs_bipartite_all_batch <off> <nbr> <N> <M> "
            "<n_ent> <max_steps> <workers> <src> ..."
        )
    var offsets = read_i32(String(args[1]), Int(args[3]) + 1)
    var neighbors = read_i32(String(args[2]), Int(args[4]))
    var n = Int(args[3])
    var n_ent = Int(args[5])
    var max_steps = Int(args[6])
    var workers = Int(args[7])
    var nsrc = len(args) - 8
    if workers < 1:
        workers = 1
    if workers > nsrc:
        workers = nsrc
    var n_hops = max_steps // 2 + 1

    var srcs = alloc(Layout[Int32](count=nsrc)).unsafe_leak()
    for si in range(nsrc):
        srcs.unsafe_offset(si)[] = Int32(Int(String(args[8 + si])))
    # insertion sort ascending (nsrc is small; avoids a sort API dependency)
    for i in range(1, nsrc):
        var key = srcs.unsafe_offset(i)[]
        var j = i - 1
        while j >= 0 and srcs.unsafe_offset(j)[] > key:
            srcs.unsafe_offset(j + 1)[] = srcs.unsafe_offset(j)[]
            j -= 1
        srcs.unsafe_offset(j + 1)[] = key

    var dist_base = alloc(Layout[Int32](count=workers * n)).unsafe_leak()
    var cur_base = alloc(Layout[Int32](count=workers * n)).unsafe_leak()
    var nxt_base = alloc(Layout[Int32](count=workers * n)).unsafe_leak()
    var reach_out = alloc(Layout[Int32](count=nsrc)).unsafe_leak()
    var hist_out = alloc(Layout[Int32](count=nsrc * n_hops)).unsafe_leak()

    var chunk = (nsrc + workers - 1) // workers
    var tg = TaskGroup()
    for w in range(workers):
        var start = w * chunk
        var end = start + chunk
        if end > nsrc:
            end = nsrc
        if start >= end:
            continue
        tg.create_task(
            bfs_worker(
                offsets,
                neighbors,
                srcs,
                dist_base,
                cur_base,
                nxt_base,
                reach_out,
                hist_out,
                n,
                n_ent,
                max_steps,
                n_hops,
                start,
                end,
                w,
            )
        )
    tg.wait()

    for si in range(nsrc):
        var line = (
            String(srcs.unsafe_offset(si)[])
            + " "
            + String(reach_out.unsafe_offset(si)[])
        )
        var hbase = si * n_hops
        for h in range(n_hops):
            line += " " + String(hist_out.unsafe_offset(hbase + h)[])
        print(line)
