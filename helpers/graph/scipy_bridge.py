#!/usr/bin/env python3
"""SciPy bridge — BSD-native analytics second lane (scipy_graph_bridge S1).

Owns L1's route (d) (graph_perf_l1_bfs_scale S1 consumes this lane):
``scipy.sparse.csgraph.dijkstra`` with ``indices=<contract sources>``,
``unweighted=True`` — BSD, already a declared dependency, no venv split.
Spike (/tmp/graph_spike.txt PART 3): 2.87 s wall 4-way for BOTH L1a
metrics (6.49 s single-process); parity vs the live stamp r=0.9571,
top-100 overlap 91/100 — digit-identical to the license-dead igraph
route, because it is the same unweighted BFS over the same endpoint set.

Build discipline mirrors the retired igraph bridge (commit 720e38ff):
SQLite ``graph_edges`` plus the sector-membership projection (see
``_edge_rows``) is the source of truth; entity names sorted for
deterministic dense ids; unweighted (weights present on the edges, unused
by this lane).

Sources: the company contract — ``research.db.graph_analytics`` DISTINCT
entity_name WHERE metric='closeness_centrality' (1,734 names, in query
order; results row i corresponds to contract name i — the spike's
part-1 shuffle bug makes order part of the contract). The ~19k degree-1
VIGIL leaves stay as TARGETS, so contract scores are exact.

Write surface: ``graph_analytics`` (the contract's own home), UPSERT via
``algorithms.write_analytics``, opt-in ``--apply`` (D13); dry-run by
default. Metric names written: closeness_centrality, harmonic_centrality.

Lanes (ROUTING): scipy owns restricted-source closeness/harmonic;
Onager keeps every healthy SQL lane (pagerank, louvain, WCC,
clustering — all sub-second). Betweenness is NOT here (no Brandes in
scipy — L1b folding + Onager-on-core stands).

Single-process is the default; ``--jobs`` forks the multi-source
dijkstra (CoW inherits the CSR; map order preserves contract order) —
use it when the budget demands it (the perf leg does).

Post-apply updates (2026-09-23, after L1 S2/S3 landed): betweenness is
owned by the L1b fold lane, not Onager (ROUTING below); link-prediction
scoring is SQL-side in onager.py (the scipy sparse kernel stays the
conditional S4 fallback — not needed).

S2 exact-solve lanes (2026-09-23 late): Katz (``spsolve``) + eigenvector
(``eigsh``) over the UNWEIGHTED ex-index CSR. Measured, not assumed:
the umbrella proposed a weighted CSR, but weighted solves par worse
against live Onager (r=0.92) than unweighted (r=0.9996) — Onager's
pinned Katz/eigenvector are effectively unweighted at 1e-4 scale, so
the lane is too. Both stay ONAGER_DEFAULT (opt-in lanes + tests, no
dispatch flip).

s-t lanes (2026-09-24, scipy_st_lanes_routing_switch): Yen K-shortest
(``csgraph.yen``) and max-flow (``csgraph.maximum_flow``) between two
named entities — neither SQL nor Onager can express them. Read-only (an
s-t pair has no per-entity metric shape, so no ``--apply``); registered in
ROUTING and guarded by ``LANE_BUDGETS``: a lane that overruns its
wall-clock budget raises, so a slow lane cannot silently blow the caller's
runtime. The ROUTING switch owns lane TIME as well as ownership.
"""

from __future__ import annotations

import argparse
import json
from functools import partial
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
from scipy.sparse import csr_array, csr_matrix, identity
from scipy.sparse.csgraph import (
    dijkstra,
    maximum_flow as csgraph_maximum_flow,
    yen as csgraph_yen,
)
from scipy.sparse.linalg import ArpackNoConvergence, eigsh, spsolve

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402  # needs the shim above
from helpers.core.forkmap import fork_map  # noqa: E402  # needs the shim above
from helpers.graph.edge_projection import edge_rows as _edge_rows  # noqa: E402

DEFAULT_DB_PATH = _PROJECT_ROOT / "memory" / "research.db"
CLOSINESS_METRIC = "closeness_centrality"
HARMONIC_METRIC = "harmonic_centrality"
KATZ_METRIC = "katz_centrality"
EIGEN_METRIC = "eigenvector_centrality"
PPR_LANE = "pagerank_personalized"
PPR_METRIC = "pagerank_personalized"
PPR_ALPHA_DEFAULT = 0.85
# PPR-specific projection exclusions (operator: "yes it is producing noise.
# Drop listed_in and lets see", 2026-11-06): the baseline
# load_centrality_projection exclusion set plus listed_in — a country node
# routes seed mass to every home-market peer (HDFC Bank's #2 was `india`,
# ~0.005 sprinkle on all India-listed rows), which is hub noise, not walk
# proximity. Baseline default unchanged: closeness/harmonic/katz/eigenvector
# lanes and their graph_analytics contract keep parity.
PPR_EXCLUDE_EDGE_TYPES = ("listed_on_index", "listed_in")
# Second noise pass (operator: "sure lets try it", 2026-11-06): flip PPR to
# an include-only relational projection. co_mentioned_in is newsletter
# mention VOLUME (9,638 edges) — IT majors hoovered seed mass regardless of
# the seed's sector (HDFC Bank's post-listed_in top-10 was still
# Tech Mahindra/TCS/HCL/Wipro/Infosys). Keep company↔company relational
# structure: competitor, group, supply-chain, ownership, JV, M&A,
# embedding neighbours. Sector/theme/country/regulator/note hubs stay out.
# invested_in dropped on operator call ("drop invested_in", 2026-11-06):
# company→institution edges pulled 66 non-company rows (mutual funds) into
# stored top-100s.
PPR_INCLUDE_EDGE_TYPES = (
    "competes_with",
    "same_group",
    "supplier_to",
    "customer_of",
    "subsidiary_of",
    "jv_with",
    "acquired",
    "semantic_peer",
)
#: Seed-keyed results table (ppr_results_table §2). `graph_analytics`
#: PK is (metric, entity) — per-seed PPR rows there overwrote each
#: other (last-seed-wins, the 865k UPSERT / 17,302-row explosion the
#: live trial measured 2026-11-06). This table is PK (seed, entity).
PPR_TABLE = "ppr_results"
#: Top-N per seed first (proposal §4): 50 seeds x 100 = 5,000 rows.
PPR_TOP_DEFAULT = 100
#: Explicit ARPACK tolerance (the lane never inherits a default).
EIGSH_TOL = 1e-10
#: Katz admissibility margin: refuse alpha at/above this fraction of the
#: spectral radius (at the radius Katz is undefined, not slow).
KATZ_RADIUS_MARGIN = 0.99

#: s-t lanes (no per-entity metric shape — CLI/route output only).
YEN_LANE = "yen_k_shortest"
MAXFLOW_LANE = "maximum_flow"
#: Yen K bound: cost is ~K Dijkstras, so unbounded K is refused (fail
#: loud, same posture as the pref-attach all-pairs gate).
YEN_K_DEFAULT = 5
YEN_K_MAX = 25
#: Wall-clock budgets (seconds) for the s-t lanes. A lane that overruns
#: its budget raises instead of returning a slow result — the ROUTING
#: switch owns lane TIME as well as ownership (2026-09-24). Budgets are
#: the measured live values (yen ~0.03 s, max-flow ~0.02 s) x ~100
#: headroom: the guard catches super-linear decay at future scale, it is
#: not a millisecond shaver.
LANE_BUDGETS: dict[str, float] = {
    YEN_LANE: 5.0,
    MAXFLOW_LANE: 5.0,
    PPR_LANE: 5.0,
}


#: Lane ownership — Onager keeps the healthy SQL lanes; scipy owns the
#: restricted-source BFS family (this module) and the s-t lanes (Yen /
#: max-flow — no Onager/SQL equivalent); betweenness is the L1b 2-core
#: fold (helpers/graph/l1_betweenness.py — no Brandes in scipy).
ROUTING: dict[str, str] = {
    "closeness_centrality": "SCIPY",
    "harmonic_centrality": "SCIPY",
    "pagerank": "ONAGER_DEFAULT",
    "pagerank_weighted": "ONAGER_DEFAULT",
    "betweenness_centrality": "L1B_FOLD",
    "degree_centrality": "ONAGER_DEFAULT",
    "eigenvector_centrality": "ONAGER_DEFAULT",
    "louvain_community": "ONAGER_DEFAULT",
    "link_prediction": "ONAGER_DEFAULT",
    YEN_LANE: "SCIPY",
    MAXFLOW_LANE: "SCIPY",
    PPR_LANE: "SCIPY",
}


def load_projection(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[csr_matrix, list[str]]:
    """CSR adjacency from SQLite ``graph_edges`` — the sole source of truth.

    Bridge id discipline (commit 720e38ff): endpoint names sorted for
    deterministic dense ids; edges added BOTH directions (undirected).
    Weights ride the edges but this lane is unweighted by design
    (``unweighted=True`` matches Onager's unweighted centralities).
    Returns ``(A, names)`` — ``names[i]`` is the entity for CSR row i.

    Reads through :func:`_edge_rows`, so the retired part_of/has_company
    pair still contributes its projected edges (2026-10-19).
    """
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        # edge_projection: projects the retired part_of/has_company pair back
        # out of the canonical sector hyperedges (2026-10-19).
        rows = [(s, t) for s, t, _et, _w in _edge_rows(con)]
    finally:
        con.close()
    names = sorted({s for s, _ in rows} | {t for _, t in rows})
    pos = {n: i for i, n in enumerate(names)}
    r = np.fromiter((pos[s] for s, _ in rows), dtype=np.int64, count=len(rows))
    c = np.fromiter((pos[t] for _, t in rows), dtype=np.int64, count=len(rows))
    both = np.concatenate([r, c])
    A = csr_matrix(
        (np.ones(both.size, dtype=np.bool_), (both, np.concatenate([c, r]))),
        shape=(len(names), len(names)),
    )
    return A, names


def contract_sources(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    metric: str = CLOSINESS_METRIC,
) -> list[str]:
    """The persisted served-universe contract, in contract order (row i <-> contract[i]).

    graph_analytics_contract S1: reads the ``graph_analytics_contract``
    table (``ORDER BY seq`` — seed-time order; order is part of the
    contract, see :func:`source_positions`) when that table has rows for
    the metric. Table absent or empty for the metric → the LEGACY
    self-read (``SELECT DISTINCT entity_name FROM graph_analytics``), so
    fresh and pre-v14-restored stores keep serving unchanged. After
    seeding, the definition no longer equals this function's own output:
    the metric rows are pure cache, and growth happens only via
    ``seed_analytics_contract`` (S2 grow path), never as a side effect of
    a capped top-N run.
    """
    import sqlite3

    con = connect(db_path, read_only=True, row_factory=None)
    try:
        try:
            rows = con.execute(
                "SELECT entity_name FROM graph_analytics_contract WHERE metric = ? ORDER BY seq",
                (metric,),
            ).fetchall()
        except sqlite3.OperationalError:  # table absent: fresh / pre-v14 restore
            rows = []
        if rows:
            return [r[0] for r in rows]
        return [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?",
                (metric,),
            ).fetchall()
        ]
    finally:
        con.close()


def source_positions(contract: list[str], names: list[str]) -> np.ndarray:
    """CSR row positions for the contract names, IN CONTRACT ORDER.

    The (d) spike's part-1 bug was shuffling this zip — parity collapsed to
    r=0.006 while timing looked fine. Order is part of the contract."""
    pos = {n: i for i, n in enumerate(names)}
    missing = [n for n in contract if n not in pos]
    if missing:
        raise KeyError(f"contract names missing from the projection: {missing[:5]}...")
    return np.array([pos[n] for n in contract], dtype=np.int64)


def _structure_worker(payload: tuple) -> tuple[float, float, float, int]:
    """Dijkstra over one source shard, reduced to four scalars.

    Module-level for the fork pool. Returns (shard diameter, shard min
    eccentricity over GIANT-COMPONENT rows with >=1 finite reach, finite-
    pair distance sum, finite-pair count) — ordered pairs, self excluded.
    The parent combines shards into exact diameter / radius / average
    path length without ever assembling the n×n matrix
    (scipy_exact_universe S1). Radius restricted to the largest
    component: the global min-eccentricity is degenerate on a
    disconnected graph (any 2-node isolated pair pins it to 1).
    """
    A, lo, hi, comp, giant = payload
    D = dijkstra(A, directed=False, indices=np.arange(lo, hi), unweighted=True)
    finite = np.isfinite(D)
    D[~finite] = 0.0  # unreachable -> 0; excluded from sums, invisible to max
    # self pairs (diagonal) are not graph pairs — ROWS are shard-local,
    # COLUMNS stay global, so the diagonal sits at (j, lo+j):
    local = np.arange(hi - lo)
    D[local, np.arange(lo, hi)] = 0.0
    finite[local, np.arange(lo, hi)] = False
    row_max = D.max(axis=1)
    has_reach = finite.any(axis=1)
    ecc = row_max[has_reach]
    giant_rows = (comp[lo:hi] == giant) & has_reach
    ecc_giant = row_max[giant_rows]
    return (
        float(ecc.max()) if ecc.size else 0.0,
        float(ecc_giant.min()) if ecc_giant.size else float("inf"),
        float(D[finite].sum()),
        int(finite.sum()),
    )


def _full_universe_worker(payload: tuple) -> tuple:
    """One sharded dijkstra pass -> WF centralities + structure scalars.

    Module-level for the fork pool. Returns (clo_wf chunk, harm chunk,
    (shard diameter, shard min giant-comp ecc, finite-pair sum,
    finite-pair count)) — the structure scalars ride the SAME distance
    matrix as the centralities, so the stamp's all-universe addition
    costs one pass, not two (scipy_exact_universe S6). Diagonal:
    excluded from structure sums/counts; INCLUDED in the WF reach count
    r (the (r-1)/(N-1) convention counts self).
    """
    A, lo, hi, comp, giant, n_all = payload
    D = dijkstra(A, directed=False, indices=np.arange(lo, hi), unweighted=True)
    finite = np.isfinite(D)
    D[~finite] = 0.0
    local = np.arange(hi - lo)
    D[local, np.arange(lo, hi)] = 0.0
    finite[local, np.arange(lo, hi)] = False
    # centralities (diagonal excluded from sums, included in r):
    r = finite.sum(axis=1) + 1  # restore the self count for WF
    sum_d = D.sum(axis=1)
    N = n_all
    clo_wf = np.where(
        sum_d > 0,
        (N - 1) / np.where(sum_d > 0, sum_d, 1.0) * ((r - 1.0) / (N - 1.0)) ** 2,
        0.0,
    )
    harm = np.where(D > 0, 1.0 / np.where(D > 0, D, 1.0), 0.0).sum(axis=1)
    # structure scalars:
    row_max = D.max(axis=1)
    has_reach = finite.any(axis=1)
    ecc = row_max[has_reach]
    giant_rows = (comp[lo:hi] == giant) & has_reach
    ecc_giant = row_max[giant_rows]
    scalars = (
        float(ecc.max()) if ecc.size else 0.0,
        float(ecc_giant.min()) if ecc_giant.size else float("inf"),
        float(D[finite].sum()),
        int(finite.sum()),
    )
    return clo_wf.astype(np.float64), harm.astype(np.float64), scalars


class FullUniverseStats(TypedDict):
    """Return shape of :func:`full_universe_stats`.

    ``closeness``/``harmonic`` are POSITIONAL ARRAYS, not name-keyed dicts —
    they align with ``load_projection``'s name order, which is the contract
    (results row i belongs to contract name i). Callers zip them against
    that name list; the earlier docstring claimed dicts and the
    ``dict[str, object]`` annotation hid the shape from the type checker,
    which is how the stamp lane in ``query.py`` ended up passing ``object`` to
    ``map(float, ...)``.
    """

    closeness: np.ndarray
    harmonic: np.ndarray
    structure: dict[str, float | int]


def full_universe_stats(A: csr_matrix, jobs: int = 4) -> FullUniverseStats:
    """One-pass all-universe lane: WF closeness/harmonic + structure scalars.

    The stamped-default form of S3+S1 (scipy_exact_universe S6): a single
    sharded dijkstra from EVERY node yields
    ``{"closeness": <array>, "harmonic": <array>, "structure": {...}}``
    where closeness is Wasserman-Faust scaled (full-reach rows keep the
    raw contract values), harmonic is raw stamp semantics, and structure
    carries diameter / radius (giant-component convention) / APL /
    component counts. Both arrays are positional and align with
    ``load_projection``'s name order; see :class:`FullUniverseStats`.
    """
    from scipy.sparse.csgraph import connected_components

    n = A.shape[0]
    if n == 0:
        return {
            "closeness": np.empty(0, dtype=np.float64),
            "harmonic": np.empty(0, dtype=np.float64),
            "structure": {
                "diameter": 0,
                "radius": 0,
                "avg_path_length": 0.0,
                "reachable_pairs": 0,
                "components": 0,
                "largest_component": 0,
            },
        }
    n_comp, comp = connected_components(A, directed=False)
    sizes = np.bincount(comp, minlength=n_comp)
    giant = int(sizes.argmax())
    bounds = [
        (i * n // jobs, (i + 1) * n // jobs)
        for i in range(jobs)
        if i * n // jobs < (i + 1) * n // jobs
    ]
    parts = fork_map(
        _full_universe_worker,
        [(A, lo, hi, comp, giant, n) for lo, hi in bounds],
        jobs,
    )
    clo = np.concatenate([p[0] for p in parts])
    harm = np.concatenate([p[1] for p in parts])
    diameter = max(p[2][0] for p in parts)
    radius_vals = [p[2][1] for p in parts if p[2][3] > 0]
    radius = min(radius_vals) if radius_vals else 0.0
    total_sum = sum(p[2][2] for p in parts)
    total_cnt = sum(p[2][3] for p in parts)
    return {
        "closeness": clo,
        "harmonic": harm,
        "structure": {
            "diameter": int(diameter),
            "radius": int(radius),
            "avg_path_length": (total_sum / total_cnt) if total_cnt else 0.0,
            "reachable_pairs": total_cnt,
            "components": int(n_comp),
            "largest_component": int(sizes[giant]),
        },
    }


def structure_stats(A: csr_matrix, jobs: int = 4) -> dict[str, float | int]:
    """Exact diameter / radius / average path length over the whole CSR.

    Unweighted multi-source dijkstra from EVERY node, sharded across the
    house fork pool; workers reduce to scalars so the parent never holds
    an n×n distance matrix (peak ≈ jobs × shard-chunk). APL is the mean
    over reachable ORDERED pairs (accumulated across all components; self
    pairs excluded). Diameter is the global max; RADIUS is the min
    eccentricity over the LARGEST component — the global min is
    degenerate on a disconnected graph (any 2-node isolated pair pins it
    to 1; measured live 2026-09-26).

    Replaces the graph_metrics NULL-on-disconnected serving (the Onager
    component-check skipped all-pairs; 106 live components meant these
    three were never served). Opt-in lane — not in ROUTING, not gated.
    """
    from scipy.sparse.csgraph import connected_components

    n = A.shape[0]
    if n == 0:
        return {
            "diameter": 0,
            "radius": 0,
            "avg_path_length": 0.0,
            "reachable_pairs": 0,
            "components": 0,
            "largest_component": 0,
        }
    n_comp, comp = connected_components(A, directed=False)
    sizes = np.bincount(comp, minlength=n_comp)
    giant = int(sizes.argmax())
    bounds = [
        (i * n // jobs, (i + 1) * n // jobs)
        for i in range(jobs)
        if i * n // jobs < (i + 1) * n // jobs
    ]
    parts = fork_map(_structure_worker, [(A, lo, hi, comp, giant) for lo, hi in bounds], jobs)
    diameter = max(p[0] for p in parts)
    radius_vals = [p[1] for p in parts if p[3] > 0]
    radius = min(radius_vals) if radius_vals else 0.0
    total_sum = sum(p[2] for p in parts)
    total_cnt = sum(p[3] for p in parts)
    return {
        "diameter": int(diameter),
        "radius": int(radius),
        "avg_path_length": (total_sum / total_cnt) if total_cnt else 0.0,
        "reachable_pairs": total_cnt,
        "components": int(n_comp),
        "largest_component": int(sizes[giant]),
    }


def fused_derive(D: np.ndarray, return_counts: bool = False):
    """Closeness + harmonic from one distance matrix (one memory pass).

    - closeness: (N-1) / sum(reachable distances); 0 for unreachable rows
    - harmonic: sum(1/d) over finite positive distances (raw, stamp semantics)

    ``return_counts=True`` additionally returns the per-row finite-reach
    count r (self included) — the Wasserman-Faust factor for the
    all-universe lane: scale = (r-1)/(N-1), 1.0 for full-reach rows, so
    CONTRACT parity is untouched while disconnected tiny-component rows
    stop exploding (a 2-node pair would otherwise score N-1).
    """
    N = D.shape[1]
    F = np.isfinite(D)
    M = np.where(F, D, 0.0)
    sum_d = M.sum(axis=1)
    clo = (N - 1) / np.where(sum_d == 0, np.inf, sum_d)
    harm = np.where(M > 0, 1.0 / np.where(M == 0, np.inf, M), 0.0).sum(axis=1)
    if return_counts:
        return clo, harm, F.sum(axis=1)
    return clo, harm


def _worker(chunk: np.ndarray, A: csr_matrix, return_counts: bool = False):
    D = dijkstra(A, directed=False, indices=chunk, unweighted=True)
    return fused_derive(D, return_counts=return_counts)


def compute(
    A: csr_matrix,
    src: np.ndarray,
    *,
    jobs: int = 1,
    return_counts: bool = False,
):
    """Multi-source dijkstra, optionally fork-split (CoW inherits the CSR).

    Row order of the outputs matches ``src`` order (pool.map preserves it) —
    i.e. contract order. ``jobs=1`` stays in-process (deterministic; the
    default per scipy_graph_bridge §5 — the split is a budget lever).
    ``return_counts`` adds the per-row finite-reach count (Wasserman-Faust
    input for the all-universe lane); default keeps the 2-tuple contract.
    """
    chunks = np.array_split(src, jobs)
    parts = fork_map(partial(_worker, A=A, return_counts=return_counts), chunks, jobs)
    clo = np.concatenate([p[0] for p in parts])
    harm = np.concatenate([p[1] for p in parts])
    if return_counts:
        counts = np.concatenate([p[2] for p in parts])
        return clo, harm, counts
    return clo, harm


def load_centrality_projection(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    exclude_edge_types: tuple[str, ...] = ("listed_on_index",),
    include_edge_types: tuple[str, ...] | None = None,
) -> tuple[csr_matrix, list[str]]:
    """Unweighted ex-index CSR for the exact-solve lanes (Katz/eigenvector).

    Same bridge-id discipline as ``load_projection`` (sorted names =
    deterministic ids; edges both directions; self-loops dropped), PLUS
    the centrality-projection rule (``listed_on_index`` excluded — the
    lanes par Onager, which applies it). Unweighted by measurement:
    weighted solves par worse (r=0.92) than unweighted (r=0.9996).

    ``exclude_edge_types`` extends the baseline exclusion set. The PPR
    lane passes ``("listed_on_index", "listed_in")``: a country node
    routes seed mass to every home-market peer (measured 2026-11-06 —
    HDFC Bank's #2 was ``india``), which is hub noise, not proximity.

    ``include_edge_types`` (when not ``None``) flips to an allow-list:
    only those edge types enter the projection. The PPR lane uses it to
    keep relational structure only — ``co_mentioned_in`` is newsletter
    mention volume, so IT majors hoovered seed mass regardless of the
    seed's sector (measured 2026-11-06: HDFC Bank's post-listed_in
    top-10 was still Tech Mahindra/TCS/HCL/Wipro/Infosys). Entities
    reachable only through excluded types are absent from the
    projection; the PPR seed guard reports them loudly.
    """
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        # _edge_rows projects the retired part_of/has_company pair back out
        # of the canonical sector hyperedges (2026-10-19).
        rows = [(s, t, et) for s, t, et, _w in _edge_rows(con)]
    finally:
        con.close()
    if include_edge_types is not None:
        inc = frozenset(include_edge_types)
        kept = [(s, t) for s, t, et in rows if s != t and et in inc]
    else:
        excl = frozenset(exclude_edge_types)
        kept = [(s, t) for s, t, et in rows if s != t and et not in excl]
    names = sorted({s for s, _ in kept} | {t for _, t in kept})
    pos = {n: i for i, n in enumerate(names)}
    eb = sorted({tuple(sorted((pos[s], pos[t]))) for s, t in kept})
    r = np.fromiter((a for a, _ in eb), dtype=np.int64, count=len(eb))
    c = np.fromiter((b for _, b in eb), dtype=np.int64, count=len(eb))
    both_r = np.concatenate([r, c])
    both_c = np.concatenate([c, r])
    A = csr_matrix(
        (np.ones(both_r.size), (both_r, both_c)),
        shape=(len(names), len(names)),
    )
    return A, names


def spectral_radius(A: csr_matrix) -> float:
    """Largest eigenvalue magnitude (ARPACK, explicit tolerance)."""
    try:
        return float(
            eigsh(
                A.astype(float),
                k=1,
                which="LA",
                tol=EIGSH_TOL,
                return_eigenvectors=False,
            )[0]
        )
    except ArpackNoConvergence as e:
        raise RuntimeError(
            f"eigsh failed to converge at tol={EIGSH_TOL:g}; refusing to "
            "serve a partial spectrum — loosen the tolerance or check for "
            "degenerate structure"
        ) from e


def katz_scores(A: csr_matrix, alpha: float, beta: float = 1.0) -> np.ndarray:
    """Exact Katz ``(I - alpha*A)^-1 beta*1`` (one sparse solve, no iteration).

    Admissibility is guarded, not hoped: alpha at/above
    ``KATZ_RADIUS_MARGIN`` of the spectral radius raises (there Katz is
    undefined — the matrix singular — not merely slow). Near-critical
    solves amplify float noise (condition number grows into 1e2-1e4);
    the values stay exact, the last digits don't.
    """
    if not alpha > 0:
        raise ValueError(f"Katz alpha must be positive, got {alpha}")
    lam = spectral_radius(A)
    if alpha >= KATZ_RADIUS_MARGIN / lam:
        raise ValueError(
            f"Katz alpha={alpha} at/above {KATZ_RADIUS_MARGIN}/lambda_max "
            f"(lambda_max={lam:.4f}, bound={KATZ_RADIUS_MARGIN / lam:.6f}): "
            "the series diverges there — lower alpha or grow the margin"
        )
    M = identity(A.shape[0], format="csc") - alpha * A.tocsc()
    return np.asarray(spsolve(M, beta * np.ones(A.shape[0]))).ravel()


def eigenvector_scores(A: csr_matrix) -> np.ndarray:
    """Dominant eigenvector (ARPACK): L2 unit-norm, sign flipped so the
    dominant node is positive (networkx/Onager contract). On disconnected
    input only the dominant component scores; the rest read ~0.
    Non-convergence raises (never a silent partial vector)."""
    try:
        _vals, vecs = eigsh(A.astype(float), k=1, which="LA", tol=EIGSH_TOL)
    except ArpackNoConvergence as e:
        raise RuntimeError(
            f"eigsh failed to converge at tol={EIGSH_TOL:g}; refusing to "
            "serve a partial eigenvector — loosen the tolerance or check "
            "for degenerate structure"
        ) from e
    v = np.asarray(vecs[:, 0]).ravel()
    v = v / np.linalg.norm(v)
    if v[np.argmax(np.abs(v))] < 0:
        v = -v
    return v


def sector_cohort(
    db_path: str | Path = DEFAULT_DB_PATH,
    seed: str = "",
    *,
    include_edge_types: tuple[str, ...] = PPR_INCLUDE_EDGE_TYPES,
) -> frozenset[str]:
    """Hybrid per-sector scope (operator decision 2026-11-06, proposal §5c).

    Cohort = the seed's ``sector_classification`` cohort (every entity
    sharing the label) **∪** the seed's direct relational neighbours
    (either direction, ``include_edge_types`` only). The direct-neighbour
    union keeps genuinely cross-sector structure the trial surfaced —
    CRISIL → Oracle Financial Services Software (Technology) via the
    exchange-infrastructure supply chain — which a hard sector cut would
    delete. Entities with no sector label only enter via that
    neighbour arm. An unknown seed or a seed with no sector and no
    relational neighbours yields the empty set (the caller raises).
    """
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        row = con.execute(
            "SELECT sector_classification FROM entities WHERE name = ?", (seed,)
        ).fetchone()
        members: set[str] = {seed}
        if row and row[0]:
            members.update(
                r[0]
                for r in con.execute(
                    "SELECT name FROM entities WHERE sector_classification = ?",
                    (row[0],),
                ).fetchall()
            )
        placeholders = ",".join("?" * len(include_edge_types))
        members.update(
            r[0]
            for r in con.execute(
                f"SELECT target FROM graph_edges WHERE source = ? "  # noqa: S608  # placeholders are ?-marks, values bound
                f"AND edge_type IN ({placeholders})",
                (seed, *include_edge_types),
            ).fetchall()
        )
        members.update(
            r[0]
            for r in con.execute(
                f"SELECT source FROM graph_edges WHERE target = ? "  # noqa: S608  # placeholders are ?-marks, values bound
                f"AND edge_type IN ({placeholders})",
                (seed, *include_edge_types),
            ).fetchall()
        )
    finally:
        con.close()
    return frozenset(members)


def personalized_pagerank_scores(
    A: csr_matrix,
    names: list[str],
    seed: str,
    alpha: float = PPR_ALPHA_DEFAULT,
) -> np.ndarray:
    """Exact personalized PageRank ``x = alpha*P^T x + (1-alpha) v``.

    ``P`` is row-stochastic (``P = D^-1 A``); dangling rows teleport to the
    seed. ``v`` is one-hot on ``seed``. Solves ``(I - alpha*P^T) x =
    (1-alpha) v`` with :func:`spsolve` — no iteration, no divergence risk.
    ``x`` sums to 1 when ``P`` is row-stochastic and the system is
    non-singular (alpha in (0, 1)).
    """
    _assert_scipy_lane(PPR_LANE)
    if not 0 < alpha < 1:
        raise ValueError(f"PPR alpha must be in (0, 1), got {alpha!r}")
    pos = {n: i for i, n in enumerate(names)}
    if seed not in pos:
        raise KeyError(f"PPR seed {seed!r} not in the projection")
    n = A.shape[0]
    if n == 0:
        raise ValueError("PPR: empty projection")
    seed_idx = pos[seed]
    t0 = time.perf_counter()
    # Row-stochastic P = D^-1 A: row sums (A is symmetric from
    # load_centrality_projection / load_projection — both directions
    # emitted, self-loops dropped — so row sums == column sums; use row
    # sums for the general D^-1 A form anyway).
    deg = np.asarray(A.sum(axis=1)).ravel().astype(float)
    inv_deg = np.zeros(n, dtype=float)
    nz = deg > 0
    inv_deg[nz] = 1.0 / deg[nz]
    # D^-1 A: scale rows.
    P = csr_matrix(A.astype(float))
    P.data = P.data * np.repeat(inv_deg, np.diff(P.indptr))
    # Dangling rows (deg == 0) teleport to seed: P[dangling, seed] = 1.
    if np.any(~nz):
        P = P.tolil()
        for d in np.flatnonzero(~nz):
            P[d, seed_idx] = 1.0
        P = P.tocsr()
    v = np.zeros(n, dtype=float)
    v[seed_idx] = 1.0
    M = identity(n, format="csc") - alpha * P.T.tocsc()
    x = np.asarray(spsolve(M, (1.0 - alpha) * v)).ravel()
    _assert_budget(PPR_LANE, time.perf_counter() - t0)
    return x


def write_ppr_results(
    conn: Any,
    seed: str,
    payload: dict[str, float],
    *,
    alpha: float = PPR_ALPHA_DEFAULT,
    replace: bool = False,
) -> int:
    """Persist a PPR top-N payload to ``ppr_results`` (seed-keyed UPSERT).

    ``payload`` maps entity -> score (top-N, already sorted/truncated by
    the caller if desired). ``alpha`` is recorded per row so mixed-alpha
    batches stay honest. Returns the number of rows written.

    ``replace=True`` (the canonical-refresh posture) first deletes every
    stored row for ``seed`` inside the same transaction: a projection
    change can drop entities out of the payload entirely, and a pure
    UPSERT would leave their stale scores behind forever (measured
    2026-11-06 — dropping ``listed_in`` stranded per-seed ``india`` rows
    even though no fresh computation could produce them; the same drift
    class ``write_analytics`` S3 solved with ``replace_population``).

    Row-by-row loop, no executemany — same deliberate posture as
    ``algorithms.write_analytics`` (measured slower on the live graph).
    Reruns overwrite the same (seed, entity) slots via the PK, not
    append: a ``--all-ppr-top50`` rerun stays at 5,000 rows, it does
    not grow to 10,000.
    """
    with conn:
        if replace:
            conn.execute("DELETE FROM ppr_results WHERE seed = ?", (seed,))
        for entity, score in payload.items():
            conn.execute(
                "INSERT INTO ppr_results(seed, entity, score, alpha) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(seed, entity) DO UPDATE SET "
                "score = excluded.score, alpha = excluded.alpha, "
                "computed_at = CURRENT_TIMESTAMP "
                "WHERE score != excluded.score OR alpha != excluded.alpha",
                (seed, entity, float(score), float(alpha)),
            )
    return len(payload)


def _assert_scipy_lane(lane: str) -> None:
    """Refuse to run a lane the ROUTING switch does not own.

    The table is the single source of truth (same discipline as
    ``algorithms._scipy_routed``): a lane whose owner is not ``SCIPY``
    must not run here — there is no silent fallback in either direction.
    """
    owner = ROUTING.get(lane)
    if owner != "SCIPY":
        raise RuntimeError(
            f"lane {lane!r} is not routed to SCIPY (ROUTING says {owner!r}); "
            "refusing to run — the ROUTING switch is the single source of truth"
        )


def _assert_budget(lane: str, elapsed: float) -> None:
    """Fail loud when a lane overruns its wall-clock budget.

    The ROUTING switch owns lane TIME as well as ownership: a slow lane
    raises instead of returning (on the CLI path, instead of the caller
    silently absorbing minutes). Budgets are re-baselined only with a
    fresh measurement — see ``LANE_BUDGETS``.
    """
    budget = LANE_BUDGETS[lane]
    if elapsed > budget:
        raise RuntimeError(
            f"{lane} lane exceeded its {budget:.1f}s wall-clock budget "
            f"({elapsed:.2f}s); refusing the slow result — re-baseline "
            "LANE_BUDGETS with a measurement, do not raise it blindly"
        )


def load_capacity_projection(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    unweighted: bool = False,
) -> tuple[csr_array, list[str]]:
    """Directed integer capacity CSR from SQLite ``graph_edges``.

    Undirected edges are modelled as two opposite arcs of equal capacity
    (scipy's ``maximum_flow`` is directed-only); unordered pairs are
    deduped first so a stored reverse duplicate does not double the
    capacity. Capacities are ``max(1, round(weight))`` (integer-only, a
    scipy requirement), or 1 in ``unweighted`` mode (edge-disjoint count).
    """
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        # _edge_rows projects the retired part_of/has_company pair back out
        # of the canonical sector hyperedges (2026-10-19), so a
        # sector-only company stays reachable for maxflow too.
        rows = [(s, t, w) for s, t, _et, w in _edge_rows(con)]
    finally:
        con.close()
    names = sorted({s for s, _, _ in rows} | {t for _, t, _ in rows})
    pos = {n: i for i, n in enumerate(names)}
    caps: dict[tuple[int, int], int] = {}
    for s, t, w in rows:
        if s == t:
            continue
        key = (min(pos[s], pos[t]), max(pos[s], pos[t]))
        cap = 1 if unweighted else max(1, int(round(float(w if w is not None else 1.0))))
        caps[key] = max(caps.get(key, 0), cap)
    r: list[int] = []
    c: list[int] = []
    v: list[int] = []
    for (a, b), cap in caps.items():
        r.extend((a, b))
        c.extend((b, a))
        v.extend((cap, cap))
    A = csr_array(
        (np.array(v, dtype=np.int64), (np.array(r), np.array(c))),
        shape=(len(names), len(names)),
    )
    return A.tocsr(), names


def yen_paths(
    A: csr_matrix,
    names: list[str],
    source: str,
    sink: str,
    k: int = YEN_K_DEFAULT,
    *,
    directed: bool = False,
    unweighted: bool = True,
) -> list[tuple[float, list[str]]]:
    """K shortest SIMPLE paths (Yen) between two named entities.

    Returns ``[(cost, [name, ...]), ...]`` ranked by cost, first == the
    Dijkstra answer. Pre-flight: ROUTING owns the lane, ``k`` is bounded
    (``YEN_K_MAX`` — cost is ~K Dijkstras), and both endpoints resolve.
    """
    _assert_scipy_lane(YEN_LANE)
    if not isinstance(k, int) or not (1 <= k <= YEN_K_MAX):
        raise ValueError(
            f"Yen k must be an int in [1, {YEN_K_MAX}], got {k!r} — unbounded "
            "K is refused (cost is ~K Dijkstras)"
        )
    pos = {n: i for i, n in enumerate(names)}
    for label, node in (("source", source), ("sink", sink)):
        if node not in pos:
            raise KeyError(f"Yen {label} {node!r} not in the projection")
    if source == sink:
        raise ValueError("Yen source and sink must differ")
    src, dst = pos[source], pos[sink]
    t0 = time.perf_counter()
    dists, preds = csgraph_yen(
        A,
        src,
        dst,
        k,
        directed=directed,
        return_predecessors=True,
        unweighted=unweighted,
    )
    _assert_budget(YEN_LANE, time.perf_counter() - t0)
    out: list[tuple[float, list[str]]] = []
    for i, dist in enumerate(dists):
        seq = [dst]
        cur = dst
        for _ in range(A.shape[0]):
            if cur == src:
                break
            cur = int(preds[i, cur])
            if cur < 0:
                break
            seq.append(cur)
        seq.reverse()
        out.append((float(dist), [names[j] for j in seq]))
    return out


def max_flow(
    cap: csr_array,
    names: list[str],
    source: str,
    sink: str,
) -> tuple[int, list[tuple[str, str, int]]]:
    """Max-flow value + min-cut arc listing between two named entities.

    The cut is the certificate, not a by-product: when no augmenting path
    remains, the nodes still reachable from the source in the RESIDUAL
    graph (capacity − flow) form one side of a minimum cut, and the
    crossing arcs ARE the bottleneck. Returns ``(value, [(u, v, cap), ...])``.
    """
    _assert_scipy_lane(MAXFLOW_LANE)
    pos = {n: i for i, n in enumerate(names)}
    for label, node in (("source", source), ("sink", sink)):
        if node not in pos:
            raise KeyError(f"max-flow {label} {node!r} not in the projection")
    if source == sink:
        raise ValueError("max-flow source and sink must differ")
    src, dst = pos[source], pos[sink]
    t0 = time.perf_counter()
    res = csgraph_maximum_flow(cap, src, dst)
    _assert_budget(MAXFLOW_LANE, time.perf_counter() - t0)
    resid = (cap - res.flow).tocsr()
    resid.eliminate_zeros()
    reachable = {src}
    stack = [src]
    while stack:
        u = stack.pop()
        for v in resid.indices[resid.indptr[u] : resid.indptr[u + 1]]:
            v = int(v)
            if v not in reachable:
                reachable.add(v)
                stack.append(v)
    cap_csr = cap.tocsr()
    cut: list[tuple[str, str, int]] = []
    for u in reachable:
        for idx in range(cap_csr.indptr[u], cap_csr.indptr[u + 1]):
            v = int(cap_csr.indices[idx])
            if v not in reachable and cap_csr.data[idx] > 0:
                cut.append((names[u], names[v], int(cap_csr.data[idx])))
    return int(res.flow_value), sorted(cut)


def _run_yen_lane(args: argparse.Namespace) -> int:
    """CLI: Yen K-shortest between two named entities (read-only)."""
    if not args.source or not args.sink:
        raise SystemExit("yen requires --source and --sink")
    A, names = load_projection(args.db)
    paths = yen_paths(A, names, args.source, args.sink, args.k, directed=args.directed)
    print(f"yen {args.source} -> {args.sink}: {len(paths)} path(s) (k={args.k})")
    for cost, seq in paths:
        print(f"  cost={cost:.0f} len={len(seq)}: {' -> '.join(seq)}")
    return 0


def _run_max_flow_lane(args: argparse.Namespace) -> int:
    """CLI: max-flow value + min-cut between two named entities (read-only)."""
    if not args.source or not args.sink:
        raise SystemExit("max-flow requires --source and --sink")
    cap, names = load_capacity_projection(args.db, unweighted=args.unweighted)
    value, cut = max_flow(cap, names, args.source, args.sink)
    print(f"max-flow {args.source} -> {args.sink}: value={value}")
    print(f"  min-cut: {len(cut)} arc(s)")
    for a, b, c in cut[:50]:
        print(f"    {a} -> {b} (cap {c})")
    if len(cut) > 50:
        print(f"    ... {len(cut) - 50} more")
    return 0


def _run_exact_lane(args: argparse.Namespace, scon) -> int:
    """Katz / eigenvector exact lanes (S2): full-vector solve, contract persist.

    Solves run single-process (nothing to fork-split); ``--jobs`` is
    accepted but ignored by these commands. Contract drift fails loud
    (UPSERT never deletes — skipping would serve stale rows).
    """
    from helpers.graph.algorithms import write_analytics

    t0 = time.perf_counter()
    A, names = load_centrality_projection(args.db)
    pos = {n: i for i, n in enumerate(names)}
    t_build = time.perf_counter() - t0
    if args.command == "katz":
        metric = KATZ_METRIC
        alpha = args.alpha if args.alpha is not None else 1e-4
        vec = katz_scores(A, alpha)
    else:
        metric = EIGEN_METRIC
        vec = eigenvector_scores(A)
    print(
        f"CSR: n={A.shape[0]} nnz={A.nnz} | build {t_build:.2f}s",
        file=sys.stderr,
        flush=True,
    )
    contract = contract_sources(args.db, metric=metric)
    if not contract:
        scon.close()
        raise ValueError(
            f"exact lane: empty {metric} contract; refusing to compute against nothing"
        )
    missing = [c for c in contract if c not in pos]
    if missing:
        scon.close()
        raise KeyError(
            f"contract names missing from the projection: {missing[:5]}... "
            "(rebuild the contract or check edge ingest)"
        )
    payload = {c: float(vec[pos[c]]) for c in contract}
    ranked = sorted(payload.items(), key=lambda kv: (-kv[1], kv[0]))[: args.top]
    print(f"[{metric}] top {min(args.top, len(payload)) if args.top is not None else len(payload)}")
    for name, score in ranked:
        print(f"  {name}: {score:.6f}")
    if args.apply:
        n = write_analytics(metric, payload, conn=scon)
        print(f"applied {n} rows under {metric!r}")
    else:
        print(f"dry-run: would write {len(payload)} rows under {metric!r}")
    scon.close()
    return 0


def _run_ppr_lane(args: argparse.Namespace, scon) -> int:
    """Personalized PageRank exact lane (seed-conditioned solve).

    Top-N-persisted payload (no contract — a relative metric has no
    population); ``--apply`` UPSERTs the top-N under ``ppr_results``
    (seed-keyed, ppr_results_table §3.1). Single-process solve;
    ``--jobs`` accepted but ignored.
    """
    if not args.seed:
        scon.close()
        raise SystemExit("pagerank-personalized requires --seed")
    alpha = PPR_ALPHA_DEFAULT if args.alpha is None else args.alpha
    t0 = time.perf_counter()
    A, names = load_centrality_projection(
        args.db,
        include_edge_types=PPR_INCLUDE_EDGE_TYPES,
    )
    sector_note = ""
    if getattr(args, "sector", False):
        cohort = sector_cohort(args.db, args.seed)
        keep = [i for i, n in enumerate(names) if n in cohort]
        if not keep or args.seed not in {names[i] for i in keep}:
            scon.close()
            raise KeyError(f"PPR seed {args.seed!r} not in the projection")
        A = A[keep][:, keep]
        names = [names[i] for i in keep]
        sector_note = f" | sector-scope: {len(names)} nodes"
    pos = {n: i for i, n in enumerate(names)}
    t_build = time.perf_counter() - t0
    # personalized_pagerank_scores validates seed/alpha and the budget.
    vec = personalized_pagerank_scores(A, names, args.seed, alpha=alpha)
    print(
        f"CSR: n={A.shape[0]} nnz={A.nnz} | build {t_build:.2f}s{sector_note}",
        file=sys.stderr,
        flush=True,
    )
    full = {
        name: float(vec[pos[name]])
        for name in names
        if np.isfinite(vec[pos[name]]) and vec[pos[name]] > 0
    }
    # Top-N first (proposal §4): --full persists the full projection.
    # args.top=None (argparse default) means "no explicit top" → PPR_TOP_DEFAULT;
    # never slice [:None] (that is the full projection, not a top-N).
    top_n = args.top if getattr(args, "top", None) else PPR_TOP_DEFAULT
    payload = (
        full
        if getattr(args, "full", False)
        else dict(sorted(full.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n])
    )
    ranked = sorted(full.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    print(f"[{PPR_METRIC} seed={args.seed!r} alpha={alpha}]")
    for name, score in ranked:
        print(f"  {name}: {score:.6f}")
    if args.apply:
        n = write_ppr_results(scon, args.seed, payload, alpha=alpha, replace=True)
        print(f"applied {n} rows under {PPR_TABLE!r}")
    else:
        print(f"dry-run: would write {len(payload)} rows under {PPR_TABLE!r}")
    scon.close()
    return 0


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    ap = argparse.ArgumentParser(
        description="scipy bridge: L1a closeness/harmonic + exact Katz/eigenvector + "
        "s-t lanes (yen/max-flow); dry-run by default"
    )
    ap.add_argument(
        "command",
        choices=[
            "closeness-harmonic",
            "katz",
            "eigenvector",
            "yen",
            "max-flow",
            "structure",
            "routing",
            "pagerank-personalized",
        ],
        help="closeness-harmonic = the L1a lane; katz = exact Katz solve "
        "(--alpha, guarded); eigenvector = ARPACK dominant eigenvector "
        "(katz/eigenvector ignore --jobs: single-process solves); "
        "yen = K shortest simple paths (--source/--sink/--k); "
        "max-flow = Dinic value + min-cut (--source/--sink); "
        "structure = exact diameter/radius/APL over every node "
        "(scipy_exact_universe S1, opt-in); "
        "routing = print ROUTING; "
        "pagerank-personalized = exact PPR solve (--seed/--alpha/--top)",
    )
    ap.add_argument(
        "--universe",
        choices=["contract", "all"],
        default="contract",
        help="closeness-harmonic source set: contract (default, the "
        "persisted 1,734-row company surface) or all (every endpoint, "
        "the old full-walk stamp semantics, ~36 s at live scale; "
        "compute-only — --apply is refused, it would fork the contract)",
    )
    ap.add_argument(
        "--metrics",
        choices=["closeness", "harmonic", "both"],
        default="both",
        help="which metrics to derive (both come from one dijkstra pass; "
        "the flag exists for future leg splits)",
    )
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument(
        "--alpha",
        type=float,
        default=None,
        help="Katz alpha (katz command only; refused at/above "
        "0.99/lambda_max — see KATZ_RADIUS_MARGIN); PPR alpha "
        "(pagerank-personalized, must be in (0,1); default "
        f"{PPR_ALPHA_DEFAULT})",
    )
    ap.add_argument(
        "--seed",
        default=None,
        help="pagerank-personalized: seed entity name (required)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="UPSERT results into graph_analytics (or ppr_results for pagerank-personalized) (default: dry-run)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="pagerank-personalized: persist the full projection "
        "instead of the top-N (ppr_results_table §3.1)",
    )
    ap.add_argument(
        "--sector",
        action="store_true",
        help="pagerank-personalized: hybrid per-sector scope — solve on "
        "the seed's sector cohort UNION its direct relational neighbours "
        "(proposal §5c; bounds ego-localization by construction)",
    )
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="fork-split the multi-source dijkstra (1 = in-process; the "
        "perf leg passes --jobs 4 when the budget demands it)",
    )
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--source", default=None, help="s-t lanes: source entity name")
    ap.add_argument("--sink", default=None, help="s-t lanes: sink entity name")
    ap.add_argument(
        "--k",
        type=int,
        default=YEN_K_DEFAULT,
        help=f"Yen K (1..{YEN_K_MAX}; cost is ~K Dijkstras)",
    )
    ap.add_argument(
        "--directed",
        action="store_true",
        help="yen: directed graph (default: undirected)",
    )
    ap.add_argument(
        "--unweighted",
        action="store_true",
        help="max-flow: unit capacities (edge-disjoint count) instead of rounded weights",
    )
    args = ap.parse_args(argv)

    if args.command == "routing":
        print(json.dumps(ROUTING, indent=1))
        return 0
    if args.command == "yen":
        return _run_yen_lane(args)
    if args.command == "max-flow":
        return _run_max_flow_lane(args)

    from helpers.graph.algorithms import write_analytics

    # --apply writes to THIS lane's --db (tests pass a tmp store); never the
    # default research.db implicitly.
    scon = connect(args.db, row_factory=None)
    if args.command in ("katz", "eigenvector"):
        return _run_exact_lane(args, scon)
    if args.command == "pagerank-personalized":
        return _run_ppr_lane(args, scon)
    t0 = time.perf_counter()
    A, names = load_projection(args.db)
    contract = contract_sources(args.db)
    if args.universe == "all":
        if args.apply:
            scon.close()
            raise ValueError(
                "closeness-harmonic --universe all refuses --apply: writing "
                f"all {A.shape[0]} endpoints into the {len(contract)}-row "
                "contract metrics would fork the lane-served universe "
                "(scipy_exact_universe S3). Compute-only by design."
            )
        src = np.arange(A.shape[0], dtype=np.int64)
        src_names = names
    else:
        src = source_positions(contract, names)
        src_names = contract
    t_build = time.perf_counter() - t0
    print(
        f"CSR: n={A.shape[0]} nnz={A.nnz} | sources: {len(src)} ({args.universe}) | build {t_build:.2f}s",
        file=sys.stderr,
        flush=True,
    )

    t0 = time.perf_counter()
    if args.universe == "all":
        # Wasserman-Faust scaling: full-reach rows (factor 1.0) keep the
        # exact contract values; disconnected tiny-component rows stop
        # exploding (a 2-node pair would otherwise score N-1 raw).
        # clo_wf = ((r-1)/(N-1)) * ((r-1)/sum_d), with
        # sum_d = (N-1)/clo_raw  =>  clo_wf = clo_raw * (r-1)^2/(N-1)^2.
        clo, harm, counts = compute(A, src, jobs=args.jobs, return_counts=True)
        clo = clo * ((counts - 1.0) / (A.shape[0] - 1)) ** 2
    else:
        clo, harm = compute(A, src, jobs=args.jobs)
    t_compute = time.perf_counter() - t0
    print(f"compute ({args.jobs}-job): {t_compute:.2f}s", file=sys.stderr, flush=True)

    if args.metrics in ("closeness", "both"):
        clo_map = dict(zip(src_names, clo))
        ranked = sorted(clo_map.items(), key=lambda kv: (-kv[1], kv[0]))[: args.top]
        print(
            f"[{CLOSINESS_METRIC}] top {min(args.top, len(clo_map)) if args.top is not None else len(clo_map)}"
        )
        for name, score in ranked:
            print(f"  {name}: {score:.6f}")
        if args.apply:
            n = write_analytics(
                CLOSINESS_METRIC, {k: float(v) for k, v in clo_map.items()}, conn=scon
            )
            print(f"applied {n} rows under {CLOSINESS_METRIC!r}")
        else:
            note = (
                "compute-only (dry-run, not written)"
                if args.universe == "all"
                else "dry-run: would write"
            )
            print(f"{note} {len(clo_map)} rows under {CLOSINESS_METRIC!r}")
    if args.metrics in ("harmonic", "both"):
        harm_map = dict(zip(src_names, harm))
        ranked = sorted(harm_map.items(), key=lambda kv: (-kv[1], kv[0]))[: args.top]
        print(
            f"[{HARMONIC_METRIC}] top {min(args.top, len(harm_map)) if args.top is not None else len(harm_map)}"
        )
        for name, score in ranked:
            print(f"  {name}: {score:.6f}")
        if args.apply:
            n = write_analytics(
                HARMONIC_METRIC, {k: float(v) for k, v in harm_map.items()}, conn=scon
            )
            print(f"applied {n} rows under {HARMONIC_METRIC!r}")
        else:
            note = (
                "compute-only (dry-run, not written)"
                if args.universe == "all"
                else "dry-run: would write"
            )
            print(f"{note} {len(harm_map)} rows under {HARMONIC_METRIC!r}")
    scon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
