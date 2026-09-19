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

- [`subsector_authoring_pass.md`](subsector_authoring_pass.md) —
  Sub-sector authoring pass — resolve the 16 parked buckets via
  SUB_SECTOR_ALIASES additions + per-note authored `subsector:`
  (eval-gated; decision matrix inside).

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
