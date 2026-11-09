---
title: "Rust BFS kernels for graph-stats and trail lanes (all-pairs stats + limited multi-source)"
status: executed
filed: "2026-11-09"
executed: "2026-11-09"
completed_md: "497"
area: graph
---

# Rust BFS kernels for graph-stats and trail lanes

**Date:** 2026-11-09 · **Status:** EXECUTED same day · **completed.md:** #497

## Summary

Two CLI lanes spend their wall in pure-BFS Python/SciPy work that a small
Rust kernel (the same CSR-frontier shape already proven by the
`cosine_knn` crate) beats by **29x** and **40x** measured, with exact
hop-level parity proven against scipy ground truth on the live graph and
14 synthetic families:

| Lane | Today | Rust kernel | Win |
|---|---|---|---|
| `make graph-stats` (`helpers/graph/stats.py`, all-pairs diameter/median/ties over 19,130-node projections) | 76.25 s wall; ~64 s of it in `shortest_path` + `_ifub_diameter` + `_chain_dist_stats` | ~2.2 s for BOTH projections (4-thread rayon) | **~29x** on the replaced segment |
| `helpers/graph/hyper_trails.py` (800 sources, `dijkstra(unweighted=True, limit=40)`) | 3.16 s kernel leg (3.36-8.90 ms/source incl. wrapper) | 0.079 s | **~40x** |

Second finding, larger than the speed: the sampled path materialises
6,000 roots x 19,130 nodes of `dist`+`pred` int64 = **1,751 MB** (the
recorded "2.9 GB process peak"). The kernel folds each root's sweep into
a distance histogram and holds one `i32` array per thread = **0.29 MB**.
With the kernel, `CHAIN_CAP` stops being a memory guard and becomes a
pure display choice.

This is a developer-loop and diagnostics proposal, NOT gate headroom: no
leg in `tests/run_perf_benchmarks.py` calls `longest_chains` or the
trail CLI. (`route_graph_stats` benches the all-SQLite `/api/graph/stats`
route; `shortest_path_bfs` benches the `/api/graph/shortest` route —
neither touches these code paths.)

## Evidence (2026-11-09 session, live DB, i5-6500 4C/4T)

### graph-stats wall breakdown

`python3 helpers/graph/stats.py` wall 76.25 s (cProfile of the CLI:
`stats.py:207(bfs)` 38,130 calls, cumtime 45.7 s; `longest_chains`
cumtime 77.0 s):

| stage | ALL | ACTIVITY |
|---|---|---|
| `scipy shortest_path(method="D", unweighted=True, return_predecessors=True, indices=6000 sampled roots)` | 12.43 s | 11.20 s |
| `_chain_dist_stats` (stats.py:112) | 2.46 s | 0.72 s |
| `_ifub_diameter` (stats.py:160, called at :447) | 20.64 s | 16.95 s |
| `_select_chain_pairs` (stats.py:285) | 0.87 s | 0.53 s |

Projections (via `stats._chain_edge_matrix`): ALL n=19,130 nnz=73,676
(114 components, largest 17,076); ACTIVITY n=19,130 nnz=66,674 (470
components, largest 16,730). n > `CHAIN_CAP` = 6,000, so the SAMPLED
path is the live one.

Rust all-pairs over ALL 19,130 roots (throwaway probe, rayon 1.12,
offline build, CSR from the same projections): ALL wall 1.05-1.16 s,
ACTIVITY 0.96-1.06 s, diameter 13 on both — replacing ~64 s of the wall.
The 5 displayed chains keep Python predecessors: `shortest_path` over 5
roots measured ~14 ms.

### Parity

- 14/14 synthetic families EXACT match vs scipy `shortest_path` ground
  truth (diameter/median/ties/total): connected random (n=120/200/300/600),
  disconnected 3-block and 25-small, isolates, `path_250` (diameter 249),
  `star_150` (ties 22,350), `grid_12x12` (diameter 22), `cycle_200`
  (diameter 100), single-node, two-isolated, empty. Coverage deliberately
  spans the degenerate shapes.
- Trail lane, live bipartite (n=21,305, nnz=85,080, n_entities=17,998,
  limit=40, 800 sources): `total_reached` = 11,902,037 AND a
  per-entity hop checksum (sum of entity_idx * hops, u64 wrapping) =
  719,161,342,662 — identical between scipy and Rust.

### The panic the family sweep caught (lesson for S1)

The first probe hardcoded a 64-slot distance histogram;
`path_250`/`cycle_200` panicked (`index out of bounds: the len is 64
but the index is 64`). The live graph (diameter 13) would never have
fired it. Production kernel MUST size the histogram to `m.max(2)`
(worst case for a path graph is m-1), and parity tests MUST include
path/cycle families, not just random graphs.

### Why the mojo trail lane only reached parity

`_trail_bfs_mojo` (hyper_trails.py:244) shells out PER SOURCE
(`subprocess.run` + text parsing per call) — process spawn + parse eats
the kernel win (recorded 6.09 vs 6.10 ms/source). The 40x here comes
from batching ALL sources into ONE invocation. The integration must be
batched (one kernel call for the source set), not per-source.

## Doctrine hooks

- `pending.md` kernel ladder (~:735-760): "custom zero-copy logic ->
  Rust/PyO3" — this proposal is exactly that rung: zero-copy CSR arrays
  (`indptr`/`indices` int32) handed to a Rust kernel, the
  `cosine_knn` abi3 pattern.
- `graph_enhancement_roadmap.md` register (~:106-115) T2 "Mojo BFS CSR
  promotion, trigger ~10M rows / measured >2x": the measured->2x half of
  the trigger is now satisfied at 147,572 doubled rows for these CLI
  lanes, via Rust rather than Mojo. Register gets an annotation (S4).
- `mojo_12_adoptions.md` freeze: UNAFFECTED — the unfreeze trigger is
  "a wall neither native Python nor Rust closes"; this proposal CLOSES
  walls with Rust. No unfreeze.
- `scaling_next_walls.md` Wall 2 (trail parallelism): S1 pool landed;
  this supersedes the scipy default with the measured 40x kernel.

## Plan

- **S1 — `Rust/graph_kernels` crate** (new workspace member, abi3
  module like `cosine_knn`; the probe's subprocess+temp-file shape was
  throwaway only). Two entry points, both taking zero-copy CSR views:
  `all_pairs_bfs_stats(indptr, indices) -> (diameter, median, ties,
  total_pairs, histogram)` and `limited_bfs_sources(indptr, indices,
  sources, n_entities, limit) -> per-source (entity_idx, hops)`.
  Histogram sized `m.max(2)`. Proptest properties (#465 pattern) +
  scipy parity in `tests/`.
- **S2a — stats.py kernel swap (zero semantic change, recommended
  first):** `_ifub_diameter` body becomes one kernel call (signature
  preserved). 37.6 s -> ~2.2 s. No test rewrites; existing parity test
  keeps iFUB semantics as ground truth.
- **S2b — full path replacement (after S2a soaks):** all-roots kernel
  stats replace the sampled `shortest_path` + `_chain_dist_stats` +
  `_select_chain_pairs` source stats; Python keeps `_chain_path`
  predecessors for the displayed top-K chains only. Retires the sampling
  compromise and the 1.75 GB materialisation; resolves the ties-unit
  inconsistency (below). Touches `tests/test_hyper_incidence.py` and
  `tests/test_graph_stats.py`.
- **S3 — hyper_trails kernel lane:** `_trail_bfs` gains a batched
  kernel path behind a selector (rust -> scipy, the `SCRIPT_KNN`
  pattern), env-gated first (`TRAIL_BFS_RUST=1`, mirroring
  `MOJO_TRAIL_BFS`), default after parity run. One invocation for the
  whole source set, never per-source.
- **S4 — registers/docs:** roadmap T2 annotation, `pending.md` ladder
  entry, this file's status flip on execution.

## Acceptance criteria

- `make graph-stats` wall < 15 s (from 76.25 s; kernels ~2.2 s + CSR
  build 0.56 s + chain predecessors ~0.14 s + CLI overhead).
- `python3 helpers/graph/hyper_trails.py` full run < 1.5 s (from
  3.66 s; kernel 0.079 s + bipartite build 0.19 s + wrapper).
- Parity: kernel vs scipy on the live projections (diameter/median/
  ties/total exact); trail hop-checksum test green; 14-family sweep in
  CI-shaped test (path/cycle/star/grid mandatory); `cargo test
  --workspace` green; `make qa` green.
- No perf-gate budget changes (no leg touches these lanes).

## Decision points (operator)

1. **Ties unit.** The exact branch halves (`stats.py:451`,
   canonical a<b pairs); the LIVE capped branch never halves (ordered
   sample cells) — a latent inconsistency. Kernel reports ordered.
   Recommend: canonical everywhere, one-line change on the SAMPLED
   line, output annotation.
2. **abi3 in-venv module vs subprocess binary.** Recommend abi3
   (`cosine_knn` precedent, zero-copy, no spawn cost).
3. **S2a first then S2b, or both at once.** Recommend S2a first —
   independently shippable, de-risks the test churn.
4. **Trail kernel default.** Recommend env-gated first run, flip to
   default after one parity soak.

## Execution record (2026-11-09)

Executed the same day as filing, completed.md #497. Decision-point
resolutions: (1) ties canonical everywhere — the kernel lane halves
once (ordered → a<b pairs) and both render lines report canonical
counts; (2) abi3 in-venv module (`graph_kernels.abi3.so` via
`make rust-install`, cosine_knn precedent); (3) S2a landed first, S2b
followed in the same arc after a targeted-test soak; (4) the kernel is
DEFAULT from landing — the env gate is an escape hatch, not an opt-in
(`TRAIL_BFS_RUST=0` restores the SciPy/pool lanes), and the parity soak
rode the live A/B, the 58-source smoke, and the pinned test families.

## Alternatives measured and rejected (2026-11-09)

- `igraph.Graph.diameter(unconn=True)`: 7.96/8.33 s vs iFUB
  20.64/16.95 s (~2.5x) — viable stopgap if Rust is rejected; keeps
  sampling and the 1.75 GB peak.
- numpy frontier BFS variants: stamp+unique 5.417 ms/sweep,
  stamp+nodup 68.026 ms, setbits 5.055 ms — all worse than the
  incumbent 1.54 ms; the incumbent is at the practical numpy floor.
- scipy `breadth_first_order` 1.83 ms, unweighted `dijkstra` 4.13 ms,
  igraph `distances` 3.14 ms per sweep — 0.4-0.8x the incumbent.
- numba prange: absent from the venv; recorded Phase C 1.94x would
  still trail the kernel and adds a dependency.
- cosine_knn promotions: terminally closed — rust+rayon 0.47-0.49x
  numpy at Phase-C scale (250k/1M x 512), 0.54x at live convo scale.
- Mojo: freeze stands (std.python bridge holds the GIL; per-source
  subprocess shape caps at parity).

## Out of scope — other Rust-shaped findings from the 2026-11-09 survey

Recorded so the survey's full outcome lives in one place:

- **`l1_betweenness` Brandes — the biggest remaining Python lane.**
  `brandes_core` is 8.5 s of the 17.5 s `algorithms.py --all` wall
  (3,199 sources, numpy-vectorised two-accumulator, 6.66 s tottime in
  `_brandes_source`). Deliberately NOT this proposal: the fold's pair
  coverage semantics (U/TU/KK accumulators) need their own parity
  story. Natural follow-on `graph_kernels` entrant once S1-S3 land.
- **`Rust/embed_cache` — built, benched, no wall.** Crate exists in the
  workspace (no `.so` installed, no production import); recorded
  2.5-3.4x full-pipeline win over Python stands. Warm note rebuilds
  run at single-digit cache misses, so there is nothing to speed up.
  Revisit only if a wall appears.
- **MAX engine — no production wall.** `max.graph`/`max.engine`
  importable in the venv; the profitable rung is n≈2-16 batched GEMM
  at ≥7.6M elements while production similarity runs 1,209 vectors /
  ~3 ms. Revisit only if small-batch GEMM ever becomes hot.
- **`derive_insights` IPC idle-wait** — 4.0 s of its 9.8 s in
  `select.poll` under `ProcessPoolExecutor`
  (derive_insights.py:3722-3748, :3774-3797) plus 1.4 s in YAML
  parsing. Real, but process-topology work, not a kernel.
- **`app.py /api/graph/stats`** duplicates the stats surface in SQL —
  an all-SQLite route by design; not a kernel lane.

## Bench artifacts

Throwaway probe workspace `$TMPDIR/bfs_probe` (`src/main.rs` all-pairs
+ `src/bin/trail.rs` limited-source, both with checksums) and CSR bins
`$TMPDIR/{ALL,ACTIVITY,TRAIL}_{indptr,indices}.i32` — reproducible
from this doc's numbers; S1 re-implements the same kernels in-tree.
