#!/usr/bin/env python3
"""Graph scale watch — the T1 tripwire the vault ladder promised.

doc/local/perf/graph_scaling.md (§T1 watch) names "the advisory tripwire on
e_all_und rows" as the watcher that fires Phase B (offline CSR substrate +
mmap query path + Mojo BFS lane). Until 2026-10-18 it was doctrine without
code: nothing read the count, so T1 could arrive silently. This advisory leg
is that watcher.

Thresholds (rows of the doubled undirected projection ``e_all_und``):

- < 500k            OK   — count + distance ratio to T1
- >= 500k (WARN)         — half-way to T1; start recording the growth driver
- >= 1M   (FIRE)         — exit 1: T1 reached, Phase B planning activates
- DB missing / locked    — SKIP line, exit 0 (a concurrent graph rebuild must
  not red the advisory gate; live-invariants owns rebuilds)

Read-only DuckDB open; two COUNT queries, sub-ms at any planned scale.

Usage:
    python3 helpers/validators/graph_scale_watch.py [--check] [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

#: vault_scaling ladder T1 (graph_scaling.md §T1 watch): full BFS >100 ms
#: class, Phase B activation line.
T1_ROWS = 1_000_000

#: 50% of T1 — start recording the growth driver (to date: event density,
#: not note volume — graph_scaling.md §T1 watch).
WARN_ROWS = 500_000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--check", action="store_true", help="advisory gate leg mode")
    ap.add_argument("--db", default="memory/graph.duckdb", help="graph cache path")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"graph-scale-watch: SKIP — {db} not found")
        return 0
    try:
        con = duckdb.connect(str(db), read_only=True)
        try:
            row = con.execute("SELECT COUNT(*) FROM e_all_und").fetchone()
            rows = int(row[0]) if row is not None else 0
            row = con.execute("SELECT COUNT(*) FROM e_dir").fetchone()
            directed = int(row[0]) if row is not None else 0
        finally:
            con.close()
    except duckdb.CatalogException:
        print(f"graph-scale-watch: SKIP — e_all_und not built yet in {db}")
        return 0
    except duckdb.Error as exc:
        # A concurrent rebuild holds the write lock; live-invariants owns
        # rebuilds — the watcher must not red the gate on a transient lock.
        print(f"graph-scale-watch: SKIP — {db} unreadable ({exc})")
        return 0

    ratio = rows / T1_ROWS
    line = (
        f"graph-scale-watch: e_all_und={rows:,} doubled rows "
        f"(directed={directed:,}) — {ratio:.2f} of T1 ({T1_ROWS:,})"
    )
    if rows >= T1_ROWS:
        print(
            f"{line}\n"
            "graph-scale-watch: FIRE — T1 reached; Phase B activates "
            "(offline CSR substrate + mmap query path + Mojo BFS lane; "
            "doc/local/perf/graph_scaling.md §T1 watch). Record the growth driver."
        )
        return 1
    if rows >= WARN_ROWS:
        print(
            f"{line}\n"
            "graph-scale-watch: WARN — past 50% of T1; record the growth "
            "driver in graph_scaling.md §T1 watch."
        )
        return 0
    print(f"{line} OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
