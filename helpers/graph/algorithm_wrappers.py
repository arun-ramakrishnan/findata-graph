#!/usr/bin/env python3
"""Onager-backed algorithm wrappers over the materialised graph cache.

Extracted from :mod:`helpers.graph.query` on 2026-11-08 (hotspot-module
split, shortlist item #8) so the graph-query module stays below the
~5300-line inflection point. These four wrappers are the only public
algorithmic surface of the query layer — :func:`pagerank`,
:func:`pagerank_weighted`, :func:`weakly_connected_components`,
:func:`clustering_coefficient` — plus three private helpers
(:func:`_label_to_edge_type`, :func:`_label_to_table`,
:func:`_company_names`) they alone need.

Why a separate module and not :mod:`helpers.graph.algorithms`?
    algorithms.py is the engine-selection layer: a compute() dispatcher
    + the raw onager_* / networkx_* / igraph_* entry points. The
    wrappers in THIS module filter engine output to company vertices
    only (the duckpgq-era JOIN v_company contract), so the boundary is
    engine -> company-scoped result. Keeping them adjacent but distinct
    makes the engine-side changes free of the company-filter concern.

Circular-import stance:
    helpers.graph.onager does top-level imports of duckdb / igraph /
    helpers.core; it lazy-imports helpers.graph.query._current_generation_for_cache
    inside two functions (lines 67, 374). Safe to import onager at
    module top here. _label_to_edge_type and _label_to_table do read
    the EDGE_REGISTRY dictionaries from query.py, but only when actually
    called (CLI path), so we lazy-import query inside those function
    bodies -- never at module top, never as a default.
"""

from __future__ import annotations

import duckdb

from helpers.graph.graph_fallbacks import _extension_first
from helpers.graph.onager import (
    ECONOMIC_EDGE_TYPES,
    onager_clustering,
    onager_components,
    onager_pagerank,
)


def _label_to_edge_type(label: str) -> str:
    """Map an edge label (e.g. 'BelongsTo') to its graph_edges.edge_type.

    Covers the EDGE_REGISTRY labels plus the two out-of-registry tables
    (hierarchy / exposure) declared outside the EDGE_REGISTRY loop
    separately.
    """
    from helpers.graph import query as _query  # noqa: PLC0415 (lazy: avoid cycle)

    spec = _query.EDGE_REGISTRY_BY_LABEL.get(label)
    if spec is not None:
        return spec["edge_type"]
    if label == "BelongsToHierarchy":
        return "belongs_to"
    if label == "ExposedTo":
        return "exposed_to"
    raise ValueError(f"unknown edge label: {label}")


def _label_to_table(label: str) -> str | None:
    """Map an edge label (e.g. 'BelongsTo') to its table name (e_g: 'e_belongs')."""
    from helpers.graph import query as _query  # noqa: PLC0415 (lazy: avoid cycle)

    for spec in _query.EDGE_REGISTRY.values():
        if spec["label"] == label:
            return spec["table"]
    return None


def _company_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    """The set of company vertex names (replaces the JOIN v_company the
    duckpgq wrappers used to filter sector vertices out of the results)."""
    return {r[0] for r in con.execute("SELECT name FROM v_company").fetchall()}


def pagerank(
    con: duckdb.DuckDBPyConnection,
    edge_label: str = "Economic",
    vertex_label: str = "Entity",
    as_of=None,
) -> list[tuple[str, float]]:
    """PageRank over the graph (Onager-backed).

    Returns a list of (entity_name, pagerank_score) sorted by score desc.
    Only company names are returned (sector vertices are filtered out,
    matching the previous duckpgq-native implementation).

    edge_label is a property-graph label (e.g. 'Economic', 'BelongsTo',
    'CompetesWith'); it is resolved to the underlying graph_edges
    edge_type(s). The default 'Economic' (pagerank_graph_enhancements
    S1) runs over onager.ECONOMIC_EDGE_TYPES -- every non-membership
    type -- so the rank measures economic centrality; 'BelongsTo' (the
    pre-S1 default) stays available as the legacy membership view.
    vertex_label is accepted for signature compatibility and ignored
    (Onager has no property-graph vertex labels).
    """
    if edge_label == "Economic":
        scores = _extension_first(
            onager_pagerank,
            con,
            edge_types=ECONOMIC_EDGE_TYPES,
            weighted=False,
            as_of=as_of,
        )
    else:
        scores = _extension_first(
            onager_pagerank,
            con,
            edge_types=[_label_to_edge_type(edge_label)],
            weighted=False,
            as_of=as_of,
        )
    companies = _company_names(con)
    rows = [(name, float(score)) for name, score in scores.items() if name in companies]
    rows.sort(key=lambda kv: (-kv[1], kv[0]))
    return rows


def pagerank_weighted(
    con: duckdb.DuckDBPyConnection,
    edge_label: str = "Economic",
    vertex_label: str = "Entity",
    as_of=None,
) -> list[tuple[str, float]]:
    """Weighted PageRank over the ECONOMIC projection (S2).

    Same projection and shape as pagerank() but consumes the carried
    edge weights (cited_in n_quotes + 1, invested_in stakes, competes_with
    similarity) instead of unit weights. Persisted under the distinct
    metric name pagerank_weighted so weighted and unweighted outputs can
    be evaluated side by side.
    """
    if edge_label == "Economic":
        scores = _extension_first(
            onager_pagerank,
            con,
            edge_types=ECONOMIC_EDGE_TYPES,
            weighted=True,
            as_of=as_of,
        )
    else:
        scores = _extension_first(
            onager_pagerank,
            con,
            edge_types=[_label_to_edge_type(edge_label)],
            weighted=True,
            as_of=as_of,
        )
    companies = _company_names(con)
    rows = [(name, float(score)) for name, score in scores.items() if name in companies]
    rows.sort(key=lambda kv: (-kv[1], kv[0]))
    return rows


def weakly_connected_components(
    con: duckdb.DuckDBPyConnection,
    edge_label: str = "BelongsTo",
    vertex_label: str = "Entity",
) -> list[tuple[str, int]]:
    """Weakly-connected component labels (Onager-backed).

    Returns a list of (entity_name, component_id) sorted by (component_id,
    name). Only company names are returned (sector vertices are filtered
    out, matching the previous duckpgq-native implementation). Component
    ids are arbitrary labels -- only the partition is meaningful.
    """
    comps = _extension_first(onager_components, con, edge_types=[_label_to_edge_type(edge_label)])
    companies = _company_names(con)
    rows = [(name, int(cid)) for name, cid in comps.items() if name in companies]
    rows.sort(key=lambda kv: (kv[1], kv[0]))
    return rows


def clustering_coefficient(
    con: duckdb.DuckDBPyConnection,
    edge_label: str = "BelongsTo",
    vertex_label: str = "Entity",
) -> list[tuple[str, float]]:
    """Local clustering coefficient per vertex (Onager-backed).

    Returns a list of (entity_name, coefficient) sorted by coefficient desc.
    Only company names are returned (sector vertices are filtered out,
    matching the previous duckpgq-native implementation).
    """
    ccs = _extension_first(onager_clustering, con, edge_types=[_label_to_edge_type(edge_label)])
    companies = _company_names(con)
    rows = [(name, float(cc)) for name, cc in ccs.items() if name in companies]
    rows.sort(key=lambda kv: (-kv[1], kv[0]))
    return rows


__all__ = [
    "clustering_coefficient",
    "pagerank",
    "pagerank_weighted",
    "weakly_connected_components",
]
