---
title: "Repair pre-annotation escalation judgment flaws"
status: executed
filed: "2026-10-05"
executed: "2026-10-05"
completed_md: "350"
area: "graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Repair pre-annotation escalation judgment flaws

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

**Date:** 2026-10-05 · **Status:** EXECUTED 2026-10-05 (completed.md #350) ·
**Area:** helpers/graph/triage_preannotate.py, helpers/core/typed_judgment.py.
Execution record: S1–S4 landed same day with hermetic tests (335 passed
across the four touched suites; ruff + ty clean). Eval-v3 regression
re-run through the shipped module: artifacts 37/37, Q2 54/56 (bar
≥53) — the S3 re-judge path is test-verified only (zero verified
flips live: 36 escalations all confirmed the base verdicts).

## 1. Motivation

The 2026-10-05 follow-up audit of the jev/system_one arc
(`doc/local/evaluations/jev_assessment.md` §Follow-up audit) re-ran
every headline number green — then found judgment-logic flaws in the
shipped escalation path by execution, not inspection. Three are
retrieval-correctness bugs (evidence reaches the wrong rows, or never
reaches them); three are agreement/cache accounting flaws (silence
reads as consensus; stale reads as fresh). None has bitten a live
queue yet, because the live queue is small and glm-only — but the
mercury lane and the fresh-event path exist precisely for the rows
these flaws mishandle. Trigger: operator audit request 2026-10-05.

## 2. Evidence (measured 2026-10-05, this box)

| Check | Result | Verdict |
|---|---|---|
| `_relevance_filter` vs tcs.com result on a TCS row | kept (docstring: must never evidence) | bug — initials computation dead |
| `_relevance_filter` vs dbs.com result on a DBS row | dropped | ok (token path) — masks the bug |
| `edition_is_recent("Edition 2027-01-01 ne Sleek")` | True | bug — no lower bound |
| `needs_escalation` consults `fresh_flag` | never (computed into annotations, zero routing call sites) | bug — docstring assumes fresh events surface as low p_fact; the pilot's confident false-rejects (TCS–MHP) disprove that |
| escalation re-judges Q2 | never (Q1-only prompt; `rubric_admit &=` ratchet) | bug — factual Q2 misses unfixable |
| `ab()` with one errored carrier | `agree=True` over the survivor | bug — consensus by default |
| `ask()` cache blob fields | answers/wall/tokens only, no ts/version; empty answers cached | bug — timeless + poisonable |
| scorer + preann3 recompute | matches README exactly (50/54, 37/37; 52/55) | adopt — the keys are sound |

The initials bug is exact, not heuristic: `re.findall(r"[A-Za-z]",
source)` yields characters, so `initials` becomes the whole string
de-spaced (`tataconsultancyservices`) — never a substring of any
host. Full-word domains survive by accident (token substring), which
is why the lane looked healthy.

## 3. Design

Slices in audit suggested order; each lands independently with its
own tests. No prompt-text changes anywhere (BATCH_HDR and the S7
pinned questions are the eval-v3 key surface — S4's fix below is
code-only).

- **S1 — domain filter + fresh routing** (`triage_preannotate.py`).
  Word-initials for the domain gate (`tataconsultancyservices` →
  `tcs`); `needs_escalation` becomes `p_fact < 0.5 OR fresh_flag`
  (confident fresh-event rejects finally escalate). Note the OR is
  deliberate over the narrower fresh∧reject: a fresh ADMIT is exactly
  as suspect — a parametric judge cannot know a fresh event, so
  retrieval is the only guard against a hallucinated admit on one.
  `edition_is_recent` gains a lower bound (a future date is not
  fresh). Rejected: folding recency into p_fact — the model must not
  see the routing signal. Also flips `DEFAULT_MODEL` glm-5.3 →
  **glm-5.3-flash** (operator ruling 2026-10-05: non-flash
  concurrency limits too low for lane work — measured same day,
  5.3 ≈ 3 batches/6 min vs flash ≈ 9 batches/2 min at workers=4 in
  the external-eval adapter); re-run the eval-v3 regression key on
  flash before shipping (floors: 37/37 artifact rejection, Q2 ≥ 53 —
  flash's v3 leg scored exactly 53/56, meets the bar).
- **S2 — timeless cache** (`typed_judgment.py`). Stamp blobs with
  `ts` at write (carrier id already keys the cache path); never cache
  empty `answers`; add a `cache_age_days` reporter so the drift lane
  can distinguish stale reads from provider drift. Rejected: TTL
  eviction — deletion policy is the operator's call; visibility first.
- **S3 — Q2 re-judge + survivor unknowns.** Escalation re-asks Q2 for
  rows whose fact flipped false→true with quoted evidence (rubric can
  grant, not only revoke); `ab()`/`gap_of`/`brief()` return
  unknown-not-agree on single survivors (triage surfaces keep their
  both-required guard; the library stops lying for future callers).
  Rejected: re-judging Q2 for all rows — cost without signal.
- **S4 — honesty tail, same files.** Sitting render prints
  field-missing markers instead of `0.0` defaults (journal already
  keeps Nones); quote-without-URL verdicts get a flag instead of
  silent drop; `rank_contentious` docstring corrected to the
  `> AGREE_TOL` filter; `judge_rows` validates boolean types
  (string `"false"` is truthy under `bool()`); judge/scorer/dump
  warn on id-coverage shrinkage instead of intersecting silently.
  Rejected: strict-fail on dropout — best-effort lanes stay best
  effort, but loud.

## 4. Acceptance criteria & shakedown

1. `tcs.com` result on a TCS row drops while a third-party result on
   the same row passes: `.venv/bin/python3 -c` probe (Appendix run
   1) green; dbs.com control still green.
2. Fresh rows escalate in both directions: synthetic reject
   (`p_fact=0.9/false`, recent edition) and synthetic admit
   (`p_fact=0.9/true`, recent edition) in `needs_escalation` unit
   coverage → True; non-fresh confident accept (`p_fact=0.9/true`,
   no edition) → False (today's behavior preserved);
   `tests/test_triage_preannotate.py` + S5 sitting tests green.
3. `tests/test_typed_judgment.py` green with new survivor-unknown
   cases; cache-blob has `ts`, empty answers never touch disk
   (mutation: delete-blob re-ask still works).
4. `make lint`, `make types`, `make static-checks` green; ruff clean
   on touched files.
5. Eval-v3 regression key re-run through the shipped module: floors
   hold (37/37 artifact, Q2 bar ≥53) — the prompt text is untouched,
   so this is a guard, not a requalification.

No ontology eval-gate bullet: nothing here alters query-visible
semantics (no rosters, crosswalks, hierarchies, or extractor rules
change) — N/A stated explicitly per the house rule.

| Projected outcome | Today | After |
|---|---|---|
| Initialism-domain self-evidence | kept (wrong) | dropped |
| Confident fresh-event rejects escalated | never | always |
| Fact-proven Q2 misses recoverable | never | yes, quoted only |
| Single-survivor agreement | agree (wrong) | unknown |
| Cache blobs timestamped | no | yes; empty never cached |

## 5. Risks

- **Fresh-flag escalation widens retrieval spend** — bounded: one
  search per row, batched single re-judge (existing S2 shape);
  `--no-escalate` still skips.
- **Survivor-unknown changes brief copy** — single-carrier cards now
  say unknown instead of AGREE; the triage render's both-required
  guard means live copy barely moves.
- **Q2 re-judge cost** — restricted to flipped rows only (a handful
  per run by measurement).

## 6. Non-goals

No prompt-text changes (eval-v3 key stays valid); no new carriers;
no mercury-lane producer (that is the gaps proposal); no gate
integration of judgment calls (assessment doctrinal guardrail
stands); no cache eviction policy.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-05 | `pilot_score3.py` | glm-5.3 50/54, 37/37; flash 50/53; span ctx Q2 16/36 | matches README |
| 2026-10-05 | preann3 recompute | Q1 52/56, Q2 55/56 | matches acceptance record |
| 2026-10-05 | `_relevance_filter` probe, tcs.com | kept=True | bug; control dbs.com dropped |
| 2026-10-05 | `edition_is_recent` future date | True | bug; no-date → False ok |
| 2026-10-05 | legs coverage sweep | 56/56 all chat legs, zero string bools | silent-denominator hazard unmaterialized |
| 2026-10-05 | `extractor_gate_key.py` | 59 items, 49/49 noise, 10/10 non-noise | docstring says 58/7 — drift |
| 2026-10-05 | `derive_metrics_gate_key.py` | PASS 15 items | green |
| 2026-10-05 | external carrier runs, rate-limit `{}`s | 255 persisted into `~/.cache/typed_judgment` as answers | S2 flaw demonstrated live, not modeled |
