from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from helpers.graph import scipy_matching as sm


def _make_db(edges):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    conn.executemany(
        "INSERT INTO graph_edges VALUES (?,?,?)",
        [(s, t, "supplier_to") for s, t in edges],
    )
    conn.commit()
    conn.close()
    return db


def test_s2_simple():
    # 3 suppliers, 4 consumers; perfect matching covers all 3 suppliers -> 75%
    edges = [("S1", "C1"), ("S1", "C2"), ("S2", "C2"), ("S2", "C3"), ("S3", "C3"), ("S3", "C4")]
    db = _make_db(edges)
    inc = sm.load_supplier_incidence(str(db))
    res = sm.s2_matching(inc)
    assert res["matching_size"] == 3, f"expected 3 got {res['matching_size']}"
    assert res["coverage_pct"] == 75.0, f"expected 75.0 got {res['coverage_pct']}"
    assert len(res["unmatched_consumers"]) == 1  # C4 unmatched
    assert len(res["zero_degree_consumers"]) == 0
    db.unlink()


def test_s2_no_overlap():
    # 2 suppliers, 2 consumers, disjoint -> matching=2, coverage=100%
    edges = [("S1", "C1"), ("S2", "C2")]
    db = _make_db(edges)
    inc = sm.load_supplier_incidence(str(db))
    res = sm.s2_matching(inc)
    assert res["matching_size"] == 2
    assert res["coverage_pct"] == 100.0
    db.unlink()


def test_s3_n_greater_than_m():
    # 5 investors, 3 targets -> impossible to have full matching
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    edges = [
        ("I1", "T1", "supplier_to"),
        ("I1", "T2", "supplier_to"),
        ("I2", "T2", "supplier_to"),
        ("I2", "T3", "supplier_to"),
        ("I3", "T3", "supplier_to"),
        ("I3", "T1", "supplier_to"),
        ("I4", "T1", "supplier_to"),
        ("I5", "T2", "supplier_to"),
    ]
    conn.executemany("INSERT INTO graph_edges VALUES (?,?,?)", edges)
    conn.commit()
    conn.close()
    inc = sm.load_supplier_incidence(str(db))
    res = sm.s3_feasibility(inc)
    assert not res["full_matching"]
    assert res["max_matching_size"] <= 3
    assert res["n_investors"] == 5
    assert res["n_targets"] == 3
    db.unlink()


def test_s3_possible():
    # 3 investors, 3 targets, complete -> perfect matching
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    for i in range(1, 4):
        for j in range(1, 4):
            conn.execute(
                "INSERT INTO graph_edges VALUES (?,?,?)", (f"I{i}", f"T{j}", "supplier_to")
            )
    conn.commit()
    conn.close()
    inc = sm.load_supplier_incidence(str(db))
    assert sm.s3_feasibility(inc)["full_matching"]
    db.unlink()


def test_s3_hall_collision():
    # Hall violator: 4 investors but only 3 targets with structure blocking
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    for s, t in [("I1", "T1"), ("I2", "T1"), ("I3", "T2"), ("I4", "T3")]:
        conn.execute("INSERT INTO graph_edges VALUES (?,?,?)", (s, t, "supplier_to"))
    conn.commit()
    conn.close()
    inc = sm.load_supplier_incidence(str(db))
    res = sm.s3_feasibility(inc)
    assert not res["full_matching"]
    assert res["max_matching_size"] == 3
    db.unlink()


def test_live():
    dbp = Path("memory/research.db")
    if not dbp.exists():
        raise AssertionError("live DB missing for test_live")
    inc = sm.load_supplier_incidence(str(dbp))
    res = sm.s2_matching(inc)
    assert "matching_size" in res
    assert "coverage_pct" in res
    assert res["n_suppliers"] > 0
    assert res["n_consumers"] > 0
    print(f"live S2: {res['matching_size']} / {res['n_consumers']} = {res['coverage_pct']}%")


if __name__ == "__main__":
    test_s2_simple()
    test_s2_no_overlap()
    test_s3_n_greater_than_m()
    test_s3_possible()
    test_s3_hall_collision()
    test_live()
    print("\nall ok")
