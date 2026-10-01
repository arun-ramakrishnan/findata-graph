---
title: "Guard the BOLD_LINE_RE scaling test against sub-millisecond ratio noise"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "326"
area: "tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Guard the BOLD_LINE_RE scaling test against sub-millisecond ratio noise

**Date:** 2026-10-01 · **Status:** EXECUTED ·
**Area:** `tests/test_fuzz_regex.py` (test-only; no production code)

## 1. Motivation

`test_bold_line_regex_scales_subquadratically` is the gate board's only
red item: gate 1137 (2026-09-30 23:29, commit `07098c4c`) failed its
pytest leg on it — 3669 passed / 1 failed — with ratios
`[12.39, 4.88, 1.03] ms` against the `< 8.0` budget. The same test
passes on an idle rerun (1.99 s, this box, 2026-10-01). This is the
third flake of the same shape: the in-code comment records a
2026-09-22 occurrence (first ratio `0.36→4.44 ms = 12.3x` while
middle/last stayed at the calibrated ~4.2x/~2.9x), and the
2026-09-30 collation session saw it fail in-gate and pass both in
isolation and on a full-leg re-run. The test guards a real property
(AVAIL-2-class scaling); the measurement, not the property, is noisy.

## 2. Evidence (measured 2026-09-20→2026-10-01, this box + gate corpus)

| Configuration | Result | Verdict |
|---|---|---|
| gate 1137 pytest leg (xdist load) | ratios [12.39, 4.88, 1.03], max > 8.0 | flake — first ratio only |
| idle rerun (2026-10-01, this box) | PASS in 1.99 s | baseline healthy |
| 2026-09-22 record (in-code) | first 12.3x, middle/last ~4.2x/~2.9x | same first-ratio shape |
| nested-quantifier regression (recorded 2026-09-20) | 12.2x on the MIDDLE doubling | the discriminator the test exists for |

The test already carries warm-up + min-of-5 + a 0.01 ms denominator
floor — added after the first flake — and still flaked twice since.
The failure shape explains why: at n=160 the match is sub-millisecond
on this box, so the FIRST doubling's ratio divides a tiny denominator;
one preempted sample inflates it past any threshold. Under load the
instability is bidirectional — the large-n samples inflate together,
deflating the LAST ratio (1.03 observed at gate 1137, below the
healthy ~4.2x). Only the middle doubling, whose denominator is
already ≥ ~1.5 ms, is stable in every recorded run.

## 3. Design

**S1 — score only ratios whose denominator (the smaller size's
best-of-5) is ≥ 1.0 ms.** A ratio from a sub-millisecond base is
scheduler noise, not scaling evidence. The assertion keeps the 8.0
threshold and the calibration rationale; warm-up and min-of-5 are
unchanged. If NO ratio has a ≥ 1 ms base the test fails loud with a
recalibrate-the-ladder message — that cannot happen at the calibrated
sizes (n=1280 measures ~26 ms healthy) and would mean the box or the
pattern changed out from under the test.

Alternatives, and why they lost:

- **Accept as flaky** — rejected: it is the board's only red, the
  gate runs under xdist load by design, and three flakes are on
  record; every future gate re-run pays for it.
- **Drop the first doubling unconditionally** — achieves the same on
  today's box but discards evidence on slower boxes where the first
  base IS meaningful; the adaptive floor keeps that ratio when it is
  measurable and drops it when it is not.
- **Absolute budget instead of scaling** — rejected: the healthy vs
  regressed gap at n=1280 is ~25x (≈26 ms vs the ~12.2x-per-doubling
  projection), not the ~4000x margin that makes the AVAIL-2 test's
  absolute bound (`test_range_regex_adversarial_space_runs_are_linear`)
  unflappable.
- **More samples** — cost without removing the failure class: the
  noise lives in the denominator's magnitude, not the sample count.

## 4. Acceptance criteria & shakedown

1. `.venv/bin/python3 -m pytest tests/test_fuzz_regex.py -q` green.
2. Repeat count (timing-shaped, never one run): the scaling test 5x
   consecutively, plus one `pytest tests/test_fuzz_regex.py -n 4`
   invocation to reproduce the gate's load shape.
3. Mutation shakedown: fold `\s*` into the lazy group
   (`^(?:\*\*(?:.+?\s*)+\*\*\s*)+$`, the recorded regression shape) →
   the test must go RED on the scored ratios; restore → green. If the
   obvious mutation does not reproduce the 12.2x middle-doubling
   shape, measure and record what does before trusting the guard.
4. ruff + `make md-lint` clean on the touched file.

| Projected outcome | Today | After |
|---|---|---|
| gate flakes of this test on record | 3 (09-22, 09-30, gate 1137) | 0 projected |
| doublings contributing to the assertion | 4 (all, incl. noise) | the ≥ 1 ms ones (typically 2–3) |
| regression discriminator | middle doubling 12.2x vs 8.0 | unchanged |

## 5. Risks

- **Over-loosening hides a regression** — only if every base stays
  under 1 ms; that path now fails loud (empty scored set) instead of
  passing silently.
- **Faster hardware shrinks all bases** — the floor is size-adaptive:
  bases scale with the machine, so the scored set shrinks gracefully
  before the loud-fail path, which names the remedy.

## 6. Non-goals

- No change to `BOLD_LINE_RE` itself — quadratic-bounded is the
  correct, measured behavior (0.02 ms @ 10 segments → 71 ms @ 320).
- No edits to the AVAIL-2 absolute-bound test (different regime,
  4000x margin, zero flake record).
- No budget or threshold changes to any other perf test.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-30 23:29 | gate 1137, commit `07098c4c` | ratios [12.39, 4.88, 1.03] vs < 8.0 | the only failed test, 3669 passed |
| 2026-09-30 (collation) | isolation + full-leg re-run | PASS both | recorded in `../tooling/review_findings_collation.md` §S5 context |
| 2026-10-01 | `pytest tests/test_fuzz_regex.py::test_bold_line_regex_scales_subquadratically -q` | PASS 1.99 s | idle box |
| 2026-09-20 (in-code) | min-of-3 calibration | ~4.2x/doubling healthy; 12.2x mutated middle | comment block above the test |
