# Tests for helpers/maintenance/shareholding_sync.py
# (ownership_ingestion_nse_shp.md S4): hermetic parser/store/derive
# contract tests. Fixture XBRL mirrors the verified in-bse-shp structure
# (MainD/MainI contexts, <Category>_ContextI aggregates, D_<Category>_
# Context<N> named rows pairing with <Category>_Context<N> numerics) —
# no network.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from helpers.maintenance import shareholding_sync as ss

XBRLI_NS = "http://www.xbrl.org/2003/instance"


def _ctx(root, cid, symbol, instant=None, start=None, end=None):
    import xml.etree.ElementTree as ET

    c = ET.SubElement(root, f"{{{XBRLI_NS}}}context", {"id": cid})
    ent = ET.SubElement(c, f"{{{XBRLI_NS}}}entity")
    ident = ET.SubElement(ent, f"{{{XBRLI_NS}}}identifier", {"scheme": "http://x/bse-shp/Symbol"})
    ident.text = symbol
    per = ET.SubElement(c, f"{{{XBRLI_NS}}}period")
    if instant:
        ET.SubElement(per, f"{{{XBRLI_NS}}}instant").text = instant
    else:
        ET.SubElement(per, f"{{{XBRLI_NS}}}startDate").text = start
        ET.SubElement(per, f"{{{XBRLI_NS}}}endDate").text = end


def _fact(root, name, ctx, value):
    import xml.etree.ElementTree as ET

    el = ET.SubElement(root, f"{{{XBRLI_NS}}}unused", {"contextRef": ctx})
    el.tag = f"{{http://example.com/in-bse-shp}}{name}"
    el.text = value


def fixture_xbrl(symbol: str = "WHEELS", period_end: str = "2026-09-21") -> bytes:
    import xml.etree.ElementTree as ET

    root = ET.Element(f"{{{XBRLI_NS}}}xbrl")
    _ctx(root, "MainD", symbol, start="2026-07-01", end=period_end)
    _ctx(root, "MainI", symbol, instant=period_end)
    for cat in (
        "ShareholdingPattern",
        "PublicShareholding",
        "ShareholdingOfPromoterAndPromoterGroup",
    ):
        _ctx(root, f"{cat}_ContextI", symbol, instant=period_end)
    _ctx(root, "IndividualsOrHUF_Context3", symbol, instant=period_end)
    _ctx(root, "D_IndividualsOrHUF_Context3", symbol, instant=period_end)
    _ctx(root, "OthersIndianShareholders_Context1", symbol, instant=period_end)
    _ctx(root, "D_OthersIndianShareholders_Context1", symbol, instant=period_end)
    _fact(
        root,
        "ShareholdingAsAPercentageOfTotalNumberOfShares",
        "ShareholdingPattern_ContextI",
        "1.0",
    )
    _fact(
        root,
        "ShareholdingAsAPercentageOfTotalNumberOfShares",
        "PublicShareholding_ContextI",
        "0.6197",
    )
    _fact(
        root,
        "ShareholdingAsAPercentageOfTotalNumberOfShares",
        "ShareholdingOfPromoterAndPromoterGroup_ContextI",
        "0.3803",
    )
    # named holder 1: an individual at 6.59% (0.0659 fraction)
    _fact(root, "NameOfTheShareholder", "D_IndividualsOrHUF_Context3", "Manohar Lal Punglia")
    _fact(root, "PermanentAccountNumberOfShareholder", "D_IndividualsOrHUF_Context3", "******")
    _fact(root, "NumberOfFullyPaidUpEquityShares", "IndividualsOrHUF_Context3", "8220000")
    _fact(
        root,
        "ShareholdingAsAPercentageOfTotalNumberOfShares",
        "IndividualsOrHUF_Context3",
        "0.0659",
    )
    # named holder 2: a corporate holder at 27.81% (0.2781)
    _fact(
        root,
        "NameOfTheShareholder",
        "D_OthersIndianShareholders_Context1",
        "TSF INVESTMENTS LIMITED",
    )
    _fact(
        root, "PermanentAccountNumberOfShareholder", "D_OthersIndianShareholders_Context1", "******"
    )
    _fact(root, "NumberOfFullyPaidUpEquityShares", "OthersIndianShareholders_Context1", "7136608")
    _fact(
        root,
        "ShareholdingAsAPercentageOfTotalNumberOfShares",
        "OthersIndianShareholders_Context1",
        "0.2781",
    )
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8")


RSS_TEXT = """<rss version="2.0"><channel><title>NSE News</title>
<item><title>Wheels India Limited</title>
<link>https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_22092026032615_WEB.xml</link>
<description>AS ON DATE : 21-Sep-2026 | PR_AND_PRGRP: 38.03 | PUBLIC_VAL: 61.97 | EMPTR: 0 | NDS_REVISED_STATUS: - | SUBMISSION_DT: 22-Sep-2026 | REVISION_DT: -</description>
</item>
<item><title>Not a filing</title><link>https://nsearchives.nseindia.com/other/index.html</link>
<description>skip me</description></item>
</channel></rss>"""


class TestParse:
    def test_filing_meta_and_units(self):
        p = ss.parse_shp(fixture_xbrl())
        assert p["symbol"] == "WHEELS"
        assert p["period_start"] == "2026-07-01"
        assert p["period_end"] == "2026-09-21"
        # fractions -> percent
        assert p["promoter_pct"] == pytest.approx(38.03)
        assert p["aggregates"]["PublicShareholding"] == pytest.approx(61.97)
        assert p["aggregates"]["ShareholdingPattern"] == pytest.approx(100.0)

    def test_named_holders_pair_names_with_numerics(self):
        p = ss.parse_shp(fixture_xbrl())
        by_name = {h["holder_name"]: h for h in p["named"]}
        ind = by_name["Manohar Lal Punglia"]
        assert ind["category"] == "IndividualsOrHUF"
        assert ind["stake_pct"] == pytest.approx(6.59)
        assert ind["shares"] == 8220000
        assert ind["holder_pan"] is None  # masked in the public feed
        corp = by_name["TSF INVESTMENTS LIMITED"]
        assert corp["stake_pct"] == pytest.approx(27.81)


class TestRss:
    def test_items_and_scalars(self):
        items = ss.parse_rss(RSS_TEXT)
        assert len(items) == 1  # non-XBRL links skipped
        it = items[0]
        assert it["company"] == "Wheels India Limited"
        assert it["promoter_pct"] == pytest.approx(38.03)
        assert it["public_pct"] == pytest.approx(61.97)
        assert it["as_on"] == "21-Sep-2026"


@pytest.fixture()
def lane(tmp_path: Path):
    src = duckdb.connect(str(tmp_path / "sources.duckdb"))
    ss.ensure_sources_schema(src)
    src.execute(
        "CREATE TABLE exchange_listings (symbol VARCHAR, name VARCHAR, "
        "asset_type VARCHAR, exchange VARCHAR, segment VARCHAR)"
    )
    src.execute(
        "INSERT INTO exchange_listings VALUES ('WHEELS', 'Wheels India Limited', 'equity', 'NSE', 'main')"
    )
    conn = sqlite3.connect(tmp_path / "research.db")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
            normalized_name TEXT, file_path TEXT);
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL REFERENCES entities(name),
            target TEXT NOT NULL REFERENCES entities(name),
            edge_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}',
            valid_from DATE, valid_to DATE, source_ref TEXT NOT NULL,
            symmetric INTEGER NOT NULL DEFAULT 0,
            source_tier TEXT,
            UNIQUE(source, target, edge_type), CHECK (source != target));
        """
    )
    conn.execute("INSERT INTO entities VALUES ('Wheels India', 'company', 'Wheels_India', NULL)")
    # existing company entity that a corporate holder must reconcile to
    # normalized_name mirrors the pipeline's suffix-stripped form
    conn.execute(
        "INSERT INTO entities VALUES ('TSF Investments', 'company', 'TSF_INVESTMENTS', NULL)"
    )
    conn.commit()
    return src, conn


class TestStoreAndDerive:
    def test_store_roundtrip(self, lane):
        src, conn = lane
        url = "https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_22092026032615_WEB.xml"
        parsed = ss.parse_shp(fixture_xbrl())
        rss = ss.parse_rss(RSS_TEXT)[0]
        ss.store_filing(src, url, parsed, rss)
        n = src.execute("SELECT COUNT(*) FROM shp_filings").fetchone()[0]
        h = src.execute("SELECT COUNT(*) FROM shp_holders WHERE named=1").fetchone()[0]
        assert (n, h) == (1, 2)

    def test_apply_reconciles_company_holder_and_creates_person(self, lane):
        src, conn = lane
        url = "https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_22092026032615_WEB.xml"
        ss.store_filing(src, url, ss.parse_shp(fixture_xbrl()), ss.parse_rss(RSS_TEXT)[0])
        companies = ss._resolve_companies(src, conn)
        assert companies == {"WHEELS": "Wheels India"}
        norm_to_name = {
            (r[0] or "").upper(): r[1]
            for r in conn.execute("SELECT normalized_name, name FROM entities").fetchall()
        }
        cands, unresolved = ss.build_candidates(src, companies, set(norm_to_name), min_stake=0.1)
        assert unresolved == []
        by_holder = {c["holder"]: c for c in cands}
        # the corporate holder reconciles to the EXISTING company entity
        assert by_holder["TSF INVESTMENTS LIMITED"]["holder_kind"] == "company"
        assert by_holder["Manohar Lal Punglia"]["holder_kind"] == "person"
        n_ent, n_fresh, _ = ss.apply_candidates(conn, cands, norm_to_name, dry_run=True)
        assert (n_ent, n_fresh) == (1, 2)
        n_ent, n_fresh, written = ss.apply_candidates(conn, cands, norm_to_name, dry_run=False)
        assert written == 2
        rows = conn.execute(
            "SELECT source, target, weight, valid_from, source_ref, source_tier, properties "
            "FROM graph_edges WHERE edge_type='invested_in' ORDER BY weight DESC"
        ).fetchall()
        # canonical reconciliation: filing display name -> entity name
        assert rows[0][0] == "TSF Investments"
        assert rows[0][1] == "Wheels India"
        assert rows[0][2] == pytest.approx(27.81)
        assert rows[0][3] == "2026-09-21"
        assert rows[0][4] == "nse:shp:SHP_1_22092026032615_WEB"
        assert rows[0][5] == "regulator"
        assert json.loads(rows[0][6])["shares"] == 7136608
        assert rows[1][0] == "Manohar Lal Punglia"
        kinds = dict(conn.execute("SELECT name, entity_type FROM entities").fetchall())
        assert kinds["Manohar Lal Punglia"] == "person"
        assert "TSF INVESTMENTS LIMITED" not in kinds  # no duplicate shell entity

    def test_supersede_sets_valid_to(self, lane):
        src, conn = lane
        url1 = "https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_01092026000000_WEB.xml"
        url2 = "https://nsearchives.nseindia.com/corporate/xbrl/SHP_2_22092026000000_WEB.xml"
        ss.store_filing(src, url1, ss.parse_shp(fixture_xbrl(period_end="2026-06-30")), None)
        ss.store_filing(src, url2, ss.parse_shp(fixture_xbrl(period_end="2026-09-21")), None)
        companies = ss._resolve_companies(src, conn)
        norm_to_name = {
            (r[0] or "").upper(): r[1]
            for r in conn.execute("SELECT normalized_name, name FROM entities").fetchall()
        }
        cands, _ = ss.build_candidates(src, companies, set(norm_to_name), min_stake=0.1)
        # latest filing only (DISTINCT ON symbol, period_end DESC)
        assert all(c["valid_from"] == "2026-09-21" for c in cands)
        # a PRIOR edge from an older filing; the newer apply must supersede
        # its validity (valid_to = the new valid_from) instead of duplicating
        conn.execute(
            "INSERT INTO entities VALUES ('Manohar Lal Punglia', 'person', 'Manohar_Lal_Punglia', NULL)"
        )
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, weight, valid_from, source_ref) "
            "VALUES ('Manohar Lal Punglia', 'Wheels India', 'invested_in', 5.0, '2026-03-31', 'nse:shp:OLD')"
        )
        conn.commit()
        newer = [
            dict(c, valid_from="2026-12-31") for c in cands if c["holder"] == "Manohar Lal Punglia"
        ]
        n_ent, n_fresh, written = ss.apply_candidates(conn, newer, norm_to_name, dry_run=False)
        assert written == 1
        # the pair row IS the latest interval: new period, weight, open end
        row = conn.execute(
            "SELECT valid_from, valid_to, weight, source_ref FROM graph_edges "
            "WHERE source='Manohar Lal Punglia' AND target='Wheels India'"
        ).fetchone()
        assert row[0] == "2026-12-31"
        assert row[1] is None
        assert row[2] == pytest.approx(6.59)
        assert row[3].startswith("nse:shp:")
        n = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source='Manohar Lal Punglia' "
            "AND target='Wheels India'"
        ).fetchone()
        assert n[0] == 1  # exactly one row per pair


class TestSameGroup:
    def test_shared_corporate_holder_pairs(self, lane):
        src, conn = lane
        # one corporate holder holding two companies >= 10%
        url_a = "https://nsearchives.nseindia.com/corporate/xbrl/SHP_A_WEB.xml"
        url_b = "https://nsearchives.nseindia.com/corporate/xbrl/SHP_B_WEB.xml"
        ss.store_filing(src, url_a, ss.parse_shp(fixture_xbrl()), None)
        src.execute(
            "INSERT INTO exchange_listings (symbol, name, asset_type, exchange, segment) "
            "VALUES ('WHEELS2', 'Wheels Two Limited', 'equity', 'NSE', 'main')"
        )
        conn.execute("INSERT INTO entities VALUES ('Wheels Two', 'company', 'Wheels_Two', NULL)")
        conn.commit()
        ss.store_filing(src, url_b, ss.parse_shp(fixture_xbrl(symbol="WHEELS2")), None)
        companies = ss._resolve_companies(src, conn)
        assert set(companies) == {"WHEELS", "WHEELS2"}
        pairs = ss.build_same_group(src, companies, {"TSF_INVESTMENTS"}, min_stake=10.0)
        assert pairs == [("Wheels India", "Wheels Two", "TSF INVESTMENTS LIMITED")]


def test_normalize_strips_suffixes_and_collapses_whitespace():
    # case-preserving (house normalized_name style); the uppercase-folded
    # dedup key is built by callers (build_candidates holder_norm)
    assert ss._normalize("Wheels India Limited") == "Wheels_India"
    assert ss._normalize("MANOJ  B GANDHI").upper() == ss._normalize("Manoj B Gandhi").upper()


BSE_RSS_TEXT = """<rss version="2.0"><channel><title>BSE LATEST SHAREHOLDING PATTERNS</title>
<item><title>Gandhi Special Tubes Ltd-$ (513108)</title>
<link>/XBRLFILES/SHPXBRLDataXML/513108_229202617213_SP.html</link>
<description>AS ON DATE: 22-09-2026 | PR_AND_PRGRP: 71.70 | PUBLIC_VAL: 28.30 | STATUS: New | SUBMISSION_DT: 22-09-2026 | REVISED FILING DATE: -</description>
</item>
<item><title>Not a filing</title><link>/other/page.html</link><description>x</description></item>
</channel></rss>"""

BSE_HTML = """<html><body>
<table>
<tr><td>Scrip code</td><td>513108</td></tr>
<tr><td>NSE Symbol</td><td>GANDHITUBE</td></tr>
<tr><td>ISIN</td><td>INE524B01027</td></tr>
<tr><td>Quarter Ended / Half year ended/Date of Report (For Prelisting / Allotment)</td><td>22-09-2026</td></tr>
</table>
<table><tr><td>flags</td></tr></table>
<table>
<tr><td>Category (I)</td><td>Category of shareholder (II)</td><td>III</td><td>IV</td><td>V</td><td>VI</td><td>VII</td><td>VIII</td></tr>
<tr><td>(A)</td><td>Promoter &amp; Promoter Group</td><td>16</td><td>8090553</td><td></td><td></td><td>8090553</td><td>71.70%</td></tr>
<tr><td>(B)</td><td>Public</td><td>12262</td><td>3193347</td><td></td><td></td><td>3193347</td><td>28.30%</td></tr>
</table>
<table>
<tr><td>Searial No.</td><td>Category &amp; Name of the Shareholders (I)</td><td>Category</td><td>%</td><td>Bank</td><td>No of the Shareholders (III)</td><td>No.of fully paid up equity shares held (IV)</td><td>V</td><td>VI</td><td>Total nos.shares held (VII)</td><td>Shareholding as a % (VIII)</td><td>IX</td><td>X</td><td>XI</td><td>XII</td></tr>
<tr><td>(a)</td><td>Individuals/Hindu undivided family</td><td></td><td></td><td></td><td>14</td><td>7529710</td><td></td><td></td><td>7529710</td><td>66.73%</td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td>MANOJ B GANDHI</td><td></td><td></td><td></td><td>1</td><td>2829129</td><td></td><td></td><td>2829129</td><td>25.07%</td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td>QUANT MUTUAL FUND</td><td></td><td></td><td></td><td>1</td><td>50000</td><td></td><td></td><td>50000</td><td>0.44%</td><td></td><td></td><td></td><td></td></tr>
</table>
<table><tr><td>limits</td></tr></table>
</body></html>"""


class TestBseRss:
    def test_items_and_scalars(self):
        items = ss.parse_bse_rss(BSE_RSS_TEXT)
        assert len(items) == 1  # non-filing links skipped
        it = items[0]
        assert it["scrip"] == "513108"
        assert it["company"] == "Gandhi Special Tubes Ltd-$"
        assert it["promoter_pct"] == pytest.approx(71.70)
        assert it["url"].startswith("https://www.bseindia.com/XBRLFILES/")
        assert it["as_on"] == "22-09-2026"


class TestBseHtmlParse:
    def test_header_crossref_and_dates(self):
        p = ss.parse_shp_html(BSE_HTML)
        assert p["symbol"] == "GANDHITUBE"  # NSE Symbol cross-ref
        assert p["scrip_code"] == "513108"
        assert p["isin"] == "INE524B01027"
        assert p["period_end"] == "2026-09-22"  # dd-mm-yyyy -> ISO

    def test_aggregates_and_named_holders(self):
        p = ss.parse_shp_html(BSE_HTML)
        assert p["aggregates"]["Promoter & Promoter Group"] == pytest.approx(71.70)
        by_name = {h["holder_name"]: h for h in p["named"]}
        mg = by_name["MANOJ B GANDHI"]
        assert mg["shares"] == 2829129
        assert mg["stake_pct"] == pytest.approx(25.07)
        # category falls back to the current sub-category header
        assert mg["category"] == "Individuals/Hindu undivided family"
        assert by_name["QUANT MUTUAL FUND"]["stake_pct"] == pytest.approx(0.44)

    def test_store_roundtrip_bse(self, lane):
        src, conn = lane
        url = "https://www.bseindia.com/XBRLFILES/SHPXBRLDataXML/513108_229202617213_SP.html"
        ss.store_bse_filing(
            src, url, ss.parse_shp_html(BSE_HTML), ss.parse_bse_rss(BSE_RSS_TEXT)[0]
        )
        assert src.execute("SELECT source FROM shp_filings").fetchall() == [("BSE",)]
        assert src.execute("SELECT COUNT(*) FROM shp_holders WHERE named=1").fetchone()[0] == 2
        # filing id namespace does not collide with NSE ids
        fid = src.execute("SELECT filing_id FROM shp_filings").fetchone()[0]
        assert fid == "BSE_513108_229202617213"
