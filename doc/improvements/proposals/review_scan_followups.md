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

#### 2.2a The span did not cover a multi-line f-string (RESOLVED 2026-10-09)

Found while executing `gate_query_sql_fragment_registry.md`. The three
teeth above pin the span's *paren* behaviour, but the paren walk is not
the only way a citation line can sit inside a multi-line statement — and
the case it misses is the one that mattered.

bandit cites the **first** physical line of a multi-line f-string. Paren
depth there is 0 (the `f"""` opener carries no paren; the `execute(`
opener is the line *above*), so the walk stopped immediately and never
reached the house noqa on the string's closing line. `_statement_span`
therefore returned `{cited_line}`, and every `gate_query` B608 finding
surfaced **while carrying an S608 noqa in the source** — indistinguishable
from an unadjudicated finding by reading the roster. That is what the
`4fd3a20f..bda590995` scan reported (7 bandit + 2 semgrep sites, all
noqa-justified), and it means the span gate gave false confidence for
every multi-line SQL string in the repo, not just this one.

Meanwhile ruff honours a directive on the line where the multi-line
string *ends*. So the two tools anchor on **different lines**, and no
single-line placement satisfies both: putting `# noqa: S608` on the
f-string's opening line to match bandit's anchor makes the directive
**part of the string literal**, so the text reaches the query and DuckDB
rejects it (`ParserException: syntax error at or near "#"`). There is no
placement that satisfies both linters without widening the gate.

**Fix:** `_statement_span` also covers the body of a triple-quoted
literal opened at the cited line (`_string_close_line`), taking
`max(paren_walk_end, string_close_line)` and bounded by the existing
`_NOQA_SPAN_CAP`. Both anchors now land inside one span, and the
directive stays on the closing line where ruff's span ends.

**Teeth (four, all mutation-verified):**
- `test_multiline_fstring_noqa_on_the_closing_line_is_dropped` — citation
  on the f-string's first line, noqa on the closing line → dropped, for
  both bandit and the doubled SARIF rule id. Mutation: make
  `_string_close_line` return `idx` → red (this is the pre-fix behaviour,
  and the mutation was run against the real module to confirm).
- `test_multiline_fstring_walk_stops_at_the_literal_close` — a
  *neighbouring* statement's S608 noqa must not drop an unadjudicated
  finding whose f-string closed on the previous line. The wider walk is
  otherwise a new leakage channel, and false drops are worse than
  false keeps.
- `test_span_cap_bounds_an_unterminated_string` — an unterminated literal
  cannot widen the walk past the cap.
- `test_single_line_fstring_span_stays_one_line` — the common inline case
  must not widen; the noqa still has to sit on the cited line itself.

**Verified:** all 8 bandit B608 sites in `gate_query.py` adjudicated
against the real `review_scan._adjudicated`, `review_scan.py STACK=1`
surfaces zero B608/S608, and no directive text reaches any query string.
`doc/improvements/proposals/gate_query_sql_fragment_registry.md` §8
records the same finding from the SQL side.

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
- **S6 — Cover a multi-line f-string in the span. DONE 2026-10-09**
  (§2.2a), four mutation-verified tests. Surfaced by executing
  `gate_query_sql_fragment_registry.md`: the paren-only walk returned
  `{cited_line}` for every bandit citation on a multi-line SQL string, so
  adjudicated B608 findings were surfacing in rosters. Widened the span to
  cover the literal body (`_string_close_line`).

## 5. Acceptance

- `tests/test_review_scan.py` + `tests/test_review_tooling.py`: 56
  passed, ruff clean (pre-S6 baseline).
- Mutation-verified: span neutered, span cap dropped, fixed-window
  span, `_rule_excluded` fallback removed — each goes red, restore
  returns to green.
- `--from 4fd3a20f --to bda590995` runs both legs end-to-end from the
  Makefile; selection teeth pass, exit 0.
- S4/S5 are now DONE: sqlfluff has live targets (`schema/**`), shellcheck
  left standing, and the archived record carries the vacuous-golden
  annotation. All slices complete — archivable alongside
  `schema_ddl_review_surface`.
- **S6 adds:** `tests/test_review_scan.py` + `tests/test_gate_query.py`
  = 82 passed, ruff clean (including `--select S608`). All 8 bandit B608
  sites in `gate_query.py` adjudicated against the real
  `review_scan._adjudicated`; `review_scan.py STACK=1` surfaces zero
  B608/S608. Mutation-verified by disabling `_string_close_line` against
  the real module (drop reverts to `False`).

## 6a. Follow-up still open (not part of S6)

The S6 fix corrects the span, but the **placement rule is still
undocumented as a rule** — the reason the defect survived is that
"where does a noqa go in a multi-line f-string" is tribal knowledge
split across two linters. The house checklist item is
"noqa sits on the line the linter reports", which is *wrong* for a
multi-line f-string (ruff ends the span on the closing line; bandit
starts it on the opening line).

- **DONE 2026-10-09:** the two-anchor exception is now stated in the house
  checklist itself (`HOUSE_CHECKLIST` in `helpers/misc/review_selection.py`,
  printed by `make review-patch`) — "ruff's span ENDS on the closing-quotes
  line while bandit CITES the opening line: put the directive after the
  closing quotes, never on the opening line (there it becomes part of the
  string and reaches the query)". That is the surface every review actually
  reads; `doc/procedures/ocr_review.md` only *references* the checklist
  (its table row), so amending the constant was sufficient and avoids
  duplicating a rule in two places.
- **Open:** consider a lint that fails on `# noqa` appearing inside a
  triple-quoted literal. The failure mode is silent and severe (SQL text
  corruption); a mechanical guard is cheap. Deferred: it needs a
  house-wide sweep first to confirm no existing code relies on the
  pattern.

## 6. Risks

- The span gate drops findings whose noqa is anywhere in the cited
  statement. That is the intended reading of the house rule, but it is
  strictly more permissive than ruff's line-anchored noqa; the paren
  balance, the string-literal close walk, and the span cap bound the
  exposure, and the two neighbour tests pin the boundaries (one for
  parens, one for string literals).
- The S6 widening means a finding cited on a multi-line string's opening
  line is dropped by a noqa anywhere up to that string's close. This is
  correct for both current anchors, but it is a **behavioural change** to
  a shared gate: rosters generated before S6 kept findings that
  post-S6 drops. Any comparison against a pre-S6 roster needs this noted
  (it does not affect the `4fd3a20f..bda590995` conclusions, whose
  findings were the unadjudicated kind).
- `REVIEW_RANGE_ARGS` makes `FROM`/`TO` outrank `COMMIT`/`STACK`
  silently. A caller passing both gets FROM/TO; the helpers' own
  conflict checks cannot fire because the other flags never reach them.
  Documented in the macro comment; worth a lint-level guard later.
- Widening the scan's path scope is off the table (operator ruling,
  §3), so the runtime concern in the archived proposal's cost note does
  not arise.
