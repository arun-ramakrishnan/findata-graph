#!/usr/bin/env python3
"""Gate timing watch — per-leg drift detector over the gate-run history.

Catches, during the advisory cycle itself, the slow-creep timing
regressions that previously surfaced only when someone ran gate_query
weeks later (the 2026-11-08 sweep: qa pytest 311 -> 445 s and
convo-fresh-check 26 -> 62 s grew for days before anyone looked).

Per (gate, leg) with enough history on both sides:

- RECENT : median of the last 5 recorded runs inside the last 14 days
- BASE   : median of the prior 10 runs (runs 6..15 back)
- FIRE   : RECENT > 1.5x BASE and RECENT - BASE > 5 s (ratio guards
  against noise on sub-second legs; the absolute floor guards against
  re-flagging a 0.1 -> 0.2 s leg forever)

DB missing / locked / empty — SKIP line, exit 0 (same safe-skip
doctrine as graph_scale_watch: a concurrent gate-query writer must not
red the advisory gate). Read-only DuckDB open with the shared lock
retry.

Usage:
    python3 helpers/validators/gate_timing_watch.py [--db PATH]
        [--recent 5] [--baseline 10] [--ratio 1.5] [--floor 5.0]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_DB = _REPO_ROOT / "outputs" / "gate_runs.duckdb"


def _median(values: list[float]) -> float:
    vs = sorted(values)
    n = len(vs)
    if n == 0:
        return 0.0
    mid = n // 2
    return vs[mid] if n % 2 else (vs[mid - 1] + vs[mid]) / 2.0


def main_with_db(
    db: Path,
    *,
    recent: int = 5,
    baseline: int = 10,
    ratio: float = 1.5,
    floor: float = 5.0,
) -> int:
    if not db.exists():
        print(f"gate-timing-watch: SKIP — no index at {db}")
        return 0
    try:
        import duckdb

        from helpers.misc.duckdb_lock import connect_with_lock_retry

        con = connect_with_lock_retry(lambda: duckdb.connect(str(db), read_only=True))
    except Exception as exc:
        print(f"gate-timing-watch: SKIP — index unreadable ({type(exc).__name__}: {exc})")
        return 0

    try:
        rows = con.execute(
            """
            select gate, leg, seconds from (
              select gate, leg, seconds,
                     row_number() over (
                       partition by gate, leg
                       order by started_at desc
                     ) as rn
              from runs r join legs l using(run_id)
              where started_at > now() - interval 14 day and seconds is not null
            ) where rn <= ?
            """,
            [recent + baseline],
        ).fetchall()
    finally:
        con.close()

    history: dict[tuple[str, str], list[float]] = {}
    for gate, leg, seconds in rows:
        history.setdefault((gate, leg), []).append(float(seconds))

    regressions: list[tuple[str, str, float, float, int, int]] = []
    for (gate, leg), vals in sorted(history.items()):
        recent_vals = vals[:recent]
        base_vals = vals[recent : recent + baseline]
        if len(recent_vals) < 3 or len(base_vals) < 3:
            continue  # not enough history on one side — no verdict
        m_recent, m_base = _median(recent_vals), _median(base_vals)
        if m_base <= 0:
            continue
        if m_recent > ratio * m_base and m_recent - m_base > floor:
            regressions.append((gate, leg, m_base, m_recent, len(recent_vals), len(base_vals)))

    quiet = sum(1 for v in history.values() if len(v) >= 3)
    if regressions:
        print(f"gate-timing-watch: {quiet} leg(s) with history — {len(regressions)} REGRESSED")
        for gate, leg, m_base, m_recent, nr, nb in regressions:
            print(
                f"  FIRE {gate}/{leg}: {m_base:.1f}s -> {m_recent:.1f}s "
                f"({m_recent / m_base:.2f}x, n={nr}/{nb})"
            )
        print(
            "gate-timing-watch: FAIL — investigate before the drift bakes in "
            "(gate_query timing --leg <leg> for the trend; loaded-box confound "
            "per doc/procedures/gates.md Phase 0 applies)"
        )
        return 1
    print(f"gate-timing-watch: OK — {quiet} leg(s) with history, no drift above threshold")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--recent", type=int, default=5)
    p.add_argument("--baseline", type=int, default=10)
    p.add_argument("--ratio", type=float, default=1.5)
    p.add_argument("--floor", type=float, default=5.0)
    args = p.parse_args()
    return main_with_db(
        Path(args.db),
        recent=args.recent,
        baseline=args.baseline,
        ratio=args.ratio,
        floor=args.floor,
    )


if __name__ == "__main__":
    sys.exit(main())
