#!/usr/bin/env python3
"""igraph pilot bridge tests (2026-09-12).

- Toy 12n/16e parity vs tests/data/onager_toy_baseline.json (Onager numbers).
- Full-graph smoke: 1648 nodes / 19261 edges, PR+Louvain+Leiden timing.

Run with the isolated interpreter (igraph lives ONLY there)::

    /tmp/venv_igraph/bin/python -m pytest tests/test_igraph_bridge.py -x -q

(The in-tree command needs pyyaml + hypothesis + python-dotenv + numpy +
duckdb in that interpreter — conftest/addopts need the first four; duckdb
is imported at module level by ``helpers/graph/query.py``, which
``--apply`` persistence reaches through ``algorithms.write_analytics``.
Full recreate recipe: proposal §3.)

igraph is NOT in .venv/pyproject — the module degrades to a skip there.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ig = pytest.importorskip("igraph", reason="igraph only in /tmp/venv_igraph (pilot, not .venv)")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import igraph_bridge as B

#: Onager toy measurements (2026-09-12 eval session). The
#: /tmp/onager_baseline_raw.json original went with the /tmp space purge
#: 2026-09-12 — this fixture is the durable home.
BASELINE = Path(__file__).parent / "data" / "onager_toy_baseline.json"

# Toy graph — same definition as /tmp/eval_onager_baseline.md §1:
# 10 companies + 2 sectors; 10x BelongsTo w=1.0 + 6x competes w=2.0.
TOY_NAMES = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "TATAMOTORS",
    "MARUTI",
    "SUNPHARMA",
    "CIPLA",
    "SECTOR_FIN",
    "SECTOR_AUTO",
]
_FIN = "SECTOR_FIN"
_AUTO = "SECTOR_AUTO"
TOY_ROWS: list[tuple[str, str, str, float]] = [
    ("HDFCBANK", _FIN, "belongs_to", 1.0),
    ("ICICIBANK", _FIN, "belongs_to", 1.0),
    ("SBIN", _FIN, "belongs_to", 1.0),
    ("TATAMOTORS", _AUTO, "belongs_to", 1.0),
    ("MARUTI", _AUTO, "belongs_to", 1.0),
    ("TCS", _FIN, "belongs_to", 1.0),
    ("INFY", _FIN, "belongs_to", 1.0),
    ("RELIANCE", _AUTO, "belongs_to", 1.0),
    ("SUNPHARMA", _AUTO, "belongs_to", 1.0),
    ("CIPLA", _AUTO, "belongs_to", 1.0),
    ("TCS", "INFY", "competes_with", 2.0),
    ("HDFCBANK", "ICICIBANK", "competes_with", 2.0),
    ("TATAMOTORS", "MARUTI", "competes_with", 2.0),
    ("SUNPHARMA", "CIPLA", "competes_with", 2.0),
    ("RELIANCE", "TCS", "competes_with", 2.0),
    ("SBIN", "HDFCBANK", "competes_with", 2.0),
]


def _toy_graph():
    g, name_to_id = B.build_graph(TOY_ROWS)
    assert g.vcount() == 12 and g.ecount() == 16
    assert set(g.vs["name"]) == set(TOY_NAMES)
    assert sorted(g.es["weight"]) == [1.0] * 10 + [2.0] * 6
    return g, name_to_id


def test_build_graph_attrs_and_map():
    g, name_to_id = _toy_graph()
    assert len(name_to_id) == 12
    assert g.vs.find(name="INFY").index == name_to_id["INFY"]
    assert set(g.es["etype"]) == {"belongs_to", "competes_with"}


def test_toy_parity_vs_onager_baseline():
    """Toy parity vs the Onager baseline raw JSON (same 12n/16e graph)."""
    if not BASELINE.exists():
        pytest.skip("no /tmp/onager_baseline_raw.json")
    base = json.loads(BASELINE.read_text())["toy"]
    g, _ = _toy_graph()

    # PageRank: same top-4 set (order may differ slightly — weighted C vs
    # Onager converge differently on a 12-node graph), same mass.
    on_pr = base["pagerank"]["values"]
    ig_pr = B.weighted_pagerank(g)
    # Baseline values are rounded to 5dp (mass 0.99999), so tolerance is 1e-4.
    assert abs(sum(ig_pr.values()) - sum(on_pr.values())) < 1e-4
    assert set(sorted(ig_pr, key=ig_pr.get, reverse=True)[:4]) == set(
        sorted(on_pr, key=on_pr.get, reverse=True)[:4]
    )

    # Degree centrality is exact on both engines.
    on_deg = base["degree_centrality"]["values"]
    for node, val in on_deg.items():
        # Onager degree is unweighted count/(n-1); igraph weighted_degree is
        # strength/(n-1) — equal here only for weight-1.0 neighbourhoods, so
        # compare against the raw unweighted degree instead.
        deg = g.degree(g.vs.find(name=node).index)
        # Baseline rounded to 5dp — tolerance 1e-4.
        assert abs(deg / 11 - val) < 1e-4, node

    # WCC: single component of 12 on both.
    assert len(g.connected_components(mode="weak")) == 1
    assert set(base["weakly_connected_component"]["values"].values()) == {0.0}

    # Shortest path TCS->MARUTI visits the same nodes as the baseline BFS.
    found = B.weighted_shortest_path(g, "TCS", "MARUTI")
    assert found is not None
    path, _dist = found
    assert path == base["shortest_path_TCS_MARUTI"]

    # Louvain: 3 communities, comparable modularity (weighted Q differs by
    # engine: Onager 0.3321 vs igraph ~0.52 — same partition count).
    cmp_ = B.louvain_compare(g)
    assert cmp_["louvain"]["n_communities"] == 3
    assert cmp_["leiden"]["n_communities"] == 3

    # Eigenvector: igraph CONVERGES where Onager FAILED on this toy.
    assert "error" in base["eigenvector_centrality"]
    eig = B.weighted_eigenvector(g)
    assert len(eig) == 12 and all(v >= 0 for v in eig.values())


def test_weighted_shortest_path_per_label():
    g, _ = _toy_graph()
    # competes_with-only subgraph: TCS-INFY directly connected.
    found = B.weighted_shortest_path(g, "TCS", "INFY", edge_label="competes_with")
    assert found is not None and found[0] == ["TCS", "INFY"]
    # belongs_to-only subgraph: TCS and MARUTI disconnected (no shared sector).
    assert B.weighted_shortest_path(g, "TCS", "MARUTI", edge_label="belongs_to") is None
    # unknown endpoints -> None.
    assert B.weighted_shortest_path(g, "TCS", "NOPE") is None


def test_persist_dry_run_writes_nothing(tmp_path):
    n = B.persist("igraph_pagerank", {"A": 1.0, "B": 2.0}, apply=False)
    assert n == 2  # counted, not written


def test_persist_apply_delegates_to_write_analytics(monkeypatch):
    calls = {}

    def _fake(metric, values, conn=None):
        calls["metric"] = metric
        calls["n"] = len(values)
        return len(values)

    import types

    stub = types.ModuleType("helpers.graph.algorithms")
    stub.write_analytics = _fake
    monkeypatch.setitem(sys.modules, "helpers.graph.algorithms", stub)
    n = B.persist("igraph_leiden", {"A": 0, "B": 1}, apply=True)
    assert n == 2 and calls["metric"] == "igraph_leiden"


def test_routing_table():
    assert B.ROUTING["leiden_community"] == "IGRAPH"
    assert B.ROUTING["weighted_shortest_path"] == "IGRAPH"
    assert B.ROUTING["pagerank"] == "ONAGER_DEFAULT"
    assert B.ROUTING["louvain_community"] == "ONAGER_DEFAULT"


def test_leiden_seeded_deterministic():
    """Seeded Leiden/louvain_compare are reproducible (proposal S2/acceptance 3).

    igraph's community_leiden has no seed argument; unseeded full-graph runs
    measured Q 0.5326 vs 0.5308 (2026-09-12). The bridge seeds igraph's RNG
    per call (default 42) — two calls must agree exactly.
    """
    g, _ = _toy_graph()
    l1, q1 = B.leiden_communities(g, seed=42)
    l2, q2 = B.leiden_communities(g, seed=42)
    assert l1 == l2 and q1 == q2
    c1 = B.louvain_compare(g, seed=42)
    c2 = B.louvain_compare(g, seed=42)
    assert c1 == c2


def test_maxflow_mincut_chokepoint():
    """S5: maxflow/mincut with hand-checked numbers (proposal acceptance 5).

    Topology S-A(1)-T + S-B(5)-T: maxflow 6 (1 through A + 5 through B);
    mincut 6; 2 edge-disjoint and 2 vertex-disjoint S->T paths. The
    chokepoint variant S-A(1)-T alone collapses to 1 with connectivity 1.
    """
    rows6 = [
        ("S", "A", "competes_with", 1.0),
        ("A", "T", "competes_with", 1.0),
        ("S", "B", "competes_with", 5.0),
        ("B", "T", "competes_with", 5.0),
    ]
    g, _ = B.build_graph(rows6)
    r = B.maxflow_mincut(g, "S", "T")
    assert r is not None
    assert r["maxflow"] == 6.0 and r["mincut"] == 6.0
    assert r["edge_connectivity"] == 2 and r["vertex_connectivity"] == 2
    # Cut validity: every cut edge crosses the partition, capacity sum
    # equals the cut value, and both endpoints appear on opposite sides.
    caps = {frozenset((s, d)): w for s, d, _, w in rows6}
    side = set(r["source_side"])
    total = 0.0
    for a, b in r["cut_edges"]:
        assert (a in side) != (b in side), (a, b)
        total += caps[frozenset((a, b))]
    assert abs(total - r["mincut"]) < 1e-9
    assert "S" in side and "T" not in side

    g1, _ = B.build_graph([("S", "A", "competes_with", 1.0), ("A", "T", "competes_with", 1.0)])
    r1 = B.maxflow_mincut(g1, "S", "T")
    assert r1["maxflow"] == 1.0 and r1["mincut"] == 1.0
    assert r1["edge_connectivity"] == 1 and r1["vertex_connectivity"] == 1
    assert B.maxflow_mincut(g, "S", "NOPE") is None


def test_full_graph_smoke_pr_louvain_leiden_timing():
    """Full live-graph smoke: real SQLite projection, timed, bounds-checked."""
    rows = B.load_projection()
    # S7: bounds, not pins — the projection grows with the corpus
    # (19,261 edges / 1,648 endpoints at proposal time, 2026-09-12).
    assert 19_000 <= len(rows) <= 21_000, f"unexpected edge count {len(rows)}"
    t0 = time.perf_counter()
    g, _ = B.build_graph(rows)
    t_build = time.perf_counter() - t0
    assert 1_600 <= g.vcount() <= 1_800, f"unexpected node count {g.vcount()}"

    t0 = time.perf_counter()
    pr = B.weighted_pagerank(g)
    t_pr = time.perf_counter() - t0

    t0 = time.perf_counter()
    cmp_ = B.louvain_compare(g)
    t_lv = time.perf_counter() - t0

    t0 = time.perf_counter()
    labels, q = B.leiden_communities(g)
    t_le = time.perf_counter() - t0

    assert len(pr) == g.vcount() and len(labels) == g.vcount()
    # Seeded determinism at full scale (acceptance 3): two runs identical.
    l2, q2 = B.leiden_communities(g, seed=42)
    assert labels == l2 and q == q2
    print(
        f"\n[igraph smoke] build={t_build:.2f}s PR={t_pr:.2f}s "
        f"louvain+leiden-compare={t_lv:.2f}s leiden={t_le:.2f}s "
        f"Q_louvain={cmp_['louvain']['modularity']:.4f} Q_leiden={q:.4f} "
        f"ncomms={cmp_['leiden']['n_communities']}"
    )
    # Pilot budget: whole smoke (build + 3 analytics) under 60s.
    assert t_build + t_pr + t_lv + t_le < 60
