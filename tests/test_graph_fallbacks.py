#!/usr/bin/env python3
"""Contract tests for the onager-free fallback lanes.

``helpers/graph/graph_fallbacks.py`` must reproduce ``helpers/graph/onager.py``'s
documented semantics using in-tree engines (igraph / numpy / pure Python)
because the onager extension has no duckdb-2.0 distribution
(completed.md #427). Two oracles:

* the hand-worked vectors that onager's own docstrings pin (5-node star,
  P5, C6) — these are the contract, not an approximation;
* networkx, the engine onager replaced, for whole-vector parity on random
  graphs.

Every call must also be *non-aborting*: an absent extension is the normal
case on this box, so no test may need it to pass.
"""

from __future__ import annotations

import itertools
import math
import os
import random

import duckdb
import pytest

import helpers.graph.graph_fallbacks as fb
from helpers.graph import onager as on

# --------------------------------------------------------------------------- #
# graphs + helpers
# --------------------------------------------------------------------------- #

STAR = [(0, 1, 1.0), (0, 2, 1.0), (0, 3, 1.0), (0, 4, 1.0)]
P5 = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0), (3, 4, 1.0)]
C6 = [(i, (i + 1) % 6, 1.0) for i in range(6)]
TRIANGLE = [(0, 1, 1.0), (1, 2, 1.0), (0, 2, 1.0)]


def _close(actual: dict, expected: dict, tol: float = 1e-4) -> None:
    assert set(actual) == set(expected), (sorted(actual), sorted(expected))
    for key, value in expected.items():
        assert actual[key] == pytest.approx(value, abs=tol), (key, actual[key], value)


def _nx(edges: list[tuple[int, int, float]]):
    import networkx as nx

    graph = nx.Graph()
    for a, b, _w in edges:
        graph.add_edge(a, b)
    return nx, graph


# --------------------------------------------------------------------------- #
# hand-worked vectors (onager docstrings)
# --------------------------------------------------------------------------- #


def test_degree_matches_onager_vector():
    _close(fb.onager_degree(edges=P5), {0: 0.25, 1: 0.5, 2: 0.5, 3: 0.5, 4: 0.25})


def test_laplacian_closed_form():
    # X(v) = d(v)^2 + d(v) + 2*sum_{u in N(v)} d(u) — Qi et al. 2012.
    _close(fb.onager_laplacian(edges=STAR), {0: 28.0, 1: 10.0, 2: 10.0, 3: 10.0, 4: 10.0})
    _close(fb.onager_laplacian(edges=P5), {0: 6.0, 1: 12.0, 2: 14.0, 3: 12.0, 4: 6.0})


def test_local_reaching_is_two_hop_size():
    _close(fb.onager_local_reaching(edges=P5), {0: 3.0, 1: 4.0, 2: 5.0, 3: 4.0, 4: 3.0})
    _close(fb.onager_local_reaching(edges=C6), {i: 5.0 for i in range(6)})
    _close(fb.onager_local_reaching(edges=STAR), {i: 5.0 for i in range(5)})


def test_harmonic_hand_vector():
    _close(
        fb.onager_harmonic(edges=STAR),
        {0: 4.0, 1: 2.5, 2: 2.5, 3: 2.5, 4: 2.5},
    )
    # P5 centre: 1+1+1/2+1/2 = 3.0. onager.py:1185 prints 3.5 there, but its
    # own formula (score = sum of 1/d) and networkx both give 3.0 — the
    # docstring vector is the typo, the formula is the contract.
    _close(
        fb.onager_harmonic(edges=P5),
        {0: 25 / 12, 1: 17 / 6, 2: 3.0, 3: 17 / 6, 4: 25 / 12},
    )


def test_voterank_seed_order():
    assert fb.onager_voterank(edges=STAR) == [0]
    assert fb.onager_voterank(edges=P5) == [1, 3]


def test_clustering_star_is_zero():
    _close(fb.onager_clustering(edges=STAR), {i: 0.0 for i in range(5)})
    _close(fb.onager_clustering(edges=TRIANGLE), {i: 1.0 for i in range(3)})


def test_components_labels_group_correctly():
    labels = fb.onager_components(edges=[(0, 1, 1.0), (2, 3, 1.0)])
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_empty_projection_never_raises():
    assert fb.onager_pagerank() == {}
    assert fb.onager_betweenness() == {}
    assert fb.onager_closeness() == {}
    assert fb.onager_eigenvector() == {}
    assert fb.onager_harmonic() == {}
    assert fb.onager_katz() == {}
    assert fb.onager_laplacian() == {}
    assert fb.onager_local_reaching() == {}
    assert fb.onager_degree() == {}
    assert fb.onager_components() == {}
    assert fb.onager_clustering() == {}
    assert fb.onager_graph_metrics() == {}
    assert fb.onager_voterank() == []
    assert fb.onager_link_prediction() == []


# --------------------------------------------------------------------------- #
# networkx parity on random graphs
# --------------------------------------------------------------------------- #


def _random_graph(seed: int, nodes: int = 12, edge_prob: float = 0.35):
    rng = random.Random(seed)
    edges = []
    for a, b in itertools.combinations(range(nodes), 2):
        if rng.random() < edge_prob:
            edges.append((a, b, 1.0))
    return edges


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_pagerank_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    expected = nx.pagerank(graph, alpha=0.85, weight=None)
    _close(fb.onager_pagerank(edges=edges, weighted=False), expected, tol=1e-5)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_betweenness_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    _close(fb.onager_betweenness(edges=edges), nx.betweenness_centrality(graph), tol=1e-6)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_closeness_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    _close(fb.onager_closeness(edges=edges), nx.closeness_centrality(graph), tol=1e-6)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_eigenvector_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    _close(fb.onager_eigenvector(edges=edges), nx.eigenvector_centrality(graph), tol=1e-4)


def test_eigenvector_is_deterministic_across_calls():
    """ARPACK's start vector is random, so igraph returns ULP-different
    vectors per call; the stamp lane and a live compute must still agree
    (tests/test_centrality_cache.py compares them for equality)."""
    edges = _random_graph(7)
    first = fb.onager_eigenvector(edges=edges)
    second = fb.onager_eigenvector(edges=edges)
    assert first == second
    assert max(first.values()) > 0  # sign canonicalised, not flipped


def test_louvain_is_deterministic_across_process_rng_state():
    """igraph's community_multilevel reads the process-global RNG with no seed
    parameter, so a degenerate-modularity tie resolved differently depending on
    how much randomness the process had already consumed (measured: same edges,
    same modularity 0.2245, different community labels). The stamp lane and a
    live compute must agree (tests/test_centrality_cache.py compares them for
    equality), so the RNG stream is pinned inside onager_louvain."""
    # C6 plus a pendant: two equal modularity splits, so the tie is degenerate.
    edges = [
        (0, 1, 1.0),
        (1, 2, 1.0),
        (2, 3, 1.0),
        (3, 4, 1.0),
        (4, 5, 1.0),
        (5, 0, 1.0),
        (3, 6, 1.0),
    ]

    def run(perturb):
        random.seed(perturb)
        fb.ig.set_random_number_generator(random.Random(perturb))
        return fb.onager_louvain(edges=edges)

    runs = [run(n) for n in (0, 99, 12345)]
    first_labels, first_mod = runs[0]
    assert all(labels == first_labels and abs(mod - first_mod) < 1e-9 for labels, mod in runs)
    # onager_louvain must leave the global RNG alone for everyone else.
    random.seed(0)
    before = random.random()
    random.seed(0)
    fb.onager_louvain(edges=edges)
    assert random.random() == before


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_harmonic_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    _close(fb.onager_harmonic(edges=edges), nx.harmonic_centrality(graph), tol=1e-6)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_degree_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    _close(fb.onager_degree(edges=edges), nx.degree_centrality(graph))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_clustering_matches_networkx(seed):
    edges = _random_graph(seed)
    nx, graph = _nx(edges)
    _close(fb.onager_clustering(edges=edges), nx.clustering(graph), tol=1e-6)


def test_louvain_labels_partition_and_modularity_matches():
    edges = _random_graph(5, nodes=16, edge_prob=0.4)
    labels, modularity = fb.onager_louvain(edges=edges)
    nx, graph = _nx(edges)
    partition = {node: labels[node] for node in graph}
    assert modularity == pytest.approx(
        nx.algorithms.community.modularity(
            graph,
            [{n for n, c in partition.items() if c == label} for label in set(partition.values())],
        ),
        abs=1e-6,
    )


def test_katz_matches_networkx():
    # NetworkX normalises katz by default; the fallback returns onager's raw
    # beta + alpha*A x values (the pinned-alpha contract), so compare shapes.
    edges = _random_graph(7)
    nx, graph = _nx(edges)
    raw = fb.onager_katz(edges=edges, alpha=0.05)
    norm = math.sqrt(sum(v * v for v in raw.values()))
    _close(
        {k: v / norm for k, v in raw.items()},
        nx.katz_centrality(graph, alpha=0.05, beta=1.0),
        tol=1e-4,
    )


def test_katz_returns_raw_values_not_normalised():
    # The live contract: alpha=1e-4, beta=1 -> score ~ 1 + alpha*degree.
    star = [(0, 1, 1.0), (0, 2, 1.0), (0, 3, 1.0)]
    scores = fb.onager_katz(edges=star)
    assert scores[0] == pytest.approx(1.0003, abs=2e-5)
    assert scores[1] == pytest.approx(1.0001, abs=2e-5)


def test_graph_metrics_matches_networkx():
    edges = _random_graph(9, nodes=10, edge_prob=0.5)
    nx, graph = _nx(edges)
    actual = fb.onager_graph_metrics(edges=edges)
    assert actual["density"] == pytest.approx(nx.density(graph), abs=1e-9)
    assert actual["triangles"] == pytest.approx(sum(nx.triangles(graph).values()) / 3.0)
    assert actual["transitivity"] == pytest.approx(nx.transitivity(graph), abs=1e-9)
    assert actual["avg_clustering"] == pytest.approx(nx.average_clustering(graph), abs=1e-9)


def test_pagerank_weighted_is_weight_sensitive():
    weighted = [(0, 1, 9.0), (0, 2, 1.0), (0, 3, 1.0), (0, 4, 1.0)]
    hot = fb.onager_pagerank(edges=weighted, weighted=True)
    flat = fb.onager_pagerank(edges=weighted, weighted=False)
    assert hot[1] > hot[2]
    assert flat[1] == pytest.approx(flat[2])


# --------------------------------------------------------------------------- #
# link prediction
# --------------------------------------------------------------------------- #


def test_link_prediction_shared_methods_rank_by_shared_neighbours():
    # 0 and 1 share neighbours 3 and 4; 0 and 5 share only 4.
    edges = [(0, 3, 1.0), (0, 4, 1.0), (1, 3, 1.0), (1, 4, 1.0), (5, 4, 1.0), (2, 6, 1.0)]
    ranked = fb.onager_link_prediction(edges=edges, method="common-neighbors", allow_all_pairs=True)
    assert ranked
    top = ranked[0]
    assert {top[0], top[1]} == {0, 1}
    assert top[2] == 2.0
    scores = {(a, b): s for a, b, s in ranked}
    assert scores[(0, 5)] == 1.0


def test_link_prediction_jaccard_between_zero_and_one():
    edges = [(0, 3, 1.0), (0, 4, 1.0), (1, 3, 1.0), (1, 4, 1.0)]
    for _a, _b, score in fb.onager_link_prediction(
        edges=edges, method="jaccard", allow_all_pairs=True
    ):
        assert 0.0 < score <= 1.0


def test_link_prediction_pref_attach_uses_degree_product():
    # 5-star: every non-existing pair is two leaves (degree 1 each) → 1.0.
    # The hub's pairs all exist, so its 4×d products never surface.
    star = [(0, i, 1.0) for i in range(1, 5)]
    ranked = fb.onager_link_prediction(edges=star, method="pref-attach", allow_all_pairs=True)
    assert ranked
    assert all(score == 1.0 for _a, _b, score in ranked)


def test_link_prediction_unknown_method_raises():
    with pytest.raises(ValueError):
        fb.onager_link_prediction(edges=P5, method="nope")


# --------------------------------------------------------------------------- #
# dispatch + scope
# --------------------------------------------------------------------------- #


def test_dispatch_uses_fallback_when_onager_raises(monkeypatch):
    monkeypatch.setattr(fb, "onager_available", lambda: True)

    def boom(*_a, **_kw):
        raise fb.OnagerUnavailable("onager extension unavailable: test")

    monkeypatch.setattr("helpers.graph.onager.onager_pagerank", boom)
    got = fb.onager_pagerank(edges=P5, weighted=False)
    _close(got, {0: 0.1345, 1: 0.2459, 2: 0.2391, 3: 0.2459, 4: 0.1345})


def test_with_onager_connection_scope_never_loads_the_extension():
    with fb.with_onager_connection() as con:
        assert con.execute("SELECT 42").fetchone()[0] == 42


def test_available_probe_is_a_bool():
    assert isinstance(fb.onager_available(), bool)


def test_link_prediction_top_k_respected():
    edges = _random_graph(11, nodes=9, edge_prob=0.3)
    ranked = fb.onager_link_prediction(edges=edges, method="jaccard", top=3, allow_all_pairs=True)
    assert len(ranked) <= 3
    scores = [score for _a, _b, score in ranked]
    assert scores == sorted(scores, reverse=True)


def test_math_import_contract():
    """Laplacian/harmonic/louvain results stay finite (no inf/nan leaks)."""
    edges = _random_graph(13, nodes=14, edge_prob=0.25)
    for result in (fb.onager_laplacian(edges=edges), fb.onager_harmonic(edges=edges)):
        assert all(math.isfinite(value) for value in result.values())


# --------------------------------------------------------------------------- #
# vendored-extension resolution (2026-10-24, onager_vendored_source_build)
# --------------------------------------------------------------------------- #
class TestVendoredResolution:
    """``_load_onager`` resolves distribution-first, vendored-second; the
    failure paths are deterministic regardless of whether the artifact is
    built, the success path is the LOAD canary for a stale-ABI trip.

    The degradation tests must neutralise the DISTRIBUTION arm: a trusted
    copy under ~/.duckdb/extensions (2026-10-24: upstream dist artifact,
    community-repo install) makes bare ``LOAD onager`` succeed, so each
    failure-path test runs against an empty fake HOME where the
    distribution arm 404s and the vendored arm decides."""

    @staticmethod
    def _empty_home(tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

    def test_flagless_con_degrades_on_untrusted_path(self, tmp_path, monkeypatch):
        # allow_unsigned_extensions is a CONNECT-TIME config in duckdb 2.0 —
        # a plain con must NOT load the vendored file (it is unsigned and,
        # unlike a copy under the extensions dir, no install metadata
        # blesses it). With no trusted distribution either, the whole
        # resolution degrades to OnagerUnavailable.
        self._empty_home(tmp_path, monkeypatch)
        con = duckdb.connect()
        try:
            with pytest.raises(on.OnagerUnavailable):
                on._load_onager(con)
        finally:
            con.close()

    def test_junk_vendored_file_degrades(self, tmp_path, monkeypatch):
        junk = tmp_path / "not_an_extension.duckdb_extension"
        junk.write_bytes(b"garbage")
        self._empty_home(tmp_path, monkeypatch)
        monkeypatch.setenv("ONAGER_VENDORED_EXTENSION", str(junk))
        con = duckdb.connect(**on.connect_kwargs())
        try:
            with pytest.raises(on.OnagerUnavailable):
                on._load_onager(con)
        finally:
            con.close()

    def test_missing_vendored_file_degrades(self, monkeypatch, tmp_path):
        self._empty_home(tmp_path, monkeypatch)
        monkeypatch.setenv("ONAGER_VENDORED_EXTENSION", str(tmp_path / "absent.duckdb_extension"))
        con = duckdb.connect(**on.connect_kwargs())
        try:
            with pytest.raises(on.OnagerUnavailable):
                on._load_onager(con)
        finally:
            con.close()

    def test_vendored_load_canary(self):
        """LOAD verdict for the vendored build — the PROVENANCE gate. Fails
        on a stale artifact after a duckdb bump (rebuild via
        `make onager-build`), skips clean while unbuilt."""
        if not os.path.exists(on._VENDORED_EXTENSION):
            pytest.skip("vendored onager not built (vendor/onager/build.sh)")
        con = duckdb.connect(**on.connect_kwargs())
        try:
            on._load_onager(con)
            row = con.execute(
                "SELECT loaded, installed FROM duckdb_extensions() WHERE extension_name = 'onager'"
            ).fetchone()
            assert row is not None  # the extension registers a catalog row on LOAD
            (loaded, _installed) = row
            # installed stays False for a path-LOAD (not in an extension
            # repo); loaded is the verdict this canary exists for.
            assert loaded
        finally:
            con.close()

    def test_probe_sees_vendored_artifact(self, monkeypatch):
        """The dispatch gate routes through the same resolver — with the
        artifact present it must report available (else the whole family
        silently stays on fallbacks)."""
        if not os.path.exists(on._VENDORED_EXTENSION):
            pytest.skip("vendored onager not built (vendor/onager/build.sh)")
        monkeypatch.setattr(fb, "_AVAILABLE", None)  # bypass the process cache
        assert fb.onager_available() is True
