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

Hyper-scale watch (2026-11-02): the growth record's tail (09-25) saw the
hyper layer at h_edge 1,558 / 8,070 incidences; live is 11,693 / 51,554 —
a 6-7x surge no watcher covered. Three axes now read ``h_incidence`` on
the same handle: incidence count (the star-lane cost driver; WARN at the
100K bench posture, FIRE at 250K — the only exit-1 hyper axis), max
hyperedge cardinality (spectral lanes build operators up to max_k), and
the clique tax Σ C(m,2) (the dyadic-fallback wall the bench measured as
a louvain OOM at ~3.42M dyads). The latter two report only — measured
anchors, not gate lines.

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

#: Hyper axes, anchored 2026-11-02 on helpers/bench/hyper_scale_bench.py
#: (09-13 baseline re-run) + the live snapshot (51,554 incidences, max
#: hyperedge 850, clique tax 2,453,321 dyads). WARN 100K = the measured
#: bench posture — past it, re-anchor the star lane. FIRE 250K = ~5x live,
#: unmeasured territory — hyper Phase B.
H_INCIDENCE_WARN = 100_000
H_INCIDENCE_FIRE = 250_000

#: Max hyperedge cardinality (live 850): spectral/community lanes build
#: operators up to max_k, so the tail edge drives their cost. Report-only.
MAX_HYPEREDGE_WARN = 1_000

#: Clique tax Σ C(m,2) — the dyadic-fallback wall. hyper_scale_bench
#: 09-13: louvain completed at 2.85M dyads (142.7 s) and DID NOT COMPLETE
#: at 3.42M (>440 s, OOM on the 14 GB box). Report-only: staying
#: hyper-native is already decided; this records the wall's approach.
CLIQUE_TAX_WARN = 3_400_000


def _hyper_line(inc: int, max_edge: int, tax: int) -> int:
    """Hyper-report lines given the three counts (live or mirror). Returns
    the exit-code contribution: 1 only on the h_incidence FIRE."""
    rc = 0
    line = (
        f"hyper-scale-watch: h_incidence={inc:,} "
        f"(max hyperedge={max_edge:,}, clique tax={tax:,} dyads)"
    )
    if inc >= H_INCIDENCE_FIRE:
        print(
            f"{line}\n"
            "hyper-scale-watch: FIRE — hyper Phase B: re-anchor "
            "hyper_scale_bench at live shape and budget the star lane "
            "(doc/local/perf/graph_scaling.md)."
        )
        rc = 1
    elif inc >= H_INCIDENCE_WARN:
        print(
            f"{line}\n"
            "hyper-scale-watch: WARN — past the measured 100K-incidence "
            "bench posture; re-anchor hyper_scale_bench and record the "
            "growth driver in graph_scaling.md."
        )
    else:
        print(f"{line} OK")
    if max_edge >= MAX_HYPEREDGE_WARN:
        print(
            f"hyper-scale-watch: WARN — max hyperedge {max_edge:,} >= "
            f"{MAX_HYPEREDGE_WARN:,}: spectral/community lanes build "
            "operators up to max_k; re-check lane budgets before the "
            "next giant-k probe."
        )
    if tax >= CLIQUE_TAX_WARN:
        print(
            f"hyper-scale-watch: WARN — clique tax {tax:,} dyads at/above "
            "the measured louvain OOM anchor (~3.42M, hyper_scale_bench "
            "09-13): the dyadic projection is the OOM wall, but the "
            "bipartite-louvain lane (helpers/graph/bipartite_communities.py) "
            "reaches ARI parity on the incidence directly (Wall 4 probe "
            "2026-11-02) — prefer it over dyadic community analytics."
        )
    return rc


def _hyper_watch(con) -> int:
    """Hyper-layer axes on an open live handle: query, then report via
    _hyper_line (shared with the mirror path). Returns the exit-code
    contribution (1 only on the h_incidence FIRE). Every read failure is
    a SKIP line, never a red (advisory leg).
    """
    try:
        row = con.execute("SELECT COUNT(*) FROM h_incidence").fetchone()
        inc = int(row[0]) if row and row[0] is not None else 0
        row = con.execute(
            "SELECT COALESCE(MAX(m), 0), COALESCE(SUM(m * (m - 1) / 2), 0) "
            "FROM (SELECT COUNT(*) AS m FROM h_incidence GROUP BY edge_id)"
        ).fetchone()
        max_edge = int(row[0] or 0)
        tax = int(row[1] or 0)
    except duckdb.CatalogException:
        print("hyper-scale-watch: SKIP — h_incidence not built yet in this cache")
        return 0
    except duckdb.Error as exc:
        print(f"hyper-scale-watch: SKIP — hyper tables unreadable ({exc})")
        return 0
    return _hyper_line(inc, max_edge, tax)


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


def _mirror_axes(db: Path) -> tuple[int | None, int, int]:
    """Wall 5 S2: degrade to the parquet snapshot mirror when the live
    store is mid-rebuild (single-writer funnel holds the DuckDB lock).
    Reads the same structure axes from snapshots/parquet/duckdb (the CSR
    path's manifest-gated mirror). Returns (rows, directed, hyper_rc) —
    rows=None only if the mirror itself lacks e_all_und. Never raises for
    a missing mirror: primary None -> caller SKIPs as today. The mirror is
    structure-only and carries the same tables the watcher reads, so the
    counts are comparable (a rebuild is in flight, so the mirror may be
    one generation behind — that is the sanctioned degraded mode).
    """
    from helpers.maintenance.analytics.query import connect_snapshots

    try:
        con = connect_snapshots()
    except Exception:  # noqa: BLE001 — advisory leg: any mirror failure → SKIP
        return None, 0, 0
    try:
        try:
            row = con.execute("SELECT COUNT(*) FROM duckdb__e_all_und").fetchone()
            rows = int(row[0]) if row is not None else 0
        except duckdb.CatalogException:
            return None, 0, 0
        directed = 0
        try:
            row = con.execute("SELECT COUNT(*) FROM duckdb__e_dir").fetchone()
            directed = int(row[0]) if row is not None else 0
        except duckdb.CatalogException:
            pass
        # hyper axes from the mirror — same shape as the live h_incidence
        try:
            row = con.execute("SELECT COUNT(*) FROM duckdb__h_incidence").fetchone()
            inc = int(row[0]) if row and row[0] is not None else 0
            row = con.execute(
                "SELECT COALESCE(MAX(m), 0), COALESCE(SUM(m * (m - 1) / 2), 0) "
                "FROM (SELECT COUNT(*) AS m FROM duckdb__h_incidence GROUP BY edge_id)"
            ).fetchone()
            max_edge = int(row[0] or 0) if row else 0
            tax = int(row[1] or 0) if row else 0
        except duckdb.CatalogException:
            inc = 0
            max_edge = 0
            tax = 0
        hyper_rc = 0
        if inc > 0 or max_edge > 0 or tax > 0:
            hyper_rc = _hyper_line(inc, max_edge, tax)
        return rows, directed, hyper_rc
    finally:
        con.close()


def _graph_axes(db: Path) -> tuple[int | None, int, int]:
    """(rows, directed, hyper_rc) over one read-only funnel open.

    The quack-served graph store is file-mode unreadable (duckdb 2.0
    refuses a second reader while the server holds the write lock), so
    route through the read-only funnel: wire attach when served, flock +
    file open otherwise. rows=None when e_all_und is absent (pre-build);
    raises duckdb.CatalogException if the funnel open itself fails and
    duckdb.Error on a transient lock (live-invariants owns rebuilds —
    the watcher must not red the gate on a busy store). Wall 5: when a
    rebuild window is detected (the single-writer funnel holds the
    DuckDB lock) the watcher DEGRADES to the parquet mirror instead of
    SKIPing — the advisory gate stays green, labeled degraded.
    """
    from helpers.misc.duckdb_lock import open_read_only, is_rebuild_in_progress

    try:
        con = open_read_only(db)
    except duckdb.Error:
        if is_rebuild_in_progress(db):
            print(
                "graph-scale-watch: DEGRADED — rebuild window detected; "
                "reading structure axes from the parquet mirror "
                f"(snapshots/parquet/duckdb) for {db}"
            )
            return _mirror_axes(db)
        raise

    try:
        rows: int | None = None  # dyadic section runs only when e_all_und exists
        directed = 0
        try:
            row = con.execute("SELECT COUNT(*) FROM e_all_und").fetchone()
            rows = int(row[0]) if row is not None else 0
            row = con.execute("SELECT COUNT(*) FROM e_dir").fetchone()
            directed = int(row[0]) if row is not None else 0
        except duckdb.CatalogException:
            print(f"graph-scale-watch: SKIP — e_all_und not built yet in {db}")
        # Hyper axes read on the SAME open handle — sub-ms queries; a
        # second funnel open would be pure overhead. Sections are
        # independent: a dyadic FIRE must not mask the hyper axes.
        hyper_rc = _hyper_watch(con)
        return rows, directed, hyper_rc
    finally:
        con.close()


def _dyadic_line(rows: int, directed: int) -> int:
    """Dyadic growth line; prints OK/WARN/FIRE, returns 1 only on FIRE."""
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
        return 1  # fall through: a dyadic fire must not mask the other axes
    if rows >= WARN_ROWS:
        print(
            f"{line}\n"
            "graph-scale-watch: WARN — past 50% of T1; record the growth "
            "driver in graph_scaling.md §T1 watch."
        )
    else:
        print(f"{line} OK")
    return 0


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
    rc = 0
    try:
        rows, directed, hyper_rc = _graph_axes(db)
    except duckdb.CatalogException:
        print(f"graph-scale-watch: SKIP — e_all_und not built yet in {db}")
        return 0
    except duckdb.Error as exc:
        # A concurrent rebuild holds the write lock; live-invariants owns
        # rebuilds — the watcher must not red the gate on a transient lock.
        print(f"graph-scale-watch: SKIP — {db} unreadable ({exc})")
        return 0

    if rows is not None:
        rc = _dyadic_line(rows, directed)

    # Chains-cap watch (longest_chains_cap_policy S3, 2026-10-19): the
    # exact→sampled crossover is SANCTIONED degraded mode, never a gate
    # failure — this tripwire exists so the next crossover is a
    # notification, not an archaeology find (the 3,000 crossover went
    # live unrecorded and was only caught by a session census).
    research = Path(args.research_db)
    n: int | None = None
    if not research.exists():
        print(f"chains-cap-watch: SKIP — {research} not found")
    else:
        try:
            n = _edge_touched_count(research)
        except Exception as exc:  # noqa: BLE001 — advisory leg: any read failure is a SKIP, not a red
            print(f"chains-cap-watch: SKIP — {research} unreadable ({exc})")
    if n is not None:
        if n > CHAIN_CAP:
            print(
                f"chains-cap-watch: SAMPLED — edge-touched n={n:,} > cap {CHAIN_CAP:,} "
                f"({n / CHAIN_CAP:.1f}x): diameter exact via iFUB; distant-pairs "
                "are stride-sample LOWER BOUNDS (stats.longest_chains). Exact "
                "numbers on demand: longest_chains(exact=True) (~80 s / ~6 GB, "
                "scipy_exact_universe)."
            )
        else:
            print(f"chains-cap-watch: exact — edge-touched n={n:,} <= cap {CHAIN_CAP:,}")
    # All sections ran; the worst section wins (FIRE=1, everything else 0).
    return max(rc, hyper_rc)


if __name__ == "__main__":
    sys.exit(main())
