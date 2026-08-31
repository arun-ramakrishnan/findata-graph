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

- `liteparse_pdf_engine.md` EXECUTED 2026-09-01 (same day), archived to `../archive/pipeline/` (#186): Slices 0–2 — lite no-ocr 0.10s bbox sidecar (96.04% accepted, pdf_local primary), lite OCR Tesseract 0.16–0.30s, pix2text formula opt-in, liteparse_engine.py + image sidecar + per-page verify + MPLBACKEND fix, 23 tests, docs + trial addendum.
- `doc_drift_audit_2026_08.md` EXECUTED 2026-08-29 (same day), archived to `../archive/tooling/` (#178): F1–F10 — README Mojo row, _build_meta key lists, run-log backfills #170/#171/#172, gzip→zstd doc sweep (12 rows incl. 2 broken restore lines), note-search --check contract doc, procedures index, identity-scrub forward-leak closure. Prior executions: `zstd_binary_backups.md` → #176, `snapshot_parallel_and_compressed_backups.md` → #175, `maint_full_single_snapshot.md` → #174, `parallel_cold_embed.md` → #173. 2026-08-29 (same day as filing), archived to `../archive/database/` (#176): snapshot binary branch .gz → .zst (library-default zstd), `make snapshot` 8.0 s → 2.35 s, old .gz trio deleted. Prior executions: `snapshot_parallel_and_compressed_backups.md` → #175, `maint_full_single_snapshot.md` → #174, `parallel_cold_embed.md` → #173.
- `gate_xdist_phase2.md` EXECUTED 2026-08-31 (same day), archived to `../archive/tooling/` (#189): live-invariants xdist-safe via per-worker DuckDB caches (`worker-pid` key — two concurrent gate invocations share gw indices), `real_graph_cache` opt-out marker, live suite slimmed 72.5s → 57.4s serial, integration scaling guards → paired rounds; advisory ~94s → 76–87s, live `-n auto` 218/218 ×5. Phase 1 (xdist wiring, qa 114s → 65s) same day, one shared entry. Slice C (integration+fuzz split) stays OFF.
- `corpus_uniformity.md` PROPOSED 2026-08-31: templates + contracts + the
  doc/okf boundary — S1 `doc/` consolidation EXECUTED same day (design docs
  → `doc/design/`, `doc/schema/`→`doc/okf/`, schema.md → db_schema.md;
  37-file sweep), S2 note YAML templates + PAIRINGS guards EXECUTED, S3
  proposals frontmatter contract (option 2 FULL: schema + validator +
  Proposal-lifecycle check + 26-file backfill) EXECUTED, S4 test_module.py
  + S5 ts_module.ts seeds, S6 kind='ts' script_search footprint, S7
  format gates (ruff format / prettier) pending.
- _(none)_
