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

_(none)_

_(Previously: ontology_governance.md archived 2026-09-17 as completed.md
entry 244 — master ontology doc + qa-gated roster-drift check, concept
lifecycle (status/supersede/promote, schema v12), deterministic ontology
change gate over a doc-driven 79-question frozen set; execution record in
`../archive/database/ontology_governance.md`; EvoOntology (ruc-datalab)
design-reference borrow, deferred items in §7 + pending.md.)_

_(Previously: search_tui.md archived 2026-09-16 as completed.md entry
242 — one terminal front door over the five search surfaces; execution
record in `../archive/tooling/search_tui.md`. hgx_first_scaling.md
archived 2026-09-16 as completed.md entry 241 — execution record in
`../archive/graph/hgx_first_scaling.md`; S1/S2 committed 55318803,
S3/S2c committed 7a8cf92e, aim corrections 78758ab3.)_

— (Previously: live_inv_longest_chains.md archived 2026-09-16 as completed.md
entry 240 — execution record in `../archive/graph/live_inv_longest_chains.md`;
S1/S2 executed in e5728add (leg 267.4s -> 66.3/79.0s), S3 superseded by the
live hgx_first_scaling.md.)_

— (Previously: hyper_lane_wiring.md archived 2026-09-16 as completed.md
entry 239 — execution record in `../archive/graph/hyper_lane_wiring.md`;
D4 `h_*` materialisation + D10 prediction/motifs deferrals moved to
`../pending.md`; D11 operator authoring closed-for-now with 16 unmapped
taxonomy buckets in `findata/Misc/subsector_worklist.json`.)_

— (Previously: hyper_metric_ui_visibility.md archived 2026-09-16 as
completed.md entry 235 — execution record in `../archive/ui/hyper_metric_ui_visibility.md`.)_

— (Previously: concall_title_edition_normalisation.md archived 2026-09-16 as
completed.md entry 236 — execution record in
`../archive/pipeline/concall_title_edition_normalisation.md`; writer worklist
deferred inside.)_

— (Previously: jv_promoter_capture_upgrade.md archived 2026-09-16 as
completed.md entry 237 — execution record in
`../archive/graph/jv_promoter_capture_upgrade.md`.)_

— (Previously: company_subsector_authored_lane.md archived 2026-09-16 as
completed.md entry 238 — execution record in
`../archive/graph/company_subsector_authored_lane.md`; authored adoption is
operator surface via `findata/Misc/subsector_worklist.json`.)_

— (Previously: ontology_convention_stack.md archived 2026-09-14 as completed.md
entry 234 - execution record in
`../archive/database/ontology_convention_stack.md`; S0–S5 landed in one
day: provenance registry (100% coverage), SKOS concept conventions,
CIN/identifier surfaces, incidence-store event roles, cited glossary;
SQLite schema v7→v11; S1b writer stamping + NIC seed table deferred
inside the proposal §7.)_

— (Previously: hypergraph_incidence_hyx.md archived 2026-09-14 as completed.md
entry 233 - execution record in
`../archive/graph/hypergraph_incidence_hyx.md`; S0–S21 landed: star store +
backfills + HGX communities/centralities + the parquet/Arrow/HIF data
standard; capture-gap ledger deferred inside the proposal.)_

— (Previously: easygraph_cpp_readoption.md archived 2026-09-12 as completed.md
entry 231 - verdict: adoption DEFERRED, parked behind R1-R4 upstream-fix
triggers; lane matrix + full run log folded into the proposal.)_

— (Previously: hybrid_graph_onager_igraph.md archived 2026-09-12 as completed.md
entry 230 - execution record in
`../archive/graph/hybrid_graph_onager_igraph.md`; standing posture: bridge
landed pilot-gated, integration DEFERRED per D15 — revival conditions in
proposal §7.)_

— (Previously: unified_search.md archived 2026-09-12 as completed.md entry
229 - execution record in `../archive/ui/unified_search.md`; decode-class
fixes + the embedding-chokepoint static check landed in the same arc.)_

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
