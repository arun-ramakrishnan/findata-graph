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
  `Read(offset=line)` target. Full sentences are safe (`--json` for
  machine output). Covers ALL of `doc/` incl. gitignored `doc/local/`.
- Operator doc: `doc/procedures/doc-search.md`.

## script_query — the script/test/make/Mojo index

Every `helpers/**` script, `tests/**` module, root `app.py`, Makefile
target, and Mojo module is indexed by purpose, CLI flags, make wiring,
and tests. Query BEFORE grepping — and BEFORE writing any new
helper/test (it may already exist):

```bash
.venv/bin/python3 helpers/misc/script_query.py "audit relation diffs" --kind script
```

- Filters: `--kind script|test|make|mojo`, `--area`, `--json`.
- Operator doc: `doc/procedures/script-search.md`.

## Rules

- **Stale index?** The CLI warns and still answers (script_query may
  exit 1 with the build command). Rebuild via
  `helpers/maintenance/rebuild_{doc,script}_search.py` (warm ≈ instant;
  content-hash embed cache).
- Never read `doc/improvements/completed.md` (166 KB) or 40 KB archived
  proposals wholesale — query first.
- Division of labor: these two answer INTENT; STRUCTURE (symbols,
  callers) is codebase-memory-mcp + `rg`; Mojo language/API questions go
  to the **Mojo docs MCP, never web fetchers**.

## Gates & hygiene

- Blocking: `make qa` — ruff, **md-lint** (markdownlint-cli2; Node-gated,
  skips without Node), types, deptry, static_checks, pytest,
  verify_notes, integrity, snapshot. Non-blocking sweep: `make advisory`.
- Markdown is lint-only: NEVER run markdownlint `--fix` over
  `findata/**` (writer-owned vault, sentinel machinery). `doc/` is the
  remediable surface.
- After editing `doc/**`, the `Makefile`, or helper docstrings:
  `make search-fresh APPLY=1` converges all three indexes (doc, script,
  note). Index checks are advisory, never qa-gated.
- Full gates ONCE per arc, at the end, with the user's go. The user
  stages and commits — leave the tree dirty.
- Use `.venv/bin/python3` explicitly in non-interactive shells (examples
  above do).
