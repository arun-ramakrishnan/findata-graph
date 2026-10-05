# Procedure: OCR delegation review (advisory, no LLM)

**Date:** 2026-09-28 · **Proposal:** `doc/improvements/archive/tooling/ocr_review_pipeline.md` (S2)
**Scope:** `.opencodereview/rule.json`, `ocr delegate preview` / `ocr delegate rule` (bun global install, unpinned), the four repo indexes, and the host-agent review loop. Not the domain triage kit in `review-kit.md` — that one rehouses evidence queues, it does not review diffs.

**Related issue:** [prime-agent#3082](https://github.com/PrimeIntellect-ai/prime-agent/issues/3082) — kernel snapshot/restore silently truncates files: dill-revived `'w'` file handles reopen with `O_TRUNC` at every kernel restart. This zeroed the evidence file `doc/local/engineering/code_review.md` three times during the 2026-09-28 trial. Until it lands upstream, write doc-local notes with `Path(p).write_text(...)` — never a captured `open(..., 'w')` handle.

## What this loop is

OCR contributes two deterministic things — **which files are reviewable** and **a checklist of things to look for**. The host agent contributes judgment. Delegation mode calls no LLM, so it is free and needs no key. Output is advisory: never a `make qa` leg, never fails a build.

## Invocation

OCR is installed globally with bun — **unpinned, latest** (house choice 2026-09-28; the npm global was removed). Nothing installs into the repo: there is still no `package.json` here to hang a devDependency on.

    bun add -g @alibaba-group/open-code-review     # install, or refresh to latest
    ocr --version                                  # open-code-review v1.12.11 (a758d9c) linux/amd64

`ocr` is then the command. `$OCR` stays the indirection the rest of this procedure uses:

    OCR=ocr                                                # default: bun global, unpinned
    OCR="bunx @alibaba-group/open-code-review@1.12.10"     # one-off, version-locked, no install
    OCR="npx -y @alibaba-group/open-code-review"           # fallback where only node exists

| Fact | Detail |
|---|---|
| `$OCR` may be a prefix, not an executable | the `bunx`/`npx` forms are prefixes (`bunx … delegate preview`); `bunx` may print `Saved lockfile` — that lands in bun's global cache, never the repo (no `bun.lock` appeared) |
| **unpinned by default** | `bun update -g` can move the version under you — re-verify flags after any global update (revisit table) |
| package version ≈ binary version | package `1.12.10` → native binary `v1.12.10 (579b931)`; the earlier pin `1.12.9` *also* reported `v1.12.10`, which is why reports carry both numbers |
| bun blocks the package's postinstall | `node scripts/install.js` is a legacy downloader; the Go binary ships in `@alibaba-group/ocr-linux-x64/bin/opencodereview` (56 MB) and works without it — smoke: `ocr delegate preview --commit HEAD --format json` |
| scratch lives under `$TMPDIR`, never a literal `/tmp` | use `${TMPDIR:-/tmp}`. `/tmp` is not durable (a parity proof was already lost to "a scratch script `/tmp` would eventually eat") and holds other runs' residue — a file you did not write is indistinguishable from one you did |
| the model set is a default, not a gate | `z-ai-coding` ships `glm-5.3, glm-5.2, glm-5.1, glm-5-turbo, glm-4.7`; anything else errors `available models:` until registered: `ocr config set providers.z-ai-coding.models '["glm-5.3-flash"]'` then `… model glm-5.3-flash` (which also makes it the unpinned default → cheap flash tier for routine work) |
| a Makefile wrapper is proposal S3 | resolve whichever runner exists, SKIP when neither does — mirror `helpers/misc/markdown_lint.py` |

## When to run

- after a `stg refresh`, before the operator reviews the patch;
- pre-arc landing, over the stack (`--from`/`--to`);
- on demand for a specific patch.

Never as a `make qa` leg, and never in place of reading the diff yourself.

## Step 0 — pick the ref, before touching the tree

**Never default to the moving stgit top** — the top patch changes mid-review, so a review that does not name its ref cannot be reproduced. Ask, then resolve:

| Target | Ref |
|---|---|
| one named patch | `--commit "$(stg id <patch>)"` |
| current top patch | `--commit "$(stg id)"` |
| range / whole stack | `--from <base> --to <target>` |
| uncommitted worktree | no ref — workspace mode (below) |

Refs are SHAs: resolve patch names with `stg id <name>` first, because an agent reads a bare `--from "$X"` literally. `--commit` is single-commit-vs-its-parent, **not** a range — measured on one stack: 5 files vs 12 for `--from/--to`.

## 1. Selection — prove the test spine is visible

    $OCR delegate preview --commit "$(stg id <patch>)" --format json

Read `reviewable_count` / `total_files` / `reviewable_files` (entries are objects with a `path` key, not strings) and verify **both halves**:

1. **at least one `tests/` path.** This repo already paid for an invisible test spine: `csr_lane_fixes` saw 3/10 files and zero test lines — which is how an inert CSR lane plus two green-but-broken defects shipped. No test path ⇒ stop and fix `.opencodereview/rule.json`. (On 1.12.10 the *default* selection excluded tests and only `include` admitted them; on 1.12.11 the extension allow-list selects tests like all code — the check stays because it is the teeth, not because the default still hides tests.)
2. **at least one non-test source path.** On 1.12.10 `include` was a restrictive allow-list — `{"include": ["tests/**/*.py"]}` passed check 1, reported green, and reviewed nothing that mattered. **1.12.11 inverted this (sandbox-probed 2026-09-30): `include` is additive, and selection defaults to an extension allow-list (`.py`/`.pyi`/`.ipynb`/`.json` observed), so every code-shaped file in the diff is reviewable unless `exclude` prunes it.** The mirror-image failure is dead; the live risks are its inverse — stray code files sneaking into the roster, and stale excludes — because `exclude` is now the only pruner and `make review-patch`'s teeth assertion is the guard. **Wider than recorded (observed 2026-10-01 on the 272477d4 review, both legs): the allow-list also admits `.rs`, `.js`, and the extensionless `Makefile` — 22/22 selected on a diff carrying Rust/JS/Makefile traffic, each resolving to a dedicated rule group (`system/**/*.rs`, `system/**/*.{ts,js,…}`). Treat the `.py/.pyi/.ipynb/.json` list above as the 2026-09-30 observation, not the current set; re-derive from `preview` output every run.**

The rule was widened to `tests/**/*.py`, `helpers/**/*.py`, `app.py` on 2026-09-28 (6/14 reviewable on that range: 3 production, 2 test, 1 JSON; 8 exclusions all Markdown) and to `Mojo/src/**/*.mojo` + `Mojo/tests/**/*.mojo` (`Mojo/vendor/**` excluded) on 2026-09-29 — the ef8a17d4 review had to cover the Mojo BFS by hand before that; `ocr delegate preview --commit ef8a17d4` now selects 12/16 with `bfs_csr.mojo` visible. On 2026-09-30 (`ocr_selection_drift`) it gained `doc/procedures/**` + `doc/improvements/**` — under 1.12.11's additive semantics an explicit include is what admits `.md` past the `unsupported_ext` gate. The excludes are boundary declarations, not selection tuning: `findata/**` is the writer-owned vault, and `doc/local/**` is gitignored machine-local evidence — inert for selection (never in a diff) but kept so a force-add or a future careless `doc/**` include can never pull local evidence into review rosters (operator ruling 2026-09-30). Re-derive the ratio every run — never store it. `--exclude a,b` merges with the rule excludes for a one-off; `-B <file>` supplies background (§2).

**`make review-patch` runs this check mechanically** (ocr_review_pipeline S3's deterministic remainder): stgit→ref mapping (`--stack N` ⇒ `HEAD~N..HEAD`), the roster print, and the selection-teeth assertion — every rule-covered family with diff traffic must have a selected file, checked against a **hardcoded** product-family list (`tests/`, `Mojo/src/`, `Mojo/tests/`) so a rule.json regression fires instead of self-masking. Mutation-verified 2026-09-29 (drop `tests/` from the rule ⇒ exit 1 naming the family). Advisory only — never a `make qa` leg. The two manual checks above stay as the judgment layer on top.

## 1b. Review freshness — was this diff already reviewed?

`make review-patch` also prints a freshness line; the ledger behind it is
`memory/data/review-freshness.json` (machine-local, gitignored),
driven by `helpers/misc/review_freshness.py`:

    $OCR="$OCR" ; .venv/bin/python3 helpers/misc/review_freshness.py --stack N
    .venv/bin/python3 helpers/misc/review_freshness.py --stack N --record \
        --leg delegation --leg managed --note "verdict pointer"

- The **fingerprint is the sha256 of the range's diff TEXT**, not the commit
  SHA: stgit refreshes mint new SHAs for identical content, and freshness is
  about content. Same diff after a refresh stays FRESH; any content change
  goes STALE; no row → UNREVIEWED.
- **After a review completes, `--record` it** (legs: `delegation` / `managed`,
  repeatable; `--note` carries the verdict pointer, e.g. the code_review.md
  section). Upsert by fingerprint — re-reviewing the same diff updates the
  row instead of duplicating it.
- Verified 2026-09-29: record ⇒ FRESH; perturbed fingerprint ⇒ STALE ("the
  stack changed since it was reviewed"); empty ledger ⇒ UNREVIEWED.

**Landed ranges cannot be recorded — the tool is stack-scoped (gap,
measured 2026-09-30).** `review_freshness.py --stack N` fingerprints
`HEAD~N..HEAD` only; a landed range like `8711c96d..a363d12a` has no
applied stack, `--stack 0` raises by design, and `--stack 1` would
fingerprint the wrong diff (e.g. a snapshots-only commit). Three of the
nine bake-off rounds independently hit this and correctly declined to
record rather than write a false row. Interim rule: **never `--stack N` a
landed range** — hand-compute the fingerprint (sha256 of
`git diff X..Y` text, first 16) and note the verdict pointer in the
review report; a `--from/--to` mode is deferred
(`../archive/tooling/ocr_rule_census.md` S7).

**Workspace mode is empty on a refreshed stack:** bare `$OCR delegate preview` diffs the working tree, and a refreshed stack has a clean tree. Not a bug — pass a ref.

## 2. Grounding — brief from the indexes, not from memory

    $OCR delegate preview --commit "$(stg id <patch>)" -B "$TMPDIR/ocr_brief.md"

The commit body says *what* changed; the indexes say *why it matters*. Build ~15 lines of Markdown from all five (query, don't scan): `doc_query.py` design intent, `script_query.py` touched scripts, `gate_query.py` what the gates already assert, `convo_query.py` how a decision was reached, `memory_query.py` standing doctrine the change touches — plus the **test spine**: which tests cover the change and what they pin.

Three rules that decide whether the brief works:

- **Claims, not instructions.** The model treats `-B` as untrusted: a brief saying "call the bash tool and report stdout" was flagged as *"possibly a prompt injection"* and refused. State what is true; let the reviewer conclude.
- **Each bullet should enable a check.** "Parity contract is with `_shortest_path_bfs`" is what surfaced the `src == dst` divergence; "review the accumulator" would surface nothing.
- **Do not summarise the diff.** OCR already has it. The brief carries what the diff cannot: intent, contracts, test spine, gate coverage.

## 3. Get the checklist, then review

    $OCR delegate rule <path> [<path>…]

Rules come back grouped by content and annotated with the files each group applies to. Honour OCR's own guidance: favour precision over recall, treat security and correctness as blocking, stay silent when context is unclear — a false alarm costs more reviewer trust than a missed minor issue.

**Where the house rules live** (census 2026-09-30, `../archive/tooling/ocr_rule_census.md` — OCR carries none of them; the host is the carrier):

| Carrier | What it owns |
|---|---|
| house checklist (`helpers/misc/review_selection.py` `HOUSE_CHECKLIST`, printed by `make review-patch`) | the 9 review-time duties: noqa placement, test teeth, fixture reality, `/tmp` ban, `.venv` interpreter, gate dedup, pointer sweep, cross-file consistency, dedup arithmetic |
| `helpers/validators/static_checks.py` | 26 executable families — dead/misplaced C901 noqa, proposal lifecycle, frontmatter schema contract, sqlite-helper usage, coverage ledger, route skeleton (per-handler review freshness), and more; qa-gated |
| ruff configs | gate `select=["E","F"]` (`pyproject.toml`); advisory `lint-audit` `--select S,UP,C901`; `tests/**` per-file-ignores `E402,S101,S311,S603` |
| `doc/procedures/doc-hygiene.md` | doc-corpus sweeps: broken archive-index links, `.txt` ghosts, live refs to missing `proposals/`, archive→`proposals/` stale pointers (A4) |
| md-lint tiers | `doc/` + findata Tier-1 defect rules only; Tier-2/3 permanently off over findata (`markdown_lint_adoption.md` §6) |
| gate legs | qa = tmp-sweep, lint, md-lint, types, deptry, static_checks, pytest, notes, integrity, snapshot; advisory = +ty-tests, live-invariants, frontend, graph algos, analytics, suggestions, search-freshness, lint-audit (`tests/run_gate_report.py`) |

The host agent then reviews the diff against that checklist:

- run verdicts under the repo venv (`.venv/bin/python3`, never bare `python3`);
- verify every finding against source, and **cross-check the gate** — if `make qa` (ruff, md-lint, ty, pytest) already flags it, it is not a review finding;
- **a test's docstring is a claim, not evidence.** Check the asserted branch is *reachable*. Cheap mutation check: neuter the branch, run the test, confirm it goes red, then restore — and prefer a self-validating precondition (`assert len(reference) >= 4 * limit`) so a shrunken corpus fails loudly. Paid case: four tests claimed "the 4× prune fires repeatedly; limits 1/2/3 all prune", but the fixture built 4 paths = 6 pairs so only `limit=1` crossed `prune_at`, and disabling the branch left all four green.
- **fixture reality — verify the fixture exercises what its comment names.** For a diff that adds tests or fixtures, check cross-file: trigger phrasings actually match the PATTERNS they claim to exercise ("customers include" is not a customer_of trigger; the paren form is), sentinels match the real regex (an invented `<!-- auto:chatter -->` silently falls back to whole-body scan), and referenced constants are actually consumed (a fixture `INDEX_NOISE` dead next to the module's own frozenset tests nothing). Paid case: the c83a11d9 managed leg found four such fixtures — each rendered green-and-vacuous while its comment claimed lane coverage. The generic OCR checklist cannot carry this (project rules don't surface, S4a); it is a host duty.

## 4. Triage — severity, then the operator

**The gate owns deterministic checks; this loop owns semantics.** Ruff, `ty`, md-lint and pytest are already `make qa` legs — faster, reproducible, and they actually fail the build. Do not route the toolchain through the reviewer to "double-check" it; that only re-reports the gate.

**OCR cannot run them, by design.** Asked to call a bash tool (2026-09-28, `glm-5.3-flash`), the model declined and named its own inventory: `task_done`, `code_comment`, `code_search`, `file_read`, `file_read_diff`, `file_find` (+ `approve_all_comments`) — read-only context plus output tools, no shell. `--tools` *replaces* that set rather than extending it, and its entries accept only `name` (other keys silently ignored), so a shell tool is not authorable. `ocr llm test` printing "Tool-call round trip verified" validates the *loop*, not shell access.

What the checks cannot catch is the defect class this loop exists for: every `csr_lane_fixes` finding was semantic while all four checks stayed green — a docstring promising a "top-k heap" the code did not use, a warning contradicting its default, a fast path that never fired, a parity claim that did not hold.

| Severity | Action |
|---|---|
| Critical / High | always report; correctness and security are blocking |
| Medium | report with surrounding context, or drop it — say which |
| Low | discard unless it carries real value |

Report findings; do not act on them unilaterally. The operator accepts, rejects or defers.

## 4b. Validity — prove the finding set before it becomes work

A finding set is a hypothesis until it survives these checks. Two legs make it mandatory: on 2026-09-28 (`0b63ce4507`) the two legs produced 23 raw reports — 3 duplicates, 3 false positives → **17 distinct real**, 15 actionable at HEAD.

1. **Dedup across legs.** Same defect twice = one finding, cited to both. Report the arithmetic, not just a total: `raw − reported-by-both − false positives = distinct real`.
2. **Execute every claim you did not verify yourself.** One `python3 -c "import helpers.validators.static_checks"` dismissed all three managed-leg CRITICALs — Python 3.14 accepts PEP 758's `except A, B:`. A claim never executed is a hypothesis; report it as one.
3. **Re-verify at HEAD, not at the reviewed ref.** The tree moves. Walk the finding list with a presence script and drop what later commits already fixed (2026-09-28: the inert `findata-graph-desktop` allowlist, gone via `ff59665c`). Keep the script's output in the evidence file — "15/17 present" is the number the fix arc works from.
4. **Sweep the vault before calling anything a defect.** `doc_query.py`, `convo_query.py`, `memory_query.py` (standing doctrine — "known class" often lives in the harness pools), an `rg` over `pending.md` and the `status: deferred|rejected` markers, `ripwire . --grep=<symbol>` — then classify each finding: **regression-of-record** (violates what a completed entry landed), **documented scope** (the proposal drew the boundary on purpose), **known class / deferred**, or **novel**. The sweep's job is to find the ones that are none of your business before the operator has to.
5. **File the proposal before fixing** — house rule for multi-slice work; the index line in `proposals/README.md` rides the same patch, and anything touching rosters/crosswalks/extractor rules carries the eval-gate bullet.
6. **Persist the triage**, not just the verdict: accounting table, presence-script output, sweep queries with their hits, false-positive list — in `doc/local/engineering/code_review.md` (gitignored evidence home). The false positives matter; they stop the next session re-litigating them.
7. **Audit commit/gate claims against the corpus** (bake-off class 7, caught by only 2 of 9 rounds): a commit message's "make qa 11/11" is a hypothesis — re-derive via `gate_query recent --gate qa`, and `git merge-base --is-ancestor <sha> HEAD` any SHA the claim leans on (a gate recorded at a rewritten, non-ancestor SHA backs nothing). The companion proposal record often states it accurately; diff the two.
8. **Sweep for pointers the diff broke.** Any `git mv`/deletion in range: grep the old path repo-wide (docs *and* code — a docstring in a touched `.py` counts). doc-hygiene's sweeps own the doc corpus (`proposals/` refs, A4 archive pointers); the review-time duty is diff-scoped because fixture strings in tests make a mechanical code sweep false-positive-prone — read the citing line before filing. An armed `pending.md` trigger pointing nowhere is blocking.

## 5. Apply and verify

Apply **only operator-accepted** fixes. Then re-run step 1 and re-review the same ref: fixing a finding does not close it, the re-review does.

**Mutation-check the TEST, not just the fix** (learned the expensive way, c83a11d9 round 2026-09-29): a new regression test written alongside a fix can pass identically on the pre-fix code — the c83a round's two HIGH-severity catches were exactly such vacuous tests (the S1 test warmed the cache and never reached the fixed route; the F2 test resolved nothing anywhere, so both paths emitted the same row). Before calling a fix closed: strip/neuter the fix, run the new test, confirm it goes RED, restore. A test that cannot fail on the bug it pins is a docstring.

### Managed mode — results file, sessions, failure shapes

Split of labour: **OCR's LLM writes the findings, the host reads them afterwards.** It has no shell and cannot read its own output — never ask it to.

| Gotcha | Rule |
|---|---|
| `-o`, not `>` | `--format json` on stdout interleaves `[ocr] …` progress with the JSON, so `> out.json` yields unparseable output. `delegate preview` has **no** `-o` (`unknown shorthand flag: 'o'`) — redirect it and strip `/^\[ocr\]/` before `json.loads`; do not skip the strip and read the tail: summary fields sit at the top of the payload, `excluded_files` + `exclude_reason` at the bottom |
| `status: partial` | HTTP 429 left files unreviewed — read `status`, `files_reviewed`, `total_files` and say so rather than implying full coverage |
| `status: failed` + `comments: 0` + `total_tokens: 0` | **no review happened at all** (a 401 produces exactly this, in ~2s, in a well-formed file) — not a clean bill of health; fix the cause first |
| `LLM grouping failed … falling back to per-file dispatch`, `Plan phase failed … (continuing without plan)` | both benign — degraded/planless, but reviewed. Only the 401 is fatal |
| verification | managed findings are directionally right and structurally incomplete: the near-dup pruning test was correctly flagged as under-covering, but only host mutation-testing showed the assertion could not fail at all |

Sessions exist only in managed mode — the one capability delegation lacks, and what makes apply-and-verify mechanical:

    $OCR session list
    $OCR session comments <id> --json --severity high   # filterable
    $OCR session compare <id> <id> --json               # before/after

Loop: review → accept fixes → re-review the same ref → `compare` the two sessions. In delegation mode `session list` returns nothing; verify by re-running steps 1–3.

## 6. Output

Advisory. Record findings in the review report, not in `doc/improvements/`, unless the operator asks for a proposal. Gate coupling stays off.

**Every review report carries its own price (operator rule 2026-09-30).**
Capture what the harness exposes, and say which parts are estimates:

| Field | Source |
|---|---|
| phase wall times | OCR `preview`/`rule` are deterministic sub-second; the host review dominates — timestamp start/end |
| requests, input split cache-read vs fresh, output tokens | zcode hosts: `~/.zcode/cli/db/db.sqlite` `model_usage` (open `mode=ro`; GLM feed: `input_tokens` INCLUDES cache reads — fresh = input − cache_read; no cache-write bucket). **Filter by `session_id`** — the table has no `model` column and parallel sessions (bake-off rounds!) share the DB, so a time-window-only query silently sums them (measured 2026-09-30: two overlapping rounds contributed 25 + 53 requests to one naive window) |
| notional cost | rates per 1M (GLM-5.3: cached $0.26 / uncached $1.40 / output $4.40) × volumes |
| actually billed | plan-side reality — the coding-plan subscription meters $0 marginal; say so instead of implying API spend |

Measured example (2026-09-30 glm-5.3 delegation round): 27 requests
(review turns through snapshot time — the session then continued, reaching
53 requests / 6.48 M input by 19:37 IST as the patch and proposal were
filed from the same session), 1,430,594 input (1,370,112 cache-read =
95.8 %, 60,482 fresh), 26,694 output → $0.56 notional, $0 billed, ~9 min
wall; the OCR delegation leg itself $0 / 0 tokens / 0.3 s. Delegation reviews are free by design — the
cost line is the host's, and it is what keeps delegation-vs-managed
comparisons honest (see the cache-fairness note in
`doc/local/engineering/code_review.md`).

## Known limits

| Limit | Consequence |
|---|---|
| Markdown gets only the generic `default` group | 1.12.11 ships a system `default` rule group (generic Correctness checklist) that resolves `.md`/`.mojo` at the rule layer; selection still drops `.md` as `unsupported_ext` unless an explicit include admits it — the rule has carried `doc/procedures/**` + `doc/improvements/**` since 2026-09-30. The default checklist is not doc-aware: the host still reads doc-heavy diffs itself |
| project `rules` content does not reach OCR | re-verified on 1.12.11 (2026-09-30 probes): a `rules` array in `rule.json` and a `.opencodereview/rules/` dir file surface nowhere; the `--rule <file>` flag on `delegate rule`/`review` parses a single `ProjectRule` object whose schema is undocumented — guessed field names are silently ignored (probe: parsed clean, resolution unchanged). **The host agent is the only carrier of the `AGENTS.md` conventions**; `make review-patch` prints the house checklist alongside OCR's |
| selection is only as good as the rules | 1.12.11: selection defaults to an extension allow-list; `include` adds paths (it is what admits `.md`), `exclude` prunes. Verify the test spine every run; `make review-patch` asserts it mechanically |
| coverage is diff-scoped | whole-file problems outside the diff are out of scope; `ocr scan` exists for that and is a separate decision |

## Keeping this current

**This procedure is the canonical home for the rules** — model/tooling facts, the triage contract, the selection caveats. Evidence and transcripts belong in `doc/local/engineering/code_review.md` (local, gitignored); decisions belong in `doc/improvements/archive/tooling/ocr_review_pipeline.md`. Do not restate evidence here.

**Never cite scratch as evidence.** `csr_lane_remediation.md` §2 titles its evidence table "from `/tmp/ocr_delegation.md`" — a throwaway path that will not resolve for the next reader, and that table backs four remediation slices. Either copy the transcript into `code_review.md` and cite that, or inline the findings and cite the commit.

**A managed pass is a second opinion only if it runs a different model from yours.** Delegation is not an independent check on the host — it *is* the host. Record requested-vs-served model *and* the host model; if they match, the pass costs tokens and buys nothing. The 2026-09-28 pairing that earned its keep: GLM `glm-5.3` spotted a defect, Space Bunny as host proved it by mutation.

**Both schemas (`rules` entries, `--tools` entries) accept unknown keys silently.** When you add either, verify the key is actually read — grep the consumer's output for a unique marker string — before believing it works.

Last verified **2026-09-30** against the bun global install — package `1.12.11`, native binary `v1.12.11 (a758d9c)`, `ocr` → `~/.bun/bin/ocr`. Re-verified live: `delegate preview --from/--to --format json` and `delegate rule` flags behave unchanged; selection semantics re-probed in a sandbox repo (include now additive — §1); the project-rules channel re-probed dead (Known limits); `rule.json` selecting 10/19 on the lint-audit range with the test spine visible. Carried over from the 2026-09-28 stamp (package `1.12.10`, binary `v1.12.10 (579b931)`, 5/10 selected) but NOT re-checked in this pass: the managed-mode tool inventory (`code_comment`/`code_search`/`file_read`/`file_read_diff`/`file_find`/`task_done`/`approve_all_comments`) — re-probe before relying on it.

Revisit when any of these change — each invalidates a specific claim above:

| Trigger | Claim it invalidates |
|---|---|
| OCR version bump (any source — `bun update -g`, registry latest, a pinned `bunx`) | invocation forms, flags, both schemas, the last-verified stamp |
| `rule.json` edit | selection ratios, test-spine check |
| a new tool in the inventory | "OCR cannot run commands" |
| a new rule group (e.g. Markdown) | the doc-coverage limit |
| managed mode lands (S4) | the whole delegation-is-enough premise |
| the `AGENTS.md` OCR rule changes | "host agent is sole convention carrier" |
| the provider's throttle changes, or a throttle flag ships upstream | the `--concurrency 2` rule in §5b |

## Runbook — the loop as commands

The sections above are the rules; this is the same loop in execution order, with nothing to look up. Anything not said here is in the numbered step it names. `$OCR` is `ocr` (the bun global install) unless you exported one of the forms in *Invocation*.

### 0. Trigger

    OCR delegation review of <SHA_X>..<SHA_Y>.
    OCR review of patch <name> on the current stack.

### 1. Resolve the ref

    X=$(stg id <older-patch>); Y=$(stg id <newer-patch>)   # or Y=$(git rev-parse <sha>)

### 2. Selection, and its precondition

    $OCR delegate preview --from "$X" --to "$Y" --format json

Both halves of the §1 precondition — a `tests/` path **and** a non-test source path — before reviewing anything. `--exclude a,b` merges with rule excludes.

### 3. Checklist

    $OCR delegate rule <each path from reviewable_files>

### 4. Build the brief (~15 lines, no generator)

The four indexes are scripts under `helpers/misc/` and need an explicit interpreter — bare `python3` or bare `gate_query` is not on `PATH`:

    .venv/bin/python3 helpers/misc/doc_query.py    "<topic>" --limit 5
    .venv/bin/python3 helpers/misc/script_query.py "<task>"  --kind script
    .venv/bin/python3 helpers/misc/convo_query.py  "<question>" --limit 5
    .venv/bin/python3 helpers/misc/gate_query.py   latest

    # Context
    <one paragraph: what this change does and why now>

    Background facts relevant to correctness:
    - <invariant the code must hold, and where it is specified>
    - <the contract it must match, and the function that defines it>
    - <when a path must fall through untouched>
    - <documented bound that must not be exceeded>

    - Test spine: <test files> are part of this change and pin the above.

### 5a. Delegation review (default, free)

Hand the host agent the brief and the resolved refs; it runs selection + checklist itself, reviews the diff, and verifies each finding against source. No key, no cost.

### 5b. Managed review (costs tokens; second opinion)

**Ask the operator for the key's variable name (`<KEY_NAME>`, required) and optionally the file it lives in (`<KEY_FILE>`)** — do not assume or hardcode either. If no file is given, the variable is expected to be already present in the environment. A duplicated `=` (`<KEY_NAME>=="<key>"`) makes every `NAME=VALUE` parser assign a value beginning with `=`, and z.ai answers `401 / code 1000` on every request — hit on 2026-09-28, costing a full 15-minute pass. Keep the guard line (no-op when well-formed).

**Then map the key onto the PROVIDER's expected env name** — the operator's name is never enough by itself (hit 2026-09-29: exporting the operator key under the house name failed with *"provider z-ai-coding has no api_key or api_key_cmd configured and no environment variable fallback found"*; every built-in provider has its own fixed fallback name, e.g. `z-ai-coding` → `Z_AI_CODING_API_KEY`). The table + bridge live in `helpers/misc/ocr_key_map.py`:

    .venv/bin/python3 helpers/misc/ocr_key_map.py list          # provider -> env var (no secrets)
    .venv/bin/python3 helpers/misc/ocr_key_map.py run \
        --provider z-ai-coding --key-name "$KEY_NAME" --key-file "$KEY_FILE" \
        -- $OCR review --from "$X" --to "$Y" --model glm-5.3 \
          --concurrency 2 -B "$TMPDIR/ocr_brief.md" --format json -o review.json

`run` resolves the operator key (flags or `OCR_KEY_NAME`/`OCR_KEY_FILE`), execs the command with the provider's env var set, and the key crosses only via the child's environment — never argv, never config.json, never output. Verified end-to-end 2026-09-29 (`ocr llm test` through the bridge).

**No plaintext at rest — three routes, in order of preference:**

1. **Per-invocation bridge** (`run` above) — nothing persisted anywhere; the operator supplies name+file every run.
2. **`api_key_cmd`** (persistent, still no key at rest): `ocr config set providers.z-ai-coding.api_key_cmd "grep -m1 '^<KEY_NAME>=' <KEY_FILE> | cut -d= -f2-"` — config.json (0600) stores only the *command*; OCR runs it at invocation and hard-fails on non-zero/empty output (no silent fallback). The command string does name the key variable and file — acceptable: it is not the secret.
3. **Static `api_key`** — plain text in config.json. **Never use it.** (`ocr config provider` TUI writes this form.)

Precedence inside OCR: `api_key` > `api_key_cmd` > env-var fallback (the table). **Route 2 is CONFIGURED (2026-09-29):** `providers.z-ai-coding.api_key_cmd` greps the operator's key name from the key file at invocation (command recorded in config, secret never is — `ocr llm test` passes with zero key env vars exported). Managed launches therefore need **no key step at all**; route 1 (the `ocr_key_map.py run` bridge) remains for other providers and for runs where the operator wants per-invocation key supply. Note: provider probes showed `edenai`/`litellm`/`ollama-cloud` have no env fallback — for those, `api_key_cmd` is the only no-plaintext route.

**Prompt ceiling vs the model's context window.** OCR already chunks: a review runs as subtasks of *one file or a bundle of related files*, each subtask's prompt capped by `max_tokens` (embedded default **200,000** for `ocr review`). A model with a smaller window does not need diff-chunking built — it needs the ceiling lowered: `--max-tokens N` per run (≈60–70% of the model's context, leaving room for the tool loop and the fixed 16,384 output cap), or `ocr config set max_tokens N` to persist. If a single reviewable file exceeds even the lowered ceiling, no config saves that subtask — that diff is a delegation-mode job (the host agent reads big files in slices). Know the window before launching; the provider table names endpoints, not model context sizes.

Key hygiene: the stored value may be quoted (`tr -d '"'` strips that too); the 2026-09-28 key was 49 characters and every wrong variant measured 50 or 52. Probe before spending a run — a 401 costs a full pass and yields no findings:

    curl -s -o /dev/null -w '%{http_code}\n' -X POST \
      https://api.z.ai/api/coding/paas/v4/chat/completions \
      -H "Authorization: Bearer $ZKEY" -H 'Content-Type: application/json' \
      -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
    # 200 = key good; 401 = fix the key, the OCR invocation is fine.

To persist it instead: `ocr config set providers.z-ai-coding.api_key "$ZKEY"`.

- **Pass `--concurrency 2`.** Default is 8 concurrent subtasks, which trips z.ai's `429 code 1302` faster than backoff recovers: three runs of the same 31-file commit went **8 → 45 retries, 0 recovered, 31/31 failed**; **8 → 4×429, aborted**; **2 → zero 429s end to end** (`status: complete`). No persisted knob exists (`ocr config` supports only `set`), so the flag is per-invocation.
- **`-o`, not `>`** — see §5.
- **Check `status`** — see §5; `partial` and `failed`/`comments: 0` are not coverage.
- **Unpinned resolves to the provider's configured model** — pin when you want a specific one, and only spend on a pass whose model differs from the host's.

### 6. Triage

Per §4 and §4b: verify against source, mutate anything about a test, drop what `make qa` already flags, rank severity, say what you dropped. Advisory — the operator accepts.

### 6b. Validity sweep (when more than one leg ran)

The §4b checks as commands. Do these **before** ranking severity — a finding that dies here never reaches the report:

    # 1. dedup + arithmetic: raw − both − false = distinct real
    # 2. execute claims you did not verify:
    .venv/bin/python3 -c "import helpers.validators.static_checks"

    # 3. re-verify at HEAD, and record the SHA:
    git rev-parse --short HEAD

    # 4. sweep the vault — classification is the output, not the hit list:
    Q=".venv/bin/python3 helpers/misc"
    $Q/doc_query.py "<finding topic>" --limit 4
    $Q/convo_query.py "<how did we decide X>" --limit 4
    rg -n -i "status: (rejected|deferred)" doc/improvements/
    ripwire . --grep="<symbol>" --grep-in=any --legend=compact

    # 5. proposal + index line in the same patch
    # 6. evidence → doc/local/engineering/code_review.md

### 7. Apply what was accepted

### 8. Verify

Re-run §1 and re-review the same ref; a fix is not closed until re-reviewed. Managed mode makes it mechanical with `session list` / `session comments` / `session compare` (§5); delegation has no session, so re-run selection + checklist.
