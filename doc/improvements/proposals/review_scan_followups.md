---
title: "Native scan leg follow-ups — rule-scope fallback, statement-span noqa gate, --from/--to ranges"
status: proposed
filed: "2026-10-09"
executed: null
completed_md: null
area: "helpers/misc/review_scan.py, helpers/misc/review_selection.py, Makefile, tests/, .opencodereview/rule.json"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). -->

# Native scan leg follow-ups — rule-scope fallback, statement-span noqa gate, `--from/--to` ranges

**Date:** 2026-10-09 · **Status:** PROPOSED · **Area:**
`helpers/misc/review_scan.py`, `helpers/misc/review_selection.py`,
`Makefile`, `tests/test_review_scan.py`, `tests/test_review_tooling.py`,
`.opencodereview/rule.json`

Follow-on to `../archive/tooling/review_scan_leg.md` (executed
2026-10-09, completed.md 371). Slices S1 and S2 of that proposal
shipped in the `code_review` patch; S3 (this document) records the
defects their acceptance runs exposed, the two code fixes, the test
teeth that were missing for a shipped fix, and one coverage finding
that acceptance could not certify.

## 1. Motivation

The archived proposal's acceptance was three golden ranges. Running
them surfaced three defects the unit tests did not cover, one of them
a crash:

- The noqa gate shipped **without any test** — the change that made
  golden run 1 pass (10 leaked semgrep findings → 0) was unpinned, so
  a future refactor could silently reintroduce the noise class the leg
  exists to filter.
- Golden run 1 (KNN commit `03743294b`, scanned from a worktree at that
  commit — the leg's method for a historical range) **crashed** with
  `UnboundLocalError` — see §2.1.
- A landed, non-HEAD-relative range (`4fd3a20f..bda590995`, 236 files)
  had **no CLI encoding at all**: `--stack` is `HEAD~N..HEAD` and
  `--commit` is a single ref. The operator could not review the range
  they were working on without checking out its head. See §2.3.

## 2. Fixes

### 2.1 `_rule_scope` crashed when `review_selection` was unimportable

`review_scan._rule_scope()` imports `_families` and `_rule_excluded`
from `review_selection` inside a `try`. The `except` branch rebound
only `include_roots`/`exclude_globs`; the loop below still calls
`_rule_excluded(...)`, which was never bound → `UnboundLocalError` and
a dead leg. Observed on the `03743294b` tree, which predates
`review_selection.py` entirely, so the import cannot resolve.

**Fix:** bind a no-op `_rule_excluded` fallback *before* the import, so
the documented degrade ("missing/corrupt rule.json must not kill the
leg") is what actually happens. Scoping then falls back to "scan
everything changed" — the fail-open direction the leg already chose
elsewhere (`_adjudicated` keeps findings when content is unavailable).

**Teeth:** `test_rule_scope_survives_a_missing_selection_helper` —
monkeypatches `sys.modules["helpers.misc.review_selection"] = None`
and asserts the full changed set comes back. Mutation-verified: delete
the fallback line → the test raises the original `UnboundLocalError`.

### 2.2 The statement-span noqa gate had no test

semgrep anchors `sqlalchemy-execute-raw-query` at the `conn.execute(`
line; ruff anchors S608 on the f-string, so the house `noqa` sits on the
*argument* line below. A same-line gate therefore cannot drop the
adjudicated sites — golden run 1 reported 10 medium findings, all of
them noqa-justified. `_statement_span` walks forward while parentheses
stay open (capped at `_NOQA_SPAN_CAP`) so the drop is scoped to the
cited statement.

The fix was verified by a golden run but had **no test**: `noqa sits on
the line the linter reports` is a house rule, and the span is exactly
where that rule gets subtle.

**Teeth (three, all mutation-verified):**
- `test_noqa_below_the_citation_drops` — citation on `conn.execute(`,
  noqa on the argument line → dropped, for both the bandit (`B608`) and
  semgrep (doubled SARIF `ruleId`) forms. Mutation: replace the span
  walk with `[line - 1]` → red.
- `test_neighbouring_statement_noqa_does_not_leak` — an unadjudicated
  `execute()` directly above a noqa-carrying one survives. This is the
  false-drop the paren balance exists to prevent; mutation: swap the
  paren walk for a fixed ±1 window → red.
- `test_span_cap_bounds_a_runaway_paren` — a stray `(` in a literal
  cannot widen the walk over the file. Mutation: drop the cap → red.

### 2.3 `--from/--to` for both review legs

`review_scan.py` already had `--from/--to`; `review_selection.py` did
not, and neither `make` target passed them. A landed pair had no
encoding, so the operator's own range was unreviewable.

**Fix, three surfaces:**
- `review_selection._ref_args()` takes `from_ref`/`to_ref` and returns
  `["--from", base, "--to", head]` — no git call, matching the existing
  commit-mode contract (OCR resolves refs itself).
- `review_selection.main()` rejects the combinations: `--from/--to`
  with `--stack` or `--commit` → exit 2; a half range → exit 2. The
  messages reuse `review_scan`'s wording verbatim ("specify one of
  --stack, --commit or --from/--to", "--from and --to go together") so
  one rule has one voice. The pre-existing `--stack`+`--commit` message
  is unchanged (a test pins it).
- `Makefile` gains `REVIEW_RANGE_ARGS`, shared by `review-patch` and
  `review-scan`: `FROM`/`TO` win, else `COMMIT`, else `STACK`.
  `FROM` without `TO` aborts in the macro rather than reaching argparse
  as a dangling half-range. `review-patch`'s scan invocation now also
  inherits `OFFLINE=1`, which it previously ignored.

Freshness and roster scope follow the mode: `--from/--to` prints
`review-freshness: n/a` (the ledger fingerprints `HEAD~N..HEAD` only,
procedure §1b) and names the range in the durable roster path, the way
`--commit` already did.

**Teeth (three):** `_ref_args` passthrough with git forbidden
(`AssertionError` if subprocess runs); half range → exit 2; from/to
plus commit or stack → exit 2.

**Verified live** on the motivating range, from a worktree at its
head: selection 236 files, teeth OK, exit 0; scan 16 findings, roster
written.

## 3. Finding — two goldens were vacuously clean (RESOLVED 2026-10-09)

Golden runs 3 and 4 report "all scanners clean", but the legs never
ran on the files they were meant to judge:

| Golden | Expected to run | What happened |
|---|---|---|
| `4a2dcf7b6` (llamacpp) | shellcheck on `vendor/llamacpp/build.sh` (SC2015), osv on the Vue lockfile | `build.sh` is outside `rule.json`'s `include` → "shellcheck: no matching files"; the commit changes no lockfile (the `source-map-js` catch belongs to the multi-commit range `e4258377b..4a2dcf7b6`) |
| `2dd8818c8` (snapshots) | sqlfluff on `snapshots/parquet/_schema.sqlite.sql` | `snapshots/**` is in `rule.json`'s `exclude` → "sqlfluff: no matching files" |

**Root cause (revised 2026-10-09, operator ruling):** this is not a
scoping mistake. The repo has **no tracked `.sh` outside `vendor/`** and
**no tracked `.sql` outside `snapshots/`** (`git ls-files '*.sh'`,
`git ls-files '*.sql'`), so excluding vendor and snapshots leaves the
shellcheck and sqlfluff legs with an empty target set. There is no
live `.sql` file to scan: the schema is DDL embedded in
`helpers/maintenance/rebuild_schema.py` (the `CREATE TABLE` constants a
Python module transforms), and `snapshots/parquet/_schema.sqlite.sql`
is a generated backup written by `helpers/maintenance/snapshot_db.py`.
Per the operator: vendor trees are correctly excluded (they build, not
ship) and snapshots are backup copies, not sources.

Consequence: **no scoping override would change this.** Both legs are
structurally dead here, and the live schema the operator cares about is
already covered where it actually lives — as Python in `helpers/`,
which the bandit and semgrep legs do reach.

**This was a coverage gap, not a pass.** Golden 3 and 4 certify nothing
about shellcheck or sqlfluff in this repo. Resolution: sqlfluff gained
live targets via `schema/` (sibling proposal, executed) and shellcheck
is left standing by operator default — see S4/S5 in §4.

## 4. Slices

- **S1 — Fix the rule-scope crash.** DONE (§2.1), mutation-verified.
- **S2 — Pin the statement-span noqa gate.** DONE (§2.2), three
  mutation-verified tests.
- **S3 — `--from/--to` on both legs.** DONE (§2.3), three tests +
  live run on `4fd3a20f..bda590995`.
- **S4 — The dead-leg question. RESOLVED 2026-10-09, split:**
  sqlfluff is **ALIVE** — option 3 landed as the sibling proposal
  `schema_ddl_review_surface` (executed, completed.md 372): all 14
  `memory/` DBs dump to `schema/<engine>/`, rule-scoped in, per-engine
  dialect wired, and the leg's first real catch was its own vacuous-0
  (`start_line_no` vs `line_no`, found by the golden on the schema
  commit). shellcheck **stays standing** — no tracked non-vendor `.sh`
  exists, the status line is self-documenting, and dropping it would
  need a code change to revive. Not dropped (operator default; no word
  to remove it).
- **S5 — Record the vacuous goldens. DONE 2026-10-09:** the executed
  record in `../archive/tooling/review_scan_leg.md` §10 now carries the
  annotation — goldens `4a2dcf7b6` and `2dd8818c8` produced no
  shellcheck or sqlfluff verdict (empty target sets), so "goldens
  green" must not be read as covering those legs.

## 5. Acceptance

- `tests/test_review_scan.py` + `tests/test_review_tooling.py`: 56
  passed, ruff clean.
- Mutation-verified: span neutered, span cap dropped, fixed-window
  span, `_rule_excluded` fallback removed — each goes red, restore
  returns to green.
- `--from 4fd3a20f --to bda590995` runs both legs end-to-end from the
  Makefile; selection teeth pass, exit 0.
- S4/S5 are now DONE: sqlfluff has live targets (`schema/**`), shellcheck
  left standing, and the archived record carries the vacuous-golden
  annotation. All slices complete — archivable alongside
  `schema_ddl_review_surface`.

## 6. Risks

- The span gate drops findings whose noqa is anywhere in the cited
  statement. That is the intended reading of the house rule, but it is
  strictly more permissive than ruff's line-anchored noqa; the paren
  balance and the span cap bound the exposure, and the neighbour test
  pins the boundary.
- `REVIEW_RANGE_ARGS` makes `FROM`/`TO` outrank `COMMIT`/`STACK`
  silently. A caller passing both gets FROM/TO; the helpers' own
  conflict checks cannot fire because the other flags never reach them.
  Documented in the macro comment; worth a lint-level guard later.
- Widening the scan's path scope is off the table (operator ruling,
  §3), so the runtime concern in the archived proposal's cost note does
  not arise.
