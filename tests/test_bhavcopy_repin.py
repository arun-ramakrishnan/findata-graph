from __future__ import annotations

from datetime import date

import duckdb

from helpers.maintenance import bhavcopy_repin as br


def test_parse_udiff_nse_rows() -> None:
    text = "TckrSymb,SctySrs,ISIN\nRELIANCE,EQ,INE002A01018\nTCS,EQ,INE467B01029\n bad,EQ,NOPE\n"
    rows = br.parse_udiff_csv(text, "NSE", date(2026, 11, 27))
    assert [r["ticker"] for r in rows] == ["RELIANCE.NS", "TCS.NS"]
    assert rows[0]["isin"] == "INE002A01018"
    assert rows[0]["trade_date"] == "2026-11-27"


def test_quarter_months_uses_previous_completed_quarter() -> None:
    assert br._quarter_months(date(2026, 11, 27)) == (
        "2026-Q3",
        [(2026, 7), (2026, 8), (2026, 9)],
    )
    assert br._quarter_months(date(2027, 1, 5)) == (
        "2026-Q4",
        [(2026, 10), (2026, 11), (2026, 12)],
    )


def test_parse_udiff_bse_numeric_scrip_code() -> None:
    text = "FinInstrmId,TckrSymb,ISIN,Sgmt\n500325,RELIANCE,INE002A01018,CM\n"
    rows = br.parse_udiff_csv(text, "BSE", date(2026, 11, 27))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "500325"
    assert rows[0]["ticker"] == "500325.BO"
    assert rows[0]["isin"] == "INE002A01018"


def test_days_back_skips_weekends_and_future_days() -> None:
    days = br._days_back(2026, 11, date(2026, 11, 27))
    assert days[:4] == [
        date(2026, 11, 27),
        date(2026, 11, 26),
        date(2026, 11, 25),
        date(2026, 11, 24),
    ]
    assert all(d.weekday() < 5 for d in days)
    assert max(days) == date(2026, 11, 27)


def test_previous_months_three_month_window() -> None:
    assert br._previous_months(date(2026, 11, 27), 3) == [
        (2026, 9),
        (2026, 10),
        (2026, 11),
    ]


def test_persist_sample_upserts_daily_rows(tmp_path) -> None:
    db = tmp_path / "sources.duckdb"
    staged = [
        {
            "month": "2026-09",
            "rows": {
                "NSE": [
                    {
                        "exchange": "NSE",
                        "trade_date": "2026-09-30",
                        "symbol": "RELIANCE",
                        "ticker": "RELIANCE.NS",
                        "isin": "INE002A01018",
                        "name": "",
                        "series": "EQ",
                    }
                ],
                "BSE": [
                    {
                        "exchange": "BSE",
                        "trade_date": "2026-09-30",
                        "symbol": "500325",
                        "ticker": "500325.BO",
                        "isin": "INE002A01018",
                        "name": "",
                        "series": "A",
                    }
                ],
            },
        }
    ]
    assert br.persist_sample(staged, "2026-Q3", path=db) == 2
    assert br.persist_sample(staged, "2026-Q3", path=db) == 2
    con = duckdb.connect(str(db))
    try:
        assert con.execute("select count(*) from bhavcopy_sample").fetchone()[0] == 2
        assert con.execute(
            "select series, quarter, source from bhavcopy_sample where ticker='500325.BO'"
        ).fetchone() == ("A", "2026-Q3", "bse_bhavcopy")
    finally:
        con.close()
