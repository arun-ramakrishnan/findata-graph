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
        # Roadmap A′ reference: PARTICIPATION count (entity on either side
        # of a co_mentioned_in row) — orientation-free, matching the
        # materialised e_comention both-sides read.
        rows = con.execute(
            "SELECT entity, COUNT(*) AS co_mentions FROM ("
            "SELECT source AS entity FROM graph_edges "
            "WHERE edge_type='co_mentioned_in' "
            "UNION ALL "
            "SELECT target FROM graph_edges WHERE edge_type='co_mentioned_in'"
            ") GROUP BY entity ORDER BY co_mentions DESC, entity LIMIT ?",
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
    # Spot-check the deterministic ranking (Infosys 6 > HDFC 5 > ICICI 4).
    assert [g["entity"] for g in got] == [
        "Infosys",
        "HDFC Bank",
        "ICICI Bank",
        "No Ticker Co",
        "Cap Conflict Co",
    ]


def _ref_cross_sector_bridges(db):
    """Independent raw-SQL reference for the S2b semantics: membership from
    the CANONICAL hyper store (hyper_edges/hyper_incidences), one bridge
    group per (edge, src sector, dst sector) cross-pair; same-sector pairs
    excluded. Deliberately NOT the wrapper's DuckDB projection path.
    """
    con = _conn(db)
    try:
        members = {}
        for comp, sec in con.execute(
            "SELECT hi.entity_name, he.label FROM hyper_incidences hi "
            "JOIN hyper_edges he ON he.id = hi.edge_id "
            "WHERE he.edge_type = 'sector'"
        ):
            members.setdefault(comp, set()).add(sec)
        edges = con.execute(
            "SELECT edge_type, source, target FROM graph_edges "
            "WHERE edge_type IN ('jv_with','acquired')"
        ).fetchall()
    finally:
        con.close()
    counts = {}
    for e in edges:
        for sa in sorted(members.get(e["source"], ())):
            for sb in sorted(members.get(e["target"], ())):
                if sa != sb:
                    k = (e["edge_type"], sa, sb)
                    counts[k] = counts.get(k, 0) + 1
    out = [
        {"edge_type": k[0], "sector_a": k[1], "sector_b": k[2], "count": v}
        for k, v in counts.items()
    ]
    out.sort(key=lambda d: (-d["count"], d["edge_type"], d["sector_a"], d["sector_b"]))
    return out


def test_cross_sector_bridges_matches_reference(tmp_path):
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        got = cross_sector_bridges(con)
    finally:
        con.close()
    assert sorted(
        got, key=lambda d: (-d["count"], d["edge_type"], d["sector_a"], d["sector_b"])
    ) == _ref_cross_sector_bridges(db)
    # The same-sector acquired (HDFC->ICICI, both Banking) must be excluded.
    assert all(b["sector_a"] != b["sector_b"] for b in got)
    assert {"acquired", "jv_with"} == {b["edge_type"] for b in got}


def test_cross_sector_bridges_dual_membership(tmp_path):
    """graph_read_path_consolidation S2b acceptance: the dual-member
    company surfaces in TWO sector-bridge groups.

    Kuantum Papers carries legacy sector_classification='Agriculture' but
    canonical membership in Agriculture AND Packaging; the wrapper reads
    the hyper-backed projection, so its acquired edge to Infosys
    (Technology) must yield BOTH Agriculture->Technology and
    Packaging->Technology — the pre-S2b single-valued path returned only
    the first.
    """
    db = build_seed_db(tmp_path / "seed.db")
    con = _conn(db)
    try:
        got = cross_sector_bridges(con)
    finally:
        con.close()
    kuantum = [g for g in got if g["sector_a"] == "Agriculture" or g["sector_a"] == "Packaging"]
    assert {(g["sector_a"], g["sector_b"]) for g in kuantum} == {
        ("Agriculture", "Technology"),
        ("Packaging", "Technology"),
    }
    assert all(g["edge_type"] == "acquired" and g["count"] == 1 for g in kuantum)


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
    assert [g["year"] for g in got] == ["2020", "2021", "2022", "2023"]


def test_c_series_per_edge_type_row_pins(tmp_path):
    """graph_read_path_consolidation S2a: exact per-edge-type row pins.

    DuckDB 1.5.6 had a CSPE wrong-results bug where UNION-ALL arms
    sharing a join subtree returned silently wrong counts; 2.0 rejects
    the combined form outright (S1'). A differential test alone can pass
    on a wrong-but-consistent pair, so the amendment demands the frozen
    fixture counts be pinned literally — every union arm of
    _edge_resolved included.
    """
    from helpers.graph.query import (
        _c_series_duck_conn,
        _resolve_edges,
        _stage_edges,
    )

    db = build_seed_db(tmp_path / "seed.db")
    scon = _conn(db)
    try:
        # Wrapper-level pins (frozen fixture numbers). Roadmap A′ re-base
        # (2026-10-25): participation counts (both sides of each pair),
        # orientation-free — 6/5/4 for the 9 seeded rows, not the old
        # stored-orientation 4/3/2.
        assert co_mention_top(10, scon) == [
            {"entity": "Infosys", "co_mentions": 6},
            {"entity": "HDFC Bank", "co_mentions": 5},
            {"entity": "ICICI Bank", "co_mentions": 4},
            {"entity": "No Ticker Co", "co_mentions": 2},
            {"entity": "Cap Conflict Co", "co_mentions": 1},
        ]
        assert sorted(
            cross_sector_bridges(scon),
            key=lambda d: (d["edge_type"], d["sector_a"]),
        ) == [
            {
                "edge_type": "acquired",
                "sector_a": "Agriculture",
                "sector_b": "Technology",
                "count": 1,
            },
            {"edge_type": "acquired", "sector_a": "Banking", "sector_b": "Technology", "count": 1},
            {
                "edge_type": "acquired",
                "sector_a": "Packaging",
                "sector_b": "Technology",
                "count": 1,
            },
            {"edge_type": "jv_with", "sector_a": "Banking", "sector_b": "Technology", "count": 1},
        ]
        assert edges_by_year(scon) == [
            {"year": "2020", "edge_type": "jv_with", "count": 1},
            {"year": "2021", "edge_type": "acquired", "count": 1},
            {"year": "2022", "edge_type": "acquired", "count": 1},
            {"year": "2023", "edge_type": "acquired", "count": 1},
        ]

        # Union-arm pins: every _edge_resolved edge type, exact counts.
        # Seed = 9 co_mentioned_in + 3 acquired + 1 jv_with graph_edges rows,
        # + 7 sector incidences projected BOTH ways (part_of + has_company;
        # Kuantum Papers carries two).
        dcon, owned = _c_series_duck_conn(scon)
        try:
            _stage_edges(dcon)
            _resolve_edges(dcon)
            got = dict(
                dcon.execute(
                    "SELECT edge_type, COUNT(*) FROM _edge_resolved GROUP BY edge_type"
                ).fetchall()
            )
        finally:
            if owned:
                dcon.close()
        assert got == {
            "co_mentioned_in": 9,
            "acquired": 3,
            "jv_with": 1,
            "part_of": 7,
            "has_company": 7,
        }
    finally:
        scon.close()


def _ref_sector_members(db, sector):
    con = _conn(db)
    try:
        rows = con.execute(
            """
            -- part_of/has_company retirement (2026-10-19): membership is
            -- canonical in the sector hyperedges; the dyad no longer exists
            -- to read. The DuckDB side projects it back out of this store.
            SELECT e.name AS company,
                   CASE WHEN t.tag IS NULL THEN NULL
                        ELSE substr(MIN(t.tag), length('market_cap/')+1) END AS mcap
            FROM entities e
            JOIN hyper_incidences hi
              ON hi.entity_name = e.name
            JOIN hyper_edges he
              ON he.id = hi.edge_id AND he.edge_type='sector' AND he.label=?
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


# ---------------------------------------------------------------------------
# Roadmap A′ — served-store compatibility (read-only default conn)
# ---------------------------------------------------------------------------
def test_c_series_default_conn_routes_read_only(monkeypatch):
    """A′ contract: the production default (conn=None) resolves through the
    duckdb_lock open_read_only funnel — wire attach under quack, read-only
    file open otherwise — NEVER a file-mode RW open (split-brain guard)."""
    from helpers.graph import query as q
    from helpers.misc import duckdb_lock

    seen = {}

    class _StubCon:
        def execute(self, *a, **k):
            seen.setdefault("sql", []).append(a[0] if a else None)
            return self

        def fetchall(self):
            return []

        def close(self):
            seen["closed"] = True

    def fake_ro(path):
        seen["path"] = path
        return _StubCon()

    monkeypatch.setattr(duckdb_lock, "open_read_only", fake_ro)
    try:
        got = q.co_mention_top(5)
    finally:
        monkeypatch.undo()
    assert seen["path"] == str(q.DUCKDB_PATH)
    assert got == []
    assert seen.get("closed") is True
