#!/usr/bin/env python3
"""Long-horizon event studies over dated graph events vs weekly bars
(price_lane_alpha_research S3 — report-only, no served metric, no gate).

Event source = the S0 as-of substrate: an event is a dated row
(``valid_from``), and the study NEVER sees a bar before its event date
as anything but the baseline. Post-dated events without forward bars
are CENSORED (counted, not studied) — the lookahead guard is structural.

Subjects and sides per class (data, not code — new classes plug into
``EVENT_SOURCES``):

- ``acquired``: subject = the ACQUIRER (source side; deal dates are
  month-granular prose extractions — caveat, not precision dates).
- ``invested_in``: subject = the COMPANY (target side; ``valid_from`` =
  SHP filing period — a real periodic disclosure event).

Outcome per horizon h ∈ {4, 13, 26} weeks: forward return of the
subject vs the MEDIAN forward return of its canonical-sector peers
(hyper_edges ``sector`` membership, subject excluded) — abnormal return;
plus a volume ratio (post-window mean volume vs the 8-week pre-window
mean, z-scored against the pre-window std).

Survivorship caveat (S2's, inherited): subjects without Yahoo history
— disproportionately DELISTED acquired targets — are censored, so the
acquirer-side read is the honest one.

Usage:
    python3 helpers/research/event_study.py run --horizons 4,13,26
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.maintenance.price_sync import OUT_PATH  # noqa: E402

#: (subject side, description) per event class — the subject's ENTITY
#: name; the ticker comes from ``entities``.
EVENT_SOURCES: dict[str, str] = {
    "acquired": "source",  # acquirer
    "invested_in": "target",  # the company whose SHP filing moved
}
HORIZONS_DEFAULT = (4, 13, 26)
PRE_VOLUME_WEEKS = 8
MIN_FORWARD_WEEKS = 4  # an event needs at least the shortest horizon forward


def load_events(con, edge_type: str) -> list[tuple[str, str]]:
    """Dated (subject_entity, event_date) pairs for one class.

    Raises ValueError for a class with no source mapping — an unknown
    class is a caller bug, never a silent empty study.
    """
    if edge_type not in EVENT_SOURCES:
        raise ValueError(f"unknown event class {edge_type!r}; known: {sorted(EVENT_SOURCES)}")
    side = EVENT_SOURCES[edge_type]
    rows = con.execute(
        f"SELECT {side}, valid_from FROM graph_edges "  # noqa: S608  side is a module constant
        "WHERE edge_type = ? AND valid_from IS NOT NULL",
        (edge_type,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def load_prices(parquet: Path = OUT_PATH) -> dict[str, list[tuple[str, float, float]]]:
    """``symbol -> [(week, close, volume)]`` sorted ascending, from the parquet."""
    import duckdb

    duck = duckdb.connect()
    try:
        duck.execute(f"CREATE VIEW px AS SELECT * FROM read_parquet('{parquet}')")  # noqa: S608  constant path
        out: dict[str, list[tuple[str, float, float]]] = {}
        for sym, d, c, v in duck.execute(
            "SELECT symbol, date, close, volume FROM px ORDER BY symbol, date"
        ).fetchall():
            out.setdefault(sym, []).append((d, float(c), float(v)))
        return out
    finally:
        duck.close()


def entity_tickers(con) -> dict[str, str]:
    """``entity name -> ticker`` (entities with a Yahoo-suffixed ticker)."""
    return {
        r["name"]: r["ticker"]
        for r in con.execute(
            "SELECT name, ticker FROM entities WHERE ticker IS NOT NULL AND ticker != ''"
        ).fetchall()
        if "." in r["ticker"]
    }


def sector_peers(con) -> dict[str, set[str]]:
    """Canonical sector membership: ``company -> set(peer company names)``."""
    peers: dict[str, set[str]] = {}
    for r in con.execute(
        "SELECT hi.entity_name, he.label FROM hyper_incidences hi "
        "JOIN hyper_edges he ON he.id = hi.edge_id WHERE he.edge_type = 'sector'"
    ):
        peers.setdefault(r["entity_name"], set()).add(f"sector:{r['label']}")
    # invert label -> members so each subject's peers are its sector-mates
    by_label: dict[str, set[str]] = {}
    for name, labels in peers.items():
        for lbl in labels:
            by_label.setdefault(lbl, set()).add(name)
    out: dict[str, set[str]] = {}
    for label, members in by_label.items():
        for name in members:
            out[name] = members - {name}
    return out


def _forward_return(bars, idx: int, h: int) -> float | None:
    if idx + h >= len(bars):
        return None
    base = bars[idx][1]
    if base <= 0:
        return None
    return bars[idx + h][1] / base - 1.0


def _volume_ratio(bars, idx: int, h: int) -> float | None:
    pre = [v for _d, _c, v in bars[max(0, idx - PRE_VOLUME_WEEKS) : idx]]
    post = [v for _d, _c, v in bars[idx + 1 : idx + 1 + h]]
    if not pre or not post:
        return None
    mean_pre = statistics.fmean(pre)
    if mean_pre <= 0:
        return None
    return statistics.fmean(post) / mean_pre - 1.0


def study(  # noqa: C901 — the event kernel: anchor/censor/peer-baseline per horizon, linear in events
    events: list[tuple[str, str]],
    tickers: dict[str, str],
    prices: dict[str, list[tuple[str, float, float]]],
    peers: dict[str, set[str]],
    horizons: tuple[int, ...] = HORIZONS_DEFAULT,
) -> dict:
    """One event class → per-horizon outcome rows.

    Lookahead guard: the baseline bar is the LAST bar with date strictly
    ON or BEFORE the event date; every measured close is after it. An
    event without MIN_FORWARD_WEEKS of forward bars is censored.
    """
    per_h: dict[int, dict] = {
        h: {"abnormal": [], "pos": 0, "vol": [], "censored": 0, "no_data": 0} for h in horizons
    }
    for subject, ev_date in events:
        ticker = tickers.get(subject)
        bars = prices.get(ticker) if ticker else None
        if not bars:
            per_h[horizons[0]]["no_data"] += 1
            continue
        idx = None
        for i, (d, _c, _v) in enumerate(bars):
            if d <= ev_date:
                idx = i
            else:
                break
        if idx is None or len(bars) - 1 - idx < min(horizons):
            for h in horizons:
                per_h[h]["censored"] += 1
            continue
        peer_names = peers.get(subject, set())
        peer_returns: dict[int, list[float]] = {h: [] for h in horizons}
        for p in peer_names:
            pbars = prices.get(tickers.get(p, ""), [])
            if not pbars:
                continue
            pidx = None
            for i, (d, _c, _v) in enumerate(pbars):
                if d <= ev_date:
                    pidx = i
                else:
                    break
            if pidx is None:
                continue
            for h in horizons:
                r = _forward_return(pbars, pidx, h)
                if r is not None:
                    peer_returns[h].append(r)
        for h in horizons:
            subj = _forward_return(bars, idx, h)
            if subj is None or not peer_returns[h]:
                per_h[h]["censored"] += 1
                continue
            abnormal = subj - statistics.median(peer_returns[h])
            per_h[h]["abnormal"].append(abnormal)
            if abnormal > 0:
                per_h[h]["pos"] += 1
            vr = _volume_ratio(bars, idx, h)
            if vr is not None:
                per_h[h]["vol"].append(vr)
    rows = []
    for h in horizons:
        d = per_h[h]
        n = len(d["abnormal"])
        rows.append(
            {
                "horizon_weeks": h,
                "n": n,
                "median_abnormal": round(statistics.median(d["abnormal"]), 4) if n else None,
                "pct_positive": round(d["pos"] / n * 100, 1) if n else None,
                "median_volume_ratio": round(statistics.median(d["vol"]), 3) if d["vol"] else None,
                "censored": d["censored"],
                "no_price_data": d["no_data"],
            }
        )
    return {"n_events": len(events), "rows": rows}


def run_class(con, edge_type: str, horizons: tuple[int, ...]) -> dict:
    events = load_events(con, edge_type)
    return study(
        events,
        entity_tickers(con),
        load_prices(),
        sector_peers(con),
        horizons=horizons,
    )


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["run"], help="run the study over known event classes")
    p.add_argument(
        "--horizons",
        default=",".join(str(h) for h in HORIZONS_DEFAULT),
        help="comma list of forward horizons in weeks",
    )
    args = p.parse_args(argv)
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    con = connect(read_only=True)
    try:
        for etype in sorted(EVENT_SOURCES):
            res = run_class(con, etype, horizons)
            print(
                f"\n{etype} (subject = {EVENT_SOURCES[etype]} side; {res['n_events']} dated events)"
            )
            print(
                f"{'h(wk)':>6} {'N':>5} {'med_abn':>9} {'%pos':>6} {'med_volΔ':>9} "
                f"{'censored':>9} {'no_px':>6}"
            )
            for r in res["rows"]:
                ma = f"{r['median_abnormal']:+.4f}" if r["median_abnormal"] is not None else "—"
                mv = (
                    f"{r['median_volume_ratio']:+.3f}"
                    if r["median_volume_ratio"] is not None
                    else "—"
                )
                pp = f"{r['pct_positive']:.0f}%" if r["pct_positive"] is not None else "—"
                print(
                    f"{r['horizon_weeks']:>6} {r['n']:>5} {ma:>9} {pp:>6} {mv:>9} "
                    f"{r['censored']:>9} {r['no_price_data']:>6}"
                )
        print(
            "\nSURVIVORSHIP CAVEAT: subjects without Yahoo history (delisted/"
            "renamed, disproportionately acquired targets) are censored — "
            "the acquirer-side read is the honest one."
        )
        print("Report-only (price_lane_alpha_research S3) — no served metric, no gate.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
