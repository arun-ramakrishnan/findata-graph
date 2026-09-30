"""Regenerate src-vue/test/fixtures.js from the live SQLite sidecars.

Mirrors the data-layer queries in `findata-core` (core/src/lib.rs) so the
headless smoke harness (`src-vue/test/main.js`) speaks the same shapes
the real Tauri app does, without a database connection in the browser.

  make fixtures   → rewrite test/fixtures.js (ruff-clean, ~80 KB)

Two read-only sources: `memory/research.db` (graph + note FTS) and
`memory/doc_search.db` (doc/ FTS sidecar for the docs browser).

The DB path is found the same way `find_root()` does: `$FINDATA_GRAPH_ROOT`,
else walk up from the repo root. Default: this repo's memory/research.db.
"""

import json
import os
import sqlite3
from pathlib import Path


def find_db() -> Path:
    root = os.environ.get("FINDATA_GRAPH_ROOT")
    if root and Path(root, "memory/research.db").exists():
        return Path(root, "memory/research.db")
    here = Path(__file__).resolve()
    for d in (here, *here.parents):
        cand = (
            d.parent / "memory" / "research.db"
            if d.name == "test"
            else d / "memory" / "research.db"
        )
        if cand.exists():
            return cand
        cand = d / "memory" / "research.db"
        if cand.exists():
            return cand
    raise FileNotFoundError("memory/research.db not found")


def main() -> None:
    db_path = find_db()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    doc_db_path = db_path.parent / "doc_search.db"
    doc_conn = sqlite3.connect(f"file:{doc_db_path}?mode=ro", uri=True)
    try:
        ego = _ego(conn, "CEAT")
        ego_names = [n["name"] for n in ego["nodes"]]
        fixtures = {
            "stats": _stats(conn),
            "sectors": _sectors(conn),
            "ego": ego,
            "search": _search(conn, "shrimp feed"),
            "note": _note(db_path.parent.parent, "findata/Companies/Agriculture/Avanti_Feeds.md"),
            "suggest": _suggest(conn, "CEAT"),
            "cloud": _cloud_acquired(conn),
            "docs": _docs(doc_conn),
            "metrics": _metrics(conn, "CEAT"),
            "metric_values": _metric_values(conn, "pagerank", 200, ego_names),
            "hyperedges": _hyperedges(conn, "CEAT"),
            "similar": _similar(conn, "findata/Companies/Agriculture/Avanti_Feeds.md", 10),
            "hybrid": _search(conn, "shrimp feed"),
        }
    finally:
        conn.close()
        doc_conn.close()

    out = f"export const FIXTURES = {json.dumps(fixtures, indent=2)};\n"
    dest = Path(__file__).parent / "fixtures.js"
    dest.write_text(out, encoding="utf-8")
    print(f"wrote {dest} ({len(out):,} bytes, {len(fixtures)} fixtures)")


def _stats(conn: sqlite3.Connection) -> dict:
    entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    entity_types = [
        {"type": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM entities GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
    ]
    edge_types = [
        {"type": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT edge_type, COUNT(*) AS n FROM graph_edges GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
    ]
    return {
        "entities": entities,
        "edges": edges,
        "entity_types": entity_types,
        "edge_types": edge_types,
    }


def _sectors(conn: sqlite3.Connection) -> list:
    return [
        {"name": r[0], "entity_type": r[1], "file_path": r[2]}
        for r in conn.execute(
            "SELECT name, entity_type, file_path FROM entities "
            "WHERE entity_type IN ('sector', 'super_sector', 'theme') "
            "ORDER BY CASE entity_type WHEN 'super_sector' THEN 0 WHEN 'sector' THEN 1 ELSE 2 END, name"
        ).fetchall()
    ]


def _cloud_acquired(conn: sqlite3.Connection) -> dict:
    edges = [
        {"source": r[0], "target": r[1], "edge_type": r[2]}
        for r in conn.execute(
            "SELECT source, target, edge_type FROM graph_edges WHERE edge_type = 'acquired'"
        ).fetchall()
    ]
    names = set()
    for e in edges:
        names.add(e["source"])
        names.add(e["target"])
    type_map = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT name, entity_type FROM entities WHERE name IN ({})".format(  # noqa: S608  # placeholder list only; values bound
                ",".join("?" for _ in names)
            ),
            tuple(names),
        ).fetchall()
    }
    nodes = [
        {"id": n, "label": n, "entity_type": type_map.get(n, "unknown")} for n in sorted(names)
    ]
    relationship_types = [
        {"edge_type": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT edge_type, COUNT(*) FROM graph_edges GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "relationship_types": relationship_types,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "as_of": None,
    }


def _ego(conn: sqlite3.Connection, name: str) -> dict:
    rows = conn.execute(
        "SELECT edge_type, source, target, properties, weight FROM graph_edges "
        "WHERE source = ?1 OR target = ?1 ORDER BY COALESCE(weight, 0) DESC, id LIMIT 800",
        (name,),
    ).fetchall()
    edges = []
    for r in rows:
        props = r[3]
        try:
            props = json.loads(props) if props else None
        except json.JSONDecodeError, TypeError:
            props = None
        edges.append(
            {
                "edge_type": r[0],
                "source": r[1],
                "target": r[2],
                "properties": props,
                "weight": r[4],
            }
        )
    names = [name]
    seen = {name}
    for e in edges:
        for n in (e["source"], e["target"]):
            if n != name and n not in seen:
                seen.add(n)
                names.append(n)
    entity_map = {
        r[0]: {"entity_type": r[1], "file_path": r[2]}
        for r in conn.execute(
            "SELECT name, entity_type, file_path FROM entities WHERE name IN ({})".format(  # noqa: S608  # placeholder list only; values bound
                ",".join("?" for _ in names)
            ),
            names,
        ).fetchall()
    }
    nodes = []
    for n in names:
        meta = entity_map.get(n, {"entity_type": "unknown", "file_path": None})
        nodes.append(
            {
                "name": n,
                "entity_type": meta["entity_type"],
                "file_path": meta["file_path"],
                "focal": n == name,
            }
        )
    truncated = len(rows) >= 800
    return {
        "name": name,
        "entity_type": entity_map.get(name, {}).get("entity_type", "unknown"),
        "file_path": entity_map.get(name, {}).get("file_path"),
        "edges": edges,
        "nodes": nodes,
        "truncated": truncated,
        "as_of": None,
    }


def _search(conn: sqlite3.Connection, q: str) -> dict:
    def fts_expr(text: str, joiner: str) -> str | None:
        tokens = [
            t for t in "".join(c if (c.isalnum() or c == "_") else " " for c in text).split() if t
        ]
        if not tokens:
            return None
        return joiner.join(f'"{t}"' for t in tokens)

    and_expr = fts_expr(q, " ")
    or_expr = fts_expr(q, " OR ") or and_expr
    limit = 20

    def search_page(expr: str) -> list:
        rows = conn.execute(
            "SELECT doc_type, file_path, title, sector, section_title, "
            "snippet(note_search, 4, '<mark>', '</mark>', '…', 12), rank "
            "FROM note_search WHERE note_search MATCH ? ORDER BY rank LIMIT 1024",
            (expr,),
        ).fetchall()
        best: dict[str, dict] = {}
        for r in rows:
            fp = r[1]
            if fp not in best or r[6] > best[fp]["_rank"]:
                best[fp] = {
                    "doc_type": r[0],
                    "file_path": r[1],
                    "title": r[2],
                    "sector": r[3],
                    "section_title": r[4],
                    "snippet": r[5],
                    "_rank": r[6],
                }
        return sorted(best.values(), key=lambda x: x.pop("_rank"), reverse=True)

    hits = search_page(and_expr)
    if len(hits) < limit:
        for h in search_page(or_expr):
            if h["file_path"] not in {x["file_path"] for x in hits}:
                hits.append(h)
                if len(hits) >= limit:
                    break
    total = conn.execute(
        "SELECT COUNT(DISTINCT file_path) FROM note_search WHERE note_search MATCH ?",
        (or_expr,),
    ).fetchone()[0]
    return {"results": hits, "total": total, "query": q.strip()}


def _note(root: Path, rel: str) -> dict:  # root = repo root (parent of memory/)
    path = root / rel
    path = root / rel
    markdown = path.read_text(encoding="utf-8")
    title = ""
    for line in markdown.splitlines()[:30]:
        t = line.strip()
        if t.startswith("# "):
            title = t[2:].strip()
            if title:
                break
    if not title:
        title = path.stem
    return {"path": rel, "title": title, "markdown": markdown}


def _suggest(conn: sqlite3.Connection, q: str) -> list:
    like_q = f"%{q.replace('%', '\\%').replace('_', '\\_').replace('\\', '\\\\')}%"
    prefix = f"{q.replace('%', '\\%').replace('_', '\\_').replace('\\', '\\\\')}%"
    rows = conn.execute(
        "SELECT name, entity_type, file_path FROM entities "
        "WHERE name LIKE ? ESCAPE '\\' "
        "ORDER BY CASE WHEN name = ? THEN 0 WHEN name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END, length(name), name LIMIT 10",
        (like_q, q, prefix),
    ).fetchall()
    return [{"name": r[0], "entity_type": r[1], "file_path": r[2]} for r in rows]


def _docs(doc_conn: sqlite3.Connection) -> list:
    """Mirror `findata-core::browse_docs(None)`: DISTINCT doc_search rows."""
    return [
        {"path": r[0], "title": r[1]}
        for r in doc_conn.execute(
            "SELECT file_path, max(title) FROM doc_search GROUP BY file_path ORDER BY file_path"
        ).fetchall()
    ]


def _metrics(conn: sqlite3.Connection, name: str) -> dict:
    """Mirror `findata-core::entity_metrics`: analytics (no payload metrics)
    + latest-first company metrics."""
    analytics = [
        {"metric": r[0], "value": r[1]}
        for r in conn.execute(
            "SELECT metric, value FROM graph_analytics WHERE entity_name = ? ORDER BY metric",
            (name,),
        ).fetchall()
        if r[0] not in ("link_prediction", "voterank")
    ]
    company = [
        {
            "label": r[0],
            "value_raw": r[1],
            "value_num": r[2],
            "unit": r[3],
            "period": r[4],
        }
        for r in conn.execute(
            "SELECT metric_label, value_raw, value_num, unit, period "
            "FROM company_metrics WHERE entity = ? ORDER BY id DESC LIMIT 100",
            (name,),
        ).fetchall()
    ]
    return {"analytics": analytics, "company": company}


def _metric_values(
    conn: sqlite3.Connection, metric: str, limit: int, extra_names: list[str] | None = None
) -> list:
    """Mirror `findata-core::metric_values`: scalar ranking, top-N for size,
    unioned with the fixture ego's nodes so smoke tint overlap is covered
    by construction (the live path fetches the full column)."""
    rows = [
        {"entity": r[0], "value": r[1]}
        for r in conn.execute(
            "SELECT entity_name, CAST(json_extract(value, '$.value') AS REAL) AS v "
            "FROM graph_analytics WHERE metric = ? "
            "AND json_extract(value, '$.value') IS NOT NULL "
            "ORDER BY v DESC LIMIT ?",
            (metric.lower(), limit),
        ).fetchall()
    ]
    have = {r["entity"] for r in rows}
    missing = [n for n in (extra_names or []) if n not in have]
    if missing:
        rows.extend(
            {"entity": r[0], "value": r[1]}
            for r in conn.execute(
                "SELECT entity_name, CAST(json_extract(value, '$.value') AS REAL) AS v "  # noqa: S608  # placeholder list only; values bound
                "FROM graph_analytics WHERE metric = ? AND entity_name IN ({}) ".format(
                    ",".join("?" for _ in missing)
                )
                + "AND json_extract(value, '$.value') IS NOT NULL",
                (metric.lower(), *missing),
            ).fetchall()
        )
        rows.sort(key=lambda r: r["value"], reverse=True)
    return rows


def _hyperedges(conn: sqlite3.Connection, name: str) -> list:
    """Mirror `findata-core::entity_hyperedges`: incident hyperedges with
    capped member lists, ordered by (edge_type, label)."""
    out = []
    for r in conn.execute(
        "SELECT he.id, he.edge_type, he.label, hi.role "
        "FROM hyper_edges he JOIN hyper_incidences hi ON hi.edge_id = he.id "
        "WHERE hi.entity_name = ? ORDER BY he.edge_type, he.label LIMIT 100",
        (name,),
    ).fetchall():
        members = [
            {"name": m[0], "role": m[1]}
            for m in conn.execute(
                "SELECT entity_name, role FROM hyper_incidences "
                "WHERE edge_id = ? ORDER BY entity_name LIMIT 50",
                (r[0],),
            ).fetchall()
        ]
        out.append({"id": r[0], "edge_type": r[1], "label": r[2], "role": r[3], "members": members})
    return out


def _similar(conn: sqlite3.Connection, path: str, limit: int) -> list:
    """Mirror `findata-core::similar_notes`: max section-pair cosine over
    the embedding BLOBs (384 f32 LE), self excluded. stdlib only."""
    import struct

    def vec(blob: bytes) -> list[float] | None:
        if blob is None or len(blob) != 384 * 4:
            return None
        return list(struct.unpack(f"<{384}f", blob))

    rows = [
        (r[0], r[1], r[2], vec(r[3]))
        for r in conn.execute(
            "SELECT file_path, title, sector, embedding FROM note_search "
            "WHERE embedding IS NOT NULL"
        ).fetchall()
    ]
    rows = [r for r in rows if r[3] is not None]
    query = [v for fp, _t, _s, v in rows if fp == path]
    if not query:
        return []

    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a)
        nb = sum(y * y for y in b)
        return dot / ((na**0.5) * (nb**0.5)) if na and nb else 0.0

    best: dict[str, dict] = {}
    for fp, title, sector, v in rows:
        if fp == path:
            continue
        s = max(cos(q, v) for q in query)
        if fp not in best or s > best[fp]["score"]:
            best[fp] = {"file_path": fp, "title": title, "sector": sector, "score": s}
    return sorted(best.values(), key=lambda h: h["score"], reverse=True)[:limit]


if __name__ == "__main__":
    main()
