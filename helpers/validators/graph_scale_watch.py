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

Chains-cap watch (longest_chains_cap_policy S3, 2026-10-19): a third
section reads the SQLite store and reports the edge-touched universe
against ``stats.CHAIN_CAP`` — below cap: exact; above: SAMPLED advisory
(lower-bound mode is sanctioned, exit stays 0). Read-only; any read
failure is a SKIP line.

Usage:
    python3 helpers/validators/graph_scale_watch.py [--check] [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

# Repo root: helpers/validators/graph_scale_watch.py -> parents[2]. House
# bootstrap — needed now that this watcher imports the chains cap from
# helpers.graph.stats (single source of truth, no drift).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.graph.stats import CHAIN_CAP  # noqa: E402

#: vault_scaling ladder T1 (graph_scaling.md §T1 watch): full BFS >100 ms
#: class, Phase B activation line.
T1_ROWS = 1_000_000

#: 50% of T1 — start recording the growth driver (to date: event density,
#: not note volume — graph_scaling.md §T1 watch).
WARN_ROWS = 500_000


def _edge_touched_count(db: Path) -> int:
    """Edge-touched universe — the chains-cap denominator. Same SQL as
    ``stats.longest_chains``: entities touched by at least one non-index
    edge (isolated entities add distance-matrix cells, not chains).

    part_of/has_company retirement (2026-10-19): reads through
    ``edge_projection``. ``stats.longest_chains`` measures the DuckDB
    ``e_all_und`` substrate, which projects the retired pair out of the
    canonical sector hyperedges — so a raw ``graph_edges`` read here would
    undercount the denominator by every sector-only company (live: ~200
    companies whose only incident was their sector membership) and could
    report "exact" for a universe the exact lane cannot actually solve.
    """
    from helpers.core.db import connect
    from helpers.graph.edge_projection import edge_rows

    con = connect(db, read_only=True)
    try:
        names = {n for s, t, et, _w in edge_rows(con) if et != "listed_on_index" for n in (s, t)}
        if not names:
            return 0
        placeholders = ", ".join("?" for _ in names)
        row = con.execute(
            f"SELECT COUNT(*) FROM entities WHERE name IN ({placeholders})",  # noqa: S608
            tuple(names),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--check", action="store_true", help="advisory gate leg mode")
    ap.add_argument("--db", default="memory/graph.duckdb", help="graph cache path")
    ap.add_argument(
        "--research-db",
        default="memory/research.db",
        help="SQLite store for the chains-cap watch (edge-touched count)",
    )
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

    # Chains-cap watch (longest_chains_cap_policy S3, 2026-10-19): the
    # exact→sampled crossover is SANCTIONED degraded mode, never a gate
    # failure — this tripwire exists so the next crossover is a
    # notification, not an archaeology find (the 3,000 crossover went
    # live unrecorded and was only caught by a session census).
    research = Path(args.research_db)
    if not research.exists():
        print(f"chains-cap-watch: SKIP — {research} not found")
        return 0
    try:
        n = _edge_touched_count(research)
    except Exception as exc:  # noqa: BLE001 — advisory leg: any read failure is a SKIP, not a red
        print(f"chains-cap-watch: SKIP — {research} unreadable ({exc})")
        return 0
    if n > CHAIN_CAP:
        print(
            f"chains-cap-watch: SAMPLED — edge-touched n={n:,} > cap {CHAIN_CAP:,} "
            f"({n / CHAIN_CAP:.1f}x): diameters/distant-pairs are stride-sample "
            "LOWER BOUNDS (stats.longest_chains). Exact numbers on demand: "
            "longest_chains(exact=True) (~80 s / ~6 GB, scipy_exact_universe)."
        )
    else:
        print(f"chains-cap-watch: exact — edge-touched n={n:,} <= cap {CHAIN_CAP:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
