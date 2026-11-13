"""Tests for helpers/maintenance/jv_dyad_census.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.jv_dyad_census import (  # noqa: E402
    _is_jv_name,
    run_census,
)


def _make_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    con.execute(
        """
        CREATE TABLE entities (
            name VARCHAR, entity_type VARCHAR, sector_classification VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE graph_edges (
            id INTEGER, source VARCHAR, target VARCHAR,
            edge_type VARCHAR, source_ref VARCHAR
        )
        """
    )
    companies = [
        ("Adani Wilmar", "company", None),
        ("Renuka Sugars", "company", None),
        ("Wilmar West Africa", "company", None),
        ("ABC Joint Venture", "company", None),
        ("XYZ Power Private", "company", None),
        ("Solo Co", "company", None),
        ("Person", "person", None),
    ]
    con.executemany("INSERT INTO entities VALUES (?, ?, ?)", companies)
    edges = [
        (1, "Adani Wilmar", "Renuka Sugars", "same_group", "vigil:rpt:AWL"),
        (2, "Adani Wilmar", "Wilmar West Africa", "same_group", "vigil:rpt:AWL"),
        (3, "ABC Joint Venture", "XYZ Power Private", "same_group", "vigil:rpt:ABC"),
        (4, "ABC Joint Venture", "Person", "same_group", "vigil:rpt:ABC"),
        (5, "Adani Wilmar", "Wilmar West Africa", "jv_with", "derive:relations:JV"),
        (6, "ABC Joint Venture", "XYZ Power Private", "jv_with", "derive:relations:JV"),
    ]
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?)", edges)
    return con


def test_is_jv_name():
    assert _is_jv_name("ABC Joint Venture")
    assert _is_jv_name("XYZ Power Private")
    assert _is_jv_name("JV Partners")
    assert not _is_jv_name("Solo Co")
    assert not _is_jv_name("Person")


def test_census_name_hits():
    con = _make_con()
    c = run_census(con)
    assert "ABC Joint Venture" in c["name_hits"]
    assert "XYZ Power Private" in c["name_hits"]
    # Adani Wilmar / Renuka Sugars don't carry JV-words → not name hits
    assert "Adani Wilmar" not in c["name_hits"]
    con.close()


def test_census_anchors():
    con = _make_con()
    c = run_census(con)
    assert c["anchors"]["AWL"] == ["Adani Wilmar", "Renuka Sugars", "Wilmar West Africa"]
    assert c["anchors"]["RENUKA"] == []
    con.close()


def test_census_pairs():
    con = _make_con()
    c = run_census(con)
    pairs = {(p["source"], p["target"]): p["flags"] for p in c["pairs"]}
    assert ("ABC Joint Venture", "XYZ Power Private") in pairs
    assert pairs[("ABC Joint Venture", "XYZ Power Private")] == ["name-hit"]
    con.close()


def test_json_roundtrip():
    con = _make_con()
    c = run_census(con)
    text = json.dumps(c)
    loaded = json.loads(text)
    assert "ABC Joint Venture" in loaded["name_hits"]
    con.close()
