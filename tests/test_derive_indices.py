#!/usr/bin/env python3
"""Tests for helpers/graph/derive_indices.py (index-membership fill S2).

Two layers, mirroring test_derive_countries: pure resolution/convergence
over seeded DBs, then the idempotent entity/edge writers.
"""

from __future__ import annotations

import json
import sqlite3

from helpers.graph import derive_indices as di
from tests.schema import EDGES_12COL, ENTITIES_8COL


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(ENTITIES_8COL + EDGES_12COL)
    return conn


def _seed(conn: sqlite3.Connection, rows: list[tuple[str, str | None, str | None]]) -> None:
    conn.executemany(
        "INSERT INTO entities (name, entity_type, ticker, sector_classification) "
        "VALUES (?, 'company', ?, ?)",
        rows,
    )
    conn.commit()


def _const(symbol: str, isin: str = "", industry: str = "Financial Services") -> dict:
    return {
        "index_name": "Nifty 50",
        "index_slug": "ind_nifty50list.csv",
        "symbol": symbol,
        "isin": isin,
        "company_name": f"{symbol} Ltd.",
        "industry": industry,
        "as_of": "2026-09-18",
    }


# --------------------------------------------------------------------------- #
# Resolution                                                                  #
# --------------------------------------------------------------------------- #
class TestResolve:
    def test_isin_resolution(self):
        entities = [("Reliance Industries", "company", "RELIANCE.NS", None)]
        exchange = [("INE002A01018", "RELIANCE", "NSE")]
        edges, names, worklist, industry = di.resolve(
            [_const("RELIANCE", "INE002A01018", "Oil Gas & Consumable Fuels")], entities, exchange
        )
        assert names == ["Nifty 50"] and worklist == []
        assert edges[0][:2] == ("Reliance Industries", "Nifty 50")
        assert edges[0][2]["symbol"] == "RELIANCE"
        assert edges[0][3] == di.SOURCE_REF
        assert industry == {"Oil Gas & Consumable Fuels": ["Reliance Industries"]}

    def test_symbol_resolution_without_isin(self):
        entities = [("Tata Consultancy Services", "company", "TCS.NS", None)]
        edges, _, worklist, _ = di.resolve([_const("TCS", "")], entities, [])
        assert len(edges) == 1 and worklist == []

    def test_unresolved_goes_to_worklist_and_index_still_named(self):
        edges, names, worklist, _ = di.resolve([_const("NOPE", "INE999A01000")], [], [])
        assert edges == [] and names == ["Nifty 50"]
        assert worklist[0]["symbol"] == "NOPE" and worklist[0]["reason"] == "no_entity"

    def test_non_company_entity_never_resolves(self):
        entities = [("Some Index", "index", "NIFTY50", None)]
        edges, _, worklist, _ = di.resolve([_const("NIFTY50")], entities, [])
        assert edges == [] and worklist[0]["reason"] == "no_entity"


# --------------------------------------------------------------------------- #
# Entity + edge writers                                                       #
# --------------------------------------------------------------------------- #
class TestWriters:
    def test_create_index_entities_idempotent(self):
        conn = _conn()
        try:
            n1 = di.create_index_entities(conn, ["Nifty 50", "Nifty Bank"], apply=True)
            n2 = di.create_index_entities(conn, ["Nifty 50", "Nifty Bank"], apply=True)
            assert n1 == 2 and n2 == 0
            rows = conn.execute(
                "SELECT name FROM entities WHERE entity_type='index' ORDER BY name"
            ).fetchall()
            assert [r[0] for r in rows] == ["Nifty 50", "Nifty Bank"]
        finally:
            conn.close()

    def test_create_index_entities_normalizes_name(self):
        import re

        conn = _conn()
        try:
            di.create_index_entities(conn, ["Nifty REITs & Realty", "NIFTY 50"], apply=True)
            rows = dict(
                conn.execute(
                    "SELECT name, normalized_name FROM entities WHERE entity_type='index'"
                ).fetchall()
            )
            assert rows["Nifty REITs & Realty"] == "Nifty_REITs_Realty"
            assert rows["NIFTY 50"] == "NIFTY_50"
            assert all(re.match(r"^[A-Za-z0-9][A-Za-z0-9_]*$", v) for v in rows.values())
        finally:
            conn.close()

    def test_create_index_entities_repairs_bad_normalized_name(self):
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO entities (name, entity_type, normalized_name) "
                "VALUES ('Nifty 50', 'index', 'Nifty 50')"
            )
            conn.commit()
            di.create_index_entities(conn, ["Nifty 50"], apply=True)
            got = conn.execute(
                "SELECT normalized_name FROM entities WHERE name='Nifty 50'"
            ).fetchone()[0]
            assert got == "Nifty_50"
        finally:
            conn.close()

    def test_apply_edges_idempotent(self):
        conn = _conn()
        try:
            edges = [("Reliance Industries", "Nifty 50", {}, di.SOURCE_REF)]
            n1 = di.apply_typed_edges(
                edges, edge_type=di.EDGE_TYPE, symmetric=0, conn=conn, dry_run=False
            )
            n2 = di.apply_typed_edges(
                edges, edge_type=di.EDGE_TYPE, symmetric=0, conn=conn, dry_run=False
            )
            assert n1 == 1 and n2 == 0
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Industry convergence                                                        #
# --------------------------------------------------------------------------- #
class TestConvergeIndustry:
    def test_fills_empty_and_parks_unmapped(self):
        conn = _conn()
        try:
            _seed(conn, [("Reliance Industries", "RELIANCE.NS", None)])
            filled, unmapped = di.converge_industry(
                conn,
                {
                    "Oil Gas & Consumable Fuels": ["Reliance Industries"],
                    "Services": ["Mystery Co"],
                },
                apply=True,
            )
            assert filled == 1
            assert unmapped == {"Services": ["Mystery Co"]}
            got = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Reliance Industries'"
            ).fetchone()[0]
            assert got == "Energy"
        finally:
            conn.close()

    def test_never_overwrites_authored_sector(self):
        conn = _conn()
        try:
            _seed(conn, [("HDFC Bank", "HDFCBANK.NS", "Banking")])
            filled, _ = di.converge_industry(
                conn, {"Financial Services": ["HDFC Bank"]}, apply=True
            )
            assert filled == 0
            got = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='HDFC Bank'"
            ).fetchone()[0]
            assert got == "Banking"
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self):
        conn = _conn()
        try:
            _seed(conn, [("Reliance Industries", "RELIANCE.NS", None)])
            planned, _ = di.converge_industry(
                conn, {"Oil Gas & Consumable Fuels": ["Reliance Industries"]}, apply=False
            )
            assert planned == 1
            got = conn.execute(
                "SELECT sector_classification FROM entities WHERE name='Reliance Industries'"
            ).fetchone()[0]
            assert got is None
        finally:
            conn.close()

    def test_every_mapped_label_targets_a_canonical_sector(self):
        from helpers.validators.static_checks import CANONICAL_SECTORS

        assert set(di.INDUSTRY_TO_SECTOR.values()) <= set(CANONICAL_SECTORS)


# --------------------------------------------------------------------------- #
# Worklist                                                                    #
# --------------------------------------------------------------------------- #
def test_write_worklist(tmp_path):
    path = tmp_path / "index_worklist.json"
    n = di.write_worklist(
        [{"index": "Nifty 50", "symbol": "NOPE", "reason": "no_entity"}],
        {"Services": ["Mystery Co"]},
        path,
    )
    assert n == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["constituents"][0]["symbol"] == "NOPE"
    assert data["unmapped_industries"] == {"Services": ["Mystery Co"]}


# --------------------------------------------------------------------------- #
# S3: listed_on_index valid_from backfill
# --------------------------------------------------------------------------- #
class TestBackfillValidity:
    def _edges_conn(self):
        conn = _conn()
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
            "VALUES (?, 'Nifty 50', 'listed_on_index', ?, 'derive:indices:nse-constituents')",
            [
                ("Reliance Industries", json.dumps({"as_of": "2026-09-18", "symbol": "RELIANCE"})),
                ("TCS", json.dumps({"symbol": "TCS"})),  # no as_of -> untouched
            ],
        )
        conn.commit()
        return conn

    def test_backfill_sets_valid_from_from_as_of(self):
        conn = self._edges_conn()
        n = di.backfill_validity(conn=conn)
        assert n == 1
        rows = conn.execute(
            "SELECT source, valid_from FROM graph_edges WHERE edge_type='listed_on_index' ORDER BY source"
        ).fetchall()
        assert rows == [("Reliance Industries", "2026-09-18"), ("TCS", None)]
        # idempotent: converged -> 0 updates
        assert di.backfill_validity(conn=conn) == 0
        conn.close()
