#!/usr/bin/env python3
"""
Embedding quality eval — local_embeddings proposal §6 (2026-08-21).

Measures the three success criteria against the LIVE index (read-only):

- ``search``    : hybrid (BM25+vector RRF) vs BM25-only recall@5 over the
                  labeled question set, per category (exact / variant /
                  semantic / newsletter). Criterion: hybrid >= BM25 overall,
                  with NO regression on exact queries.
- ``docs``      : the doc/ corpus knowledge index (doc_search sidecar,
                  doc_search_embeddings proposal) — scan (#107 naive
                  baseline) vs BM25 vs hybrid recall@5 over the docs
                  question set, per category (exact / variant / semantic).
                  Criterion: hybrid >= BM25 >= scan, no scan regression.
- ``vss``       : get_tickers.vss_match top-1 accuracy on Yahoo-style
                  longNames. Criterion: beats the hash-pseudo baseline
                  (pre-apply it was ~0 on these variants).
- ``neighbors`` : semantic_neighbors same-sector dominance — >=3 of the
                  top-5 neighbours share the company's sector.

The labeled set lives in embed_eval_questions.json (ground truth verified
against raw corpus content, not the engine under test; doubles as the
OpenViking eval set if that pilot is revived).

Usage:
    python3 helpers/misc/embed_eval.py            # all sections
    python3 helpers/misc/embed_eval.py search     # one section

Always exits 0 — this is a report, not a gate.
"""

import json
import sys
from pathlib import Path
from urllib.parse import quote

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DB_PATH = _REPO_ROOT / "memory" / "research.db"
QUESTIONS = json.loads(
    (Path(__file__).parent / "embed_eval_questions.json").read_text(encoding="utf-8")
)


def _top5(client, query: str, hybrid: bool) -> list[str]:
    """Top-5 file_paths. BM25: direct top-5. Hybrid: the endpoint re-ranks
    the BM25 candidate page it fetches (limit+offset rows) — a page of
    exactly 5 leaves the vector leg nothing to rescue, so hybrid fetches a
    25-candidate page and we take the re-ranked top-5. That is the honest
    hybrid posture (BM25 recall, vector precision) per the A1 global-rank
    semantics."""
    url = f"/api/search?q={quote(query)}&limit={25 if hybrid else 5}"
    if hybrid:
        url += "&hybrid=true"
    body = client.get(url).get_json()
    return [h["file_path"] for h in (body or {}).get("results", [])][:5]


def run_search() -> None:
    import app as A  # lazy: Flask startup only when the search eval runs

    client = A.app.test_client()
    rows = []
    for q in QUESTIONS["search"]:
        bm = _top5(client, q["query"], hybrid=False)
        hy = _top5(client, q["query"], hybrid=True)
        rows.append(
            (q, any(e in bm for e in q["expect"]), any(e in hy for e in q["expect"]), bm, hy)
        )

    print(f"{'category':10s} {'n':>3s} {'bm25':>5s} {'hybrid':>6s}")
    cats = ["exact", "variant", "semantic", "newsletter"]
    tot_b = tot_h = tot_n = 0
    for cat in cats:
        sub = [r for r in rows if r[0]["category"] == cat]
        b = sum(1 for r in sub if r[1])
        h = sum(1 for r in sub if r[2])
        tot_b += b
        tot_h += h
        tot_n += len(sub)
        print(f"{cat:10s} {len(sub):3d} {b / len(sub):5.2f} {h / len(sub):6.2f}")
    print(f"{'TOTAL':10s} {tot_n:3d} {tot_b / tot_n:5.2f} {tot_h / tot_n:6.2f}")

    diffs = [r for r in rows if r[1] != r[2]]
    if diffs:
        print("\nper-question differences (bm25 -> hybrid):")
        for q, b, h, bm, hy in diffs:
            mark = "GAIN " if h else "LOSS "
            print(f"  {mark}{q['id']:8s} {q['query']!r}")
            if h and not b:
                hit = next(e for e in q["expect"] if e in hy)
                print(
                    f"         hybrid found {hit.split('/')[-1]} "
                    f"(bm25 top: {bm[0].split('/')[-1] if bm else '-'})"
                )
            if b and not h:
                print(
                    f"         bm25 found it at rank "
                    f"{bm.index(next(e for e in q['expect'] if e in bm)) + 1}"
                )
    zeros = [r for r in rows if not r[1] and not r[2]]
    if zeros:
        print("\nmissed by BOTH modes (label sanity check):")
        for q, _b, _h, _bm, _hy in zeros:
            print(f"  {q['id']:8s} {q['query']!r}")


def _docs_top5(client, query: str, mode: str) -> tuple[list[str], str]:
    """(top-5 doc paths, served mode) for one docs-corpus leg.

    hybrid is the endpoint default; bm25 is the ?hybrid=0 opt-out. The scan
    baseline is taken by the caller bypassing the index in-process."""
    url = f"/api/docs/search?q={quote(query)}&limit=25"
    if mode == "bm25":
        url += "&hybrid=0"
    body = client.get(url).get_json()
    return (
        [h["path"] for h in (body or {}).get("results", [])][:5],
        (body or {}).get("mode", "?"),
    )


def run_docs() -> None:  # noqa: C901
    import app as A  # lazy: Flask startup only when the eval runs

    client = A.app.test_client()
    real_index = A._docs_index_search
    rows = []
    for q in QUESTIONS["docs"]:
        # Scan baseline: bypass the sidecar index in-process so the #107
        # filesystem walk is measured even on a machine with a fresh index.
        def _bypass(*_args: object, **_kwargs: object) -> None:
            return None

        A._docs_index_search = _bypass  # ty: ignore[invalid-assignment]  # eval-only scan bypass
        sc, _sc_mode = _docs_top5(client, q["query"], "scan")
        A._docs_index_search = real_index
        bm, bm_mode = _docs_top5(client, q["query"], "bm25")
        hy, hy_mode = _docs_top5(client, q["query"], "hybrid")
        rows.append(
            (
                q,
                any(e in sc for e in q["expect"]),
                any(e in bm for e in q["expect"]),
                any(e in hy for e in q["expect"]),
                sc,
                bm,
                hy,
                hy_mode,
            )
        )

    served = {r[7] for r in rows}
    if served != {"hybrid"}:
        print(
            f"NOTE: hybrid leg served modes {sorted(served)} — expected "
            "{'hybrid'}; a stale/missing index degrades the comparison."
        )

    print(f"{'category':10s} {'n':>3s} {'scan':>5s} {'bm25':>5s} {'hybrid':>6s}")
    cats = ["exact", "variant", "semantic"]
    tot_s = tot_b = tot_h = tot_n = 0
    for cat in cats:
        sub = [r for r in rows if r[0]["category"] == cat]
        s = sum(1 for r in sub if r[1])
        b = sum(1 for r in sub if r[2])
        h = sum(1 for r in sub if r[3])
        tot_s += s
        tot_b += b
        tot_h += h
        tot_n += len(sub)
        print(
            f"{cat:10s} {len(sub):3d} {s / len(sub):5.2f} {b / len(sub):5.2f} {h / len(sub):6.2f}"
        )
    print(
        f"{'TOTAL':10s} {tot_n:3d} {tot_s / tot_n:5.2f} {tot_b / tot_n:5.2f} {tot_h / tot_n:6.2f}"
    )

    diffs = [r for r in rows if r[1] != r[2] or r[2] != r[3]]
    if diffs:
        print("\nper-question differences (scan -> bm25 -> hybrid):")
        for q, s, b, h, sc, bm, hy, _m in diffs:
            flags = "".join("1" if x else "0" for x in (s, b, h))
            print(f"  {flags} {q['id']:8s} {q['query']!r}")
            if h and not s:
                hit = next(e for e in q["expect"] if e in hy)
                print(f"         index found {hit} (scan top: {sc[0] if sc else '-'})")
            if s and not h:
                lost = next(e for e in q["expect"] if e in sc)
                print(f"         scan found {lost} — REGRESSION vs index")
    zeros = [r for r in rows if not r[1] and not r[2] and not r[3]]
    if zeros:
        print("\nmissed by ALL modes (label sanity check):")
        for q, _s, _b, _h, _sc, _bm, _hy, _m in zeros:
            print(f"  {q['id']:8s} {q['query']!r}")


def run_vss() -> None:
    from helpers.core.db import connect
    from helpers.core.get_tickers import vss_match

    con = connect(str(DB_PATH))
    names = [
        r[0]
        for r in con.execute("SELECT name FROM entities WHERE entity_type = 'company'").fetchall()
    ]
    con.close()

    ok = 0
    misses = []
    for q in QUESTIONS["vss"]:
        match, score = vss_match(q["query"], names, threshold=0.5)
        good = match == q["expect"]
        ok += good
        if not good:
            misses.append((q["query"], q["expect"], match, score))
    print(f"vss top-1: {ok}/{len(QUESTIONS['vss'])}")
    for q, exp, got, sc in misses:
        print(f"  MISS {q!r}: expected {exp!r}, got {got!r} (score {sc:.3f})")


def run_neighbors() -> None:
    from helpers.graph import query as gq

    con = gq.connect()
    ok = 0
    misses = []
    for q in QUESTIONS["neighbors"]:
        try:
            nn = gq.semantic_neighbors(con, q["company"], k=5)
        except Exception as e:  # noqa: BLE001  # report, don't crash the eval
            misses.append((q["company"], f"error: {e}"))
            continue
        same = sum(1 for n in nn if n[1] == q["sector"])
        good = same >= 3
        ok += good
        if not good:
            misses.append(
                (q["company"], f"{same}/5 same-sector: " + ", ".join(f"{n[0]}({n[1]})" for n in nn))
            )
    con.close()
    print(f"neighbors same-sector dominance (>=3/5): {ok}/{len(QUESTIONS['neighbors'])}")
    for co, why in misses:
        print(f"  MISS {co}: {why}")


def main(argv: list[str] | None = None) -> int:
    # Test seam: argv = post-script-name args; default stays the real argv.
    raw = sys.argv[1:] if argv is None else argv
    mode = raw[0] if raw else "all"
    if mode in ("all", "search"):
        print("== search: hybrid vs bm25 (recall@5, any-of expect) ==")
        run_search()
        print()
    if mode in ("all", "docs"):
        print("== docs: scan vs bm25 vs hybrid (recall@5, any-of expect) ==")
        run_docs()
        print()
    if mode in ("all", "vss"):
        print("== vss_match top-1 (Yahoo longNames) ==")
        run_vss()
        print()
    if mode in ("all", "neighbors"):
        print("== semantic_neighbors sector dominance ==")
        run_neighbors()
    return 0


if __name__ == "__main__":
    sys.exit(main())
