"""Tests for helpers/core/get_tickers.py.

`search_ticker` resolves a company name to an NSE/BSE ticker via yfinance. It
has no injection seam — it calls `yf.Search(...)` and `yf.Ticker(...).info`
directly — so the network is mocked at the module attribute level
(`monkeypatch.setattr(gt, "yf", ...)`). The pure name-matching helpers
(`fuzzy_match` pipeline, `vss_match`, `is_likely_correct_company`) are tested
directly without mocks.

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
            gt,
            "yf",
            _FakeYf(
                tickers={"TATAMOTORS.NS": _FakeInfo(longName="Tata Motors Limited")},
                search_results={
                    "Tata Motors": [
                        # decoy: symbol present, name does NOT match the query
                        {
                            "symbol": "TCS.NS",
                            "shortname": "TCS",
                            "longname": "Tata Consultancy Services",
                        },
                        {
                            "symbol": "TATAMOTORS.NS",
                            "shortname": "Tata Motors",
                            "longname": "Tata Motors Limited",
                        },
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
            gt,
            "yf",
            _FakeYf(
                tickers={
                    "TataSteel.NS": _FakeInfo(symbol="TataSteel.NS", longName="Tata Steel Limited"),
                }
            ),
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
            longName="Tata Steel Limited",
            exchange="NSI",
            currency="INR",
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
            longName="Tata Steel Limited",
            exchange="NSI",
            currency="INR",
            sector="Basic Materials",
            industry="Steel",
            marketCap=1500000000000,
            enterpriseValue=1600000000000,
            fullTimeEmployees=36000,
            country="India",
            city="Mumbai",
            website="https://tatasteel.com",
            previousClose=145.0,
            open=146.0,
            dayLow=144.0,
            dayHigh=148.0,
            fiftyTwoWeekLow=120.0,
            fiftyTwoWeekHigh=165.0,
            trailingPE=12.5,
            forwardPE=11.0,
            priceToBook=1.8,
            enterpriseToEbitda=6.5,
            dividendRate=3.6,
            dividendYield=0.0248,
        )
        import pandas as pd

        class _HistTicker:
            def __init__(self, symbol, info):
                self.symbol = symbol
                self.info = info

            def history(self, period=None):
                return pd.DataFrame(
                    {
                        "Close": [145.0, 146.5, 147.0],
                        "Volume": [1000000, 1200000, 1100000],
                    },
                    index=pd.date_range("2026-08-05", periods=3),
                )

            @property
            def income_stmt(self):
                return pd.DataFrame(
                    {"2026-03-31": [200000, 50000]}, index=["Total Revenue", "Net Income"]
                )

            @property
            def quarterly_income_stmt(self):
                return pd.DataFrame()

            @property
            def balance_sheet(self):
                return pd.DataFrame(
                    {"2026-03-31": [300000, 150000]}, index=["Total Assets", "Total Liabilities"]
                )

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
# vss_match — vector-similarity fallback (deferred N5 item)
# ---------------------------------------------------------------------------
def _embed_db(tmp_path, names, dims=16, seed=1):
    """Build a temp company_embeddings table from deterministic vectors.

    Uses a seeded per-name hash embedder so distinct names get distinct but
    repeatable vectors, and the query can be re-embedded the same way.
    """
    import hashlib

    import sqlite3

    db = tmp_path / "embeddings.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE company_embeddings ("
        "company_name TEXT PRIMARY KEY, embedding TEXT, model TEXT)"
    )

    def _vec(name):
        h = hashlib.sha256(f"{seed}:{name}".encode()).digest()
        v = []
        for i in range(dims):
            b = h[i * 4 : (i + 1) * 4]
            val = int.from_bytes(b, byteorder="little", signed=True)
            v.append(val / 2**31)
        norm = (sum(x * x for x in v)) ** 0.5
        return [x / norm for x in v]

    for name in names:
        vec = _vec(name)
        vec_str = "[" + ", ".join(repr(x) for x in vec) + "]"
        con.execute(
            "INSERT INTO company_embeddings VALUES (?, ?, ?)",
            (name, vec_str, f"test-v{dims}"),
        )
    con.commit()
    con.close()
    return db, _vec


def _test_embed_fn(vec_fn):
    return lambda query, dims: vec_fn(query)


class TestVssMatch:
    def test_exact_name_matches(self, tmp_path):
        db, vec_fn = _embed_db(tmp_path, ["Tata Consultancy Services", "Wipro"])
        match, score = gt.vss_match(
            "Tata Consultancy Services",
            ["Tata Consultancy Services", "Wipro"],
            db_path=db,
            embed_fn=_test_embed_fn(vec_fn),
        )
        assert match == "Tata Consultancy Services"
        assert score > 0.99

    def test_restricted_to_entities_list(self, tmp_path):
        db, vec_fn = _embed_db(tmp_path, ["Alpha", "Beta", "Gamma"])
        # Gamma has the highest cosine to "Alpha" textually in this scheme,
        # but is excluded by the entities allowlist.
        match, score = gt.vss_match(
            "Alpha", ["Alpha", "Beta"], db_path=db, embed_fn=_test_embed_fn(vec_fn)
        )
        assert match == "Alpha"

    def test_threshold_gates_low_similarity(self, tmp_path):
        db, vec_fn = _embed_db(tmp_path, ["One", "Two", "Three"])
        # A query that shares no hash-vector with any entity.
        match, score = gt.vss_match(
            "Zebra", ["One", "Two", "Three"], db_path=db, embed_fn=_test_embed_fn(vec_fn)
        )
        assert match is None
        assert score == 0.0

    def test_empty_table_returns_none(self, tmp_path):
        db, vec_fn = _embed_db(tmp_path, [])
        match, score = gt.vss_match("Any", ["Any"], db_path=db, embed_fn=_test_embed_fn(vec_fn))
        assert match is None
        assert score == 0.0

    def test_missing_table_returns_none(self, tmp_path):
        db = tmp_path / "no_table.db"
        match, score = gt.vss_match("Any", ["Any"], db_path=db)
        assert match is None
        assert score == 0.0

    def test_resolve_entity_vss_fallback(self, tmp_path, monkeypatch):
        """fuzzy_match misses, VSS catches — method is 'vss'."""
        db, vec_fn = _embed_db(tmp_path, ["Tata Consultancy Services", "Wipro"])
        monkeypatch.setattr(
            gt, "vss_match", lambda q, e, conn=None: ("Tata Consultancy Services", 0.98)
        )
        # "TataC Ltd" shares no distinctive token with the entity names
        # ("tatac" vs "tata consultancy services" — zero overlap), so no
        # heuristic fires and the VSS fallback catches it.
        info = _FakeInfo(longName="TataC Ltd")
        match, method = gt.resolve_entity("TCS.NS", info, ["Tata Consultancy Services", "Wipro"])
        assert match == "Tata Consultancy Services"
        assert method == "vss"

    def test_resolve_entity_heuristic_wins_before_vss(self, tmp_path, monkeypatch):
        """fuzzy_match's exact/word-overlap stage beats the VSS fallback."""
        db, vec_fn = _embed_db(tmp_path, ["Tata Consultancy Services", "Wipro"])
        monkeypatch.setattr(gt, "vss_match", lambda q, e, conn=None: ("Wipro", 1.0))
        info = _FakeInfo(longName="Tata Consultancy Services")
        match, method = gt.resolve_entity("TCS.NS", info, ["Tata Consultancy Services"])
        assert match == "Tata Consultancy Services"
        assert method == "exact"


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


# ---------------------------------------------------------------------------
# local_embeddings (2026-08-20): _pick_embedder routes bge-labeled rows
# through local_embedder.embed_query, or yields (None, 0) with a warning when
# the query can't be embedded into the SAME vector space as the stored rows.
# ---------------------------------------------------------------------------
class TestPickEmbedderLocalModel:
    @staticmethod
    def _rows(model, dims=384):
        vec_str = "[" + ", ".join(repr(0.02) for _ in range(dims)) + "]"
        return [("Acme", vec_str, model)]

    def test_local_label_uses_embed_query(self, monkeypatch):
        from helpers.core import local_embedder as LE

        monkeypatch.setattr(LE, "available", lambda: True)
        monkeypatch.setattr(LE, "embed_query", lambda t: [0.5] * 384)
        fn, dims = gt._pick_embedder(self._rows(LE.MODEL_ID), None)
        assert fn is not None
        assert dims == 384
        assert fn("anything", 384) == [0.5] * 384  # (query, dims) interface

    def test_local_label_unavailable_warns_and_skips(self, monkeypatch, capsys):
        from helpers.core import local_embedder as LE

        monkeypatch.setattr(LE, "available", lambda: False)
        fn, dims = gt._pick_embedder(self._rows(LE.MODEL_ID), None)
        assert (fn, dims) == (None, 0)
        assert "local embedder is unavailable" in capsys.readouterr().err

    def test_local_label_wrong_dims_skips(self, monkeypatch):
        from helpers.core import local_embedder as LE

        monkeypatch.setattr(LE, "available", lambda: True)
        # bge label on 64-dim rows: different vector space — no match rather
        # than zip-truncated garbage cosine.
        fn, dims = gt._pick_embedder(self._rows(LE.MODEL_ID, dims=64), None)
        assert (fn, dims) == (None, 0)

    def test_unknown_api_label_still_none(self):
        # Non-dry-run, non-local label: no local embedder can reconstruct the
        # query vector — unchanged pre-local_embeddings behaviour.
        fn, dims = gt._pick_embedder(self._rows("text-embedding-3-small"), None)
        assert (fn, dims) == (None, 0)

    def test_vss_match_end_to_end_with_local_model(self, tmp_path, monkeypatch):
        """Rows embedded by a fake embed_document are matched by queries sent
        through (a fake) embed_query — the full local-model contract."""
        import hashlib
        import sqlite3

        from helpers.core import local_embedder as LE

        def vec(text, dims=384):
            h = hashlib.sha256(f"7:{text}".encode()).digest()
            v = []
            for i in range(dims):
                b = h[(i * 4) % len(h) : (i * 4) % len(h) + 4]
                v.append(int.from_bytes(b, byteorder="little", signed=True) / 2**31)
            n = (sum(x * x for x in v)) ** 0.5
            return [x / n for x in v]

        db = tmp_path / "local.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE company_embeddings ("
            "company_name TEXT PRIMARY KEY, embedding TEXT, model TEXT)"
        )
        for name in ("Avanti Feeds", "Ramkrishna Exports"):
            vs = "[" + ", ".join(repr(x) for x in vec(name)) + "]"
            con.execute(
                "INSERT INTO company_embeddings VALUES (?, ?, ?)",
                (name, vs, LE.MODEL_ID),
            )
        con.commit()
        con.close()

        monkeypatch.setattr(LE, "available", lambda: True)
        monkeypatch.setattr(LE, "embed_query", lambda t: vec(t))
        match, score = gt.vss_match(
            "Avanti Feeds", ["Avanti Feeds", "Ramkrishna Exports"], db_path=db
        )
        assert match == "Avanti Feeds"
        assert score > 0.99
