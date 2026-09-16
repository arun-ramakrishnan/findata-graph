---
title: "Search TUI — one terminal front door over the search surfaces"
status: executed
filed: "2026-09-16"
executed: "2026-09-16"
completed_md: "242"
area: "helpers/misc/search_tui.py (terminal-free adapters), helpers/misc/search_tui_app.py (Textual UI), tests/test_search_tui.py, Makefile + pyproject tui extra — pure consumer of existing indexes"
---

# Search TUI: one terminal front door over the search surfaces

## 1. Why now

The repo's query discipline is query-don't-scan, but every surface is
a separate CLI invocation with its own flags and output shape:
`doc_query.py`, `script_query.py`, the `note_search` tables (only
reachable via app.py), `ripwire`, and `rg`. Reading a hit means a
second manual step (open the file, find the line). The operator asked
for a single full-screen interface — htop / `witr -i` class — with
glow as the markdown reader.

No new index is built: the TUI is a pure consumer of the three
`make search-fresh` sidecars plus the two stateless structural tools.
The status bar shows each index's age so staleness stays visible (the
search-fresh contract carries through).

## 2. Design

- **Five lanes**, mapped exactly to what exists:

  | Lane | Backend | Parse |
  |------|---------|-------|
  | docs | `doc_query.py --json` | anchor/title/section/similarity |
  | scripts | `script_query.py --json` | kind/area/purpose; `make` rows jump to the Makefile rule line |
  | notes | `note_search` FTS5, bm25 | snippet(note_search, 4), anchor |
  | code | `ripwire` | `<d l= n= p= r=>` ranked rows, `<s t= n= p=path:line>` callers/impact rows, `<f p=><hit l=>` grep rows, `━━ path (relevance)` recall blocks |
  | literal | `rg -n -S --no-heading` | `path:line:text`, capped 300 |

  Code-lane verbs honor AGENTS.md: `callers:`, `impact:`, `grep:`
  (always `--grep-in=any`), `recall:`; default `--for=`.

- **Template**: witr/htop conventions — title bar, query line,
  lane strip, results table | preview pane (±14 lines around the hit,
  hit line reversed), status line (lane · hits · index ages), keybind
  footer. Live-as-you-type, 300 ms debounce, worker-thread queries.

- **Reading**: `enter` opens the hit — markdown via `glow -p`,
  otherwise `$VISUAL`/`$EDITOR`/nvim/vim/less with `+line` when line
  aware. `e` forces the editor. `y`/`Y` copy path / `path:line`
  (OSC52 with wl-copy/xclip/xsel/pbcopy fallback).

- **Dependency posture**: `textual>=8.2` as an optional `tui` extra
  (`uv sync --extra tui`). The adapter half
  (`run_lane`, parsers, open/copy resolution) imports without textual,
  so the dependency stays off the runtime set and tests need no TTY.

## 3. Slices

1. **S1 adapters** — Hit model, five lane backends, ripwire verb
   parsing (4 output shapes), FTS-safe query degradation, make-target
   location, open/editor/copy resolution. Terminal-free.
2. **S2 app** — Textual UI: layout per the witr template, debounced
   worker queries, preview pane, lane switching, suspend-to-open
   external readers.
3. **S3 wiring** — `make search-tui` target (help alphabetical,
   .PHONY), `tui` optional group in pyproject, procedure doc
   `doc/procedures/search-tui.md`.
4. **S4 tests** — adapter/parser unit tests plus one optional
   headless pilot test (skips without textual or the doc sidecar).

## 4. Risks

- ripwire's minified-XML stdout is an interface, not a contract; the
  parsers are lenient (four shapes, fallback chain) and a format break
  degrades to "no rows", not a crash.
- `note_search` FTS5 MATCH syntax errors on raw punctuation;
  `fts_safe` degrades to quoted OR-tokens after a probe.
- glow inside `app.suspend()` owns the terminal until exit; acceptable
  (same as witr -i's detail view).

## 5. Deferred

- Semantic notes lane (embedding MATCH over note_search) — the granite
  model gate lives in `local_embedder.available()`; keyword bm25 covers
  the interactive case first.
- Script-lane `--kind` filter cycling; saved queries; multi-lane
  fan-out view.

## 6. Execution Results

- S1-S3 implemented 2026-09-16 in the working tree:
  `helpers/misc/search_tui.py` (adapters + entry),
  `helpers/misc/search_tui_app.py` (Textual UI), Makefile target +
  help line, pyproject `tui` extra, `doc/procedures/search-tui.md`.
- Lane smoke (live store): docs 6 hits (langgraph assessment),
  scripts 10 (snapshot targets), notes 10 (Infosys concall, real note
  paths), code verbs callers/impact/grep/recall/for all returning
  rows, literal 62 (`_SCHEMA_VERSION`).
- Process note: implementation preceded this proposal (operator
  caught it); filed retroactively with the executed state recorded
  rather than a fictional forward plan.
- Post-exploration fix wave (operator session found real breakage):
  rows keyed by `path:line` raised DuplicateKey on every lane except
  docs (scripts/notes share paths) — rows now keyed by index and
  `_selected` maps cursor_row directly; preview refresh wired to
  RowHighlighted (row-cursor type); `anchor` arrives as str from
  untyped SQLite columns — `_int_or_none` normalizes at parse time;
  the lane status line is rendered (was dropped); `check_action` dims
  single-key actions while typing (escape blurs to results); suspend
  hardened with post-resume refresh. House conformance: shebangs,
  sqlite via `helpers.core.db.connect`, lint-audit noqa at the three
  subprocess sites. Regression pilots added (scripts-lane duplicates,
  preview-on-arrow, digit-binding lane switch).
- Gates: qa + advisory green after the fix wave; search-fresh APPLY=1.
- Reopened arc (operator found live breakage post-archival; un-archived,
  fixed against a VLM-screenshot loop): enter-to-search only (typing
  must not fire lanes); tabbed lane strip with an arming gate for the
  `Tabs.add_tab` deferred-activation race (awaited adds + 0.75 s arm —
  a dropped timer call once left `armed` False forever and every
  lane click dead; direct arming let mount churn switch lanes —
  timer-only is the settled design); title-first table (path tail-
  truncated, score narrow); hit-line callout with numbered gutter +
  term chips; GitHub-dark markdown palette (push_theme — rich's red
  headings unreadable); index monitor rebuilt (enclosed dialog, honest
  per-state verdicts, strictly sequential checks/rebuilds); code lane
  demotes `--for` doc sections below symbol rows; literal lane rg is
  regex-first with `-F` fallback for non-regex patterns; persistent
  `mode <x>` status prefix.
- Call-chain drill-through: `h` renders the chain as table rows;
  click/enter on a caller/callee drills into its own chain; `b`
  unwinds the view stack; preview follows every row.
- Focus contract: strip-initiated lane switches keep focus on the
  strip (arrow-browsing); search/digit paths land on results.
- README hero screenshots regenerated from the final build (docs,
  chain drill, monitor).
