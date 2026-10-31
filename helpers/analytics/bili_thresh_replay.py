#!/usr/bin/env python3
"""Threshold replay: what would a T1 threshold T have done, at a given growth floor?

Read-only. Parses every `nudge idle:` evaluation line from the whole log family and
replays it under a range of thresholds -- i.e. "how many folds would a lower T1
bar have bought, and how much compressible backlog would each have carried?"
Use it before moving `compress.nudgeGrowthTokens` in the proxy config.

A fire needs BOTH gates: pendingT1 >= T  AND  growth >= floor (default 67,500,
which is 0.45 x the 150k interval). The pendingT1 at fire is the best available
predictor of how many tokens that fold would save, so we report its distribution,
not just the count.

  bili_thresh_replay.py [floor=67500] [T1 T2 ...]

The log path is discovered (bili_logpath.py walks /proc for the running
billion-context proxy); override with BILI_LOG.

Env: BILI_LOG, BILI_WATCH_DIR
"""

from __future__ import annotations

import os
import re
import statistics
import sys
from collections import defaultdict

# Standalone entry point: bootstrap the repo root so `helpers.*` resolves.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from helpers.analytics.bili_logpath import discover_all  # noqa: E402

LOG, LOG_OLD = discover_all()
RE_HDR = re.compile(
    r"\[((?:ses_[A-Za-z0-9]+)|(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))\]"
)
RE = re.compile(
    r"usage=(?P<usage>\d+)% \((?P<input>\d+)/(?P<win>\d+)\).*?"
    r"growth=(?P<g>-?\d+)/(?P<f>\d+).*?pendingT1=(?P<p>\d+)/(?P<pt>\d+)"
)
THRESHOLDS = [200_000, 175_000, 150_000, 125_000, 100_000, 80_000, 60_000]


def main(argv: list[str]) -> int:  # noqa: C901 - report formatting branches
    floor = 67_500
    thresholds = THRESHOLDS
    if argv:
        try:
            floor = int(argv[0])
            thresholds = [int(t) for t in argv[1:]] or THRESHOLDS
        except ValueError:
            # argv[0] is a FLOOR, not a session id -- passing a SID produced a
            # raw ValueError traceback; the usage line is the answer.
            print(__doc__)
            return 2
    rows = defaultdict(list)  # sid -> [(pending, growth, input, win, pt)]
    for fp in (LOG_OLD, LOG):
        try:
            fh = open(fp, errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                if "nudge" not in ln:
                    continue
                h = RE_HDR.search(ln)
                m = RE.search(ln)
                if not h or not m:
                    continue
                g = m.groupdict()
                rows[h.group(1)].append(
                    (
                        int(g["p"]),
                        int(g["g"]),
                        int(g["input"]),
                        int(g["win"]),
                        int(g["pt"]),
                    )
                )
    total = sum(len(v) for v in rows.values())
    print(f"floor={floor:,}  evaluations parsed={total:,}  sessions={len(rows)}")
    print()
    hdr = f"{'thr':>9} | {'fires':>6} {'session(s)':>11} | {'median pending at fire':>22} | {'min':>8} {'max':>8}"
    print(hdr)
    print("-" * len(hdr))
    for t in thresholds:
        fires = []
        sids = set()
        for sid, rr in rows.items():
            for p, g, _inp, _win, _pt in rr:
                if p >= t and g >= floor:
                    fires.append(p)
                    sids.add(sid)
        med = f"{statistics.median(fires):>22,.0f}" if fires else f"{'-':>22}"
        mn = f"{min(fires):>8,}" if fires else f"{'-':>8}"
        mx = f"{max(fires):>8,}" if fires else f"{'-':>8}"
        print(f"{t:>9,} | {len(fires):>6} {len(sids):>11} | {med} | {mn} {mx}")
    print()
    # Per-session fire counts at the interesting thresholds.
    print("per-session fires:")
    print(f"{'session':>38} | " + " ".join(f"{t // 1000:>5}k" for t in thresholds))
    for sid, rr in sorted(rows.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        cells = []
        for t in thresholds:
            n = sum(1 for p, g, *_ in rr if p >= t and g >= floor)
            cells.append(f"{n:>6}")
        print(f"{sid:>38} | " + " ".join(cells))
    print()
    print("peak pendingT1 ever seen, per session:")
    for sid, rr in sorted(rows.items(), key=lambda kv: (-max(x[0] for x in kv[1]), kv[0])):
        peak = max(p for p, *_ in rr)
        peak_in = max((x[2] for x in rr if x[0] == peak), default=0)
        share = f"{100 * peak / peak_in:5.1f}%" if peak_in else "  n/a"
        print(f"  {sid:>38}  peak={peak:>9,}  of input {peak_in:>9,} = {share}   (n={len(rr)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
