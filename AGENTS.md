# Instructions for LLM/agent sessions

Query, don't scan — the doc corpus and the code surface are both
content-addressable. Grep and wholesale reads are the fallback.

## doc_query — the `doc/` knowledge index

Design/decision/history questions are answered by the index (~380
sections: architecture, archived proposals, completed.md run log,
procedures, local notes). Query, then `Read` only the linked section:

```bash
.venv/bin/python3 helpers/misc/doc_query.py "why did we not adopt langgraph" --limit 5
```

- Hits are `path:line [section] snippet`, repo-rooted → direct
  `Read(offset=line)` target. Covers ALL of `doc/` incl. gitignored
  `doc/local/`. Scripts/tests/make/Mojo have their own index —
  `helpers/misc/script_query.py "<task>" --kind script|test|make|mojo`
  (query BEFORE grepping or writing a new helper; it may exist).

## ripwire — structural code discovery (map-before-read)

Symbols, callers, blast radius, doc ranking: the offline `ripwire`
binary (no daemon, no API key; adopted per
doc/improvements/archive/tooling/ripwire_adoption.md — head-to-head
eval, verb families, Mojo-lane scope there). Map before read: locate
with ripwire, then Read only what it names.

```bash
ripwire . --for="<task in words>"        # orient: ranked signatures
ripwire . --callers=SYM | --impact=SYM   # callers / blast radius
ripwire . --grep=STR --grep-in=any --legend=compact  # literals — ALWAYS both flags
```

## witr — process discovery for long-running jobs

`witr` (`/usr/local/bin/witr`) explains WHY a process/resource is busy.
Reach for it before `ps aux | grep` — DB lock clashes, orphaned
servers, and confirming a PID is yours before killing it:

```bash
witr <job> --tree                 # ancestry chain
witr -f memory/data/sources.duckdb  # WHO holds this file open
witr --pid N --warnings           # suspicious env/args/parents
witr --port 5432 --env            # who owns a port + env
```

- `prime-agent → bash → timeout → python3` ancestry = launched by THIS
  session (safe to kill at the leaf). Two writers on a `.duckdb`:
  `witr -f` names the holder — kill the stale one, rerun.

## Commit messages — stg patches

Patch messages follow `doc/procedures/commit-messages.md` (seed:
`doc/templates/commit_message.md`): fill the slots in a temp file at an
absolute path, apply with `stg edit -f <file> <patch>` — never
`git commit`/`amend` (operator owns structure, § Rules). Subject:
`[Findata] <area>: <imperative, ≤72ch>`; body WHY-first, WHAT slices
with one number each, `Gates:` trailer listing only gates actually run.

## Rules

- **Stale index?** The CLI warns and still answers (script_query may
  exit 1 with the build command). Rebuild via
  `helpers/maintenance/rebuild_{doc,script}_search.py` (warm ≈ instant;
  content-hash embed cache).
- Never read `doc/improvements/completed.md` (166 KB) or 40 KB archived
  proposals wholesale — query first.
- Division of labor: doc_query/script_query answer INTENT; STRUCTURE
  (symbols, callers) is `ripwire` (§ above), `rg` the fallback; Mojo
  language/API questions go to the **Mojo docs MCP, never web fetchers**.

## Gates & hygiene

- **Keep the session todo list current** — mark items completed the
  moment they land, add blockers/follow-ups as they emerge. A stale
  list actively misleads the operator.
- Blocking: `make qa` — ruff, **md-lint** (markdownlint-cli2; Node-gated,
  skips without Node), types, deptry, static_checks, pytest,
  verify_notes, integrity, snapshot. Non-blocking sweep: `make advisory`.
- Markdown is lint-only: NEVER run markdownlint `--fix` over
  `findata/**` (writer-owned vault, sentinel machinery). `doc/` is the
  remediable surface.
- After editing `doc/**`, the `Makefile`, or helper docstrings:
  `make search-fresh` checks all three indexes (doc, script,
  note). Index checks are advisory, never qa-gated.
- Full gates ONCE per arc, at the end, with the user's go. The user
  stages and commits — leave the tree dirty.
- **No patch/commit lifecycle ops.** Never `stg new`/`push`/`pop`/
  `delete`/`squash`, never `git commit`/`amend`/`rebase` — the operator
  owns patch structure (2026-09-11: agent-created archival patch had to
  be manually squashed). Edit files and `stg refresh` into the CURRENT
  top patch only (scoped pathspec when the tree holds unrelated dirt).
- Use `.venv/bin/python3` explicitly in non-interactive shells (examples
  above do).
