"""
 BFS over the CSR substrate — vault_scaling Phase B-B (bfs_csr.mojo).

 Files in, paths out — no bridge (the tests parity driver shells this
 binary directly, so process startup is measurable too). Reads the CSR
 binaries built by helpers/graph/csr.py (int32 LE, both directions,
 sorted slices) and answers ONE s-t query per invocation:

   bfs_csr <offsets.bin> <neighbors.bin> <N> <M> <src> <dst> <max_hops>

 Prints the path as space-separated node ids (+ a final "hops H" line);
 exit 0 = path, 2 = unreachable within max_hops, 1 = error.

 Determinism: neighbors are visited in ascending id order (sorted-slice
 layout) — identical tie-breaks to the Python oracle (csr.bfs_path) and
 the SQL layer-order semantics. Direction optimization (Beamer, from
 day one per vault_scaling B-B): frontier density > N/20 switches the
 level to BOTTOM-UP (each unvisited node scans its own row for the
 smallest visited claimant — the recorded MIN-parent rule); otherwise
 TOP-DOWN expansion. Toolchain note (Mojo 1.1): def-only, typed
 FileHandle.read (read_bytes clobbers the first 8 bytes — cosine.mojo
 load_f32 note).
"""

from std.sys import argv
from std.memory import Layout

from bridge import sys_exit  # -I Mojo/src/bench required


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


def print_path(
    parent: Pointer[Int32, MutUntrackedOrigin], src: Int, dst: Int
) raises:
    # walk parent links dst -> src, reverse, print ids + hops
    var length = 1
    var cur = dst
    while cur != src:
        cur = Int(parent[cur])
        length += 1
    var path = alloc(Layout[Int32](count=length)).unsafe_leak()
    cur = dst
    var i = length - 1
    while i >= 0:
        path[i] = Int32(cur)
        if cur == src:
            break
        cur = Int(parent[cur])
        i -= 1
    var out = String("")
    for k in range(length):
        if k > 0:
            out += " "
        out += String(path[k])
    print(out)
    print("hops " + String(length - 1))
    sys_exit(0)


def main() raises:
    var args = argv()
    if len(args) < 8:
        print(
            "usage: bfs_csr <offsets.bin> <neighbors.bin> <N> <M> <src> <dst>"
            " <max_hops>"
        )
        raise Error("expected 7 arguments")

    var off_path = String(args[1])
    var nbr_path = String(args[2])
    var n = Int(String(args[3]))
    var m = Int(String(args[4]))
    var src = Int(String(args[5]))
    var dst = Int(String(args[6]))
    var max_hops = Int(String(args[7]))
    if not (0 <= src < n and 0 <= dst < n):
        raise Error("src/dst out of range")

    var offsets = read_i32(off_path, n + 1)
    var neighbors = read_i32(nbr_path, m)

    var parent = alloc(Layout[Int32](count=n)).unsafe_leak()
    var in_frontier = alloc(Layout[UInt8](count=n)).unsafe_leak()
    var cur = alloc(Layout[Int32](count=n)).unsafe_leak()
    var nxt = alloc(Layout[Int32](count=n)).unsafe_leak()
    for i in range(n):
        parent[i] = -1
        in_frontier[i] = 0
        cur[i] = -1
        nxt[i] = -1
    var cur_len = 1
    cur[0] = Int32(src)
    parent[src] = Int32(src)
    in_frontier[src] = 1
    if src == dst:
        print_path(parent, src, dst)
        return
    var hops = 0

    var claim_parent = alloc(Layout[Int32](count=n)).unsafe_leak()
    while cur_len > 0 and hops < max_hops:
        # Beamer switch: bottom-up when the frontier is dense. BOTH modes
        # are two-phase MIN-claimant (phase 1 discovers, phase 2 assigns)
        # so the parent tree — and hence the path — is identical across
        # modes and identical to the Python oracle (vault_scaling B-B:
        # parents[x] = MIN over same-level claimants; first-claim order
        # was retracted).
        var bottom_up = cur_len * 20 > n
        for i in range(n):
            claim_parent[i] = -1
        if not bottom_up:
            # CLAIM (top-down): scan frontier rows
            for i in range(cur_len):
                var u = Int(cur[i])
                var s = Int(offsets[u])
                var e = Int(offsets[u + 1])
                for j in range(s, e):
                    var v = Int(neighbors[j])
                    if parent[v] == -1:
                        var cp = Int(claim_parent[v])
                        if cp == -1 or u < cp:
                            claim_parent[v] = Int32(u)
        else:
            # CLAIM (bottom-up): scan unvisited rows; the first ascending
            # visited hit IS the minimum claimant
            for v in range(n):
                if parent[v] != -1:
                    continue
                var s = Int(offsets[v])
                var e = Int(offsets[v + 1])
                for j in range(s, e):
                    var w = Int(neighbors[j])
                    if in_frontier[w] == 1:
                        claim_parent[v] = Int32(w)
                        break
        # ASSIGN: parents, next frontier, dst check
        var nxt_len = 0
        var dst_claimed = False
        for v in range(n):
            var u = Int(claim_parent[v])
            if u != -1:
                parent[v] = Int32(u)
                nxt[nxt_len] = Int32(v)
                nxt_len += 1
                if v == dst:
                    dst_claimed = True
        if dst_claimed:
            print_path(parent, src, dst)
            return
        if nxt_len == 0:
            print("UNREACHABLE")
            sys_exit(2)
        for i in range(cur_len):
            in_frontier[cur[i]] = 0
        for i in range(nxt_len):
            in_frontier[nxt[i]] = 1
        var tmp = cur
        cur = nxt
        nxt = tmp
        cur_len = nxt_len
        hops += 1

    print("UNREACHABLE")
    sys_exit(2)
