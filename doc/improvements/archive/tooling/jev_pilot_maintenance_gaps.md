---
title: "Close the jev-pilot maintenance gaps"
status: executed
filed: "2026-10-05"
executed: "2026-10-05"
completed_md: "349"
area: "tooling"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Close the jev-pilot maintenance gaps

<!--
House proposal skeleton (matches the executed corpus's shape — see
doc/improvements/archive/ for real examples). Rules:

- File the proposal BEFORE implementing multi-slice work (house rule
  2026-08-21). One proposal per arc; slices inside it.
- Every number is MEASURED on this box — a proposal with unmeasured
  claims gets challenged; keep the raw log in the Appendix.
- Tables for comparisons, prose for causality.
- On EXECUTED: git mv to ../archive/<topic>/, completed.md entry (unique
  number), pending.md sweep, archive/README topic line, README pointer
  reset, `make search-fresh APPLY=1` — the full checklist lives in the
  proposals README.
- If a proposals frontmatter contract lands, this template gains it —
  until then the bold-line header below is the canonical status field.
-->

**Date:** 2026-10-05 · **Status:** EXECUTED 2026-10-05 (completed.md #349) ·
**Area:** Makefile advisory wiring, jev_pilot keys/runners, truncate_mention.
Execution record: the S2 evidence arm executed same day (external evals
vendored, 4-carrier legs + canonical rescore, carrier preference decided
flash > free-jev > mercury-decide > paid jev, mercury-2 lane removed with
an allowlist gate after unapproved spend). **Deferred:** S1 advisory
wiring, S3 truncation rule (+ontology gate), S4 runner re-pointing, S5
tol naming, and the S2 mercury producer — remainder tracked in the §3
slices below, which stand as specified.

## 1. Motivation

The 2026-10-05 follow-up audit
(`doc/local/evaluations/jev_assessment.md` §Follow-up audit) found
the jev-pilot evidence in good shape but its maintenance tail already
rotting: the two gate keys run nowhere (one already drifted from its
own docstring), the mercury lane has no producer, the fresh-date
escalation path was never evaluated, and 7 of 13 pilot runners carry
absolute roots — 3 at a deleted directory, 4 machine-pinned to the
worktree. Each gap is small; together they mean the
"frozen regression set" the framework leans on is frozen by accident,
not by ownership. Trigger: operator audit request 2026-10-05.

## 2. Evidence (measured 2026-10-05, this box)

| Check | Result | Verdict |
|---|---|---|
| Makefile hits for gate keys / jev_pilot / preannotate | zero (`rg` over Makefile) | gap — no owner, no schedule |
| extractor key docstring vs dataset | docstring 58 items / 7 admit; dataset 59 / 8 | gap — drifted at #343, prose not updated |
| extractor key `score.json` date after re-run | stamps `2026-10-04` on a 2026-10-05 run | gap — fixed string, silently wrong |
| mercury annotations producer in tree | none (definition + read + test only) | gap — 2 hand-placed rows, lane unwired |
| eval rows with non-empty edition | 0/56 | gap — fresh path never evaluated |
| pilot scripts with absolute roots | 7 of 13 — dead tmp ROOT ×3 (`judge3`, `respan_judge3`, `audit84_screen`); worktree-absolute ×4, live today via the worktree `doc/local`→main symlink but machine-pinned (`mercury_callout`, `rebuild_context`, `regen_candidates`, `preann_regression3`) | gap — 3 broken, 4 unportable |
| extractor misses sharing a pattern | 2/5: leading qualitative adjective kept ("European giant DWS Group") | rule opportunity |
| derive S4 `tol = 0.2` vs `AGREE_TOL` | equal values, different meanings (ambiguity vs agreement) | naming hazard |
| external runs vs consensus (agent_trace, 1124) | mercury-2 **0.660** clean; mercury-decide 0.683 on answered, 255 quota-errors pending reset; genuine Jev 0.716 (their table) | evidence — S2 call now has numbers |
| free-lane quota vs cents | daily `free-models-per-day-high-balance` cap hit mid-run; spend $0.00 throughout | requirement — producer paces by quota, not spend |

## 3. Design

Slices in audit suggested order; each lands independently.

- **S1 — keys get an owner.** Wire both gate keys into `make
  advisory` (hermetic, seconds-class — measured: extractor key runs
  in-process with no network); fix the extractor docstring (58/7 →
  59/8) and derive the score `date` from the run instead of the
  fixed string; derive `build_names` live-row resolutions from
  `human`-labeled items instead of the hardcoded pair. Rejected:
  qa-gating the keys — provider-adjacent hermetic checks belong in
  advisory, and judgment calls never enter `make qa` (doctrine).
- **S2 — mercury lane: evidence, then produce or declare.** The
  decision gets measured first. Vendor the TypeSafe WorkflowEvals
  datasets (`agent_trace_observability` first, then
  `security_incidents`; invoice/customer-service parked) and run our
  carriers over them: mercury-decide / mercury-2 / mercury-2.5 beside
  glm-5.3-as-baseline, scored with *their* references under *their*
  conventions (explicit missing, errors count as wrong). The tuning
  target is the harness around mercury (question text, thresholds),
  never third-party weights; eval-v3 stays the regression floor so
  external tuning cannot regress the house gate. Then either a
  committed producer for `_pending_annotations_mercury.jsonl`
  (module-ab pilot output → JSONL writer, schema-pinned) or, if the
  operator defers the second carrier, an explicit single-carrier
  mode: sitting copy says "mercury unavailable" instead of the
  vacuous "no contested rows" agreement message. Data placement:
  vendored corpora in `bench_data/workflowevals/` (the house pattern
  — congress-bills, trivago-clicks live there, gitignored), adapter +
  runs + analysis in `jev_pilot/` beside the legs and gate keys.
  License audit per dataset first (collection notes separate
  dataset licenses). Rejected: silently keeping the hand-placed rows
  — they are unreproducible evidence; and adopting their keys as
  qualification — consensus references tune toward big-model
  agreement, our operator-labeled keys stay the boss.
  Measured 2026-10-05 (`legs_ext/agent_trace_observability/EVIDENCE.md`,
  canonical rescore via `rescore_legs.py` after two same-day scorer
  bugs): **glm-5.3-flash 0.8568 vs genuine Jev 0.8612** (gap 0.004)
  vs mercury-2 0.7829; mercury-decide partial with 328 quota-errors
  pending reset. Flash × mercury-2 agree 85.1% with flash right 67%
  of disagreements; flash × Jev agree 89.3% at a 48/52 split. Misses
  cluster on evidence-class questions (escalation, policy, grounding).
  Spend: jev leg 900K tok / $0.03; OpenRouter key cumulative $1.45
  (shared key, no cap) — paid runs need explicit per-run approval.
  Decided 2026-10-05 (operator): carrier preference **flash >
  jev-1.13-free > mercury-decide (free) > jev-1.13 (paid)** — flash
  retained as default, genuine-article free Jev as backup, mercury
  kept as the free second lane, paid Jev behind it.
  The free-lane daily quota — not cents — is the producer's binding
  constraint; the decide clean rerun stays pending as calibration
  data, not as a gate.
- **S3 — truncation adjective rule** (`truncate_mention`). Strip
  leading qualitative adjectives / generic descriptors
  ("European giant", "leading", "global") from captured mentions
  before canonical comparison. This alters extractor rules, so the
  eval-gate bullet below is mandatory: `ontology_eval_gate.py` over
  the frozen set (zero regressions) + gate3 key re-run (truncation
  5/9 must improve, noise/non-noise recalls must hold 1.0).
  Rejected: a stoplist in the noise gate — truncation owns mention
  shape, the gate owns admission.
- **S4 — runners re-pointed (portability, not repair).** The 7
  scripts resolve repo and pilot roots `__file__`-relative (the
  `pilot_score3.py` pattern), dropping the dead TMPDIR ROOT and the
  worktree pinning. Note the 4 worktree-absolute scripts run today —
  the worktree's `doc/local` symlinks to main, where the work
  landed — so this slice buys checkout-independence, not revival.
  Smoke: `--help` + import on each, from main. Rejected: deleting
  the runners — eval-v1/v2 already lost re-runnability in the 10-05
  cleanup; v3 runners stay runnable.
- **S5 — name the S4 tolerance.** `AMBIGUITY_TOL` in
  `derive_insights.py` for the `|P−0.5|` shortlist cut, documented
  as distinct from `AGREE_TOL` (cross-carrier gap). Value unchanged
  (0.2); the point is that the next reader cannot "deduplicate"
  them. One-line + test comment. Rejected: importing AGREE_TOL
  there — that would assert a sameness that is false.

## 4. Acceptance criteria & shakedown

1. `make advisory` runs both gate keys green; extractor docstring
   matches its dataset counts; a re-run stamps today's date.
2. External evidence run first: vendored WorkflowEvals corpora in
   `bench_data/workflowevals/`, adapter + carrier runs in `jev_pilot/`,
   eval-v3 floors held; then the mercury lane resolved either way —
   producer committed with a schema test, or single-carrier copy
   renders and the vacuous agreement message is gone (sitting test
   asserts the distinction).
3. **Eval-gate (mandatory — S3 alters extractor rules):**
   `helpers/misc/ontology_eval_gate.py` over the frozen question
   set (`helpers/misc/ontology_questions.json`) between dry-run and
   canonical apply — zero regressions, no undeclared changes
   (ontology_governance S2); plus gate3 truncation accuracy up from
   5/9 with noise/non-noise recalls held at 1.0.
4. All 7 re-pointed runners import clean from a bare checkout path
   (no `/mnt/data/tmp/jev-pilot`, no worktree); `pilot_score3.py`
   output unchanged.
5. `make lint`, `make types`, `make static-checks` green.

| Projected outcome | Today | After |
|---|---|---|
| Gate keys scheduled | nowhere | advisory |
| Extractor docstring vs dataset | 58/7 vs 59/8 | match, date derived |
| Mercury lane state | hand rows, silent glm-only | produced or declared |
| Fresh-date path eval coverage | 0/56 rows | evaluated or explicitly parked |
| Absolute-rooted pilot runners | 7 | 0 |
| Truncation accuracy (gate3) | 5/9 | improves, recalls held |

## 5. Risks

- **Advisory runtime creep** — both keys are hermetic and fast;
  if advisory budgets complain, the keys move to a weekly lane,
  not back to manual.
- **S3 over-strips** — adjective rules can eat real name parts
  ("Good" in a proper noun); the gate3 key + ontology gate are the
  guard, and the rule stays conservative (leading descriptors only).
- **S2 producer scope** — if mercury decisions-API access lapses,
  the declare-single-carrier fallback keeps the slice landable
  without the carrier.

## 6. Non-goals

No prompt-text changes; no new carriers or transports; no Q1/Q2
logic changes (those are the flaws proposal); no `make qa`
integration for anything model-adjacent; the known-false LVB accept
row stays in the extractor key (regression pins operator behavior,
not truth).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-05 | `rg gate3\|extractor_gate_key\|… Makefile` | zero hits | S1 trigger |
| 2026-10-05 | `extractor_gate_key.py` | 59 items; misses listed (2 adjective-class) | S1+S3 trigger |
| 2026-10-05 | `derive_metrics_gate_key.py` | PASS 15 items | baseline |
| 2026-10-05 | `rg MERCURY_ANNOTATIONS helpers/ tests/` | definition + read + test only | S2 trigger |
| 2026-10-05 | edition coverage over eval3 | 0/56 non-empty | fresh path unevaluated |
| 2026-10-05 | `rg stax/worktrees\|/mnt/data/tmp/jev-pilot jev_pilot/*.py` | 7 files | S4 trigger |
