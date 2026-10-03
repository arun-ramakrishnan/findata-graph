---
title: Pre-annotation + retrieval escalation for the relations triage queue
status: implemented
filed: '2026-10-03'
area: graph
---

# Proposal: Pre-annotation + retrieval escalation for the relations triage queue

**Date**: 2026-10-03
**Status**: IMPLEMENTED (S1–S2–S4; S3 unchanged). Module:
`helpers/graph/triage_preannotate.py`; tests:
`tests/test_triage_preannotate.py`; acceptance record:
`../../local/evaluations/jev_pilot/preannotate/` — artifact rejection
37/37 (floor held), Q2 55/56 (bar ≥53), TCS-MHP escalation probe flipped
with quoted evidence + URL (operator-supplied URL; autonomous search
providers remain the weak leg — browser/search-API lane is the
follow-up). Live-queue dry-run (criterion 3) awaits the next natural
queue fill.
**Depends on**: `triage_pending_relations.py` queue machinery (S1–S3,
archived `../archive/graph/pending_relations_triage.md`); B2 relation
sidecars; the Jev-pattern pilot record (`../../local/evaluations/jev_pilot/`,
key artifacts `../../local/evaluations/jev_assessment.md` §Pilot results).
**Trigger**: operator 2026-10-03, after the pilot concluded — *"yes lets do
the escalation arm"*.

## Problem

The relations triage queue is reviewed fully manually (third full-manual
pass recorded at the S1 triage). The harness-assisted accepts that bypassed
structure produced 16 bad edges, found and fixed in the 2026-10-03 audit
(`jev_pilot/CLEANUP.md`): a collapsed deal recorded as an acquisition, a
mangled alias as a target, wrong-type JVs, and 11 mis-typed or absurd
peer/competition edges. The pilot measured why: LLM judges fed bare
fragments degenerate to all-noise (eval-v1), and even the best parametric
judge (glm-5.3) false-rejects events newer than its knowledge (all four
carriers missed TCS→MHP, announced 2026-08-24).

## Backdrop (from `../../local/evaluations/jev_assessment.md`)

The Jev evaluation (same day) settled the question this proposal
operationalizes. The DuckDB extension is irrelevant — our judgment
surface is Python text-file queues — but the **typed-judgment pattern**
(yes/no-with-probability per row, batched, cached, cost-capped) fits the
relations triage queue almost one-to-one. The pilot then measured, on
the graph_edges queue, what works and what must never ship:

- **Fragment-shaped judgment degenerates.** eval-v1 (entity + bare
  mention, assertion-demanding predicate) drove all carriers to
  unanimous all-noise — the instrument, not the model, was broken.
- **Context is the complete fix for artifact admission.** eval-v3
  attached the mentioning sentence: glm-5.3, flash AND space-bunny
  reject 37/37 co-mention artifacts; Q1 stayed knowledge-bound. Q1/Q2
  must be split; the generic-collective counterparty rule must be
  stated in the prompt.
- **span-01-lite is disqualified from admission** (Q2 collapses with
  context, 16/36 — would admit the false LVB–Clix edge).
- **The graph already carries the cost of skipping structure:** 16 bad
  edges from ad-hoc harness-assisted accepts (collapsed LVB deal,
  mangled Kering Beauté alias, wrong-type JVs), all removed in the
  2026-10-03 cleanup (`jev_pilot/CLEANUP.md`).
- **The deterministic pipeline has zero LLM calls** (verified via
  `model_usage.duckdb`); glm lives in the agent-harness lane. So this
  work is workflow discipline over the queue, not pipeline surgery —
  the human `--apply-decisions` gate stays the only writer.

Measured endpoints feeding the design: context-equipped glm-5.3 is
production-viable (100% artifact precision); committees do not beat it;
vault prose beats model knowledge on obscure facts; fresh events need
retrieval (TCS→MHP, announced 2026-08-24).

## Approach

- **S1 — pre-annotator — SHIPPED AS A STANDALONE MODULE.** Design note:
  the flag variant (`triage_pending_relations.py --pre-annotate`) was
  NOT built; a two-command flow was chosen instead, so the judge can be
  re-run without regenerating the report and vice versa:
  1. `.venv/bin/python3 -m helpers.graph.triage_preannotate` (report-only;
     reads the queue, writes `findata/Misc/_pending_annotations.jsonl`)
  2. `triage_pending_relations.py --report` renders those annotations
     beside each row (`load_annotations`, keyed by row triple)
  Per row: attach the match context (the row's own quote when present,
  else a regenerated `_extract_quote_around` window), then call glm-5.3
  (zai coding-plan lane, temperature 0) with the pinned two-question
  prompt — Q1 *factually real as stated* / Q2 *house rubric admit* with
  the generic-collective counterparty rule stated. Emit
  `{p_fact, rubric_admit, p_rubric, fresh_flag}` per row. glm-5.3-flash
  optional as a cheap second vote.
- **S2 — retrieval escalation — SHIPPED WITH TWO CORRECTIONS.** Rows with
  low confidence (`p_fact < 0.5` — the `p_*` fields are the model's
  CONFIDENCE in its boolean, not P(truth); deriving booleans from them
  inverts confident negatives, measured 35/56 vs 55/56) and either a
  recent-date context flag route to a retrieval substep: web search
  (DDG html with backoff, Bing fallback) → snippet extraction → batched
  Q1 re-judge with retrieved evidence → fused verdict with the evidence
  URL and supporting quote recorded. Flipping a verdict requires BOTH an
  explicit supporting quote AND a non-empty URL. `rubric_admit` is left
  at the base verdict on flip by design: the evidence re-judges the
  FACT, admission of a fresh edge stays with the human gate.
- **S3 — human gate unchanged.** `--apply-decisions` stays operator-only;
  annotations are advisory report fields. No auto-writes to `graph_edges`.
- **S4 — instrumentation.** The pilot's eval-v3 set (56 items, contexts,
  web-anchored key, `jev_pilot/dataset/eval3/`) becomes the regression key
  for any prompt or model change.

## What does NOT change (and why no ontology eval-gate bullet)

Extractor rules, rosters, crosswalks, hierarchies — no query-visible
semantics are altered (the S2 gate rule in this README does not trigger).
The pre-annotator only reads the queue and writes advisory annotations.

## Acceptance criteria

1. eval-v3 regression floor: Q2 artifact rejection stays 37/37; overall
   Q2 ≥ 53/56 for glm-5.3 (currently 54/56).
2. Escalation recall probe: the TCS→MHP fresh-event case (and one
   synthetic fresh probe) flips to factually-true post-retrieval with an
   evidence URL; pre-escalation failure is already on record.
3. Live dry-run: the next natural `--report` queue fill runs annotated
   end-to-end with zero writes; annotations render in the report.
4. Resilience: search-provider rate limits degrade to needs-retry
   annotations, never hangs; every LLM/retrieval call bounded-retry.
5. Cost: $0 measured (zai coding-plan subscription; no per-token spend).

## Risks

- Retrieval-sourced hallucination — mitigated by quote-required flips and
  URL evidence in the annotation.
- Prompt drift — prompt text pinned in-repo; eval-v3 key gates changes.
- Provider drift on the zai plan (model IDs/limits) — flash fallback path.
- Web search fragility (DDG captcha observed 2026-10-03) — Bing fallback,
  bounded retries.

## Alternatives (measured, rejected)

- Typed decisions API (`respan/span-01-lite:free`): conservative on facts
  but Q2 collapses with context (16/36) — rejected for any admission role.
- Committee voting (glm-5.3 + flash + space-bunny): does not beat glm-5.3
  alone on facts; adds cost without accuracy.
- Status quo manual triage: produced the 16 bad edges and takes a full
  manual pass per queue fill.

## Remaining rollout

1. **Live-queue dry-run** (criterion 3) — awaiting the next natural
   `_pending_relations.txt` fill; the queue is empty today. Command:
   `.venv/bin/python3 -m helpers.graph.triage_preannotate` (report-only;
   annotations land in `findata/Misc/_pending_annotations.jsonl`).
2. **Report integration — DONE (2026-10-03).** `write_report` renders
   annotations beside each prose row (verdict, p values, ESC/NEEDS-RETRY,
   evidence URL, trimmed quote) plus a header count;
   `load_annotations` keys by the row triple so the report's sha256
   prefix ids match. Lazy import (cycle-safe), best-effort — a missing
   or broken annotations file never blocks the report. Tests in
   `tests/test_triage_pending_relations.py::TestPreannotationRendering`.
3. **Search lane** — wire a real browser/search-API path into
   `_search_evidence`. Measured 2026-10-03: Google serves a JS wall to
   non-browser agents, DDG html captcha'd, Bing returns brand
   homepages for niche queries, tcs.com 403s plain fetchers. The
   TCS-MHP probe flipped via an operator-supplied URL (Financial
   Express); autonomous retrieval remains the weak leg.
4. **Prompt-pin discipline** — the batch prompt is the regression-key
   surface; ANY change to `BATCH_HDR`/`BATCH_SCHEMA` or the model id
   re-runs the eval-v3 key (floor: artifacts 37/37, Q2 ≥ 53/56).
5. **Optional flash second vote** — cheap; unmeasured benefit (the
   committee finding says do not expect accuracy gains).
