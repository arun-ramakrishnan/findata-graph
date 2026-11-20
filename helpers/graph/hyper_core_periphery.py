#!/usr/bin/env python3
"""Core-periphery lane (HGX unlock-shelf #2).

Per-node coreness in [0,1] via hypergraphx's core-periphery MLE — a
graded signal no other seat produces (MMSBM/HySC/HypergraphMT emit
blocks; this emits a coreness score).

Standing (2026-11-20, harvest_promotions_and_census_writeup S2b):
RECORDED PROBE, unwired by design — no make target, no test, zero
importers outside doc/. Smoke (#549, sector, n_iter=50): 1225 nodes in
14.8 s, top coreness ~1.0, 781/1225 >= 0.5 (near-degenerate on the
partition families). Wire (target + test) only when a consumer needs a
core/periphery ranking; until then run by hand:

    python3 helpers/graph/hyper_core_periphery.py            # dry-run
    python3 helpers/graph/hyper_core_periphery.py --apply    # metric core_periphery
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
METRIC = "core_periphery"


def _require_hgx():
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            from hypergraphx import Hypergraph
            from hypergraphx.communities.core_periphery.model import core_periphery
    except ImportError as exc:
        raise ImportError("hypergraphx not installed") from exc
    return Hypergraph, core_periphery


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


def fit_core_periphery(incidence, seed=42, n_iter=1000, greedy_start=False):
    Hypergraph, core_periphery = _require_hgx()
    hg = Hypergraph(edge_list=[tuple(members) for members in incidence.values()])
    scores = core_periphery(hg, greedy_start=greedy_start, N_ITER=n_iter, seed=seed)
    return {
        node: {"coreness": round(float(score), 6), "seed": seed, "n_iter": n_iter}
        for node, score in scores.items()
    }


def _report(memberships):
    lines = ["top-10 coreness:"]
    for node, payload in sorted(memberships.items(), key=lambda kv: (-kv[1]["coreness"], kv[0]))[
        :10
    ]:
        lines.append(f"  {payload['coreness']:.4f}  {node}")
    n_core = sum(1 for v in memberships.values() if v["coreness"] >= 0.5)
    lines.append(f"nodes with coreness>=0.5: {n_core} / {len(memberships)}")
    return lines


def _cli(argv=None):
    p = argparse.ArgumentParser(description="Core-periphery lane")
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-iter", type=int, default=1000)
    p.add_argument("--greedy-start", action="store_true")
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
    memberships = fit_core_periphery(
        kept, seed=args.seed, n_iter=args.n_iter, greedy_start=args.greedy_start
    )
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
