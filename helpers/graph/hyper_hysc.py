#!/usr/bin/env python3
"""HySC (hypergraph spectral clustering) community lane."""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import hyper_arrow as ha  # noqa: E402

DEFAULT_SOURCES = ("sector", "theme", "industry")
METRIC = "hysc_community"


def _require_hgx():
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            from hypergraphx import Hypergraph
            from hypergraphx.communities.hy_sc.model import HySC
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "hypergraphx is not installed in this interpreter "
            "(pyproject dependency since hypergraph_incidence_hyx)."
        ) from exc
    return Hypergraph, HySC


def _exclude_degenerate(incidence, *, allow_giant, max_frac=0.25):
    n_nodes = len({m for members in incidence.values() for m in members})
    threshold = max(2.0, max_frac * n_nodes)
    kept, giants, singles = {}, [], []
    for label, members in incidence.items():
        if len(members) < 2:
            singles.append(label)
        elif len(members) > threshold and not allow_giant:
            giants.append(label)
        else:
            kept[label] = members
    return kept, giants, singles


def fit_hysc(incidence, *, k, seed=42):
    Hypergraph, HySC = _require_hgx()
    hg = Hypergraph(edge_list=[tuple(members) for members in incidence.values()])
    model = HySC(seed=seed)
    u = model.fit(hg, K=k)
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
        }
    return out


def _block_report(memberships, incidence):
    label_of = {}
    for label, members in incidence.items():
        for m in members:
            label_of.setdefault(m, []).append(label)
    blocks = {}
    for entity, payload in memberships.items():
        blocks.setdefault(int(payload["block"]), []).append(entity)
    lines = ["blocks: %d (K=%d)" % (len(blocks), next(iter(memberships.values()))["k"])]
    for b in sorted(blocks, key=lambda x: (-len(blocks[x]), x)):
        members_b = blocks[b]
        cats = Counter(c for m in members_b for c in label_of.get(m, [])).most_common(3)
        cat_txt = ", ".join("%s (%d)" % (c, n) for c, n in cats)
        lines.append("  block %2d: %4d companies | %s" % (b, len(members_b), cat_txt))
    return lines


def _cli(argv=None):
    p = argparse.ArgumentParser(description="HySC community lane")
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--allow-giant", action="store_true")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    n_store = ha.store_rowcount(sources)
    if not n_store:
        print("[hyper-hysc] incidence store empty for sources (%s) - skipping" % ", ".join(sources))
        return 0
    raw = ha.incidence_dict(ha.load_incidence_arrow(sources))
    kept, giants, singles = _exclude_degenerate(raw, allow_giant=args.allow_giant)
    if giants:
        print("WARNING: excluded %d giant hyperedge(s): %s" % (len(giants), ", ".join(sorted(giants)[:5])), file=sys.stderr)
    if singles:
        print("NOTE: dropped %d singleton hyperedge(s)" % len(singles), file=sys.stderr)
    if not kept:
        print("SKIP: no hyperedges left after degeneracy guard")
        return 0
    n_members = sum(len(v) for v in kept.values())
    print("incidence: %d hyperedges loaded, %d kept (%d memberships over %d entities; sources: %s)" % (len(raw), len(kept), n_members, len({m for v in kept.values() for m in v}), ", ".join(sources)))

    t0 = time.perf_counter()
    memberships = fit_hysc(kept, k=args.k, seed=args.seed)
    print("fit: %d nodes in %.2fs" % (len(memberships), time.perf_counter() - t0))
    for line in _block_report(memberships, kept):
        print(line)

    if args.apply:
        from helpers.graph.algorithms import write_analytics
        n = write_analytics(METRIC, memberships, replace_population=True)
        print("APPLY: wrote %d rows to graph_analytics (metric %s)" % (n, METRIC))
    else:
        print("DRY-RUN (use --apply to persist to graph_analytics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
