"""Tests for helpers/maintenance/psu_dyad_pass.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.psu_dyad_pass import (  # noqa: E402
    PSU_PARENTS,
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
        ("SBI", "company", "Financials"),
        ("SBI Cards", "company", "Financials"),
        ("SBI Life", "company", "Financials"),
        ("SBI DFHI", "company", "Financials"),
        ("NHPC", "company", "Utilities"),
        ("NTPC", "company", "Utilities"),
        ("NTPC Green", "company", "Utilities"),
        ("Person", "person", None),
    ]
    con.executemany("INSERT INTO entities VALUES (?, ?, ?)", companies)
    edges = [
        (1, "SBI", "SBI Cards", "same_group", "vigil:rpt:SBIN"),
        (2, "SBI", "SBI Life", "same_group", "vigil:rpt:SBIN"),
        (3, "SBI Cards", "SBI Life", "same_group", "vigil:rpt:SBIN"),
        (4, "SBI", "Person", "same_group", "vigil:rpt:SBIN"),
        (5, "NHPC", "NTPC", "same_group", "vigil:rpt:NTPC"),
        (6, "NTPC", "NTPC Green", "same_group", "vigil:rpt:NTPC"),
        (7, "NHPC", "NTPC Green", "same_group", "vigil:rpt:NTPC"),
    ]
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?)", edges)
    con.execute("INSERT INTO hyper_edges VALUES (1, 'group', 'vigil:rpt:SBIN', 'x')")
    con.execute("INSERT INTO hyper_incidences VALUES (1, 'SBI Life')")
    return con


def test_members_for_filer_company_only():
    con = _make_con()
    assert _members_for_filer(con, "SBIN") == ["SBI", "SBI Cards", "SBI Life"]
    # person edge never appears
    assert "Person" not in _members_for_filer(con, "SBIN")
    con.close()


def test_run_pass_matches_psu_filers():
    con = _make_con()
    profiles, uncharted = run_pass(con)
    symbols = {p["symbol"] for p in profiles}
    assert {"SBIN", "NTPC"} <= symbols
    sbin = next(p for p in profiles if p["symbol"] == "SBIN")
    assert sbin["ministry"] == "State Bank group"
    assert sbin["level"] == "central"
    assert sbin["size"] == 3
    assert sbin["internal_dyads"] == 3
    assert sbin["density"] == 1.0
    con.close()


def test_run_pass_nonpsu_ticker_not_matched():
    con = _make_con()
    profiles, _ = run_pass(con)
    # NTPC label covers NHPC/NTPC/NTPC Green via cross edges; only PSU
    # symbols in PSU_PARENTS are profiled. SBICARD is not wired in the
    # fixture store, so it must appear as uncharted, never profiled.
    assert all(p["symbol"] != "SBICARD" for p in profiles)
    con.close()


def test_uncharted_reports_absent_symbols():
    con = _make_con()
    _, uncharted = run_pass(con)
    observed = {"SBIN", "NTPC"}
    assert set(uncharted) == set(PSU_PARENTS) - observed
    con.close()


def test_json_roundtrip():
    con = _make_con()
    profiles, uncharted = run_pass(con)
    payload = {"profiles": profiles, "uncharted": uncharted}
    text = json.dumps(payload)
    loaded = json.loads(text)
    assert loaded["profiles"][0]["ministry"] == "State Bank group"
    con.close()
