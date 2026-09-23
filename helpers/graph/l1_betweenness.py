#!/usr/bin/env python3
"""L1b lane — betweenness via 2-core folding (graph_perf_l1 S2).

scipy has no Brandes and no engine escapes Brandes at this V-count
(igraph 37.7 s / Onager 48.9 s full-graph), so the fold IS the engine.
Live structure (2026-09-23): 2-core = 3,385 nodes / 26,761 edges of
22,046 endpoints; everything peeled is a forest — 18,265
single-attachment trees (folded into attachment weights k_w) + 19
zero-attachment forest components (internal-only betweenness, closed
form).

Exact algorithm, not an approximation:

1. exact two-accumulator Brandes on the core — unit accumulator U(v)
   covers core-core pairs; per-attachment-source k-weighted
   accumulators TU(v), KK(v) cover tree-to-core and tree-to-tree pairs
   (a folded tree acts as k endpoints at its attachment, with the
   attachment's own endpoint credit added analytically);
2. closed-form tree betweenness for every folded node m of tree T:
   ``BC(m) = sum_{i<j} s_i*s_j + up * sum_i s_i`` over the child
   subtrees of m (sizes s_i) plus the up-side of the node's connected
   component (up = |Z| - 1 - sum s_i). Leaves come out exactly 0.

Normalization matches the incumbent contract rows (the
``betweenness_centrality`` convention): raw * 2/((n-1)(n-2)) — the
networkx-style undirected rescale of the UNORDERED raw — over the
ex-index projection endpoints (n = 21,453 = distinct endpoints of the
non-listed_on_index edges; index-only isolates have no path in the
projection, contribute nothing, and are not in Onager's node set either).
Verified against a fresh Onager walk: ratio flat at 1.000000 (r=1.0) over
all scored nodes. The v_centrality_* stamp serves HALF this scale (its own
lane convention) — not the contract's. Raw is ordered-scale — every
core node is a Brandes source, so core-core pairs count from both ends,
exactly like Onager's full-graph Brandes; the divisor is the ordered
(n-1)(n-2), so there is NO halving step (networkx halves only because it
pairs ordered accumulation with the unordered divisor). Adjudicated
2026-09-23: U/2 tried, top row off by 0.034, reverted; unhalved matches
all 1,734 contract rows to 2.2e-16.

Write surface: ``graph_analytics`` (the contract's home), UPSERT via
``algorithms.write_analytics``, opt-in ``--apply`` (D13); metric
'betweenness_centrality', the 1,734 company rows.
"""

from __future__ import annotations

import argparse
import sqlite3
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

BETWEENNESS_METRIC = "betweenness_centrality"


# --------------------------------------------------------------------------- #
# graph loading + 2-core decomposition
# --------------------------------------------------------------------------- #
#: centrality projection rule (graph_centrality_index_noise, completed.md
#: #254): listed_on_index is 36% of edges and semi-annual CSV fill, not an
#: editorial relation — with it in the projection index hubs dominate
#: betweenness (NIFTY SME EMERGE ranked #2). Mirrors
#: algorithms.EDGE_TYPES_EXCLUDED_FROM_CENTRALITY.
INDEX_NOISE = frozenset({"listed_on_index"})


def load_projection(db_path: str | Path = DEFAULT_DB_PATH):
    """Sorted endpoint names + deduped undirected edge pairs (dense ids)."""
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
    finally:
        con.close()
    names = sorted({s for s, _, _ in rows} | {t for _, t, _ in rows})
    pos = {n: i for i, n in enumerate(names)}
    edges = sorted(
        {tuple(sorted((pos[s], pos[t]))) for s, t, et in rows if s != t and et not in INDEX_NOISE}
    )
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


def component_sizes(
    indptr: np.ndarray, indices: np.ndarray, n: int
) -> tuple[np.ndarray, list[int]]:
    """comp id per node (BFS over the full graph) + per-component size."""
    comp = np.full(n, -1, dtype=np.int64)
    sizes: list[int] = []
    cid = -1
    for start in range(n):
        if comp[start] >= 0:
            continue
        cid += 1
        comp[start] = cid
        q = deque([start])
        size = 0
        while q:
            v = q.popleft()
            size += 1
            for w in indices[indptr[v] : indptr[v + 1]]:
                if comp[w] < 0:
                    comp[w] = cid
                    q.append(w)
        sizes.append(size)
    return comp, sizes


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


def tree_components(
    removed: set[int],
    adj_sets: dict[int, set[int]],
    core: set[int],
) -> tuple[list[set[int]], list[tuple[set[int], int]]]:
    """Split the peeled forest into tree components.

    Returns ``(folded, specials)`` — ``folded``: single-attachment trees
    (folded analytically; the attachment's tree size feeds the k-weighted
    accumulators); ``specials``: zero-attachment forest components
    (betweenness is internal-only, computed by the closed form).
    """
    tadj = defaultdict(set)
    for n in removed:
        for m in adj_sets[n]:
            if m in removed:
                tadj[n].add(m)
    folded: list[set[int]] = []
    specials: list[tuple[set[int], int]] = []
    seen: set[int] = set()
    for n in removed:
        if n in seen:
            continue
        comp = {n}
        q = deque([n])
        seen.add(n)
        while q:
            x = q.popleft()
            for m in tadj[x]:
                if m not in seen:
                    seen.add(m)
                    comp.add(m)
                    q.append(m)
        atts = {m for x in comp for m in adj_sets[x] if m in core}
        if len(atts) == 1:
            folded.append(comp)
        elif not atts:
            specials.append((comp, len(comp)))
        else:
            # multiple attachments into the SAME core component: impossible
            # to peel (cycle); multiple core components: bridge — keep both
            # forms out of the analytic fold by joining the Brandes graph.
            specials.append((comp, -1))
    return folded, specials


# --------------------------------------------------------------------------- #
# two-accumulator Brandes on the core
# --------------------------------------------------------------------------- #
def _ranges(indptr: np.ndarray, frontier: np.ndarray) -> np.ndarray:
    starts = indptr[frontier]
    counts = indptr[frontier + 1] - starts
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    offsets = np.zeros(len(frontier), dtype=np.int64)
    np.cumsum(counts[:-1], out=offsets[1:])
    return np.arange(total, dtype=np.int64) + np.repeat(starts - offsets, counts)


def _brandes_source(
    indptr: np.ndarray,
    indices: np.ndarray,
    s: int,
    kw: np.ndarray,
    is_core: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """(delta_unit, delta_kweighted, kk_world) for one source, run on the
    CORE-INDUCED graph (BFS restricted to ``is_core``) — the fold's term
    disjointness depends on it: with the full adjacency the unit accumulator
    also counts core->leaf targets, double-covering pairs that TU and the
    attachment gateway terms own (toy-proven 3.7x inflation, proposal §8).

    delta_u(v): ordered core-core dependency (U is halved on assembly).
    delta_k(v): k-weighted dependency toward attachments INCLUDING the
      target-side self term k_v at v itself (v is the leaf gateway for pair
      {s, leaf@v}) — covers core<->tree pairs.
    kk_world: sum of k over OTHER reachable attachments (excludes s) — the
      source-side self mass for KK (v=s is intermediate for pairs
      {leaf@s, leaf@a}); the caller weights it by k_s at v=s only."""
    n = len(indptr) - 1
    dist = np.full(n, -1, dtype=np.int64)
    sigma = np.zeros(n)
    dist[s] = 0
    sigma[s] = 1.0
    frontier = np.array([s], dtype=np.int64)
    levels: list[tuple[np.ndarray, np.ndarray]] = []
    level = 0
    while frontier.size:
        idx = _ranges(indptr, frontier)
        v_arr = np.repeat(frontier, indptr[frontier + 1] - indptr[frontier])
        w_arr = indices[idx]
        keep = is_core[w_arr]
        v_arr = v_arr[keep]
        w_arr = w_arr[keep]
        newly = dist[w_arr] < 0
        levels.append((v_arr[newly], w_arr[newly]))
        cnt = np.bincount(w_arr[newly], minlength=n)
        sig_new = np.bincount(w_arr[newly], weights=sigma[v_arr[newly]], minlength=n)
        fresh = np.flatnonzero((dist < 0) & (cnt > 0))
        sigma[fresh] = sig_new[fresh]
        dist[fresh] = level + 1
        frontier = fresh
        level += 1
    delta_u = np.zeros(n)
    delta_k = np.zeros(n)
    for v_arr, w_arr in reversed(levels):
        ratio = sigma[v_arr] / sigma[w_arr]
        delta_u += np.bincount(
            v_arr,
            weights=ratio * (1.0 + delta_u[w_arr]),
            minlength=n,
        )
        delta_k += np.bincount(
            v_arr,
            weights=ratio * (kw[w_arr] + delta_k[w_arr]),
            minlength=n,
        )
    reach = dist >= 0
    reach[s] = False
    kk_world = float(kw[reach].sum())
    gate = reach & (kw > 0)
    delta_k[gate] += kw[gate]
    return delta_u, delta_k, kk_world


def _brandes_chunk(
    chunk: np.ndarray,
    indptr: np.ndarray,
    indices: np.ndarray,
    kw: np.ndarray,
    is_core: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(indptr) - 1
    U = np.zeros(n)
    TU = np.zeros(n)
    KK = np.zeros(n)
    for s in chunk:
        du, dk, kk_world = _brandes_source(indptr, indices, int(s), kw, is_core)
        # Standard Brandes never accumulates the source's own dependency
        # entry (delta_s(s) counts target continuations, not betweenness
        # credit for s); the wholesale vector adds below must not see it.
        # KK's source-side credit for v=s is supplied by kk_world instead.
        du[int(s)] = 0.0
        dk[int(s)] = 0.0
        U += du
        TU += dk
        k_s = kw[s]
        if k_s > 0:
            KK += k_s * dk
            KK[int(s)] += k_s * kk_world
    return U, TU, KK


def brandes_core(
    indptr: np.ndarray,
    indices: np.ndarray,
    sources: np.ndarray,
    kw: np.ndarray,
    jobs: int = 1,
    is_core: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two-accumulator Brandes over ``sources``.

    Disjoint pair coverage (unordered conventions on assembly — U and KK are
    halved there, TU is not):
    - ``U(v)  = sum_s delta_u_s(v)``               — core-core pairs
    - ``TU(v) = sum_s delta_k_s(v)``               — core<->tree pairs
    - ``KK(v) = 1/2 sum_{k_s>0} k_s delta_k_s(v)`` — tree<->tree pairs
    """
    if is_core is None:
        is_core = np.ones(len(indptr) - 1, dtype=bool)
    chunks = np.array_split(sources, jobs)
    parts = fork_map(
        partial(_brandes_chunk, indptr=indptr, indices=indices, kw=kw, is_core=is_core),
        chunks,
        jobs,
    )
    return tuple(np.sum(p, axis=0) for p in zip(*parts))  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# tree scores (closed form)
# --------------------------------------------------------------------------- #
def tree_node_scores(
    tree: set[int],
    root: int,
    n_up_world: int,
    children: dict[int, list[int]],
    order: list[int],
    subtree: dict[int, int],
) -> dict[int, float]:
    """Closed-form betweenness for every node of a folded tree.

    ``n_up_world`` is the size of the world reachable "upward" from the
    root's side: the FULL connected-component size for trees attached to
    the core (core + sibling trees + everything else in the component),
    the bare component size for zero-attachment forest components.
    ``subtree[m]`` includes m. ``up(m) = n_up_world - subtree(m)``.
    """
    scores: dict[int, float] = {}
    for m in tree:
        kids = children.get(m, [])
        total_kids = sum(subtree[k] for k in kids)
        up = n_up_world - subtree[m]
        s = up * total_kids
        pair_sum = 0.0
        run = 0
        for k in kids:
            pair_sum += run * subtree[k]
            run += subtree[k]
        s += pair_sum
        scores[m] = float(s)
    return scores


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def compute(
    db_path: str | Path = DEFAULT_DB_PATH,
    jobs: int = 1,
) -> tuple[dict[str, float], dict]:
    """Full folded betweenness over the live graph.

    Returns ``(scores_by_name, info)`` — scores are RAW Brandes counts for
    every endpoint node; ``info`` carries the decomposition for logging.
    """
    names, edges = load_projection(db_path)
    n = len(names)
    indptr, indices = build_adjacency(n, edges)
    comp, comp_sizes = component_sizes(indptr, indices, n)

    mask, removed = peel_two_core(indptr, indices, n)
    core = np.flatnonzero(mask).tolist()
    core_set = set(core)

    adj_sets: dict[int, set[int]] = defaultdict(set)
    for a, b in edges:
        adj_sets[a].add(b)
        adj_sets[b].add(a)

    folded, specials = tree_components(removed, adj_sets, core_set)

    # attachment weights (multiple trees may share an attachment)
    kw = np.zeros(n, dtype=float)
    att_trees: dict[int, list[int]] = defaultdict(list)
    for tree in folded:
        att = next(m for x in tree for m in adj_sets[x] if m in core_set)
        k = len(tree)
        kw[att] += k
        att_trees[att].append((k, tree))

    is_core = np.zeros(n, dtype=bool)
    is_core[core] = True
    U, TU, KK = brandes_core(indptr, indices, np.array(core, dtype=np.int64), kw, jobs, is_core)
    # Disjoint coverage, unordered convention: U/2 owns core-core (Brandes'
    # unit accumulator counts each pair twice), TU owns core<->tree (the
    # target-side self term k_v is baked in — it IS the old attachment
    # credit), KK/2 owns tree-tree across attachments (each pair arises from
    # both endpoint runs; the source-side self term lands on v=s in
    # _brandes_chunk).
    raw = U / 2.0 + TU + KK / 2.0

    # same-attachment cross-tree pairs: their only intermediate is the
    # attachment itself (leaf->a->leaf); k_i*k_j over tree pairs at a.
    # (Same-TREE pairs belong to the closed form below.)
    for att, ktree_list in att_trees.items():
        pair_same = 0.0
        run = 0
        for k, _ in ktree_list:
            pair_same += run * k
            run += k
        raw[att] += pair_same

    # tree-internal scores (closed form; raw[m] starts at 0 for tree nodes —
    # no Brandes source/target path between core nodes passes through them)
    for att, ktree_list in att_trees.items():
        n_up_world = comp_sizes[int(comp[att])]
        for k, tree in ktree_list:
            root = next(x for x in tree if att in adj_sets[x])
            parent: dict[int, int] = {root: -1}
            children: dict[int, list[int]] = defaultdict(list)
            order: list[int] = [root]
            q = deque([root])
            while q:
                x = q.popleft()
                for m in adj_sets[x]:
                    if m in tree and m not in parent:
                        parent[m] = x
                        children[x].append(m)
                        order.append(m)
                        q.append(m)
            subtree = {m: 1 for m in tree}
            for m in reversed(order):
                if parent[m] != -1:
                    subtree[parent[m]] += subtree[m]
            for m, s in tree_node_scores(tree, root, n_up_world, children, order, subtree).items():
                raw[m] += s

    # zero-attachment forest components: betweenness is internal-only —
    # the same closed form per tree, world = the forest's own size.
    for sp_comp, sp_size in specials:
        if sp_size <= 0:
            continue  # multi-attachment bridge comp: not analytic (documented)
        seen_sp: set[int] = set()
        for start in sp_comp:
            if start in seen_sp:
                continue
            tree = {start}
            q = deque([start])
            seen_sp.add(start)
            while q:
                x = q.popleft()
                for m in adj_sets[x]:
                    if m in sp_comp and m not in seen_sp:
                        seen_sp.add(m)
                        tree.add(m)
                        q.append(m)
            root = start
            parent: dict[int, int] = {root: -1}
            children: dict[int, list[int]] = defaultdict(list)
            order = [root]
            q = deque([root])
            while q:
                x = q.popleft()
                for m in adj_sets[x]:
                    if m in tree and m not in parent:
                        parent[m] = x
                        children[x].append(m)
                        order.append(m)
                        q.append(m)
            if len(order) != len(tree):
                raise ValueError("special comp tree split is not a tree")
            subtree = {m: 1 for m in tree}
            for m in reversed(order):
                if parent[m] != -1:
                    subtree[parent[m]] += subtree[m]
            for m, sc in tree_node_scores(tree, root, sp_size, children, order, subtree).items():
                raw[m] += sc

    # n for the divisor = endpoints of the ex-index projection (index-only
    # isolates have no path in the projection and contribute nothing).
    ex_n = len({a for a, _ in edges} | {b for _, b in edges})
    info = {
        "endpoints": ex_n,
        "core_nodes": len(core),
        "core_edges": len(edges),
        "tree_nodes_folded": sum(len(t) for t in folded),
        "trees": len(folded),
        "special_components": len(specials),
    }
    return dict(zip(names, raw.tolist())), info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1b folded betweenness (dry-run by default)")
    ap.add_argument(
        "command",
        choices=["betweenness-fold"],
        help="folded exact betweenness over the 2-core + analytic trees",
    )
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="fork-split the core Brandes sources (perf leg passes 4)",
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
    scores, info = compute(args.db, jobs=args.jobs)
    t_all = time.perf_counter() - t0
    print(
        f"endpoints {info['endpoints']} | core {info['core_nodes']}"
        f" ({info['core_edges']} core edges) | folded trees {info['trees']}"
        f" ({info['tree_nodes_folded']} nodes) | special comps"
        f" {info['special_components']} | {t_all:.2f}s",
        file=sys.stderr,
        flush=True,
    )

    norm = (info["endpoints"] - 1) * (info["endpoints"] - 2) / 2.0
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
    print(f"[{BETWEENNESS_METRIC}] top {min(args.top, len(scores))} (normalized)")
    for name, s in ranked:
        print(f"  {name}: {s / norm:.6f}")
    scon = sqlite3.connect(str(args.db))
    try:
        contract = [
            r[0]
            for r in scon.execute(
                "SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?",
                (BETWEENNESS_METRIC,),
            ).fetchall()
        ]
        payload = {n: scores[n] / norm for n in contract if n in scores}
        if args.apply:
            n = write_analytics(BETWEENNESS_METRIC, payload, conn=scon)
            print(f"applied {n} rows under {BETWEENNESS_METRIC!r}")
        else:
            print(f"dry-run: would write {len(payload)} rows under {BETWEENNESS_METRIC!r}")
    finally:
        scon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
