#!/usr/bin/env python3
"""Embedding-model A/B trial harness — mirrors helpers/misc/embed_eval.py
semantics with a CANDIDATE model instead of the live bge-small index.

Born in the 2026-09-05/06 model-swap eval — the authoritative
record is now doc/improvements/archive/database/embed_full_reembed.md (the
doc/local notes were eliminated with the wrong early conclusions).
Settled verdicts: granite-97m-r2 sectioned BEAT bge (deep 14/15 vs
11/15, S2b + endpoint A/B) and is the PRODUCTION model since
2026-09-06; bge-small is the rollback. nomic / MiniLM-L6 / gte-small
ELIMINATED (quality/cost); their GGUFs were deleted at arc close —
legs fail fast on the missing file, do not re-download without a new
proposal. Four ops lessons are embedded in the design:

- SERIAL in-process embedding, never a pinned spawn pool: under desktop
  load a pinned worker cannot migrate off a stolen core — the pool
  measured 3x SLOWER than a floating single process (the #173 pinning
  doctrine is idle-box advice). 1T == 4T for bert forwards (sync-bound).
- Per-SURFACE vector-cache checkpoints (/tmp/embtrial_cache_<tag>.pkl,
  keys sha1(prefix+text) so a two-sided model's query/document vectors
  never collide) — a killed leg keeps its completed surfaces.
- Live per-64-text progress meters; decisive-surface-first order via
  SURFACES (default docs,companies,notes — docs discriminates models,
  search/vss sit at bge's ceiling).
- Question-JSON trap: vss/neighbors entries in embed_eval_questions.json
  carry no `id` field — only search/docs do.

Reopen triggers this harness serves (from the record): a concrete
long-context semantic retrieval need (granite @ 2k/4k ctx probe) or a
labeled-set revision where bge measurably fails.

Usage:
    python3 helpers/bench/embed_model_trial.py <tag> [-- SURFACES=docs]
Candidate GGUFs are NOT committed (all trial models deleted at arc
close). Re-fetch into models/ before running — sources:
  granite97m  mykor/granite-embedding-97m-multilingual-r2-GGUF (Q8_0;
              modern-bert arch, CLS pooling, 32k trained ctx, 384d)
  minilm6     LLukas22/all-MiniLM-L6-v2-GGUF (all-minilm-l6-v2_q8_0.gguf)
  gte_small   ggml-org/gte-small-Q8_0-GGUF (gte-small-q8_0.gguf)
  nomic       nomic-ai/nomic-embed-text-v1.5-GGUF (Q8_0; two-sided
              prefixes search_query:/search_document:, 768d)
NOTE: cstr GGUF conversions do NOT load on current llama.cpp — they lack
`tokenizer.ggml.token_type_count` (required by src/models/bert.cpp in
llama-cpp-python >= 0.3.x; the KV is NOT `bert.token_type_count`).
"""

import hashlib
import json
import math
import os
import pickle
import sys
import time
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MODELS = {
    "granite97m": dict(
        path=REPO / "models/granite-embedding-97M-multilingual-r2-Q8_0.gguf",
        q_prefix="",
        d_prefix="",
    ),
    "minilm6": dict(
        path=REPO / "models/all-MiniLM-L6-v2-q8_0-llukas.gguf",
        q_prefix="",
        d_prefix="",
    ),
    "gte_small": dict(
        path=REPO / "models/gte-small-q8_0-ggmlorg.gguf",
        q_prefix="",
        d_prefix="",
    ),
    # Two-sided task prefixes invert bge's one-sided contract; failure is
    # silent cosine degradation (batch-1 verified: prefixes correct iff
    # search/vss stay at ceiling).
    "nomic": dict(
        path=REPO / "models/nomic-embed-text-v1.5.Q8_0.gguf",
        q_prefix="search_query: ",
        d_prefix="search_document: ",
    ),
}

N_CTX = 512  # parity with production bge truncation; raise per-probe (granite 2k)
WORKERS = 4  # kept for SURFACES docs; serial path ignores
K_RRF = 60

QUESTIONS = json.loads(
    (REPO / "helpers/misc/embed_eval_questions.json").read_text(encoding="utf-8")
)


# ---------- corpora (read-only; text bases mirror the production rebuilds) ----------

_RO_CONN = None


def _ro_conn():
    global _RO_CONN
    import sqlite3

    if _RO_CONN is None:
        _RO_CONN = sqlite3.connect(f"file:{REPO}/memory/research.db?mode=ro", uri=True)
    return _RO_CONN


def load_corpora():
    """(notes, docs, (companies, sectors)) with the exact production text bases."""
    import sqlite3

    notes = []  # (key=file_path, text) — title\nsector\ncontent[:8000]
    c = sqlite3.connect(f"file:{REPO}/memory/research.db?mode=ro", uri=True)
    for fp, title, sector, content in c.execute(
        "SELECT file_path, title, sector, content FROM note_search"
    ):
        notes.append((fp, f"{title}\n{sector}\n{content[:8000]}"))
    c.close()

    docs = []  # (key=path, text) — title\nsection\ncontent[:4000]
    d = sqlite3.connect(f"file:{REPO}/memory/doc_search.db?mode=ro", uri=True)
    for title, section, path, _anchor, content in d.execute(
        "SELECT c0, c1, c2, c3, c4 FROM doc_search_content"
    ):
        docs.append((path, f"{title}\n{section}\n{content[:4000]}"))
    d.close()

    from helpers.graph.embeddings import _get_company_text

    e = sqlite3.connect(f"file:{REPO}/memory/research.db?mode=ro", uri=True)
    names = [
        r[0]
        for r in e.execute("SELECT name FROM entities WHERE entity_type='company' ORDER BY name")
    ]
    sectors = dict(
        e.execute("SELECT name, sector_classification FROM entities WHERE entity_type='company'")
    )
    e.close()
    companies = [(n, _get_company_text(_ro_conn(), n)) for n in names]
    return notes, docs, (companies, sectors)


# ---------- serial in-process embedding (see module docstring: NOT a pool) ----------

_LLAMA = None


def _get_llama(tag):
    global _LLAMA
    if _LLAMA is None:
        from llama_cpp import Llama

        _LLAMA = Llama(
            str(MODELS[tag]["path"]),
            embedding=True,
            n_ctx=N_CTX,
            verbose=False,
            n_threads=1,
        )
    return _LLAMA


def embed_pool(tag, texts, side="d"):
    """Embed texts on one side of the model's asymmetry ('d' index side,
    'q' query side). Cache keys hash prefix+text; symmetric models keep
    plain-text keys. Checkpoints the cache after the call."""
    prefix = MODELS[tag].get(f"{side}_prefix", "")
    cache_path = Path(f"/tmp/embtrial_cache_{tag}.pkl")  # noqa: S108  # throwaway checkpoint scratch, single-user box
    cache = pickle.loads(cache_path.read_bytes()) if cache_path.exists() else {}  # noqa: S301  # self-written checkpoint file, never untrusted input
    keys = [hashlib.sha1((prefix + t).encode()).hexdigest() for t in texts]  # noqa: S324  # non-crypto cache key; collision safety only
    miss_idx = [i for i, k in enumerate(keys) if k not in cache]
    if miss_idx:
        t0 = time.perf_counter()
        m = _get_llama(tag)
        for j, i in enumerate(miss_idx, 1):
            inp = prefix + texts[i]
            cache[keys[i]] = m.create_embedding(input=[inp])["data"][0]["embedding"]
            if j % 64 == 0 or j == len(miss_idx):
                el = time.perf_counter() - t0
                eta = (len(miss_idx) - j) / (j / el) / 60 if j else 0
                print(
                    f"    [embed/{side}] {j}/{len(miss_idx)} ({j / el:.2f}/s, eta {eta:.0f}m)",
                    flush=True,
                )
        cache_path.write_bytes(pickle.dumps(cache))
    return [cache[k] for k in keys]


# ---------- ranking (mirrors app.py A1 semantics: RRF over global cosine ranks) ----------


def cosine(a, b):
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return d / (na * nb) if na and nb else 0.0


def rrf_top5(page, qv, vecs):
    """page: corpus indices in BM25 order. Returns top-5 fused indices."""
    sims = sorted(range(len(vecs)), key=lambda i: -cosine(qv, vecs[i]))
    ranks = {doc_i: pos for pos, doc_i in enumerate(sims)}
    fused = []
    for bm_pos, doc_i in enumerate(page):
        score = 1.0 / (K_RRF + bm_pos + 1) + 1.0 / (K_RRF + ranks[doc_i] + 1)
        fused.append((score, doc_i))
    fused.sort(key=lambda t: -t[0])
    return [doc_i for _s, doc_i in fused[:5]]


def _report(rows, section, cats):
    parts = []
    for cat in cats:
        sub = [h for qq, h in rows if qq["category"] == cat]
        parts.append(f"{cat} {sum(sub)}/{len(sub)}")
    parts.append(f"TOTAL {sum(h for _q, h in rows)}/{len(rows)}")
    print(f"{section}: " + "  ".join(parts), flush=True)
    for q, h in rows:
        if not h:
            print(f"  MISS {section}/{q.get('id', q['query'][:30])} {q['query']!r}", flush=True)


def run_leg(tag, corpora):  # noqa: C901  # bench script: one straight-line leg
    surfaces = os.environ.get("SURFACES", "docs,companies,notes").split(",")
    notes, docs, (companies, sectors) = corpora

    note_keys = [k for k, _t in notes]
    doc_keys = [k for k, _t in docs]
    comp_names = [n for n, _t in companies]
    key_idx_n = {k: i for i, k in enumerate(note_keys)}
    key_idx_d = {k: i for i, k in enumerate(doc_keys)}

    import app as A

    client = A.app.test_client()

    def bm25_search(q):
        body = client.get(f"/api/search?q={quote(q)}&limit=25").get_json()
        return [h["file_path"] for h in (body or {}).get("results", [])]

    def bm25_docs(q):
        body = client.get(f"/api/docs/search?q={quote(q)}&limit=25&hybrid=0").get_json()
        return [h["path"] for h in (body or {}).get("results", [])]

    print(f"\n===== {tag} =====", flush=True)

    if "docs" in surfaces:
        doc_vecs = embed_pool(tag, [t for _k, t in docs])
        q_docs = embed_pool(tag, [q["query"] for q in QUESTIONS["docs"]], side="q")
        rows = []
        for q, qv in zip(QUESTIONS["docs"], q_docs):
            page = [key_idx_d[p] for p in bm25_docs(q["query"]) if p in key_idx_d]
            top5 = rrf_top5(page, qv, doc_vecs)
            rows.append((q, any(e in [doc_keys[i] for i in top5] for e in q["expect"])))
        _report(rows, "docs", ["exact", "variant", "semantic"])

    if "companies" in surfaces:
        comp_vecs = embed_pool(tag, [t for _n, t in companies])
        q_vss = embed_pool(tag, [q["query"] for q in QUESTIONS["vss"]], side="q")
        vss_ok, vss_miss = 0, []
        for q, qv in zip(QUESTIONS["vss"], q_vss):
            best = max(range(len(comp_vecs)), key=lambda i: cosine(qv, comp_vecs[i]))
            good = comp_names[best] == q["expect"]
            vss_ok += good
            if not good:
                vss_miss.append((q.get("id", q["query"][:30]), comp_names[best]))
        print(f"vss:    {vss_ok}/{len(QUESTIONS['vss'])}  misses: {vss_miss}", flush=True)

        q_comp = embed_pool(
            tag,
            [dict(companies)[c] for c in (q["company"] for q in QUESTIONS["neighbors"])],
        )
        nb_ok, nb_miss = 0, []
        for q, qv in zip(QUESTIONS["neighbors"], q_comp):
            sims = sorted(
                (i for i in range(len(comp_vecs)) if comp_names[i] != q["company"]),
                key=lambda i: -cosine(qv, comp_vecs[i]),
            )[:5]
            same = sum(1 for i in sims if sectors.get(comp_names[i]) == q["sector"])
            good = same >= 3
            nb_ok += good
            if not good:
                nb_miss.append((q["company"], same))
        print(f"neighbors: {nb_ok}/{len(QUESTIONS['neighbors'])}  misses: {nb_miss}", flush=True)

    if "notes" in surfaces:
        note_vecs = embed_pool(tag, [t for _k, t in notes])
        q_search = embed_pool(tag, [q["query"] for q in QUESTIONS["search"]], side="q")
        rows = []
        for q, qv in zip(QUESTIONS["search"], q_search):
            page = [key_idx_n[f] for f in bm25_search(q["query"]) if f in key_idx_n]
            top5 = rrf_top5(page, qv, note_vecs)
            rows.append((q, any(e in [note_keys[i] for i in top5] for e in q["expect"])))
        _report(rows, "search", ["exact", "variant", "semantic", "newsletter"])


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    if tag not in MODELS:
        sys.exit(f"unknown tag {tag!r}; choose from {list(MODELS)}")
    print("loading corpora (read-only)...", flush=True)
    corpora = load_corpora()
    print(
        f"notes={len(corpora[0])} docrows={len(corpora[1])} companies={len(corpora[2][0])}",
        flush=True,
    )
    t0 = time.perf_counter()
    run_leg(tag, corpora)
    print(f"\nleg wall: {time.perf_counter() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
