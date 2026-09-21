# Procedure: The search TUI (search_tui)

**Date:** 2026-09-16
**Scope:** one full-screen terminal front door over every search
surface the repo already keeps fresh. No new index is built here — the
TUI is a consumer of the three `make search-fresh` sidecars plus the
two stateless structural tools.

## Lanes

| # | Lane | Backend | Notes |
|---|------|---------|-------|
| 1 | docs | `helpers/misc/doc_query.py --json` | FTS5 + embeddings over `doc/` (incl. `doc/local/`) |
| 2 | scripts | `helpers/misc/script_query.py --json` | helpers/tests/make/mojo; `make` rows jump to the Makefile rule |
| 3 | notes | `note_search` FTS5 in `memory/research.db` | bm25-ranked keyword search over the findata vault |
| 4 | code | `ripwire` | default `--for=`; verbs below |
| 5 | literal | `rg` | gitignore-aware, `path:line:text` rows, capped at 300 |
| 6 | reports | `outputs/*_report.md` | append-only run reports; empty query = per-report overview |

Code-lane verbs (query prefixes): `callers:SYM`, `impact:SYM`,
`grep:STR` (always carries `--grep-in=any`), `recall: question`.
Anything else runs `--for=<query>` (the ranked symbol map).

Reports-lane verbs: `qa:`, `advisory:`, `integration:`, `maint:`,
`perf:`, `integrity:`, `verify:` select a report file. Verb alone →
latest run's rows; verb + text → matches across all runs of that
report; bare text → matches across latest runs of every report; empty
query → one overview row per report (latest verdict). Unknown `x:`
prefixes are treated as plain text.

## Keys

| Key | Action |
|-----|--------|
| type + enter | run the query (enter-to-search; no type-to-search) |
| `1`-`6` | switch lane and re-run (stale rows clear immediately) |
| tab / shift+tab | cycle panes: query → tabs → results → preview |
| `m` | toggle hybrid / bm25 match mode (docs & scripts lanes) |
| `t` | cycle theme (github-dark → github-light → solarized-dark → high-contrast) |
| `T` | theme picker screen (enter applies + persists) |
| `h` | call chain of the selected code symbol → table rows |
| click/enter on a chain row | drill into that caller/callee |
| `b` | back — unwind the chain stack to the previous view |
| `i` | index monitor (c/C deep-check, r/R rebuild, esc close; busy indexes animate and reject double triggers) |
| `V` | report screen — comprehensive view over `outputs/` reports |
| `d` | database screen — schema tree + SQL + results (read-only, see below) |
| alt+`d` | database screen from anywhere, even while typing |
| enter (overview row, reports lane) | open the report screen for that file |
| `/` | focus the query line |
| enter (normal row) | open the hit: markdown via `glow -p`, else `$VISUAL`/`$EDITOR`/nvim/vim/less (line-aware) |
| `e` | open in the editor even for markdown |
| `y` / `Y` | copy path / `path:line` (OSC52, falls back to wl-copy/xclip/xsel/pbcopy) |
| `q` / ctrl+c | quit |

The status bar shows each index's age — the `search-fresh` contract:
lanes are only as fresh as the last `make search-fresh APPLY=1`. While
a query or call-chain walk runs, the results table carries a loading
overlay; in the index monitor, busy rows animate with an elapsed-time
note line (frames ported from ratatui-spinner's FluxFrames).

## Database screen (`d` / alt+`d`)

Three panes over the live stores — schema tree (left), SQL editor
(middle-top, syntax-highlighted), results grid (middle-bottom).
Read-only by construction: one short-lived read-only connection per
query (SQLite `mode=ro`, DuckDB `read_only=True`), closed after each
run, so no lock is ever held across the session; writes are rejected
by the engines (`attempt to write a readonly database`). 200-row cap
with a truncation notice; SQLite statements abort after 2 s via a
progress handler; every query runs in a worker thread so the screen
never blocks. Needs the `tui` extra (now includes `tree-sitter` +
`tree-sitter-sql` for highlighting).

| Key | Action |
|-----|--------|
| enter (tree row) | run `SELECT *` (table) or one column (leaf), capped |
| f5 | run the editor SQL (enter is a newline — multiline supported) |
| type prefix + tab | complete from keywords + tables + `table.column` (matches list under the editor) |
| `P` / `N` | older / newer query (per-store history in `~/.config/search_tui/`; ctrl+`p` is the app command palette) |
| `/` | focus the row filter (fuzzy subsequence over loaded rows, live) |
| enter (filter) | back to the editor |
| `v` | inspect the row (`col: value` lines under the grid) |
| `y` / `Y` | yank cell / row (tab-separated) |
| `j`/`k`/`h`/`l` | vim-style move in tree/results (expand/collapse on `l` in tree) |
| `s` | switch store: `research.db` (SQLite) ↔ `sources.duckdb` (DuckDB) |
| `[` / `]` | widen/narrow the schema tree (modal fills the terminal) |
| `-` / `=` | shrink/grow the SQL box (the results grid takes the rest) |
| esc / `q` | safe harbor — focus the tree (never exits the modal) |
| alt+`q` | close (the only way out; `ctrl+q` is Textual's app force-quit) |

Single-key actions are inert while the editor or filter has focus (they
are text there) — focus the tree/results first. The footer shows modal
keys only; search-lane keys don't apply here.

The safety line names the store and the posture
(`research.db · SQLite · read-only · live · one connection per query ·
200-row cap`). Blobs render as `<N bytes>`, `NULL`s as `NULL`, long
cells truncate at 120 chars. FTS5 `note_search MATCH …` queries work
through the same box. Terminal-free adapters (`db_schema`, `db_run`)
live in `helpers/misc/search_tui.py` and are unit-tested against tmp
fixture DBs — never the live stores.

## Dependency

`textual` is the optional `tui` extra: `uv sync --extra tui`
(or `uv pip install textual`). Without it, the CLI prints install
help and exits 2. Adapters (`run_lane` and friends) are importable
without textual, so tests never need the extra.

## Launch

```bash
make search-tui
python3 helpers/misc/search_tui.py -q "langgraph" --lane docs
```

## Related

- `doc/procedures/doc-search.md` — the doc index itself
- `doc/procedures/script-search.md` — the script index itself
- `AGENTS.md` — ripwire verb reference
