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

    def test_benefit_instruments_rejected(self):
        # 2026-10-16 exclusion (#400 flag): Ind AS 24 related-party
        # benefit trusts never become counter-parties — the relation
        # vocabulary has no class for 'company administers its trust'.
        assert rps._clean_counterparty("MedPlus Employees group gratuity assurance scheme") is None
        assert rps._clean_counterparty("Century Rayon Employees' Provident Fund Trust No.1") is None
        assert (
            rps._clean_counterparty("Kotak Mahindra Capital Company Superannuation Scheme") is None
        )
        assert rps._clean_counterparty("UltraTech Provident Fund") is None

    def test_pension_fund_managers_survive(self):
        # PFRDA-licensed operating companies, not instruments — the
        # negative lookahead keeps their (true) subsidiary_of edges.
        for name in (
            "Aditya Birla Sun Life Pension Fund Management",
            "HDFC Pension Management Company",
            "Axis Pension Fund Management",
            "Tata Pension Management Fund",
        ):
            assert rps._clean_counterparty(name) is not None, name


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


def _seed_rpt(con, rows, *, company_name=None):
    # company_name defaults to NULL, i.e. a GROUP-DISCLOSURE row where the
    # entity_name is some OTHER company speaking. Pass the filer's own legal
    # name to model a SELF-disclosure row (see the A4 discriminator in
    # apply_pairs): there the filer talks about itself, so its relationship
    # text is authoritative.
    con.execute("DELETE FROM rpt_transactions")
    if company_name is not None:
        rows = [(*r, company_name) for r in rows]
        con.executemany(
            "INSERT INTO rpt_transactions (symbol, entity_name, counter_party, "
            "relationship, rel_group, transaction_type, amount_during_period, "
            "period_end_date, period_end_date_parsed, xbrl_url, company_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return
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
        n_ent, n_edges, written, _, _cycles, _nc, _su, _vd = rps.apply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
        assert written == 3
        rows = conn.execute(
            "SELECT source, target, edge_type, symmetric FROM graph_edges ORDER BY edge_type, source"
        ).fetchall()
        # forward: singapore sub -> filer
        assert (
            "TVS Motor (Singapore) Pte",
            "TVS Motor Company",
            "subsidiary_of",
            0,
        ) in rows
        # reverse: filer -> holding
        assert ("TVS Motor Company", "TVS Holdings", "subsidiary_of", 0) in rows
        # fellow sub -> same_group, canonical order, symmetric
        sg = [r for r in rows if r[2] == "same_group"][0]
        assert sg[0] < sg[1] and sg[3] == 1
        kinds = dict(conn.execute("SELECT name, entity_type FROM entities").fetchall())
        assert kinds["TVS Motor (Singapore) Pte"] == "company"  # created

    def test_pairs_tie_break_prefers_trust_passing_relationship(self, lane):
        # Same (symbol, entity_name, counter_party, period) tie: the row
        # whose relationship text the ownership closure trusts
        # (metapath._STRICT_OWNERSHIP_REL) must win — pre-flight found the
        # TCS/'Tata sons' key landing on 'Ultimate Holding Company',
        # which the trust filter rejects, silently dropping Tata sons
        # as TCS's parent.
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "TCS",
                    "Tata Consultancy Services Limited",
                    "Tata Sons Private Limited",
                    "Ultimate Holding Company",
                    "Group Companies",
                    "Any other transaction",
                    "30000000",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/a.xml",
                ),
                (
                    "TCS",
                    "Tata Consultancy Services Limited",
                    "Tata Sons Private Limited",
                    "Holding Company",
                    "Group Companies",
                    "Any other transaction",
                    "10000000",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/b.xml",
                ),
            ],
        )
        pairs = rps.build_pairs(con)
        assert len(pairs) == 1
        assert pairs[0]["relationship"] == "Holding Company"

    def test_same_group_rejects_noncompany_endpoints(self, lane):
        # 2026-11-03 operator ruling: institutional investors (LIC,
        # HDFC LIFE, ...) are portfolio holders, not group members —
        # same_group pairs with a non-company-typed endpoint are
        # dropped at mint; the company<->company pair survives.
        con, conn = lane
        # canonical live name (fold registry maps the bare form to it)
        conn.execute(
            "INSERT INTO entities VALUES"
            " ('Life Insurance Corporation of India (LIC)', 'institution', 'LIC', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "Life Insurance Corporation of India",
                    "Associate",
                    "Group Companies",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "Sundaram Auto Components",
                    "Fellow Subsidiary",
                    "Group Companies",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            ],
        )
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _u, _c, n_nc, _su, _vd = rps.apply_pairs(
            conn, pairs, {"TVSMOTOR": "TVS Motor Company"}, {}, dry_run=False
        )
        assert n_nc == 1 and written == 1
        rows = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='same_group'"
        ).fetchall()
        assert rows == [("Sundaram Auto Components", "TVS Motor Company")]

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
        n_ent, n_edges, written, n_unres, _cycles, _nc, _su, _vd = rps.apply_pairs(
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
    def test_agency_entities_and_rated_by(self, lane, monkeypatch):
        # unregistered variant: no fold registered → legacy mint behavior
        monkeypatch.setattr(rps, "_fold_map", lambda: {})
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

    def test_folded_agency_resolves_not_reminted(self, lane, monkeypatch):
        """polyadic S4: a folded variant agency (name_dedup ledger) must
        resolve to its canonical BEFORE the mint check — the next vigil
        sync must not re-mint the merged-away entity."""
        con, conn = lane
        # the canonical exists as a company (reality: CRISIL is listed)
        conn.execute("INSERT INTO entities VALUES ('CRISIL', 'company', 'CRISIL', NULL)")
        conn.commit()
        monkeypatch.setattr(rps, "_fold_map", lambda: {"crisil ratings": "CRISIL"})
        con.execute(rps._DDL_RATINGS)
        con.execute("DELETE FROM credit_ratings")
        con.execute(
            "INSERT INTO credit_ratings (nse_symbol, rating_agency, credit_rating, "
            "rating_action, date_of_rating, outlook, instrument_name, xbrl_url, red_flag_reason) "
            "VALUES ('TVSMOTOR', 'CRISIL Ratings Limited', 'AA+', 'New', '2026-01-26', "
            "'Stable', 'EQ', 'https://x/r.xml', 'not_flagged')"
        )
        pairs = rps.build_rating_pairs(con)
        n_ag, n_edges, written = rps.apply_rating_pairs(
            conn, pairs, {"TVSMOTOR": "TVS Motor Company"}, dry_run=False
        )
        assert n_ag == 0, "folded variant must not re-mint an agency entity"
        assert written == 1
        row = conn.execute("SELECT target FROM graph_edges WHERE edge_type='rated_by'").fetchone()
        assert row[0] == "CRISIL", "rated_by must resolve to the canonical entity"
        assert not conn.execute(
            "SELECT COUNT(*) FROM entities WHERE name = 'CRISIL Ratings'"
        ).fetchone()[0]


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
        n_ent, n_edges, written, _, n_cycles, _nc, _su, _vd = rps.apply_pairs(
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
        assert (
            "ABC Auto Components",
            "TVS Motor Company",
            "vigil:rpt:TVSMOTOR",
        ) in remaining
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
                (
                    "Dup Co",
                    "Dup Company",
                    json.dumps({"relationship": "Holding Company"}),
                ),
                (
                    "Dup Company",
                    "Dup Co",
                    json.dumps({"relationship": "Wholly owned subsidiary"}),
                ),
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
        assert (src_e, dst_e) == (
            "TVS Motor Company",
            "Sundaram Auto",
        )  # larger amount wins
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
        _PAIRS_SQL CASE — guards first, catch-alls last (R6a/C1). The
        pre-cure rule-order artifacts ('Subsidiary of Holding Company'
        → sub_rev, which manufactured 15 of the 37 live cycles) now
        route to same_group; subsidiary-ADJACENT parties route to
        None instead of sub_fwd. A-prime (rpt_attribution §2.3): step-down
        texts flip to sub_rev — the party sits BELOW the counterparty —
        except 'of the company' texts (filer-owned step-down subs)."""
        cases = [
            ("Holding Company", None, "sub_rev"),
            (
                "Subsidiary of Holding Company",
                None,
                "same_group",
            ),  # sibling, not parent
            ("Subsidiary of Ultimate holding company", None, "same_group"),
            ("Subsidiary of listed holding company", None, "same_group"),
            ("Subsidiary of listed company", None, "same_group"),
            ("Subsidiary of the Company", None, "sub_fwd"),  # 'the Company' = the filer
            (
                "Wholly owned Subsidiary of APSEZ",
                None,
                "same_group",
            ),  # named third party
            (
                "Subsidiary/Joint Venture of an Entity with Significant Influence "
                "on Holding Company",
                None,
                "same_group",
            ),
            ("Wholly Owned Subsidiary", None, "sub_fwd"),
            ("Subsidiary", None, "sub_fwd"),
            ("Step-down subsidiary", None, "sub_rev"),  # A-prime: party sits BELOW the counterparty
            ("Stepdown Subsidiary", None, "sub_rev"),  # marker variant
            ("Step Down Subsidiary of the Company", None, "sub_fwd"),  # filer-owned: carve-out
            ("holding-subsidiary", None, "sub_fwd"),
            ("KMP of Subsidiary", None, None),  # was sub_fwd — the pollution class
            ("Related Party of Subsidiary", None, None),
            ("Key management personnel of Subsidiary", None, None),
            ("Fellow Subsidiary", None, "same_group"),
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


class TestSubsidiaryCureBackfill:
    """R6a/C2: the one-shot reclassification of vigil subsidiary_of
    edges under the guarded ladder — keep / reclass-to-same_group /
    reclass-to-jv_with / drop, prose rows untouched, idempotent."""

    def _seed(self, conn):
        ents = [
            "KidCo",
            "ParentCo",
            "SiblingA",
            "SiblingB",
            "KmpCo",
            "SomeCo",
            "JvSub",
            "BlankCo",
            "ProseKid",
            "ProseParent",
        ]
        conn.executemany(
            "INSERT INTO entities VALUES (?, 'company', ?, NULL)",
            [(e, e.lower()) for e in ents],
        )
        rows = [
            # (source, target, source_ref, properties) — all subsidiary_of
            (
                "KidCo",
                "ParentCo",
                "vigil:rpt:PARENT",
                {"relationship": "Wholly Owned Subsidiary"},
            ),  # keep
            (
                "SiblingB",
                "SiblingA",
                "vigil:rpt:SIB",
                {"relationship": "Subsidiary of Ultimate Holding Company"},
            ),  # → same_group
            (
                "KmpCo",
                "SomeCo",
                "vigil:rpt:KMP",
                {"relationship": "Key management personnel of Subsidiary"},
            ),  # drop
            (
                "JvSub",
                "SomeCo",
                "vigil:rpt:JV",
                {"relationship": "Joint Venture Subsidiary"},
            ),  # → jv_with
            ("BlankCo", "SomeCo", "vigil:rpt:RAW", {}),  # unverifiable → drop
            (
                "ProseKid",
                "ProseParent",
                "derive:relations:n1",
                {"relationship": None},
            ),  # non-vigil: untouched
        ]
        for s, t, ref, props in rows:
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) "
                "VALUES (?, ?, 'subsidiary_of', ?, ?)",
                (s, t, ref, json.dumps(props)),
            )
        conn.commit()

    def test_cure_dry_run_reports_plan_without_writing(self, lane):
        _con, conn = lane
        self._seed(conn)
        plan = rps.cure_subsidiary_backfill(conn, apply=False)
        assert plan["counts"] == {
            "scanned": 5,
            "keep": 1,
            "to_same_group": 1,
            "to_jv_with": 1,
            "dropped": 2,
            "unparsable_dropped": 1,
        }
        # nothing written: all six subsidiary rows still present
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='subsidiary_of'"
            ).fetchone()[0]
            == 6
        )

    def test_cure_apply_reclasses_drops_and_leaves_prose(self, lane):
        _con, conn = lane
        self._seed(conn)
        rps.cure_subsidiary_backfill(conn, apply=True)
        kept = conn.execute(
            "SELECT source, target, source_ref FROM graph_edges "
            "WHERE edge_type='subsidiary_of' ORDER BY source"
        ).fetchall()
        assert [tuple(r) for r in kept] == [
            ("KidCo", "ParentCo", "vigil:rpt:PARENT"),  # strict claim kept
            ("ProseKid", "ProseParent", "derive:relations:n1"),  # prose untouched
        ]
        # sibling pair → canonical orientation, symmetric, provenance kept
        sg = conn.execute(
            "SELECT source, target, symmetric, source_ref FROM graph_edges "
            "WHERE edge_type='same_group'"
        ).fetchall()
        assert [tuple(r) for r in sg] == [("SiblingA", "SiblingB", 1, "vigil:rpt:SIB")]
        jv = conn.execute(
            "SELECT source, target, symmetric FROM graph_edges WHERE edge_type='jv_with'"
        ).fetchall()
        assert [tuple(r) for r in jv] == [("JvSub", "SomeCo", 1)]
        # KMP + unverifiable rows are gone entirely
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE source IN ('KmpCo', 'BlankCo')"
            ).fetchone()[0]
            == 0
        )

    def test_cure_idempotent(self, lane):
        _con, conn = lane
        self._seed(conn)
        rps.cure_subsidiary_backfill(conn, apply=True)
        plan2 = rps.cure_subsidiary_backfill(conn, apply=True)
        assert plan2["counts"]["keep"] == 1 and plan2["counts"]["dropped"] == 0
        assert conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 4


class TestNameDedupS3:
    """entity_name_dedup S3: resolve-before-insert at the vigil boundary."""

    def test_footnote_markers_stripped_from_display(self):
        assert rps._safe_display("Sundaram Auto Components**") == "Sundaram Auto Components"
        assert rps._safe_display("Acme Industries *") == "Acme Industries"

    def test_census_key_hit_reuses_entity(self, lane, monkeypatch):
        con, conn = lane
        # existing entity 'E.I.D. Parry (India)'; feed ships the glued form
        conn.execute(
            "INSERT INTO entities VALUES ('E.I.D. Parry (India)', 'company', 'EID_Parry_India', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "E.I.D.PARRY (INDIA)",
                    "Fellow Subsidiary",
                    "Group Companies",
                    "Sale of goods or services",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/9.xml",
                )
            ],
        )
        monkeypatch.setattr(rps, "_fold_map", dict)  # no plan artifact in play
        pairs = rps.build_pairs(con)
        n_ent, _n, written, _u, _c, _nc, _su, _vd = rps.apply_pairs(
            conn, pairs, {"TVSMOTOR": "TVS Motor Company"}, {}, dry_run=False
        )
        assert n_ent == 0  # NO duplicate entity minted
        edge = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='same_group'"
        ).fetchone()
        assert set(edge) == {"TVS Motor Company", "E.I.D. Parry (India)"}
        assert written == 1

    def test_fold_map_wins_when_registry_misses(self, lane, monkeypatch):
        con, conn = lane
        conn.execute("INSERT INTO entities VALUES ('Parry Agro', 'company', 'Parry_Agro', NULL)")
        conn.commit()
        # plan artifact carries the variant fold (S1 registry-is-the-artifact)
        plan = tmp_plan(
            "== Parry Agro  (key: parry agro; edge rows 3)\n   Parry Agro Industries Ltd  -> fold\n"
        )
        monkeypatch.setattr(rps, "_NAME_DEDUP_PLAN", plan)
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor Company Limited",
                    "Parry Agro Industries Ltd",
                    "Fellow Subsidiary",
                    "Group Companies",
                    "Sale of goods or services",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/9.xml",
                )
            ],
        )
        pairs = rps.build_pairs(con)
        n_ent, _n, _w, _u, _c, _nc, _su, _vd = rps.apply_pairs(
            conn, pairs, {"TVSMOTOR": "TVS Motor Company"}, {}, dry_run=False
        )
        assert n_ent == 0
        edge = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='same_group'"
        ).fetchone()
        assert "Parry Agro" in set(edge)


def tmp_plan(text: str):
    import tempfile
    from pathlib import Path

    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


class TestNoLoad:
    def test_no_load_downloads_without_touching_sources_db(self, monkeypatch, capsys):
        # refresh-chain phase A runs --download --no-load in parallel with
        # sibling sources.duckdb openers: caches land on disk and the store
        # is never opened (the long RW hold that crashed exchange_sync).
        from pathlib import Path

        def boom(*_a, **_k):  # pragma: no cover - fail loudly if reached
            raise AssertionError("sources.duckdb must not be opened under --no-load")

        monkeypatch.setattr(rps, "download_bulk", lambda refetch=False: Path("rpt.csv.gz.zst"))
        monkeypatch.setattr(
            rps, "download_ratings", lambda refetch=False: Path("ratings.csv.gz.zst")
        )
        monkeypatch.setattr(rps.duckdb, "connect", boom)
        rc = rps.main(["--download", "--ratings-download", "--no-load"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "rpt_transactions cache ready: rpt.csv.gz.zst" in out
        assert "credit_ratings cache ready: ratings.csv.gz.zst" in out


class TestSubjectAttribution:
    """S2 (rpt_attribution A4): the edge subject is the filing's disclosed
    entity_name, not the filer — the fix for the inverted 'Holding
    Company' edges that re-rooted 238 companies under 'Reliance Lithium
    Werks B.V'."""

    def test_subject_is_disclosed_entity_not_filer(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor (Singapore) Pte Limited",
                    "TVS Motor Company",
                    "Holding Company",
                    "Group Companies",
                    "Any other transaction",
                    None,
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/1.xml",
                ),
            ],
        )
        companies = {"TVSMOTOR": "TVS Motor Company"}
        norm = {"TVS_MOTOR_COMPANY": "TVS Motor Company"}
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _u, _c, _nc, n_su, _vd = rps.apply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
        assert (n_su, written) == (0, 1)
        rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
        # the disclosed entity (the filer's own sub) is the CHILD; the
        # counterparty named as holding company is the PARENT
        assert rows == [("TVS Motor (Singapore) Pte", "TVS Motor Company", "subsidiary_of")]

    def test_of_the_company_carveout_keeps_filer_as_subject(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "TVS Motor (Singapore) Pte Limited",
                    "Sundaram Auto Components",
                    "Step Down Subsidiary of the Company",
                    "Group Companies",
                    "Sale of goods or services",
                    "500",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/2.xml",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Sundaram Auto Components', 'company', "
            "'SUNDARAM_AUTO_COMPONENTS', NULL)"
        )
        conn.commit()
        companies = {"TVSMOTOR": "TVS Motor Company"}
        norm = {"SUNDARAM_AUTO_COMPONENTS": "Sundaram Auto Components"}
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _u, _c, _nc, n_su, _vd = rps.apply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
        assert (n_su, written) == (0, 1)
        rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
        # 'of the company' = the counterparty is the FILER's own step-down
        # sub: the filer stays the subject (A4 carve-out; A-prime keeps
        # this row sub_fwd, not flipped)
        assert rows == [("Sundaram Auto Components", "TVS Motor Company", "subsidiary_of")]

    def test_filer_written_as_a_legal_variant_does_not_fork(self, lane):
        """A4 subject attribution's one real cost. INDHOTEL files rows whose
        entity_name is 'The Indian Hotels Company Limited' while the entity
        is 'Indian Hotels'. Resolving that through the shared ladder would
        MINT a second node and orphan every edge naming the filer (live: the
        bank's Indian-Hotels jv pin went 57 -> 0). A disclosed subject whose
        loose key equals the filer's IS the filer."""
        con, conn = lane
        conn.execute(
            "INSERT INTO entities VALUES ('Indian Hotels', 'company', 'INDIAN_HOTELS', NULL)"
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Taj GVK Hotels', 'company', 'TAJ_GVK_HOTELS', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "INDHOTEL",
                    "The Indian Hotels Company Limited",
                    "Taj GVK Hotels",
                    "Joint Venture",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/ihcl.xml",
                ),
            ],
            company_name="The Indian Hotels Company Limited",
        )
        companies = {"INDHOTEL": "Indian Hotels"}
        norm = {"TAJ_GVK_HOTELS": "Taj GVK Hotels"}
        pairs = rps.build_pairs(con)
        n_ent, _n, written, *_rest = rps.apply_pairs(conn, pairs, companies, norm, dry_run=False)
        # no entity was minted for the filer's long-form spelling
        assert n_ent == 0
        assert written == 1
        names = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
        assert not any("Indian Hotels Company" in n for n in names), names
        rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
        assert rows == [("Indian Hotels", "Taj GVK Hotels", "jv_with")]

    def test_relationship_is_read_relative_to_the_disclosed_entity(self, lane):
        """The disclosed entity_name is the SUBJECT of its own relationship
        text — not the filer. Verified against the store: REDINGTON row
        (entity_name 'REDINGTON RWANDA LTD', counter_party 'Redington Gulf
        FZE', relationship 'Holding company') really does mean Gulf FZE is
        Rwanda's parent. Re-reading it relative to the filer would flatten a
        true parent claim into same_group and emptied Redington's subsidiary
        pin (9 -> 0)."""
        con, conn = lane
        conn.execute("INSERT INTO entities VALUES ('REDINGTON', 'company', 'REDINGTON', NULL)")
        conn.execute(
            "INSERT INTO entities VALUES ('Redington Rwanda', 'company', 'REDINGTON_RWANDA', NULL)"
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Redington Gulf FZE', 'company', "
            "'REDINGTON_GULF_FZE', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "REDINGTON",
                    "Redington Rwanda",
                    "Redington Gulf FZE",
                    "Holding company",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/hold.xml",
                ),
            ],
            company_name="Redington Limited",
        )
        companies = {"REDINGTON": "REDINGTON"}
        norm = {
            "REDINGTON_RWANDA": "Redington Rwanda",
            "REDINGTON_GULF_FZE": "Redington Gulf FZE",
        }
        pairs = rps.build_pairs(con)
        assert len(pairs) == 1
        _e, _n, written, _u, _c, _nc, _su, _vd = rps.apply_pairs(
            conn, pairs, companies, norm, dry_run=False
        )
        assert written == 1
        rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
        # Rwanda -> Gulf FZE is a real parent claim and must survive
        assert rows == [("Redington Rwanda", "Redington Gulf FZE", "subsidiary_of")]

    def test_bare_stepdown_filer_self_lists_own_stepdowns(self, lane):
        """S3 polarity correction, verdict 1: a bare 'Step down subsidiary'
        row the FILER writes about ITSELF (entity_name ~= company_name)
        lists the filer's own step-down subsidiary — the counter is the
        CHILD (sub_fwd), never the parent. Live pattern: ADVENZYMES
        listing Cal India Foods as its step-down."""
        con, conn = lane
        conn.execute(
            "INSERT INTO entities VALUES "
            "('Advanced Enzyme Technologies', 'company', 'ADVANCED_ENZYME_TECHNOLOGIES', NULL)"
        )
        _seed_rpt(
            con,
            [
                (
                    "ADVENZYMES",
                    "Advanced Enzyme Technologies Limited",
                    "Cal India Foods International",
                    "Step down subsidiary",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/sd1.xml",
                ),
            ],
            company_name="Advanced Enzyme Technologies Limited",
        )
        companies = {"ADVENZYMES": "Advanced Enzyme Technologies"}
        norm = {"ADVANCED_ENZYME_TECHNOLOGIES": "Advanced Enzyme Technologies"}
        pairs = rps.build_pairs(con)
        _e, _n, written, *_rest = rps.apply_pairs(conn, pairs, companies, norm, dry_run=False)
        assert written == 1
        rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
        # Cal India Foods is the filer's step-down sub: child -> filer
        assert rows == [
            ("Cal India Foods International", "Advanced Enzyme Technologies", "subsidiary_of")
        ]

    def test_bare_stepdown_declared_ancestor_keeps_sub_rev(self, lane):
        """S3 polarity correction, verdict 2 (the ROUTE anchor): a bare
        'Step down subsidiary' row whose counterparty the SAME filer
        declares as its holding company points UP — the disclosed entity
        is the counterparty's step-down sub (sub_rev)."""
        con, conn = lane
        conn.execute(
            "INSERT INTO entities VALUES ('Route Mobile (UK)', 'company', 'ROUTE_MOBILE_UK', NULL)"
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Proximus Global', 'company', 'PROXIMUS_GLOBAL', NULL)"
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Route Mobile', 'company', 'ROUTE_MOBILE', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "ROUTE",
                    "Route Mobile Limited",
                    "Proximus Global",
                    "Holding Company",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/hold.xml",
                ),
                (
                    "ROUTE",
                    "Route Mobile (UK)",
                    "Proximus Global",
                    "Step down subsidiary",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/sd.xml",
                ),
            ],
            company_name="Route Mobile Limited",
        )
        companies = {"ROUTE": "Route Mobile"}
        norm = {
            "ROUTE_MOBILE": "Route Mobile",
            "ROUTE_MOBILE_UK": "Route Mobile (UK)",
            "PROXIMUS_GLOBAL": "Proximus Global",
        }
        pairs = rps.build_pairs(con)
        _e, _n, written, *_rest = rps.apply_pairs(conn, pairs, companies, norm, dry_run=False)
        assert written == 2
        rows = sorted(conn.execute("SELECT source, target FROM graph_edges").fetchall())
        # filer -> its holdco, and the UK sub -> the same holdco (UP)
        assert rows == [
            ("Route Mobile", "Proximus Global"),
            ("Route Mobile (UK)", "Proximus Global"),
        ]

    def test_bare_stepdown_nonself_default_points_down(self, lane):
        """S3 polarity correction, verdict 3 (the Ashok Leyland anchor): a
        bare 'Step Down Subsidiary' row about ANOTHER group company with no
        ancestor declaration for the counter defaults DOWN — the counter is
        the disclosed entity's step-down sub (sub_fwd). The A-prime flip
        inverted exactly these (live: Hinduja Tech -> Drive System Design)."""
        con, conn = lane
        conn.execute(
            "INSERT INTO entities VALUES ('Hinduja Tech', 'company', 'HINDUJA_TECH', NULL)"
        )
        conn.execute(
            "INSERT INTO entities VALUES ('Drive System Design', 'company', 'DRIVE_SYSTEM_DESIGN', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "ASHOKLEY",
                    "Hinduja Tech",
                    "Drive System Design",
                    "Step Down Subsidiary",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/sd.xml",
                ),
            ],
            company_name="Ashok Leyland Limited",
        )
        companies = {"ASHOKLEY": "Ashok Leyland"}
        norm = {
            "HINDUJA_TECH": "Hinduja Tech",
            "DRIVE_SYSTEM_DESIGN": "Drive System Design",
        }
        pairs = rps.build_pairs(con)
        _e, _n, written, *_rest = rps.apply_pairs(conn, pairs, companies, norm, dry_run=False)
        assert written == 1
        rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
        # Drive System Design is Hinduja Tech's step-down sub
        assert rows == [("Drive System Design", "Hinduja Tech", "subsidiary_of")]

    def test_reroute_made_mutual_conflict_resolves_by_recency(self, lane):
        """S3 polarity follow-up (ASHOKLEY DSD residual): two filings of the
        SAME filer pair the two companies in OPPOSITE orders under the bare
        step-down text (the FY26 row pairs (Hinduja Tech, DSD UK); the
        30-SEP-2025 row pairs (DSD UK, Hinduja Tech)). Both reroute to
        sub_fwd guesses, so the old co-control tie kept BOTH directions and
        minted an ownership cycle. A reroute-made tie resolves by filing
        recency: only the newest filing's direction survives."""
        con, conn = lane
        conn.execute(
            "INSERT INTO entities VALUES ('Hinduja Tech', 'company', 'HINDUJA_TECH', NULL)"
        )
        conn.execute(
            "INSERT INTO entities VALUES "
            "('Drive System Design', 'company', 'DRIVE_SYSTEM_DESIGN', NULL)"
        )
        conn.commit()
        _seed_rpt(
            con,
            [
                (
                    "ASHOKLEY",
                    "Drive System Design",
                    "Hinduja Tech",
                    "Step Down Subsidiary",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "30-SEP-2025",
                    "2025-09-30",
                    "https://x/sd_old.xml",
                ),
                (
                    "ASHOKLEY",
                    "Hinduja Tech",
                    "Drive System Design",
                    "Step down subsidiary",
                    "Group Companies",
                    "Any other transaction",
                    "0",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/sd_new.xml",
                ),
            ],
            company_name="Ashok Leyland Limited",
        )
        companies = {"ASHOKLEY": "Ashok Leyland"}
        norm = {
            "HINDUJA_TECH": "Hinduja Tech",
            "DRIVE_SYSTEM_DESIGN": "Drive System Design",
        }
        pairs = rps.build_pairs(con)
        assert len(pairs) == 2
        _e, _n, written, *_rest = rps.apply_pairs(conn, pairs, companies, norm, dry_run=False)
        # one direction survives — the FY26 filing's, not both
        assert written == 1
        rows = conn.execute("SELECT source, target FROM graph_edges").fetchall()
        # the NEWER filing says (entity=Hinduja Tech, counter=DSD) -> DSD
        # is Hinduja Tech's step-down sub: child -> parent
        assert rows == [("Drive System Design", "Hinduja Tech")]

    def test_cross_ref_vote_keeps_majority_direction(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "SYMA",
                    "Alpha Ltd",
                    "Beta Ltd",
                    "Subsidiary",
                    "Group Companies",
                    "Investment",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/a1.xml",
                ),
                (
                    "SYMC",
                    "Alpha Ltd",
                    "Beta Ltd",
                    "Subsidiary",
                    "Group Companies",
                    "Investment",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/c1.xml",
                ),
                (
                    "SYMB",
                    "Beta Ltd",
                    "Alpha Ltd",
                    "Subsidiary",
                    "Group Companies",
                    "Investment",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/b1.xml",
                ),
            ],
        )
        companies = {"SYMA": "Alpha", "SYMB": "Beta", "SYMC": "Alpha"}
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _u, _c, _nc, _su, n_vd = rps.apply_pairs(
            conn, pairs, companies, {}, dry_run=False
        )
        assert n_vd == 1
        rows = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='subsidiary_of'"
        ).fetchall()
        # two filings say Beta is Alpha's sub; one says the reverse —
        # the vote keeps the majority direction
        assert rows == [("Beta", "Alpha")]

    def test_vote_tie_keeps_mutual_claims(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "SYMA",
                    "Alpha Ltd",
                    "Beta Ltd",
                    "Subsidiary",
                    "Group Companies",
                    "Investment",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/a1.xml",
                ),
                (
                    "SYMB",
                    "Beta Ltd",
                    "Alpha Ltd",
                    "Subsidiary",
                    "Group Companies",
                    "Investment",
                    "100",
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/b1.xml",
                ),
            ],
        )
        companies = {"SYMA": "Alpha", "SYMB": "Beta"}
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _u, _c, _nc, _su, n_vd = rps.apply_pairs(
            conn, pairs, companies, {}, dry_run=False
        )
        assert n_vd == 0
        rows = conn.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type='subsidiary_of' ORDER BY source"
        ).fetchall()
        # 1-vs-1 tie: both sub_fwd claims persist (co-control, not a cycle)
        assert rows == [("Alpha", "Beta"), ("Beta", "Alpha")]

    def test_subject_unresolvable_counted(self, lane):
        con, conn = lane
        _seed_rpt(
            con,
            [
                (
                    "TVSMOTOR",
                    "Brand New Subject Ltd",
                    "TVS Motor Company",
                    "Holding Company",
                    "Group Companies",
                    "Any other transaction",
                    None,
                    "31-MAR-2026",
                    "2026-03-31",
                    "https://x/3.xml",
                ),
            ],
        )
        companies = {"TVSMOTOR": "TVS Motor Company"}
        norm = {"TVS_MOTOR_COMPANY": "TVS Motor Company"}
        pairs = rps.build_pairs(con)
        n_ent, n_edges, written, _u, _c, _nc, n_su, _vd = rps.apply_pairs(
            conn, pairs, companies, norm, max_new_entities=0, dry_run=False
        )
        # mint cap 0: the disclosed subject cannot be minted -> the pair
        # drops and is COUNTED, never silently skipped
        assert (n_su, written) == (1, 0)

    def test_purge_rpt_edges_dry_run_then_apply(self, lane):
        con, conn = lane
        conn.executemany(
            "INSERT INTO entities VALUES (?, 'company', ?, NULL)",
            [("A", "A"), ("B", "B")],
        )
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('A', 'B', 'subsidiary_of', 'vigil:rpt:SY')"
        )
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('A', 'B', 'same_group', 'vigil:rpt:SY')"
        )
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('A', 'B', 'jv_with', 'other:src')"
        )
        conn.commit()
        counts = rps.purge_rpt_edges(conn, ["subsidiary_of", "same_group"], dry_run=True)
        assert counts == {"subsidiary_of": 1, "same_group": 1}
        assert conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 3
        counts = rps.purge_rpt_edges(conn, ["subsidiary_of", "same_group"], dry_run=False)
        assert counts == {"subsidiary_of": 1, "same_group": 1}
        rows = conn.execute("SELECT edge_type FROM graph_edges").fetchall()
        assert rows == [("jv_with",)]  # non-vigil rows are never purged
