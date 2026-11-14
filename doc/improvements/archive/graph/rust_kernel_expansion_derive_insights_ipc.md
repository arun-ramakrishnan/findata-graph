---
title: "Rust kernel expansion (persistent matrix cache, first-party PPR, CSR scale-up builder, graph-health kernels) + derive_insights IPC/YAML wall"
status: executed
filed: "2026-11-14"
executed: "2026-11-14"
completed_md: "522"
area: "Rust/graph_kernels + helpers/graph/derive_insights.py"
---

# Rust kernel expansion + derive_insights IPC/YAML wall

**Date:** 2026-11-14 · **Status:** EXECUTED (partial scope — S6 + S7 built
and landed same-day with measured verdicts; S1–S5 remain plan-now /
trigger-gated with their trials recorded in §6 — none of their triggers
has fired; `status: executed` is the schema's only executed value) ·
**Supersedes:** its own S2 slice is superseded by
`graph_kernels_ppr_lane.md` (same dir, completed.md #523) — the PPR
prototype became the shipped lane there ·
**Area:** `Rust/graph_kernels` (abi3, PyO3 0.30 + numpy 0.30 + rayon 1.10),
`helpers/graph/derive_insights.py`, `helpers/misc/convo_query.py`,
`helpers/graph/onager.py`, `helpers/validators/graph_scale_watch.py`

**Archive note 2026-11-14:** S6 shipped NOT as filed — the thread-local /
epoch-stamp reuse measured ~5% slower than HEAD at every scale, so the
landed form is a per-source `vec![0u32; m]` stamp with the write-only
`dist` vec dropped (~6–7% faster, bit-parity, 11/11 green). S7's per-call
copy premise measured 0.000 ms at live scale (scipy CSR already yields
int32 contiguous); landed as a shared `kernel_csr_arrays()` helper +
zero-copy identity test. Both verdicts and numbers live in §4 S6/S7.
Same-day follow-up: the stamp trick extended to the all-pairs kernel —
`bfs_hist()` now takes a caller-owned `stamp: &mut [u32]` + per-root
`epoch` (no per-root `fill`), 1.058× faster on the 22.3k bench
(5,114.9 → 4,833.7 ms best-of-6 interleaved), hist bit-exact; numbers in
`completed.md` #522 follow-up.
Same-day trial verdicts for S1/S2/S4/S5 are in §6; their build triggers
stay unmet — except S2, for which a first-party prototype landed anyway
(`personalized_pagerank` power iteration, scipy parity 1.7e-13, 10.3×
per seed on the live projection, plus the spsolve fill-in-blowup finding
on unstructured graphs; numbers in `completed.md` #522 follow-up 2).

**Revision 2026-11-14 (search-backed):** S3 re-scoped — the CSR substrate
already shipped early (`doc/improvements/archive/graph/csr_substrate.md:19`,
115,264 rows 2026-09-26); S3 is now CSR scale-up, not initial builder. Mojo
nuance added (§5): freeze holds for ports, but re-evaluation 2026-11-13
shows parity 92/92 (#520 steps 1–3 executed). PPR degeneracy record
superseded — proposal's seed-dependence proof stands.

## 1. Motivation

Rust is the active, sanctioned compute lane in this repo. `graph_kernels`
(abi3) landed 2026-11-09 (`b87e84c60`, "Rust BFS all-pairs stats + batched
trail map — 76.6s→11.3s") with two adopted kernels:
`all_pairs_bfs_stats` (lib.rs:199) and `limited_bfs_sources` (lib.rs:304),
wired into `helpers/graph/stats.py` via `_graph_kernels_stats`
(stats.py:168) with an `ImportError` fallback to the incumbent lane.
`cosine_knn` is terminally closed (0.47–0.49× vs numpy, rayon refuted).
Mojo is FROZEN for ports (2026-10-26, bench-side only; `mojo_12_adoptions.md:112`),
but NOT dead: re-evaluation 2026-11-13 (`mojo_12_adoptions.md:138`) shows
parity 92/92 and unfreeze steps 1–3 executed (#520). The `std.python` bridge
still holds the GIL, so Mojo cannot follow the PyO3 model.

This proposal files the **new Rust features** that the 2026-11-14 analysis
identified as plannable, plus the **one non-Rust wall** (`derive_insights`
IPC-shape + YAML) that the same analysis surfaced and that should be
recorded here so it is not re-derived as a Mojo/Rust problem. Each slice is
**plan-now / execute-gated**: it is filed now, executed only when its named
trigger fires (or, for S4, as a cheap add-on).

## 2. Current state (exact seams)

| Seam | Location | Today |
|---|---|---|
| Convo exact-KNN matrix | `helpers/misc/convo_query.py:280-330` | exact numpy scan over the Arrow-loaded matrix, cached **per process** (LRU `_MATRIX_CACHE_MAX=2`, ~320 MB f32 each); `warm_matrix()` (convo_query.py:298) for long-lived lanes; every one-shot CLI takes the in-database scan leg |
| PPR | `helpers/graph/onager.py:836` | blocked by an upstream Onager bug — personalisation column ignored, restart node hardcoded to node_id 1, requires a BIGINT weight col; variants A/B produce identical output |
| T1 scale watch | `helpers/validators/graph_scale_watch.py --check` | advisory leg `graph-scale-watch`; WARN @500k, FIRE=exit 1 @1M; live 129,694 `e_all_und` rows = 0.13 of T1 (~8.7× away, ~11 VIGIL intakes) |
| graph_kernels crate | `Rust/graph_kernels/src/lib.rs` | two adopted kernels (`all_pairs_bfs_stats` :199, `limited_bfs_sources` :304) + helpers (`all_pairs_hist_seq` :96, `all_pairs_hist` :123, `stats_from_hist` :166, `limited_bfs_hists` :241); `PARALLEL_THRESHOLD=1024`, `ROOT_CHUNK=256` |
| derive_insights | `helpers/graph/derive_insights.py` | `ProcessPoolExecutor` at :3886 and :3938; `yaml_safe_load` at :141/:201 |

## 3. Evidence (measured 2026-11-09 / 2026-11-14, this box)

**Convo exact-KNN** (203,921×384 live corpus, DuckDB 2.0.0, pyarrow 26.0.1):
Arrow fetch **264 ms**, flatten 41 ms, matmul 86 ms, argsort 25 ms. The
fetch is paid once per process (the matrix is immutable between rebuilds);
the matmul is BLAS-bound. The 2026-11-09 session concluded: "the real
unlock is a PERSISTENT CACHE, not the kernel."

**graph_kernels live** (n=19,130 / nnz=73,676): exact all-pairs = 1.16 s vs
37.6 s incumbent; scratch ~0.3 MB vs 1.75 GB. stats CLI 76.6 s→11.3 s;
trail map 166.8 s→3.56 s (6.35→0.17 ms/source).

**PPR seed-dependence** (proves the lane is not degenerate): Infosys hop≥2
0.038 (ego-local) vs HDFC Bank 0.508 (propagating). The sole blocker is the
Onager bug, not degeneracy. Note: an older convo record ("structurally
degenerate… independent") is superseded by this measurement and by latest
doctrine (`zcode/graph-analytics-integrity`, `zcode/algorithms-sweep-2026-11`:
"Onager hardcodes restart node 1"); do not cite it.

**derive_insights** (2026-11-09 profile): 4.0 s of a 9.83 s run in
`select.poll` (ProcessPoolExecutor idle-wait) + 1.4 s YAML re-parse. This is
an **IPC-shape + YAML** problem, not a Mojo/Rust one.

## 4. Slices

### S1 — Persistent in-memory matrix cache (Rust daemon)

- **What:** a small Rust daemon (or a shared-memory segment) that holds the
  203,921×384 convo embedding matrix and serves exact top-k over IPC, so a
  one-shot CLI no longer pays the 264 ms Arrow fetch.
- **Why:** the per-process fetch is the dominant cost (264 ms of ~416 ms
  total); the matmul is BLAS-bound, so the kernel is not the bottleneck.
- **Trigger:** when the convo lane becomes latency-sensitive (TUI
  in-process expand, an API, or a bench harness). `warm_matrix()`
  (convo_query.py:298) already exists for long-lived lanes; this
  generalizes it cross-process.
- **Effort:** medium (new crate + IPC protocol + invalidation by
  `(path, mtime_ns, size, dims)`).
- **Acceptance:** a one-shot CLI query's total time drops by ~264 ms vs the
  current in-database scan leg; top-k parity with the exact numpy scan
  (same k, recall 1.000).

### S2 — First-party Rust personalised-PPR

- **What:** a first-party Rust PPR kernel (in `graph_kernels` or a new
  crate), or a fix to the vendored Onager, that honours the personalisation
  vector.
- **Why:** the shipped PPR lane is scipy exact
  (`helpers/graph/algorithms.py:1110`, "not via onager — the extension
  cannot serve it"). PPR is seed-DEPENDENT, so a faster first-party solver
  is a genuinely new "why is this node connected / path-explanation"
  surface. (Earlier Onager-bug framing superseded — see §6 trial.)
- **Trigger:** the first path-explanation UI/API ask (the s-walk shortest
  paths + `calc_prop_true_dyad_paths_per_spl` shelf item).
- **Effort:** medium. Cheapest route: a `graph_kernels` kernel with parity
  vs the scipy reference, rather than fixing the Onager extension.
- **Acceptance:** PPR output varies with the personalisation vector
  (seed-dependent), matching the scipy reference on a frozen graph.

### S3 — Rust CSR scale-up builder (re-scoped: substrate already shipped)

- **What:** a Rust builder that reads the graph from DuckDB and writes the
  offline CSR substrate (`csr_offsets/neighbors.bin`, int32, ~420 MB at
  100M rows) + an mmap query path — at scale, not as the initial builder.
- **Why:** the T1 scale-ladder action is "CSR rebuild + ART bridge, NOT a
  rewrite." A Rust CSR builder is the natural implementation at scale.
- **Rescope note:** T1 substrate is ALREADY SHIPPED — `csr_substrate.md:19`
  records 115,264 directed rows built early 2026-09-26 (`helpers/graph/csr.py`).
  S3 is no longer "build CSR for the first time"; it is the measured
  scale-up path when the Python builder becomes the wall.
- **Trigger:** T1 watch firing — `graph_scale_watch.py --check` (FIRE=exit 1
  @1M; live 129,694 rows = 0.13 of T1) AND a measured builder slowdown, or
  the 100M-row substrate target (~420 MB bin).
- **Effort:** low-medium. Plan-now / execute-gated (Phase B).
- **Acceptance:** CSR build completes in a bounded time at the live scale;
  the CSR matches the DuckDB edge set exactly (parity gate).

### S4 — More `graph_kernels` kernels (graph-health instruments)

- **What:** extend the existing `graph_kernels` crate with
  articulation-points / 2-core / biconnected-components kernels.
- **Why:** the igraph second-opinion shelf (algorithms_assessment.md S5)
  lists "articulation_points/coreness/assortativity as cheap health
  instruments" = PARTIAL ADOPT. Cheap graph-health diagnostics riding the
  existing PyO3/rayon lane at in-process speed.
- **Trigger:** when graph health becomes a concern, or as a cheap add-on to
  the existing crate.
- **Effort:** low (extends the existing crate; the BFS kernels are already
  there).
- **Acceptance:** parity with igraph (or a Python reference) on a frozen
  graph; wired into a health-instrument lane.

### S5 — derive_insights IPC-shape + YAML wall (observation, not a Rust feature)

- **What:** an observation recorded so it is not re-derived as a Mojo/Rust
  problem. `derive_insights` spends 4.0 s of a 9.83 s run in `select.poll`
  (ProcessPoolExecutor idle-wait, derive_insights.py:3886/:3938) + 1.4 s
  YAML re-parse (`yaml_safe_load`, derive_insights.py:141/:201).
- **Why:** the cost is in the **IPC shape** (idle-wait between workers) and
  the **YAML re-parse**, not in any kernel. A Rust process pool or a cached
  YAML would help, but that is a Python-architecture change, not a kernel.
- **Trigger:** when `derive_insights` becomes a wall.
- **Effort:** N/A (observation). Candidate fixes: re-shape the IPC (avoid
  the idle-wait), cache the parsed YAML, or move the pool to a Rust-backed
  worker.
- **Acceptance:** N/A (observation). If acted on: `derive_insights` total
  time drops by the `select.poll` + YAML re-parse cost.

### S6 — `limited_bfs_hists` scratch reuse (the per-source O(m) alloc)

- **What (as filed):** reuse per-thread scratch in `limited_bfs_hists`
  (`Rust/graph_kernels/src/lib.rs:241`): every source allocated a full
  `vec![-1i32; m]` dist + 2× `with_capacity(m)` frontiers. Fix: thread-local
  scratch pool or a visited-stamp bitmap (`u32` epoch array, no fill-reset).
- **As built (2026-11-14, kept):** per-source `vec![0u32; m]` stamp replaces
  the `dist` vec; the TLS/epoch variant was built first and lost (see
  Acceptance) — the memset is not the bottleneck, so no reuse.
- **Why:** at T1 (m=1M) that's ~12 MB of alloc+fill per source per worker.
  Source-linearity is already the dominant graph wall (iFUB 38k sweeps = 128 s).
  Sits behind S4's no-build verdict only because Python is 0.26 s today;
  inside Rust this is the remaining kernel-side lever.
- **Trigger:** T1 watch firing, or a measured per-source slowdown in the
  trail lane (`hyper_trails.py:417`).
- **Effort:** low-medium (one kernel function + a proptest pinning parity).
- **Acceptance (met 2026-11-14):** bit-parity with the old kernel on the
  frozen suite (`tests/properties.rs`, 11/11 green). Per-source wall drops
  ~6–7% at 800 sources (166.6–172.1 ms vs 177.8–183.8 ms, 5 interleaved
  rounds, both built today). **Not built as filed:** the thread-local /
  epoch-stamp reuse measured ~5% SLOWER than HEAD at every scale tried
  (22k/100k/300k nodes, 200–800 sources) — the memset premise was false.
  TLS leg closed; do not re-derive. Shipped form: per-source `vec![0u32; m]`
  stamp, `dist` dropped (write-only in this loop — depth is `level`).

### S7 — PyO3 caller-copy discipline at the kernel boundary

- **What:** `helpers/graph/stats.py:190` and `hyper_trails.py:417` call
  `tocsr() + sort_indices() + ascontiguousarray(int32)×2` on *every* kernel
  call. At T1 that's ~10 MB + sort per call, amplified across chain /
  per-component calls. Fix: keep one int32 sorted CSR alive, skip
  `ascontiguousarray` when already contiguous (the kernel takes
  `PyReadonlyArray1`), or push the downcast into Rust.
- **Why:** this is the "jemalloc-type leg" that actually exists — caller
  copies, not allocator overhead (round #468 measured Rust-internal churn at
  176 B/query; the caller copies dominate). A global allocator is the wrong
  fix (see §5).
- **Trigger:** T1 (1M rows), or any caller doing repeated kernel calls over
  the same matrix.
- **Effort:** low (Python-side checks; optional trivial Rust overload).
- **Acceptance (met 2026-11-14):** one shared helper
  `kernel_csr_arrays()` (`helpers/graph/stats.py`), used by both kernel call
  sites (`stats.py:192`, `hyper_trails.py:420`); the zero-copy ingress is
  pinned by `test_kernel_csr_arrays_zero_copy` (identity `is` assert on
  int32 input, dtype assert on int64 input). **Premise corrected on
  measurement:** at 22.3k nodes / 179k nnz the conversion is 0.000 ms vs
  ~170 ms kernel (scipy CSR constructors already yield int32 contiguous —
  `ascontiguousarray` is the identity), so there is no per-call copy to
  eliminate today. The helper keeps it provably zero-copy; the only real
  cast left is int64→int32, which no current caller produces. T1 note:
  even a 10 MB int64 cast is ~1% of a seconds-scale kernel call — this was
  never the wall; the wall is BFS work itself (§S6).

**Return-copy prototype (landed 2026-11-14, kept):** `limited_bfs_sources`
now moves the flat hist buffer into numpy via `Array2::from_shape_vec` +
`PyArray2::from_owned_array` instead of `chunks → Vec<Vec> → from_vec2`.
Bit-parity verified (3 rounds, 800 and 10k sources); wall-clock parity
(171.2 ms vs 172.8 ms clean A/B, both built today). No speedup claimed —
the payload is <1% of call time today; kept for alloc-count hygiene.
Toolchain lesson pinned: rebuilding unmodified 2026-11-09 source today costs
~3% codegen drift, so kernel trials must rebuild the control.

## 5. Closed negatives (do NOT re-derive)

- **Rust cosine** — 0.47–0.49× vs numpy even with rayon; "No Rust cosine
  promotion left to make."
- **HGNN/HyperGAT, KaHyPar, Graphina Rust, EasyGraph C++** — closed.
- **igraph as primary** — shelved, then seated as backup D17 (Leiden,
  weighted centralities/paths, maxflow/mincut, motifs).
- **jemalloc** — closed twice over; its sanctioned replacement is S7
  (caller-copy discipline), not an allocator swap.
- **numba Phase C** — 179k < 500k trigger.
- **HNSW** — deferred (brute cosine remains the shipped path); next check
  when vectors → 1e6.
- **Mojo** — FROZEN for ports 2026-10-26 (`mojo_12_adoptions.md:112`); lane
  re-evaluated 2026-11-13 (`mojo_12_adoptions.md:138`) with parity 92/92,
  unfreeze steps 1–3 executed (#520). Unfreeze trigger = "measured wall neither
  Python nor Rust closes." Sanctioned path = T2 ~10M Mojo BFS promotion on
  a measured >2× crossover; MAX-engine GEMM at 1e5+ embeddings; GPU A–K
  parked until >50 MB resident or a discrete GPU.

## 6. Trial results (2026-11-14, this box)

- **S4 articulation (PASS):** Tarjan on live CSR (`memory/csr/`: n=22,787,
  nnz=179,124, gen 1139134) finds 1,629 APs (7.1%) in **0.264 s**; synthetic
  path/cycle/bowtie parity matches `networkx.articulation_points` exactly.
  Confirms S4 as cheap add-on; a Rust kernel is not needed for speed at
  current scale (0.26 s Python) — only if AP becomes a hot lane.
- **S1 matrix cache (CONFIRMED, re-measured):** `warm_matrix()` = **1.461 s**
  cold, **0.001 s** cached; one-shot CLI total **2.46 s** (`convo_query.py
  "embedding cache" --limit 5`). Cold fetch is larger than the 264 ms
  2026-11-09 figure (corpus grew since) — strengthens the S1 case; trigger
  (TUI/API latency demand) still governs.
- **S2 PPR (CONFIRMED seed-dependent, path corrected):** scipy PPR solve
  (0.34–0.35 s/seed, n=20,705, nnz=112,832): Infosys self-mass 0.2656 vs
  HDFC Bank 0.2025, distinct top-5 — seed-dependent. Correction: the shipped
  lane is scipy exact (`helpers/graph/algorithms.py:1110`, "not via onager —
  the extension cannot serve it"), NOT Onager. S2 should re-target to a
  first-party PPR solver (or graph_kernels kernel) + parity vs the scipy
  reference, not "fix vendored Onager".
- **S5 derive_insights (PARTIAL):** frontmatter parse = **0.3 ms/file**
  (50× `findata/Companies/Technology/Infosys.md` = 0.017 s); `derive_insights.py`
  has 6× `ProcessPoolExecutor` + 2× `yaml_safe_load` — the 1.4 s re-parse
  claim implies ~4,600 files at this rate, plausible for the full corpus.
  Idle-wait share not re-profiled (full run ~9.8 s) — left as observation.

## 7. Follows / related

- `doc/improvements/archive/graph/cosine_knn_production_integration.md`
  (the closed cosine lane this proposal does NOT re-open).
- `doc/local/perf/graph_scaling.md` (the T0–T3 scale ladder; S3 re-scoped to
  CSR scale-up).
- `doc/local/evaluations/algorithms_assessment.md` §9.3 (the adoption shelf
  S1–S8; S4 is the S5 health-instrument item).
- `helpers/graph/algorithms.py:1110` (scipy PPR lane; S2 re-targeted).
- `helpers/misc/convo_query.py:280-330` (the per-process matrix cache; S1).
