---
title: "Fast validation engine by default — fastjsonschema gates, jsonschema advises"
status: executed
filed: "2026-09-21"
executed: "2026-09-21"
completed_md: "261"
area: "helpers/validators"
---

# Fast validation engine by default — fastjsonschema gates, jsonschema advises

**Date:** 2026-09-21 · **Status:** EXECUTED ·
**Area:** `helpers/validators/frontmatter_schema.py`,
`helpers/validators/static_checks.py`, `helpers/maintenance/maint.py`,
`pyproject.toml` + `uv.lock`

## 1. Motivation

The schema leg's per-file cost is validation-dominated: 431 µs per
note all-in under jsonschema (Avanti_Feeds fixture, best-of-3) vs
25 µs under fastjsonschema (~17×; validation term 0.449 s → 0.044 s
full-corpus). Post dirty-gate-flip the validation term survives only
on `--full` runs (maint-full backstop, release checks) — but those are
exactly the runs that should be cheap, and fail-fast is sufficient for
a gate: it answers "broken: yes/no + first error", which is all a
blocking check needs. Full multi-error detail moves to a non-blocking
advisory track where it can't stall ingests.

## 2. Evidence (measured 2026-09-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| per-validation, Avanti_Feeds fixture | 431 µs jsonschema / 25 µs fastjsonschema | ~17× |
| validation term, full corpus | 0.449 s / 0.044 s | ~10× |
| verdict agreement, 1456 real notes (6 types + proposals) | 1456/1456, 0 FP / 0 FN | gate track sound |
| 28 adversarial mutations (drop-required, wrong-type, extra-key, pattern, enum) | 28/28 flagged by both | no false negatives |
| consumer coupling | tests assert emptiness + one single-defect substring (survives: otherwise-valid fixture still trips `additionalProperties` first) | no machine consumer needs multi-error |

Soundness was proven with both validators fed identically `_normalize`d
input (datetime normalization is required — raw YAML timestamps are
not JSON values). All 5 `doc/okf` schemas compile under
fastjsonschema 2.22.2.

## 3. Design

Default fast everywhere; `--strict` opts into jsonschema:

- `frontmatter_schema`: engine param on `load_validator` /
  `validate_frontmatter` (`engine="fast"` default). Fast path returns
  the single-error list (`{path}: {message}` shape preserved);
  strict keeps today's `iter_errors` behavior. Fast engine absent →
  degrade to jsonschema + advisory (same pattern as today's
  jsonschema-missing advisory — minimal envs stay green).
- CLIs: `--strict` on both `static_checks.py` (module-global like
  `_DIRTY_SCOPE`) and the frontmatter CLI. `--report` advisory mode
  on the frontmatter CLI: full corpus + strict engine, all violations
  printed as warnings, **exit 0 always**.
- `maint.py` TIER2 gains `frontmatter_schema.py --report` after the
  blocking `static-checks --full` (which now runs the fast engine —
  same file set flagged, abort-before-snapshot preserved): full detail
  surfaces every ingest without blocking it.
- Dependency: `fastjsonschema` pin in `pyproject.toml` + `uv lock`
  (manylinux wheel — installed in 1 ms on this box, no build needed).

Alternatives considered: hard swap (rejected — kills the advisory
detail entirely); hybrid per-file fast-then-full (rejected — pays both
on every broken file for no verdict gain; soundness makes the second
pass redundant on the gate track).

Slices: S1 engine param + `--strict` both CLIs (+ fallback); S2
`--report` mode + maint step + tests; S3 pin + lock + full suites.

**Strict-engine consumers (explicit inventory).** Everything not
listed here runs the fast default; verdicts agree across engines, so
only multi-error *shape* assertions and full-detail surfaces need
strict:

| Consumer | Location | Why strict |
|---|---|---|
| count-pin walker tests (2) | `tests/test_frontmatter_schema.py:351,371` | assert exact multi-error list lengths (2, 3) — fail-fast reports 1/file |
| engine parity class | `tests/test_frontmatter_schema.py:690-712` | strict side of the agreement assertion + single-error fast shape |
| report-mode test | `tests/test_dirty_scope.py` (`--report` implies strict) | asserts advise-downgrade + rc 0 on a broken vault |
| maint advisory step | `helpers/maintenance/maint.py:393` | full multi-error detail every ingest, never blocking |
| CLI `--strict` | both validator CLIs | manual/release opt-in to full detail |
| fast-missing fallback | `helpers/validators/frontmatter_schema.py` | minimal envs degrade to strict + advisory (stay green) |

Deliberately engine-agnostic (fast-default, passing): the
`rogue_key errs[0]` assertion (single-defect fixture trips
`additionalProperties` first under both engines), `live_corpus_is_clean`,
all dirty-scope tests, and the OKF CLI tests (`--full` there selects
scope, not engine).

## 4. Acceptance criteria & shakedown

1. `static_checks.py --full` verdicts == `--strict` full verdicts on
   the real tree (same file set flagged; messages may be fewer) —
   plus the in-repo engine-parity test (both engines over valid +
   broken fixtures, all types).
2. `--report` exits 0 with violations printed on a seeded-broken tmp
   vault; TIER2 count/order pins + sandbox shim updated.
3. `rogue_key errs[0]` test and all emptiness assertions pass
   unmodified under the fast default.
4. `make qa` green (operator go); deptry clean with the new pin;
   `make search-fresh` converges.

| Projected outcome | Today | After |
|---|---|---|
| schema-leg validation term, `--full` | 0.449 s | ~0.05 s |
| gate error detail per broken file | all violations | first violation (+ full list in maint advisory) |
| maint-full blocking behavior | aborts pre-snapshot on corpus fail | unchanged (fast engine, same file set) |

## 5. Risks

- **Fail-fast iteration tax** — authors fix one violation per gate run.
  Mitigation: 4.2 s gated runs make iteration cheap; full list in
  every maint advisory; single-defect case (the common one) is
  unaffected.
- **C-extension dependency** — build/packaging surface. Mitigation:
  manylinux wheels (no build on this box); pinned; deptry-gated.
- **Engine divergence over time** — future schema keywords the two
  engines read differently. Mitigation: the in-repo parity test runs
  every gate; divergence fails loudly at introduction, not in prod.

## 6. Non-goals

Changing *what* the schemas assert; touching the OKF leg (no
jsonschema use); the import-trim slice 2 (`-X importtime` profile);
the 15-checks follow-up (separate pending.md item, separate arc).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-21 | spike: compile 5 schemas + bad-note surface + toy bench | all compile; fail-fast confirmed (required-only, rogue_key unreported) | venv-local install, removed after; first decline, then overturned |
| 2026-09-21 | soundness: both engines over 1456 notes + 28 mutations | 100% verdict agreement, 0 FP/FN | identically `_normalize`d input; scripts in `/tmp/opencode/fjs_sound.py` + inline pattern/enum run |
| 2026-09-21 | micro-bench Avanti_Feeds ×2000 best-of-3 | 431 µs vs 25 µs | jsonschema all-in (normalize+sort+format) vs validate-only |
| 2026-09-21 | S1–S3 build: engine param, `--strict` both CLIs, `--report` advisory mode, maint TIER2 `--report` twin, pin/`uv lock`/deptry | 350 tests green (2 walker count-tests migrated to strict, parity class added, sandbox shims for both new steps); `--full` == `--full --strict` verdicts live; rogue_key test unmodified and passing | EXECUTED same day |
| 2026-09-21 | engine-delta + suites | schema leg fast 0.40 / strict 0.93 s best-of-3 in-process (same 0/0 verdicts); `--full` end-to-end 6.15 / 7.17 s best-of-3 (box load ~10, noisy — direction solid, magnitude approximate) | validation term ~10× as projected; fastjsonschema import 42.7 ms vs jsonschema 59 ms |
