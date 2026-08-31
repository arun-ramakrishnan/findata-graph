"""Graph-algos + FTS access layer for the Mojo bridge bench.

Phase 1 of the graph-algos port (proposal
doc/improvements/proposals/mojo_graph_algos_port.md): the ORIGINAL python
modules are the engine; this fixture is the parity oracle + connection
holder. The Mojo probe (Mojo/src/bench/graph_algos_probe.mojo) executes
the SQL cases itself on these connections (repr-checksum parity vs the
native run below) and drives the original metric functions end-to-end
(canonical-string parity). Covers:

  * the Onager DuckDB community extension — the `make graph-algos`
    engine (table functions over (src, dst, weight) temp tables)
  * DuckDB extensions sqlite (ATTACH) + vss via query.connect()
  * the repo's full FTS5 surface: note_search (/api/search shape),
    doc_search + script_search (bm25-weighted, OR-quoted MATCH),
    plus the sqlite-vec vec0 KNN mirror

EVERY connection is read-only (connect(..., read_only=True)); the vec case
is pre-gated with lazy_backfill=False so it can never write. Onager temp
tables are CREATE OR REPLACE TEMP — session-local, idempotent.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RESEARCH_DB = REPO / "memory" / "research.db"
DOC_DB = REPO / "memory" / "doc_search.db"
SCRIPT_DB = REPO / "memory" / "script_search.db"

VEC_DIMS = 384  # bge-small-en-v1.5, same as the hybrid-search read path

_gcon = None
_sq: sqlite3.Connection | None = None
_doc: sqlite3.Connection | None = None
_scr: sqlite3.Connection | None = None


def graph_con():
    """The canonical graph connection: query.connect() loads sqlite
    (ATTACH fin) + vss; onager is LOADed here (idempotent) so §1 of the
    probe can assert all three extensions in one inventory query."""
    global _gcon
    if _gcon is None:
        from helpers.graph import query as gq

        _gcon = gq.connect(read_only=True)
        _gcon.execute("LOAD onager;")
    return _gcon


def _connect_ro(db: pathlib.Path) -> sqlite3.Connection:
    """Read-only connection via the shared helper (P0 static check).
    row_factory=None keeps raw tuples: sum_rows hashes repr(row) and the
    Mojo side mirrors Python's tuple repr byte-for-byte — the sqlite3.Row
    default would change every checksum and break parity."""
    from helpers.core.db import connect

    return connect(db, read_only=True, row_factory=None)


def research_con() -> sqlite3.Connection:
    global _sq
    if _sq is None:
        _sq = _connect_ro(RESEARCH_DB)
    return _sq


def doc_con() -> sqlite3.Connection:
    global _doc
    if _doc is None:
        if not DOC_DB.exists():
            raise FileNotFoundError("memory/doc_search.db missing — run rebuild_doc_search")
        _doc = _connect_ro(DOC_DB)
    return _doc


def script_con() -> sqlite3.Connection:
    global _scr
    if _scr is None:
        if not SCRIPT_DB.exists():
            raise FileNotFoundError("memory/script_search.db missing — run rebuild_script_search")
        _scr = _connect_ro(SCRIPT_DB)
    return _scr


def conn_for(kind: str):
    return {"graph": graph_con, "research": research_con, "doc": doc_con, "script": script_con}[
        kind
    ]()


# --------------------------------------------------------------------------- #
# SQL cases — the Mojo side executes these itself. Onager-path SQL is
# composed with the ORIGINAL module's own helpers (_where_inline) and
# mirrors the templates in onager.py (_materialize_from_db /
# _onager_named / onager_louvain / onager_voterank /
# onager_link_prediction) — keep in sync; the metric FUNCTION cases are
# the drift alarm (they run the original code path verbatim). ORDER OF
# THE LIST IS LOAD-BEARING: each table-function case needs the temp
# tables left by the preceding materialisation case.
# --------------------------------------------------------------------------- #
def _mat_pair(edge_types):
    """(int_sql, e_sql) composed exactly like onager._materialize_from_db."""
    from helpers.graph import onager as _on

    where = _on._where_inline(edge_types)
    t = _on._EDGE_TABLE
    int_sql = f"""
        CREATE OR REPLACE TEMP TABLE _onager_int AS
        SELECT name, (row_number() OVER (ORDER BY name) - 1)::BIGINT AS nid
        FROM (
            SELECT source AS name FROM {t}{where}
            UNION
            SELECT target AS name FROM {t}{where}
        );
        """  # noqa: S608  # interpolates the _EDGE_TABLE schema constant + _where_inline output (?-clauses only)
    e_sql = f"""
        CREATE OR REPLACE TEMP TABLE _onager_e AS
        SELECT s.nid AS src, t.nid AS dst,
               COALESCE(TRY_CAST(e.weight AS DOUBLE), 1.0) AS weight
        FROM {t} e
        JOIN _onager_int s ON s.name = e.source
        JOIN _onager_int t ON t.name = e.target{where}
        ORDER BY src, dst;
        """  # noqa: S608  # interpolates the _EDGE_TABLE schema constant + _where_inline output (?-clauses only)
    return int_sql, e_sql


def _tf_named_sql(fn: str, col: str) -> str:
    """_onager_named's template (+ ORDER BY name for a stable checksum;
    the un-ordered original shape is covered by the function cases)."""
    return (
        f"SELECT i.name, out.{col} "  # noqa: S608  # fn/col are caller-passed literals from the closed onager table-function set
        f"FROM {fn}((SELECT src, dst, weight FROM _onager_e)) out "
        f"JOIN _onager_int i ON i.nid = out.node_id "
        f"ORDER BY i.name"
    )


_BT = ["belongs_to"]  # the CLI's default BelongsTo edge label
_PRED = ["co_mentioned_in", "jv_with", "competes_with", "same_group"]
_MAT_BT = _mat_pair(_BT)
_MAT_ALL = _mat_pair(None)
_MAT_PRED = _mat_pair(_PRED)

_LOUVAIN_SQL = """
    SELECT out.node_id, i.name, out.community
    FROM onager_cmm_louvain(
        (SELECT src, dst, weight FROM _onager_e), seed => 42) out
    JOIN _onager_int i ON i.nid = out.node_id
    ORDER BY i.name
"""
_VOTERANK_SQL = """
    SELECT i.name
    FROM onager_ctr_voterank(
        (SELECT src, dst, weight FROM _onager_e)) v
    JOIN _onager_int i ON i.nid = v.node_id
"""
# onager_link_prediction's SQL verbatim (limit omitted — full ranking).
_JACCARD_SQL = """
    WITH pairs AS (
        SELECT DISTINCT LEAST(node1, node2) AS lo, GREATEST(node1, node2) AS hi,
               coefficient AS score
        FROM onager_lnk_jaccard((SELECT src, dst, weight FROM _onager_e))
    )
    SELECT i1.name, i2.name, p.score
    FROM pairs p
    JOIN _onager_int i1 ON i1.nid = p.lo
    JOIN _onager_int i2 ON i2.nid = p.hi
    WHERE p.score > 0
      AND NOT EXISTS (
          SELECT 1 FROM _onager_e ee
          WHERE (ee.src = p.lo AND ee.dst = p.hi)
             OR (ee.src = p.hi AND ee.dst = p.lo))
    ORDER BY p.score DESC, p.lo, p.hi
"""

# FTS shapes — verbatim from the production paths (app.py /api/search,
# rebuild_doc_search.search_docs, rebuild_script_search.search_scripts).
_SNIP_NOTE = "snippet(note_search, 4, '<mark>', '</mark>', '…', 12)"


def _doc_expr() -> str:
    from helpers.maintenance.rebuild_doc_search import fts_match_expr

    return fts_match_expr("why did we not adopt langgraph")


def _script_expr() -> str:
    from helpers.maintenance.rebuild_doc_search import fts_match_expr

    return fts_match_expr("integrity")


SQL_CASES: list[dict] = [
    dict(
        name="ext_inventory",
        group="graph",
        conn="graph",
        reps=5,
        fetch=True,
        sql=(
            "SELECT extension_name, loaded, installed FROM duckdb_extensions() "
            "WHERE extension_name IN ('onager','sqlite_scanner','vss') "
            "ORDER BY extension_name"
        ),
        params=[],
    ),
    dict(
        name="edge_counts",
        group="graph",
        conn="graph",
        reps=20,
        fetch=True,
        sql=(
            "SELECT edge_type, COUNT(*) FROM fin.graph_edges GROUP BY edge_type ORDER BY edge_type"
        ),
        params=[],
    ),
    dict(
        name="node_counts",
        group="graph",
        conn="graph",
        reps=20,
        fetch=True,
        sql=("SELECT kind, COUNT(*) FROM v_node GROUP BY kind ORDER BY kind"),
        params=[],
    ),
    dict(
        name="build_meta",
        group="graph",
        conn="graph",
        reps=5,
        fetch=True,
        sql="SELECT key, value FROM _build_meta ORDER BY key",
        params=[],
    ),
    dict(
        name="mat_bt_int",
        group="onager",
        conn="graph",
        reps=1,
        fetch=False,
        sql=_MAT_BT[0],
        params=[],
    ),
    dict(
        name="mat_bt_e",
        group="onager",
        conn="graph",
        reps=1,
        fetch=False,
        sql=_MAT_BT[1],
        params=[],
    ),
    dict(
        name="tf_pagerank",
        group="onager",
        conn="graph",
        reps=3,
        fetch=True,
        sql=_tf_named_sql("onager_ctr_pagerank", "rank"),
        params=[],
    ),
    dict(
        name="tf_components",
        group="onager",
        conn="graph",
        reps=3,
        fetch=True,
        sql=_tf_named_sql("onager_par_components", "component"),
        params=[],
    ),
    dict(
        name="tf_louvain",
        group="onager",
        conn="graph",
        reps=3,
        fetch=True,
        sql=_LOUVAIN_SQL,
        params=[],
    ),
    dict(
        name="mat_all_int",
        group="onager",
        conn="graph",
        reps=1,
        fetch=False,
        sql=_MAT_ALL[0],
        params=[],
    ),
    dict(
        name="mat_all_e",
        group="onager",
        conn="graph",
        reps=1,
        fetch=False,
        sql=_MAT_ALL[1],
        params=[],
    ),
    dict(
        name="tf_voterank",
        group="onager",
        conn="graph",
        reps=3,
        fetch=True,
        # NO ORDER BY: VoteRank's row order IS the ranking (do NOT re-sort)
        sql=_VOTERANK_SQL,
        params=[],
    ),
    dict(
        name="mat_pred_int",
        group="onager",
        conn="graph",
        reps=1,
        fetch=False,
        sql=_MAT_PRED[0],
        params=[],
    ),
    dict(
        name="mat_pred_e",
        group="onager",
        conn="graph",
        reps=1,
        fetch=False,
        sql=_MAT_PRED[1],
        params=[],
    ),
    dict(
        name="tf_jaccard",
        group="onager",
        conn="graph",
        reps=3,
        fetch=True,
        sql=_JACCARD_SQL,
        params=[],
    ),
    dict(
        name="fts_note_rank_snippet",
        group="fts",
        conn="research",
        reps=20,
        fetch=True,
        sql=(
            f"SELECT doc_type, file_path, title, sector, {_SNIP_NOTE} "  # noqa: S608  # _SNIP_NOTE is a module constant; values ride ?-params
            "FROM note_search WHERE note_search MATCH ? "
            "ORDER BY rank LIMIT ? OFFSET ?"
        ),
        params=["shrimp feed", 20, 0],
    ),
    dict(
        name="fts_note_typed",
        group="fts",
        conn="research",
        reps=20,
        fetch=True,
        sql=(
            f"SELECT doc_type, file_path, title, sector, {_SNIP_NOTE} "  # noqa: S608  # _SNIP_NOTE is a module constant; values ride ?-params
            "FROM note_search WHERE note_search MATCH ? AND doc_type = ? "
            "ORDER BY rank LIMIT ? OFFSET ?"
        ),
        params=["shrimp feed", "company", 20, 0],
    ),
    dict(
        name="fts_note_hybrid",
        group="fts",
        conn="research",
        reps=20,
        fetch=True,
        sql=(
            f"SELECT doc_type, file_path, title, sector, embedding, rank, "  # noqa: S608  # _SNIP_NOTE is a module constant; values ride ?-params
            f"{_SNIP_NOTE} "
            "FROM note_search WHERE note_search MATCH ? "
            "ORDER BY rank LIMIT ? OFFSET ?"
        ),
        params=["shrimp feed", 20, 0],
    ),
    dict(
        name="fts_note_count",
        group="fts",
        conn="research",
        reps=20,
        fetch=True,
        sql="SELECT COUNT(*) FROM note_search WHERE note_search MATCH ?",
        params=["shrimp feed"],
    ),
    dict(
        name="fts_note_boolean_prefix",
        group="fts",
        conn="research",
        reps=20,
        fetch=True,
        sql=(
            f"SELECT doc_type, file_path, title, sector, {_SNIP_NOTE} "  # noqa: S608  # _SNIP_NOTE is a module constant; values ride ?-params
            "FROM note_search WHERE note_search MATCH ? "
            "ORDER BY rank LIMIT ? OFFSET ?"
        ),
        params=["shrimp* OR feed*", 20, 0],
    ),
    dict(
        name="fts_doc_bm25",
        group="fts",
        conn="doc",
        reps=20,
        fetch=True,
        sql=(
            "SELECT rowid, title, section_title, file_path, anchor, "
            "embedding, rank, "
            "snippet(doc_search, 4, '<mark>', '</mark>', ' … ', 16) AS snip "
            "FROM doc_search WHERE doc_search MATCH ? "
            "ORDER BY bm25(doc_search, 2.0, 2.0, 0.0, 0.0, 1.0, 0.0) "
            "LIMIT ?"
        ),
        params=[_doc_expr(), 25],
    ),
    dict(
        name="fts_script_bm25",
        group="fts",
        conn="script",
        reps=20,
        fetch=True,
        sql=(
            "SELECT rowid, title, kind, rel_path, area, purpose, "
            "embedding, rank, "
            "snippet(script_search, 5, '<mark>', '</mark>', ' … ', 16) "
            "AS snip FROM script_search WHERE script_search MATCH ? "
            "AND kind = ? "
            "ORDER BY bm25(script_search, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0) "
            "LIMIT ?"
        ),
        params=[_script_expr(), "test", 25],
    ),
    dict(
        name="fts_doc_embed_scan",
        group="fts",
        conn="doc",
        reps=3,
        fetch=True,
        sql=(
            "SELECT rowid, embedding FROM doc_search "
            "WHERE embedding IS NOT NULL AND embedding != ''"
        ),
        params=[],
    ),
]


def sql_cases() -> list[dict]:
    return SQL_CASES


def ncases() -> int:
    return len(SQL_CASES)


# --------------------------------------------------------------------------- #
# Native SQL baseline (checksum parity oracle). Cases run IN LIST ORDER —
# materialisation state must build up exactly as it will for the Mojo run.
# --------------------------------------------------------------------------- #
def sum_rows(rows) -> int:
    """Deterministic row checksum, computable identically on both sides:
    UTF-8 BYTES of repr(row) — the Mojo side measures byte_length()."""
    return sum(len(repr(r).encode("utf-8")) for r in rows)


_NATIVE_SQL: dict[str, tuple] = {}


def native_sql(name: str):
    """(rows, checksum, elapsed_s) for one case, cached. reps loop like
    the Mojo side so the elapsed figures are comparable."""
    if name in _NATIVE_SQL:
        return _NATIVE_SQL[name]
    case = next(c for c in SQL_CASES if c["name"] == name)
    con = conn_for(case["conn"])
    t0 = time.perf_counter()
    nrows = checksum = 0
    for _ in range(case["reps"]):
        if case["fetch"]:
            rows = con.execute(case["sql"], case["params"]).fetchall()
            nrows = len(rows)
            checksum = sum_rows(rows)
        else:
            con.execute(case["sql"], case["params"])
            nrows = checksum = 0
    out = (nrows, checksum, time.perf_counter() - t0)
    _NATIVE_SQL[name] = out
    return out


# --------------------------------------------------------------------------- #
# Metric cases — the ORIGINAL functions driven end-to-end. These are the
# 14 `make graph-algos` metrics (12 compute() + link-predict + voterank)
# plus the whole-graph onager_graph_metrics block (make graph-stats) and
# the vec0 KNN mirror. kind selects the canonical form both sides build.
# --------------------------------------------------------------------------- #
def _metric_fns():
    from helpers.graph import algorithms as alg
    from helpers.graph import onager as _on

    def _c(metric):
        return lambda: alg.compute(metric, graph_con())

    return {
        "pagerank": ("float_dict", _c("pagerank")),
        "degree_centrality": ("float_dict", _c("degree_centrality")),
        "betweenness_centrality": ("float_dict", _c("betweenness_centrality")),
        "closeness_centrality": ("float_dict", _c("closeness_centrality")),
        "eigenvector_centrality": ("float_dict", _c("eigenvector_centrality")),
        "harmonic_centrality": ("float_dict", _c("harmonic_centrality")),
        "katz_centrality": ("float_dict", _c("katz_centrality")),
        "laplacian_centrality": ("float_dict", _c("laplacian_centrality")),
        "local_reaching_centrality": ("float_dict", _c("local_reaching_centrality")),
        "local_clustering_coefficient": ("float_dict", _c("local_clustering_coefficient")),
        "weakly_connected_component": ("partition", _c("weakly_connected_component")),
        "louvain_community": ("int_dict", _c("louvain_community")),
        "link_prediction": ("pair_list", lambda: alg.link_prediction(graph_con())),
        "voterank": ("name_list", lambda: alg.voterank_seeds(graph_con())),
        # direct onager call (not the cached query.py wrapper) so the
        # Mojo-side timing is not flattered by the result cache
        "graph_metrics": ("scalars", lambda: _on.onager_graph_metrics(graph_con())),
        "vec0_knn": ("float_dict", vec_knn),
    }


_METRICS: dict[str, tuple] | None = None


def _metrics():
    global _METRICS
    if _METRICS is None:
        _METRICS = _metric_fns()
    return _METRICS


def metric_cases() -> list[tuple]:
    return [(n, k) for n, (k, _) in _metrics().items()]


def run_metric(name: str):
    """Run the ORIGINAL function for `name` and return the raw result."""
    return _metrics()[name][1]()


def vec_knn():
    """sqlite-vec KNN over the note embedding mirror — the hybrid-search
    neighbour leg. Pre-gated read-only: lazy_backfill=False means an
    absent mirror returns None (probe prints SKIP), NEVER a write."""
    import json as _json

    from helpers.core import vec_search as vs

    conn = research_con()
    if not vs._attach_ok(conn):
        return None
    if not vs.vec_available(conn, VEC_DIMS, lazy_backfill=False):
        return None
    row = conn.execute(
        "SELECT embedding FROM note_search "
        "WHERE embedding IS NOT NULL AND embedding != '' "
        "ORDER BY file_path LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return vs.knn_similarities(conn, _json.loads(row[0]), None, VEC_DIMS)


# --------------------------------------------------------------------------- #
# Canonical forms — the NATIVE half. The Mojo side rebuilds each of these
# from the same raw result using only the fmt_float/canon_scalar bridge
# lambdas (Mojo lacks %.6f) plus its own sort/join logic — mirroring the
# integrity port's formatting discipline.
# --------------------------------------------------------------------------- #
def fmt_float(v) -> str:
    return f"{v:.6f}"


def canon_scalar(v) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool) or not isinstance(v, int):
        return f"{v:.6f}"
    return str(v)


def _canon_partition(result: dict) -> str:
    sizes: dict[int, int] = {}
    for cid in result.values():
        sizes[cid] = sizes.get(cid, 0) + 1
    ordered = sorted(sizes.values(), reverse=True)
    return f"{len(ordered)}:" + ",".join(str(s) for s in ordered)


def canonical(kind: str, result) -> str:
    if result is None:
        return "__SKIP__"
    if kind == "float_dict":
        return ",".join(f"{k}:{fmt_float(v)}" for k, v in sorted(result.items()))
    if kind == "int_dict":
        return ",".join(f"{k}:{int(v)}" for k, v in sorted(result.items()))
    if kind == "partition":
        return _canon_partition(result)
    if kind == "pair_list":
        return ",".join(sorted(f"{a}|{b}|{fmt_float(s)}" for a, b, s in result))
    if kind == "name_list":
        return ">".join(str(n) for n in result)
    if kind == "scalars":
        return ",".join(f"{k}:{canon_scalar(v)}" for k, v in sorted(result.items()))
    raise ValueError(f"unknown canonical kind {kind!r}")


_NATIVE_METRIC: dict[str, tuple] = {}


def native_metric(name: str):
    """(canonical, elapsed_s) computed natively, cached. __SKIP__ marks a
    case the environment cannot serve (vec mirror absent)."""
    if name in _NATIVE_METRIC:
        return _NATIVE_METRIC[name]
    kind, fn = _metrics()[name]
    t0 = time.perf_counter()
    canon = canonical(kind, fn())
    out = (canon, time.perf_counter() - t0)
    _NATIVE_METRIC[name] = out
    return out


# --------------------------------------------------------------------------- #
# §1 / §4 support
# --------------------------------------------------------------------------- #
def table_counts() -> dict[str, int]:
    return {
        "note_search": research_con().execute("SELECT COUNT(*) FROM note_search").fetchone()[0],
        "doc_search": doc_con().execute("SELECT COUNT(*) FROM doc_search").fetchone()[0],
        "script_search": script_con().execute("SELECT COUNT(*) FROM script_search").fetchone()[0],
    }


def cli_all() -> dict:
    """The `make graph-algos` command verbatim (algorithms --all
    --no-apply), stdout+stderr captured. rc is 0 unless the CLI itself
    blew up — per-metric FAILs land in err with rc still 0, so the probe
    also counts the 14 metric headers."""
    import contextlib
    import io

    from helpers.graph import algorithms as alg

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = alg._cli(["--all", "--no-apply"])
    return {"rc": rc, "out": out.getvalue(), "err": err.getvalue()}
