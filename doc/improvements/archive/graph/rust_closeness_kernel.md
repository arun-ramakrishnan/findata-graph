---
title: "Rust/PyO3 core-BFS for L1 closeness/harmonic — batch sources, pin parity"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "539"
area: graph
---

# Rust/PyO3 core-BFS for L1 closeness/harmonic

## 0. Finding

`helpers/graph/l1_closeness.py::compute` takes **~8.8–10.1 s (jobs=1)**
on the live store (2026-11-18, profiled). cProfile attributes the cost
precisely:

| call | ncalls | cumtime | share |
|---|---|---|---|
| `core_bfs` (`:272`) | 1,486 | 5.78 s | 57% |
| `np.unique` inside `core_bfs` (via frontier dedup) | 10,487 | 3.34 s | 33% |
| `core_source_scores` (`:302`) | 1,414 | 2.16 s | 21% |
| `peeled_source_scores` (`:377`) | 307 | 1.45 s | 14% |

(The overlap in shares is because `core_source_scores`/`peeled_source_scores`
call into `core_bfs` indirectly; the tottime split is ~1.3 s in BFS itself
+ ~3.0 s in `np.unique`'s `_unique_hash` + ~1.9 s in the source-score
Python loops.)

The dominant, clearly-parallelizable cost is the **1,486 vectorized core
BFS runs** over the 11,414-node core CSR, each with ~7 `np.unique` calls
for frontier dedup. `compute` already forks over source chunks when
`jobs>1` (`fork_map` at `:556`); jobs=4 roughly halves the wall. This is
the same BFS-shaped work that `graph_kernels` already owns in Rust/rayon
(`all_pairs_bfs_stats`, `limited_bfs_sources`) — a legitimate port, not an
ad-hoc spike. `np.unique` (a hash-based sort per level) is the specific
hotspot a compiled implementation removes.

## 1. Scope — kernel only, not a rewrite

- **Port target**: `core_bfs` (`:272`) → a `#[pyfunction]` in
  `Rust/graph_kernels` (new export e.g. `core_bfs_sources_batch`) taking
  the core CSR (`core_indptr`, `core_indices`) and a batch of source ids;
  returning a distance matrix (n_sources × core_nodes, `-1` unreachable)
  or the derived closeness/harmonic aggregates directly. Batch ALL sources
  into ONE call (the #534 lesson). Rayon over source chunks, each task
  owning its dist array + frontiers (mirrors `all_pairs_bfs_stats`'s
  per-root scratch, `Rust/graph_kernels/src/lib.rs:50-56`).
- **Keep in Python**: the fold decomposition (`peel_two_core`,
  `build_forest`, `build_core_adjacency`, `get_contract`), and the
  forest/peeled/special source-score assembly
  (`core_source_scores`, `peeled_source_scores`, `special_source_scores`,
  `_scores_chunk`). The kernel replaces only the repeated core BFS frontier
  expansion + dedup.
- **Exact semantics to preserve**:
  - BFS on the **core-induced** subgraph only (`build_core_adjacency` at
    `:230` already restricts edges); `-1` for unreachable;
  - `core_dist` is consumed by core sources (self excluded:
    `reachable_core[u]=False` at `:309`) AND by peeled-tree sources as the
    root distance (`peeled_source_scores`, `h = root[w]`), so the kernel
    must return distances for **all** contract roots, not just core
    endpoints;
  - closeness `(n-1)/sum_d`, harmonic `sum(1/d)` — f64, same division
    order; parity within the harness epsilon.
- **Two implementation options** for the kernel output: (a) return the
  full distance matrix and keep `_scores_chunk`/score functions untouched
  (lowest risk, memory ~1,486×11,414 f64 ≈ 135 MB — needs a batching cap);
  (b) fuse the forest histogram fold into the kernel and return only the
  `(sum_d, harm)` aggregates (less memory, larger parity surface). Start
  with (a) and a source-batch cap (e.g. 256) to bound memory.

## 2. Integration / dispatch

- Mirror `rust_betweenness_kernel.md` and the cosine selector
  (`rust_cosine_knn_port.md:76`): `auto → rust if module imports, else
  numpy/fork_map`. Gate behind an opt-in `L1C_RUST=1` until parity passes.
- `algorithms.py::closeness_centrality` (`:215`) serves from the
  `v_centrality_closeness` cache table when stamped, otherwise routes to
  scipy (`ROUTING` maps closeness→SCIPY, `algorithms.py:22/658`). The
  `l1_closeness.py` module is the **exact** lane underneath that routing
  (`:989` "the exact lane"), not yet a routed default. This proposal
  accelerates that exact lane in place; promoting it into the router is a
  separate operator decision. No change to `algorithms.py` needed.

## 3. Parity + acceptance

- Baseline captured: `l1_closeness.compute(jobs=1)` wall **8.780 s**,
  1,731 scored names, SHA-256 fingerprint
  `e80f9b2ae5606648…` of sorted `(name, closeness, harmonic)`
  (`/mnt/data/tmp/opencode/closeness_baseline.json`, cross-check
  `bench_data/hunt_python_lanes_2026-11-18.md`).
- Parity: compare Rust distances/scores to the frozen Python output over
  all 1,731 names at jobs=1 and jobs=4; epsilon from the parity harness
  (no loosen-to-pass). Add a `l1_closeness_compute` fixture to
  `helpers/misc/parity_harness.py` (betweenness already has one at `:270`).
- Acceptance: `tests/test_l1_closeness.py` green, parity fixture green,
  `cargo test` in `Rust/graph_kernels` green. Target: >2× wall (8.8 s →
  <4.4 s) at jobs=1.

## 4. Risks / non-goals

- Non-goal: changing closeness/harmonic semantics or the ROUTING table;
  no query-visible change (exact parity) → **no eval-gate bullet**.
- Risk: 135 MB distance matrix at full batch — mitigate with the source
  batch cap and rayon task-local scratch; measure RSS.
- Risk: f64 reduction-order drift in `sum(1/d)`; parity harness is the
  gate, epsilon justified not loosened.
- Risk: new graph_kernels export must not break existing BFS/PPR/cosine
  exports (abi3-py39, shared venv).
- Gates parked (operator m01133) — targeted `cargo test` + parity + tests
  only, no `make qa`/`make perf` until operator permission.

## 5. Outcome (2026-11-18, executed)

**Shipped.** `Rust/graph_kernels/src/lib.rs` gained
`core_bfs_sources_batch(indptr, indices, sources)` → int32 matrix
`sources x n` (`-1` = unreachable), backed by `core_bfs_into` (level loop
with `std::mem::swap`, `dist.fill(-1)` per row) and
`pub fn core_bfs_distances`, which fans rows out with
`out.par_chunks_mut(n).zip(sources.par_iter())` — option (a) from §1.

Python side: `helpers/graph/l1_closeness.py` gained `_rust_lane` /
`_kernel_core_bfs` / `core_bfs_batch` (`L1C_RUST`, same vocabulary and
posture as the betweenness lane). **The kernel is the DEFAULT lane** —
unset means kernel-when-importable; `L1C_RUST=0`/`off`/`numpy` pins the
numpy lane for A/B and fallback validation; `L1C_RUST=1` demands the
kernel. The per-source numpy `core_bfs` remains the fallback and the
reference.
`_scores_chunk` was restructured into two passes: pass 1 classifies each
contract name and COLLECTS the BFS roots it needs (`u` for core sources,
`forest["root"][u]` for peeled ones), pass 2 scores from one batched
call — which also removed the old per-chunk `core_dist_cache` because
duplicate roots now collapse inside `core_bfs_batch`
(`dict.fromkeys`). `core_source_scores` / `peeled_source_scores` /
`special_source_scores` are untouched.

**`_SOURCE_BATCH = 256`.** 1,731 contract names resolve to ~1,486 BFS
roots; one call would materialize 1,486 x 22,125 int32 ~ 130 MB, so the
batch cap holds peak RSS at one 256 x n slab. A regression test drives
the split boundary (`monkeypatch.setattr(lc, "_SOURCE_BATCH", 1)`).

**Measured** (live store, 1,731 scored names, quiet box):

| jobs | numpy lane | Rust lane | speedup |
|---|---|---|---|
| 1 | 7.44 s | 3.07 s | **2.42x** |
| 4 | 3.62 s | 1.76 s | 2.06x |

Scores are **bit-identical** between lanes at both job counts (all 1,731
`closeness`/`harmonic` values, `assert ref == got`, plus an identical
`info` block) — distances are integers, so nothing drifts. Meets the
proposal's >2x-at-jobs=1 target (8.8 s → <4.4 s) at 7.44 s → 3.07 s.

**Guards.** `Rust/graph_kernels/tests/properties.rs`:
`core_bfs_batch_matches_reference` (proptest, EXACT equality against a
naive `VecDeque` BFS) and
`core_bfs_batch_unreachable_rows_are_minus_one`.
`tests/test_l1_kernel_parity.py` adds the batched-vs-single-source check,
the batch-cap split check, and a full-pipeline exact-equality test.

**Parity fixture.** `helpers/misc/parity_harness.py` gained
`l1_closeness_compute` (render `_render_l1c_compute`). The fixture store
had to grow a `graph_analytics` table first: `compute()` reads its
CONTRACT from it (`get_contract` = DISTINCT entity_name WHERE metric =
'closeness_centrality'), so without those rows the render was empty and
proved nothing. `helpers/graph/fixture_graph_db.py` now creates the table
with 8 contract rows (values are placeholders — the render recomputes
them). Live-vs-baseline: 2/2 fixtures byte-identical at seeds 0/1/7.

**Posture.** Kernel-only, default-on with an opt-out env var; no routing
change (`l1_closeness.py` stays the lane UNDER the SCIPY routing —
promoting it is a separate operator decision) → no eval-gate bullet.
Gates parked per operator m01133.
