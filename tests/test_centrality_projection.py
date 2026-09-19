#!/usr/bin/env python3
"""Centrality projection semantics (graph_centrality_index_noise, 2026-09-19).

The nine Onager-backed centralities exclude ``listed_on_index`` on the
DB path (index hubs are membership lists, not mediators — with them in
the projection NIFTY SME EMERGE ranked betweenness #2 on the live
graph). Synthetic ``edges=`` lists and explicit ``edge_types`` bypass
the exclusion. See doc/improvements/proposals/graph_centrality_index_noise.md.
"""

from __future__ import annotations

import sqlite3
import duckdb
import pytest

from helpers.graph import query as gq
from helpers.graph import algorithms as alg

# Fixture graph: a relation triangle + a membership star over the same
# companies. IDX is a pure membership hub — exactly the artifact class
# the exclusion removes.
_REL = [("A", "B"), ("B", "C")]
_STAR = [("A", "IDX"), ("B", "IDX"), ("C", "IDX")]
# Synthetic edge lists are INT-id triples (ints -> int result keys).
_IDS = {n: i for i, n in enumerate(("A", "B", "C", "IDX"))}
_ALL = [(_IDS[s], _IDS[t], 1.0) for s, t in _REL + _STAR]


@pytest.fixture()
def con(tmp_path):
    sdb = tmp_path / "g.db"
    sql = sqlite3.connect(sdb)
    sql.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, weight REAL)")
    sql.executemany("INSERT INTO graph_edges VALUES (?, ?, 'competes_with', 1.0)", _REL)
    sql.executemany("INSERT INTO graph_edges VALUES (?, ?, 'listed_on_index', 1.0)", _STAR)
    sql.commit()
    sql.close()
    d = duckdb.connect()
    d.execute("LOAD sqlite;")
    d.execute("LOAD onager;")
    d.execute(f"ATTACH '{sdb}' AS fin (READ_ONLY);")
    gq.clear_graph_cache()  # closeness keys its P2.3 cache by generation
    yield d
    gq.clear_graph_cache()
    d.close()


def test_projection_resolver_excludes_membership(con):
    types = alg._centrality_edge_types(con)
    assert types == ["competes_with"]


def test_projection_resolver_falls_back_without_fin():
    bare = duckdb.connect()
    try:
        assert alg._centrality_edge_types(bare) is None
    finally:
        bare.close()


@pytest.mark.parametrize(
    "fn",
    [
        alg.degree_centrality,
        alg.closeness_centrality,
        alg.betweenness_centrality,
        alg.eigenvector_centrality,
        alg.harmonic_centrality,
        alg.laplacian_centrality,
        alg.local_reaching_centrality,
    ],
)
def test_db_path_excludes_membership_hub(con, fn):
    res = fn(con)
    assert "IDX" not in res, f"{fn.__name__}: membership hub leaked in"
    assert set(res) == {"A", "B", "C"}


def test_db_path_excludes_membership_hub_katz(con):
    # katz alpha pinned small (spectral radius safety on any projection)
    res = alg.katz_centrality(con, alpha=1e-4)
    assert "IDX" not in res
    assert set(res) == {"A", "B", "C"}


def test_db_path_louvain_excludes_membership_hub(con):
    res = alg.louvain_communities(con)
    assert "IDX" not in res.labels
    assert set(res.labels) == {"A", "B", "C"}


def test_db_path_louvain_modularity_excludes_membership(con):
    # runs through the same exclusion rule; smoke value check
    assert alg.compute_louvain_modularity(con) >= 0.0


def test_synthetic_louvain_bypasses_exclusion(con):
    res = alg.louvain_communities(con, edges=_ALL)
    assert _IDS["IDX"] in res.labels
    assert set(res.labels) == {_IDS[n] for n in ("A", "B", "C", "IDX")}


def test_db_path_voterank_excludes_membership_hub(con):
    seeds = alg.voterank_seeds(con)
    assert "IDX" not in seeds
    assert set(seeds) <= {"A", "B", "C"}


def test_synthetic_edges_bypass_exclusion(con):
    # Synthetic edges define their own graph: the star IS the input here.
    res = alg.closeness_centrality(con, edges=_ALL)
    assert _IDS["IDX"] in res
    assert set(res) == {_IDS[n] for n in ("A", "B", "C", "IDX")}
