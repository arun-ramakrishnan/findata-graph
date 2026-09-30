---
title: "Review findings collation — one disposition sheet for the ten bake-off classes"
status: executed
filed: "2026-09-30"
executed: "2026-09-30"
completed_md: "324"
area: "helpers/misc (review tooling), tests, doc/improvements archive pointers"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Review findings collation — one disposition sheet for the ten bake-off classes

**Date:** 2026-09-30 · **Status:** EXECUTED 2026-09-30 — S1–S7 DELIVERED ·
**Area:** findings from the 9-model delegation bake-off + 3 zcode re-review
legs, over `8711c96d..a363d12a` (fingerprint `a51bb91a91fbe061`)

## 1. Motivation

Ten review legs (nine blind models + three zcode legs of decreasing cost:
$0.061 → $0.043 → $0.021) produced findings scattered across ephemeral
reports (`/mnt/data/tmp/reviews/`, vanishing), gitignored evidence
(`code_review.md` Legs 4–7), and a local note (`delegation_reviews.md`).
The operator needs ONE durable sheet to accept/reject/defer per finding —
this proposal is that sheet. Every class below was verified (RUF100 run,
mutation/execution proof, gate-corpus query, or live sweep) at least once;
discovery counts cite the blind rounds.

## 2. Evidence — the collated matrix (measured 2026-09-30)

| # | Class (sev) | Sites | Blind discoverers | Verification | Live state |
|---|---|---|---|---|---|
| 1 | Dead noqa `review_freshness.py:66` (LOW) | 1 | 6 of 9 (space-bunny, big-pickle, deepseek, mimo, glm-5.3, glm-5.3-flash) | RUF100: `S603`,`S607` unused | open → S1 |
| 2 | Dead noqa `review_selection.py:77` (LOW) | 1 | glm-5.3 (only, as a finding) | RUF100: `S603` unused | open → S1 |
| 3 | S603 fires only on non-literal argv — ruff 0.16.9 fact | knowledge | glm-5.3, glm-5.3-flash (probes), mimo (repo-wide sweep), deepseek | probe file | explains 1+2; every `# noqa: S603` in tree is inert (mimo's sweep) |
| 4 | S108 left test names/docstring saying "tmp" (LOW) | `test_tmp_hygiene.py:168-169,210` (+ orphaned conftest `:726` wording, pre-existing) | space-bunny, deepseek, mimo | branch never keyed on tmp-ness (execution proof; teeth discriminate) | open → S2 |
| 5 | `duckdb_pin_reconciliation.md:16` still "AWAITING ARCHIVAL"; 09-29 vs 09-30 date contradiction with `:22` + README batch record (LOW) | 1 file | space-bunny, deepseek (deepest), mimo | sed, three records compared | open → S3 |
| 6 | Dangling pointers after the 5 `git mv`s incl. armed `pending.md:7` (LOW) | 9 doc sites + 2 code docstrings | space-bunny (1 site), deepseek (4) | live sweep after repair | **executed in-tree** (`ocr_rule_census.md` S6, 2026-09-30) — awaits gate/re-review |
| 7 | Commit claims "make qa 11/11"; corpus = run 1130 FAIL 8/11 at non-ancestor `1ee7d9f2` (record accuracy) | `8ec7575c` message | deepseek, mimo (only 2 of 9) | `gate_query` + `merge-base --is-ancestor`, re-run twice | operator decision → S5 |
| 8 | 4 new C901 masks vs deferred `c901_complexity_debt` §4.1 "no new suppression" (policy) | `csr.py:249`, `extract_relations.py:2057`, `query.py:3650`, `review_selection.py:92` | muse-spark, deepseek, space-bunny | all load-bearing (11–12 > 10), rationales present | decision → S6 |
| 9 | `review_freshness.py`/`review_selection.py` have zero unit tests (MEDIUM — the bake-off's only Medium) | 2 modules | space-bunny (as finding); deepseek (observation) | `rg -c` → 0/0; `test_review_kit.py` covers a different module | open → S4 |
| 10 | Pre-existing: misplaced C901 `query.py:1591`; dead `_tmp_hygiene.py:180` S608 + `:248` S603; 4 stale `helpers/maintenance` docstring refs → 3 missing `proposals/*.md` (geo_converge:4, googlesheets_metrics:4, googlefinance:5, finnhub_search:4) | 7 sites | space-bunny (1591), leg-10 §4b step 8 (maintenance refs) | live checks | open → S7 |

## 3. Design

- **S1 — drop the two dead in-range noqa directives** (classes 1–2).
  One-line deletes; no behaviour change. Prevention lever (operator
  decision, not this slice): a `RUF100` term in `make lint-audit` would
  surface every dead directive repo-wide — mimo's sweep says the tree
  carries more.
- **S2 — naming honesty** (class 4): rename
  `test_tmp_db_passes_through_untouched` →
  `test_non_production_db_passes_through_untouched`, reword the class
  docstring to "any non-production path", and fix the orphaned "tmp
  fixture" phrase in `conftest.py:726` while there. No other file
  references the test name (checked).
- **S3 — align the archive record** (class 5): line 16 →
  `DONE — ARCHIVED 2026-09-29` (matching the canonical
  `proposals/README.md` batch record + `completed.md` #315), reword
  line 22's "Archived 2026-09-30" (that was the unrelated batch's move
  date). Drop the stale "only unticked box" sentence.
- **S4 — small test file for the review tooling** (class 9, the Medium):
  `tests/test_review_tooling.py` pinning ledger round-trip (tmp dir),
  `--stack 0` refusal, stg-failure clean raise, `_ref_args` mapping.
  Companion: `--from/--to` mode stays deferred at
  `ocr_rule_census.md` S7 — do not duplicate here.
- **S5 — operator decision, no unilateral fix** (class 7): either reword
  `8ec7575c`'s message to the README's accurate form (history op —
  operator-only) or record a green `make qa` at an ancestor SHA; until
  then `proposals/README.md:47` remains the canonical accurate record.
- **S6 — operator adjudication** (class 8): keep the four C901 masks
  (rationales satisfy §4.5; deferred proposal owns the debt) or schedule
  the four functions for the D1 split work. No code change in this arc.
- **S7 — pre-existing sweep** (class 10): fix the 4 stale maintenance
  docstring refs (repoint at archive paths or drop); `_tmp_hygiene`
  dead noqas fold into S1's RUF100 decision; `query.py:1591` folds into
  the next `c901_complexity_debt` pass.

## 4. Acceptance criteria & shakedown

1. After S1–S3: `ruff check --select S,UP,C901,RUF100` clean on touched
   files; `pytest tests/test_tmp_hygiene.py tests/test_review_tooling.py`
   green; `make md-lint` + `make search-fresh` rc=0.
2. Selection teeth unaffected: `ocr delegate preview` on the arc's range
   keeps the test spine visible; `make review-patch` teeth pass.
3. Re-review per procedure §5/§8 — **a fix is not closed until
   re-reviewed**: re-run selection + the §4b battery over the fix range.

| Projected outcome | Today | After |
|---|---|---|
| dead noqa directives in range-touched files | 2 (+2 pre-existing) | 0 (or 0 + policy decision) |
| review-tooling test coverage | 0 tests | 1 file, ≥4 tests |
| stale archive status lines | 1 | 0 |
| live stale pointer sites | 4 code refs (+2 doctrine-exempt) | 0 (+2 exempt) |

## 5. Risks

- **Test rename churn** — no references outside the file (checked);
  `git mv`-free rename is churn-free.
- **Date adjudication** — the 09-29 README batch record is canonical;
  if the operator rules the physical-move date authoritative, the README
  + `completed.md` change instead (one decision, applied consistently).
- **RUF100 adoption** would flag every historical dead directive
  repo-wide at once — adopt alongside S7's sweep, not before.

## 6. Non-goals

- No checklist wording changes, no rule.json edits (owned by
  `ocr_rule_census.md`), no managed-mode work, no `c901_complexity_debt`
  extraction (deferred proposal owns it), no history rewrites (operator).

## Appendix — provenance

| Source | Content |
|---|---|
| `/mnt/data/tmp/reviews/<model>/` (ephemeral) | nine blind-round reports |
| `doc/local/engineering/code_review.md` Legs 4–7 | zcode legs, durable |
| `doc/local/notes/delegation_reviews.md` | ranking, matrix, spot-checks, leg 9–10 measurements |
| `ocr_rule_census.md` | config/proc/sweep arc; class 6 executed there (S6) |
| `memory/data/review-freshness.json` | fingerprint `a51bb91a91fbe061`, all legs noted |

## Execution Results

### S4 executed 2026-09-30 (class 9, the Medium)

`tests/test_review_tooling.py` — **12 tests, all green** (`.venv/bin/python3
-m pytest tests/test_review_tooling.py -q` → 12 passed in 0.15 s; wider run
with `test_tmp_hygiene.py` → 35 passed). Coverage:

- freshness ledger: record→status FRESH round-trip + upsert-by-fingerprint
  (never duplicates), content change → STALE at same scope, empty ledger →
  UNREVIEWED, `--stack 0` refusal ("lying scope label" guard),
  stack-depth-exceeds refusal (the guard three bake-off rounds relied on),
  stg-failure raises naming the failure (c83a11d9 fix pinned), corrupt
  ledger names itself (`SystemExit`, no traceback).
- selection mapping: `_ref_args` single-commit vs range forms, `_families`
  keeps the hardcoded teeth families + wildcard-prefix rule (Mojo collapse
  trap), `_rule_excluded` glob/dir forms, HOUSE_CHECKLIST carried (≥9).

**Hermetic:** ledger redirected to `tmp_path`; git/stg faked — zero contact
with the real stack or `memory/data/review-freshness.json`.

**Mutation-checked per procedure §5** (strip → RED → restore, backups
byte-identical after): removing the `--stack < 1` guard reds exactly its
test; replacing the stg-failure raise with the old bogus-depth path reds
exactly its test; gutting HOUSE_CHECKLIST reds exactly its test.

One incidental documented in the test file: `_rule_excluded` uses `fnmatch`,
where `**` is just `*` — a directly-nested file (`static/app.bundle.js`)
does not match `static/**/*.bundle.js`. Harmless (the function only filters
the teeth's diff-traffic count; real selection is OCR's globbing), recorded
as a comment, not fixed.

### S5 + S6 resolved, S1–S3 + S7 executed 2026-09-30 (same session)

- **S5 (operator ruling: green gate run).** `make qa` at HEAD `0e9ccfa1`
  (an ancestor of itself): **9/11 in-gate** — the only failures were
  `pytest::test_ruff_format_footprint_clean` (my own new test file had one
  unformatted line; fixed via `ruff format`) and `snapshot-fresh` (sqlite
  generation drift live=414334 vs snapshot=414332 — pre-existing data
  churn, remedied by `make snapshot` exactly as the gate prescribes). Both
  failed legs re-run green at the same SHA: `make snapshot-fresh` rc=0
  (414334/414334), `make test` **3670 passed**. Record stands in the
  honest house form (run 9/11 + two legs re-run clean), superseding the
  misleading "make qa 11/11" in `8ec7575c`. The gate precondition for
  archiving `ocr_selection_drift.md` is now met.
- **S6 (operator ruling: schedule D1 splits).** The four functions are
  booked in `doc/improvements/pending.md` ("C901 D1 splits — four named
  functions scheduled"); each split landing removes its `# noqa: C901`.
- **S1 executed**: the two dead directives at `review_freshness.py:66`
  and `review_selection.py:104` (line moved from 77 after the checklist
  landed) are now bare comments; reason text preserved. Verified:
  `ruff --select S,UP,C901,RUF100` on both files → only the pre-existing
  decorative BLE001 noqa remains (invisible to the audit select).
- **S2 executed**: test renamed to
  `test_non_production_db_passes_through_untouched`; class docstring and
  the orphaned `conftest.py:726` "tmp fixture" phrase reworded. No other
  references existed.
- **S3 executed**: `duckdb_pin_reconciliation.md:16` → `DONE — ARCHIVED
  2026-09-29` (canonical README batch record); the stale "only unticked
  box" sentence dropped; line 22's "Archived 2026-09-30" corrected to
  09-29.
- **S7 executed — and the class was 4× bigger than the leg-10 sample.**
  The full code-scope census (§4b step 8 pattern, all of app.py/helpers/
  tests) found **15 stale docstring refs**, all repointed to their archive
  homes (security ×3, pipeline ×4, graph ×3, tooling ×3, ui ×1,
  database ×1 across 15 files). Only residue: the synthetic `x.md`
  fixture string in `test_markdown_lint.py` (exempt).

**Verification:** ruff gate-config clean on all 19 touched code files;
pytest 61 passed on the four touched suites + 97 on `test_api_graph_unit`;
md-lint clean; `make search-fresh` rc=0; the repaired doc-hygiene sweep
holds at its 2 documented known-exempt hits.

**§8 closure re-review (workspace leg):** `ocr delegate preview` (no ref —
working-tree diff) → **23/23 reviewable**; every hunk read against its
recorded intent — no collateral changes; OCR leg 0.28 s; host leg 3
requests / ≈$0.026. Fixing a finding does not close it; this re-review does.
