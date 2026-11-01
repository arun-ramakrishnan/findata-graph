#!/usr/bin/env python3
"""Integration: the temporal lane end-to-end (test_coverage_gap_sweep S3).

price_sync (faked download) → weekly parquet → price_join census →
as-of event selection → event_study. The chain that turns dated edges
plus OHLCV bars into an event study, on synthetic data only — the
parquet, the entities DB and the edge store all live in tmp_path; the
network and the live research.db are never touched.

The hand-checked number at the end: subject +10% at 4w, peers +5%/+20%
→ sector median +12.5% → abnormal −2.5% (same arithmetic the S3 unit
test pins, now flowing through the parquet and the as-of substrate).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import ENTITIES_8COL  # noqa: E402

from helpers.maintenance import price_sync as ps  # noqa: E402
from helpers.research import price_join as pj  # noqa: E402
from helpers.research import event_study as es  # noqa: E402
from helpers.graph import as_of as ao  # noqa: E402

WEEKS = [
    "2026-01-05",
    "2026-01-12",
    "2026-01-19",
    "2026-01-26",
    "2026-02-02",
    "2026-02-09",
    "2026-02-16",
    "2026-02-23",
    "2026-03-02",
    "2026-03-09",
    "2026-03-16",
    "2026-03-23",
]
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
EVENT_DATE = "2026-02-02"  # bar index 4


def _schedule(step_level: float) -> list[float]:
    """Flat 100 for the first 5 bars, then ``step_level`` — every 4w
    forward window from the event spans the step exactly once."""
    return [100.0] * 5 + [step_level] * 7


def _download_df() -> pd.DataFrame:
    """MultiIndex (ticker, field) frame like yfinance group_by='ticker'."""
    symbols = ["SUB.NS", "P1.NS", "P2.NS", "GHOST.NS"]
    closes = {
        "SUB.NS": _schedule(110.0),
        "P1.NS": _schedule(105.0),
        "P2.NS": _schedule(120.0),
        "GHOST.NS": [100.0] * 12,
    }
    cols = pd.MultiIndex.from_product([symbols, FIELDS], names=["ticker", "field"])
    data = {}
    for sym in symbols:
        for f in FIELDS:
            if f == "Close":
                data[(sym, f)] = closes[sym]
            elif f == "Volume":
                data[(sym, f)] = [1000.0] * 12
            else:
                data[(sym, f)] = [c - 0.5 for c in closes[sym]]
    return pd.DataFrame(
        data, index=pd.DatetimeIndex(pd.to_datetime(WEEKS), name="Date"), columns=cols
    )


@pytest.fixture()
def chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect every seam: the parquet, the entities DB and the sleeps."""
    px = tmp_path / "px.parquet"
    db = tmp_path / "research.db"
    con = sqlite3.connect(str(db))
    con.execute(ENTITIES_8COL)
    con.executemany(
        "INSERT INTO entities (name, entity_type, ticker) VALUES (?, 'company', ?)",
        [
            ("Sub Ltd", "SUB.NS"),
            ("P1 Ltd", "P1.NS"),
            ("P2 Ltd", "P2.NS"),
            ("Ghost Ltd", "GHOST.NS"),
        ],
    )
    con.commit()
    con.close()

    monkeypatch.setattr(ps, "OUT_PATH", px)
    monkeypatch.setattr(ps, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(ps, "load_tickers", lambda: ["SUB.NS", "P1.NS", "P2.NS", "GHOST.NS"])
    monkeypatch.setattr(ps, "_download_batch", lambda chunk, since: _download_df())
    monkeypatch.setattr(ps.time, "sleep", lambda _s: None)

    def fake_connect(*a, **k):
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row  # the real connect() default
        return c

    monkeypatch.setattr(pj, "OUT_PATH", px)
    monkeypatch.setattr(pj, "connect", fake_connect)
    return SimpleNamespace(parquet=px, db=db)


def test_temporal_chain_end_to_end(chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 1 — price_sync: bulk backfill writes the weekly parquet
    assert ps.build() == 0
    assert chain.parquet.exists()

    # 2 — price_join: the whole universe is joinable (survivorship view)
    census = pj.entity_week_census()
    assert census["tickered_entities"] == 4
    assert census["joinable"] == 4
    assert census["yahoo_absent"] == 0
    assert census["exchange_split"] == {"NSE": 4}
    assert census["full_depth_5y"] == 0  # 12 weeks each — thin by design
    assert census["thin_under_1y"] == 4

    # 3 — as-of: dated edges pick the events; future/expired edges never leak
    edge_db = tmp_path / "edges.db"
    econ = sqlite3.connect(str(edge_db))
    econ.execute(
        "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, "
        "weight REAL, valid_from DATE, valid_to DATE, source_ref TEXT)"
    )
    econ.executemany(
        "INSERT INTO graph_edges VALUES (?,?,?,?,?,?,?)",
        [
            # the event: Sub Ltd acquired by Parent Ltd, dated from the filing
            ("Sub Ltd", "Parent Ltd", "acquired", 1.0, EVENT_DATE, None, "rpt:1"),
            # born after the probe — must NOT become an event (lookahead)
            ("Sub Ltd", "Future Ltd", "acquired", 1.0, "2026-09-01", None, "rpt:2"),
            # expired before the probe — must NOT become an event
            ("Sub Ltd", "Old Ltd", "acquired", 1.0, "2025-01-01", "2026-01-01", "rpt:3"),
            # the peer set, as dyads
            ("Sub Ltd", "P1 Ltd", "peer_of", 1.0, None, None, "n1"),
            ("Sub Ltd", "P2 Ltd", "peer_of", 1.0, None, None, "n2"),
        ],
    )
    econ.commit()

    events = [
        (r["source"], r["valid_from"])
        for r in ao.edges_as_of(econ, EVENT_DATE, edge_type="acquired")
    ]
    assert events == [("Sub Ltd", EVENT_DATE)]
    # negative control: probing before the event sees no acquisitions
    assert ao.edges_as_of(econ, "2026-01-01", edge_type="acquired") == []

    # peers from the same store, tickers from the entities DB
    peers: dict[str, set[str]] = {}
    for src, tgt in econ.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type = 'peer_of'"
    ):
        peers.setdefault(src, set()).add(tgt)
    tcon = sqlite3.connect(str(chain.db))
    tickers = {
        n: t for n, t in tcon.execute("SELECT name, ticker FROM entities WHERE ticker IS NOT NULL")
    }
    tcon.close()

    # 4 — prices from the PARQUET the sync wrote (the real flow), not hand dicts
    import duckdb

    duck = duckdb.connect()
    try:
        rows = duck.execute(
            f"SELECT symbol, date, close, volume FROM read_parquet('{chain.parquet}') "  # noqa: S608
            "ORDER BY symbol, date"
        ).fetchall()
    finally:
        duck.close()
    prices: dict[str, list[tuple[str, float, float]]] = {}
    for sym, date, close, vol in rows:
        prices.setdefault(sym, []).append((date, float(close), float(vol)))
    assert set(prices) == {"SUB.NS", "P1.NS", "P2.NS", "GHOST.NS"}
    assert all(len(v) == 12 for v in prices.values())

    # 5 — event study over the chain output
    res = es.study(events, tickers, prices, peers, horizons=(4,))
    r = res["rows"][0]
    assert r["n"] == 1
    assert r["censored"] == 0
    assert r["no_price_data"] == 0
    # subject 100→110 (+10%), peers +5% and +20% → median +12.5% → −2.5%
    assert r["median_abnormal"] == pytest.approx(-0.025)


def test_chain_refuses_events_before_first_bar(chain, tmp_path: Path):
    """An event dated before the parquet's first bar has no baseline and
    no forward bars from THIS store — the study must report it censored,
    never silently interpolate."""
    assert ps.build() == 0
    edge_db = tmp_path / "edges.db"
    econ = sqlite3.connect(str(edge_db))
    econ.execute(
        "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, "
        "weight REAL, valid_from DATE, valid_to DATE, source_ref TEXT)"
    )
    econ.execute(
        "INSERT INTO graph_edges VALUES ('Sub Ltd','Parent Ltd','acquired',1.0,'2025-06-01',NULL,'rpt:0')"
    )
    econ.commit()
    events = [
        (r["source"], r["valid_from"])
        for r in ao.edges_as_of(econ, "2025-12-31", edge_type="acquired")
    ]
    assert events == [("Sub Ltd", "2025-06-01")]
    prices = {"SUB.NS": [(d, 100.0, 1000.0) for d in WEEKS]}
    res = es.study(events, {"Sub Ltd": "SUB.NS"}, prices, {}, horizons=(4,))
    r = res["rows"][0]
    assert r["n"] == 0
    assert r["censored"] == 1
