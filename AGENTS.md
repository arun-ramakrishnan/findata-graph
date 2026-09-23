# Instructions for LLM/agent sessions

Query, don't scan — docs and code are content-addressable; grep and
wholesale reads are the fallback.

## doc_query — `doc/` knowledge index

Design/decision/history questions. Query, then Read only the linked
section (`path:line [section] snippet` hits; covers all `doc/` incl.
gitignored `doc/local/`):

```bash
.venv/bin/python3 helpers/misc/doc_query.py "why did we not adopt langgraph" --limit 5
```

Scripts/tests/make/Mojo have their own index:
`helpers/misc/script_query.py "<task>" --kind script|test|make|mojo` —
query BEFORE grepping or writing a new helper; it may exist.

## ripwire — structural code discovery (map-before-read)

Symbols, callers, blast radius. Offline binary (eval:
doc/improvements/archive/tooling/ripwire_adoption.md). Locate, then Read
only what it names.

```bash
ripwire . --for="<task in words>"        # orient: ranked signatures
ripwire . --callers=SYM | --impact=SYM   # callers / blast radius
ripwire . --grep=STR --grep-in=any --legend=compact  # literals — ALWAYS both flags
```

## witr — process discovery (`/usr/local/bin/witr`)

WHY a process/resource is busy — before `ps aux | grep`. Full usage:
`man witr`. Session-specific: `prime-agent → bash → timeout → python3`
ancestry = THIS session (safe to kill the leaf); `.duckdb` two-writers —
`witr -f <db>` names the holder.

## Commit messages — stg patches

Follow `doc/procedures/commit-messages.md` exactly: seed template →
fill slots → `stg edit -f <file> <patch>`. All format detail lives
there; the never-`git commit`/`amend` rule is § Rules.

## gate_query — search the gate-run corpus

`outputs/*_report.md` (main + `outputs/wt/<name>/outputs/`) indexed by
`helpers/misc/gate_query.py` (DuckDB, incremental) — query BEFORE
tailing reports (`gate_query` = `.venv/bin/python3 helpers/misc/gate_query.py`):

```bash
gate_query latest                     # newest run digest (default gate: qa)
gate_query latest --gate all          # newest run PER GATE, one line each
gate_query failures [--full]          # newest failed run: legs/tests + err heads
gate_query failures --recent 15       # failure landscape across runs
gate_query recent --gate qa --last 10 --tests   # history: PASS/FAIL + failed ids
gate_query latest -wt graph_algos     # worktree copies (-wt = --wt)
gate_query timing --leg graph_l1_betweenness --last 10
```

## Rules

- **Stale index?** CLI warns and still answers (script_query may exit 1
  with the build command). Rebuild:
  `helpers/maintenance/rebuild_{doc,script}_search.py` (warm ≈ instant).
- Never read `doc/improvements/completed.md` (166 KB) or 40 KB archived
  proposals wholesale — query first.
- doc_query/script_query = INTENT; STRUCTURE = `ripwire`, `rg` fallback;
  Mojo language/API → **Mojo docs MCP, never web fetchers**.

## Gates & hygiene

- **Keep the session todo list current** — a stale list misleads.
- Blocking: `make qa` — ruff, **md-lint** (markdownlint-cli2,
  Node-gated), types, deptry, static_checks, pytest, verify_notes,
  integrity, snapshot. Non-blocking: `make advisory`.
- Markdown lint-only: NEVER `markdownlint --fix` on `findata/**`
  (writer-owned vault). `doc/` is remediable.
- After editing `doc/**`, `Makefile`, or helper docstrings:
  `make search-fresh` (advisory, never qa-gated).
- Full gates ONCE per arc, on the user's go. After fixes, re-run ONLY
  the failed legs (their individual make targets), not the whole gate.
  The user stages and commits — leave the tree dirty.
- **No patch/commit lifecycle ops.** Never `stg
  new`/`push`/`pop`/`delete`/`squash`, never
  `git commit`/`amend`/`rebase` — the operator owns patch structure.
  Edit files; `stg refresh` into the CURRENT top patch only (scoped
  pathspec when the tree holds unrelated dirt).
- Use `.venv/bin/python3` explicitly in non-interactive shells.
