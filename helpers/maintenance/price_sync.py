#!/usr/bin/env python3
"""Weekly OHLCV backfill for the listed universe (price_lane_alpha_research S1).

One-shot bulk: WEEKLY bars, 5-year window, via the Yahoo lane (unauthed,
`yfinance`), over the 6,081 entities that already carry Yahoo-suffixed
tickers (`SBIN.NS`, `.BO` for BSE — written by the enrichment lane).
Output `memory/data/price_history_weekly.parquet` (zstd, git-managed
house sidecar, ~1.6M rows).

Deliberately NOT a recurring lane: no daily feed, no charting, no
scheduled sync (operator ruling 2026-10-31 — weekly signals for
long-horizon analysis). `--since` tops up a study's window on demand.

Usage:
    python3 helpers/maintenance/price_sync.py build              # 5y backfill
    python3 helpers/maintenance/price_sync.py build --since 2026-10-01
    python3 helpers/maintenance/price_sync.py coverage           # row census
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402

OUT_PATH = _REPO_ROOT / "memory" / "data" / "price_history_weekly.parquet"
BATCH = 200
PERIOD = "5y"


def load_tickers() -> list[str]:
    """Yahoo-suffixed tickers from entities, sorted for stable batches."""
    con = connect(read_only=True)
    try:
        rows = con.execute(
            "SELECT ticker FROM entities WHERE ticker IS NOT NULL AND ticker != '' ORDER BY ticker"
        ).fetchall()
    finally:
        con.close()
    return sorted({r[0] for r in rows})


def _download_batch(tickers: list[str], since: str | None) -> Any:  # noqa: ANN401  pandas DF, kept opaque
    import warnings

    import yfinance as yf

    # yfinance 1.7 emits Pandas4Warning (inplace deprecations) from its own
    # internals — upstream noise, suppressed at the seam like HGX's
    # SyntaxWarnings in hyper_communities.
    warnings.filterwarnings("ignore", category=Warning, module="yfinance")

    kwargs: dict = {
        "interval": "1wk",
        "group_by": "ticker",
        "threads": True,
        "auto_adjust": True,  # single adjusted OHLC — the research-grade series
        "progress": False,
    }
    if since:
        kwargs["start"] = since
    else:
        kwargs["period"] = PERIOD
    return yf.download(tickers=tickers, **kwargs)


def build(  # noqa: C901 — batch/retry/merge state machine reads best linear
    since: str | None = None, *, fill_missing: bool = False
) -> int:
    """Bulk backfill; writes the parquet atomically (tmp + os.replace).

    ``fill_missing`` diffs the stored parquet against the ticker universe
    and fetches ONLY absent symbols, merging into what exists — the
    recovery path after a rate-limited run (Yahoo 429s arrive in bursts;
    the failed tail is re-fetched after a cooldown instead of the whole
    universe).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    tickers = load_tickers()
    old_rows: list[dict] = []
    if fill_missing and OUT_PATH.exists():
        import duckdb

        duck = duckdb.connect()
        try:
            duck.execute(
                f"CREATE VIEW px AS SELECT * FROM read_parquet('{OUT_PATH}')"  # noqa: S608  constant path
            )
            have = {r[0] for r in duck.execute("SELECT DISTINCT symbol FROM px").fetchall()}
            cols = [d[0] for d in duck.execute("DESCRIBE px").fetchall()]
            old_rows = [
                dict(zip(cols, r, strict=True)) for r in duck.execute("SELECT * FROM px").fetchall()
            ]
        finally:
            duck.close()
        tickers = [t for t in tickers if t not in have]
        print(f"[price-sync] fill-missing: {len(have)} present, fetching {len(tickers)} absent")
    if not tickers:
        print("[price-sync] nothing to fetch — store already complete")
        return 0
    print(
        f"[price-sync] {len(tickers)} tickered entities; window={'since ' + since if since else PERIOD}"
    )
    rows: list[dict] = old_rows
    failed: list[str] = []
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i : i + BATCH]
        df = None
        for attempt in (1, 2):
            try:
                df = _download_batch(chunk, since)
                break
            except Exception as exc:  # noqa: BLE001  per-batch retry, never fatal
                print(
                    f"[price-sync] batch {i // BATCH} attempt {attempt} failed: {exc}",
                    file=sys.stderr,
                )
                if "Rate limited" in str(exc) or "429" in str(exc):
                    time.sleep(90)  # Yahoo 429s clear on a minutes-scale window
                else:
                    time.sleep(5 + attempt * 5)
        if df is None or df.empty:
            failed.extend(chunk)
            continue
        # group_by="ticker" → MultiIndex columns (ticker, field); a
        # single-ticker batch may collapse to flat columns
        items = df.columns.unique(level=0) if hasattr(df.columns, "levels") else [df.columns]
        for sym in items:
            if sym not in chunk:
                continue
            sub = df[sym].dropna(subset=["Close"])
            for date_idx, r in sub.iterrows():
                rows.append(
                    {
                        "symbol": sym,
                        "date": str(date_idx.date()),
                        "open": float(r["Open"]) if r.get("Open") == r.get("Open") else None,
                        "high": float(r["High"]) if r.get("High") == r.get("High") else None,
                        "low": float(r["Low"]) if r.get("Low") == r.get("Low") else None,
                        "close": float(r["Close"]),
                        "volume": int(r["Volume"]) if r.get("Volume") == r.get("Volume") else 0,
                    }
                )
        done = min(i + BATCH, len(tickers))
        print(f"[price-sync] {done}/{len(tickers)} tickers ({len(rows)} rows so far)", flush=True)
        time.sleep(1 + random.random() * 2)  # noqa: S311  jitter, not crypto
    if failed:
        print(
            f"[price-sync] {len(failed)} ticker(s) failed after retry: {failed[:10]}...",
            file=sys.stderr,
        )
    table = pa.Table.from_pylist(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, OUT_PATH)
    print(f"[price-sync] wrote {len(rows)} rows -> {OUT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def coverage() -> int:
    """Row census over the stored parquet: totals, depth quantiles, failures."""
    import duckdb

    if not OUT_PATH.exists():
        print(f"no parquet at {OUT_PATH.relative_to(_REPO_ROOT)} — run build first")
        return 1
    duck = duckdb.connect()
    try:
        duck.execute(
            f"CREATE VIEW px AS SELECT * FROM read_parquet('{OUT_PATH}')"  # noqa: S608  constant path
        )
        row = duck.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM px").fetchone()
        if row is None:  # aggregate always returns one row; unreachable on a real store
            raise ValueError("price parquet aggregates returned no row")
        n_rows, n_sym = row
        print(f"rows {n_rows} | symbols with data {n_sym}")
        q = duck.execute(
            "SELECT min(w), quantile_cont(w, 0.25), median(w), "
            "quantile_cont(w, 0.75), max(w) FROM "
            "(SELECT symbol, COUNT(*) AS w FROM px GROUP BY symbol)"
        ).fetchone()
        if q is None:
            raise ValueError("quantile aggregate returned no row")
        print(
            f"weeks/symbol: min {q[0]} | p25 {q[1]:.0f} | median {q[2]:.0f} | p75 {q[3]:.0f} | max {q[4]}"
        )
        thin_row = duck.execute(
            "SELECT COUNT(*) FROM (SELECT symbol, COUNT(*) AS w FROM px GROUP BY symbol HAVING w < 52)"
        ).fetchone()
        if thin_row is None:
            raise ValueError("thin-count aggregate returned no row")
        thin = thin_row[0]
        print(f"symbols with <1y of weeks (thin/new/delisted): {thin}")
        date_row = duck.execute("SELECT min(date), max(date) FROM px").fetchone()
        if date_row is None:
            raise ValueError("date-range aggregate returned no row")
        print(f"date range: {date_row[0]} .. {date_row[1]}")
    finally:
        duck.close()
    return 0


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="bulk weekly backfill")
    b.add_argument("--since", default=None, help="incremental top-up start date (ISO)")
    b.add_argument(
        "--fill-missing",
        action="store_true",
        help="fetch only symbols absent from the stored parquet, merge in place",
    )
    sub.add_parser("coverage", help="row census over the stored parquet")
    args = p.parse_args(argv)
    if args.cmd == "build":
        return build(args.since, fill_missing=args.fill_missing)
    return coverage()


if __name__ == "__main__":
    raise SystemExit(_cli())
