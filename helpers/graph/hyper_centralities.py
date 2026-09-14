#!/usr/bin/env python3
"""HGX higher-order centralities over the incidence store (S12,
hypergraph_incidence_hyx; the D16 alternate-engine lanes made runnable).

Three lanes replace the retired igraph weighted-centrality pilot with
hyper-native semantics over the star incidence:

  ho_pagerank   — RW_stationary_state: the stationary distribution of the
                  higher-order random walk (pick a hyperedge ∝ weight, then
                  a node within it). Per-NODE values, sums to 1.
  s_betweenness — s-walk betweenness (measures/s_centralities). HGX returns
                  PER-HYPEREDGE values (the s-line-graph's vertices are the
                  hyperedges); persisted rows are the per-node MEAN over
                  incident hyperedges (documented aggregation — a company's
                  score = the betweenness of the sets it belongs to).
  s_closeness   — same shape, s-walk closeness.

S14: the ho_pagerank lane consumes per-incidence weights (W[n,e] =
hyper_incidences.weight, default 1; auto-engaged when the loaded sources
carry weights — today the S8 edition quote counts). Exact unweighted
reduction to HGX's binary walk is test-pinned. The s-lanes stay
unweighted pending S16 (weights in the s-line-graph).

Read-only by default; --apply persists to graph_analytics via
algorithms.write_analytics (metrics ho_pagerank / s_betweenness /
s_closeness). Same degeneracy guard as the communities lane (singletons
always dropped — they 0/0 the EM; giants >25% of nodes unless --allow-giant).

Usage:
    python3 helpers/graph/hyper_centralities.py                  # dry-run
    python3 helpers/graph/hyper_centralities.py --s 2            # s-walk order
    python3 helpers/graph/hyper_centralities.py --apply
    python3 helpers/graph/hyper_centralities.py --sources sector,theme,industry
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import hyper_arrow as ha  # noqa: E402
from helpers.graph.hyper_communities import (  # noqa: E402
    DEFAULT_SOURCES,
    _exclude_degenerate,
)

METRICS = ("ho_pagerank", "s_betweenness", "s_closeness", "eigen_cec", "eigen_zec", "eigen_hec")


def stationary_pi(
    memberships: dict[str, set[str]],
    weights: dict[tuple[str, str], float] | None = None,
    *,
    tol: float = 1e-12,
    max_iter: int = 10_000,
) -> dict[str, float]:
    """S14: stationary distribution of the WEIGHTED higher-order walk.

    HGX's ``RW_stationary_state`` is binary by construction (its
    ``transition_matrix`` builds ``binary_incidence_matrix`` — verified
    2026-09-13; ``weighted=True`` on the constructor changes nothing for
    the walk). Weighted analogue, Carletti et al. (2017) construction with
    the incidence term promoted from binary to weighted:

        W[n,e] = incidence weight (default 1.0)
        M = W · diag(s_e - 1) · Wᵀ ; zero diagonal ; row-normalise

    Exact unweighted reduction: unit weights reproduce the HGX matrix
    term-for-term (pinned by test against ``RW_stationary_state``).
    Stationary pi by power iteration from the uniform start.
    """
    import numpy as np
    from scipy.sparse import csr_matrix, diags

    nodes = sorted({m for ms in memberships.values() for m in ms})
    labels = sorted(memberships)
    nidx = {n: i for i, n in enumerate(nodes)}
    eidx = {lbl: i for i, lbl in enumerate(labels)}
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for label, members in memberships.items():
        e = eidx[label]
        for m in members:
            rows.append(nidx[m])
            cols.append(e)
            vals.append(float((weights or {}).get((label, m), 1.0)))
    w_mat = csr_matrix((vals, (rows, cols)), shape=(len(nodes), len(labels)))
    scale = diags([float(len(memberships[lbl]) - 1) for lbl in labels])
    m_mat = (w_mat @ scale @ w_mat.T).tocsr()
    m_mat.setdiag(0)
    m_mat.eliminate_zeros()
    row_sums = np.asarray(m_mat.sum(axis=1)).ravel()
    if (row_sums == 0).any():
        raise ValueError("walk undefined: a node has no outgoing mass")
    t_mat = (diags(1.0 / row_sums) @ m_mat).tocsr()
    pi = np.full(len(nodes), 1.0 / len(nodes))
    for _ in range(max_iter):
        nxt = t_mat.T @ pi
        if abs(nxt - pi).sum() < tol:
            pi = nxt
            break
        pi = nxt
    return {n: float(pi[nidx[n]]) for n in nodes}


def _require_hgx():
    """Lazy HGX import (collection works without it; SyntaxWarnings muted)."""
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            from hypergraphx import Hypergraph
            from hypergraphx.dynamics.randwalk import RW_stationary_state
            from hypergraphx.measures.eigen_centralities import (
                CEC_centrality,
                HEC_centrality,
                ZEC_centrality,
            )
            from hypergraphx.measures.s_centralities import s_betweenness, s_closeness
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "hypergraphx is not installed in this interpreter "
            "(pyproject dependency since hypergraph_incidence_hyx)."
        ) from exc
    return (
        Hypergraph,
        RW_stationary_state,
        s_betweenness,
        s_closeness,
        CEC_centrality,
        ZEC_centrality,
        HEC_centrality,
    )


def compute_centralities(
    incidence: dict[str, list[str]],
    *,
    s: int = 1,
    allow_giant: bool = False,
    weights: dict[tuple[str, str], float] | None = None,
) -> dict[str, dict[str, Any]]:
    """All six lanes over one filtered incidence (S12 + S15 eigen).

    Returns ``{"ho_pagerank": ..., "s_betweenness": ..., "s_closeness": ...,
    ["eigen_cec"/"eigen_zec"/"eigen_hec" iff uniform], "_meta": {...}}`` —
    node-keyed, deterministic (eigen trio: power-method seed=42).
    Per-hyperedge raw s-values are NOT returned (report-only inside the CLI);
    the node aggregation is the mean over incident hyperedges.
    """
    Hypergraph, _RW, s_betweenness, s_closeness, CEC, ZEC, HEC = _require_hgx()
    kept, giants, singletons = _exclude_degenerate(incidence, allow_giant=allow_giant)
    if not kept:
        raise ValueError("no hyperedges left after the degeneracy guard")
    kept_sets = {k: set(v) for k, v in kept.items()}
    n_weighted = (
        sum(1 for lbl, ms in kept_sets.items() for m in ms if (lbl, m) in (weights or {}))
        if weights
        else 0
    )

    hg_full = Hypergraph(edge_list=[tuple(m) for m in kept.values()])
    # RW_stationary_state requires a CONNECTED hypergraph; restrict all three
    # lanes to the largest component for a consistent node set (excluded
    # node count goes to _meta — s-centralities on the full graph would
    # disagree across lanes otherwise).
    hg = hg_full.subhypergraph_largest_component()  # returns a Hypergraph
    excluded = hg_full.num_nodes() - hg.num_nodes()

    # ho_pagerank: S14 weighted walk (exact HGX reduction when unweighted).
    # Component restriction mirrors HGX's subhypergraph rule exactly: a
    # hyperedge survives iff FULLY contained in the largest component's
    # nodes (verified against core/undirected.py).
    lc_nodes = set(hg.get_nodes())
    lc_memberships = {lbl: ms for lbl, ms in kept_sets.items() if ms <= lc_nodes}
    ho_pagerank = stationary_pi(lc_memberships, weights=weights if n_weighted else None)

    def _node_mean(edge_scores: dict[tuple, float]) -> dict[str, float]:
        acc: dict[str, list[float]] = {}
        for members, score in edge_scores.items():
            for m in members:
                acc.setdefault(m, []).append(score)
        return {m: sum(v) / len(v) for m, v in acc.items()}

    sb_edge = s_betweenness(hg, s=s)
    sc_edge = s_closeness(hg, s=s)

    # S15: CEC/ZEC/HEC (Benson, doi:10.1137/18M1203031) are UNIFORM-
    # hypergraph definitions — ZEC/HEC assert it, CEC is only defined for
    # it. Mixed-size lanes (sector/sub_sector) skip with a meta note
    # instead of raising; the eigen trio runs when the filtered family
    # happens to be uniform (e.g. pairwise edition sources).
    res: dict[str, Any] = {
        "ho_pagerank": ho_pagerank,
        "s_betweenness": _node_mean(sb_edge),
        "s_closeness": _node_mean(sc_edge),
        "_meta": {
            "s": s,
            "hyperedges": len(kept),
            "dropped_giants": giants,
            "dropped_singletons": len(singletons),
            "excluded_offcomponent": excluded,
            "weighted_incidences": n_weighted,
        },
    }
    # HGX's eigen trio indexes the weight tensor by the node objects
    # themselves (CEC: W[edge[i], edge[j]]) — string names crash it. Build
    # an int-mapped copy of the component-restricted family, run there,
    # map keys back.
    sizes = sorted({len(m) for m in lc_memberships.values()})
    uniform = len(sizes) == 1
    res["_meta"]["eigen_uniform"] = uniform
    res["_meta"]["eigen_edge_sizes"] = sizes[:4]
    if uniform:
        nodes = sorted({m for ms in lc_memberships.values() for m in ms})
        id2node = dict(enumerate(nodes))
        node2id = {n: i for i, n in enumerate(nodes)}
        hg_int = Hypergraph(
            edge_list=[tuple(sorted(node2id[m] for m in ms)) for ms in lc_memberships.values()]
        )
        for name, fn in (("eigen_cec", CEC), ("eigen_zec", ZEC), ("eigen_hec", HEC)):
            raw = fn(hg_int, seed=42)
            res[name] = {id2node[k]: float(v) for k, v in raw.items()}
    else:
        res["_meta"]["eigen_skipped"] = (
            f"non-uniform edge sizes {sizes[0]}..{sizes[-1]} — CEC/ZEC/HEC "
            "are uniform-hypergraph definitions"
        )
    return res


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    p.add_argument("--s", type=int, default=1, help="s-walk order (s=1 ~ pairwise)")
    p.add_argument("--allow-giant", action="store_true")
    p.add_argument("--top", type=int, default=10)
    p.add_argument(
        "--apply",
        action="store_true",
        help="persist per-node values to graph_analytics "
        f"({', '.join(METRICS)}); default: report only",
    )
    args = p.parse_args(argv)

    sources = [x.strip() for x in args.sources.split(",") if x.strip()]
    # Never-block precheck (hyper_lane_wiring W1): absent store / no rows
    # for the requested sources -> clean skip (maint-full on fresh DBs).
    if not ha.store_rowcount(sources):
        print(
            f"[hyper-centralities] incidence store empty for sources "
            f"({', '.join(sources) or 'none'}) — nothing to compute, skipping"
        )
        return 0
    tbl = ha.load_incidence_arrow(sources)  # S18(b)/S14 exit: single Arrow load
    raw = ha.incidence_dict(tbl)
    weights = ha.weights_from_arrow(tbl)
    # same W1 skip for a guard-exhausted store (compute_centralities keeps
    # raising — its contract is pinned; the CLI pre-checks with the same
    # guard so the maint chain never sees that raise on degenerate input)
    kept_probe, _, _ = _exclude_degenerate(raw, allow_giant=args.allow_giant)
    if not kept_probe:
        print("SKIP: no hyperedges left after the degeneracy guard — nothing to compute")
        return 0
    res = compute_centralities(raw, s=args.s, allow_giant=args.allow_giant, weights=weights or None)
    meta = res.pop("_meta")
    print(
        f"incidence: {len(raw)} hyperedges loaded, {meta['hyperedges']} kept "
        f"(s={meta['s']}; dropped {meta['dropped_singletons']} singletons, "
        f"{len(meta['dropped_giants'])} giants, "
        f"{meta['excluded_offcomponent']} nodes off the largest component"
        + (
            f"; WEIGHTED walk: {meta['weighted_incidences']} weighted incidences"
            if meta["weighted_incidences"]
            else "; unweighted (no incidence weights in sources)"
        )
    )
    for metric, values in res.items():
        ranked = sorted(values.items(), key=lambda kv: -kv[1])[: args.top]
        print(f"  {metric} (top {args.top}):")
        for name, v in ranked:
            print(f"    {name:44} {v:.6f}")

    if args.apply:
        from helpers.graph.algorithms import write_analytics

        for metric, values in res.items():
            payload = {
                n: {
                    "value": round(v, 8),
                    "s": args.s,
                    "sources": sources,
                    "weighted": metric == "ho_pagerank" and bool(meta["weighted_incidences"]),
                }
                for n, v in values.items()
            }
            n = write_analytics(metric, payload)
            print(f"APPLY: wrote {n} rows (metric {metric})")
    else:
        print("DRY-RUN (use --apply to persist to graph_analytics)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
