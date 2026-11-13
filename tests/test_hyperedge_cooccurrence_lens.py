"""Tests for helpers/maintenance/hyperedge_cooccurrence_lens.py."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance.hyperedge_cooccurrence_lens import (  # noqa: E402
    cooccurrence_pairs,
    hub_members,
    load_labels,
)

LABELS = {
    "group": {
        "vigil:rpt:TCS": {"A", "B", "C", "D"},
        "vigil:rpt:VOLTAS": {"B", "C", "D", "E"},
        "family:Tata": {"A", "B", "C", "D", "E", "F"},
        "vigil:rpt:WIPRO": {"X", "Y"},
    },
    "event": {
        "jv:1": {"A", "B"},
        "jv:2": {"A", "B", "C"},
    },
}


@pytest.fixture()
def conn():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY, edge_type TEXT, label TEXT)")
    con.execute("CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT)")
    eid = 0
    for etype, lab in LABELS.items():
        for label, members in lab.items():
            eid += 1
            con.execute(
                "INSERT INTO hyper_edges VALUES (?, ?, ?)",
                (eid, etype, label),
            )
            for m in members:
                con.execute(
                    "INSERT INTO hyper_incidences VALUES (?, ?)",
                    (eid, m),
                )
    yield con
    con.close()


def test_load_labels_shapes(conn):
    labels = load_labels(conn)
    assert set(labels) == {"group", "event"}
    assert labels["group"]["vigil:rpt:TCS"] == {"A", "B", "C", "D"}
    assert set(labels["event"]) == {"jv:1", "jv:2"}


def test_load_labels_type_filter(conn):
    labels = load_labels(conn, types=("event",))
    assert set(labels) == {"event"}


def test_cooccurrence_pairs_ranked(conn):
    labels = load_labels(conn)["group"]
    pairs = cooccurrence_pairs(labels, min_overlap=2)
    assert len(pairs) == 3
    # highest overlap: family:Tata vs TCS (4 shared)
    first = pairs[0]
    assert first["a"] == "family:Tata"
    assert first["b"] == "vigil:rpt:TCS"
    assert first["overlap"] == 4
    # family:Tata vs VOLTAS (4 shared)
    assert pairs[1]["overlap"] == 4
    # TCS vs VOLTAS (3 shared)
    assert pairs[2] == {
        "a": "vigil:rpt:TCS",
        "b": "vigil:rpt:VOLTAS",
        "overlap": 3,
        "jaccard": pytest.approx(3 / 5),
        "size_a": 4,
        "size_b": 4,
    }


def test_cooccurrence_min_overlap(conn):
    pairs = cooccurrence_pairs(load_labels(conn)["group"], min_overlap=4)
    assert all(p["overlap"] >= 4 for p in pairs)


def test_hub_members(conn):
    hubs = hub_members(load_labels(conn)["group"], min_labels=2)
    names = {h["member"]: h["labels"] for h in hubs}
    assert names["B"] == 3
    assert names["C"] == 3
    assert names["A"] == 2
    assert "X" not in names
