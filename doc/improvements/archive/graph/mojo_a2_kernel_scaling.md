---
title: "A² supplier-peer kernel — Mojo scaling spike with parity oracle"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "535"
area: graph
---

# A² supplier-peer kernel — Mojo scaling spike

**Filed:** 2026-11-18 · **Status:** PROPOSED · **completed.md:** (none yet)

## Summary

`helpers/graph/a2_candidates.py:71 a2_kernel` computes `B·Bᵀ` over the supplier
incidence matrix. The sparse product itself is C (scipy), but the result
extraction is a pure-Python **O(n²) double loop** (`:88-99`) over all supplier
pairs plus a full Python sort. At live scale (6,780 suppliers / 9,789 consumers
/ 18,293 nnz) the dense `shared` matrix is ~46M int64 cells and the extraction
loop is the wall (3,958–4,607 ms measured 2026-11-18). This is the top-ranked
no-Rust-kernel compute lane from the S4 survey
(`mojo_trail_bfs_batch_taskgroup.md`, S4 addendum).

The operator ruling (m01277) is to **forget the single live wall and evaluate
by scaling**: build a Mojo batched kernel and measure the Python vs Mojo wall
as matrix order **n** (supplier count) grows, with parity as the correctness
oracle. The decision is whether the gap widens with n (Mojo worth wiring) or
stays flat / the dense materialization dominates (record the negative).

## Why this is a clean target

- **Parity oracle is exact**: top-K list of `(shared_count, supplier_i,
  supplier_j)` for i<j, deterministic order. Compare full list vs the Python
  reference at every scale.
- **Fan-out unit is independent**: rows of the `shared` matrix can be partitioned
  across workers; each worker scans its row-block for positive cells above the
  running top-K threshold, then a final reduction merges per-worker top-K.
  Disjoint buffers, no locks — the S1/S2 BFS template
  (`Mojo/src/bench/bfs_bipartite_all_batch.mojo`) applies directly.
- **No query-visible semantics change** (perf-only, report-only instrument) →
  no `ontology_eval_gate` bullet required; flagged explicitly per the
  proposals/README house rule.
- **No Rust kernel exists** — `graph_kernels` exports only
  `all_pairs_bfs_stats`, `limited_bfs_sources`, `personalized_pagerank{,_batch,_vec}`
  (S4 survey, cited there with `path:line`). Not a candidate for the Rust lane.

## Slices

**S1 — Python scaling harness + frozen parity vectors (no Mojo yet).**
Extend the measurement (scratch, gitignored) to sweep n at fixed density:
n ∈ {1k, 2k, 4k, 6,780} with shared-consumer structure sampled deterministically
from the live incidence. For each n, record the Python `a2_kernel` extraction
wall and freeze the exact top-K list as the parity vector. Also record the
`mat @ mat.T` dense-materialization wall separately from the extraction loop —
per the BFS proposal's note, the 46M-cell materialization may itself be the
real wall, and the spike must say which leg dominates at each n.

**S2 — Mojo batched top-K kernel.** New
`Mojo/src/bench/a2_topk_batch.mojo`: read the dense `shared` matrix (int64,
row-major) from a bin file; workers=1 sequential (baseline), workers>1
TaskGroup fan-out over disjoint row-blocks, each worker emits a local top-K
with tie-break by (i,j); final reduction merges to global top-K. Mojo 1.2
conventions: `def`-only, `alloc(Layout[...]).unsafe_leak()`,
`unsafe_offset(i)[]`, non-raising `async def`, pointer slots, `wait()` barrier
(reuse the BFS batch kernel's idioms). Emit compact `SHARED I J` lines sorted
by (-shared, i, j).

**S3 — scaling comparison + parity + decision.** Run S2 at each S1 scale with
w1 and w4; compare walls and plot the Python:Mojo ratio vs n. Parity: full
top-K list equality vs the S1 frozen vector at every scale. Decision rule:
- If Mojo's advantage **grows with n** and parity is exact → wire an opt-in
  Python integration (follow-up slice, separate proposal if multi-slice) with
  the BFS template's default-OFF env gate and hermetic tests.
- If the gap is flat, or dense materialization dominates, or parity breaks →
  record the negative and do NOT wire. (Negative is a valid outcome.)

## Acceptance criteria

- Exact parity (top-K list) vs Python reference at every swept n.
- Scaling table recorded (gitignored evidence file) with separate
  materialization vs extraction walls; Python:Mojo ratio at each n.
- No change to the default `a2_kernel` path; any integration strictly opt-in.
- `make mojo-build` / `make mojo-test` green for the new kernel; targeted tests
  only (gates parked per operator m01133 until further notice).

## Risk / doctrine notes

- Parity is on floating exactness? No — all counts are int64; no float compare.
- 46M-cell dense matrix at n=6,780 is ~370 MB int64; ensure the spike reads
  it as a bin (not per-row text) or the parse floor dominates as in the BFS
  per-source spawn trap.
- Thesis-first: the "no Rust kernel" claim is cited via the S4 survey's
  `graph_kernels` symbol inventory, not assumed.

## Status 2026-11-18 — spike run, parity exact, scaling POSITIVE

`Mojo/src/bench/a2_topk_batch.mojo` (built to scratch bin): reads the dense
int64 `shared` bin once; w1 sequential, w>1 TaskGroup fan-out over disjoint
row-blocks with per-worker top-K buffers; exact `(-shared,i,j)` merge. Mojo 1.2
idioms (`def`-only, `alloc(Layout).unsafe_leak()`, `unsafe_offset(i)[]`,
`async def` + pointer slots + `wait()`). `mojo build` clean.

Python reference density ≈2% synthetic incidence; walls are extraction-only
(the `mat@mat.T` materialization is reported separately). Mojo walls are best
of 5 subprocess runs (startup included); parity = full top-K list equality at
every scale — **OK at all scales**.

| n | Python extract | materialize | Mojo w1 | Mojo w4 | w1 speedup | parity |
|---|---|---|---|---|---|---|
| 1,000 | 239 ms | 8.6 ms | 19.0 ms | 15.4 ms | 12.6× | OK |
| 2,000 | 2,184 ms | 54 ms | 55.7 ms | 39.4 ms | 39.2× | OK |
| 4,000 | 35,257 ms | 458 ms | 226 ms | 136 ms | 156× | OK |

**Decision:** the Python/Mojo gap GROWS with n (12.6× → 39.2× → 156×), the
materialization leg is an order of magnitude below extraction, and parity is
exact → POSITIVE. The w4 fan-out adds a modest further gain (1.2× → 1.4× →
1.7×) because the scan is memory-bound, consistent with the BFS trial's
sub-2.89× fan-out. No Python integration wired (would be a follow-up slice);
default `a2_kernel` untouched. Evidence bins + driver under
`$TMPDIR/opencode/scaling_spike/` (gitignored scratch).

## Resolution 2026-11-18 — numpy-first WINS; Mojo kernel stays a parked spike

Operator ruling (m01409/m01410, after reviewing the spike): "agree with all 4.
Do the numpy bits for 2" — i.e. apply the repo's "numpy first, not native"
doctrine (`rust_cosine_knn_port.md:118`) to A² rather than cutting over to
Mojo. The numpy-first fix landed in `helpers/graph/a2_candidates.py:71
a2_kernel`: stay SPARSE (`prod = (m32@m32.T).tocoo()`), filter the upper
triangle, `argpartition` + exact `lexsort` tie-break. No dense n×n
materialization (~370 MB), no Python O(n²) scan. Also promoted int8→int32
before the product, fixing a latent overflow (int8 product capped shared
counts at 126 vs 316; 532 suppliers have degree >127 — all overflow was on
the diagonal, which is excluded, so live top-K output is unchanged).

**Measured:** live top-K wall 4,000–4,600 ms → **16.9 ms** (~250×), exact
parity vs the reference at k=1,5,10,50,100,1000. Targeted tests 20 passed
(`tests/test_a2_candidates.py`, `tests/test_suggest_relations.py`); ruff clean.
The Mojo spike kernel (`Mojo/src/bench/a2_topk_batch.mojo`) stays in the tree
as a documented scaling fallback only — NOT wired, default path untouched.
Cutover posture for this lane: numpy owns it, no unfreeze needed.

## Worktree / landing note

Filed in the main checkout per the current operator stgit arc. Full gate arc
NOT run (parked per m01133). No commit/stg/patch ops by the agent; changes left
dirty for operator staging. Stays `status: proposed` until a gate arc runs and
the operator elects to archive.
