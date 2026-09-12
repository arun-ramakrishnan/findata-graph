#!/usr/bin/env python3
"""Scale benchmark: hyper-native (HGX HyMMSBM, star incidence) vs the current
dyadic stack (DuckDB clique expansion + Onager table functions).

Captured from the hypergraph_incidence_hyx S0 measurements (2026-09-13,
proposal §2.1): the same synthetic truth — heavy-tailed hyperedge sizes like
the live taxonomy (lognormal(3.1, 0.9), seeded) — run through BOTH
representations:

  star lane :  Hypergraph build + HyMMSBM fit (scipy-sparse EM).
  dyad lane :  SQL self-join clique expansion (the Σ C(|e|,2) tax) into
               _onager_e, then onager_ctr_pagerank / onager_cmm_louvain.

Recorded baseline (this box, 2026-09-13): tests/data/hyper_scale_baseline.json
— 100K incidences -> star 1.0-1.2 s / 100 EM iters (9 MB); clique blow-up
34x to 3.42M dyads (0.34 s SQL); onager pagerank 196.6 s; louvain 142.7 s at
2.85M dyads and DID NOT COMPLETE at 3.42M (>440 s, suspected OOM, 14 GB box).

Usage (default is the FAST shape — hyper + expansion only, seconds):
    python3 helpers/bench/hyper_scale_bench.py                       # 30K incidences
    python3 helpers/bench/hyper_scale_bench.py --incidences 100000   # scale posture
    python3 helpers/bench/hyper_scale_bench.py --dyadic-analytics    # + pagerank/louvain
                                                            # (MINUTES at 100K)
    python3 helpers/bench/hyper_scale_bench.py --json                # machine rows

NOT wired into make perf (BENCHMARKS is an explicit list) — the louvain leg
exceeds any sane perf budget at scale; run it deliberately.
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
sys.path.insert(0, str(REPO_ROOT / "helpers"))

import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=SyntaxWarning)  # HGX docstrings

import numpy as np  # noqa: E402


def gen_hyper(n_nodes: int, target_inc: int, cap: int, seed: int = 7) -> list[tuple[int, ...]]:
    """Hyperedges with live-like heavy-tailed sizes (seeded, deterministic)."""
    rng = random.Random(seed)
    edges: list[tuple[int, ...]] = []
    total = 0
    while total < target_inc:
        size = min(
            max(2, int(np.random.default_rng(rng.randrange(1 << 30)).lognormal(3.1, 0.9))),
            cap,
        )
        edges.append(tuple(rng.sample(range(n_nodes), min(size, n_nodes))))
        total += len(edges[-1])
    return edges


def lane_hyper(edges: list[tuple[int, ...]], n_iter: int, tracemem: bool = True) -> dict:
    """Star lane: Hypergraph build + HyMMSBM fit."""
    from hypergraphx import Hypergraph
    from hypergraphx.communities.hy_mmsbm.model import HyMMSBM

    peak_mb = None
    t0 = time.perf_counter()
    hg = Hypergraph(edge_list=edges)
    build_s = time.perf_counter() - t0

    tm = None
    if tracemem:
        import tracemalloc

        tracemalloc.start()
        tm = tracemalloc
    t0 = time.perf_counter()
    model = HyMMSBM(K=8, assortative=True, seed=42)
    model.fit(hg, n_iter=n_iter)
    fit_s = time.perf_counter() - t0
    if tm is not None:
        peak_mb = round(tm.get_traced_memory()[1] / 1e6)
        tm.stop()

    return {
        "lane": "hyper",
        "hyperedges": len(edges),
        "incidences": sum(len(e) for e in edges),
        "nodes": hg.num_nodes(),
        "build_s": round(build_s, 2),
        f"fit{n_iter}_s": round(fit_s, 2),
        "peak_mb": peak_mb,
    }


def lane_dyadic(edges: list[tuple[int, ...]], *, analytics: bool) -> dict:
    """Clique lane: SQL self-join expansion (+ optional Onager analytics).

    ``_onager_e`` is built with the BIGINT cast the onager_ctr_* table
    functions require (INTEGER inputs are rejected).
    """
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("INSTALL onager")
        con.execute("LOAD onager")
        con.execute("CREATE TABLE inc(edge_id INTEGER, node INTEGER)")
        con.executemany(
            "INSERT INTO inc VALUES (?, ?)", [(e, n) for e, m in enumerate(edges) for n in m]
        )
        t0 = time.perf_counter()
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _onager_e AS "
            "SELECT DISTINCT p1.node::BIGINT AS src, p2.node::BIGINT AS dst, "
            "1.0::DOUBLE AS weight "
            "FROM inc p1 JOIN inc p2 "
            "ON p1.edge_id = p2.edge_id AND p1.node < p2.node"
        )
        expand_s = time.perf_counter() - t0
        _row = con.execute("SELECT COUNT(*) FROM _onager_e").fetchone()
        n_dy = _row[0] if _row else 0
        out = {
            "lane": "dyadic",
            "hyperedges": len(edges),
            "dyadic_edges": n_dy,
            "blowup_x": round(n_dy / max(1, sum(len(e) for e in edges)), 1),
            "expand_sql_s": round(expand_s, 2),
        }
        if analytics:
            t0 = time.perf_counter()
            con.execute(
                "SELECT node_id, rank FROM onager_ctr_pagerank("
                "(SELECT src, dst, weight FROM _onager_e))"
            ).fetchall()
            out["pagerank_s"] = round(time.perf_counter() - t0, 2)
            t0 = time.perf_counter()
            rows = con.execute(
                "SELECT node_id, community FROM onager_cmm_louvain("
                "(SELECT src, dst, weight FROM _onager_e), seed => 42)"
            ).fetchall()
            out["louvain_s"] = round(time.perf_counter() - t0, 2)
            out["communities"] = len({r[1] for r in rows})
        return out
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--incidences", type=int, default=30_000, help="target star incidences")
    p.add_argument("--nodes", type=int, default=10_000)
    p.add_argument("--cap", type=int, default=500, help="max hyperedge size")
    p.add_argument("--n-iter", type=int, default=100, help="EM iterations")
    p.add_argument(
        "--dyadic-analytics",
        action="store_true",
        help="also run onager pagerank+louvain over the clique "
        "(MINUTES at 100K incidences — see docstring)",
    )
    p.add_argument("--skip-dyadic", action="store_true", help="star lane only")
    p.add_argument("--json", action="store_true", help="JSON rows instead of text")
    args = p.parse_args(argv)

    edges = gen_hyper(args.nodes, args.incidences, args.cap)
    rows = [lane_hyper(edges, args.n_iter)]
    if not args.skip_dyadic:
        rows.append(lane_dyadic(edges, analytics=args.dyadic_analytics))

    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print("  ".join(f"{k}={v}" for k, v in r.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
