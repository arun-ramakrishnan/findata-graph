---
title: "OCR review pipeline hardening — procedure, rules, wrapper, mode decision"
status: proposed
filed: "2026-09-28"
executed: null
completed_md: null
area: "doc/procedures/ocr_review.md, .opencodereview/rule.json, Makefile, helpers/dev/"
---

# OCR review pipeline hardening — procedure, rules, wrapper, mode decision

**Date:** 2026-09-28 · **Status:** PROPOSED ·
**Area:** `doc/procedures/ocr_review.md` (new), `.opencodereview/rule.json` (new),
`Makefile`, `helpers/dev/` (brief-generator helper).

## 1. Motivation

The delegation-mode trial (`doc/local/engineering/code_review.md`) produced
real, verified findings on `ef8a17d4` and `0b63ce4` at zero OCR-side LLM cost.
But six robustness gaps (ranked in code_review.md §ef8a17d4 review) prevent
trusting OCR-managed mode, and the loop is tribal knowledge — it worked because
this session improvised it. This proposal closes the gaps and pins the
procedure so any agent (prime, opencode, a future harness) can drive it
reproducibly.

## 2. Gaps addressed

1. Test-spine exclusion wrong-by-default (tests are the product here).
2. Mojo gets generic `default.md` coverage.
3. Workspace-mode emptiness on refreshed stacks.
4. Recall lower by design (precision-over-noise).
5. Judgment robustness is the host agent's, not OCR's.
6. Process is tribal knowledge (no procedure doc, no wrapper).

## 3. Design / Slices

- **S1 — `.opencodereview/rule.json`** (project, commit-safe) — **LANDED,
  verified**: `include` `tests/**/*.py`; exclude `static/**/*.bundle.js`,
  `static/**/*.bundle.js.map`, `node_modules/**`, `findata/**`, `doc/local/**`.
  Recursive bundle globs (not `static/*.bundle.js`) so nested asset dirs are
  covered, and the sourcemap + vendored-`node_modules` excludes keep minified
  noise out of the diff. `ocr delegate preview` on `csr_lane_fixes` went
  3/10 → 5/10 reviewable files, and from zero visible test lines to both test
  files (`tests/test_csr.py` +156, `tests/test_note_embeddings.py` +23). This
  was the prerequisite for trusting OCR selection: before it landed, OCR
  silently under-reviewed the test spine, which is exactly how an inert CSR
  lane plus two green-but-broken defects shipped. **S1 is the filter only** —
  convention rules moved to S4.
- **S2 — `doc/procedures/ocr_review.md`** (tracked; operator-approved): pin the
  loop — **when** (patch refresh / pre-arc landing / on demand; never a
  `make qa` leg), **selection** (stgit mapping: `--commit $(git rev-parse HEAD)`
  for top patch, `--from HEAD~N --to HEAD` for the stack; workspace mode is
  empty on a refreshed stack), **grounding** (brief file from the four repo
  indexes — doc/script/gate/convo — plus the commit body OCR promotes),
  **review + triage** (OCR severity contract: Critical/High always, Medium with
  context, Low discarded unless valuable; verdicts only under the activated
  repo venv), **apply-and-verify** (host applies only operator-accepted diffs
  from `ocr session comments --json`, then fresh review + `ocr session compare`
  proves resolution), **output** (advisory only). **Step 0:** ask which ref to
  review before touching the tree — never default to the moving stgit top.
- **S3 — `make review-patch` wrapper — DEMOTED to the deterministic part only.**
  The original scope (stgit mapping → `delegate preview` → `delegate rule` →
  generated brief → host review) automated a loop the host agent can drive
  directly, and the brief generator is strictly worse than the host writing
  the brief from the four indexes. Keep only what is deterministic and not
  already trivial: the stgit→ref mapping, selection, and the **assertion that
  a `tests/` path is selected** (that check has real teeth — it is the defect
  class that shipped). No `helpers/dev/` brief generator. Gate coupling off.
- **S4 — mode decision — DECIDED 2026-09-28: delegation is the loop; managed
  review is an occasional second opinion.** The case against making OCR a
  pipeline stage:
  1. **Custom rules do not work** (S4a), so OCR cannot carry this repo's house
     conventions — the single most valuable thing a review tool would do here.
  2. **OCR cannot run the gate.** Its tool inventory is read-only context
     tools; no shell. Every deterministic check stays with the host agent and
     `make qa`, so OCR could only ever re-report what the gate already says.
  3. **The host agent strictly dominates on verification** — it can run the
     tests, mutate them, query the four indexes, and trace callers.
  4. **Cost.** A managed pass over 5 files cost 273,562 tokens and 6m1s, and
     still finished `partial` on HTTP 429. Delegation costs nothing.

  What OCR retains, and why it is still worth keeping: deterministic
  selection + checklist (`delegate preview`/`delegate rule`), and — the
  decisive datum — a managed pass **found a real defect in our own test that
  the delegation review missed** (a `near_duplicate_notes` pruning test whose
  docstring claimed coverage its fixture could not reach). So: managed review
  on high-stakes patches, not as a stage. Evidence log:
  `doc/local/engineering/code_review.md`.

  **The value is contingent on model diversity — state this explicitly.** The
  two lanes only form a review because they are *different reviewers*:
  delegation runs on the **host agent** (2026-09-28: Space Bunny), managed ran
  on **GLM** (`glm-5.3` served, confirmed in session metadata). Delegation is
  not an independent check on the host — it *is* the host, with a checklist
  and full tool access. And a managed pass on the same model as the host buys
  nothing for its cost. So the pairing that actually earned its keep was
  model-diverse and split by strength: **GLM spotted** the defect (a careful
  read of the test's claims against its fixture), **the host proved** it
  (mutation: disable the branch, then inject an off-by-one, both green). Each
  did the half it was good at. Any future managed pass must record
  requested-vs-served model *and* the host model, or "second opinion" is
  unfalsifiable.
- **S4a — convention rules — RESOLVED via `AGENTS.md`, not via OCR.** `AGENTS.md`
  is read by every harness, and in delegation mode the HOST agent is the
  reviewer, so the conventions now reach the review where they actually land:
  a new `AGENTS.md` rule points at `doc/procedures/ocr_review.md` and states
  that the host agent is the sole carrier of the conventions. Do **not** add
  the OCR `rules` key — it is accepted by the parser but does not surface in
  either consumer, so it would be a silent no-op. Evidence and the probe
  method are in `doc/local/engineering/code_review.md`; the rule itself lives
  in the procedure. Revisit only if OCR-managed mode lands and needs
  conventions the host cannot supply.

## 4. Acceptance

- `.opencodereview/rule.json` makes OCR selection see the test spine — **DONE**
  and verified: `ocr delegate preview` on `csr_lane_fixes` lists 5/10
  reviewable files including both `tests/test_*.py`. Reproduce with
  `ocr delegate preview --commit $(stg id csr_lane_fixes)` and assert a
  `tests/` path is present.
- Convention rules reach the reviewer via the `AGENTS.md` rule (S4a); the OCR
  `rules` key stays out.
- `doc/procedures/ocr_review.md` filed and followed reproducibly by a fresh
  agent, and kept current — **DONE**; it carries the revisit-trigger table.
- `make review-patch` exists **only** for the deterministic part of S3, and
  still fails loudly if no `tests/` path is selected.
- Gate coupling stays off; findings triaged by the operator like any report.
