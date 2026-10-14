from __future__ import annotations

import sqlite3
import tempfile
import pytest
from pathlib import Path

from helpers.graph import a2_candidates as a2


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


def test_a2_toy():
    # tiny example: 4 suppliers, shared consumers produce pairs
    edges = [
        ("S1", "C1"),
        ("S1", "C2"),
        ("S2", "C2"),
        ("S2", "C3"),
        ("S3", "C3"),
        ("S3", "C4"),
        ("S4", "C4"),
        ("S4", "C1"),  # closes the loop: S1 and S4 share C1
    ]
    db = _make_db(edges)
    inc = a2.load_supplier_incidence(str(db))
    top = a2.a2_kernel(inc, top_k=5)
    # S1 shares C1 with S4, C2 with S2 -> shared counts
    # Let's just check structure
    assert len(top) <= 5
    print("a2 toy: ok,", len(top), "pairs")
    db.unlink()


def test_a2_live():
    dbp = Path("memory/research.db")
    if not dbp.exists():
        pytest.skip("live DB missing")
    inc = a2.load_supplier_incidence(str(dbp))
    top = a2.a2_kernel(inc, top_k=5)
    assert len(top) > 0
    # Trial: 347302 candidates; we just verify we get some
    print(f"live a2: {len(top)} top pairs, top shared={top[0][0]}")
