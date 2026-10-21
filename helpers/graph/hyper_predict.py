#!/usr/bin/env python3
"""D10 — hyper-native link prediction over the star incidence store.

Three score lanes over the prediction-grade hyperedge families (edition,
theme, sector, industry, sub_sector, org_membership — event/jv/country are
structurally gated and opt-in only):

  1. ``jaccard``      — incidence-neighborhood jaccard:
                        |E_u ∩ E_v| / |E_u ∪ E_v| over shared hyperedges.
  2. ``s_walk``       — s-walk connectance: number of shared hyperedges;
                        candidate only when shared >= s (cap s<=3, contractual).
  3. ``stationary_pi``— stationary-π proximity: π_u * π_v from HypergraphX's
                        RW_stationary_state (dual-random-walk stationary).

Star-native only: no clique projection (the 41-clique country artifact;
hgx_first_scaling.md §5). Reads through hyper_arrow.hypergraph_from_arrow.
Report-first; ``--apply`` (D13 opt-in) persists per-node candidate lists
under the ``hyper_link_prediction`` metric in graph_analytics. Dry-run
default. No UI/API surface (D15).

Usage::

    python3 helpers/graph/hyper_predict.py                       # dry-run, all lanes
    python3 helpers/graph/hyper_predict.py --method jaccard --top 20
    python3 helpers/graph/hyper_predict.py --sources sector,theme --apply
    python3 helpers/graph/hyper_predict.py --s 3 --top 50
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import hyper_arrow as ha  # noqa: E402

PREDICTION_FAMILIES = ("edition", "theme", "sector", "industry", "sub_sector", "org_membership")
OPT_IN_FAMILIES = ("country", "event", "jv", "group")
METRIC = "hyper_link_prediction"
DEFAULT_TOP = 20
DEFAULT_S = 2
RNG_SEED = 42


def _require_hgx():
    import hypergraphx  # noqa: F401

    from hypergraphx import Hypergraph
    from hypergraphx.dynamics.randwalk import RW_stationary_state
    from hypergraphx.measures.edge_similarity import jaccard_similarity

    return Hypergraph, RW_stationary_state, jaccard_similarity


def _load_family(source: str):
    """Return (hypergraph, labels_map, node->hyperedge-set) for one family."""
    tbl = ha.load_incidence_arrow([source])
    hg, labels = ha.hypergraph_from_arrow(tbl)
    # labels maps member-tuple -> edge label (hypergraph_from_arrow contract);
    # invert to node -> set of hyperedge labels.
    node_edges: dict[str, set[str]] = defaultdict(set)
    for members, edge_label in labels.items():
        for m in members:
            node_edges[m].add(edge_label)
    return hg, labels, node_edges


def _candidates(node_edges: dict[str, set[str]]) -> list[tuple[str, str, int]]:
    """Pairs sharing at least one hyperedge, with shared count."""
    nodes = sorted(node_edges)
    out = []
    for a, b in combinations(nodes, 2):
        shared = len(node_edges[a] & node_edges[b])
        if shared >= 1:
            out.append((a, b, shared))
    return out


def _jaccard_scores(node_edges, pairs):
    res = {}
    for a, b, shared in pairs:
        union = len(node_edges[a] | node_edges[b])
        res[(a, b)] = (shared / union) if union else 0.0
    return res


def _s_walk_scores(node_edges, pairs, s):
    res = {}
    for a, b, shared in pairs:
        if shared >= s:
            res[(a, b)] = float(shared)
    return res


def _components_from_pairs(node_edges, pairs):
    """Connected components of the node graph implied by candidate pairs."""
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b, _ in pairs:
        adj[a].add(b)
        adj[b].add(a)
    seen: set[str] = set()
    components: list[list[str]] = []
    for n in node_edges:
        if n in seen:
            continue
        comp = []
        stack = [n]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(adj.get(x, ()))
        if comp:
            components.append(comp)
    return components


def _component_stationary(hg, comp, Hypergraph, RW_stationary_state, pi_map):
    """Stationary distribution for one component, uniform on failure."""
    if len(comp) == 1:
        pi_map[comp[0]] = 1.0
        return
    # build sub-hypergraph from original hyperedges restricted to this comp
    comp_set = set(comp)
    sub_edge_list = []
    for e in hg.get_edges():
        members = [m for m in e if m in comp_set]
        if len(members) >= 2:
            sub_edge_list.append(tuple(sorted(members)))
    if not sub_edge_list:
        for m in comp:
            pi_map[m] = 1.0 / len(comp)
        return
    try:
        sub_hg = Hypergraph(edge_list=sub_edge_list)
        pi = RW_stationary_state(sub_hg)
        sub_nodes = list(sub_hg.get_nodes())
        for i, nd in enumerate(sub_nodes):
            pi_map[nd] = float(pi[i])
    except Exception:
        for m in comp:
            pi_map[m] = 1.0 / len(comp)


def _stationary_pi_scores(hg, node_edges, pairs):
    try:
        Hypergraph, RW_stationary_state, _ = _require_hgx()
    except Exception:
        return {}
    # RW on a disconnected hypergraph raises, so compute per component.
    components = _components_from_pairs(node_edges, pairs)
    pi_map: dict[str, float] = {}
    for comp in components:
        _component_stationary(hg, comp, Hypergraph, RW_stationary_state, pi_map)
    res = {}
    for a, b, _shared in pairs:
        pa = pi_map.get(a, 0.0)
        pb = pi_map.get(b, 0.0)
        res[(a, b)] = pa * pb
    return res


def _rank(scores: dict[tuple[str, str], float], top: int) -> list[tuple[str, str, float]]:
    return [
        (a, b, score)
        for (a, b), score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[
            :top
        ]
    ]


def _run_family(source: str, method: str, s: int, top: int) -> dict[str, Any]:
    hg, labels, node_edges = _load_family(source)
    pairs = _candidates(node_edges)
    if method == "jaccard":
        scores = _jaccard_scores(node_edges, pairs)
    elif method == "s_walk":
        scores = _s_walk_scores(node_edges, pairs, s)
    elif method == "stationary_pi":
        scores = _stationary_pi_scores(hg, node_edges, pairs)
    elif method == "all":
        # combined: sum of normalised-ish lanes; report per-lane separately
        scores = _jaccard_scores(node_edges, pairs)
    else:
        raise ValueError(f"unknown method {method!r}")
    ranked = _rank(scores, top)
    return {
        "source": source,
        "method": method,
        "n_hyperedges": len(labels),
        "n_nodes": len(node_edges),
        "n_candidates": len(pairs),
        "n_scored": len(scores),
        "ranked": ranked,
        "labels": labels,
        "node_edges": node_edges,
        "hg": hg,
    }


def _print_report(family_results: list[dict], method: str, s: int, top: int) -> None:
    for fr in family_results:
        print(f"\n=== {fr['source']} (method={method}, s={s}) ===")
        print(
            f"  hyperedges={fr['n_hyperedges']} nodes={fr['n_nodes']} "
            f"candidate_pairs={fr['n_candidates']} scored={fr['n_scored']}"
        )
        for (a, b), score in fr["ranked"]:
            shared = len(fr["node_edges"][a] & fr["node_edges"][b])
            print(f"    {a:40} <-> {b:40} score={score:.6f} shared={shared}")


def _apply(family_results: list[dict], method: str, sources: list[str], s: int = 2) -> int:
    from helpers.graph.algorithms import write_analytics

    values: dict[str, dict[str, Any]] = {}

    def _node_row(name: str) -> dict[str, Any]:
        return values.setdefault(
            name, {"method": method, "edge_types": list(sources), "candidates": []}
        )

    for fr in family_results:
        # recompute scores for the chosen method to get full ranked list
        if method == "jaccard":
            scores = _jaccard_scores(fr["node_edges"], _candidates(fr["node_edges"]))
        elif method == "s_walk":
            scores = _s_walk_scores(fr["node_edges"], _candidates(fr["node_edges"]), s)
        elif method == "stationary_pi":
            scores = _stationary_pi_scores(
                fr["hg"], fr["node_edges"], _candidates(fr["node_edges"])
            )
        else:
            scores = _jaccard_scores(fr["node_edges"], _candidates(fr["node_edges"]))
        for (a, b), score in scores.items():
            _node_row(a)["candidates"].append({"name": b, "score": round(float(score), 6)})
            _node_row(b)["candidates"].append({"name": a, "score": round(float(score), 6)})

    # per-node top-50 cap (avoid huge payloads)
    for row in values.values():
        row["candidates"].sort(key=lambda c: -c["score"])
        del row["candidates"][50:]
    if not values:
        return 0
    return write_analytics(METRIC, values)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sources", default=",".join(PREDICTION_FAMILIES))
    ap.add_argument(
        "--method", default="jaccard", choices=["jaccard", "s_walk", "stationary_pi", "all"]
    )
    ap.add_argument("--s", type=int, default=DEFAULT_S, help="s-walk threshold (cap 3)")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--apply", action="store_true", help="persist to graph_analytics")
    args = ap.parse_args(argv)

    s = min(max(args.s, 2), 3)
    sources = [x.strip() for x in args.sources.split(",") if x.strip()]
    # validate against prediction-grade + opt-in
    allowed = set(PREDICTION_FAMILIES) | set(OPT_IN_FAMILIES)
    bad = [x for x in sources if x not in allowed]
    if bad:
        print(f"unknown sources: {bad}; allowed: {sorted(allowed)}", file=sys.stderr)
        return 2

    methods = ["jaccard", "s_walk", "stationary_pi"] if args.method == "all" else [args.method]

    all_results = {}
    for method in methods:
        frs = [_run_family(src, method, s, args.top) for src in sources]
        _print_report(frs, method, s, args.top)
        all_results[method] = frs

    if args.apply:
        # apply the primary method (first in list)
        primary = methods[0]
        n = _apply(all_results[primary], primary, sources, s)
        print(f"\nAPPLY: wrote {n} entity rows (metric {METRIC}, method={primary})")
    else:
        print("\nDRY-RUN (use --apply to persist to graph_analytics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
