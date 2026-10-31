#!/usr/bin/env python3
"""S1 — Minimum spanning (forest) backbone over the weighted graph projection.

SciPy csgraph scale lane (doc/improvements/proposals/scipy_csgraph_scale_lanes.md
S1). House pattern mirrors l1_closeness.py: dry-run by default, no --apply
(this is a relational readout, not a per-entity metric).

Projection: whole-graph weighted CSR, ex-index, self-loops dropped, parallel
edges collapsed to min(weight) (MST wants the cheapest relationship).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree, connected_components

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
DEFAULT_DB_PATH = _REPO_ROOT / "memory" / "research.db"

from helpers.core.db import connect  # noqa: E402
from helpers.graph.edge_projection import edge_rows  # noqa: E402

INDEX_NOISE = frozenset({"listed_on_index"})


def load_projection(db_path=str(DEFAULT_DB_PATH)):
    """Return (names, edges) where edges are (u,v,w) dense-id triples,
    parallel edges collapsed to min weight, self-loops dropped, ex-index."""
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        # edge_projection: the retired part_of/has_company pair is projected
        # back out of the canonical sector hyperedges (2026-10-19). Without
        # it an MST could not connect a sector-only company at all.
        rows = edge_rows(con)
    finally:
        con.close()
    kept = [(s, t, et, float(w)) for s, t, et, w in rows if s != t and et not in INDEX_NOISE]
    names = sorted({s for s, _, _, _ in kept} | {t for _, t, _, _ in kept})
    pos = {n: i for i, n in enumerate(names)}
    # collapse parallel edges to min weight
    best: dict[tuple[int, int], float] = {}
    for s, t, _et, w in kept:
        a, b = sorted((pos[s], pos[t]))
        key = (a, b)
        if key not in best or w < best[key]:
            best[key] = w
    edges = [(a, b, w) for (a, b), w in best.items()]
    return names, edges


def build_csr(n: int, edges):
    rows = np.array([a for a, _, _ in edges], dtype=np.int64)
    cols = np.array([b for _, b, _ in edges], dtype=np.int64)
    data = np.array([w for _, _, w in edges], dtype=float)
    # symmetric
    r = np.concatenate([rows, cols])
    c = np.concatenate([cols, rows])
    d = np.concatenate([data, data])
    return csr_matrix((d, (r, c)), shape=(n, n))


def mst_forest(db_path=str(DEFAULT_DB_PATH)):
    names, edges = load_projection(db_path)
    n = len(names)
    adj = build_csr(n, edges)
    n_comp, labels = connected_components(adj, directed=False)
    t0 = time.perf_counter()
    tree = minimum_spanning_tree(adj)
    t_mst = time.perf_counter() - t0
    # extract directed parent edges
    coo = tree.tocoo()
    out_edges = []
    for u, v, w in zip(coo.row.tolist(), coo.col.tolist(), coo.data.tolist(), strict=False):
        a, b = (u, v) if u < v else (v, u)
        out_edges.append((a, b, float(w)))
    # dedup reverse duplicates (minimum_spanning_tree yields one per child)
    seen = set()
    unique = []
    for a, b, w in out_edges:
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        unique.append((a, b, w))
    total_weight = sum(w for _, _, w in unique)
    return {
        "names": names,
        "n_nodes": n,
        "n_components": int(n_comp),
        "n_forest_edges": len(unique),
        "total_weight": total_weight,
        "elapsed_s": t_mst,
        "edges": [(names[a], names[b], w) for a, b, w in unique],
        "labels": labels,
    }


def find_bridges(n: int, edges: list[tuple[int, int, float]]) -> set[tuple[int, int]]:
    """Bridges of the undirected simple graph (iterative Tarjan lowlink).

    graph_lane_unlocks S4: the MSF is a forest, so every forest edge is
    critical BY CONSTRUCTION — the honest resilience question is on the
    FULL projection: a backbone pair is a bridge only if removing the
    PAIR disconnects its component (no alternate path). The projection
    is pair-collapsed (parallel edges to min weight), so this is the
    pair-level read. Returns canonical (min, max) dense-id pairs.
    """
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for i, (a, b, _w) in enumerate(edges):
        adj[a].append((b, i))
        adj[b].append((a, i))
    disc = [-1] * n
    low = [0] * n
    bridges: set[tuple[int, int]] = set()
    timer = 0
    for root in range(n):
        if disc[root] != -1:
            continue
        disc[root] = low[root] = timer
        timer += 1
        stack: list[tuple[int, int, object]] = [(root, -1, iter(adj[root]))]
        while stack:
            u, pe, it = stack[-1]
            pushed = False
            for v, ei in it:  # noqa: B007  # resume-per-frame iterator
                if ei == pe:
                    continue
                if disc[v] == -1:
                    disc[v] = low[v] = timer
                    timer += 1
                    stack.append((v, ei, iter(adj[v])))
                    pushed = True
                    break
                low[u] = min(low[u], disc[v])
            if not pushed:
                stack.pop()
                if pe >= 0 and stack:
                    p = stack[-1][0]
                    low[p] = min(low[p], low[u])
                    if low[u] > disc[p]:
                        a, b = edges[pe][0], edges[pe][1]
                        bridges.add((min(a, b), max(a, b)))
    return bridges


def resilience(db_path=str(DEFAULT_DB_PATH)) -> dict:
    """Full-graph bridge census cross-referenced with the MSF backbone
    (graph_lane_unlocks S4). Read-only; loads the projection twice
    (once for the forest, once for the bridge input) — ~1 s total,
    acceptable for a report mode."""
    names, edges = load_projection(db_path)
    n = len(names)
    forest = mst_forest(db_path)
    name_pos = {name: i for i, name in enumerate(names)}
    forest_pairs = set()
    for s, t, _w in forest["edges"]:
        a, b = sorted((name_pos[s], name_pos[t]))
        forest_pairs.add((a, b))
    bridges = find_bridges(n, edges)
    weight_of = {(a, b): w for a, b, w in edges}
    backbone_bridges = forest_pairs & bridges
    # per-component: node count, forest edges, full-graph bridges among them
    labels = forest["labels"]
    comp: dict[int, dict[str, int]] = {}
    for lbl in labels.tolist():
        c = comp.setdefault(int(lbl), {"nodes": 0, "forest": 0, "bridges": 0})
        c["nodes"] += 1
    for a, b in forest_pairs:
        comp[int(labels[a])]["forest"] += 1
    for a, b in bridges:
        comp[int(labels[a])]["bridges"] += 1
    return {
        "names": names,
        "n_nodes": n,
        "n_components": forest["n_components"],
        "n_forest_edges": forest["n_forest_edges"],
        "n_bridges": len(bridges),
        "n_backbone_bridges": len(backbone_bridges),
        "components": comp,
        "bridge_edges": [
            (names[a], names[b], weight_of.get((a, b), 0.0)) for a, b in sorted(bridges)
        ],
        "backbone_bridge_edges": [
            (names[a], names[b])
            for a, b in sorted(backbone_bridges)
        ],
    }


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(description="S1 MST/MSF backbone (dry-run, no apply)")
    p.add_argument(
        "command",
        choices=["mst-backbone"],
        help="minimum spanning tree/forest backbone",
    )
    p.add_argument(
        "--format", choices=["summary", "edges", "json", "resilience"], default="summary"
    )
    p.add_argument("--top", type=int, default=20, help="top edges to print in edges mode")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = p.parse_args(argv)
    if args.format == "resilience":
        res = resilience(args.db)
        pct = (
            f"{res['n_backbone_bridges'] / res['n_forest_edges'] * 100:.1f}%"
            if res["n_forest_edges"]
            else "n/a"
        )
        print(
            f"nodes {res['n_nodes']} | components {res['n_components']} | "
            f"forest_edges {res['n_forest_edges']} | full-graph bridges "
            f"{res['n_bridges']} | backbone ties that are true bridges: "
            f"{res['n_backbone_bridges']} ({pct} of forest)"
        )
        print("  (a forest edge is always critical WITHIN the MSF; 'true bridge'")
        print("   = removing the PAIR disconnects the component in the full graph)")
        comps = sorted(
            res["components"].items(), key=lambda kv: (-kv[1]["bridges"], -kv[1]["nodes"])
        )[: args.top]
        print("components with full-graph bridges:")
        for lbl, c in comps:
            if not c["bridges"]:
                break
            print(
                f"  component {lbl}: {c['nodes']} nodes | "
                f"{c['forest']} forest edges | {c['bridges']} bridges"
            )
        print("bridge pairs (full graph, by dense-id order):")
        for s, t, w in res["bridge_edges"][: args.top]:
            print(f"  {s} — {t}: {w:.3f}")
        return 0
    res = mst_forest(args.db)
    if args.format == "json":
        payload = {
            "n_nodes": res["n_nodes"],
            "n_components": res["n_components"],
            "n_forest_edges": res["n_forest_edges"],
            "total_weight": res["total_weight"],
            "elapsed_s": res["elapsed_s"],
            "edges": [{"source": s, "target": t, "weight": w} for s, t, w in res["edges"]],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.format == "edges":
        for s, t, w in sorted(res["edges"], key=lambda x: x[2])[: args.top]:
            print(f"{s}\t{t}\t{w}")
        print(
            f"# {len(res['edges'])} forest edges, {res['n_components']} components",
            file=sys.stderr,
        )
    else:
        print(
            f"nodes {res['n_nodes']} | components {res['n_components']} | "
            f"forest_edges {res['n_forest_edges']} | total_weight {res['total_weight']:.3f} | "
            f"{res['elapsed_s']:.3f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
