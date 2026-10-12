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
7. Reference-rot sweep: grep by proposal NAME, not by tracked path —
   proposals are filed untracked, so path-based sweeps never see code
   references (range review 4fd3a20f, H2; the 2026-10-11 sweep
   repointed 16 dead sites).

## Current live proposals

- `worktree_fresh_trample_guard` (filed 2026-10-12) — a behind-worktree
  APPLY regresses the shared notes surface and bumps the shared
  generation (the "ghost" gate-failure chase, 4× on 2026-10-12; live
  ping-pong reproduced and caller-tagged). Slices: working-set stamp
  (S1), refuse backward applies with `--allow-stale` (S2, eval-gated),
  bump-log-citing snapshot_check (S3), etiquette line (S4), `--sandbox`
  preview lane for behind-wt validation (S5, deferred). Evidence: live
  reproduction in the proposal §2.

`default_incremental_rebuilds` was filed and executed the same
day (2026-10-12) and archived immediately after
(`../archive/tooling/default_incremental_rebuilds.md`, completed.md
#384): the search-index CLIs default to incremental rebuilds with
`--full` as the opt-out, ~40 MB of no-op gate rewrites avoided. Every
other proposal filed here is executed and archived. The range-review
remediation (`range_4fd3a20f_review_remediation`, all 13
slices + low-tier dispositions, completed.md #382) archived 2026-10-11.
  Evidence in `doc/local/engineering/code_review.md`.


_(none)_ besides the above — every other proposal filed here is executed
and archived. The last three batches went out filed + executed + archived
in one session (2026-10-10):

_(previous batch — all executed and archived in one session 2026-10-10)_:

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
