---
title: "OCR selection drift — refresh the procedure for 1.12.11, admit doc Markdown, carry the house checklist, price every review"
status: proposed
filed: "2026-09-30"
executed: null
completed_md: null
area: "doc/procedures/ocr_review.md, .opencodereview/rule.json, helpers/misc/review_selection.py"
---

# OCR selection drift — refresh the procedure for 1.12.11, admit doc Markdown, carry the house checklist, price every review

**Date:** 2026-09-30 · **Status:** PROPOSED (S1–S4 executed in the filing
session; archival awaits the operator gate flow) ·
**Area:** OCR review loop — procedure doc, selection rules, delegation spine

## 1. Motivation

The unpinned bun global moved open-code-review **1.12.10 → 1.12.11**
(`a758d9c`, built 2026-09-29), firing row 1 of `doc/procedures/ocr_review.md`'s
revisit table (version bump invalidates invocation forms, flags, both schemas,
the last-verified stamp). The 2026-09-30 glm-5.3 delegation round over
`8711c96d..a363d12a` (fingerprint `a51bb91a91fbe061`; 0 blocking, 2 LOW —
dead-noqa annotations) re-probed the doc's claims against the live binary and
found the drift is real, not cosmetic:

1. **`include` semantics inverted.** On 1.12.10 include was a restrictive
   allow-list (§1's "mirror-image" warning). On 1.12.11 a sandbox repo with
   `include: ["tests/**/*.py"]` alone still selects **every** `.py` in the tree
   plus a `.json` — selection now defaults to an extension allow-list, include
   only *adds* paths (it admitted a `.md` when added), and **exclude** is the
   only pruner. The documented failure mode ("include-only-tests reviews
   nothing that matters") is dead; its inverse (everything code-shaped is
   reviewable unless excluded) is the new truth.
2. **A system `default` rule group exists.** `ocr rules check` resolves
   `.md` and `.mojo` paths to "System built-in / Pattern: default" (a generic
   Correctness checklist). Selection still drops `.md` as `unsupported_ext`
   unless explicitly included — so the doc-coverage limit is now a selection
   choice, not a rule-layer absence.
3. **The project-rules channel is still dead** — re-verified: a `rules` array
   in `rule.json` and a `.opencodereview/rules/` dir file surface nowhere;
   the `--rule <file>` flag (present on `delegate rule` and `review`) parses a
   single `ProjectRule` object but undocumented field names are silently
   ignored. The host agent remains the sole carrier of house conventions.
4. **Review-cost capture is not in the procedure** (operator directive
   2026-09-30): every review round must record timing, input/cache token
   splits, and cost where the harness exposes them.

## 2. Evidence (measured 2026-09-30, sandbox scratch git repo + this box)

| Probe | Configuration | Result | Verdict |
|---|---|---|---|
| T1 | `include=["tests/**/*.py"]` only | all `.py` repo-wide + `data.json` reviewable; only `note.md` dropped (`unsupported_ext`) | include no longer restricts |
| T5 | T2 config + `note.md` in include | `note.md` reviewable | include widens (additive) |
| T2 | `rules` array w/ `HOUSEMARKER` content in `rule.json` | absent from `ocr rules check` + `ocr delegate rule` | project rules key inert |
| T3 | `.opencodereview/rules/house.json` file | not picked up | rules dir inert |
| T4 | `--rule` with an array file | hard error: `cannot unmarshal array into Go value of type rules.ProjectRule` | flag expects ONE object |
| T6/T9 | `--rule` with guessed single object (`name`/`pattern`/`content`) | parses clean, resolution unchanged, marker absent | schema undocumented, guesses silently ignored |
| — | `ocr rules check` on `doc/*.md`, `Mojo/src/*.mojo` | "System built-in / Pattern: default" generic checklist | default rule group exists |
| — | live review run (this arc's trigger) | 10/19 reviewable; all 9 exclusions `.md` `unsupported_ext`; `desktop/src-vue/test/*.py` selected despite matching no include glob | live confirmation of T1 |

The round that surfaced all of this: delegation review by GLM-5.3 (zcode
harness), 27 model requests (review turns through snapshot time; the
session then continued to 53 requests / 6.48 M input by 19:37 IST as this
proposal was filed from it), 1.43 M input tokens (95.8 % cache-read, 60.5 k
fresh), 26.7 k output, **$0.56 notional** at plan rates, **$0 billed**
(coding-plan subscription); OCR delegation leg itself $0 / 0 tokens / <0.3 s
for both commands. Raw log in §Appendix.

## 3. Design

- **S1 — procedure refresh** (`doc/procedures/ocr_review.md`): stamp →
  2026-09-30 / 1.12.11; rewrite the §1 include caveat (additive semantics,
  extension-allow-list default, excludes as the pruner, `make review-patch`
  teeth as the guard); note the `default` rule group and the `--rule`
  finding in Known limits; §6 gains the review-cost capture requirement with
  the measured recipe.
- **S2 — admit doc Markdown** (`.opencodereview/rule.json`): add
  `doc/procedures/**` and `doc/improvements/**` to include — the probe-T5
  path. Doc-heavy diffs enter the roster with the default-group checklist;
  the host still reads them (delegation has no other reader).
- **S3 — host-carried house checklist** (`helpers/misc/review_selection.py`):
  `make review-patch` prints a fixed HOUSE CHECKLIST after the roster —
  noqa-must-sit-on-the-reported-line, test-teeth mutation check,
  fixture-reality, no literal `/tmp`, `.venv` interpreter, gate
  cross-check. Zero OCR cooperation required (the channel is inert; the
  host is the carrier — this just makes the carrier visible at the point of
  use).
- **S4 — evidence + ledger**: persist the glm-5.3 round triage to
  `doc/local/engineering/code_review.md` (§4b.6 duty); repoint the
  freshness-ledger note from the ephemeral `/mnt/data/tmp` report at the
  durable section.

## 4. Acceptance criteria & shakedown

1. `ocr delegate preview` over a doc-touching range lists the touched
   `doc/procedures/**` / `doc/improvements/**` files as reviewable
   (re-checked live, not assumed from T5).
2. `make md-lint` green; `.venv/bin/ruff check --select S,UP,C901 .` still
   green; `review_selection.py` smoke prints roster + house checklist +
   `selection-teeth OK`.
3. Mutation: drop the two `doc/**` entries from include → `ocr delegate
   preview --from 8711c96d --to a363d12a` no longer lists the range's `.md`
   files (they return to `unsupported_ext`); restore → listed again. Note
   the honest limit: derived roots are self-masking for *presence*
   (dropping the include also drops the family from `_families()`), so the
   exit-1 teeth guard the *admission* path — include present but every
   family file landing in `excluded_files` still fires `touched and not
   selected`. PRODUCT_FAMILIES remain the hard, non-self-masking guard.
4. `make search-fresh APPLY=1`, then plain `make search-fresh` → rc=0.

No eval-gate bullet: nothing here alters rosters, crosswalks, hierarchies,
or extractor rules.

| Projected outcome | Today | After |
|---|---|---|
| doc/ Markdown in OCR selection | dropped (`unsupported_ext`) | admitted for `doc/procedures/**`, `doc/improvements/**` |
| house-checklist carrier | procedure doc, read manually | emitted by `make review-patch` |
| review cost accounting | ad hoc (this round did it unprompted) | §6 recipe: timing, input/cache split, cost |
| procedure stamp | 2026-09-28 / 1.12.10 (stale by its own table) | 2026-09-30 / 1.12.11 |

## 5. Risks

- **`doc/improvements/**` drags archive Markdown into rosters** — bounded:
  the default-group checklist is generic, the host reads doc anyway, and
  excludes remain available if volume annoys.
- **Teeth now fire on doc traffic** — a `doc/**`-rooted diff where every
  family file lands excluded (admission broken) exits 1. Derived roots are
  self-masking for presence (drop the include, the family stops being
  checked) — deliberate, matches `_families()`'s design; the self-masking
  lesson binds only the hardcoded PRODUCT_FAMILIES, which this arc does not
  touch.
- **1.12.11 semantics drift again** — the stamp + revisit table now say so;
  the Appendix probes re-run in seconds on a scratch repo.

## 6. Non-goals

- **Upstream filings to alibaba/open-code-review** (project-rules
  surfacing, a dedicated Markdown rule group, publishing the ProjectRule
  schema) — explicitly dropped by the operator 2026-09-30 "for now".
- No managed-mode changes, no PRODUCT_FAMILIES edits, no rule-group
  authoring (no verified channel), no eval-visible semantics.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-30 | `ocr --version` | `v1.12.11 (a758d9c)` | bun global, unpinned |
| 2026-09-30 | sandbox `delegate preview`, include=`tests/**/*.py` | 6 reviewable incl. non-tests + `data.json` | T1 |
| 2026-09-30 | sandbox preview, include += `note.md` | `note.md` reviewable | T5 |
| 2026-09-30 | sandbox `rules check` / `delegate rule` w/ HOUSEMARKER rules | marker count 0 | T2/T3/T9 |
| 2026-09-30 | `ocr rules check --rule <array file>` | unmarshal error naming `rules.ProjectRule` | T4 |
| 2026-09-30 | `ruff --select S603 --ignore-noqa` scratch probe | S603 fires only on non-literal (spread) argv | explains the 2 LOW dead-noqa findings |
| 2026-09-30 | review telemetry (`model_usage`, ro) | 27 req; 1,430,594 input / 1,370,112 cache-read / 60,482 fresh / 26,694 out | $0.5584 notional @ $0.26/$1.40/$4.40 per 1M; $0 billed; review-turns-only snapshot — session had reached 53 req / 6.48 M in by 19:37 IST; `session_id` filter is mandatory (a parallel round's naive window mixed two sessions the same day) |
| 2026-09-30 | OCR command wall time | preview 0.2 s + rule 0.07 s | delegation = deterministic, sub-second |
