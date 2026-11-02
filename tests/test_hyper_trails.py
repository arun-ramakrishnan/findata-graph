"""Hermetic tests for the repo-native bipartite BFS trail lane
(helpers/graph/hyper_trails.py, proposal
hyper_trail_bipartite_bfs_lane).

Ground truth is networkx shortest paths over the same bipartite
incidence graph: trail hops == bipartite distance / 2, even steps
only. Toy duckdb fixtures cover the DISTINCT/absence SQL paths; no
live store.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import networkx as nx
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.graph.hyper_trails import (  # noqa: E402
    bipartite_from_pairs,
    build_bipartite,
    census,
    trail_distances,
    trail_source_stats,
)

CHAIN3 = [("e:1", "a"), ("e:1", "b"), ("e:2", "b"), ("e:2", "c"), ("e:3", "c"), ("e:3", "d")]
STAR = [("e:9", "a"), ("e:9", "b"), ("e:9", "c")]
SPLIT = [("e:1", "a"), ("e:1", "b"), ("e:2", "c"), ("e:2", "d")]
CHAIN5 = [
    ("e:1", "a"),
    ("e:1", "b"),
    ("e:2", "b"),
    ("e:2", "c"),
    ("e:3", "c"),
    ("e:3", "d"),
    ("e:4", "d"),
    ("e:4", "e"),
    ("e:5", "e"),
    ("e:5", "f"),
]


def _nx_hops(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """Ground truth: networkx shortest path over the same bipartite graph.

    Entity-entity hop count = nx shortest path length // 2, odd lengths
    (mid-hop) excluded — mirrors _trail_bfs exactly.
    """
    g = nx.Graph()
    g.add_edges_from(pairs)
    ents = sorted({e for _, e in pairs})
    out: dict[tuple[str, str], int] = {}
    for i, s in enumerate(ents):
        for t in ents[i + 1 :]:
            d = nx.shortest_path_length(g, s, t)
            if d % 2 == 0:
                out[(s, t)] = d // 2
    return out


def _toy_con(rows: list[tuple[int, str]]):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE h_incidence(edge_id INTEGER, entity_name VARCHAR)")
    con.executemany("INSERT INTO h_incidence VALUES (?, ?)", rows)
    return con


def test_chain_distances_match_networkx_zero_mismatches():
    g = bipartite_from_pairs(CHAIN3)
    got = trail_distances(g, ["a", "b", "c", "d"])
    truth = _nx_hops(CHAIN3)
    mismatches = [
        (pair, truth[pair], got[s].get(t))
        for pair in truth
        for s, t in [pair]
        if got[s].get(t) != truth[pair]
    ]
    assert mismatches == []
    assert got["a"]["d"] == 3  # a-e1-b-e2-c-e3-d: 6 bipartite steps = 3 hops


def test_star_pairwise_one_hop():
    g = bipartite_from_pairs(STAR)
    got = trail_distances(g, ["a", "b", "c"])
    assert got["a"]["b"] == 1 and got["a"]["c"] == 1 and got["b"]["c"] == 1


def test_arcs_guard_two_x_distinct_pairs():
    # duplicate incidence (b inside e:1 twice) collapses at DISTINCT
    dup = CHAIN3 + [("e:1", "b")]
    g = bipartite_from_pairs(dup)
    assert g["pairs"] == len(set(dup))
    assert g["arcs"] == 2 * g["pairs"]  # a silent clique growth is impossible


def test_limit_truncates_and_semantics():
    g = bipartite_from_pairs(CHAIN5)
    full = trail_distances(g, ["a"])  # default limit 40 reaches f (10 steps)
    assert full["a"]["f"] == 5
    cut = trail_distances(g, ["a"], limit=8)  # 8 steps = 4 hops: f is out
    assert "f" not in cut["a"]
    assert cut["a"]["e"] == 4
    odd = trail_distances(g, ["a"], limit=1)  # only hub spokes inside 1 step
    assert odd["a"] == {}  # odd bipartite distances end mid-hop: dropped


def test_stats_report_limit():
    g = bipartite_from_pairs(STAR)
    st = trail_source_stats(g, "a")
    assert st["limit"] == 40  # outputs are lower bounds: the limit travels
    assert st["reachable"] == 2


def test_absent_store_degrades_none():
    con = duckdb.connect(":memory:")  # no h_incidence table
    assert build_bipartite(con) is None


def test_build_from_store_distinct_pairs():
    con = _toy_con([(1, "a"), (1, "b"), (2, "b"), (2, "c"), (1, "b")])  # dup row
    g = build_bipartite(con)
    assert g["pairs"] == 4
    assert g["arcs"] == 8
    got = trail_distances(g, ["a"])
    assert got["a"]["c"] == 2  # a-e1-b-e2-c


def test_unknown_source_raises():
    g = bipartite_from_pairs(STAR)
    with pytest.raises(ValueError):
        trail_distances(g, ["nope"])


def test_census_two_components():
    g = bipartite_from_pairs(SPLIT)
    c = census(g)
    assert c["components"] == 2
    assert c["entities"] == 4
    assert c["hubs"] == 2
    assert c["largest_component_entities"] == 2
    assert c["largest_fraction"] == 0.5


def test_census_single_giant_component():
    g = bipartite_from_pairs(CHAIN5)
    c = census(g)
    assert c["components"] == 1
    assert c["largest_fraction"] == 1.0


def test_hub_prefix_no_collision():
    # an entity literally named like a hub stays a separate node
    pairs = [("e:1", "a"), ("e:1", "e:1")]
    g = bipartite_from_pairs(pairs)
    assert g["n_entities"] == 2 and g["n_hubs"] == 1
    assert g["arcs"] == 4


def test_live_store_smoke_guard():
    """The one live-store touch: build + census only (no per-source work).

    Skipped when the star store is absent — degrades, never blocks.
    """
    g = build_bipartite()
    if g is None:
        pytest.skip("h_incidence absent (star store not built)")
    c = census(g)
    assert c["arcs"] == 2 * g["pairs"]
    assert c["entities"] == g["n_entities"]


def test_self_is_excluded():
    g = bipartite_from_pairs(STAR)
    got = trail_distances(g, ["a"])
    assert "a" not in got["a"]


def test_odd_limit_mid_hop_boundary():
    g = bipartite_from_pairs(CHAIN3)
    # limit=5: a->d needs 6 steps; a->c needs 4. 5-step cutoff keeps c, drops d
    got = trail_distances(g, ["a"], limit=5)
    assert got["a"]["c"] == 2
    assert "d" not in got["a"]
