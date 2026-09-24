"""Exactness tests for the L1b folded-betweenness lane (graph_perf_l1 S2).

Ground truth = a direct Brandes pass on the UNFOLDED graph, halved to the
unordered convention. The fold must match it node-for-node on arbitrary
graphs — the 2026-09-23 defect (core<->leaf pairs counted by U, the
attachment credit, AND TU) shipped a ~33x core inflation that rank
correlation could not see; only node-exact comparison catches it.
"""

import random
import sqlite3
from collections import deque
from pathlib import Path

import pytest

from helpers.graph import algorithms as alg
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


def _run_lane(
    n: int, edges: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> dict[str, float]:
    monkeypatch.setattr(
        lb, "load_projection", lambda *a, **k: ([str(i) for i in range(n)], list(edges))
    )
    scores, _info = lb.compute("unused", jobs=1)
    return scores


def _adj(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    adj = [[] for _ in range(n)]
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    return adj


def _assert_exact(n: int, edges: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    truth = _brandes_unordered(n, _adj(n, edges))
    scores = _run_lane(n, edges, monkeypatch)
    for i in range(n):
        assert scores[str(i)] == pytest.approx(truth[i], abs=1e-9), (
            f"node {i}: lane {scores[str(i)]} vs truth {truth[i]}"
        )


def test_toy_core_trees_and_special_component(monkeypatch: pytest.MonkeyPatch) -> None:
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
    _assert_exact(16, edges, monkeypatch)


def test_random_graphs_exact_vs_brute_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeded random graphs: the fold must equal unfolded Brandes exactly."""
    rng = random.Random(7)  # noqa: S311  # seeded test-data generation, not crypto
    for _trial in range(8):
        n = rng.randint(8, 14)
        p = rng.uniform(0.15, 0.45)
        edges = sorted((a, b) for a in range(n) for b in range(a + 1, n) if rng.random() < p)
        if not edges:
            continue
        _assert_exact(n, edges, monkeypatch)


def test_normalization_matches_incumbent_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented divisor: raw * 2/((n-1)(n-2)) over the ex-index endpoints
    (here: no index noise, so n = all endpoints). Path P4 center = 2/3."""
    edges = [(0, 1), (1, 2), (2, 3)]
    scores = _run_lane(4, edges, monkeypatch)
    norm = (4 - 1) * (4 - 2) / 2.0
    assert scores["1"] / norm == pytest.approx(2 / 3)
    assert scores["0"] == 0.0


def test_index_only_isolates_excluded_from_divisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(lb, "load_projection", lambda *a, **k: (names, proj_edges))
    scores, info = lb.compute("unused", jobs=1)
    # five endpoints loaded; ex-index endpoints = {0,1,2} -> n = 3
    assert info["endpoints"] == 3
    assert scores["3"] == 0.0 and scores["4"] == 0.0


# --------------------------------------------------------------------------- #
# ROUTING dispatch (scipy_routing_dispatch S3 — L1B_FOLD flip): algorithms.py
# serves the fold on the DB-backed path. Evidence for the flip itself lives
# in the proposal §8.3 (toy harness + fresh-bypass countersign, both exact).
# --------------------------------------------------------------------------- #
_PATH4 = [("a", "b"), ("b", "c"), ("c", "d")]
_CONTRACT4 = ["c", "b"]  # deliberately not sorted order


@pytest.fixture()
def bstore(tmp_path: Path) -> Path:
    spath = tmp_path / "b.db"
    scon = sqlite3.connect(str(spath))
    scon.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, edge_type VARCHAR)")
    scon.executemany("INSERT INTO graph_edges VALUES (?, ?, 'cited_in')", _PATH4)
    scon.execute(
        "CREATE TABLE graph_analytics (entity_name VARCHAR, metric VARCHAR, "
        "value VARCHAR, PRIMARY KEY (metric, entity_name))"
    )
    scon.executemany(
        "INSERT INTO graph_analytics VALUES (?, 'betweenness_centrality', '0.0')",
        [(n,) for n in _CONTRACT4],
    )
    scon.commit()
    scon.close()
    return spath


def test_dispatch_betweenness_tmpstore(bstore):
    got = alg._run_betweenness(None, edges=None, top_k=None, approximate=None, db_path=bstore)
    assert set(got) == set(_CONTRACT4)  # contract population, not full graph
    # P4 interior nodes: unordered raw 2 each, norm (4-1)(4-2)/2 = 3
    assert got["b"] == pytest.approx(2.0 / 3.0)
    assert got["c"] == pytest.approx(2.0 / 3.0)


def test_dispatch_betweenness_top_k_preserved(bstore):
    got = alg._run_betweenness(None, edges=None, top_k=1, approximate=None, db_path=bstore)
    assert len(got) == 1  # same top-k slicing the Onager path applies


def test_dispatch_betweenness_drift_fails_loud(bstore):
    scon = sqlite3.connect(str(bstore))
    scon.execute("INSERT INTO graph_analytics VALUES ('ghost', 'betweenness_centrality', '0.0')")
    scon.commit()
    scon.close()
    with pytest.raises(KeyError, match="ghost"):
        alg._run_betweenness(None, edges=None, top_k=None, approximate=None, db_path=bstore)


def test_dispatch_betweenness_synthetic_stays_onager():
    edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0)]
    via_dispatch = alg._run_betweenness(None, edges=edges, top_k=None, approximate=None)
    direct = alg.betweenness_centrality(None, edges=edges)
    assert via_dispatch == direct  # same Onager path, not the lane
    assert set(via_dispatch) == {0, 1, 2, 3}  # full population, int ids


@pytest.mark.live
def test_dispatch_live_betweenness_exact_vs_incumbents():
    import json as _json

    def _val(s):
        try:
            return float(s)
        except ValueError:
            return float(_json.loads(s)["value"])

    got = alg._run_betweenness(None, edges=None, top_k=None, approximate=None)
    scon = sqlite3.connect("memory/research.db")
    try:
        inc = {
            r[0]: _val(r[1])
            for r in scon.execute(
                "SELECT entity_name, value FROM graph_analytics "
                "WHERE metric='betweenness_centrality'"
            ).fetchall()
        }
    finally:
        scon.close()
    assert set(got) == set(inc) and len(got) == 1734
    # 1e-12, not 0.0: Brandes accumulation order differs between jobs=1
    # here and the --jobs 4 the rows were written with (1-ulp dust);
    # anything bigger is a real value divergence.
    assert max(abs(got[k] - inc[k]) for k in inc) < 1e-12
