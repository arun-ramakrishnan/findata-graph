#!/usr/bin/env python3
"""Deep-probe candidate legs — the 15 deep-content questions vs CANDIDATE models.

note_deep_probe measured the live sectioned index (after_*) and a bge
whole-note simulation (before_*). This sibling re-runs ONLY the
simulation shape against candidate embedding models (nomic / MiniLM /
granite / a bge control), so models previously eliminated on
contaminated timing numbers can be judged on deep-content retrieval in
one clean serial per-text window (embed_full_reembed proposal §S2).

Shape (identical to note_deep_probe's before-leg unless noted):
  corpus   legacy one-row-per-note extraction (eb230fdd logic), base =
           f"{title}\n{sector}\n{body[:8000]}" — the SAME base string for
           every model; each model's embedder applies its own ctx cap.
  BM25     throwaway FTS5 (porter unicode61), MATCH on the raw query.
  hybrid   BM25 25-doc page position + GLOBAL cosine rank, RRF k=60,
           recall@5. Also reports cosine-only recall@5 (no BM25).
  bge tag  control leg — local_embedder semantics via note_deep_probe's
           _legacy_vectors (embed-store cache + /tmp spill, embed_query
           for questions), i.e. the recorded before_hybrid reproduced.

Per-model document prefixes: nomic is two-sided ("search_document: " /
"search_query: "); granite/MiniLM raw. Vectors checkpoint per model to
/tmp/deep_probe_cand_<tag>.json (sha256(base) keys) so killed legs
resume. Serial per-text, warmup first, live per-64 progress meter,
preflight clean state (no concurrent benches).

Usage:
    python3 helpers/bench/note_deep_probe_candidates.py bge minilm6 granite97m nomic
    python3 helpers/bench/note_deep_probe_candidates.py nomic --json out.json
Recorded controls for the same questions: after_bm25 13/15, after_hybrid
(sectioned bge, live) 11/15, before_hybrid (bge whole-note) 11/15.

The nomic and MiniLM GGUFs were DELETED at arc close (2026-09-06,
embed_full_reembed: both eliminated on measured quality/cost) — those
legs now fail fast on the missing file. Do not re-download without a
new proposal; the verdicts live in that proposal's appendix.
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.bench.embed_runtime_bench import preflight_clean_state  # noqa: E402
from helpers.bench.note_deep_probe import (  # noqa: E402
    PAGE,
    QUESTIONS_PATH,
    RRF_K,
    _cosine,
    _legacy_rows,
)


class _ModelCfg(TypedDict):
    path: Path | None  # None = the bge local_embedder control leg
    ctx: int
    q_prefix: str | None
    d_prefix: str | None


MODELS: dict[str, _ModelCfg] = {
    "bge": _ModelCfg(path=None, ctx=512, q_prefix=None, d_prefix=None),  # local_embedder control
    "minilm6": _ModelCfg(
        path=REPO / "models/all-MiniLM-L6-v2-q8_0-llukas.gguf",
        ctx=512,
        q_prefix="",
        d_prefix="",
    ),
    "granite97m": _ModelCfg(
        path=REPO / "models/granite-embedding-97M-multilingual-r2-Q8_0.gguf",
        ctx=2048,
        q_prefix="",
        d_prefix="",
    ),
    "nomic": _ModelCfg(
        path=REPO / "models/nomic-embed-text-v1.5.Q8_0.gguf",
        ctx=2048,
        q_prefix="search_query: ",
        d_prefix="search_document: ",
    ),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vec: list[float]) -> list[float]:
    import math

    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def _flat_embedding(raw: object) -> list[float]:
    """llama.cpp's stubs type the payload list[float] | list[list[float]]
    (batch inputs return nested); single-input calls are flat at runtime."""
    from typing import cast

    return cast(list[float], raw)


def _embed_candidate(
    tag: str, texts: list[str], cache_name: str | None = None
) -> tuple[list[list[float]], int]:
    """Serial per-text document vectors with per-model scratch checkpoint."""
    cfg = MODELS[tag]
    # The bge control dispatches to local_embedder before reaching here;
    # candidates always carry a path and concrete prefixes.
    assert cfg["path"] is not None and cfg["d_prefix"] is not None  # noqa: S101  # ty narrowing: bge control dispatches before here
    scratch = Path(f"/tmp/deep_probe_cand_{cache_name or tag}.json")  # noqa: S108  # throwaway probe scratch, single-user box
    cache: dict[str, list[float]] = {}
    if scratch.exists():
        cache = json.loads(scratch.read_text(encoding="utf-8"))

    from llama_cpp import Llama

    model = Llama(str(cfg["path"]), embedding=True, n_ctx=cfg["ctx"], verbose=False)
    for t in texts[:4]:  # warmup: cold first texts read ~2.6x slow
        model.create_embedding(input=[cfg["d_prefix"] + t])
    out, misses, t0, done = [], 0, time.perf_counter(), 0
    for t in texts:
        h = _sha(t)
        v = cache.get(h)
        if v is None:
            v = _normalize(
                _flat_embedding(
                    model.create_embedding(input=[cfg["d_prefix"] + t])["data"][0]["embedding"]
                )
            )
            cache[h] = v
            misses += 1
        out.append(v)
        done += 1
        if done % 64 == 0:
            r = done / (time.perf_counter() - t0)
            print(f"  [{tag}] {done}/{len(texts)} ({r:.1f}/s fresh)", file=sys.stderr, flush=True)
    del model
    if misses:
        scratch.write_text(json.dumps(cache), encoding="utf-8")
    return out, misses


def _query_vecs(tag: str, queries: list[str]) -> list[list[float]]:
    cfg = MODELS[tag]
    if tag == "bge":
        from helpers.maintenance.rebuild_note_search import query_embedder

        q_embed, _dims = query_embedder()
        return [q_embed(q) for q in queries]
    assert cfg["path"] is not None and cfg["q_prefix"] is not None  # noqa: S101  # ty narrowing; bge returned above
    from llama_cpp import Llama

    model = Llama(str(cfg["path"]), embedding=True, n_ctx=cfg["ctx"], verbose=False)
    out = [
        _normalize(
            _flat_embedding(
                model.create_embedding(input=[cfg["q_prefix"] + q])["data"][0]["embedding"]
            )
        )
        for q in queries
    ]
    del model
    return out


def _doc_vecs(tag: str, bases: list[str]) -> tuple[list[list[float]], int]:
    if tag == "bge":
        from helpers.bench.note_deep_probe import _legacy_vectors

        return _legacy_vectors(bases), 0
    return _embed_candidate(tag, bases)


def run(tags: list[str]) -> dict:
    preflight_clean_state("note_deep_probe_candidates.py")
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    rows = _legacy_rows()
    bases = [f"{r[2]}\n{r[3]}\n{r[4][:8000]}" for r in rows]
    print(
        f"corpus: {len(rows)} legacy notes, avg {sum(map(len, bases)) / len(bases):.0f} chars",
        file=sys.stderr,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    lconn = sqlite3.connect(tmp.name)
    lconn.execute(
        "CREATE VIRTUAL TABLE note_search USING fts5("
        "doc_type, file_path UNINDEXED, title, sector, content, "
        "tokenize = 'porter unicode61')"
    )
    lconn.executemany(
        "INSERT INTO note_search (doc_type, file_path, title, sector, content) VALUES (?, ?, ?, ?, ?)",
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows],
    )
    lconn.commit()

    report: dict = {"models": {}}
    for tag in tags:
        cfg = MODELS[tag]
        print(f"\n=== {tag} (ctx={cfg['ctx']}) ===", file=sys.stderr, flush=True)
        t0 = time.perf_counter()
        vecs, misses = _doc_vecs(tag, bases)
        qvs = _query_vecs(tag, [q["query"] for q in questions])
        wall = time.perf_counter() - t0

        res = []
        for q, qv in zip(questions, qvs):
            page = [
                r[0]
                for r in lconn.execute(
                    "SELECT file_path FROM note_search WHERE note_search MATCH ? ORDER BY rank LIMIT ?",
                    (q["query"], PAGE),
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
            top5_hybrid = [fp for _s, fp in fused[:5]]
            top5_cosine = [rows[i][1] for i, _s in sims[:5]]
            hit = any(e in top5_hybrid for e in q["expect"])
            cos_hit = any(e in top5_cosine for e in q["expect"])
            res.append({"id": q["id"], "category": q["category"], "hybrid": hit, "cosine": cos_hit})
        hyb = sum(r["hybrid"] for r in res)
        cos = sum(r["cosine"] for r in res)
        print(
            f"{tag}: hybrid {hyb}/{len(res)}  cosine-only {cos}/{len(res)}  "
            f"(doc leg {wall:.0f}s, {misses} fresh embeds)",
            flush=True,
        )
        for r in res:
            mark = "H" if r["hybrid"] else ("c" if r["cosine"] else "-")
            print(f"  {r['id']:26s} {r['category']:9s} {mark}")
        report["models"][tag] = {
            "hybrid": hyb,
            "cosine": cos,
            "n": len(res),
            "doc_wall_s": round(wall, 1),
            "fresh": misses,
            "per_question": res,
        }

    lconn.close()
    Path(tmp.name).unlink(missing_ok=True)
    print(
        "\ncontrols (recorded, same questions): after_bm25 13/15 | "
        "after_hybrid (sectioned bge) 11/15 | before_hybrid (bge whole-note) 11/15"
    )
    report["controls"] = {"after_bm25": 13, "after_hybrid": 11, "before_hybrid": 11, "n": 15}
    return report


def _live_sections() -> list[tuple[str, str, str, str, str, str | None]]:
    """(file_path, title, sector, section_title, content, embedding_json)
    per section row from the LIVE sectioned note_search index."""
    conn = sqlite3.connect(f"file:{REPO / 'memory/research.db'}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT file_path, title, sector, section_title, content, embedding FROM note_search"
    ).fetchall()
    conn.close()
    return rows


def run_sectioned(tags: list[str]) -> dict:  # noqa: C901  # bench script: one straight-line report
    """SECTIONED candidate legs — granite under the SAME note composition
    as bge (user challenge 2026-09-06: the whole-note bake-off never tested
    this cell). Mirrors the production S2 endpoint semantics: AND-first +
    OR-fill candidates (fts_match_expr), note-dedup BM25 page via
    ROW_NUMBER window, note-best cosine collapse, RRF k=60, page 25.
    The bge leg reads vectors straight from the live index (zero embeds)
    and doubles as the harness-vs-endpoint validation control.
    Question sets: the 15 deep-probe questions + the 27 eval-search
    questions (embed_eval_questions.json)."""
    preflight_clean_state("note_deep_probe_candidates.py")
    from helpers.maintenance.rebuild_doc_search import fts_match_expr

    deep_qs = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    eval_qs = json.load(open(REPO / "helpers/misc/embed_eval_questions.json"))["search"]

    sections = _live_sections()
    bases = [f"{t}\n{s}\n{st}\n{c[:4000]}" for _fp, t, s, st, c, _e in sections]
    paths = [r[0] for r in sections]
    n_notes = len(set(paths))
    print(
        f"corpus: {len(sections)} sections / {n_notes} notes, "
        f"avg {sum(map(len, bases)) / len(bases):.0f} chars",
        file=sys.stderr,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    sconn = sqlite3.connect(tmp.name)
    sconn.execute(
        "CREATE VIRTUAL TABLE sec USING fts5("
        "path UNINDEXED, title, sector, content, section_title, "
        "tokenize = 'porter unicode61')"
    )
    sconn.executemany(
        "INSERT INTO sec (path, title, sector, content, section_title) VALUES (?, ?, ?, ?, ?)",
        [(r[0], r[1], r[2], r[4], r[3]) for r in sections],
    )
    sconn.commit()

    def bm25_page(query: str, window: int) -> list[str]:
        def fetch(expr: str, w: int) -> list[str]:
            return [
                r[0]
                for r in sconn.execute(
                    "SELECT path FROM (SELECT path, rank, ROW_NUMBER() OVER "
                    "(PARTITION BY path ORDER BY rank) AS rn FROM (SELECT path, rank "
                    "FROM sec WHERE sec MATCH ? ORDER BY rank LIMIT 1024)) "
                    "WHERE rn = 1 ORDER BY rank LIMIT ?",
                    (expr, w),
                )
            ]

        or_expr = fts_match_expr(query)
        and_expr = or_expr.replace(" OR ", " ") if or_expr else ""
        page = fetch(and_expr, window) if and_expr else []
        if len(page) < window and or_expr != and_expr:
            seen = set(page)
            for fp in fetch(or_expr, 1024):
                if fp not in seen:
                    page.append(fp)
                    seen.add(fp)
                if len(page) >= window:
                    break
        return page

    report: dict = {"mode": "sectioned", "models": {}}
    for tag in tags:
        cfg = MODELS[tag]
        print(f"\n=== {tag} sectioned (ctx={cfg['ctx']}) ===", file=sys.stderr, flush=True)
        t0 = time.perf_counter()
        if tag == "bge":
            vecs = [json.loads(e) if e else None for e in (r[5] for r in sections)]
            misses = 0
        else:
            vecs, misses = _embed_candidate(tag, bases, cache_name=f"{tag}_sectioned")
        qtexts = [q["query"] for q in deep_qs] + [q["query"] for q in eval_qs]
        qvs = _query_vecs(tag, qtexts)
        wall = time.perf_counter() - t0

        for label, qs in (("deep", deep_qs), ("search", eval_qs)):
            q_sub = qvs[len(deep_qs) :] if label == "search" else qvs[: len(deep_qs)]
            res = []
            for q, qv in zip(qs, q_sub):
                page = bm25_page(q["query"], PAGE)
                best: dict[str, float] = {}
                for i, v in enumerate(vecs):
                    if v is None:
                        continue
                    sim = _cosine(qv, v)
                    if sim > best.get(paths[i], -2.0):
                        best[paths[i]] = sim
                cos_order = sorted(best, key=lambda fp: best[fp], reverse=True)
                cos_rank = {fp: pos for pos, fp in enumerate(cos_order)}
                fused = [
                    (
                        1.0 / (RRF_K + pos)
                        + 1.0 / (RRF_K + cos_rank.get(fp, len(cos_order) + pos)),
                        fp,
                    )
                    for pos, fp in enumerate(page)
                ]
                fused.sort(key=lambda t: t[0], reverse=True)
                top5_h = [fp for _s, fp in fused[:5]]
                top5_c = cos_order[:5]
                res.append(
                    {
                        "id": q.get("id", q["query"]),
                        "hybrid": any(e in top5_h for e in q["expect"]),
                        "cosine": any(e in top5_c for e in q["expect"]),
                    }
                )
            hyb = sum(r["hybrid"] for r in res)
            cos = sum(r["cosine"] for r in res)
            print(
                f"{tag} [{label}]: hybrid {hyb}/{len(res)}  cosine-only {cos}/{len(res)}",
                flush=True,
            )
            misses_q = [str(r["id"]) for r in res if not r["hybrid"]]
            if misses_q:
                print(f"  misses: {', '.join(misses_q)}", flush=True)
            report["models"].setdefault(tag, {})[label] = {
                "hybrid": hyb,
                "cosine": cos,
                "n": len(res),
            }
        report["models"][tag]["doc_wall_s"] = round(wall, 1)
        report["models"][tag]["fresh"] = misses

    sconn.close()
    Path(tmp.name).unlink(missing_ok=True)
    print(
        "\nrecorded endpoint controls: after_hybrid (bge sectioned) deep 11/15, "
        "search 1.00 (27/27); after_bm25 deep 13/15"
    )
    return report


if __name__ == "__main__":
    # No heavy work at import time.
    args = [a for a in sys.argv[1:]]
    json_out = None
    sectioned = False
    if "--sectioned" in args:
        sectioned = True
        args.remove("--sectioned")
    if "--json" in args:
        i = args.index("--json")
        json_out = args[i + 1]
        args = args[:i] + args[i + 2 :]
    tags = args or (
        ["bge", "granite97m"] if sectioned else ["bge", "minilm6", "granite97m", "nomic"]
    )
    unknown = [t for t in tags if t not in MODELS]
    if unknown:
        sys.exit(f"unknown model tag(s): {unknown} — known: {sorted(MODELS)}")
    report = run_sectioned(tags) if sectioned else run(tags)
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {json_out}", file=sys.stderr)
