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
   number; reset the live-proposal pointer below to `*(none)*`.
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

- *(none)* — every proposal filed here is executed and archived. Most
  recent: `bili_absorb_stats_display` (filed + executed + archived
  same day 2026-10-29, #452,
  `../archive/tooling/bili_absorb_stats_display.md`) and
  `notes_thin_tail_quality` (filed 2026-10-28, executed with its D1–D3
  remainder DEFERRED 2026-10-29, #453,
  `../archive/pipeline/notes_thin_tail_quality.md`; remainder tracked
  in `../pending.md`). Detail bullets for every archived proposal live
  in `../archive/README.md`; `../completed.md` is the canonical
  record.

- Family provenance: split 2026-10-21 from the bundled
  `duckdb20_quack_variant` filing; the executed 2.0-compat migration
  is completed.md #427. Upstream changeset:
  <https://github.com/duckdb/duckdb/releases/tag/v2.0.0>.
