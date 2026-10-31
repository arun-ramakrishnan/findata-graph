#!/usr/bin/env python3
"""Point-in-time (as-of) edge visibility over ``graph_edges``.

price_lane_alpha_research S0: an edge is visible at date D iff its
validity window covers D — ``(valid_from IS NULL OR valid_from <= D)``
AND ``(valid_to IS NULL OR valid_to > D)`` (the ``valid_to`` exclusive
rule matches the graph_analytics_integrity D1 semantics). Rows with
NULL bounds are always visible. This is the substrate every
event-study/backtest lane builds on: scoring a candidate list "as of"
date D must never see an edge dated after D (lookahead leakage).

Deliberately narrow (S0 scope): raw ``graph_edges`` only — the
sector-pair projection, DuckDB stamps and metric recomputation are
NOT here. Tolerances mirror :mod:`helpers.graph.edge_projection`
(fixtures declare only ``(source, target)``; a missing validity
column reads as NULL = always-visible).

Usage:
    python3 helpers/graph/as_of.py --date 2026-06-30 --summary
    python3 helpers/graph/as_of.py --date 2026-06-30 --edge-type acquired
    python3 helpers/graph/as_of.py --date 2026-06-30 --differential
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402  (house connect; read-only here)

DEFAULT_DB_PATH = _REPO_ROOT / "memory" / "research.db"

_VISIBILITY_SQL = "(valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to > ?)"


def _columns(con: sqlite3.Connection, table: str = "graph_edges") -> set[str]:
    """Column names, tolerating narrow fixture schemas."""
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def edges_as_of(
    con: sqlite3.Connection,
    as_of: str,
    *,
    edge_type: str | None = None,
) -> list[dict]:
    """Edges visible at ``as_of`` (ISO date), optionally one edge type.

    Returns dicts with source/target/edge_type/weight/valid_from/
    valid_to/source_ref. A missing weight column reads as 1.0, a
    missing validity column as NULL (always-visible), matching the
    edge_projection tolerances.
    """
    cols = _columns(con)
    # Missing columns degrade per the edge_projection tolerances: weight
    # -> 1.0, edge_type -> '', validity/source_ref -> NULL.
    select_parts = [
        "source",
        "target",
        "edge_type" if "edge_type" in cols else "'' AS edge_type",
        "COALESCE(weight, 1.0)" if "weight" in cols else "1.0 AS weight",
        "valid_from" if "valid_from" in cols else "NULL AS valid_from",
        "valid_to" if "valid_to" in cols else "NULL AS valid_to",
        "source_ref" if "source_ref" in cols else "NULL AS source_ref",
    ]
    where = _VISIBILITY_SQL
    params: list = [as_of, as_of]
    if "valid_from" not in cols or "valid_to" not in cols:
        # no validity columns: every row is always-visible; the WHERE
        # would reference columns that do not exist
        where = "1=1"
        params = []
    if edge_type is not None:
        if "edge_type" not in cols:
            return []  # filtering by type on a typeless schema: nothing matches
        where += " AND edge_type = ?"
        params.append(edge_type)
    rows = con.execute(
        f"SELECT {', '.join(select_parts)} FROM graph_edges WHERE {where}",  # noqa: S608  # ?-placeholders only
        params,
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "source": r[0],
                "target": r[1],
                "edge_type": r[2],
                "weight": float(r[3]),
                "valid_from": r[4],
                "valid_to": r[5],
                "source_ref": r[6],
            }
        )
    return out


def as_of_summary(con: sqlite3.Connection, as_of: str) -> dict[str, int]:
    """Per-edge-type visible counts at ``as_of``, sorted by count desc."""
    counts: dict[str, int] = {}
    for row in edges_as_of(con, as_of):
        counts[row["edge_type"]] = counts.get(row["edge_type"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def differential(con: sqlite3.Connection, as_of: str) -> dict:
    """Live vs as-of census: what expires, what is not yet born.

    Returns live/total counts, the as-of total, and the two drift
    classes — ``expired_at`` (live rows whose window closed before
    ``as_of``) and ``not_yet_born`` (rows whose ``valid_from`` is after
    ``as_of``). Both must equal the live-vs-asof difference together.
    """
    live_total = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    cols = _columns(con)
    has_bounds = "valid_from" in cols and "valid_to" in cols
    asof = edges_as_of(con, as_of)
    asof_total = len(asof)
    expired = not_yet = 0
    if has_bounds:
        expired = con.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE "
            "(valid_from IS NOT NULL AND valid_from <= ?) "
            "AND (valid_to IS NOT NULL AND valid_to <= ?)",
            (as_of, as_of),
        ).fetchone()[0]
        not_yet = con.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE valid_from IS NOT NULL AND valid_from > ?",
            (as_of,),
        ).fetchone()[0]
    return {
        "as_of": as_of,
        "live_total": live_total,
        "asof_total": asof_total,
        "expired_at": expired,
        "not_yet_born": not_yet,
    }


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", required=True, help="ISO date for the as-of snapshot")
    p.add_argument("--edge-type", default=None, help="restrict to one edge type")
    p.add_argument(
        "--summary",
        action="store_true",
        help="per-type visible counts (the default output; explicit flag for readability)",
    )
    p.add_argument("--differential", action="store_true", help="live vs as-of drift census")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = p.parse_args(argv)
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        p.error(f"--date must be ISO YYYY-MM-DD, got {args.date!r}")
    con = connect(args.db, read_only=True)
    try:
        if args.differential:
            d = differential(con, args.date)
            print(
                f"as-of {d['as_of']}: live {d['live_total']} edges | visible "
                f"{d['asof_total']} | expired_at {d['expired_at']} | "
                f"not_yet_born {d['not_yet_born']}"
            )
            return 0
        summary = as_of_summary(con, args.date)
        if args.edge_type is not None:
            print(f"{args.edge_type}: {summary.get(args.edge_type, 0)} visible at {args.date}")
            return 0
        total = sum(summary.values())
        print(f"as-of {args.date}: {total} edges visible")
        for etype, n in summary.items():
            print(f"  {etype:28} {n:6}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
