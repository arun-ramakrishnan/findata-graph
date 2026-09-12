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

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

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


# --------------------------------------------------------------------------- #
# Longest chains (capture quality across domains) — hypergraph_incidence_hyx
# follow-up, 2026-09-13. Longest SHORTEST paths (all-pairs BFS, unweighted,
# undirected projection): the diameter is set by periphery-to-periphery
# pairs, and WHICH edge families carry those chains shows how well the
# capture domains bridge each other (a chain that rides only 1-2 families
# = weakly-bridged domains). scipy is a direct import -> declared dep.
# --------------------------------------------------------------------------- #
#: Membership/provenance star families excluded from the ACTIVITY view —
#: taxonomy/country/theme hubs collapse distances without carrying
#: economic signal (same exclusion family as analytics._MEMBERSHIP_TYPES).
_CHAINS_EXCLUDE = ("part_of", "has_company", "belongs_to", "cited_in", "listed_in", "exposed_to")


def longest_chains(conn, top_k: int = 5) -> list[str]:  # noqa: C901
    """Render the longest-chains section lines (pure function of ``conn``).

    Two views: ALL edges, and ACTIVITY edges only (membership stars
    excluded). For each: component count, diameter, median distance, the
    top-K most distant pairs, and the #1 chain hop-by-hop with the edge
    family carrying each hop, plus the family tally across all top-K
    chains (the capture-quality signal).
    """
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, shortest_path

    names = [r[0] for r in conn.execute("SELECT name FROM entities ORDER BY rowid")]
    idx = {n: i for i, n in enumerate(names)}
    n = len(names)

    pair_types: dict[tuple[int, int], set[str]] = {}

    def _add(exclude: bool) -> csr_matrix:
        rows_, cols_ = [], []
        for s, tgt, et in conn.execute("SELECT source, target, edge_type FROM graph_edges"):
            if exclude and et in _CHAINS_EXCLUDE:
                continue
            i, j = idx.get(s), idx.get(tgt)
            if i is None or j is None or i == j:
                continue
            rows_ += [i, j]
            cols_ += [j, i]
            key = (i, j) if i < j else (j, i)
            pair_types.setdefault(key, set()).add(et)
        return csr_matrix((np.ones(len(rows_), dtype=np.int8), (rows_, cols_)), shape=(n, n))

    lines: list[str] = []
    for label, mat in (("ALL edges", _add(False)), ("ACTIVITY edges", _add(True))):
        n_comp, comp = connected_components(mat, directed=False)
        dist, pred = shortest_path(mat, method="D", unweighted=True, return_predecessors=True)
        iu = np.triu_indices(n, 1)
        finite = dist[iu]
        finite = finite[np.isfinite(finite)]
        if not len(finite):
            lines.append(f"  {label}: no edges")
            continue
        diameter = int(finite.max())
        rank = np.where(np.isfinite(dist), dist, -1)
        # Distinct-candidate selection: greedy by distance, accepting a pair
        # only while BOTH endpoints are unused (node-disjoint), so the top-K
        # are K different chains rather than one hub endpoint repeated K
        # times (the naive top-5 at the diameter was Food_Processing -> five
        # different holders — one chain shape, five times). If the graph
        # cannot supply K disjoint pairs, a relaxed second pass fills the
        # remainder allowing reuse, never repeating an accepted pair.
        pairs: list[tuple[int, int, int]] = []
        seen: set[tuple[int, int]] = set()
        used: set[int] = set()
        order = [
            (int(a), int(b))
            for a, b in np.dstack(np.unravel_index(np.argsort(-rank.ravel()), rank.shape))[0]
            if a < b and np.isfinite(dist[a, b])  # disconnected = never a chain
        ]
        for relax in (False, True):
            for a, b in order:
                if len(pairs) == top_k:
                    break
                if (a, b) in seen:
                    continue
                if not relax and (a in used or b in used):
                    continue
                seen.add((a, b))
                used.update((a, b))
                pairs.append((a, b, int(dist[a, b])))
            if len(pairs) == top_k:
                break
        ties = int((dist[iu] == diameter).sum())

        lines.append(
            f"  {label}: {n_comp} components | diameter {diameter} "
            f"({ties} pairs at d={diameter}) | median dist {np.median(finite):.0f}"
        )
        family_tally: dict[str, int] = {}
        for cand_i, (a, b, d) in enumerate(pairs, 1):
            lines.append(
                f"    {cand_i}. d={d}  {names[a]}  <->  {names[b]}  "
                f"[comp {int((comp == comp[a]).sum())} nodes]"
            )
            # reconstruct this chain and tally the families carrying its hops
            path, cur = [b], b
            while cur != a and pred[a, cur] >= 0:
                cur = int(pred[a, cur])
                path.append(cur)
            for u, v in zip(path, path[1:]):
                key = (u, v) if u < v else (v, u)
                for fam in pair_types.get(key, {"?"}):
                    family_tally[fam] = family_tally.get(fam, 0) + 1
        if pairs:
            a, b, _d = pairs[0]
            path, cur = [b], b
            while cur != a and pred[a, cur] >= 0:
                cur = int(pred[a, cur])
                path.append(cur)
            hops = []
            for u, v in zip(path, path[1:]):
                key = (u, v) if u < v else (v, u)
                fams = "/".join(sorted(pair_types.get(key, {"?"})))
                hops.append(f"{names[u]} -[{fams}]-> {names[v]}")
            lines.append(f"    #1 chain ({len(path) - 1} hops): " + "; ".join(hops))
        tally = ", ".join(
            f"{k} x{v}" for k, v in sorted(family_tally.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"    chain composition across top-{len(pairs)}: {tally}")
    return lines


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

            print(
                f"  density {_fmt(metrics['density'])}"
                f"   triangles {_fmt(metrics['triangles'])}"
                f"   transitivity {_fmt(metrics['transitivity'])}"
            )
            print(
                f"  avg clustering {_fmt(metrics['avg_clustering'])}"
                f"   assortativity {_fmt(metrics['assortativity'])}"
            )
            if metrics["diameter"] is None:
                print(
                    "  diameter/radius/avg path length: — (graph is "
                    "disconnected under this projection)"
                )
            else:
                print(
                    f"  diameter {metrics['diameter']}"
                    f"   radius {metrics['radius']}"
                    f"   avg path length {_fmt(metrics['avg_path_length'])}"
                )

    # --- Longest chains (capture quality across domains) ---
    # Advisory + best-effort like the sections around it: the chain reader
    # needs scipy (declared dep) and reads straight from SQLite.
    print(_hr("Longest chains — capture quality across domains", "-"))
    _conn = connect()
    try:
        for _line in longest_chains(_conn):
            print(_line)
    except Exception as e:  # noqa: BLE001  # advisory section; never fail stats
        print(f"  (unavailable: {type(e).__name__}: {str(e)[:120]})")
    finally:
        _conn.close()

    # --- Sector size distribution ---
    print(_hr("Sectors by member count", "-"))
    ss = gs["sector_size_summary"]
    largest = gs["largest_sectors"]
    smallest = gs["smallest_sectors"]
    if ss["sector_count"]:
        print(
            f"  {ss['sector_count']} sectors  "
            f"(min={ss['min']}, median={ss['median']}, "
            f"max={ss['max']}, mean={ss['mean']})"
        )
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

    # --- Country census (country layer C1 / #219; exposure S5) ---
    # DuckDB-side (e_listed_in x v_country x v_company). Degrades
    # gracefully when the graph cache is absent — the SQLite-side
    # sections above stay authoritative.
    print(_hr("Countries by listing count", "-"))
    try:
        from helpers.graph.query import DUCKDB_PATH, connect_read_only

        _dcon = connect_read_only(DUCKDB_PATH)
        try:
            _rows = _dcon.execute(
                """
                SELECT c."name", COUNT(*)
                FROM e_listed_in e
                JOIN v_country c ON c.id = e.country_id
                GROUP BY 1 ORDER BY 2 DESC
                """
            ).fetchall()
            _row = _dcon.execute(
                """
                SELECT COUNT(*)
                FROM e_listed_in e
                JOIN v_company v ON v.id = e.company_id
                WHERE v.market_cap IS NULL
                """
            ).fetchone()
            _unbucketed = _row[0] if _row else 0
        finally:
            _dcon.close()
        _total = sum(n for _, n in _rows)
        for _name, _n in _rows:
            print(f"  {_name:20} {_n:5}  {_bar(_n, _total)}")
        if _unbucketed:
            print(f"  listed without a cap bucket: {_unbucketed}")
    except Exception as e:  # noqa: BLE001  # census is best-effort by design
        print(f"  (graph cache unavailable: {e})")

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
            most_recent_entity = conn.execute("SELECT MAX(last_updated) FROM entities").fetchone()[
                0
            ]
            most_recent_analytics = conn.execute(
                "SELECT MAX(computed_at) FROM graph_analytics"
            ).fetchone()[0]
            if (
                most_recent_entity
                and most_recent_analytics
                and most_recent_entity > most_recent_analytics
            ):
                print(
                    f"\n  ⚠ STALE: entities.last_updated={most_recent_entity} "
                    f"> analytics.computed_at={most_recent_analytics}"
                )
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
