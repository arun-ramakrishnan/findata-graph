---
title: "Dirty-gated corpus validation — validate changed notes only, full-run default until measured"
status: executed
filed: "2026-09-21"
executed: "2026-09-21"
completed_md: "260"
area: "helpers/validators"
---

# Dirty-gated corpus validation — validate changed notes only, full-run default until measured

**Date:** 2026-09-21 · **Status:** EXECUTED ·
**Area:** `helpers/validators/static_checks.py`,
`helpers/validators/frontmatter_schema.py`, `make qa` gate time

## 1. Motivation

Every `make qa` rewalks, reparses, and revalidates the entire 1102-file
corpus even when a session touches three notes. The walk itself is
already optimal (single-walk generator, `static_checks.py:622` — reads
each file once instead of three times); what remains is the *set*: the
three corpus legs below run over all 1102 files unconditionally. S0
measured the legs in isolation (2026-09-21, clean tree, load ~0.8):

| Leg | Time | Share of static_checks (5.33 s) |
|---|---|---|
| Findata YAML | 0.38 s | per-file `_check_*_one` × 1102 |
| Frontmatter schema | 0.81 s | per-file parse + `iter_errors` × 1102 |
| OKF conformance | 0.36 s | per-note + advisory census |
| Proposal lifecycle | 0.01 s | excluded — stays full-run |

Gateable prize ≈ **1.55 s → milliseconds** on small-dirty trees (the
common case: a session dirties a handful of notes). Not a request-path
item — this is gate wall-clock, paid by every arc, every `make qa`.

## 2. Evidence (measured 2026-09-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo, clean tree | YAML 0.38 / schema 0.81 / OKF 0.36 / lifecycle 0.01 s | baseline (denominator) |
| per-file independence audit (S1) | YAML `_one` checks vs static CANONICAL sets; schema vs static JSON; OKF fatals per-note, census advisory-only | all three gateable; lifecycle excluded (cross-file, 0.01 s — no prize) |
| validator self-test (S0a) | bare `frontmatter_schema` run tripped a live FATAL on the new `skylake_igpu_eval.md` (`completed_md: 257` int vs string pattern) | fixed (`"257"`), 0 fatal — the gate works; dirty-gating must not weaken what it catches |

S1 ruled out the only structural objection: no leg carries
corpus-accumulated fatal state (no cross-note duplicate registries, no
fatal census thresholds). The census is advisory-only, so a partial
census over the dirty set is acceptable if documented.

## 3. Design

Mechanism: derive the dirty set from `git status --porcelain` plus
untracked `findata/**/*.md`, and run the three gateable legs over the
dirty set only. Proposal lifecycle always runs full (0.01 s — gating it
buys nothing and risks the archive↔completed.md↔README agreement). A
`--full` flag preserves today's semantics for `maint-full`, release
checks, and any session that wants them. **DEFAULT FLIPPED 2026-09-21**
on the S3 evidence below — the gate now runs dirty-gated unless
`--full` is passed; `maint-full` gained a `static-checks --full`
TIER2 backstop step (pre-snapshot) so the whole corpus is still
revalidated every ingest. The remaining evidence-gated question is
whether the backstop cadence suffices long-term, not whether gating
works.

Alternatives considered: `fastjsonschema` (changes the error surface for
a gain incremental gating makes unnecessary — deferred); mtime-based
dirty detection (git status is already the tree's source of truth and
handles renames/deletes — adopted git status); gating lifecycle too
(rejected: cross-file by nature, 0.01 s).

Slices (S0+S1 landed as measurement; S2+S3 are the build):

- S0 baseline timing — DONE 2026-09-21 (denominator above).
- S1 independence audit — DONE 2026-09-21 (verdicts above).
- S2 implement: dirty-set builder (porcelain + untracked) shared by the
  three legs; `--full` escape hatch; schema-file change forces full
  (a changed `doc/okf/*.json` invalidates cached assumptions); git
  absent → degrade to full, never to skip.
- S3 tests + numbers: dirty-break flagged; clean-tree fast path timed
  (best-of-3, solo); full-vs-dirty parity on a seeded dirty tree
  (same fatals); the weakened guarantee documented in-test.
  DONE 2026-09-21 — `tests/test_dirty_scope.py` (12 tests: builder
  parsing + degrade-to-full, per-leg scoping, full-vs-scoped parity,
  weakened-guarantee documentation, wrapper passthrough, main flags);
  numbers in the Appendix.

## 4. Acceptance criteria & shakedown

1. `python3 helpers/validators/static_checks.py` on a clean tree shows
   the three legs near-zero (fast path) with 0 fatal — same verdicts as
   `--full` on the same tree.
2. Breaking one note's frontmatter with three others dirty flags exactly
   that note (dirty-break test); breaking a clean note while others are
   dirty does NOT flag (weakened-guarantee test — documents, not hides).
3. Timing legs run best-of-3, solo, load-recorded — never one run
   (the pool-artifact lesson: window-dependent numbers are not evidence).
4. `make qa` + `make static_checks` green; `make search-fresh` converges.

| Projected outcome | Today | After |
|---|---|---|
| static_checks corpus legs, small-dirty tree | ~1.55 s | milliseconds (fast path) |
| static_checks corpus legs, clean tree | ~1.55 s | milliseconds (empty dirty set) |
| `--full` run | n/a (new flag) | == today's numbers (parity test) |

## 5. Risks

- **Weakened guarantee** — a break in a clean file goes unflagged while
  other files are dirty. Mitigation: documented in-test; `--full`
  exists; default stays full until evidence flips it; `maint-full`
  keeps running full.
- **Dirty-set misses** — new watched file types, schema-file edits, or
  non-porcelain changes (e.g. `skip-worktree`) slipping past the set.
  Mitigation: schema-file change forces full; unknown extensions fall
  back to full; the parity test guards the builder.
- **No-git environments** — mitigation: degrade to full, never skip
  (same pattern as the jsonschema-absent advisory).

## 6. Non-goals

Proposal lifecycle gating (0.01 s, excluded); the default flip (separate
decision on S3 evidence); `fastjsonschema` migration; embed-delta (a)
and pool-granularity (c) from `doc/local/perf/graph_scaling.md` Part 2 §9
§9 (separate arcs); any change to what the checks *assert* (this arc
changes only the file set each run examines).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-21 | `python3 -m helpers.validators.frontmatter_schema` (clean tree) | 0.88 s, 1 fatal → 0 after fix | the 1 fatal was `skylake_igpu_eval.md` `completed_md` int-vs-string; fixed to `"257"` |
| 2026-09-21 | `python3 helpers/validators/static_checks.py` (clean tree) | 5.33 s, all pass (1 non-blocking advisory) | load ~0.8 |
| 2026-09-21 | per-leg isolation via import + `perf_counter` | YAML 0.38 / schema 0.81 / lifecycle 0.01 / OKF 0.36 s, all fatal=0 | read-only; denominator for the fast-path claim |
| 2026-09-21 | S3 leg isolation best-of-3, solo (load ~1.7–2.4) | full YAML 0.373 / schema 0.759 / OKF 0.369 s → scoped-to-empty 0.013 / 0.018 / 0.016 s (sums ≈1.50 s → ≈0.05 s) | fast-path claim lands (~30×); reproduces the S0 denominator |
| 2026-09-21 | S3 end-to-end best-of-3: default vs `--full` vs `--dirty` | default 5.83 / `--full` 5.92 / `--dirty` 4.22 s; identical verdicts (0 fatal, 1 advisory) | default == `--full` (flag is behavior-preserving); `--dirty` banks the corpus-leg ~1.5 s; remaining ~4.2 s is the other 15 checks — a wider fast-path is a separate arc |
| 2026-09-21 | S3 `pytest tests/test_dirty_scope.py` + neighbors | 12 new pass; 251 + 183 neighboring pass; ruff check + format clean | deterministic gate green; `make qa` proper pending operator go |
| 2026-09-21 | default flip + backstop | bare `static_checks.py` prints `dirty-gated: 0 file(s)`, 0 fatal / 1 advisory (== `--full` verdicts); `static-checks --full` appended to `maint.py` TIER2 pre-snapshot; 217 tests pass incl. updated count/order pins + sandbox shim | flip evidence-gated on the S3 rows above; weakened guarantee now default — covered by the maint-full backstop + `--full` escape |
| 2026-09-21 | fastjsonschema spike (deferred §3 alternative) | all 5 schemas compile; Avanti_Feeds fixture 431 µs vs 25 µs (~17× per validation) | re-examined same day (see next row) — spike stands, verdict changed |
| 2026-09-21 | fastjsonschema soundness re-examination | 1456/1456 corpus verdict agreement (0 FP/FN) + 28/28 mutations both-flagged; gate track is sound — verdicts identical, only message count differs | split-track design in pending.md (gate=fast blocking, maint=+advisory full report); awaits go + own arc proposal |
