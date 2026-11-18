---
title: "Rust/PyO3 Brandes core for L1b betweenness — batched, parity-pinned"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "538"
area: graph
---

# Rust/PyO3 Brandes core for L1b betweenness

## 0. Finding

`helpers/graph/l1_betweenness.py::compute` takes **59.6 s (jobs=1) /
22.4 s (jobs=4)** on the live store (2026-11-18). cProfile/CUDA of the
call confirms the cost is inherent, not redundant work:

- `brandes_core` (`:330`) fans `_brandes_chunk` (`:302`) over source
  chunks via `fork_map` (`helpers/core/forkmap.py`). Each source runs
  `_brandes_source` (`:236`) — a full BFS over the **core-induced** graph
  (11,414 core nodes of 22,125 total; 10,711 peeled into folded trees).
- `_brandes_source` is already numpy-vectorized (level-by-level expansion,
  `np.bincount` accumulators) and the two-accumulator fold (U core-core,
  TU core↔tree, KK tree↔tree) is correct and toy-proven. There is no
  recomputation to memoize (unlike `signal_sweep`, candidate 1 in
  `bench_data/hunt_python_lanes_2026-11-18.md`).
- The wall is therefore the per-source BFS + dependency accumulation in
  Python/numpy — exactly the BFS-shaped work `graph_kernels` already owns
  for `all_pairs_bfs_stats` and `limited_bfs_sources` (Rust/rayon, landed
  `rust_bfs_kernels_stats_trails`, completed.md #? — see
  `doc/improvements/pending.md` "Rust/PyO3 rung — FIRED for BFS-shaped
  work").

This is a **real port candidate**, not an ad-hoc spike: the repo doctrine
(`doc/improvements/archive/graph/rust_cosine_knn_port.md:118` "the first
fix is numpy, not Rust") is satisfied — numpy is already the Python lane
and cannot close the gap.

## 1. Scope — kernel only, not a rewrite

- **Port target**: `_brandes_source` → a `#[pyfunction]` in
  `Rust/graph_kernels` (new export e.g. `brandes_fold_sources`), taking the
  core CSR (`indptr`, `indices`), `kw` (attachment weights), `is_core`,
  and an array of source ids; returning the three accumulated vectors
  `U, TU, KK`. Batch ALL sources into ONE call (the Mojo per-source-
  subprocess lesson from #534: batching is where the win lives). Rayon
  over source chunks, each task owning scratch (dist, sigma, two deltas)
  — mirrors `all_pairs_bfs_stats`'s rayon-over-roots with per-task scratch
  (`Rust/graph_kernels/src/lib.rs:50-56`).
- **Keep in Python**: the fold decomposition (`peel_two_core`,
  `tree_components`, `_compute_attachment_weights`, `tree/special`
  closed-form scores, `raw = U/2 + TU + KK/2`). The kernel replaces ONLY
  the BFS+accumulator hot loop. This is the same seam as the scipy/
  Onager lanes — the contract population stays Python.
- **Exact semantics to preserve** (these are correctness, not perf):
  - dependency on the **core-induced** subgraph only (full adjacency
    double-covers core→leaf pairs — 3.7× inflation per the fold's toy
    proof);
  - `delta_u`/`delta_k` ordered accumulation (σ ratio × (1+δ) and
    (kw+δ)), U halved and KK halved only at Python assembly;
  - source's own dependency entry zeroed (`du[s]=dk[s]=0`) and `kk_world`
    credit for `v=s` added only when `k_s>0` (`_brandes_chunk:313-320`).
- **Floating-point**: accumulate in f64, same order of `np.bincount`
  reductions where feasible; parity is checked within a tight epsilon,
  not bit-exact (numpy scatter order already differs across thread counts
  in the Python lane — see how `brandes_core` tolerates fork_map parts).

## 2. Integration / dispatch

- Follow the selector ladder in `doc/improvements/pending.md` and the
  cosine precedent (`rust_cosine_knn_port.md:76`): `auto → rust if the
  module imports, else the numpy/fork_map lane, else ...`. Add a new
  routing owner if a second lane needs to coexist; default behavior on
  this host (where the Rust .so is installed) becomes the Rust kernel
  ONLY after parity is proven — until then keep an opt-in env flag
  (`L1B_RUST=1`).
- `algorithms.py::_run_l1b_lane` (`:941`) calls `l1_betweenness.compute(db,
  jobs=1)`; that call site is unchanged — the dispatch lives inside
  `compute`/`brandes_core`, preserving the fail-loud posture (no silent
  Onager fallthrough) and the parity harness fixture
  (`helpers/misc/parity_harness.py:270 "l1_betweenness_compute"`).

## 3. Parity + acceptance

- Fixture: existing `parity_harness` `l1_betweenness_compute` fixture
  (folded betweenness over a fixture graph DB, noise/self-loop rows
  included) plus the live-store full compute. Compare the Rust result to
  the frozen Python output at jobs=1 and jobs=4.
- Acceptance: U, TU, KK vectors and final `scores_by_name` within the
  parity harness tolerance (float32/float64 epsilon documented there);
  `tests/test_l1_betweenness.py` green; `make mojo/parity`-style parity
  leg green; rust crate tests (`cargo test` in `Rust/graph_kernels`) pass.
- Target: >2× wall reduction at jobs=1 (59.6 s → <30 s), ideally
  approaching the rayon BFS wins (~3–5×) seen in `all_pairs_bfs_stats`.

## 4. Risks / non-goals

- Non-goal: replacing the fold decomposition or the contract population
  path; no query-visible semantics change (exact parity) → **no eval-gate
  bullet required**.
- Risk: f64 scatter-order drift between rayon tasks could shift low-order
  digits; parity harness is the gate and epsilon must be justified, not
  loosened to pass.
- Risk: ABI/build — `graph_kernels` is abi3-py39, already installed in the
  shared venv; adding an export must not break existing BFS/PPR/cosine
  exports (29-binary Mojo build is separate).
- Gates parked (operator m01133) — targeted `cargo test` + parity + tests
  only, no `make qa`/`make perf` until operator permission.

## 5. Outcome (2026-11-18, executed)

**Shipped.** `Rust/graph_kernels/src/lib.rs` gained
`brandes_fold_sources(indptr, indices, kw, is_core, sources,
force_parallel=False)` → `(U, TU, KK)` f64, plus `BrandesGraph` (a
borrowed-args struct so clippy's 7-arg limit holds),
`BrandesScratch` (per-source `dist`/`sigma`/`delta_u`/`delta_k`/`touched`/
frontier/next/edges/level_bounds with O(visited) `clear`, NOT an O(n)
refill), `brandes_source_fold` (level-synchronous core BFS + one reverse
level pass) and `brandes_chunk`. Rayon chunks sources in `SOURCE_CHUNK =
256` blocks with per-chunk accumulators summed in chunk order;
`PARALLEL_THRESHOLD = 1_024` keeps small source sets sequential.

Python side: `helpers/graph/l1_betweenness.py` gained `_rust_lane` /
`_kernel_brandes_core` and dispatches inside `brandes_core`. **The kernel
is the DEFAULT lane** — house convention, matching `stats.py`,
`scipy_bridge._ppr_kernel_lane` and `hyper_trails.TRAIL_BFS_RUST`, all of
which default TO the kernel and let the env var opt out. UNSET or `auto`
= kernel when `graph_kernels` imports, else numpy (a contributor without
cargo still gets correct numbers); `L1B_RUST=0`/`off`/`numpy` = the
untouched numpy+fork_map lane, bit-for-bit the pre-kernel values, for A/B
and fallback pinning; `L1B_RUST=1`/`on` = kernel or a loud
`RuntimeError` (a stale build must never silently fall back). The fold
decomposition, closed-form tree/special scores and
`raw = U/2 + TU + KK/2` assembly stay in Python.

**Measured** (live store, 22,130 projections / 11,448 core nodes, quiet
4-core box, `tests/bench_l1_kernels.py`):

| jobs | numpy lane | Rust lane | speedup |
|---|---|---|---|
| 1 | 51.8 s | 3.9 s | **13.3x** |
| 4 | 19.2 s | 4.6 s | 4.1x |

Parity against the numpy lane, max RELATIVE deviation over the whole
`U`/`TU`/`KK` vectors: `U` 4.5e-13, `TU` 4.6e-13, `KK` 1.4e-14 at jobs=1
(1.2e-13 / 8.0e-14 / 3.1e-15 at jobs=4) — the documented 1e-9 epsilon is
~4 orders of headroom. `compute()` end-to-end scores agree to 2.7e-13
relative over all 22,130 names with an identical `info` block.

**Guards.** `Rust/graph_kernels/tests/properties.rs`:
`brandes_fold_matches_textbook` (proptest against a textbook Brandes with
explicit predecessor lists — a different accumulation path, 1e-9; plus
seq-vs-par at 1e-12), `brandes_fold_empty_sources_is_all_zero`.
`tests/test_l1_kernel_parity.py` pins the selector vocabulary
(`off`/`auto`/`force`), asserts the kernel is actually TAKEN (a silent
fallback would make parity vacuous), and checks numpy-vs-Rust agreement on
the fixture store.

**Perf leg.** `tests/bench_l1_kernels.py`, registered as `l1_kernels`
(240 s budget) in `tests/run_perf_benchmarks.py` — asserts parity AND the
>2x bar on the live store.

**Posture.** Kernel-only; no routing or query-visible semantics change
(exact parity) → no eval-gate bullet. Default-on with an opt-out env var,
so a box without the extension degrades to the numpy lane rather than
failing. Gates parked per operator
m01133 — targeted `cargo test` + `cargo clippy` + parity + the two L1
suites + the bench leg only.
