#!/usr/bin/env python3
"""Graph algorithms for the FinData knowledge graph.

**Architecture (post-duckpgq-retirement, 2026-08-14):** duckpgq was fully
retired (Phases A-E, doc/improvements/archive/graph/duckpgq_retirement.txt) —
it was the sole reason DuckDB was pinned to 1.5.4. Everything now runs on
plain SQL (pattern queries as JOINs over the materialised e_* tables in
``helpers/graph/query.py``; shortest_path as a level-by-level BFS over the
materialised e_all_und adjacency — sql_capability_unlocks B2) plus the
Onager community extension (Apache-2.0) for all graph algorithms.
NetworkX was retired earlier (2026-08-14 consolidation).

| Metric                         | Engine        |
|--------------------------------|---------------|
| pagerank                       | Onager        |
| weakly_connected_component     | Onager        |
| local_clustering_coefficient   | Onager        |
| shortest_path (single label)   | BFS over e_all_und |
| louvain_community              | Onager        |
| betweenness_centrality         | Onager        |
| degree_centrality             | Onager        |
| closeness_centrality           | Onager        |
| eigenvector_centrality         | Onager        |

Onager needs no property graph and stores nothing of its own: it reads the
``graph_edges`` table, projected as plain ``(src, dst, weight)`` integer
edge ids. See helpers/graph/onager.py and doc/graph_design.txt.

The query wrappers live in ``helpers/graph/query.py`` (plain SQL) and
``helpers/graph/onager.py`` (Onager). This module hosts:
  - A unified ``compute(metric, con=...)`` dispatcher that picks the engine.
  - ``write_analytics()`` — persist results to ``graph_analytics`` (UPSERT).
  - CLI: ``python3 helpers/graph/algorithms.py {degree|pagerank|betweenness|louvain|wcc|clustering|closeness|eigenvector} [--apply]``,
    plus the ``link-predict`` command (Phase 1 of
    doc/improvements/archive/graph/graph_algos.txt — candidate missing-edge
    hypotheses; persisted to ``graph_analytics`` as per-node candidate
    lists under the ``link_prediction`` metric). Writes are opt-in
    ``--apply`` for every metric (D13); dry-run by default.

Conventions (mirrors helpers/graph/query.py):
  - SQLite (memory/research.db) is the sole source of truth.
  - All SQLite access goes through ``helpers.core.db.connect()``.
  - Results are recompute-on-demand, never persisted as Python objects.
  - Results written to ``graph_analytics`` (PRIMARY KEY metric, entity_name)
    via UPSERT.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any, NamedTuple

# Make `from helpers.* import ...` work when this file is run directly as a
# script (`python3 helpers/graph/algorithms.py ...`) — pytest already puts
# the repo root on sys.path, but a bare `python3 <script>` does not.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402
# Metric wrappers from query.py — Onager-backed since Phase A of the
# duckpgq-retirement proposal (formerly duckpgq-native):
from helpers.graph.query import (  # noqa: E402
    _current_generation_for_cache,
    _query_cache_get,
    _query_cache_set,
    clustering_coefficient as _graph_clustering,
    connect as duckdb_connect,
    pagerank as _graph_pagerank,
    weakly_connected_components as _graph_wcc,
)
# Onager-backed metrics (replaces the NetworkX bridge):
from helpers.graph.onager import (  # noqa: E402
    onager_betweenness,
    onager_clustering,
    onager_closeness,
    onager_components,
    onager_degree,
    onager_eigenvector,
    onager_graph_metrics,
    onager_harmonic,
    onager_katz,
    onager_laplacian,
    onager_link_prediction,
    onager_local_reaching,
    onager_voterank,
    onager_louvain,
    onager_pagerank,
    DEFAULT_PREDICTION_EDGE_TYPES,
)

PROJECT_ROOT = _PROJECT_ROOT
DB_PATH = PROJECT_ROOT / "memory" / "research.db"


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #
class LouvainResult(NamedTuple):
    """Result of Louvain community detection.

    ``labels`` maps entity_name -> community id (int). ``modularity`` is the
    graph-level modularity score of that partition.
    """

    labels: dict[str, int]
    modularity: float


# --------------------------------------------------------------------------- #
# Onager-backed metric functions (replace the old NetworkX bridge)
# --------------------------------------------------------------------------- #
def louvain_communities(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> LouvainResult:
    """Louvain community detection via Onager.

    Returns a ``LouvainResult(labels name->community_id, modularity float)``.
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        labels, modularity = onager_louvain(con, edges=edges)
    finally:
        if own:
            con.close()
    return LouvainResult(labels=labels, modularity=modularity)


def compute_louvain_modularity(con: Any | None = None) -> float:
    """Return just the Louvain modularity score for the whole graph."""
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_louvain(con)[1]
    finally:
        if own:
            con.close()


def eigenvector_centrality(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> dict[str, float]:
    """Eigenvector centrality (unweighted) -> {entity_name: score}."""
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_eigenvector(con, edges=edges)
    finally:
        if own:
            con.close()


def closeness_centrality(
    con: Any | None = None,
    approximate: bool | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[str, float]:
    """Closeness centrality (unweighted) -> {entity_name: score}.

    ``approximate`` is accepted for API compatibility but ignored: Onager
    computes exact closeness (it is fast enough at our scale).
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_closeness(con, edges=edges)
    finally:
        if own:
            con.close()


def betweenness_centrality(
    con: Any | None = None,
    top_k: int | None = None,
    approximate: bool | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[str, float]:
    """Betweenness centrality (unweighted) -> {entity_name: score}.

    ``approximate`` is accepted for API compatibility but ignored: Onager
    computes exact betweenness efficiently. ``top_k`` returns only the
    ``top_k`` highest-scoring nodes.
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        bc = onager_betweenness(con, edges=edges)
    finally:
        if own:
            con.close()
    if top_k is not None and top_k > 0:
        return dict(sorted(bc.items(), key=lambda kv: kv[1], reverse=True)[:top_k])
    return bc


def degree_centrality(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> dict[str, float]:
    """Degree centrality -> {entity_name: score}, normalised by (n-1)."""
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_degree(con, edges=edges)
    finally:
        if own:
            con.close()


# --------------------------------------------------------------------------- #
# Extra centralities (Phase 3, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
def harmonic_centrality(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> dict[str, float]:
    """Harmonic centrality (unweighted) -> {entity_name: score}."""
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_harmonic(con, edges=edges)
    finally:
        if own:
            con.close()


def katz_centrality(
    con: Any | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    alpha: float = 0.0001,
    beta: float = 1.0,
) -> dict[str, float]:
    """Katz centrality -> {entity_name: score}.

    ``alpha`` is pinned to 1e-4 by default: Onager's default 0.1 diverges
    on the live graph (see ``onager_katz``). Raise it only for small /
    known-spectral-radius projections.
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_katz(con, edges=edges, alpha=alpha, beta=beta)
    finally:
        if own:
            con.close()


def laplacian_centrality(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> dict[str, float]:
    """Laplacian centrality (Qi et al., unweighted) -> {entity_name: score}."""
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_laplacian(con, edges=edges)
    finally:
        if own:
            con.close()


def local_reaching_centrality(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> dict[str, float]:
    """Local reaching centrality -> {entity_name: score}.

    Onager's variant: the size of the node's 2-hop neighbourhood
    (incl. itself) — see ``onager_local_reaching``.
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_local_reaching(con, edges=edges)
    finally:
        if own:
            con.close()


def voterank_seeds(
    con: Any | None = None, edges: list[tuple[int, int, float]] | None = None
) -> list[str]:
    """VoteRank seed set -> ordered list of entity names.

    List-valued (like link prediction), so it is NOT in
    ``_METRIC_DISPATCH``; the CLI persists it via ``_persist_voterank``.
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return list(onager_voterank(con, edges=edges))
    finally:
        if own:
            con.close()


# --------------------------------------------------------------------------- #
# Whole-graph structural metrics (Phase 2, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
def graph_metrics(
    con: Any | None = None, edge_types: list[str] | None = None
) -> dict[str, float | int | None]:
    """Whole-graph structural metrics (density, diameter, radius,
    avg_path_length, transitivity, triangles, avg_clustering,
    assortativity) via Onager — one round-trip.

    ``edge_types=None`` projects the FULL edge set (all types). Metrics are
    unweighted; diameter/radius/avg_path_length are ``None`` on a
    disconnected projection; ``triangles`` is the unique-triangle count.
    Returns ``{}`` on an empty edge set. Consumers:
    ``make graph-stats`` and ``/api/graph/stats`` (structure block).

    Result is cached per (generation, edge_types) in the query-result cache
    (P2.3) — the metrics are a pure function of the edge set, which only
    changes when the SQLite source bumps its generation. Computing them is
    ~300ms (two temp-table materialisations + a 3-query metric run); the
    cache makes repeat calls (every ``/api/graph/stats`` request, repeated
    ``make graph-stats`` runs) near-free. ``clear_graph_cache()`` (invoked
    on every rebuild/refresh) evicts the entry, so the first call after a
    data change recomputes.
    """
    try:
        gen = _current_generation_for_cache()
    except Exception:  # noqa: S110  # best-effort; absence of generation just disables the cache
        gen = None
    key = ("graph_metrics", tuple(edge_types or []), gen)
    cached = _query_cache_get(key)
    if cached is not None:
        return cached
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        result = onager_graph_metrics(con, edge_types=edge_types)
    finally:
        if own:
            con.close()
    _query_cache_set(key, result)
    return result


# --------------------------------------------------------------------------- #
# Link prediction (Phase 1, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
def link_prediction(
    con: Any | None = None,
    edge_types: list[str] | None = None,
    *,
    method: str = "jaccard",
    top: int | None = None,
) -> list[tuple[str, str, float]]:
    """Rank candidate (missing) edges by neighbourhood similarity.

    Returns ``[(entity_a, entity_b, score), ...]`` sorted by score
    descending. Pairs already connected in the projected subgraph are
    excluded — this is a hypothesis list of *missing* typed edges (the H1
    extraction backlog), most useful for the symmetric types
    (competes_with / jv_with / same_group).

    ``edge_types=None`` projects the non-membership types
    (co_mentioned_in, jv_with, competes_with, same_group) so predictions
    are not dominated by trivial sector co-occurrence (proposal risk #1).
    ``method``: jaccard | adamic-adar | common-neighbors | pref-attach |
    resource-alloc.

    Deliberately NOT in ``_METRIC_DISPATCH``: that registry serves the
    node-keyed ``{entity_name: value}`` contract; link prediction is
    pair-valued, so persistence (opt-in ``--apply``, D13) is done by the
    CLI via ``_persist_link_prediction``,
    which stores per-node candidate lists under the ``link_prediction``
    metric. This function itself stays pure/read-only.
    """
    own = False
    if con is None:
        con = duckdb_connect()
        own = True
    try:
        return onager_link_prediction(
            con, edge_types=edge_types, method=method, top=top
        )
    finally:
        if own:
            con.close()


def _persist_link_prediction(
    pairs: list[tuple[str, str, float]],
    method: str,
    edge_types: list[str] | None,
    conn: Any | None = None,
) -> int:
    """Persist link-prediction pairs to ``graph_analytics`` (UPSERT).

    The table is node-keyed (PK metric, entity_name), so each predicted pair
    fans out to BOTH endpoints: every node gets a row under the
    ``link_prediction`` metric whose JSON value carries the provenance
    (method + edge_type projection) and that node's candidate list::

        {"method": "jaccard", "edge_types": ["co_mentioned_in", ...],
         "candidates": [{"name": "<other endpoint>", "score": 1.0}, ...]}

    Recomputing with a different method/projection replaces the previous
    rows wholesale (UPSERT on the same metric name), so there is exactly one
    authoritative prediction set at any time. Nodes with no candidates get
    no row. Returns the number of entity rows written.
    """
    projection = list(edge_types) if edge_types is not None else list(DEFAULT_PREDICTION_EDGE_TYPES)
    values: dict[str, dict[str, Any]] = {}

    def _node_row(name: str) -> dict[str, Any]:
        return values.setdefault(
            name, {"method": method, "edge_types": projection, "candidates": []}
        )

    for a, b, score in pairs:
        _node_row(a)["candidates"].append({"name": b, "score": score})
        _node_row(b)["candidates"].append({"name": a, "score": score})
    if not values:
        return 0
    return write_analytics("link_prediction", values, conn=conn)


def _persist_voterank(seeds: list[str], conn: Any | None = None) -> int:
    """Persist the VoteRank seed set to ``graph_analytics`` (UPSERT).

    VoteRank is graph-valued (one ordered seed list for the whole
    projection), not node-keyed, so — mirroring link_prediction — every
    seed node gets a row under the ``voterank`` metric carrying the same
    provenance payload::

        {"seeds": ["First Seed", "Second Seed", ...]}

    Recomputing replaces the previous rows wholesale. Returns the number
    of entity rows written.
    """
    if not seeds:
        return 0
    values = {name: {"seeds": list(seeds)} for name in seeds}
    return write_analytics("voterank", values, conn=conn)


# --------------------------------------------------------------------------- #
# Unified dispatcher
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Metric dispatch
# --------------------------------------------------------------------------- #
# Each handler receives the full parameter set and ignores what it does not use
# via the ``**_`` catch-all, keeping ``compute`` free of a long branch chain.
def _resolve_approx(approximate: bool | None, default: bool) -> bool:
    return default if approximate is None else bool(approximate)


def _run_pagerank(con, *, edges, edge_label, vertex_label, **_) -> dict[str, Any]:
    if edges is not None:
        return onager_pagerank(con, edges=edges)
    return {n: s for n, s in _graph_pagerank(con, edge_label=edge_label, vertex_label=vertex_label)}


def _run_wcc(con, *, edges, edge_label, vertex_label, **_) -> dict[str, Any]:
    if edges is not None:
        return onager_components(con, edges=edges)
    return {n: c for n, c in _graph_wcc(con, edge_label=edge_label, vertex_label=vertex_label)}


def _run_clustering(con, *, edges, edge_label, vertex_label, **_) -> dict[str, Any]:
    if edges is not None:
        return onager_clustering(con, edges=edges)
    return {n: c for n, c in _graph_clustering(con, edge_label=edge_label, vertex_label=vertex_label)}


def _run_louvain(con, *, edges, **_) -> dict[str, Any]:
    return louvain_communities(con, edges=edges).labels


def _run_betweenness(con, *, edges, top_k, approximate, **_) -> dict[str, Any]:
    return betweenness_centrality(
        con, top_k=top_k, approximate=_resolve_approx(approximate, True), edges=edges
    )


def _run_degree(con, *, edges, **_) -> dict[str, Any]:
    return degree_centrality(con, edges=edges)


def _run_closeness(con, *, edges, approximate, **_) -> dict[str, Any]:
    return closeness_centrality(
        con, approximate=_resolve_approx(approximate, False), edges=edges
    )


def _run_eigenvector(con, *, edges, **_) -> dict[str, Any]:
    return eigenvector_centrality(con, edges=edges)


def _run_harmonic(con, *, edges, **_) -> dict[str, Any]:
    return harmonic_centrality(con, edges=edges)


def _run_katz(con, *, edges, **_) -> dict[str, Any]:
    return katz_centrality(con, edges=edges)


def _run_laplacian(con, *, edges, **_) -> dict[str, Any]:
    return laplacian_centrality(con, edges=edges)


def _run_local_reaching(con, *, edges, **_) -> dict[str, Any]:
    return local_reaching_centrality(con, edges=edges)


_METRIC_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "pagerank": _run_pagerank,
    "weakly_connected_component": _run_wcc,
    "local_clustering_coefficient": _run_clustering,
    "louvain_community": _run_louvain,
    "betweenness_centrality": _run_betweenness,
    "degree_centrality": _run_degree,
    "closeness_centrality": _run_closeness,
    "eigenvector_centrality": _run_eigenvector,
    "harmonic_centrality": _run_harmonic,
    "katz_centrality": _run_katz,
    "laplacian_centrality": _run_laplacian,
    "local_reaching_centrality": _run_local_reaching,
}


def compute(
    metric: str,
    con: Any | None = None,
    *,
    edges: list[tuple[int, int, float]] | None = None,
    edge_label: str = "BelongsTo",
    vertex_label: str = "Entity",
    top_k: int | None = None,
    approximate: bool | None = None,
) -> dict[str, Any]:
    """Compute a single graph metric and return {entity_name: value}.

    All metrics are computed directly from the DuckDB connection via
    Onager — no in-memory NetworkX graph is built. ``con`` is a DuckDB
    connection from ``helpers.graph.query.connect()``; if omitted, a fresh one
    is opened and closed.

    Parameters
    ----------
    metric : one of pagerank, weakly_connected_component,
        local_clustering_coefficient, louvain_community, betweenness_centrality,
        degree_centrality, closeness_centrality, eigenvector_centrality,
        harmonic_centrality, katz_centrality, laplacian_centrality,
        local_reaching_centrality.
    edge_label : graph label (BelongsTo, CompetesWith, ...) resolved to an
        edge_type for the pagerank/wcc/clustering metrics.
    top_k : optional cap on returned nodes (betweenness_centrality).
    approximate : accepted for API compatibility; ignored by Onager except
        where noted (closeness defaults to False, betweenness to True).
    """
    own = con is None
    if own:
        con = duckdb_connect()
    try:
        handler = _METRIC_DISPATCH.get(metric)
        if handler is None:
            raise ValueError(f"unknown metric: {metric!r}")
        return handler(
            con,
            edges=edges,
            edge_label=edge_label,
            vertex_label=vertex_label,
            top_k=top_k,
            approximate=approximate,
        )
    finally:
        if own:
            con.close()


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def write_analytics(
    metric: str, values: dict[str, Any], conn: Any | None = None
) -> int:
    """Persist a metric result to ``graph_analytics`` (UPSERT).

    ``values`` maps entity_name -> value (value is json-serialised). Returns
    the number of rows written.

    Note on the row-by-row loop: audited for executemany() conversion but
    measured slower on the live graph. Left as-is deliberately.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()
    try:
        with conn:
            for entity_name, value in values.items():
                conn.execute(
                    "INSERT INTO graph_analytics(entity_name, metric, value) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(metric, entity_name) DO UPDATE SET value = excluded.value",
                    (entity_name, metric, json.dumps(value)),
                )
    finally:
        if own_conn:
            conn.close()
    return len(values)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
_METRIC_TO_ANALYTICS_NAME = {
    "degree": "degree_centrality",
    "pagerank": "pagerank",
    "betweenness": "betweenness_centrality",
    "louvain": "louvain_community",
    "wcc": "weakly_connected_component",
    "clustering": "local_clustering_coefficient",
    "closeness": "closeness_centrality",
    "eigenvector": "eigenvector_centrality",
    "harmonic": "harmonic_centrality",
    "katz": "katz_centrality",
    "laplacian": "laplacian_centrality",
    "local-reaching": "local_reaching_centrality",
}


def _format_value(v: float | str) -> str:
    return f"{v:.6f}" if isinstance(v, float) else str(v)


def _print_result(
    cmd: str,
    result: dict[str, Any],
    top: int | None,
    *,
    modularity: float | None = None,
) -> None:
    """Pretty-print a metric result. Inverts component-id for wcc."""
    if cmd == "wcc":
        buckets: dict[int, int] = {}
        for cid in result.values():
            buckets[cid] = buckets.get(cid, 0) + 1
        print(f"weakly connected components: {len(buckets)}")
        for cid, size in sorted(buckets.items(), key=lambda kv: -kv[1])[: max(top or 10, 10)]:
            print(f"  component {cid}: {size} nodes")
        return
    if cmd == "louvain":
        buckets2: dict[int, int] = {}
        for label in result.values():
            buckets2[label] = buckets2.get(label, 0) + 1
        print(f"communities: {len(buckets2)}")
        if modularity is not None:
            print(f"modularity: {modularity:.6f}")
        for label, size in sorted(buckets2.items(), key=lambda kv: -kv[1])[: max(top or 10, 10)]:
            print(f"  community {label}: {size} nodes")
        return
    ranked = sorted(result.items(), key=lambda kv: kv[1], reverse=True)
    limit = top if top is not None else 10
    for name, value in ranked[:limit]:
        print(f"  {name:40} {_format_value(value)}")
    if top is None:
        print(f"({len(result)} total)", file=sys.stderr)


def _wrap_for_analytics(
    cmd: str,
    result: dict[str, Any],
    *,
    modularity: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Wrap a metric result into the JSON shape expected by graph_analytics."""
    if cmd == "louvain":
        if modularity is not None:
            return {
                node: {"community": label, "modularity": modularity}
                for node, label in result.items()
            }
        return {node: {"community": label} for node, label in result.items()}
    if cmd == "wcc":
        return {node: {"componentId": cid} for node, cid in result.items()}
    return {node: {"value": v} for node, v in result.items()}


def _cli(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(description="FinData graph-algorithm CLI")
    p.add_argument(
        "cmd",
        nargs="?",
        choices=["degree", "pagerank", "betweenness", "louvain", "wcc",
                 "clustering", "closeness", "eigenvector", "harmonic",
                 "katz", "laplacian", "local-reaching", "link-predict",
                 "voterank"],
        help="Algorithm to run. Omit when using --all.",
    )
    p.add_argument("--top", type=int, default=None, help="Show/print only top-N nodes.")
    p.add_argument(
        "--method",
        choices=["jaccard", "adamic-adar", "common-neighbors", "pref-attach",
                 "resource-alloc"],
        default="jaccard",
        help="Link-prediction similarity measure (link-predict command).",
    )
    p.add_argument(
        "--edge-types",
        default=None,
        help="Comma-separated edge_type projection for link-predict (default: "
             "co_mentioned_in,jv_with,competes_with,same_group — the "
             "non-membership types).",
    )
    p.add_argument(
        "--edge-label",
        default="BelongsTo",
        help="edge label (default: BelongsTo). Used by pagerank/wcc/clustering.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Run all metrics (degree, pagerank, betweenness, louvain, wcc, "
             "clustering, closeness, eigenvector, harmonic, katz, laplacian, "
             "local-reaching) plus link-predict and voterank.",
    )
    write_flags = p.add_mutually_exclusive_group()
    write_flags.add_argument(
        "--apply",
        action="store_true",
        help="Persist results to graph_analytics (UPSERT). Opt-in for EVERY "
             "metric, link-predict and voterank included (D13); the default "
             "is dry-run. `make recompute-graph` = --all --apply.",
    )
    write_flags.add_argument(
        "--no-apply",
        action="store_true",
        help="Explicitly skip writes (the default; kept for explicitness).",
    )
    p.add_argument(
        "--exact-betweenness",
        action="store_true",
        help="Retained for CLI compatibility. Onager computes exact "
             "betweenness efficiently, so this flag is a no-op.",
    )
    args = p.parse_args(argv)

    if not args.cmd and not args.all:
        p.error("either a command or --all is required")
    if args.all and args.cmd:
        p.error("--all cannot be combined with an explicit command")

    cmd_to_metric = {
        "degree": "degree_centrality",
        "pagerank": "pagerank",
        "betweenness": "betweenness_centrality",
        "louvain": "louvain_community",
        "wcc": "weakly_connected_component",
        "clustering": "local_clustering_coefficient",
        "closeness": "closeness_centrality",
        "eigenvector": "eigenvector_centrality",
        "harmonic": "harmonic_centrality",
        "katz": "katz_centrality",
        "laplacian": "laplacian_centrality",
        "local-reaching": "local_reaching_centrality",
    }
    # --all refreshes everything recompute-graph persists: node-keyed
    # metrics plus the list-valued link-predict and voterank. Writes are
    # uniformly opt-in via --apply (D13); the default is dry-run.
    commands = [*cmd_to_metric.keys(), "link-predict", "voterank"] if args.all else [args.cmd]

    # Open one DuckDB connection for all metrics in this run. Every metric
    # is Onager-backed (post-duckpgq-retirement); connect() loads sqlite/vss
    # and onager.py loads onager per call.
    duck_con = duckdb_connect()
    try:
        pending_writes: list[tuple[str, dict[str, Any]]] = []
        for cmd in commands:
            if cmd == "link-predict":
                print(f"\nlink-predict (method={args.method}):")
                edge_types = (
                    [t.strip() for t in args.edge_types.split(",") if t.strip()]
                    if args.edge_types else None
                )
                try:
                    # Full ranked list: --top caps the DISPLAY, persistence
                    # keeps every positive-score candidate.
                    pairs = link_prediction(duck_con, edge_types=edge_types, method=args.method)
                except Exception as e:
                    print(f"  FAIL: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
                    continue
                print("  [via onager]", file=sys.stderr)
                limit = args.top if args.top is not None else 20
                shown = pairs[:limit]
                if not shown:
                    print("  (no candidate pairs with a positive score)")
                for a, b, s in shown:
                    print(f"  {a:36} {b:36} {s:.6f}")
                if len(pairs) > len(shown):
                    print(f"  ... ({len(pairs)} candidate pairs total, "
                          f"showing {len(shown)})", file=sys.stderr)
                if not args.apply:
                    print("  [dry-run: nothing written to graph_analytics "
                          "(pass --apply to persist)]", file=sys.stderr)
                else:
                    n_rows = _persist_link_prediction(pairs, args.method, edge_types)
                    print(f"  applied link_prediction: {n_rows} entity rows to "
                          f"graph_analytics", file=sys.stderr)
                continue
            if cmd == "voterank":
                print("\nvoterank (seed set, in seed order):")
                try:
                    seeds = voterank_seeds(duck_con)
                except Exception as e:
                    print(f"  FAIL: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
                    continue
                if not seeds:
                    print("  (no seeds)")
                else:
                    for i, name in enumerate(seeds, 1):
                        print(f"  {i:2}. {name}")
                if not args.apply:
                    print("  [dry-run: nothing written to graph_analytics "
                          "(pass --apply to persist)]", file=sys.stderr)
                else:
                    n_rows = _persist_voterank(seeds)
                    print(f"  applied voterank: {n_rows} entity rows to "
                          f"graph_analytics", file=sys.stderr)
                continue
            metric = cmd_to_metric[cmd]
            print(f"\n{cmd} (metric={metric}):")
            louvain_modularity: float | None = None
            if cmd == "louvain":
                louvain_modularity = louvain_communities(duck_con).modularity
            try:
                result = compute(
                    metric,
                    con=duck_con,
                    edge_label=args.edge_label,
                    top_k=args.top,
                )
                print("  [via onager]", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
                continue
            _print_result(cmd, result, args.top, modularity=louvain_modularity)

            # Every metric states its write status (D13 made dry-run the
            # default for ALL commands; silent dry-runs read as omissions).
            if not args.apply:
                print("  [dry-run: nothing written to graph_analytics "
                      "(pass --apply to persist)]", file=sys.stderr)
                continue
            metric_db = _METRIC_TO_ANALYTICS_NAME.get(cmd)
            if metric_db is None:
                continue
            payload = _wrap_for_analytics(
                cmd, result, modularity=louvain_modularity
            )
            pending_writes.append((metric_db, payload))

        if args.apply:
            if not pending_writes:
                print("nothing to apply", file=sys.stderr)
            else:
                total = 0
                for metric_db, payload in pending_writes:
                    total += write_analytics(metric_db, payload)
                print(
                    f"applied {len(pending_writes)} metric(s), {total} rows to graph_analytics",
                    file=sys.stderr,
                )
    finally:
        duck_con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
