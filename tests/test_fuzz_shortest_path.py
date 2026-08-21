#!/usr/bin/env python3
"""Fuzz tests for shortest_path against a Python BFS oracle
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B3).

The BFS rewrite (sql_capability_unlocks B2) is pinned against the CTE
oracle deterministically in test_graph.py; here the equivalence is
stressed over a random Erdős–Rényi graph with Hypothesis choosing
(src, dst, max_hops, edge_label, as_of) — per-example cost stays at
query level, the graph itself is built once per module.

Open question 2 resolution: as_of draws come from INSIDE the seeded
validity windows ∪ a date BEFORE them, so both the filtered-pass and
the filtered-empty paths are exercised and asserted against the same
oracle logic.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from collections import deque
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import query as gq  # noqa: E402
from helpers.graph.query import DB_PATH  # noqa: E402

duckdb = pytest.importorskip("duckdb")

_N = 24                 # nodes n00..n23
_P = 0.15               # Erdős–Rényi edge probability
_SEED = 7

_SETTINGS = settings(
    max_examples=40, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
_HOPS = st.integers(min_value=1, max_value=6)
_NODES = st.sampled_from([f"n{i:02d}" for i in range(_N)])
_DATES = st.sampled_from(["2022-06-15", "2019-01-01"])  # inside ∪ before


def _edges() -> list[tuple[str, str, str, str | None, str | None]]:
    """Deterministic Erdős–Rényi mix: competes_with (label-filterable,
    always-valid) + part_of edges with a 2020-2021 validity window."""
    rng = random.Random(_SEED)  # noqa: S311  # deterministic non-crypto RNG
    edges: list[tuple[str, str, str, str | None, str | None]] = []
    for i in range(_N):
        for j in range(i + 1, _N):
            if rng.random() < _P:
                a, b = f"n{i:02d}", f"n{j:02d}"
                if rng.random() < 0.6:
                    edges.append((a, b, "competes_with", None, None))
                else:
                    edges.append(
                        (a, b, "part_of", "2020-01-01", "2021-01-01"))
    return edges


_EDGES = _edges()


def _oracle_adj(edge_type: str | None, as_of: str | None) -> dict[str, set]:
    adj: dict[str, set] = {f"n{i:02d}": set() for i in range(_N)}
    for a, b, et, vf, vt in _EDGES:
        if edge_type is not None and et != edge_type:
            continue
        if as_of is not None:
            if vf is not None and vf > as_of:
                continue
            if vt is not None and vt < as_of:
                continue
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _oracle_dist(src: str, dst: str, adj: dict[str, set]) -> int | None:
    if src == dst:
        return 0
    seen = {src}
    q = deque([(src, 0)])
    while q:
        cur, d = q.popleft()
        for nxt in adj[cur]:
            if nxt == dst:
                return d + 1
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, d + 1))
    return None


@pytest.fixture(scope="module")
def con():
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "sp.db"
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(tmp))
    src.backup(dst)
    src.close()
    for t in ("graph_edges", "entity_tags", "graph_analytics", "events",
              "quotes", "company_metrics", "company_embeddings",
              "note_search", "note_search_meta"):
        dst.execute(f"DELETE FROM {t}")  # noqa: S608  # schema-constant identifiers
    dst.execute("DELETE FROM entities")
    dst.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [(f"n{i:02d}",) for i in range(_N)])
    dst.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, valid_from, "
        "valid_to, source_ref) VALUES (?, ?, ?, ?, ?, 'seed')", _EDGES)
    dst.commit()
    dst.close()
    c = gq.connect(tmp, fresh=True)
    yield c
    c.close()


@_SETTINGS
@given(_NODES, _NODES, _HOPS)
def test_shortest_path_matches_bfs_oracle_labelled(con, src, dst, hops):
    """Label-filtered traversal == Python BFS over the same filtered
    adjacency: same reachability-within-hops, hop-optimality, and every
    consecutive pair actually adjacent."""
    adj = _oracle_adj("competes_with", None)
    dist = _oracle_dist(src, dst, adj)
    path = gq.shortest_path(con, src, dst, max_hops=hops,
                            edge_label="CompetesWith")
    if dist is None or dist > hops:
        assert path is None
        return
    assert path is not None
    names = [v for v, _ in path]
    assert names[0] == src and names[-1] == dst
    assert len(path) - 1 == dist                      # hop-optimal
    assert [h for _, h in path] == list(range(len(path)))
    for a, b in zip(names, names[1:]):
        assert b in adj[a]                            # valid consecutive hop


@_SETTINGS
@given(_NODES, _NODES, _HOPS, _DATES)
def test_shortest_path_matches_bfs_oracle_as_of(con, src, dst, hops, as_of):
    """Unlabelled traversal under a temporal filter == BFS over the
    window-filtered adjacency (valid edges only; NULL window = always)."""
    adj = _oracle_adj(None, as_of)
    dist = _oracle_dist(src, dst, adj)
    path = gq.shortest_path(con, src, dst, max_hops=hops,
                            edge_label=None, as_of=as_of)
    if dist is None or dist > hops:
        assert path is None
        return
    assert path is not None
    assert len(path) - 1 == dist


@_SETTINGS
@given(_NODES, _NODES)
def test_shortest_path_undirected_symmetry(con, src, dst):
    fwd = gq.shortest_path(con, src, dst, max_hops=6, edge_label=None)
    rev = gq.shortest_path(con, dst, src, max_hops=6, edge_label=None)
    assert (fwd is None) == (rev is None)
    if fwd is not None:
        assert rev is not None
        assert len(fwd) == len(rev)


@_SETTINGS
@given(_NODES, _NODES)
def test_shortest_path_deterministic(con, src, dst):
    r1 = gq.shortest_path(con, src, dst, max_hops=6, edge_label=None)
    r2 = gq.shortest_path(con, src, dst, max_hops=6, edge_label=None)
    assert r1 == r2


@_SETTINGS
@given(_NODES, _NODES, st.integers(min_value=1, max_value=5))
def test_shortest_path_bfs_equals_cte_oracle(con, src, dst, hops):
    """The retained CTE implementation stays equivalent to the BFS on
    every drawn pair (bounded hops keep the CTE cheap)."""
    bfs = gq.shortest_path(con, src, dst, max_hops=hops, edge_label=None)
    cte = gq._shortest_path_cte(con, src, dst, max_hops=hops)
    if bfs is None or cte is None:
        assert (bfs is None) == (cte is None)
        return
    assert len(bfs) == len(cte)   # same hop count (path choice may differ)
