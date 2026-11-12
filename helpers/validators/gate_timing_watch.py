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
- NOTE   : no verdict when the BASE window itself spans > 4x (a leg that
  alternates fast/slow modes — md-lint SKIP vs real, convo-rebuild
  incremental vs full — has a median that is not a reliable reference;
  the two windows catch different mode-mixes and the median comparison
  is meaningless). Printed as a NOTE, never a FIRE.

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


def _load_history(
    db: Path, *, recent: int, baseline: int
) -> dict[tuple[str, str], list[float]] | None:
    """Open the gate-timing index read-only and bucket per-leg durations.

    Returns None when the index is absent or unreadable - the caller
    treats that as a SKIP (no history to judge), never a failure.
    """
    if not db.exists():
        print(f"gate-timing-watch: SKIP - no index at {db}")
        return None
    try:
        import duckdb

        from helpers.misc.duckdb_lock import connect_with_lock_retry

        from helpers.core.duckdb_shared import memory_cap_config

        con = connect_with_lock_retry(
            lambda: duckdb.connect(str(db), read_only=True, **memory_cap_config())
        )
    except Exception as exc:
        print(f"gate-timing-watch: SKIP - index unreadable ({type(exc).__name__}: {exc})")
        return None

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
    return history


def _classify_leg(
    gate: str,
    leg: str,
    vals: list[float],
    *,
    recent: int,
    baseline: int,
    ratio: float,
    floor: float,
) -> tuple[str, tuple] | None:
    """Verdict for one leg: a (kind, payload) pair, or None for no verdict.

    A leg needs at least 3 samples on each side before it can drift. A
    multimodal baseline (spans more than 4x) records a NOTE instead of a
    FIRE because the two windows then catch different mode-mixes.
    """
    recent_vals = vals[:recent]
    base_vals = vals[recent : recent + baseline]
    if len(recent_vals) < 3 or len(base_vals) < 3:
        return None  # not enough history on one side - no verdict
    m_recent, m_base = _median(recent_vals), _median(base_vals)
    if m_base <= 0:
        return None
    if not (m_recent > ratio * m_base and m_recent - m_base > floor):
        return None
    # Multimodal-baseline guard: a leg that alternates between a fast
    # mode and a slow mode (md-lint SKIP vs real lint; convo-rebuild
    # incremental vs full) has a baseline window whose median is not a
    # reliable reference - the two windows catch different mode-mixes.
    # When the baseline itself spans more than 4x, this would be a false
    # fire: record a NOTE instead of a regression (2026-11-08: the
    # md-lint 0.9->8.5s and convo-rebuild 16->80s "regressions" were
    # both this artifact). Only legs that would otherwise FIRE reach
    # here, so sub-second noise legs never produce a NOTE.
    if min(base_vals) > 0 and max(base_vals) / min(base_vals) > 4.0:
        return (
            "note",
            (gate, leg, m_base, m_recent, min(base_vals), max(base_vals)),
        )
    return (
        "fire",
        (gate, leg, m_base, m_recent, len(recent_vals), len(base_vals)),
    )


def main_with_db(
    db: Path,
    *,
    recent: int = 5,
    baseline: int = 10,
    ratio: float = 1.5,
    floor: float = 5.0,
) -> int:
    history = _load_history(db, recent=recent, baseline=baseline)
    if history is None:
        return 0

    regressions: list[tuple[str, str, float, float, int, int]] = []
    multimodal: list[tuple[str, str, float, float, float, float]] = []
    for (gate, leg), vals in sorted(history.items()):
        verdict = _classify_leg(
            gate,
            leg,
            vals,
            recent=recent,
            baseline=baseline,
            ratio=ratio,
            floor=floor,
        )
        if verdict is None:
            continue
        kind, payload = verdict
        if kind == "note":
            multimodal.append(payload)
        else:
            regressions.append(payload)

    quiet = sum(1 for v in history.values() if len(v) >= 3)
    for gate, leg, m_base, m_recent, lo, hi in multimodal:
        print(
            f"  NOTE {gate}/{leg}: baseline multimodal ({lo:.1f}s..{hi:.1f}s, "
            f"median {m_base:.1f}s -> recent {m_recent:.1f}s) — no verdict "
            f"(fast/slow mode mix; gate_query timing --leg {leg} for the trend)"
        )
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
