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
        n_ent, n_edges, written, _, _cycles = rps.apply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
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
        n_ent, n_edges, written, n_unres, _cycles = rps.apply_pairs(
            conn, pairs, {}, {}, dry_run=True
        )
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
        n_edges, written, _cross = rps.apply_supply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
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


class TestSymmetricEmissionPrecision:
    """Vigil symmetric-emission S2/S3: same-ref mutual pairs are filing
    noise, cross-ref pairs are never touched, dups are flagged. S1
    measured: 37 same-ref subsidiary cycles (all exactly sub_fwd+sub_rev,
    15 rule-order artifacts) + 91 cross-period supplier aggregates out of
    4,452 same-ref supplier mutuals; 32 cross-ref mutuals stay."""

    @staticmethod
    def _seed_cycle(
        con, symbol="TVSMOTOR", ref_texts=("Holding Company", "Wholly owned subsidiary")
    ):
        """One filer, one counter entity, TWO raw spellings whose rows
        classify to opposite directions (how live cycles arise —
        build_pairs' DISTINCT ON is per raw counter_party)."""
        _seed_rpt(
            con,
            [
                (
                    symbol,
                    "TVS Motor Company Limited",
                    "ABC Auto Components Limited",
                    ref_texts[0],
                    "Group Companies",
                    "Any other transaction",
                    None,
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/rev.xml",
                ),
                (
                    symbol,
                    "TVS Motor Company Limited",
                    "ABC Auto Components Pvt Ltd",
                    ref_texts[1],
                    "Group Companies",
                    "Any other transaction",
                    None,
                    "31-MAR-2025",
                    "2025-03-31",
                    "https://x/fwd.xml",
                ),
            ],
        )

    def test_same_ref_sub_cycle_collapses_at_mint(self, lane):
        con, conn = lane
        self._seed_cycle(con)
        companies = {"TVSMOTOR": "TVS Motor Company"}
        norm = {"ABC_Auto_Components": "ABC Auto Components"}  # both spellings resolve
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _, n_cycles = rps.apply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
        assert n_cycles == 1
        assert written == 1  # only the sub_fwd direction survives
        rows = conn.execute(
            "SELECT source, target, properties FROM graph_edges WHERE edge_type='subsidiary_of'"
        ).fetchall()
        assert rows == [("ABC Auto Components", "TVS Motor Company", rows[0][2])]
        assert json.loads(rows[0][2])["xbrl_url"] == "https://x/fwd.xml"  # sub_fwd evidence

    def test_prune_drops_existing_same_ref_cycle_only(self, lane):
        con, conn = lane
        for name in ("ABC Auto Components", "Cross Co", "Cross Holding"):
            conn.execute(
                "INSERT INTO entities VALUES (?, 'company', ?, NULL)",
                (name, name.replace(" ", "_")),
            )
        # same-ref cycle (rpt:TVSMOTOR): (TVS->ABC) is the sub_rev noise side
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) "
            "VALUES (?, ?, 'subsidiary_of', ?, ?)",
            [
                (
                    "TVS Motor Company",
                    "ABC Auto Components",
                    "vigil:rpt:TVSMOTOR",
                    json.dumps({"relationship": "Holding Company"}),
                ),
                (
                    "ABC Auto Components",
                    "TVS Motor Company",
                    "vigil:rpt:TVSMOTOR",
                    json.dumps({"relationship": "Wholly owned subsidiary"}),
                ),
                # cross-ref mutual: NEVER touched
                (
                    "Cross Holding",
                    "Cross Co",
                    "vigil:rpt:OTHER",
                    json.dumps({"relationship": "Holding Company"}),
                ),
                (
                    "Cross Co",
                    "Cross Holding",
                    "vigil:rpt:CROSSCO",
                    json.dumps({"relationship": "Wholly owned subsidiary"}),
                ),
            ],
        )
        conn.commit()
        counts = rps.prune_symmetric_artifacts(
            conn, con, {"TVSMOTOR": "TVS Motor Company"}, {}, dry_run=False
        )
        assert counts["subsidiary_cycles_dropped"] == 1
        remaining = conn.execute(
            "SELECT source, target, source_ref FROM graph_edges "
            "WHERE edge_type='subsidiary_of' ORDER BY source_ref"
        ).fetchall()
        assert ("ABC Auto Components", "TVS Motor Company", "vigil:rpt:TVSMOTOR") in remaining
        # the cross-ref pair survives intact (2 rows)
        assert sum(1 for r in remaining if r[2] != "vigil:rpt:TVSMOTOR") == 2

    def test_prune_flags_entity_dup_never_merges(self, lane):
        con, conn = lane
        conn.execute("INSERT INTO entities VALUES ('Dup Co', 'company', 'Same_Thing', NULL)")
        conn.execute("INSERT INTO entities VALUES ('Dup Company', 'company', 'Same_Thing', NULL)")
        # the mutual pair's ENDPOINTS are the normalized-collision pair —
        # flagged for the resolver lane, never merged or cycle-cut
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) "
            "VALUES (?, ?, 'subsidiary_of', 'vigil:rpt:DUP', ?)",
            [
                ("Dup Co", "Dup Company", json.dumps({"relationship": "Holding Company"})),
                ("Dup Company", "Dup Co", json.dumps({"relationship": "Wholly owned subsidiary"})),
            ],
        )
        conn.commit()
        counts = rps.prune_symmetric_artifacts(
            conn, con, {"DUP": "TVS Motor Company"}, {}, dry_run=True
        )
        assert counts["entity_dup_flagged"] == 1
        assert counts["subsidiary_cycles_dropped"] == 0
        assert conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 2

    def test_supplier_crossperiod_collapses_to_dominant_direction(self, lane):
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
                    "31-MAR-2025",
                    "2025-03-31",
                    "https://x/p.xml",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Sundaram Auto', 'company', 'Sundaram_Auto', NULL)"
        )
        conn.commit()
        pairs = rps.build_supply_pairs(con)
        assert pairs[0]["sale_period"] == "31-MAR-2026"
        assert pairs[0]["buy_period"] == "31-MAR-2025"
        n_edges, written, n_cross = rps.apply_supply_pairs(
            conn,
            pairs,
            {"TVSMOTOR": "TVS Motor Company"},
            {"SUNDARAM_AUTO": "Sundaram Auto"},
            dry_run=False,
        )
        assert (n_edges, written, n_cross) == (1, 1, 1)
        src_e, dst_e, props = conn.execute(
            "SELECT source, target, properties FROM graph_edges WHERE edge_type='supplier_to'"
        ).fetchone()
        assert (src_e, dst_e) == ("TVS Motor Company", "Sundaram Auto")  # larger amount wins
        p = json.loads(props)
        assert p["sale_amount"] == 1500.0 and p["purchase_amount"] == 700.0  # both kept
        assert p["two_way"] == "cross-period"

    def test_supplier_same_period_two_way_still_survives(self, lane):
        """The existing two-way test's S2 twin: equal periods keep BOTH
        directions — the fix must not over-collapse genuine two-way."""
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
        n_edges, _, n_cross = rps.apply_supply_pairs(
            conn,
            rps.build_supply_pairs(con),
            {"TVSMOTOR": "TVS Motor Company"},
            {"SUNDARAM_AUTO": "Sundaram Auto"},
            dry_run=False,
        )
        assert (n_edges, n_cross) == (2, 0)

    def test_classify_mirror_agrees_with_sql(self, lane):
        """The load-bearing pin: classify_relationship reproduces the
        _CLASSIFY_SQL CASE order — including the rule-order artifact
        ('Subsidiary of Holding Company' routes sub_rev, which is what
        manufactured 15 of the 37 live cycles)."""
        cases = [
            ("Holding Company", None, "sub_rev"),
            ("Subsidiary of Holding Company", None, "sub_rev"),  # rule-order artifact
            ("Subsidiary of Ultimate holding company", None, "sub_rev"),  # artifact
            ("Wholly Owned Subsidiary", None, "sub_fwd"),
            ("Subsidiary", None, "sub_fwd"),
            ("Step-down subsidiary", None, "sub_fwd"),
            ("Fellow Subsidiary", None, "same_group"),
            ("Subsidiary of listed holding company", None, "sub_rev"),  # '%holding%' fires first
            ("Subsidiary of listed company", None, "same_group"),
            ("Joint Venture", None, "jv_with"),
            ("Key Management Personnel", "KMP", None),
            ("Anything", "Promoter Group", "same_group"),
            ("Anything", "Relatives", None),
        ]
        con, _conn = lane
        for rel, grp, want in cases:
            _seed_rpt(
                con,
                [
                    (
                        "SY",
                        "E",
                        "Counter Co",
                        rel,
                        grp or "Relatives",
                        None,
                        None,
                        None,
                        None,
                        None,
                    )
                ],
            )
            sql_cls = next((p["cls"] for p in rps.build_pairs(con)), None)
            py_cls = rps.classify_relationship(rel, grp)
            assert sql_cls == py_cls == want, (rel, sql_cls, py_cls, want)
