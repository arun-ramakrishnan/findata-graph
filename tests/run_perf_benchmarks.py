#!/usr/bin/env python3
"""Performance benchmark runner — individual wall-clock timings + report.

Runs each perf-gated benchmark individually under ``time.perf_counter`` and
prints a formatted table to stdout, then appends the same table to
``perf_report.txt``.  Invoked by ``make perf``.

Usage::

    python3 tests/run_perf_benchmarks.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "perf_report.txt"

# Each entry: (label, args, budget_seconds).
BENCHMARKS: list[tuple[str, list[str], float]] = [
    ("integrity_check",      ["helpers/misc/database_integrity_check.py"], 2.0),
    ("verify_notes",         ["helpers/validators/verify_notes.py"], 3.0),
    ("sync_tags",            ["helpers/core/sync_tags.py"], 2.0),
    # 8.0s since 2026-08-19: the B1 corpus check gained the 108 newsletter
    # notes (frontmatter.newsletter.v1.json), ~+2s of jsonschema validation.
    ("static_checks",        ["helpers/validators/static_checks.py"], 8.0),
    ("snapshot_check",       ["helpers/maintenance/snapshot_db.py", "--check"], 4.0),
    ("graph_pagerank",       ["helpers/graph/algorithms.py", "pagerank", "--top", "10"], 3.0),
    ("graph_closeness",      ["helpers/graph/algorithms.py", "closeness", "--top", "10"], 4.0),
    ("graph_louvain",        ["helpers/graph/algorithms.py", "louvain", "--top", "10"], 4.0),
    ("graph_betweenness",     ["helpers/graph/algorithms.py", "betweenness", "--top", "10"], 4.0),
    ("graph_eigenvector",     ["helpers/graph/algorithms.py", "eigenvector", "--top", "10"], 2.0),
    ("graph_link_prediction", ["helpers/graph/algorithms.py", "link-predict",
                               "--top", "10", "--method", "jaccard",
                               "--no-apply"], 2.0),

    ("graph_rebuild",        ["helpers/graph/query.py", "rebuild"], 5.0),
    # sql_capability_unlocks B2 gate: BFS shortest_path steady-state
    # (<100ms on the default request AND the unreachable-dst full
    # component traversal, asserted inside the script).
    ("shortest_path_bfs",    ["tests/bench_shortest_path.py"], 5.0),
    ("extract_relations",    ["helpers/graph/extract_relations.py",
                              "findata/The_Chatter", "findata/Points_And_Figures",
                              "findata/The_PlotLines", "--no-write-sidecar"], 5.0),
    ("derive_co_mentions",   ["helpers/graph/derive_co_mentions.py",
                              "--newsletter", "The_Chatter"], 2.0),
    ("derive_events",        ["helpers/graph/derive_events.py"], 3.0),
    ("rebuild_note_search",  ["helpers/maintenance/rebuild_note_search.py", "--check"], 2.0),
    ("derive_insights",      ["helpers/graph/derive_insights.py"], 4.0),
    ("parse_newsletter",     ["helpers/core/parse_newsletter.py",
                              "findata/The_Chatter/Embracing_the_Unknown.md"], 3.0),
    ("enrich_yfinance",      ["helpers/maintenance/enrich_from_yfinance.py",
                              "--company", "Infosys", "--dry-run"], 5.0),
]


def run_one(label: str, args: list[str], budget: float) -> tuple[float, str, bool]:
    """Run a single benchmark, return (elapsed_seconds, status, passed)."""
    t0 = time.perf_counter()
    r = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        [sys.executable, *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        cwd=REPO_ROOT,
    )
    dt = time.perf_counter() - t0
    ok = r.returncode == 0 and dt < budget
    if r.returncode != 0:
        status = "FAIL(rc)"
    elif dt >= budget:
        status = "OVER_BUDGET"
    else:
        status = "OK"
    return dt, status, ok


def main() -> int:
    # ── run ──
    results: list[tuple[str, float, str, bool, float]] = []
    for label, args, budget in BENCHMARKS:
        print(f"  running {label:.<30s}", end="", flush=True)
        dt, status, ok = run_one(label, args, budget)
        results.append((label, dt, status, ok, budget))
        print(f" {dt:6.2f}s  [{status}]")
        if not ok:
            # surface stderr on failure for immediate feedback
            r = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
                [sys.executable, *args], capture_output=True,
                stdin=subprocess.DEVNULL, cwd=REPO_ROOT,
            )
            if r.stderr:
                print(f"    stderr: {r.stderr.decode()[:200]}", file=sys.stderr)

    # ── table ──
    lines: list[str] = []
    lines.append("")
    lines.append("Benchmark                              Time (s)   Budget   Status")
    lines.append("-" * 70)
    all_ok = True
    for label, dt, status, ok, budget in results:
        flag = "✓" if ok else "✗"
        lines.append(f"  {label:.<36s} {dt:7.2f}  {budget:7.1f}s   {flag} {status}")
        if not ok:
            all_ok = False
    lines.append("-" * 70)

    passed = sum(1 for *_, ok, _ in results if ok)
    total = len(results)
    lines.append(f"  {passed}/{total} passed")

    table = "\n".join(lines)
    print(table)

    # ── append to report ──
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(REPORT, "a") as f:
        f.write(f"=== make perf  {ts}  (Python {sys.version.split()[0]}) ===\n")
        f.write(table + "\n\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
