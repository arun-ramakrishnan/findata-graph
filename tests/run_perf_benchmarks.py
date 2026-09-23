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
#
# S5 re-baseline (graph_perf_l1, 2026-09-23, post S2/S3 landing; S1 pending
# scipy_improvs apply). The three VIGIL-era DELIBERATE-RED legs are retired:
# closeness/betweenness/link-prediction now run on cheap lanes and the
# minutes-scale wall is gone from the default gate — so NO perf-cold split
# is needed; the only minutes-scale job left (the v_centrality_* stamp,
# ~5-6 min) was already an explicit `make stamp-centrality` target.
# Fresh measured -> budget, with the scaling class each budget guards:
#
#   leg                      measured    budget  scaling class guarded
#   graph_l1_centrality      2.6-2.9 s   5.0     O(sources x E) restricted
#                                                dijkstra (1,734 x 15.8k)
#   graph_l1_betweenness     3.8-5.3 s   8.0     O(core_sources x core_E)
#                                                Brandes (3,385 x 40k);
#                                                peeled leaves are O(1)
#                                                closed-form each
#   graph_link_prediction    2.5-2.7 s   4.0     O(E x avg_deg) 2-hop join
#                                                (633k slots); hub cap 512
#                                                bounds the star term
#   graph_rebuild            2.5-2.7 s   5.0     O(E) data-only rebuild
#                                                (tightened from 8.0 — was
#                                                a regression ceiling)
#   snapshot_check           4.6 s       6.0     O(tables) verify; grew with
#                                                S4's per-table zst
#                                                roundtrip (58 tables)
#   integrity_check          4.4-4.7 s   10.0    O(entities+relations)
#                                                (operator-approved headroom)
#   shortest_path_bfs        8.2-8.3 s   10.0    inner per-route asserts are
#                                                the real gate (12/76 ms)
#
# Super-linear decay (the thing these budgets catch) would show as a leg
# blowing past its budget at constant corpus: 2-hop slots grow ~E x d,
# Brandes ~core^2, dijkstra ~sources x E — none of which track total node
# count unless the CORE grows, which is exactly what the fold contains.
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
    # 6.0 since 2026-09-23 (S5): S4 made --check verify every parquet table
    # with a zstd roundtrip (58 tables) — legitimate assurance growth; was
    # 4.0s and measured 4.55-4.67 standalone.
    ("snapshot_check", ["helpers/maintenance/snapshot_db.py", "--check"], 6.0),
    ("graph_pagerank", ["helpers/graph/algorithms.py", "pagerank", "--top", "10"], 3.0),
    # --compute (graph_centrality_persistent_cache): the centrality legs
    # bypass the v_centrality_* disk cache so these budgets keep
    # measuring the compute path; the cache path is smoke-tested
    # in tests/test_centrality_cache.py instead.
    # GREEN since 2026-09-23 (graph_perf_l1 S1, route (d)): the leg
    # measures the scipy restricted-source lane — 1,734 contract sources,
    # leaves stay as targets so contract scores are exact. Both metrics
    # from one dijkstra pass: 2.9s wall 4-way vs the 156.8s Onager
    # full-walk closeness + ~192s stamp-share harmonic (the closeness leg
    # was DELIBERATELY RED at 31x budget; the tracker now rides
    # betweenness + link_prediction until L1b/L1c). Spike:
    # /tmp/graph_spike.txt PART 3.
    (
        "graph_l1_centrality",
        ["helpers/graph/scipy_bridge.py", "closeness-harmonic", "--jobs", "4", "--top", "10"],
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
    # L1b: folded exact betweenness (2-core fold + tree analytic).
    # RAW end-to-end ~3.85s (4-way fork), O(V+E) linear scaling.
    # Leaves tree nodes as analytic; attachments store k-weighted credits.
    # DELIBERATELY RED at pre-VIGIL budget 4.0s — measured 48.9s full-walk Onager,
    # 3.85s folded. Operator approved 8.0s headroom for scale-fair budget
    # (baseline: core 3,385 nodes, edges 40,031, fold 18,265 trees).
    # See l1_betweenness.py betweenness-fold --jobs <NUM> for fork-split.
    (
        "graph_l1_betweenness",
        ["helpers/graph/l1_betweenness.py", "betweenness-fold", "--jobs", "4"],
        8.0,
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
    # GREEN since 2026-09-23 (graph_perf_l1 S3): scoring moved from the
    # Onager all-pairs extension (~485M pairs, 57-62s) to a SQL 2-hop
    # candidate join over the materialised projection — exact for the
    # shared-neighbour methods (score > 0 <=> shared neighbour), hub-side
    # degree cap 512 + per-lo top-K early exit. 61s -> 0.65s in-process,
    # 2.7s end-to-end process (boot-dominated); top-10 bit-identical vs the
    # unpruned run. Budget 4.0 = measured + ~50% boot-noise headroom;
    # re-tighten in S5's table pass.
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
        4.0,
    ),
    # Back to ~3.0s since centrality_rebuild_contract (2026-09-22): the
    # rebuild is data-only again — the ten v_centrality_* tables are
    # dropped, not stamped (the stamp is the explicit stamp-centrality
    # lane; its BFS family costs minutes at the 56k-edge scale). The 8.0s
    # budget stays as a regression ceiling, not the expectation.
    # 5.0 since 2026-09-23 (S5): measured 2.5-2.7s; the old 8.0 was a
    # post-redistribution regression ceiling, tightened to a real signal.
    ("graph_rebuild", ["helpers/graph/query.py", "rebuild"], 5.0),
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
