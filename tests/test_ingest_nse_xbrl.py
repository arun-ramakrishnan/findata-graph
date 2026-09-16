"""Tests for helpers/maintenance/ingest_nse_xbrl.py (D20, offline)."""

import json
import sqlite3

from helpers.maintenance import ingest_nse_xbrl as nx


class FakeFiling:
    company_name = "Reliance Industries Limited"
    period_start = "2026-04-01"
    period_end = "2026-06-30"
    is_consolidated = True
    is_audited = False
    xbrl_url = "https://nsearchives.nseindia.com/XBRL/1695741.xml"
    q_revenue = "3118500000000.0"  # raw rupees, as the XBRL delivers
    q_pat = "23,196"
    q_diluted_eps = "15.48"
    bs_total_assets = None
    q_ebitda = "-"
    debt_equity_ratio = "0.41"


def test_parse_value_forms():
    assert nx.parse_value("3,11,850") == ("3,11,850", 311850.0, None)
    assert nx.parse_value("\u20b9 23,196 Cr") == ("\u20b9 23,196 Cr", 23196.0, None)
    assert nx.parse_value(15.48)[1] == 15.48
    assert nx.parse_value(None)[1] is None
    assert nx.parse_value("-")[1] is None
    assert nx.parse_value("")[1] is None


def test_fy_quarter_indian_fy():
    assert nx.fy_quarter("2026-06-30") == "FY27Q1"
    assert nx.fy_quarter("2026-09-30") == "FY27Q2"
    assert nx.fy_quarter("2026-12-31") == "FY27Q3"
    assert nx.fy_quarter("2027-03-31") == "FY27Q4"
    assert nx.fy_quarter("not-a-date") == "not-a-date"


def test_filing_to_rows_labels_units_period():
    rows = nx.filing_to_rows(FakeFiling(), "Reliance Industries")
    by_label = {r["metric_label"]: r for r in rows}
    assert by_label["q_revenue"]["value_num"] == 311850.0  # ₹ raw / 1e7
    assert by_label["q_revenue"]["unit"] == "crore_inr"
    assert by_label["q_diluted_eps"]["unit"] == "inr_per_share"
    assert by_label["debt_equity_ratio"]["unit"] == "ratio"
    assert by_label["q_revenue"]["period"] == "FY27Q1"
    assert by_label["q_revenue"]["source_ref"].endswith("1695741.xml")
    assert "bs_total_assets" not in by_label  # None skipped
    assert "q_ebitda" not in by_label  # '-' skipped
    props = json.loads(by_label["q_pat"]["properties"])
    assert props["consolidated"] is True and props["via"] == "nse-xbrl"


def test_apply_rows_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "create table company_metrics (id integer primary key, entity text,"
        " metric_label text, value_raw text, value_num real, unit text,"
        " period text, source_ref text not null, source_tier text,"
        " properties text)"
    )
    rows = nx.filing_to_rows(FakeFiling(), "Reliance Industries")
    n1 = nx.apply_rows(conn, rows)
    n2 = nx.apply_rows(conn, rows)
    assert n1 > 0 and n2 == 0


def test_targets_ns_only():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table entities (name text, ticker text, file_path text, entity_type text)")
    conn.executemany(
        "insert into entities values (?,?,?,?)",
        [
            ("Reliance Industries", "RELIANCE.NS", "a.md", "company"),
            ("Wabco India", None, "b.md", "company"),
            ("Apple", "AAPL", "c.md", "company"),
            ("A Stub", "STUB.NS", None, "company"),
        ],
    )
    got = nx.targets(conn)
    assert got == [("Reliance Industries", "RELIANCE.NS")]


class FakeClient:
    """List-first fake: counts XBRL document downloads."""

    def __init__(self, rows, docs):
        self.rows = rows  # listing["data"]
        self.docs = docs  # url -> xml
        self.downloads = []

    def get_integrated_filings(self, symbol, issuer, page=1, size=50):
        return {"data": self.rows}

    def get_integrated_xbrl(self, url):
        self.downloads.append(url)
        return self.docs.get(url)


def _seed_client(monkeypatch, rows, docs):
    fc = FakeClient(rows, docs)
    monkeypatch.setattr(nx, "_CLIENT", fc)
    return fc


def test_fetch_filings_skips_known_urls(monkeypatch):
    known = "https://nsearchives.nseindia.com/XBRL/1.xml"
    fresh = "https://nsearchives.nseindia.com/XBRL/2.xml"
    rows = [{"xbrl": known, "seq_Id": "1"}, {"xbrl": fresh, "seq_Id": "2"}]
    docs = {fresh: "<xml/>"}
    fc = _seed_client(monkeypatch, rows, docs)
    out = nx.fetch_filings("RELIANCE", "Reliance Industries", known_refs={known})
    assert [f.xbrl_url for f in out] == [fresh]  # known doc never downloaded
    assert fc.downloads == [fresh]


def test_fetch_filings_full_bypasses_skip(monkeypatch):
    known = "https://nsearchives.nseindia.com/XBRL/1.xml"
    rows = [{"xbrl": known, "seq_Id": "1"}]
    docs = {known: "<xml/>"}
    fc = _seed_client(monkeypatch, rows, docs)
    out = nx.fetch_filings("RELIANCE", "Reliance Industries", known_refs={known}, full=True)
    assert len(out) == 1 and fc.downloads == [known]
