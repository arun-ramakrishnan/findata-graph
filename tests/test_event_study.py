#!/usr/bin/env python3
"""Tests for helpers/research/event_study.py (price_lane_alpha_research S3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.research import event_study as es  # noqa: E402

# 10 weekly bars, flat 100.0 closes except where a test moves them
BARS = [
    (f"2026-{m:02d}-{d:02d}", 100.0, 1000.0)
    for m, d in [
        (1, 5),
        (1, 12),
        (1, 19),
        (1, 26),
        (2, 2),
        (2, 9),
        (2, 16),
        (2, 23),
        (3, 2),
        (3, 9),
    ]
]


def _price_map(subject_close=100.0, peer_close=100.0, subject_vol=None):
    s = [(d, subject_close, 1000.0) for d, _c, _v in BARS]
    p = [(d, peer_close, 1000.0) for d, _c, _v in BARS]
    return {"SUB.NS": s, "PEER.NS": p}


def test_lookahead_baseline_is_last_bar_on_or_before_event():
    """The baseline bar is the last close <= the event date; a bar AFTER
    the event date never enters the baseline (the lookahead guard)."""
    step = lambda lvl: [(d, lvl if d > "2026-01-19" else 100.0, 1000.0) for d, _c, _v in BARS]  # noqa: E731
    prices = {"SUB.NS": step(150.0), "PEER.NS": step(100.0)}
    events = [("Sub Ltd", "2026-01-19")]  # event ON the 01-19 bar
    res = es.study(
        events,
        {"Sub Ltd": "SUB.NS", "Peer Ltd": "PEER.NS"},
        prices,
        {"Sub Ltd": {"Peer Ltd"}},
        horizons=(4,),
    )
    # baseline = 01-19 close (100.0); forward 4w = 02-16 close (150.0) → +50%;
    # peer flat → abnormal +50%
    assert res["rows"][0]["n"] == 1
    assert res["rows"][0]["median_abnormal"] == pytest.approx(0.5)


def test_post_dated_event_is_censored_not_studied():
    """An event after the last bar (no forward weeks) is censored — a
    post-dated edge can never leak into a window (acceptance 4)."""
    prices = {"SUB.NS": BARS}
    events = [("Sub Ltd", "2026-03-09"), ("Sub Ltd", "2026-12-01")]
    res = es.study(events, {"Sub Ltd": "SUB.NS"}, prices, {}, horizons=(4,))
    r = res["rows"][0]
    assert r["censored"] >= 1
    assert r["n"] == 0


def test_abnormal_is_subject_minus_sector_median():
    """Hand-checked: subject +10% at 4w, two peers at +5% and +20% →
    sector median +12.5% → abnormal −2.5%."""

    def series(level):
        # first 5 weeks at 100, then the level — every forward window
        # spans the step, so the 4w return is exactly level/100 − 1
        return [(d, level if i >= 4 else 100.0, 1000.0) for i, (d, _c, _v) in enumerate(BARS)]

    prices = {"SUB.NS": series(110.0), "P1.NS": series(105.0), "P2.NS": series(120.0)}
    tickers = {"Sub Ltd": "SUB.NS", "Peer1": "P1.NS", "Peer2": "P2.NS"}
    peers = {"Sub Ltd": {"Peer1", "Peer2"}}
    events = [("Sub Ltd", BARS[0][0])]
    res = es.study(events, tickers, prices, peers, horizons=(4,))
    assert res["rows"][0]["median_abnormal"] == pytest.approx(0.10 - 0.125)
    assert res["rows"][0]["pct_positive"] == 0.0


def test_unknown_event_class_raises():
    import sqlite3

    con = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        es.load_events(con, "not_a_class")


def test_no_price_data_counted_separately():
    res = es.study([("Ghost Ltd", "2026-01-05")], {}, {}, {}, horizons=(4,))
    assert res["rows"][0]["no_price_data"] == 1
    assert res["rows"][0]["n"] == 0
