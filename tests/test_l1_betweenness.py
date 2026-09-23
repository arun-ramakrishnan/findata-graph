"""Exactness tests for the L1b folded-betweenness lane (graph_perf_l1 S2).

Ground truth = a direct Brandes pass on the UNFOLDED graph, halved to the
unordered convention. The fold must match it node-for-node on arbitrary
graphs — the 2026-09-23 defect (core<->leaf pairs counted by U, the
attachment credit, AND TU) shipped a ~33x core inflation that rank
correlation could not see; only node-exact comparison catches it.
"""

import random
from collections import deque

import pytest

from helpers.graph import l1_betweenness as lb


def _brandes_unordered(n: int, adj: list[list[int]]) -> list[float]:
    """Reference exact betweenness, unordered convention (each pair once)."""
    U = [0.0] * n
    for s in range(n):
        order: list[int] = []
        preds: list[list[int]] = [[] for _ in range(n)]
        sig = [0] * n
        sig[s] = 1
        dist = [-1] * n
        dist[s] = 0
        q = deque([s])
        while q:
            v = q.popleft()
            order.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sig[w] += sig[v]
                    preds[w].append(v)
        delta = [0.0] * n
        for w in reversed(order):
            for v in preds[w]:
                delta[v] += sig[v] / sig[w] * (1 + delta[w])
            if w != s:
                U[w] += delta[w]
    return [u / 2 for u in U]


def _run_lane(n: int, edges: list[tuple[int, int]]) -> dict[str, float]:
    lb_orig = lb.load_projection
    lb.load_projection = lambda *a, **k: ([str(i) for i in range(n)], list(edges))
    try:
        scores, _info = lb.compute("unused", jobs=1)
    finally:
        lb.load_projection = lb_orig
    return scores


def _adj(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    adj = [[] for _ in range(n)]
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    return adj


def _assert_exact(n: int, edges: list[tuple[int, int]]) -> None:
    truth = _brandes_unordered(n, _adj(n, edges))
    scores = _run_lane(n, edges)
    for i in range(n):
        assert scores[str(i)] == pytest.approx(truth[i], abs=1e-9), (
            f"node {i}: lane {scores[str(i)]} vs truth {truth[i]}"
        )


def test_toy_core_trees_and_special_component() -> None:
    """4-cycle core + leaf trees + a multi-node tree + a zero-attachment
    forest component: every node exact, including the closed-form internals."""
    edges = [
        (0, 1),
        (1, 2),
        (2, 3),
        (0, 3),  # core cycle
        (0, 4),
        (1, 5),
        (1, 6),
        (2, 7),  # leaf trees
        (3, 8),
        (3, 9),
        (3, 10),  # multi-leaf attachment
        (0, 11),
        (11, 12),
        (12, 13),  # non-trivial tree (internals)
        (14, 15),  # zero-attachment special comp
    ]
    _assert_exact(16, edges)


def test_random_graphs_exact_vs_brute_force() -> None:
    """Seeded random graphs: the fold must equal unfolded Brandes exactly."""
    rng = random.Random(7)
    for _trial in range(8):
        n = rng.randint(8, 14)
        p = rng.uniform(0.15, 0.45)
        edges = sorted((a, b) for a in range(n) for b in range(a + 1, n) if rng.random() < p)
        if not edges:
            continue
        _assert_exact(n, edges)


def test_normalization_matches_incumbent_scale() -> None:
    """Documented divisor: raw * 2/((n-1)(n-2)) over the ex-index endpoints
    (here: no index noise, so n = all endpoints). Path P4 center = 2/3."""
    edges = [(0, 1), (1, 2), (2, 3)]
    scores = _run_lane(4, edges)
    norm = (4 - 1) * (4 - 2) / 2.0
    assert scores["1"] / norm == pytest.approx(2 / 3)
    assert scores["0"] == 0.0


def test_index_only_isolates_excluded_from_divisor() -> None:
    """An endpoint whose only edge is listed_on_index has no path in the
    projection: it must not enter the normalization n."""
    rows = [
        ("0", "1", "cited_in"),
        ("1", "2", "cited_in"),
        ("2", "0", "cited_in"),
        ("3", "4", "listed_on_index"),
    ]
    names = sorted({r[0] for r in rows} | {r[1] for r in rows})
    proj_edges = sorted(
        tuple(sorted((int(r[0]), int(r[1])))) for r in rows if r[2] != "listed_on_index"
    )
    lb_orig = lb.load_projection
    lb.load_projection = lambda *a, **k: (names, proj_edges)
    try:
        scores, info = lb.compute("unused", jobs=1)
    finally:
        lb.load_projection = lb_orig
    # five endpoints loaded; ex-index endpoints = {0,1,2} -> n = 3
    assert info["endpoints"] == 3
    assert scores["3"] == 0.0 and scores["4"] == 0.0
