#!/usr/bin/env python3
"""54-question bank gate for the script_search gemma adoption
(script_search_gemma_adoption S4).

Runs the repo bank (helpers/misc/script_eval_questions.json) through the
PRODUCTION search_scripts path (default routing) against a given sidecar
and reports MRR / R@1 / R@5 per scope, plus the routed mode per question.

Floors (proposal AC#4): intent MRR >= 0.94 (measured 0.964 vector-only,
shipped granite hybrid 0.914 — the floor sits between, catching
regressions without overfitting the bank); ident MRR == 1.000 with 8/8
R@1 (the lexical path is deterministic). Exit 1 below floors.

Drift tolerance: intent questions whose expected rel_paths no longer
exist in the index are SKIPPED (reported) — the index drifts, the bank
does not chase it. Ident ground truth is substring containment in the
vector-visible columns (title/purpose/content), same rule as the eval.

Usage:
    python3 helpers/bench/script_eval_bank.py --db memory/script_search.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance import rebuild_script_search as rss  # noqa: E402

BANK_PATH = _REPO_ROOT / "helpers" / "misc" / "script_eval_questions.json"

INTENT_FLOOR_MRR = 0.94
IDENT_FLOOR_MRR = 1.0


def _rank_of(results: list[dict], pred) -> int | None:
    """1-based rank of the first hit satisfying pred, else None."""
    for i, hit in enumerate(results, start=1):
        if pred(hit):
            return i
    return None


def _mrr(ranks: list[int | None]) -> float:
    scored = [1.0 / r for r in ranks if r is not None]
    return sum(scored) / len(ranks) if ranks else 0.0


def _recall_at(ranks: list[int | None], k: int) -> float:
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks) if ranks else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="script_search sidecar to score")
    ap.add_argument("--limit", type=int, default=25, help="result window per question")
    args = ap.parse_args(argv)

    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT rowid, rel_path, title, purpose, content FROM script_search"
        ).fetchall()
    except sqlite3.Error as e:
        print(f"cannot read index: {e}")
        return 2
    live_paths = {r[1] for r in rows}
    path_to_text = {r[1]: f"{r[2]}\n{r[3]}\n{r[4] or ''}".lower() for r in rows}

    intent_ranks: list[int | None] = []
    skipped = 0
    for item in bank["intent"]:
        present = [p for p in item["expected"] if p in live_paths]
        if not present:
            skipped += 1
            continue
        out = rss.search_scripts(conn, item["q"], limit=args.limit)
        intent_ranks.append(_rank_of(out["results"], lambda h, ps=set(present): h["path"] in ps))

    ident_ranks: list[int | None] = []
    for item in bank["ident"]:
        sub = item["substr"].lower()
        out = rss.search_scripts(conn, item["q"], limit=args.limit)
        ident_ranks.append(
            _rank_of(out["results"], lambda h, s=sub: s in path_to_text.get(h["path"], ""))
        )
    conn.close()

    def _report(name: str, ranks: list[int | None]) -> None:
        n = len(ranks)
        print(
            f"{name:8s} n={n:3d} MRR={_mrr(ranks):.3f} "
            f"R@1={_recall_at(ranks, 1):.3f} R@5={_recall_at(ranks, 5):.3f}"
        )

    _report("intent", intent_ranks)
    _report("ident", ident_ranks)
    if skipped:
        print(f"skipped {skipped} intent question(s) with no expected path in the index")

    ok = True
    if _mrr(intent_ranks) < INTENT_FLOOR_MRR:
        print(f"FLOOR FAIL: intent MRR < {INTENT_FLOOR_MRR}")
        ok = False
    if _mrr(ident_ranks) < IDENT_FLOOR_MRR:
        print(f"FLOOR FAIL: ident MRR < {IDENT_FLOOR_MRR}")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
