#!/usr/bin/env python3
"""Onager-free fallback lanes for the graph analytics surface.

The ``onager`` DuckDB community extension has no duckdb-2.0 build
(``INSTALL onager`` → HTTP 404; see completed.md #427), so every surface
that calls ``helpers.graph.onager.onager_*`` would otherwise abort or
skip wholesale. This module exposes the *same 16 signatures* and
dispatches **onager → fallback** per call:

* onager is tried first whenever it is loadable (the preferred engine —
  its results stay the incumbent numbers);
* when it is absent (or raises ``OnagerUnavailable``) the call is served
  by an in-tree engine instead: ``igraph`` for the bulk-centrality
  families, numpy power iteration for Katz, pure-Python closed forms for
  Laplacian / local-reaching / VoteRank, and set algebra for link
  prediction.

The fallback semantics mirror ``onager.py``'s documented contracts, which
are hand-verified there (5-node star and P5/C6 vectors repeat in
``tests/test_graph_fallbacks.py``): unweighted metrics ignore the weight
column, every metric is computed over the deduplicated UNDIRECTED
projection, and the empty projection returns ``{}``.

Import note: importing ``helpers.graph.onager`` is safe (the extension is
only loaded inside ``_load_onager``); ``helpers.graph.query`` must never
be imported at module level here (circular).
"""

from __future__ import annotations

import contextlib
import duckdb
import heapq
import math
import random
from typing import Any

import igraph as ig
import numpy as np
import scipy.sparse
import scipy.sparse.linalg

from helpers.graph.onager import (
    LP_HUB_DEGREE_CAP,
    PREF_ATTACH_COMPUTE_FLOOR,
    _edge_source_sql,
    _where_inline,
)
from helpers.graph.onager import OnagerUnavailable
from helpers.graph.onager import with_onager_connection as _onager_batch

__all__ = [
    "OnagerUnavailable",
    "with_onager_connection",
    "onager_available",
    "onager_betweenness",
    "onager_clustering",
    "onager_closeness",
    "onager_components",
    "onager_degree",
    "onager_eigenvector",
    "onager_graph_metrics",
    "onager_harmonic",
    "onager_katz",
    "onager_laplacian",
    "onager_link_prediction",
    "onager_local_reaching",
    "onager_louvain",
    "onager_pagerank",
    "onager_voterank",
]

#: Probe result cache: ``None`` = not probed yet, ``True`` = loadable.
_AVAILABLE: bool | None = None

#: Tie-break on equal scores follows insertion order (first-seen node wins),
#: which matches the onager row order (edges are emitted source-first).
_UNDIRECTED = False  # every metric here is computed on the undirected graph

# igraph's community_multilevel reads the process-global RNG and exposes no
# seed, so a degenerate-modularity tie resolves by however much randomness the
# process already consumed — the same edges partitioned differently in the same
# run. Pinning the stream makes the partition (and its canonical labels) a pure
# function of the edge list.
_LOUVAIN_SEED = 20261022


# --------------------------------------------------------------------------- #
# availability probe + projection
# --------------------------------------------------------------------------- #
def onager_available() -> bool:
    """Whether the onager extension can be LOADed in this process.

    Probed once (a fresh in-memory connection); the answer is cached for
    the process. Cheap enough to call per dispatch and lets the fallbacks
    skip a doomed onager attempt entirely (the extension gap is a
    distribution fact, not a per-call accident).

    Resolves through ``onager._load_onager`` (2026-10-24): distribution
    first, then the vendored build (``vendor/onager/`` ->
    ``models/onager/bin/``) — the bare ``LOAD onager`` probe predates the
    vendored lane and kept the whole dispatch family on fallbacks even
    with a loadable artifact on disk.
    """
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            from helpers.graph.onager import (
                OnagerUnavailable,
                _load_onager,
                connect_kwargs,
            )

            probe = duckdb.connect(**connect_kwargs())
            try:
                _load_onager(probe)
                _AVAILABLE = True
            finally:
                probe.close()
        except duckdb.Error, OnagerUnavailable:
            _AVAILABLE = False
    return _AVAILABLE


def _project(
    con: duckdb.DuckDBPyConnection | None,
    edge_types: list[str] | None,
    edges: list[tuple[int, int, float]] | None,
    as_of: str | None = None,
) -> tuple[list[Any], list[tuple[int, int, float]]]:
    """Return ``(labels, edges)`` over the deduplicated undirected projection.

    * ``edges=`` path: node labels are the caller's int ids (onager's
      synthetic contract), weights are taken as given.
    * DB path: labels are entity names, read through the same
      ``_edge_source_sql`` projection (graph_edges UNION projected
      sector-membership) and the same ``_where_inline`` filter onager
      materialises from — so a fallback answers over exactly the edge set
      the extension would have seen.
    * ``con=None`` with no ``edges``: nothing to read → ``([], [])``
      (graceful, never an abort).

    Parallel edges collapse into one edge whose weight is the SUM (the
    simple-graph convention every downstream metric here assumes);
    self-loops are dropped.
    """
    from_db = edges is None
    if edges is None:
        if con is None:
            return [], []
        src = _edge_source_sql(con)
        where = _where_inline(edge_types, as_of)
        # Identifiers/edge types are validated by _where_inline (_IDENT_RE,
        # _DATE_RE) and the source union is module SQL; only literals inline.
        rows = con.execute(
            f"SELECT source, target, weight FROM {src}{where}"  # noqa: S608 — see comment above
        ).fetchall()
        edges = [(r[0], r[1], r[2]) for r in rows if r[0] is not None and r[1] is not None]

    # Onager's ids are ``row_number() OVER (ORDER BY name)`` (see
    # helpers/graph/onager.py::_materialize_from_db), and its metrics break
    # score ties by lowest id — so the DB path must number labels by name to
    # reproduce the same seed order. The ``edges=`` path keeps the caller's
    # int ids in list order (``_materialize_edges`` inserts them verbatim).
    if from_db:
        order: list[Any] = sorted({a for a, _b, _w in edges} | {b for _a, b, _w in edges})
    else:
        order = [a for a, _b, _w in edges] + [b for _a, b, _w in edges]

    idx: dict[Any, int] = {node: i for i, node in enumerate(dict.fromkeys(order))}
    agg: dict[tuple[int, int], float] = {}
    for a, b, weight in edges:
        if a == b:
            continue
        ia = idx[a]
        ib = idx[b]
        if ia == ib:
            continue
        key = (ia, ib) if ia < ib else (ib, ia)
        w = 1.0 if weight is None else float(weight)
        agg[key] = agg.get(key, 0.0) + w
    undirected = [(a, b, w) for (a, b), w in agg.items()]
    return list(idx), undirected


def _graph(
    labels: list[Any], edges: list[tuple[int, int, float]], *, weighted: bool = True
) -> ig.Graph:
    """Undirected igraph over the projected simple graph."""
    g = ig.Graph(n=len(labels), edges=[(a, b) for a, b, _ in edges], directed=_UNDIRECTED)
    if weighted:
        g.es["weight"] = [w for _, _, w in edges]
    return g


def _adjacency(n: int, edges: list[tuple[int, int, float]]) -> list[dict[int, float]]:
    """Weighted adjacency maps over the deduplicated projection."""
    adj: list[dict[int, float]] = [dict() for _ in range(n)]
    for a, b, w in edges:
        adj[a][b] = w
        adj[b][a] = w
    return adj


def _power_vector(
    adj: list[dict[int, float]],
    *,
    alpha: float,
    beta: float,
    max_iter: int = 1000,
    tolerance: float = 1e-6,
    renorm: bool = False,
) -> np.ndarray:
    """``x ← beta + alpha * A x`` power iteration (Katz; eigenvector when
    beta=0), stopped on the mean L1 change. ``renorm`` normalises each
    sweep (eigenvector use: without it an oscillating or growing
    spectrum overflows float64 within the 1000 sweeps — measured on a
    3-cycle); Katz keeps raw scale (its pinned values, e.g. hub 1.0003,
    are the beta+alpha·A·x fixed point)."""
    n = len(adj)
    x = np.full(n, 1.0 / max(n, 1))  # fixed nonzero start: pure function of the edges
    for _ in range(max_iter):
        y = np.full(n, float(beta))
        for i, neighbours in enumerate(adj):
            if neighbours:
                y[i] += alpha * sum(x[j] for j in neighbours)
        if renorm:
            norm = float(np.linalg.norm(y))
            if norm > 0:
                y = y / norm
        delta = float(np.abs(y - x).sum()) / max(n, 1)
        x = y
        if delta < tolerance:
            break
    return x


@contextlib.contextmanager
def with_onager_connection(con: duckdb.DuckDBPyConnection | None = None):
    """Batch scope that works with or without the onager extension.

    With onager loadable this is ``onager.with_onager_connection`` — the
    incumbent behaviour (one shared materialisation, signature-cached
    across the block). Without it the block simply yields the caller's
    connection: the fallback lanes materialise nothing, so the scope is a
    no-op and the CLI never aborts on a missing extension.
    """
    if onager_available():
        with _onager_batch(con) as shared:
            yield shared
        return
    if con is None:
        con = duckdb.connect()
        try:
            yield con
        finally:
            con.close()
        return
    yield con


def _extension_first(fn, *args, **kwargs):
    """Call the extension twin of ``fn`` first, degrade per call (never abort).

    graph_fallbacks and the onager module share names and signatures (this
    module is a drop-in), so ``fn.__name__`` resolves the twin. Lives HERE
    (not algorithms.py) so query.py's production metric paths can use it
    without an import cycle — both modules already import this one.
    Degradation never aborts: OnagerUnavailable (artifact gone mid-process)
    latches the availability probe off, and extension-flavoured duckdb
    errors fall through to ``fn`` while anything else propagates.
    (Supersedes the never-wired ``_dispatch`` — the 735e10e60 lesson:
    a dispatcher with zero callers changes nothing.)
    """
    global _AVAILABLE
    if not onager_available():
        return fn(*args, **kwargs)
    import helpers.graph.onager as _ext_mod

    ext = getattr(_ext_mod, fn.__name__, None)
    if ext is None:
        return fn(*args, **kwargs)
    try:
        return ext(*args, **kwargs)
    except OnagerUnavailable:
        _AVAILABLE = False
    except duckdb.Error as exc:
        if "onager" not in str(exc).lower():
            raise
        _AVAILABLE = False
    return fn(*args, **kwargs)


# --------------------------------------------------------------------------- #
# centrality families
# --------------------------------------------------------------------------- #
def onager_pagerank(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    weighted: bool = True,
    as_of: str | None = None,
    damping: float = 0.85,
) -> dict[Any, float]:
    """PageRank (igraph) → name→score / int→score. Same S2 contract:
    ``weighted=False`` ignores the weight column."""
    labels, proj = _project(con, edge_types, edges, as_of)
    if not labels:
        return {}
    g = _graph(labels, proj, weighted=weighted)
    ranks = g.pagerank(
        directed=_UNDIRECTED,
        damping=damping,
        weights=g.es["weight"] if weighted else None,
    )
    return {label: float(score) for label, score in zip(labels, ranks)}


def onager_components(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, int]:
    """Weakly-connected components (igraph membership, first-seen order)."""
    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return {}
    g = _graph(labels, proj, weighted=False)
    return {
        label: int(c) for label, c in zip(labels, g.connected_components(mode="weak").membership)
    }


def onager_clustering(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Local clustering coefficient, unweighted; degree≤1 nodes → 0.0
    (the networkx convention onager's contract documents)."""
    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return {}
    g = _graph(labels, proj, weighted=False)
    coeffs = g.transitivity_local_undirected(mode="zero", weights=None)
    return {label: float(c) for label, c in zip(labels, coeffs)}


def onager_louvain(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> tuple[dict[Any, int], float]:
    """Louvain communities (igraph multilevel) → (labels, modularity).

    Community ids are canonicalised by first-seen member so repeated runs
    (and the onager path's ``_canonical_relabel``) agree on the numbering.
    """
    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return {}, 0.0
    g = _graph(labels, proj)
    # igraph's multilevel has no seed parameter and reads the process-global
    # RNG, so a degenerate-modularity tie resolves by however much randomness
    # the process has already consumed. Pinning the RNG to a fixed stream makes
    # the partition (and the canonical labels below) a pure function of the edge
    # list, so a stamped table and a live compute agree regardless of what else
    # ran in the process. The RNG is restored so nothing else is disturbed.
    rng = random.Random(_LOUVAIN_SEED)  # noqa: S311 — deterministic seeded partition, not crypto
    ig.set_random_number_generator(rng)
    try:
        clustering = g.community_multilevel(weights=g.es["weight"])
    finally:
        ig.set_random_number_generator(random)
    membership = list(clustering.membership)
    canonical: dict[int, int] = {}
    relabelled = [canonical.setdefault(c, len(canonical)) for c in membership]
    modularity = g.modularity(relabelled, weights=g.es["weight"], directed=_UNDIRECTED)
    return {label: c for label, c in zip(labels, relabelled)}, float(modularity)


def onager_betweenness(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Betweenness (igraph exact Brandes, unweighted).

    Scale matches the incumbent contract rows: igraph's undirected count
    (unordered pairs, counted once) rescaled by ``2/((n-1)(n-2))`` — the
    networkx style the ``betweenness_centrality`` stamp uses.
    """
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n < 3:
        return {label: 0.0 for label in labels}
    g = _graph(labels, proj, weighted=False)
    raw = g.betweenness(directed=_UNDIRECTED, weights=None)
    scale = 2.0 / ((n - 1) * (n - 2))
    return {label: float(v) * scale for label, v in zip(labels, raw)}


def onager_degree(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Undirected degree centrality, normalised by (n - 1).

    Matches ``onager_degree``, which reads the extension's undirected
    ``in_degree`` and divides by ``n - 1`` itself, and therefore NetworkX
    ``degree_centrality`` semantics (values in [0, 1]).
    """
    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return {}
    g = _graph(labels, proj, weighted=False)
    degree = [float(d) for d in g.degree(mode="all", loops=False)]
    n = len(labels)
    if n > 1:
        degree = [d / (n - 1) for d in degree]
    return {label: d for label, d in zip(labels, degree)}


def _principal_vector(adj: list[dict[int, float]]) -> np.ndarray:
    """Deterministic principal eigenvector (unit L2 norm, sign canonicalised) of the WHOLE adjacency.

    scipy ARPACK with a FIXED ``v0`` start is deterministic across calls
    (the randomised default start is what made igraph's eigenvector
    non-reproducible), and unlike a bare power iteration it does not
    oscillate on a bipartite component whose spectrum is symmetric in ±λ.

    Disconnected graphs: this is the GLOBAL dominant vector — the largest
    component dominates and minor components land at ~0. That is the
    onager/scipy_bridge contract (the exact-lane live parity,
    tests/test_scipy_bridge.py::test_exact_lanes_live_parity_vs_fresh,
    compares this lane against ``scipy_bridge.eigenvector_scores`` — a
    global eigsh — and matched in the onager era), NOT a per-component
    normalisation. Do not "fix" minor components to 0.7071-style local
    values; that breaks live parity with r ≈ 0 (measured 2026-10-23).
    """
    n = len(adj)
    if n == 0:
        return np.zeros(0, dtype=float)
    rows, cols, vals = [], [], []
    for i, neighbours in enumerate(adj):
        for j in neighbours:
            rows.append(i)
            cols.append(j)
            vals.append(1.0)
    # Unweighted on purpose: onager's eigenvector drops edge weights and the
    # scipy_bridge exact lane is unweighted by measurement (weighted solves
    # par worse, r=0.92 vs 0.9996 — scipy_bridge.load_centrality_projection).
    # Share-count weights in graph_edges otherwise hijack the spectrum
    # (λ 923 vs 58.7; the principal vector flips to shareholding-table
    # labels — measured 2026-10-23).
    matrix = scipy.sparse.csr_matrix((vals, (rows, cols)), shape=(n, n))
    if n == 1 or matrix.nnz == 0:
        return np.array([1.0] + [0.0] * (n - 1), dtype=float)
    try:
        _vals, vecs = scipy.sparse.linalg.eigsh(matrix, k=1, v0=np.ones(n), which="LA")
    except Exception:  # noqa: BLE001 — deterministic fallback if ARPACK chokes
        dense = np.zeros((n, n), dtype=float)
        for i, neighbours in enumerate(adj):
            for j, w in neighbours.items():
                dense[i][j] = w
        _vals, vecs = np.linalg.eigh(dense)
        vecs = vecs[:, -1:]
    vec = np.asarray(vecs[:, 0], dtype=float)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    if vec.size and vec[int(np.argmax(np.abs(vec)))] < 0:
        vec = -vec
    return vec


def onager_eigenvector(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Eigenvector centrality, unweighted, unit-norm (networkx scale).

    Connected graphs: igraph ARPACK (unique dominant vector; sign
    canonicalised + quantised below). Disconnected graphs: per-component
    deterministic power iteration — on a degenerate spectrum ARPACK's
    random start returns genuinely different vectors per call (not just
    ULP or sign, measured 2026-10-22), so a stamped table and a live
    compute would never compare equal; a fixed-start sweep is a pure
    function of the edge list."""
    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return {}
    adj = _adjacency(len(labels), proj)
    g = _graph(labels, proj, weighted=False)
    if g.is_connected():
        try:
            # igraph 1.0.0 rejects directed=False here ("Invalid mode"); on
            # an undirected Graph the default (directed=True) already
            # computes the undirected fixed point — scale=False keeps the
            # raw vector for the L2 normalisation below.
            values = np.asarray(g.eigenvector_centrality(scale=False, weights=None), dtype=float)
            norm = float(np.linalg.norm(values))
            if norm > 0:
                values = values / norm
            # Eigenvectors are defined only up to sign, and ARPACK's pick
            # differs call to call — canonicalise the same way
            # scipy_bridge.eigenvector_scores does: largest-|value| entry
            # positive.
            if values.size and values[int(np.argmax(np.abs(values)))] < 0:
                values = -values
        except ig.InternalError:
            # ARPACK-style failure (degenerate spectra): the global eigsh
            # below gets the same principal vector.
            values = _principal_vector(adj)
    else:
        # Disconnected: the GLOBAL deterministic eigsh — the largest
        # component dominates, minor components land at ~0. That is the
        # onager/scipy_bridge contract; per-component normalisation
        # breaks the exact-lane live parity (r ≈ 0, measured 2026-10-23).
        values = _principal_vector(adj)
    # igraph's ARPACK start vector is random, so the same graph yields
    # ULP-different vectors across calls (measured: 0.5773502691896258 vs
    # ...56). Quantise below that noise so a stamped table and a live
    # compute compare equal; 12 decimals is far tighter than any consumer.
    return {label: round(float(v), 12) for label, v in zip(labels, values)}


def onager_closeness(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Closeness centrality, unweighted, networkx ``wf_improved`` scale:
    ``(r-1)/sum_d * (r-1)/(n-1)`` over reachable nodes ``r``."""
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n == 0:
        return {}
    g = _graph(labels, proj, weighted=False)
    base = np.asarray(g.closeness(normalized=True, weights=None), dtype=float)
    sizes = [len(members) for members in g.connected_components(mode="weak")]
    reachable = np.array(
        [sizes[c] for c in g.connected_components(mode="weak").membership], dtype=float
    )
    if n > 1:
        base = base * ((reachable - 1.0) / (n - 1.0))
    return {label: float(v) for label, v in zip(labels, base)}


def onager_harmonic(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Harmonic centrality, unweighted: raw ``sum 1/d(v,u)`` (onager's scale —
    unreachable nodes contribute 0, so it stays defined when disconnected)."""
    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return {}
    g = _graph(labels, proj, weighted=False)
    values = g.harmonic_centrality(normalized=False, weights=None)
    return {label: float(v) for label, v in zip(labels, values)}


def onager_katz(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    alpha: float = 0.0001,
    beta: float = 1.0,
    max_iter: int = 1000,
    tolerance: float = 1e-6,
) -> dict[Any, float]:
    """Katz centrality by power iteration (unweighted), raw values.

    ``x <- beta + alpha * A x`` until the L1 change per node falls under
    ``tolerance``. Values are returned RAW (no normalisation): onager's
    ``onager_ctr_katz`` hands back the beta-scaled vector, so with the
    pinned alpha=1e-4 the scores sit just above 1 and rank by degree
    (degree 3 -> 1.0003) — the contract tests/test_integration_graph_
    algorithms.py::test_katz_default_pin_ranking pins. αλmax < 1 holds
    for the default alpha on any real graph.
    """
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n == 0:
        return {}
    adj = _adjacency(n, proj)
    x = _power_vector(
        adj,
        alpha=float(alpha),
        beta=float(beta),
        max_iter=max_iter,
        tolerance=tolerance,
    )
    return {label: float(v) for label, v in zip(labels, x)}


def onager_laplacian(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Laplacian centrality (Qi et al. 2012), unweighted closed form:
    ``X(v) = d(v)^2 + d(v) + 2 * sum_{u in N(v)} d(u)``."""
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n == 0:
        return {}
    adj = _adjacency(n, proj)
    degrees = [len(neighbours) for neighbours in adj]
    return {
        label: float(degrees[i] ** 2 + degrees[i] + 2 * sum(degrees[j] for j in adj[i]))
        for i, label in enumerate(labels)
    }


def onager_local_reaching(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Local reaching centrality as onager actually computes it: the size of
    the 2-hop neighbourhood INCLUDING the node itself (P5 → [3,4,5,4,3])."""
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n == 0:
        return {}
    adj = _adjacency(n, proj)
    scores = {}
    for i, label in enumerate(labels):
        two_hop = {i}
        for j in adj[i]:
            two_hop.add(j)
            two_hop.update(adj[j])
        scores[label] = float(len(two_hop))
    return scores


def onager_voterank(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    num_seeds: int | None = None,
) -> list[Any]:
    """VoteRank seeds (Zhang et al. 2016) in seed order.

    Each node starts with voting ability 1; a round selects the node with
    the highest neighbour-ability sum, zeroes the winner's ability and
    weakens each of its neighbours by ``1/avg_degree`` (floor 0). Stops
    when no positive score remains (star → centre only; P5 → [1, 3]),
    which is onager's documented behaviour. Ties break on first-seen
    order (lowest index).
    """
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n == 0:
        return []
    adj = _adjacency(n, proj)
    degrees = [len(neighbours) for neighbours in adj]
    avg_degree = (sum(degrees) / n) if n else 1.0
    avg_degree = avg_degree or 1.0
    ability = np.ones(n, dtype=float)
    removed = np.zeros(n, dtype=bool)
    seeds: list[Any] = []
    while True:
        scores = np.array(
            [0.0 if removed[i] else sum(ability[j] for j in adj[i]) for i in range(n)],
            dtype=float,
        )
        if not scores.size or scores.max() <= 0.0:
            break
        winner = int(np.argmax(scores))
        seeds.append(labels[winner])
        if num_seeds is not None and len(seeds) >= num_seeds:
            break
        removed[winner] = True
        ability[winner] = 0.0
        for j in adj[winner]:
            ability[j] = max(ability[j] - 1.0 / avg_degree, 0.0)
    return seeds


# --------------------------------------------------------------------------- #
# whole-graph metrics
# --------------------------------------------------------------------------- #
def onager_graph_metrics(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[str, float | int | None]:
    """Whole-graph structural metrics (all UNWEIGHTED, onager's caveat).

    ``diameter``/``radius``/``avg_path_length`` are ``None`` when the
    projection is disconnected (onager returns NULL rather than
    component-wise values); ``{}`` when the projection is empty.
    """
    labels, proj = _project(con, edge_types, edges)
    n = len(labels)
    if n == 0:
        return {}
    g = _graph(labels, proj, weighted=False)
    m = g.ecount()
    density = (2.0 * m / (n * (n - 1))) if n > 1 else 0.0
    membership = g.connected_components(mode="weak").membership
    connected = len(set(membership)) == 1

    if connected and n > 1:
        diameter: float | None = float(g.diameter(directed=_UNDIRECTED, weights=None))
        radius: float | None = float(g.radius(weights=None))
        avg_path: float | None = float(g.average_path_length(directed=_UNDIRECTED, weights=None))
    elif connected:
        diameter = radius = avg_path = 0.0
    else:
        diameter = radius = avg_path = None

    degrees = g.degree(mode="all", loops=False)
    triples = sum(d * (d - 1) / 2.0 for d in degrees)
    transitivity = g.transitivity_undirected(mode="nan")
    if transitivity is None or math.isnan(transitivity):
        transitivity = 0.0
    # global transitivity = 3T / triples → T (igraph has no triangles() helper)
    triangles = float(transitivity) * triples / 3.0
    local = g.transitivity_local_undirected(mode="zero")
    avg_clustering = float(np.mean(local)) if len(local) else 0.0
    assortativity = g.assortativity_degree(directed=_UNDIRECTED)
    if assortativity is None or math.isnan(assortativity):
        assortativity = 0.0

    return {
        "density": float(density),
        "diameter": diameter,
        "radius": radius,
        "avg_path_length": avg_path,
        "transitivity": float(transitivity),
        "triangles": int(round(triangles)),
        "avg_clustering": avg_clustering,
        "assortativity": float(assortativity),
    }


# --------------------------------------------------------------------------- #
# link prediction (set algebra over the projection)
# --------------------------------------------------------------------------- #
_SHARED_METHODS = ("jaccard", "adamic-adar", "common-neighbors", "resource-alloc")


def onager_link_prediction(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    method: str = "jaccard",
    top: int | None = None,
    hub_degree_cap: int | None = LP_HUB_DEGREE_CAP,
    allow_all_pairs: bool = False,
) -> list[tuple[Any, Any, float]]:
    """Rank candidate (missing) edges by neighbourhood similarity.

    Same five methods as ``_LINK_METHODS``. Shared-neighbour methods count
    shared neighbours only through nodes of degree <= ``hub_degree_cap``
    (the same hub cap the SQL 2-hop lane applies); pref-attach is exact
    top-K by degree product. Existing edges never appear; scores <= 0 are
    dropped. Output: ``[(n1, n2, score)]`` sorted score DESC then index,
    with ``n1 < n2`` in first-seen order.
    """
    if method not in _SHARED_METHODS and method != "pref-attach":
        raise ValueError(f"unknown link-prediction method: {method!r}")
    if method == "pref-attach" and not allow_all_pairs and top is None:
        top = PREF_ATTACH_COMPUTE_FLOOR

    labels, proj = _project(con, edge_types, edges)
    if not labels:
        return []
    adj: list[set[int]] = [set() for _ in range(len(labels))]
    for a, b, _w in proj:
        adj[a].add(b)
        adj[b].add(a)
    degrees = [len(neighbours) for neighbours in adj]
    existing = {(a, b) for a, b, _w in proj}
    if method == "pref-attach":
        return _lp_pref_attach(labels, degrees, existing, top)
    return _lp_shared(labels, adj, degrees, existing, method, hub_degree_cap, top)


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _lp_pref_attach(
    labels: list[Any],
    degrees: list[int],
    existing: set[tuple[int, int]],
    top: int | None,
) -> list[tuple[Any, Any, float]]:
    """Exact top-K preferential attachment: degree product, heap bounded."""
    n = len(labels)
    if top is None or top <= 0:
        top = n * (n - 1) // 2
    heap: list[tuple[float, int, int]] = []
    order = sorted(range(n), key=lambda v: (-degrees[v], v))
    dd = [degrees[v] for v in order]
    for i, u in enumerate(order):
        if len(heap) >= top and i + 1 < len(order) and dd[i] * dd[i + 1] < heap[0][0]:
            break
        for j in range(i + 1, len(order)):
            v = order[j]
            score = float(dd[i] * dd[j])
            if len(heap) >= top and score < heap[0][0]:
                break
            if score <= 0 or _pair_key(u, v) in existing:
                continue
            lo, hi = _pair_key(u, v)
            if len(heap) >= top:
                heapq.heappushpop(heap, (score, lo, hi))
            else:
                heapq.heappush(heap, (score, lo, hi))
    ranked = heapq.nlargest(len(heap), heap)
    return [(labels[lo], labels[hi], score) for score, lo, hi in ranked]


def _lp_shared(
    labels: list[Any],
    adj: list[set[int]],
    degrees: list[int],
    existing: set[tuple[int, int]],
    method: str,
    hub_degree_cap: int | None,
    top: int | None,
) -> list[tuple[Any, Any, float]]:
    """Shared-neighbour methods (jaccard / common / adamic-adar / ralloc).

    Same contract as the pair-loop version it replaced: each unordered
    pair is counted (and weight-accumulated) once per shared neighbour
    whose degree passes the hub cap, existing edges and non-positive
    scores are dropped, results order by (score DESC, label). Sparse
    form: with A the symmetric 0/1 adjacency, D the 0/1 hub-mask
    diagonal and W the per-hub weight diagonal (1/log deg adamic-adar,
    1/deg resource-alloc), ``(A @ D @ A)[a, b]`` is the capped shared-
    neighbour count and ``(A @ W @ A)[a, b]`` the weight sum — identical
    pair universe, C-speed instead of the Python pair loop.
    """
    n = len(labels)
    if n < 2:
        return []
    deg = np.asarray(degrees, dtype=np.int64)
    hub_mask = np.ones(n, dtype=np.float64)
    if hub_degree_cap is not None:
        hub_mask[deg > hub_degree_cap] = 0.0
    if method == "adamic-adar":
        wgt = np.where(deg > 1, hub_mask / np.log(np.maximum(deg, 2)), 0.0)
    elif method == "resource-alloc":
        wgt = np.where(deg > 0, hub_mask / np.maximum(deg, 1), 0.0)
    else:
        wgt = hub_mask

    src = np.repeat(np.arange(n, dtype=np.int32), [len(s) for s in adj])
    dst = np.fromiter((b for s in adj for b in s), dtype=np.int32, count=len(src))
    a_mat = scipy.sparse.csr_matrix((np.ones(len(src), dtype=np.float64), (src, dst)), shape=(n, n))
    # ONE complex matmul so count/weight stay positionally aligned: the
    # real part accumulates the capped shared-neighbour count, the
    # imaginary part the per-hub weights (0 where every shared hub is
    # weightless, e.g. degree-1 adamic-adar). Two separate COOs would
    # not align — scipy's matmul emits explicit zeros and the entry
    # lists differ (observed 660 vs 662).
    diag = scipy.sparse.diags(hub_mask + 1j * wgt)
    prod = (a_mat @ diag @ a_mat).tocoo()

    c_r, c_c = prod.row.astype(np.int64), prod.col.astype(np.int64)
    keep = c_r < c_c
    tri = scipy.sparse.triu(a_mat, k=1).tocsr()
    is_edge = np.asarray(tri[c_r[keep], c_c[keep]]).ravel().astype(bool)
    keep[keep] = ~is_edge

    cnt = prod.data.real[keep]
    w = prod.data.imag[keep]
    da, db = deg[c_r[keep]], deg[c_c[keep]]
    if method == "jaccard":
        union = da + db - cnt
        scores = np.divide(cnt, union, out=np.zeros_like(cnt), where=union > 0)
    elif method == "common-neighbors":
        scores = cnt
    else:
        scores = w

    live = scores > 0
    r, c, s = c_r[keep][live], c_c[keep][live], scores[live]
    if method in ("adamic-adar", "resource-alloc"):
        # scipy's matmul accumulation order differs from the sequential
        # hub loop it replaced — ULP-level drift only, but callers pin
        # scores; same quantisation as onager_eigenvector.
        s = np.round(s, 12)
    order = sorted(range(n), key=labels.__getitem__)  # label rank == label order
    rank = np.empty(n, dtype=np.int64)
    for pos, idx in enumerate(order):
        rank[idx] = pos
    by_rank = np.lexsort((rank[c], rank[r], -s))[:top]
    return [(labels[r[i]], labels[c[i]], float(s[i])) for i in by_rank]
