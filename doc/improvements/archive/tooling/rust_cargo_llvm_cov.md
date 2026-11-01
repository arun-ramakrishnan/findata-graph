---
title: "Rust coverage via cargo-llvm-cov — advisory report leg"
status: executed
filed: "2026-11-01"
executed: "2026-11-01"
completed_md: "467"
area: "Rust/ (tooling) + Makefile advisory"
---

# Rust coverage via cargo-llvm-cov — advisory report leg

**Date:** 2026-11-01 · **Status:** EXECUTED (filed + executed same day) · **completed.md:** #467 ·
**Area:** `Rust/` (tooling) + Makefile advisory

## 1. Motivation

The tooling-mapping verdict (2026-10-26) settled the coverage tool —
**cargo-llvm-cov** is the standard (tarpaulin SKIP: slower, sporadic) — but
ruled filing it *deliberately premature*: "cargo test runs green with 0
Rust-side tests; file cargo-llvm-cov when there's something to measure."
Since then the surface grew: 8 workspace tests including the rayon parallel
kernel (#448) whose `topk_parallel` path (1024-row chunks, k-way merge) has
zero coverage visibility — parallel code paths are exactly where example
tests under-measure. The Python side has `make cover` (pytest-cov,
branch+term-missing+html); Rust has nothing.

This proposal is filed now to have the trigger assessed on the record; the
recommendation below is still trigger-gated, not immediate.

## 2. Evidence (measured 2026-11-01, this box)

- `cargo test --workspace` → 8 passed; of these, cosine_knn carries 6
  (all single-path examples; the parallel unit test covers ONE shape).
- `tests/test_cosine_knn_backends.py` → 9 passed (python-side parity —
  exercises the .so but reports no coverage).
- `make cover` exists for pytest only; no Rust analogue.
- cargo-llvm-cov not installed (`cargo install cargo-llvm-cov` — heavier
  install than cargo-deny: LLVM source-based coverage tooling).
- divan benches (#448) measure latency but say nothing about branch/path
  coverage.

## 3. Design

Slices:

1. **S1 — install + one-shot measurement (no wiring).** `cargo install
   cargo-llvm-cov`; run `cargo llvm-cov --workspace --html` once; archive
   the summary table in the proposal addendum. Decision point: if parallel-
   path coverage is materially below single-path (expected — one shape
   exercises it), S2 is justified; if coverage is already near-total,
   close as measured-no-action.
2. **S2 — advisory report leg (only if S1 justifies).** `make rust-cov`
   generating an lcov/html artifact under `outputs/` (report-artifact
   convention), added to the advisory gate runner as a report-only step.
   NOT qa — coverage numbers inform, they don't gate (mirrors `make cover`:
   manual, not in the quartet).
3. **S3 — threshold target.** Only if a concrete gap shows: add the
   targeted unit tests the coverage report names (likely: parallel path at
   k > 1024-chunk boundaries, multi-chunk merge with ties across chunk
   edges).

**Recommendation: run S1 when the next kernel-surface change lands** (any
edit to `topk_parallel`/merge logic), or bundle it into the proptest arc —
property suites + coverage report are complementary teeth. Not before:
measuring 8 tests buys a number, not a decision.

## 4. Acceptance criteria & shakedown

1. S1 produces a coverage table for cosine_knn + embed_cache (line + region
   %), archived in the addendum.
2. S2 (if run): `make rust-cov` idempotent, artifact under `outputs/`,
   advisory leg green, no qa impact.
3. No timing-repeat requirement (coverage is deterministic per build).

| Projected outcome | Today | After S1 |
|---|---|---|
| Rust coverage visibility | none | line/region % per crate |
| parallel-path gap knowledge | none | measured (drives S2/S3 or closes) |

## 5. Risks

- **Install weight** — cargo-llvm-cov pulls LLVM coverage tooling; one-time
  cargo install, no repo footprint beyond the binary.
- **Coverage theater** — a number nobody acts on. Mitigation: S1 is a
  decision point with a named closing condition, not a standing dashboard.

## 6. Non-goals

- No qa gating on coverage thresholds.
- No tarpaulin trial (SKIP verdict already on record).
- No changes to `make rust-install`/release path.

## 7. Execution addendum (2026-11-01, same day — S1 EXECUTED, closed MEASURED-NO-ACTION)

cargo-llvm-cov installed; `cargo llvm-cov --workspace` one-shot:

| File | Lines | Missed | Cover |
|---|---|---|---|
| cosine_knn/src/lib.rs | 241 | 63 | **73.86%** |
| embed_cache/src/lib.rs | 87 | 0 | **100%** |
| cosine_knn/src/bin/trial.rs | 163 | 163 | 0% (trial artifact) |
| embed_cache bins (4) | 411 | 411 | 0% (trial artifacts) |
| TOTAL | 902 | 637 | 29.38% |

**Decision point resolved: close, no S2/S3.** The uncovered lines in
cosine_knn/src/lib.rs are EXACTLY the PyO3 wrapper functions (`knn`/
`knn_batch`, lines 161–231) — unrunnable under cargo test and already
exercised from the Python side (`tests/test_cosine_knn_backends.py` 9/9
under SCRIPT_KNN_STRICT=1). The parallel path is fully exercised by the
proptest suite (`local_topk`: 3.23k calls / 2.43M row iterations in one
report). The 0% rows are trial bins (#446 explicitly keeps embed_cache
trial.rs as a recorded artifact) — real coverage of production-carrying
code is 74%/100%. A standing advisory leg would measure trial-bin noise;
the safety net is fine.
