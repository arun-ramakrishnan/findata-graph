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

- *(none live)* — every proposal filed here is executed and
  archived. Most recent: `price_lane_alpha_research` (filed + executed
  same day 2026-10-31, #461,
  `../archive/graph/price_lane_alpha_research.md`; as-of substrate,
  weekly OHLCV sidecar 90.2%, event studies, pre-registered precision
  instrument), then `graph_lane_unlocks` (filed + executed same
  day 2026-10-31, #460,
  `../archive/graph/graph_lane_unlocks.md`; S1 matching Hall print,
  S2 group convergence, S3 index opt-in decision, S4 backbone
  resilience 82.7% bridges, §5 trigger register frozen), then
  `mst_backbone_wiring` (filed 2026-10-30, executed
  2026-10-30, #458,
  `../archive/graph/mst_backbone_wiring.md`; stats.py advisory section
  + `make graph-mst`) and `maximum_bipartite_matching_wiring` (filed
  2026-10-30, executed 2026-10-30, #459,
  `../archive/graph/maximum_bipartite_matching_wiring.md`; stats.py
  advisory section + `make graph-matching`), then
  `graph_analytics_integrity` (filed + executed
  same day 2026-10-30, #456,
  `../archive/graph/graph_analytics_integrity.md`; voterank
  freshest-wins + replace-on-write, clustering/wcc off the star at
  dispatch level, live-vs-stored drift print, `prune_analytics.py`
  sweep, debris-entity quarantine; ledger-amend fingerprint-restamp
  defect fixed as by-catch) and `hgx_over_dyads` (filed + executed same
  day 2026-10-30, #457, `../archive/graph/hgx_over_dyads.md`; hy-MMSBM
  NaN guard, `index` family 57/6,615, `group` widened filer-keyed
  498/10,738 with fold provenance 1,545→1,450, `subsidiary_of`/
  `supplier_to` DEFERRED, #451 record corrected), then
  `db_scaling_tuning` (filed 2026-10-29, executed 2026-10-30,
  #454, `../archive/database/db_scaling_tuning.md`; §3.2 conn cache +
  §3.3 SQLite cache 80→256MB landed in `db_tuning`, §3.1 threads/
  memory_limit declined, §6.3 orjson ADOPTED via `helpers/core/jsonio.py`,
  §6.1 FTS5-optimize and §6.2 preserve_insertion_order
  MEASURED-NOT-ADOPTED, §6.4 opencode drift-query lever parked to
  pending.md), then `bili_absorb_stats_display` (filed + executed +
  archived same day 2026-10-29, #452,
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
