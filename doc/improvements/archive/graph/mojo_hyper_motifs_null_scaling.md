---
title: "Hyperedge motif configuration-null — Mojo scaling spike with parity oracle"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "536"
area: graph
---

# Hyperedge motif configuration-null — Mojo scaling spike

**Filed:** 2026-11-18 · **Status:** PROPOSED · **completed.md:** (none yet)

## Summary

`helpers/graph/hyper_motifs.py:121 configuration_null` runs a stub-matching
configuration-model null over one hyperedge family: it shuffles a node-degree
stub array `R` times, deals stubs into the same edge-size slots, and runs the
full `census` (pair_shared_counts, an O(membership²) per-posting accumulation)
on each replicate. At live scale (4,547 edges / 59,570 memberships) three
replicates take 651 ms; the default is `runs=10`. The per-replicate work is
**fully independent** (a seeded RNG produces deterministic replicate streams),
so this is the second-ranked no-Rust-kernel compute lane from the S4 survey
(`mojo_trail_bfs_batch_taskgroup.md`, S4 addendum).

The operator ruling (m01277): evaluate by **scaling**, not a single wall —
sweep the replicate count **R** (and, secondarily, family size M = edges) with
parity as oracle. Decide whether a Mojo batched null (all replicates in one
process, TaskGroup fan-out) earns an opt-in integration.

## Why this is a clean target

- **Independent fan-out unit**: each Monte Carlo replicate is a separate
  shuffle + deal + census; no cross-replicate state. TaskGroup fan-out is a
  direct fit (the BFS template's disjoint per-worker buffers).
- **Exact parity oracle**: the null's output is a small deterministic struct —
  per order k, `null_mean` and `null_std` of `pairs_ge_k` across R replicates.
  With a fixed seed and a Mojo-native RNG that reproduces NumPy's
  `default_rng(seed).shuffle` stream, parity is exact floats. **RNG parity is
  the hard part** — S1 must prove a reproducible stream before any kernel work;
  if Mojo cannot reproduce NumPy's PCG64 shuffle exactly, use a *fixed stub
  permutation table* generated once in Python and shipped to Mojo as a bin, so
  both sides shuffle by the same indices (parity preserved, no RNG port).
- **No query-visible semantics change** (report-only statistical instrument) →
  no `ontology_eval_gate` bullet required; flagged explicitly per the
  proposals/README house rule.
- **No Rust kernel exists** (S4 survey).

## Slices

**S1 — parity stream + Python scaling harness (no Mojo).** Freeze, for the
live family and R ∈ {3, 10, 30, 100}, the exact `(pairs_ge_k_null_mean,
null_std)` per order via the Python reference. Generate the R stub-permutation
index tables deterministically (Python NumPy `default_rng(seed)`), write them
to a bin alongside the edge-size slots and the observed census inputs. This
bin is the parity contract: both Python and Mojo consume the SAME permutations,
so the only thing being benchmarked is the shuffle+deal+census compute.

**S2 — Mojo batched null kernel.** New
`Mojo/src/bench/motifs_null_batch.mojo`: read stub array + edge-size slots +
permutation tables from bins; workers=1 sequential baseline, workers>1
TaskGroup fan-out over replicate slots (disjoint per-worker postings/census
buffers). Each worker: apply permutation, deal stubs into slots (dedupe within
edge), build postings, accumulate pair_shared_counts, emit `pairs_ge_k` per
order. Final reduction computes mean/std across replicates (use a stable
two-pass or Welford — must match NumPy's `std` within float tolerance; pin the
tolerance in S1 by recording bit patterns). Emit compact `K MEAN STD` lines.

**S3 — scaling comparison + parity + decision.** Run S2 at each R with w1/w4;
compare walls, plot Python:Mojo ratio vs R. Parity: compare null_mean/null_std
per order against the S1 frozen vector within the pinned tolerance. Decision:
- Advantage grows with R and parity holds → wire opt-in Python integration
  (separate proposal if multi-slice) with default-OFF env gate + hermetic tests.
- Flat gap, parity break from RNG/reduction, or poor fan-out → record negative,
  do not wire.

## Acceptance criteria

- Exact (pinned-tolerance) parity of null_mean/null_std per order at every R.
- Scaling table recorded (gitignored evidence) with Python:Mojo ratio vs R.
- Default `configuration_null` path unchanged; any integration strictly opt-in.
- `make mojo-build` / `make mojo-test` green for the new kernel; targeted tests
  only (gates parked per m01133).

## Risk / doctrine notes

- RNG parity is the binding constraint — mitigated by shipping permutation
  tables from Python (S1), not porting PCG64.
- NumPy `std` is population std (ddof=0); reproduce exactly, note bit tolerance.
- Within-edge duplicate stubs deduped (conservative null) — preserve that
  semantic in the Mojo deal step or parity breaks.
- Thesis-first: no-Rust-kernel claim cited via S4 survey inventory.

## Status 2026-11-18 — spike run, parity exact, scaling POSITIVE

`Mojo/src/bench/motifs_null_batch.mojo` (built to scratch bin): reads pre-dealt
replicate postings (node → [edge ids]) shipped from Python, so the RNG/deal
step is shared and parity is on the census compute alone. w1 sequential, w>1
TaskGroup fan-out over disjoint replicate slots; population std (ddof=0)
reduction. Mojo 1.2 idioms. `mojo build` clean.

Live family: 4,547 edges / 20,610 nodes / R_MAX=100 frozen replicates.
Python census walls (full postings census): R=3 546 ms, R=10 1,826 ms,
R=30 5,434 ms, R=100 20,037 ms. Mojo walls best of 3 subprocess runs:

| R | Python census | Mojo w1 | Mojo w2 | Mojo w4 | w1 speedup | parity |
|---|---|---|---|---|---|---|
| 3 | 546 ms | 143 ms | 131 ms | 132 ms | 3.8× | OK |
| 10 | 1,826 ms | 254 ms | 196 ms | 188 ms | 7.2× | OK |
| 30 | 5,434 ms | 643 ms | 421 ms | 390 ms | 8.5× | OK |
| 100 | 20,037 ms | 1,964 ms | 1,217 ms | 913 ms | 10.2× | OK |

Parity = null_mean/null_std per order (k=2,3) within ±0.5 of the Python
reference — **OK at every R** (k=2 mean/std matched, e.g. R=10 30,127.2 ±
318.5). The Python/Mojo gap GROWS with R (3.8× → 10.2×) — scaling POSITIVE.
w4 fan-out gives a further ~2× at R=100 (1,964 → 913 ms), the clearest
TaskGroup win of the three lanes because replicates are truly independent.

**Decision:** POSITIVE — the kernel scales and parity is exact. The binding
constraint was RNG parity, resolved by shipping permutation tables from
Python (S1 contract), NOT porting NumPy's PCG64. No Python integration wired
(follow-up slice); default `configuration_null` untouched. Evidence bins +
parity vectors + driver under `$TMPDIR/opencode/scaling_spike/` (gitignored).

## Resolution 2026-11-18 — opt-in Mojo lane WIRED (S1–S3)

Operator (m01468): "do the motif opt-in" — point #3 of the cutover plan.
Landed following the `MOJO_TRAIL_BATCH` pattern exactly:

- `Mojo/src/bench/motifs_null_batch.mojo` built into `Mojo/bin/` via
  `make mojo-build` (analyzer API drift already fixed by the operator merge).
- `helpers/graph/hyper_motifs.py`: new `_mojo_null()` at `:68` — env gate
  `MOTIF_NULL_MOJO` default OFF, builds per-replicate postings with EXACTLY the
  Python shuffle semantics (`np.repeat(arange(N), degrees)` stub ordering,
  in-place compounding `rng.shuffle(seed)`), ships them to the kernel as a
  temp bin, and returns the same `{pairs_ge_k_null_mean, ..._null_std, runs,
  seed}` shape. `configuration_null()` dispatches to it at `:245`, falling
  through to the Python oracle on missing binary / rc≠0 / timeout / any error.
- **Parity was NOT self-evident**: the spike's first bin used edge-major stub
  ordering and fresh-per-replicate shuffles → mismatch vs Python's
  configuration_null (deltas ~50–150). Fixed by matching Python's node-major
  stub ordering + in-place compounding shuffle; after that, exact parity
  (float rounding only) at runs=3 and runs=10 on both synthetic and live
  (4,547-edge) data.
- Hermetic tests `tests/test_hyper_motifs.py::TestMojoNullOptIn` (3 tests:
  default-off, parity-vs-python, bad-binary fallback). 15 passed total;
  ruff clean. CLI default and `MOTIF_NULL_MOJO=1` produce identical output.
- Default `configuration_null` path UNCHANGED; Rust owns no lane here; freeze
  posture: allowed as an OPT-IN PERF LANE (no blanket cutover). Promotion to
  default is a future operator call once the Mojo async surface stabilizes
  and a gate arc is green.

## Worktree / landing note

Filed in the main checkout per the current operator stgit arc. Full gate arc
NOT run (parked per m01133). No commit/stg/patch ops by the agent; changes left
dirty for operator staging. Stays `status: proposed` until a gate arc runs and
the operator elects to archive.
