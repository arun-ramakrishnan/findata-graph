#!/usr/bin/env python3
"""24-question bank gate for the git_search leg (git_history_search
S4/S5 + the 2026-10-17 gemma adoption).

Runs the acceptance bank through the PRODUCTION search_commits path
(default routing — identifier + rare-literal routes included) against
the live sidecar and reports top-1 / top-3 per tier, graded by SUBJECT
FRAGMENT (sha pins rot: the operator rewrites history live; the bank
does not chase shas).

Tiers: 10 subject-near/paraphrase, 3 identifier-shaped, 5 body-mechanism
(paraphrase past the 400-char head), 8 deep-body (mechanism text past
~char 1500, the S5 chunk tier). Floors (proposal §8.1, gemma cutover
measured 24/24): top-3 >= 22/24 (91.7%) overall, and the two S4-miss
queries must hold top-3 — they are the reason S5 exists. Exit 1 below
floors.

Bank drift tolerance: a query whose expected subject fragment matches
NO row in the index is SKIPPED (reported) — e.g. when history rewrite
renames an arc. Floor denominators use the graded count.

Usage:
    python3 helpers/bench/git_eval_bank.py --db memory/git_search.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance import rebuild_git_search as rgs  # noqa: E402

TOP3_FLOOR = 22  # of 24 (91.7%)

BILI = "route bili summaries"
BANK: tuple[tuple[str, str, str], ...] = (
    # (query, expected subject fragment, tier)
    ("route bili summaries to the owning harness", BILI, "subject"),
    ("_bili_namespace", BILI, "identifier"),
    ("bili session files collided by name", BILI, "body"),
    ("D14 ticker convergence stub hygiene", "D14 ticker convergence", "subject"),
    ("scipy lanes supplier peer feed kernel", "scipy lanes", "subject"),
    ("parallel fetch lanes sequential apply", "refresh chain", "subject"),
    ("gate fallout schema regen maint pin data repair", "gate fallout", "subject"),
    ("entity rename permalink site-form path safety", "entity rename", "subject"),
    ("person resolver proposal record update", "person resolver", "subject"),
    ("sector unlock proposal archived", "S3 sector unlock", "subject"),
    ("harvest fresh scoped to owned harnesses", BILI, "body"),  # S4 miss #1
    ("snapshot parquet mirror refresh classifier labels", "parquet mirror refresh", "subject"),
    ("gate fixes stamp semantics fixture decoupling verifier doctrine", "gate fixes", "subject"),
    ("db_sync parquet mirror refresh meta bump", "parquet mirror refresh", "subject"),
    ("_BILI_OWNED_HARNESSES", BILI, "identifier"),
    ("SessionWriter flush writes the touched buffer alone", BILI, "body"),  # S4 miss #2
    ("tiny backup held leaked fixture rows from tests", "Snapshot/backup overhaul", "deep-body"),
    (
        "table-global cache eviction deleted every other root's rows",
        "Scale corpus + embeddings",
        "deep-body",
    ),
    (
        "session economics total silently covered only opencode and prime",
        "agent traces: token basis",
        "deep-body",
    ),
    (
        "roster said all scanners clean but a leg never ran",
        "review scan: dead bandit leg",
        "deep-body",
    ),
    (
        "louvain modularity varies run to run on the same graph",
        "Zero-churn maint-full",
        "deep-body",
    ),
    (
        "a model swap must rewrite column matrix and mirror together",
        "Granite embedding swap",
        "deep-body",
    ),
    (
        "sorted items with bare unary minus key rejected by AST scan",
        "parity arc: c901",
        "deep-body",
    ),
    (
        "row-by-row binds took most of an hour arrow bulk load two minutes",
        "convo search: 96,907-row corpus",
        "deep-body",
    ),
)
S4_MISSES = (
    "harvest fresh scoped to owned harnesses",
    "SessionWriter flush writes the touched buffer alone",
)


def main(argv: list[str] | None = None) -> int:
    import time

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(rgs.GIT_SEARCH_DB), help="git_search sidecar path")
    ap.add_argument("--limit", type=int, default=3, help="top-k graded (default 3)")
    args = ap.parse_args(argv)

    conn = rgs.connect_git_db(args.db)
    try:
        subjects = [(r[0] or "").lower() for r in conn.execute("SELECT subject FROM git_search")]
        top1 = top3 = graded = 0
        misses: list[str] = []
        t0 = time.perf_counter()
        for query, expect, tier in BANK:
            if not any(expect.lower() in s for s in subjects):
                print(f"[skip ] ({tier}) {query} — fragment {expect!r} not in index (drift)")
                continue
            out = rgs.search_commits(conn, query, limit=args.limit)
            subs = [(r["subject"] or "") for r in out["results"]]
            hit1 = bool(subs) and expect.lower() in subs[0].lower()
            hit3 = any(expect.lower() in s.lower() for s in subs)
            graded += 1
            top1 += hit1
            top3 += hit3
            flag = "top1" if hit1 else ("top3" if hit3 else "MISS")
            note = " [S4-miss]" if query in S4_MISSES else ""
            print(f"[{flag:4s}] ({tier}) {query}{note}")
            if not hit3:
                misses.append(query)
        dt = time.perf_counter() - t0
    finally:
        conn.close()

    print(
        f"\ngit bank: top1 {top1}/{graded}  top3 {top3}/{graded}  "
        f"(floor top3 >= {TOP3_FLOOR}/{len(BANK)})  {dt / max(graded, 1) * 1000:.0f} ms/query"
    )
    ok = top3 >= TOP3_FLOOR and graded >= len(BANK) - 2
    for q in misses:
        print(f"MISS: {q}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
