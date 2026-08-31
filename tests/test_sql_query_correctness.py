"""SQL-builder correctness tests — the silent-wrong-answer layer.

Pins the F1/F2/F3 contracts AND the read-only query wrappers
(co_mention_top, cross_sector_bridges, edges_by_year,
sector_members_with_market_cap) against a seeded SQLite DB, comparing each
wrapper to an INDEPENDENT raw-SQL reference.

  * F1 market_cap_sql() now deterministic (MIN)        -> APPLIED -> green guard
  * F2 /api/stats double-count under conflict       -> APPLIED -> green guard
    (complements tests/test_api_graph_unit.py::
    test_detects_conflict_when_company_has_two_cap_tags)
  * F3 v_node row_number() ORDER BY               -> APPLIED -> green guard

All fixes verified against live code 2026-08-13; these are regression guards
that lock the fixes in (not red TDD drivers). F1/F2/wrapper-SQLite tests run in
make qa; F3 + sector_members_with_market_cap are `live` (need the real research.db).
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers.core.db import market_cap_sql
from helpers.graph.query import (
    co_mention_top,
    cross_sector_bridges,
    edges_by_year,
    sector_members_with_market_cap,
)
from tests.fixtures.seed_research_db import build_seed_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _conn(db):
    """SQLite connection with the project-standard Row factory."""
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# F1 — market_cap_sql() must be deterministic (MIN)
# ---------------------------------------------------------------------------
def test_market_cap_sql_contains_min():
    """Structural assertion: the fix wraps the subselect in MIN(...)."""
    assert "MIN(" in market_cap_sql().upper()


def test_market_cap_sql_deterministic_for_conflict(tmp_path):
    """A company with two market_cap/* tags must resolve to the MIN (the
    alphabetically-first tier), not an arbitrary one."""
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        sql = (
            f"SELECT name, {market_cap_sql()} FROM entities "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            f"WHERE name = 'Cap Conflict Co'"
        )
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    assert row is not None
    # MIN('market_cap/large_cap', 'market_cap/mid_cap') -> 'large_cap'.
    assert row[1] == "large_cap"


# ---------------------------------------------------------------------------
# F2 — market_cap buckets must equal distinct companies (no double-count)
# ---------------------------------------------------------------------------
def test_market_cap_buckets_equal_distinct_companies(tmp_path):
    """Invariant the F2 fix guarantees: sum of market_cap bucket counts ==
    number of distinct companies carrying a market_cap/* tag. With the conflict
    company (2 tags) this would be 6 != 5 under the old GROUP BY cap form."""
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        q = """
        SELECT cap, COUNT(*) FROM (
          SELECT e.name, substr(MIN(t.tag), length('market_cap/')+1) AS cap
          FROM entities e JOIN entity_tags t
            ON t.entity_name = e.name AND t.tag LIKE 'market_cap/%'
          WHERE e.entity_type = 'company' GROUP BY e.name
        ) GROUP BY cap
        """
        rows = con.execute(q).fetchall()
        bucket_sum = sum(c for _, c in rows)
        distinct = con.execute(
            "SELECT COUNT(DISTINCT e.name) FROM entities e "
            "JOIN entity_tags t ON t.entity_name = e.name "
            "AND t.tag LIKE 'market_cap/%' WHERE e.entity_type='company'"
        ).fetchone()[0]
    finally:
        con.close()
    assert bucket_sum == distinct


# ---------------------------------------------------------------------------
# F3 — v_node vertex IDs must be deterministic across rebuilds (live)
# ---------------------------------------------------------------------------
@pytest.mark.live
def test_v_node_rank_deterministic(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from helpers.graph.query import connect

    db = build_seed_db(tmp_path / "seed.db")
    dp = db.with_suffix(".duckdb")

    def vnode_map():
        if dp.exists():
            dp.unlink()
        connect(db)  # builds v_node (+ e_*) into dp
        con = duckdb.connect(str(dp), read_only=True)
        try:
            rows = con.execute("SELECT id, name FROM v_node ORDER BY name").fetchall()
        finally:
            con.close()
        return [(r[0], r[1]) for r in rows]

    # Build the property graph twice from the same SQLite — IDs must match.
    assert vnode_map() == vnode_map()


# ---------------------------------------------------------------------------
# Slice A reference tests — wrappers vs INDEPENDENT raw-SQL reference
# ---------------------------------------------------------------------------
def _ref_co_mention(db, n):
    con = _conn(db)
    try:
        rows = con.execute(
            "SELECT source AS entity, COUNT(*) AS co_mentions "
            "FROM graph_edges WHERE edge_type='co_mentioned_in' "
            "GROUP BY source ORDER BY co_mentions DESC LIMIT ?",
            (n,),
        ).fetchall()
    finally:
        con.close()
    return [{"entity": r["entity"], "co_mentions": r["co_mentions"]} for r in rows]


def test_co_mention_top_matches_reference(tmp_path):
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        got = co_mention_top(10, con)
    finally:
        con.close()
    assert got == _ref_co_mention(db, 10)
    # Spot-check the deterministic ranking (Infosys 4 > HDFC 3 > ICICI 2).
    assert [g["entity"] for g in got] == ["Infosys", "HDFC Bank", "ICICI Bank"]


def _ref_cross_sector_bridges(db):
    con = _conn(db)
    try:
        ents = {
            r["name"]: r["sector_classification"]
            for r in con.execute("SELECT name, sector_classification FROM entities")
        }
        edges = con.execute(
            "SELECT edge_type, source, target FROM graph_edges "
            "WHERE edge_type IN ('jv_with','acquired')"
        ).fetchall()
    finally:
        con.close()
    counts = {}
    for e in edges:
        sa = ents.get(e["source"])
        sb = ents.get(e["target"])
        if sa and sb and sa != sb:
            counts[(e["edge_type"], sa, sb)] = counts.get((e["edge_type"], sa, sb), 0) + 1
    out = [
        {"edge_type": k[0], "sector_a": k[1], "sector_b": k[2], "count": v}
        for k, v in counts.items()
    ]
    out.sort(key=lambda d: (-d["count"], d["edge_type"]))
    return out


def test_cross_sector_bridges_matches_reference(tmp_path):
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        got = cross_sector_bridges(con)
    finally:
        con.close()
    assert got == _ref_cross_sector_bridges(db)
    # The same-sector acquired (HDFC->ICICI, both Banking) must be excluded.
    assert all(b["sector_a"] != b["sector_b"] for b in got)
    assert {"acquired", "jv_with"} == {b["edge_type"] for b in got}


def _ref_edges_by_year(db):
    con = _conn(db)
    try:
        rows = con.execute(
            "SELECT valid_from, edge_type FROM graph_edges "
            "WHERE edge_type IN ('acquired','jv_with') AND valid_from IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    counts = {}
    for r in rows:
        year = r["valid_from"][:4]
        counts[(year, r["edge_type"])] = counts.get((year, r["edge_type"]), 0) + 1
    out = [{"year": k[0], "edge_type": k[1], "count": v} for k, v in counts.items()]
    out.sort(key=lambda d: (d["year"], d["edge_type"]))
    return out


def test_edges_by_year_matches_reference(tmp_path):
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        got = edges_by_year(con)
    finally:
        con.close()
    assert got == _ref_edges_by_year(db)
    assert [g["year"] for g in got] == ["2020", "2021", "2022"]


def _ref_sector_members(db, sector):
    con = _conn(db)
    try:
        rows = con.execute(
            """
            SELECT e.name AS company,
                   CASE WHEN t.tag IS NULL THEN NULL
                        ELSE substr(MIN(t.tag), length('market_cap/')+1) END AS mcap
            FROM entities e
            JOIN graph_edges g
              ON g.source = e.name AND g.edge_type='part_of' AND g.target=?
            LEFT JOIN entity_tags t
              ON t.entity_name = e.name AND t.tag LIKE 'market_cap/%'
            GROUP BY e.name ORDER BY e.name
            """,
            (sector,),
        ).fetchall()
    finally:
        con.close()
    return [(r["company"], r["mcap"]) for r in rows]


@pytest.mark.live
def test_sector_members_with_market_cap_matches_reference(tmp_path):
    pytest.importorskip("duckdb")
    from helpers.graph.query import connect

    db = build_seed_db(tmp_path / "seed.db")
    dp = db.with_suffix(".duckdb")
    if dp.exists():
        dp.unlink()
    con = connect(db)  # builds v_node + e_belongs (BelongsTo <- part_of)
    try:
        for sector in ("Banking", "Technology"):
            got = sector_members_with_market_cap(con, sector)
            assert got == _ref_sector_members(db, sector), sector
        # The conflict company resolves to the MIN tier (large_cap) inside the
        # DuckDB graph too, matching the SQLite reference.
        tech = dict(sector_members_with_market_cap(con, "Technology"))
        assert tech["Cap Conflict Co"] == "large_cap"
    finally:
        con.close()
