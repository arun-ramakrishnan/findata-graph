"""
Hyperedge motif configuration-null — Mojo scaling spike (proposal
``doc/improvements/archive/graph/mojo_hyper_motifs_null_scaling.md``).

Reads PRE-DEALT replicate POSTINGS (node -> [edge ids]) from a bin — the
exact structure Python's ``pair_shared_counts`` builds — and runs the full
census on each replicate, then computes mean/std of pairs_ge_k.

The permutation/deal step is NOT done here: Python ships the exact
postings (S1 parity contract), so both legs run identical data. The only
difference is the census compute engine.

    motifs_null_batch <postings.bin> <R> <workers>

Bin layout (all int32):
  header: R_MAX, N_EDGES, n_orders, orders...
  per rep: n_groups, then per group: node, n_edges, edge_ids...
Only the first R reps are processed.

Output (one line per order k, in header order):
  K MEAN STD      (population std, ddof=0, matches numpy.std)

Mojo 1.2: def-only, alloc(Layout[...]).unsafe_leak(), unsafe_offset(i)[].
TaskGroup: non-raising async def, pointer slots, wait() is the barrier.
"""

from std.sys import argv
from std.memory.alloc import alloc, Layout
from std.runtime._asyncrt import TaskGroup


def read_all_i32(path: String) raises -> Pointer[Int32, MutUntrackedOrigin]:
    var f = open(path, "r")
    var size = f.seek(0, 2)  # SEEK_END -> UInt64
    var count = Int(size // 4)
    f.seek(0, 0)
    var p = alloc(Layout[Int32](count=count)).unsafe_leak()
    var nbytes = f.read(
        Span[UInt8](unsafe_ptr=p.unsafe_bitcast[UInt8](), length=count * 4)
    )
    f.close()
    if nbytes != count * 4:
        raise Error("short read: " + path)
    return p


struct RepIdx:
    var off: Int
    var groups: Int


def parse_reps(
    data: Pointer[Int32, MutUntrackedOrigin],
    R: Int,
    idx: Pointer[RepIdx, MutUntrackedOrigin],
) -> None:
    var pos = 3 + Int(data.unsafe_offset(2)[])  # header length
    for r in range(R):
        idx.unsafe_offset(r)[].off = pos
        var ng = Int(data.unsafe_offset(pos)[])
        idx.unsafe_offset(r)[].groups = ng
        pos += 1
        for g in range(ng):
            var ne = Int(data.unsafe_offset(pos + 1)[])
            pos += 2 + ne


def count_pairs_ge(
    data: Pointer[Int32, MutUntrackedOrigin],
    r_off: Int,
    n_groups: Int,
    pairbuf: Pointer[Int32, MutUntrackedOrigin],
    max_edges: Int,
    orders: Pointer[Int32, MutUntrackedOrigin],
    n_orders: Int,
    outp: Pointer[Int32, MutUntrackedOrigin],
    oslot: Int,
) -> None:
    var p = r_off + 1
    for g in range(n_groups):
        var ne = Int(data.unsafe_offset(p + 1)[])
        p += 2
        var base = p
        var a = 0
        while a < ne:
            var ea = Int(data.unsafe_offset(base + a)[])
            var b = a + 1
            while b < ne:
                var eb = Int(data.unsafe_offset(base + b)[])
                var lo = ea
                var hi = eb
                if lo > hi:
                    lo = eb
                    hi = ea
                pairbuf.unsafe_offset(lo * max_edges + hi)[] += 1
                b += 1
            a += 1
        p = base + ne
    for k in range(n_orders):
        outp.unsafe_offset(oslot * n_orders + k)[] = 0
    for i in range(max_edges):
        for j in range(i + 1, max_edges):
            var c = pairbuf.unsafe_offset(i * max_edges + j)[]
            if c > 0:
                for k in range(n_orders):
                    if c >= orders.unsafe_offset(k)[]:
                        outp.unsafe_offset(oslot * n_orders + k)[] += 1
    for i in range(max_edges * max_edges):
        pairbuf.unsafe_offset(i)[] = 0


async def null_worker(
    data: Pointer[Int32, MutUntrackedOrigin],
    idx: Pointer[RepIdx, MutUntrackedOrigin],
    pairbuf_base: Pointer[Int32, MutUntrackedOrigin],
    max_edges: Int,
    orders: Pointer[Int32, MutUntrackedOrigin],
    n_orders: Int,
    outp: Pointer[Int32, MutUntrackedOrigin],
    start: Int,
    end: Int,
    slot: Int,
) -> None:
    var pairbuf = pairbuf_base.unsafe_offset(slot * max_edges * max_edges)
    for r in range(start, end):
        count_pairs_ge(
            data,
            idx.unsafe_offset(r)[].off,
            idx.unsafe_offset(r)[].groups,
            pairbuf,
            max_edges,
            orders,
            n_orders,
            outp,
            r,
        )


def main() raises:
    var args = argv()
    if len(args) < 4:
        raise Error("usage: motifs_null_batch <postings.bin> <R> <workers>")
    var data = read_all_i32(String(args[1]))
    var R = Int(args[2])
    var workers = Int(args[3])
    var R_MAX = Int(data.unsafe_offset(0)[])
    var max_edges = Int(data.unsafe_offset(1)[])
    var n_orders = Int(data.unsafe_offset(2)[])
    if R > R_MAX:
        R = R_MAX
    if R < 1:
        R = 1
    if workers < 1:
        workers = 1
    if workers > R:
        workers = R

    var orders = data.unsafe_offset(3)
    var idx = alloc(Layout[RepIdx](count=R)).unsafe_leak()
    parse_reps(data, R, idx)

    # per-replicate output: R × n_orders int32
    var outp = alloc(Layout[Int32](count=R * n_orders)).unsafe_leak()
    # per-worker pair scratch: max_edges² each
    var pairbuf = alloc(
        Layout[Int32](count=workers * max_edges * max_edges)
    ).unsafe_leak()

    var chunk = (R + workers - 1) // workers
    var tg = TaskGroup()
    for w in range(workers):
        var start = w * chunk
        var end = start + chunk
        if end > R:
            end = R
        if start >= end:
            continue
        tg.create_task(
            null_worker(
                data,
                idx,
                pairbuf,
                max_edges,
                orders,
                n_orders,
                outp,
                start,
                end,
                w,
            )
        )
    tg.wait()

    # reduce mean/std per order (population std, ddof=0)
    for k in range(n_orders):
        var sum = 0.0
        for r in range(R):
            sum += Float64(outp.unsafe_offset(r * n_orders + k)[])
        var mean = sum / Float64(R)
        var sq = 0.0
        for r in range(R):
            var d = Float64(outp.unsafe_offset(r * n_orders + k)[]) - mean
            sq += d * d
        var std = (sq / Float64(R)) ** 0.5
        print(
            String(orders.unsafe_offset(k)[])
            + " "
            + String(mean)
            + " "
            + String(std)
        )
