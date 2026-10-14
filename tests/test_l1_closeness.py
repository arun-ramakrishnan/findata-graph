"""Tests for helpers/graph/l1_closeness.py — folded exact closeness/harmonic.

Toy oracles + frozen-subgraph exactness against full-graph BFS.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph.l1_closeness import (  # noqa: E402
    build_adjacency,
    build_adj_sets,
    build_core_adjacency,
    core_bfs,
    compute,
    core_source_scores,
    peeled_source_scores,
    peel_two_core,
)


class TestPeelTwoCore(unittest.TestCase):
    def test_path_graph(self):
        """Path of 5 nodes: 0-1-2-3-4. No 2-core exists — all nodes peel."""
        edges = [(0, 1), (1, 2), (2, 3), (3, 4)]
        indptr, indices = build_adjacency(5, edges)
        mask, removed = peel_two_core(indptr, indices, 5)
        self.assertEqual(removed, {0, 1, 2, 3, 4})
        self.assertEqual(int(mask.sum()), 0)

    def test_cycle_graph(self):
        """Cycle of 4 nodes: no node has degree < 2. Nothing peeled."""
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        indptr, indices = build_adjacency(4, edges)
        mask, removed = peel_two_core(indptr, indices, 4)
        self.assertEqual(removed, set())
        self.assertTrue(mask.all())

    def test_star_graph(self):
        """Star: center 0, leaves 1,2,3,4. No 2-core — center peels too (deg drops to 0)."""
        edges = [(0, 1), (0, 2), (0, 3), (0, 4)]
        indptr, indices = build_adjacency(5, edges)
        mask, removed = peel_two_core(indptr, indices, 5)
        self.assertEqual(removed, {0, 1, 2, 3, 4})
        self.assertEqual(int(mask.sum()), 0)


class TestCoreBFS(unittest.TestCase):
    def test_path_core(self):
        """Path 0-1-2-3-4, all core. BFS from 0 gives distances 0,1,2,3,4."""
        edges = [(0, 1), (1, 2), (2, 3), (3, 4)]
        indptr, indices = build_adjacency(5, edges)
        is_core = np.ones(5, dtype=bool)
        core_indptr, core_indices = build_core_adjacency(indptr, indices, is_core)
        dist = core_bfs(0, core_indptr, core_indices)
        np.testing.assert_array_equal(dist, [0, 1, 2, 3, 4])

    def test_disconnected(self):
        """Two disconnected components. BFS from 0 doesn't reach 3."""
        edges = [(0, 1), (2, 3)]
        indptr, indices = build_adjacency(4, edges)
        is_core = np.ones(4, dtype=bool)
        core_indptr, core_indices = build_core_adjacency(indptr, indices, is_core)
        dist = core_bfs(0, core_indptr, core_indices)
        np.testing.assert_array_equal(dist, [0, 1, -1, -1])


class TestCoreSourceScores(unittest.TestCase):
    def test_path_graph(self):
        """Path 0-1-2-3-4, all core. Source 0: distances 1,2,3,4. sum=10.
        closeness = (5-1)/10 = 0.4. harmonic = 1/1+1/2+1/3+1/4 = 2.0833..."""
        edges = [(0, 1), (1, 2), (2, 3), (3, 4)]
        indptr, indices = build_adjacency(5, edges)
        is_core = np.ones(5, dtype=bool)
        core_indptr, core_indices = build_core_adjacency(indptr, indices, is_core)
        dist = core_bfs(0, core_indptr, core_indices)
        forest = {
            "tree_depth_hist": {},
            "tree_nodes": {},
            "tree_sum_depths": {},
            "root": {},
            "depth": {},
            "parent": {},
            "special_components": [],
        }
        clo, harm = core_source_scores(0, dist, forest, 5)
        self.assertAlmostEqual(clo, 0.4, places=10)
        self.assertAlmostEqual(harm, 1.0 + 0.5 + 1.0 / 3.0 + 0.25, places=10)


class TestPeeledSourceScores(unittest.TestCase):
    def test_single_leaf(self):
        """Core node 0, leaf 1 attached to 0. Source 1 (peeled, delta=1).
        d(1,0)=1. closeness=(2-1)/1=1.0. harmonic=1/1=1.0."""
        edges = [(0, 1)]
        indptr, indices = build_adjacency(2, edges)
        is_core = np.array([True, False])
        core_indptr, core_indices = build_core_adjacency(indptr, indices, is_core)
        adj_sets = build_adj_sets(edges)
        mask, removed = peel_two_core(indptr, indices, 2)
        forest = {
            "tree_depth_hist": {0: {1: 1}},
            "tree_nodes": {0: {1}},
            "tree_sum_depths": {0: 1},
            "root": {1: 0},
            "depth": {1: 1},
            "parent": {1: 0},
            "special_components": [],
        }
        dist = core_bfs(0, core_indptr, core_indices)
        clo, harm = peeled_source_scores(1, forest, dist, adj_sets, 2)
        self.assertAlmostEqual(clo, 1.0, places=10)
        self.assertAlmostEqual(harm, 1.0, places=10)


class TestComputeToyGraph(unittest.TestCase):
    """End-to-end: small graph, verify against manual BFS."""

    def _manual_closeness_harmonic(self, n, edges, source):
        """Reference: full-graph BFS from source."""
        from collections import deque

        adj = [[] for _ in range(n)]
        for a, b in edges:
            adj[a].append(b)
            adj[b].append(a)
        dist = [-1] * n
        dist[source] = 0
        q = deque([source])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if dist[v] < 0:
                    dist[v] = dist[u] + 1
                    q.append(v)
        reachable = [d for d in dist if d > 0]
        if not reachable:
            return 0.0, 0.0
        sum_d = sum(reachable)
        clo = (n - 1) / sum_d
        harm = sum(1.0 / d for d in reachable)
        return clo, harm

    def test_path_graph(self):
        """Path 0-1-2-3-4. Source 0 is peeled (degree 1)."""
        # Build a minimal DB for this test
        import tempfile
        import sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
        edges = [
            ("a", "b", "test_edge"),
            ("b", "c", "test_edge"),
            ("c", "d", "test_edge"),
            ("d", "e", "test_edge"),
        ]
        conn.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)", edges)
        conn.execute(
            "CREATE TABLE graph_analytics (entity_name TEXT, metric TEXT, value TEXT, computed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO graph_analytics VALUES (?, ?, ?, ?)",
            ("a", "closeness_centrality", "0", "2026-01-01"),
        )
        conn.commit()
        conn.close()

        results, info = compute(db_path)
        self.assertIn("a", results)

        # Source "a" is a leaf (degree 1), peeled
        # Manual: distances to b,c,d,e = 1,2,3,4. sum=10. clo=4/10=0.4
        clo_ref, harm_ref = self._manual_closeness_harmonic(5, [(0, 1), (1, 2), (2, 3), (3, 4)], 0)
        self.assertAlmostEqual(results["a"]["closeness"], clo_ref, places=10)
        self.assertAlmostEqual(results["a"]["harmonic"], harm_ref, places=10)

        Path(db_path).unlink()

    def test_siblings_through_root(self):
        """Peeled source whose siblings hang off the same root: the
        own-tree BFS must traverse THROUGH the root (the tree's only
        core contact) or every sibling is lost. 2026-10-15 audit: the
        pre-fix loop blocked at the root and dropped 6 nodes at d=2
        (3.0 harmonic) on Luxury_Goods.
        """
        import sqlite3
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
        # core cycle r-h-h2-r; leaves l1,l2,l3 on r
        edges = [
            ("r", "h", "test_edge"),
            ("h", "h2", "test_edge"),
            ("h2", "r", "test_edge"),
            ("r", "l1", "test_edge"),
            ("r", "l2", "test_edge"),
            ("r", "l3", "test_edge"),
        ]
        conn.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)", edges)
        conn.execute(
            "CREATE TABLE graph_analytics "
            "(entity_name TEXT, metric TEXT, value TEXT, computed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO graph_analytics VALUES (?, ?, ?, ?)",
            ("l1", "closeness_centrality", "0", "2026-01-01"),
        )
        conn.commit()
        conn.close()
        try:
            results, info = compute(db_path)
            # n=6; l1: d(r)=1, d(h)=d(h2)=d(l2)=d(l3)=2 (r-h2 edge!) -> sum=9
            self.assertAlmostEqual(results["l1"]["closeness"], 5 / 9, places=10)
            self.assertAlmostEqual(results["l1"]["harmonic"], 1.0 + 0.5 * 4, places=10)
        finally:
            Path(db_path).unlink()

    def test_index_only_entity_not_in_universe(self):
        """An entity whose only edge is listed_on_index must NOT join the
        projection: it would sit as a phantom isolate and inflate the
        (n-1) closeness normalization (2026-10-15 audit: 593 isolates
        scaled every live closeness score by 22066/21473).
        """
        import sqlite3
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
        edges = [
            ("a", "b", "test_edge"),
            ("b", "c", "test_edge"),
            ("z", "a", "listed_on_index"),
        ]
        conn.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)", edges)
        conn.execute(
            "CREATE TABLE graph_analytics "
            "(entity_name TEXT, metric TEXT, value TEXT, computed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO graph_analytics VALUES (?, ?, ?, ?)",
            ("a", "closeness_centrality", "0", "2026-01-01"),
        )
        conn.commit()
        conn.close()
        try:
            results, info = compute(db_path)
            self.assertNotIn("z", results)
            self.assertEqual(info["n_nodes"], 3)  # z absent from the universe
            # a: d(b)=1, d(c)=2 -> clo=(3-1)/3
            self.assertAlmostEqual(results["a"]["closeness"], 2 / 3, places=10)
        finally:
            Path(db_path).unlink()


if __name__ == "__main__":
    unittest.main()
