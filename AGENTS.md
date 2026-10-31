# Instructions for LLM/agent sessions

Query, don't scan — seven indexes + one federated front door cover this
repo; grep and wholesale reads are the fallback. TUI lanes (`make
search-tui`, 1-8) are a usability shell over the CLIs below, never the
only way in.

## Search surfaces

| intent | tool | index |
|---|---|---|
| anything, once (federated grouped) | `helpers/misc/master_query.py` | all seven |
| design/decision/history in `doc/` | `helpers/misc/doc_query.py` | `doc_search.db` |
| scripts/tests/make/Mojo | `helpers/misc/script_query.py` | `script_search.db` |
| past agent sessions | `helpers/misc/convo_query.py` | `convo_search.duckdb` + `_fts.db` |
| git history (landed WHY + diffs, `main` only) | `helpers/misc/git_query.py` | `git_search.db` |
| vault notes (`findata/**`) | `helpers/misc/note_query.py` | `research.db` (`note_search`) |
| harness memory (zcode/prime/opencode) | `helpers/misc/memory_query.py` | `memory_search.db` |
| gate-run reports | `helpers/misc/gate_query.py` | `outputs/*_report.md` |
| code structure / callers | `ripwire` | offline binary |
| why a process/file is busy | `witr` | live |

**Harness conversations are not in `doc/`.** Docs hold conclusions;
`convo_search` holds the path to them — dead ends, discarded options,
why a live default is what it is. Commits hold what LANDED: `git_search`
answers when/why/what-moved (`--since`, `--path`); main-checkout-only
and `--no-textconv` extraction are doctrine.

```bash
Q=".venv/bin/python3 helpers/misc"
$Q/master_query.py "embed cache"       # ALL legs, grouped; --flat rank-RRF; --age-guard N skips stale legs
$Q/doc_query.py "why did we not adopt langgraph" --limit 5   # doc/ incl doc/local
$Q/note_query.py "promoter holding" --limit 5                # findata/** vault
$Q/script_query.py "<task>" --kind script|test|make|mojo     # before grepping
$Q/convo_query.py "hypergraph scaling ceiling" --limit 5     # --expand POINTER; --harness name; --kinds user,assistant,reasoning
$Q/git_query.py "bili namespace routing" --since 30d
$Q/memory_query.py "stg refresh scope" --kind zcode|prime|opencode
```

- doc/script/note hits are text locators (`path:line`) — Read only the
  linked section, never the file.
- convo hits are pointers (`file.parquet:<row_no>`): `--expand POINTER`
  prints the body; tool traffic (~56% of corpus) is already demoted by a
  rank prior. Sources are the LIVE harness stores — drift mid-session is
  normal; `make convo-fresh` (check exits 1, `APPLY=1` refreshes).
- Past-trial recovery: `convo_query` the topic, then sweep
  `bench_data/<topic>/` (durable, gitignored — banks/builders/scorers,
  e.g. `embgemma2/banks/`) and `$TMPDIR/opencode/`; `ls` the subdirs —
  shallow `find | head` misses them.
- `convo_search.duckdb` is single-writer: a running rebuild holds the
  lock (`witr -f memory/convo_search.duckdb`); never a second writer.
- Stale index? The CLI warns and still answers. Rebuild:
  `helpers/maintenance/rebuild_{doc,script}_search.py` (warm ≈ instant)
  or `make search-fresh`.
- Never read `doc/improvements/completed.md` (166 KB) or 40 KB archived
  proposals wholesale — query first.

## ripwire / witr / gate_query

```bash
ripwire . --for="<task in words>"                    # orient: ranked signatures
ripwire . --callers=SYM | ripwire . --impact=SYM     # callers / blast radius
ripwire . --grep=STR --grep-in=any --legend=compact  # literals — ALWAYS both flags
gate_query latest                                    # newest run digest (default gate: qa)
gate_query failures [--full] [--recent 15]           # newest failure; landscape
gate_query recent --gate qa --last 10 --tests        # PASS/FAIL + failed ids
gate_query timing --leg <leg> --last 10              # where the time goes
```

- `witr` (`/usr/local/bin/witr`, `man witr`) — WHY a process/resource
  is busy, before `ps aux | grep`. `prime-agent → bash → timeout →
  python3` ancestry = THIS session (safe to kill the leaf); `witr -f
  <db>` names a DuckDB holder.
- gate_query indexes `outputs/*_report.md` (main +
  `outputs/wt/<name>/`) incrementally — query BEFORE tailing reports.

## Thesis first — search before you claim

**Never state a claim about what this repo has, lacks, or already does
until a search backs it.** Session context is a *cache*, not a source;
a confident answer from a thin session is a guess wearing a suit. Run
the index that owns the question (table above), then cite the hit
(`path:line`, hit count, command + output) so it can be re-verified
without re-deriving it.

- **Absence claims need a search too** — absence of memory is not
  absence of code.
- **Never name a path you haven't stat'd** — `ls` it or query for it;
  fabricated paths read as authoritative.
- **Stale ≠ false, but stale is not a claim either** — re-read live tree
  state (`git status`, `stg status`, `stg series`) at the moment of
  speaking; a session snapshot is not evidence about now.

## Rules

- Query CLIs = INTENT; STRUCTURE = `ripwire`, `rg` fallback; Mojo →
  **Mojo docs MCP, never web fetchers**.
- **Thesis first** (above): search, then claim, then cite the hit.
- **All `memory/research.db` writes go through
  `helpers.core.db.connect()`** — never a raw `sqlite3.connect()`.
  Raw connections leave `PRAGMA foreign_keys` OFF: an unregistered
  `agent_id` (e.g. `''`) inserts silently and surfaces hours later as
  an integrity-gate `foreign_key_check` failure. `connect()` defaults
  `enable_fk=True` (`helpers/core/db.py:136`) so the bad INSERT raises
  `IntegrityError` immediately; only doc-index/lint paths may pass
  `enable_fk=False`. Rows carrying `agent_id` need the agent
  registered in `provenance_agents` first (FK → `ON DELETE SET NULL`:
  `NULL` = unknown, never `''`).
- Keep the session todo list current — a stale list misleads.
- Blocking: `make qa` (ruff, md-lint, types, pytest, integrity,
  snapshot…); non-blocking: `make advisory`. After editing `doc/**`,
  `Makefile`, or helper docstrings: `make search-fresh` (advisory). A
  worktree behind the shared index refuses APPLY on purpose
  (`worktree_fresh_trample_guard`) — sync, or re-run with
  `--allow-stale` and a reason; do not retry. Preview with `--sandbox`.
- Markdown lint-only: NEVER `markdownlint --fix` on `findata/**`
  (writer-owned vault); `doc/` is remediable.
- Gate arcs follow `doc/procedures/gates.md`: full gates ONCE per arc
  (qa + integration + perf + advisory) on the user's go;
  `search-fresh`/`convo-fresh` APPLY once at arc start; fixes
  validated by TARGETED tests only — never `make pytest`/`make test`;
  perf timing failures parked, no reruns. The user stages and commits —
  leave the tree dirty.
- **OCR review is advisory** — read `doc/procedures/ocr_review.md`
  first. `ocr delegate preview|rule` give selection + checklist; the
  HOST agent is the reviewer and sole carrier of these conventions
  (OCR's own `rules` key is unverified — do not add it). Never a gate.
  Step 0: ask which ref, never the moving stgit top. Prove a `tests/`
  path is selected before trusting a review; apply only accepted
  findings.
- **No patch/commit lifecycle ops.** Never
  `stg new`/`push`/`pop`/`delete`/`squash`, never
  `git commit`/`amend`/`rebase` — the operator owns patch structure.
  Edit files; `stg refresh` into the CURRENT top patch only (scoped
  pathspec when the tree holds unrelated dirt). EXCEPTION — gate
  close-out per `doc/procedures/gates.md`: on the arc's explicit go the
  agent may `stg new` the `gate_fixes` collector and, if the operator
  opts in, distribute fixes into owning patches (`stg refresh -p` fast
  path, `goto -k`/`spill` fallback) and message via `stg edit -f`.
  For patch messages read `doc/procedures/commit-messages.md` first.
- **Session worktrees (worktrunk `wt`)** — full playbook:
  `doc/local/engineering/wt_workflow.md`. Session arcs run in a wt
  worktree (`wt switch -c <arc-slug>` from main; hooks wire symlinks,
  `.envrc`, memory scope). Inside a worktree: read-only index work
  (`--check`/`--sandbox`) — **never APPLY any `*-fresh` target**
  (search-fresh/convo-fresh/analytics-fresh/…; the Makefile refuses) —
  APPLY is a main-tree operation; no graph-DB rebuilds; no Rust `.so`
  installs into the (shared) venv; **never complete/archive a proposal**
  (`completed.md` numbers are main-owned; amending an already-archived
  proposal's record is fine). Landing: operator runs `wt merge main`
  (commit crafted on the branch per the commit-message template, merge
  with `--no-squash`), then `stg repair` on main — the landing rides the
  stack as the TOP patch by design; first post-merge APPLY from main
  needs `--allow-stale` once (stamps lag the ff).
- Use `.venv/bin/python3` explicitly in non-interactive shells.
- **Python 3.14 / PEP 758:** `except OSError, TimeoutError:` (no
  parens, no `as`) is VALID Python 3.14 syntax — looks like Py2
  remnants but is not; `ast.parse` accepts it. With
  `target-version = "py314"` (pyproject `[tool.ruff]`), `ruff format`
  rewrites `except (A, B):` → `except A, B:` repo-wide. Never "fix"
  those lines back to parens and never flag them as syntax errors —
  that diff is expected, not a regression.
- **Backgrounding past a tool call (all harnesses):** tool timeouts kill
  the process GROUP — plain `&`/nohup children die with it. Use
  `setsid bash -c '<abs cmd> > <abs log> 2>&1; echo $? > <cmd>.exit' </dev/null >/dev/null 2>&1 &`
  — absolute paths INSIDE the subshell (cwd binds at parse time), then
  poll the `.exit` marker with short commands; never sleep-poll, never
  assume death from a missing log before checking the marker.
- **Scratch files: `$TMPDIR` ONLY** (here `/mnt/data/tmp`). Never
  `/tmp/opencode` or any other `/tmp` path — the operator rejects
  those. Expand it (`"${TMPDIR:-/tmp}/x"`) when it may be unset; logs,
  `.exit` markers and driver scripts go there too.
