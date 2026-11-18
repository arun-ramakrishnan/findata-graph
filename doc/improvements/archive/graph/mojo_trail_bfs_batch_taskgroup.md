---
title: "Batched + TaskGroup Mojo bipartite BFS kernel — integration shape and a reusable acceleration template"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "534"
area: graph
---

# Batched + TaskGroup Mojo bipartite BFS kernel

**Filed:** 2026-11-18 · **Status:** PROPOSED · **completed.md:** (none yet)

## Summary

The 2026-11-18 hyper_trails BFS trial (`bench_data/mojo_hyper_trails_trial_2026-11-18.md`,
gitignored evidence) found two things that are both true and pointed in opposite
directions:

1. The Mojo bipartite BFS **kernel is genuinely fast** — once subprocess spawn and
   per-node text parsing are removed (all sources in ONE process, one compact line
   per source), it runs **~9–10× faster than scipy** at every tested scale
   (live 25k-node graph 5.75 → 0.65 ms/source; 600k nodes / 2.6M arcs
   322 → 33.9 ms/source). Parity was exact 20/20 at all three sizes.
2. The **shipped integration throws that win away**: `_trail_bfs_mojo`
   (`helpers/graph/hyper_trails.py:254`) shells out PER SOURCE and parses ~18k
   lines/source, landing at ~37 ms/source — ~6× SLOWER than scipy. This reproduces
   the 2026-11-09 finding (6.09 vs 6.10 ms/source, `rust_bfs_kernels_stats_trails.md:96`).
   The Rust batched lane that already owns this path is another 2.5–3× faster
   than even the batched Mojo kernel.

This proposal does **not** argue for unfreezing Mojo on hyper_trails (Rust won that
lane; the unfreeze trigger — a wall neither Python nor Rust closes — has not
fired). It treats the trail BFS kernel as the **cheapest existing substrate on
which to prove a reusable engineering pattern**: batch all sources into one
kernel call, fan the sources out across cores with `TaskGroup`, and keep parity
and the default lanes green. The payoff is a measured, transferable Mojo
acceleration template (kernel + fan-out + compact-output contract + parity
harness) that the repo-wide survey (S4) can match against lanes that have NO
Rust kernel.

## Evidence (2026-11-18 trial, this machine, i5-6500 4C/4T)

| graph | scipy | Mojo shipped (per-source spawn+parse) | Mojo batched (one process) | Rust batched (production default) |
|---|---|---|---|---|
| live 25,248 nodes / 119,314 arcs | 5.75 ms/src | ~37 ms/src | 0.65 ms/src (0.11×) | 0.20 ms/src (0.035×) |
| synth 120k nodes / 520k arcs | 45.5 ms/src | ~138 ms/src | 4.30 ms/src (0.095×) | 1.48 ms/src (0.033×) |
| synth 600k nodes / 2.6M arcs | 322 ms/src | ~621 ms/src | 33.9 ms/src (0.105×) | 14.6 ms/src (0.045×) |

Parity: full-dict equality on 20/20 sources (live); reach + hop-sum 20/20 at all
three sizes for both batched lanes. The 2.89–4.00× `TaskGroup` fan-out number is
from `doc/local/mojo/mojo_pilot.md:441-485` / `Mojo/src/common/taskgroup_fanout.mojo`
(4 CPU-bound tasks, 15.6 → 5.4 ms, 4 logical cores).

## Slices

**S1 — batched multi-source kernel + compact output contract (no fan-out yet).**
New `Mojo/src/bench/bfs_bipartite_all_batch.mojo`: read the S2 CSR bins once,
accept a list of source indices as argv, return per source a single deterministic
`SRC REACH HOP_SUM` line (sorted by source id), no per-node output. Same BFS
semantics as `bfs_bipartite_all.mojo` (even bipartite distance only, self
excluded, `limit` capped). Parity driver shells the binary once for all sources;
20-source × 3-size parity vs scipy. This closes the subprocess/parse gap and
pins the output contract S2/S3 depend on.

**S2 — TaskGroup fan-out across sources.**
Inside the S1 binary, partition the source list across `n_workers` (default =
`num_logical_cores()`, env/argv override), one `async def` task per worker,
each with DISJOINT `dist`/`cur`/`nxt` int32 buffers (no locks needed — CSR read
state is shared, frontier state is per-worker). `tg.create_task` for non-raising
coroutines, results into per-worker pointer slots, `tg.wait()` as the barrier
(`taskgroup_fanout.mojo` API contract). Per-worker reach/hop-sum written to
slots, reduced to the S1 output contract in source order. Measure sequential
vs fan-out wall at each size; record the real speedup (do NOT assume 2.89× —
the BFS inner loop has different memory pressure from the probe's arithmetic).

**S3 — opt-in Python integration + parity gate.**
Wire an opt-in lane in `helpers/graph/hyper_trails.py` that batches ALL sources
through the S2 binary in one call (mirroring the Rust `TRAIL_BFS_RUST` shape:
env var read at CALL time, default OFF, Python/Rust default stays untouched).
Return byte-compatible per-source stats (`ms` = amortized share, `reachable`,
`hop_histogram` — note the compact contract returns reach + hop-sum only, so
the histogram leg either needs a compact per-hop count vector or stays on the
rust/scipy lanes; decide in S1 by the contract). Add hermetic tests in
`tests/test_hyper_trails.py`: parity vs scipy, env-off default, fallback on
missing binary / rc≠0. No gate/perf leg changes — developer-loop only, consistent
with `rust_bfs_kernels_stats_trails.md:30-39`.

**S4 — repo-wide usecase survey.**
Query `doc_search` / `script_search` / `ripwire` for compute-heavy inner loops
that (a) have NO first-party Rust/graph_kernels equivalent, (b) process a batch
of independent items so one kernel call amortizes startup, and (c) are not
GIL-bound bridge work (TaskGroup gains nothing when tasks mostly call Python —
`multiproc_bridge.mojo:5`). Rank candidates by measured wall/pressure and list
the ones where a 9–10× kernel edge would be load-bearing. Deliverable: a short
survey section in this proposal (or a follow-up proposal per candidate), not
code.

## Acceptance criteria

- Full parity (reach + hop-sum, 20 sources, 3 sizes) vs scipy for S1 and S2;
  S3 hermetic parity vs the existing 14-test trail suite.
- S2 fan-out must show a MEASURED wall improvement over the S1 sequential
  batch at ≥1 size; if it does not, record the negative and do not wire S3.
- Default lanes (scipy when `TRAIL_BFS_RUST=0`, Rust batched otherwise) unchanged;
  opt-in env default OFF.
- `make qa` non-regression on touched files (targeted tests only during the
  fix loop per `doc/procedures/gates.md`); `make mojo-build`, `make mojo-test`,
  `make mojo-parity` green.

## Risk / doctrine notes

- No query-visible semantics change (perf-only, opt-in) → no `ontology_eval_gate`
  bullet required; flagged explicitly per the proposals/README house rule.
- TaskGroup contracts: non-raising `async def`, no futures (pointer slots),
  `wait()` is the barrier, per-worker disjoint buffers (no race). `fn` is removed
  in Mojo 1.2 — `def` only (scratch build confirmed).
- Freeze posture (`doc/improvements/archive/tooling/mojo_12_adoptions.md`):
  UNAFFECTED. This is pattern validation + survey, not an unfreeze; the trail
  lane's wall stays closed by Rust.
- Survey must respect "thesis first — search before you claim" (AGENTS.md):
  cite `path:line` / hit counts for every "no Rust kernel exists" claim.

## Status 2026-11-18 — S1–S3 implemented and validated, S4 survey done

**S1 + S2 landed (code):** `Mojo/src/bench/bfs_bipartite_all_batch.mojo` —
read bins once, all sources in one process, compact `SRC REACH H0 H1 ...`
output (sorted source ids); workers=1 sequential (S1), workers>1 TaskGroup
fan-out over disjoint per-worker frontier buffers (S2). Built to
`Mojo/bin/bfs_bipartite_all_batch`. `def`-only, `unsafe_offset(i)[]` pointer
access (Mojo 1.2); `mojo format` clean.

Measured S1/S2 (20 sources, 2 reps best, parity 20/20 vs scipy at every worker
count and size — full reach + per-hop histogram):

| graph | w1 sequential | w2 TaskGroup | w4 TaskGroup | fan-out w4 speedup |
|---|---|---|---|---|
| live 25k nodes / 119k arcs | 18.65 ms | 14.73 ms | 13.93 ms | 1.34× |
| synth 120k / 520k arcs | 89.79 ms | 53.56 ms | 35.29 ms | 2.54× |
| synth 600k / 2.6M arcs | 536.61 ms | 369.82 ms | 263.69 ms | 2.03× |

Positive at all sizes → S3 wired. (Fan-out < the probe's 2.89× because the BFS
inner loop is memory-bound on disjoint frontier buffers, not pure arithmetic.)

**S3 landed (code):** `_trail_stats_mojo_batch` in
`helpers/graph/hyper_trails.py` — opt-in `MOJO_TRAIL_BATCH=1`, tried ahead of
Rust in `trail_source_stats_parallel`, default OFF, returns byte-compatible
per-source stats (full hop_histogram preserved via the compact contract),
falls back to Rust/scipy on missing binary / rc≠0 / timeout. Tests added in
`tests/test_hyper_trails.py::TestMojoTrailBatch` (default-off, parity+order,
fallback). `tests/test_hyper_trails.py`: 24 passed, 1 skipped. `ruff check` /
`ruff format --check` clean; static checks pass; proposal lifecycle check clean.
`make mojo-parity`: 92/92 keys, GOLDEN PARITY OK (untouched lane).
`make mojo-build` is RED on this host for a PRE-EXISTING reason unrelated to
this work: `Mojo/src/bench/analyzer.mojo:22-24` references
`std.sys.info.has_{nvidia,amd,apple}_gpu_accelerator`, absent in Mojo 1.2.0
stable. The new kernel builds clean in isolation (`mojo build ... -o`), and
the binary is already built; fixing analyzer.mojo is a separate arc (do NOT
fold into this proposal).

**S4 survey — lanes with NO first-party Rust kernel, ranked by measured wall:**

`graph_kernels` (abi3) exports only 5 symbols: `all_pairs_bfs_stats`,
`limited_bfs_sources`, `personalized_pagerank{,_batch,_vec}`. Together with
`cosine_knn`, Rust owns: cosine KNN, BFS/graph-stats, PPR. Those are NEGATIVE
candidates (no gap to fill). Positive candidates, from `script_query` /
`master_query` + live measurement:

1. **`helpers/graph/a2_candidates.py:71 a2_kernel`** — STRONGEST. `B·Bᵀ`
   top-K over the supplier incidence matrix. Sparse product is C, but the
   result extraction is a pure-Python **O(n²) double loop** (`:88-99`) over
   6,780 suppliers × 6,780 + a full Python sort. **Live measured 4,607 ms**
   (`load_supplier_incidence()`: 6,780 suppliers / 9,789 consumers / 18,293
   nnz). No Rust kernel (not in `graph_kernels` symbols). A Mojo batched
   kernel doing the dense `shared` matrix top-K in one process with TaskGroup
   row-partition is a direct fit: the work is an independent-per-row scan with
   a final reduction — the exact S1/S2 shape. (Note: at 46M cells the dense
   materialization itself may be the real wall; S1 should measure whether a
   row-streaming top-K beats it before porting.)
2. **`helpers/graph/l1_betweenness.py` / `l1_closeness.py`** — Brandes on the
   2-core + closed-form forest terms; `l1_betweenness.py:7-9` documents
   "scipy has no Brandes and no engine escapes Brandes at this V-count
   (igraph 37.7 s / Onager 48.9 s full-graph)". No Rust kernel. Algorithm is
   intricate (folded-tree accumulators) → higher port cost than A².
3. **`helpers/graph/co_supply.py`** — shared-supplier buyer-pair scoring
   (co_supply_peer_lane S1); report-only, prereg-gated promotion. Bounded
   candidate but lower measured wall than A²; verify before committing.

**S4 addendum (2026-11-18) — master_query sweep + live walls.** A follow-up
`master_query` sweep (queries: "nested double loop O(n squared) matrix pairwise
scoring", "monte carlo bootstrap random sampling", "graph centrality betweenness
closeness", "string similarity ... corpus sweep") plus `git_query` for prior
decisions confirmed the shortlist rather than surfacing a new lane. Measured
live walls, read-only, this machine (i5-6500 4C/4T):

| candidate | symbol | live wall | fan-out unit | no Rust kernel? |
|---|---|---|---|---|
| A² kernel | `helpers/graph/a2_candidates.py:71 a2_kernel` | 3,958–4,607 ms | independent (i,j) row blocks | yes |
| motifs null | `helpers/graph/hyper_motifs.py:121 configuration_null` | 651 ms / 3 runs | independent Monte Carlo replicates | yes |
| signal sweep | `helpers/research/signal_sweep.py:175 sweep` | not timed (nested per-date × mode × horizon, C901) | partial | yes |
| motifs pairs | `helpers/graph/hyper_motifs.py:57 pair_shared_counts` | 131 ms (4,547 edges / 59,570 memberships) | partial (shared accumulator) | yes |
| co-supply | `helpers/graph/co_supply.py` load/score | 266 ms / 15 ms | yes but DuckDB-load dominated | yes |
| Brandes L1 | `helpers/graph/l1_betweenness.py` | docs cite igraph 37.7 s / Onager 48.9 s full-graph | moderate | yes |

Ruled OUT (already Rust-owned): cosine KNN, BFS/graph-stats, PPR —
`graph_kernels` exports only `all_pairs_bfs_stats`, `limited_bfs_sources`,
`personalized_pagerank{,_batch,_vec}`, plus `cosine_knn`. The fuzzy/name-dedup
cluster (`helpers/core/fuzzy_duckdb.py`, `name_dedup.py`, `entity_identity.py`)
is DuckDB-pushdown-owned, not a raw compute loop.

Operator ruling (m01277): "forget the wall and lets use scaling if need be for
these 2 cases" — spike targets are **A² kernel** and **hyper_motifs null**,
evaluated by scaling curves (matrix order n / replicate count R) rather than a
single live wall. Each gets its own proposal (house rule) before multi-slice code.

**Each candidate requires its own proposal** (house rule) before multi-slice
code. This proposal's S1–S3 pattern (batched kernel + disjoint-buffer
TaskGroup fan-out + compact output + parity harness) is the reusable template.

## Worktree / landing note

Filed + implemented in the main checkout per the operator's current stgit arc
(`db_fixes` top patch). Full gate arc (`make qa`/`advisory`/`perf`) NOT run —
per `doc/procedures/gates.md` that needs the operator's explicit go and is ONCE
per arc. No commit/stg/patch ops performed by the agent; all changes left dirty
for operator staging. Proposal stays `status: proposed` until a gate arc runs
and the operator elects to archive.
