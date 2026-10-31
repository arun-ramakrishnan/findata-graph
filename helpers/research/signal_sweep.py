#!/usr/bin/env python3
"""Graph-signal unlock sweep — as-of cross-sections vs forward returns
(price_lane_alpha_research follow-on; EXPLORATORY).

For each quarter-end T: compute per-company graph signals from the S0
as-of edge set, then Spearman rank-IC between signal and the forward
return (weekly bars, S1 sidecar) on TWO targets — RAW and sector-
ABNORMAL (subject minus sector-mate median). Two visibility modes
because undated edges (NULL bounds = always visible) are LOOKAHEAD for
backtesting: ``all`` (substrate as-is, optimistic) and ``dated`` (only
``valid_from <= T``, leak-free). A signal must be consistent in BOTH
modes, across dates, and across targets before it earns a
pre-registration (``linkpred_precision_prereg.md`` pattern).

Report-only — anything actionable gets pre-registered before any
confirmatory test. Survivorship caveat (S2's) applies throughout.

Usage:
    python3 helpers/research/signal_sweep.py sweep --horizons 4,13,26
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.research.event_study import load_prices, sector_peers  # noqa: E402

#: quarter-end probe dates (T); forward windows truncate at the last
#: date with a full 26-bar window.
QUARTER_ENDS = [
    "2022-03-31",
    "2022-06-30",
    "2022-09-30",
    "2022-12-31",
    "2023-03-31",
    "2023-06-30",
    "2023-09-30",
    "2023-12-31",
    "2024-03-31",
    "2024-06-30",
    "2024-09-30",
    "2024-12-31",
    "2025-03-31",
    "2025-06-30",
    "2025-09-30",
    "2025-12-31",
    "2026-03-31",
    "2026-06-30",
]


def _deg(rows):
    return float(len(rows))


def _typed(rows, etype):
    return float(sum(1 for et, _s, _t in rows if et == etype))


SIGNALS: dict[str, Callable[[list[tuple[str, str, str]]], float]] = {
    "degree": _deg,
    "suppliers": lambda rows: _typed(rows, "supplier_to"),
    "competes": lambda rows: _typed(rows, "competes_with"),
    "rpt_disclosure": lambda rows: _typed(rows, "same_group"),
    "holders": lambda rows: _typed(rows, "invested_in"),
    "subsidiaries": lambda rows: _typed(rows, "subsidiary_of"),
    "jv": lambda rows: _typed(rows, "jv_with"),
    "index_membership": lambda rows: _typed(rows, "listed_on_index"),
}


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank IC (Spearman) via average ranks + Pearson on ranks."""
    n = len(xs)
    if n < 8:
        return None

    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _bar_index(prices: dict) -> dict[str, dict[str, int]]:
    """symbol -> {date: row index} — O(1) anchor lookups (the naive
    linear scan was millions of bar walks per sweep once sector peers
    joined)."""
    out: dict[str, dict[str, int]] = {}
    for sym, bars in prices.items():
        out[sym] = {d: i for i, (d, _c, _v) in enumerate(bars)}
    return out


def _forward_return(
    prices: dict,
    idx_map: dict[str, dict[str, int]],
    ticker: str,
    t: str,
    h_bars: int,
) -> float | None:
    """Forward return from the last close <= T to the close h bars later.

    The anchor is the last bar ON or BEFORE T (lookahead guard); the
    horizon counts BARS, not calendar weeks (halted weeks shift both
    legs equally — the relative read is unaffected).
    """
    bars = prices.get(ticker)
    mi = idx_map.get(ticker)
    if not bars or mi is None:
        return None
    idx = mi.get(t)
    if idx is None:
        earlier = [d for d in mi if d <= t]
        if not earlier:
            return None
        idx = mi[max(earlier)]
    if idx + h_bars >= len(bars):
        return None
    base = bars[idx][1]
    if base <= 0:
        return None
    return bars[idx + h_bars][1] / base - 1.0


def _abnormal(
    prices: dict,
    idx_map: dict[str, dict[str, int]],
    ticker: str,
    t: str,
    h_bars: int,
    peer_tickers: list[str],
) -> float | None:
    """Subject forward return minus the sector-mate median (None if the
    subject or the peer set lacks the window)."""
    subj = _forward_return(prices, idx_map, ticker, t, h_bars)
    if subj is None:
        return None
    peer_r = [
        r
        for pt in peer_tickers
        if (r := _forward_return(prices, idx_map, pt, t, h_bars)) is not None
    ]
    if not peer_r:
        return None
    return subj - statistics.median(peer_r)


def sweep(  # noqa: C901 — per-date × per-mode × per-horizon cross-section; the nesting IS the design
    horizons: tuple[int, ...] = (4, 13, 26),
) -> dict:
    con = connect(read_only=True)
    prices = load_prices()
    idx_map = _bar_index(prices)
    tickers = {
        r["name"]: r["ticker"]
        for r in con.execute(
            "SELECT name, ticker FROM entities WHERE ticker IS NOT NULL AND ticker != ''"
        ).fetchall()
        if "." in r["ticker"]
    }
    peers_names = sector_peers(con)
    peers = {
        name: [tickers[p] for p in mates if p in tickers and p != name]
        for name, mates in peers_names.items()
    }
    try:
        keys = [(m, h) for m in ("all", "dated") for h in horizons]
        results: dict[tuple[str, int], dict] = {
            k: {name: {"ics_raw": [], "ics_abn": []} for name in SIGNALS} for k in keys
        }
        n_by_date: dict[tuple[str, str], int] = {}
        from helpers.graph import as_of as ao

        for t in QUARTER_ENDS:
            for mode in ("all", "dated"):
                edges = ao.edges_as_of(con, t)
                if mode == "dated":
                    edges = [
                        e for e in edges if e["valid_from"] is not None and e["valid_from"] <= t
                    ]
                touch: dict[str, list[tuple[str, str, str]]] = {}
                for e in edges:
                    touch.setdefault(e["source"], []).append(
                        (e["edge_type"], e["source"], e["target"])
                    )
                    touch.setdefault(e["target"], []).append(
                        (e["edge_type"], e["source"], e["target"])
                    )
                for h in horizons:
                    sig: dict[str, list[float]] = {name: [] for name in SIGNALS}
                    fwd: list[float] = []
                    abn: list[float] = []
                    for name, ticker in tickers.items():
                        subj = _forward_return(prices, idx_map, ticker, t, h)
                        if subj is None or name not in touch:
                            continue
                        a = _abnormal(prices, idx_map, ticker, t, h, peers.get(name, []))
                        if a is None:
                            continue  # abnormal target needs the peer window too
                        fwd.append(subj)
                        abn.append(a)
                        rows = touch[name]
                        for sname, fn in SIGNALS.items():
                            sig[sname].append(float(fn(rows)))
                    n_by_date[(t, mode)] = len(fwd)
                    for sname in SIGNALS:
                        ic = _spearman(sig[sname], fwd)
                        if ic is not None:
                            results[(mode, h)][sname]["ics_raw"].append((t, ic))
                        ica = _spearman(sig[sname], abn)
                        if ica is not None:
                            results[(mode, h)][sname]["ics_abn"].append((t, ica))

        summary: dict[tuple[str, int, str, str], dict] = {}
        for key, per_signal in results.items():
            mode, h = key
            for sname, d in per_signal.items():
                for kind, series in (("raw", d["ics_raw"]), ("abn", d["ics_abn"])):
                    ics = [v for _t, v in series]
                    if not ics:
                        continue
                    mean_ic = statistics.fmean(ics)
                    sd = statistics.stdev(ics) if len(ics) > 1 else 0.0
                    summary[(mode, h, sname, kind)] = {
                        "mean_ic": mean_ic,
                        "n_dates": len(ics),
                        "t_stat": mean_ic / (sd / len(ics) ** 0.5) if sd else 0.0,
                        "positive_dates": sum(1 for v in ics if v > 0),
                    }
        return {"summary": summary, "n_by_date": n_by_date}
    finally:
        con.close()


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["sweep"], help="run the cross-sectional sweep")
    p.add_argument(
        "--horizons",
        default="4,13,26",
        help="comma list of forward horizons in bars (weeks)",
    )
    args = p.parse_args(argv)
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    out = sweep(horizons=horizons)
    print(
        f"graph-signal sweep: {len(QUARTER_ENDS)} quarter-ends, horizons "
        f"{list(horizons)} bars, Spearman rank-IC on RAW and sector-ABNORMAL "
        "forward returns (exploratory; undated-edge lookahead in 'all', "
        "leak-free in 'dated'; survivorship applies)"
    )
    n_max = max(out["n_by_date"].values(), default=0)
    print(
        f"universe per date: up to ~{n_max} companies (late dates censor the "
        "26-bar window — structural, counted per date)"
    )
    print(
        f"{'signal':<18} {'mode':<7} {'h':>3} {'target':<5} "
        f"{'meanIC':>8} {'t':>6} {'pos/dates':>10}"
    )
    rows = sorted(
        out["summary"].items(),
        key=lambda kv: (-abs(kv[1]["mean_ic"]), kv[0][0], kv[0][1], kv[0][3]),
    )
    for (mode, h, sname, kind), s in rows:
        print(
            f"{sname:<18} {mode:<7} {h:>3} {kind:<5} {s['mean_ic']:>+8.4f} "
            f"{s['t_stat']:>6.2f} {s['positive_dates']:>4}/{s['n_dates']:<5}"
        )
    print(
        "\nEXPLORATORY: no multiple-testing correction — a signal must be "
        "consistent in BOTH modes, across dates, and across targets before "
        "it earns a pre-registration (linkpred_precision_prereg.md pattern)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
