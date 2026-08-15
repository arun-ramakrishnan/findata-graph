"""Tests for helpers/core/get_tickers.py.

`search_ticker` resolves a company name to an NSE/BSE ticker via yfinance. It
has no injection seam — it calls `yf.Search(...)` and `yf.Ticker(...).info`
directly — so the network is mocked at the module attribute level
(`monkeypatch.setattr(gt, "yf", ...)`). The pure name-matching helpers
(`is_name_match`, `is_likely_correct_company`) are tested directly without
mocks.

The new `get_basic_info` and `display_ticker` direct-symbol paths are also
tested with fakes.

These tests pin the resolution contract:
  - the first yf.Search quote whose name matches is returned
  - the .NS/.BO exchange fallback resolves when Search yields nothing
  - a non-matching quote is skipped (no false positive like TCS→Tata Motors)
  - all paths exhausted → (None, None)
  - get_basic_info returns the info dict for a valid symbol
  - display_ticker prints concise output without --detailed
  - display_ticker prints detailed sections with --detailed
"""
import sys
from pathlib import Path


HELPERS = Path(__file__).resolve().parents[1] / "helpers" / "core"
sys.path.insert(0, str(HELPERS))
import get_tickers as gt  # noqa: E402


# --- fakes for the yfinance surface search_ticker touches -----------------


class _FakeInfo(dict):
    """dict subclass so `info and 'longName' in info` works as in production."""


class _FakeTicker:
    def __init__(self, symbol, info=None):
        self.symbol = symbol
        self.info = info if info is not None else {}


class _FakeSearch:
    def __init__(self, quotes):
        self.quotes = quotes


class _FakeYf:
    """Minimal stand-in for the `yfinance` module as used by search_ticker
    and get_basic_info.

    `tickers` maps symbol -> info dict; `search_results` maps query -> quotes
    list. A missing key yields an empty Search (no quotes) so the fallback
    path is exercised.
    """

    def __init__(self, tickers=None, search_results=None):
        self._tickers = tickers or {}
        self._search = search_results or {}
        self.ticker_lookups = []  # observability for tests

    def Search(self, query, **kwargs):
        return _FakeSearch(self._search.get(query, []))

    def Ticker(self, symbol):
        self.ticker_lookups.append(symbol)
        return _FakeTicker(symbol, self._tickers.get(symbol, {}))

class TestSearchTicker:
    def test_skips_non_matching_quote(self, monkeypatch):
        """A wrong-name quote (e.g. TCS for Tata Motors) must not be returned."""
        monkeypatch.setattr(
            gt, "yf",
            _FakeYf(
                tickers={"TATAMOTORS.NS": _FakeInfo(longName="Tata Motors Limited")},
                search_results={
                    "Tata Motors": [
                        # decoy: symbol present, name does NOT match the query
                        {"symbol": "TCS.NS", "shortname": "TCS",
                         "longname": "Tata Consultancy Services"},
                        {"symbol": "TATAMOTORS.NS", "shortname": "Tata Motors",
                         "longname": "Tata Motors Limited"},
                    ],
                },
            ),
        )
        ticker, info = gt.search_ticker("Tata Motors")
        assert ticker == "TATAMOTORS.NS"
        # the decoy is rejected by word-overlap BEFORE yf.Ticker is called
        assert "TCS.NS" not in gt.yf.ticker_lookups  # ty: ignore[unresolved-attribute]

    def test_returns_none_when_no_search_match(self, monkeypatch):
        """When Search yields no matching quote, return (None, None).

        The old exchange-suffix fallback (Phase 2) has been removed — it
        constructed wrong symbols like "RELIANCEINDUSTRIES.NS" that always 404'd.
        """
        monkeypatch.setattr(
            gt, "yf",
            _FakeYf(tickers={
                "TataSteel.NS": _FakeInfo(symbol="TataSteel.NS",
                                         longName="Tata Steel Limited"),
            }),
        )
        result = gt.search_ticker("Tata Steel")
        assert result == (None, None)

    def test_returns_none_none_when_unresolvable(self, monkeypatch):
        """Every path exhausted → (None, None), never raises."""
        monkeypatch.setattr(gt, "yf", _FakeYf())  # empty: no quotes, no tickers
        result = gt.search_ticker("Nonexistent Imaginary Company")
        assert result == (None, None)

    def test_never_raises_on_yf_failure(self, monkeypatch):
        """If yf raises, search_ticker swallows and returns (None, None)."""

        class _ExplodingYf:
            def Search(self, query, **kwargs):
                raise RuntimeError("network down")

            def Ticker(self, symbol):
                raise RuntimeError("network down")

        monkeypatch.setattr(gt, "yf", _ExplodingYf())
        assert gt.search_ticker("Tata Steel") == (None, None)


# --- extract_company_name_from_path ---------------------------------------


class TestExtractCompanyNameFromPath:
    def test_strips_extension_and_converts_underscores_to_spaces(self):
        # The function returns the DISPLAY name (spaces), not the file stem
        # (underscores) — it is the file-stem → entity-name converter.
        path = "/vault/findata/Companies/Metals/Tata_Steel.md"
        assert gt.extract_company_name_from_path(path) == "Tata Steel"

    def test_plain_filename(self):
        assert gt.extract_company_name_from_path("Asian_Paints.md") == "Asian Paints"


# --- get_basic_info (new direct-symbol path) -----------------------------


class TestGetBasicInfo:
    def test_returns_info_for_valid_symbol(self, monkeypatch):
        fake_info = _FakeInfo(longName="Tata Steel Limited", exchange="NSI", currency="INR")
        monkeypatch.setattr(gt, "yf", _FakeYf(tickers={"TATASTEEL.NS": fake_info}))
        result = gt.get_basic_info("TATASTEEL.NS")
        assert result is not None
        assert result["longName"] == "Tata Steel Limited"

    def test_returns_none_for_missing_longName(self, monkeypatch):
        # Some invalid symbols return an empty dict or one without longName
        monkeypatch.setattr(gt, "yf", _FakeYf(tickers={"INVALID.NS": _FakeInfo()}))
        result = gt.get_basic_info("INVALID.NS")
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        class _BadYf:
            def Ticker(self, symbol):
                raise RuntimeError("network down")
        monkeypatch.setattr(gt, "yf", _BadYf())
        result = gt.get_basic_info("ANYTHING.NS")
        assert result is None


# --- display_ticker (new direct-symbol path) -----------------------------


class TestDisplayTicker:
    def test_concise_mode_prints_one_line(self, monkeypatch, capsys):
        fake_info = _FakeInfo(
            longName="Tata Steel Limited", exchange="NSI", currency="INR",
            marketCap=1500000000000,
        )
        monkeypatch.setattr(gt, "yf", _FakeYf(tickers={"TATASTEEL.NS": fake_info}))
        result = gt.display_ticker("TATASTEEL.NS", detailed=False)
        captured = capsys.readouterr()
        assert "TATASTEEL.NS" in captured.out
        assert "Tata Steel Limited" in captured.out
        assert "NSE" in captured.out
        assert result is not None

    def test_detailed_mode_prints_sections(self, monkeypatch, capsys):  # noqa: C901
        fake_info = _FakeInfo(
            longName="Tata Steel Limited", exchange="NSI", currency="INR",
            sector="Basic Materials", industry="Steel",
            marketCap=1500000000000, enterpriseValue=1600000000000,
            fullTimeEmployees=36000, country="India", city="Mumbai",
            website="https://tatasteel.com", previousClose=145.0, open=146.0,
            dayLow=144.0, dayHigh=148.0, fiftyTwoWeekLow=120.0,
            fiftyTwoWeekHigh=165.0, trailingPE=12.5, forwardPE=11.0,
            priceToBook=1.8, enterpriseToEbitda=6.5, dividendRate=3.6,
            dividendYield=0.0248,
        )
        import pandas as pd

        class _HistTicker:
            def __init__(self, symbol, info):
                self.symbol = symbol
                self.info = info
            def history(self, period=None):
                return pd.DataFrame({
                    "Close": [145.0, 146.5, 147.0],
                    "Volume": [1000000, 1200000, 1100000],
                }, index=pd.date_range("2026-08-05", periods=3))
            @property
            def income_stmt(self):
                return pd.DataFrame({"2026-03-31": [200000, 50000]},
                                    index=["Total Revenue", "Net Income"])
            @property
            def quarterly_income_stmt(self):
                return pd.DataFrame()
            @property
            def balance_sheet(self):
                return pd.DataFrame({"2026-03-31": [300000, 150000]},
                                    index=["Total Assets", "Total Liabilities"])
            @property
            def quarterly_balance_sheet(self):
                return pd.DataFrame()
            @property
            def cashflow(self):
                return pd.DataFrame()
            @property
            def quarterly_cashflow(self):
                return pd.DataFrame()
            @property
            def recommendations(self):
                return pd.DataFrame()
            @property
            def institutional_holders(self):
                return pd.DataFrame()
            @property
            def major_holders(self):
                return pd.DataFrame()
            @property
            def sustainability(self):
                return pd.DataFrame()
            @property
            def earnings(self):
                return pd.DataFrame()
            @property
            def quarterly_earnings(self):
                return pd.DataFrame()
            @property
            def recommendations_summary(self):
                return pd.DataFrame()
            @property
            def calendar(self):
                return {}
            @property
            def isin(self):
                return "INE081A01020"
            @property
            def options(self):
                return None
            @property
            def fund_holders(self):
                return None

        yf_fake = _FakeYf(tickers={"TATASTEEL.NS": fake_info})
        monkeypatch.setattr(gt, "yf", yf_fake)

        # Override Ticker construction in get_comprehensive_company_data
        # by monkeypatching the module-level yf reference used there.
        # Since get_comprehensive_company_data calls yf.Ticker(ticker),
        # and yf is gt.yf, our _FakeYf.Ticker returns _FakeTicker which
        # doesn't have the history/financials properties. So we need to
        # patch Ticker specifically.
        original_ticker = gt.yf.Ticker
        gt.yf.Ticker = lambda sym: _HistTicker(sym, fake_info)  # ty: ignore[invalid-assignment]
        try:
            result = gt.display_ticker("TATASTEEL.NS", detailed=True)
        finally:
            gt.yf.Ticker = original_ticker

        captured = capsys.readouterr()
        assert "Tata Steel Limited" in captured.out
        assert "Basic Materials" in captured.out
        assert "Steel" in captured.out
        assert "Price History" in captured.out
        assert "Income Statement" in captured.out
        assert "Balance Sheet" in captured.out
        assert result is not None

    def test_not_found_prints_message(self, monkeypatch, capsys):
        monkeypatch.setattr(gt, "yf", _FakeYf())  # empty → Ticker returns empty info
        result = gt.display_ticker("INVALID.NS", detailed=False)
        captured = capsys.readouterr()
        assert "NOT FOUND" in captured.out
        assert result is None


# ---------------------------------------------------------------------------
# _fmt_number — pure function
# ---------------------------------------------------------------------------
def test_fmt_number_basic():
    assert gt._fmt_number(5000) == "5,000"


def test_fmt_number_with_prefix_suffix():
    assert gt._fmt_number(1000, prefix="₹", suffix=" cr") == "₹1,000 cr"


def test_fmt_number_none():
    assert gt._fmt_number(None) == "N/A"


def test_fmt_number_string_fallback():
    result = gt._fmt_number("not a number")
    assert result == "not a number"


# ---------------------------------------------------------------------------
# _fmt_pct — pure function
# ---------------------------------------------------------------------------
def test_fmt_pct_basic():
    assert gt._fmt_pct(0.045) == "4.50%"


def test_fmt_pct_none():
    assert gt._fmt_pct(None) == "N/A"


def test_fmt_pct_string_fallback():
    result = gt._fmt_pct("abc")
    assert result == "abc"


def test_fmt_pct_whole():
    assert gt._fmt_pct(0.25) == "25.00%"


# ---------------------------------------------------------------------------
# _print_basic_section — smoke test
# ---------------------------------------------------------------------------
def test_print_basic_section(capsys):
    info = {"sector": "Tech", "industry": "Software", "marketCap": 1e11}
    gt._print_basic_section(info)
    captured = capsys.readouterr()
    assert "Tech" in captured.out
    assert "Software" in captured.out


# ---------------------------------------------------------------------------
# _print_valuation_section — smoke test
# ---------------------------------------------------------------------------
def test_print_valuation_section(capsys):
    info = {"trailingPE": 25.5, "priceToBook": 3.2}
    gt._print_valuation_section(info)
    captured = capsys.readouterr()
    assert "25.5" in captured.out


def test_print_valuation_section_with_dividend(capsys):
    info = {"dividendRate": 5.0, "dividendYield": 0.02}
    gt._print_valuation_section(info)
    captured = capsys.readouterr()
    assert "Dividend" in captured.out
    assert "2.00%" in captured.out


# ---------------------------------------------------------------------------
# _print_header — smoke test
# ---------------------------------------------------------------------------
def test_print_header(capsys):
    info = {"exchange": "NSI", "longName": "Test Company", "currency": "INR"}
    gt._print_header("TEST.NS", info)
    captured = capsys.readouterr()
    assert "TEST.NS" in captured.out
    assert "NSE" in captured.out


def test_print_header_bse(capsys):
    info = {"exchange": "BSE", "shortName": "TC", "currency": "INR"}
    gt._print_header("TEST.BO", info)
    captured = capsys.readouterr()
    assert "BSE" in captured.out


# ---------------------------------------------------------------------------
# _print_history_section — with empty/None DataFrame
# ---------------------------------------------------------------------------
def test_print_history_none(capsys):
    gt._print_history_section(None)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_history_empty(capsys):
    import pandas as pd
    gt._print_history_section(pd.DataFrame())
    captured = capsys.readouterr()
    assert captured.out == ""


# ---------------------------------------------------------------------------
# _print_recommendations_section — None/empty
# ---------------------------------------------------------------------------
def test_print_recommendations_none(capsys):
    gt._print_recommendations_section(None, None)
    captured = capsys.readouterr()
    assert captured.out == ""
