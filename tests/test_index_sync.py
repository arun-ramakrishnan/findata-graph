#!/usr/bin/env python3
"""Tests for helpers/maintenance/index_sync.py (index-membership fill S1).

Pure layers (parser, detail-page discovery, Last-Modified) plus a temp
DuckDB round-trip for fold idempotency and the latest-vintage view.
"""

from __future__ import annotations

from datetime import date

import duckdb

from helpers.maintenance import index_sync as ix

CSV = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
)


# --------------------------------------------------------------------------- #
# Parsing / discovery (no DB)                                                 #
# --------------------------------------------------------------------------- #
def test_parse_constituent_csv_maps_columns_and_bom():
    rows = ix.parse_constituent_csv("\ufeff" + CSV)
    assert rows == [
        {
            "symbol": "RELIANCE",
            "isin": "INE002A01018",
            "company_name": "Reliance Industries Ltd.",
            "industry": "Oil Gas & Consumable Fuels",
            "series": "EQ",
        }
    ]


def test_parse_drops_rows_without_symbol():
    text = "Company Name,Industry,Symbol,Series,ISIN Code\nFoo,Foo,,EQ,\n"
    assert ix.parse_constituent_csv(text) == []


def test_parse_accepts_isin_without_code_alias():
    text = "Company Name,Industry,Symbol,ISIN\nFoo,Foo,FOO,INE1\n"
    assert ix.parse_constituent_csv(text)[0]["isin"] == "INE1"


def test_detail_paths_scoped_to_category_and_deduped():
    page = (
        '<a href="/indices/equity/broad-based-indices/nifty-50">a</a>'
        '<a href="/indices/equity/thematic-indices/foo">b</a>'
        '<a href="/indices/equity/broad-based-indices/nifty-50">dup</a>'
        '<a href="/indices/equity/broad-based-indices">self</a>'
    )
    assert ix._detail_paths(page, "broad-based-indices") == [
        "/indices/equity/broad-based-indices/nifty-50"
    ]


def test_csv_url_and_name_from_detail_page():
    page = (
        "<title>\n  Nifty 50\n</title>"
        '<a href="https://www.niftyindices.com//IndexConstituent/ind_nifty50list.csv">x</a>'
    )
    assert ix._csv_url(page) == "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
    assert ix._index_name(page, "fallback") == "Nifty 50"


def test_index_name_unescapes_html_entities():
    assert ix._index_name("<title>Nifty REITs &amp; Realty</title>", "x") == "Nifty REITs & Realty"


def test_index_name_falls_back_without_title():
    assert ix._index_name("<html>no title</html>", "slug-fallback") == "slug-fallback"


def test_csv_url_absent_returns_none():
    assert ix._csv_url("<html>no link</html>") is None


def test_as_of_from_headers_parses_last_modified():
    assert ix._as_of_from_headers({"Last-Modified": "Fri, 18 Sep 2026 18:00:24 GMT"}) == (
        "2026-09-18"
    )


def test_as_of_from_headers_falls_back_to_today():
    assert ix._as_of_from_headers({}) == date.today().isoformat()


# --------------------------------------------------------------------------- #
# Fold (temp DuckDB)                                                          #
# --------------------------------------------------------------------------- #
def _batch(as_of: str = "2026-09-18") -> ix.IndexBatch:
    return ix.IndexBatch(
        index_name="Nifty 50",
        index_slug="ind_nifty50list.csv",
        as_of=as_of,
        rows=ix.parse_constituent_csv(CSV),
    )


def test_fold_apply_then_rerun_is_idempotent(tmp_path):
    con = duckdb.connect(str(tmp_path / "sources.duckdb"))
    try:
        s1 = ix.fold(con, [_batch()], "2026-09-19", apply=True)
        assert s1["indices"] == 1 and s1["rows"] == 1 and s1["inserted"] == 1
        s2 = ix.fold(con, [_batch()], "2026-09-19", apply=True)
        assert s2["inserted"] == 0
        assert con.execute("SELECT COUNT(*) FROM index_constituents").fetchone()[0] == 1
    finally:
        con.close()


def test_fold_dry_run_writes_nothing(tmp_path):
    con = duckdb.connect(str(tmp_path / "sources.duckdb"))
    try:
        stats = ix.fold(con, [_batch()], "2026-09-19", apply=False)
        assert stats["inserted"] == 0
        assert not ix._table_exists(con)
    finally:
        con.close()


def test_view_selects_latest_vintage_per_index(tmp_path):
    con = duckdb.connect(str(tmp_path / "sources.duckdb"))
    try:
        ix.fold(con, [_batch("2026-03-31")], "2026-04-01", apply=True)
        ix.fold(con, [_batch("2026-09-18")], "2026-09-19", apply=True)
        assert con.execute("SELECT COUNT(*) FROM index_constituents").fetchone()[0] == 2
        latest = con.execute("SELECT DISTINCT as_of FROM vw_index_constituent").fetchall()
        assert [r[0].isoformat() for r in latest] == ["2026-09-18"]
    finally:
        con.close()
