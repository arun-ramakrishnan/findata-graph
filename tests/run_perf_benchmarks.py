#!/usr/bin/env python3
"""Performance benchmark runner — individual wall-clock timings + report.

Runs each perf-gated benchmark individually under ``time.perf_counter`` and
prints a formatted table to stdout, then appends the same table to
``outputs/perf_report.md``.  Invoked by ``make perf``.

Usage::

    python3 tests/run_perf_benchmarks.py [--force]

Exit codes: ``0`` all benches within budget · ``1`` a bench failed ·
``3`` CONTENDED — the box was busy (another gate lane active, or load
above the core count), so timings would lie. A refused run appends
nothing; a mid-run trip marks the remaining rows untrusted in the
appended block (see the contention guard below). ``--force`` runs
through contention: the start refusal and the mid-run cutoff are
skipped, the appended block carries a FORCED **Status:** line, rows
keep pressure attribution, and the exit code reflects leg results
only.

Failure rows carry attribution in the report's Status cell —
``✗ FAIL(budget)`` / ``✗ FAIL(rc)`` / ``✗ FAIL(contended)`` /
``✗ FAIL(budget, psi avg10 18.3%)`` — so a loaded box's red wall is
readable as load, not regression (the run-1787 lesson). The ``FAIL``
substring is invariant: gate_query's failure surfaces key on it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "outputs" / "perf_report.md"
_TAIL_CAP = 2000  # per-leg stderr ceiling; matches gate_query's err_head cap

# ── Scratch expansion (2026-10-22, quack arc) ───────────────────────────
# A bench leg may name "{scratch}" in its argv: it expands to a FRESH
# temp dir per invocation (under $TMPDIR), removed when the run ends.
# The graph_rebuild leg uses it because the rebuild is a SWAP build
# (os.replace of the target inode): refused while the store is served by
# the quack server, and destructive to the live cache. A scratch target
# keeps the same code path while leaving the live store alone.
_SCRATCH_DIRS: list[Path] = []


def _expand_scratch(args: list[str]) -> list[str]:
    """Replace the {scratch} placeholder with a fresh temp dir."""
    if not any("{scratch}" in a for a in args):
        return args
    scratch = Path(tempfile.mkdtemp(prefix="perf-scratch-"))
    _SCRATCH_DIRS.append(scratch)
    return [a.replace("{scratch}", str(scratch)) for a in args]


def _cleanup_scratch() -> None:
    for d in _SCRATCH_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _SCRATCH_DIRS.clear()


# ── Contention guard (2026-10-12) ────────────────────────────────────────
# Perf benches measure wall-clock truth, so a run that overlaps another
# lane reports CONTENDED instead of FAIL — today's evidence: perf 1456
# ran 475.9s (vs 75.6s quiet) at 6/26 while qa's pytest (896s) and a
# convo-fresh check shared the window, and every leg class dragged
# uniformly 2-4x over budget. Two probes, either one refuses:
#   - lane heartbeat: run_gate_report.py publishes outputs/.gate_active
#     (pid + lane) for its whole run — a cross-lane race, stated plainly;
#   - CPU pressure: /proc/pressure/cpu "some avg10" is the sharpest no-lag
#     signal of runnable-but-stalled tasks. Calibration on this 4c/4t
#     i5-6500 (2026-10-12): idle desktop <5%, one background embed stream
#     + browser 14.7%, pytest -n4 far higher — while our own serial bench
#     sits at ~0-2%. 10% catches the embed-stream class without tripping
#     on ourselves.
# (Heartbeat locations resolved by _heartbeat_paths() below — the publisher
# contract lives in run_gate_report._outputs_root; see review F5.)
#
# REFUSAL policy (2026-10-31 operator directive): the start/mid-run
# refusal keys on load1 vs the CORE COUNT — a box is "too busy to time"
# only when load1 > _LOAD_RUN_CEIL (= nproc), so light desktop load (the
# run-2066 all-contended wall) can run. PSI keeps the diagnostic roles:
# per-leg attribution below, plus the guard fallback on loadavg-less
# hosts. ``--force`` overrides both refusal paths.
_PSI_SOME_CEIL = 10.0  # percent, "some avg10" — start guard, loadavg-less hosts only
_PSI_SUSTAINED_CEIL = 15.0  # percent, "some avg60" — attribution ceiling: sustained external
# load trips; our own multi-thread bench bursts (centrality/betweenness measured
# ~10.3% avg10 on a quiet run, 2026-10-12) cannot hold a 60s window that high.
_LOAD_CEIL = 2.0  # 1-minute load for ATTRIBUTION tags only — run-1787 lesson stays sensitive
_LOAD_RUN_CEIL = float(os.cpu_count() or 1)  # refusal bar: load1 vs cores ("unless load > nproc")

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
#   graph_link_prediction    6.1 s (fb) 8.0     fallback-lane sparse matmul
#                                                (sub-second) + boot/wire
#                                                fetch; was 2.5-2.7 s onager
#   derive_insights          4.8-7.5 s   9.0     scan/render wall (corpus
#                                                growth); +2 s operator
#                                                boost 09-26
#   graph_rebuild            2.5-2.7 s   5.0     O(E) data-only rebuild
#                                                (tightened from 8.0 — was
#                                                a regression ceiling)
#   snapshot_check           4.6 s       6.0     O(tables) verify; grew with
#                                                S4's per-table zst
#                                                roundtrip (58 tables)
#   route_graph_stats         3.3-5.5 s   7.5     end-to-end Flask boot +
#                                                graph build + 4 routes;
#                                                per-route 0.5 s bars live
#                                                inside bench_routes.py
#   integrity_check          4.4-4.7 s   10.0    O(entities+relations)
#                                                (operator-approved headroom)
#   shortest_path_bfs        8.1-10.2 s  12.0    inner per-route asserts are
#                                                the real gate (12/76 ms);
#                                                +2 s operator boost 09-26
#   cosine_knn_ratios        2.4 s       15.0    inner RATIO bars are the
#                                                real gate (>=1.3x numpy,
#                                                >=5x pure); outer budget
#                                                guards only the harness
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
    # 9.0 since 2026-10-01 (gate_wall_time_reclaim S3): --check verifies
    # every parquet table with a zstd roundtrip and the corpus has grown
    # past the 58-table assumption the 6.0 budget was sized against
    # (2026-09-23, S4/S5) — standalone breach measured 6.99s in the run-1155
    # era perf reports (5.33-6.99s band). Re-baseline if the table count
    # jumps again.
    ("snapshot_check", ["helpers/maintenance/snapshot_db.py", "--check"], 9.0),
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
        [
            "helpers/graph/scipy_bridge.py",
            "closeness-harmonic",
            "--jobs",
            "4",
            "--top",
            "10",
        ],
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
    # degree cap 512 + per-lo top-K early exit. Onager era: 2.5-2.7s
    # end-to-end. Since the 2.0 fallback lanes (completed.md #427) the LP
    # is a single sparse matmul (sub-second) but the lane is boot+wire
    # fetch dominated: 6.15s end-to-end (39.8s pre-vectorisation).
    # The onager shape RETURNED 2026-10-24 (onager_vendored_source_build):
    # the vendored v0.5.0 extension + extension-first dispatch measured
    # 2.88s (loaded box, [via onager] in the CLI output). Budget stays 8.0
    # until the operator's quiet-box gate arc re-bases it toward 4.0.
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
        8.0,
    ),
    # Back to ~3.0s since centrality_rebuild_contract (2026-09-22): the
    # rebuild is data-only again — the ten v_centrality_* tables are
    # dropped, not stamped (the stamp is the explicit stamp-centrality
    # lane; its BFS family costs minutes at the 56k-edge scale). The 8.0s
    # budget stays as a regression ceiling, not the expectation.
    # 5.0 since 2026-09-23 (S5): measured 2.5-2.7s; the old 8.0 was a
    # post-redistribution regression ceiling, tightened to a real signal.
    # quack arc (2026-10-22): the rebuild is a SWAP build (os.replace of the
    # target inode) — refused while the store is served by the quack server
    # (assert_not_served) and destructive to the live cache. {scratch} = a
    # fresh temp dir per invocation: same code path, no live mutation.
    (
        "graph_rebuild",
        [
            "helpers/graph/query.py",
            "rebuild",
            "--duckdb",
            "{scratch}/graph_rebuild.duckdb",
        ],
        5.0,
    ),
    # test_gap_closure S3: the only legs that drive the FLASK REQUEST PATH.
    # Every other entry invokes a helper script directly, so the gate could
    # not see the AVAIL-1 class (a 53 s unauthenticated GET). Best-of-3 per
    # route with a warm-up call; budgets are steady-state x ~10 (see the
    # script for the measured baselines).
    # Budget is the END-TO-END process cost (interpreter + Flask app +
    # graph-layer build + 4 routes), measured 3.3 s stable 2026-09-20;
    # 7.5 s since 2026-10-23 (operator call): quack-wire fetch of the
    # 130k-edge projection + fallback-lane boot put the observed outer
    # cost at 5.5 s (perf 1801), still far under the onager-era legs.
    # The route-level budgets that catch the AVAIL-1 class live INSIDE the
    # script (best-of-3 per route against a 0.5 s bar), so this outer
    # budget only guards the harness itself.
    ("route_graph_stats", ["tests/bench_routes.py"], 7.5),
    # sql_capability_unlocks B2 gate: BFS shortest_path steady-state
    # (<100ms on the default request AND the unreachable-dst full
    # component traversal, asserted inside the script).
    # 10.0s since 2026-09-23: the inner per-case budgets (100ms default /
    # 250ms unreachable) are the real gate and PASS comfortably at VIGIL
    # scale (12ms / 76ms measured). The outer budget only covers process +
    # connect + best-of-3 harness cost, which hit 8.17s when scheduled
    # right after the minutes-scale --compute legs (page-cache/scheduler
    # pressure). Headroom for that adjacency, no semantics change.
    ("shortest_path_bfs", ["tests/bench_shortest_path.py"], 12.0),
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
    # doc/script/note/memory-search-check rows (exit 1 on drift, per-index
    # report tails). Perf keeps only the QUERY-latency rows below.
    (
        "doc_query",
        ["helpers/misc/doc_query.py", "embed cache sidecar", "--limit", "5"],
        3.0,
    ),
    (
        "script_query",
        ["helpers/misc/script_query.py", "database integrity", "--limit", "5"],
        3.0,
    ),
    (
        "memory_query",
        ["helpers/misc/memory_query.py", "stg refresh scope", "--limit", "5"],
        3.0,
    ),
    # Local PDF pipeline (#156/#157) on the largest Reports PDF (30 pp):
    # convert + render + verify. Warm ≈3.2s on this box (2026-09-01; the
    # older ≈7.5s figure was a slower corpus/machine state); Tesseract on
    # embedded rasters dominates; internal sub-budgets asserted by the
    # script itself. Budget tightened 20s -> 7s (2026-09-01): 2.2x headroom
    # over the measured warm time while still catching regressions.
    ("pdf_pipeline_local", ["tests/bench_pdf_pipeline.py"], 7.0),
    ("derive_insights", ["helpers/graph/derive_insights.py"], 9.0),
    (
        "parse_newsletter",
        [
            "helpers/core/parse_newsletter.py",
            "findata/The_Chatter/Embracing_the_Unknown.md",
        ],
        3.0,
    ),
    (
        "enrich_yfinance",
        # bare = dry-run report since guard unification (2026-09-03); the old
        # --dry-run flag is gone (unrecognized → argparse rc 2).
        ["helpers/maintenance/enrich_from_yfinance.py", "--company", "Infosys"],
        5.0,
    ),
    # Near-duplicate GEMM endpoint (near_duplicate_guard_invariant S2,
    # 2026-10-01): this compute had NO leg and NO budget — the function's
    # ~2 s live wall was invisible to make perf. Two thresholds budgeted
    # SEPARATELY so a default-vs-wide divergence shows in the table rather
    # than averaging away. The 4x accumulator prune fires 188x on the
    # default path (peak pinned at 4 x limit), so the wide threshold is
    # consistently the slower leg. Measured CLI walls 2026-10-01:
    # min_sim 0.9 -> 2.2-2.4 s warm (4.3 s cold first-open);
    # min_sim 0.01 -> 3.5-3.6 s. Budgets ~2x warm headroom, still far
    # below any super-linear decay.
    (
        "near_duplicates_default",
        [
            "helpers/graph/query.py",
            "near-duplicates",
            "--min-sim",
            "0.9",
            "--limit",
            "100",
        ],
        7.0,
    ),
    (
        "near_duplicates_wide",
        [
            "helpers/graph/query.py",
            "near-duplicates",
            "--min-sim",
            "0.01",
            "--limit",
            "100",
        ],
        8.0,
    ),
    # rust_cosine_knn_port: the Rust kernel's reason-to-exist, measured as
    # RATIOS at the live script-search shape (717 x 512) — the leg asserts
    # rust >= 1.3x numpy and >= 5x the pure loop, NOT an absolute q/s.
    # Absolute numbers were deliberately avoided: they encode this CPU and
    # would punish a faster box, and this 4-core i5-6500 runs `powersave`
    # (3300-3488 MHz observed), so absolute budgets are flaky here.
    # Calibration 2026-10-27, quiet box (interleaved): rust 1.00-1.11 ms/query
    # (~900-1000 q/s), numpy 2.62-2.88 ms (350-382 q/s) = 2.53-2.59x, pure
    # 42.9-43.8 ms (23 q/s) = 41-44x; per-round numpy ratio held 2.36-2.86,
    # so 1.3x sits outside the noise floor, not inside it. The 1.3x bar exists
    # to catch SILENT DEGRADATION: with no abi3 module the selector falls
    # back to numpy and both halves time the same code, collapsing the
    # ratio to ~1.0 — rankings stay correct, so only this leg notices.
    # Verified red on exactly that failure (0.96x, exit 1) and green on the
    # rest; also verified SKIP-exit-0 with the module hidden.
    # Runtime ~2.5 s (warm-up + 5 rounds x 60 queries x2 + 5 pure); the 15.0 s
    # budget only guards the harness itself, like route_graph_stats does.
    # SKIPs with exit 0 when the module is not built; qa's
    # SCRIPT_KNN_STRICT=1 remains the hard pin for a vanished .so.
    ("cosine_knn_ratios", ["Rust/bench/cosine_gate.py"], 15.0),
]


def _psi_some(window: str = "avg10") -> float | None:
    """CPU pressure-stall "some" for ``window`` (avg10/avg60/avg300) in
    percent; None when PSI is absent."""
    try:
        text = Path("/proc/pressure/cpu").read_text()
    except OSError:
        return None
    for ln in text.splitlines():
        if ln.startswith("some "):
            for field in ln.split()[1:]:
                if field.startswith(f"{window}="):
                    try:
                        return float(field.split("=", 1)[1])
                    except ValueError:
                        return None
    return None


def _heartbeat_paths() -> list[Path]:
    """Candidate `.gate_active` locations, publisher path first.

    Mirrors run_gate_report._outputs_root (the publisher contract, which names
    this probe as its consumer): inside a linked worktree the gate lane
    publishes under <main>/outputs/wt/<name>/, not the worktree-local outputs/.
    The local path stays as fallback (a main-checkout lane + a worktree perf
    run meet there). Review F5: polling only the local path never fires in a
    worktree, so the lane probe silently never fires and perf runs contended.
    """
    local = REPO_ROOT / "outputs" / ".gate_active"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],  # noqa: S607  # PATH-resolved git by design (same as stgit/node call sites)
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
        ).stdout.strip()
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],  # noqa: S607  # PATH-resolved git by design (same as stgit/node call sites)
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return [local]
    if not out or not common:
        return [local]
    git_p, common_p = Path(out).resolve(), Path(common).resolve()
    if git_p == common_p:
        return [local]
    main_pub = common_p.parent / "outputs" / "wt" / git_p.name / "outputs" / ".gate_active"
    return [main_pub, local] if main_pub != local else [local]


def _gate_lane_active() -> str | None:
    """Description of a live qa/integration/advisory lane, or None.

    pid-liveness keeps a crashed lane's stale heartbeat from blocking
    forever; PermissionError still means alive (same user here, but cheap
    to be correct).
    """
    for beat in _heartbeat_paths():
        try:
            raw = json.loads(beat.read_text())
            pid = int(raw["pid"])
        except OSError, ValueError, KeyError, TypeError:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        return f"gate lane {raw.get('lane', '?')} active (pid {pid}, started {raw.get('started', '?')})"
    return None


def _contention_reason(midrun: bool = False) -> str | None:
    """Why wall-clock timing would lie right now, or None when quiet.

    2026-10-31 operator directive: refusal keys on load1 vs the core
    count — unless load > num_cpus, the run proceeds — so light desktop
    load can never wall perf off again (run-2066). The calibrated PSI
    ceilings survive as the guard fallback on loadavg-less hosts and in
    the per-leg attribution (_load_pressure_reason). ``--force`` (main)
    bypasses this guard entirely.
    """
    lane = _gate_lane_active()
    if lane is not None:
        return lane
    try:
        load1: float | None = os.getloadavg()[0]
    except OSError:  # loadavg-less host — fall back to the PSI ceilings
        load1 = None
    if load1 is not None:
        if load1 > _LOAD_RUN_CEIL:
            return f"load1 {load1:.2f} > {_LOAD_RUN_CEIL:g} cores (box saturated)"
        return None
    psi = _psi_some("avg10")
    if psi is None:
        return None
    if midrun:
        sustained = _psi_some("avg60") or 0.0
        if sustained > _PSI_SUSTAINED_CEIL:
            return (
                f"CPU pressure some avg60 {sustained:.1f}% > "
                f"{_PSI_SUSTAINED_CEIL:.0f}% ceiling (sustained external load)"
            )
        return None
    if psi > _PSI_SOME_CEIL:
        return f"CPU pressure some avg10 {psi:.1f}% > {_PSI_SOME_CEIL:.0f}% ceiling"
    return None


def _load_pressure_reason() -> str | None:
    """External-pressure evidence at this instant, for failure attribution.

    The refusal guard (_contention_reason) refuses a run whose timings
    would lie; this probe is diagnostic — a failed leg gets tagged with
    WHY it probably failed. Same self-load constraint though: our own
    bench bursts (centrality/betweenness measured ~10.3% avg10) must not
    read as "external load", so avg10 is held to the SUSTAINED ceiling,
    not the start ceiling. avg60 (the guard's window) is too laggy to
    catch a spike that began and ended inside one leg, hence avg10 here.
    Lane activity is binary fact. Returns a short fragment for the status
    cell ("psi avg10 18.3%" / "load1 3.41" / "lane qa") or None.
    """
    lane = _gate_lane_active()
    if lane is not None:
        m = re.search(r"lane (\S+) active", lane)
        return f"lane {m.group(1)}" if m else "gate lane"
    psi = _psi_some("avg10")
    if psi is not None:
        if psi > _PSI_SUSTAINED_CEIL:
            return f"psi avg10 {psi:.1f}%"
        return None
    load1 = os.getloadavg()[0]
    if load1 > _LOAD_CEIL:
        return f"load1 {load1:.2f}"
    return None


def run_one(label: str, args: list[str], budget: float) -> tuple[float, str, bool, str]:
    """Run a single benchmark, return (elapsed_seconds, status, passed, stderr_tail).

    The tail (last ~2 KB of stderr) rides the result so failures surface in
    the report's ``## <label> (FAILED)`` sections — gate_query's err_head —
    without the old failure-time subprocess re-run (which doubled the cost
    of every red leg and printed to a console nobody archives).
    """
    t0 = time.perf_counter()
    r = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        [sys.executable, *_expand_scratch(args)],
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
    tail = r.stderr.decode(errors="replace").strip()[-_TAIL_CAP:]
    return dt, status, ok, tail


def _run_all_benchmarks(
    force: bool = False,
) -> tuple[list[tuple[str, float, str, bool, float, str]], str | None, str | None]:
    """Run every BENCHMARKS leg; returns (results, contended_from, contend_why).

    Extracted from main() (C901): the mid-run contention probe + the
    failure-stderr surface live here, the table/report tail stays in main.
    ``force=True`` (``--force``) skips the mid-run cutoff: rows still get
    pressure attribution, the run just never declares itself untrusted.
    """
    results: list[tuple[str, float, str, bool, float, str]] = []
    contended_from: str | None = None
    contend_why: str | None = None
    for label, args, budget in BENCHMARKS:
        if contended_from is None and not force:
            mid = _contention_reason(midrun=True)
            if mid is not None:
                contended_from = label
                contend_why = mid
                print(
                    f"\n  ⚠ contention mid-run: {mid}\n  rows from {label} on are untrusted",
                    file=sys.stderr,
                )
        print(f"  running {label:.<30s}", end="", flush=True)
        # Pressure endpoints bracket the leg: avg10 before + after catches a
        # spike that started and ended inside the leg's window (avg60 lags).
        pre_load = _load_pressure_reason()
        dt, status, ok, tail = run_one(label, args, budget)
        if contended_from is not None:
            status = "CONTENDED"
            ok = False
        elif not ok:
            post_load = _load_pressure_reason()
            load_frag = pre_load or post_load
            if load_frag:
                # attribution, not excuse: the row still FAILs; the tag says
                # the box was busy while it did (run 1787 lesson — a loaded
                # box painted 15 legs red and read as a regression wall).
                status = f"{status} [{load_frag}]"
        results.append((label, dt, status, ok, budget, tail))
        print(f" {dt:6.2f}s  [{status}]")
        if not ok and tail:
            print(f"    stderr: {tail[:200]}", file=sys.stderr)
    return results, contended_from, contend_why


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_perf_benchmarks",
        description="Wall-clock perf benchmarks; appends outputs/perf_report.md.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run through contention: skip the start refusal and the mid-run "
        "cutoff; rows keep pressure attribution, exit reflects results",
    )
    ns = parser.parse_args(argv)

    # ── run ──
    from datetime import datetime

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    refuse = _contention_reason()
    forced_note: str | None = None
    if refuse is not None and ns.force:
        forced_note = f"FORCED — contended at start: {refuse} — timings untrusted"
        print(f"  ⚠ --force: {forced_note}", file=sys.stderr)
    elif refuse is not None:
        print(f"perf refused — contended box: {refuse}", file=sys.stderr)
        print(
            "timings would lie; re-run once the lane clears (exit 3, nothing "
            "appended) or re-run with --force to run anyway",
            file=sys.stderr,
        )
        return 3

    t0 = time.perf_counter()
    results, contended_from, contend_why = _run_all_benchmarks(force=ns.force)

    # ── table ──
    lines: list[str] = []
    lines.append("")
    lines.append("Benchmark                              Time (s)   Budget   Status")
    lines.append("-" * 70)
    all_ok = True
    for label, dt, status, ok, budget, _tail in results:
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
    status_note = None
    if contended_from is not None:
        status_note = f"CONTENDED — {contend_why} — rows from {contended_from} on are untrusted"
        print(f"  ⚠ {status_note}")
    elif forced_note is not None:
        status_note = forced_note
    write_report(results, started, elapsed, status_note=status_note)

    if contended_from is not None:
        _cleanup_scratch()
        return 3
    _cleanup_scratch()
    return 0 if all_ok else 1


def _md_status(status: str, ok: bool) -> str:
    """The md Status cell for one leg.

    Every failure row keeps the literal ``FAIL`` — gate_query's failure
    surfaces (_pick_run/_run_digest) key on that substring, and a
    contended run must stay VISIBLE there, never silently green. The
    parenthesised attribution is new (2026-10-24): budget vs rc vs
    contended vs load-coincident, so a loaded box's red wall reads as
    load, not regression. ``[frag]`` is appended by _run_all_benchmarks
    when a leg failed under measured external pressure.
    """
    if ok:
        return "✓ OK"
    if status == "CONTENDED":
        return "✗ FAIL(contended)"
    kind = "rc" if status.startswith("FAIL(rc)") else "budget"
    m = re.search(r"\[(.+)\]$", status)
    return f"✗ FAIL({kind}, {m.group(1)})" if m else f"✗ FAIL({kind})"


def write_report(
    results: list[tuple[str, float, str, bool, float, str]],
    started: str,
    elapsed: float,
    status_note: str | None = None,
) -> None:
    """Append one ``#``-delimited run block to outputs/perf_report.md.

    Appending (not overwriting) is the cross-report convention: the TUI
    reports panel reads the run chain; pinned by
    tests/test_report_append_convention.py. ``status_note`` (a contention
    trip) adds a **Status:** line after the Generated header; the parser
    ignores lines it doesn't match, so the chain stays readable.

    Failing legs additionally get a ``## <label> (FAILED)`` section carrying
    the leg status and the captured stderr tail — the same section grammar
    run_gate_report writes, which is what gate_query's ``err_head`` greps.
    Before 2026-10-26 a red perf leg stored err_head NULL: the report had
    no sections to find (run 1869: integrity_check FAIL(rc) invisible in
    ``gate_query failures``).
    """
    from datetime import datetime

    ended = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for *_, ok, _, _ in results if ok)
    total = len(results)
    md_lines: list[str] = []
    md_lines.append("# make perf — benchmark report")
    md_lines.append("")
    md_lines.append(
        f"**Generated:** {ts}  ·  **Started:** {started}  ·  **Ended:** {ended}  ·  "
        f"**Elapsed:** {elapsed:.1f}s  ·  **Python:** {sys.version.split()[0]}"
    )
    if status_note:
        md_lines.append("")
        md_lines.append(f"**Status:** {status_note}")
    md_lines.append("")
    md_lines.append("| Benchmark | Time (s) | Budget | Status |")
    md_lines.append("|---|---|---|---|")
    for label, dt, status, ok, budget, _tail in results:
        flag = _md_status(status, ok)
        md_lines.append(f"| {label} | {dt:.2f} | {budget:.1f}s | {flag} |")
    md_lines.append(f"| **{passed}/{total} passed** | | | |")
    for label, _dt, status, ok, _budget, tail in results:
        if ok:
            continue
        md_lines.append("")
        md_lines.append(f"## {label} (FAILED)")
        md_lines.append("")
        md_lines.append(f"status: {status}")
        md_lines.append(tail if tail else "(no stderr output)")
    md_lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "a") as f:
        f.write("\n".join(md_lines))


if __name__ == "__main__":
    raise SystemExit(main())
