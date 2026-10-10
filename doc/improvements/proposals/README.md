# Live proposals

Home for proposals awaiting execution (first occupant: the doc-search
proposal, archived 2026-08-23). House rule (2026-08-21): file a proposal
here BEFORE implementing multi-slice work. Acceptance criteria for any
change that alters query-visible semantics (rosters, crosswalks,
hierarchies, extractor rules) MUST carry an eval-gate bullet —
`helpers/misc/ontology_eval_gate.py` over the frozen question set
between dry-run and canonical apply (ontology_governance S2). On Status
EXECUTED, move it to
`../archive/<topic>/` and add the `../completed.md` entry in the same
change — and update any `embed_eval_questions.json` labels referencing
the old path (see `doc/procedures/search.md` §Corpus lifecycle;
reference-rot sweeps: `doc/procedures/doc-hygiene.md`).

Full archival checklist (extended 2026-08-26 after finding a duplicate
entry number and stale DONE pointers):

1. `git mv` to `../archive/<topic>/`; repoint any `**Follows**:` /
   cross-references at the old `proposals/` path.
2. `../completed.md`: entry exists and number is UNIQUE (audit
   `rg '^## \d+\.'` for duplicates — parallel sessions can mint the same
   number; suffix the un-referenced one, e.g. `105b`, never renumber).
3. `../pending.md`: grep the topic; close/annotate deferred items the
   work completes.
4. `../archive/README.md`: add the topic-index line with the completed.md
   number; reset the live-proposal pointer below to `_(none)_`.
5. `make search-fresh APPLY=1`, then plain `make search-fresh` (rc=0)
   to converge the doc index.
6. Flip the frontmatter block in the same change: `status: executed`
   + the executed date + the completed.md number — the Proposal
   lifecycle static check fails an archived proposal still saying
   `proposed` (corpus_uniformity S3).

## Current live proposals

_(none)_ — every proposal filed here is executed and archived. The last
three batches went out filed + executed + archived in one session
(2026-10-10):

- `checklist_leg_review` (completed.md #381) — the OCR checklist leg against
  the 108 reviewable Python files, swept by checklist group using the
  off-gate ruff rulesets as the mechanical proxy.
- `review_pass_findings` (completed.md #380) — six review-pass slices, all
  one failure shape: **the tool reports success while having done less than
  it claims**. Worth carrying forward: a `# noqa` can be reachable from one
  tool's anchor and not another's, so "I annotated it" is not the same as
  "the finding is adjudicated" — check the span, per tool.
- `lockfile_coverage` (completed.md #379) — `source-map-js` had been open
  since 2026-10-07 behind two structural gaps, not a scanner bug. Its
  non-obvious mechanic — **removing an `exclude` from rule.json does not
  admit a path**, an explicit `include` is required past 1.12.11's extension
  allow-list — is recorded in `doc/procedures/ocr_review.md` §1.
