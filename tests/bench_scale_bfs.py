#!/usr/bin/env python3
"""Perf diagnostic: synthetic BFS scale ladder (vault_scaling.md §3).

Reproduces the folded evidence ladder in-repo (the /tmp/scale_bfs.py +
scratch DBs were wiped): for each target DOUBLED-rows scale, build a
synthetic vault-shaped graph in a scratch DuckDB and measure —

  materialize  `_materialise_walk_substrate` (the production e_dir +
               e_all_und CTAS — also the rebuild cost that breaks first)
  1 expand     point lookup `WHERE a_id = ?` (zone-map-pruned; the §3
               footnote warns this is NOT per-level cost)
  full BFS     `shortest_path(n0, m7, hops=5)` where m7 sits in a
               detached 2-node component — unreachable, so the BFS
               exhausts the giant component within 5 levels (the §3
               "full 5-level BFS" semantics; best-of-3)

Reference (2026-09-04, this box): 1M → 0.5 s / 15 ms / 144 ms;
10M → 5.0 s / 9 ms / 321 ms; 100M → 48.5 s / 4 ms / 1750 ms.
Tier flags: full BFS > 100 ms = T1-class (CSR build due); materialize
> 5 s = T2-class (rebuild-at-scale due) — vault_scaling.md §2.

Synthetic shape mirrors production: degree-D directed edges per node
(default 22 ≈ today's 22.4 directed rows/node), endpoints doubled into
e_all_und; ids BIGINT like the real tables. Targets come from a Knuth
multiplicative hash of (src, k) — deterministic, no Python loop, no RNG
state. NOT a make-perf leg (100M ≈ 10 GB scratch + ~1 min materialize):
run on demand, e.g.

    .venv/bin/python3 tests/bench_scale_bfs.py                  # 1M, 10M
    .venv/bin/python3 tests/bench_scale_bfs.py --rows 100000000 --keep
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb  # noqa: E402

from helpers.graph.query import (  # noqa: E402
    _materialise_walk_substrate,
    shortest_path,
)

# §3 reference numbers (doubled rows -> (materialize_s, expand_ms, bfs_ms)).
REFERENCE = {
    1_000_000: (0.5, 15.0, 144.0),
    10_000_000: (5.0, 9.0, 321.0),
    100_000_000: (48.5, 4.0, 1750.0),
}
T1_BFS_S = 0.100
T2_MATERIALIZE_S = 5.0


def _best_of(fn, reps: int) -> float:
    """Best-of-N wall time in seconds (absorbs scheduler jitter)."""
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return min(times)


def _build(con: duckdb.DuckDBPyConnection, n_nodes: int, degree: int) -> None:
    """Deterministic synthetic vault: n0..n{N-1} giant component + m0/m7 pair."""
    con.execute(
        "CREATE TABLE v_node AS "
        "SELECT i AS id, 'n' || i AS name, 'synthetic' AS kind FROM range(?) t(i)",
        [n_nodes],
    )
    # Knuth multiplicative hash picks: dst = ((i*2654435761) + k*40503) % N —
    # distinct per (i, k), seeded only by the constants (no RNG state).
    con.execute(
        """
        CREATE TABLE _stg_edges AS
        SELECT 'n' || i AS source,
               'n' || ((i * 2654435761 + k * 40503 + 2654435761) % ?) AS target,
               'synthetic' AS edge_type, NULL AS valid_from, NULL AS valid_to
        FROM range(?) t(i), range(?) d(k)
        WHERE i != ((i * 2654435761 + k * 40503 + 2654435761) % ?)
        """,
        [n_nodes, n_nodes, degree, n_nodes],
    )
    # Detached component: m0 -> m1 only. m7 (no edges) is the unreachable dst.
    con.execute("INSERT INTO _stg_edges VALUES ('m0', 'm1', 'synthetic', NULL, NULL)")
    con.execute("INSERT INTO v_node VALUES (1000000000, 'm0', 'synthetic')")
    con.execute("INSERT INTO v_node VALUES (1000000007, 'm7', 'synthetic')")


def _run_scale(rows: int, degree: int, keep: bool) -> dict[str, float]:
    n_nodes = max(2, rows // (2 * degree))
    scratch = Path(tempfile.mkdtemp(prefix=f"scale_bfs_{rows}_"))
    db_path = scratch / "graph.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        t0 = time.perf_counter()
        _build(con, n_nodes, degree)
        gen_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        _materialise_walk_substrate(con)
        materialize_s = time.perf_counter() - t0

        actual_rows = con.execute("SELECT COUNT(*) FROM e_all_und").fetchone()[0]

        src_id = con.execute("SELECT id FROM v_node WHERE name = 'n0'").fetchone()[0]
        expand_s = _best_of(
            lambda: con.execute(
                "SELECT count(*) FROM e_all_und WHERE a_id = ?", [src_id]
            ).fetchone(),
            5,
        )
        bfs_s = _best_of(lambda: shortest_path(con, "n0", "m7", max_hops=5, edge_label=None), 3)
        return {
            "nodes": n_nodes,
            "gen_s": gen_s,
            "materialize_s": materialize_s,
            "rows": actual_rows,
            "expand_ms": expand_s * 1000,
            "bfs_ms": bfs_s * 1000,
        }
    finally:
        con.close()
        if keep:
            print(f"  kept scratch DB: {db_path}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--rows",
        type=int,
        nargs="+",
        default=[1_000_000, 10_000_000],
        help="Doubled-rows ladder (default: 1M 10M; 100M ≈ 10 GB scratch, opt in)",
    )
    p.add_argument("--degree", type=int, default=22, help="Directed edges/node (prod ≈ 22.4)")
    p.add_argument("--keep", action="store_true", help="Keep scratch DBs for inspection")
    args = p.parse_args(argv)

    print(
        f"{'rows':>12} {'nodes':>10} {'gen':>7} {'materialize':>11} "
        f"{'1 expand':>9} {'full BFS':>9}  flags"
    )
    for rows in sorted(args.rows):
        r = _run_scale(rows, args.degree, args.keep)
        flags = []
        if r["bfs_ms"] / 1000 > T1_BFS_S:
            flags.append("T1-class: BFS > 100 ms — CSR build due (§2)")
        if r["materialize_s"] > T2_MATERIALIZE_S:
            flags.append("T2-class: materialize > 5 s — rebuild-at-scale due (§2)")
        ref = REFERENCE.get(rows)
        ref_s = f"  (ref {ref[0]:.1f}s/{ref[1]:.0f}ms/{ref[2]:.0f}ms)" if ref else ""
        print(
            f"{r['rows']:>12,} {r['nodes']:>10,} {r['gen_s']:>6.1f}s {r['materialize_s']:>10.1f}s "
            f"{r['expand_ms']:>8.1f}ms {r['bfs_ms']:>8.1f}ms  {'; '.join(flags)}{ref_s}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
