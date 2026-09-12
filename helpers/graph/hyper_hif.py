#!/usr/bin/env python3
"""S18(d) — HIF export lane: canonical parquet, transient JSON skin.

The Hypergraph Interchange Format record arrays ARE three tables; per the
repo data standard (architecture.md §10) the CANONICAL at-rest form here
is **three zstd parquet tables with HIF column names**:

- ``hif_nodes.parquet``        — ``id`` (entity name), ``attrs`` (JSON str)
- ``hif_edges.parquet``        — ``id`` (``edge_type:label``), ``attrs``
  (source_ref + properties + created_at packed)
- ``hif_incidences.parquet``   — ``edge``, ``node``, ``weight`` (nullable),
  ``attrs`` (direction packed)

Network type rides the parquet key-value footer metadata on every table
(``network_type=undirected`` — the HGX writer pins undirected, so the
export does too; directed lanes would change this).

``write_hif_json`` is the TRANSIENT serialization: produced FROM the
canonical parquet on consumer demand (HGX's ``read_hif`` consumes it
directly), never stored. The round-trip validator asserts counts,
per-incidence weights, edge types, and attrs packing survive both
boundaries (store ↔ parquet; parquet ↔ JSON skin).

Usage::

    python3 helpers/graph/hyper_hif.py --sources sector,sub_sector \
        --out-dir /tmp/hif --validate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import DEFAULT_DB_PATH  # noqa: E402

NODES_FN = "hif_nodes.parquet"
EDGES_FN = "hif_edges.parquet"
INCIDENCES_FN = "hif_incidences.parquet"


def _footer_meta(sources: list[str], network_type: str) -> dict[bytes, bytes]:
    return {
        b"network_type": network_type.encode(),
        b"hif": b"export-lane-v1",
        b"sources": ",".join(sources).encode(),
    }


def write_hif_parquet(
    sources: list[str],
    out_dir: str | Path,
    *,
    db_path: str | Path | None = None,
    network_type: str = "undirected",
) -> dict[str, Path]:
    """Live store → three zstd parquet tables with HIF column names."""
    import duckdb

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ph = ", ".join("?" for _ in sources)
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{db_path or DEFAULT_DB_PATH}' AS src (TYPE SQLITE, READ_ONLY)")
        nodes = con.execute(
            "SELECT DISTINCT hi.entity_name AS id, "
            "       '{\"entity_type\": \"' || e.entity_type || '\"}' AS attrs "
            "FROM src.hyper_incidences hi "
            "JOIN src.hyper_edges he ON he.id = hi.edge_id "
            "LEFT JOIN src.entities e ON e.name = hi.entity_name "
            f"WHERE he.edge_type IN ({ph}) ORDER BY 1",
            sources,
        ).to_arrow_table()
        edges = con.execute(
            "SELECT he.edge_type || ':' || he.label AS id, "
            "       json_object('source_ref', he.source_ref, "
            "                   'properties', he.properties, "
            "                   'created_at', he.created_at) AS attrs "
            "FROM src.hyper_edges he "
            f"WHERE he.edge_type IN ({ph}) ORDER BY 1",
            sources,
        ).to_arrow_table()
        incs = con.execute(
            "SELECT he.edge_type || ':' || he.label AS edge, "
            "       hi.entity_name AS node, hi.weight AS weight, "
            "       json_object('direction', hi.direction) AS attrs "
            "FROM src.hyper_incidences hi "
            "JOIN src.hyper_edges he ON he.id = hi.edge_id "
            f"WHERE he.edge_type IN ({ph}) ORDER BY 1, 2",
            sources,
        ).to_arrow_table()
    finally:
        con.close()
    meta = _footer_meta(sources, network_type)
    paths = {}
    for fn, tbl in ((NODES_FN, nodes), (EDGES_FN, edges), (INCIDENCES_FN, incs)):
        p = out / fn
        pq.write_table(tbl.replace_schema_metadata(meta), p, compression="zstd")
        paths[fn] = p
    return paths


def read_hif_parquet(out_dir: str | Path) -> dict[str, pa.Table]:
    """The three canonical tables back (zstd parquet → Arrow)."""
    d = Path(out_dir)
    return {
        "nodes": pq.read_table(d / NODES_FN),
        "edges": pq.read_table(d / EDGES_FN),
        "incidences": pq.read_table(d / INCIDENCES_FN),
    }


def write_hif_json(out_dir: str | Path, json_path: str | Path) -> Path:
    """Transient HIF JSON skin FROM the canonical parquet (never stored)."""
    tbls = read_hif_parquet(out_dir)
    nodes_meta = pq.read_metadata(Path(out_dir) / NODES_FN).metadata or {}
    network_type = nodes_meta.get(b"network_type", b"undirected").decode()
    data = {
        "type": network_type,
        "metadata": {"format": "hif-json-skin", "from": "parquet-canonical"},
        "nodes": [
            {"node": r["id"], **json.loads(r["attrs"] or "{}")} for r in tbls["nodes"].to_pylist()
        ],
        "edges": [
            {"edge": r["id"], **json.loads(r["attrs"] or "{}")} for r in tbls["edges"].to_pylist()
        ],
        "incidences": [
            {
                "edge": r["edge"],
                "node": r["node"],
                **({"weight": r["weight"]} if r["weight"] is not None else {}),
                **json.loads(r["attrs"] or "{}"),
            }
            for r in tbls["incidences"].to_pylist()
        ],
    }
    p = Path(json_path)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def validate_hif_roundtrip(
    sources: list[str], out_dir: str | Path, *, db_path: str | Path | None = None
) -> dict[str, int]:
    """Assert fidelity across store ↔ parquet ↔ JSON skin. Raises on drift."""
    import duckdb

    tbls = read_hif_parquet(out_dir)
    ph = ", ".join("?" for _ in sources)
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{db_path or DEFAULT_DB_PATH}' AS src (TYPE SQLITE, READ_ONLY)")

        def _store_count(q: str) -> int:
            row = con.execute(q, sources).fetchone()
            if row is None:
                raise ValueError(f"count query returned no row: {q[:60]}")
            return int(row[0])

        store_edges, store_incs, store_nodes = (
            _store_count(q)
            for q in (
                f"SELECT COUNT(*) FROM src.hyper_edges he WHERE he.edge_type IN ({ph})",
                "SELECT COUNT(*) FROM src.hyper_incidences hi JOIN src.hyper_edges he "
                f"ON he.id = hi.edge_id WHERE he.edge_type IN ({ph})",
                "SELECT COUNT(DISTINCT hi.entity_name) FROM src.hyper_incidences hi "
                f"JOIN src.hyper_edges he ON he.id = hi.edge_id WHERE he.edge_type IN ({ph})",
            )
        )
    finally:
        con.close()
    if tbls["edges"].num_rows != store_edges:
        raise ValueError(f"edge count drift: hif={tbls['edges'].num_rows} store={store_edges}")
    if tbls["incidences"].num_rows != store_incs:
        raise ValueError(
            f"incidence count drift: hif={tbls['incidences'].num_rows} store={store_incs}"
        )
    if tbls["nodes"].num_rows != store_nodes:
        raise ValueError(f"node count drift: hif={tbls['nodes'].num_rows} store={store_nodes}")
    # per-incidence weights survive (float compare on sorted multisets)
    w = sorted(r["weight"] for r in tbls["incidences"].to_pylist() if r["weight"] is not None)
    # edge types survive inside the packed ids
    types = {r["id"].split(":", 1)[0] for r in tbls["edges"].to_pylist()}
    if types != set(sources):
        raise ValueError(f"edge-type drift: {types} != {set(sources)}")
    # attrs are valid JSON on every row
    for kind in ("nodes", "edges", "incidences"):
        for r in tbls[kind].to_pylist():
            json.loads(r["attrs"] or "{}")
    # footer network type
    m = pq.read_metadata(Path(out_dir) / EDGES_FN).metadata or {}
    footer = m.get(b"network_type", b"").decode()
    if footer != "undirected":
        raise ValueError(f"footer network_type drift: {footer!r} != 'undirected'")
    return {
        "nodes": store_nodes,
        "edges": store_edges,
        "incidences": store_incs,
        "weighted": len(w),
    }


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", required=True, help="comma-separated edge types")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--db-path", default=None)
    p.add_argument("--json", default=None, help="also emit the transient JSON skin here")
    p.add_argument("--validate", action="store_true", help="round-trip validator")
    args = p.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    paths = write_hif_parquet(sources, args.out_dir, db_path=args.db_path)
    for fn, pth in paths.items():
        print(f"wrote {pth} ({pq.read_metadata(pth).num_rows} rows, zstd)")
    if args.json:
        jp = write_hif_json(args.out_dir, args.json)
        print(f"wrote JSON skin {jp} (transient — do not store)")
    if args.validate:
        stats = validate_hif_roundtrip(sources, args.out_dir, db_path=args.db_path)
        print(f"validate OK: {stats}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
