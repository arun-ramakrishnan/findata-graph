#!/usr/bin/env python3
"""D10 S2 — eval bank for hyper_predict lanes.

Two legs, both report recall@k per family per lane:

  1. Temporal split — train on incidences with valid_from < D (median),
     score at D, recall@k against post-D-formed hyperedges. The store's
     validity windows make this a real forward test.
  2. Retrospective holdout — mask a fraction of k>=3-family edges, rank,
     recall@k against the masked set.

Floors are recorded in the bank header; one-command re-measure (house bank
doctrine). Report-first: prints recall@k tables, does not mutate the store.

Usage::

    python3 helpers/bench/hyper_predict_bank.py
    python3 helpers/bench/hyper_predict_bank.py --sources edition,theme --k 10,50
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph.hyper_predict import (  # noqa: E402
    _candidates,
    _jaccard_scores,
    _s_walk_scores,
    _stationary_pi_scores,
)

PREDICTION_GRADE = (
    "edition",
    "theme",
    "sector",
    "industry",
    "sub_sector",
    "org_membership",
)


def _load_with_validity(source: str):
    """Load incidence rows with valid_from for one family."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute(
            f"ATTACH '{_PROJECT_ROOT / 'memory' / 'research.db'}' AS src (TYPE SQLITE, READ_ONLY)"
        )
        rows = con.execute(
            "SELECT he.label, hi.entity_name, he.valid_from "
            "FROM src.hyper_incidences hi JOIN src.hyper_edges he ON he.id = hi.edge_id "
            "WHERE he.edge_type = ? ORDER BY he.label, hi.entity_name",
            [source],
        ).fetchall()
    finally:
        con.close()
    # group by edge label -> set of nodes + valid_from
    edges: dict[str, set[str]] = defaultdict(set)
    edge_valid: dict[str, Any] = {}
    for label, node, vf in rows:
        edges[label].add(node)
        edge_valid[label] = vf
    return edges, edge_valid


def _node_edges_from(edges: dict[str, set[str]]) -> dict[str, set[str]]:
    ne: dict[str, set[str]] = defaultdict(set)
    for label, members in edges.items():
        for m in members:
            ne[m].add(label)
    return ne


def _score_family(edges, method, s, held_out: set[str] | None = None):
    """Build hypergraph from `edges` (excluding held_out labels) and score all
    pairs; return dict (u,v)->score."""
    if held_out:
        train_edges = {k: v for k, v in edges.items() if k not in held_out}
    else:
        train_edges = edges
    ne = _node_edges_from(train_edges)
    pairs = _candidates(ne)
    if method == "jaccard":
        return _jaccard_scores(ne, pairs)
    if method == "s_walk":
        return _s_walk_scores(ne, pairs, s)
    if method == "stationary_pi":
        from hypergraphx import Hypergraph

        hg = Hypergraph(edge_list=[tuple(sorted(m)) for m in train_edges.values()])
        return _stationary_pi_scores(hg, ne, pairs)
    raise ValueError(method)


def _recall_at_k(scores, truth_pairs, k):
    if not truth_pairs:
        return 0.0, 0, 0
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    top = {p for p, _ in ranked[:k]}
    hits = sum(1 for tp in truth_pairs if tp in top or (tp[1], tp[0]) in top)
    return hits / len(truth_pairs), hits, len(truth_pairs)


def temporal_split(source, method, s, ks, cutoff=None):
    edges, ev = _load_with_validity(source)
    if not edges:
        return None
    valids = [v for v in ev.values() if v is not None]
    if not valids:
        return None
    if cutoff is None:
        cutoff = sorted(valids)[len(valids) // 2]
    train = {k: v for k, v in edges.items() if ev[k] is not None and ev[k] < cutoff}
    held_labels = {k for k, v in edges.items() if ev[k] is not None and ev[k] >= cutoff}
    if not train or not held_labels:
        return None
    # ground truth pairs = pairs within held-out hyperedges
    truth_pairs = set()
    for lab in held_labels:
        members = sorted(edges[lab])
        for a, b in combinations(members, 2):
            truth_pairs.add((a, b))
    scores = _score_family(train, method, s)
    out = {
        "cutoff": str(cutoff),
        "train_edges": len(train),
        "held_edges": len(held_labels),
        "truth_pairs": len(truth_pairs),
    }
    for k in ks:
        r, h, t = _recall_at_k(scores, truth_pairs, k)
        out[f"recall@{k}"] = round(r, 4)
        out[f"hits@{k}"] = h
    return out


def retrospective_holdout(source, method, s, ks, frac=0.2, seed=42):
    edges, _ = _load_with_validity(source)
    if not edges:
        return None
    labels = list(edges)
    rng = random.Random(seed)  # noqa: S311 — deterministic seeded holdout split, not crypto
    rng.shuffle(labels)
    n_hold = max(1, int(len(labels) * frac))
    held = set(labels[:n_hold])
    train = {k: v for k, v in edges.items() if k not in held}
    truth_pairs = set()
    for lab in held:
        for a, b in combinations(sorted(edges[lab]), 2):
            truth_pairs.add((a, b))
    scores = _score_family(train, method, s)
    out = {
        "held_edges": len(held),
        "train_edges": len(train),
        "truth_pairs": len(truth_pairs),
    }
    for k in ks:
        r, h, t = _recall_at_k(scores, truth_pairs, k)
        out[f"recall@{k}"] = round(r, 4)
        out[f"hits@{k}"] = h
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sources", default=",".join(PREDICTION_GRADE))
    ap.add_argument("--methods", default="jaccard,s_walk,stationary_pi")
    ap.add_argument("--k", default="10,50,100")
    ap.add_argument("--s", type=int, default=2)
    args = ap.parse_args(argv)
    ks = [int(x) for x in args.k.split(",")]
    methods = [m.strip() for m in args.methods.split(",")]
    sources = [x.strip() for x in args.sources.split(",") if x.strip()]

    for source in sources:
        for method in methods:
            print(f"\n=== {source} / {method} ===")
            tr = temporal_split(source, method, args.s, ks)
            if tr:
                print("  temporal:", tr)
            else:
                print("  temporal: skipped (no validity windows on this family)")
            rh = retrospective_holdout(source, method, args.s, ks)
            if rh:
                print("  holdout: ", rh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
