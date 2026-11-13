"""Tests for foreign_links_generate.py (mapping tier policy).

Covers: target-universe filter (India excluded), exact mapping, fuzzy tiers
(strict ≥0.80 vs review 0.66–0.79), ADR primary-listing preference, ISIN
upgrade recording.
"""

from __future__ import annotations

import duckdb
import pytest

from helpers.maintenance.foreign_links_generate import generate, _is_adr_name


def _entities_con():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE entities (name VARCHAR, entity_type VARCHAR, ticker VARCHAR)
        """
    )
    con.execute(
        """
        CREATE TABLE entity_tags (entity_name VARCHAR, tag VARCHAR)
        """
    )
    rows = [
        ("Tencent", "company", "TCEHY"),
        ("Samsung Electronics", "company", "005930.KS"),
        ("BYD", "company", "01211.HK"),
        ("Brookfield Infrastructure Partners L.P", "company", "BIP.TO"),
        ("Canada Goose Holdings", "company", "GOOS.TO"),
        ("VinFast Auto", "company", "VFS"),
        ("Dassault Systèmes", "company", "DSY.PA"),
        ("Reliance Industries", "company", "RELIANCE.NS"),
        ("HDFC Bank", "company", "HDFCBANK.NS"),
    ]
    con.executemany("INSERT INTO entities VALUES (?,?,?)", rows)
    con.executemany(
        "INSERT INTO entity_tags VALUES (?,?)",
        [
            (n, "geography/international")
            for n in (
                "Tencent",
                "Samsung Electronics",
                "BYD",
                "Brookfield Infrastructure Partners L.P",
                "Canada Goose Holdings",
                "VinFast Auto",
                "Dassault Systèmes",
            )
        ],
    )
    return con


def _sources_con():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE exchange_listings (
            isin VARCHAR, name VARCHAR, exchange VARCHAR, symbol VARCHAR, segment VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO exchange_listings VALUES (?,?,?,?,?)",
        [
            (None, "Tencent Holdings Ltd", "HKEX", "00700", None),
            (
                None,
                "Tencent Holdings - American Depositary Shares each representing five Class A ordinary shares",
                "NASDAQ",
                "TCEHY",
                None,
            ),
            (None, "Samsung Electronics", "KRX", "005930.KS", None),
            ("CNE100000296", "BYD COMPANY", "HKEX", "01211", None),
            (None, "Brookfield Infrastructure Partners L.P.", "TSX", "BIP.TO", None),
            (None, "Canada Goose Inc", "TSX", "GOOS.TO", None),
            (None, "VinFast Auto Ltd. - Warrant", "NASDAQ", "VFSWW", None),
            (None, "Dassault Systèmes", "EURONEXT", "DSY.PA", None),
            (None, "Reliance Industries", "BSE", "RELIANCE", None),
            (None, "HDFC Bank", "NSE", "HDFCBANK", None),
        ],
    )
    return con


@pytest.fixture()
def maps():
    return generate(_entities_con(), _sources_con())


def test_universe_size(maps):
    # 7 international-tagged entities; Reliance/HDFC (Indian tickers) excluded
    assert maps["universe_size"] == 7


def test_exact_matches_present(maps):
    ents = {m["entity"] for m in maps["strict"]}
    assert {"Tencent", "Samsung Electronics", "Dassault Systèmes"} <= ents
    tencent = next(m for m in maps["strict"] if m["entity"] == "Tencent")
    assert tencent["exchange"] == "HKEX"
    assert tencent["symbol"] == "00700"


def test_indian_entities_never_mapped(maps):
    ents = {m["entity"] for m in maps["strict"]}
    assert "Reliance Industries" not in ents
    assert "HDFC Bank" not in ents


def test_adr_row_demoted_to_aliases(maps):
    # Tencent's NASDAQ ADR line must not appear as a primary listing
    assert not any(m["entity"] == "Tencent" and m["exchange"] == "NASDAQ" for m in maps["strict"])
    alias_ents = {m["entity"] for m in maps["aliases"]}
    assert "Tencent" in alias_ents
    assert any(m["entity"] == "Tencent" and m["exchange"] == "NASDAQ" for m in maps["aliases"])
    # home primary stays the canonical row
    tencent_primary = [m for m in maps["strict"] if m["entity"] == "Tencent"]
    assert len(tencent_primary) == 1
    assert tencent_primary[0]["exchange"] == "HKEX"


def test_isin_upgrade_recorded(maps):
    byd = next(m for m in maps["strict"] if m["entity"] == "BYD")
    assert byd["isin"] == "CNE100000296"


def test_review_tier_separated(maps):
    assert len(maps["review"]) == 1
    r = maps["review"][0]
    assert r["score"] == pytest.approx(0.667, abs=0.001)
    assert r["entity"] == "VinFast Auto"
    assert r["symbol"] == "VFSWW"
    assert not any(m["entity"] == "VinFast Auto" for m in maps["strict"])


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Tencent Holdings - American Depositary Shares each representing five Class A ordinary shares",
            True,
        ),
        ("Tencent Holdings Ltd", False),
        ("BYD COMPANY", False),
        ("Mercedes-Benz Group AG - American Depositary Shares", True),
    ],
)
def test_is_adr_name(name, expected):
    assert _is_adr_name(name) is expected
