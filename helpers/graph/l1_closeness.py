#!/usr/bin/env python3
"""L1c lane — closeness/harmonic via 2-core folding + analytic forest term.

The live graph is 84% forest (18,604 of 22,067 nodes are degree-1 leaves
hanging off a 3,463-node 2-core). The BFS family (closeness/harmonic)
currently computes all-pairs over the full graph — 84% of that work is
redundant because a peeled node's distance to any source is analytically
determined by its attachment point.

Exact algorithm, not an approximation:

1. Iteratively peel degree-1 nodes → 2-core + forest parent/depth maps.
2. BFS from each source on the core only.
3. Forest contribution is a closed-form sum over precomputed depth
   histograms: for a core source u and a peeled node w at depth δ from
   forest root h, d(u, w) = d(u, h) + δ.
4. For peeled sources, intra-tree distances use BFS within the tree;
   inter-tree distances use d(w, x) = δ + d(h, root(x)) + depth(x).

Formula (matches scipy_bridge.fused_derive):
- closeness = (N-1) / sum(reachable distances); 0 for unreachable rows
- harmonic = sum(1/d) over finite positive distances (raw, stamp semantics)

Normalization: raw (no WF scaling) — the contract stamp convention.

Write surface: ``graph_analytics`` (the contract's home), UPSERT via
``algorithms.write_analytics``, opt-in ``--apply`` (D13); dry-run by
default. Metric names written: closeness_centrality, harmonic_centrality.

ROUTING: stays SCIPY (the lane is scipy-owned; this is a faster
implementation of the same lane, not a new metric).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict, deque
from functools import partial
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
DEFAULT_DB_PATH = _PROJECT_ROOT / "memory" / "research.db"

from helpers.core.forkmap import fork_map  # noqa: E402  # needs the shim above
from helpers.core.db import connect  # noqa: E402  # needs the shim above

CLOSENESS_METRIC = "closeness_centrality"
HARMONIC_METRIC = "harmonic_centrality"

#: centrality projection rule (mirrors l1_betweenness.INDEX_NOISE)
INDEX_NOISE = frozenset({"listed_on_index"})


# --------------------------------------------------------------------------- #
# graph loading + 2-core decomposition
# --------------------------------------------------------------------------- #
def load_projection(db_path: str | Path = DEFAULT_DB_PATH):
    """Sorted endpoint names + deduped undirected edge pairs (dense ids).

    Names come from KEPT edges only (the scipy_bridge discipline, mirroring
    ``load_centrality_projection``): an entity whose only edges are
    listed_on_index / self-loops must not join the universe — a phantom
    isolate does not perturb harmonic (no 1/d terms) but inflates the
    ``(n - 1)`` closeness normalization by its count (2026-10-15 audit:
    593 phantom isolates scaled every closeness score by 22066/21473).
    """
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        rows = con.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
    finally:
        con.close()
    kept = [(s, t) for s, t, et in rows if s != t and et not in INDEX_NOISE]
    names = sorted({s for s, _ in kept} | {t for _, t in kept})
    pos = {n: i for i, n in enumerate(names)}
    edges = sorted({tuple(sorted((pos[s], pos[t]))) for s, t in kept})
    return names, edges


def build_adjacency(n: int, edges: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    """CSR (indptr, indices), both directions per edge."""
    r = np.fromiter((a for a, _ in edges), dtype=np.int64, count=len(edges))
    c = np.fromiter((b for _, b in edges), dtype=np.int64, count=len(edges))
    both_r = np.concatenate([r, c])
    both_c = np.concatenate([c, r])
    order = np.argsort(both_r, kind="stable")
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(indptr, both_r + 1, 1)
    indptr = np.cumsum(indptr)
    return indptr, both_c[order]


def build_adj_sets(edges: list[tuple[int, int]]) -> dict[int, set[int]]:
    """Adjacency as dict of sets (for forest BFS)."""
    adj_sets: dict[int, set[int]] = defaultdict(set)
    for a, b in edges:
        adj_sets[a].add(b)
        adj_sets[b].add(a)
    return adj_sets


def peel_two_core(indptr: np.ndarray, indices: np.ndarray, n: int) -> tuple[np.ndarray, set[int]]:
    """Iterative degree<2 peel. Returns (core_mask, removed_set)."""
    deg = (indptr[1:] - indptr[:-1]).astype(np.int64).tolist()
    nbrs = [indices[indptr[i] : indptr[i + 1]].tolist() for i in range(n)]
    removed: set[int] = set()
    q = deque(i for i in range(n) if deg[i] < 2)
    while q:
        v = q.popleft()
        if v in removed:
            continue
        removed.add(v)
        for w in nbrs[v]:
            if w in removed:
                continue
            deg[w] -= 1
            if deg[w] < 2 and w not in removed:
                q.append(w)
    mask = np.ones(n, dtype=bool)
    for v in removed:
        mask[v] = False
    return mask, removed


# --------------------------------------------------------------------------- #
# forest structure (BFS from core)
# --------------------------------------------------------------------------- #
def _tree_histograms(
    root: dict[int, int], depth: dict[int, int], tree_nodes: dict[int, set[int]]
) -> tuple[dict[int, dict[int, int]], dict[int, int]]:
    """Per-root depth histograms + depth sums."""
    tree_depth_hist: dict[int, dict[int, int]] = {}
    tree_sum_depths: dict[int, int] = {}
    for r, nodes in tree_nodes.items():
        hist: dict[int, int] = defaultdict(int)
        sum_d = 0
        for v in nodes:
            hist[depth[v]] += 1
            sum_d += depth[v]
        tree_depth_hist[r] = dict(hist)
        tree_sum_depths[r] = sum_d
    return tree_depth_hist, tree_sum_depths


def _special_components(
    removed: set[int], adj_sets: dict[int, set[int]], root: dict[int, int]
) -> list[set[int]]:
    """Peeled components with no core attachment (never got a root)."""
    special_components: list[set[int]] = []
    seen: set[int] = set()
    for v in removed:
        if v in root or v in seen:
            continue
        comp = {v}
        q = deque([v])
        seen.add(v)
        while q:
            x = q.popleft()
            for w in adj_sets[x]:
                if w in removed and w not in seen and w not in root:
                    seen.add(w)
                    comp.add(w)
                    q.append(w)
        special_components.append(comp)
    return special_components


def build_forest(
    removed: set[int],
    adj_sets: dict[int, set[int]],
    core_set: set[int],
) -> dict:
    """Build forest structure: parent/depth/root maps + tree depth histograms.

    BFS from core nodes outward through peeled nodes gives the tree
    structure directly. Each peeled node's parent is its neighbor closer
    to the core; its root is the core node at the attachment point.
    """
    parent: dict[int, int] = {}
    depth: dict[int, int] = {}
    root: dict[int, int] = {}

    # BFS from core nodes
    q: deque[int] = deque()
    for c in core_set:
        for v in adj_sets[c]:
            if v in removed and v not in parent:
                parent[v] = c
                depth[v] = 1
                root[v] = c
                q.append(v)

    while q:
        u = q.popleft()
        for v in adj_sets[u]:
            if v in removed and v not in parent:
                parent[v] = u
                depth[v] = depth[u] + 1
                root[v] = root[u]
                q.append(v)

    # Group nodes by forest root
    tree_nodes: dict[int, set[int]] = defaultdict(set)
    for v in removed:
        if v in root:
            tree_nodes[root[v]].add(v)

    tree_depth_hist, tree_sum_depths = _tree_histograms(root, depth, tree_nodes)
    special_components = _special_components(removed, adj_sets, root)

    return {
        "parent": parent,
        "depth": depth,
        "root": root,
        "tree_nodes": dict(tree_nodes),
        "tree_depth_hist": tree_depth_hist,
        "tree_sum_depths": tree_sum_depths,
        "special_components": special_components,
    }


# --------------------------------------------------------------------------- #
# core BFS
# --------------------------------------------------------------------------- #
def build_core_adjacency(
    indptr: np.ndarray,
    indices: np.ndarray,
    is_core: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a core-only CSR adjacency (only core-core edges)."""
    n = len(indptr) - 1
    # Count core neighbors per core node
    core_count = np.zeros(n, dtype=np.int64)
    for u in range(n):
        if not is_core[u]:
            continue
        for v in indices[indptr[u] : indptr[u + 1]]:
            if is_core[v]:
                core_count[u] += 1
    core_indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(core_count, out=core_indptr[1:])
    # Fill core indices
    core_indices = np.zeros(core_indptr[-1], dtype=np.int64)
    offset = np.zeros(n, dtype=np.int64)
    for u in range(n):
        if not is_core[u]:
            continue
        for v in indices[indptr[u] : indptr[u + 1]]:
            if is_core[v]:
                core_indices[core_indptr[u] + offset[u]] = v
                offset[u] += 1
    return core_indptr, core_indices


def _core_bfs_ranges(indptr: np.ndarray, frontier: np.ndarray) -> np.ndarray:
    """Vectorized neighbor expansion: returns all neighbors of frontier nodes."""
    starts = indptr[frontier]
    counts = indptr[frontier + 1] - starts
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    offsets = np.zeros(len(frontier), dtype=np.int64)
    np.cumsum(counts[:-1], out=offsets[1:])
    return np.arange(total, dtype=np.int64) + np.repeat(starts - offsets, counts)


def core_bfs(
    source: int,
    core_indptr: np.ndarray,
    core_indices: np.ndarray,
) -> np.ndarray:
    """BFS from source on the core-induced subgraph. Returns dist array (-1 = unreachable).

    Vectorized level-by-level expansion (mirrors l1_betweenness._ranges).
    """
    n = len(core_indptr) - 1
    dist = np.full(n, -1, dtype=np.int64)
    dist[source] = 0
    frontier = np.array([source], dtype=np.int64)
    level = 0
    while frontier.size:
        idx = _core_bfs_ranges(core_indptr, frontier)
        w_arr = core_indices[idx]
        newly = dist[w_arr] < 0
        w_arr = w_arr[newly]
        if w_arr.size == 0:
            break
        level += 1
        dist[w_arr] = level
        frontier = np.unique(w_arr)
    return dist


# --------------------------------------------------------------------------- #
# source scores
# --------------------------------------------------------------------------- #
def core_source_scores(
    u: int,
    core_dist: np.ndarray,
    forest: dict,
    n: int,
) -> tuple[float, float]:
    """Closeness and harmonic for a core source u."""
    # Core contribution (exclude self)
    reachable_core = core_dist >= 0
    reachable_core[u] = False
    n_reachable_core = int(reachable_core.sum())
    if n_reachable_core > 0:
        sum_d_core = float(core_dist[reachable_core].sum())
        harm_core = float((1.0 / core_dist[reachable_core]).sum())
    else:
        sum_d_core = 0.0
        harm_core = 0.0

    # Forest contribution
    n_reachable_forest = 0
    sum_d_forest = 0.0
    harm_forest = 0.0

    for r, hist in forest["tree_depth_hist"].items():
        if core_dist[r] < 0:
            continue
        d_to_r = int(core_dist[r])
        for d, count in hist.items():
            n_reachable_forest += count
            sum_d_forest += count * (d_to_r + d)
            harm_forest += count / (d_to_r + d)

    sum_d = sum_d_core + sum_d_forest
    harm = harm_core + harm_forest

    if sum_d == 0:
        clo = 0.0
    else:
        clo = (n - 1) / sum_d

    return clo, harm


def _same_tree_scan(
    w: int,
    h: int,
    tree_set: set[int],
    adj_sets: dict[int, set[int]],
) -> tuple[int, float, float]:
    """BFS from w within its tree (through the root h, excluding w).

    Traversal passes THROUGH the root h (the tree's only core contact):
    blocking it orphans every sibling/cousin whose tree path routes via
    h — the 2026-10-15 audit lost 6 nodes at d=2 (3.0 harmonic) on
    Luxury_Goods that way. h itself is counted by the core leg (delta +
    0), never here; `visited` covers it as a hub.
    """
    n_same = 0
    sum_d_same = 0.0
    harm_same = 0.0
    visited = {w}
    q: deque[tuple[int, int]] = deque([(w, 0)])
    while q:
        x, d = q.popleft()
        for v in adj_sets[x]:
            if (v == h or v in tree_set) and v not in visited:
                visited.add(v)
                if v != h:
                    n_same += 1
                    sum_d_same += d + 1
                    harm_same += 1.0 / (d + 1)
                q.append((v, d + 1))
    return n_same, sum_d_same, harm_same


def peeled_source_scores(
    w: int,
    forest: dict,
    core_dist: np.ndarray,
    adj_sets: dict[int, set[int]],
    n: int,
) -> tuple[float, float]:
    """Closeness and harmonic for a peeled source w."""
    h = forest["root"][w]
    delta = forest["depth"][w]
    tree_set = forest["tree_nodes"][h]

    # Same-tree contribution (BFS from w within the tree, excluding w).
    n_same, sum_d_same, harm_same = _same_tree_scan(w, h, tree_set, adj_sets)

    # Core contribution: d(w, v) = delta + d(h, v) for all core v reachable from h
    reachable_core = core_dist >= 0
    n_core = 0
    sum_d_core = 0.0
    harm_core = 0.0
    for v in np.flatnonzero(reachable_core):
        d_hv = int(core_dist[v])
        n_core += 1
        sum_d_core += delta + d_hv
        harm_core += 1.0 / (delta + d_hv)

    # Other-tree contribution: d(w, x) = delta + d(h, h2) + depth(x)
    n_other = 0
    sum_d_other = 0.0
    harm_other = 0.0
    for r, hist in forest["tree_depth_hist"].items():
        if r == h:
            continue
        if core_dist[r] < 0:
            continue
        d_hr = int(core_dist[r])
        for d, count in hist.items():
            n_other += count
            sum_d_other += count * (delta + d_hr + d)
            harm_other += count / (delta + d_hr + d)

    sum_d = sum_d_same + sum_d_core + sum_d_other
    harm = harm_same + harm_core + harm_other

    if sum_d == 0:
        clo = 0.0
    else:
        clo = (n - 1) / sum_d

    return clo, harm


def special_source_scores(
    u: int,
    forest: dict,
    adj_sets: dict[int, set[int]],
    n: int,
) -> tuple[float, float]:
    """Source in a zero-attachment component: BFS within the component only."""
    comp = None
    for c in forest["special_components"]:
        if u in c:
            comp = c
            break
    if comp is None:
        return 0.0, 0.0

    visited = {u}
    q = deque([(u, 0)])
    n_reachable = 0
    sum_d = 0.0
    harm = 0.0
    while q:
        x, d = q.popleft()
        for v in adj_sets[x]:
            if v in comp and v not in visited:
                visited.add(v)
                n_reachable += 1
                sum_d += d + 1
                harm += 1.0 / (d + 1)
                q.append((v, d + 1))

    if sum_d == 0:
        clo = 0.0
    else:
        clo = (n - 1) / sum_d

    return clo, harm


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def get_contract(db_path: str | Path) -> list[str]:
    """Contract sources: DISTINCT entity_name WHERE metric='closeness_centrality'."""
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?",
                (CLOSENESS_METRIC,),
            ).fetchall()
        ]
    finally:
        con.close()


def _scores_chunk(
    chunk: list[str],
    pos: dict[str, int],
    core_indptr: np.ndarray,
    core_indices: np.ndarray,
    is_core: np.ndarray,
    forest: dict,
    adj_sets: dict[int, set[int]],
    n: int,
) -> dict[str, dict[str, float]]:
    results: dict[str, dict[str, float]] = {}
    core_dist_cache: dict[int, np.ndarray] = {}
    for name in chunk:
        if name not in pos:
            results[name] = {"closeness": 0.0, "harmonic": 0.0}
            continue
        u = pos[name]
        if is_core[u]:
            core_dist = core_bfs(u, core_indptr, core_indices)
            clo, harm = core_source_scores(u, core_dist, forest, n)
        elif u in forest["root"]:
            h = forest["root"][u]
            if h not in core_dist_cache:
                core_dist_cache[h] = core_bfs(h, core_indptr, core_indices)
            core_dist = core_dist_cache[h]
            clo, harm = peeled_source_scores(u, forest, core_dist, adj_sets, n)
        else:
            clo, harm = special_source_scores(u, forest, adj_sets, n)
        results[name] = {"closeness": clo, "harmonic": harm}
    return results


def compute(
    db_path: str | Path = DEFAULT_DB_PATH,
    jobs: int = 1,
) -> tuple[dict[str, dict[str, float]], dict]:
    """Full folded closeness/harmonic over the live graph.

    Returns ``(scores_by_name, info)`` — scores map entity names to
    {"closeness": float, "harmonic": float}; ``info`` carries decomposition
    for logging.
    """
    names, edges = load_projection(db_path)
    n = len(names)
    pos = {name: i for i, name in enumerate(names)}
    indptr, indices = build_adjacency(n, edges)
    adj_sets = build_adj_sets(edges)

    # Peel
    mask, removed = peel_two_core(indptr, indices, n)
    core_nodes = np.flatnonzero(mask)
    core_set = set(core_nodes.tolist())
    is_core = np.zeros(n, dtype=bool)
    is_core[core_nodes] = True

    # Forest
    forest = build_forest(removed, adj_sets, core_set)

    # Contract sources
    contract = get_contract(db_path)

    # Build core-only adjacency for fast BFS
    core_indptr, core_indices = build_core_adjacency(indptr, indices, is_core)

    # Compute scores
    if jobs == 1:
        results = _scores_chunk(
            contract, pos, core_indptr, core_indices, is_core, forest, adj_sets, n
        )
    else:
        chunks = np.array_split(np.array(contract, dtype=object), jobs)
        parts = fork_map(
            partial(
                _scores_chunk,
                pos=pos,
                core_indptr=core_indptr,
                core_indices=core_indices,
                is_core=is_core,
                forest=forest,
                adj_sets=adj_sets,
                n=n,
            ),
            chunks,
            jobs,
        )
        results = {}
        for part in parts:
            results.update(part)

    info = {
        "n_nodes": n,
        "n_core": len(core_nodes),
        "n_peeled": len(removed),
        "n_trees": len(forest["tree_nodes"]),
        "n_special": len(forest["special_components"]),
        "n_contract": len(contract),
    }
    return results, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1c folded closeness/harmonic (dry-run by default)")
    ap.add_argument(
        "command",
        choices=["closeness-harmonic-fold"],
        help="folded exact closeness/harmonic over the 2-core + analytic trees",
    )
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="fork-split the sources (perf leg passes 4)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="UPSERT the contract rows into graph_analytics (default: dry-run)",
    )
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)

    from helpers.graph.algorithms import write_analytics

    t0 = time.perf_counter()
    results, info = compute(args.db, jobs=args.jobs)
    t_all = time.perf_counter() - t0
    print(
        f"nodes {info['n_nodes']} | core {info['n_core']}"
        f" | peeled {info['n_peeled']} | trees {info['n_trees']}"
        f" | special {info['n_special']} | contract {info['n_contract']}"
        f" | {t_all:.2f}s",
        file=sys.stderr,
        flush=True,
    )

    # Print top closeness and harmonic
    ranked_clo = sorted(results.items(), key=lambda kv: (-kv[1]["closeness"], kv[0]))[: args.top]
    ranked_harm = sorted(results.items(), key=lambda kv: (-kv[1]["harmonic"], kv[0]))[: args.top]
    print(f"[{CLOSENESS_METRIC}] top {min(args.top, len(results))}")
    for name, s in ranked_clo:
        print(f"  {name}: {s['closeness']:.6f}")
    print(f"[{HARMONIC_METRIC}] top {min(args.top, len(results))}")
    for name, s in ranked_harm:
        print(f"  {name}: {s['harmonic']:.6f}")

    if args.apply:
        clo_payload = {name: results[name]["closeness"] for name in results}
        harm_payload = {name: results[name]["harmonic"] for name in results}
        scon = connect(str(args.db), row_factory=None)
        try:
            n1 = write_analytics(CLOSENESS_METRIC, clo_payload, conn=scon)
            n2 = write_analytics(HARMONIC_METRIC, harm_payload, conn=scon)
            print(f"applied {n1} {CLOSENESS_METRIC} + {n2} {HARMONIC_METRIC} rows")
        finally:
            scon.close()
    else:
        print(f"dry-run: would write {len(results)} rows per metric")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
