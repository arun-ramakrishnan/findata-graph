# Instructions for LLM/agent sessions

Query, don't scan — five indexes cover this repo; grep and wholesale
reads are the fallback. TUI lanes are a usability shell over the CLIs
below, never the only way in.

## Search surfaces — pick the index by intent

| intent | tool | index |
|---|---|---|
| design/decision/history in `doc/` | `helpers/misc/doc_query.py` | `doc_search.db` |
| scripts/tests/make/Mojo | `helpers/misc/script_query.py` | `script_search.db` |
| **past agent sessions** (opencode+prime-rlm+zcode) | `helpers/misc/convo_query.py` | `convo_search.duckdb` + `_fts.db` |
| vault notes (`findata/**`) | `helpers/misc/note_query.py` | `research.db` (`note_search`) |
| gate-run reports | `helpers/misc/gate_query.py` | `outputs/*_report.md` |
| code structure / callers | `ripwire` | offline binary |
| why a process or file is busy | `witr` | live |
| all of it, interactively | `make search-tui` (lanes 1-7) | — |

One rule the table hides: **harness conversations are not in `doc/`.**
Docs hold conclusions; `convo_search` holds the path to them — dead
ends, discarded options, why a live default is what it is.

## The four query CLIs

```bash
Q=".venv/bin/python3 helpers/misc"
$Q/doc_query.py "why did we not adopt langgraph" --limit 5   # doc/ incl doc/local
$Q/note_query.py "promoter holding" --limit 5              # findata/** vault
$Q/script_query.py "<task>" --kind script|test|make|mojo   # before grepping
$Q/convo_query.py "hypergraph scaling ceiling" --limit 5   # past sessions
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

## Rules

- The four query CLIs = INTENT; STRUCTURE = `ripwire`, `rg` fallback;
  Mojo language/API → **Mojo docs MCP, never web fetchers**.
- **Keep the session todo list current** — a stale list misleads.
- Blocking `make qa` (ruff, md-lint, types, pytest, integrity,
  snapshot…), non-blocking `make advisory`. After editing `doc/**`,
  `Makefile` or helper docstrings: `make search-fresh` (advisory).
- Markdown lint-only: NEVER `markdownlint --fix` on `findata/**`
  (writer-owned vault). `doc/` is remediable.
- Full gates ONCE per arc, on the user's go. After fixes, re-run ONLY
  the failed legs (their individual make targets), not the whole gate.
  The user stages and commits — leave the tree dirty.
- **No patch/commit lifecycle ops.** Never `stg
  new`/`push`/`pop`/`delete`/`squash`, never
  `git commit`/`amend`/`rebase` — the operator owns patch structure.
  Edit files; `stg refresh` into the CURRENT top patch only (scoped
  pathspec when the tree holds unrelated dirt). If asked to prepare a
  patch message, read `doc/procedures/commit-messages.md` first.
- Use `.venv/bin/python3` explicitly in non-interactive shells.
