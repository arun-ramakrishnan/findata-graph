#!/usr/bin/env python3
"""B3 spike: FTS5 vs DuckDB fts (match_bm25) parity on live note_search.

Local-eval only. Reads memory/research.db READ-ONLY; builds a throwaway
DuckDB fts index under /tmp; never writes memory/.

Arms (per query, top-5 notes, ANY-OF recall@5 vs helpers/misc/embed_eval_questions.json):
  1. FTS5 baseline  — replicates app.py AND-first/OR-fill + note-level dedup.
  2. DuckDB fts     — same OR expression via fts_main_notes.match_bm25.
  3. Hybrid each    — RRF k=60 fusion with a SHARED numpy vector leg
                      (bge-small query_embedder vs stored 384-d f32
                      BLOB, post-#223), so the hybrid delta isolates
                      lexical-rank differences.

Usage:
    .venv/bin/python3 helpers/bench/fts_duckdb_parity.py [--limit N] [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

EVAL_SET = REPO / "helpers" / "misc" / "embed_eval_questions.json"
WINDOWS = 1024  # app.py inner LIMIT
K_RRF = 60


def load_eval(path: Path = EVAL_SET) -> list[dict]:
    return json.loads(path.read_text())["search"]


def export_corpus(db_path: str | Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT rowid, doc_type, file_path, title, sector, content,"
            " section_title, anchor, embedding FROM note_search"
        ).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        out.append(
            {
                "rowid": r[0],
                "doc_type": r[1],
                "file_path": r[2],
                "title": r[3] or "",
                "sector": r[4] or "",
                "content": r[5] or "",
                "section_title": r[6] or "",
                "anchor": r[7],
                "embedding": r[8],
            }
        )
    return out


def fts5_search(db_path: str | Path, or_expr: str, and_expr: str) -> list[str]:
    """Replicate app.py candidate generation; return file_paths note-level, rank-ordered."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()

        def fetch(expr: str) -> list[tuple[str, float]]:
            cur.execute(
                "SELECT file_path, rank FROM ("
                " SELECT file_path, rank,"
                " ROW_NUMBER() OVER (PARTITION BY file_path ORDER BY rank) AS rn"
                " FROM (SELECT file_path, rank FROM note_search"
                " WHERE note_search MATCH ? ORDER BY rank LIMIT ?)) WHERE rn = 1"
                " ORDER BY rank LIMIT ?",
                (expr, WINDOWS, WINDOWS),
            )
            return cur.fetchall()

        rows: list[tuple[str, float]] = []
        if and_expr:
            try:
                rows.extend(fetch(and_expr))
            except sqlite3.OperationalError:
                pass
        if len(rows) < WINDOWS and or_expr != and_expr:
            seen = {fp for fp, _ in rows}
            try:
                for fp, rank in fetch(or_expr):
                    if fp not in seen:
                        seen.add(fp)
                        rows.append((fp, rank))
            except sqlite3.OperationalError:
                pass
        return [fp for fp, _ in rows]
    finally:
        con.close()


def build_duckdb(corpus: list[dict], path: Path):
    import duckdb

    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        con.execute("INSTALL fts")
        con.execute("LOAD fts")
        con.execute(
            "CREATE TABLE notes(id INTEGER, doc_type VARCHAR, file_path VARCHAR,"
            " title VARCHAR, sector VARCHAR, content VARCHAR,"
            " section_title VARCHAR, anchor VARCHAR)"
        )
        con.executemany(
            "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    c["rowid"],
                    c["doc_type"],
                    c["file_path"],
                    c["title"],
                    c["sector"],
                    c["content"],
                    c["section_title"],
                    c["anchor"],
                )
                for c in corpus
            ],
        )
        con.execute(
            "PRAGMA create_fts_index('notes','id','title','sector','content','section_title')"
        )
    finally:
        con.close()


_DDB_CON = None


def duckdb_search(db_path: Path, or_expr: str) -> list[str]:
    import duckdb

    global _DDB_CON
    if _DDB_CON is None:
        _DDB_CON = duckdb.connect(str(db_path), read_only=True)
    rows = _DDB_CON.execute(
        "SELECT file_path, fts_main_notes.match_bm25(id, ?) AS s FROM notes"
        " WHERE s IS NOT NULL ORDER BY s DESC LIMIT ?",
        [or_expr, WINDOWS],
    ).fetchall()
    seen: set[str] = set()
    out: list[str] = []
    for fp, _ in rows:
        if fp not in seen:
            seen.add(fp)
            out.append(fp)
    return out


def build_vector_leg(corpus: list[dict]):
    """Return (matrix_ids, matrix, norms) as numpy arrays + note index.

    bulk_data_lanes S6 repair: embeddings are f32 BLOB post-#223 (raw
    json.loads died on every section — UnicodeDecodeError is a ValueError
    subclass, so the silent ``continue`` skipped 100% of the corpus and
    main() crashed on the empty matrix). Decodes via the tolerant
    ``vec_codec.load_vec`` and computes in float32, mirroring production
    cosine. Fails LOUD on an empty leg — never hand numpy an empty
    matrix again.
    """
    import numpy as np

    from helpers.core.vec_codec import load_vec

    ids: list[str] = []
    vecs: list[list[float]] = []
    for c in corpus:
        if not c["embedding"]:
            continue
        try:
            v = load_vec(c["embedding"])
        except TypeError, ValueError:
            continue
        if not v:
            continue
        ids.append(c["file_path"])
        vecs.append(v)
    if not vecs:
        raise RuntimeError(
            "build_vector_leg: 0 embedded sections parsed from a corpus of"
            f" {len(corpus)} rows — embedding storage changed?"
        )
    m = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1)
    norms[norms == 0] = 1.0
    return ids, m, norms


def vector_ranking(q_vec: list[float], ids: list[str], m, norms) -> dict[str, int]:
    """file_path -> global cosine rank (best section wins, mirrors _note_best)."""
    import numpy as np

    q = np.asarray(q_vec, dtype=np.float32)
    nq = np.linalg.norm(q) or 1.0
    sims = (m @ q) / (norms * nq)
    best: dict[str, float] = {}
    for fp, s in zip(ids, sims.tolist()):
        if s > best.get(fp, -2.0):
            best[fp] = s
    order = sorted(best, key=lambda fp: best[fp], reverse=True)
    return {fp: pos for pos, fp in enumerate(order)}


def rrf_fuse(lexical: list[str], cos_rank: dict[str, int], k: int = K_RRF) -> list[str]:
    worst = len(cos_rank)
    scored = [
        ((1.0 / (k + i + 1)) + (1.0 / (k + cos_rank.get(fp, worst) + 1)), fp)
        for i, fp in enumerate(lexical)
    ]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [fp for _, fp in scored]


def recall_at_5(ranked: list[str], expect: list[str]) -> bool:
    return any(e in ranked[:5] for e in expect)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default=str(REPO / "memory" / "research.db"))
    args = ap.parse_args()

    from helpers.maintenance.rebuild_doc_search import fts_match_expr
    from helpers.maintenance.rebuild_note_search import query_embedder

    eval_set = load_eval()
    if args.limit:
        eval_set = eval_set[: args.limit]
    print(f"queries: {len(eval_set)}, db: {args.db}")

    corpus = export_corpus(args.db)
    print(f"corpus rows: {len(corpus)}")

    tmp = Path(tempfile.mkdtemp(prefix="fts_parity_"))
    ddb = tmp / "notes.duckdb"
    t0 = time.perf_counter()
    build_duckdb(corpus, ddb)
    print(f"duckdb build: {(time.perf_counter() - t0) * 1000:.0f} ms -> {ddb}")

    ids, m, norms = build_vector_leg(corpus)
    print(f"vector leg: {len(ids)} embedded sections")
    embed_q, qdims = query_embedder()
    print(f"query embedder: {embed_q.__module__}.{getattr(embed_q, '__name__', '?')} dim={qdims}")

    cats: Counter = Counter()
    hits = Counter()
    hyb_hits = Counter()
    rows_out: list[dict] = []
    lat_fts: list[float] = []
    lat_ddb: list[float] = []

    for item in eval_set:
        q, expect, cat = item["query"], item["expect"], item["category"]
        or_expr = fts_match_expr(q)
        and_expr = or_expr.replace(" OR ", " ") if or_expr else ""
        q_vec = embed_q(q)

        t = time.perf_counter()
        lex_fts = fts5_search(args.db, or_expr, and_expr)
        lat_fts.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        lex_ddb = duckdb_search(ddb, or_expr)
        lat_ddb.append((time.perf_counter() - t) * 1000)

        crank = vector_ranking(q_vec, ids, m, norms)
        hyb_fts = rrf_fuse(lex_fts, crank)
        hyb_ddb = rrf_fuse(lex_ddb, crank)

        h1, h2 = recall_at_5(lex_fts, expect), recall_at_5(lex_ddb, expect)
        hh1, hh2 = recall_at_5(hyb_fts, expect), recall_at_5(hyb_ddb, expect)
        cats[cat] += 1
        hits[("fts5", cat)] += h1
        hits[("duckdb", cat)] += h2
        hyb_hits[("fts5", cat)] += hh1
        hyb_hits[("duckdb", cat)] += hh2
        rows_out.append(
            {
                "id": item["id"],
                "cat": cat,
                "query": q,
                "or_expr": or_expr,
                "fts5_top5": lex_fts[:5],
                "duckdb_top5": lex_ddb[:5],
                "fts5_hit": h1,
                "duckdb_hit": h2,
                "hyb_fts_hit": hh1,
                "hyb_duckdb_hit": hh2,
            }
        )

    def pct(a: int, b: int) -> str:
        return f"{a}/{b}={a / b:.2f}" if b else "n/a"

    print("\n== lexical recall@5 (ANY-OF) ==")
    for cat in sorted(cats):
        print(
            f"  {cat:10s} n={cats[cat]:2d}  FTS5 {pct(hits[('fts5', cat)], cats[cat])}"
            f"  duckdb {pct(hits[('duckdb', cat)], cats[cat])}"
        )
    n = len(eval_set)
    t_fts = sum(hits[("fts5", c)] for c in cats)
    t_ddb = sum(hits[("duckdb", c)] for c in cats)
    print(f"  {'ALL':10s} n={n:2d}  FTS5 {pct(t_fts, n)}  duckdb {pct(t_ddb, n)}")

    print("\n== hybrid (RRF k=60 + shared vector leg) recall@5 ==")
    for cat in sorted(cats):
        print(
            f"  {cat:10s} n={cats[cat]:2d}  FTS5+vec {pct(hyb_hits[('fts5', cat)], cats[cat])}"
            f"  duckdb+vec {pct(hyb_hits[('duckdb', cat)], cats[cat])}"
        )
    h_fts = sum(hyb_hits[("fts5", c)] for c in cats)
    h_ddb = sum(hyb_hits[("duckdb", c)] for c in cats)
    print(f"  {'ALL':10s} n={n:2d}  FTS5+vec {pct(h_fts, n)}  duckdb+vec {pct(h_ddb, n)}")

    import statistics

    # Warm pass: persistent DuckDB connection is hot now; re-time lexical legs.
    warm_fts, warm_ddb = [], []
    for item in eval_set:
        or_expr = fts_match_expr(item["query"])
        and_expr = or_expr.replace(" OR ", " ") if or_expr else ""
        t = time.perf_counter()
        fts5_search(args.db, or_expr, and_expr)
        warm_fts.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        duckdb_search(ddb, or_expr)
        warm_ddb.append((time.perf_counter() - t) * 1000)

    print("\n== lexical latency ms (per query; cold incl. connect) ==")
    print(
        f"  FTS5   mean {statistics.mean(lat_fts):.1f}  p50 {statistics.median(lat_fts):.1f}"
        f"  max {max(lat_fts):.1f}"
    )
    print(
        f"  duckdb mean {statistics.mean(lat_ddb):.1f}  p50 {statistics.median(lat_ddb):.1f}"
        f"  max {max(lat_ddb):.1f}"
    )
    print(
        f"  warm FTS5   mean {statistics.mean(warm_fts):.1f}  p50 {statistics.median(warm_fts):.1f}"
        f"  max {max(warm_fts):.1f}"
    )
    print(
        f"  warm duckdb mean {statistics.mean(warm_ddb):.1f}  p50 {statistics.median(warm_ddb):.1f}"
        f"  max {max(warm_ddb):.1f}"
    )

    print("\n== per-query misses (lexical) ==")
    for r in rows_out:
        if not (r["fts5_hit"] and r["duckdb_hit"]):
            print(
                f"  {r['id']:9s} [{r['cat']}] {r['query']!r} FTS5={'HIT' if r['fts5_hit'] else 'miss'}"
                f" duckdb={'HIT' if r['duckdb_hit'] else 'miss'}"
                f" hybFTS={'HIT' if r['hyb_fts_hit'] else 'miss'}"
                f" hybDDB={'HIT' if r['hyb_duckdb_hit'] else 'miss'}"
            )

    out_json = tmp / "parity_results.json"
    out_json.write_text(json.dumps(rows_out, indent=1))
    print(f"\nper-query dump: {out_json}")
    print(f"duckdb throwaway: {ddb} (delete when done)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
