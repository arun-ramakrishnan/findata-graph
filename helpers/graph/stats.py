#!/usr/bin/env python3
"""Print a one-shot summary of the FinData graph state.

This is now a THIN RENDERER over DatabaseIntegrityChecker.check_graph_summary()
(the single source of truth for entity/edge/sector/market-cap distributions).
The query logic lives in the checker so the integrity gate and this printer
can never drift apart.

Three presentation-only extras that don't belong in the integrity gate stay
here, queried directly:
  - ``graph_analytics`` freshness (a cache, not a data-integrity concern)
  - notes-on-disk vs company-entity count (a sanity counter, advisory)
  - whole-graph structural metrics (density/diameter/etc.) via Onager —
    Phase 2 of doc/improvements/archive/graph/graph_algos.txt

Usage:
    python3 helpers/graph/stats.py
    make graph-stats
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.misc.database_integrity_check import DatabaseIntegrityChecker  # noqa: E402


def _hr(label: str, char: str = "=") -> str:
    return f"\n{char * 60}\n{label}\n{char * 60}"


def _bar(n: int, total: int, width: int = 30) -> str:
    if total == 0:
        return "[" + " " * width + "] 0"
    filled = int(round(width * n / total))
    return "[" + "#" * filled + "." * (width - filled) + f"] {n}"


def print_stats() -> int:  # noqa: C901
    # --- Distributions: sourced from the checker (single source of truth) ---
    checker = DatabaseIntegrityChecker()
    try:
        gs = checker.check_graph_summary()
    finally:
        checker.close()

    ec = gs["entity_counts"]
    xec = gs["edge_counts"]
    n_entities = sum(ec.values())
    n_companies = ec.get("company", 0)
    n_sectors = ec.get("sector", 0)
    n_edges = sum(xec.values())

    print(_hr("FinData Graph — Stats"))
    print(f"\nEntities: {n_entities}  (companies: {n_companies}, sectors: {n_sectors})")
    print(f"Edges:    {n_edges}  across graph_edges")

    print("\nEdge-type breakdown:")
    max_n = max(xec.values(), default=1)
    for etype, n in xec.items():
        print(f"  {etype:20} {_bar(n, max_n)}")

    # --- Structure (Onager whole-graph metrics, Phase 2 of the
    # graph_algos proposal) ---
    # Unweighted, over the FULL edge set (all types); the node set is the
    # edge endpoints (isolated entities have no edges). Sub-second on the
    # live graph. Degrades gracefully — the SQLite-side summary above stays
    # authoritative if the Onager layer is unavailable.
    print(_hr("Structure (Onager, full edge set)", "-"))
    try:
        from helpers.graph.algorithms import graph_metrics
        metrics = graph_metrics()
    except Exception as e:  # noqa: BLE001  # advisory section; never fail stats
        print(f"  (unavailable: {type(e).__name__}: {str(e)[:120]})")
    else:
        if not metrics:
            print("  (no edges)")
        else:
            def _fmt(v: float | int | None) -> str:
                if v is None:
                    return "—"
                if isinstance(v, float):
                    return f"{v:.4f}"
                return str(v)

            print(f"  density {_fmt(metrics['density'])}"
                  f"   triangles {_fmt(metrics['triangles'])}"
                  f"   transitivity {_fmt(metrics['transitivity'])}")
            print(f"  avg clustering {_fmt(metrics['avg_clustering'])}"
                  f"   assortativity {_fmt(metrics['assortativity'])}")
            if metrics["diameter"] is None:
                print("  diameter/radius/avg path length: — (graph is "
                      "disconnected under this projection)")
            else:
                print(f"  diameter {metrics['diameter']}"
                      f"   radius {metrics['radius']}"
                      f"   avg path length {_fmt(metrics['avg_path_length'])}")

    # --- Sector size distribution ---
    print(_hr("Sectors by member count", "-"))
    ss = gs["sector_size_summary"]
    largest = gs["largest_sectors"]
    smallest = gs["smallest_sectors"]
    if ss["sector_count"]:
        print(f"  {ss['sector_count']} sectors  "
              f"(min={ss['min']}, median={ss['median']}, "
              f"max={ss['max']}, mean={ss['mean']})")
        print("\n  Top 10 largest:")
        for s in largest:
            print(f"    {s['n']:4}  {s['sector']}")
        print("\n  Bottom 5 smallest:")
        for s in smallest:
            print(f"    {s['n']:4}  {s['sector']}")

    # --- Market cap distribution ---
    print(_hr("Market cap distribution", "-"))
    for m in gs["market_cap_distribution"]:
        print(f"  {m['tier']:15} {m['n']}")

    # --- Data hygiene ---
    # These mirror ERROR-level checks in the integrity gate (orphan
    # companies, self-loops, orphan edges); reprinted here for the
    # human-readable snapshot. Sourced directly from SQLite (cheap,
    # and keeps this printer independent of the full check_integrity()
    # pipeline for a quick `make graph-stats`).
    print(_hr("Data hygiene", "-"))
    conn = connect()
    try:
        n_orphan_companies = conn.execute(
            """
            SELECT COUNT(*) FROM entities e
            WHERE e.entity_type='company'
              AND NOT EXISTS (
                SELECT 1 FROM graph_edges ge
                WHERE ge.edge_type='part_of'
                  AND (ge.source = e.name OR ge.target = e.name)
              )
            """
        ).fetchone()[0]
        n_no_ticker = conn.execute(
            "SELECT COUNT(*) FROM entities "
            "WHERE entity_type='company' AND (ticker IS NULL OR ticker='null')"
        ).fetchone()[0]
        n_self_loops = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source = target"
        ).fetchone()[0]
        n_orphan_edges = conn.execute(
            """
            SELECT COUNT(*) FROM graph_edges ge
            WHERE ge.source NOT IN (SELECT name FROM entities)
               OR ge.target NOT IN (SELECT name FROM entities)
            """
        ).fetchone()[0]
        print(f"  Companies with no sector edge:    {n_orphan_companies}")
        print(f"  Companies with no ticker:         {n_no_ticker}")
        print(f"  Self-loops in graph_edges:        {n_self_loops} (should be 0)")
        print(f"  Orphan edges (FK violation):      {n_orphan_edges} (should be 0)")

        # --- Analytics freshness (cache, not data integrity) ---
        print(_hr("graph_analytics", "-"))
        ga_metrics = conn.execute(
            "SELECT metric, COUNT(*) AS n, MAX(computed_at) AS last_at "
            "FROM graph_analytics GROUP BY metric ORDER BY metric"
        ).fetchall()
        if not ga_metrics:
            print("  (empty — run `make recompute-graph`)")
        else:
            for metric, n, last_at in ga_metrics:
                print(f"  {metric:25} {n:5} rows  last: {last_at}")
            most_recent_entity = conn.execute(
                "SELECT MAX(last_updated) FROM entities"
            ).fetchone()[0]
            most_recent_analytics = conn.execute(
                "SELECT MAX(computed_at) FROM graph_analytics"
            ).fetchone()[0]
            if most_recent_entity and most_recent_analytics \
               and most_recent_entity > most_recent_analytics:
                print(f"\n  ⚠ STALE: entities.last_updated={most_recent_entity} "
                      f"> analytics.computed_at={most_recent_analytics}")
                print("    Run `make recompute-graph` to refresh.")
            else:
                print("\n  ✓ fresh (analytics computed at/after most recent entity update)")

        # --- Notes on disk ---
        notes_dir = _PROJECT_ROOT / "findata" / "Companies"
        if notes_dir.is_dir():
            n_notes = sum(1 for _ in notes_dir.rglob("*.md"))
            print(_hr("Markdown notes on disk", "-"))
            print(f"  {n_notes} notes under findata/Companies/")
            if n_notes != n_companies:
                print(f"  ⚠ mismatch: {n_companies} company entities vs {n_notes} notes")
            else:
                print(f"  ✓ {n_companies} company entities match")
    finally:
        conn.close()

    print()  # trailing newline
    return 0


if __name__ == "__main__":
    sys.exit(print_stats())
