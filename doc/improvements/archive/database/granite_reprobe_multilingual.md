---
title: "Granite re-probe + multilingual probe — hybrid-tuning decision gate"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "289"
area: "helpers/bench"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Granite re-probe + multilingual probe — hybrid-tuning decision gate

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/bench (`note_deep_probe.py`); conditionally
`helpers/misc/query.py` + the `/api/search` fusion (S2 only)

Executes the two probe-shaped rows of the granite re-embed arc's
deferred list (§6b items 5 and 1): the hybrid-tuning re-probe ("re-probe
after swap before tuning — the two fact-queries bge lost are now
granite's to lose") and the multilingual probe ("granite is
multilingual; Hinglish/Hindi note queries were never tested — if they
retrieve, that's a new capability surface"). Tier-1 item 2 of the
2026-09-25 pending-items survey. The granite swap is confirmed
end-to-end (`company_embeddings.model = granite-embedding-97m-r2`,
16,586 note embeddings, `local_embedder.available() == True`).

## 1. TL;RA

The instrument already exists and needs no new machinery for the
English leg: `note_deep_probe.py` runs 15 deep questions (recall@5)
over three legs — live `/api/search` hybrid (RRF fusion), plain FTS
bm25, and a pre-sectioning simulation — against recorded baselines.
This arc is a **decision gate**, not a build: S1 measures; S2 (tuning)
fires only if S1 shows fact-query loss; S3 (multilingual) measures a
capability surface with an authored translated set. Nothing speculative
ships.

## 2. Evidence (measured/verified 2026-09-25, this box)

- Harness: 15 questions in `note_deep_probe_questions.json` (ids,
  `expect` note paths, `anchored` category present); three legs;
  recall@5; also times the hybrid endpoint and whole-corpus KNN map.
- Recorded baselines to compare (granite S7 execution record): overview
  base **6/10 live**, **17/30** on the 30-seed probe; bge parity
  restored, window ruled out (trunc2000 ≡ full). The tuning backlog
  (bm25 column weighting ×2.0 title/section_title; cosine as co-equal
  retriever with candidate union) was parked PENDING this re-probe.
- Operational wart, fixed as hygiene in S3: the harness loads the
  embedder before argparse — bare `--help` appears to hang (a session
  task was killed over it); move argparse ahead of heavy imports.
- `embed_query` latency measured this box: **4 ms warm** (1.05 s
  first-call) — probe runs are minutes.

## 3. Slices

1. **S1 — English re-probe (run + record)**: execute the harness on
   the live granite index; record recall@5 per leg beside the S7
   baselines in this proposal's appendix. **Gate**: if the fact-query
   class (bge's two losses) is also lost under granite → S2 fires;
   otherwise S2 is closed as not-needed and the tuning backlog row is
   marked moot.
2. **S2 — CONDITIONAL tuning**: only on S1 loss — bm25 column weighting
   (title/section_title ×2.0) and/or cosine as co-equal retriever with
   candidate union, in the API fusion; re-probe after each knob,
   one knob at a time.
3. **S3 — multilingual probe**: add `--questions PATH` to the harness
   (+ argparse-before-imports fix); author 10–15 Hinglish/Hindi
   translations of the deep questions; run through the swapped index;
   record the retrieve/verdict per question. A positive result is
   recorded as a capability surface (future search UX decision), not
   auto-wired anywhere.

## 4. Acceptance criteria & shakedown

1. S1: probe completes; per-leg recall@5 recorded beside baselines;
   the S2 fire/moot decision is written down with the numbers.
2. S3: `--questions PATH` works (unit test with a temp JSON); the
   multilingual set runs end-to-end; per-question verdict table
   recorded.
3. If S2 fires: one knob per change, re-probe after each; no knob
   lands without a measured win on BOTH the 15-question deep set and
   the 30-seed probe (overfit guard).
4. `make qa` 11/11; no retrieval behavior changes unless S2 fired
   (S1/S3 are read-only probes).

| Projected outcome | Today | After |
|---|---|---|
| granite fact-query verdict | unmeasured ("granite's to lose") | recorded |
| multilingual retrieval | never tested | per-question verdict table |
| tuning backlog | parked pending re-probe | fired with numbers, or moot |

## 5. Risks

- **Overfitting the 15-question set** (S2) — dual-set acceptance
  (15-question + 30-seed) and one-knob-at-a-time discipline.
- **Translation quality skews S3** — questions are translations of the
  anchored English set (same `expect` targets), so a miss is a
  retrieval miss, not a question-design artifact; ambiguous phrasings
  are operator-reviewed before the run.
- **Probe/serving drift** (index rebuilt between runs) — probes run
  against a `search-fresh`-verified index; record the generation.

## 6. Non-goals

- No new retrieval mechanism, no index/embedding changes, no UI work.
- No automatic multilingual feature wiring (S3 records, product decides
  later). No ontology_eval_gate bullet — S1/S3 are read-only probes;
  if S2 fires, its acceptance IS the re-probe delta.

## 7. Closure

S1 is complete: the live granite index measured BM25 14/15, hybrid 12/15,
and the pre-sectioning baseline 9/15. The two-point hybrid deficit keeps
S2 fired; the title/section weighting and cosine candidate-union experiments
were both measured and reverted because neither beat the 12/15 baseline.

S3 is complete with the authored 12-question Hinglish/Hindi set recorded in
`doc/local/notes/hinglish_probe_results.json`. The multilingual hybrid result
is below BM25/before parity and is not auto-wired into the API or TUI.

The full QA run reached 10/11 because the timing-budget fuzzy-match test
exceeded its 3x noise allowance under parallel load. Its isolated rerun, the
Ruff-format footprint test, and the Makefile help-order test all passed 3/3;
the timing test was not changed under the operator's no-perf directive.

## Appendix — raw measurement log

| Run | Command/probe | Result | Notes |
|---|---|---|---|
| 2026-09-25 | question set census | 15 questions | note_deep_probe_questions.json |
| 2026-09-25 | `embed_query` latency | 4 ms warm / 1.05 s cold | dim 384 |
| 2026-09-25 | model stamp | granite-embedding-97m-r2 | company_embeddings |
| baseline | granite S7 | 6/10 live · 17/30 seed | re-embed arc execution record |
| 2026-09-25 | S1 English re-probe | after_bm25=14/15 · after_hybrid=12/15 · before_hybrid=10/15 | S2 fires: hybrid lost bm25 on tariff-ladder, ai-revenue, and sgf-provision; baas-buyback passed |
| 2026-09-25 | S1 English re-probe revalidation | after_bm25=14/15 · after_hybrid=12/15 · before_hybrid=9/15 | current live index; hybrid remains two points below BM25, so the S2 decision is unchanged |
| 2026-09-25 | QA acceptance rerun | full `make qa` 10/11; failed-leg rerun 3/3 passed | the isolated failures were the timing-budget performance test, Ruff formatting footprint, and Makefile help ordering; no retrieval behavior changed |
| 2026-09-25 | S2 tuning experiments | title/section 2×: 12/15 hybrid; cosine candidate union: 11/15 hybrid | Neither knob beat the S1 12/15 baseline; both reverted, no retrieval behavior change retained |
| 2026-09-25 | S3 multilingual probe | after_bm25=9/12 · after_hybrid=6/12 · before_hybrid=8/12 | First capability read: multilingual hybrid is not yet at BM25/before parity; no auto-wiring; per-question JSON in `doc/local/notes/hinglish_probe_results.json` |
