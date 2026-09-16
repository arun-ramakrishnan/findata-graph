"""Tests for helpers/maintenance/exchange_sync.py (D19)."""

import sqlite3
import zipfile
import duckdb

from helpers.maintenance import exchange_sync as xs


DEF = (
    "create table exchange_listings (isin varchar, name varchar, industry varchar,"
    " asset_type varchar, exchange varchar, segment varchar, symbol varchar,"
    " fetched_at date)"
)


def make_con():
    con = duckdb.connect(":memory:")
    con.execute(DEF)
    return con


def test_clean_name_strips_suffixes():
    assert xs.clean_name("Reliance Industries Ltd.") == "Reliance Industries"
    assert xs.clean_name("Foo Pvt. Ltd") == "Foo"
    assert xs.clean_name("Foo Private Limited") == "Foo"
    assert xs.clean_name("Bar (I) Limited") == "Bar (I)"
    assert xs.clean_name("") == ""


def test_normalized_name_underscore_form():
    assert xs.normalized_name("Bajaj Finserv") == "Bajaj_Finserv"
    assert xs.normalized_name("Arrowstreet Capital, Limited Partnership") == (
        "Arrowstreet_Capital_Limited_Partnership"
    )
    # D19 (2026-09-16): listing names must not leak punctuation — the
    # integrity check's normalization contract ([A-Za-z0-9_] only).
    assert xs.normalized_name("Adhiraj Distributors Ltd (ITP)") == ("Adhiraj_Distributors_Ltd_ITP")
    assert xs.normalized_name("ICICI Prudential Nifty EV & New Age Automotive ETF") == (
        "ICICI_Prudential_Nifty_EV_New_Age_Automotive_ETF"
    )
    assert xs.normalized_name("iSIF Hybrid Long-Short Fund - Growth") == (
        "iSIF_Hybrid_Long_Short_Fund_Growth"
    )
    assert xs.normalized_name("Dummy India Glycols ltd. 1") == "Dummy_India_Glycols_ltd_1"


def test_fold_new_and_idempotent():
    con = make_con()
    rows = [
        {
            "exchange": "LSE",
            "symbol": "AZN",
            "name": "AstraZeneca",
            "segment": "ftse100",
            "asset_type": "equity",
        }
    ]
    new, gone = xs.fold(con, rows, "2026-09-16", apply=True)
    assert len(new) == 1 and gone == []
    new2, _ = xs.fold(con, rows, "2026-09-16", apply=True)
    assert new2 == []  # idempotent
    n, stamp = con.execute("select count(*), max(fetched_at) from exchange_listings").fetchone()
    assert n == 1 and str(stamp) == "2026-09-16"


def test_fold_flags_gone_symbols():
    con = make_con()
    con.execute(
        "insert into exchange_listings values (null, \u0027Old Co\u0027, null, \u0027equity\u0027, \u0027LSE\u0027, \u0027ftse100\u0027, \u0027OLD\u0027, null)"
    )
    rows = [
        {
            "exchange": "LSE",
            "symbol": "AZN",
            "name": "AstraZeneca",
            "segment": "ftse100",
            "asset_type": "equity",
        }
    ]
    new, gone = xs.fold(con, rows, "2026-09-16", apply=True)
    assert len(new) == 1
    assert gone == [{"exchange": "LSE", "symbol": "OLD", "segment": "ftse100"}]


WIKI_HTML = """<html><body>
<table class="wikitable"><tr><th>Year</th><th>Close</th></tr>
<tr><td>2024</td><td>8,000</td></tr></table>
<table class="wikitable"><tr><th>Company</th><th>Ticker</th></tr>
<tr><td>Shell plc</td><td>SHEL</td></tr>
<tr><td>AstraZeneca</td><td>AZN</td></tr></table>
</body></html>"""


def test_pick_wiki_table_ignores_history_decoy():
    result = xs.pick_wiki_table(WIKI_HTML)
    assert result is not None  # None only when no wikitable exists
    hdr, trs = result
    assert hdr[0] == "company" and len(trs) == 3


def test_parse_wiki_table_pairs():
    pairs = xs.parse_wiki_table(WIKI_HTML)
    assert ("SHEL", "Shell plc") in pairs and ("AZN", "AstraZeneca") in pairs


def _mini_xlsx() -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/sharedStrings.xml",
            "<sst><si><t>Stock Code</t></si><si><t>Name</t></si>"
            "<si><t>Equity</t></si><si><t>00700</t></si>"
            "<si><t>Tencent</t></si><si><t>Debt</t></si></sst>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<ws><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c>'
            '<c t="s"><v>2</v></c></row>'
            '<row><c t="s"><v>3</v></c><c t="s"><v>4</v></c>'
            '<c t="s"><v>2</v></c></row>'
            '<row><c t="s"><v>3</v></c><c t="s"><v>4</v></c>'
            '<c t="s"><v>5</v></c></row></ws>',
        )
    return buf.getvalue()


def test_parse_hkex_filters_non_equity():
    rows = xs.parse_hkex_xlsx(_mini_xlsx())
    assert len(rows) == 1
    assert rows[0]["symbol"] == "00700" and rows[0]["name"] == "Tencent"
    assert rows[0]["exchange"] == "HKEX"


def test_seed_stubs_dedupe_and_skip_short():
    con = sqlite3.connect(":memory:")
    con.execute(
        "create table entities (name text primary key, ticker text,"
        " normalized_name text, entity_type text)"
    )
    rows = [
        {"exchange": "LSE", "symbol": "AZN", "name": "AstraZeneca plc", "segment": "ftse100"},
        {"exchange": "LSE", "symbol": "X", "name": "AB", "segment": "ftse100"},
    ]
    made = xs.seed_stubs(rows, apply=True, con=con)
    assert made == [("AstraZeneca plc", "AZN")]  # plc kept, 2-char skipped
    made2 = xs.seed_stubs(rows, apply=True, con=con)
    assert made2 == []  # insert-or-ignore dedupe


def test_write_worklist(tmp_path, monkeypatch):
    monkeypatch.setattr(xs, "WORKLIST", tmp_path / "wl.csv")
    xs.write_worklist(
        [{"exchange": "NSE", "symbol": "NEW.NS", "name": "New IPO Ltd", "segment": "main"}],
        [{"exchange": "NSE", "symbol": "OLD.NS", "segment": "main"}],
        [("New IPO", "NEW.NS")],
    )
    lines = (tmp_path / "wl.csv").read_text().strip().splitlines()
    assert lines[0].startswith("exchange,symbol,name")
    assert any("gone-review" in ln for ln in lines)
    assert any(ln.split(",")[2] == "New IPO Ltd" for ln in lines)


def test_fold_gone_scoped_to_fetched_segments():
    """wiki tsx60 fetch must not flag tsx/tsxv full-master rows as gone."""
    con = make_con()
    con.execute(
        "insert into exchange_listings values (null, 'Big Miner', null,"
        " 'equity', 'TSX', 'tsx', 'BM.TO', null)"
    )
    con.execute(
        "insert into exchange_listings values (null, 'Index Co', null,"
        " 'equity', 'TSX', 'tsx60', 'IDX.TO', null)"
    )
    rows = [
        {
            "exchange": "TSX",
            "symbol": "NEW.TO",
            "name": "New Co",
            "segment": "tsx60",
            "asset_type": "equity",
        }
    ]
    new, gone = xs.fold(con, rows, "2026-09-16", apply=True)
    assert len(new) == 1
    assert gone == [{"exchange": "TSX", "symbol": "IDX.TO", "segment": "tsx60"}]


def test_lane_fresh_per_exchange():
    con = make_con()
    # sse never fetched -> not fresh
    assert xs.lane_fresh(con, "sse", 80) is False
    con.execute(
        "insert into exchange_listings values (null, 'x', null, 'equity', 'SSE', 'main', '600000.SS', '2026-09-16')"
    )
    assert xs.lane_fresh(con, "sse", 80) is True
    # 2026-09-01 fetch is stale under a 7-day window on 2026-09-16 (relative to date.today())
    con.execute("update exchange_listings set fetched_at = date '2020-01-01'")
    assert xs.lane_fresh(con, "sse", 7) is False


def test_lane_fresh_all_exchanges_must_be_fresh():
    con = make_con()
    con.execute(
        "insert into exchange_listings values (null, 'a', null, 'equity', 'NASDAQ', 'main', 'AAPL', '2026-09-16')"
    )
    assert xs.lane_fresh(con, "us", 80) is False  # NYSE et al missing
    for exch, sym in (("NYSE", "BAC"), ("NYSEAMER", "FUN"), ("NYSEARCA", "SPY"), ("BATS", "ONEQ")):
        con.execute(
            "insert into exchange_listings values (null, 'x', null, 'equity', ?, 'main', ?, '2026-09-16')",
            [exch, sym],
        )
    assert xs.lane_fresh(con, "us", 80) is True


def test_lane_fresh_wiki_segment_scoped():
    con = make_con()
    # TSX rows exist but under the tmx segment, NOT tsx60 -> wiki not fresh
    con.execute(
        "insert into exchange_listings values (null, 'b', null, 'etp', 'TSX', 'etp', 'XXX', '2026-09-16')"
    )
    assert xs.lane_fresh(con, "wiki", 80) is False
    for exch, seg in (
        ("LSE", "ftse100"),
        ("XETRA", "dax40"),
        ("EURONEXT", "cac40"),
        ("TSX", "tsx60"),
    ):
        con.execute(
            "insert into exchange_listings values (null, 'x', null, 'equity', ?, ?, 'Y', '2026-09-16')",
            [exch, seg],
        )
    assert xs.lane_fresh(con, "wiki", 80) is True
