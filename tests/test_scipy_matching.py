from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest
from scipy.sparse import csr_matrix

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
    edges = [
        ("S1", "C1"),
        ("S1", "C2"),
        ("S2", "C2"),
        ("S2", "C3"),
        ("S3", "C3"),
        ("S3", "C4"),
    ]
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
                "INSERT INTO graph_edges VALUES (?,?,?)",
                (f"I{i}", f"T{j}", "supplier_to"),
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


def test_s3b_covering_assignment():
    """S3b toy oracle (archived scipy_csgraph_scale_lanes §S3b): two
    targets sharing one investor → exactly one excluded, the rest fully
    assigned to their minimum-stake investor."""
    inc = {
        "investors": ["i1", "i2"],
        "targets": ["tA", "tB", "tC"],
        "matrix": csr_matrix(
            (
                np.array([0.9, 0.5, 0.8, 0.7]),
                (np.array([0, 0, 0, 1]), np.array([0, 1, 2, 2])),
            ),
            shape=(2, 3),
        ),
        "n_investors": 2,
        "n_targets": 3,
    }
    res = sm.s3_assign_primary(inc)
    assert res["admissible"]
    assert res["n_excluded"] == 1
    assert res["excluded"][0]["target"] == "tB"  # tA,tC keep the covering
    got = {r["target"]: (r["investor"], r["stake"]) for r in res["assignment"]}
    # minimum-stake orientation: tC has i1@0.8 vs i2@0.7 → i2 wins
    assert got["tA"] == ("i1", 0.9)
    assert got["tC"] == ("i2", 0.7)
    assert res["n_assigned"] == 2


def test_s3b_already_admissible_passthrough():
    """No Hall violation → zero exclusions, full assignment."""
    inc = {
        "investors": ["i1", "i2"],
        "targets": ["tA", "tB"],
        "matrix": csr_matrix(
            (np.array([1.0, 1.0]), (np.array([0, 1]), np.array([0, 1]))), shape=(2, 2)
        ),
        "n_investors": 2,
        "n_targets": 2,
    }
    res = sm.s3_assign_primary(inc)
    assert res["admissible"] and res["n_excluded"] == 0 and res["n_assigned"] == 2


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


def test_s3_sector_toy():
    """s3_sector_unlock toy oracle (proposal §2): two targets sharing one
    investor within ONE sector → exactly one excluded per cut, the rest
    assigned to their minimum-stake investor. Cuts are INDEPENDENT: the
    same pathology in a second sector excludes its own Hall violator,
    and a cross-sector investor (k1) is assigned in BOTH cuts at min
    stake (at most once per cut — matching property)."""
    investors = ["i1", "i2", "j1", "j2", "k1"]
    targets = ["tA", "tB", "tC", "uA", "uB", "uC"]
    # Alpha: the test_s3b_covering_assignment shape verbatim; Beta: same
    # shape on j-investors; k1 undercuts both tC and uC.
    entries = [
        ("i1", "tA", 0.9),
        ("i1", "tB", 0.5),
        ("i1", "tC", 0.8),
        ("i2", "tC", 0.7),
        ("j1", "uA", 0.9),
        ("j1", "uB", 0.5),
        ("j1", "uC", 0.8),
        ("j2", "uC", 0.7),
        ("k1", "tC", 0.6),
        ("k1", "uC", 0.6),
    ]
    inv_ix = {n: i for i, n in enumerate(investors)}
    tgt_ix = {n: j for j, n in enumerate(targets)}
    mat = csr_matrix(
        (
            np.array([w for _, _, w in entries]),
            (
                np.array([inv_ix[s] for s, _, _ in entries]),
                np.array([tgt_ix[t] for _, t, _ in entries]),
            ),
        ),
        shape=(len(investors), len(targets)),
    )
    sectors = {
        "tA": "Alpha",
        "tB": "Alpha",
        "tC": "Alpha",
        "uA": "Beta",
        "uB": "Beta",
        "uC": "Beta",
    }
    inc = {
        "investors": investors,
        "targets": targets,
        "matrix": mat,
        "n_investors": len(investors),
        "n_targets": len(targets),
    }
    res = sm.s3_assign_primary_sector(inc, sectors)
    assert res["admissible"]
    t = res["totals"]
    assert t["n_assigned"] == 4 and t["n_excluded"] == 2
    assert t["n_sectors"] == 2 and t["n_classified"] == 6
    assert {r["target"] for r in res["sectors"]["Alpha"]["excluded"]} == {"tB"}
    assert {r["target"] for r in res["sectors"]["Beta"]["excluded"]} == {"uB"}
    got = {
        r["target"]: (r["investor"], r["stake"])
        for s in res["sectors"].values()
        for r in s["assignment"]
    }
    # minimum-stake orientation per cut: k1 (0.6) undercuts i1/i2 on tC
    # and j1/j2 on uC — assigned in BOTH sector cuts
    assert got["tA"] == ("i1", 0.9)
    assert got["tC"] == ("k1", 0.6)
    assert got["uA"] == ("j1", 0.9)
    assert got["uC"] == ("k1", 0.6)
    # unclassified targets: skipped by the cut, reported not failed
    inc_x = {
        "investors": ["i1"],
        "targets": ["tA", "tX"],
        "matrix": csr_matrix(
            (np.array([1.0, 1.0]), (np.array([0, 0]), np.array([0, 1]))), shape=(1, 2)
        ),
        "n_investors": 1,
        "n_targets": 2,
    }
    res_x = sm.s3_assign_primary_sector(inc_x, {"tA": "Alpha"})
    assert res_x["totals"]["n_unclassified"] == 1
    assert res_x["unclassified_targets"] == ["tX"]
    assert res_x["totals"]["n_assigned"] == 1


def test_s3_sector_live():
    """Live acceptance (proposal §2): per-sector cuts assign ALL classified
    invested_in targets with zero exclusions; repeated runs (and JSON)
    are byte-stable. Skipped when the live DB is absent."""
    dbp = Path("memory/research.db")
    if not dbp.exists():
        pytest.skip("live DB absent")
    inc = sm.load_investment_incidence(str(dbp))
    sectors = sm.load_target_sectors(str(dbp))
    res = sm.s3_assign_primary_sector(inc, sectors)
    t = res["totals"]
    assert t["n_assigned"] == t["n_classified"], (
        f"coverage {t['n_assigned']}/{t['n_classified']} — cuts leaked a target"
    )
    assert t["n_excluded"] == 0
    assert res["admissible"]
    # determinism: re-run on a fresh load, identical result + stable JSON
    res2 = sm.s3_assign_primary_sector(sm.load_investment_incidence(str(dbp)), sectors)
    assert res == res2
    assert json.dumps(res, sort_keys=True) == json.dumps(res2, sort_keys=True)


if __name__ == "__main__":
    test_s2_simple()
    test_s2_no_overlap()
    test_s3_n_greater_than_m()
    test_s3_possible()
    test_s3_hall_collision()
    test_live()
    print("\nall ok")


def test_s3_matching_size_is_not_the_column_count():
    """graph_lane_unlocks S1: s3_feasibility counted len() of scipy's
    permutation array = COLUMN count, never the match size. This shape
    makes the bug flip the VERDICT: 3 investors (I1 and I2 both only
    reach T1) over exactly 3 targets, so m == n and the true max
    matching is 2. Bug: len(perm)=3 == n=3 → full_matching True (wrong).
    Fixed: matched 2 < 3 → False, reason carries the matched count.
    """
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    for s, t in [("I1", "T1"), ("I2", "T1"), ("I3", "T2")]:
        conn.execute("INSERT INTO graph_edges VALUES (?,?,?)", (s, t, "supplier_to"))
    conn.commit()
    conn.close()
    inc = sm.load_supplier_incidence(str(db))
    res = sm.s3_feasibility(inc)
    assert res["max_matching_size"] == 2  # T3 unreachable; T1 serves one of I1/I2
    assert not res["full_matching"]  # the bug's len(perm)==3==n said True
    assert res["reason"] == "Hall collisions: max matching 2/3"
    db.unlink()
