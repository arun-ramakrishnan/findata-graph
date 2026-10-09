---
title: "Native scan leg — repo-managed scanners over the review diff"
status: executed
filed: "2026-10-09"
executed: "2026-10-09"
completed_md: "371"
area: "helpers/misc/review_scan.py, Makefile, tests/, doc/procedures/ocr_review.md"
---

# Native scan leg — repo-managed scanners over the review diff

**Date:** 2026-10-09 · **Status:** PROPOSED · **Area:**
`helpers/misc/review_scan.py` (new), `Makefile` (`review-scan` target +
roster line in `review-patch`), `tests/test_review_scan.py` (new),
`doc/procedures/ocr_review.md` (§1c).

## 1. Motivation

The OpenQodex trial (`doc/local/evaluations/qodex_assessment.md` — three
scan trials plus the full component matrix) settled both what a scanner
leg is worth here and what it costs to keep one:

- **Yield:** across four ranges the only real finding was one
  dependency-vuln class (`source-map-js@1.2.1`, CVSS 8.7, fixed 1.2.2) —
  and the changed-line filter *hid* it. Everything else was the
  noqa-justified S608/S310 accepted-risk class, re-reported by tools that
  cannot see ruff `# noqa` adjudications.
- **Cost of the external route:** OpenQodex's pins were stale (ruff 0.8.4
  broken on `target-version = "py314"`; semgrep ~180 releases behind),
  and its custom-scanner trust model cannot reference the repo venv —
  `install: path` refuses any binary inside the repo, and the `uv` form
  installs OpenQodex-private copies (470 MB under `~/.openqodex` before
  the cleanup).

Operator ruling (2026-10-09): **no external legs.** The scanner stack is
repo-managed through the new `review` optional-extra (pyproject.toml,
unpinned specs — `uv.lock` holds the pins): semgrep, bandit,
shellcheck-py, sqlfluff (verified installed in the shared venv:
1.180.0 / 1.9.4 / 0.11.0 / 4.4.0). This proposal builds the leg that
runs **those** tools: a deterministic, advisory, diff-scoped scan that
runs before the delegation review and hands the host a candidate roster.

## 2. What the leg is — and is not

**Is:** a runner over the review range that executes the repo's own
scanners on changed files, keeps only findings on changed lines, drops
findings whose cited line carries a house noqa adjudication, prints one
roster line per surviving finding, and exits 0 always.

**Is not:** a gate (house rule — review never gate-couples); an LLM
reviewer; a replacement for the OCR delegation loop (it is that loop's
deterministic pre-leg); a secrets scanner (§7).

## 3. Design

### 3.1 CLI and range resolution

`helpers/misc/review_scan.py` mirrors `review_selection.py` (house
pattern): `--stack N` (⇒ `HEAD~N..HEAD`), `--from X --to Y`, `--commit
<sha>` (sugar for `<sha>^..<sha>` — one commit, not a range), `--offline`,
`--only`, `--skip`, `--json`. Range resolution **imports** `_ref_args`
from `review_selection` rather than re-deriving it — one stack→ref
semantics, and the workspace-mode-on-a-refreshed-stack documentation
stays in one place.

### 3.2 Changed-line mapper (test T1)

`git diff -U0 X..Y` → per-file new-file line sets built from
`@@ -a,b +c,d @@` hunks (`+` ranges only). OpenQodex's deletion rule is
adopted deliberately: the line just above and just below each pure-
deletion point also counts as changed (a removed check can carry a
finding). Context lines never count.

### 3.3 Scanner runners — SKIP-if-missing, like `markdown_lint.py`

| Scanner | Invocation | Files | Network |
|---|---|---|---|
| bandit | `bandit -f json <files>` → B-codes | `.py`/`.pyi` | no |
| shellcheck | `shellcheck --format=json1 <files>` | `.sh`/`.bash` | no |
| sqlfluff | `sqlfluff lint --dialect <sqlite\|duckdb\|ansi> <files>` | `.sql` | no |
| semgrep | `semgrep scan --metrics=off --config p/default --config p/security-audit --config p/secrets --sarif --output {tmp} <files>` | `.py`/`.pyi` | yes (registry) |
| osv leg | stdlib `urllib` POST to `https://api.osv.dev/v1/querybatch` | changed lockfiles | yes (osv.dev) |

Binaries resolve from the repo venv (`.venv/bin` via the shared symlink —
never a hardcoded absolute path, so main checkout and worktrees behave
identically). Each runner prints a status line
(`ran / not installed (uv sync --extra review) / skipped (--offline)`)
and never raises; the leg exits 0 regardless.

### 3.4 NOQA_MAP — the filter that makes the leg worth running (test T2)

The trial's dominant noise class is findings on lines the arcs already
adjudicated with ruff noqas that bandit/semgrep cannot see. The leg
parses each cited line (working tree at the range head) for
`# noqa: <codes>` and drops a finding whose rule maps to a noqa'd code:

| scanner:rule | noqa code |
|---|---|
| bandit `B608`; semgrep `…sqlalchemy-execute-raw-query…`, `…formatted-sql-query…` | `S608` |
| bandit `B310`; semgrep `…dynamic-urllib-use…` | `S310` |
| bandit `B404` / `B603` / `B607` | `S404` / `S603` / `S607` |

The map is a house data file; each row cites its adjudication
(`S608`: constant table name + `?`-bound values — bake-off classes 1–2
and the trial-2/3 verifications; `S310`: loopback-literal URLs — trial
3). A noqa for a *different* code must NOT drop the finding — T2 asserts
both directions. The tools' native suppressions (`# nosec`,
`# nosemgrep`) are honored by the tools themselves.

### 3.5 The osv leg reports the whole lockfile, not changed lines

The trial's one real finding was hidden precisely because a dependency's
vulnerable version is never the changed line. The osv leg therefore
parses **every** dependency from a **changed** lockfile (uv.lock
`[[package]]` name/version; package-lock v3 `packages` map) and reports
all vulns in it — the changed-line filter is explicitly not applied.
`--offline` skips it.

### 3.6 Output

A roster for `make review-patch` — one line per finding
(`severity  scanner:rule  file:line  message-first-line`), per-scanner
status lines above it, and a summary line (`N findings across M files` /
`all scanners clean`). `--json` writes the full artifact under `$TMPDIR`
for the host. Exit code 0 always.

### 3.7 Deliberately not in the leg

- **ruff** — qa's lint leg owns E,F (house §4 gate-dedup: a finding
  `make qa` already flags is not a review finding). A test asserts the
  leg never invokes ruff.
- **secrets** — §7.
- the advisory `lint-audit` S/UP/C901 selects — that leg owns those
  codes already.

## 4. Slices

- **S1** — skeleton: range resolution (import `_ref_args`), changed-line
  mapper, status-line plumbing. Tests T1.
- **S2** — bandit + shellcheck legs, SKIP-if-missing, severity mapping.
  Tests T3, T5 (partial).
- **S3** — NOQA_MAP + cited-line parsing. Tests T2; the golden run on
  `03743294b^..03743294b` must yield **0 findings** (the KNN commit's
  12 semgrep majors + B608s are all S608-adjudicated) — the leg's
  acceptance proof is that it turns the trial's 32-finding noise into
  zero while every scanner still reports `ran`.
- **S4** — semgrep leg + `--offline`. Tests T5 + a golden semgrep SARIF
  fixture.
- **S5** — osv leg: uv.lock + package-lock parsers, querybatch,
  whole-lockfile reporting. Tests T4 (parsers against fixtures; a planted
  old-version dep must surface); the live-query test is network-optional.
- **S6** — sqlfluff leg + Makefile wiring (`review-scan` target, roster
  line in `review-patch`, help entries placed alphabetically —
  MakefileHelpCompleteness) + procedure §1c + `make search-fresh APPLY=1`.

## 5. Acceptance

1. Golden ranges from the trial: `03743294b^..03743294b` → **0
   findings**, all scanners `ran`. `4a2dcf7b6^..4a2dcf7b6` → shellcheck
   SC2015 (info) and nothing else new. `2dd8818c8^..2dd8818c8` →
   sqlfluff clean.
2. Fixture lockfile with a planted vulnerable dep → osv leg reports it
   (the source-map-js class, offline-parsed).
3. `make qa` green on the new helper: ruff E,F, ty, pytest,
   static_checks, deptry (the `review` group is already wired into
   `optional_dependencies_dev_groups` — verified exit 0 pre-sync).
4. SKIP-if-missing proven by mutation: hide semgrep from PATH → status
   line, exit 0, no crash.
5. All tests mutation-verified — house doctrine: a test that cannot fail
   on the bug it pins is a docstring.

## 6. Risks

- **semgrep registry latency** (~10–45 s measured in the trials) — the
  leg is a pre-step, not a gate; `--offline` exists; caching is P2.
- **NOQA_MAP rot** — a stale map entry silently drops a real finding.
  Mitigations: each row cites its adjudication; T2 pins both directions;
  the existing house-checklist noqa item already forces a review whenever
  a `# noqa: S608/S310/S6xx` lands in an arc.
- **osv.dev availability** — `--offline`, and the failure mode is a
  status line, never a false clean.
- **No eval-gate bullet** — nothing here alters ontology/query-visible
  semantics (rosters, crosswalks, extractor rules untouched); the review
  roster gains a section, which is not an ontology surface.

## 7. Secrets — the open gap

gitleaks has no PyPI distribution (`gitleaks-py` 0.3.1 is a stale
wrapper predating gitleaks 8.x), and an in-repo binary is refused by
OpenQodex's trust model — so there is no repo-managed path to its
detectors today. The leg therefore ships **without** a secrets leg;
existing coverage stands (`helpers/misc/git_secret_scan.py`, full
history, `make secret-scan`). Deferred option if diff-scoped secrets are
wanted: a house-grown pass reusing `git_secret_scan.py`'s `PATTERNS`
over changed lines only (P2, its own slice).

## 8. Component disposition record (2026-10-09)

Which components the trial replaced with trust-approved custom scanners,
which stayed on the built-in pins, and where each lands after this
proposal. Versions verified live during the trial.

### 8.1 Custom scanners installed by us (trust-approved replacements)

| Custom leg | Built-in pin (rejected) | Installed | Install form | Fate after this proposal |
|---|---|---|---|---|
| `ruff16` | ruff **0.8.4** — *functionally broken*, cannot parse `target-version = "py314"` | **0.16.10** | GitHub release, sha256 checked against ruff's published `sha256.sum` | superseded — the venv's ruff 0.16.10 (`dev` extra) covers it; qa's lint leg already owns E,F, so the leg §3.7 does not run ruff |
| `semgrep1180` | semgrep **1.94.0** (~180 releases stale) | **1.180.0** | `install: { uv: semgrep==1.180.0 }` | superseded — `semgrep` in the `review` extra (venv, 1.180.0) |
| `shellcheck011` | shellcheck **0.10.0** (0.11.0 upstream) | **0.11.0** | `install: { uv: shellcheck-py==0.11.0.1 }`, json-map format (0.11 ships no SARIF) | superseded — `shellcheck-py` in the `review` extra (venv, 0.11.0) |
| `gitleaks830` | gitleaks **8.21.2** (8.30.1 upstream) | **8.30.1** | `install: path`, binary at `~/.local/bin/gitleaks` (in-repo binaries are refused by the trust model; no PyPI package) | **not carried** — secrets leg deferred (§7); the `~/.local/bin` binary stays as the only gitleaks copy |

All four were approved via `openqodex trust` and trialed end-to-end
(see `doc/local/evaluations/qodex_assessment.md`, component matrix).
The OpenQodex copies of the first three (and of built-in bandit's runtime)
were deleted in the 2026-10-09 cleanup once the `review` extra made them
redundant: `~/.openqodex` went 548M → 70M.

### 8.2 Left at their original built-in versions

| Component | Built-in version | Why unchanged | Fate after this proposal |
|---|---|---|---|
| bandit | **1.9.4** | already current upstream | venv `bandit` (review extra) feeds the leg |
| oxlint | **1.86.0** | ≈current (upstream 1.87.0; the project releases weekly); never replaced | **not carried** — npm-distributed, no PyPI path; its 15M binary remains in `~/.openqodex` as the only copy |
| osv-scanner | **2.6.0** | already current upstream; its vuln DB is queried live from osv.dev regardless of scanner version | **not carried as a binary** — the native osv leg (§3.5) is stdlib `urllib` against the osv.dev API; the 55M binary remains in `~/.openqodex` as the only copy |
| sqllint | built-in (no version) | ran once on the schema range (0 findings) | replaced by `sqlfluff` (review extra, 4.4.0) — the sqllint-equivalent leg |
| hadolint | 2.15.1 | trialed once on the only Dockerfile commit in history (0 findings) | **removed** in the cleanup — the repo has no Dockerfile; its ~54M copy was deleted |

### 8.3 Components that never triggered (untriable by content)

`actionlint` (no `.github/workflows` file has ever existed in this repo),
`golangci-lint` (no Go file ever), `brakeman` / `rubocop` (no Ruby/Rails
ever) — verified via `git log --all -- <paths>`. No install, no custom
leg, nothing to carry.

**Net effect:** of thirteen OpenQodex components, three are repo-managed
in the venv and feed the native leg (semgrep, bandit, shellcheck-py),
one is replaced by a venv package (sqllint → sqlfluff), one becomes an
API call (osv-scanner → the §3.5 leg), one is deferred with its gap
documented (gitleaks → §7), one stays an OpenQodex-only binary with no
repo-managed path (oxlint), and the rest were never applicable. Nothing
outside the repo's own dependency management runs after this proposal
lands.

## 9. Invocation

Refs are SHAs — resolve stgit patch names with `stg id <name>` first
(procedure §Step 0: never review the moving top patch by name). Workspace
mode is empty on a refreshed stack — always pass a ref (procedure §1b).

### Single commit

```bash
.venv/bin/python3 helpers/misc/review_scan.py --commit <sha>
# equivalent explicit form:
.venv/bin/python3 helpers/misc/review_scan.py --from <sha>^ --to <sha>
```

### Range of commits

```bash
.venv/bin/python3 helpers/misc/review_scan.py --from <older> --to <newer>
# the whole applied stack, mirroring review_selection.py:
.venv/bin/python3 helpers/misc/review_scan.py --stack N     # HEAD~N..HEAD
```

### Options

| Flag | Effect |
|---|---|
| `--offline` | skips the semgrep (registry) and osv (osv.dev) legs; status line says so |
| `--only <list>` / `--skip <list>` | restrict to / drop from `bandit, shellcheck, sqlfluff, semgrep, osv` |
| `--json` | writes the full findings artifact to `$TMPDIR` (below) |
| `--stack N` / `--from X --to Y` / `--commit <sha>` | the change to scan (above) |

Makefile wrappers (S6): `make review-scan` standalone,
`make review-scan STACK=N`; `make review-patch` gains the scan roster
section in its output.

### Where the reviews live

| Artifact | Location |
|---|---|
| Roster + per-scanner status lines | stdout, inside `make review-patch`'s output (S6), AND durably under `outputs/reviews/` — `review_scan_<base>..<head>_<digest>.md` / `review_patch_<scope>_<digest>.md` (operator ruling 2026-10-09: the roster is the deliverable, not stdout ephemera; digest-keyed so a rerun overwrites its own generation) |
| `--json` findings artifact | `outputs/reviews/review_scan_<range-fingerprint>.json` (moved out of $TMPDIR 2026-10-09 with the roster — durable, gitignored, same store) |
| Review-round record (was this diff reviewed?) | `outputs/reviews/review-freshness.json`, via `helpers/misc/review_freshness.py --stack N --record --leg delegation --note "<verdict pointer>"` — unchanged: the scan is a pre-step of the delegation round, not a leg of its own |
| Durable evidence (findings, accounting, dispositions) | `doc/local/engineering/code_review.md` — the house evidence home, same as every prior round; never cite `$TMPDIR` paths as evidence |

For comparison, the OpenQodex trial's own scan reports were written
wherever `--output` pointed (`$TMPDIR/qodex_*.md`, ephemeral) with the
substance recorded in `doc/local/evaluations/qodex_assessment.md` — and
`openqodex review` (never run here: no Claude Code/Codex installed)
would have written `report.html/.md/.json/.sarif` under
`.openqodex/reviews/` in the repo.

## 10. Execution record (2026-10-09, landed from the worktree move)

- **S1–S5 landed with the patch**, S6 wired same day (`review-scan`
  target + roster appended to `review-patch` + `.PHONY`/help lines).
- **Golden ranges all green after one real fix**: the landed patch
  scanned WORKING-TREE files while the noqa gate read range-head
  content — on any drifted tree the two coordinate systems diverge and
  the gate silently misses (the KNN commit's fully-adjudicated sites
  all survived). Fix: `_materialize_at` mirrors the range head's
  content under `$TMPDIR` and every leg (scanners, noqa gate, osv
  parser) reads that one tree. Golden #1 `03743294b` → **0 findings,
  bandit + semgrep ran**; #2 `4a2dcf7b6` → shellcheck SC2015 (info)
  only; #3 `2dd8818c8` → sqlfluff clean.
- **Two more hardenings the golden runs forced**: (1) house
  config-adjudication mirror — `pyproject` per-file-ignores
  (`tests/**`: S101/S311/S603) maps to bandit B101/B311/B603, else the
  assert-in-tests class floods the roster; (2) `_git`/`_run` decode
  with `errors="replace"` — a non-UTF-8 file in range #3 crashed the
  leg outright.
- 26 tests green (T1–T5 + the config-adjudication + materialization
  pins); ruff/ty/deptry clean; `.openqodex/` root-ignored (nothing in
  it is check-in-able — dead config + self-ignored tool output).

## 11. Amendment (2026-10-09, same day — the operator's review of the gemma-adoption range)

Reviewing `5111964ee..7113b7125` (the gemma adoption + this leg) surfaced
three closure items, all landed same day:

1. **rule.json scoping (the operator's original ask — the rule was
   enforced ONLY in review-patch's teeth assertion).** The scan leg now
   imports `review_selection._families` / `_rule_excluded` — one
   selection authority, never re-derived — and skips files rule.json
   excludes (`- <path> (rule.json excluded)`) or does not include
   (`- <path> (outside rule.json include)`). The **osv leg deliberately
   overrides the lockfile excludes**: whole-lockfile reporting is the
   trial's CVE lesson (§3.5) and the one real finding lived in an
   excluded `package-lock.json`. A missing sys.path bootstrap (the file
   had never imported helpers) was the hidden first failure — the
   swallowed `ModuleNotFoundError` surfaced as `UnboundLocalError`.
   **No new rules file**: the scan leg *shares* the checked-in
   `.opencodereview/rule.json` through those two imported helpers, so
   selection semantics keep exactly one home. Its own rule-shaped tables
   stay in-code beside the code that applies them — `NOQA_MAP` (scanner
   rule → house noqa code, every row citing its adjudication) and
   `HOUSE_PER_FILE_IGNORES` (the pyproject per-file mirror) — because a
   stale row silently dropping a finding is a rot risk that wants its
   citation next to the drop site.
2. **Durable review outputs — `outputs/reviews/` (operator ruling:
   "the roster is the deliverable, not stdout ephemera").** Both legs
   write there now, digest-keyed per range so a rerun overwrites its own
   generation: `review_scan_<base>..<head>_<digest>.md` (roster +
   status lines, always), `review_scan_<digest>.json` (`--json`),
   `review_patch_<scope>_<digest>.md` (selection roster + teeth verdict
   + house checklist; stdout teed via `redirect_stdout`). Machine-local
   and gitignored, same class as the gate reports in `outputs/`.
3. **The freshness ledger moved** from `memory/data/review-freshness.json`
   to `outputs/reviews/review-freshness.json` — rosters and the record of
   "was this diff reviewed" now share one store (operator ruling). The
   procedure §1b, this proposal's §9 table, and the tooling test
   docstring follow. `review_freshness.py --stack N --record --leg … --
   note "<roster pointers>"` closes the loop: the note cites the roster
   files, never a `$TMPDIR` path.
4. **The coordinate fix is pinned and mutation-verified.**
   `TestHeadCoordinateSystem` (3 tests) holds the contract: the scanner
   argv receives the materialized scratch path and never the working-tree
   rel path; a finding reported against the scratch path maps back to the
   repo-relative path; the S608 drop works end-to-end on the content the
   scanner actually read. Mutation (revert the leg to `*files`): the pin
   goes red — restore: 29 green. The fix that turned the trial's
   "fully-adjudicated sites all survive" into a 0-finding golden range is
   therefore not removable without a red test.

### 11.1 Leg quality — delegation vs native scan (measured on 5111964ee..7113b7125)

| | native scan | OCR delegation (host) |
|---|---|---|
| Cost | free, ~30–60 s, no LLM | free by design; host attention is the cost |
| Determinism | identical every run over a ref | judgment varies run to run |
| Scope | changed lines only (except osv: whole lockfile) | whole diff, whole files, any file type |
| Catches | S-class mechanical risks, dep CVEs the line-filter hides | wrong-but-passing design, contract breaks, fixture/docstring lies |
| Misses | every semantic defect — it found none of the day's real issues | exhaustive per-line sweeps; dependency vulns entirely |
| Signal on this range | 3 findings, all one class (uncommitted adjudications — the fixes live in the tree, the scan reads committed HEAD) | 1 substantive catch (the committed `review_scan.py` v1 tree-vs-head coordinate bug) + design verifications (stamp-guard refusal semantics, seed↔basis byte-parity, sidecar-down degradation, test mutation-teeth) |

**The honest read:** on a range the host reviews closely, the scan leg's
output was *bookkeeping* (adjudication hygiene) while the delegation leg
found the only bug. But that inverts on the trial's ranges, where the scan
leg surfaced `source-map-js@1.2.1` (CVSS 8.7) — a finding the changed-line
filter and the human skim both hid — and where "did anyone review this"
was unanswerable without a ledger. Neither leg subsumes the other: the
scan is the exhaustive mechanical floor, the delegation review is the only
leg that can catch wrong-but-green. `review-patch` runs both; the roster
store makes both citable.
