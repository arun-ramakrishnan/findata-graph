#!/usr/bin/env python3
"""P7 — Performance integration tests: algorithm scaling + correctness.

Tests the Onager-backed graph algorithms (helpers/graph/onager.py, wired
through helpers/graph/algorithms.py:compute) on SYNTHETIC edge lists of
varying sizes to verify:
  1. Correctness: each metric produces valid output on a known graph.
  2. Scaling: algorithmic complexity stays within expected bounds.
  3. Mutation: adding/removing edges is correctly reflected by re-running.
  4. Persistence: write_analytics round-trip (compute -> write -> read back).
  5. Multi-metric consistency: all metrics run together without interference.

NetworkX was removed (2026-08-14); Onager is the sole algorithm engine for
eigenvector/closeness/betweenness/louvain/degree. Synthetic graphs are plain
``(src, dst, weight)`` integer edge lists (exactly what Onager consumes) — no
NetworkX graph objects are involved.

See doc/improvements/archive/testing/integration_plan.txt § Priority 7.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from typing import cast

import pytest

from helpers.graph.algorithms import (
    betweenness_centrality,
    closeness_centrality,
    compute,
    degree_centrality,
    eigenvector_centrality,
    louvain_communities,
    write_analytics,
)
from helpers.graph.query import DUCKDB_PATH, connect as duckdb_connect

pytestmark = [pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Synthetic edge-list generators (Onager consumes (src, dst, weight) BIGINTs)
# --------------------------------------------------------------------------- #
def make_random_edges(
    n: int, edge_prob: float = 0.05, seed: int = 42
) -> list[tuple[int, int, float]]:
    """Erdős–Rényi random edge list over nodes 0..n-1."""
    rng = random.Random(seed)  # noqa: S311  # deterministic non-crypto RNG (tests)
    edges: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < edge_prob:
                edges.append((i, j, 1.0))
    return edges


def make_cycle_edges(n: int) -> list[tuple[int, int, float]]:
    """Cycle graph over nodes 0..n-1 - O(n) edges, O(n) generation."""
    return [(i, (i + 1) % n, 1.0) for i in range(n)]


def make_clustered_edges(
    n_clusters: int = 3,
    per_cluster: int = 10,
    p_in: float = 0.5,
    p_out: float = 0.02,
    seed: int = 42,
) -> list[tuple[int, int, float]]:
    """Edge list with strong community structure for louvain/wcc testing."""
    rng = random.Random(seed)  # noqa: S311
    edges: list[tuple[int, int, float]] = []
    nodes: list[int] = []
    for c in range(n_clusters):
        cluster = list(range(c * per_cluster, (c + 1) * per_cluster))
        nodes.extend(cluster)
        for i in range(per_cluster):
            for j in range(i + 1, per_cluster):
                if rng.random() < p_in:
                    edges.append((cluster[i], cluster[j], 1.0))
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if rng.random() < p_out:
                edges.append((nodes[i], nodes[j], 1.0))
    return edges


def node_ids(edges: list[tuple[int, int, float]]) -> set[int]:
    s: set[int] = set()
    for a, b, _ in edges:
        s.add(a)
        s.add(b)
    return s


# --------------------------------------------------------------------------- #
# Schema for write_analytics tests
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL
);
CREATE TABLE graph_edges (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    properties  TEXT NOT NULL DEFAULT '{}',
    valid_from  DATE,
    valid_to    DATE,
    source_ref  TEXT NOT NULL DEFAULT 'test',
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
CREATE TABLE graph_analytics (
    metric TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (metric, entity_name),
    FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE CASCADE
);
"""


@pytest.fixture
def synth_db(tmp_path):
    """SQLite DB with entities + graph_analytics for write_analytics tests."""
    db_path = str(tmp_path / "p7_perf.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    for i in range(20):
        conn.execute(
            "INSERT INTO entities(name, entity_type) VALUES (?, 'company')",
            (f"Company_{i:04d}",),
        )
    # Known topology so the Onager-backed compute() path (over fin.graph_edges)
    # returns name-keyed results that match the seeded entities.
    for name, etype in [
        ("SectorA", "sector"),
        ("SectorB", "sector"),
        ("CompanyA", "company"),
        ("CompanyB", "company"),
        ("CompanyC", "company"),
        ("CompanyD", "company"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO entities(name, entity_type) VALUES (?, ?)",
            (name, etype),
        )
    for src, tgt, etype in [
        ("CompanyA", "SectorA", "part_of"),
        ("SectorA", "CompanyA", "has_company"),
        ("CompanyB", "SectorA", "part_of"),
        ("SectorA", "CompanyB", "has_company"),
        ("CompanyC", "SectorA", "part_of"),
        ("SectorA", "CompanyC", "has_company"),
        ("CompanyD", "SectorB", "part_of"),
        ("SectorB", "CompanyD", "has_company"),
        ("CompanyA", "CompanyB", "competes_with"),
        ("CompanyB", "CompanyC", "competes_with"),
    ]:
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES (?, ?, ?, 'seed')",
            (src, tgt, etype),
        )
    conn.commit()
    yield conn, db_path
    conn.close()


@pytest.fixture
def con():
    """A DuckDB connection with Onager loaded.

    Read-only whenever the shared cache exists: the perf cases pass
    ``edges=`` explicitly (the connection is just the Onager engine), and
    N read-only connections coexist across processes while an RW one holds
    an EXCLUSIVE lock — the pre-xdist source of "Conflicting lock" errors
    against the production graph.duckdb (xdist workers or a concurrently
    running live-invariants suite).
    """
    if DUCKDB_PATH.exists():
        c = duckdb_connect(read_only=True)
    else:
        c = duckdb_connect()  # pristine clone: build the cache once
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# 1. Correctness: each metric produces valid output
# --------------------------------------------------------------------------- #
class TestMetricCorrectness:
    def test_degree_centrality_valid(self, con):
        edges = make_random_edges(100, edge_prob=0.05, seed=42)
        result = degree_centrality(con, edges=edges)
        n = len(node_ids(edges))
        assert isinstance(result, dict)
        assert len(result) == n
        for val in result.values():
            assert 0.0 <= val <= 1.0, f"Degree centrality {val} out of [0,1]"

    def test_betweenness_centrality_valid(self, con):
        edges = make_random_edges(100, edge_prob=0.05, seed=42)
        result = betweenness_centrality(con, edges=edges)
        n = len(node_ids(edges))
        assert isinstance(result, dict)
        assert len(result) == n
        for val in result.values():
            assert 0.0 <= val <= 1.0, f"Betweenness {val} out of [0,1]"

    def test_closeness_centrality_valid(self, con):
        edges = make_random_edges(100, edge_prob=0.05, seed=42)
        result = closeness_centrality(con, edges=edges)
        n = len(node_ids(edges))
        assert isinstance(result, dict)
        assert len(result) == n
        for val in result.values():
            assert 0.0 <= val <= 1.0, f"Closeness {val} out of [0,1]"

    def test_eigenvector_centrality_valid(self, con):
        edges = make_random_edges(100, edge_prob=0.05, seed=42)
        result = eigenvector_centrality(con, edges=edges)
        n = len(node_ids(edges))
        assert isinstance(result, dict)
        assert len(result) == n
        # L2-normalised -> unit norm, all non-negative for a connected core.
        norm = sum(v * v for v in result.values()) ** 0.5
        assert abs(norm - 1.0) < 1e-6
        for val in result.values():
            assert val >= 0.0, f"Eigenvector {val} negative"

    def test_louvain_communities_valid(self, con):
        edges = make_clustered_edges(n_clusters=3, per_cluster=10, p_in=0.5, p_out=0.02)
        result = louvain_communities(con, edges=edges)
        n = len(node_ids(edges))
        assert isinstance(result.labels, dict)
        assert len(result.labels) == n
        assert result.modularity > 0.0, f"Modularity={result.modularity} <= 0"
        n_communities = len(set(result.labels.values()))
        assert 2 <= n_communities <= 6, f"Expected ~3 communities, got {n_communities}"


# --------------------------------------------------------------------------- #
# 2. Scaling: algorithmic complexity stays within bounds
# --------------------------------------------------------------------------- #
def _scaling_ratio(fn, edges_small, edges_large, rounds=3):
    """Min over `rounds` of (t_large / t_small), measured back-to-back.

    Paired rounds are the contention-robust statistic for scaling guards
    under an xdist gate: a scheduler stall inflates BOTH legs of a round
    roughly equally, so the round's ratio stays near the true complexity
    factor — independent minima (the old shape) let a stall inflate one
    size's minimum and flake the budget (measured 2026-08-31: betweenness
    failed 2/5 advisory runs at -n auto; 550 passed, this test the only
    failure).

    Returns (min_ratio, smallest t_small seen) so callers keep the
    too-fast-to-measure skip.
    """
    best = float("inf")
    t_small_min = float("inf")
    t_large_min = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(edges=edges_small)
        t_small = time.perf_counter() - t0
        t0 = time.perf_counter()
        fn(edges=edges_large)
        t_large = time.perf_counter() - t0
        best = min(best, t_large / t_small)
        t_small_min = min(t_small_min, t_small)
        t_large_min = min(t_large_min, t_large)
    return best, t_small_min, t_large_min


class TestAlgorithmScaling:
    def test_degree_centrality_scales_linearly(self, con):
        """degree_centrality is O(V+E); doubling nodes should be <5x."""
        e_small = make_cycle_edges(10000)
        e_large = make_cycle_edges(20000)
        ratio, t_small, t_large = _scaling_ratio(degree_centrality, e_small, e_large)
        if t_small < 0.001:
            pytest.skip("baseline too fast to measure reliably")
        assert ratio < 5.0, (
            f"Degree centrality scaling: 20000 nodes took {ratio:.1f}x of 10000 "
            f"(expected ~2x, budget 5x). t_small={t_small:.3f}s t_large={t_large:.3f}s"
        )

    def test_betweenness_scales_reasonably(self, con):
        """Exact betweenness is O(V*E); doubling should be <8x."""
        e_small = make_random_edges(150, edge_prob=0.05, seed=300)
        e_large = make_random_edges(300, edge_prob=0.05, seed=300)
        ratio, t_small, t_large = _scaling_ratio(betweenness_centrality, e_small, e_large)
        if t_small < 0.001:
            pytest.skip("baseline too fast to measure reliably")
        assert ratio < 8.0, (
            f"Betweenness scaling: 300 nodes took {ratio:.1f}x of 150 "
            f"(budget 8x). t_small={t_small:.3f}s t_large={t_large:.3f}s"
        )

    def test_louvain_scales_reasonably(self, con):
        """Louvain is near-linear; doubling nodes should be <8x."""
        e_small = make_clustered_edges(3, 50, 0.1, 0.01, seed=400)
        e_large = make_clustered_edges(3, 100, 0.1, 0.01, seed=400)
        ratio, t_small, t_large = _scaling_ratio(louvain_communities, e_small, e_large)
        if t_small < 0.001:
            pytest.skip("baseline too fast to measure reliably")
        assert ratio < 8.0, (
            f"Louvain scaling: 300 nodes took {ratio:.1f}x of 150 nodes "
            f"(budget 8x). t_small={t_small:.3f}s t_large={t_large:.3f}s"
        )

    def test_closeness_scales_reasonably(self, con):
        """Closeness is O(V*(V+E)); doubling must stay under a generous budget."""
        e_small = make_clustered_edges(3, 50, 0.1, 0.01, seed=500)
        e_large = make_clustered_edges(3, 100, 0.1, 0.01, seed=500)
        ratio, t_small, t_large = _scaling_ratio(closeness_centrality, e_small, e_large)
        if t_small < 0.001:
            pytest.skip("baseline too fast to measure reliably")
        assert ratio < 12.0, (
            f"Closeness scaling: 300 nodes took {ratio:.1f}x of 150 nodes "
            f"(budget 12x; degrades super-linearly). "
            f"t_small={t_small:.3f}s t_large={t_large:.3f}s"
        )


# --------------------------------------------------------------------------- #
# 3. Mutation: edge changes are correctly reflected by re-running metrics
# --------------------------------------------------------------------------- #
class TestGraphMutationCorrectness:
    def test_degree_centrality_reflects_new_edge(self, con):
        edges = [(0, 1, 1.0), (1, 2, 1.0)]
        deg_before = cast("dict[int, float]", degree_centrality(con, edges=edges))
        edges_after = edges + [(0, 2, 1.0)]
        deg_after = cast("dict[int, float]", degree_centrality(con, edges=edges_after))
        assert deg_after[0] > deg_before[0]
        assert deg_after[2] > deg_before[2]
        assert deg_after[1] == deg_before[1]

    def test_degree_centrality_reflects_removed_edge(self, con):
        # Triangle 0-1-2 plus a leaf 1-3; move the leaf edge 1-3 -> 0-3 so all
        # four nodes persist (n constant) and node 1's normalised degree drops.
        edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 0, 1.0), (1, 3, 1.0)]
        deg_before = cast("dict[int, float]", degree_centrality(con, edges=edges))
        edges_after = [(0, 1, 1.0), (1, 2, 1.0), (2, 0, 1.0), (3, 0, 1.0)]
        deg_after = cast("dict[int, float]", degree_centrality(con, edges=edges_after))
        # node 1 lost an edge (1-3 -> 0-3); its normalised degree drops.
        assert deg_after[1] < deg_before[1]
        # node 3 kept a degree-1 connection after the move, so unchanged.
        assert deg_after[3] == deg_before[3]

    def test_betweenness_changes_with_bridge(self, con):
        """Adding a bridge node increases its betweenness."""
        edges = []
        for i in range(5):
            edges.append((i, i + 1, 1.0))  # left chain 0-1-2-3-4
        for i in range(5, 10):
            edges.append((i, i + 1, 1.0))  # right chain 5-6-7-8-9
        edges.append((4, 5, 1.0))  # bridge 4-5 (no B yet)
        btwn = betweenness_centrality(con, edges=edges)
        assert max(btwn, key=lambda k: btwn[k]) in (4, 5)

    def test_louvain_detects_community_structure(self, con):
        """A strongly-clustered edge set should produce higher modularity
        than a random one of similar size."""
        e_clustered = make_clustered_edges(3, 10, 0.6, 0.01, seed=500)
        e_random = make_random_edges(30, 0.3, seed=500)
        m_clustered = louvain_communities(con, edges=e_clustered).modularity
        m_random = louvain_communities(con, edges=e_random).modularity
        assert m_clustered > m_random, (
            f"Expected clustered modularity ({m_clustered:.3f}) > random ({m_random:.3f})"
        )


# --------------------------------------------------------------------------- #
# 4. write_analytics persistence round-trip
# --------------------------------------------------------------------------- #
class TestWriteAnalyticsRoundTrip:
    def test_write_and_read_back_numeric(self, synth_db):
        conn, db_path = synth_db
        values = {"Company_0000": 0.15, "Company_0001": 0.08, "Company_0002": 0.12}
        n = write_analytics("pagerank", values, conn=conn)
        assert n == 3
        rows = conn.execute(
            "SELECT entity_name, value FROM graph_analytics WHERE metric='pagerank'"
        ).fetchall()
        assert len(rows) == 3
        for r in rows:
            v = json.loads(r["value"])
            assert r["entity_name"] in values
            assert abs(v - values[r["entity_name"]]) < 1e-9

    def test_write_and_read_back_dict_values(self, synth_db):
        conn, db_path = synth_db
        values = {
            "Company_0000": {"community": 0, "modularity": 0.42},
            "Company_0001": {"community": 1, "modularity": 0.42},
        }
        n = write_analytics("louvain_community", values, conn=conn)
        assert n == 2
        rows = conn.execute(
            "SELECT entity_name, value FROM graph_analytics WHERE metric='louvain_community'"
        ).fetchall()
        assert len(rows) == 2
        for r in rows:
            v = json.loads(r["value"])
            assert isinstance(v, dict)
            assert "community" in v

    def test_write_analytics_upsert(self, synth_db):
        conn, db_path = synth_db
        write_analytics("pagerank", {"Company_0000": 0.1}, conn=conn)
        write_analytics("pagerank", {"Company_0000": 0.5}, conn=conn)
        rows = conn.execute(
            "SELECT value FROM graph_analytics WHERE metric='pagerank' AND entity_name='Company_0000'"
        ).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0]["value"]) == 0.5

    def test_write_analytics_idempotent(self, synth_db):
        conn, db_path = synth_db
        values = {"Company_0000": 0.1, "Company_0001": 0.2}
        write_analytics("pagerank", values, conn=conn)
        n_before = conn.execute(
            "SELECT COUNT(*) FROM graph_analytics WHERE metric='pagerank'"
        ).fetchone()[0]
        write_analytics("pagerank", values, conn=conn)
        n_after = conn.execute(
            "SELECT COUNT(*) FROM graph_analytics WHERE metric='pagerank'"
        ).fetchone()[0]
        assert n_before == n_after == 2

    def test_write_multiple_metrics_no_interference(self, synth_db):
        conn, db_path = synth_db
        write_analytics("pagerank", {"Company_0000": 0.1}, conn=conn)
        write_analytics("degree_centrality", {"Company_0000": 0.5}, conn=conn)
        write_analytics("betweenness_centrality", {"Company_0000": 0.3}, conn=conn)
        rows = conn.execute(
            "SELECT metric, value FROM graph_analytics WHERE entity_name='Company_0000' ORDER BY metric"
        ).fetchall()
        assert len(rows) == 3
        metrics = [r["metric"] for r in rows]
        assert "pagerank" in metrics
        assert "degree_centrality" in metrics
        assert "betweenness_centrality" in metrics


# --------------------------------------------------------------------------- #
# 5. Multi-metric integration: all metrics run together
# --------------------------------------------------------------------------- #
class TestMultiMetricConsistency:
    def test_compute_dispatcher_on_edges(self, con):
        """compute() routes each metric through the Onager backend."""
        edges = make_random_edges(60, edge_prob=0.1, seed=7)
        nodes = node_ids(edges)
        for metric in (
            "degree_centrality",
            "betweenness_centrality",
            "closeness_centrality",
            "eigenvector_centrality",
            "louvain_community",
        ):
            res = compute(metric, con=con, edges=edges)
            assert set(res.keys()) == nodes

    def test_all_onager_metrics_on_same_edges(self, con):
        edges = make_random_edges(60, edge_prob=0.1, seed=42)
        nodes = node_ids(edges)
        deg = degree_centrality(con, edges=edges)
        btwn = betweenness_centrality(con, edges=edges)
        cls = closeness_centrality(con, edges=edges)
        eig = eigenvector_centrality(con, edges=edges)
        louv = louvain_communities(con, edges=edges)
        assert set(deg.keys()) == nodes
        assert set(btwn.keys()) == nodes
        assert set(cls.keys()) == nodes
        assert set(eig.keys()) == nodes
        assert set(louv.labels.keys()) == nodes


# --------------------------------------------------------------------------- #
# 6. End-to-end: compute on the seeded synth_db graph -> persist -> verify
# --------------------------------------------------------------------------- #
def _duckdb_over(db_path):
    """Open a DuckDB connection with onager loaded, attached to db_path as fin."""
    import duckdb as _dd

    c = _dd.connect()
    try:
        c.execute("INSTALL sqlite;")
    except Exception:  # noqa: S110
        pass
    c.execute("LOAD sqlite;")
    c.execute(f"ATTACH '{db_path}' AS fin (TYPE sqlite, READ_ONLY);")
    try:
        c.execute("INSTALL onager FROM community;")
    except Exception:  # noqa: S110
        pass
    c.execute("LOAD onager;")
    return c


class TestEndToEndComputePersist:
    def test_degree_compute_persist_verify(self, synth_db):
        conn_db, db_path = synth_db
        con = _duckdb_over(db_path)
        deg = degree_centrality(con)  # name-keyed over synth_db graph_edges
        write_analytics("degree_centrality", deg, conn=conn_db)
        rows = conn_db.execute(
            "SELECT entity_name, value FROM graph_analytics "
            "WHERE metric='degree_centrality' ORDER BY entity_name"
        ).fetchall()
        assert len(rows) == len(deg)
        for r in rows:
            v = json.loads(r["value"])
            assert abs(v - deg[r["entity_name"]]) < 1e-9
        con.close()

    def test_betweenness_compute_persist_verify(self, synth_db):
        conn_db, db_path = synth_db
        con = _duckdb_over(db_path)
        btwn = betweenness_centrality(con)
        write_analytics("betweenness_centrality", btwn, conn=conn_db)
        rows = conn_db.execute(
            "SELECT entity_name, value FROM graph_analytics WHERE metric='betweenness_centrality'"
        ).fetchall()
        assert len(rows) == len(btwn)
        for r in rows:
            v = json.loads(r["value"])
            assert abs(v - btwn[r["entity_name"]]) < 1e-9
        con.close()

    def test_louvain_compute_persist_verify(self, synth_db):
        conn_db, db_path = synth_db
        con = _duckdb_over(db_path)
        result = louvain_communities(con)
        wrapped = {
            name: {"community": label, "modularity": result.modularity}
            for name, label in result.labels.items()
        }
        write_analytics("louvain_community", wrapped, conn=conn_db)
        rows = conn_db.execute(
            "SELECT entity_name, value FROM graph_analytics WHERE metric='louvain_community'"
        ).fetchall()
        assert len(rows) == len(wrapped)
        for r in rows:
            v = json.loads(r["value"])
            assert isinstance(v, dict)
            assert "community" in v
            assert "modularity" in v
        con.close()
