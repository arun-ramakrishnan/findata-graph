#!/usr/bin/env python3
"""Permanent regression guard for the #504 quick-wins (S1 dumps swap).

Port of the measurement harness used in perf_quick_wins_json_dumps_regex_hoist
(archived, #504): stdlib `json.dumps` vs `jsonio.dumps` (orjson) on the same
five payload shapes, plus a real-ledger row loop and the H1 hoist microbench.

The S1 win is a constant-factor ratio (measured 3.9–9.1x across shapes). The
guard is a RATIO floor, not an absolute wall: a regression that re-introduces
stdlib dumps in a mass lane collapses the ratio toward ~1.0 and trips the
floor, while faster/slower boxes stay green.

Exits 0 on pass, 1 on ratio breach.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.core import jsonio  # noqa: E402

REPS = 50_000
BEST_OF = 3
RATIO_FLOOR = 2.5

shapes = [
    (
        "delegation-row",
        {
            "at": "2026-11-11T12:00:00Z",
            "op": "delegation.run",
            "agent": "general",
            "task": "build index",
            "session": "ses_abc123",
            "n": 7,
        },
    ),
    (
        "log-warn-row",
        {
            "ts": "2026-11-11T12:00:00Z",
            "level": "warn",
            "msg": "retry 2/3",
            "session": "ses_abc123",
        },
    ),
    (
        "tool-input",
        {
            "input": "regenerate the 'Performance' section for Gati with 2026 numbers, include revenue splits "
            * 8
        },
    ),
    (
        "tools-list",
        [{"name": f"tool{i}", "description": "does something " * 10} for i in range(12)],
    ),
    ("head-dict-sorted", {"session": "ses_abc123", "model": "mercury-3", "count": 42}),
]


def best(fn, *args):
    best_t = float("inf")
    for _ in range(BEST_OF):
        t0 = time.perf_counter()
        fn(*args)
        best_t = min(best_t, time.perf_counter() - t0)
    return best_t


def bench_dumps(name, obj, sort_keys=False):
    def s():
        for _ in range(REPS):
            json.dumps(obj, sort_keys=sort_keys)

    def o():
        for _ in range(REPS):
            jsonio.dumps(obj, sort_keys=sort_keys)

    ts, to = best(s), best(o)
    ratio = ts / to
    print(f"B1 {name:28s} stdlib {ts:7.3f}s  jsonio {to:7.3f}s  ratio {ratio:5.2f}x")
    return ratio


ratios = []
for name, obj in shapes:
    sort_keys = name == "head-dict-sorted"
    ratios.append(bench_dumps(name, obj, sort_keys=sort_keys))
    assert json.loads(jsonio.dumps(obj)) == obj, f"parity {name}"

# B2: real ledger rows when present (guard skips if the ledger is absent)
ledger = Path.home() / ".prime" / "agent" / "rlm-ledger" / "20d0e37e665dc969.jsonl"
if ledger.is_file():
    lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()][:5000]

    def row_loop_stdlib():
        rows = []
        for ln in lines:
            r = json.loads(ln)
            rows.append((None, None, None, None, None, None, None, json.dumps(r)))
        return rows

    def row_loop_jsonio():
        rows = []
        for ln in lines:
            r = jsonio.loads(ln)
            rows.append((None, None, None, None, None, None, None, jsonio.dumps(r)))
        return rows

    t_s, t_o = best(row_loop_stdlib), best(row_loop_jsonio)
    b2 = t_s / t_o
    ratios.append(b2)
    print(f"B2 row-loop stdlib {t_s:7.3f}s  jsonio {t_o:7.3f}s  ratio {b2:5.2f}x")

lo = min(ratios)
print(f"min ratio {lo:.2f}x (floor {RATIO_FLOOR}x)")
sys.exit(0 if lo >= RATIO_FLOOR else 1)
