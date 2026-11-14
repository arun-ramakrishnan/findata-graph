#!/usr/bin/env python3
"""Q3 PPR edge-type attribution harness (helpers/graph/ppr_edge_attribution.py).

Synthetic sqlite store (graph_edges with typed dyads), exercising the same
``load_centrality_projection`` path the live harness uses. The graph is tiny
so the scipy fallback is instant; the assertions target *semantics* (edge
existence, coverage, determinism, metric range), never exact float values.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from helpers.graph.ppr_edge_attribution import (
    _resolve_cohort,
    _topk_others,
    run,
)
from helpers.graph.scipy_bridge import PPR_INCLUDE_EDGE_TYPES

# Triangle via competes_with (A-B, B-C) + a supplier chain (A->D, B->D)
# + semantic peers (C-E, D-E). E is a leaf reachable only through
# semantic_peer — a leave-one-type-out drop of semantic_peer orphans it.
_EDGES = [
    ("A", "B", "competes_with"),
    ("B", "C", "competes_with"),
    ("A", "D", "supplier_to"),
    ("B", "D", "supplier_to"),
    ("C", "E", "semantic_peer"),
    ("D", "E", "semantic_peer"),
]


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "synthetic.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, weight REAL)")
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, ?, 1.0)", _EDGES)
    con.commit()
    con.close()
    return db


def _report(store: Path, seeds=None, cohort_size=5, top_k=3):
    return run(
        store,
        seeds=seeds,
        cohort_size=cohort_size,
        top_k=top_k,
        alpha=0.85,
    )


def test_resolve_cohort_explicit_preserves_order_and_drops_missing(store):
    names = ["A", "B", "C", "D", "E"]
    chosen, dropped = _resolve_cohort(names, ["D", "A", "ZZZ"], 5)
    assert chosen == ["D", "A"]
    assert dropped == ["ZZZ"]


def test_resolve_cohort_even_spread_deterministic(store):
    names = ["A", "B", "C", "D", "E"]
    c1, _ = _resolve_cohort(names, None, 3)
    c2, _ = _resolve_cohort(names, None, 3)
    assert c1 == c2
    assert len(c1) <= 3


def test_topk_others_excludes_seed(store):
    names = ["A", "B", "C", "D", "E"]
    scores = [0.1, 0.9, 0.5, 0.3, 0.2]
    top = _topk_others(scores, names, "A", 3)
    assert "A" not in top
    assert top == ["B", "C", "D"]


def test_run_report_shape_and_coverage(store):
    r = _report(store, seeds=["A", "B", "C"], cohort_size=5)
    assert r["schema"] == "ppr_edge_attribution/v1"
    assert r["parameters"]["top_k"] == 3
    assert r["cohort"]["seeds"] == ["A", "B", "C"]
    assert set(r["ablation"].keys()) == set(PPR_INCLUDE_EDGE_TYPES)
    # every seeded ablation scores all three seeds (none orphaned in this graph)
    for block in r["ablation"].values():
        assert block["cohort"]["scored"] == 3
        assert block["cohort"]["dropped_seeds"] == []
    x = r["cross_projection"]
    assert x["projection"]["entities"] == 5
    assert 0.0 <= x["metrics"]["topk_jaccard_mean"] <= 1.0


def test_run_leaving_out_semantic_peer_orphans_E(store):
    r = _report(store, seeds=["A", "B", "C", "E"], cohort_size=5)
    block = r["ablation"]["semantic_peer"]
    # E is only reachable via semantic_peer -> orphaned in the ablation
    assert "E" in block["cohort"]["dropped_seeds"]
    assert block["cohort"]["scored"] == 3
    # the orphan gets an explicit coverage_ok=False (0 jaccard row)
    scored = [s for s in r["cohort"]["seeds"] if s in block["cohort"]["dropped_seeds"]]
    assert len(scored) == 1


def test_run_metrics_are_bounded_and_deterministic(store):
    r1 = _report(store, seeds=["A", "B", "C"], cohort_size=5)
    r2 = _report(store, seeds=["A", "B", "C"], cohort_size=5)
    # wall time + generated_at are run artefacts, not results
    for r in (r1, r2):
        r.pop("wall_seconds")
        r.pop("generated_at")
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    for block in r1["ablation"].values():
        m = block["metrics"]
        assert 0.0 <= m["topk_jaccard_mean"] <= 1.0
        assert 0.0 <= m["rank_displacement_norm_mean"] <= 1.0
        assert m["max_score_shift_mean"] >= 0.0
        assert m["seeds_scored"] == 3


def test_run_empty_cohort_raises(store):
    with pytest.raises(ValueError, match="empty cohort"):
        _report(store, seeds=["ZZZ"], cohort_size=5)


def test_run_cross_projection_diff_within_range(store):
    r = _report(store, seeds=["A", "B", "C"], cohort_size=5)
    x = r["cross_projection"]
    assert 0.0 <= x["metrics"]["topk_jaccard_mean"] <= 1.0
    assert x["metrics"]["seeds_scored"] == 3
