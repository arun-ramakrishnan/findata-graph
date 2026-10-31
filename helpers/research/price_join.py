#!/usr/bin/env python3
"""Entity-week price join + coverage census (price_lane_alpha_research S2).

The read-side recipe every long-horizon study starts from: the weekly
parquet (`memory/data/price_history_weekly.parquet`) joined to
``entities`` on the Yahoo ticker (``SBIN.NS``/``.BO`` — written by the
enrichment lane, the stable join; the ISIN is the identifier of record
but Yahoo serves symbols).

SURVIVORSHIP CAVEAT — carried on every census and study header: 594
tickered entities have NO Yahoo history (repeat-verified across four
build passes; predominantly old numeric `.BO` scrip codes — delisted or
renamed companies). Every join is survivor-biased toward names still
listed in 2026. Delisted-data sourcing is a separate lane; nothing here
fixes it.

Usage:
    python3 helpers/research/price_join.py census
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.maintenance.price_sync import OUT_PATH  # noqa: E402

SURVIVORSHIP_CAVEAT = (
    "Survivorship bias: entities without Yahoo history (delisted/renamed, "
    "predominantly legacy numeric BSE codes) are absent from every join — "
    "universe = names still listed as of the build date."
)


def entity_week_census() -> dict:
    """Entities ⋈ weekly bars: joinable universe, split, depth, span.

    Returns the census dict; prints nothing (callers format).
    """
    import duckdb

    if not OUT_PATH.exists():
        raise FileNotFoundError(f"no price parquet at {OUT_PATH} — run price_sync.py build first")
    con = connect(read_only=True)
    duck = duckdb.connect()
    try:
        duck.execute(
            f"CREATE VIEW px AS SELECT * FROM read_parquet('{OUT_PATH}')"  # noqa: S608  constant path
        )
        entities = [
            (r["ticker"], r["name"], r["entity_type"])
            for r in con.execute(
                "SELECT ticker, name, entity_type FROM entities "
                "WHERE ticker IS NOT NULL AND ticker != ''"
            ).fetchall()
        ]
        have = {r[0] for r in duck.execute("SELECT DISTINCT symbol FROM px").fetchall()}
        span_row = duck.execute("SELECT COUNT(*), min(date), max(date) FROM px").fetchone()
        if span_row is None:
            raise ValueError("span aggregate returned no row")
        n_rows, lo, hi = span_row
        joined = [(t, name, et) for t, name, et in entities if t in have]
        per_weeks = {
            t: w
            for t, w in duck.execute("SELECT symbol, COUNT(*) FROM px GROUP BY symbol").fetchall()
        }
        split: dict[str, int] = {}
        for t, _name, _et in joined:
            ex = "NSE" if t.endswith(".NS") else "BSE"
            split[ex] = split.get(ex, 0) + 1
        full_5y = sum(1 for w in per_weeks.values() if w >= 250)
        thin = sum(1 for w in per_weeks.values() if w < 52)
        return {
            "tickered_entities": len(entities),
            "joinable": len(joined),
            "yahoo_absent": len(entities) - len(joined),
            "exchange_split": split,
            "full_depth_5y": full_5y,
            "thin_under_1y": thin,
            "rows": int(n_rows),
            "span": (str(lo), str(hi)),
        }
    finally:
        duck.close()
        con.close()


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["census"], help="join coverage census")
    p.parse_args(argv)
    try:
        c = entity_week_census()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        f"entities {c['tickered_entities']} | price-joinable {c['joinable']} | "
        f"yahoo-absent {c['yahoo_absent']}"
    )
    print(f"  exchange split: {c['exchange_split']}")
    print(
        f"  depth: full-5y {c['full_depth_5y']} | thin(<1y) {c['thin_under_1y']} | "
        f"rows {c['rows']} | span {c['span'][0]}..{c['span'][1]}"
    )
    print(f"  {SURVIVORSHIP_CAVEAT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
