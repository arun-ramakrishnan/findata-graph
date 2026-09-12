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

- **unified_search.md** (filed 2026-09-12) — `/api/scripts/search`
  endpoint over `search_scripts()` plus a client-side grouped
  Docs/Scripts/Notes search view; snippets highlighted via the landed
  sugar-high path (mojo→python approximation). Evidence: same-day
  operator observation after `../archive/ui/sugar_high_highlighter.md`
  landed.

_(Previously: hybrid_derive_graph_microperf.md, archived as completed.md
entry 228 on 2026-09-12 — execution record in
`../archive/graph/hybrid_derive_graph_microperf.md`; standing posture: S2
shipped, S1 deferred, S3 no-op with evidence.)_

_(Previously: sugar_high_highlighter.md, archived as completed.md
entry 227 on 2026-09-12 — execution record in
`../archive/ui/sugar_high_highlighter.md`. Earlier: prefab_ui_flask_views.md,
archived as completed.md entry 226 on 2026-09-12 — execution record in
`../archive/ui/prefab_ui_flask_views.md`; standing posture: dual-URL,
`/findata` authoritative.)_

_(Previously: bulk_data_lanes_arrow_numpy.md, archived as completed.md
entry 224 on 2026-09-11 — execution record in
`../archive/database/bulk_data_lanes_arrow_numpy.md`.)_

_(Previously: embedding_blob_migration.md, archived as completed.md
entry 223 on 2026-09-10 — execution record in
`../archive/database/embedding_blob_migration.md`.)_

_(Previously: graph_db_optimization.md, archived as completed.md entry
222 on 2026-09-10 — execution record in
`../archive/graph/graph_db_optimization.md` — all three issues
executed, incl. the same-day `_splice_sources` wrap-up.)_

_(Previously: snapshot_trust_country_exposure.md, archived as
completed.md entry 221 on 2026-09-10 — execution record in
`../archive/database/snapshot_trust_country_exposure.md`.)_

_(Previously: archify_diagram_refresh.md, archived as completed.md
entry 220 on 2026-09-10 — execution record in
`../archive/tooling/archify_diagram_refresh.md`.)_

_(Previously: country_layer_institution_lanes.md, archived as
completed.md entry 219 on 2026-09-10 — execution record in
`../archive/graph/country_layer_institution_lanes.md`.)_

_(Previously: cli_param_bundling_doc_anchor_repair.md, archived as
completed.md #217 on 2026-09-09 — execution record in
`../archive/tooling/cli_param_bundling_doc_anchor_repair.md`.)_

_(Previously: code_duplication_consolidation.md, archived as
completed.md #216 on 2026-09-08 — execution record in
`../archive/tooling/code_duplication_consolidation.md`.)_

_(Previously: quote_capture_coverage.md, archived as completed.md #215 on
2026-09-08 — execution record in
`../archive/graph/quote_capture_coverage.md`.)_

_(Previously: derive_render_shared_note_grouping.md, archived as
completed.md #214 on 2026-09-08 — execution record in
`../archive/graph/derive_render_shared_note_grouping.md`.)_

_(Previously: ripwire_adoption.md, archived as completed.md #213 on
2026-09-08 — execution record in
`../archive/tooling/ripwire_adoption.md`.)_

_(Previously: get_ticker_fixes.md, archived as completed.md #212 on
2026-09-07 — execution record in
`../archive/graph/get_ticker_fixes.md`.)

_(Previously: scan_render_vss_microperf.md, archived as completed.md #211
on 2026-09-07 — execution record in
`../archive/graph/scan_render_vss_microperf.md`.)

_(Previously: embed_full_reembed.md, archived as completed.md #210 on
2026-09-06 — the granite swap execution record lives in
`../archive/database/embed_full_reembed.md`; note_section_search.md
(#209, 2026-09-06) in `../archive/tooling/`; derive_insights_perf.md
(#208, 2026-09-05) in `../archive/graph/`.)_
