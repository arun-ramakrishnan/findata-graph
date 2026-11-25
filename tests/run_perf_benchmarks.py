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

A leg may also declare a SKIP instead of a result: exit 0 with a line
starting ``SKIP:`` on stdout (the documented contract for optional
modules — extension-gated and live-store legs). 2026-11-24 lesson:
``ok = rc == 0 and dt < budget`` scored such legs as fast passes —
``l1_kernels`` "passed" at 0.19s against a 240s budget for six runs
while its store was gone. A skip is now recorded SKIPPED — neither OK
nor FAIL: it never counts toward ``passed``, the footer counts skips on
their own, perf.json rows carry ``"status": "warn"`` (the gate_query
perf-json.v1 adapter's non-pass slot; unknown statuses coerce to pass),
the report gains a ``## <label> (SKIPPED)`` section, and the run still
exits 0 when nothing actually failed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.misc import gate_retain  # noqa: E402  # imported after the sys.path seed above

REPORT = REPO_ROOT / "outputs" / "perf_report.md"
_TAIL_CAP = 2000  # per-leg stderr ceiling; matches gate_query's err_head cap
# A leg's stdout skip declaration (see module docstring). Matched against
# the whole stdout so a SKIP banner anywhere in the output counts.
_SKIP_RE = re.compile(r"^SKIP:.*$", re.MULTILINE)

# ── Artifact retention (gate_output_fidelity S4) ────────────────────────
# Perf used to retain NOTHING: subprocess.run(capture_output=True) kept a
# 2 KB stderr tail and discarded the rest, so all 173 indexed perf runs
# carried artifact_state='not_collected' and `gate_query output --gate perf
# --leg derive_events` answered "no retained artifact directory" (measured
# 2026-11-09). The lane now writes, per run, into
# `<outputs>/.artifacts/perf/<run-id>/`:
#   perf.<leg>.log   complete stdout+stderr of every leg
#   perf.json        machine-readable rows (gate_query's perf-json.v1 adapter)
# and stamps the report block with **Artifacts:** so the index can find it.
# The artifacts root follows the REPORT file, not a worktree guess: gate_query
# resolves `**Artifacts:**` against the outputs root it found the report in,
# so the two must be the same tree even when a linked worktree writes locally.
OUTPUTS_ROOT = gate_retain.artifacts_root_for(REPORT).parent
ARTIFACTS_ROOT = gate_retain.artifacts_root_for(REPORT)
_ARTIFACT_KEEP_RUNS = 40  # bounded like gate_query rotate's default keep policy
_CURRENT_LOG_DIR: Path | None = None  # set per run by _run_all_benchmarks
_RUN_ID: str | None = None  # this run's artifact dir name; set by main()

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


_STORE_CLONE_DIR = REPO_ROOT / "outputs" / ".perf_clone"


def _prepare_store_clone() -> Path | None:
    """Copy the live research store (+ WAL) for the writer legs (S1).

    Writer legs (`sync_tags --apply`) must not race real store holders: the
    clone gives them a private, same-scale target. Read-only legs keep the
    live store (RO opens coexist with holders; S0 retries cover the rest).
    Returns None when there is no live store — the writer leg then fails on
    its own, same as before the clone existed.
    """
    try:
        from tests._live_store import live_store_path
    except ImportError:  # pragma: no cover - tests/ always importable from REPO_ROOT
        return None
    src = live_store_path()
    if src is None:
        return None
    wal = src.with_name(src.name + ".wal")
    _STORE_CLONE_DIR.mkdir(parents=True, exist_ok=True)
    dst = _STORE_CLONE_DIR / src.name
    shutil.copy2(src, dst)
    if wal.exists():
        shutil.copy2(wal, dst.with_name(dst.name + ".wal"))
    else:
        dst.with_name(dst.name + ".wal").unlink(missing_ok=True)
    return dst


def _drop_store_clone(clone_db: Path | None) -> None:
    if clone_db is not None:
        shutil.rmtree(_STORE_CLONE_DIR, ignore_errors=True)


def _expand_clone(args: list[str], clone_db: Path | None) -> list[str]:
    """Replace the {clone_db} placeholder with the ephemeral store clone."""
    if clone_db is None or not any("{clone_db}" in a for a in args):
        return args
    return [a.replace("{clone_db}", str(clone_db)) for a in args]


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
#   derive_insights          7.2-9.6 s  12.0    serial scan/render wall
#                                                (pool default removed
#                                                2026-11-19: pooled runs
#                                                cost 30.8 s wall vs 7.9 s
#                                                serial — fork+IPC overhead
#                                                dominates the 131-file
#                                                scan; --workers N opts in;
#                                                post-fix serial observed
#                                                9.64 s max, so 12.0 ≈ 1.25×
#                                                headroom, bfs-style)
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
#                                                (operator-approved headroom);
#                                                S6 cutover 2026-11-21: the
#                                                leg reads the canonical
#                                                store over the quack wire —
#                                                measured 13.8-14.0 s on the
#                                                quiet box, budget re-baselined
#                                                to 20.0 (ceiling, rollback to
#                                                sqlite would only get faster)
#   shortest_path_bfs        ~1.2 s      12.0    inner per-route asserts are
#                                                the real gate (1/81 ms);
#                                                +2 s operator boost 09-26;
#                                                2026-11-19: _unreachable_dst
#                                                CTE -> python BFS over one
#                                                e_all_und scan (10.6 s of
#                                                the wall was planner
#                                                pathology)
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
    ("integrity_check", ["helpers/misc/database_integrity_check.py"], 20.0),
    ("verify_notes", ["helpers/validators/verify_notes.py"], 3.0),
    # --apply (2026-09-03): guard unification made the bare run a dry-run
    # report; the leg times the write path it has always timed (and the 2.0s
    # budget was calibrated on).
    ("sync_tags", ["helpers/core/sync_tags.py", "--apply", "--db", "{clone_db}"], 2.0),
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
    # test_coverage_gap_sweep S4 (2026-11-01): legs for the late-October
    # feature wave's hot paths, budgets = measured baseline x2 on the
    # loaded box (raw numbers in the proposal appendix):
    #   graph_mst 0.72s, graph_matching 1.06s, hyper_derive 1.71s,
    #   price_census 0.25s. The MST/matching legs exercise the Hall-fix
    # lanes (graph_lane_unlocks S1); hyper_derive is the collect+summary
    # dry-run (never applies); price_census is the temporal lane's
    # read-side join (read-only over the live parquet + entities).
    (
        "graph_mst",
        ["helpers/graph/l1_mst.py", "mst-backbone", "--format", "summary"],
        2.0,
    ),
    (
        "graph_matching",
        ["helpers/graph/scipy_matching.py", "match-suppliers"],
        3.0,
    ),
    # hyper_derive re-baselined 2026-11-23 (operator ruling: "raise budget
    # to 12s"). The 4.0 entry dates from 2026-11-01 (baseline 1.71 s x2);
    # by 2026-11-23, after the 2026-11-22 duckdb cutover, the dry-run
    # measured 18.34 s on a quiet box. The batched compact_membership /
    # apply_hyperedges rewrite brought it to 4.58 s (identical dry-run
    # output, old vs new).
    (
        "hyper_derive",
        ["helpers/graph/derive_hyperedges.py"],
        12.0,
    ),
    (
        "price_census",
        ["helpers/research/price_join.py", "census"],
        1.0,
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
    # 5.0s since 2026-11-09 (operator call): the 3.0s budget was calibrated on
    # the pre-polyadic corpus. d820ef8fc (2026-11-06, 'polyadic capture: n-ary
    # events') shifted this leg 2.17-2.76s -> 3.23-3.42s deterministically —
    # measured on a quiet box (load 2.2, psi avg10 4.3%), pre-polyadic
    # d820ef8fc~1 = 2.62/2.62/2.76s vs HEAD 3.37/3.42/3.40s, so it is real
    # work, not contention. Attribution: the two new prose arms
    # (_extract_jv_consortium, _extract_acquisition_triangle) add ~0.42s;
    # EntityResolver.__init__ over 24,711 names adds ~0.31s. Raising the
    # budget parks that cost as accepted baseline instead of a standing red —
    # perf_improvs P1 already landed its regex fixes (2026-08-10), so the
    # remaining time is extraction logic, not a harness artefact.
    ("derive_events", ["helpers/graph/derive_events.py"], 5.0),
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
    ("derive_insights", ["helpers/graph/derive_insights.py"], 12.0),
    # ladder_indexing arc (2026-11-11): micro-leg for the entity-resolution
    # ladder. Guards the O(N) full-map scan returning to
    # _qualifier_variants/_fuzzy_suggestions: measured 2026-11-11 with 5k
    # synthetic keys — qv 0.075 ms/call, fz 2.9 ms/call post-indexing vs
    # ~10.3/~69.6 ms pre-indexing. The leg asserts its own per-call ceilings
    # (5 ms / 40 ms); the outer budget only guards the harness.
    ("ladder_indexing", ["tests/bench_ladder_indexing.py"], 15.0),
    # ppr_edge_attribution Q3 harness (graph_kernels_ppr_lane §7, live store):
    # leave-one-type-out over the 8 relational PPR types + economic
    # cross-projection. The leg asserts its own wall ceiling (60s); the
    # outer budget guards the harness itself (10 projection loads + 10
    # batch solves on research.db — measured ~30-40s warm on a quiet box).
    ("ppr_edge_attribution", ["tests/bench_ppr_edge_attribution.py"], 90.0),
    # L1 fold-kernel legs (rust_betweenness_kernel / rust_closeness_kernel,
    # 2026-11-18): both lanes run twice on the live store (numpy vs Rust).
    # The leg asserts its own parity epsilon (betweenness 1e-9 relative,
    # closeness exact — distances are integers) and a 1.5x speedup floor.
    # The proposals' >2x acceptance target was verified once on a quiet box
    # (13.3x / 2.42x, recorded in the archived proposals); the leg's floor
    # sits below it so a contended box does not fail a haltable timing gate.
    # Budget 240 s = both lanes (numpy 51.8 s + 7.4 s dominate) plus the
    # ~1.7x headroom the other live legs carry.
    ("l1_kernels", ["tests/bench_l1_kernels.py"], 240.0),
    # quick_wins S1 guard (proposal #504, archived): stdlib json.dumps vs
    # jsonio.dumps (orjson) ratio floor 2.5x (measured 3.9-9.1x across the
    # five shapes + ledger row loop). A regression re-introducing stdlib
    # dumps in a mass lane collapses the ratio toward 1.0 and trips the floor.
    ("quick_wins_json_dumps", ["tests/bench_quick_wins.py"], 8.0),
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
    # RATIOS, NOT absolute q/s. The production script-search shape (717 x 512)
    # asserts rust >= 1.3x numpy and >= 5x the pure loop. S4 adds the shapes
    # where Rust currently loses — live-note (19,356 x 512) and scale
    # (250k/500k/1M x 384) — with an honest rust/numpy >= 0.14x floor. The
    # gate prints one per-shape table and a score-based top-1 parity check
    # (ids are not a stable contract where similarities tie within float32 ulps).
    # Absolute numbers were deliberately avoided: they encode this CPU and
    # would punish a faster box, and this 4-core i5-6500 runs `powersave`
    # (3300-3488 MHz observed), so absolute budgets are flaky here.
    # The floor is not "below the loss regime": it is set by the feasible-window
    # rule (must beat half the widest healthy ratio, stay under the narrowest).
    # The old 0.40x floor failed healthy builds 5/5 because Rayon-4 medians span
    # 0.42-0.77x. Pinning Rayon to one worker narrows the band to 0.17-0.24x, so
    # 0.14x passes a healthy build 3/3 and still fails a 2x/3x slowdown.
    # SKIPs with exit 0 when the module is not built; qa's
    # SCRIPT_KNN_STRICT=1 remains the hard pin for a vanished .so.
    # Budget 25.0s since 2026-11-19 (operator call): S4 grew the leg from
    # 2.4s to ~11.3s (production + live-note + 250k/1M scale shapes; the
    # redundant 500k middle point was dropped), and the house rule is
    # measured x2 — 11.3s x2 = 22.6s, rounded to 25.0s. The inner ratio
    # bars remain the real gate; this outer budget only catches super-linear
    # decay of the harness itself.
    ("cosine_knn_ratios", ["Rust/bench/cosine_gate.py"], 25.0),
    # test_coverage_gap_sweep S6 (2026-11-24): legs for the DuckDB
    # cutover write paths + the new hyper-trails lane + semantic-pack
    # reads. Budgets = quiet-box baseline x2 (house rule), measured
    # 2026-11-24 without concurrent workload:
    #   db_engine_10k  executemany 71.87/69.21/71.10s (10k row inserts
    #                   through the house _DuckDBConn proxy — the
    #                   pre-set-based per-row cost class); bulk Arrow
    #                   rewrite 0.27/0.28/0.28s. The leg prints both
    #                   shapes; the outer budget guards the slow one
    #                   (the rewrite is a sub-second smoke).
    #   hyper_trails   0.94/0.87/0.94s (--sources 200, build_bipartite
    #                   from the live research store each invocation).
    #   context_pack   1.21/1.26/1.17s (INFY.NS read over the live
    #                   research store).
    (
        "db_engine_10k",
        [
            "helpers/bench/db_engine_write_bench.py",
            "--db",
            "{scratch}/db_engine_bench.duckdb",
            "--rows",
            "10000",
        ],
        150.0,
    ),
    (
        "db_engine_2k",
        [
            "helpers/bench/db_engine_write_bench.py",
            "--db",
            "{scratch}/db_engine_bench_2k.duckdb",
            "--rows",
            "2000",
        ],
        25.0,
    ),
    (
        "hyper_trails",
        ["helpers/graph/hyper_trails.py", "--sources", "200", "--limit", "40"],
        2.0,
    ),
    ("context_pack", ["helpers/graph/context_pack.py", "INFY.NS"], 2.6),
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


def run_one(
    label: str, args: list[str], budget: float, clone_db: Path | None = None
) -> tuple[float, str, bool, str, bool]:
    """Run a single benchmark, return (elapsed_seconds, status, passed, stderr_tail, skipped).

    The tail (last ~2 KB of stderr) rides the result so failures surface in
    the report's ``## <label> (FAILED)`` sections — gate_query's err_head —
    without the old failure-time subprocess re-run (which doubled the cost
    of every red leg and printed to a console nobody archives). For a
    skipped leg the tail leads with the leg's own ``SKIP:`` line (stdout).
    """
    argv = [sys.executable, *_expand_clone(_expand_scratch(args), clone_db)]
    t0 = time.perf_counter()
    # communicate() (not a stdout loop) because a leg that writes more than
    # a pipe buffer to stderr while we block on stdout would deadlock; the
    # in-memory profile is identical to the old capture_output=True.
    proc = subprocess.Popen(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=REPO_ROOT,
    )
    out_raw, err_raw = proc.communicate()
    dt = time.perf_counter() - t0
    stdout_text = out_raw.decode(errors="replace")
    stderr_text = err_raw.decode(errors="replace")
    # Writer legs (sync_tags --apply) can lose a transient race with an
    # unrelated store holder (a live gate leg, an agent peek). A lock
    # conflict is retryable — the holder releases; a real failure is not.
    # One retry after a short wait, re-measured from zero (the first
    # attempt's timing is meaningless once it never got the store).
    if proc.returncode != 0 and "Conflicting lock is held" in stderr_text:
        print(f"  {label}: store lock conflict — retrying once after 15s", file=sys.stderr)
        time.sleep(15)
        t0 = time.perf_counter()
        proc = subprocess.Popen(  # noqa: S603  # as above
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=REPO_ROOT,
        )
        out_raw, err_raw = proc.communicate()
        dt = time.perf_counter() - t0
        stdout_text = out_raw.decode(errors="replace")
        stderr_text = err_raw.decode(errors="replace")
    if _CURRENT_LOG_DIR is not None:
        gate_retain.write_step_log(
            _CURRENT_LOG_DIR / f"perf.{gate_retain.safe_label(label)}.log",
            f"{label}: {' '.join(argv)}",
            stdout_text + stderr_text,
        )
    rc = proc.returncode
    # A skip is a DECLARATION (exit 0 + a `SKIP:` line on stdout), not an
    # absence of failure: before 2026-11-24 such a leg scored as a fast OK
    # (l1_kernels "passed" at 0.19s against a 240s budget, six runs, while
    # its store was gone).
    skip_line = _SKIP_RE.search(stdout_text) if rc == 0 else None
    skipped = skip_line is not None
    ok = rc == 0 and not skipped and dt < budget
    if skipped:
        status = "SKIPPED"
    elif rc != 0:
        status = "FAIL(rc)"
    elif dt >= budget:
        status = "OVER_BUDGET"
    else:
        status = "OK"
    tail = stderr_text.strip()[-_TAIL_CAP:]
    if skipped:
        tail = "\n".join(part for part in (skip_line.group(0), tail) if part)
    return dt, status, ok, tail, skipped


_MEDIAN_BUDGET_MAX = 10.0


def _run_leg_median(
    label: str,
    args: list[str],
    budget: float,
    *,
    clone_db: Path | None = None,
    repeats: int = 3,
) -> tuple[float, str, bool, str, bool]:
    """Run one leg ``repeats`` times and judge the MEDIAN wall (S3).

    Sub-10s legs are dominated by first-touch costs (cold clone WAL replay,
    cold caches) and single-sample load spikes; the median of N reps is the
    honest wall. Semantics: a SKIP rep returns immediately (skip dominates —
    repeating a skip is waste); rc-clean reps form the median set, status is
    recomputed from the median wall; the tail rides the rep closest to the
    median; only when every rep fails rc does the last failure surface.
    Per-rep logs land as ``perf.<label>#repK.log`` (rep 1 keeps the bare
    name), so the runs stay individually inspectable.
    """
    goods: list[tuple[float, str, bool, str, bool]] = []
    last: tuple[float, str, bool, str, bool] | None = None
    for i in range(repeats):
        rep_label = label if i == 0 else f"{label}#rep{i + 1}"
        last = run_one(rep_label, args, budget, clone_db=clone_db)
        dt, status, ok, tail, skipped = last
        if skipped:
            return last
        if ok or status == "OVER_BUDGET":  # rc-clean: timing belongs in the median set
            goods.append(last)
    if not goods:
        assert last is not None  # repeats >= 1 guarantees at least one rep ran
        return last
    walls = sorted(g[0] for g in goods)
    mid = statistics.median(walls)
    rep = min(goods, key=lambda g: abs(g[0] - mid))
    ok = mid < budget
    status = "OK" if ok else "OVER_BUDGET"
    return mid, status, ok, rep[3], False


def _run_all_benchmarks(
    force: bool = False, clone_db: Path | None = None, repeats: int = 3
) -> tuple[list[tuple[str, float, str, bool, float, str, bool]], str | None, str | None]:
    """Run every BENCHMARKS leg; returns (results, contended_from, contend_why).

    Extracted from main() (C901): the mid-run contention probe + the
    failure-stderr surface live here, the table/report tail stays in main.
    ``force=True`` (``--force``) skips the mid-run cutoff: rows still get
    pressure attribution, the run just never declares itself untrusted.
    ``clone_db`` (S1) replaces the ``{clone_db}`` placeholder in writer-leg
    argv so they measure against the ephemeral clone, not the live store.
    ``repeats`` (S3, ``--repeats``) re-runs sub-10s-budget legs and judges
    the median wall (1 = single run).
    """
    results: list[tuple[str, float, str, bool, float, str, bool]] = []
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
        if repeats > 1 and budget < _MEDIAN_BUDGET_MAX:
            dt, status, ok, tail, skipped = _run_leg_median(
                label, args, budget, clone_db=clone_db, repeats=repeats
            )
        else:
            dt, status, ok, tail, skipped = run_one(label, args, budget, clone_db=clone_db)
        if contended_from is not None:
            status = "CONTENDED"
            ok = False
            skipped = False  # contention attribution wins: the row reads untrusted, not skipped
        elif not ok and not skipped:
            post_load = _load_pressure_reason()
            load_frag = pre_load or post_load
            if load_frag:
                # attribution, not excuse: the row still FAILs; the tag says
                # the box was busy while it did (run 1787 lesson — a loaded
                # box painted 15 legs red and read as a regression wall).
                status = f"{status} [{load_frag}]"
        results.append((label, dt, status, ok, budget, tail, skipped))
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
    parser.add_argument(
        "--wait-minutes",
        type=float,
        default=5.0,
        help="when the box is contended at start, poll for a quiet window up "
        "to this long before refusing (0 = instant refusal, the old behavior)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="repetitions per sub-10s-budget leg; the median wall is judged "
        "against the budget (1 = single run, the old behavior)",
    )
    ns = parser.parse_args(argv)

    # ── run ──
    global _CURRENT_LOG_DIR, _RUN_ID
    # Exclusive run lock: two perf runs racing the same stores produce lock
    # conflicts in the writable legs and lie in every leg's timings (2026-11-25:
    # a background waiter run and a manual `make -j2 perf` overlapped). One
    # run at a time; a second invocation refuses cleanly (exit 3, nothing
    # appended) instead of corrupting both.
    _lock_path = REPO_ROOT / "outputs" / ".perf_run.lock"
    _lock_path.parent.mkdir(parents=True, exist_ok=True)
    _lock_fd = os.open(_lock_path, os.O_CREAT | os.O_RDWR)  # noqa: S603  # literal flags; fixed path
    try:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = _lock_path.read_text(encoding="utf-8", errors="replace").strip() or "unknown"
            print(
                f"perf refused — another perf run is active ({holder}); "
                "perf runs are exclusive (outputs/.perf_run.lock)",
                file=sys.stderr,
            )
            return 3  # the outer finally closes the fd
        os.ftruncate(_lock_fd, 0)
        os.write(_lock_fd, f"pid {os.getpid()} started {datetime.now():%Y-%m-%d %H:%M:%S}".encode())
        # S2: quiet-window wait. The lock is ours; the box may still be busy with
        # unrelated desktop load. One start probe always runs (the old refusal);
        # --wait-minutes > 0 extends it into a 30s poll loop before refusing.
        if not ns.force:
            _deadline = time.monotonic() + max(ns.wait_minutes, 0.0) * 60.0
            _reason = _contention_reason()
            if _reason is not None and ns.wait_minutes > 0:
                print(
                    f"perf: box loaded ({_reason}) — waiting up to {ns.wait_minutes:g}m "
                    "for a quiet window (poll 30s)",
                    file=sys.stderr,
                )
            while _reason is not None and time.monotonic() < _deadline:
                time.sleep(min(30.0, max(0.0, _deadline - time.monotonic())))
                _reason = _contention_reason()
            if _reason is not None:
                waited = f" after {ns.wait_minutes:g}m wait" if ns.wait_minutes > 0 else ""
                print(
                    f"perf refused — box still contended{waited} ({_reason})",
                    file=sys.stderr,
                )
                return 3
        # S1: one ephemeral clone of the live store per run, taken BEFORE the
        # first leg so no leg timing includes the copy; writer legs point at it
        # via the {clone_db} placeholder. Dropped in the finally below.
        clone_db: Path | None = None
        try:
            clone_db = _prepare_store_clone()
            return _main_locked(argv, ns, clone_db=clone_db)
        finally:
            _drop_store_clone(clone_db)
    finally:
        # Every exit path after the open must release the lock — the refusal
        # returns above included (a held flock would poison the rest of the
        # process, e.g. every later test in the same pytest session).
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        os.close(_lock_fd)


def _main_locked(
    argv: list[str] | None, ns: argparse.Namespace, clone_db: Path | None = None
) -> int:
    global _CURRENT_LOG_DIR, _RUN_ID
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _RUN_ID = gate_retain.run_id_now()
    # Created BEFORE the first leg so a crashed or refused-mid-run lane still
    # keeps the legs it completed (the logs are written as each leg ends).
    _CURRENT_LOG_DIR = gate_retain.run_dir(ARTIFACTS_ROOT, "perf", _RUN_ID)
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
    results, contended_from, contend_why = _run_all_benchmarks(
        force=ns.force, clone_db=clone_db, repeats=ns.repeats
    )

    # ── table ──
    lines: list[str] = []
    lines.append("")
    lines.append("Benchmark                              Time (s)   Budget   Status")
    lines.append("-" * 70)
    all_ok = True
    for label, dt, status, ok, budget, _tail, leg_skipped in results:
        flag = "✓" if ok else "✗"
        lines.append(f"  {label:.<36s} {dt:7.2f}  {budget:7.1f}s   {flag} {status}")
        if not ok and not leg_skipped:
            all_ok = False
    lines.append("-" * 70)

    passed = sum(1 for _l, _dt, _s, ok, _b, _t, _sk in results if ok)
    skipped = sum(1 for _l, _dt, _s, _ok, _b, _t, leg_skipped in results if leg_skipped)
    total = len(results)
    footer = f"  {passed}/{total} passed"
    if skipped:
        footer += f", {skipped} skipped"
    lines.append(footer)

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
    artifact_dir = _retain_run_artifacts(results, started, elapsed)
    write_report(results, started, elapsed, status_note=status_note, artifact_dir=artifact_dir)

    if contended_from is not None:
        _cleanup_scratch()
        return 3
    _cleanup_scratch()
    return 0 if all_ok else 1


def _retain_run_artifacts(
    results: list[tuple[str, float, str, bool, float, str, bool]],
    started: str,
    elapsed: float,
) -> str | None:
    """Finish the run's artifact dir: perf.json + prune, return the
    `**Artifacts:**` value (relative to the outputs root).

    The per-leg logs are already on disk (run_one wrote them while the leg
    ran, so a crashed run still keeps the legs it completed). This adds the
    machine-readable sidecar gate_query's perf-json.v1 adapter reads and
    bounds the directory count.
    """
    log_dir = _CURRENT_LOG_DIR
    if log_dir is None:
        return None
    payload = {
        "schema": "perf-json.v1",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "started": started,
        "elapsed_s": round(elapsed, 3),
        "benchmarks": [
            {
                "name": label,
                "seconds": round(dt, 3),
                "budget_s": budget,
                # gate_query's perf-json.v1 adapter accepts
                # pass/fail/warn/info and COERCES anything else to pass —
                # so a skip must ride "warn", never a bare "skip".
                "status": ("warn" if skipped else "pass" if ok else "fail"),
                "raw_status": status,
            }
            for label, dt, status, ok, budget, _tail, skipped in results
        ],
    }
    try:
        gate_retain.write_json_artifact(log_dir / "perf.json", payload)
    except OSError:
        return None
    gate_retain.prune_old_runs(ARTIFACTS_ROOT, "perf", _ARTIFACT_KEEP_RUNS)
    return gate_retain.artifact_meta_value(OUTPUTS_ROOT, log_dir)


def _md_status(status: str, ok: bool) -> str:
    """The md Status cell for one leg.

    Every failure row keeps the literal ``FAIL`` — gate_query's failure
    surfaces (_pick_run/_run_digest) key on that substring, and a
    contended run must stay VISIBLE there, never silently green. The
    parenthesised attribution is new (2026-10-24): budget vs rc vs
    contended vs load-coincident, so a loaded box's red wall reads as
    load, not regression. ``[frag]`` is appended by _run_all_benchmarks
    when a leg failed under measured external pressure. A skipped leg
    renders ``⏸ SKIPPED`` — deliberately no ``OK`` (the TUI pass-count
    keys on it) and no ``FAIL`` (failure surfaces must stay truthful);
    gate_query's md parser maps any Status containing ``SKIP`` to info.
    """
    if status == "SKIPPED":
        return "⏸ SKIPPED"
    if ok:
        return "✓ OK"
    if status == "CONTENDED":
        return "✗ FAIL(contended)"
    kind = "rc" if status.startswith("FAIL(rc)") else "budget"
    m = re.search(r"\[(.+)\]$", status)
    return f"✗ FAIL({kind}, {m.group(1)})" if m else f"✗ FAIL({kind})"


def write_report(
    results: list[tuple[str, float, str, bool, float, str, bool]],
    started: str,
    elapsed: float,
    status_note: str | None = None,
    artifact_dir: str | None = None,
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
    ended = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Explicit unpacks, NOT `*_, ok, _` star patterns: on the 7-tuple those
    # bind ok to `budget` (a truthy float) — the exact run-4694-class bug
    # the footer pin below guards.
    passed = sum(1 for _l, _dt, _s, ok, _b, _t, _sk in results if ok)
    skipped = sum(1 for _l, _dt, _s, _ok, _b, _t, leg_skipped in results if leg_skipped)
    total = len(results)
    artifacts_str = f"  ·  **Artifacts:** {artifact_dir}" if artifact_dir else ""
    md_lines: list[str] = []
    md_lines.append("# make perf — benchmark report")
    md_lines.append("")
    md_lines.append(
        f"**Generated:** {ts}  ·  **Started:** {started}  ·  **Ended:** {ended}  ·  "
        f"**Elapsed:** {elapsed:.1f}s  ·  **Python:** {sys.version.split()[0]}{artifacts_str}"
    )
    if status_note:
        md_lines.append("")
        md_lines.append(f"**Status:** {status_note}")
    md_lines.append(f"**Exit:** {0 if passed + skipped == total else 1}")
    md_lines.append("")
    md_lines.append("| Benchmark | Time (s) | Budget | Status |")
    md_lines.append("|---|---|---|---|")
    for label, dt, status, ok, budget, _tail, _leg_skipped in results:
        flag = _md_status(status, ok)
        md_lines.append(f"| {label} | {dt:.2f} | {budget:.1f}s | {flag} |")
    footer = f"| **{passed}/{total} passed** | | | |"
    if skipped:
        footer = f"| **{passed}/{total} passed, {skipped} skipped** | | | |"
    md_lines.append(footer)
    for label, _dt, status, ok, _budget, tail, leg_skipped in results:
        if ok or leg_skipped:  # a skip gets its own (SKIPPED) section below — never a FAILED one
            continue
        md_lines.append("")
        md_lines.append(f"## {label} (FAILED)")
        md_lines.append("")
        md_lines.append(f"status: {status}")
        md_lines.append(tail if tail else "(no stderr output)")
    for label, _dt, status, _ok, _budget, tail, leg_skipped in results:
        if not leg_skipped:
            continue
        md_lines.append("")
        md_lines.append(f"## {label} (SKIPPED)")
        md_lines.append("")
        md_lines.append(f"status: {status}")
        md_lines.append(tail if tail else "(no skip detail)")
    md_lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "a") as f:
        f.write("\n".join(md_lines))


if __name__ == "__main__":
    raise SystemExit(main())
