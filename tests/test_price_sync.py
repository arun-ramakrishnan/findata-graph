#!/usr/bin/env python3
"""Tests for helpers/maintenance/price_sync.py (test_coverage_gap_sweep S1).

The OHLCV backfill is the substrate the whole temporal lane trusts
(as-of joins, event studies, signal sweeps), so the batch/merge/atomic
write state machine is exercised here OFFLINE: ``_download_batch`` and
``load_tickers`` are faked, sleeps are patched out, and the parquet
lands in ``tmp_path`` — never the network, never the live store.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import ENTITIES_8COL  # noqa: E402

from helpers.maintenance import price_sync as ps  # noqa: E402

WEEKS = ["2026-01-05", "2026-01-12", "2026-01-19"]
FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def _multi_index_df(symbols: list[str], close_by_symbol: dict[str, list[float]]) -> pd.DataFrame:
    """MultiIndex (ticker, field) frame like yfinance group_by='ticker'."""
    cols = pd.MultiIndex.from_product([symbols, FIELDS], names=["ticker", "field"])
    data = {}
    for sym in symbols:
        closes = close_by_symbol[sym]
        for f in FIELDS:
            if f == "Close":
                data[(sym, f)] = closes
            elif f == "Volume":
                data[(sym, f)] = [1000.0] * len(closes)
            else:
                data[(sym, f)] = [c - 0.5 for c in closes]
    idx = pd.DatetimeIndex(pd.to_datetime(WEEKS[: len(closes)]), name="Date")
    return pd.DataFrame(data, index=idx, columns=cols)


@pytest.fixture()
def offline_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the store to tmp and silence the batch sleeps. _REPO_ROOT
    is redirected with it — build()/coverage() print OUT_PATH.relative_to
    (_REPO_ROOT), and a pytest tmp_path lives outside the repo."""
    monkeypatch.setattr(ps, "OUT_PATH", tmp_path / "px.parquet")
    monkeypatch.setattr(ps, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(ps.time, "sleep", lambda _s: None)


def test_happy_path_writes_one_row_per_week(monkeypatch: pytest.MonkeyPatch, offline_sync):
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS", "B.BO"])
    df = _multi_index_df(["A.NS", "B.BO"], {"A.NS": [10.0, 11.0, 12.0], "B.BO": [5.0, 5.0, 6.0]})
    monkeypatch.setattr(ps, "_download_batch", lambda chunk, since: df)

    rc = ps.build()
    assert rc == 0
    assert ps.OUT_PATH.exists()
    # the atomic write leaves no tmp artifact behind
    assert not ps.OUT_PATH.with_suffix(".parquet.tmp").exists()
    import duckdb

    duck = duckdb.connect()
    try:
        rows = duck.execute(
            "SELECT symbol, date, close, volume FROM read_parquet(?) ORDER BY symbol, date",
            [str(ps.OUT_PATH)],
        ).fetchall()
    finally:
        duck.close()
    assert len(rows) == 6  # 2 symbols x 3 weeks
    assert rows[0] == ("A.NS", "2026-01-05", 10.0, 1000)
    # closes are floats, volumes ints (int() cast in the row builder)
    assert all(isinstance(r[3], int) for r in rows)


def test_nan_cells_become_none_and_zero_not_nan(monkeypatch: pytest.MonkeyPatch, offline_sync):
    """NaN Open/Volume must not survive as NaN into the parquet — the row
    builder's self-equality check maps them to None / 0 (the as-of join
    downstream treats None as missing, never as a poisoned number)."""
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS"])
    df = _multi_index_df(["A.NS"], {"A.NS": [10.0, 11.0, 12.0]})
    df[("A.NS", "Open")] = [float("nan"), 9.5, float("nan")]
    df[("A.NS", "Volume")] = [float("nan"), 2000.0, float("nan")]
    monkeypatch.setattr(ps, "_download_batch", lambda chunk, since: df)

    assert ps.build() == 0
    import duckdb

    duck = duckdb.connect()
    try:
        rows = duck.execute(
            f"SELECT open, volume FROM read_parquet('{ps.OUT_PATH}') ORDER BY date"  # noqa: S608
        ).fetchall()
    finally:
        duck.close()
    assert rows[0] == (None, 0)
    assert rows[1] == (9.5, 2000)
    assert rows[2] == (None, 0)


def test_close_nan_row_is_dropped(monkeypatch: pytest.MonkeyPatch, offline_sync):
    """A bar without a Close is not a bar: dropna(subset=['Close']) must
    remove it, because every downstream return computation divides by it."""
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS"])
    df = _multi_index_df(["A.NS"], {"A.NS": [10.0, 11.0, 12.0]})
    df[("A.NS", "Close")] = [10.0, float("nan"), 12.0]
    for f in ("Open", "High", "Low", "Volume"):
        df[("A.NS", f)] = [10.5, float("nan"), 12.5] if f != "Volume" else [1.0, float("nan"), 1.0]
    monkeypatch.setattr(ps, "_download_batch", lambda chunk, since: df)

    assert ps.build() == 0
    import duckdb

    duck = duckdb.connect()
    try:
        dates = duck.execute(
            f"SELECT date FROM read_parquet('{ps.OUT_PATH}') ORDER BY date"  # noqa: S608
        ).fetchall()
    finally:
        duck.close()
    assert [d[0] for d in dates] == ["2026-01-05", "2026-01-19"]


def test_all_batches_failing_reports_and_writes_empty(
    monkeypatch: pytest.MonkeyPatch, offline_sync, capsys
):
    """Both attempts exhausted → the ticker lands in the failed list on
    stderr and the run still terminates 0 (recovery is --fill-missing,
    not a crash)."""
    monkeypatch.setattr(ps, "load_tickers", lambda: ["DEAD.NS"])

    def boom(chunk, since):
        raise RuntimeError("Rate limited: 429")

    monkeypatch.setattr(ps, "_download_batch", boom)
    assert ps.build() == 0
    err = capsys.readouterr().err
    assert "failed after retry" in err
    assert "DEAD.NS" in err
    # FIXED 2026-11-01 (was FINDING): the store is schema-typed, so the
    # zero-row parquet stays readable — --fill-missing can open the very
    # store it recovers instead of being locked out by a zero-column file.
    assert ps.OUT_PATH.exists()
    import duckdb

    duck = duckdb.connect()
    try:
        assert (
            duck.execute(
                f"SELECT COUNT(*) FROM read_parquet('{ps.OUT_PATH}')"  # noqa: S608
            ).fetchone()[0]
            == 0
        )
    finally:
        duck.close()


def test_flat_columns_single_ticker_batch_serves_the_lone_ticker(
    monkeypatch: pytest.MonkeyPatch, offline_sync
):
    """FIXED 2026-11-01 (was FINDING/crash): a single-ticker batch may
    collapse to flat (field-only) columns — the flat frame now attributes
    to the lone ticker and serves its 3 bars (previously died at the
    Index-in-list elementwise membership test with an ambiguous-truth
    ValueError)."""
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS"])
    flat = pd.DataFrame(
        {
            "Open": [9.5] * 3,
            "High": [10.5] * 3,
            "Low": [9.0] * 3,
            "Close": [10.0, 11.0, 12.0],
            "Volume": [1000.0] * 3,
        },
        index=pd.DatetimeIndex(pd.to_datetime(WEEKS), name="Date"),
    )
    monkeypatch.setattr(ps, "_download_batch", lambda chunk, since: flat)
    assert ps.build() == 0
    import duckdb

    duck = duckdb.connect()
    try:
        n = duck.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT symbol) FROM read_parquet('{ps.OUT_PATH}')"  # noqa: S608
        ).fetchone()
    finally:
        duck.close()
    assert n == (3, 1)


def test_flat_columns_multi_ticker_batch_fails_the_chunk(
    monkeypatch: pytest.MonkeyPatch, offline_sync, capsys
):
    """A flat frame from a MULTI-ticker batch is unattributable — the
    chunk fails loudly (recoverable via --fill-missing) instead of
    crashing, and the zero-row store stays readable."""
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS", "B.NS"])
    flat = pd.DataFrame(
        {
            "Open": [9.5] * 3,
            "High": [10.5] * 3,
            "Low": [9.0] * 3,
            "Close": [10.0, 11.0, 12.0],
            "Volume": [1000.0] * 3,
        },
        index=pd.DatetimeIndex(pd.to_datetime(WEEKS), name="Date"),
    )
    monkeypatch.setattr(ps, "_download_batch", lambda chunk, since: flat)
    assert ps.build() == 0
    err = capsys.readouterr().err
    assert "unattributable" in err
    assert "A.NS" in err and "B.NS" in err
    import duckdb

    duck = duckdb.connect()
    try:
        assert (
            duck.execute(
                f"SELECT COUNT(*) FROM read_parquet('{ps.OUT_PATH}')"  # noqa: S608
            ).fetchone()[0]
            == 0
        )
    finally:
        duck.close()


def test_retry_succeeds_on_second_attempt(monkeypatch: pytest.MonkeyPatch, offline_sync):
    """A transient batch failure is retried once (non-429 backoff path)."""
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS"])
    df = _multi_index_df(["A.NS"], {"A.NS": [10.0, 11.0, 12.0]})
    calls = {"n": 0}

    def flaky(chunk, since):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset")
        return df

    monkeypatch.setattr(ps, "_download_batch", flaky)
    assert ps.build() == 0
    assert calls["n"] == 2
    import duckdb

    duck = duckdb.connect()
    try:
        n = duck.execute(
            f"SELECT COUNT(*) FROM read_parquet('{ps.OUT_PATH}')"  # noqa: S608
        ).fetchone()[0]
    finally:
        duck.close()
    assert n == 3


def test_fill_missing_fetches_only_absent_symbols(monkeypatch: pytest.MonkeyPatch, offline_sync):
    """The 429-recovery path: a stored parquet with A.NS and a universe of
    {A.NS, B.NS} fetches ONLY B.NS and merges into what exists."""
    old = pa.Table.from_pylist(
        [
            {
                "symbol": "A.NS",
                "date": "2026-01-05",
                "open": None,
                "high": None,
                "low": None,
                "close": 10.0,
                "volume": 1,
            }
        ]
    )
    pq.write_table(old, ps.OUT_PATH, compression="zstd")
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS", "B.NS"])
    fetched: list[str] = []

    def fake_dl(chunk, since):
        fetched.extend(chunk)
        return _multi_index_df(["B.NS"], {"B.NS": [5.0, 6.0, 7.0]})

    monkeypatch.setattr(ps, "_download_batch", fake_dl)
    assert ps.build(fill_missing=True) == 0
    assert fetched == ["B.NS"]  # A.NS was already present — never re-fetched
    import duckdb

    duck = duckdb.connect()
    try:
        syms = duck.execute(
            f"SELECT DISTINCT symbol FROM read_parquet('{ps.OUT_PATH}') ORDER BY symbol"  # noqa: S608
        ).fetchall()
        n_a = duck.execute(
            f"SELECT COUNT(*) FROM read_parquet('{ps.OUT_PATH}') WHERE symbol='A.NS'"  # noqa: S608
        ).fetchone()[0]
    finally:
        duck.close()
    assert syms == [("A.NS",), ("B.NS",)]
    assert n_a == 1  # the old row survives the merge untouched


def test_fill_missing_with_complete_store_is_a_noop(monkeypatch: pytest.MonkeyPatch, offline_sync):
    """Universe fully present → early return, no download, store mtime
    untouched (nothing to fetch)."""
    old = pa.Table.from_pylist(
        [
            {
                "symbol": "A.NS",
                "date": "2026-01-05",
                "open": None,
                "high": None,
                "low": None,
                "close": 10.0,
                "volume": 1,
            }
        ]
    )
    pq.write_table(old, ps.OUT_PATH, compression="zstd")
    before = ps.OUT_PATH.stat().st_mtime_ns
    monkeypatch.setattr(ps, "load_tickers", lambda: ["A.NS"])

    def must_not_fetch(chunk, since):
        raise AssertionError("no download expected — store already complete")

    monkeypatch.setattr(ps, "_download_batch", must_not_fetch)
    assert ps.build(fill_missing=True) == 0
    assert ps.OUT_PATH.stat().st_mtime_ns == before


def test_coverage_census_math(monkeypatch: pytest.MonkeyPatch, offline_sync, capsys):
    """coverage() quantiles over a two-symbol store: 260-week deep + 10-week
    thin → thin-under-1y=1, and the date span is reported."""
    rows = [
        {
            "symbol": "DEEP.NS",
            "date": f"2026-{(i % 52) + 1:02d}-01",
            "open": None,
            "high": None,
            "low": None,
            "close": 100.0 + i,
            "volume": 1,
        }
        for i in range(260)
    ] + [
        {
            "symbol": "THIN.BO",
            "date": f"2026-{i + 1:02d}-01",
            "open": None,
            "high": None,
            "low": None,
            "close": 50.0,
            "volume": 1,
        }
        for i in range(10)
    ]
    pq.write_table(pa.Table.from_pylist(rows), ps.OUT_PATH, compression="zstd")
    assert ps.coverage() == 0
    out = capsys.readouterr().out
    assert "rows 270 | symbols with data 2" in out
    assert "thin/new/delisted): 1" in out
    assert "date range: 2026-01-01" in out


def test_coverage_without_store_exit_1(monkeypatch: pytest.MonkeyPatch, offline_sync, capsys):
    assert ps.coverage() == 1
    assert "run build first" in capsys.readouterr().out


def test_load_tickers_reads_entities_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real load_tickers over a fixture entities DB: distinct,
    non-empty tickers only, sorted (stable batches)."""
    db = tmp_path / "research.db"
    con = sqlite3.connect(str(db))
    con.execute(ENTITIES_8COL)
    con.executemany(
        "INSERT INTO entities (name, entity_type, ticker) VALUES (?, 'company', ?)",
        [("A Ltd", "A.NS"), ("B Ltd", "B.BO"), ("C Ltd", "A.NS"), ("D Ltd", None), ("E Ltd", "")],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(ps, "connect", lambda *a, **k: sqlite3.connect(str(db)))
    assert ps.load_tickers() == ["A.NS", "B.BO"]  # deduped, sorted, empties out
