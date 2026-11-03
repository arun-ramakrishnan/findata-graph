#!/usr/bin/env python3
"""Bipartite-louvain community lane (Wall 4 S3 landing).

Wall 4 probe (2026-11-02, bench_data/hyper_community_probe/) proved that
igraph Louvain (multilevel) run directly on the bipartite (hub, entity)
incidence graph reaches ARI parity with the clique-projection louvain
baseline (onager) while never materializing the C(m,2) dyad projection
(17-49x blowup) and running ~10-60x faster wall. This lane lands that as a
production community metric:

  metric: bipartite_louvain_community  (entity -> community id)

Read-only by default; --apply persists to graph_analytics via
algorithms.write_analytics. Reads the live h_incidence star store through
the duckdb_lock.open_read_only funnel (same seam as hyper_trails). Degrades
cleanly when the star store is absent (pristine clone) - SKIP line, never a
crash. Deterministic default seed 42 (wired via
igraph.set_random_number_generator before multilevel).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DUCKDB_PATH = _PROJECT_ROOT / "memory" / "graph.duckdb"
METRIC = "bipartite_louvain_community"
SEED = 42


def _load_pairs() -> list[tuple[str, str]] | None:
    """DISTINCT (edge_id, entity_name) pairs from the live h_incidence star store.

    Read-only via duckdb_lock.open_read_only (same seam as hyper_trails).
    Returns None when the star store is absent (degrade, never block)."""
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
        return [("e:" + str(e), name) for e, name in rows]
    finally:
        con.close()


def bipartite_louvain_communities(pairs, *, seed: int = SEED) -> dict[str, int] | None:
    """Community id per entity from igraph multilevel (Louvain) over the bipartite graph.

    Returns {entity_name: cid} for entity nodes (hubs excluded). None if there
    are fewer than 2 entities (nothing meaningful to cluster)."""
    import igraph as ig

    entities = sorted({e for _, e in pairs})
    if len(entities) < 2:
        return None
    hubs = sorted({h for h, _ in pairs})
    e_idx = {e: i for i, e in enumerate(entities)}
    h_idx = {h: len(entities) + i for i, h in enumerate(hubs)}
    n = len(entities) + len(hubs)
    g = ig.Graph(n=n, edges=[(e_idx[e], h_idx[h]) for h, e in pairs], directed=False)
    g.es["weight"] = [1.0] * len(pairs)
    ig.set_random_number_generator(random.Random(seed))
    comm = g.community_multilevel(weights=g.es["weight"])
    return {e: int(comm.membership[e_idx[e]]) for e in entities}


def _community_report(communities) -> list[str]:
    """Compact per-block summary lines for the dry-run report."""
    from collections import Counter

    counts = Counter(communities.values())
    lines = [f"bipartite-louvain: {len(communities)} entities -> {len(counts)} communities"]
    for cid, n in sorted(counts.items()):
        lines.append(f"  block {cid}: {n} entities")
    return lines


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="persist to graph_analytics")
    p.add_argument("--seed", type=int, default=SEED)
    args = p.parse_args(argv)

    pairs = _load_pairs()
    if pairs is None:
        print("bipartite-communities: SKIP - h_incidence absent (star store not built)")
        return 0
    communities = bipartite_louvain_communities(pairs, seed=args.seed)
    if communities is None:
        print("bipartite-communities: SKIP - fewer than 2 entities in the incidence")
        return 0

    for line in _community_report(communities):
        print(line)

    if args.apply:
        from helpers.graph.algorithms import write_analytics

        n = write_analytics(METRIC, communities, replace_population=True)
        print(f"APPLY: wrote {n} rows to graph_analytics (metric {METRIC})")
    else:
        print("DRY-RUN (use --apply to persist to graph_analytics)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
