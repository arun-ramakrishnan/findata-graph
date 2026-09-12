#!/usr/bin/env python3
"""igraph pilot bridge (2026-09-12) — experimental alternative to Onager.

Runs graph analytics through python-igraph (C core, GPL-2.0-or-later) over
the SAME ``graph_edges`` SQLite source of truth. Read-only by default;
write-back to ``graph_analytics`` is opt-in ``--apply`` (D13), mirroring
``helpers/graph/algorithms.py``.

Intentionally dependency-light: ``igraph`` is imported LAZILY inside the
build functions so this module (and the test file) still COLLECTS under
the repo ``.venv`` which does NOT have igraph installed. Real igraph runs
use the isolated ``/tmp/venv_igraph`` interpreter — never add igraph to
``pyproject.toml``/``.venv`` without a licence + gate decision (igraph is
copyleft GPL vs Onager Apache-2.0; see /tmp/eval_igraph_hands.md).

Contract mirrors ``helpers/graph/onager.py``:
  - SQLite ``graph_edges`` is the sole source of truth.
  - Entity names are remapped to integer ids (igraph vertices are 0..n-1).
  - All SQLite access goes through ``helpers.core.db.connect()`` (lazy
    import, so the module stays importable without dotenv/duckdb).
  - Results are recompute-on-demand; persistence is UPSERT via
    ``algorithms.write_analytics()`` only when ``apply=True``.

Routing (pilot proposal):
  - ONAGER_DEFAULT: pagerank, WCC, clustering, degree, closeness,
    betweenness, eigenvector, link-prediction scaffolding, graph_metrics.
  - IGRAPH: Leiden communities, weighted centralities (weights honoured,
    unlike Onager's unweighted betweenness/closeness/eigenvector),
    weighted shortest path, flow/cut analyses.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB_PATH = _PROJECT_ROOT / "memory" / "research.db"

#: Routing table — which engine owns each analysis family in the pilot.
#: Values are "ONAGER_DEFAULT" (current production engine) or "IGRAPH".
ROUTING: dict[str, str] = {
    # Production stays on Onager (validated, Apache-2.0, wired into CLI/API).
    "pagerank": "ONAGER_DEFAULT",
    "weakly_connected_component": "ONAGER_DEFAULT",
    "local_clustering_coefficient": "ONAGER_DEFAULT",
    "degree_centrality": "ONAGER_DEFAULT",
    "closeness_centrality": "ONAGER_DEFAULT",
    "betweenness_centrality": "ONAGER_DEFAULT",
    "eigenvector_centrality": "ONAGER_DEFAULT",
    "louvain_community": "ONAGER_DEFAULT",
    "link_prediction": "ONAGER_DEFAULT",
    "graph_metrics": "ONAGER_DEFAULT",
    # Pilot lanes where igraph adds value over Onager (verified 2026-09-12):
    # Leiden has no Onager equivalent; Onager betweenness/closeness/
    # eigenvector ignore weights while igraph honours them; flow/cut and
    # weighted shortest-path have no repo equivalent (shortest_path is an
    # unweighted SQL BFS over e_all_und).
    "leiden_community": "IGRAPH",
    "weighted_pagerank": "IGRAPH",
    "weighted_betweenness": "IGRAPH",
    "weighted_closeness": "IGRAPH",
    "weighted_eigenvector": "IGRAPH",
    "weighted_shortest_path": "IGRAPH",
    "maxflow_mincut": "IGRAPH",
    "louvain_compare": "IGRAPH",
}

#: Edge types known in the live DB (informational; load_projection accepts
#: any list and None = full projection).
KNOWN_EDGE_TYPES = [
    "acquired",
    "approved_by",
    "belongs_to",
    "cited_in",
    "co_mentioned_in",
    "competes_with",
    "customer_of",
    "exposed_to",
    "has_company",
    "invested_in",
    "jv_with",
    "listed_in",
    "part_of",
    "rated_by",
    "regulated_by",
    "same_group",
    "semantic_peer",
    "subsidiary_of",
    "supplier_to",
]


def _require_igraph():
    """Import igraph lazily (keeps .venv collection working without it)."""
    try:
        import igraph as ig
    except ImportError as exc:
        raise ImportError(
            "python-igraph is not installed in this interpreter. "
            "Run igraph workloads with /tmp/venv_igraph/bin/python "
            "(do NOT add igraph to .venv/pyproject yet; venv recreate "
            "recipe: doc/improvements/proposals/"
            "hybrid_graph_onager_igraph.md §3)."
        ) from exc
    return ig


# --------------------------------------------------------------------------- #
# (1) Projection loader — SELECT source,target,edge_type,weight
# --------------------------------------------------------------------------- #
def load_projection(
    edge_types: list[str] | None = None,
    db_path: str | Path | None = None,
) -> list[tuple[str, str, str, float]]:
    """Load ``(source, target, edge_type, weight)`` rows from graph_edges.

    ``edge_types=None`` = FULL projection (all types). Uses
    ``helpers.core.db.connect()`` when available, else plain sqlite3
    against ``db_path``/``DEFAULT_DB_PATH`` (keeps /tmp/venv_igraph working
    without dotenv).
    """
    if db_path is None:
        try:
            from helpers.core.db import connect as _connect

            con = _connect()
        except Exception:
            con = sqlite3.connect(str(DEFAULT_DB_PATH))
            con.row_factory = sqlite3.Row
    else:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
    try:
        if edge_types:
            ph = ",".join("?" for _ in edge_types)
            sql = (
                "SELECT source, target, edge_type, weight FROM graph_edges "  # noqa: S608  # parameterized; interpolated part is a `?`-clause list
                f"WHERE edge_type IN ({ph})"
            )
            rows = con.execute(sql, list(edge_types)).fetchall()
        else:
            rows = con.execute(
                "SELECT source, target, edge_type, weight FROM graph_edges"
            ).fetchall()
        out: list[tuple[str, str, str, float]] = []
        for r in rows:
            try:
                w = float(r["weight"]) if r["weight"] is not None else 1.0
            except ValueError, TypeError:
                w = 1.0
            out.append((r["source"], r["target"], r["edge_type"], w))
        return out
    finally:
        try:
            con.close()
        except Exception:  # noqa: S110  # best-effort close; cleanup is optional
            pass


# --------------------------------------------------------------------------- #
# (2) Graph builder — name_to_id + batch add_vertices/add_edges
# --------------------------------------------------------------------------- #
def build_graph(
    rows: list[tuple[str, str, str, float]],
    *,
    directed: bool = False,
):
    """Build an undirected igraph Graph from projection rows.

    Returns ``(graph, name_to_id)``. Vertices batched via ``add_vertices``,
    edges batched via ``add_edges``; ``vs["name"]``, ``es["weight"]`` and
    ``es["etype"]`` attributes set for downstream per-label subgraphs.
    Names sorted for deterministic ids.

    Interchange caveat (S1 review 2026-09-12): an NCOL/edgelist export
    roundtrip is LOSSY — it carries (source, target, weight) only and drops
    ``es["etype"]``, so label-filtered analyses (per-etype subgraphs,
    labelled shortest paths) cannot be reconstructed from it. The bridge
    always rebuilds from the ``graph_edges`` SQLite projection; a stored
    edge file is never a source of truth.
    """
    ig = _require_igraph()
    names = sorted({s for s, _, _, _ in rows} | {t for _, t, _, _ in rows})
    name_to_id = {n: i for i, n in enumerate(names)}
    g = ig.Graph(directed=directed)
    g.add_vertices(len(names))
    g.vs["name"] = names
    if rows:
        g.add_edges([(name_to_id[s], name_to_id[t]) for s, t, _, _ in rows])
        g.es["weight"] = [float(w) for _, _, _, w in rows]
        g.es["etype"] = [e for _, _, e, _ in rows]
    else:
        g.es["weight"] = []
        g.es["etype"] = []
    return g, name_to_id


def _names(g) -> list[str]:
    return list(g.vs["name"])


def _score_dict(g, scores: list[float]) -> dict[str, float]:
    names = _names(g)
    return {n: float(s) for n, s in zip(names, scores)}


# --------------------------------------------------------------------------- #
# (3) Analytics funcs
# --------------------------------------------------------------------------- #
def weighted_pagerank(g, damping: float = 0.85) -> dict[str, float]:
    """Weighted PageRank -> {name: score} (weights honoured)."""
    return _score_dict(g, g.pagerank(weights=g.es["weight"], damping=damping))


def weighted_betweenness(g) -> dict[str, float]:
    """Weighted betweenness -> {name: score} (Onager's is UNWEIGHTED)."""
    return _score_dict(g, g.betweenness(weights=g.es["weight"]))


def weighted_closeness(g) -> dict[str, float]:
    """Weighted closeness -> {name: score} (Onager's is UNWEIGHTED)."""
    return _score_dict(g, g.closeness(weights=g.es["weight"]))


def weighted_eigenvector(g) -> dict[str, float]:
    """Weighted eigenvector centrality -> {name: score}.

    igraph converges on the 12-node toy where Onager's eigenvector FAILS
    (see baseline ``eigenvector_centrality.error``).
    """
    return _score_dict(g, g.eigenvector_centrality(weights=g.es["weight"]))


def weighted_degree(g) -> dict[str, float]:
    """Weighted degree (strength) normalised by (n-1) -> {name: score}."""
    n = g.vcount()
    denom = (n - 1) if n > 1 else 1
    return {n_: float(d) / denom for n_, d in zip(_names(g), g.strength(weights=g.es["weight"]))}


def _seed_rng(seed: int | None) -> None:
    """Install ``random.Random(seed)`` as igraph's process-wide RNG.

    ``community_leiden``/``community_multilevel`` have no seed argument;
    without this, two full-graph Leiden runs differ (measured 2026-09-12:
    Q 0.5326 vs 0.5308). Seeding per call makes each call individually
    reproducible (fresh ``Random(seed)``); side effect is process-wide for
    other stochastic igraph calls. ``seed=None`` keeps igraph's default.
    Default 42 mirrors Onager's ``seed => 42`` louvain convention.
    """
    if seed is None:
        return
    import random

    _require_igraph().set_random_number_generator(random.Random(seed))  # noqa: S311  # deterministic Leiden seeding, not crypto


def leiden_communities(
    g,
    *,
    objective: str = "modularity",
    resolution: float = 1.0,
    seed: int | None = 42,
) -> tuple[dict[str, int], float]:
    """Leiden communities (no Onager equivalent) -> ({name: cid}, Q).

    Built into igraph 1.0.0 C core — no ``leidenalg`` package needed.
    Deterministic by default (``seed=42``); ``seed=None`` for stochastic.
    """
    _seed_rng(seed)
    comm = g.community_leiden(
        weights=g.es["weight"], objective_function=objective, resolution=resolution
    )
    q = float(g.modularity(comm.membership, weights=g.es["weight"]))
    labels = {n: int(c) for n, c in zip(_names(g), comm.membership)}
    return labels, q


def louvain_compare(g, *, seed: int | None = 42) -> dict[str, Any]:
    """Side-by-side Louvain (multilevel) vs Leiden partition + modularity.

    Both legs seeded (default 42) so the comparison is reproducible.
    """
    _seed_rng(seed)
    louv = g.community_multilevel(weights=g.es["weight"])
    louv_q = float(g.modularity(louv.membership, weights=g.es["weight"]))
    leid_labels, leid_q = leiden_communities(g, seed=seed)
    return {
        "louvain": {
            "membership": {n: int(c) for n, c in zip(_names(g), louv.membership)},
            "modularity": louv_q,
            "n_communities": len(louv),
        },
        "leiden": {
            "membership": leid_labels,
            "modularity": leid_q,
            "n_communities": len(set(leid_labels.values())),
        },
    }


def weighted_shortest_path(
    g,
    src: str,
    dst: str,
    *,
    edge_label: str | None = None,
) -> tuple[list[str], float] | None:
    """Weighted shortest path on a per-label subgraph.

    ``edge_label=None`` = full graph; otherwise only edges whose ``etype``
    equals the label are traversable (mirrors query.shortest_path's label
    filter, but weighted and in-memory). Returns ``(names, distance)`` or
    None when unreachable. Unweighted hop BFS (repo default) == weights of
    1.0 per traversed edge.
    """
    names = _names(g)
    if src not in names or dst not in names:
        return None
    if edge_label is None:
        sub = g
    else:
        keep = [e.index for e in g.es if e["etype"] == edge_label]
        sub = g.subgraph_edges(keep, delete_vertices=False)
    s, t = names.index(src), names.index(dst)
    # Distance first: doubles as the reachability check without tripping
    # igraph's "Couldn't reach some vertices" RuntimeWarning from vpath.
    dist = float(sub.distances(source=s, target=t, weights=sub.es["weight"])[0][0])
    if dist == float("inf"):
        return None
    vpath = sub.get_shortest_paths(s, to=t, weights=sub.es["weight"], output="vpath")[0]
    if not vpath:
        return None
    return [sub.vs[i]["name"] for i in vpath], dist


def maxflow_mincut(g, source: str, target: str) -> dict[str, Any] | None:
    """s-t maxflow + minimum cut (chokepoint analysis) — proposal S5.

    Edge weights act as capacities (live weights are continuous 0.4-3.0;
    igraph's ``capacity=`` — singular, unlike pagerank/shortest-path's
    ``weights=``). ``partition`` is whichever minimum cut the solver lands
    on: the VALUE is unique, the edge set may not be. Connectivity counts
    are unweighted by definition (edge-disjoint / vertex-disjoint path
    counts). Returns None when either endpoint is missing.
    """
    names = _names(g)
    if source not in names or target not in names:
        return None
    s, t = names.index(source), names.index(target)
    flow = g.maxflow(s, t, capacity=g.es["weight"])
    cut = g.mincut(s, t, capacity=g.es["weight"])
    source_side = sorted(names[i] for i in cut.partition[0])
    target_side = sorted(names[i] for i in cut.partition[1])
    return {
        "maxflow": float(flow.value),
        "mincut": float(cut.value),
        "source_side": source_side,
        "target_side": target_side,
        "cut_edges": [sorted((names[g.es[e].source], names[g.es[e].target])) for e in cut.cut],
        "edge_connectivity": int(g.edge_connectivity(source=s, target=t)),
        "vertex_connectivity": int(g.vertex_connectivity(source=s, target=t)),
    }


def modularity(g, membership: dict[str, int] | list[int]) -> float:
    """Modularity of a partition (name->cid map or raw membership list)."""
    if isinstance(membership, dict):
        mem = [membership[n] for n in _names(g)]
    else:
        mem = list(membership)
    return float(g.modularity(mem, weights=g.es["weight"]))


# --------------------------------------------------------------------------- #
# (4) Write-back via algorithms.write_analytics() — UPSERT, --apply dry-run
# --------------------------------------------------------------------------- #
def persist(
    metric: str,
    values: dict[str, Any],
    *,
    apply: bool = False,
    conn: Any | None = None,
) -> int:
    """Persist ``values`` under ``metric``. Dry-run (default) writes nothing.

    ``apply=True`` delegates to ``algorithms.write_analytics()`` (UPSERT on
    (metric, entity_name)). Returns the row count that was (or would be)
    written.
    """
    if not apply:
        return len(values)
    from helpers.graph.algorithms import write_analytics

    return write_analytics(metric, values, conn=conn)


# --------------------------------------------------------------------------- #
# CLI — every write is opt-in --apply (D13); dry-run by default
# --------------------------------------------------------------------------- #
#: Score-style commands: fn(g) -> {name: score}; share the ranked-print +
#: persist tail of the CLI (leiden/louvain-compare/path/flow have custom
#: shapes and early-return instead).
_METRIC_COMMANDS = {
    "pagerank": weighted_pagerank,
    "betweenness": weighted_betweenness,
    "closeness": weighted_closeness,
    "eigenvector": weighted_eigenvector,
    "degree": weighted_degree,
}


def main(argv: list[str] | None = None) -> int:  # noqa: C901  # CLI dispatch; argparse fan-out is inherently branchy
    ap = argparse.ArgumentParser(description="igraph pilot bridge (dry-run by default)")
    ap.add_argument(
        "command",
        choices=[
            "pagerank",
            "betweenness",
            "closeness",
            "eigenvector",
            "degree",
            "leiden",
            "louvain-compare",
            "shortest-path",
            "maxflow-mincut",
            "routing",
        ],
    )
    ap.add_argument(
        "--edge-type",
        action="append",
        default=None,
        help="project one edge_type (repeatable; default: all)",
    )
    ap.add_argument("--src", default=None)
    ap.add_argument("--dst", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument(
        "--apply", action="store_true", help="write results to graph_analytics (default: dry-run)"
    )
    ap.add_argument(
        "--metric",
        default=None,
        help="graph_analytics metric name for --apply (default: igraph_<command>)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for leiden/louvain-compare (default 42 — "
        "deterministic, mirrors Onager seed => 42)",
    )
    args = ap.parse_args(argv)

    if args.command == "routing":
        print(json.dumps(ROUTING, indent=1))
        return 0

    t0 = time.perf_counter()
    rows = load_projection(args.edge_type)
    g, _ = build_graph(rows)
    print(f"projection: n={g.vcount()} m={g.ecount()} load+build={time.perf_counter() - t0:.2f}s")

    result: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    metric_fn = _METRIC_COMMANDS.get(args.command)
    if metric_fn is not None:
        result = metric_fn(g)
    elif args.command == "leiden":
        labels, q = leiden_communities(g, seed=args.seed)
        result = {k: float(v) for k, v in labels.items()}
        extra["modularity"] = q
    elif args.command == "louvain-compare":
        cmp_ = louvain_compare(g, seed=args.seed)
        print(
            json.dumps(
                {
                    k: {"modularity": v["modularity"], "n_communities": v["n_communities"]}
                    for k, v in cmp_.items()
                },
                indent=1,
            )
        )
        if args.apply:
            metric = args.metric or "igraph_louvain_compare"
            persist(metric, {n: c for n, c in cmp_["leiden"]["membership"].items()}, apply=True)
            print(f"applied {len(cmp_['leiden']['membership'])} rows under {metric!r}")
        else:
            print(
                f"dry-run: would write {len(cmp_['leiden']['membership'])} rows "
                f"(pass --apply to persist)"
            )
        return 0
    elif args.command == "shortest-path":
        if not args.src or not args.dst:
            print("--src/--dst required for shortest-path", file=sys.stderr)
            return 2
        found = weighted_shortest_path(
            g, args.src, args.dst, edge_label=(args.edge_type[0] if args.edge_type else None)
        )
        print(json.dumps(found))
        return 0
    elif args.command == "maxflow-mincut":
        if not args.src or not args.dst:
            print("--src/--dst required for maxflow-mincut", file=sys.stderr)
            return 2
        res = maxflow_mincut(g, args.src, args.dst)
        if res is None:
            print(f"endpoint missing from projection: {args.src!r}/{args.dst!r}", file=sys.stderr)
            return 2
        summary = {
            **{
                k: res[k]
                for k in (
                    "maxflow",
                    "mincut",
                    "cut_edges",
                    "edge_connectivity",
                    "vertex_connectivity",
                )
            },
            "source_side_n": len(res["source_side"]),
            "target_side_n": len(res["target_side"]),
            "target_side": res["target_side"],
        }
        print(json.dumps(summary, indent=1))
        if args.apply:
            print(
                "maxflow-mincut is dry-run only — s-t pair result has no "
                "per-entity metric shape to persist",
                file=sys.stderr,
            )
        return 0

    ranked = sorted(result.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
    for name, score in ranked:
        print(f"  {name}: {score:.6f}")
    if extra:
        print(json.dumps(extra))
    metric = args.metric or f"igraph_{args.command}"
    if args.apply:
        n = persist(metric, result, apply=True)
        print(f"applied {n} rows under {metric!r}")
    else:
        print(f"dry-run: would write {len(result)} rows under {metric!r} (pass --apply to persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
