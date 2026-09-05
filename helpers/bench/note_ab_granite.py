#!/usr/bin/env python3
"""Full factual A/B: production bge index vs granite-embedded sandbox, real endpoint.

S2b showed sectioned granite winning the deep probe in the harness (14/15
vs bge 11/15, embed_full_reembed appendix). Per user direction this runs
the DECISION-GRADE comparison — no shape simulations:

  leg A  production research.db, bge vectors + bge query embedder, the
         live /api/search endpoint (Flask test client, hybrid=true)
  leg B  /tmp sandbox copy of research.db with note_search.embedding
         rewritten from the granite section-vector checkpoint
         (/tmp/deep_probe_cand_granite97m_sectioned.json — same
         _embedding_text composition the rebuild uses), granite query
         embedder patched in; same endpoint, same questions, same window

Mechanics that make leg B faithful: at 14.5k rows the endpoint's vec0
KNN auto-routes past VEC0_KNN_MAX to the flat-matrix leg, which reads
the embedding column — so rewriting that column IS the model swap;
query_embedder is imported inside the handler, so patching the
rebuild_note_search module attribute redirects every request; connect()
is imported per call in get_db_connection, so patching
helpers.core.db.connect's default path points the app at the sandbox.
db_meta note_embed_model is stamped for correctness (serving only reads
dims, which match at 384).

Evals per leg: 27 eval-search questions (hybrid recall@5), 15 deep-probe
questions (hybrid + bm25 legs), endpoint latency p50/p95 (15x3).

Usage:
    python3 helpers/bench/note_ab_granite.py [--json out.json]
"""

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.bench.embed_runtime_bench import preflight_clean_state  # noqa: E402
from helpers.bench.note_deep_probe import QUESTIONS_PATH  # noqa: E402

SANDBOX = Path("/tmp/ab_granite_research.db")  # noqa: S108  # throwaway sandbox copy, single-user box
GRANITE_GGUF = REPO / "models/granite-embedding-97M-multilingual-r2-Q8_0.gguf"
GRANITE_CTX = 2048
VEC_CACHE = Path("/tmp/deep_probe_cand_granite97m_sectioned.json")  # noqa: S108  # probe checkpoint scratch


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vec: list[float]) -> list[float]:
    import math

    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def _flat_embedding(raw: object) -> list[float]:
    """llama.cpp's stubs type create_embedding's payload as
    list[float] | list[list[float]] (batch inputs return nested); a
    single-input call is flat at runtime — this is the honest cast."""
    from typing import cast

    return cast(list[float], raw)


def _rewrite_sandbox(fresh_fallback) -> tuple[int, int]:
    """Copy research.db to the sandbox; rewrite note_search.embedding from
    the granite checkpoint. Returns (rows, cache_misses_fresh_embedded)."""
    from helpers.maintenance.rebuild_note_search import _embedding_text

    shutil.copyfile(REPO / "memory/research.db", SANDBOX)
    cache: dict[str, list[float]] = json.loads(VEC_CACHE.read_text(encoding="utf-8"))
    conn = sqlite3.connect(SANDBOX)
    rows = conn.execute(
        "SELECT rowid, title, sector, section_title, content FROM note_search"
    ).fetchall()
    hits = misses = 0
    for rowid, title, sector, section_title, content in rows:
        base = _embedding_text(title, sector, section_title, content)
        vec = cache.get(_sha(base))
        if vec is None:
            vec = _normalize(fresh_fallback(base))
            misses += 1
        else:
            hits += 1
        conn.execute(
            "UPDATE note_search SET embedding = ? WHERE rowid = ?", (json.dumps(vec), rowid)
        )
    conn.execute("CREATE TABLE IF NOT EXISTS db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', ?)",
        ("granite-embedding-97m-r2",),
    )
    conn.commit()
    conn.close()
    return hits, misses


_Q_MODEL = None


def _granite_query_embedder():
    """(fn, dims) matching rebuild_note_search.query_embedder's contract.
    Model memoized — a fresh Llama load per request added ~600ms to every
    query in the first A/B run (p50 808ms) before this fix."""
    global _Q_MODEL
    if _Q_MODEL is None:
        from llama_cpp import Llama

        _Q_MODEL = Llama(str(GRANITE_GGUF), embedding=True, n_ctx=GRANITE_CTX, verbose=False)

    def embed(q: str) -> list[float]:
        return _normalize(
            _flat_embedding(_Q_MODEL.create_embedding(input=[q])["data"][0]["embedding"])
        )

    return embed, 384


def _build_sandbox_matrix():
    """Materialize the sandbox f32 matrix from the sandbox's granite
    embeddings — composite file_path#anchor ids, matching what the
    rebuild's _refresh_embed_matrix writes. (embed_matrix.from_note_search
    is a legacy pre-sectioning reader with BARE file_path ids — never use
    it for the sectioned index; its ids fail the endpoint's staleness
    gate. First A/B run bug: only the embedding column was swapped, so
    the endpoint served granite queries against the BGE production
    matrix — cross-model cosine garbage, search 6/27.)"""
    import numpy as np
    from helpers.core.embed_matrix import EmbedMatrixStore

    conn = sqlite3.connect(SANDBOX)
    rows = conn.execute(
        "SELECT file_path || '#' || anchor, embedding FROM note_search "
        "WHERE embedding IS NOT NULL ORDER BY file_path, anchor"
    ).fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    emb = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
    store = EmbedMatrixStore(
        matrix_path=Path("/tmp/ab_granite_matrix.f32"),  # noqa: S108  # throwaway sandbox artifact
        meta_path=Path("/tmp/ab_granite_matrix.json"),  # noqa: S108  # throwaway sandbox artifact
    )
    store.build(ids, emb, model="note_search")
    print(f"sandbox matrix: {len(ids)} rows built", flush=True)
    return store


def _eval_endpoint(client, label: str) -> dict:
    """Search eval + deep probe + latency through the real endpoint."""
    eval_qs = json.load(open(REPO / "helpers/misc/embed_eval_questions.json"))["search"]
    deep_qs = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]

    def top5(query: str, hybrid: bool) -> list[str]:
        url = f"/api/search?q={quote(query)}&limit={'25' if hybrid else '5'}"
        if hybrid:
            url += "&hybrid=true"
        r = client.get(url)
        if r.status_code != 200:
            raise SystemExit(f"[{label}] search failed ({r.status_code}): {r.get_json()}")
        return [h["file_path"] for h in r.get_json()["results"][:5]]

    search_hits = sum(any(e in top5(q["query"], True) for e in q["expect"]) for q in eval_qs)
    deep_h = sum(any(e in top5(q["query"], True) for e in q["expect"]) for q in deep_qs)
    deep_b = sum(any(e in top5(q["query"], False) for e in q["expect"]) for q in deep_qs)

    samples = []
    for q in deep_qs * 3:
        t0 = time.perf_counter()
        client.get(f"/api/search?q={quote(q['query'])}&hybrid=true&limit=25")
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p50, p95 = samples[len(samples) // 2], samples[int(len(samples) * 0.95)]

    out = {
        "search_hybrid": f"{search_hits}/{len(eval_qs)}",
        "deep_hybrid": f"{deep_h}/{len(deep_qs)}",
        "deep_bm25": f"{deep_b}/{len(deep_qs)}",
        "p50_ms": round(p50),
        "p95_ms": round(p95),
    }
    print(f"{label}: {json.dumps(out)}", flush=True)
    return out


def run() -> dict:
    preflight_clean_state("note_ab_granite.py")
    import app as A

    client = A.app.test_client()

    # Leg A: production bge, untouched wiring, same window.
    print("=== leg A: production bge (live endpoint) ===", flush=True)
    leg_a = _eval_endpoint(client, "prod-bge")

    # Leg B: granite sandbox — rewrite embeddings, patch connect + embedder.
    print("\n=== leg B: granite sandbox (same endpoint, patched db + embedder) ===", flush=True)
    doc_model = None

    def fresh_fallback(base: str) -> list[float]:
        nonlocal doc_model
        if doc_model is None:
            from llama_cpp import Llama

            doc_model = Llama(str(GRANITE_GGUF), embedding=True, n_ctx=GRANITE_CTX, verbose=False)
        return _flat_embedding(doc_model.create_embedding(input=[base])["data"][0]["embedding"])

    hits, misses = _rewrite_sandbox(fresh_fallback)
    print(f"sandbox rewrite: {hits} cache hits, {misses} fresh", flush=True)

    import helpers.core.db as dbmod
    import helpers.maintenance.rebuild_note_search as R

    sandbox_store = _build_sandbox_matrix()
    _orig_connect, _orig_query_embedder, _orig_flat = (
        dbmod.connect,
        R.query_embedder,
        A._flat_knn_map,
    )
    # Intentional seams (call-time imports are the clean monkeypatch
    # points — the A/B runs the real endpoint against the sandbox DB).
    dbmod.connect = lambda *a, **k: _orig_connect(str(SANDBOX), **k)  # ty: ignore[invalid-assignment]
    R.query_embedder = _granite_query_embedder  # ty: ignore[invalid-assignment]
    A._flat_knn_map = lambda q, p, store=None: _orig_flat(q, p, store=sandbox_store)  # ty: ignore[invalid-assignment]
    try:
        leg_b = _eval_endpoint(client, "sandbox-granite")
    finally:
        dbmod.connect, R.query_embedder, A._flat_knn_map = (
            _orig_connect,
            _orig_query_embedder,
            _orig_flat,
        )
        if doc_model is not None:
            del doc_model

    SANDBOX.unlink(missing_ok=True)
    Path("/tmp/ab_granite_matrix.f32").unlink(missing_ok=True)  # noqa: S108  # cleanup of the throwaway artifact
    Path("/tmp/ab_granite_matrix.json").unlink(missing_ok=True)  # noqa: S108  # cleanup of the throwaway artifact
    return {"prod_bge": leg_a, "sandbox_granite": leg_b}


if __name__ == "__main__":
    # No heavy work at import time.
    args = sys.argv[1:]
    json_out = None
    if "--json" in args:
        i = args.index("--json")
        json_out = args[i + 1]
        args = args[:i] + args[i + 2 :]
    report = run()
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {json_out}", file=sys.stderr)
