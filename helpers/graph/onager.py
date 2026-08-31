#!/usr/bin/env python3
"""Onager-backed graph algorithms (replaces the NetworkX bridge in
``helpers/graph/algorithms.py`` for eigenvector / closeness / betweenness /
louvain / degree centrality).

Onager is a DuckDB *community* extension (Rust, Apache-2.0) that exposes
native graph-analytics table functions. Its contract is deliberately simple:
every function takes a **plain edge table** ``(src BIGINT, dst BIGINT,
optional weight DOUBLE)`` as a subquery and returns rows. There is no
property-graph model, no vertex/edge label tables, and nothing is persisted
— Onager is pure compute over whatever edge subquery you hand it.

That means Onager needs **no new table format and stores nothing of its own**.
It operates on the *same* ``graph_edges`` data the retired DuckPGQ layer
used (2026-08-14; see the duckpgq_retirement archive), just projected as
``(src, dst, weight)``. Onager reads the edges via a subquery over the SQLite
``graph_edges`` table that is attached to the DuckDB connection as ``fin``.

Because our ``graph_edges.source/target`` columns store **entity names** (not
integer ids), and Onager requires ``src``/``dst`` to be ``BIGINT``, we remap
entity names to deterministic integer ids. For the DB-backed path this remap
is done **entirely in SQL** (a ``row_number()`` over the distinct endpoint
names) which is far faster than pulling every edge into Python and re-inserting
it (DuckDB's ``executemany`` is pathologically slow — ~5s for ~4k rows, versus
~15ms for the SQL remap). Synthetic callers that pass an ``edges=`` list get a
single batched ``VALUES`` insert instead.

Storage summary
---------------
* Onager: no persistent tables. We materialise two temp tables per call:
  ``_onager_int`` (name -> int id) and ``_onager_e`` (remapped edges), inside
  the caller's DuckDB connection.
* duckpgq: RETIRED entirely (Phases A-E, 2026-08-14 — see
  doc/improvements/archive/graph/duckpgq_retirement.txt). There is no property
  graph; ``query.py`` materialises plain ``v_node`` / ``e_*`` tables and all
  pattern queries are plain SQL JOINs. See doc/design/graph_design.txt.
"""
from __future__ import annotations

import re
from typing import Any

import duckdb

# graph_edges is attached to the DuckDB connection as `fin` (SQLite, read-only)
# by connect() in helpers/graph/query.py.
_EDGE_TABLE = "fin.graph_edges"


# --------------------------------------------------------------------------- #
# Connection handling
# --------------------------------------------------------------------------- #
def _prepare(con: duckdb.DuckDBPyConnection | None) -> tuple[duckdb.DuckDBPyConnection, bool]:
    """Return ``(con, owns)``.

    * When ``con`` is ``None`` a fresh in-memory DuckDB connection is created
      and Onager (+ sqlite, for the DB-backed path) is loaded; the caller is
      responsible for closing it (``owns`` is ``True``).
    * When ``con`` is supplied we assume it is a DuckDB connection from
      ``query.connect()`` (which loads sqlite + vss; Onager itself loads
      lazily here). ``LOAD onager`` is idempotent, so synthetic callers that
      build their own connection also work.
    """
    if con is None:
        con = duckdb.connect()
        con.execute("LOAD sqlite;")
        con.execute("LOAD onager;")
        return con, True
    con.execute("LOAD onager;")
    return con, False


# --------------------------------------------------------------------------- #
# Edge materialisation (the name->int remap)
# --------------------------------------------------------------------------- #
def _where(edge_types: list[str] | None) -> tuple[str, list[str]]:
    """Return a ``(WHERE clause, params)`` fragment filtering by edge_type.

    Uses DuckDB *numbered* parameters (``$1``) rather than positional ``?``
    because the fragment is interpolated more than once in the same
    statement (``_materialize_from_db`` filters both the source and target
    endpoint subqueries); numbered parameters may repeat, positional ones
    may not.
    """
    if not edge_types:
        return "", []
    ph = ",".join(f"${i + 1}" for i in range(len(edge_types)))
    return f" WHERE edge_type IN ({ph})", list(edge_types)


# Perf (2026-08-26): duckdb's Python client imports pandas (~0.5s) on the
# FIRST parameterized execute of the process — measured via import-spy, it
# fires inside _materialize_from_db's first con.execute(sql, params). The
# link-predict CLI budget is 2.0s, so that fixed tax matters. Edge-type
# names are simple identifiers; when they pass the strict pattern below we
# inline them as SQL literals instead of binding parameters, which skips
# the pandas-importing binding path entirely. Non-matching names fall back
# to parameter binding (correctness over speed).
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _where_inline(edge_types: list[str] | None) -> str:
    """Literal-inlined variant of :func:`_where` (no param binding).

    Returns "" for no filter. Raises ValueError on anything that does not
    look like a bare snake_case identifier — callers never pass arbitrary
    user text here (CLI --edge-type values are validated upstream), but the
    check makes inlining unconditionally safe.
    """
    if not edge_types:
        return ""
    for t in edge_types:
        if not _IDENT_RE.match(t):
            raise ValueError(
                f"edge type not a bare identifier: {t!r} — refusing to inline")
    lst = ", ".join(f"'{t}'" for t in edge_types)
    return f" WHERE edge_type IN ({lst})"


def _materialize_from_db(con: duckdb.DuckDBPyConnection, edge_types: list[str] | None) -> bool:
    """Build ``_onager_int`` (name -> int id) and ``_onager_e`` (remapped
    edges) directly from ``fin.graph_edges`` using SQL.

    Returns ``True`` if any edges were materialised, ``False`` if the edge
    table is empty.
    """
    # Inlined literals, not bound params: duckdb's python client imports
    # pandas (~0.5s fixed cost) on the first parameterized execute. See
    # _where_inline above.
    where = _where_inline(edge_types)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _onager_int AS
        SELECT name, (row_number() OVER (ORDER BY name) - 1)::BIGINT AS nid
        FROM (
            SELECT source AS name FROM {_EDGE_TABLE}{where}
            UNION
            SELECT target AS name FROM {_EDGE_TABLE}{where}
        );
        """,  # noqa: S608
    )
    # ORDER BY src, dst (maint_full_zero_churn F3): without it the parallel
    # scan hands louvain a different edge order every run, and onager's
    # community detection is order-sensitive — pinning the input order is
    # one half of the determinism fix (the other half is the fixed seed).
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _onager_e AS
        SELECT s.nid AS src, t.nid AS dst,
               COALESCE(TRY_CAST(e.weight AS DOUBLE), 1.0) AS weight
        FROM {_EDGE_TABLE} e
        JOIN _onager_int s ON s.name = e.source
        JOIN _onager_int t ON t.name = e.target{where}
        ORDER BY src, dst;
        """,  # noqa: S608
    )
    row = con.execute("SELECT count(*) FROM _onager_e").fetchone()
    return row is not None and row[0] > 0


def _materialize_edges(con: duckdb.DuckDBPyConnection, edges: list[tuple[int, int, float]]) -> None:
    """Materialise ``_onager_e`` from a Python edge list using a single batched
    ``VALUES`` insert (DuckDB ``executemany`` is far too slow for this).
    """
    con.execute("CREATE OR REPLACE TEMP TABLE _onager_e(src BIGINT, dst BIGINT, weight DOUBLE)")
    if edges:
        flat = [v for e in edges for v in e]
        ph = "(?,?,?)"
        sql = "INSERT INTO _onager_e VALUES " + ",".join([ph] * len(edges))  # noqa: S608
        con.execute(sql, flat)


def _onager_named(
    con: duckdb.DuckDBPyConnection, fn: str, col: str,
    extra: str = "", params: list[Any] | None = None,
) -> dict[str, float]:
    """Run an Onager table function and map integer node_ids back to names.

    ``extra`` appends named-function parameters (e.g. ``", alpha => $1"``)
    bound from ``params`` — Onager's non-table parameters are named-only.
    """
    rows = con.execute(
        f"""
        SELECT i.name, out.{col}
        FROM {fn}((SELECT src, dst, weight FROM _onager_e){extra}) out
        JOIN _onager_int i ON i.nid = out.node_id
        """  # noqa: S608
        , params or [],
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _onager_int(
    con: duckdb.DuckDBPyConnection, fn: str, col: str,
    extra: str = "", params: list[Any] | None = None,
) -> dict[int, float]:
    """Run an Onager table function, returning int node_id -> value."""
    rows = con.execute(
        f"SELECT node_id, {col} FROM {fn}((SELECT src, dst, weight FROM _onager_e){extra})",  # noqa: S608
        params or [],
    ).fetchall()
    return {int(r[0]): r[1] for r in rows}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def onager_eigenvector(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Eigenvector centrality (unweighted) -> name->score or int->score.

    Onager returns the principal eigenvector on an arbitrary scale; we L2-
    normalise it (matching NetworkX ``eigenvector_centrality``'s unit-norm
    contract) and flip the global sign so the dominant node is positive.
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_ctr_eigenvector", "eigenvector")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_ctr_eigenvector", "eigenvector")
    finally:
        if owns:
            con.close()
    norm = (sum(v * v for v in res.values())) ** 0.5
    if norm > 0:
        res = {k: v / norm for k, v in res.items()}
    if res and max(res.values()) < 0:
        res = {k: -v for k, v in res.items()}
    return res


def onager_closeness(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Closeness centrality (unweighted) -> name->score or int->score."""
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_ctr_closeness", "closeness")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_ctr_closeness", "closeness")
    finally:
        if owns:
            con.close()
    return res


def onager_betweenness(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Betweenness centrality (unweighted) -> name->score or int->score."""
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_ctr_betweenness", "betweenness")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_ctr_betweenness", "betweenness")
    finally:
        if owns:
            con.close()
    return res


def onager_degree(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Degree centrality -> name->score or int->score.

    Onager exposes ``onager_ctr_degree`` as ``in_degree`` / ``out_degree``
    columns that are *already undirected* — it treats the edge table as an
    undirected multigraph and deduplicates reverse edges, so ``in_degree ==
    out_degree ==`` undirected degree. We therefore use ``in_degree`` (not the
    sum) and divide by ``(n - 1)`` to match NetworkX ``degree_centrality``
    semantics.
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_ctr_degree", "in_degree")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_ctr_degree", "in_degree")
    finally:
        if owns:
            con.close()
    nodes = set(res.keys())
    n = len(nodes)
    if n > 1:
        res = {k: v / (n - 1) for k, v in res.items()}
    return res


def onager_pagerank(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """PageRank -> name->score or int->score.

    Replaces the duckpgq-native ``pagerank(fin_graph, ...)`` wrapper. The
    score scale differs slightly from duckpgq's (different normalisation);
    the node ranking is preserved (verified on the live graph, 2026-08-14).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_ctr_pagerank", "rank")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_ctr_pagerank", "rank")
    finally:
        if owns:
            con.close()
    return {k: float(v) for k, v in res.items()}


def onager_components(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, int]:
    """Weakly-connected components -> name->component id or int->component id.

    Replaces the duckpgq-native ``weakly_connected_component(fin_graph, ...)``
    wrapper. Component ids are arbitrary labels (as with duckpgq) — only the
    *partition* is meaningful. Verified exact partition parity on the live
    graph (42 = 42 components over the BelongsTo subgraph, 2026-08-14).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_par_components", "component")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_par_components", "component")
    finally:
        if owns:
            con.close()
    return {k: int(v) for k, v in res.items()}


def onager_clustering(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Local clustering coefficient -> name->coefficient or int->coefficient.

    Replaces the duckpgq-native ``local_clustering_coefficient(fin_graph,
    ...)`` wrapper. Verified exact value parity on the live graph (max abs
    diff 0.0 across all nodes, 2026-08-14).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            res = _onager_named(con, "onager_par_clustering", "coefficient")
        else:
            _materialize_edges(con, edges)
            res = _onager_int(con, "onager_par_clustering", "coefficient")
    finally:
        if owns:
            con.close()
    return {k: float(v) for k, v in res.items()}


def _canonical_relabel(labels: dict[Any, int]) -> dict[Any, int]:
    """Renumber communities canonically: descending member count, ties
    broken by the smallest member. Onager's raw community ids follow node
    iteration order, so a rebuild of the same graph permutes them — the
    maint_full_zero_churn F3 audit saw all 1,293 labels change under a
    bit-identical modularity. Canonical numbering makes the labels a pure
    function of the partition (members may be names or int node ids; a
    graph never mixes them)."""
    groups: dict[int, list[Any]] = {}
    for member, cid in labels.items():
        groups.setdefault(cid, []).append(member)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), min(kv[1])))
    remap = {old: new for new, (old, _) in enumerate(ordered)}
    return {member: remap[cid] for member, cid in labels.items()}


def onager_louvain(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> tuple[dict[Any, int], float]:
    """Louvain community detection -> (labels, modularity).

    ``labels`` is name->community (or int->community when ``edges`` is
    given), canonically renumbered (see ``_canonical_relabel``) so the
    same partition always yields the same labels regardless of node
    iteration order. The detection itself is seeded (``seed => 42``) —
    without a seed onager's louvain is non-deterministic run-to-run
    (observed modularity 0.3286–0.3322 and community counts 21–24 on the
    same graph), which churned all louvain_community rows on every
    recompute. Modularity is computed in Python from the edge set
    (Onager does not return a modularity scalar from
    ``onager_cmm_louvain``).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}, 0.0
            rows = con.execute(
                """
                SELECT out.node_id, i.name, out.community
                FROM onager_cmm_louvain(
                    (SELECT src, dst, weight FROM _onager_e), seed => 42) out
                JOIN _onager_int i ON i.nid = out.node_id
                """
            ).fetchall()
            labels_name = {r[1]: int(r[2]) for r in rows}
            labels_int = {int(r[0]): int(r[2]) for r in rows}
            edges_int = [
                (int(r[0]), int(r[1]), float(r[2]))
                for r in con.execute("SELECT src, dst, weight FROM _onager_e").fetchall()
            ]
        else:
            _materialize_edges(con, edges)
            rows = con.execute(
                "SELECT node_id, community FROM onager_cmm_louvain("
                "(SELECT src, dst, weight FROM _onager_e), seed => 42)"
            ).fetchall()
            labels_name = {int(r[0]): int(r[1]) for r in rows}
            labels_int = labels_name
            edges_int = edges
    finally:
        if owns:
            con.close()
    return _canonical_relabel(labels_name), _modularity(edges_int, labels_int, None)


# --------------------------------------------------------------------------- #
# Link prediction (Phase 1, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
# Onager's link-prediction table functions share the same (src, dst, weight)
# edge contract and return (node1, node2, score) — one row per unordered
# node pair, canonical node1 < node2 (verified live 2026-08-14). All five
# reward shared neighbourhood structure; only preferential-attachment is
# degree-only (deg(u) * deg(v)).
_LINK_METHODS: dict[str, tuple[str, str]] = {
    "jaccard": ("onager_lnk_jaccard", "coefficient"),
    "adamic-adar": ("onager_lnk_adamic_adar", "score"),
    "common-neighbors": ("onager_lnk_common_neighbors", "count"),
    "pref-attach": ("onager_lnk_pref_attach", "score"),
    "resource-alloc": ("onager_lnk_resource_alloc", "score"),
}

# Default projection for candidate-edge prediction: the NON-membership edge
# types (proposal risk #1 — predicting over membership/hierarchy edges is
# trivially sector co-occurrence). Callers override with edge_types=[...].
DEFAULT_PREDICTION_EDGE_TYPES = [
    "co_mentioned_in", "jv_with", "competes_with", "same_group",
]


def onager_link_prediction(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    method: str = "jaccard",
    top: int | None = None,
) -> list[tuple[Any, Any, float]]:
    """Rank candidate (missing) edges by neighbourhood similarity.

    Returns ``[(name1, name2, score), ...]`` (int-keyed when ``edges`` is
    given) sorted by score descending, ties broken by ascending node order
    for determinism. This is a *missing-edge* hypothesis list, not a
    similarity ranking of existing links: pairs that already have an edge in
    the projected subgraph are excluded (both directions checked), and
    zero-score pairs are dropped (no shared-neighbour signal).

    ``edge_types=None`` (DB path) projects ``DEFAULT_PREDICTION_EDGE_TYPES``
    — the non-membership types — so scores are not dominated by trivial
    sector co-occurrence. ``method`` is one of the ``_LINK_METHODS`` keys;
    ``top`` caps the returned list.

    This function is pure/read-only: persistence lives one layer up
    (``algorithms._persist_link_prediction``, opt-in ``--apply`` in the
    CLI — D13, reversing the 2026-08-14 default-apply answer).
    """
    if method not in _LINK_METHODS:
        raise ValueError(
            f"unknown link-prediction method: {method!r} "
            f"(choose from {sorted(_LINK_METHODS)})"
        )
    fn, col = _LINK_METHODS[method]
    con, owns = _prepare(con)
    try:
        if edges is None:
            types = DEFAULT_PREDICTION_EDGE_TYPES if edge_types is None else edge_types
            if not _materialize_from_db(con, types):
                return []
            endpoints = "i1.name, i2.name"
            name_joins = (
                "JOIN _onager_int i1 ON i1.nid = p.lo "
                "JOIN _onager_int i2 ON i2.nid = p.hi"
            )
        else:
            _materialize_edges(con, edges)
            endpoints = "p.lo, p.hi"
            name_joins = ""
        # Canonicalise pair direction in SQL: onager emits each unordered
        # pair exactly once but in NO guaranteed direction (verified live
        # 2026-08-14 — (hi, lo) on some layouts, (lo, hi) on others), so we
        # normalise with LEAST/GREATEST and DISTINCT rather than filtering.
        # int(top) validated before interpolation; LIMIT cannot bind a ?.
        limit = f" LIMIT {int(top)}" if top is not None else ""
        rows = con.execute(
            f"""
            WITH pairs AS (
                SELECT DISTINCT LEAST(node1, node2) AS lo, GREATEST(node1, node2) AS hi,
                       {col} AS score
                FROM {fn}((SELECT src, dst, weight FROM _onager_e))
            )
            SELECT {endpoints}, p.score
            FROM pairs p
            {name_joins}
            WHERE p.score > 0
              AND NOT EXISTS (
                  SELECT 1 FROM _onager_e ee
                  WHERE (ee.src = p.lo AND ee.dst = p.hi)
                     OR (ee.src = p.hi AND ee.dst = p.lo))
            ORDER BY p.score DESC, p.lo, p.hi
            {limit}
            """  # noqa: S608  # parameterized; interpolated parts are schema-constant identifiers / validated int
        ).fetchall()
    finally:
        if owns:
            con.close()
    return [(r[0], r[1], float(r[2])) for r in rows]


# --------------------------------------------------------------------------- #
# Whole-graph structural metrics (Phase 2, doc/improvements/archive/graph/graph_algos.txt)
# --------------------------------------------------------------------------- #
_GRAPH_METRIC_SQL = """
    WITH ee AS (SELECT src, dst, weight FROM _onager_e)
    SELECT
      (SELECT density           FROM onager_mtr_density((SELECT * FROM ee))),
      (SELECT diameter          FROM onager_mtr_diameter((SELECT * FROM ee))),
      (SELECT radius            FROM onager_mtr_radius((SELECT * FROM ee))),
      (SELECT avg_path_length   FROM onager_mtr_avg_path_length((SELECT * FROM ee))),
      (SELECT transitivity      FROM onager_mtr_transitivity((SELECT * FROM ee))),
      (SELECT avg_clustering    FROM onager_mtr_avg_clustering((SELECT * FROM ee))),
      (SELECT assortativity     FROM onager_mtr_assortativity((SELECT * FROM ee))),
      (SELECT coalesce(sum(triangles), 0)
                             FROM onager_mtr_triangles((SELECT * FROM ee)))
"""


def onager_graph_metrics(
    con: duckdb.DuckDBPyConnection | None = None,
    edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[str, float | int | None]:
    """Whole-graph structural metrics in one round-trip.

    Returns a dict with (all UNWEIGHTED — the documented Onager caveat;
    weights are ignored, verified live 2026-08-14):

    * ``density`` — 2m / (n(n-1)) over the deduplicated undirected simple
      graph; n counts edge *endpoints* only (isolated entities have no
      edges and therefore never appear).
    * ``diameter`` / ``radius`` / ``avg_path_length`` — hop distances.
      ``None`` when the projected graph is DISCONNECTED (Onager returns
      NULL rather than component-wise or infinite values) or empty.
    * ``transitivity`` — 3 x triangles / connected triples.
    * ``triangles`` — UNIQUE triangle count (the per-node sum counts each
      triangle three times; we divide).
    * ``avg_clustering`` — mean local clustering (degree<=1 nodes count
      as 0, not excluded).
    * ``assortativity`` — Pearson degree correlation across edges; 0.0 on
      regular graphs (undefined 0/0 collapsed to 0).

    Returns ``{}`` when the projected edge set is empty.
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
        else:
            if not edges:
                return {}
            _materialize_edges(con, edges)
        row = con.execute(_GRAPH_METRIC_SQL).fetchone()
    finally:
        if owns:
            con.close()
    if row is None:
        return {}
    density, diameter, radius, apl, transitivity, aclustering, assort, tri_sum = row
    return {
        "density": float(density) if density is not None else None,
        "diameter": int(diameter) if diameter is not None else None,
        "radius": int(radius) if radius is not None else None,
        "avg_path_length": float(apl) if apl is not None else None,
        "transitivity": float(transitivity) if transitivity is not None else None,
        "triangles": int(tri_sum) // 3,
        "avg_clustering": float(aclustering) if aclustering is not None else None,
        "assortativity": float(assort) if assort is not None else None,
    }


def _modularity(
    edges: list[tuple[int, int, float]], labels: dict[Any, int],
    id_to_name: dict[int, str] | None,
) -> float:
    """Standard modularity Q for an undirected weighted graph.

    ``labels`` must be keyed by the *integer node id* of each edge endpoint.
    For the DB-backed path ``labels_int`` is int-keyed; for the synthetic path
    the labels are already int-keyed and ``id_to_name`` is ``None``.
    """
    if not edges:
        return 0.0
    total = sum(w for _, _, w in edges)
    if total == 0.0:
        return 0.0
    two_m = 2.0 * total
    deg: dict[int, float] = {}
    for s, t, w in edges:
        deg[s] = deg.get(s, 0.0) + w
        deg[t] = deg.get(t, 0.0) + w

    q = 0.0
    for s, t, w in edges:
        if labels[s] == labels[t]:
            q += w - (deg[s] * deg[t]) / two_m
    return q / two_m


# --------------------------------------------------------------------------- #
# Extra centralities (Phase 3, doc/improvements/archive/graph/graph_algos.txt)
#
# NOTE: onager_ctr_personalized_pagerank from the same family is
# deliberately NOT wrapped: its 4-column edge projection's personalization
# column is ignored entirely (any positive values give byte-identical
# output), the restart node is hardcoded to node_id 1, and it raises
# "Personalization node 1 not found in graph" on any projection without a
# node id 1. Verified 2026-08-14 on synthetic graphs; revisit if a later
# onager build wires the personalization column through.
# --------------------------------------------------------------------------- #
def onager_harmonic(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Harmonic centrality (unweighted) -> name->score or int->score.

    score(v) = sum over reachable u != v of 1/d(v,u); unreachable nodes
    contribute 0, so harmonic (unlike closeness) stays well-defined on
    disconnected graphs. Hand-verified on a 5-node star (center 4.0,
    leaves 2.5) and P5 ([25/12, 17/6, 3.5, 17/6, 25/12]).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            return _onager_named(con, "onager_ctr_harmonic", "harmonic")
        _materialize_edges(con, edges)
        return _onager_int(con, "onager_ctr_harmonic", "harmonic")
    finally:
        if owns:
            con.close()


def onager_katz(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    alpha: float = 0.0001, beta: float = 1.0,
) -> dict[Any, float]:
    """Katz centrality -> name->score or int->score.

    ``alpha`` is PINNED to 1e-4 (``beta`` 1.0) for a reason: Katz
    converges iff alpha < 1/lambda_max(A), and Onager's default
    alpha=0.1 DIVERGES on the live graph ("Convergence failed after 100
    iterations", 2026-08-14 — max degree 89 pushes lambda_max past 10).
    1e-4 keeps a ~100x margin below 1/d_max; the node ranking is stable
    across alpha in [1e-4, 1e-2]. Hand-verified on a 5-node star with
    alpha=0.1 (center 1.4583 = 1 + 4a/(1-4a^2)... exact star solution).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            return _onager_named(
                con, "onager_ctr_katz", "katz",
                extra=", alpha => $1, beta => $2", params=[alpha, beta],
            )
        _materialize_edges(con, edges)
        return _onager_int(
            con, "onager_ctr_katz", "katz",
            extra=", alpha => $1, beta => $2", params=[alpha, beta],
        )
    finally:
        if owns:
            con.close()


def onager_laplacian(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Laplacian centrality (Qi et al. 2012, unweighted) -> name->score or
    int->score: X(v) = d(v)^2 + d(v) + 2*sum_{u in N(v)} d(u) — the drop in
    Laplacian graph energy when v is removed. Hand-verified on a 5-node
    star (center 28, leaves 10) and P5 ([6, 12, 14, 12, 6]).
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            return _onager_named(con, "onager_ctr_laplacian", "centrality")
        _materialize_edges(con, edges)
        return _onager_int(con, "onager_ctr_laplacian", "centrality")
    finally:
        if owns:
            con.close()


def onager_local_reaching(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
) -> dict[Any, float]:
    """Local reaching centrality -> name->score or int->score.

    Empirically (2026-08-14, verified on star/P5/C6): the size of the
    2-hop neighbourhood including the node itself — NOT networkx's
    average-reachable-fraction definition. P5 -> [3, 4, 5, 4, 3]; C6 ->
    all 5.0; 5-node star -> all 5.0.
    """
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return {}
            return _onager_named(con, "onager_ctr_local_reaching", "centrality")
        _materialize_edges(con, edges)
        return _onager_int(con, "onager_ctr_local_reaching", "centrality")
    finally:
        if owns:
            con.close()


def onager_voterank(
    con: duckdb.DuckDBPyConnection | None = None, edge_types: list[str] | None = None,
    edges: list[tuple[int, int, float]] | None = None,
    num_seeds: int | None = None,
) -> list[Any]:
    """VoteRank seed set -> ordered list of names (DB path) or node ids
    (edges path). VoteRank's output IS the ranking: one column (node_id),
    rows in seed order — do NOT re-sort. The list stops when no remaining
    node has a positive vote score (star -> only the center; P5 ->
    [1, 3]). ``num_seeds`` optionally caps it (Onager named param).
    """
    extra = ", num_seeds => $1" if num_seeds is not None else ""
    params: list[Any] = [num_seeds] if num_seeds is not None else []
    con, owns = _prepare(con)
    try:
        if edges is None:
            if not _materialize_from_db(con, edge_types):
                return []
            rows = con.execute(
                f"""
                SELECT i.name
                FROM onager_ctr_voterank(
                    (SELECT src, dst, weight FROM _onager_e){extra}) v
                JOIN _onager_int i ON i.nid = v.node_id
                """  # noqa: S608
                , params,
            ).fetchall()
            return [r[0] for r in rows]
        _materialize_edges(con, edges)
        rows = con.execute(
            f"SELECT node_id FROM onager_ctr_voterank((SELECT src, dst, weight FROM _onager_e){extra})",  # noqa: S608
            params,
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        if owns:
            con.close()