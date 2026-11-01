#!/usr/bin/env python3
"""Tests for helpers/research/price_join.py (test_coverage_gap_sweep S1).

``entity_week_census`` is the read-side recipe every long-horizon study
starts from; here it runs over a fixture parquet + fixture entities DB
(``connect`` is redirected), never the live store. The survivorship
asymmetry — entities WITHOUT Yahoo bars vanish from every join — is the
core behavior under pin, not an accident to paper over.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import ENTITIES_8COL  # noqa: E402

from helpers.research import price_join as pj  # noqa: E402


def _write_px(path: Path, bars: dict[str, int]) -> None:
    """bars: symbol → week count; weekly bars 2026-W01.. upward."""
    rows = []
    for sym, n in bars.items():
        rows.extend(
            {
                "symbol": sym,
                "date": f"2026-{(i % 52) + 1:02d}-{(i % 27) + 1:02d}",
                "open": None,
                "high": None,
                "low": None,
                "close": 100.0 + i,
                "volume": 1,
            }
            for i in range(n)
        )
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


@pytest.fixture()
def census_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fixture entities DB + fixture parquet, both reachable via the
    module's seams (OUT_PATH, connect)."""
    px = tmp_path / "px.parquet"
    db = tmp_path / "research.db"
    con = sqlite3.connect(str(db))
    con.execute(ENTITIES_8COL)
    con.executemany(
        "INSERT INTO entities (name, entity_type, ticker) VALUES (?, 'company', ?)",
        [("Alpha Ltd", "A.NS"), ("Beta Ltd", "B.BO"), ("Ghost Ltd", "G.NS")],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(pj, "OUT_PATH", px)

    def fake_connect(*a, **k):
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row  # the real connect() default — census reads r["ticker"]
        return con

    monkeypatch.setattr(pj, "connect", fake_connect)
    return px


def test_happy_join_split_and_absent(census_env):
    _write_px(census_env, {"A.NS": 100, "B.BO": 30})
    c = pj.entity_week_census()
    assert c["tickered_entities"] == 3
    assert c["joinable"] == 2
    assert c["yahoo_absent"] == 1  # Ghost Ltd has a ticker, no Yahoo history
    assert c["exchange_split"] == {"NSE": 1, "BSE": 1}
    assert c["rows"] == 130
    assert c["full_depth_5y"] == 0  # nothing reaches the 250-week bar yet
    assert c["thin_under_1y"] == 1  # B.BO at 30 weeks
    lo, hi = c["span"]
    assert lo.startswith("2026-") and hi.startswith("2026-")


def test_full_depth_threshold_is_250_weeks(census_env):
    """The 5y bar in the census dict is >=250 weeks (52*5=260 nominal;
    the threshold is deliberately the proposal's 250, not 260)."""
    _write_px(census_env, {"A.NS": 250, "B.BO": 249})
    c = pj.entity_week_census()
    assert c["full_depth_5y"] == 1
    assert c["thin_under_1y"] == 0  # 249 >= 52 — deep enough not to be thin


def test_entity_without_ticker_never_joins(census_env, monkeypatch: pytest.MonkeyPatch):
    _write_px(census_env, {"A.NS": 10})
    db = sqlite3.connect(str(pj.OUT_PATH.parent / "research.db"))
    db.execute("INSERT INTO entities (name, entity_type) VALUES ('No Ticker Ltd', 'company')")
    db.commit()
    db.close()
    c = pj.entity_week_census()
    assert c["tickered_entities"] == 3  # the NULL-ticker row is filtered by the WHERE
    assert c["joinable"] == 1


def test_duplicate_tickers_count_twice(census_env):
    """Two entities sharing a ticker are counted twice — the census is a
    per-ENTITY join, not a per-symbol one (pin: deliberate, the entity
    universe is the denominator of every study)."""
    _write_px(census_env, {"A.NS": 10})
    db = sqlite3.connect(str(pj.OUT_PATH.parent / "research.db"))
    db.execute(
        "INSERT INTO entities (name, entity_type, ticker) VALUES ('Alpha II Ltd', 'company', 'A.NS')"
    )
    db.commit()
    db.close()
    c = pj.entity_week_census()
    assert c["tickered_entities"] == 4
    assert c["joinable"] == 2  # both Alpha rows join; Beta/Ghost stay yahoo-absent


def test_missing_parquet_raises_filenotfound(census_env):
    with pytest.raises(FileNotFoundError, match="run price_sync.py build first"):
        pj.entity_week_census()


def test_survivorship_caveat_is_carried():
    """The caveat is a module constant — CLI output and any study header
    that imports it can never silently drop the bias disclosure."""
    assert "Survivorship bias" in pj.SURVIVORSHIP_CAVEAT
    assert "delisted" in pj.SURVIVORSHIP_CAVEAT


def test_cli_census_happy_path(census_env, capsys):
    _write_px(census_env, {"A.NS": 260, "B.BO": 40})
    assert pj._cli(["census"]) == 0
    out = capsys.readouterr().out
    assert "price-joinable 2 | yahoo-absent 1" in out
    assert "Survivorship bias" in out


def test_cli_census_missing_store_exit_1(census_env, capsys):
    assert pj._cli(["census"]) == 1
    assert "run price_sync.py build first" in capsys.readouterr().err
