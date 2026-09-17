# Search TUI Design

**Status:** LIVE. The five-lane front door shipped in `b28991d548`
(2026-09-16); the reports lane, theme system, and report screen shipped
under `doc/improvements/archive/tooling/search_tui_enhancements.md` (patch
`tui_db`); the read-only database screen shipped 2026-09-17
(same patch, §2.6 item 8 decision: native over harlequin).

## 1. Problem & Scope

The repo keeps three search indexes fresh (`make search-fresh`: doc,
script, note FTS5 sidecars) plus two sanctioned structural tools
(ripwire, rg) — but reaching them meant remembering five CLIs, their
flags, and their output shapes. The search TUI is one full-screen
terminal front door over every search surface the repo already keeps
fresh. It builds no new index: lanes 1-3 are only as fresh as the last
`search-fresh` run (the status bar shows each index's age); lanes 4-5
are stateless.

Non-goals: replacing the CLIs (they remain the scriptable surface),
embedding new indexes, or editing code/notes from inside the TUI
(openers are read-only viewers; make-target execution stays explicit).

## 2. Architecture

Two halves, split on the TTY boundary so tests never need a terminal:

```text
helpers/misc/search_tui.py      terminal-free adapters (importable without
                                the `tui` extra; every unit test lives here)
  Hit                   normalized row (path/line/title/section/snippet/score/lane/kind)
  parse_*               backend normalizers (doc/script JSON, rg lines, ripwire XML-ish)
  run_lane(lane, q)     dispatch over _LANE_RUNNERS (+ report/theme helpers below)
  open_command          glow-for-md else $VISUAL/$EDITOR/nvim/vim/less (line-aware)
  index_*               freshness snapshot + deep-check/rebuild argv builders
   report adapters       RunBlock/RunStep/CheckRow parsers over outputs/*_report.md
   THEMES                palette data (textual vars + rich tokens + description) + persist
   db adapters           DbTable/DbColumn/DbResult over memory/research.db (SQLite,
                         house connect read_only) + memory/data/sources.duckdb
                         (DuckDB read_only): db_schema, db_run (never raises)

helpers/misc/search_tui_app.py  Textual front-end (imported lazily; needs TTY)
   SearchApp             query line, lane tabs, results table | preview pane
   IndexMonitor          modal: ages, deep checks, sequential rebuilds
   ThemeScreen           modal: theme picker (enter applies + persists)
   ReportScreen          modal: report drill-down + rerun
   DbScreen              modal: schema tree + SQL input + results grid (key `d`)
```

Backend contract per lane: `(hits, status-string)` — the status bar
always explains what ran, never just row counts.

## 3. Lanes

| # | Lane | Backend | Query language |
|---|------|---------|----------------|
| 1 | docs | `helpers/misc/doc_query.py --json` | hybrid semantic+lexical; `m` toggles bm25 (every hit contains the word) |
| 2 | scripts | `helpers/misc/script_query.py --json` | same toggle; `make` rows jump to the Makefile rule |
| 3 | notes | `note_search` FTS5 in `memory/research.db` | bm25 keyword; FTS5-unsafe input degrades to OR-tokens |
| 4 | code | `ripwire` | `--for=` map default; `callers:` `impact:` `grep:` (`--grep-in=any` always) `recall:` verbs; doc sections demoted below symbol rows |
| 5 | literal | `rg` | regex first, `-F` fallback on invalid patterns; capped at 300 rows |
| 6 | reports | `outputs/*_report.md` | verbs below; empty query = per-report overview |

Reports-lane semantics (mirrors the ripwire verbs): `qa:` `advisory:`
`integration:` `maint:` `perf:` `integrity:` `verify:` select a file;
verb alone → latest run's rows; verb + text → matches across all runs;
bare text → latest runs of every file; empty query → one overview row
per file (latest verdict). Unknown `x:` prefixes degrade to plain text.
Row `line` values are 1-based file offsets so preview + enter jump into
the report file.

## 4. Report format contract

All run reports consolidate under `outputs/` (gitignored, like
`bench_data`): gate family (`qa/advisory/integration/maint`),
`perf`, `database_integrity`, `verify_notes`, plus the enrichment
reports (`metrics`, `relations`) — nine files, all append-only `.md`.
Canonical shape is fixed by `doc/templates/report.md`: `# <header>`
per run, `**Generated:**` meta line, `| col |` tables,
`## <step> (STATUS)` detail sections. Parsers split multi-run files on
the repeated `#` H1 (headerless leading chunks are skipped, never
parsed as runs); integrity returns the latest run's sections, verify's
dict is last-run-wins. The lane does not parse the enrichment reports
yet (no verbs for them).

## 5. Theme system

Palettes are data, not code: `THEMES[name]` carries Textual theme
variables, rich preview tokens, and a one-line description. The app
registers all four with Textual's built-in theme registry at mount and
switches via `self.theme` — the single CSS block resolves
`$panel`/`$accent`/… from the active theme, so no CSS strings change
hands. The markdown preview swaps pop-then-push so palettes never
stack. Shipped presets: `github-dark` (default), `github-light`,
`solarized-dark`, `high-contrast` (accessibility). Choice persists to
`~/.config/search_tui/theme` (1 line; `memory/search_tui_theme`
fallback). `t` cycles, `T` opens the picker.

## 6. Interaction model

- **Enter-to-search only** — typing never runs lanes; `enter` fires,
  focus moves to results. Tab cycles panes query → tabs → results →
  preview (the generic focus-next skipped the strip, so the order is
  explicit). On the strip, arrows browse lanes natively; from
  results/preview, arrow keys switch lanes (`_cycle_lane`).
- **Results table** is title-first (title/section gets the max width,
  path collapses to tail + `:line`, score stays narrow); query terms
  highlight. **Preview** renders markdown via rich, code as a numbered
  context slice (±14 lines, hit line reversed).
- **Call chain** (`h` on a code-lane symbol): callers + callees land as
  table rows with section headers; click/enter drills deeper, `b`
  unwinds the stack.
- **Index monitor** (`i`): ages on mount (cheap), deep `--check` on
  demand (`c`/`C`), rebuilds sequential (`r`/`R`, one embedder at a
  time, each followed by a check). Rebuilding notes touches
  `research.db` — the monitor says so.
- **Openers:** `enter` opens (glow for markdown, editor otherwise),
  `e` forces the editor, `y`/`Y` copy path / path:line (OSC52, then
  wl-copy/xclip/xsel/pbcopy fallbacks). `q`/ctrl+c quits.
- **Database screen** (`d` / alt+`d`): schema tree (tables + typed columns),
  syntax-highlighted multiline SQL editor (F5 runs, Tab completes from
  keywords/tables/`table.column`, P/N per-store history), results
  grid with live fuzzy filter (`/`) and row inspect (`v`), vim-style
  hjkl + yank. One short-lived read-only connection per query (closed
  after each run — no held locks); 200-row cap + truncation notice;
  SQLite 2 s abort; errors render in the status line, never as
  exceptions. The safety line always states store + posture
  (`read-only · live · one connection per query`).
- **Modals** (`IndexMonitor`, `ThemeScreen`, `ReportScreen`, `DbScreen`)
  stop row-selected events so enter behind the modal never fires, and the
  app captures widget refs once (late worker callbacks must not
  re-query while a modal is up). The `Tabs` arming gate (awaited adds
  + 0.75s timer) absorbs mount-time auto-activation churn.
- **Observability:** `/tmp` event log (key/activation/worker lines;
  capped — it appends forever otherwise).

## 7. Testing strategy

- Adapters are terminal-free: `tests/test_search_tui.py` (pinned to
  one xdist worker via `xdist_group("search_tui")`) covers parsers,
  verbs, openers, themes, and the reports lane against synthetic
  fixtures plus a monkeypatched `REPO_ROOT` — never the live 300 KB
  reports, never the embedding model, never ripwire in loops.
- UI mechanics run as mocked Textual pilots (`run_test`): canned hits,
  direct action calls where keys are typing-gated (same reason the
  monitor test calls `action_monitor()` instead of pressing `i`).
- Manual smoke stays `make search-tui` (TTY only; exits 2 without one,
  with install help when the `tui` extra is missing).

## 8. Wiring

- `make search-tui` (venv-first recipe), `pyproject.toml` `tui` extra
  (`textual>=8.2`, `rich>=15`), operator doc
  `doc/procedures/search-tui.md`, README section with screenshots.
- `SearchApp(query, lane, limit)` CLI: `-q/--query`, `--lane`
  (choices follow `LANES`), `--limit` (default 40).

## 9. History

- `b28991d548` (2026-09-16) — five-lane front door + index monitor +
  call chain + 25 adapter/pilot tests (proposal
  `doc/improvements/archive/tooling/search_tui.md`, `completed.md` entry).
- `tui_db` (2026-09-17, this patch) — report writers to
  append-only `.md` in `outputs/`, report adapters, sixth lane, theme
  system, report screen, read-only database screen (proposal
  `doc/improvements/archive/tooling/search_tui_enhancements.md`, §6 execution
  log). Deps: SQLite via stdlib + house `connect`, DuckDB already
  vendored, `tree-sitter` + `tree-sitter-sql` for SQL highlighting
  (harlequin evaluated and parked —
  `doc/local/evaluations/tui_db_assessment.md`, preserved trial inputs
  in `bench_data/dbtui/`).

## 10. Future

- Lane-appropriate empty queries beyond reports, multi-lane fan-out
  (`*:`), make-target execution from script-lane rows, perf trend
  sparklines, yank-all paths, inline tail expansion.
- DbScreen follow-ups: multi-line SQL input, cell yank (`y` per cell),
  snapshot-at-open if a held-`ro` complaint ever appears (current
  posture is per-query connections, so no lock is held), harlequin
  suspend-launch as a glow-style optional external for DuckDB.
- Inspiration catalogue: [awesome-tuis](https://github.com/clayne/awesome-tuis) —
  a curated collection of terminal UI apps; worth mining for
  picker/preview/status-bar patterns before inventing new ones.

## Related

- `doc/procedures/search-tui.md` — operator manual (lanes, keys, launch)
- `doc/improvements/archive/tooling/search_tui_enhancements.md` — enhancement spec + execution log (`completed.md` #243)
- `doc/templates/report.md` — the report format contract writers follow
- `doc/improvements/archive/tooling/search_tui.md` — original build proposal
