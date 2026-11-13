"""Tests for helpers/maintenance/tag_international_entities.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.tag_international_entities import (  # noqa: E402
    INTERNATIONAL_TAG,
    apply_tags,
    collect_international_candidates,
)


def _make_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    con.execute("CREATE TABLE entities (name VARCHAR, ticker VARCHAR)")
    con.execute("CREATE TABLE entity_tags (entity_name VARCHAR, tag VARCHAR)")
    companies = [
        ("Apple", "AAPL"),
        ("Samsung Electronics", "005930.KS"),
        ("Tencent", "0700.HK"),
        ("Reliance", "RELIANCE.NS"),
        ("Tata Motors", "TATAMOTORS.NS"),
        ("Infosys", "INFY.BO"),
        ("NoTicker", None),
    ]
    con.executemany("INSERT INTO entities VALUES (?, ?)", companies)
    con.execute(
        "INSERT INTO entity_tags VALUES ('Apple', 'geography/usa'), "
        "('Tencent', 'geography/hong_kong'), "
        "('Reliance', 'geography/india')"
    )
    return con


def test_candidates_exclude_indian_and_null_tickers():
    con = _make_con()
    assert collect_international_candidates(con) == ["Apple", "Samsung Electronics", "Tencent"]
    con.close()


def test_apply_is_idempotent_and_preserves_existing_tags():
    con = _make_con()
    names = collect_international_candidates(con)
    assert apply_tags(con, names) == 3  # all three get INTERNATIONAL_TAG
    # reapply → 0 new
    assert apply_tags(con, names) == 0
    rows = con.execute(
        "SELECT entity_name, tag FROM entity_tags ORDER BY entity_name, tag"
    ).fetchall()
    tencent = [t for n, t in rows if n == "Tencent"]
    assert INTERNATIONAL_TAG in tencent
    assert "geography/hong_kong" in tencent  # additive, old country tag kept
    con.close()


def test_empty_candidates_apply_noop():
    con = _make_con()
    assert apply_tags(con, []) == 0
    con.close()
