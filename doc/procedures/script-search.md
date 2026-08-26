# Procedure: The script metadata index (script_search)

**Date:** 2026-08-25
**Proposal:** `doc/improvements/archive/tooling/script_metadata_search.md`
**Scope:** build/refresh/query of the FTS5 + embeddings index over the
repo's own code surface — every `helpers/**` script, `tests/**` module,
root `app.py`, and Makefile target. Companion to `procedures/doc-search.md`
(the doc/ corpus index); both reuse the same machinery and sidecar doctrine.

## What it answers

Intent questions grep and codebase-memory don't: *which script audits
relation diffs*, *which test file covers the yfinance driver*, *what does
`make qa` actually run*, *which targets execute `helpers/graph/query.py`*.
Each row is one script / test / make target, composed from the module
docstring (purpose = first paragraph), regex-extracted argparse flags,
AST-import-derived `tested_by` links, and Makefile recipe wiring —
zero new authoring burden; metadata comes from what the code already says.

## What exists where

| Surface | Store | Refresh |
|---|---|---|
| `script_search` FTS5 (BM25 + JSON embeddings, hybrid RRF) | `memory/script_search.db` (gitignored sidecar) | this procedure |
| Row composition (docstrings, CLI tokens, imports, Makefile parse) | recomputed every rebuild (~185 files, sub-second) | automatic |
| Embed cache (`(sha256(text), model) -> vector`) | pooled `memory/embed_store.db` (schema `vecdb`; shared with every indexer) | automatic |
| Model stamp (`embed_model` / `embed_dims`) | `script_search_info` in the sidecar | same run |

The sidecar is deliberately NOT in `memory/research.db` (same locality rule
as doc_search). It is derived state — delete it and one warm rebuild
restores everything, embeddings included (the cache is content-addressed).

## Build / refresh

```bash
.venv/bin/python3 helpers/maintenance/rebuild_script_search.py              # full rebuild
.venv/bin/python3 helpers/maintenance/rebuild_script_search.py --incremental
.venv/bin/python3 helpers/maintenance/rebuild_script_search.py --check      # freshness gate
make script-search-rebuild                                                  # same, via make
```

Which mode when (measured 2026-08-25 on the live tree: warm full ≈ 1s with
an embed-cache hit rate near 100%; first cold build embeds ~230 rows):

- **`--incremental`** always re-extracts every unit (script rows embed
  cross-file inputs — test imports and Makefile refs — so unchanged files
  can't be blindly carried), but only WRITES rows whose tuple changed
  (row-keyed diff on `title`) and only re-embeds through the shared cache.
  A no-change cycle writes nothing.
- **Plain full** is the convergence pass (catches any pre-existing table
  drift) and writes a last-good-state backup into gitignored `db-backup/`
  (`script_search_backup.db` + `_vec`). Zero-churn aware: it reports
  `content_changed: False` when the row multiset didn't move.
- **`--check`** prints FRESH or the unit-level drift breakdown
  (`N changed, N new, N deleted` + file list + refresh command), **exits 1
  on drift** (house gate doctrine), writes no rows, but warms the embed
  cache.

## Query

```bash
.venv/bin/python3 helpers/misc/script_query.py "audit relation diffs"
.venv/bin/python3 helpers/misc/script_query.py "yfinance" --kind test
.venv/bin/python3 helpers/misc/script_query.py "integrity" --area misc --json
.venv/bin/python3 helpers/misc/script_query.py "what does make qa run" --kind make
```

Filters post on the UNINDEXED `kind` (`script|test|make`) and `area`
(helpers subdir name, `app`, `test`, `make`) columns. A stale index warns
on stderr and still answers; a missing index is a hard exit 1 with the
build command. Division of labor with codebase-memory-mcp: this index is
INTENT (what is it for, what runs it, what tests it); codebase-memory is
STRUCTURE (symbols, callers/callees). No HTTP endpoint yet (proposal S4,
deferred — the query core is endpoint-ready).

## Extraction contract (what a row promises)

- `purpose` — first paragraph of the module docstring; filename-derived
  fallback when absent. The `## annotation` for make rows.
- `cli:` / `subcommands:` — regex over `add_argument("--x")` /
  `add_parser("x")` calls. Discovery surface only; `--help` is ground truth.
- `tested_by:` / `imports:` — **AST imports only** (deliberately no
  grep-mention: comments and string literals would make the map noisy, and
  it is only useful if precise).
- `make:` / `scripts:` — bidirectional Makefile wiring (recipe substring
  match on indexed paths).
- `defs:` — top-level def/class names, enrichment only (not a symbol index).

## Gates & perf coverage

The freshness gate moved out of `make perf` (2026-08-26, #159) into
`make search-fresh` + the advisory gate's `script-search-check` row
(same doctrine as doc-search). `make perf` keeps the `script_query`
hybrid query latency benchmark (budget 3.0 s) as a real subprocess
against the live tree. Deliberately NOT in `make qa`: code edits land
between maint cycles and would redden qa constantly. Builder + CLI behavior
tests: `tests/test_rebuild_script_search.py`, `tests/test_script_query.py`
(hermetic tmp mini-trees, marked `integration`).

## Agent sessions

`AGENTS.md` (repo root) directs sessions to query `script_query.py` before
guessing filenames — and before writing any new helper/test (it may already
exist).
