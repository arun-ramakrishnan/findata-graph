---
title: "Record and pin the 2026-09-26 near-duplicate GEMM rework — its exactness claim lives only in a code comment"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "329"
area: "helpers/graph/query.py, doc/improvements/, tests/test_note_embeddings.py"
---

# Record and pin the 2026-09-26 near-duplicate GEMM rework — its exactness claim lives only in a code comment

**Date:** 2026-10-01 · **Status:** EXECUTED ·
**Area:** `helpers/graph/query.py:3666-3676` (the comment block), this
proposal as the durable record, `tests/test_note_embeddings.py`

> **Position:** documentation + verification arc for the function
> `c901_d1_split_near_duplicate_notes.md` refactors. Deliberately **no
> algorithm change** — that proposal owns the refactor; folding a record into
> it would make a refactor arc carry a measurement arc. Shares the same
> function and branch on purpose: the split cites this record as its evidence.

## 1. Motivation

On 2026-09-26 the near-duplicate self-join was reworked from a per-row SQL
`array_distance` join to a single f64 GEMM. The rework is described in a
comment at `query.py:3666-3676` and **nowhere else**. Verified 2026-10-01:

| Search | Hits in `doc/` |
|---|---|
| `near_dup_prune` | **0** |
| `GFLOP` | **0** |
| `0.5 GFLOP` | **0** |
| the 4.71 s measurement | 0 (the one `4.71` hit is `completed.md:1061`, unrelated — removed lines) |
| a `completed.md` entry | **none** |
| an archived proposal | **none** (the only near-dup archives are `near_duplicates_api_compute_cap.md` (AVAIL-1, #249) and `code_duplication_consolidation.md`) |

So the following exist only as prose in a source file, with no proposal, no
completion record, no benchmark, and no test that pins them:

- **the measurement** — *"the per-row `array_distance` self-join cost 4.71 s
  at 1,186 paths (~5.7 µs/pair, row-VM-bound), while the whole problem fits one
  f64 GEMM (1,186²×384 ≈ 0.5 GFLOP)"*;
- **the exactness argument** — *"Exactness unchanged: same renormalized means,
  same `sim = 1 − d²/2 = cosine`, same `(dist, path_a, path_b)` ordering"*;
- **the arithmetic dependency** — *"The section→path collapse is vectorized
  the same way (`np.add.at` replaces the per-row Python float loop)"*, i.e.
  bit-reproducibility now rests on first-seen path order in the `acc` dict;
- **the memory correction** (S4, 2026-09-28) — `S = X @ Xᵀ` materialises the
  **full P×P** matrix (8·P² bytes; 800 MB at the 10,000-path ceiling, ~11 MB
  at 1,186), correcting the earlier "O(block·paths), never O(paths²)" claim.

Why this matters now: `c901_d1_split_near_duplicate_notes.md` is about to
extract `_collapsed_note_matrix` — the exact function that holds this
arithmetic. A refactor justified by a claim that exists only in a comment has
no durable counterweight. A future maintainer who reorders the `np.add.at`
accumulation, changes the dict insertion order, or swaps the GEMM for a
blocked loop would break a bit-exactness property that nothing in `doc/`
records and nothing in `tests/` asserts.

## 2. Evidence (measured 2026-10-01, this box)

Live call against a read-only `memory/graph.duckdb`, `doc_type='company'`,
P = 1,186 distinct paths (702,705 unordered pairs), `limit=100`, `stats`
supplied:

| `min_sim` | wall (s) | `prunes` | `peak` | `post_prune_max` | pairs out |
|---|---|---|---|---|---|
| 0.9 | 2.07 | 188 | 400 | 100 | 100 |
| 0.5 | 2.41 | 2,342 | 400 | 100 | 100 |
| 0.1 | 2.32 | 2,342 | 400 | 100 | 100 |
| 0.01 | 2.34 | 2,342 | 400 | 100 | 100 |
| 0.001 | 2.40 | 2,342 | 400 | 100 | 100 |

What the numbers mean — three things the comment does not say:

1. **The GEMM rework's win is real but the per-pair scan was never the
   bottleneck at live scale.** Wall time is flat within 16% across a
   900× threshold sweep, while the prune count moves 12×. The ~2 s is
   dominated by the `v_note_embeddings` fetch, the section→path collapse, and
   the GEMM itself — not by the pair iteration. The comment's "row-VM-bound"
   diagnosis was about the *retired* SQL path and should not be read as a
   claim about where today's 2 s goes.
2. **`peak` is pinned at exactly `4 × limit` (400) and `post_prune_max` at
   `limit` (100) at every threshold** — the accumulator bound holds
   unconditionally, which is the empirical counterpart to the guard analysis
   in `near_duplicate_guard_invariant.md`.
3. **No perf leg covers any of this.** `rg near_dup tests/run_perf_benchmarks.py`
   returns nothing; the 2.07 s live figure has no budget and no gate, so a
   regression here is invisible to `make perf`.

Existing test coverage is semantic, not numeric: the four tests in
`tests/test_note_embeddings.py::TestNearDuplicateNotes` assert the top pair,
`sim ≈ 1.0`, threshold/doc_type filtering, and `limit=0` → `[]`. **None**
pins the mean-collapse arithmetic, the `1 − d²/2` identity, or the tie-break —
the three properties the comment claims are preserved.

## 3. Design

- **S1 — write the record.** This proposal becomes that record: on
  EXECUTED it archives here (this directory) with a `completed.md` number
  (per the proposals README checklist), carrying the retired-SQL measurement,
  the identity argument, the accumulation-order dependency, the S4 memory
  correction, and the 2026-10-01 live table above. S1 is a documentation
  slice with no code change; it can land alone.
- **S2 — pin the numerics.** Extend `TestNearDuplicateNotes` (or a new class
  beside it) with three assertions that make the comment's claims testable:
  (a) **collapse determinism** — the path-mean matrix is byte-identical
  across repeated calls and across a reordered *input* row order *only* where
  the reordering preserves first-seen path order, pinning the documented
  dependency rather than leaving it implicit; (b) **identity** — for a
  controlled pair, `sim` matches the cosine of the two renormalized means to
  within float tolerance, so the `1 − d²/2` claim is exercised; (c)
  **tie-break** — two paths at identical similarity emit the pair in
  `path_a < path_b` order, which is the property the S1 fixture in
  `c901_d1_split_near_duplicate_notes.md` also relies on. These tests consume
  that proposal's fixture builder if it has landed; they do not duplicate it.
- **S3 — publish a baseline.** Re-run the §2 sweep as a one-shot and record
  the numbers in the archived copy so a future regression has a denominator.
  Fold into S1 if the sweep is stable on re-run.

Ordering: S1 unblocks S2 (there is nothing to assert against until the claims
are written down where the repo will find them). S2 must not race the split —
if the split lands first, these assertions are written against the extracted
`_collapsed_note_matrix` instead, which is the better target anyway.

## 4. Acceptance criteria & shakedown

1. `rg -l "near_dup_prune" doc/` returns the archived copy of this proposal
   (today: 0 hits). The record is findable by the term the code uses.
2. `pytest tests/test_note_embeddings.py` green with the three new
   assertions; mutation-check at least (a) by perturbing the accumulation
   order and confirming RED → restore → green, per the `completed.md` #324
   strip→RED→restore precedent.
3. The archived copy carries the §2 table with its measurement date and the
   read-only call used, so a reader can re-run it.
4. `make qa` legs green: `lint`, `md-lint`, `types`, `static_checks`, `pytest`.
5. `make search-fresh APPLY=1` then plain `make search-fresh` (rc=0) after the
   archival, per the proposals README checklist.

| Projected outcome | Today | After |
|---|---|---|
| `near_dup_prune` mentions in `doc/` | 0 | 1 archived record |
| numeric properties under test | 0 | 3 (collapse order, identity, tie-break) |
| live wall figure with a recorded denominator | none | 2.07 s @ `min_sim` 0.9, P=1,186 |

## 5. Risks

- **Encoding a claim in a test makes it a contract.** If the identity or
  tie-break assertions are written too tightly they will block a *correct*
  future change (e.g. switching to a blocked, memory-bounded similarity).
  Mitigation: assert the *properties* (determinism given the documented order,
  cosine identity within tolerance, canonical orientation) rather than exact
  float bit patterns of the whole matrix.
- **The record may fossilise a stale number.** The 2.07 s figure is a
  point-in-time measurement on a loaded box. Mitigation: date every figure and
  mark the sweep as a denominator, not a budget — the budget is the other
  proposal's job.
- **S2 racing the split** would write assertions against a function that is
  being rewritten. Mitigation: the ordering note in §3; check the split's
  state before starting S2.

## 6. Non-goals

- **No algorithm change.** The GEMM, the block scan, the prune threshold and
  the norm guard all stay as they are. This arc records and pins, it does not
  optimise.
- **Not the refactor** — `_collapsed_note_matrix` / `_pair_mask` /
  `_scan_top_pairs` belong to `c901_d1_split_near_duplicate_notes.md`.
- **Not the guard/budget work** — the `min_sim` floor claim, the
  0.9-vs-(0,1] correction and the missing perf leg belong to
  `near_duplicate_guard_invariant.md`.
- **Not AVAIL-1's memo** — the generation memo and the 10,000-path ceiling
  stay as they are; this arc touches neither.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-01 | `rg -c "near_dup_prune\|GFLOP" doc/` | 0 / 0 | the rework has no record in `doc/` |
| 2026-10-01 | `rg -c "4\.71" doc/` | 1, unrelated | `completed.md:1061`, removed lines — not this measurement |
| 2026-10-01 | `ls doc/improvements/archive/*/ \| grep -iE 'near\|dup'` | `near_duplicates_api_compute_cap.md`, `code_duplication_consolidation.md` | neither covers the GEMM rework |
| 2026-10-01 | `near_duplicate_notes(con, min_sim=…, doc_type='company', limit=100, stats=st)` over 0.9/0.5/0.1/0.01/0.001, read-only `memory/graph.duckdb` | 2.07–2.41 s; `prunes` 188→2,342; `peak` 400; `post_prune_max` 100 | flat wall across a 900× threshold sweep; accumulator bound unconditional |
| 2026-10-01 | `rg near_dup tests/run_perf_benchmarks.py` | 0 hits | no perf leg, no budget |
| 2026-10-01 | read `query.py:3666-3676` | the 4.71 s / 0.5 GFLOP / exactness / `np.add.at` claims | comment-only, verbatim |

## Execution Results

### S2 + S3 executed 2026-10-01 (S1 is this proposal becoming the record — executes at archival)

- **S3 (folded into S1 per the proposal's own rule):** the §2 sweep
  re-run post-split, read-only `memory/graph.duckdb` — prunes 188 →
  2,342, peak pinned 400, post_prune_max 100 at every threshold, wall
  1.75–3.30 s (the filed 2.07–2.41 band, noisier box). Stable.
- **S2:** `tests/test_note_embeddings.py::TestNearDuplicateNumerics` —
  (a) collapse determinism (bit-identical tobytes across calls) with the
  order dependency made OBSERVABLE via a 1e16-absorption witness: in the
  documented section order one trailing 1 survives the big-pair
  cancellation (normalized direction 0.4472…), in the perturbed order
  both survive (0.7071…) — verified RED under a surgical within-path
  order-reversal mutation, green on restore; cross-path interleaving
  that preserves first-seen order and per-path section order is proven
  free; (b) the returned sim equals both u·v and 1 − |u−v|²/2 = 0.5 for
  the controlled (e_multi, f_dir) pair — a first-section-only collapse
  would return 0.0; (c) tie-break: owned by
  `TestNearDuplicateInvariants::test_tie_lattice_order_and_orientation`,
  referenced not duplicated (per §3's ordering note; the split landed
  first, so the pins target `_collapsed_note_matrix` as the record
  required).
- Residual for archival: the completed.md entry + `archive/graph/` move
  happen at the batch archival (qa on operator go), per the README
  checklist.
