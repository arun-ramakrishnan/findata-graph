---
title: "Execute the scheduled C901 D1 splits — batch 1, the three P2.2-safe sites"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "327"
area: "helpers/graph, helpers/misc, tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Execute the scheduled C901 D1 splits — batch 1, the three P2.2-safe sites

**Date:** 2026-10-01 · **Status:** EXECUTED ·
**Area:** `helpers/graph/csr.py`, `helpers/graph/extract_relations.py`,
`helpers/misc/review_selection.py`, `tests/` (extraction-only refactors)

## 1. Motivation

Operator ruling 2026-09-30 (review findings collation S6, recorded in
`review_findings_collation.md`): the four new C901
masks are scheduled for the D1 split work, booked in
`../../pending.md` ("C901 D1 splits — four named functions scheduled").
House policy is one mask in, one split out — c901_complexity_debt §4.1
"no new suppression"; each split landing removes its `# noqa: C901`.
All four masks verified live on this tree 2026-10-01:

- `helpers/graph/csr.py:249` `try_shortest_path` (11 > 10)
- `helpers/graph/extract_relations.py:2057` `_process_pattern_matches` (12 > 10)
- `helpers/graph/query.py:3650` `near_duplicate_notes` (12 > 10)
- `helpers/misc/review_selection.py:119` `main` (11 > 10; 12 with the
  house checklist growth)

**Batch 1 = the first three.** The fourth, `query.near_duplicate_notes`,
was excluded here for a fold-collision reason that has since been
**retracted**: this proposal asserted it "sits in `query.py` — the file a
parallel session is actively reworking for P2.2 (the build path)", but the
P2.2 arc's entire diff is **8 files, all under `doc/`, zero non-doc
changes** — verified in `main` by `git status -- helpers/ Mojo/ tests/`
returning empty, and by `main`'s P2.2 commit `9c0731f7f`. There is no
`query.py` change to collide with; the P2.2 measurement was scratch-isolated
by construction (a non-`DB_PATH` `db_path` resolves to a sibling `.duckdb`),
so it never needed a source edit. That function is now filed as its own
proposal, `c901_d1_split_near_duplicate_notes.md`, and per operator
sequencing 2026-10-01 it executes **ahead of this batch**. Retraction recorded
here rather than silently dropped, because a cross-branch *fact* is what made
the original exclusion look justified.

## 2. Evidence (verified 2026-10-01, this tree)

| Function | Branches | Parity surface | P2.2 surface? |
|---|---|---|---|
| `csr.try_shortest_path` | 11 | `tests/test_csr.py` (+ csr_lane_remediation #309 regressions) | no |
| `extract_relations._process_pattern_matches` | 12 | 8 extract_relations test modules (extraction, v2, resolver, apply, yaml, years, fuzz) | no |
| `review_selection.main` | 11–12 | `tests/test_review_tooling.py` (12 tests) | no |
| `query.near_duplicate_notes` (own proposal, runs first) | 12 | 4 semantic tests; **no** `make parity` fixture — the split proposal adds one | no — the P2.2 premise was false |

## 3. Design

Extraction-only, per the c901 S2–S5 house pattern: helpers are pulled
out, bodies delegate, no behavior change, and each slice removes its
function's noqa as it lands. Slices are independently landable.

- **S1 — `csr.try_shortest_path`.** Extract `_hit_fresh` (the
  cache-hit freshness rule that mirrors `load()`, long comment
  preserved), `_cache_lookup` (hit/miss + pos derivation + evict-then-
  store; single-load semantics preserved — a stale hit must NOT
  reload, it must fall through), and `_generation_ok` (the
  `_build_meta` generation gate with its BLE001 noqa). The entry
  function keeps the ladder: substrate gate → src==dst → endpoint
  membership → BFS.
- **S2 — `extract_relations._process_pattern_matches`.** Extract
  `_queue_unresolved` (the two byte-identical `Unresolved(...)` blocks
  dedup into one helper that also carries the `_should_skip_unresolved`
  noise gate), `_resolve_target` (the INSTITUTION_LANES branch), and
  `_process_match` (the per-match tail: list-chunks → list-shaped →
  resolve → self-loop skip → edge creation). The main function becomes
  the double loop plus two guards.
- **S3 — `review_selection.main`.** Extract `_in_family` (the
  triple-duplicated prefix-or-startswith test), `_family_traffic`
  (touched/selected computation per include root), `_selection_teeth`
  (the violation loop), and `_print_freshness` (the advisory
  freshness block with its lazy import). Add unit tests for the teeth
  helpers and the freshness paths to `tests/test_review_tooling.py` —
  `main` itself is CLI-only-exercised (needs the `ocr` binary), so the
  extracted seams are what the suite can hold.

## 4. Acceptance criteria & shakedown

1. `ruff check` (gate config, incl. C901) clean on the three touched
   files with the noqas removed.
2. `rg -n 'noqa: C901' helpers/graph/csr.py
   helpers/graph/extract_relations.py helpers/misc/review_selection.py`
   → 0 hits.
3. pytest green: `tests/test_csr.py`,
   `tests/test_extract_relations_extraction.py`,
   `tests/test_extract_relations_v2.py`, `tests/test_review_tooling.py`
   (wider extract_relations siblings on the S2 diff).
4. Every extracted helper measures < 10 mccabe (census after landing
   — no knife-edge 10s left where avoidable).
5. Booking residue: `../../pending.md` still books
   `query.near_duplicate_notes` as a named D1 split; that booking now points
   at `c901_d1_split_near_duplicate_notes.md` rather than at a batch-2 slot
   (coordinated with that proposal, not by this one).

| Projected outcome | Today | After |
|---|---|---|
| masked functions in batch-1 files | 3 | 0 |
| open masks vs the ruling | 4 | 1 (booked to `c901_d1_split_near_duplicate_notes.md`, which runs first) |
| review-tooling tests | 12 | 12 + ~4 helper tests |

## 5. Risks

- **`extract_relations` is the derive hot path** — extraction-only
  with eight sibling test modules as the parity surface; no rule or
  pattern edits ride along.
- **`csr` cache semantics are subtle** — the stale-hit-must-not-reload
  and warm-degradation records live in moved comments; preserved
  verbatim in the helpers, and `test_csr.py` pins lane/fallback
  behavior.
- **Helper branch counts creep back** — census in acceptance step 4;
  the S3 decomposition exists precisely because a naive two-way split
  re-mints the mask in `_selection_teeth`.

## 6. Non-goals

- `query.near_duplicate_notes` — split separately in
  `c901_d1_split_near_duplicate_notes.md`, which executes **first** and
  carries its own parity fixture. The original fold-collision exclusion above
  is retracted; do not re-derive it from the P2.2 reference.
- The three pre-existing `_cli` masks outside the ruling:
  `query.py:1591` is booked to the next c901_complexity_debt pass by
  collation S7; `query.py:4012` and `extract_relations.py:2705` are
  unruled.
- No ruff/mccabe configuration changes; no behavior changes anywhere.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-01 | `rg -n "C901" <four files>` | masks at csr:249, er:2057, q:3650, rs:119 | all live; +3 pre-existing `_cli` masks not in the ruling |
| 2026-09-30 | operator ruling (collation S6) | "schedule D1 splits" | booked in `../../pending.md` |
| 2026-10-01 | `pytest tests/test_fuzz_regex.py::test_bold_line_regex_scales_subquadratically` | 1 passed 1.99 s | unrelated-board context: A1 filed separately |
