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
