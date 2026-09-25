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
#: Index membership is forbidden in BOTH views (2026-09-20, operator
#: sign-off): listed_on_index stars are not relationships — they collapsed
#: the whole graph into one 2,508-node component and the "longest chains"
#: devolved into index-roster hops (NIFTY SME EMERGE -> constituent), while
#: the edge-touched universe the distance matrix spans grew 1,722 -> 2,508
#: (quadratic render cost, 21s -> 61s). Same doctrine as
#: EDGE_TYPES_EXCLUDED_FROM_CENTRALITY (graph_centrality_index_noise,
#: completed.md #254) and the louvain amendment.
_CHAIN_FORBIDDEN = frozenset(_CHAINS_EXCLUDE) | {"listed_on_index"}


def longest_chains(
    conn, top_k: int = 5, *, max_exact: int = 3000, exact: bool = False
) -> list[str]:  # noqa: C901
    """Render the longest-chains section lines (pure function of ``conn``).

    Two views: ALL edges (index membership excluded — see
    _CHAIN_FORBIDDEN), and ACTIVITY edges only (membership stars excluded).
    For each: component count, diameter, median distance, the top-K most
    distant pairs, and the #1 chain hop-by-hop with the edge family
    carrying each hop, plus the family tally across all top-K chains (the
    capture-quality signal).

    S3 hard cap (hgx_first_scaling): diameter/distant-pairs are inherently
    pairwise questions, so this diagnostic may NEVER again dominate a
    gate leg — above ``max_exact`` edge-touched nodes the all-pairs
    matrix is replaced by distances from a deterministic stride sample
    of roots (O(sample*n) memory, not O(n^2)); sampled lines are labeled
    and distances read as lower bounds. Below the cap: exact, unchanged.
    Component counts stay exact either way (O(n+m)).

    ``exact=True`` (scipy_exact_universe S2, opt-in — never the gate
    path) lifts the sample to ALL roots: distances become exact, the
    SAMPLED note disappears. Cost at the 21,461-root live set: ~80 s
    dijkstra + ~6 GB peak per projection (full dist float64 +
    predecessors int32, in-process — the 14 GB box holds it; measured
    2026-09-26). The old ``triu_indices`` finite-stats pass (3.7 GB of
    index arrays at 21k, an OOM-shaped leftover from the 1.7k era) is
    replaced by a chunked integer histogram — identical median/diameter/
    ties, O(chunk*n) memory.
    """
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, shortest_path

    def _dist_stats(dist: "np.ndarray") -> tuple[int, float, int, int]:
        """(diameter, median, ordered ties at diameter, finite count).

        Chunked row scan — ordered-pair integer histogram; no triu
        materialization. The median replicates np.median over the
        ordered finite distances exactly (mean of the two middle order
        statistics), so the rendered :.0f output is byte-identical to
        the old materialized path. Ties are ORDERED; on a symmetric
        square matrix (exact mode) the caller halves them for canonical
        (a<b) counting.
        """
        hist = None
        for lo in range(0, dist.shape[0], 512):
            sub = dist[lo : lo + 512]
            vals = sub[np.isfinite(sub) & (sub > 0)]
            if not vals.size:
                continue
            ints = vals.astype(np.int64)
            chunk_hist = np.bincount(ints)
            if hist is None:
                hist = chunk_hist
            elif chunk_hist.size > hist.size:
                hist = np.concatenate(
                    [hist, np.zeros(chunk_hist.size - hist.size, dtype=hist.dtype)]
                )
                hist += chunk_hist
            else:
                hist[: chunk_hist.size] += chunk_hist
        if hist is None:
            return 0, 0.0, 0, 0
        nz = np.nonzero(hist)[0]
        diameter = int(nz.max())
        total = int(hist[nz].sum())
        k1 = (total - 1) // 2
        k2 = total // 2
        cum = np.cumsum(hist)

        def _order_stat(k: int) -> int:
            return int(np.searchsorted(cum, k + 1, side="left"))

        median = (_order_stat(k1) + _order_stat(k2)) / 2.0
        return diameter, median, int(hist[diameter]), total

    # Node universe = entities touched by at least one graph_edge.
    # Isolated entities (e.g. D19 exchange stubs: pathless, ticker-only,
    # zero edges) add n^2 distance-matrix cells while contributing
    # nothing to chains, diameter, or distant pairs — 5,026 of 6,748
    # entities (74%) on 2026-09-16, a 16x cell blowup over the 1,722
    # edge-touched universe this restores.
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM entities WHERE name IN "
            "(SELECT source FROM graph_edges WHERE edge_type != 'listed_on_index' "
            "UNION SELECT target FROM graph_edges WHERE edge_type != 'listed_on_index') "
            "ORDER BY rowid"
        )
    ]
    idx = {n: i for i, n in enumerate(names)}
    n = len(names)
    if not n:
        return ["  ALL edges: no edges", "  ACTIVITY edges: no edges"]

    pair_types: dict[tuple[int, int], set[str]] = {}

    def _add(forbidden: frozenset[str]) -> csr_matrix:
        rows_, cols_ = [], []
        for s, tgt, et in conn.execute("SELECT source, target, edge_type FROM graph_edges"):
            if et in forbidden:
                continue
            i, j = idx.get(s), idx.get(tgt)
            if i is None or j is None or i == j:
                continue
            rows_ += [i, j]
            cols_ += [j, i]
            key = (i, j) if i < j else (j, i)
            pair_types.setdefault(key, set()).add(et)
        return csr_matrix((np.ones(len(rows_), dtype=np.int8), (rows_, cols_)), shape=(n, n))

    capped = (n > max_exact) and not exact
    roots = np.unique(np.linspace(0, n - 1, max_exact).astype(int)) if capped else None
    n_roots = 0 if roots is None else int(len(roots))
    lines: list[str] = []
    for label, mat in (
        ("ALL edges (excl. index membership)", _add(frozenset({"listed_on_index"}))),
        ("ACTIVITY edges", _add(_CHAIN_FORBIDDEN)),
    ):
        n_comp, comp = connected_components(mat, directed=False)
        if capped:
            dist, pred = shortest_path(
                mat,
                method="D",
                unweighted=True,
                return_predecessors=True,
                indices=roots,
            )
            diameter, median, ties, _total = _dist_stats(dist)
        else:
            dist, pred = shortest_path(mat, method="D", unweighted=True, return_predecessors=True)
            diameter, median, ties, _total = _dist_stats(dist)
            ties //= 2  # symmetric square → canonical (a<b) pair count
        if _total == 0:
            lines.append(f"  {label}: no edges")
            continue
        # Distinct-candidate selection: greedy by distance, accepting a pair
        # only while BOTH endpoints are unused (node-disjoint), so the top-K
        # are K different chains rather than one hub endpoint repeated K
        # times (the naive top-5 at the diameter was Food_Processing -> five
        # different holders — one chain shape, five times). If the graph
        # cannot supply K disjoint pairs, a relaxed second pass fills the
        # remainder allowing reuse, never repeating an accepted pair.
        pairs: list[tuple[int, int, int, int]] = []
        seen: set[tuple[int, int]] = set()
        used: set[int] = set()

        # Candidates come tier by tier: unweighted BFS gives integer
        # distances, so walk d = diameter … 1 and lift each tier's pairs
        # with one numpy scan. The old approach materialised ALL n²
        # ordered pairs in Python (np.argsort over the full rank matrix +
        # a tuple comprehension) — 6.3M tuples for n=2,508, ~97% of a 61s
        # render, quadratic in the edge-touched universe (2026-09-20).
        # np.nonzero is row-major, matching the old argsort's flat-index
        # order within a tier; both orientations of a pair always get the
        # same verdict in the selection below (key + endpoint checks are
        # symmetric) so exact mode scans the canonical a<b triangle only.
        # Yields (node_a, node_b, d, row_a) — node ids for names/sets,
        # row_a (the sampled-root row, == node_a when exact) indexes
        # dist/pred, which are row-space under the cap.
        def _pair_tiers():
            for d in range(diameter, 0, -1):
                mask = np.isfinite(dist) & (dist == d)
                if roots is None:
                    mask = np.triu(mask, 1)
                if not mask.any():
                    continue
                for a, b in np.argwhere(mask):
                    yield (int(a) if roots is None else int(roots[a]), int(b), d, int(a))

        for relax in (False, True):
            for a, b, d, _row in _pair_tiers():
                if len(pairs) == top_k:
                    break
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                if not relax and (a in used or b in used):
                    continue
                seen.add(key)
                used.update((a, b))
                pairs.append((a, b, d, _row))
            if len(pairs) == top_k:
                break
        cap_note = (
            f" | SAMPLED {n_roots}/{n} roots (cap {max_exact}): "
            f"d >= {diameter}, distances are lower bounds"
            if capped
            else ""
        )
        lines.append(
            f"  {label}: {n_comp} components | diameter {diameter} "
            f"({ties} pairs at d={diameter}) | median dist {median:.0f}"
            f"{cap_note}"
        )
        family_tally: dict[str, int] = {}
        for cand_i, (a, b, d, row) in enumerate(pairs, 1):
            lines.append(
                f"    {cand_i}. d={d}  {names[a]}  <->  {names[b]}  "
                f"[comp {int((comp == comp[a]).sum())} nodes]"
            )
            # reconstruct this chain and tally the families carrying its hops
            path, cur = [b], b
            while cur != a and pred[row, cur] >= 0:
                cur = int(pred[row, cur])
                path.append(cur)
            for u, v in zip(path, path[1:]):
                key = (u, v) if u < v else (v, u)
                for fam in pair_types.get(key, {"?"}):
                    family_tally[fam] = family_tally.get(fam, 0) + 1
        if pairs:
            a, b, _d, row = pairs[0]
            path, cur = [b], b
            while cur != a and pred[row, cur] >= 0:
                cur = int(pred[row, cur])
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


def hyper_structure_lines(conn, top_k: int = 5) -> list[str]:
    """Render the hypergraph structure section (pure function of ``conn``).

    D4's first SQL-over-incidence consumer (hgx_first_scaling S2a):
    block/hyperedge structure straight from the star store — cost scales
    with |incidences| (~6k rows), never with node pairs. The capture-
    quality signal moves here from the dyadic top-K chain tally: the
    family mix across the largest hyperedges shows WHICH capture lanes
    dominate, without materialising any pairwise distance.
    """
    have = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        " AND name IN ('hyper_edges','hyper_incidences')"
    ).fetchone()[0]
    if have != 2:
        return ["  (incidence store absent)"]

    n_he, n_inc = conn.execute(
        "SELECT (SELECT COUNT(*) FROM hyper_edges),       (SELECT COUNT(*) FROM hyper_incidences)"
    ).fetchone()
    fams = conn.execute(
        "SELECT edge_type, COUNT(*) FROM hyper_edges GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    roles = conn.execute(
        "SELECT role, COUNT(*) FROM hyper_incidences WHERE role IS NOT NULL"
        " GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    dated = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences WHERE valid_from IS NOT NULL"
    ).fetchone()[0]
    top = conn.execute(
        """
        SELECT he.edge_type, he.label, COUNT(*) AS members
        FROM hyper_edges he JOIN hyper_incidences hi ON hi.edge_id = he.id
        GROUP BY 1, 2 ORDER BY members DESC, 1, 2 LIMIT ?
        """,
        (top_k,),
    ).fetchall()
    blocks = conn.execute(
        "SELECT COUNT(DISTINCT value), MAX(n) FROM"
        " (SELECT value, COUNT(*) AS n FROM graph_analytics"
        "  WHERE metric = 'hypermmsbm_community' GROUP BY value)"
    ).fetchone()

    lines = [
        f"  hyperedges {n_he}  |  incidences {n_inc}  |  dated valid_from {dated}",
        "  families: " + ", ".join(f"{f} {n}" for f, n in fams),
    ]
    if roles:
        lines.append("  roles: " + ", ".join(f"{r} {n}" for r, n in roles))
    lines.append("  top by membership: " + ", ".join(f"{et}/{lab} ({m})" for et, lab, m in top))
    tally = {}
    for et, n in fams:
        for tet, _lab, _m in top:
            if tet == et:
                tally[et] = tally.get(et, 0) + 1
    lines.append(
        "  capture-quality family tally (top-"
        + str(top_k)
        + "): "
        + (", ".join(f"{k} {v}" for k, v in sorted(tally.items(), key=lambda x: -x[1])) or "—")
    )
    if blocks and blocks[0]:
        lines.append(
            f"  hy-MMSBM blocks {blocks[0]} (largest {blocks[1]} members) — graph_analytics"
        )
    return lines


def print_stats(exact_chains: bool = False) -> int:  # noqa: C901
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
        # scipy_exact_universe S6: exact structure scalars from the stamp
        # (all-sources scipy pass; the Onager row above serves NULL on the
        # disconnected live graph). Best-effort — absent stamp prints the
        # advisory, never fails stats.
        try:
            import duckdb as _dq

            _gcon = _dq.connect(str(_PROJECT_ROOT / "memory" / "graph.duckdb"), read_only=True)
            try:
                rows = _gcon.execute("SELECT metric, value FROM v_graph_structure").fetchall()
            finally:
                _gcon.close()
            if rows:
                st = {m: v for m, v in rows}
                print(
                    f"  exact (stamped): diameter {int(st['diameter'])}"
                    f"   radius {int(st['radius'])}"
                    f"   avg path length {st['avg_path_length']:.4f}"
                    f"   components {int(st['components'])}"
                    f"   largest {int(st['largest_component'])}"
                )
            else:
                print("  exact (stamped): absent — run make stamp-centrality")
        except Exception as e:  # noqa: BLE001  # advisory; never fail stats
            print(f"  exact (stamped): unavailable ({type(e).__name__})")

    # --- Longest chains (capture quality across domains) ---
    # Advisory + best-effort like the sections around it: the chain reader
    # needs scipy (declared dep) and reads straight from SQLite.
    print(_hr("Longest chains — capture quality across domains", "-"))
    _conn = connect()
    try:
        for _line in longest_chains(_conn, exact=exact_chains):
            print(_line)
    except Exception as e:  # noqa: BLE001  # advisory section; never fail stats
        print(f"  (unavailable: {type(e).__name__}: {str(e)[:120]})")
    finally:
        _conn.close()

    # --- Hypergraph structure (D4 first SQL-over-incidence consumer) ---
    # Incidence-native: block/hyperedge structure straight from the star
    # store — O(|incidences|), no pairwise materialisation. Advisory +
    # best-effort like the sections around it.
    print(_hr("Hypergraph structure (incidence SQL)", "-"))
    _hconn = connect()
    try:
        for _line in hyper_structure_lines(_hconn):
            print(_line)
    except Exception as e:  # noqa: BLE001  # advisory section; never fail stats
        print(f"  (unavailable: {type(e).__name__}: {str(e)[:120]})")
    finally:
        _hconn.close()

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
    import argparse

    _ap = argparse.ArgumentParser(description="FinData graph stats render")
    _ap.add_argument(
        "--exact-chains",
        action="store_true",
        help="longest chains over ALL roots, exact distances (scipy_exact_universe "
        "S2; ~4-5 min and ~6 GB peak at the live 21.5k-root set — never the "
        "gate path; the default stays a 3,000-root stride sample)",
    )
    _args = _ap.parse_args()
    sys.exit(print_stats(exact_chains=_args.exact_chains))
