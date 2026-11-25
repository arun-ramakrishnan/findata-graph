---
title: "Rust SIMD cosine kernel, hop-histogram lanes (all-universe + contract pair), PPR attribution Python pass"
status: executed
filed: "2026-11-25"
executed: "2026-11-25"
completed_md: "572"
area: "Rust/cosine_knn + Rust/graph_kernels + helpers/graph/scipy_bridge.py + helpers/graph/ppr_edge_attribution.py"
---

# Rust SIMD cosine kernel, hop-histogram lanes (all-universe + contract pair), PPR attribution Python pass

**Date:** 2026-11-25 · **Status:** PROPOSED ·
**Area:** `Rust/cosine_knn` (script-search cosine), `Rust/graph_kernels`
(BFS lanes), `helpers/graph/scipy_bridge.py` (all-universe stamp pass,
contract closeness + harmonic), `helpers/graph/ppr_edge_attribution.py`
(Python pass, no Rust)

## 1. Motivation

Two Rust lanes have the Rust/Python boundary in the wrong place, and one
Rust port was already measured dead and should stay that way:

- **All-universe stamp pass.** `full_universe_stats`
  (`helpers/graph/scipy_bridge.py:400`) is scipy's heap `dijkstra` from
  every node, sharded over the fork pool. Measured today: 44.1 / 45.1 s at
  `jobs=3`, the bulk of a ~63 s `stamp-centrality` build + stamp
  (`doc/improvements/pending.md:49-60`). The Rust BFS is not the problem —
  it does all 22,840 sources in ~3.8 s. What costs another 10–40 s,
  depending on how the distance rows are reduced, is everything Python
  does with them afterwards.
- **Script-search cosine.** `cosine_knn` is the production backend
  (`helpers/maintenance/rebuild_script_search.py` `_cosine_rank_rows`;
  `SCRIPT_KNN=auto`, the default, prefers Rust). Its per-row kernel is
  scalar and loses to single-thread BLAS at large shapes
  (`Rust/bench/cosine_gate.py:1-116` records 0.17–0.24). The Trial B SIMD
  lane added today is 3.50–5.22× faster than that scalar lane but is not
  wired into `knn`.
- **`similar_notes` Rust port.** Deferred as #450 on a 1,000× decode cost.
  It is carried here only so the re-open test is written down next to the
  rest.
- **Contract closeness + harmonic.** The SCIPY-routed pair is computed
  live, not stamped (`helpers/graph/algorithms.py:765-766`), at ~9.3 s a
  call through `scipy_bridge.compute` (`:552`): 73% is scipy's heap
  `dijkstra`, the rest a 316 MB float64 matrix and its numpy reduction.
  The existing kernel does all 1,731 contract sources in 0.40 s.
- **PPR edge attribution.** The gate leg (wall 6.4–9.1 s, budget 90 s)
  spends 56% rebuilding projections and ~25% in pure-Python diff
  metrics; the PPR kernel is 15%. Not Rust-shaped — a Python slice,
  carried here so the arc's perf work stays in one place.

Trigger: 2026-11-25 session (SIMD trial go; "write proposal and add all
these as slices in it"; extended the same day with the contract pair and
the PPR Python pass).

## 2. Evidence (measured 2026-11-25, this box)

Box: Intel Core i5-6500, 4 cores / 4 threads, load average 1.3–2.8 during
the runs (not idle — treat wall times as ±10%). Projection: 22,840 nodes /
69,913 edges. Raw log in the Appendix.

| # | Configuration | Result | Verdict |
|---|---|---|---|
| E1 | `full_universe_stats` as shipped (scipy, `jobs=3`), 2 runs | 44.1 s / 45.1 s | baseline |
| E2 | `core_bfs_sources_batch` distances, all 22,840 sources in 90 calls of 256, 3 runs | 3.80 / 3.89 / 3.71 s | the BFS is solved |
| E3 | E2 + the shipped numpy reduction over a float64 matrix, one process, 3 runs | 35.3 / 41.1 / 44.5 s (1.1×) | float64 round trip is dead |
| E4 | kernel distance rows vs scipy, 3 blocks × 256 rows | exactly equal (integer compare) | kernel matches the oracle |
| E5 | E3 output vs E1 | closeness, harmonic, structure exactly equal | reduction is exact |
| E6 | Trial B `cosine_similarity_simd` vs scalar, `Rust/cosine_knn/src/bin/trial.rs`, 9 shapes | 3.50–5.22× | kernel-level only |
| E7 | `cosine_knn.knn` vs numpy, 1 thread each, 19,356×512 / 250k×384 / 1M×384 | 0.170–0.218 / 0.187–0.201 / 0.185–0.243 | gate's recorded window, not re-run today |
| E8 | E2 + a tight int32-code numpy histogram, no float64 round trip, one process, 12 blocks of 2,048 sources, 2 runs | 13.2 s / 15.1 s (3.2×); of which Python histogram 8.4–9.1 s | best no-new-Rust lane |
| E9 | `_run_scipy_lane_pair` / `scipy_bridge.compute(A, src, jobs=1)`, the live contract pair, 2 runs | 9.35 / 9.33 s; `dijkstra` 6.84 s (73%), float64 matrix + `fused_derive` ≈ 2.2 s (24%) | contract-pair baseline |
| E10 | E9 through the existing kernel, all 1,731 contract sources, 3 runs | kernel 0.37–0.57 s (median 0.40 s); + float64 + shipped reduction = 3.39 s median, closeness and harmonic exactly equal | 2.8× with no new Rust |
| E11 | E10 with an int32 histogram instead of the float64 matrix | histogram loop 0.16 s → ≈ 0.6 s total; closeness exactly equal; harmonic max rel diff 4.1e-16 (abs 1.8e-12, 916 of 1,731 rows) | ≈ 15×; needs the harmonic pin |
| E12 | `ppr_edge_attribution.run(db, cohort_size=8, top_k=10)`, 2 runs + cProfile | 5.52 / 5.80 s: 10 × `load_centrality_projection` 3.16 s (56%); solves 0.99 s (17%, kernel 0.84 s); `_diff_neighborhoods` ≈ 1.3 s, 674,481 `sorted()` calls | not kernel-shaped |
| E13 | `l1_closeness.compute(db, jobs=1)` and `l1_betweenness.compute(db, jobs=1)` | L1c 3.45 s median: scoring loops ~70%, int32→int64 copy ~20%, kernel 0.15 s (4%). L1b 4.36 s median: `brandes_fold_sources` 88% of the profiled wall | L1c is the S2 pattern off the routing; L1b has nothing to move |
| E14 | THE LANDED S2 LANE on the live projection, in-process: `full_universe_stats` 3 runs auto vs 1 run `SB_RUST=off` | kernel 3.32 / 3.32 / 3.35 s vs scipy 32.91 s (`jobs=4` both) = **9.91×**; closeness exact, structure exact, harmonic max rel diff 1.7e-16 | S2 acceptance: ≥5× ✓, ≥2× the E8 hybrid ✓, parity pin ✓ |
| E15 | THE LANDED S4 ROUTING on the live contract pair: `compute(A, src, jobs=1)` 3 runs auto vs 1 run `SB_RUST=off`, mirroring `_run_scipy_lane_pair` | scipy lane 7.54 s wall (load 0.28 s + compute 7.26 s) vs kernel lane 0.56 / 0.56 / 0.57 s = **13.4×** (compute-only 24×); closeness exactly equal; harmonic max rel diff 4.03e-16 (abs 1.82e-12, same 916 rows as E11) | S4 acceptance: ≥5× ✓, parity pin ✓ |
| E16 | THE LANDED S5 PASS on the live store: `ppr_edge_attribution.run(db, cohort_size=8, top_k=10)` old (git HEAD module) vs new, 3 runs; projection parity old-vs-new per subset | family (one fetch → 10 projections) 0.98 s vs 10 × `load_centrality_projection` 3.39 s; run() 6.09 s → 2.30 / 2.33 / 2.53 s = **2.6×**; all 10 projections EXACT (names + indptr/indices/data); report JSON EXACT | S5 acceptance: tests green unmodified ✓, report parity EXACT ✓, bench leg 2.31 s (was 6.4–9.1) ✓ |
| E17 | THE LANDED S1 KERNEL — `Rust/bench/cosine_gate.py` on the pre-S1 vs post-S1 build, 3 fresh runs each (Rayon pinned to 1, median-of-5 rounds) | production 717×512: 0.826 → 0.442 ms median = **1.87×** vs the scalar loop, ratio 2.8× → 5.1–5.3× numpy, rust/pure 48× → 90×; live-note 12.03 → 2.43 ms = **4.95×**; 250k 116.6 → 23.7 ms = **4.92×**; 1M 468.1 → 96.1 ms = **4.87×**; scale ratios 0.17–0.20 → **0.84–0.96** (kernel at parity with BLAS); no shape regressed | S1 acceptance: ≥1.5× scalar ✓, no gate-shape regression ✓, floor re-pinned 0.14 → 0.70 by the window rule ✓ |

What E1–E5 and E8 mean: the distance BFS costs 3.7–3.9 s of the 44 s.
How the rows are reduced decides the rest. The shipped reduction over a
float64 matrix costs 35–45 s (E3, 1.1×). A tight int32-code histogram
with no float64 round trip costs 13.2–15.1 s (E8, 3.2×), and 8.4–9.1 s
of that is a Python loop over 22,840 rows. The cost scales with the
number of distance entries Python touches — 521M here. Pure Rust removes
that loop and the 2 GB int32 handoff, so it should land near the BFS
time: the remaining ~3×, plus one call instead of a block loop. Both
hybrids are measured; do not re-audit them.

What E6–E7 mean: Trial-B-grade SIMD applied to today's kernel puts it at
0.17–0.24 × 3.5–5.2 ≈ 0.6–1.25 of BLAS. Parity is the realistic ceiling
for that kernel; a clear win over BLAS is not promised.

What E9–E11 mean: the live contract pair is the same disease at serving
scale — one kernel call replaces 73% of the wall, and the reduction
choice decides the rest (3.4 s through the float64 matrix, ≈ 0.6 s
through an int32 histogram). Closeness is exact on every route; harmonic
needs the same 1e-12 pin as the all-universe lane.

What E12–E13 mean: PPR attribution is projection rebuilds plus Python
sorting around a 15% kernel — numpy work, not crate work. L1c is the S2
pattern but sits off the routing (bench and parity harness only), and
L1b is already 88% kernel. Neither is a Rust target; S5 below takes the
PPR fix, and the L1c / structure questions are parked as S6.

## S0 — groundwork (LANDED 2026-11-25, recorded here for the arc trail)

- `Rust/cosine_knn/src/lib.rs`: `dot_simd` (x86_64 f32x4, SSE2 baseline,
  scalar tail, non-x86 fallback) and `cosine_similarity_simd` (SIMD dot +
  scalar norms, `0.0` on a zero norm, same contract as the scalar lane).
  Exported as `cosine_knn.cosine_similarity_simd_py`; parity test
  `simd_matches_scalar_lane` (n ∈ {10, 11, 64, 384, 768}, 1e-6).
  `cargo test -p cosine_knn --lib`: 8 pass. `make rust-install` done.
  Already refreshed into the `rust_arcs` patch by the operator.
- The exported `cosine_similarity_simd_py` has **no caller** in the repo;
  `topk_single` / `local_topk` still call the scalar `cosine_similarity`.

## S1 — fused SIMD per-row kernel inside `cosine_knn` (LANDED 2026-11-25)

**Why.** `doc/improvements/pending.md:837-838`: "The fix direction is the
per-row kernel, not more rayon." The kernel that `knn` runs per row
(`Rust/cosine_knn/src/lib.rs:37`) recomputes both norms and is scalar.

**What.**

- One fused per-row kernel: query norm hoisted out of the row loop, row
  norm and dot in SIMD, ≥ 4 independent accumulators (Trial B's
  `dot_simd` is one dependency chain), FMA / AVX2 when
  `is_x86_feature_detected!` says so, SSE2 otherwise, scalar elsewhere.
- Wire it into `topk_single` / `local_topk` (`lib.rs:125`, `lib.rs:155`).
  The scalar `cosine_similarity` stays as the parity oracle and bench
  baseline. `cosine_similarity_simd` takes the fused body, so S0's export
  stops being a second, weaker SIMD path.
- No new environment knob. Rollback = revert the wiring, or
  `SCRIPT_KNN=numpy` for the consumer.
- divan bench: scalar vs fused at the gate shapes (717×512, 250k×384,
  1M×384), 1 thread and default rayon.
- A/B = `Rust/bench/cosine_gate.py` on the pre-S1 build vs the post-S1
  build, ≥ 3 runs each on an idle box, medians. Re-pin
  `MIN_SCALE_VS_NUMPY` by the gate's own window rule if the floor moves.

**Acceptance.**

- `cargo test -p cosine_knn` and `tests/test_cosine_knn_backends.py` pass
  unmodified. Those fixtures pin EXACT row order (`test_backend_parity` on
  a seeded 60×16, `test_rust_module_importable_and_parity_with_numpy` on
  200×32, k=10). A last-ulp flip on a near-tie is a finding to report, not
  a tolerance to loosen.
- Zero-norm rows still score `0.0`.
- Adopt only if the fused kernel is ≥ 1.5× the scalar kernel at the
  production shape AND no gate shape regresses. Otherwise revert the
  wiring and record the table as measured.

**Non-goals.** Pre-normalizing or caching the matrix across calls (depends
on #450's re-open condition b); f64; AVX-512; making Rust the default for
note-vector search (that is S3).

**LANDED 2026-11-25 — every acceptance met (E17).**

- `Rust/cosine_knn/src/lib.rs`: `dot_norm` — fused dot + row-norm² in
  ONE sweep, AVX2+FMA path (4 independent dot chains + 4 norm chains,
  32 floats in flight) runtime-selected via `is_x86_feature_detected!`,
  SSE2 baseline path, scalar off-x86; `cosine_similarity_fused(query,
  row, query_norm2)`; `norm2_scalar` hoists the query norm in the same
  8-lane accumulation the oracle uses. Wired into `topk_single` /
  `local_topk` (which covers `topk_parallel` via chunking). The scalar
  `cosine_similarity` is untouched as the parity oracle; S0's
  `cosine_similarity_simd` export was left as-is (it has no caller; the
  fused kernel is the production path). No new knob.
- Tests: +3 Rust unit tests (`fused_matches_scalar_across_dims` —
  dims 10–768 within 1e-5 of the scalar oracle; `fused_zero_norm_contract`;
  `topk_fused_order_matches_scalar_reference` — exact row order vs the
  scalar reference on a seeded 40×16). `cargo test --workspace`: 11 + 14
  + 9 + 8 green; clippy 0 warnings; fmt applied.
- Python parity: `tests/test_cosine_knn_backends.py` 9 passed unmodified
  (exact row order vs numpy held — no near-tie flip), including under
  `SCRIPT_KNN_STRICT=1`.
- Measured (E17, gate A/B, 3 fresh runs per build, loadavg ≤ 1.6):
  production 717×512 0.826 → 0.442 ms = **1.87×** (bar ≥ 1.5×);
  live-note **4.95×**, 250k **4.92×**, 1M **4.87×** — scale ratios
  0.17–0.20 → 0.84–0.96, i.e. the fused kernel sits at BLAS parity, the
  honest ceiling this slice projected. `MIN_SCALE_VS_NUMPY` re-pinned
  0.14 → 0.70 (post-S1 window (0.48, 0.84]) and the gate docstring
  carries both bands; gate rc=0 on all 4 runs after the re-pin.

## S2 — pure-Rust hop-histogram lane for the all-universe stamp (LANDED 2026-11-25)

**Why.** E1–E5 and E8. `doc/improvements/pending.md:57-59` names the
lever: "a compiled queue-BFS hop-histogram kernel in `graph_kernels` … —
operator's call." Operator direction 2026-11-25: promote to pure Rust
rather than keep a Python hybrid.

**What.**

- New lane in `Rust/graph_kernels`, one call for ALL sources (the Mojo
  per-source-subprocess lesson, `pending.md:828-829`: "batch ALL sources
  into one in-process call"): per-source BFS that folds a hop histogram as
  it goes — `hist[level] += frontier.len()`, the fold `bfs_hist` already
  uses at `lib.rs:130`. It is the existing
  `limited_bfs_hists` (`lib.rs:270`) with the two hyper_trails filters
  removed (even distances only, entity ids only) and no hop cap. No
  distance row is ever materialized.
- Returns exact integers only: per source `reach`, `sum_d`, `ecc`, and the
  `uint64[n, ecc_max+1]` histogram. Python keeps the O(n) work — the two
  float expressions, WF closeness and `cnt @ (1.0 / hops)`, computed by the
  same lines as today (`helpers/graph/scipy_bridge.py:360-368`) so there
  is one definition of each.
- `full_universe_stats` gets the kernel lane; the scipy lane stays
  untouched as oracle and fallback. Selector follows the `L1C_RUST`
  convention (`helpers/graph/l1_closeness.py` `_rust_lane`): unset = auto,
  `0/off/numpy` = scipy, anything else = kernel or a loud RuntimeError.
  A stale `.so` without the lane falls back in auto mode.
- `jobs` is inert on the kernel lane (the call site at
  `helpers/graph/query.py:2709` keeps passing it).
- 8th exported lane: `EXPORTED_LANES` in
  `tests/test_graph_kernels_parity.py:66-74` gains one entry (it is pinned
  against the crate's `wrap_pyfunction!` set).

**Acceptance.**

- Parity vs the scipy lane on the live projection, in-process, ≥ 3 runs:
  closeness exactly equal; structure scalars exactly equal; harmonic
  within the pinned `rtol=1e-12`
  (`test_full_universe_stats_jobs2_matches_jobs1`), with exact equality
  reported either way.
- The existing oracles run on the kernel lane too:
  `test_full_universe_stats_hand_oracle`,
  `test_full_universe_stats_matches_oracle_across_block_sizes`,
  `test_full_universe_stats_empty_graph`, plus a disconnected graph with
  isolated rows (reach 0, ecc 0, `radius` from the giant component) and a
  force-mode / fallback test.
- `cargo test -p graph_kernels`, `tests/test_graph_kernels_parity.py`,
  `tests/test_scipy_bridge.py` green.
- Speed: ≥ 5× the scipy lane's median wall time on the same matrix, same
  process, and ≥ 2× the E8 tight-hybrid median. Below either, do not
  adopt — the hybrid is then the better lane and should be shipped
  instead.

**Projected outcome.** The fold adds one integer add per BFS level per
source, so the lane should land near the measured distance-BFS time:
≈ 4–5 s vs 44–45 s, about 10×, and about 3× over the best no-new-Rust
lane (E8, 13.2–15.1 s).

**LANDED 2026-11-25 — every acceptance met (E14).**

- `Rust/graph_kernels/src/lib.rs`: `hop_hist_fold` +
  `hop_hist_sources` (8th export, one call for ALL sources, exact
  integers only — `reach`/`sum_d`/`ecc`/`hist[k, width]`, the histogram
  folded into the BFS, no distance row materialized). Two Rust unit
  tests (hand-checked graphs + agreement with `core_bfs_distances`
  rows). `cargo test` 14 lib + 9 prop; clippy clean; fmt applied;
  `make rust-install` done.
- `helpers/graph/scipy_bridge.py`: `SB_RUST` selector (`_bridge_lane`,
  the `L1C_RUST` convention) + `_kernel_hop_hist` +
  the kernel branch in `full_universe_stats` (scipy worker untouched as
  oracle/fallback; `jobs` inert on the kernel lane; float formulas stay
  in Python, same expression lines as the worker).
- `tests/test_graph_kernels_parity.py`: `hop_hist_sources` joins
  `EXPORTED_LANES`. `tests/test_scipy_bridge.py`: +2 tests —
  kernel/off/force parity (`test_full_universe_kernel_lane_matches_scipy_lane`)
  and loud-force / silent-off (`test_full_universe_force_mode_is_loud_without_the_lane`);
  the shipped full-universe oracles now run the kernel lane and stay
  green (78 passed).
- Measured (E14, live projection, in-process, load average ≤ 2.9):
  kernel 3.32 s median vs scipy 32.91 s (`jobs=4`) = 9.91× (bar ≥ 5×);
  3.97–4.55× the E8 tight hybrid (bar ≥ 2×); closeness and structure
  exactly equal, harmonic max rel diff 1.7e-16 (pin 1e-12). The
  projection (≈ 4–5 s) was slightly pessimistic.

**Non-goals.** `structure_stats` (`scipy_bridge.py:462`, no production
caller); moving `connected_components` or the float formulas into Rust;
thread-count plumbing for rayon.

## S3 — `similar_notes` Rust port: PARKED

Carried as a slice on request, not scheduled. #450
(`doc/improvements/archive/graph/cosine_knn_production_integration.md`,
deferred 2026-10-28) measured `similar_notes(k=10)` in DuckDB SQL at
**1.70 ms** against **1,717.8 ms** to fetch and decode all section vectors
into Python. No kernel closes a 1,000× data-movement cost; S1 does not
change that.

- **Acceptance (nothing to build):** the proposal record states the
  re-open test. Re-open only when (a) a numpy argpartition top-k variant
  is benched at N = 17,550 and the surviving gap justifies an abi3
  dependency on the serving path, or (b) a materialized note-embedding
  matrix lands.
- **Non-goal:** the `app.py::_flat_knn_map` leg (#450, line 50: like-for-like
  win 1.37×, ~3 ms/query — "thin").

## S4 — contract closeness + harmonic through the kernel lane (LANDED 2026-11-25)

**Why.** E9–E11. The pair is computed live on every `--compute` run and
on any serving path that asks for the routed metrics
(`helpers/graph/algorithms.py:983-1024` → `_sb.compute`;
`algorithms.py:765-766` records it as "unstamped by design; computing
live"). scipy's heap `dijkstra` is 73% of it, and the existing kernel
already proves the ~15× (E11).

**What.**

- The lane from S2 already takes a `sources` argument (the all-universe
  call site just passes all sources). `scipy_bridge.compute`
  (`helpers/graph/scipy_bridge.py:552-575`) routes through it in auto
  mode, behind the same availability-following selector as S2 — one
  switch (`SB_RUST`) governs the bridge's kernel implementations.
- `ROUTING` is unchanged: the metrics stay SCIPY-owned. The kernel is an
  implementation detail of the bridge, the way `_ppr_kernel_lane`
  already is for PPR.
- The scipy lane stays untouched as oracle and fallback (auto / off /
  force semantics as in S2; a stale `.so` falls back in auto).
- Python keeps only O(n) work: `(N - 1) / sum_d` and the histogram
  harmonic, computed by the same formula lines as today
  (`helpers/graph/scipy_bridge.py` `fused_derive` semantics).
- `fused_derive` itself stays — the scipy oracle lane still uses it.

**Acceptance.**

- Parity on the live contract, in-process, ≥ 3 runs: closeness exactly
  equal; harmonic within the pinned `rtol=1e-12`. E11 measured max rel
  diff 4.1e-16 (abs 1.8e-12, 916 of 1,731 rows differing in the last
  bits), so the pin has measured headroom.
- Existing tests green unmodified (`tests/test_scipy_bridge.py`):
  `test_fused_derive_hand_checked`, `test_fused_derive_unreachable_excluded`,
  `test_compute_jobs1_matches_jobs2`, `test_values_match_hand_computed`,
  `test_dispatch_closeness_tmpstore`, `test_dispatch_harmonic_tmpstore`,
  `test_dispatch_compute_plumbs_db_path`,
  `test_dispatch_live_closeness_serving_stack_invariants` — plus new
  force / off selector tests and a stale-`.so` fallback test.
- Speed: ≥ 5× E9's median on the same matrix, same process. Measured
  headroom is ≈ 15×.
- If S2's fold misses its own bar and the tight hybrid ships instead,
  this slice routes through that hybrid — the E11 numbers are the floor
  either way.

**Non-goals.** Changing `ROUTING`; stamping the pair (it stays computed
live); touching the Onager lanes or `fused_derive`.

**LANDED 2026-11-25 — every acceptance met (E15).**

- `helpers/graph/scipy_bridge.py`: kernel branch at the top of
  `compute()` behind the SAME `SB_RUST` selector (auto routes to the
  lane; `off` keeps the shapped-dijkstra oracle; force without the lane
  stays loud). Closeness is bit-identical (`sum_d` stays an exact
  integer through `(n - 1) / np.where(sum_d == 0, np.inf, sum_d)`);
  harmonic is the same sum re-associated through the hop histogram
  (`hist @ (1.0 / hops)`, width-guarded); `return_counts=True` returns
  `reach + 1` so the module CLI's Wasserman-Faust caller keeps its
  contract; `jobs` inert on the lane. SciPy `fused_derive` untouched.
- Tests: all 8 named S4 tests green UNMODIFIED in both lanes
  (`GRAPH_KERNELS_STRICT=1` auto: 60 passed; `SB_RUST=off`: 15 passed).
- Measured (E15, live contract pair, in-process, loadavg ≤ 1.6): scipy
  7.54 s wall (load 0.28 + compute 7.26) vs kernel 0.56 / 0.56 / 0.57 s
  = **13.4×** (compute-only 24×; bar ≥ 5×). Closeness exactly equal;
  harmonic max rel diff 4.03e-16 (pin 1e-12).

## S5 — PPR edge attribution: projection reuse + numpy top-k (no Rust) (LANDED 2026-11-25)

**Why.** E12. The kernel is 15% of the wall; ~81% is ten near-identical
projection builds and pure-Python ranking loops. The ladder rule
(`doc/improvements/pending.md:809-811`) sends plain vector work to
numpy, not to a crate.

**What.** Measure-first: cProfile again on the changed build before
adopting either half.

- Build the base relational projection once per run and derive the nine
  leave-one-type-out ablations by masking that type's entries in CSR
  space, instead of nine more `load_centrality_projection` SQL loads.
  Node sets differ per projection today (`kept = [s for s in cohort if s
  in names_t]`), so the mask must reproduce induced-subgraph semantics —
  proven by a parity run, not assumed.
- Replace `_topk_others`' per-candidate Python sort (674,481 `sorted()`
  calls inside `_diff_neighborhoods`, E12) with numpy argsort /
  argpartition top-k, tie-broken by (score, index) so neighborhoods stay
  deterministic.

**Acceptance.**

- `tests/test_ppr_edge_attribution.py` green unmodified:
  `test_topk_others_excludes_seed`, `test_run_report_shape_and_coverage`,
  `test_run_metrics_are_bounded_and_deterministic`,
  `test_run_cross_projection_diff_within_range` and the rest.
- Report parity on the live store: ablation and cross-projection numbers
  match the current implementation exactly, or within a tolerance that a
  parity run shows is pure float reordering. A near-tie flip in a
  reported neighborhood is a finding, not a tolerance to loosen.
- Bench leg `ppr_edge_attribution` (budget 90 s; gate wall 6.4–9.1 s)
  does not regress; the projected ≈ 2 s standalone (E12 split) is
  measured before adoption.

**Non-goals.** No Rust anywhere in this slice; no change to the report
schema; no change to the PPR kernel or its guards.

**LANDED 2026-11-25 — every acceptance met (E16).**

- `helpers/graph/scipy_bridge.py`: `centrality_typed_edge_rows` (the
  one-fetch typed-row helper; `load_centrality_projection` refactored
  to call it, behavior unchanged).
- `helpers/graph/ppr_edge_attribution.py`: `_projection_family` —
  alphabetical name codes encoded once, per-subset `np.isin` type mask +
  self-loop drop, `searchsorted` local positions, `np.unique` canonical
  pairs, symmetric binary CSR (empty-subset `(0, 0)` branch mirrors the
  original); `run()` builds all ten projections from that one fetch;
  `_topk_others` now `names.index` + `order[order != si][:k]` (same
  argsort call, so the permutation — and every reported neighborhood —
  is unchanged; the per-call 22k-entry dict build and the 22k-iteration
  Python filter are gone).
- Parity: all 10 projections EXACT vs `load_centrality_projection`
  (names list + indptr/indices/data); full report JSON EXACT against
  the pre-slice module (git HEAD copy) except `wall_seconds` /
  `generated_at`.
- Measured (E16, live store, cohort 8, top-k 10): family 0.98 s vs
  3.39 s for the ten reloads; `run()` 6.09 s → 2.30 / 2.33 / 2.53 s =
  **2.6×**. Tests green unmodified (68 passed); bench leg PASS at
  2.31 s (gate range was 6.4–9.1 s).

## S6 — survey leftovers: PARKED (no work scheduled)

Carried so the 2026-11-25 survey stays answered next to the slices.
None of these is scheduled; each records its own re-open condition.

- **`l1_closeness` fold retirement.** The fold lane
  (`helpers/graph/l1_closeness.py`) is off the production routing (only
  the `helpers/misc/parity_harness.py:298` fixture and
  `tests/bench_l1_kernels.py` use it) and computes the same contract
  closeness + harmonic as the SCIPY lane, bit-identically. E13: kernel
  4% of its wall; the rest is the Python scoring the fold exists to
  make affordable. Once S4 lands, the fold has no production job —
  retire it (keeping the scipy lane as oracle) rather than port it.
  Re-open when S4 lands and the operator rules on the fold, or when the
  `l1_kernels` bench budget (240 s; wall 70–79 s, mostly the numpy
  baseline it times on purpose) becomes a problem.
- **`_structure_worker` / `structure_stats`**
  (`helpers/graph/scipy_bridge.py:270` / `:462`): the third scipy
  `dijkstra` call site; no production caller ("Opt-in lane — not in
  ROUTING, not gated"); returns a subset of what `full_universe_stats`
  already returns. Fold into the S2 lane (its ecc/reach outputs are a
  superset) or drop. Re-open with S2's arc.
- **L1b betweenness:** nothing to move — `brandes_fold_sources` is 88%
  of the profiled wall (E13). Recorded so the question stays closed.
- **Non-kernel perf legs:** closed verdicts stand, not re-profiled —
  `derive_insights` is "an IPC-shape + YAML problem, not a Mojo/Rust
  one" (#522 S5); near-duplicate GEMM rework executed 2026-10-01 (wall
  flat across a 900× threshold sweep); json → orjson (#504); regex port
  closed 2026-08-29 (#180); `l1_mst` = scipy csgraph (compiled).

## Order and gates

- S1 and S2 are independent (different crates, different gates); either
  order lands. S2 is the higher-certainty payoff and touches a nightly
  stamp; S1 is measure-first with a kill switch. S4 rides S2's lane and
  selector, so it lands after S2 (or on S2's hybrid fallback). S5 is
  independent of all of them and carries no Rust. S6 is parked — no
  work scheduled.
- Work happens in the `rust_arcs` patch. Arc gates per
  `doc/procedures/gates.md`: targeted tests during the arc; full gates
  (qa + integration + perf + advisory) once, on the operator's go.
  Rust legs that must stay green: `rust-test` (qa, strict),
  `rust-format` (qa), `rust-clippy` (advisory), `rust-deny` (advisory);
  perf leg `cosine_knn_ratios` (`tests/run_perf_benchmarks.py:590`).
- A real-run re-time of `make stamp-centrality` is owed but the maint
  chain is operator-owned (`pending.md:59-60`) — only on the operator's go.

## 5. Risks

- **S1: last-ulp score changes reorder near-ties** in fixtures pinned to
  exact order — report any flip; never widen a tolerance to pass.
- **S1: the win over BLAS may not materialise** — E7 arithmetic says
  parity is the ceiling for Trial-B-grade SIMD. The adopt rule above is
  the exit.
- **S2: stale `.so`** — a build without the new lane must fall back in auto
  mode and raise in force mode; `GRAPH_KERNELS_STRICT=1` (set by the qa
  harness) fails the missing lane loudly.
- **S2: all-cores burst** — neither crate caps rayon, so the kernel uses
  all 4 threads for ~4 s, where the house fan-out rule leaves one core
  free. Same as every existing kernel lane, and 4 s instead of 45 s; a
  thread cap is available if the desktop is unusable.
- **S2: harmonic last-ulp** — the histogram width changes the dot length,
  so BLAS may re-associate; `rtol=1e-12` is the pinned ceiling, not the
  target.
- **S4: same harmonic pin** — measured on the live contract at max abs
  diff 1.8e-12 (E11), so the S2 ceiling covers it.
- **S5: masking changes node sets** — ablated projections drop nodes that
  only exist via the removed type; the induced-subgraph equivalence must
  be proven by a parity run, not assumed.
- **S5: near-tie flips in reported neighborhoods** — top-k reordering can
  flip equal scores; tie-break by (score, index) and treat any remaining
  flip as a finding.

## 6. Non-goals

- Mojo for either lane (closed on the `pending.md` ladder).
- Changing `default_workers` or the fork-pool sharding of the scipy lane.
- Re-timing or reordering the maint chain.
- Any change to what `similar_notes` or script search return beyond
  last-ulp float differences.

## Appendix — raw measurement log

| Date | Command | Result | Notes |
|---|---|---|---|
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/fu_ab.py` | E1–E5 | read-only; `load_projection(read_only=True)`; candidate = the shipped function with only the block BFS producer swapped for `core_bfs_sources_batch`; `jobs=3` reference, `jobs=1` candidate |
| 2026-11-25 | E3 split | BFS 3.71–3.89 s; int32→float64 6.21–8.33 s; reduction + rest 25.25–32.49 s; 90 calls | the reduction is the cost |
| 2026-11-25 | E4 blocks | lo=0 max hop 10, 282,112 unreachable; lo=11,420 max hop 11, 259,946; lo=22,584 max hop 10, 237,689 | all exactly equal |
| 2026-11-25 | E1/E5 structure | diameter 11, radius 6, APL 4.303870439777702, reachable pairs 495,451,450, components 65, largest 22,259 | identical on both lanes |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/fu_ab4.py` | E8 | read-only; one process, 12 blocks of 2,048 sources; run 1 wall 15.06 s = kernel 5.98 s + histogram 9.08 s; run 2 wall 13.24 s = kernel 4.85 s + histogram 8.39 s; load average 1.3–2.8 (a background timing run shared the box) |
| 2026-11-25 | E8 oracle check | reachable pairs 495,451,450; APL 4.303870439777702; max ecc 11 | all equal to the E1 structure values |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/fu_ab2.py` | — | same lane as E8 with `fork_map` at `jobs=1` and `jobs=3`; printed 3 `jobs=1` runs (11.79 / 15.30 / 11.03 s; closeness exact, harmonic max rel 4.156e-16, structure equal) before it was terminated at 18:27 — consistent with E8 |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/fu_ab5.py` | E14 | read-only; `full_universe_stats` auto (kernel) ×3 vs `SB_RUST=off` ×1, in-process; load_projection 0.24 s; kernel 3.35/3.32/3.32 s, scipy 32.91 s (jobs=4) = 9.91×; closeness + structure exact, harmonic max rel 1.653e-16 |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/fu_ab3.py` | E9–E11 | read-only; 1,731 contract sources; scipy reference 9.50 s (second pass 8.42 s); kernel→float64→`fused_derive` 3.30/3.83/3.26 s median 3.39 s, bit-identical both metrics; histogram route closeness exact, harmonic max rel 4.117e-16 (abs 1.819e-12, 916/1,731 rows) |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/py_around_kernel_profile.py` | E12–E13 | read-only wall ×2–3 + cProfile per lane; un-profiled wall treated as truth, profile only for the split |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/fu_ab6.py` | E15 | read-only; contract pair via the production four-liner (load → contract_sources → source_positions → `compute(jobs=1)`); scipy lane 7.54 s (load 0.28 + compute 7.26) vs kernel 0.56/0.56/0.57 s = 13.4×; closeness exact, harmonic max rel 4.034e-16 (abs 1.819e-12); loadavg 1.5 |
| 2026-11-25 | `.venv/bin/python3 /mnt/data/tmp/opencode/ppr_ab.py` | E16 | read-only; `_projection_family` one fetch (0.98 s) vs 10 × `load_centrality_projection` (3.39 s), 10/10 projections EXACT (names + indptr/indices/data); run() old (git HEAD module) 6.09 s vs new 2.30/2.33/2.53 s; report JSON EXACT except wall/generated_at |
| 2026-11-25 | `.venv/bin/python3 tests/bench_ppr_edge_attribution.py` | S5 bench leg | PASS wall 2.31 s, cohort 8, top-k 10, cross-projection jaccard μ 0.977 (gate range was 6.4–9.1 s) |
| 2026-11-25 | `Rust/bench/cosine_gate.py`, 3 runs pre-S1 + 3 post-S1 + 1 post-repin | E17 | pre: prod 0.821/0.838/0.826 ms, live-note ~12.0 ms, 250k ~117 ms, 1M ~470 ms; post: prod 0.456/0.442/0.437 ms (1.87×), live-note 2.455/2.406/2.430 ms (4.95×), 250k 23.468/23.721/24.373 ms (4.92×), 1M 95.491/96.125/97.364 ms (4.87×); ratios post 0.84–0.96; floor 0.14 → 0.70 (window (0.48, 0.84]); rc=0 all runs; loadavg ≤ 1.6 |
| 2026-11-25 | `cargo test --workspace` | S1 tests | cosine_knn 11 lib + 8 prop, graph_kernels 14 lib + 9 prop — all green; clippy 0 warnings; `tests/test_cosine_knn_backends.py` 9 passed unmodified (SCRIPT_KNN_STRICT=1 incl.) |
| 2026-11-25 | `cargo run --release -p cosine_knn --bin trial` | E6 | 1×1024 2087.4→10093.6 q/s (4.84×); 1×4096 545.6→2045.1 (3.75×); 1×16384 136.8→506.0 (3.70×); 4×1024 2033.2→10616.7 (5.22×); 4×4096 461.3→2011.5 (4.36×); 4×16384 129.3→452.5 (3.50×); 16×1024 2078.4→10276.2 (4.94×); 16×4096 502.2→1803.4 (3.59×); 16×16384 128.0→470.1 (3.67×) |
| 2026-11-25 | `cosine_similarity_simd_py` microbench, 2,000 rows | ~3.3 ms vs numpy ~3.2 ms | flat; trial-scale ratios are the faithful comparison |
