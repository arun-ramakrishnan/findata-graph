"""Person-name dedup tests (person_name_dedup proposal S1/S4).

Toy graph: person clusters (same-line folds), a HUF pair (same line —
folds), an individual-vs-HUF pair (FOLDS — 2026-10-16 operator ruling: a
HUF is defined by its Karta, so they are one person), an
individual-vs-TRUST pair (NEVER merges — the trust owns, the trustee
manages), and a company-shaped cluster (S1 over-inclusion — review,
routed back to the company arc).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from helpers.graph import name_dedup as nd
from helpers.graph import person_dedup as pd


@pytest.fixture()
def con(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "g.db")
    conn.execute(
        "CREATE TABLE entities ("
        " name TEXT PRIMARY KEY, entity_type TEXT NOT NULL,"
        " normalized_name TEXT, file_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE graph_edges ("
        " id INTEGER PRIMARY KEY, source TEXT NOT NULL REFERENCES entities(name),"
        " target TEXT NOT NULL REFERENCES entities(name), edge_type TEXT,"
        " weight REAL, properties TEXT, valid_from TEXT, valid_to TEXT,"
        " source_ref TEXT, symmetric INTEGER, created_at TEXT,"
        " agent_id TEXT, source_tier TEXT)"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    persons = [
        ("D. Sivanandhan", "person"),
        ("D Sivanandhan", "person"),
        ("Deepak Agarwal (HUF)", "person"),
        ("Deepak Agarwal HUF", "person"),
        ("Ramesh Agarwal", "person"),
        ("Ramesh Agarwal (HUF)", "person"),
        ("Suresh Agarwal", "person"),
        ("SURESH AGARWAL TRUST", "person"),
        ("A S Packers", "company"),
        ("A.S. Packers", "company"),
        ("Beta Co", "company"),
    ]
    conn.executemany("INSERT INTO entities VALUES (?, ?, 'x', NULL)", persons)
    edges = [
        ("D. Sivanandhan", "Beta Co", "supplier_to"),
        ("D Sivanandhan", "Beta Co", "supplier_to"),
        ("Deepak Agarwal (HUF)", "Beta Co", "invested_in"),
        ("Deepak Agarwal HUF", "Beta Co", "invested_in"),
        ("Ramesh Agarwal", "Beta Co", "invested_in"),
        ("Ramesh Agarwal (HUF)", "Beta Co", "invested_in"),
        ("Suresh Agarwal", "Beta Co", "invested_in"),
        ("SURESH AGARWAL TRUST", "Beta Co", "invested_in"),
        ("A S Packers", "Beta Co", "supplier_to"),
        ("A.S. Packers", "Beta Co", "supplier_to"),
    ]
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, symmetric)"
        " VALUES (?, ?, ?, 'vigil:x', 0)",
        edges,
    )
    conn.commit()
    yield conn
    conn.close()


def test_census_classes(con):
    cen = pd.person_census(con)
    fold_names = {c["canonical"] for c in cen["folds"]}
    # same-line person pair folds
    assert "D. Sivanandhan" in fold_names
    # same-line HUF pair folds (within the huf line — allowed)
    assert any(c["canonical"].startswith("Deepak Agarwal") for c in cen["folds"])
    # 2026-10-16 operator ruling: a HUF is defined by its Karta, so the
    # individual-vs-HUF pair FOLDS onto the plain person (not never_merge).
    ramesh_fold = [
        c
        for c in cen["folds"]
        if {v for v in c["variants"]} >= {"Ramesh Agarwal", "Ramesh Agarwal (HUF)"}
    ]
    assert len(ramesh_fold) == 1
    # ...and the survivor is the Karta, not the HUF string: with degrees
    # tied the old (degree, name) max() let 'X (HUF)' win by accident.
    assert ramesh_fold[0]["canonical"] == "Ramesh Agarwal"
    # TRUST stays the identity boundary — the trust owns, the trustee manages
    nm = {tuple(sorted(v["variants"])) for v in cen["never_merge"]}
    assert tuple(sorted(["Suresh Agarwal", "SURESH AGARWAL TRUST"])) in nm
    assert tuple(sorted(["Ramesh Agarwal", "Ramesh Agarwal (HUF)"])) not in nm
    # company-shaped over-inclusion routes to review, not folds
    assert not any("Packers" in c["canonical"] for c in cen["folds"])
    # (2026-10-16 operator ruling: company markers VETO the person shape,
    # so Packers-class clusters are company-census stock again — the
    # person census carries none, and its review partition is empty)
    assert not any("Packers" in " ".join(cs["variants"]) for cs in cen["company_shaped"])
    company_keys = {c["key"] for c in nd.census(con)["clusters"]}
    assert "a s packers" in company_keys


def test_plan_and_apply_via_shared_machinery(con, tmp_path: Path):
    plan = tmp_path / "person_plan.txt"
    cen = pd.person_census(con)
    pd.write_plan(cen, plan)
    assert plan.with_suffix(".jsonl").exists()
    # gated: no --yes, no write
    with pytest.raises(SystemExit, match="gated"):
        nd.apply_plan(con, plan, yes=False, skip_persons=False)
    res = nd.apply_plan(con, plan, yes=True, skip_persons=False)
    assert res["zero_loss_ok"] is True
    # person variants folded to one spelling; HUF line respected
    left = {
        r[0]
        for r in con.execute("SELECT name FROM entities WHERE name LIKE '%Sivanandhan%'").fetchall()
    }
    assert len(left) == 1
    deepak = {
        r[0]
        for r in con.execute(
            "SELECT name FROM entities WHERE name LIKE 'Deepak Agarwal%'"
        ).fetchall()
    }
    assert len(deepak) == 1
    # the individual-vs-HUF pair FOLDS (2026-10-16 ruling: HUF == its Karta)
    ramesh = {
        r[0]
        for r in con.execute(
            "SELECT name FROM entities WHERE name LIKE 'Ramesh Agarwal%'"
        ).fetchall()
    }
    assert len(ramesh) == 1
    # the TRUST pair is untouched (never-merge section, not folds)
    suresh = {
        r[0]
        for r in con.execute(
            "SELECT name FROM entities WHERE name LIKE '%Suresh Agarwal%'"
        ).fetchall()
    }
    assert len(suresh) == 2
    # company-shaped cluster untouched
    packers = {
        r[0]
        for r in con.execute("SELECT name FROM entities WHERE name LIKE '%Packers%'").fetchall()
    }
    assert len(packers) == 2


def test_check_gate_baseline(con, tmp_path: Path):
    """The gated count EXCLUDES never_merge (2026-10-16 operator ruling).

    never_merge is a correct refusal that tracks filings volume, not code
    quality, so it is reported but never gates. This test pins that: the
    baseline is computed the way check() computes it, and a trust pair in
    the fixture therefore cannot move the number.
    """
    base = tmp_path / "baseline.txt"
    cen = pd.person_census(con)
    gated = len(cen["folds"]) + len(cen["company_shaped"])
    assert cen["never_merge"], "fixture must keep a trust pair for this test to mean anything"
    assert gated < len(cen["folds"]) + len(cen["never_merge"]) + len(cen["company_shaped"])
    base.write_text(str(gated) + "\n")
    assert pd.check(con, base) == 0
    base.write_text(str(gated - 1) + "\n")
    assert pd.check(con, base) == 1
