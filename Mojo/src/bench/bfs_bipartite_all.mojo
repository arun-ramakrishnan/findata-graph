"""
 Single-source-to-all bipartite BFS distances — Wall 2 promotion kernel.

 Reads the bipartite CSR bins (build_bipartite_csr) and computes, for
 ONE source, the trail hops to every reachable entity (bipartite dist /
 2, even steps only), matching _trail_bfs.

   bfs_bipartite_all <offsets.bin> <neighbors.bin> <N> <M> \
       <src> <max_steps>

 Prints one line per reachable entity: "ENTITY_INDEX HOPS" (even hops
 only; self is excluded). exit 0; 1 = error. No bridge; the parity
 driver shells this binary directly.
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


def main() raises:
    var args = argv()
    if len(args) < 7:
        print(
            "usage: bfs_bipartite_all <offsets.bin> <neighbors.bin> "
            "<N> <M> <src> <max_steps>"
        )
        sys_exit(1)
    var off_path = String(args[1])
    var nbr_path = String(args[2])
    var n = Int(String(args[3]))
    var m = Int(String(args[4]))
    var src = Int(String(args[5]))
    var max_steps = Int(String(args[6]))
    if not (0 <= src < n):
        raise Error("src out of range")
    var offsets = read_i32(off_path, n + 1)
    var neighbors = read_i32(nbr_path, m)

    # dist: -1 unvisited, else bipartite steps from src
    var dist = alloc(Layout[Int32](count=n)).unsafe_leak()
    for i in range(n):
        dist[i] = -1
    dist[src] = 0
    var cur = alloc(Layout[Int32](count=n)).unsafe_leak()
    var nxt = alloc(Layout[Int32](count=n)).unsafe_leak()
    var cur_len = 1
    cur[0] = Int32(src)
    var hops = 0
    while cur_len > 0 and hops < max_steps:
        var nxt_len = 0
        for i in range(cur_len):
            var u = Int(cur[i])
            var s = Int(offsets[u])
            var e = Int(offsets[u + 1])
            for j in range(s, e):
                var v = Int(neighbors[j])
                if dist[v] == -1:
                    dist[v] = Int32(hops + 1)
                    nxt[nxt_len] = Int32(v)
                    nxt_len += 1
        var tmp = cur
        cur = nxt
        nxt = tmp
        cur_len = nxt_len
        hops += 1

    # Entities occupy [0, n_entities); find n_entities from first hub
    # gap is not encoded, so require offsets length: n_entities = n - hubs.
    # Instead, print all reachable even-distance nodes — the driver knows
    # n_entities and filters entity ids only.
    for v in range(n):
        var d = Int(dist[v])
        if d > 0 and d % 2 == 0 and d <= max_steps:
            print(String(v) + " " + String(d // 2))
    sys_exit(0)
