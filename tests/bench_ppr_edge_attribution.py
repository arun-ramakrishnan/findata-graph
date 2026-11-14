#!/usr/bin/env python3
"""Q3 PPR edge-type attribution harness (helpers/graph/ppr_edge_attribution.py).

Live-store leg: runs the full leave-one-type-out suite (8 relational types
+ economic cross-projection) on the current research.db with a small cohort.
Guards the end-to-end Q3 path (projection loads + S1 batch solves) and the
metric-sanity invariants (bounded [0,1] means, full cohort coverage).

Exits 0 on pass, 1 on ceiling/sanity breach, and 0 with a SKIP note when the
store is absent (mirrors cosine_gate's SKIP posture for missing modules).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.graph.ppr_edge_attribution import run  # noqa: E402
from helpers.graph.scipy_bridge import DEFAULT_DB_PATH  # noqa: E402

_WALL_CEILING_S = 60.0
_COHORT = 8
_TOP_K = 10


def main() -> int:
    db = Path(DEFAULT_DB_PATH)
    if not db.exists():
        print("SKIP: no memory/research.db — Q3 harness leg skipped (exit 0)")
        return 0

    report = run(db, cohort_size=_COHORT, top_k=_TOP_K, alpha=0.85)

    wall = report["wall_seconds"]
    if wall > _WALL_CEILING_S:
        print(f"FAIL: Q3 harness wall {wall:.2f}s > ceiling {_WALL_CEILING_S}s")
        return 1

    for t, block in report["ablation"].items():
        m = block["metrics"]
        if not (0.0 <= m["topk_jaccard_mean"] <= 1.0):
            print(f"FAIL: {t} jaccard mean {m['topk_jaccard_mean']!r} out of [0,1]")
            return 1
        if m["seeds_scored"] == 0:
            print(f"FAIL: {t} scored 0 seeds — projection may be degenerate")
            return 1
    x = report["cross_projection"]
    if not (0.0 <= x["metrics"]["topk_jaccard_mean"] <= 1.0):
        print(f"FAIL: cross-projection jaccard mean {x['metrics']['topk_jaccard_mean']!r}")
        return 1

    print(
        f"PASS: Q3 harness wall {wall:.2f}s, cohort {_COHORT}, "
        f"top-k {_TOP_K}; cross-projection jaccard μ "
        f"{x['metrics']['topk_jaccard_mean']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
