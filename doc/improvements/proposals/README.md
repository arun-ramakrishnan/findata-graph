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

_(Previously: search_tui_ux_pass.md archived 2026-09-22 as
completed.md entry 265 — search_tui UX pass: busy indicators (query
overlay, index-monitor row spinners + elapsed note + double-`R`
busy-guard) and the report panel overhaul (timed-header parse fix,
worktree copies listed separately, collapsed run tree with scoped `V`,
verify/integrity run histories, writer append-at-tail pinned by tests),
plus DbScreen/rerun overlays and theme-aware verdict colors. Frames
ported from ratatui-spinner `FluxFrames::CLASSIC`; zero new
dependencies. Execution record in
`../archive/tooling/search_tui_ux_pass.md`.)_

_(Previously: snapshot_fresh_gate.md archived 2026-09-21 as
completed.md entry 264 — snapshot freshness pre-gate:
generation-only `--quick` over sqlite + DuckDB pairs with
fail-closed semantics and remediation; qa `snapshot-fresh` step +
target. Execution record in
`../archive/tooling/snapshot_fresh_gate.md`.)_

_(Previously: validator_lint_cleanup.md archived 2026-09-21 as
completed.md entry 263 — validator lint cleanup: two C901 splits +
S607 annotation, extraction-only. Execution record in
`../archive/tooling/validator_lint_cleanup.md`.)_

_(Previously: gate_latency_followups.md archived 2026-09-21 as
completed.md entry 262 — gate-latency bundle: syntax/chokepoint/
data_format/sqlite/js/shebang legs gated via py/js scopes with
scope-driven iteration; hygiene refined to in-scope staleness; dead
import dropped. Execution record in
`../archive/tooling/gate_latency_followups.md`.)_

_(Previously: fastjsonschema_split_track.md archived 2026-09-21 as
completed.md entry 261 — fast validation engine by default,
`--strict` opts into jsonschema; 1456/1456 verdict agreement;
maint-full advisory `--report` twin. Execution record in
`../archive/tooling/fastjsonschema_split_track.md`.)_

_(Previously: dirty_gated_corpus_validation.md archived 2026-09-21 as
completed.md entry 260 — dirty-gated corpus validation with flipped
default and maint-full backstop. Execution record in
`../archive/tooling/dirty_gated_corpus_validation.md`.)_
_(Previously: graph_centrality_persistent_cache.md archived 2026-09-21 as
completed.md entry 259 — persistent per-generation centrality cache: ten
`v_centrality_*` tables stamped into `graph.duckdb` at rebuild (schema 17);
Arrow-CTAS stamping after the executemany WAL finding; readers serve tables,
`--compute` keeps benchmarks honest; warm CLI reads 0.35–0.45s. Execution
record in `../archive/graph/graph_centrality_persistent_cache.md`.)_

_(Previously: subsector_authoring_pass.md archived 2026-09-21 as
completed.md entry 258 — sub-sector authoring pass: the 16 parked
worklist buckets resolved via 8 SUB_SECTOR_ALIASES additions (incl.
the Gaming leaf) + per-note authored `subsector:` on 81 company
notes; D7 convergence prune; eval gate ACCEPT. Execution record in
`../archive/graph/subsector_authoring_pass.md`.)_
_(Previously: test_gap_closure.md archived 2026-09-20 as completed.md entry
  256 — test-gap closure: the date-rot class (S1, already fixed by the
  operator), availability budgets for the hot routes (S2, 10 tests), the
  first perf legs to drive the Flask request path (S3, `bench_routes.py`),
  worker + BrokenProcessPool fallback tests (S4, 6 tests), and a fuzz guard
  for the last quadratic regex (S5). All mutation-verified; `make qa` 10/10;
  execution record in `../archive/testing/test_gap_closure.md`.)_

Per-patch rule (2026-09-19): a
proposal's index line is added by the SAME patch that adds its file,
so this section never references content from future patches.

_(Previously: conc1_graph_connection_isolation.md archived 2026-09-19 as
completed.md entry 251 — CONC-1 fix: requests no longer share one
process-wide read-only connection (63 wrong rows + 126 errors / 3,000 ->
0 / 0); each request gets its own connection on `flask.g`, closed at
teardown, with the direct-call singleton preserved for tests and CLI;
execution record in `../archive/security/conc1_graph_connection_isolation.md`.)_

_(Previously: avail2_metric_regex_deAmbiguate.md archived 2026-09-19 as
completed.md entry 250 — AVAIL-2 fix: the cubic ReDoS in the metric range
patterns is removed at the pattern (`\s*(?:[-–]|to)\s*`, both derive
copies); a drafted length cap was rejected on corpus measurement (a 300-char
cap drops 1.21% of captures) in favour of the adversarial fuzz guard, which
takes 10,349 ms on the old class and ~1 ms on the fix; execution record in
`../archive/security/avail2_metric_regex_deAmbiguate.md`.)_

_(Previously: near_duplicates_api_compute_cap.md archived 2026-09-19 as
completed.md entry 249 — AVAIL-1 fix: the unauthenticated O(n^2)
near-duplicates self-join is memoized per cache generation (59.31 s ->
0.010 ms measured) and refused over a 10,000-doc corpus ceiling (503);
execution record in `../archive/security/near_duplicates_api_compute_cap.md`;
an LRU bound on the memo was added as-implemented because min_sim is
client-controlled.)_

_(Previously: mojo_footprint_repair.md executed 2026-09-19 as
completed.md entry 247 (archived to `../archive/tooling/`);
industry_coding_completion.md executed 2026-09-19 as entry 248
(archived to `../archive/database/`); nic2008_seed_table.md executed
2026-09-19 as entry 246 (archived to `../archive/database/`);
tmpdir_sanitization.md archived 2026-09-17 as entry 245.)_
