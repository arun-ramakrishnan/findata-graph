---
title: "Mojo CSR-BFS kernel for the shortest_path_bfs perf leg — pre-flight NO-GO (the reshape dissolved the budget risk)"
status: executed
filed: '2026-11-22'
executed: '2026-11-22'
completed_md: "559"
area: helpers/graph/csr.py, helpers/graph/query.py, tests/bench_shortest_path.py, Mojo/src, Mojo/bin
---

# Proposal: Mojo CSR-BFS kernel (shortest_path_bfs perf leg)

**Date:** 2026-11-22 · **Status:** EXECUTED (pre-flight only; verdict NO-GO
— the kernel was never built. The 2026-11-21 bench reshape dissolved the
motivating budget risk before this arc wired anything; post-reshape the
leg runs at 19% of budget. Reopen trigger recorded in pending.md.)
**Area:** the CSR lane behind helpers/graph/query.py:3660
(_shortest_path_bfs) + helpers/graph/csr.py + a new Mojo kernel under
Mojo/src/ + tests/bench_shortest_path.py.

**Follows:** the 2026-11-21 acceleration-candidate survey (canonical
findings record doc/local/mojo/mojo_pilot.md); the 2026-11-21 bench
reshape spike (tests/bench_shortest_path.py header); the motifs
promotion house pattern (MOTIF_NULL_MOJO env-flip + fallback
contract).

## 1. Motivation — with a menu re-baseline

Re-baseline first, because half the survey's Tier 1 is already stale:

- **l1 Brandes family — OUT (Rust-owned).** The survey listed
  l1_betweenness + l1_closeness as the top Mojo candidate, but the
  Rust graph_kernels kernels are LIVE in both lanes:
  brandes_fold_sources (l1_betweenness.py:380) and
  core_bfs_sources_batch (l1_closeness.py:330), an "auto" selector
  (l1_betweenness.py:337) with L1B_RUST / L1C_RUST overrides, gated by
  tests/bench_l1_kernels.py (parity 1e-9, speedup floor 1.5, ceiling
  240 s). A Mojo Brandes port would be a third implementation of an
  already-accelerated lane — declined.
- **Tier 2 (Hy-MMSBM fit_s) — stays measure-first watch** (never
  baselined; survey's own caveat).
- **shortest_path_bfs — the remaining Tier-1 item, taken here.** The
  leg sits at 98.8% of its 12.0 s budget (11.9 s measured,
  tests/run_perf_benchmarks.py:369). Timing failures park with no
  reruns (gates doctrine), so a bust under contention is expensive.
- The spike already ran: the 2026-11-21 reshape measured the
  lane-exhaustion case at 147-149 ms (CSR lane, no DB in the hot loop)
  vs 61-65 ms for the Mojo binary — ~2.3× with the subprocess cost
  INCLUDED. The win is perf-gate risk-reduction, exactly the survey's
  framing.

## 2. Design

- **Kernel:** a Mojo/bin BFS over CSR arrays — indptr/indices in as
  binary arrays, hop distances/path out; ONE src/dst pair per call
  (the production shape). Pure Mojo, no std.python bridge, so the
  venv-PATH runtime resolution trap does not apply. Python owns CSR
  construction, freshness, and canonicalisation; the kernel computes
  only (the batched-kernel ground rule, single-source variant).
- **Dispatch:** opt-in env-flip SP_BFS_MOJO=1 at the CSR-lane dispatch,
  behind the generation-freshness check (freshness stays Python-side —
  a stale CSR substrate must still fall back, as it does today).
  Fallback contract per the motifs house pattern: missing binary,
  rc≠0, timeout, or any exception → incumbent Python CSR BFS.
- **Promotion is a separate arc:** opt-in → opt-out flip (absent =
  Mojo, "0" = Python oracle) only after a full gate arc on the
  operator's go — the motifs promotion shape, not this proposal.

## 3. Slices

1. **S1 kernel + fixture parity:** 1e-6 house epsilon vs the Python
   oracle on fixtures covering src==dst, unreachable pairs,
   multi-component graphs, and the as_of/edge_label filter shapes
   (filters resolve Python-side before CSR, so the kernel sees a plain
   CSR — pin that boundary).
2. **S2 dispatch + fallback tests:** forced-fallback shapes mirror the
   bench's SQL-fallback case (missing binary, nonzero rc, timeout).
3. **S3 bank:** bench_data/mojo_csr_bfs/ multi-pass bank — the ±20%
   DRAM-bound variance rule means no single pass is evidence; include
   a contended-box pass, not only idle.
4. **S4 perf-leg re-baseline:** rebuild Mojo/bin first (Mojo/bin goes
   silently stale after Mojo/src edits — no staleness gate exists;
   check mtimes), rerun tests/bench_shortest_path.py, record the wall
   delta. Budget recalibration of the 12.0 s entry is the operator's
   call (perf legs park; no reruns).
5. **S5 promotion arc:** separate proposal/arc on operator go.

## 4. Risks

- Subprocess spawn cost under desktop load: the spike's 61-65 ms
  already includes it, but the ±20% rule demands the contended pass
  before any promotion claim.
- Binary staleness: no hard gate exists — the dispatch should print an
  mtime hint in verbose mode and the bank records bin mtime alongside
  walls.
- No new make targets (the Makefile help-completeness law makes each
  target a ~4-line contract) — the bench lives in tests/ like
  bench_shortest_path.py.
- Any new .mojo file must be formatter-clean (mojo format has no
  --check; the lint gate byte-diffs a formatted copy).

## 5. Acceptance

1. Parity 1e-6 on fixtures and sampled live pairs, including the
   forced-fallback shapes.
2. bench_data/mojo_csr_bfs/ bank recorded (idle + contended, multi-pass).
3. shortest_path_bfs leg wall reduction demonstrated; targeted tests
   green; no default-path behavior change while SP_BFS_MOJO is unset
   other than through the explicit opt-in.

## 6. Out of scope

- Mojo Brandes (Rust-owned, see §1), the embeddings workflow (iGPU
  crossover not met), TaskGroup fan-out for this shape (single-pair
  calls have no fan-out), Tier-2 fit_s (measure first), and any change
  to the CSR build or freshness contract.

## 7. Pre-flight findings (2026-11-22) — verdict NO-GO, kernel not built

- **The motivating number was stale before this arc ran.** The 98.8%
  budget figure (11.9 s / 12.0 s) is from the 2026-11-21 survey — and
  the bench was RESHAPED the same day (tests/bench_shortest_path.py
  header: three cases with per-case budgets, best-of-3). The first
  post-reshape measurement, run for this arc:
  **2.28 s total wall against the 12.0 s leg budget — 19% utilization**
  (cases: default 0.6 ms, lane-exhaustion unreachable 150.3 ms vs its
  250 ms budget, SQL fallback 85.8 ms vs 2 s; all `[OK]`). The
  gate-timing history confirms the reshape has not yet been gate-run
  (newest entry 2026-11-19, all pre-reshape; 4/10 FAIL(contended)
  historically — the flake class the reshape targets).
- **Residual value, weighed and declined**: the kernel's only remaining
  win is production latency on the rare unreachable-pair query — the
  spike measured 61-65 ms (Mojo binary, subprocess included) vs
  147-149 ms (CSR lane), ~2.3× on ONE query shape. Against the wiring
  cost (a new kernel + dispatch + parity surface + fallback contract +
  a promotion arc), the no-new-moves-without-a-measured-wall doctrine
  declines it — the same third-implementation logic this proposal's §1
  applied to decline the Brandes item.
- **What survived**: the §1 menu re-baseline (Brandes is Rust-owned via
  graph_kernels; Tier-2 fit_s stays measure-first) stands as the
  authoritative acceleration-menu state. Budget recalibration of the
  12.0 s entry is recommended for the next natural perf arc (operator
  call — perf legs park). Reopen trigger (pending.md, dated 2026-11-22):
  the lane-exhaustion case regressing toward its 250 ms budget under
  real load, or a consumer needing faster unreachable-pair answers.
