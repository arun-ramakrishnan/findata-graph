---
title: "Rust proptest property suite for cosine_knn kernels"
status: executed
filed: "2026-11-01"
executed: "2026-11-01"
completed_md: "465"
area: "Rust/cosine_knn (dev-dependency + tests/)"
---

# Rust proptest property suite for cosine_knn kernels

**Date:** 2026-11-01 · **Status:** EXECUTED (filed + executed same day) · **completed.md:** #465 ·
**Area:** `Rust/cosine_knn` (dev-dependency + `tests/`)

## 1. Motivation

The Rust test surface is 8 example-style tests (6 workspace + embed_cache
round-trip) pinning specific cases: identical/orthogonal vectors, zero-norm
guard, remainder-tail, topk k-clamp, tie-rule, parallel-vs-single parity on
one shape. What no example test can cover: the *invariants across random
inputs* — ordering invariants of top-k, the single-vs-parallel equivalence
at every matrix size straddling the 5k `PARALLEL_THRESHOLD`, and the
monotone relation between true cosine and returned scores. The house fuzz
tier is Hypothesis-shaped (`make fuzz`, 28 `test_fuzz_*.py` files); the
Rust analogue on record since the tooling-mapping verdict (2026-10-26) is
**proptest** — filed then as "future", now unblocked by the rayon kernel's
parity requirement (#448).

## 2. Evidence (measured 2026-11-01, this box)

- `cargo test --workspace` → 8 passed, all fixed-vector cases.
- `tests/test_cosine_knn_backends.py` → 9 passed — but parity is pinned at
  production scale only (717×512); the 5k threshold boundary (4999/5000/5001
  rows) is untested on either side.
- `make fuzz` runs Hypothesis suites only — no Rust leg exists.
- proptest is the recorded Hypothesis analogue (rust_language_tooling
  tool-mapping verdict); cargo-fuzz explicitly deferred (libFuzzer, heavier).

## 3. Design

Slices:

1. **S1 — proptest as dev-dependency + `Rust/cosine_knn/tests/properties.rs`.**
   Properties (each shrinks on failure, unlike hand-picked cases):
   - **topk ordering**: for any matrix, returned scores are descending and
     every returned score ≥ every unreturned score in its row.
   - **tie-rule**: row indices ascend within equal-score runs (the
     partition_point invariant, generalized from the unit test).
   - **single-vs-parallel parity**: `topk_single` ≡ `topk_parallel` (exact
     equality) across random shapes — force both paths regardless of the
     5k router, plus boundary sizes 4_999/5_000/5_001 through the router.
   - **zero-norm semantics**: any row that is all-zeros yields the pinned
     score convention for every random query.
   - **cosine bounds**: every similarity ∈ [-1, 1] (+ symmetry on the
     2-vector case).
   - **k-clamp**: `k > n`, `k = 0` (or the crate's convention), random k —
     output length always min(k, n).
2. **S2 — wire into the existing rust-test leg.** `cargo test --workspace`
   already picks up `tests/`; no new Makefile target, no advisory change.
   Case-count stays bounded (default proptest cases = 256) so the advisory
   leg's wall time is unaffected beyond noise.

Alternative lost: **quickcheck** (smaller, but no shrinking of collections
and no strategy composition to express "matrix with some zero rows");
**cargo-fuzz** (libFuzzer, needs nightly/LLVM instrumentation — recorded
future, wrong tier for invariant testing).

## 4. Acceptance criteria & shakedown

1. `cargo test -p cosine_knn --test properties` green, ≥6 properties.
2. `cargo test --workspace` green (no regression to the 8 existing).
3. `make rust-test` (advisory leg) green; run twice — proptest RNG is
   deterministic per seed by default, so both runs identical.
4. Deliberately break one invariant locally (e.g. flip tie insertion) and
   confirm proptest SHRINKS to a minimal failing case — proves the suite
   has teeth, then revert.

| Projected outcome | Today | After |
|---|---|---|
| Rust tests | 8 (examples) | 8 + ≥6 property suites (256 cases each) |
| threshold-boundary coverage | none | 4_999/5_000/5_001 both paths |

## 5. Risks

- **Compile-time tax** — proptest is a dev-dependency only; release/install
  path (`make rust-install`) untouched.
- **Flaky-looking failures on genuinely wrong invariants** — mitigation:
  each property names the existing unit test it generalizes; a property
  failure is a real bug, not noise (that is the point).

## 6. Non-goals

- No cargo-fuzz/libFuzzer (stays future).
- No embedding_cache properties (backup-only ruling; its single round-trip
  test stays as-is).
- No production-code changes — if a property finds a real bug, that is a
  separate fix + parity re-verification.

## 7. Execution addendum (2026-11-01, same day — EXECUTED)

- proptest 1.x dev-dependency + `Rust/cosine_knn/tests/properties.rs`:
  **8 properties, 256 cases each, all green** (8.4 s). Workspace cargo
  tests 8 → 16. Rides the rust-test advisory leg unchanged (S2 as
  designed — no new make target).
- **Shakedown with teeth**: injected a deliberate tie-rule bug
  (`partition_point(s >= score)` → `s > score`, both paths) → property
  `identical_rows_return_ascending_row_indices` FAILED with a SHRUNK
  minimal counterexample **n = 2, dim = 1, k = 2**; reverted, all green
  again, lib.rs byte-identical to pre-arc state.
- Tooling trap recorded: proptest assert macros expand format strings
  via `concat` — inline captures (`{z}`) do NOT compile; positional
  args required (9 fixes).
- Scale-shape property (`tie_parity_at_scale`: n=5–6k identical rows,
  k=64–512, merge sort under full tie pressure) PASSED 256/256 — the
  `sort_unstable_by` tie-order concern did not materialize on this
  toolchain; the property now guards it against future changes.
