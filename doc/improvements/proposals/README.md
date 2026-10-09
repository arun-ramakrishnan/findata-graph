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

_(none)_ — as of 2026-10-09 every proposal filed here has been executed
and archived. The most recent batch (#373-377) went out executed across
the gate arc:

- `review_scan_followups`, `gate_query_sql_fragment_registry`,
  `noqa_in_sql_literal_lint`, `convo_embed_gc_stamp` — the review-gate
  hardening set, in `../archive/tooling/`. The load-bearing finding is
  #373's span-walk gap: bandit cites a multi-line f-string's FIRST line
  (paren depth 0) while ruff honours a directive on the line where the
  string ENDS, so a paren-only walk left every adjudicated `gate_query`
  B608 finding surfacing in rosters. #375 exists because a directive
  inside a SQL string is query text DuckDB rejects, and nothing in the QA
  chain said so.
- `notes_query_side_levers_trial` — closed and archived having been left
  live after its own conclusion landed; the production flip it authorised
  is recorded separately at #369.
