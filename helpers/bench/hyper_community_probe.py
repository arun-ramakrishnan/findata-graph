#!/usr/bin/env python3
"""Wall-4 S1 probe bench (banked, gitignored bench_data):
clique-projection louvain (onager) vs incidence-native Hy-MMSBM.

The CLIQUE_TAX wall: louvain/onager community work needs the clique
projection - C(m,2) per hyperedge, 34-48x inflation, OOM at >=3.42M
dyads (live 2.45M tax @ 51,554 incidences). This probe banks the
comparison the landing slices are gated on (proposal scaling_next_walls.md
Wall 4):

  (a) projection baseline : DuckDB SQL self-join -> dyads -> onager
       louvain (the exact #473 wall, reused verbatim from lane_dyadic).
  (b) incidence-native     : Hy-MMSBM (hyper_communities.fit_communities)
       directly over the incidence; no projection ever materialized.

Supplieranted-community synthetic generator (seeded) so both methods have a
recoverable signal; ARI is scored between the methods and vs the planted
ground truth. Rows banked to bench_data/hyper_community_probe/<ts>.json:
  n_incidences, n_entities, n_dyads, blowup_x, t_projection_s,
  t_onager_louvain_s, t_hgx_fit_s, ari_methods (proj vs hgx),
  ari_proj_truth, ari_hgx_truth, nodes_common, k, seed, n_hyperedges.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from sklearn.metrics import adjusted_rand_score  # noqa: E402

from helpers.graph.hyper_communities import _exclude_degenerate, fit_communities  # noqa: E402


def gen_hyper_planted(
    n_nodes: int,
    target_inc: int,
    cap: int,
    k: int,
    noise_frac: float = 0.15,
    seed: int = 7,
) -> tuple[list[tuple[int, ...]], dict[int, int]]:
    """Seeded planted-community hypergraph (ground truth in return).

    Nodes are split into k blocks; each hyperedge draws members from one
    block with prob (1-noise_frac) (else uniform across all nodes). The
    second return value maps node id -> ground-truth block so both methods
    can be scored against a recoverable signal."""
    rng = random.Random(seed)  # noqa: S311 — seeded reproducible probe sampler, not crypto
    blocks = [[] for _ in range(k)]
    for n in range(n_nodes):
        blocks[rng.randrange(k)].append(n)
    truth = {}
    for b, members in enumerate(blocks):
        for n in members:
            truth[n] = b
    edges = []
    total = 0
    while total < target_inc:
        size = min(
            max(2, int(np.random.default_rng(rng.randrange(1 << 30)).lognormal(3.1, 0.9))),
            cap,
        )
        if rng.random() < noise_frac:
            pool = list(range(n_nodes))
        else:
            pool = blocks[rng.randrange(k)]
        edges.append(tuple(rng.sample(pool, min(size, len(pool)))))
        total += len(edges[-1])
    return edges, truth


def incidence_dict(edges):
    return {f"h{i}": [f"n{n}" for n in members] for i, members in enumerate(edges)}


def projection_dyads(con, edges):
    """SQL self-join clique expansion (same SQL as lane_dyadic). Returns (s, n_dyads)."""
    con.execute("CREATE TABLE inc(edge_id INTEGER, node INTEGER)")
    con.executemany(
        "INSERT INTO inc VALUES (?, ?)", [(e, n) for e, m in enumerate(edges) for n in m]
    )
    t0 = time.perf_counter()
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _onager_e AS "
        "SELECT DISTINCT p1.node::BIGINT AS src, p2.node::BIGINT AS dst, 1.0::DOUBLE AS weight "
        "FROM inc p1 JOIN inc p2 ON p1.edge_id = p2.edge_id AND p1.node < p2.node"
    )
    expand_s = time.perf_counter() - t0
    n_dy = con.execute("SELECT COUNT(*) FROM _onager_e").fetchone()[0]
    return expand_s, n_dy


def onager_louvain(con, seed=42):
    rows = con.execute(
        "SELECT node_id, community FROM onager_cmm_louvain("
        "(SELECT src, dst, weight FROM _onager_e), seed => ?)",
        (seed,),
    ).fetchall()
    return {int(n): int(c) for n, c in rows}


def run_case(edges, truth, k, seed):
    import duckdb

    con = duckdb.connect()
    try:
        try:
            con.execute("INSTALL onager")
        except duckdb.Error:
            pass
        con.execute("LOAD onager")

        inc = incidence_dict(edges)
        kept, giants, singletons = _exclude_degenerate(inc, allow_giant=False)
        t0 = time.perf_counter()
        fit = fit_communities(kept, k=k, seed=seed, n_iter=30)
        t_hgx = time.perf_counter() - t0

        t_proj, n_dy = projection_dyads(con, edges)
        t0 = time.perf_counter()
        proj_comm = onager_louvain(con, seed=seed)
        t_louvain = time.perf_counter() - t0

        n_inc = sum(len(e) for e in edges)
        n_entities = len({m for members in kept.values() for m in members})
        named_proj = {f"n{k}": c for k, c in proj_comm.items()}
        common = sorted(set(fit.keys()) & set(named_proj.keys()))
        if len(common) < 2:
            ari_methods = ari_proj_truth = ari_hgx_truth = None
        else:
            ari_methods = float(
                adjusted_rand_score(
                    [fit[c]["block"] for c in common],
                    [named_proj[c] for c in common],
                )
            )
            truth_vals = [truth[int(c[1:])] for c in common]
            ari_proj_truth = float(adjusted_rand_score(truth_vals, [named_proj[c] for c in common]))
            ari_hgx_truth = float(
                adjusted_rand_score(truth_vals, [fit[c]["block"] for c in common])
            )
        blowup = round(n_dy / max(1, n_inc), 1)
        return {
            "n_incidences": n_inc,
            "n_entities": n_entities,
            "n_hyperedges": len(edges),
            "n_dyads": n_dy,
            "blowup_x": blowup,
            "t_projection_s": round(t_proj, 3),
            "t_onager_louvain_s": round(t_louvain, 3),
            "t_hgx_fit_s": round(t_hgx, 3),
            "ari_methods": ari_methods,
            "ari_proj_truth": ari_proj_truth,
            "ari_hgx_truth": ari_hgx_truth,
            "nodes_common": len(common),
            "k": k,
            "seed": seed,
        }
    finally:
        con.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--incidences", type=int, default=6_000)
    p.add_argument("--nodes", type=int, default=8_000)
    p.add_argument("--cap", type=int, default=500)
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    edges, truth = gen_hyper_planted(args.nodes, args.incidences, args.cap, args.k, seed=args.seed)
    row = run_case_bip(edges, truth, args.k, args.seed)

    out_dir = Path("bench_data/hyper_community_probe")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"probe_{ts}.json"
    out_path.write_text(json.dumps(row, indent=2) + "\n")

    if args.json:
        print(json.dumps(row))
    else:
        print("  ".join(f"{k}={v}" for k, v in row.items()))
        print(f"banked -> {out_path}")
    return 0


def bipartite_louvain_pairs(pairs, seed=42):
    """igraph Louvain (multilevel) over the bipartite (hub, entity) graph.

    Incidence-native: no clique projection materialized; modularity is
    optimized on the bipartite representation directly (same objective
    family as the onager projection baseline, so ARI parity is the
    meaningful go/no-go). Returns {entity_name: cid} for entity nodes.
    """
    import igraph as ig

    entities = sorted({e for _, e in pairs})
    hubs = sorted({h for h, _ in pairs})
    e_idx = {e: i for i, e in enumerate(entities)}
    h_idx = {h: len(entities) + i for i, h in enumerate(hubs)}
    n = len(entities) + len(hubs)
    g = ig.Graph(n=n, edges=[(e_idx[e], h_idx[h]) for h, e in pairs], directed=False)
    g.es["weight"] = [1.0] * len(pairs)
    comm = g.community_multilevel(weights=g.es["weight"])
    return {e: int(comm.membership[e_idx[e]]) for e in entities}


def run_case_bip(edges, truth, k, seed):
    """Both incidence-native legs vs the projection baseline; ARI vs truth and vs each other."""
    import duckdb

    con = duckdb.connect()
    try:
        try:
            con.execute("INSTALL onager")
        except duckdb.Error:
            pass
        con.execute("LOAD onager")

        inc = incidence_dict(edges)
        kept, giants, singletons = _exclude_degenerate(inc, allow_giant=False)
        t0 = time.perf_counter()
        fit = fit_communities(kept, k=k, seed=seed, n_iter=30)
        t_hgx = time.perf_counter() - t0

        kept_pairs = []
        for h, members in kept.items():
            for m in members:
                kept_pairs.append((h[1:], m))
        t0 = time.perf_counter()
        bip_comm = bipartite_louvain_pairs(kept_pairs, seed=seed)
        t_bip = time.perf_counter() - t0

        t_proj, n_dy = projection_dyads(
            con, [tuple(sorted(int(m[1:]) for m in members)) for members in kept.values()]
        )
        t0 = time.perf_counter()
        proj_comm = onager_louvain(con, seed=seed)
        t_louvain = time.perf_counter() - t0

        n_inc = sum(len(m) for m in kept.values())
        n_entities = len({m for members in kept.values() for m in members})
        named_proj = {f"n{k}": c for k, c in proj_comm.items()}
        common = sorted(set(fit.keys()) & set(named_proj.keys()) & set(bip_comm.keys()))
        ari = {}
        if len(common) < 2:
            ari = {
                "method_proj": None,
                "method_bip": None,
                "proj_truth": None,
                "bip_truth": None,
                "hgx_truth": None,
                "proj_bip": None,
                "proj_hgx": None,
                "bip_hgx": None,
            }
        else:
            tv = [truth[int(c[1:])] for c in common]
            pv = [named_proj[c] for c in common]
            bv = [bip_comm[c] for c in common]
            hv = [fit[c]["block"] for c in common]
            ari = {
                "method_proj": float(adjusted_rand_score(tv, pv)),
                "method_bip": float(adjusted_rand_score(tv, bv)),
                "proj_truth": float(adjusted_rand_score(tv, pv)),
                "bip_truth": float(adjusted_rand_score(tv, bv)),
                "hgx_truth": float(adjusted_rand_score(tv, hv)),
                "proj_bip": float(adjusted_rand_score(pv, bv)),
                "proj_hgx": float(adjusted_rand_score(pv, hv)),
                "bip_hgx": float(adjusted_rand_score(bv, hv)),
            }
        blowup = round(n_dy / max(1, n_inc), 1)
        return {
            "n_incidences": n_inc,
            "n_entities": n_entities,
            "n_hyperedges": len(kept),
            "n_dyads": n_dy,
            "blowup_x": blowup,
            "t_projection_s": round(t_proj, 3),
            "t_onager_louvain_s": round(t_louvain, 3),
            "t_bip_louvain_s": round(t_bip, 3),
            "t_hgx_fit_s": round(t_hgx, 3),
            "ari": ari,
            "nodes_common": len(common),
            "k": k,
            "seed": seed,
        }
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
