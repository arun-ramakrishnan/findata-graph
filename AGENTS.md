# Instructions for LLM/agent sessions

Query, don't scan — six indexes cover this repo, and one federated
front door fans out over all of them; grep and wholesale reads are the
fallback. TUI lanes are a usability shell over the CLIs below, never
the only way in.

## Search surfaces — pick the index by intent

| intent | tool | index |
|---|---|---|
| **anything, once** (federated grouped results) | `helpers/misc/master_query.py` | all six |
| design/decision/history in `doc/` | `helpers/misc/doc_query.py` | `doc_search.db` |
| scripts/tests/make/Mojo | `helpers/misc/script_query.py` | `script_search.db` |
| **past agent sessions** (opencode+prime-rlm+zcode) | `helpers/misc/convo_query.py` | `convo_search.duckdb` + `_fts.db` |
| vault notes (`findata/**`) | `helpers/misc/note_query.py` | `research.db` (`note_search`) |
| **harness memory** (zcode pool + prime global + opencode) | `helpers/misc/memory_query.py` | `memory_search.db` |
| gate-run reports | `helpers/misc/gate_query.py` | `outputs/*_report.md` |
| code structure / callers | `ripwire` | offline binary |
| why a process or file is busy | `witr` | live |
| all of it, interactively | `make search-tui` (lanes 1-8) | — |

One rule the table hides: **harness conversations are not in `doc/`.**
Docs hold conclusions; `convo_search` holds the path to them — dead
ends, discarded options, why a live default is what it is.

## The six query CLIs

```bash
Q=".venv/bin/python3 helpers/misc"
$Q/master_query.py "embed cache"                           # ALL legs, grouped; --flat rank-RRF; --age-guard N skips stale-index legs
$Q/doc_query.py "why did we not adopt langgraph" --limit 5   # doc/ incl doc/local
$Q/note_query.py "promoter holding" --limit 5              # findata/** vault
$Q/script_query.py "<task>" --kind script|test|make|mojo   # before grepping
$Q/convo_query.py "hypergraph scaling ceiling" --limit 5   # past sessions
$Q/memory_query.py "stg refresh scope" --kind zcode|prime|opencode  # harness memory pools
```

- **doc/script/note hits are text locators** (`path:line`) — Read only
  the linked section, never the file.
- **convo hits are pointers** (`file.parquet:<row_no>`): `--expand
  POINTER` prints the body, `--harness <name>` one lane, `--kinds
  user,assistant,reasoning` drops tool traffic (~56% of the corpus,
  already demoted by a rank prior).
- convo answers *how did we decide this / what did I already try /
  when did I last look at X*. Sources are the LIVE harness stores, so
  drift is normal mid-session: `make convo-fresh` (check exits 1,
  `APPLY=1` refreshes, seconds-class).
- `convo_search.duckdb` is DuckDB: single-writer. A running rebuild
  holds the lock — `witr -f memory/convo_search.duckdb`; never start a
  second writer.
- Stale index? The CLI warns and still answers. Rebuild:
  `helpers/maintenance/rebuild_{doc,script}_search.py` (warm ≈
  instant), `make search-fresh`, or `make convo-fresh`.
- Never read `doc/improvements/completed.md` (166 KB) or 40 KB
  archived proposals wholesale — query first.

## ripwire — structural code discovery (map-before-read)

Symbols, callers, blast radius. Locate, then Read only what it names.

```bash
ripwire . --for="<task in words>"        # orient: ranked signatures
ripwire . --callers=SYM | --impact=SYM   # callers / blast radius
ripwire . --grep=STR --grep-in=any --legend=compact  # literals — ALWAYS both flags
```

## witr — process discovery (`/usr/local/bin/witr`)

WHY a process/resource is busy — before `ps aux | grep`. `man witr` for
full usage. `prime-agent → bash → timeout → python3` ancestry = THIS
session (safe to kill the leaf); `witr -f <db>` names a DuckDB holder.

## gate_query — search the gate-run corpus

`outputs/*_report.md` (main + `outputs/wt/<name>/outputs/`), indexed
incrementally — query BEFORE tailing reports:

```bash
gate_query latest                     # newest run digest (default gate: qa)
gate_query failures [--full] [--recent 15]   # newest failure; landscape
gate_query recent --gate qa --last 10 --tests  # PASS/FAIL + failed ids
gate_query timing --leg <leg> --last 10      # where the time goes
```

## Thesis first — search before you claim

**Never state a claim about what this repo has, lacks, or already does
until a search backs it.** Session context is a *cache*, not a source; a
confident answer from a thin session is a guess wearing a suit.

Applies to every claim: "we already have X", "there's no Y here", "that
file is at path P", "the patch isn't refreshed", "finding F is still
open". Before asserting, run the index that owns the question —
`doc_query` for design/paths, `note_query` for the vault, `convo_query`
for what past sessions decided, `memory_query` for harness-memory
doctrine, `script_query` before grepping,
`ripwire` for structure, `git`/`stg` for tree state, `ls`/`test -f` for
existence. Then cite the hit.

Three rules that follow:

- **Absence claims need a search too.** "There's no PPR retrieval" is a
  finding, not a default. Absence of memory is not absence of code.
- **Never name a path you haven't stat'd.** A plausible-looking path
  invented from a neighbouring file is the single most common error;
  `ls` it or query for it. Fabricated paths read as authoritative.
- **Stale ≠ false, but stale is not a claim either.** Re-read live tree
  state (`git status`, `stg status`, `stg series`) at the moment of
  speaking. A snapshot from earlier in the session is not evidence about
  now — the user may have refreshed, committed, or deleted.

Reading a file to check a path is fine; reading one to *understand* a
subsystem is what the indexes are for. Land the conclusion with the
locator (`path:line`, hit count, command + output) so it can be
re-verified without re-deriving it.

## Rules

- The four query CLIs = INTENT; STRUCTURE = `ripwire`, `rg` fallback;
  Mojo language/API → **Mojo docs MCP, never web fetchers**.
- **Thesis first** (see above): search, then claim, then cite the hit.
- **All `memory/research.db` writes go through `helpers.core.db.connect()`**
  — never a raw `sqlite3.connect()`. Raw connections leave `PRAGMA
  foreign_keys` **OFF**, so an unregistered `agent_id` (e.g. `''`) inserts
  silently and surfaces hours later as a `foreign_key_check` failure in the
  integrity gate. `connect()` defaults `enable_fk=True`
  (`helpers/core/db.py:136`), so the bad row raises `IntegrityError` at the
  INSERT that caused it. Only the doc-index/lint paths may pass
  `enable_fk=False`. Any row carrying `agent_id` needs that agent registered
  in `provenance_agents` first (FK → `ON DELETE SET NULL`, so `NULL` is the
  correct "unknown" value, never `''`).
- **Keep the session todo list current** — a stale list misleads.
- Blocking `make qa` (ruff, md-lint, types, pytest, integrity,
  snapshot…), non-blocking `make advisory`. After editing `doc/**`,
  `Makefile` or helper docstrings: `make search-fresh` (advisory).
- Markdown lint-only: NEVER `markdownlint --fix` on `findata/**`
  (writer-owned vault). `doc/` is remediable.
- Gate arcs follow `doc/procedures/gates.md`: full gates ONCE per arc
  (qa + integration + perf + advisory) on the user's go; `search-fresh`/
  `convo-fresh` APPLY once at arc start; fixes validated by TARGETED tests
  only — never `make pytest`/`make test`; perf timing failures parked, no
  reruns. The user stages and commits — leave the tree dirty.
- **OCR review is advisory** — read `doc/procedures/ocr_review.md` first.
  `ocr delegate preview|rule` give selection + checklist; the HOST agent
  is the reviewer and the sole carrier of these conventions (OCR's own
  `rules` key is unverified — do not add it). Never a gate. Step 0: ask
  which ref, never the moving stgit top. Prove a `tests/` path is
  selected before trusting a review. Apply only accepted findings.
- **No patch/commit lifecycle ops.** Never `stg
  new`/`push`/`pop`/`delete`/`squash`, never
  `git commit`/`amend`/`rebase` — the operator owns patch structure.
  Edit files; `stg refresh` into the CURRENT top patch only (scoped
  pathspec when the tree holds unrelated dirt). EXCEPTION — gate
  close-out per `doc/procedures/gates.md`: on the arc's explicit go the
  agent may `stg new` the `gate_fixes` collector and, if the operator
  opts in, distribute fixes into owning patches (`stg refresh -p` fast
  path, `goto -k`/`spill` fallback) and message via `stg edit -f`. If
  asked to prepare a patch message, read `doc/procedures/commit-messages.md`
  first.
- Use `.venv/bin/python3` explicitly in non-interactive shells.
- **Backgrounding past a tool call (all harnesses):** tool timeouts kill
  the process GROUP — plain `&`/nohup children die with it. Use
  `setsid bash -c '<abs cmd> > <abs log> 2>&1; echo $? > <cmd>.exit' </dev/null >/dev/null 2>&1 &`
  — absolute paths INSIDE the subshell (cwd binds at parse time), then
  poll the `.exit` marker with short commands; never sleep-poll, never
  assume death from a missing log before checking the marker.
- **Scratch files: `$TMPDIR` ONLY** (here `/mnt/data/tmp`). Never
  `/tmp/opencode` or any other `/tmp` path — the operator rejects those
  calls. Expand it (`"${TMPDIR:-/tmp}/x"`) when it may be unset, and put
  logs, `.exit` markers and driver scripts there too.
