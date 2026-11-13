"""Tests for helpers/maintenance/mnc_dyad_pass.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.mnc_dyad_pass import (  # noqa: E402
    MNC_PARENTS,
    _members_for_filer,
    run_pass,
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
    con.execute(
        """
        CREATE TABLE hyper_edges (
            id INTEGER, edge_type VARCHAR, label VARCHAR,
            source_ref VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE hyper_incidences (
            edge_id INTEGER, entity_name VARCHAR
        )
        """
    )
    companies = [
        ("AB India", "company", "Industrials"),
        ("AB Pakistan", "company", "Industrials"),
        ("AB Intl", "company", "Industrials"),
        ("Tata Motors", "company", "Automobiles"),
        ("Tata Steel", "company", "Metals"),
        ("Siemens India", "company", "Industrials"),
        ("Siemens AG", "company", "Industrials"),
        ("Person", "person", None),
    ]
    con.executemany("INSERT INTO entities VALUES (?, ?, ?)", companies)
    edges = [
        (1, "AB India", "AB Pakistan", "same_group", "vigil:rpt:ABB"),
        (2, "AB India", "AB Intl", "same_group", "vigil:rpt:ABB"),
        (3, "AB Pakistan", "AB Intl", "same_group", "vigil:rpt:ABB"),
        (4, "Tata Motors", "Tata Steel", "same_group", "vigil:rpt:TATASTEEL"),
        (5, "Tata Motors", "Person", "same_group", "vigil:rpt:TATASTEEL"),
        (6, "Siemens India", "Siemens AG", "same_group", "vigil:rpt:SIEMENS"),
        (7, "Siemens India", "AB India", "same_group", "vigil:rpt:SIEMENS"),
    ]
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?)", edges)
    con.execute("INSERT INTO hyper_edges VALUES (1, 'group', 'vigil:rpt:ABB', 'x')")
    con.execute("INSERT INTO hyper_incidences VALUES (1, 'AB Intl')")
    return con


def test_members_for_filer_company_only():
    con = _make_con()
    assert _members_for_filer(con, "ABB") == ["AB India", "AB Intl", "AB Pakistan"]
    # person edge never appears; SIEMENS label includes AB India because
    # the fixture wires a Siemens-AB cross edge under the SIEMENS ref
    assert _members_for_filer(con, "SIEMENS") == ["AB India", "Siemens AG", "Siemens India"]
    con.close()


def test_run_pass_matches_mnc_filers():
    con = _make_con()
    profiles, uncharted = run_pass(con)
    symbols = {p["symbol"] for p in profiles}
    assert {"ABB", "SIEMENS"} <= symbols
    ab = next(p for p in profiles if p["symbol"] == "ABB")
    assert ab["parent"] == "ABB Ltd"
    assert ab["country"] == "CH"
    assert ab["size"] == 3
    assert ab["internal_dyads"] == 3
    assert ab["density"] == 1.0
    # fixture has no other filer hyperedge sharing an ABB member
    assert ab["bridge_labels"] == []
    con.close()


def test_run_pass_nonmnc_ticker_not_matched():
    con = _make_con()
    profiles, _ = run_pass(con)
    # TATASTEEL is not in MNC_PARENTS → no profile for it
    assert all(p["symbol"] != "TATASTEEL" for p in profiles)
    con.close()


def test_uncharted_reports_absent_symbols():
    con = _make_con()
    _, uncharted = run_pass(con)
    # every MNC_PARENTS symbol not observed in this tiny store is uncharted
    observed = {"ABB", "SIEMENS"}
    assert set(uncharted) == set(MNC_PARENTS) - observed
    con.close()


def test_json_roundtrip():
    con = _make_con()
    profiles, uncharted = run_pass(con)
    payload = {"profiles": profiles, "uncharted": uncharted}
    text = json.dumps(payload)
    loaded = json.loads(text)
    assert loaded["profiles"][0]["parent"] == "ABB Ltd"
    con.close()
