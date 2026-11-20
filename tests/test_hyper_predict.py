"""Hermetic tests for the hyper-native link-prediction lanes
(hyper_predict, D10) and the eval bank's pure scoring core
(hyper_predict_bank, D10 S2 — harvest_promotions_and_census_writeup
S2c).

Synthetic incidence only — no live store (house convention). The bank's
DB-touching entry points (temporal_split / retrospective_holdout call
_load_with_validity) stay CLI-covered; everything pure below the DB
boundary is pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import helpers.bench.hyper_predict_bank as bank  # noqa: E402
import helpers.graph.hyper_predict as hp  # noqa: E402

# a: {e1, e2}, b: {e1, e2}, c: {e2}, d: {e3} — two components, one
# bridge-less singleton family
NODE_EDGES = {
    "a": {"e1", "e2"},
    "b": {"e1", "e2"},
    "c": {"e2"},
    "d": {"e3"},
}


def test_candidates_pairs_need_a_shared_hyperedge():
    pairs = hp._candidates(NODE_EDGES)
    got = {(a, b): shared for a, b, shared in pairs}
    assert got == {("a", "b"): 2, ("a", "c"): 1, ("b", "c"): 1}


def test_jaccard_scores_are_shared_over_union():
    scores = hp._jaccard_scores(NODE_EDGES, hp._candidates(NODE_EDGES))
    assert scores[("a", "b")] == 1.0  # 2 shared / 2 union
    assert abs(scores[("a", "c")] - 1 / 2) < 1e-9  # 1 shared / |{e1,e2}∪{e2}| = 2
    assert abs(scores[("b", "c")] - 1 / 2) < 1e-9


def test_s_walk_threshold_drops_weak_pairs():
    scores = hp._s_walk_scores(NODE_EDGES, hp._candidates(NODE_EDGES), s=2)
    assert set(scores) == {("a", "b")}  # only the 2-shared pair survives
    assert scores[("a", "b")] == 2.0


def test_components_split_on_connectivity():
    comps = hp._components_from_pairs(NODE_EDGES, hp._candidates(NODE_EDGES))
    sizes = sorted(len(c) for c in comps)
    assert sizes == [1, 3]  # {a,b,c} via e1/e2, {d} via e3 alone


def test_rank_orders_by_score_then_names_and_caps_top():
    scores = {("a", "b"): 0.5, ("a", "c"): 0.9, ("b", "c"): 0.5, ("c", "d"): 0.0}
    ranked = hp._rank(scores, top=3)
    assert [p[:2] for p in ranked] == [("a", "c"), ("a", "b"), ("b", "c")]


def test_stationary_pi_scores_tiny_hypergraph():
    from hypergraphx import Hypergraph

    hg = Hypergraph(edge_list=[("a", "b"), ("b", "c")])
    pairs = [("a", "b", 1), ("a", "c", 1), ("b", "c", 1)]  # _candidates triples
    scores = hp._stationary_pi_scores(hg, NODE_EDGES, pairs)
    # every candidate gets a non-negative product of stationary masses
    assert set(scores) == {("a", "b"), ("a", "c"), ("b", "c")}
    assert all(v >= 0.0 for v in scores.values())


def test_bank_node_edges_inverts_incidence():
    edges = {"e1": {"a", "b"}, "e2": {"b", "c"}}
    ne = bank._node_edges_from(edges)
    assert ne == {"a": {"e1"}, "b": {"e1", "e2"}, "c": {"e2"}}


def test_bank_recall_counts_reversed_pairs():
    scores = {("a", "b"): 0.9, ("c", "d"): 0.8, ("a", "c"): 0.7}
    truth = {("b", "a"), ("d", "c"), ("x", "y")}
    r, hits, total = bank._recall_at_k(scores, truth, k=2)
    assert (r, hits, total) == (2 / 3, 2, 3)  # reversed orientation counts


def test_bank_score_family_respects_holdout():
    edges = {
        "e1": {"a", "b"},
        "e2": {"b", "c"},
        "e3": {"a", "c"},  # held out: (a,c) is the truth pair
    }
    scores = bank._score_family(edges, "jaccard", s=2, held_out={"e3"})
    assert ("a", "c") not in scores and ("c", "a") not in scores
    # b carries both train edges: 1 shared / union 2 for each pair
    assert scores[("a", "b")] == 0.5 and scores[("b", "c")] == 0.5
