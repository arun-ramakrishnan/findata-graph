#!/usr/bin/env python3
"""S18(b/c/e) — canonical Arrow incidence loader (architecture.md §10).

The one loader every hypergraph consumer goes through: long-form incidence
(``edge_type, label, node, weight`` — weight nullable) as a pyarrow Table,
straight off whichever at-rest source is authoritative:

- ``source="live"`` — the live store through ``helpers.core.db.connect``
  (DuckDB for the research store since S9, 2026-11-24; SQLite for any
  other ``.db`` path), read into Arrow.
- ``source="snapshot"`` — the zstd parquet snapshots under
  ``snapshots/parquet/sqlite/`` via pyarrow, joined IN FLIGHT with DuckDB
  ``from_arrow`` (no h_* materialization, no SQLite touched).

Consumers (§10 in-flight rule — Arrow until the last boundary):
- ``incidence_query`` — SQL over the incidence table in flight
  (DuckDB registered Arrow; the table never lands in any store).
- ``hypergraph_from_arrow`` — HGX boundary: ``to_pylist()`` exactly once,
  then ``Hypergraph`` construction (hypergraphx is the only tier that
  wants Python objects by design).

The legacy ``load_incidence`` dict (hyper_communities) is a thin wrapper
over this module (same prefixed-label contract, deterministic order).

Usage::

    from helpers.graph.hyper_arrow import load_incidence_arrow

    tbl = load_incidence_arrow(["sector", "sub_sector"])              # live
    tbl = load_incidence_arrow(["edition"], source="snapshot")        # parquet
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import DEFAULT_DB_PATH  # noqa: E402

SNAPSHOT_DIR = PROJECT_ROOT / "snapshots" / "parquet" / "sqlite"
HYPER_EDGES_PARQUET = "hyper_edges.parquet"
HYPER_INCIDENCES_PARQUET = "hyper_incidences.parquet"

_COLUMNS = "he.edge_type, he.label, hi.entity_name AS node, hi.weight"


def _in_clause(sources: list[str]) -> str:
    return "(" + ", ".join("?" for _ in sources) + ")"


def _from_live(sources: list[str], db_path: Path | str) -> pa.Table:
    """Live store through the house door (``helpers.core.db.connect``) → Arrow.

    Post-S6 / S9 (test_coverage_gap_sweep_nov24, 2026-11-24): the
    research store is DuckDB (``memory/research.duckdb``); the old
    SQLite-scanner ATTACH read ``memory/research.db``, which no longer
    exists, so every lane skipped. ``connect`` routes the research store
    and any ``.duckdb`` path to DuckDB and opens any other ``.db`` path
    as SQLite, so a tmp-path SQLite test store keeps working.
    """
    from helpers.core.db import connect as _connect

    conn = _connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} "
            "FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
            f"WHERE he.edge_type IN {_in_clause(sources)} "
            "ORDER BY he.edge_type, he.label, hi.entity_name",
            sources,
        ).fetchall()
    finally:
        conn.close()
    return pa.table(
        {
            "edge_type": pa.array([r[0] for r in rows], pa.string()),
            "label": pa.array([r[1] for r in rows], pa.string()),
            "node": pa.array([r[2] for r in rows], pa.string()),
            "weight": pa.array([r[3] for r in rows], pa.float64()),
        }
    )


def _from_snapshot(sources: list[str], parquet_dir: Path) -> pa.Table:
    """zstd parquet snapshots via pyarrow, joined in flight (zero SQLite)."""
    import duckdb
    import pyarrow.parquet as pq
    from helpers.core.duckdb_shared import memory_cap_config

    edges = pq.read_table(parquet_dir / HYPER_EDGES_PARQUET)
    incs = pq.read_table(parquet_dir / HYPER_INCIDENCES_PARQUET)
    con = duckdb.connect(**memory_cap_config())
    try:
        con.register("e", edges)
        con.register("i", incs)
        rel = con.sql(
            f"SELECT {_COLUMNS} "
            "FROM i AS hi JOIN e AS he ON he.id = hi.edge_id "
            f"WHERE he.edge_type IN {_in_clause(sources)} "
            "ORDER BY he.edge_type, he.label, hi.entity_name",
            params=sources,
        )
        return rel.to_arrow_table()
    finally:
        con.close()


def load_incidence_arrow(
    sources: list[str],
    *,
    source: str = "live",
    db_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
) -> pa.Table:
    """Long-form incidence table: ``edge_type, label, node, weight``.

    ``weight`` is nullable (NULL = unweighted incidence; today only the S8
    edition lane sets quote-count weights). Rows are deterministic:
    ordered by (edge_type, label, node).
    """
    if not sources:
        return pa.table(
            {
                "edge_type": pa.array([], pa.string()),
                "label": pa.array([], pa.string()),
                "node": pa.array([], pa.string()),
                "weight": pa.array([], pa.float64()),
            }
        )
    if source == "live":
        return _from_live(sources, Path(db_path) if db_path else DEFAULT_DB_PATH)
    if source == "snapshot":
        return _from_snapshot(sources, Path(parquet_dir) if parquet_dir else SNAPSHOT_DIR)
    msg = f"source must be 'live' or 'snapshot', got {source!r}"
    raise ValueError(msg)


def store_rowcount(
    sources: list[str],
    *,
    db_path: str | Path | None = None,
) -> int | None:
    """Hyperedge count for ``sources`` in the live store, or None if absent.

    The maint-lane precheck (hyper_lane_wiring W1): a DB without the
    incidence tables has nothing to compute — callers skip cleanly
    instead of dying on the loader's missing-table error. Opens through
    ``helpers.core.db.connect`` like :func:`_from_live` (S9: the store
    is DuckDB now; ``sqlite_master`` does not exist there, the probe
    is the engine-agnostic ``table_exists``). Monkeypatch the module
    attribute ``DEFAULT_DB_PATH`` to redirect both.
    """
    from helpers.core.db import connect as _connect
    from helpers.core.db_engine import table_exists

    if not sources:
        return 0
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    # The research store may live at the ``.duckdb`` sibling of the
    # default path; never let connect() create a store that is absent.
    if not (path.exists() or path.with_suffix(".duckdb").exists()):
        return None
    conn = _connect(path, read_only=True)
    try:
        if not (table_exists(conn, "hyper_edges") and table_exists(conn, "hyper_incidences")):
            return None
        placeholders = ",".join("?" for _ in sources)
        return conn.execute(
            f"SELECT COUNT(*) FROM hyper_edges WHERE edge_type IN ({placeholders})",  # noqa: S608  # fixed schema identifiers; values are ?-parameters
            sources,
        ).fetchone()[0]
    finally:
        conn.close()


def incidence_dict(tbl: pa.Table) -> dict[str, list[str]]:
    """Arrow long-form -> ``{edge_type:label: [members]}`` in row order.

    The lanes' internal working shape (degeneracy filtering, Hy-MMSBM
    edge list); the LOAD contract stays Arrow — this is the sanctioned
    Arrow->dict boundary, not a second loader (S18(b) exit, 2026-09-14).
    """
    out: dict[str, list[str]] = {}
    for et, lbl, node in zip(
        tbl["edge_type"].to_pylist(), tbl["label"].to_pylist(), tbl["node"].to_pylist()
    ):
        out.setdefault(f"{et}:{lbl}", []).append(node)
    return out


def weights_from_arrow(tbl: pa.Table) -> dict[tuple[str, str], float]:
    """Non-null weight column -> ``{(edge_type:label, member): float}``.

    Feeds stationary_pi / compute_centralities (S8 edition quote counts
    today). Keyed identically to incidence_dict labels (S14 exit).
    """
    return {
        (f"{et}:{lbl}", node): float(wt)
        for et, lbl, node, wt in zip(
            tbl["edge_type"].to_pylist(),
            tbl["label"].to_pylist(),
            tbl["node"].to_pylist(),
            tbl["weight"].to_pylist(),
        )
        if wt is not None
    }


def incidence_query(table: pa.Table, sql: str, params: list | None = None) -> pa.Table:
    """SQL over the incidence Arrow table, in flight (nothing lands anywhere).

    The table is registered as ``incidence``; DuckDB's registered-Arrow
    scan reads the caller's buffers zero-copy (§10 in-flight rule).
    """
    import duckdb
    from helpers.core.duckdb_shared import memory_cap_config

    con = duckdb.connect(**memory_cap_config())
    try:
        con.register("incidence", table)
        rel = con.sql(sql, params=params or [])
        return rel.to_arrow_table()
    finally:
        con.close()


CORE_MASS_SQL = """
WITH size AS (
  SELECT edge_type, label, COUNT(*) AS n_members FROM incidence GROUP BY edge_type, label)
SELECT node,
       COUNT(*)             AS n_hyperedges,
       AVG(n_members)        AS mean_edge_size,
       SUM(1.0 / n_members)  AS inverse_size_mass,
       MAX(n_members)        AS max_edge_size
FROM incidence JOIN size USING (edge_type, label)
GROUP BY node ORDER BY node
"""


def core_mass_arrow(table: pa.Table) -> pa.Table:
    """Per-entity core-periphery signal as a plain SQL aggregate.

    ``inverse_size_mass`` sums ``1 / hyperedge size`` over an entity's
    hyperedges (single-member hyperedges count 1 and drop out of the
    sum's signal). Measured 2026-11-24 on the partition family
    (sector, theme, industry; 152 hyperedges, 1,228 nodes): Spearman vs
    the HGX core-periphery ``coreness`` is +0.921, against +0.488 for
    the plain hyperedge count and -0.600 for mean hyperedge size. It
    replaces that lane's 25 s sampler (test_coverage_gap_sweep_nov24
    S10); it is a rank proxy, not a reproduction of the ``coreness``
    values. Keyed per (edge_type, label), so same-named labels in
    different families never merge. Consumes any
    :func:`load_incidence_arrow` table, so it follows the loader's
    source (live or snapshot).
    """
    return incidence_query(table, CORE_MASS_SQL)


SHARED_GROUP_PAIRS_SQL = """
WITH size AS (
  SELECT edge_type, label, COUNT(*) AS n_members FROM incidence GROUP BY edge_type, label),
kept AS (
  SELECT edge_type, label, n_members FROM size WHERE n_members <= ?)
SELECT a.node AS node_a,
       b.node AS node_b,
       COUNT(*)                                    AS shared_hyperedges,
       SUM(1.0 / s.n_members)                      AS shared_inverse_size_mass
FROM incidence a
JOIN incidence b
  ON a.edge_type = b.edge_type AND a.label = b.label AND a.node < b.node
JOIN kept s ON s.edge_type = a.edge_type AND s.label = a.label
GROUP BY a.node, b.node
HAVING COUNT(*) >= ?
ORDER BY a.node, b.node
"""


def shared_group_pairs_arrow(
    table: pa.Table, *, max_edge_members: int = 200, min_shared: int = 1
) -> pa.Table:
    """Pairwise co-membership: each unordered pair of entities and how much
    hyperedge structure they share.

    One row per pair, ``node_a < node_b``, columns:

    - ``shared_hyperedges`` — number of hyperedges both entities sit in
      (keyed per ``(edge_type, label)``, so same-named labels in
      different families never merge);
    - ``shared_inverse_size_mass`` — ``SUM(1 / hyperedge size)`` over the
      shared hyperedges: sharing a 2-member hyperedge weighs 1.0, a
      200-member one 0.005. This is the graded signal the community
      lanes lack (their memberships are one-hot everywhere measured,
      Appendix C of test_coverage_gap_sweep_nov24).

    Bounding (the 2026-11-24 OOM lesson: size inputs before running):

    - ``max_edge_members`` (default 200) drops giant hyperedges from the
      join. A 999-member edge adds ~498k pair rows for a mass of ~0.001
      each; treating industry-scale edges as background keeps the pair
      table at ``sum(n_members choose 2) <= edges * 19,900`` rows before
      aggregation, in flight, no Python loop.
    - ``min_shared`` (default 1) keeps every pair with at least one
      shared hyperedge; raise it to 2+ for a signal that is not one
      accidental co-membership.

    Measured on the snapshot parquet (2026-11-24, Appendix C): partition
    family 1,229 nodes → 41,471 pairs (10,615 share ≥2, max 6);
    ``edition`` 778 → 11,384 (9,203 ≥2, max 11); ``event`` 1,175 →
    1,053 (88 ≥2) — nearly disjoint, which is why every clustering seat
    returned one giant block there.

    Consumes any :func:`load_incidence_arrow` table, so it follows the
    loader's source (live or snapshot). Params are bound positionally
    (``?``), never interpolated.
    """
    return incidence_query(
        table,
        SHARED_GROUP_PAIRS_SQL,
        params=[max_edge_members, min_shared],
    )


def hypergraph_from_arrow(table: pa.Table):
    """HGX ``Hypergraph`` from the incidence table — the to_pylist boundary.

    hypergraphx wants Python objects; this is the single sanctioned
    conversion point (§10: Arrow in flight, ``to_pylist()`` at the
    boundary). Unweighted by construction, mirroring the S1-S13 lanes —
    per-incidence weights are per (edge, member), not per edge, so the
    S14 weighted walk composes them itself after loading node sets.

    Returns ``(hypergraph, labels)`` where ``labels`` maps each edge's
    frozen member tuple to its ``"edge_type:label"`` prefixed name (the
    legacy load_incidence contract).
    """
    from hypergraphx import Hypergraph

    grouped: dict[str, set[str]] = {}
    for r in table.to_pylist():
        grouped.setdefault(f"{r['edge_type']}:{r['label']}", set()).add(r["node"])
    edge_list = [tuple(sorted(m)) for m in grouped.values()]
    labels = {tuple(sorted(m)): p for p, m in grouped.items()}
    return Hypergraph(edge_list=edge_list), labels
