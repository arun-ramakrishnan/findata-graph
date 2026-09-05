#!/usr/bin/env python3
"""Deep-content probe for the note_search surface (note sectioning, S3).

Acceptance measurement for the note_section_search proposal: questions whose
answers live BEYOND token 512 of the pre-sectioning whole-note embedding
base (bge's trained cap) — the text the old cosine leg never saw. Three
legs, recall@5:

- ``after_hybrid``  — live sectioned index, /api/search hybrid (25-candidate
  page re-ranked, the embed_eval posture)
- ``after_bm25``    — live sectioned index, plain FTS
- ``before_hybrid`` — pre-sectioning simulation: the legacy one-row-per-note
  extraction (frozen copy of eb230fdd's logic), 512-capped vectors from the
  content-hash cache (fallback: fresh embed — same model, deterministic),
  a throwaway FTS5 index, and the same RRF fusion the endpoint used
  (k=60, BM25 page position + GLOBAL cosine rank, 25-doc page)

Criteria: after_hybrid >= after_bm25 AND after_hybrid >= before_hybrid.
Also times the hybrid endpoint and the whole-corpus KNN map at the
sectioned row count (S3 acceptance #4).

Usage:
    python3 helpers/bench/note_deep_probe.py            # probe + latency
    python3 helpers/bench/note_deep_probe.py --json out # machine-readable
"""

import json
import math
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DB_PATH = REPO / "memory" / "research.db"
QUESTIONS_PATH = Path(__file__).resolve().parent / "note_deep_probe_questions.json"
RRF_K = 60
PAGE = 25  # candidate page the endpoint fuses over (embed_eval posture)

# --- frozen pre-sectioning extraction (eb230fdd rebuild_note_search) -------- #
# One row per note: title/sector from entities (entity docs) or the H1
# (newsletters); body = frontmatter-stripped, noise-cleaned. Embedding base
# was f"{title}\n{sector}\n{content[:8000]}" — the 512-token cap then bit on
# the embedder side (llama.cpp), which the probe reproduces by embedding with
# the same n_ctx=512 production path.

_HTML_DIV_OPEN = re.compile(r"<div[^>]*>")
_IMG_EMBED = re.compile(r"!\[\[[^\]]*\]\]")
_HTML_IMG_TAG = re.compile(r"<img[^>]*/?>")
_WS_RE = re.compile(r"\s+")
_H1_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.M)

_DOC_TYPE_BY_PREFIX = [
    ("Companies/", "company"),
    ("Sectors/", "sector"),
    ("Super_Sectors/", "super_sector"),
    ("The_Chatter/", "chatter"),
    ("Points_And_Figures/", "points_and_figures"),
    ("Plotlines/", "plotlines"),
]


def _clean_body(body: str) -> str:
    body = _HTML_DIV_OPEN.sub("", body)
    body = _IMG_EMBED.sub("", body)
    body = _HTML_IMG_TAG.sub("", body)
    return _WS_RE.sub(" ", body).strip()


def _doc_type_for(rel_path: Path) -> str | None:
    s = rel_path.as_posix()
    for prefix, dtype in _DOC_TYPE_BY_PREFIX:
        if s.startswith(prefix):
            return dtype
    return None


def _legacy_rows() -> list[tuple[str, str, str, str, str]]:
    """(doc_type, rel_path, title, sector, content) per note, eb230fdd shape."""
    from helpers.core.frontmatter import split_frontmatter_with_title

    findata = REPO / "findata"
    ent: dict[str, tuple[str, str]] = {}
    econn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for r in econn.execute(
        "SELECT file_path, normalized_name, sector_classification FROM entities "
        "WHERE file_path IS NOT NULL"
    ):
        ent[r[0]] = (r[1] or "", r[2] or "")
    econn.close()

    rows = []
    for p in sorted(findata.rglob("*.md")):
        rel = p.relative_to(findata)
        dtype = _doc_type_for(rel)
        if dtype is None:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        _title, body = split_frontmatter_with_title(text)
        if dtype in ("chatter", "points_and_figures", "plotlines"):
            body = _clean_body(body)
            h1 = _H1_TITLE.search(body)
            title = h1.group(1).strip() if h1 else rel.stem
            sector = ""
        else:
            name, sector = ent.get(rel.as_posix(), ("", ""))
            title = name or _title or ""
        rows.append((dtype, f"findata/{rel.as_posix()}", title, sector, body))
    return rows


def _legacy_vectors(bases: list[str]) -> list[list[float]]:
    """Cache-first vectors for the legacy bases (sha256+model keyed), fresh
    embed on miss — same n_ctx=512 production path, so misses reproduce the
    old truncated vectors exactly. Freshly embedded vectors are spilled to a
    scratch JSON (/tmp) keyed by base sha256 so reruns skip the ~6-minute
    re-embed."""
    from helpers.core.embed_cache import _hash
    from helpers.core import local_embedder
    from helpers.core.local_embedder import MODEL_ID

    # Model-keyed: a model swap orphans the old scratch rather than reusing
    # it — cross-model cosine is garbage (the 2026-09-05 granite swap lesson).
    scratch = Path(f"/tmp/note_deep_probe_legacy_vecs_{MODEL_ID}.json")  # noqa: S108  # throwaway probe scratch, single-user box
    scratch_map: dict[str, list[float]] = {}
    if scratch.exists():
        scratch_map = json.loads(scratch.read_text(encoding="utf-8"))

    conn = sqlite3.connect(f"file:{REPO / 'memory/embed_store.db'}?mode=ro", uri=True)
    from helpers.core.vec_search import _attach_vec_db

    _attach_vec_db(conn)
    cached = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT text_hash, embedding FROM vecdb.embed_cache WHERE model = ?",
            (MODEL_ID,),
        )
    }
    conn.close()

    embed = local_embedder.embed_document
    out, misses, spilled = [], 0, {}
    for t in bases:
        h = _hash(t)
        j = cached.get(h)
        if j is not None:
            out.append(json.loads(j))
            continue
        v = scratch_map.get(h)
        if v is None:
            v = embed(t)
            misses += 1
            spilled[h] = v
        out.append(v)
    if spilled:
        scratch.write_text(json.dumps({**scratch_map, **spilled}), encoding="utf-8")
    print(
        f"  legacy vectors: {len(bases) - misses} cache hits, {misses} fresh",
        file=sys.stderr,
    )
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def run() -> int:  # noqa: C901  # bench script: one straight-line report
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]

    # --- AFTER legs: the live endpoint ------------------------------------ #
    import app as A

    client = A.app.test_client()

    def top5(query: str, hybrid: bool) -> list[str]:
        url = f"/api/search?q={quote(query)}&limit={PAGE if hybrid else 5}"
        if hybrid:
            url += "&hybrid=true"
        r = client.get(url)
        if r.status_code != 200:
            raise SystemExit(f"search failed ({r.status_code}): {r.get_json()}")
        return [h["file_path"] for h in r.get_json()["results"][:5]]

    # --- BEFORE leg: pre-sectioning simulation ---------------------------- #
    print("building pre-sectioning simulation (legacy rows + FTS + vectors)...", file=sys.stderr)
    rows = _legacy_rows()
    bases = [f"{r[2]}\n{r[3]}\n{r[4][:8000]}" for r in rows]
    vecs = _legacy_vectors(bases)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    lconn = sqlite3.connect(tmp.name)
    lconn.execute(
        "CREATE VIRTUAL TABLE note_search USING fts5("
        "doc_type, file_path UNINDEXED, title, sector, content, "
        "tokenize = 'porter unicode61')"
    )
    lconn.executemany(
        "INSERT INTO note_search (doc_type, file_path, title, sector, content) "
        "VALUES (?, ?, ?, ?, ?)",
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows],
    )
    lconn.commit()

    from helpers.maintenance.rebuild_note_search import query_embedder

    q_embed, _dims = query_embedder()
    q_vecs = {q["query"]: q_embed(q["query"]) for q in questions}

    def before_top5(query: str) -> list[str]:
        qv = q_vecs[query]
        # BM25 candidate page (rank-sorted), then GLOBAL cosine ranks over the
        # whole legacy corpus — the A1 semantics the endpoint ran with.
        page = [
            r[0]
            for r in lconn.execute(
                "SELECT file_path FROM note_search WHERE note_search MATCH ? ORDER BY rank LIMIT ?",
                (query, PAGE),
            )
        ]
        sims = sorted(
            ((i, _cosine(qv, v)) for i, v in enumerate(vecs)),
            key=lambda t: t[1],
            reverse=True,
        )
        global_rank = {rows[i][1]: pos for pos, (i, _s) in enumerate(sims)}
        fused = [
            (1.0 / (RRF_K + pos) + 1.0 / (RRF_K + global_rank[fp]), fp)
            for pos, fp in enumerate(page)
        ]
        fused.sort(key=lambda t: t[0], reverse=True)
        return [fp for _s, fp in fused[:5]]

    # --- probe ------------------------------------------------------------- #
    res = []
    for q in questions:
        bm = top5(q["query"], hybrid=False)
        hy = top5(q["query"], hybrid=True)
        be = before_top5(q["query"])
        res.append(
            {
                "id": q["id"],
                "category": q["category"],
                "after_bm25": any(e in bm for e in q["expect"]),
                "after_hybrid": any(e in hy for e in q["expect"]),
                "before_hybrid": any(e in be for e in q["expect"]),
                "hybrid_top": hy[0] if hy else None,
            }
        )
    lconn.close()
    Path(tmp.name).unlink(missing_ok=True)

    n = len(res)
    bm25 = sum(1 for r in res if r["after_bm25"])
    hyb = sum(1 for r in res if r["after_hybrid"])
    before = sum(1 for r in res if r["before_hybrid"])
    print(f"\ndeep-content probe (recall@5, n={n})")
    print(f"{'question':24s} {'cat':9s} {'bm25':5s} {'hybrid':7s} {'before':7s}")
    for r in res:
        print(
            f"{r['id']:24s} {r['category']:9s} "
            f"{str(r['after_bm25']):5s} {str(r['after_hybrid']):7s} "
            f"{str(r['before_hybrid']):7s}"
        )
    print(f"\ntotals: after_bm25={bm25}/{n}  after_hybrid={hyb}/{n}  before_hybrid={before}/{n}")
    ok1 = hyb >= bm25
    ok2 = hyb >= before
    print(f"criterion hybrid >= bm25:      {'PASS' if ok1 else 'FAIL'}")
    print(f"criterion hybrid >= before:    {'PASS' if ok2 else 'FAIL'}")

    # --- latency (S3 acceptance #4) ---------------------------------------- #
    # The vec0 KNN caps at k=4096 < 14.5k section rows, so the production
    # cosine leg is the flat-matrix map — measure that (plus the endpoint).
    qv0 = q_vecs[questions[0]["query"]]
    t0 = time.perf_counter()
    A._flat_knn_map(qv0, ["findata/Companies/Agriculture/Avanti_Feeds.md#1"])
    knn_ms = (time.perf_counter() - t0) * 1000
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    nvec = conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
    conn.close()

    samples = []
    for q in questions * 3:
        t0 = time.perf_counter()
        client.get(f"/api/search?q={quote(q['query'])}&hybrid=true&limit=25")
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[int(len(samples) * 0.95)]
    print(
        f"\nlatency @ {nvec} section rows: KNN map {knn_ms:.1f}ms | "
        f"hybrid endpoint p50 {p50:.0f}ms p95 {p95:.0f}ms (n={len(samples)})"
    )

    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(
            json.dumps(
                {
                    "results": res,
                    "totals": {
                        "after_bm25": bm25,
                        "after_hybrid": hyb,
                        "before_hybrid": before,
                        "n": n,
                    },
                    "latency_ms": {"knn": knn_ms, "p50": p50, "p95": p95, "rows": nvec},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {out}", file=sys.stderr)
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(run())
