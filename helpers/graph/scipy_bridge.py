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
SQLite ``graph_edges`` is the sole source of truth; entity names sorted
for deterministic dense ids; unweighted (weights present on the edges,
unused by this lane).

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
"""

from __future__ import annotations

import argparse
import json
from functools import partial
import sys
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix, identity
from scipy.sparse.csgraph import dijkstra
from scipy.sparse.linalg import ArpackNoConvergence, eigsh, spsolve

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402  # needs the shim above
from helpers.core.forkmap import fork_map  # noqa: E402  # needs the shim above

DEFAULT_DB_PATH = _PROJECT_ROOT / "memory" / "research.db"
CLOSINESS_METRIC = "closeness_centrality"
HARMONIC_METRIC = "harmonic_centrality"
KATZ_METRIC = "katz_centrality"
EIGEN_METRIC = "eigenvector_centrality"
#: Explicit ARPACK tolerance (the lane never inherits a default).
EIGSH_TOL = 1e-10
#: Katz admissibility margin: refuse alpha at/above this fraction of the
#: spectral radius (at the radius Katz is undefined, not slow).
KATZ_RADIUS_MARGIN = 0.99


#: Lane ownership — Onager keeps the healthy SQL lanes; scipy owns the
#: restricted-source BFS family (this module); betweenness is the L1b
#: 2-core fold (helpers/graph/l1_betweenness.py — no Brandes in scipy).
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
    """
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        rows = con.execute("SELECT source, target FROM graph_edges").fetchall()
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
    """The persisted company contract, in query order (row i <-> contract[i])."""
    con = connect(db_path, read_only=True, row_factory=None)
    try:
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


def fused_derive(D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Closeness + harmonic from one distance matrix (one memory pass).

    - closeness: (N-1) / sum(reachable distances); 0 for unreachable rows
    - harmonic: sum(1/d) over finite positive distances (raw, stamp semantics)
    """
    N = D.shape[1]
    F = np.isfinite(D)
    M = np.where(F, D, 0.0)
    sum_d = M.sum(axis=1)
    clo = (N - 1) / np.where(sum_d == 0, np.inf, sum_d)
    harm = np.where(M > 0, 1.0 / np.where(M == 0, np.inf, M), 0.0).sum(axis=1)
    return clo, harm


def _worker(chunk: np.ndarray, A: csr_matrix) -> tuple[np.ndarray, np.ndarray]:
    D = dijkstra(A, directed=False, indices=chunk, unweighted=True)
    return fused_derive(D)


def compute(
    A: csr_matrix,
    src: np.ndarray,
    *,
    jobs: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Multi-source dijkstra, optionally fork-split (CoW inherits the CSR).

    Row order of the outputs matches ``src`` order (pool.map preserves it) —
    i.e. contract order. ``jobs=1`` stays in-process (deterministic; the
    default per scipy_graph_bridge §5 — the split is a budget lever).
    """
    chunks = np.array_split(src, jobs)
    parts = fork_map(partial(_worker, A=A), chunks, jobs)
    clo = np.concatenate([p[0] for p in parts])
    harm = np.concatenate([p[1] for p in parts])
    return clo, harm


def load_centrality_projection(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[csr_matrix, list[str]]:
    """Unweighted ex-index CSR for the exact-solve lanes (Katz/eigenvector).

    Same bridge-id discipline as ``load_projection`` (sorted names =
    deterministic ids; edges both directions; self-loops dropped), PLUS
    the centrality-projection rule (``listed_on_index`` excluded — the
    lanes par Onager, which applies it). Unweighted by measurement:
    weighted solves par worse (r=0.92) than unweighted (r=0.9996).
    """
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        rows = con.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
    finally:
        con.close()
    kept = [(s, t) for s, t, et in rows if s != t and et != "listed_on_index"]
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
            eigsh(A.astype(float), k=1, which="LA", tol=EIGSH_TOL, return_eigenvectors=False)[0]
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
        vec = katz_scores(A, args.alpha)
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
    ranked = sorted(payload.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
    print(f"[{metric}] top {min(args.top, len(payload))}")
    for name, score in ranked:
        print(f"  {name}: {score:.6f}")
    if args.apply:
        n = write_analytics(metric, payload, conn=scon)
        print(f"applied {n} rows under {metric!r}")
    else:
        print(f"dry-run: would write {len(payload)} rows under {metric!r}")
    scon.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="scipy bridge: L1a closeness/harmonic + exact Katz/eigenvector lanes (dry-run by default)"
    )
    ap.add_argument(
        "command",
        choices=["closeness-harmonic", "katz", "eigenvector", "routing"],
        help="closeness-harmonic = the L1a lane; katz = exact Katz solve "
        "(--alpha, guarded); eigenvector = ARPACK dominant eigenvector "
        "(katz/eigenvector ignore --jobs: single-process solves); "
        "routing = print ROUTING",
    )
    ap.add_argument(
        "--metrics",
        choices=["closeness", "harmonic", "both"],
        default="both",
        help="which metrics to derive (both come from one dijkstra pass; "
        "the flag exists for future leg splits)",
    )
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument(
        "--alpha",
        type=float,
        default=1e-4,
        help="Katz alpha (katz command only; refused at/above "
        "0.99/lambda_max — see KATZ_RADIUS_MARGIN)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="UPSERT results into graph_analytics (default: dry-run)",
    )
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="fork-split the multi-source dijkstra (1 = in-process; the "
        "perf leg passes --jobs 4 when the budget demands it)",
    )
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)

    if args.command == "routing":
        print(json.dumps(ROUTING, indent=1))
        return 0

    from helpers.graph.algorithms import write_analytics

    # --apply writes to THIS lane's --db (tests pass a tmp store); never the
    # default research.db implicitly.
    scon = connect(args.db, row_factory=None)
    if args.command in ("katz", "eigenvector"):
        return _run_exact_lane(args, scon)
    t0 = time.perf_counter()
    A, names = load_projection(args.db)
    contract = contract_sources(args.db)
    src = source_positions(contract, names)
    t_build = time.perf_counter() - t0
    print(
        f"CSR: n={A.shape[0]} nnz={A.nnz} | contract sources: {len(src)} | build {t_build:.2f}s",
        file=sys.stderr,
        flush=True,
    )

    t0 = time.perf_counter()
    clo, harm = compute(A, src, jobs=args.jobs)
    t_compute = time.perf_counter() - t0
    print(f"compute ({args.jobs}-job): {t_compute:.2f}s", file=sys.stderr, flush=True)

    if args.metrics in ("closeness", "both"):
        clo_map = dict(zip(contract, clo))
        ranked = sorted(clo_map.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
        print(f"[{CLOSINESS_METRIC}] top {min(args.top, len(clo_map))}")
        for name, score in ranked:
            print(f"  {name}: {score:.6f}")
        if args.apply:
            n = write_analytics(
                CLOSINESS_METRIC, {k: float(v) for k, v in clo_map.items()}, conn=scon
            )
            print(f"applied {n} rows under {CLOSINESS_METRIC!r}")
        else:
            print(f"dry-run: would write {len(clo_map)} rows under {CLOSINESS_METRIC!r}")
    if args.metrics in ("harmonic", "both"):
        harm_map = dict(zip(contract, harm))
        ranked = sorted(harm_map.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
        print(f"[{HARMONIC_METRIC}] top {min(args.top, len(harm_map))}")
        for name, score in ranked:
            print(f"  {name}: {score:.6f}")
        if args.apply:
            n = write_analytics(
                HARMONIC_METRIC, {k: float(v) for k, v in harm_map.items()}, conn=scon
            )
            print(f"applied {n} rows under {HARMONIC_METRIC!r}")
        else:
            print(f"dry-run: would write {len(harm_map)} rows under {HARMONIC_METRIC!r}")
    scon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
