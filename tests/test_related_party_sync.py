# Tests for helpers/maintenance/related_party_sync.py
# (related_party_groups_vigil.md): hermetic classification / safety /
# derivation contract tests — no network.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from helpers.maintenance import related_party_sync as rps


class TestClassify:
    def test_relationship_directions(self):
        # (relationship free text, rel_group) -> edge class; people rows
        # (KMP) classify to None and never become edges. Exercises the
        # real build_pairs SQL over the real DDL shape.
        cases = [
            ("Wholly owned subsidiary", "Group Companies", "sub_fwd"),
            ("Subsidiary", "Group Companies", "sub_fwd"),
            ("SUBSIDIARIES", "Group Companies", "sub_fwd"),
            ("Holding Company", "Group Companies", "sub_rev"),
            ("Fellow Subsidiary", "Group Companies", "same_group"),
            ("FELLOW SUBSIDIARIES", "Group Companies", "same_group"),
            ("Subsidiary of ultimate parent entity", "Group Companies", "same_group"),
            ("Associate", "Group Companies", "same_group"),
            ("Joint Venture", "Group Companies", "jv_with"),
            ("Key Management Personnel", "KMP", None),
            ("Anything", "Promoter Group", "same_group"),
            ("Anything", "Relatives", None),
        ]
        con = duckdb.connect()
        con.execute(rps._DDL)
        for rel, grp, want in cases:
            con.execute("DELETE FROM rpt_transactions")
            con.execute(
                "INSERT INTO rpt_transactions (symbol, entity_name, counter_party, "
                "relationship, rel_group) VALUES ('SY', 'E', 'Counter Co', ?, ?)",
                [rel, grp],
            )
            got = next((p["cls"] for p in rps.build_pairs(con)), None)
            assert got == want, rel


class TestSafeDisplay:
    def test_mangled_names_repaired_or_rejected(self):
        # glued suffixes and mangled tails survive clean_name — scrub or drop
        assert rps._safe_display("Mahindra Integrated Business Solutionspvt") == (
            "Mahindra Integrated Business Solutions"
        )
        assert rps._safe_display("Sunsure Solarapark Nineteen Privated limited") == (
            "Sunsure Solarapark Nineteen"
        )
        assert rps._safe_display("TVS Motor (Singapore) Pte Limited") == "TVS Motor (Singapore) Pte"
        assert rps._safe_display("") is None

    def test_check_bad_matches_substrings(self):
        assert rps._CHECK_BAD.search("Foo Limited")
        assert rps._CHECK_BAD.search("FooPvt")
        assert not rps._CHECK_BAD.search("Foo Services")


class TestCounterpartyHygiene:
    def test_garbage_rejected(self):
        assert rps._clean_counterparty("0") is None
        assert rps._clean_counterparty("3090 Mcdonald Ave") is None
        assert rps._clean_counterparty("") is None
        ok = rps._clean_counterparty("ABC Ltd (formerly known as XYZ Ltd)")
        assert ok is not None and "formerly" not in ok.lower()


@pytest.fixture()
def lane(tmp_path: Path):
    con = duckdb.connect(str(tmp_path / "sources.duckdb"))
    con.execute(rps._DDL)
    conn = sqlite3.connect(tmp_path / "research.db")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
            normalized_name TEXT, file_path TEXT);
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL REFERENCES entities(name),
            target TEXT NOT NULL REFERENCES entities(name),
            edge_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}', valid_from DATE, valid_to DATE,
            source_ref TEXT NOT NULL, symmetric INTEGER NOT NULL DEFAULT 0,
            source_tier TEXT,
            UNIQUE(source, target, edge_type), CHECK (source != target));
        """
    )
    conn.execute(
        "INSERT INTO entities VALUES ('TVS Motor Company', 'company', 'TVS_Motor_Company', NULL)"
    )
    conn.commit()
    return con, conn


def _seed_rpt(con, rows):
    con.execute("DELETE FROM rpt_transactions")
    con.executemany(
        "INSERT INTO rpt_transactions (symbol, entity_name, counter_party, "
        "relationship, rel_group, transaction_type, amount_during_period, "
        "period_end_date, period_end_date_parsed, xbrl_url) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


class TestGroupPass:
    def test_direction_and_entity_creation(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "TVS Motor (Singapore) Pte Limited",
                    "Wholly owned subsidiary",
                    "Group Companies",
                    "Investment",
                    "1000",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/1.xml",
                ),
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "TVS Holdings Limited",
                    "Holding Company",
                    "Group Companies",
                    "Any other transaction",
                    None,
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/2.xml",
                ),
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "Sundaram Auto Components",
                    "Fellow Subsidiary",
                    "Group Companies",
                    "Sale of goods or services",
                    "500",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/3.xml",
                ),
            ],
        )
        companies = {"TVSMOTOR": "TVS Motor Company"}
        norm = {"TVS_Holdings": "TVS Holdings"}  # resolves
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _ = rps.apply_pairs(conn, pairs, companies, norm, dry_run=False)
        assert written == 3
        rows = conn.execute(
            "SELECT source, target, edge_type, symmetric FROM graph_edges ORDER BY edge_type, source"
        ).fetchall()
        # forward: singapore sub -> filer
        assert ("TVS Motor (Singapore) Pte", "TVS Motor Company", "subsidiary_of", 0) in rows
        # reverse: filer -> holding
        assert ("TVS Motor Company", "TVS Holdings", "subsidiary_of", 0) in rows
        # fellow sub -> same_group, canonical order, symmetric
        sg = [r for r in rows if r[2] == "same_group"][0]
        assert sg[0] < sg[1] and sg[3] == 1
        kinds = dict(conn.execute("SELECT name, entity_type FROM entities").fetchall())
        assert kinds["TVS Motor (Singapore) Pte"] == "company"  # created

    def test_unresolvable_filer_reported(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "NOSUCH",
                    "Nope Ltd",
                    "Other Co",
                    "Subsidiary",
                    "Group Companies",
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            ],
        )
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, n_unres = rps.apply_pairs(conn, pairs, {}, {}, dry_run=True)
        assert n_unres == 1 and n_edges == 0


class TestSupplyPass:
    def test_directional_supplier_edges(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "Sundaram Auto",
                    "Fellow Subsidiary",
                    "Group Companies",
                    "Sale of goods or services",
                    "1500",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/s.xml",
                ),
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "Sundaram Auto",
                    "Fellow Subsidiary",
                    "Group Companies",
                    "Purchase of goods or services",
                    "700",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/p.xml",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Sundaram Auto', 'company', 'Sundaram_Auto', NULL)"
        )
        conn.commit()
        companies = {"TVSMOTOR": "TVS Motor Company"}
        norm = {"SUNDARAM_AUTO": "Sundaram Auto"}
        pairs = rps.build_supply_pairs(con)
        n_edges, written = rps.apply_supply_pairs(conn, pairs, companies, norm, dry_run=False)
        assert written == 2  # two-way trading lands both directions
        rows = conn.execute(
            "SELECT source, target, properties FROM graph_edges WHERE edge_type='supplier_to'"
        ).fetchall()
        by_dir = {(r[0], r[1]): json.loads(r[2]) for r in rows}
        assert ("TVS Motor Company", "Sundaram Auto") in by_dir  # filer sells
        assert ("Sundaram Auto", "TVS Motor Company") in by_dir  # filer buys
        assert by_dir[("TVS Motor Company", "Sundaram Auto")]["sale_amount"] == 1500.0


class TestRatingsPass:
    def test_agency_entities_and_rated_by(self, lane):
        con, conn = lane
        con.execute(rps._DDL_RATINGS)
        con.execute("DELETE FROM credit_ratings")
        con.execute(
            "INSERT INTO credit_ratings (nse_symbol, rating_agency, credit_rating, "
            "rating_action, date_of_rating, outlook, instrument_name, xbrl_url, red_flag_reason) "
            "VALUES ('TVSMOTOR', 'CRISIL Ratings Limited', 'AA+', 'New', '2026-01-26', "
            "'Stable', 'EQ', 'https://x/r.xml', 'not_flagged')"
        )
        pairs = rps.build_rating_pairs(con)
        assert pairs[0]["rating"] == "AA+"
        n_ag, n_edges, written = rps.apply_rating_pairs(
            conn, pairs, {"TVSMOTOR": "TVS Motor Company"}, dry_run=False
        )
        assert n_ag == 1 and written == 1
        row = conn.execute(
            "SELECT source, target, properties FROM graph_edges WHERE edge_type='rated_by'"
        ).fetchone()
        assert row[0] == "TVS Motor Company" and row[1] == "CRISIL Ratings"
        assert json.loads(row[2])["rating"] == "AA+"
        kinds = dict(conn.execute("SELECT name, entity_type FROM entities").fetchall())
        assert kinds["CRISIL Ratings"] == "institution"
