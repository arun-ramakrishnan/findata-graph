#!/usr/bin/env python3
"""Country exposure bundle units (snapshot_trust_country_exposure S3).

Hand-built mini DuckDB cache — no live graph cache, no integration
marker. The live endpoint wiring is pinned in test_api_graph_live.py
(TestCountryEndpointsLive)."""

from __future__ import annotations

from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from helpers.graph.query import country_companies, country_exposure_bundle  # noqa: E402


@pytest.fixture()
def mini(tmp_path: Path):
    """Two countries, three companies, three listings."""
    ddb = tmp_path / "mini.duckdb"
    con = duckdb.connect(str(ddb))
    con.execute('CREATE TABLE v_country(id BIGINT, "name" VARCHAR)')
    con.execute("INSERT INTO v_country VALUES (1, 'india'), (2, 'usa')")
    con.execute(
        'CREATE TABLE v_company(id BIGINT, "name" VARCHAR, '
        "sector_classification VARCHAR, market_cap VARCHAR, ticker VARCHAR)"
    )
    con.execute(
        "INSERT INTO v_company VALUES "
        "(10, 'Alpha', 'Auto', 'large_cap', 'ALPHA.NS'),"
        "(11, 'Beta', 'Auto', 'small_cap', 'BETA.NS'),"
        "(12, 'Gamma', 'Pharma', NULL, 'GMMA')"
    )
    con.execute("CREATE TABLE e_listed_in(company_id BIGINT, country_id BIGINT)")
    con.execute("INSERT INTO e_listed_in VALUES (10, 1), (11, 1), (12, 2)")
    yield con
    con.close()


def test_country_companies_resolves_and_lists(mini):
    rows = country_companies(mini, "INDIA")  # case-insensitive
    assert rows is not None
    assert [r["company"] for r in rows] == ["Alpha", "Beta"]
    assert rows[0]["market_cap"] == "large_cap"


def test_country_companies_market_cap_filter(mini):
    rows = country_companies(mini, "india", market_cap="small_cap")
    assert rows is not None
    assert [r["company"] for r in rows] == ["Beta"]


def test_country_companies_unknown_returns_none(mini):
    assert country_companies(mini, "atlantis") is None


def test_bundle_summary_histogram(mini):
    b = country_exposure_bundle(mini)
    by_name = {r["country"]: r for r in b["countries"]}
    assert by_name["india"]["companies"] == 2
    assert by_name["india"]["large_cap"] == 1
    assert by_name["india"]["small_cap"] == 1
    assert by_name["india"]["unbucketed"] == 0
    assert by_name["usa"]["companies"] == 1
    assert by_name["usa"]["unbucketed"] == 1  # Gamma has no bucket


def test_bundle_matrix_counts(mini):
    b = country_exposure_bundle(mini)
    m = {(r["country"], r["sector"]): r["companies"] for r in b["matrix"]}
    assert m[("india", "Auto")] == 2
    assert m[("usa", "Pharma")] == 1
    # NULL sectors are excluded from the matrix, not counted as a bucket
    assert all(r["sector"] is not None for r in b["matrix"])
