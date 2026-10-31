from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from helpers.graph import l1_mst as mst


def _make_db(edges):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, weight REAL)")
    conn.executemany(
        "INSERT INTO graph_edges VALUES (?,?,?,?)",
        [(s, t, "test_edge", w) for s, t, w in edges],
    )
    conn.commit()
    conn.close()
    return db


def test_triangle_oracle():
    # triangle weights 1,2,3 -> MST edges weights 1 and 2 (not 3)
    edges = [("A", "B", 1.0), ("B", "C", 2.0), ("A", "C", 3.0)]
    db = _make_db(edges)
    res = mst.mst_forest(str(db))
    ws = sorted(w for _, _, w in res["edges"])
    assert ws == [1.0, 2.0]
    assert res["n_components"] == 1
    assert res["n_forest_edges"] == 2
    db.unlink()


def test_square_diagonal():
    edges = [
        ("A", "B", 1.0),
        ("B", "C", 2.0),
        ("C", "D", 2.0),
        ("D", "A", 1.0),
        ("A", "C", 1.5),
    ]
    db = _make_db(edges)
    res = mst.mst_forest(str(db))
    ws = sorted(w for _, _, w in res["edges"])
    assert ws == [1.0, 1.0, 1.5]
    assert res["n_components"] == 1
    db.unlink()


def test_disconnected_components():
    edges = [("A", "B", 1.0), ("C", "D", 2.0)]
    db = _make_db(edges)
    res = mst.mst_forest(str(db))
    assert res["n_components"] == 2
    assert res["n_forest_edges"] == 2
    db.unlink()


def test_live_replay():
    from pathlib import Path

    dbp = Path("memory/research.db")
    if not dbp.exists():
        pytest.skip("live DB missing")
    res = mst.mst_forest(str(dbp))
    # Forest-not-tree reporting: a forest spanning V nodes with C components
    # has exactly V - C edges (Kruskal/Prim invariant). Pinning the exact
    # counts made this a snapshot that drifted on every graph/dedup arc;
    # the invariant + a loose regression band keeps the teeth without churn.
    assert res["n_forest_edges"] == res["n_nodes"] - res["n_components"], res
    assert res["n_components"] > 0, res
    assert res["n_nodes"] > 15000, res


def test_find_bridges_path_and_triangle():
    """graph_lane_unlocks S4: path a-b-c → both edges are bridges; adding
    c-a (triangle) → none are; the isolated node contributes nothing."""
    # a=0, b=1, c=2, isolated=3
    path = [(0, 1, 1.0), (1, 2, 1.0)]
    assert mst.find_bridges(4, path) == {(0, 1), (1, 2)}
    tri = path + [(0, 2, 1.0)]
    assert mst.find_bridges(4, tri) == set()
    assert mst.find_bridges(4, []) == set()


def test_resilience_crossreferences_forest():
    """On a path-with-shortcut projection, the MSF keeps 2 edges; the
    full-graph bridge count is 0 (the shortcut backs every pair) — the
    cross-reference must report 0 backbone ties as true bridges. With the
    shortcut removed, both forest edges are true bridges."""
    names = ["A", "B", "C"]
    edges_full = [(0, 1, 1.0), (1, 2, 2.0), (0, 2, 5.0)]
    # monkeypatch the loaders to a fixed projection (no DB)
    orig_load, orig_mst = mst.load_projection, mst.mst_forest
    mst.load_projection = lambda db=None: (names, list(edges_full))
    mst.mst_forest = lambda db=None: {
        "names": names,
        "n_nodes": 3,
        "n_components": 1,
        "n_forest_edges": 2,
        "edges": [("A", "B", 1.0), ("B", "C", 2.0)],
        "labels": __import__("numpy").zeros(3, dtype=int),
    }
    try:
        res = mst.resilience(db_path=None)
        assert res["n_forest_edges"] == 2
        assert res["n_bridges"] == 0  # triangle: every pair has an alternate
        assert res["n_backbone_bridges"] == 0
        # now drop the shortcut: the MSF edges become true bridges
        mst.load_projection = lambda db=None: (names, edges_full[:2])
        res2 = mst.resilience(db_path=None)
        assert res2["n_bridges"] == 2
        assert res2["n_backbone_bridges"] == 2
        assert ("A", "B") in [e[:2] for e in res2["bridge_edges"]]
    finally:
        mst.load_projection, mst.mst_forest = orig_load, orig_mst
