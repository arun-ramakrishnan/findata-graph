# Live proposals

Home for proposals awaiting execution (first occupant: the doc-search
proposal, archived 2026-08-23). House rule (2026-08-21): file a proposal
here BEFORE implementing multi-slice work. On Status EXECUTED, move it to
`../archive/<topic>/` and add the `../completed.md` entry in the same
change — and update any `embed_eval_questions.json` labels referencing
the old path (see `doc/procedures/doc-search.md` §Corpus lifecycle).

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
- `consolidate_frontend_reader.md` — shared `core/reader.ts` for the
  duplicated `entity.ts` / `views/docs.ts` paper-register stack
  (wikilinks, frontmatter, masthead/chips, index), a Jinja `<head>`
  partial, and merged `.modal-content` CSS (filed 2026-09-03).
- `consolidate_mojo_bench_common.md` — extract duplicated Mojo kernels
  (`row_cosine`, `load_f32`, `scan_serial`) + bridge helpers +
  Python-bench fixtures into shared `common/` / `bench/` modules (filed
  2026-09-03).
- `consolidate_helpers_shared_helpers.md` — adopt `env.REPO_ROOT`,
  `db.connect(read_only=True)`, new `db.utc_today_iso()`, and
  `frontmatter.iso_now_utc()` at the library-style sites; folds
  `_compute_root`/`_connect_ro`/`_now_utc` (filed 2026-09-03).
- `consolidate_tests_fixtures.md` — centralize ~20-file schema DDL,
  copy-production-DB, Flask test_client, seed data, and sys.path
  boilerplate into shared `tests/helpers.py` + `tests/schema.py` (filed
  2026-09-03).
