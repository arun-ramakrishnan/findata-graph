#!/usr/bin/env python3
"""Repo-native hyper trail distances: scipy bipartite BFS over the star
incidence store (proposal
``doc/improvements/proposals/hyper_trail_bipartite_bfs_lane.md``,
filed 2026-11-02).

Replaces unlock-shelf lane 4 (HGX ``calc_ho_shortest_paths``), which is
dead upstream twice over: the 1.8.0 ``dict_to_df`` crash AND the n^2
dense-matrix design (~2.7 GB at 18,233 entities). The bipartite star
never pays the clique tax: each hyperedge is ONE hub node with m spokes
(linear in m, 103,108 arcs at the live posture) instead of C(m,2)
all-pairs dyads (quadratic, 2.45M at the live posture — the measured
louvain OOM wall).

Distance semantics (unit-cost doctrine): a hyper trail between two
entities IS a path in the bipartite incidence graph, so a bipartite
path of 2k steps equals a trail of k hyperedge hops — EXACTLY. This
module returns hops (bipartite distance / 2), and even-step distances
only: an odd bipartite distance ends mid-hop at a hub and is dropped.

Deliberately unit-cost (proposal S5 risk, declared here per the house
rule): ``h_incidence`` carries weight/direction/role columns, but the
trail doctrine counts hops, not weights. The weighted variant is
deferred until a query needs it; arcs are built with data=1.0.

Cutoffs are LOWER BOUNDS, not reachability claims: ``limit`` caps
bipartite steps (default 40 = 20 hops) and every CLI output prints the
limit used, per the sampled-chains doctrine.

Degrades cleanly when ``h_incidence`` is absent (pristine clone):
``build_bipartite`` returns None and the CLI prints a SKIP line — the
W1 never-block posture.

Usage:
    python3 helpers/graph/hyper_trails.py --census-only
    python3 helpers/graph/hyper_trails.py                    # 800-source sample
    python3 helpers/graph/hyper_trails.py --sources 2000 --limit 40
    python3 helpers/graph/hyper_trails.py --full-map         # all entities
    python3 helpers/graph/hyper_trails.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scipy.sparse import coo_matrix  # noqa: E402
from scipy.sparse.csgraph import connected_components, dijkstra  # noqa: E402

DUCKDB_PATH = _PROJECT_ROOT / "memory" / "graph.duckdb"
DEFAULT_LIMIT = 40  # bipartite steps = 20 hyperedge hops
HUB_PREFIX = "e:"  # hub labels are namespaced so they cannot collide with entity_name


def _load_pairs(con=None) -> list[tuple[str, str]] | None:
    """DISTINCT (edge_id, entity_name) pairs from ``h_incidence``.

    Read-only via the ``duckdb_lock.open_read_only`` funnel; connection
    is owned (opened AND closed) only when opened here. Returns None
    when the star store is absent (degrade, never block). Duplicate
    incidences (an entity repeated inside one hyperedge) collapse here —
    the arcs guard in the tests pins that.
    """
    owned = con is None
    if owned:
        from helpers.misc.duckdb_lock import open_read_only

        con = open_read_only(str(DUCKDB_PATH))
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            ("h_incidence",),
        ).fetchone()
        if not row or row[0] != 1:
            return None
        rows = con.execute(
            "SELECT DISTINCT edge_id, entity_name FROM h_incidence ORDER BY edge_id, entity_name"
        ).fetchall()
        return [(HUB_PREFIX + str(e), name) for e, name in rows]
    finally:
        if owned:
            con.close()


def bipartite_from_pairs(pairs: list[tuple[str, str]]) -> dict:
    """Symmetric unit-cost CSR over (hub, entity) pairs — the lane core.

    Entities occupy indices ``[0, n_entities)``; hubs the rest, so every
    consumer slices entity outputs at ``n_entities``. Arcs are exactly
    ``2 * len(pairs)`` — the structural guard that makes a silent
    clique-projection growth impossible.
    """
    uniq = sorted(set(pairs))
    hubs = sorted({h for h, _ in uniq})
    ents = sorted({e for _, e in uniq})
    h_idx = {h: len(ents) + i for i, h in enumerate(hubs)}
    e_idx = {e: i for i, e in enumerate(ents)}
    n = len(hubs) + len(ents)
    rows = np.fromiter((e_idx[e] for _, e in uniq), dtype=np.int64, count=len(uniq))
    cols = np.fromiter((h_idx[h] for h, _ in uniq), dtype=np.int64, count=len(uniq))
    data = np.ones(len(uniq), dtype=np.float64)
    m = coo_matrix((data, (rows, cols)), shape=(n, n)).tocsr()
    matrix = m + m.T  # symmetric: hub->entity mirrors entity->hub
    labels = ents + hubs  # entities first, hubs after
    return {
        "matrix": matrix,
        "labels": labels,
        "n_entities": len(ents),
        "n_hubs": len(hubs),
        "arcs": int(matrix.nnz),
        "pairs": len(uniq),
    }


def build_bipartite(con=None) -> dict | None:
    """Bipartite CSR from the live ``h_incidence`` cache (None when absent)."""
    pairs = _load_pairs(con)
    if pairs is None:
        return None
    return bipartite_from_pairs(pairs)


def _trail_bfs(g: dict, src_idx: int, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """One source: (entity indices reached, hops), even distances only.

    ``dijkstra`` with ``unweighted=True`` is the unit-cost BFS; ``limit``
    caps bipartite steps (odd steps end at a hub, mid-hop — dropped
    here). The self entry (distance 0) is excluded.
    """
    dist = dijkstra(
        g["matrix"],
        directed=False,
        indices=int(src_idx),
        limit=float(limit),
        unweighted=True,
    )
    n_e = g["n_entities"]
    cand = np.nonzero(np.isfinite(dist[:n_e]) & (np.arange(n_e) != src_idx))[0]
    steps = np.round(dist[cand]).astype(np.int64)
    keep = steps % 2 == 0  # odd bipartite steps end mid-hop at a hub
    cand = cand[keep]
    return cand, steps[keep] // 2


def trail_distances(
    g: dict, sources: list[str], *, limit: int = DEFAULT_LIMIT
) -> dict[str, dict[str, int]]:
    """Per-source trail hops to every entity reachable within ``limit``.

    Args are entity LABELS; unknown labels raise. Hops are bipartite
    distance / 2 (see module docstring). Results are lower bounds
    bounded by ``limit`` — always report the limit alongside.
    """
    labels = g["labels"]
    index = {lbl: i for i, lbl in enumerate(labels[: g["n_entities"]])}
    unknown = [s for s in sources if s not in index]
    if unknown:
        raise ValueError(f"unknown source entities: {unknown[:5]}")
    out: dict[str, dict[str, int]] = {}
    for s in sources:
        dst, hops = _trail_bfs(g, index[s], limit)
        out[s] = dict(zip((labels[i] for i in dst), (int(h) for h in hops)))
    return out


def census(g: dict) -> dict:
    """Component census over the bipartite graph (the 240-component finding).

    Entity-side stats are what the proposal reports: every component
    contains at least one entity by construction (hubs always carry
    members), so component count == entity-side component count.
    """
    ncomp, comp = connected_components(g["matrix"], directed=False)
    n_e = g["n_entities"]
    sizes = np.bincount(comp[:n_e], minlength=ncomp)
    top = int(sizes.max())
    return {
        "components": int(ncomp),
        "entities": n_e,
        "hubs": g["n_hubs"],
        "arcs": g["arcs"],
        "largest_component_entities": top,
        "largest_fraction": round(top / n_e, 3) if n_e else 0.0,
    }


def trail_source_stats(g: dict, src_label: str, *, limit: int = DEFAULT_LIMIT) -> dict:
    """Timed single-source stats for the bench leg (no giant dicts)."""
    index = {lbl: i for i, lbl in enumerate(g["labels"][: g["n_entities"]])}
    if src_label not in index:
        raise ValueError(f"unknown source entity: {src_label}")
    t0 = time.perf_counter()
    dst, hops = _trail_bfs(g, index[src_label], limit)
    ms = (time.perf_counter() - t0) * 1000.0
    hist = {}
    for h, c in zip(*np.unique(hops, return_counts=True)):
        hist[int(h)] = int(c)
    return {
        "ms": round(ms, 2),
        "reachable": int(len(dst)),
        "hop_histogram": hist,
        "limit": limit,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", type=int, default=800, help="sample size")
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="bipartite-step cutoff (default 40 = 20 hops)",
    )
    p.add_argument(
        "--full-map", action="store_true", help="all entities instead of a sample (minutes)"
    )
    p.add_argument("--census-only", action="store_true")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    g = build_bipartite()
    if g is None:
        print("SKIP: h_incidence absent (star store not built) — nothing to trail")
        return 0

    ents = g["labels"][: g["n_entities"]]
    if args.census_only:
        c = census(g)
        print(json.dumps(c) if args.json else _fmt_census(c))
        return 0

    import random

    rng = random.Random(args.seed)  # noqa: S311 — seeded reproducible bench sampler, not crypto
    sample = ents if args.full_map else rng.sample(ents, min(args.sources, len(ents)))
    stats = [trail_source_stats(g, s, limit=args.limit) for s in sample]
    ms = sorted(s["ms"] for s in stats)
    reach = [s["reachable"] for s in stats]
    merged: dict[int, int] = {}
    for s in stats:
        for h, c in s["hop_histogram"].items():
            merged[h] = merged.get(h, 0) + c
    med_ms = ms[len(ms) // 2] if ms else 0.0
    p90_ms = ms[min(len(ms) - 1, int(len(ms) * 0.9))] if ms else 0.0
    row = {
        "sources": len(sample),
        "limit_steps": args.limit,
        "limit_hops": args.limit // 2,
        "ms_source_median": round(med_ms, 2),
        "ms_source_p90": round(p90_ms, 2),
        "reachable_median": int(sorted(reach)[len(reach) // 2]),
        "reachable_avg": round(sum(reach) / len(reach), 1),
        "full_map_projected_s": round(med_ms * len(ents) / 1000, 1),
        "hop_p50": _quantile_hops(merged, 0.5),
        "hop_p90": _quantile_hops(merged, 0.9),
        "hop_max": max(merged) if merged else None,
        "entities": len(ents),
        "arcs": g["arcs"],
    }
    if args.full_map:
        import resource

        row["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(json.dumps(row) if args.json else "  ".join(f"{k}={v}" for k, v in row.items()))
    return 0


def _quantile_hops(hist: dict[int, int], q: float) -> int | None:
    if not hist:
        return None
    total = sum(hist.values())
    acc = 0
    for h in sorted(hist):
        acc += hist[h]
        if acc >= total * q:
            return h
    return max(hist)


def _fmt_census(c: dict) -> str:
    return (
        f"components={c['components']}  entities={c['entities']}  "
        f"hubs={c['hubs']}  arcs={c['arcs']}  "
        f"largest_component_entities={c['largest_component_entities']}  "
        f"largest_fraction={c['largest_fraction']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
