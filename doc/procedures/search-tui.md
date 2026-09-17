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
| `i` | index monitor (c/C deep-check, r/R rebuild, esc close) |
| `V` | report screen — comprehensive view over `outputs/` reports |
| enter (overview row, reports lane) | open the report screen for that file |
| `/` | focus the query line |
| enter (normal row) | open the hit: markdown via `glow -p`, else `$VISUAL`/`$EDITOR`/nvim/vim/less (line-aware) |
| `e` | open in the editor even for markdown |
| `y` / `Y` | copy path / `path:line` (OSC52, falls back to wl-copy/xclip/xsel/pbcopy) |
| `q` / ctrl+c | quit |

The status bar shows each index's age — the `search-fresh` contract:
lanes are only as fresh as the last `make search-fresh APPLY=1`.

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
