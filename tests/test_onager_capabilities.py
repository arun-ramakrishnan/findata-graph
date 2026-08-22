"""Onager algorithm-engine contract tests (positive coverage).

Onager is a DuckDB community extension (Apache-2.0) adopted 2026-08-14 to
replace the old NetworkX bridge for `eigenvector_centrality`,
`closeness_centrality`, `betweenness_centrality`, `louvain_community`, and
`degree_centrality` — and, since Phase A of the duckpgq-retirement proposal
(doc/improvements/archive/graph/duckpgq_retirement.txt), also `pagerank`,
`weakly_connected_component`, and `local_clustering_coefficient`
(`onager_pagerank` / `onager_components` / `onager_clustering`).
duckpgq itself was fully retired 2026-08-14 (Phases A-E) — the property
graph is gone and every algorithm is Onager-backed.

These tests run WITHOUT the live research DB (synthetic edge lists), so they
are part of the default `make qa` gate (no `live`/`slow` marker).
"""

import math
import sqlite3
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from helpers.graph import onager as onager_mod

N = 4  # clique size for known-value checks


def _clique(n: int):
    """Bidirectional complete graph over 0..n-1."""
    edges = []
    for i in range(n):
        for j in range(n):
            if i != j:
                edges.append((i, j, 1.0))
    return edges


def _duckdb_over(db_path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("LOAD sqlite;")
    con.execute(f"ATTACH '{db_path}' AS fin (TYPE sqlite, READ_ONLY);")
    return con


# --------------------------------------------------------------------------- #
# Known-value checks on a small bidirectional clique
# --------------------------------------------------------------------------- #
def test_onager_eigenvector_clique():
    edges = _clique(N)
    res = onager_mod.onager_eigenvector(edges=edges)
    vals = [res[i] for i in range(N)]
    # Regular graph -> eigenvector centrality is uniform.
    assert all(abs(v - vals[0]) < 1e-9 for v in vals)
    # onager L2-normalises to unit norm.
    norm = sum(v * v for v in vals) ** 0.5
    assert abs(norm - 1.0) < 1e-9
    assert abs(vals[0] - 1.0 / (N ** 0.5)) < 1e-9


def test_onager_closeness_clique():
    edges = _clique(N)
    res = onager_mod.onager_closeness(edges=edges)
    # Complete graph -> every other node is 1 hop away -> closeness == 1.0.
    assert all(abs(res[i] - 1.0) < 1e-9 for i in range(N))


def test_onager_betweenness_clique():
    edges = _clique(N)
    res = onager_mod.onager_betweenness(edges=edges)
    # In a clique, every shortest path between a pair is the direct edge, so
    # no interior node carries betweenness.
    assert all(abs(res[i]) < 1e-9 for i in range(N))


def test_onager_degree_clique_bidirectional():
    edges = _clique(N)
    res = onager_mod.onager_degree(edges=edges)
    # Onager degree is undirected (reverse edges deduped), so each node has
    # n-1 neighbours -> degree == (n-1)/(n-1) == 1.0.
    assert all(abs(res[i] - 1.0) < 1e-9 for i in range(N))


def test_onager_louvain_clique_single_community():
    edges = _clique(N)
    labels, modularity = onager_mod.onager_louvain(edges=edges)
    # All nodes in one community.
    assert len(set(labels.values())) == 1
    assert set(labels.keys()) == set(range(N))


def test_onager_louvain_two_disjoint_triangles():
    edges = _clique(3) + [(i + 3, j + 3, 1.0) for i in range(3) for j in range(3) if i != j]
    labels, _ = onager_mod.onager_louvain(edges=edges)
    comms = set(labels.values())
    assert len(comms) == 2
    # Each node assigned exactly once.
    assert len(labels) == 6


# --------------------------------------------------------------------------- #
# Symmetry / undirected behaviour
# --------------------------------------------------------------------------- #
def test_onager_centrality_is_undirected():
    star = [(0, 1, 1.0), (0, 2, 1.0), (0, 3, 1.0)]  # hub at 0
    reversed_star = [(1, 0, 1.0), (2, 0, 1.0), (3, 0, 1.0)]
    for fn in (onager_mod.onager_eigenvector, onager_mod.onager_closeness,
               onager_mod.onager_betweenness):
        fwd = fn(edges=star)
        rev = fn(edges=reversed_star)
        # Onager centrality is undirected (reversing every edge leaves the
        # graph identical), but floating-point ordering makes exact equality
        # fragile across runs -> compare within tolerance.
        assert fwd == pytest.approx(rev)


# --------------------------------------------------------------------------- #
# Degree ordering on a single-direction path (values in [0, 1])
# --------------------------------------------------------------------------- #
def test_onager_degree_path_ordering():
    edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0)]
    res = onager_mod.onager_degree(edges=edges)
    n = 4
    # interior nodes have higher degree than endpoints
    assert res[1] == res[2] > res[0] == res[3]
    assert all(0.0 <= res[i] <= 1.0 for i in range(n))


# --------------------------------------------------------------------------- #
# Name-keyed production path over a SQLite graph (duckpgq-compatible schema)
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL
);
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL DEFAULT 'test',
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
"""


@pytest.fixture
def synth_db(tmp_path):
    db_path = str(tmp_path / "onager.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    for name in ["SectorA", "CompanyA", "CompanyB", "CompanyC"]:
        conn.execute(
            "INSERT OR IGNORE INTO entities(name, entity_type) VALUES (?, 'company')",
            (name,),
        )
    for src, tgt in [
        ("CompanyA", "SectorA"), ("SectorA", "CompanyA"),
        ("CompanyB", "SectorA"), ("SectorA", "CompanyB"),
        ("CompanyC", "SectorA"), ("SectorA", "CompanyC"),
    ]:
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES (?, ?, 'part_of', 'seed')",
            (src, tgt),
        )
    conn.commit()
    conn.close()
    return db_path


def test_onager_degree_name_keyed(synth_db):
    con = _duckdb_over(synth_db)
    try:
        res = onager_mod.onager_degree(con)
    finally:
        con.close()
    # Undirected degree: SectorA has 3 company neighbours, each Company has 1.
    assert set(res.keys()) == {"SectorA", "CompanyA", "CompanyB", "CompanyC"}
    assert abs(res["SectorA"] - 1.0) < 1e-9
    for c in ("CompanyA", "CompanyB", "CompanyC"):
        assert abs(res[c] - 1.0 / 3.0) < 1e-9
    assert res["SectorA"] > res["CompanyA"]


def test_onager_louvain_name_keyed(synth_db):
    con = _duckdb_over(synth_db)
    try:
        labels, modularity = onager_mod.onager_louvain(con)
    finally:
        con.close()
    assert set(labels.keys()) == {"SectorA", "CompanyA", "CompanyB", "CompanyC"}
    assert all(isinstance(v, int) for v in labels.values())
    assert -1.0 <= modularity <= 1.0


def test_onager_loads_in_extension_build():
    """Onager must be installable/loadable from the community repo."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import duckdb; c=duckdb.connect(); c.execute('INSTALL onager FROM community'); "
         "c.execute('LOAD onager'); print('ok')"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
        timeout=120,
    )
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr


# --------------------------------------------------------------------------- #
# Phase A additions: pagerank / components / clustering (duckpgq replacements)
# --------------------------------------------------------------------------- #
def test_onager_pagerank_clique_uniform():
    """Regular graph -> every node's PageRank is identical."""
    res = onager_mod.onager_pagerank(edges=_clique(N))
    vals = [res[i] for i in range(N)]
    assert all(abs(v - vals[0]) < 1e-9 for v in vals)
    # Onager normalises the scores to sum to 1.
    assert abs(sum(vals) - 1.0) < 1e-6


def test_onager_pagerank_is_undirected_hub_dominates():
    """Directed star 0->1..3: Onager (undirected, like its other centrality
    functions) still ranks the hub highest — unlike duckpgq's directed
    pagerank, which gave every company the identical teleport-floor score on
    the live BelongsTo graph."""
    star = [(0, 1, 1.0), (0, 2, 1.0), (0, 3, 1.0)]
    res = onager_mod.onager_pagerank(edges=star)
    assert res[0] == max(res.values())
    assert all(abs(res[i] - res[1]) < 1e-9 for i in (2, 3))  # leaves symmetric


def test_onager_components_two_disjoint_triangles():
    edges = _clique(3) + [(i + 3, j + 3, 1.0) for i in range(3) for j in range(3) if i != j]
    res = onager_mod.onager_components(edges=edges)
    assert len(res) == 6
    assert len(set(res.values())) == 2  # exactly two components
    assert all(isinstance(v, int) for v in res.values())


def test_onager_components_single_component_clique():
    res = onager_mod.onager_components(edges=_clique(N))
    assert len(set(res.values())) == 1


def test_onager_clustering_triangle_is_one():
    tri = [(0, 1, 1.0), (1, 2, 1.0), (2, 0, 1.0)]
    res = onager_mod.onager_clustering(edges=tri)
    assert all(abs(v - 1.0) < 1e-9 for v in res.values())


def test_onager_clustering_pendant_is_zero():
    """Triangle with a pendant vertex: triangle nodes 0/1 have cc 1.0, the
    junction 2 has 1/3, the pendant 3 has 0.0 (textbook values)."""
    brid = [(0, 1, 1.0), (1, 2, 1.0), (2, 0, 1.0), (2, 3, 1.0)]
    res = onager_mod.onager_clustering(edges=brid)
    assert abs(res[0] - 1.0) < 1e-9 and abs(res[1] - 1.0) < 1e-9
    assert abs(res[2] - 1.0 / 3.0) < 1e-9
    assert abs(res[3]) < 1e-9


def test_onager_metrics_empty_edge_list():
    assert onager_mod.onager_pagerank(edges=[]) == {}
    assert onager_mod.onager_components(edges=[]) == {}
    assert onager_mod.onager_clustering(edges=[]) == {}


def test_onager_pagerank_name_keyed_via_edge_types(synth_db):
    """DB path: edge_types filter resolves names (not ids) as keys."""
    con = _duckdb_over(synth_db)
    try:
        res = onager_mod.onager_pagerank(con, edge_types=["part_of"])
    finally:
        con.close()
    assert set(res.keys()) == {"SectorA", "CompanyA", "CompanyB", "CompanyC"}
    # Sector hub is undirected-PageRank-dominant over each leaf company.
    assert res["SectorA"] > res["CompanyA"]
    assert abs(sum(res.values()) - 1.0) < 1e-6


def test_onager_components_name_keyed_via_edge_types(synth_db):
    con = _duckdb_over(synth_db)
    try:
        res = onager_mod.onager_components(con, edge_types=["part_of"])
    finally:
        con.close()
    # Star membership graph -> one weakly-connected component.
    assert len(res) == 4
    assert len(set(res.values())) == 1


def test_onager_clustering_name_keyed_via_edge_types(synth_db):
    con = _duckdb_over(synth_db)
    try:
        res = onager_mod.onager_clustering(con, edge_types=["part_of"])
    finally:
        con.close()
    # Star: no node has two connected neighbours -> all coefficients 0.0.
    assert set(res.keys()) == {"SectorA", "CompanyA", "CompanyB", "CompanyC"}
    assert all(abs(v) < 1e-9 for v in res.values())


# --------------------------------------------------------------------------- #
# Link prediction (Phase 1, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
def _cycle4():
    """Bidirectional 4-cycle 0-1-2-3-0 (edges 0-1, 1-2, 2-3, 3-0)."""
    return [(0, 1, 1.0), (1, 0, 1.0), (1, 2, 1.0), (2, 1, 1.0),
            (2, 3, 1.0), (3, 2, 1.0), (0, 3, 1.0), (3, 0, 1.0)]


def test_link_prediction_excludes_existing_edges_and_self():
    pairs = onager_mod.onager_link_prediction(edges=_cycle4())
    edge_set = {frozenset((s, t)) for s, t, _w in _cycle4()}
    assert pairs
    for a, b, _s in pairs:
        assert a != b
        assert frozenset((a, b)) not in edge_set


def test_link_prediction_common_neighbors_cycle4():
    # Non-adjacent pairs (0,2) and (1,3) share both remaining nodes.
    pairs = onager_mod.onager_link_prediction(edges=_cycle4(), method="common-neighbors")
    assert {(a, b): s for a, b, s in pairs} == {(0, 2): 2.0, (1, 3): 2.0}


def test_link_prediction_jaccard_cycle4():
    pairs = onager_mod.onager_link_prediction(edges=_cycle4(), method="jaccard")
    # N(0)={1,3} == N(2)={1,3}: intersection == union -> 1.0.
    assert {(a, b): s for a, b, s in pairs} == {(0, 2): 1.0, (1, 3): 1.0}


def test_link_prediction_adamic_adar_cycle4():
    # Two common neighbours, each of degree 2 -> 1/ln(2) + 1/ln(2).
    expected = 2.0 / math.log(2.0)
    pairs = onager_mod.onager_link_prediction(edges=_cycle4(), method="adamic-adar")
    assert {(a, b): s for a, b, s in pairs} == pytest.approx(
        {(0, 2): expected, (1, 3): expected}
    )


def test_link_prediction_resource_alloc_cycle4():
    # 1/deg(1) + 1/deg(3) = 1/2 + 1/2.
    pairs = onager_mod.onager_link_prediction(edges=_cycle4(), method="resource-alloc")
    assert {(a, b): s for a, b, s in pairs} == pytest.approx(
        {(0, 2): 1.0, (1, 3): 1.0}
    )


def test_link_prediction_pref_attach_hub_pairs():
    # Hub 0 with leaves 1..4 plus one leaf-leaf edge 3-4. Degrees:
    # 0->4, 1->1, 2->1, 3->2, 4->2. Non-adjacent pairs and their deg
    # products: (1,3)=2 (1,4)=2 (2,3)=2 (2,4)=2 (1,2)=1; hub pairs and
    # (3,4) are existing edges -> excluded.
    edges = [(0, i, 1.0) for i in (1, 2, 3, 4)] + [(3, 4, 1.0)]
    pairs = onager_mod.onager_link_prediction(edges=edges, method="pref-attach", top=2)
    assert [(a, b, s) for a, b, s in pairs] == [(1, 3, 2.0), (1, 4, 2.0)]


def test_link_prediction_chain_top_limits_and_sorts():
    # Chain 0-1-2-3-4-5: pairs two hops apart share exactly one neighbour.
    edges = [(i, i + 1, 1.0) for i in range(5)]
    pairs = onager_mod.onager_link_prediction(edges=edges, method="common-neighbors")
    assert {(a, b) for a, b, _s in pairs} == {(0, 2), (1, 3), (2, 4), (3, 5)}
    top2 = onager_mod.onager_link_prediction(edges=edges, method="common-neighbors", top=2)
    assert len(top2) == 2
    assert all(s == 1.0 for _a, _b, s in top2)
    # Descending score; ties broken by ascending node order (deterministic).
    assert top2[0][:2] < top2[1][:2]


def test_link_prediction_empty_edges():
    assert onager_mod.onager_link_prediction(edges=[]) == []


def test_link_prediction_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown link-prediction method"):
        onager_mod.onager_link_prediction(edges=_cycle4(), method="nope")


def test_onager_link_prediction_name_keyed(synth_db):
    con = _duckdb_over(synth_db)
    try:
        # Explicit projection: the fixture only has part_of edges
        # (bidirectional Company{A,B,C} <-> SectorA).
        pairs = onager_mod.onager_link_prediction(
            con, edge_types=["part_of"], method="jaccard"
        )
    finally:
        con.close()
    # Company pairs share SectorA as their only neighbour -> J = 1/1 = 1.0;
    # Company<->SectorA pairs are existing edges -> excluded.
    assert {(a, b): s for a, b, s in pairs} == {
        ("CompanyA", "CompanyB"): 1.0,
        ("CompanyA", "CompanyC"): 1.0,
        ("CompanyB", "CompanyC"): 1.0,
    }


def test_onager_link_prediction_default_projection_non_membership(synth_db):
    # Default edge_types project the NON-membership types; the fixture has
    # none, so the default projection is empty -> no candidates.
    con = _duckdb_over(synth_db)
    try:
        assert onager_mod.onager_link_prediction(con) == []
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Whole-graph structural metrics (Phase 2, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
def test_graph_metrics_triangle_clique():
    m = onager_mod.onager_graph_metrics(edges=_clique(3))
    assert m["density"] == pytest.approx(1.0)
    assert m["diameter"] == 1
    assert m["radius"] == 1
    assert m["avg_path_length"] == pytest.approx(1.0)
    assert m["transitivity"] == pytest.approx(1.0)
    assert m["avg_clustering"] == pytest.approx(1.0)
    assert m["triangles"] == 1
    assert m["assortativity"] == pytest.approx(0.0)  # 2-regular


def test_graph_metrics_path4():
    m = onager_mod.onager_graph_metrics(edges=[(i, i + 1, 1.0) for i in range(3)])
    assert m["density"] == pytest.approx(0.5)  # 2*3/(4*3)
    assert m["diameter"] == 3
    assert m["radius"] == 2
    assert m["avg_path_length"] == pytest.approx(10.0 / 6.0)
    assert m["transitivity"] == pytest.approx(0.0)
    assert m["avg_clustering"] == pytest.approx(0.0)
    assert m["triangles"] == 0
    assort = m["assortativity"]
    assert assort is not None and -1.0 <= assort <= 1.0


def test_graph_metrics_4cycle():
    m = onager_mod.onager_graph_metrics(edges=_cycle4())
    assert m["density"] == pytest.approx(2.0 / 3.0)
    assert m["diameter"] == 2
    assert m["radius"] == 2
    assert m["avg_path_length"] == pytest.approx(8.0 / 6.0)
    assert m["triangles"] == 0
    assert m["assortativity"] == pytest.approx(0.0)  # regular graph


def test_graph_metrics_single_edge():
    m = onager_mod.onager_graph_metrics(edges=[(0, 1, 1.0)])
    assert m == {
        "density": 1.0, "diameter": 1, "radius": 1, "avg_path_length": 1.0,
        "transitivity": 0.0, "triangles": 0, "avg_clustering": 0.0,
        "assortativity": 0.0,
    }


def test_graph_metrics_disconnected_null_paths():
    # Two disjoint edges: density is still defined (2*2/(4*3)), but the
    # path-length metrics are NULL — Onager does not collapse components.
    m = onager_mod.onager_graph_metrics(edges=[(0, 1, 1.0), (2, 3, 1.0)])
    assert m["density"] == pytest.approx(1.0 / 3.0)
    assert m["diameter"] is None
    assert m["radius"] is None
    assert m["avg_path_length"] is None
    assert m["triangles"] == 0


def test_graph_metrics_empty_edges():
    assert onager_mod.onager_graph_metrics(edges=[]) == {}


def test_graph_metrics_weights_are_ignored():
    # Documented Onager caveat: every metric is unweighted.
    weighted = onager_mod.onager_graph_metrics(edges=[(0, 1, 5.0), (1, 2, 5.0)])
    plain = onager_mod.onager_graph_metrics(edges=[(0, 1, 1.0), (1, 2, 1.0)])
    assert weighted == plain


def test_graph_metrics_duplicate_directions_deduped():
    bidirectional = onager_mod.onager_graph_metrics(
        edges=[(0, 1, 1.0), (1, 0, 1.0), (1, 2, 1.0), (2, 1, 1.0)]
    )
    single = onager_mod.onager_graph_metrics(edges=[(0, 1, 1.0), (1, 2, 1.0)])
    assert bidirectional == single


def test_graph_metrics_name_keyed_star(synth_db):
    # SectorA <-> Company{A,B,C} (bidirectional part_of): 4 nodes, 3 unique
    # undirected edges, star topology.
    con = _duckdb_over(synth_db)
    try:
        m = onager_mod.onager_graph_metrics(con)
    finally:
        con.close()
    assert m["density"] == pytest.approx(0.5)  # 2*3/(4*3)
    assert m["diameter"] == 2
    assert m["radius"] == 1
    assert m["avg_path_length"] == pytest.approx(1.5)  # (3*1 + 3*2)/6
    assert m["triangles"] == 0
    assert m["transitivity"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Extra centralities (Phase 3, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
_STAR5 = [(0, i, 1.0) for i in range(1, 5)]  # center 0, 4 leaves
_PATH5 = [(i, i + 1, 1.0) for i in range(4)]  # chain 0-1-2-3-4
_CYCLE6 = [(i, (i + 1) % 6, 1.0) for i in range(6)]


def test_harmonic_star_and_path():
    res = onager_mod.onager_harmonic(edges=_STAR5)
    assert res[0] == pytest.approx(4.0)  # 4 neighbours at distance 1
    for leaf in (1, 2, 3, 4):
        assert res[leaf] == pytest.approx(2.5)  # 1 + 3 * 1/2
    res = onager_mod.onager_harmonic(edges=_PATH5)
    assert res[0] == pytest.approx(25.0 / 12.0)  # 1 + 1/2 + 1/3 + 1/4
    assert res[1] == pytest.approx(17.0 / 6.0)   # 1 + 1 + 1/2 + 1/3
    assert res[2] == pytest.approx(3.0)          # 2*1 + 2*(1/2)
    assert res[3] == pytest.approx(17.0 / 6.0)
    assert res[4] == pytest.approx(25.0 / 12.0)


def test_katz_star_exact_alpha01():
    # Exact star solution with alpha=0.1, beta=1:
    # x_leaf = (1+a)/(1-4a^2) = 1.1/0.96; x_center = 1 + 4a * x_leaf.
    res = onager_mod.onager_katz(edges=_STAR5, alpha=0.1, beta=1.0)
    assert res[0] == pytest.approx(1.0 + 0.4 * (1.1 / 0.96))
    for leaf in (1, 2, 3, 4):
        assert res[leaf] == pytest.approx(1.1 / 0.96)


def test_katz_alpha_pin_converges_on_hub_heavy_star():
    # Onager's default alpha=0.1 diverges once lambda_max > 10 (a 200-leaf
    # star has lambda_max = sqrt(200) ~ 14.1); the pinned 1e-4 default must
    # converge. Exact star solution: x_c = (1 + 200a)/(1 - 200a^2).
    big_star = [(0, i, 1.0) for i in range(1, 201)]
    with pytest.raises(duckdb.InvalidInputException, match="Convergence failed"):
        onager_mod.onager_katz(edges=big_star, alpha=0.1)
    res = onager_mod.onager_katz(edges=big_star)
    assert res[0] == pytest.approx(1.02 / (1.0 - 2e-6), abs=1e-4)
    assert res[1] == pytest.approx(1.0 + 1e-4 * res[0], abs=1e-5)


def test_katz_beta_scales_linearly():
    one = onager_mod.onager_katz(edges=_PATH5, alpha=0.1, beta=1.0)
    two = onager_mod.onager_katz(edges=_PATH5, alpha=0.1, beta=2.0)
    for k in one:
        assert two[k] == pytest.approx(2.0 * one[k])


def test_laplacian_star_and_path():
    # Qi et al.: X(v) = d(v)^2 + d(v) + 2*sum_{u in N(v)} d(u).
    res = onager_mod.onager_laplacian(edges=_STAR5)
    assert res[0] == pytest.approx(16.0 + 4.0 + 2.0 * 4.0)  # 28
    for leaf in (1, 2, 3, 4):
        assert res[leaf] == pytest.approx(1.0 + 1.0 + 2.0 * 4.0)  # 10
    res = onager_mod.onager_laplacian(edges=_PATH5)
    assert [res[i] for i in range(5)] == pytest.approx([6.0, 12.0, 14.0, 12.0, 6.0])


def test_local_reaching_two_hop_neighbourhood():
    # Onager's local reaching = |{u : d(v,u) <= 2}| (verified 2026-08-14).
    res = onager_mod.onager_local_reaching(edges=_STAR5)
    assert all(v == pytest.approx(5.0) for v in res.values())
    res = onager_mod.onager_local_reaching(edges=_PATH5)
    assert [res[i] for i in range(5)] == pytest.approx([3.0, 4.0, 5.0, 4.0, 3.0])
    res = onager_mod.onager_local_reaching(edges=_CYCLE6)
    assert all(v == pytest.approx(5.0) for v in res.values())


def test_voterank_seed_sets():
    # VoteRank's single output column IS the ranking; it stops when no
    # remaining node has a positive vote score (star -> only the center).
    assert onager_mod.onager_voterank(edges=_STAR5) == [0]
    assert onager_mod.onager_voterank(edges=_PATH5) == [1, 3]
    assert onager_mod.onager_voterank(edges=_PATH5, num_seeds=1) == [1]


def test_phase3_centralities_empty_edges():
    assert onager_mod.onager_harmonic(edges=[]) == {}
    assert onager_mod.onager_katz(edges=[]) == {}
    assert onager_mod.onager_laplacian(edges=[]) == {}
    assert onager_mod.onager_local_reaching(edges=[]) == {}
    assert onager_mod.onager_voterank(edges=[]) == []


def test_phase3_centralities_dedup_reverse_directions():
    # Same contract as the Phase 2 metrics: duplicate reverse edge rows
    # (e.g. part_of + has_company) collapse to one undirected edge.
    doubled = _STAR5 + [(i, 0, 1.0) for i in range(1, 5)]
    for fn in (onager_mod.onager_harmonic, onager_mod.onager_laplacian,
               onager_mod.onager_local_reaching):
        assert fn(edges=_STAR5) == fn(edges=doubled)
    assert (onager_mod.onager_katz(edges=_STAR5, alpha=0.1)
            == onager_mod.onager_katz(edges=doubled, alpha=0.1))
    assert onager_mod.onager_voterank(edges=_STAR5) == onager_mod.onager_voterank(edges=doubled)


def test_phase3_centralities_name_keyed_star(synth_db):
    con = _duckdb_over(synth_db)
    try:
        harm = onager_mod.onager_harmonic(con)
        lap = onager_mod.onager_laplacian(con)
        reach = onager_mod.onager_local_reaching(con)
        katz = onager_mod.onager_katz(con, alpha=0.1)
        seeds = onager_mod.onager_voterank(con)
    finally:
        con.close()
    # 3-leaf star: x_leaf = (1+a)/(1-3a^2) = 1.1/0.97; center = 1+3a*x_leaf.
    assert harm["SectorA"] == pytest.approx(3.0)
    assert all(harm[c] == pytest.approx(2.0) for c in ("CompanyA", "CompanyB", "CompanyC"))
    assert lap["SectorA"] == pytest.approx(18.0)  # 9 + 3 + 2*3
    assert all(lap[c] == pytest.approx(8.0) for c in ("CompanyA", "CompanyB", "CompanyC"))
    assert all(v == pytest.approx(4.0) for v in reach.values())
    assert katz["SectorA"] == pytest.approx(1.0 + 0.3 * (1.1 / 0.97))
    assert katz["CompanyA"] == pytest.approx(1.1 / 0.97)
    assert seeds == ["SectorA"]


def test_onager_louvain_labels_canonical_across_edge_order():
    """maint_full_zero_churn F3: community numbering must be a pure
    function of the partition. Shuffling the edge input — what a DuckDB
    rebuild effectively does to node iteration order — must not permute
    labels (the 2026-08-22 audit saw all 1,293 live labels change under a
    bit-identical modularity)."""
    import random

    # 4-clique (community of 4) + a disconnected pair (community of 2).
    edges = _clique(4) + [(10, 20, 1.0), (20, 10, 1.0)]

    labels_a, mod_a = onager_mod.onager_louvain(edges=edges)
    shuffled = edges[:]
    random.Random(42).shuffle(shuffled)  # noqa: S311  # deterministic non-crypto RNG (tests)
    labels_b, mod_b = onager_mod.onager_louvain(edges=shuffled)

    assert labels_a == labels_b
    assert mod_a == pytest.approx(mod_b)
    # Canonical numbering: larger community first -> clique is 0, pair is 1.
    assert {n for n, c in labels_a.items() if c == 0} == {0, 1, 2, 3}
    assert {n for n, c in labels_a.items() if c == 1} == {10, 20}
