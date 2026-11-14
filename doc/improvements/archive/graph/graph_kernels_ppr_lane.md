---
title: "graph_kernels PPR lane — first-party personalized-PageRank kernel wired into the scipy bridge"
status: executed
filed: "2026-11-14"
executed: "2026-11-14"
completed_md: "523"
area: "Rust/graph_kernels + helpers/graph/scipy_bridge.py"
---

# graph_kernels PPR lane

**Date:** 2026-11-14 · **Status:** EXECUTED (kernel + lane + all hardening
slices landed same-day, measured; `completed.md` #523) ·
**Area:** `Rust/graph_kernels`, `helpers/graph/scipy_bridge.py`

## 1. Context

The rust-kernel proposal's S2 (first-party PPR) stayed trigger-gated on
"first path-explanation UI/API ask" while the shipped lane solved
`(I − αPᵀ)x = (1−α)v` with `spsolve` (`personalized_pagerank_scores`,
`helpers/graph/scipy_bridge.py`). Same-day prototype (2026-11-14,
completed.md #522 follow-up 2) changed the calculus twice:

1. **It is ~10× faster per seed on the live projection.**
   `personalized_pagerank(indptr, indices, seed, alpha, tol, max_iter)`
   — power iteration on binary CSR, dangling rows teleport to seed,
   `x' = α·Pᵀx + (1−α)v` from `x0 = v`, L1 tol 1e-12 — vs the spsolve
   solve: **42.9 ms vs 442 ms per seed (10.3×)**, maxdiff **1.69e-13**,
   self-mass identical (HCL Infosystems 0.1521 both), n=22,204 /
   nnz=126,916, 5 seeds × 5 interleaved rounds.
2. **The scipy exact solve is structure-fragile.** On unstructured
   random graphs the `(I − αPᵀ)` solve blows up in fill-in: COLAMD
   7.2M LU nnz at n=6,000, **hangs >240 s at n=22,300**;
   MMD_AT_PLUS_A is 3.3× better but still minutes-scale. The live
   projection's sector-block structure is the only reason spsolve runs
   at 0.44 s. The kernel lane is LU-free — cost is
   structure-independent (20–45 ms/seed across synthetic and live
   graphs). A rebuilt or cross-sector projection could silently fall
   into the fill-in trap; the kernel lane cannot.

## 2. Landed 2026-11-14 (prototype + lane)

- `Rust/graph_kernels/src/lib.rs`: `personalized_pagerank` pyfunction +
  pure `ppr_power_iterate()` core. Validation mirrors
  `limited_bfs_sources` (malformed CSR / out-of-range seed / α ∉ (0,1) /
  tol ≤ 0 / max_iter < 1 → ValueError). Duplicates and self-loops are
  handled (they inflate row degree and entries consistently).
- `helpers/graph/scipy_bridge.py`: `personalized_pagerank_scores` now
  tries `_ppr_kernel_lane(A, seed_idx, alpha)` first and falls back to
  `_ppr_spsolve_lane` (the original body, extracted verbatim).
  Kernel-lane guards: extension import fails, stale `.so` without the
  symbol, shape/nnz beyond int32, **non-binary data** (`np.all(A.data
  == 1)` — the kernel is topology-only; weighted CSR keeps scipy).
  `_assert_scipy_lane`/`_assert_budget` (PPR_LANE, 5.0 s) still wrap
  whichever lane ran — ROUTING ownership semantics unchanged.
- Tests: `test_ppr_kernel_matches_scipy_spsolve`
  (tests/test_graph_stats.py — 6 random shapes vs an independent
  spsolve reference, stale-`.so` skip guard), `ppr_tests` Rust unit
  trio, `test_ppr_kernel_lane_engages_and_weighted_falls_back`
  (tests/test_scipy_bridge.py — lane engagement + weighted fallback +
  uniform-×2 renormalization parity). Suites: test_scipy_bridge 52/52,
  test_graph_stats 22/22, Rust 9+5+0.

## 3. Slice ledger (all landed same-day, 2026-11-14 second arc)

- **S1 batch API — LANDED**: `personalized_pagerank_batch(indptr,
  indices, seeds, alpha, tol, max_iter) -> float64[k, n]` — rayon across
  seeds, zero-copy return, 2^28-cell cap with chunk guidance. Bridge:
  `personalized_pagerank_scores_multi(A, names, seeds)` (kernel-first,
  scipy loop fallback, KeyError on unknown seeds). Measured live (k=10):
  **16.7 ms/seed** (beats the 44 ms single — rayon overlaps solves),
  batch-vs-single parity **0.00e+00**.
- **S2 budget/baseline — no change**: LANE_BUDGETS[PPR_LANE] stays 5.0 s
  (measured 42.9 ms single / 16.7 ms batch ≪ 5 s).
- **S3 sector_scope interplay — PINNED**:
  `test_ppr_cohort_slice_kernel_lane` (tests/test_graph_stats.py) —
  kernel vs scipy on arbitrary binary sub-CSR slices, the sector_scope
  shape.
- **S4 warm-up discipline — RECORDED** in
  bench_data/graph_kernels_ppr/README.md (rebuild + .so copy; stale
  guards skip, not fail).
- **S5 set-teleport — LANDED**: `personalized_pagerank_vec(indptr,
  indices, teleport, alpha, tol, max_iter)` — the core refactored to an
  arbitrary teleport vector; dangling rows redistribute ∝ v (the ONLY
  convention reducing to the one-hot scipy-parity lane — uniform
  redistribution would break parity; consequently PPR is linear in v
  only on dangling-free graphs, pinned by
  `ppr_linear_in_teleport_vector_no_dangling`). Python parity:
  `test_ppr_vec_kernel_matches_dense_reference` (dense independent
  solve, 5 random shapes). Note-style ask measured: uniform v over 10
  HDFC names → coherent group neighborhood in **50 ms**.
- **S6 query helpers — LANDED** (bridge): `ppr_alpha_curve` (#2 below)
  and `ppr_pair_asymmetry` (#4 below), tests
  `test_ppr_alpha_curve_hub_closed_form` (star hub self-mass = 1/(1+α),
  closed form) and `test_ppr_pair_asymmetry_leaf_pulls_hub`.

## 4. Acceptance criteria

- Parity: kernel lane vs spsolve reference maxdiff < 1e-8 on random
  binary graphs (directed, symmetric, dangling, self-loop variants) —
  pinned by test.
- Fallback: weighted / missing-extension / stale-`.so` inputs take the
  scipy solve and produce the same answers (uniform-reweight parity
  test pinned).
- Budget: PPR lane wall-clock under LANE_BUDGETS[PPR_LANE] = 5.0 s at
  live projection scale (measured 42.9 ms — 100× headroom).
- ROUTING unchanged: lane stays `pagerank_personalized → SCIPY`; the
  kernel is an solve-engine swap inside the lane, not a routing flip.

## 5. Trials evidence

| trial | result |
|---|---|
| live-projection A/B (n=22,204, nnz=126,916) | kernel 42.9 ms/seed, spsolve 442 ms/seed, **10.3×**, maxdiff 1.69e-13 |
| self-mass parity | HCL Infosystems 0.1521 = 0.1521 |
| spsolve fill-in (unstructured random) | COLAMD 7.2M LU nnz @ n=6,000; >240 s hang @ n=22,300 |
| spsolve fill-in (MMD_AT_PLUS_A) | 2.24M LU nnz @ n=6,000 (3.3× less, still minutes @ 22k) |
| kernel across structures | 20 ms (358k-nnz synthetic) – 43 ms (127k-nnz live) — structure-independent |
| batch (k=10, live PPR projection n=20,705) | **16.7 ms/seed**, batch-vs-single maxdiff 0.00e+00 |
| set-teleport (uniform v, 10-name group) | **50 ms**, coherent group top-8 |
| α-curve (6 points, live) | **0.28 s**/seed — HCL Infosys 0.502→0.051, HDFC Bank 0.540→0.079 (α 0.5→0.95) |
| pair asymmetry (live) | TCS parent↔TCS BS GmbH **ratio 90.7×** (0.00088 vs 0.0796), 65 ms/pair |
| suites | test_scipy_bridge 53/53 · test_graph_stats 25/25 · Rust 12+5+0 |

## 6. Cross-sector signal history — what this lane does and does not fix

The 2026-11-06 PPR trial series
(`../database/ppr_results_table.md` §7) documented the
cross-sector signal breakdown on the global projection, fixed by three
operator-driven projection passes:

- **Pass 0** — full projection: `india` #2 for HDFC Bank (`listed_in`,
  930 edges to one country hub, routed seed mass to home-market peers).
- **Pass 1** — drop `listed_in`: top-10s became IT majors regardless of
  seed sector (`co_mentioned_in`, 9,638 edges — newsletter mention
  VOLUME; high-volume mentioners hoovered mass).
- **Pass 2** — include-only 9 relational types: ranking flipped
  sector-coherent (banks seed banks; CRISIL → BSE/Oracle/CDSL;
  3M India → industrials); 66 mutual-fund rows leaked via `invested_in`.
- **Pass 3** — drop `invested_in`: final 8 company↔company types; plus
  §5c `sector_scope` hybrid (pure per-sector cut DECLINED — it deletes
  cross-sector structure; CRISIL → Oracle Financial was the best output).

**This kernel lane does NOT change any of that signal semantics — by
design.** It is parity-exact (1.69e-13): running it on the Pass-0/1
projections reproduces those polluted rankings bit-for-bit. Projection
composition lives ABOVE the solve engine, and its fix (§7 include-set +
sector_scope) is already shipped. What the lane changes about this
failure class:

1. **The debugging method gets ~10× cheaper per iteration** (44 ms vs
   442 ms/seed; 16.7 ms/seed batched) — the §7-style pass-by-pass
   operator loop becomes leave-one-type-out exhaustive ablation (8
   types × 50 seeds ≈ 7 s batched): composition questions get answered
   by measurement, not by operator passes.
2. **A wall-time failure mode those passes never hit but could have**:
   spsolve fill-in blowup triggers on UNSTRUCTURED graphs (§5 evidence);
   the live projection's sector-block structure is what keeps scipy at
   0.44 s. A rebuilt/merged/cross-sector projection losing block
   structure would hang spsolve or trip the 5 s LANE_BUDGETS guard —
   masking a signal question as a runtime refusal. The kernel lane is
   LU-free: composition experiments cannot be blocked by graph shape.
3. **Seed-localization (§5b: Infosys 0.4257 self-mass vs HDFC 0.163) is
   projection/scope shape, not solve speed — unchanged.** The shipped
   lever stays sector_scope; the α-curve (below) now measures the trait
   per seed instead of leaving it a one-point observation.

## 7. Query types unlocked (with first measurements, 2026-11-14)

Raw runs + rerun scripts: `bench_data/graph_kernels_ppr/`
(`new_surfaces.json`, `ppr_route_run.json`, `fillin.json`).

1. **Population-scale sweeps** — every entity as seed: 20,705 × 16.7 ms
   ≈ 6 min batched (vs ~2.5 h spsolve). Materialized full-PPR table and
   per-sector neighborhood maps become affordable. [LANDED: batch API]
2. **α-sensitivity curves** — self-mass vs α per seed operationalizes
   the §5b localization concern as a measurable trait. First live
   curves: HCL Infosystems 0.502→0.051, HDFC Bank 0.540→0.079 across α
   0.5→0.95 (0.28 s per 6-point curve). [LANDED: `ppr_alpha_curve`]
3. **Edge-type attribution / projection composition search** —
   leave-one-type-out over the 8 relational types + rank churn per
   seed; cross-projection diffs (economic 12-type vs relational 8-type
   at population scale). [LANDED 2026-11-14: harness
    `helpers/graph/ppr_edge_attribution.py` — batch-API composition (no
    new kernel/route), cohort even-spread across the sorted relational
    projection, per-seed diff metrics (top-k Jaccard, normalized rank
    displacement, score L-∞ shift, self-mass delta). First live run
    (memory/research.db, α 0.85, k=10, cohort 20, wall 8.95 s):
    `same_group` ablation is the largest disruptor (Jaccard μ 0.470,
    rank displacement μ 0.402 — 18/20 scored), `subsidiary_of` next
    (0.832), `customer_of`/`acquired`/`jv_with` near-inert (≥0.955);
    cross-projection economic-vs-relational Jaccard μ 0.973 (20/20).
    Data: `bench_data/graph_kernels_ppr/edge_attribution.json` +
    `edge_attribution_report.md`; perf leg `ppr_edge_attribution` in
    `tests/run_perf_benchmarks.py` (live, 90 s budget).]
4. **Directed proximity asymmetry** — PPR(A→B) vs PPR(B→A): first live
   pairs are dramatic — TCS parent↔TCS Business Services GmbH ratio
   **90.7×** (the sub holds 7.96% of its relational field on the
   parent; the parent 0.09% on the sub — degree-normalization
   asymmetry on a SYMMETRIC projection). 65 ms/pair.
   [LANDED: `ppr_pair_asymmetry`]
5. **Note-driven set-teleport** — a note's mentioned companies as one
   teleport set: "the relational neighborhood of everything this
   newsletter mentions" — the note picks where mass lands without
   polluting the walk (no co_mentioned_in hub effect). First run:
   uniform v over 10 HDFC names → coherent group top-8 in 50 ms.
   [LANDED: `personalized_pagerank_vec`]
6. **Hover/preview API asks** — 44 ms/seed single, 17 ms/seed batched:
   a "relational neighborhood" response now fits inside an API request
   budget (the revival gate's named consumer,
   `scipy_personalized_pagerank.md` §7). [Servable
   now; no consumer ask yet]

## 8. Q3 edge-type attribution harness — follow-up landing (2026-11-14)

Query-3 of §7 (the only one still marked "analysis harness not built") now
has that harness. It is a composition of landed surfaces — no new kernel,
no new routing lane, read-only on the store.

**What was built** — `helpers/graph/ppr_edge_attribution.py`:

- cohort = even spread over the sorted relational projection (20,705
  entities) when `--seeds` is absent — deterministic, population-scale;
- per-seed diff metrics vs the full relational 8-type base: top-k
  Jaccard, normalized rank displacement (mean |Δrank| over the union
  ranking / k), score L-∞ shift (absent name = 0), self-mass delta;
- leave-one-type-out ablations over all 8 `PPR_INCLUDE_EDGE_TYPES`,
  plus the cross-projection treatment (economic 14-type
  `onager.ECONOMIC_EDGE_TYPES` vs relational 8-type);
- seeds orphaned out of an ablation (no included edge of that seed) are
  reported in `cohort.dropped_seeds`, never fatal;
- outputs: `bench_data/graph_kernels_ppr/edge_attribution.json` +
  `edge_attribution_report.md` (gitignored with all of `bench_data/` —
  `.gitignore:158`; regenerate with `python3
  helpers/graph/ppr_edge_attribution.py`, ~9 s warm).

**First live run** (memory/research.db, α 0.85, k=10, cohort 20, wall
8.95 s):

| drop | Jaccard μ | Jaccard min | rank disp μ | max shift μ | scored |
|---|---|---|---|---|---|
| same_group | 0.470 | 0.111 | 0.402 | 0.0557 | 18/20 |
| subsidiary_of | 0.832 | 0.538 | 0.118 | 0.0187 | 16/20 |
| semantic_peer | 0.858 | 0.333 | 0.087 | 0.0150 | 19/20 |
| competes_with | 0.887 | 0.333 | 0.071 | 0.0053 | 20/20 |
| supplier_to | 0.905 | 0.333 | 0.076 | 0.0126 | 20/20 |
| jv_with | 0.955 | 0.818 | 0.017 | 0.0044 | 20/20 |
| acquired | 0.973 | 0.818 | 0.004 | 0.0000 | 20/20 |
| customer_of | 0.991 | 0.818 | 0.000 | 0.0000 | 20/20 |

Cross-projection (economic vs relational): Jaccard μ **0.973** (20/20),
rank disp μ 0.023. Reading: `same_group` is by far the most
neighborhood-defining relational type; `subsidiary_of` second; the
membership-adjacent `customer_of`/`acquired`/`jv_with` are near-inert at
top-k — and the economic projection's extra six types barely move
top-k neighborhoods (the verdict matches §6: composition effects live
above the solve engine).

**Acceptance evidence** — `tests/test_ppr_edge_attribution.py` 8/8
(synthetic sqlite store through the live projection path; semantics,
bounds, determinism, orphan handling, empty-cohort refusal); perf leg
`ppr_edge_attribution` registered in `tests/run_perf_benchmarks.py`
(live, 90 s budget; run wall 5.53 s); ruff check/format clean;
md-lint clean. Recorded in completed.md #523 (follow-up bullet) and
archive/README.md entry.
