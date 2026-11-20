#!/usr/bin/env python3
"""HypergraphMT lane (HGX unlock-shelf item #3) -- opt-in slow lane.

HypergraphMT is a mixed-membership probabilistic model. Default realizations=1
to keep runtime ~10-20s; full quality needs n_realizations>=10 (~100s+).

Standing (2026-11-20, harvest_promotions_and_census_writeup S2b):
RECORDED PROBE, unwired by design — no make target, no test, zero
importers outside doc/. The soft-membership matrix (u — one weight per
block per node) is the only graded-membership seat beyond MMSBM's
memberships; smoke (#549, sector, K=4, 1 realization): 1225 nodes in
13.3 s, 4 blocks (919/244/49/13). Wire (target + test) only when a
consumer needs mixed memberships at realizations>=10 quality.

Usage:
    python3 helpers/graph/hyper_hypergraphmt.py            # dry-run
    python3 helpers/graph/hyper_hypergraphmt.py --apply    # metric hypergraphmt_community
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse  # noqa: E402
from helpers.graph import hyper_arrow as ha  # noqa: E402

DEFAULT_SOURCES = ("sector", "theme", "industry")
METRIC = "hypergraphmt_community"


def _require_hgx():
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            from hypergraphx import Hypergraph
            from hypergraphx.communities.hypergraph_mt.model import HypergraphMT
    except ImportError as exc:
        raise ImportError("hypergraphx not installed") from exc
    return Hypergraph, HypergraphMT


def _exclude_degenerate(incidence, allow_giant=False, max_frac=0.25):
    n_nodes = len({m for members in incidence.values() for m in members})
    threshold = max(2.0, max_frac * n_nodes)
    kept = {}
    giants = []
    singles = []
    for label, members in incidence.items():
        if len(members) < 2:
            singles.append(label)
        elif len(members) > threshold and not allow_giant:
            giants.append(label)
        else:
            kept[label] = members
    return kept, giants, singles


def fit_hypergraphmt(incidence, k, seed=42, realizations=1, normalize=True):
    Hypergraph, HypergraphMT = _require_hgx()
    hg = Hypergraph(edge_list=[tuple(members) for members in incidence.values()])
    model = HypergraphMT(n_realizations=realizations, verbose=False)
    u, w, maxL = model.fit(hg, K=k, seed=seed, normalizeU=normalize)
    node2id = hg.get_mapping()._to_int
    id2node = {v: name for name, v in node2id.items()}
    out = {}
    for i in range(u.shape[0]):
        name = id2node[i]
        row = u[i]
        out[name] = {
            "block": int(row.argmax()),
            "memberships": {str(b): round(float(row[b]), 4) for b in range(u.shape[1])},
            "k": k,
            "seed": seed,
            "maxL": round(float(maxL), 4),
        }
    return out


def _report(memberships):
    blocks = {}
    for entity, payload in memberships.items():
        blocks.setdefault(payload["block"], []).append(entity)
    lines = [f"blocks: {len(blocks)}"]
    for b in sorted(blocks, key=lambda x: -len(blocks[x])):
        lines.append(f"  block {b:2d}: {len(blocks[b]):4d} nodes")
    return lines


def _cli(argv=None):
    p = argparse.ArgumentParser(description="HypergraphMT lane")
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--realizations", type=int, default=1)
    p.add_argument("--allow-giant", action="store_true")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    raw = ha.incidence_dict(ha.load_incidence_arrow(sources))
    kept, giants, singles = _exclude_degenerate(raw, allow_giant=args.allow_giant)
    if giants:
        print(f"WARNING: excluded {len(giants)} giant hyperedge(s)", file=sys.stderr)
    if not kept:
        print("SKIP: no hyperedges left")
        return 0
    print(f"incidence: {len(raw)} hyperedges, {len(kept)} kept")
    t0 = time.perf_counter()
    memberships = fit_hypergraphmt(kept, k=args.k, seed=args.seed, realizations=args.realizations)
    print(f"fit: {len(memberships)} nodes in {time.perf_counter() - t0:.2f}s")
    for line in _report(memberships):
        print(line)
    if args.apply:
        from helpers.graph.algorithms import write_analytics

        n = write_analytics(METRIC, memberships, replace_population=True)
        print(f"APPLY: wrote {n} rows (metric {METRIC})")
    else:
        print("DRY-RUN (use --apply to persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
