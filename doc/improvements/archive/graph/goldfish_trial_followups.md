---
title: "Goldfish-trial follow-ups: wall-scale bench rows and review-assertion teeth"
status: "executed"
filed: "2026-11-03"
executed: "2026-11-03"
completed_md: "475"
area: "helpers/bench/hyper_scale_bench.py, tests/test_hyper_trails.py, tests/test_bipartite_communities.py"
---

# Goldfish-trial follow-ups: wall-scale bench rows and review-assertion teeth

**Date:** 2026-11-03 · **Status:** LANDED + ARCHIVED 2026-11-03 (completed.md entry 475) ·

Provenance: the 2026-11-03 retrospective goldfish trial on
doc/improvements/archive/graph/scaling_next_walls.md (raw reports:
`outputs/reviews/review_goldfish_scaling_next_walls_zoomout.md` and
`..._impl_readiness.md`; triage "all defaults" adopted the 8 MAJORs in the
archived record's dated addendum). This proposal carries the items the trial
recorded but explicitly did NOT adopt at resolve, so they survive as live work
instead of being re-litigated by the next arc. Three items:

## 1. Wall-scale bench rows (trial finding 5, "better" branch)

The S3 scaling leg benches at multiples of the harness DEFAULT cap
(`hyper_scale_bench.py` `--cap` 500 => 1k/2.5k/5k nodes), not the wall cap
6,000; the wall-scale rows (12k/30k/60k) are unmeasured, and the archived
record says so in place.

Task: one rerun, `hyper_scale_bench --cap 6000`, recording the real rows;
update the archived record's S3 sentence with measured numbers (replacing
"wall-scale rows unmeasured"); confirm cap-500 numbers unchanged.

Acceptance: cap-6000 rows recorded in the bench output and cited from the
archived record; cap-500 leg reproduces prior numbers (no regression).

DONE 2026-11-03: wall rows 12k/30k/60k = iFUB 31.7s/249.7s/1248.7s vs
sampled 10.0s/24.4s/56.6s (diameters 14/17/18, all-pairs infeasible ×3,
gap 3.2×/10.2×/22.1×); cap-500 reproduced (iFUB 0.378/1.782/5.578s,
sampled 0.055/0.161/0.294s, `sampled_chunk` null). The rerun required
two memory guards in `lane_chains_exact` — sampled-leg 128 MB
dense-block chunking and a sparse graph generator above n=5000 — after
the first unguarded attempt thrashed the machine (dense (len(roots), n)
block ≈2.9 GB at the 60k rung; generator intermediate ≈28.8 GB).
Cited: doc/improvements/archive/graph/scaling_next_walls.md (S3
paragraph + Wall 3 addendum bullet).

## 2. Mirror-data assertion (Wall-5 fallback test tooth)

The duckdb-lock mirror fallback (degraded read path) is asserted only
vacuously today — a mirror serving stale or empty data still passes tests.

Task: a test that FAILS when the mirror path returns stale/empty data
(mutation-style: corrupt the mirror fixture, expect the test to go red).

Acceptance: red under a deliberately-stale mirror fixture; green on the
healthy path.

DONE 2026-11-03: tests/test_graph_scale_watch.py
`test_degraded_mirror_data_flows_not_just_banner` (stale mirror counts
7/3/5 asserted in the banner text; mutation-proven — empty fixture turns
the test red) and `test_degraded_empty_mirror_reports_zeroes_not_banner_only`.

## 3. Dispatch-spawned assertion (trail-pool test tooth)

`trail_source_stats_parallel` (hyper_trails.py) now caps workers at core
count and prints one stderr line on serial fallback, but no test distinguishes
pool-spawned execution from silent serial degradation.

Task: assertions that (a) the pool path spawns on a normal multi-core box and
(b) the fallback prints the stderr line (capsys) when pool start raises.

Acceptance: both behaviors observable in tests; the silent-degradation
regression the lens flagged becomes impossible to reintroduce unnoticed.

DONE 2026-11-03: tests/test_hyper_trails.py
`test_pool_spawns_with_capped_workers` (SpyPool asserts the `processes`
kwarg) and `test_pool_start_failure_prints_stderr_and_falls_back`
(capsys asserts the two stderr lines; results equal serial).

## Out of scope (recorded, not repo work)

Per-dispatch model assignment for goldfish legs is a DeepSeek Harness
limitation (subagent surface takes `{description, prompt, run_in_background}`
only) — an upstream request, not a change this repo can make. The
design-critic lens (procedure allows 2-3; trial ran 2) stays optional, not a
work item.

## Eval gate

No query-visible semantics change (rosters, crosswalks, hierarchies,
extractor rules): bench rows and test assertions only — no
`ontology_eval_gate` bullet required per the proposals/README contract.
