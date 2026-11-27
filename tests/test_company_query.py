"""Tests for helpers/research/company_query.py — the composite company CLI.

Fixture: a minimal snapshot parquet tree on tmp_path (sqlite/sources/duckdb
sides) mounted via connect_snapshots — no live stores, so Sources runs in
pure snapshot mode and every open is an in-memory read-only connection.
Covers the resolver tiers (each key type, the four outcomes, label
normalization, the two-level taxonomy rule), the snapshot sections incl.
missing-table guards, the three group rosters with the cycle guard, bulk
output forms, and the digest contract (snippet <= 140, ambiguity surfaced,
honest 0 hits).
"""

import json
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from helpers.research import company_query as cq

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------

_DDL = {
    ("sqlite", "entities"): (
        "name VARCHAR, entity_type VARCHAR, sector_classification VARCHAR, ticker VARCHAR, "
        "cin VARCHAR, normalized_name VARCHAR, file_path VARCHAR",
        [
            (
                "Acme Bank",
                "company",
                "Banking",
                "ACME.NS",
                "U12345MH1993PLC000001",
                "Acme_Bank",
                "findata/Acme_Bank.md",
            ),
            (
                "Acme Technologies",
                "company",
                "Banking",
                "ACMETECH.NS",
                "U12345MH1998PLC000002",
                "Acme_Technologies",
                "findata/Acme_Technologies.md",
            ),
            (
                "Acme AMC",
                "company",
                "Banking",
                "",
                "U12345MH2005PLC000003",
                "Acme_AMC",
                "findata/Acme_AMC.md",
            ),
            (
                "Old Co",
                "company",
                "Banking",
                "OLD.NS",
                "U12345MH1990PLC000004",
                "Old_Co",
                "findata/Old_Co.md",
            ),
            (
                "Acme Softworks",
                "company",
                "Technology",
                "ACMESOFT.NS",
                "U12345MH2010PLC000005",
                "Acme_Softworks",
                "findata/Acme_Softworks.md",
            ),
            (
                "Acme Software Systems",
                "company",
                "Technology",
                "",
                "U12345MH2012PLC000006",
                "Acme_Software_Systems",
                "findata/Acme_Software_Systems.md",
            ),
            (
                "Tata Consultancy Services",
                "company",
                "Technology",
                "TCS.NS",
                "L11111TN1995PLC000007",
                "Tata_Consultancy_Services",
                "findata/TCS.md",
            ),
        ],
    ),
    ("sqlite", "entity_identifiers"): (
        "entity_name VARCHAR, identifier_type VARCHAR, identifier_value VARCHAR, "
        "namespace VARCHAR, source_ref VARCHAR",
        [
            ("Acme Bank", "isin", "INE123456789", "NSE", "exchange_sync"),
            ("Acme Bank", "figi", "BBG000FIXT01", "openfigi", "openfigi"),
            ("Old Co", "isin", "INE123456789", "NSE", "exchange_sync"),  # shared isin
            ("Acme Technologies", "isin", "INE098765432", "NSE", "exchange_sync"),
            ("Acme AMC", "alias", "ACME Money", "newsletter_variant", "merge"),
        ],
    ),
    ("sqlite", "entity_ticker_status"): (
        "entity_name VARCHAR, status VARCHAR, successor VARCHAR",
        [("Old Co", "amalgamated", "Acme Bank")],
    ),
    ("sqlite", "entity_tags"): (
        "entity_name VARCHAR, tag VARCHAR",
        [("Acme Bank", "sector/banking")],
    ),
    ("sqlite", "company_metrics"): (
        "entity VARCHAR, metric_label VARCHAR, value_raw VARCHAR, period VARCHAR, "
        "as_of_edition VARCHAR, consolidated BIGINT",
        [
            ("Acme Bank", "market_capitalization", "1,000 Cr", "latest", None, None),
            ("Acme Bank", "pe_ratio", "10.1", "latest", None, None),
        ],
    ),
    ("sqlite", "graph_analytics"): (
        "entity_name VARCHAR, metric VARCHAR, value VARCHAR",
        [
            ("Acme Bank", "pagerank", '{"value": 0.5}'),
            ("Acme Bank", "betweenness_centrality", '{"value": 0.1}'),
            (
                "Acme Bank",
                "link_prediction",
                '{"method": "jaccard", "candidates": [{"name": "x", "score": 0.1}, {"name": "y", "score": 0.2}, {"name": "z", "score": 0.3}, {"name": "a", "score": 0.4}, {"name": "b", "score": 0.5}, {"name": "c", "score": 0.6}]}',
            ),
        ],
    ),
    ("sqlite", "graph_edges"): (
        "source VARCHAR, target VARCHAR, edge_type VARCHAR",
        [("Acme Bank", "Acme Technologies", "same_group")],
    ),
    ("sqlite", "hyper_edges"): (
        "id BIGINT, label VARCHAR, edge_type VARCHAR",
        [
            (910, "Asset_Management", "sub_sector"),
            (911, "family:Acme", "group"),
            (912, "Acme Bank", "subsidiary"),
            (913, "vigil:rpt:ACME", "group"),
        ],
    ),
    ("sqlite", "hyper_incidences"): (
        "edge_id BIGINT, entity_name VARCHAR, role VARCHAR, weight DOUBLE",
        [
            (910, "Acme AMC", None, None),
            (911, "Acme Bank", None, None),
            (911, "Acme Holdings", None, None),
            (912, "Acme Technologies", None, None),
            (913, "Acme AMC", None, None),
            (913, "Acme Bank", None, None),
        ],
    ),
    ("sqlite", "ultimate_parent"): (
        "company VARCHAR, ultimate_parent VARCHAR, depth BIGINT, path VARCHAR, root_kind VARCHAR",
        [
            (
                "Acme Technologies",
                "Acme Global",
                2,
                '["Acme Technologies", "Acme Inter", "Acme Global"]',
                "chain-root",
            ),
            ("Acme Bank", "Acme Global", 1, '["Acme Bank", "Acme Global"]', "cycle-guard"),
        ],
    ),
    ("duckdb", "v_node"): (
        "id BIGINT, name VARCHAR, kind VARCHAR, market_cap VARCHAR",
        [
            (1, "Acme Bank", "company", "large_cap"),
            (2, "Acme Technologies", "company", "mid_cap"),
            (3, "Acme AMC", "company", None),
            (4, "Old Co", "company", None),
            (5, "Acme Softworks", "company", "small_cap"),
            (6, "Acme Software Systems", "company", None),
            (7, "Tata Consultancy Services", "company", "large_cap"),
            (901, "Banking", "sector", None),
            (902, "Financials", "super_sector", None),
            (903, "Asset_Management", "sub_sector", None),
            (905, "Energy", "sector", None),
            (906, "Energy_Super", "super_sector", None),
            (920, "NIFTY 50", "index", None),
        ],
    ),
    ("duckdb", "e_has"): (
        "sector_name BIGINT, company_name BIGINT",
        [(901, 1), (901, 2), (901, 3), (901, 4), (905, 5)],
    ),
    ("duckdb", "e_belongs_to"): ("child_id BIGINT, parent_id BIGINT", [(905, 906)]),
    ("duckdb", "e_listed_on_index"): (
        "company_id BIGINT, index_id BIGINT, properties VARCHAR, source_ref VARCHAR",
        [
            (1, 920, '{"as_of": "2026-01-01"}', "derive:indices"),
            (1, 920, '{"as_of": "2026-02-01"}', "derive:indices"),  # older vintage
        ],
    ),
    ("duckdb", "e_subsidiary"): (
        "parent_name BIGINT, subsidiary_name BIGINT, source_ref VARCHAR",
        [(1, 2, "mca"), (2, 1, "rpt")],  # a cycle on purpose
    ),
    ("duckdb", "e_group"): ("a_name BIGINT, b_name BIGINT, source_ref VARCHAR", [(1, 2, "vigil")]),
    ("duckdb", "e_acquired"): ("acquirer_name BIGINT, target_name BIGINT, source_ref VARCHAR", []),
    ("duckdb", "e_invested"): (
        "institution_name BIGINT, company_name BIGINT, source_ref VARCHAR",
        [],
    ),
    ("sources", "exchange_listings"): (
        "isin VARCHAR, exchange VARCHAR, segment VARCHAR, symbol VARCHAR, "
        "kite_tradingsymbol VARCHAR, industry VARCHAR, asset_type VARCHAR",
        [
            ("INE123456789", "NSE", "main", "ACME", "ACME", "Financial Services", "equity"),
            ("INE123456789", "BSE", "main", "500001", "ACME", "Financial Services", "equity"),
            (
                "INE098765432",
                "NSE",
                "main",
                "ACMETECH",
                "ACMETECH",
                "Financial Services",
                "equity",
            ),
        ],
    ),
    ("sources", "bhavcopy_sample"): (
        "isin VARCHAR, exchange VARCHAR, trade_date DATE, symbol VARCHAR, series VARCHAR",
        [("INE123456789", "NSE", "2026-09-30", "ACME", "EQ")],
    ),
    ("sources", "rpt_transactions"): (
        "symbol VARCHAR, period_end_date VARCHAR, counter_party VARCHAR, amount_during_period VARCHAR",
        [
            ("ACME", "31-MAR-2026", "Acme AMC", "1000.0"),
            ("ACME", "31-MAR-2026", "Acme Holdings", "2000.0"),
        ],
    ),
    ("sources", "shp_filings"): (
        "filing_id VARCHAR, symbol VARCHAR, period_end DATE, submission_dt VARCHAR, "
        "promoter_pct DOUBLE, public_pct DOUBLE",
        [("F1", "ACME", "2026-09-30", "2026-10-01", 55.0, 45.0)],
    ),
    ("sources", "shp_holders"): (
        "filing_id VARCHAR, holder_name VARCHAR, category VARCHAR, stake_pct DOUBLE",
        [("F1", "Acme Holdings", "Promoter", 55.0), ("F1", None, "Public", 45.0)],
    ),
    ("sources", "credit_ratings"): (
        "equity_isin VARCHAR, isin VARCHAR, rating_agency VARCHAR, credit_rating VARCHAR, "
        "rating_action VARCHAR, date_of_rating DATE, instrument_type VARCHAR",
        [("INE123456789", "INE123456789", "CRISIL", "AAA", "Reaffirm", "2026-03-05", "BOND")],
    ),
    ("sources", "index_constituents"): (
        "index_name VARCHAR, isin VARCHAR, as_of DATE",
        [
            ("NIFTY 50", "INE123456789", "2026-11-13"),
            ("NIFTY 50", "INE123456789", "2026-10-13"),  # older vintage
            ("NIFTY TEST", "INE098765432", "2026-11-13"),
            ("NIFTY TEST", "INE000000000", "2026-11-13"),  # unknown entity
        ],
    ),
    ("sources", "mca_cin"): (
        "cin VARCHAR, mca_name VARCHAR, status VARCHAR, class VARCHAR, pba VARCHAR, "
        "state VARCHAR, via VARCHAR, query VARCHAR",
        [("U12345MH1993PLC000001", "Acme Bank", "Active", "", "NA", "MH", "auto-web", "q")],
    ),
    ("sources", "figi_instruments"): (
        "figi VARCHAR, ticker VARCHAR, name VARCHAR, exch_code VARCHAR, "
        "market_sector VARCHAR, security_type VARCHAR",
        [("BBG000FIXT01", "ACME.NS", "Acme Bank", "XNSE", "Equity", "Common Stock")],
    ),
}


@pytest.fixture
def snap_root(tmp_path: Path) -> Path:
    con = duckdb.connect()
    try:
        for (side, table), (ddl, rows) in _DDL.items():
            p = tmp_path / "snapshots" / "parquet" / side / f"{table}.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"CREATE TABLE fx_{table} ({ddl})")
            if rows:
                ph = ",".join("?" * len(rows[0]))
                con.executemany(f"INSERT INTO fx_{table} VALUES ({ph})", rows)  # noqa: S608  ph ?-builder over fixture DDL
            con.execute(f"COPY fx_{table} TO '{p}' (FORMAT PARQUET)")
            con.execute(f"DROP TABLE fx_{table}")
    finally:
        con.close()
    return tmp_path


@pytest.fixture
def src(snap_root: Path) -> Iterator[cq.Sources]:
    s = cq.Sources(snap_root)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Resolver: keys 1-6, the four outcomes
# ---------------------------------------------------------------------------


class TestResolverIdentifierKeys:
    def test_ticker_key(self, src):
        t = cq.resolve(src, "ACME.NS")
        assert (t.kind, t.key, t.entities) == ("one", "ticker", ["Acme Bank"])

    def test_isin_key(self, src):
        t = cq.resolve(src, "INE098765432")
        assert (t.kind, t.entities) == ("one", ["Acme Technologies"])

    def test_figi_key(self, src):
        t = cq.resolve(src, "BBG000FIXT01")
        assert (t.kind, t.entities) == ("one", ["Acme Bank"])

    def test_cin_key(self, src):
        t = cq.resolve(src, "U12345MH1998PLC000002")
        assert (t.kind, t.entities) == ("one", ["Acme Technologies"])

    def test_well_formed_but_unstored_never_falls_through(self, src):
        t = cq.resolve(src, "INE999999999")
        assert t.kind == "unresolved"
        assert "not in store: isin" in t.note

    def test_shared_isin_is_candidates(self, src):
        t = cq.resolve(src, "INE123456789")
        assert t.kind == "candidates"
        assert {c["name"] for c in t.candidates} == {"Acme Bank", "Old Co"}

    def test_caps_symbol_stem(self, src):
        t = cq.resolve(src, "ACME")
        assert (t.kind, t.entities) == ("one", ["Acme Bank"])

    def test_lowercase_word_is_not_a_symbol(self, src):
        assert cq.resolve(src, "acme").kind == "relation"  # family:Acme, not symbol


class TestResolverTaxonomyAndFamily:
    def test_sector_label_variants(self, src):
        for q in ("Banking", "banking", "BANKING"):
            t = cq.resolve(src, q)
            assert (t.kind, t.label) == ("set", "Banking")
            assert len(t.entities) == 4

    def test_sub_sector_underscore_normalization(self, src):
        for q in ("asset_management", "Asset Management", "asset management"):
            t = cq.resolve(src, q)
            assert (t.kind, t.label, t.entities) == ("set", "Asset_Management", ["Acme AMC"])

    def test_two_level_label_defaults_to_sector(self, src):
        t = cq.resolve(src, "energy")
        assert (t.label, len(t.entities)) == ("Energy", 1)  # sector Acme Softworks

    def test_two_level_label_as_super_sector(self, src):
        t = cq.resolve(src, "energy", as_level="super_sector")
        assert t.kind == "set"
        assert t.entities == ["Acme Softworks"]  # via the sector under the super

    def test_family_label_bare_name(self, src):
        t = cq.resolve(src, "Acme")
        assert (t.kind, t.label, t.relation) == ("relation", "Acme", "family")
        assert set(t.entities) == {"Acme Bank", "Acme Holdings"}

    def test_family_label_explicit_prefix(self, src):
        t = cq.resolve(src, "family:acme")
        assert (t.kind, t.label) == ("relation", "Acme")


class TestResolverRelationGrammar:
    def test_lead_form(self, src):
        t = cq.resolve(src, "subsidiaries of ACME.NS")
        assert (t.kind, t.relation, t.label) == ("relation", "subsidiaries", "Acme Bank")

    def test_tail_form(self, src):
        t = cq.resolve(src, "Acme Bank subsidiaries")
        assert (t.kind, t.relation) == ("relation", "subsidiaries")

    def test_peers_lead(self, src):
        t = cq.resolve(src, "peers of Acme Bank")
        assert t.relation == "peers"

    def test_relation_with_unresolved_remainder(self, src):
        t = cq.resolve(src, "subsidiaries of Nope Ltd")
        assert t.kind == "unresolved"
        assert "Nope Ltd" in t.note


class TestResolverNameTiers:
    def test_exact_name(self, src):
        t = cq.resolve(src, "Acme Bank")
        assert (t.kind, t.key) == ("one", "name")

    def test_normalized_name(self, src):
        t = cq.resolve(src, "acme technologies limited")
        assert (t.kind, t.entities) == ("one", ["Acme Technologies"])

    def test_suffix_stripped_name(self, src):
        t = cq.resolve(src, "Acme Technologies Private Limited")
        assert (t.kind, t.entities, t.note) == ("one", ["Acme Technologies"], "suffix-stripped")

    def test_abbreviation(self, src):
        t = cq.resolve(src, "TCS")
        assert (t.kind, t.entities) == ("one", ["Tata Consultancy Services"])

    def test_alias_identifier(self, src):
        t = cq.resolve(src, "ACME Money")
        assert (t.kind, t.entities) == ("one", ["Acme AMC"])

    def test_candidates_ranked_listed_first(self, src):
        t = cq.resolve(src, "Acme Soft")
        assert t.kind == "candidates"
        assert [c["name"] for c in t.candidates] == ["Acme Softworks", "Acme Software Systems"]
        assert cq.MAX_CANDIDATES >= len(t.candidates)

    def test_nothing_auto_picked_below_exact(self, src):
        t = cq.resolve(src, "Acme Soft")
        assert t.kind != "one"  # candidates, never a fuzzy auto-pick

    def test_total_garbage_is_unresolved(self, src):
        t = cq.resolve(src, "promoter holding")
        assert t.kind == "unresolved"


# ---------------------------------------------------------------------------
# Snapshot sections (S1)
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_all_14_sections_present(self, src):
        report = cq.snapshot_sections(src, "Acme Bank")
        assert list(report["sections"]) == list(cq.SECTION_ORDER)
        assert report["source_mode"] == {
            "research": "snapshot",
            "sources": "snapshot",
            "graph": "snapshot",
        }

    def test_entity_section(self, src):
        ent = cq.snapshot_sections(src, "Acme Bank")["sections"]["entity"]
        assert ent["ticker"] == "ACME.NS"
        assert ent["market_cap_band"] == "large_cap"
        # no entity_ticker_status row for Acme Bank -> key absent, not None
        assert "ticker_status" not in ent

    def test_old_co_shows_ticker_status(self, src):
        ent = cq.snapshot_sections(src, "Old Co")["sections"]["entity"]
        assert ent["ticker_status"] == "amalgamated"
        assert ent["ticker_successor"] == "Acme Bank"

    def test_graph_analytics_value_normalization(self, src):
        ga = cq.snapshot_sections(src, "Acme Bank")["sections"]["graph_analytics"]
        assert ga["pagerank"] == 0.5
        assert len(ga["link_prediction"]["candidates"]) == 5  # trimmed

    def test_centrality_split(self, src):
        secs = cq.snapshot_sections(src, "Acme Bank")["sections"]
        assert secs["centrality"] == {"betweenness_centrality": 0.1}
        assert "pagerank" in secs["graph_analytics"]

    def test_listings_two_exchanges(self, src):
        rows = cq.snapshot_sections(src, "Acme Bank")["sections"]["listings"]
        assert {r["exchange"] for r in rows} == {"NSE", "BSE"}

    def test_rpt_summary(self, src):
        rpt = cq.snapshot_sections(src, "Acme Bank")["sections"]["rpt_summary"]
        assert rpt["symbol"] == "ACME"
        assert rpt["periods"][0]["txns"] == 2

    def test_shareholding_coverage_line(self, src):
        shp = cq.snapshot_sections(src, "Acme Bank")["sections"]["shareholding_recent"]
        assert shp["coverage"] == "1 of 1 filings (symbol ACME)"
        assert shp["top_holders_latest"][0]["holder"] == "Acme Holdings"

    def test_index_membership_latest_vintage_only(self, src):
        rows = cq.snapshot_sections(src, "Acme Bank")["sections"]["index_membership"]
        nifty = [r for r in rows if r["index"] == "NIFTY 50"]
        # index_constituents holds two vintages; the sources-side row must win
        # with the LATEST as_of (2026-11-13), not the e_listed_on_index vintage
        assert len(nifty) == 1 and nifty[0]["as_of"] == "2026-11-13"
        assert nifty[0]["source"] == "index_constituents"

    def test_mca_cin(self, src):
        mca = cq.snapshot_sections(src, "Acme Bank")["sections"]["mca_cin"]
        assert mca["cin"] == "U12345MH1993PLC000001"
        assert mca["status"] == "Active"

    def test_figi_section_without_instruments_table(self, snap_root):
        # AC1: no traceback when figi_instruments is absent
        (snap_root / "snapshots" / "parquet" / "sources" / "figi_instruments.parquet").unlink()
        s = cq.Sources(snap_root)
        try:
            figi = cq.snapshot_sections(s, "Acme Bank")["sections"]["figi"]
            assert figi["identifiers"] == ["BBG000FIXT01"]
            assert "instruments" not in figi
        finally:
            s.close()

    def test_figi_instruments_via_ticker_stem(self, src):
        figi = cq.snapshot_sections(src, "Acme Bank")["sections"]["figi"]
        assert figi["instruments"][0]["figi"] == "BBG000FIXT01"

    def test_snapshot_is_read_only(self, snap_root):
        # no DuckDB file is ever created; parquet is only read
        cq.Sources(snap_root).close()
        assert not list(snap_root.rglob("*.duckdb"))
        assert not list(snap_root.rglob("*.wal"))


# ---------------------------------------------------------------------------
# Group (S2)
# ---------------------------------------------------------------------------


class TestGroup:
    def _report(self, src, name="Acme Bank"):
        t = cq.resolve(src, name)
        return cq.group_report(src, t, depth=2, include=["subsidiaries", "peers"])

    def test_three_labeled_rosters(self, src):
        r = self._report(src)
        assert set(r["rosters"]) == {"family", "ownership", "disclosed"}
        assert r["rosters"]["family"][0]["label"] == "Acme"
        assert {m["name"] for m in r["rosters"]["family"][0]["members"]} >= {"Acme Bank"}

    def test_ownership_roster_flags_cycle_guard(self, src):
        own = self._report(src)["rosters"]["ownership"]
        assert own["apexes"] == ["Acme Global"]
        assert own["cycle_guard"] is True
        assert own["root_kinds"] == ["cycle-guard"]

    def test_disclosed_roster(self, src):
        disc = self._report(src)["rosters"]["disclosed"]
        names = {m["name"] for m in disc["members"]}
        assert "Acme AMC" in names  # via vigil:rpt:ACME

    def test_relation_walk_bounded_with_cycle(self, src):
        walk = self._report(src)["relations"]["subsidiaries"]
        names = [e["name"] for e in walk["edges"]]
        assert "Acme Technologies" in names
        assert any(e.get("cycle") for e in walk["edges"])  # 1 -> 2 -> 1 revisits

    def test_members_decorated(self, src):
        own = self._report(src)["rosters"]["ownership"]
        assert own["members"][0]["name"]  # every member has at least a name


# ---------------------------------------------------------------------------
# Bulk (S3)
# ---------------------------------------------------------------------------


class TestBulk:
    def test_bulk_sector_jsonl(self, src, capsys):
        rc = cq.main(["--repo-root", str(src.repo_root), "bulk", "Banking", "--format", "jsonl"])
        out = capsys.readouterr().out.strip().splitlines()
        assert rc == 0 and len(out) == 4
        row = json.loads(out[0])
        assert row["name"] and "identifiers" in row

    def test_bulk_fields_selection(self, src, capsys):
        cq.main(
            [
                "--repo-root",
                str(src.repo_root),
                "bulk",
                "Banking",
                "--format",
                "jsonl",
                "--fields",
                "entity",
            ]
        )
        row = json.loads(capsys.readouterr().out.strip().splitlines()[0])
        assert row["name"] == "Acme AMC"  # sorted first
        assert set(row) == {
            "name",
            "entity_type",
            "sector_classification",
            "ticker",
            "cin",
            "normalized_name",
            "note_path",
            "market_cap_band",  # key always present; None for Acme AMC
        }

    def test_summary_rows_aggregates(self):
        rows = [
            {"name": "A", "x": 1, "tags": ["a"], "ok": True},
            {"name": "B", "x": 3, "tags": [], "ok": False},
            {"name": "C", "x": 5, "tags": ["b", "c"]},
        ]
        out = cq.summary_rows(rows)
        by_field = {r["field"]: r for r in out}
        assert by_field["x"] == {"field": "x", "count": 3, "sum": 9.0, "median": 3.0}
        assert by_field["tags"]["count"] == 3 and by_field["tags"]["sum"] == 3.0
        assert "ok" not in by_field  # booleans are not numeric fields
        assert "name" not in by_field  # strings are not numeric fields

    def test_summary_skips_non_numeric_metric_values(self):
        out = cq.summary_rows(
            [{"company_metrics": {"market_capitalization": [{"value": "1,000 Cr"}]}}]
        )
        assert out == []  # raw string metric values never become a phantom empty field

    def test_bulk_summary_csv(self, src, capsys):
        rc = cq.main(
            ["--repo-root", str(src.repo_root), "bulk", "Banking", "--summary", "--format", "csv"]
        )
        lines = capsys.readouterr().out.strip().splitlines()
        assert rc == 0 and lines[0] == "field,count,sum,median"
        by_field = {ln.split(",")[0]: ln for ln in lines[1:]}
        assert by_field["index_membership"].startswith("index_membership,4,")

    def test_bulk_summary_json_names_members(self, src, capsys):
        cq.main(
            [
                "--repo-root",
                str(src.repo_root),
                "bulk",
                "Banking",
                "--summary",
                "--format",
                "json",
                "--fields",
                "entity,index_membership",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["members"] == 4
        fields = {r["field"] for r in payload["rows"]}
        assert "index_membership" in fields

    def test_bulk_tickers_file_reports_unresolved(self, src, tmp_path, capsys):
        f = tmp_path / "t.txt"
        f.write_text("ACME.NS\nINE098765432\nINE999999999\n")
        rc = cq.main(
            [
                "--repo-root",
                str(src.repo_root),
                "bulk",
                "--tickers-file",
                str(f),
                "--format",
                "json",
            ]
        )
        cap = capsys.readouterr()  # one read: readouterr resets the buffer
        err = cap.err
        payload = json.loads(cap.out)
        assert rc == 0
        assert len(payload["rows"]) == 2
        assert "INE999999999" in err

    def test_bulk_index_universe(self, src, capsys):
        rc = cq.main(
            ["--repo-root", str(src.repo_root), "bulk", "--index", "NIFTY TEST", "--format", "json"]
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["count"] == 1  # only Acme Technologies resolves
        assert any("INE000000000" in u["token"] for u in payload["unresolved"])

    def test_bulk_csv(self, src, capsys):
        cq.main(["--repo-root", str(src.repo_root), "bulk", "Banking", "--format", "csv"])
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 5  # header + 4 members
        assert lines[0].startswith("name,")


# ---------------------------------------------------------------------------
# Digest (S5)
# ---------------------------------------------------------------------------


class TestDigest:
    def test_entity_digest_snippet(self, src):
        hits, status = cq.digest_hits(src, "ACME.NS")
        assert status.startswith("1 hits")
        h = hits[0]
        assert h["path"] == "company:ACME.NS"
        assert h["kind"] == "company"
        assert len(h["snippet"]) <= 140
        assert h["snippet"].endswith("run: company_query.py snapshot --ticker ACME.NS")

    def test_set_digest(self, src):
        hits, status = cq.digest_hits(src, "Banking")
        h = hits[0]
        assert (h["path"], h["kind"]) == ("sector:Banking", "sector")
        assert "4 companies (3 listed)" in h["title"]
        assert "1 in NIFTY 50" in h["title"]
        assert len(h["snippet"]) <= 140

    def test_family_digest(self, src):
        hits, _ = cq.digest_hits(src, "Acme")
        assert hits[0]["path"] == "family:Acme"
        assert hits[0]["kind"] == "family"

    def test_ambiguous_emits_candidates(self, src):
        hits, status = cq.digest_hits(src, "INE123456789")
        assert len(hits) == 2
        assert all("ambiguous (2)" in h["title"] for h in hits)
        assert status.startswith("2 hits")

    def test_no_match_is_honest_zero(self, src):
        hits, status = cq.digest_hits(src, "promoter holding")
        assert hits == [] and status == "0 hits"

    def test_snippets_all_le_140(self, src):
        for q in ("ACME.NS", "Banking", "Acme", "asset_management"):
            hits, _ = cq.digest_hits(src, q)
            assert all(len(h["snippet"]) <= 140 for h in hits)


# ---------------------------------------------------------------------------
# Sources plumbing
# ---------------------------------------------------------------------------


class TestSources:
    def test_missing_live_stores_degrade_to_snapshot(self, src):
        assert all(d.mode == "snapshot" for d in src.domains.values())
        assert any("missing" in w for w in src.warnings)

    def test_has_and_fetch(self, src):
        assert src.has("research", "entities")
        assert not src.has("sources", "nonexistent")
        rows = src.fetch(
            "research", "entities", "SELECT name FROM {t} WHERE ticker = ?", ["ACME.NS"]
        )
        assert rows == [("Acme Bank",)]

    def test_served_store_routes_over_wire(self, snap_root, monkeypatch):
        class _Res:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class _WireStub:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None, **kw):
                self.calls.append(sql)
                if sql.strip() == "SHOW TABLES":
                    return _Res([("entities",)])
                return _Res([("Acme Bank",)])

            def close(self):
                pass

        stub = _WireStub()
        for rel in ("research.duckdb", "data/sources.duckdb", "graph.duckdb"):
            p = snap_root / "memory" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")  # exists() gates _attach_live before is_served
        monkeypatch.setattr(cq, "is_served", lambda p: True)
        monkeypatch.setattr(cq, "connect_shared", lambda path, read_only=True: stub)
        s = cq.Sources(snap_root)
        try:
            assert all(d.mode == "wire" for d in s.domains.values())
            assert not s.warnings
            assert s.has("research", "entities")
            rows = s.fetch("research", "entities", "SELECT name FROM {t}")
            assert rows == [("Acme Bank",)]
            assert stub.calls[-1] == "SELECT name FROM entities"  # unqualified over the wire
        finally:
            s.close()

    def test_wire_attach_failure_degrades_to_snapshot(self, snap_root, monkeypatch):
        def _boom(path, read_only=True):
            raise RuntimeError("wire down")

        for rel in ("research.duckdb", "data/sources.duckdb", "graph.duckdb"):
            p = snap_root / "memory" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")  # exists() gates _attach_live before is_served
        monkeypatch.setattr(cq, "is_served", lambda p: True)
        monkeypatch.setattr(cq, "connect_shared", _boom)
        s = cq.Sources(snap_root)
        try:
            assert all(d.mode == "snapshot" for d in s.domains.values())
            assert any("wire attach failed" in w for w in s.warnings)
        finally:
            s.close()

    def test_name_maps(self, src):
        assert src.name_map()["acme bank"] == "Acme Bank"
        assert src.suffix_map()["acme technologies"] == "Acme Technologies"
        assert cq.norm_label("Asset_Management") == "asset management"
        assert cq.norm_label("Energy_Super") == "energy"  # trailing _Super dropped


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    def test_bare_relation_phrase_routes_to_group(self, snap_root, capsys):
        rc = cq.main(["--repo-root", str(snap_root), "parent of Acme Technologies"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "## relation parents" in out

    def test_candidates_exit_1(self, snap_root, capsys):
        rc = cq.main(["--repo-root", str(snap_root), "snapshot", "Acme Soft"])
        assert rc == 1
        assert "candidates" in capsys.readouterr().out

    def test_unresolved_cin_exit_1(self, snap_root, capsys):
        rc = cq.main(["--repo-root", str(snap_root), "snapshot", "U99999MH1999PLC999999"])
        assert rc == 1
        assert "not in store: cin" in capsys.readouterr().out

    def test_snapshot_json_shape(self, snap_root, capsys):
        rc = cq.main(["--repo-root", str(snap_root), "snapshot", "ACME.NS", "--format", "json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert list(payload["sections"]) == list(cq.SECTION_ORDER)

    def test_digest_json_shape(self, snap_root, capsys):
        rc = cq.main(["--repo-root", str(snap_root), "digest", "ACME.NS"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["status"].startswith("1 hits")
        assert payload["results"][0]["path"] == "company:ACME.NS"
        # the digest names its source per domain (fixture: no live stores)
        assert payload["sources"] == {
            "research": "snapshot",
            "sources": "snapshot",
            "graph": "snapshot",
        }
