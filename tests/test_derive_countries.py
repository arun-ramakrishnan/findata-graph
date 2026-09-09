#!/usr/bin/env python3
"""Tests for helpers/graph/derive_countries.py (country layer C1).

Two layers, mirroring test_derive_themes:
  * classify_ticker / derive_rows are pure over ticker strings + a
    seeded DB — these pin the CONSERVATION contract: unmapped dotted
    suffixes and no-ticker companies are never guessed (worklist), and
    the audited ADR/OTC overrides never land in the usa bucket.
  * create_country_entities / apply_edges hit a temp SQLite DB — these
    pin idempotency (INSERT OR IGNORE) and the country-entity contract.
"""

from __future__ import annotations

import json
import sqlite3

from helpers.graph import derive_countries as dc  # noqa: E402
from tests.schema import EDGES_12COL, ENTITIES_8COL, ENTITY_TAGS  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("".join([ENTITIES_8COL, EDGES_12COL, ENTITY_TAGS]))
    return conn


def _seed(conn: sqlite3.Connection, rows: list[tuple[str, str | None]]) -> None:
    conn.executemany(
        "INSERT INTO entities (name, entity_type, ticker) VALUES (?, 'company', ?)",
        rows,
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Ticker classification (no DB)                                               #
# --------------------------------------------------------------------------- #
class TestClassifyTicker:
    def test_india_suffixes(self):
        for t in ("RELIANCE.NS", "BEL.BO", "X.NSE", "Y.BSE"):
            assert dc.classify_ticker(t) == ("india", f"suffix:{t[t.rfind('.') :]}"), t

    def test_foreign_suffixes(self):
        assert dc.classify_ticker("005930.KS")[0] == "south_korea"
        assert dc.classify_ticker("060230.KQ")[0] == "south_korea"  # KOSDAQ
        assert dc.classify_ticker("4188.T")[0] == "japan"
        assert dc.classify_ticker("2356.TW")[0] == "taiwan"
        assert dc.classify_ticker("HEI.DE")[0] == "germany"
        assert dc.classify_ticker("1810.HK")[0] == "hong_kong"

    def test_plain_default_is_usa(self):
        assert dc.classify_ticker("WMT") == ("usa", "plain:default")
        assert dc.classify_ticker("NVDA") == ("usa", "plain:default")

    def test_plain_adr_overrides(self):
        """The audited ADR/OTC lines must NOT default to usa."""
        for t, country in [
            ("DEO", "uk"),
            ("GSK", "uk"),
            ("UL", "uk"),
            ("BABA", "china"),
            ("BIDU", "china"),
            ("NVS", "switzerland"),
            ("SNY", "france"),
            ("TTE", "france"),
            ("JTEKY", "japan"),
            ("SLF", "canada"),
            ("VLVLY", "sweden"),
            ("VFS", "vietnam"),
        ]:
            assert dc.classify_ticker(t) == (country, "plain:override"), t

    def test_unmapped_dotted_suffix_is_never_guessed(self):
        assert dc.classify_ticker("FOO.ZZ") == (None, "unmapped_suffix")

    def test_no_ticker(self):
        assert dc.classify_ticker(None) == (None, "no_ticker")
        assert dc.classify_ticker("") == (None, "no_ticker")
        assert dc.classify_ticker("   ") == (None, "no_ticker")


# --------------------------------------------------------------------------- #
# Derivation (seeded DB)                                                      #
# --------------------------------------------------------------------------- #
class TestDeriveRows:
    def test_partition_edges_and_worklist(self):
        conn = _conn()
        try:
            _seed(
                conn,
                [
                    ("Tata Consultancy Services", "TCS.NS"),
                    ("Walmart", "WMT"),
                    ("Diageo plc", "DEO"),
                    ("Unlisted PSU Co", None),
                ],
            )
            edges, worklist, counts = dc.derive_rows(conn)
            assert counts == {"india": 1, "usa": 1, "uk": 1}
            assert len(edges) == 3
            targets = {e[0]: e[1] for e in edges}
            assert targets["Tata Consultancy Services"] == "india"
            assert targets["Walmart"] == "usa"
            assert targets["Diageo plc"] == "uk"  # override, not usa
            # The no-ticker company is worklisted, never guessed.
            assert [w["name"] for w in worklist] == ["Unlisted PSU Co"]
            assert worklist[0]["reason"] == "no_ticker"
        finally:
            conn.close()

    def test_worklist_carries_geography_tag_hint(self):
        conn = _conn()
        try:
            _seed(conn, [("Unlisted PSU Co", None)])
            conn.execute(
                "INSERT INTO entity_tags (entity_name, tag) "
                "VALUES ('Unlisted PSU Co', 'geography/india')"
            )
            conn.commit()
            _, worklist, _ = dc.derive_rows(conn)
            assert worklist[0]["geography_tag"] == "geography/india"
        finally:
            conn.close()

    def test_edge_properties_carry_ticker_and_via(self):
        conn = _conn()
        try:
            _seed(conn, [("Walmart", "WMT")])
            edges, _, _ = dc.derive_rows(conn)
            assert edges[0][2] == {"ticker": "WMT", "via": "plain:default"}
            assert edges[0][3] == dc.SOURCE_REF
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# DB-backed: entity creation + idempotent edge apply                          #
# --------------------------------------------------------------------------- #
class TestApplyAndIdempotency:
    def test_create_country_entities_idempotent(self):
        conn = _conn()
        try:
            n1 = dc.create_country_entities(conn, ["india", "usa"], apply=True)
            assert n1 == 2
            n2 = dc.create_country_entities(conn, ["india", "usa"], apply=True)
            assert n2 == 0
            kinds = conn.execute(
                "SELECT name FROM entities WHERE entity_type='country' ORDER BY name"
            ).fetchall()
            assert [k[0] for k in kinds] == ["india", "usa"]
        finally:
            conn.close()

    def test_apply_edges_then_rerun_inserts_no_duplicates(self):
        conn = _conn()
        try:
            dc.create_country_entities(conn, ["india"], apply=True)
            edges = [("TCS", "india", {"ticker": "TCS.NS", "via": "suffix:.NS"}, dc.SOURCE_REF)]
            n1 = dc.apply_typed_edges(
                edges, edge_type="listed_in", symmetric=0, conn=conn, dry_run=False
            )
            assert n1 == 1
            n2 = dc.apply_typed_edges(
                edges, edge_type="listed_in", symmetric=0, conn=conn, dry_run=False
            )
            assert n2 == 0
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='listed_in'"
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self):
        conn = _conn()
        try:
            edges = [("TCS", "india", {}, "test")]
            n = dc.apply_typed_edges(
                edges, edge_type="listed_in", symmetric=0, conn=conn, dry_run=True
            )
            assert n == 1
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='listed_in'"
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Worklist write                                                              #
# --------------------------------------------------------------------------- #
def test_write_worklist(tmp_path):
    path = tmp_path / "country_worklist.json"
    worklist = [
        {"name": "B", "ticker": None, "sector": None, "geography_tag": None, "reason": "no_ticker"},
        {
            "name": "A",
            "ticker": "FOO.ZZ",
            "sector": None,
            "geography_tag": None,
            "reason": "unmapped_suffix",
        },
    ]
    n = dc.write_worklist(worklist, path)
    assert n == 2
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["count"] == 2
    assert [c["name"] for c in data["companies"]] == ["A", "B"]  # sorted
    assert data["companies"][0]["reason"] == "unmapped_suffix"
