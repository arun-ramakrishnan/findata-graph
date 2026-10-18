#!/usr/bin/env python3
"""Block until the next T1-nudge fire or fold for one session, then report.

  bili_watch_event.py SID [either|t1|fold] [max_wait_s]

Tails the live bili.log from its current end (no repo import, read-only) -- use
it for a one-shot "tell me when this session folds" instead of starting the
long-lived bili_monitor.py. The log path is discovered (bili_logpath.py walks
/proc for the running billion-context proxy); override with BILI_LOG. On the
event it waits 45 s to collect the follow-on lines (compress-obs, usage),
prints a compact summary, and writes the raw tail to $BILI_WATCH_DIR/event_tail.txt.

Env: BILI_LOG, BILI_WATCH_DIR, BILI_RATE
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time

# Standalone entry point: bootstrap the repo root so `helpers.*` resolves.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from helpers.analytics.bili_logpath import discover_all  # noqa: E402

LOG, _ = discover_all()
DATA = os.environ.get("BILI_WATCH_DIR", os.path.join(tempfile.gettempdir(), "bili_watch"))
TAILFILE = os.path.join(DATA, "event_tail.txt")
RE_USAGE = re.compile(r"acp-usage\] input=(\d+) cached=(\d+)")
RE_NUDGE = re.compile(
    r"growth=(-?\d+)/(\d+)\s*\(ref=(\d+),\s*interval=(\d+)\),\s*pendingT1=(\d+)/(\d+)"
)
RATE = float(os.environ.get("BILI_RATE", "0.1"))


def main(argv: list[str]) -> int:  # noqa: C901 - tail loop plus report branches
    if not argv:
        print(__doc__)
        return 2
    sid = argv[0]
    mode = argv[1] if len(argv) > 1 else "either"
    max_wait = int(argv[2]) if len(argv) > 2 else 3600

    try:
        fh = open(LOG, errors="replace")
    except OSError as exc:
        print(f"cannot open {LOG}: {exc}")
        return 1
    fh.seek(0, 2)
    start = time.time()
    collected: list[str] = []
    hits: list[tuple[str, str]] = []
    while time.time() - start < max_wait:
        line = fh.readline()
        if not line:
            time.sleep(10)
            continue
        if sid not in line:
            continue
        collected.append(line.rstrip("\n"))
        kind = ""
        if "pendingT1" in line and ("T1" in line or "fire" in line.lower()):
            m = RE_NUDGE.search(line)
            if m and int(m.group(5)) >= int(m.group(6)):
                kind = "T1"
        elif "acp-compress-obs" in line and "degenerate" not in line:
            # Whole-line check: the [warn: degenerate-fold] marker sits at
            # char ~134 (the old `line[:80]` slice never reached it), so a
            # degenerate coverage warning used to classify as FOLD and wake
            # the watcher with a mislabeled event.
            kind = "FOLD"
        elif "compress requested" in line:
            kind = "REQ"
        if kind:
            hits.append((kind, line.strip()))
            if mode == "either" or mode == kind.lower() or (mode == "fold" and kind == "FOLD"):
                break
    # let the follow-on lines arrive -- only after an actual event; on the
    # timeout path nothing fired and the extra 45 s wait is pure latency
    # (a no-event `max_wait=3` run took ~48 s without this gate)
    if hits:
        end = time.time() + 45
        while time.time() < end:
            line = fh.readline()
            if not line:
                time.sleep(5)
                continue
            if sid in line:
                collected.append(line.rstrip("\n"))
    with open(TAILFILE, "w") as out:
        out.write("\n".join(collected) + "\n")

    print(
        f"=== watch_event: sid={sid} mode={mode} elapsed={time.time() - start:.0f}s hits={len(hits)}"
    )
    for kind, ln in hits:
        print(f"[{kind}] {ln[:400]}")
    print(f"--- last usage rows for {sid}:")
    us = [ln for ln in collected if "acp-usage" in ln]
    for ln in us[-6:]:
        m = RE_USAGE.search(ln)
        ts = ln.split("T", 1)[1][:8] if "T" in ln else ln[:8]
        if not m:
            print(f"  {ts}  {ln[-80:]}")
            continue
        i, c = int(m.group(1)), int(m.group(2))
        burn = c * RATE + (i - c)
        print(f"  {ts}  in={i:>7,} hit={100 * c / i if i else 0:5.1f}%  burn={burn:>8,.0f}")
    print(f"--- raw tail ({len(collected)} line(s)) -> {TAILFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
