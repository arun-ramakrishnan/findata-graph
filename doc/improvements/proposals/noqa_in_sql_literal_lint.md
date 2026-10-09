---
title: "noqa-inside-SQL-literal: a static_checks family so a suppression can never become query text"
status: proposed
filed: "2026-10-09"
executed: null
completed_md: null
area: "helpers/validators/static_checks.py + tests/test_static_checks.py + doc/procedures/ocr_review.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). -->

# noqa-inside-SQL-literal — a `static_checks` family so a suppression can never become query text

## 1. Motivation

`review_scan_followups.md` §6a left one item open: *consider a lint that
fails on `# noqa` appearing inside a triple-quoted literal*, deferred
pending a house-wide sweep to confirm nothing relies on the pattern.

The sweep is done (§2) and it clears the way. The hazard is not
hypothetical — it was **live in this arc** and was caught by hand, not by
any gate:

```python
# the defect, as briefly written in helpers/misc/gate_query.py
rows = con.execute(
    f"""SELECT r.run_id, r.started_at, b.seconds, b.budget_s, b.status, r.wt  # noqa: S608
        FROM bench b JOIN runs r USING (run_id)
        WHERE b.bench = ?{status_clause} LIMIT ?""",
    [args.leg, args.last],
)
```

The directive was meant to sit beside bandit's citation line. Because the
f-string had not closed yet, the `# noqa: S608` became **string
content**: it was interpolated into the SQL sent to DuckDB, which
rejects it (`ParserException: syntax error at or near "#"`). The failure
mode is silent — ruff flagged S608 (correctly ignoring an inert
directive inside a literal), but nothing in the QA chain said *the query
is now corrupt*. It was caught only because a test executed that exact
statement.

The house placement rule already carries the knowledge
(`HOUSE_CHECKLIST` in `helpers/misc/review_selection.py`, amended in the
prior arc) and prime-pool prior art records the same in prose — *"ruff
attributes the diagnostic to the line of the flagged SUBEXPRESSION … S608
must TRAIL the f-string line itself"*, with a three-round proof loop that
cost a suppressed-finding round-trip. A rule three people have
re-derived is a rule that wants a mechanical guard.

## 2. Evidence — the sweep this was deferred on

Repo-wide, `tokenize`-based (a grep cannot tell a literal from a comment;
AST source-segment extraction cannot either — see §2.2).

### 2.1 Result

**43** `# noqa` occurrences sit inside multi-line string literals.
**Zero** are SQL. Every one is either:

- prose that *documents* the convention — `helpers/misc/review_scan.py:247`
  (`"""None = no noqa; {"*"} = bare \`# noqa\` …"""`),
  `helpers/validators/static_checks.py:1311` (the C901-placement
  docstring), `doc/templates/python_module.py:3-27` (the module template);
- or fixture **data** that simulates file content — `tests/test_review_scan.py:143`
  and the `tests/test_*_search.py` widget-audit snippets.

So the dangerous class is empty in the tree, and a blocking family will
not need a grandfather list.

### 2.2 A false-positive method, recorded so it is not repeated

A first pass used `ast.get_source_segment` on the arguments of every
`.execute(...)` call and grepped the returned text for `noqa`. It
reported **76 hits** across `helpers/`, `app.py`, `desktop/` and
`tests/` — all **false**. For implicitly concatenated literals

```python
execute("SELECT ... "  # noqa: S608  # parameterized
        "WHERE ...", params)
```

the source segment spans from the first quote to the last, so it
**swallows the real comment sitting between the parts**. Source-segment
text cannot distinguish that comment from text inside a literal. The
distinguishing test is `tokenize`, which labels the former `COMMENT` and
the latter `STRING`; §3.2 is built on it.

## 3. Design

### 3.1 Family

`check_noqa_in_sql_literal` in `helpers/validators/static_checks.py`,
registered in `CHECKS`. Returns the `(fatal, advisory)` tuple like every
other family; **blocking** (fatal), because §2.1 proves the tree is clean
and the defect class is silent.

Scope follows `check_python_syntax`: `_scoped_py_files(scope)` when a
dirty scope is present, else `_walk(REPO_ROOT, ".py")` — so `--dirty`
gating keeps working and clean trees pay one `ast.parse` per file.

### 3.2 Detection

For every `ast.Call` whose callee is `execute` / `executemany` /
`executescript` (attribute or bare name — sqlite3, duckdb, psycopg):

1. take `ast.get_source_segment` of each positional argument and each
   keyword value;
2. `tokenize` that segment;
3. **fail if `# noqa` appears inside a `tokenize.STRING` token.**

A `COMMENT` token in the same segment is a legitimate suppression between
concatenated parts and is ignored — that is precisely the §2.2 trap, and
tokenize is what defuses it. Docstrings are never reached, because a
docstring is not an argument to `execute`.

### 3.3 What this does not claim

- **No variable indirection.** `sql = f"""…# noqa…"""; con.execute(sql)`
  is not resolved — that is dataflow, and bandit-style resolution is out
  of scope. Recorded as a known limit, not silently assumed away.
- **Not a general "inert directive" lint.** A `# noqa` inside any
  non-docstring literal is inert by construction; only the SQL case
  corrupts a query. Widening to all literals would flag the §2.1
  fixtures for no safety gain. Non-goal.
- **No `--fix`.** The repair is a human judgement (move the directive
  after the closing quotes), consistent with the md-lint posture.

## 4. Acceptance

1. `make static-checks` reports **0 fatal** on the live tree (§2.1).
2. A seeded defect is caught: an `execute()` whose f-string carries
   `# noqa` inside the literal → fatal, naming file and line.
3. Seeded non-defects pass: the §2.2 concatenated-comment shape; a
   docstring mentioning `noqa`; an `executemany` sibling case.
4. Tests are mutation-verified: neuter the STRING-token test → red;
   restore → green.
5. `ruff check` clean on the touched files; `make md-lint` clean.
6. Cross-file consistency (§5) done in the same change.

## 5. Cross-file consistency (same change, not a follow-up)

`doc/procedures/ocr_review.md:136` states *"26 executable families"*.
That number is **already stale**: the census metric
(`rg -n "^def check_"`) yields **25** today — one family was folded into
a combined entry after the 2026-09-30 census in
`doc/improvements/archive/tooling/ocr_rule_census.md:120`. Adding this
family returns the count to 26, so the line becomes *true* again by
coincidence rather than by verification. The proposal therefore
**re-verifies the number** and names the new family in the enumeration,
and records the drift here so a future reader does not read the
coincidence as confirmation of the old number.

## 6. Slices

- **S1 — House-wide sweep.** DONE (§2): 43 hits, zero SQL; §2.2 records
  the false-positive method.
- **S2 — Family + registration.** DONE: `check_noqa_in_sql_literal` +
  `_sql_literal_noqa_findings` (split out for C901 headroom) +
  `_node_segment` + `_noqa_inside_string_tokens`, registered in `CHECKS`.
  Live tree: **0 fatal**, ~1.7s full-repo.
- **S3 — Teeth.** DONE: five tests in `tests/test_static_checks.py` —
  the defect, the §2.2 concatenated-comment shape, the house-correct
  closing-quotes placement, the `executemany` + docstring pair, and the
  single-line shape.
- **S4 — Mutation verification.** DONE: three mutants all RED —
  dropping `FSTRING_MIDDLE` (3 fail), matching every token type so the
  COMMENT distinction dies (1 fail), rebasing on `node.lineno` (1 fail).
- **S5 — Cross-file consistency.** DONE (§5): `ocr_review.md` re-verified
  and annotated so the coincidence is not mistaken for confirmation.
- **S6 — Acceptance sweep.** DONE: full `static_checks` rc=0 with the new
  family green; `ruff check` and `--select S,UP,C901` clean on both
  touched files; `md-lint` clean; `search-fresh` all four indexes fresh.

## 8. Execution findings

Two things the implementation taught that the design did not anticipate.

### 8.1 PEP 701 would have made this family vacuous

The first working version matched `tokenize.STRING` only and reported
**0 findings on every seeded defect**. On Python 3.12+ an f-string is not
one `STRING` token — the literal chunk arrives as `FSTRING_MIDDLE` between
`FSTRING_START`/`FSTRING_END`, so the directive was invisible to the check.
This is the same failure shape as the sqlfluff `start_line_no` incident
that made a whole scan leg report a vacuous zero: **a detector that
reports 0 because it recognises nothing is indistinguishable from a clean
tree.** The family now matches `STRING` + `FSTRING_MIDDLE` +
`TSTRING_MIDDLE`, and mutant #1 in S4 exists specifically to keep that
honest.

### 8.2 The off-by-one only looked right by coincidence

Findings were first rebased on the *call's* line while the arithmetic was
relative to the *argument's* text. Every seeded multi-line fixture put the
argument one line below the call, so the two formulations agreed and the
tests passed. The single-line shape
(`con.execute(f"""…directive…""")`, same line) exposed it. Pinned by
`test_noqa_inside_single_line_fstring_reports_the_right_line`, and
mutant #3 in S4 guards it.

### 8.3 Cost

Naive full-repo implementation: **6.8s** on a "fast" leg. Fixed to
**~1.7s** by two changes, both recorded in-code: a necessary-condition
prefilter (`noqa` present AND an execute call present — 237 of 493 files
survive) and replacing `ast.get_source_segment`, which re-splits the whole
source per argument (`_splitlines_no_ff`, 4.2s of the original 6.8s),
with exact byte-offset slicing against a pre-split line list.

## 7. Risks

- **False positive on legitimate fixture data.** Mitigated by §3.2's
  token-level test plus §2.1's evidence; the seeded cases pin both
  directions. A future test that genuinely needs a noqa-bearing literal
  *as data* inside an `execute` argument would fail the gate — that is the
  intended trade, and the fix is to not do that.
- **Cost.** One `ast.parse` + one `tokenize` per `execute` call-bearing
  file. The syntax leg already parses every file in the same run, so the
  marginal cost is the tokenize of execute-argument segments only.
- **Ruff/bandit drift.** The family asserts a *placement* fact that both
  linters depend on (§1). If a future ruff release re-anchors S608 onto
  the statement line, the family stays correct — it does not encode
  which line each tool picks, only that a directive may not live inside
  the literal.
