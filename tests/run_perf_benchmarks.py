#!/usr/bin/env python3
"""Performance benchmark runner — individual wall-clock timings + report.

Runs each perf-gated benchmark individually under ``time.perf_counter`` and
prints a formatted table to stdout, then appends the same table to
``outputs/perf_report.md``.  Invoked by ``make perf``.

Usage::

    python3 tests/run_perf_benchmarks.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "outputs" / "perf_report.md"

# Each entry: (label, args, budget_seconds).
BENCHMARKS: list[tuple[str, list[str], float]] = [
    # 10.0s since 2026-09-23: the VIGIL intake (26K entities / 57K
    # relations, plus the relation-homed load) puts the checker at ~4.7s
    # measured; the 2.0s budget was calibrated on the notes-era db.
    # Optimization slice (batching the per-entity loop) is optional perf-arc
    # work — re-tighten if it lands.
    ("integrity_check", ["helpers/misc/database_integrity_check.py"], 10.0),
    ("verify_notes", ["helpers/validators/verify_notes.py"], 3.0),
    # --apply (2026-09-03): guard unification made the bare run a dry-run
    # report; the leg times the write path it has always timed (and the 2.0s
    # budget was calibrated on).
    ("sync_tags", ["helpers/core/sync_tags.py", "--apply"], 2.0),
    # 8.0s since 2026-08-19: the B1 corpus check gained the 108 newsletter
    # notes (frontmatter.newsletter.v1.json), ~+2s of jsonschema validation.
    # 10.0s since 2026-09-14: static_checks gained the S19 data-format AST
    # guards (zstd + Arrow-in-flight scans over the helper corpus).
    ("static_checks", ["helpers/validators/static_checks.py"], 7.0),
    ("snapshot_check", ["helpers/maintenance/snapshot_db.py", "--check"], 4.0),
    ("graph_pagerank", ["helpers/graph/algorithms.py", "pagerank", "--top", "10"], 3.0),
    # --compute (graph_centrality_persistent_cache): the centrality legs
    # bypass the v_centrality_* disk cache so these budgets keep
    # measuring the Onager COMPUTE path; the cache path is smoke-tested
    # in tests/test_centrality_cache.py instead.
    # DELIBERATELY RED at VIGIL scale (26,120 nodes / 56,014 edges):
    # measured 156.8s / 128.8s cold against the pre-VIGIL 5.0s budget.
    # Operator call (2026-09-23): do NOT raise — minutes-scale cold
    # centrality is not acceptable; the red leg is the tracker. Fix is
    # L1a (company-source-restricted closeness/harmonic, ~130x less BFS
    # work) in the perf arc; warm serve is 0.017s and tested separately.
    (
        "graph_closeness",
        ["helpers/graph/algorithms.py", "closeness", "--top", "10", "--compute"],
        5.0,
    ),
    (
        "graph_louvain",
        ["helpers/graph/algorithms.py", "louvain", "--top", "10", "--compute"],
        4.0,
    ),
    # DELIBERATELY RED at VIGIL scale: measured 48.9s / 37.2s cold against
    # the pre-VIGIL 4.0s budget — same operator call as closeness. Fix is
    # L1b (2-core folding) in the perf arc.
    (
        "graph_betweenness",
        ["helpers/graph/algorithms.py", "betweenness", "--top", "10", "--compute"],
        4.0,
    ),
    # 2026-09-19 RESOLVED: the "corpus growth" slide was not corpus at all —
    # a stray empty memory/graph.db poisoned _is_warm's colocated-sibling
    # guess, so every graph CLI unlinked + rebuilt the cache (~2s) before
    # computing; eigenvector's native compute is ~0.04s. Fixed in
    # query.py (_is_warm takes the real db_path); waivers removed, original
    # 2.0s budgets restored: eigenvector 0.37s, link_prediction 1.34s.
    (
        "graph_eigenvector",
        ["helpers/graph/algorithms.py", "eigenvector", "--top", "10", "--compute"],
        2.0,
    ),
    # DELIBERATELY RED at VIGIL scale: measured 57.0s / 52.9s against the
    # pre-VIGIL 2.0s budget — same operator call as closeness. The jaccard
    # candidate space blew up with the widened symmetric types
    # (competes_with 3,578 + jv_with 1,070 + co_mention). Fix is L1c
    # (candidate pruning: 2-hop join with degree caps / top-K early exit)
    # in the perf arc.
    (
        "graph_link_prediction",
        [
            "helpers/graph/algorithms.py",
            "link-predict",
            "--top",
            "10",
            "--method",
            "jaccard",
            "--no-apply",
        ],
        2.0,
    ),
    # Back to ~3.0s since centrality_rebuild_contract (2026-09-22): the
    # rebuild is data-only again — the ten v_centrality_* tables are
    # dropped, not stamped (the stamp is the explicit stamp-centrality
    # lane; its BFS family costs minutes at the 56k-edge scale). The 8.0s
    # budget stays as a regression ceiling, not the expectation.
    ("graph_rebuild", ["helpers/graph/query.py", "rebuild"], 8.0),
    # test_gap_closure S3: the only legs that drive the FLASK REQUEST PATH.
    # Every other entry invokes a helper script directly, so the gate could
    # not see the AVAIL-1 class (a 53 s unauthenticated GET). Best-of-3 per
    # route with a warm-up call; budgets are steady-state x ~10 (see the
    # script for the measured baselines).
    # Budget is the END-TO-END process cost (interpreter + Flask app +
    # graph-layer build + 4 routes), measured 3.3 s stable 2026-09-20.
    # The route-level budgets that catch the AVAIL-1 class live INSIDE the
    # script (best-of-3 per route against a 0.5 s bar), so this outer
    # budget only guards the harness itself.
    ("route_graph_stats", ["tests/bench_routes.py"], 5.0),
    # sql_capability_unlocks B2 gate: BFS shortest_path steady-state
    # (<100ms on the default request AND the unreachable-dst full
    # component traversal, asserted inside the script).
    # 10.0s since 2026-09-23: the inner per-case budgets (100ms default /
    # 250ms unreachable) are the real gate and PASS comfortably at VIGIL
    # scale (12ms / 76ms measured). The outer budget only covers process +
    # connect + best-of-3 harness cost, which hit 8.17s when scheduled
    # right after the minutes-scale --compute legs (page-cache/scheduler
    # pressure). Headroom for that adjacency, no semantics change.
    ("shortest_path_bfs", ["tests/bench_shortest_path.py"], 10.0),
    (
        "extract_relations",
        [
            "helpers/graph/extract_relations.py",
            "findata/The_Chatter",
            "findata/Points_And_Figures",
            "findata/The_PlotLines",
            "--no-write-sidecar",
        ],
        5.0,
    ),
    (
        "derive_co_mentions",
        ["helpers/graph/derive_co_mentions.py", "--newsletter", "The_Chatter"],
        2.0,
    ),
    ("derive_events", ["helpers/graph/derive_events.py"], 3.0),
    # Search-index freshness --checks LEFT perf 2026-08-26 (#159): the
    # STALE gate is owned by `make search-fresh` + the advisory gate's
    # doc/script/note-search-check rows (exit 1 on drift, per-index report
    # tails). Perf keeps only the QUERY-latency pair below.
    ("doc_query", ["helpers/misc/doc_query.py", "embed cache sidecar", "--limit", "5"], 3.0),
    ("script_query", ["helpers/misc/script_query.py", "database integrity", "--limit", "5"], 3.0),
    # Local PDF pipeline (#156/#157) on the largest Reports PDF (30 pp):
    # convert + render + verify. Warm ≈3.2s on this box (2026-09-01; the
    # older ≈7.5s figure was a slower corpus/machine state); Tesseract on
    # embedded rasters dominates; internal sub-budgets asserted by the
    # script itself. Budget tightened 20s -> 7s (2026-09-01): 2.2x headroom
    # over the measured warm time while still catching regressions.
    ("pdf_pipeline_local", ["tests/bench_pdf_pipeline.py"], 7.0),
    ("derive_insights", ["helpers/graph/derive_insights.py"], 7.0),
    (
        "parse_newsletter",
        ["helpers/core/parse_newsletter.py", "findata/The_Chatter/Embracing_the_Unknown.md"],
        3.0,
    ),
    (
        "enrich_yfinance",
        # bare = dry-run report since guard unification (2026-09-03); the old
        # --dry-run flag is gone (unrecognized → argparse rc 2).
        ["helpers/maintenance/enrich_from_yfinance.py", "--company", "Infosys"],
        5.0,
    ),
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
    from datetime import datetime

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.perf_counter()
    results: list[tuple[str, float, str, bool, float]] = []
    for label, args, budget in BENCHMARKS:
        print(f"  running {label:.<30s}", end="", flush=True)
        dt, status, ok = run_one(label, args, budget)
        results.append((label, dt, status, ok, budget))
        print(f" {dt:6.2f}s  [{status}]")
        if not ok:
            # surface stderr on failure for immediate feedback
            r = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
                [sys.executable, *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                cwd=REPO_ROOT,
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

    # ── append to report (markdown format) ──
    elapsed = time.perf_counter() - t0
    write_report(results, started, elapsed)

    return 0 if all_ok else 1


def write_report(
    results: list[tuple[str, float, str, bool, float]], started: str, elapsed: float
) -> None:
    """Append one ``#``-delimited run block to outputs/perf_report.md.

    Appending (not overwriting) is the cross-report convention: the TUI
    reports panel reads the run chain; pinned by
    tests/test_run_perf_benchmarks.py.
    """
    from datetime import datetime

    ended = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for *_, ok, _ in results if ok)
    total = len(results)
    md_lines: list[str] = []
    md_lines.append("# make perf — benchmark report")
    md_lines.append("")
    md_lines.append(
        f"**Generated:** {ts}  ·  **Started:** {started}  ·  **Ended:** {ended}  ·  "
        f"**Elapsed:** {elapsed:.1f}s  ·  **Python:** {sys.version.split()[0]}"
    )
    md_lines.append("")
    md_lines.append("| Benchmark | Time (s) | Budget | Status |")
    md_lines.append("|---|---|---|---|")
    for label, dt, status, ok, budget in results:
        flag = "✓ OK" if ok else "✗ FAIL"
        md_lines.append(f"| {label} | {dt:.2f} | {budget:.1f}s | {flag} |")
    md_lines.append(f"| **{passed}/{total} passed** | | | |")
    md_lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "a") as f:
        f.write("\n".join(md_lines))


if __name__ == "__main__":
    raise SystemExit(main())
