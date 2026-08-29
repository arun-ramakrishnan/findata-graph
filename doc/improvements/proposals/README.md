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

## Current live proposals

- `doc_drift_audit_2026_08.md` EXECUTED 2026-08-29 (same day), archived to `../archive/tooling/` (#178): F1–F10 — README Mojo row, _build_meta key lists, run-log backfills #170/#171/#172, gzip→zstd doc sweep (12 rows incl. 2 broken restore lines), note-search --check contract doc, procedures index, identity-scrub forward-leak closure. Prior executions: `zstd_binary_backups.md` → #176, `snapshot_parallel_and_compressed_backups.md` → #175, `maint_full_single_snapshot.md` → #174, `parallel_cold_embed.md` → #173. 2026-08-29 (same day as filing), archived to `../archive/database/` (#176): snapshot binary branch .gz → .zst (library-default zstd), `make snapshot` 8.0 s → 2.35 s, old .gz trio deleted. Prior executions: `snapshot_parallel_and_compressed_backups.md` → #175, `maint_full_single_snapshot.md` → #174, `parallel_cold_embed.md` → #173.
- _(none)_
