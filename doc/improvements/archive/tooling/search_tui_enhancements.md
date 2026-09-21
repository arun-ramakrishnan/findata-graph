---
title: "Search TUI enhancements — themes + report lane"
status: executed
filed: "2026-09-17"
executed: "2026-09-17"
completed_md: "243"
area: "helpers/misc/search_tui.py (theme system + report-parsing adapters), helpers/misc/search_tui_app.py (theme switching + report screen), tests/test_search_tui.py (theme + report adapter tests)"
---

# Search TUI enhancements: multi-theme support + QA/integrity report lane

## 1. Why

The search TUI (`b28991d548`) is one full-screen front door over five
lanes. Two gaps remain:

1. **Theme** — the app hard-codes a GitHub-dark rich palette in
   `SearchApp.on_mount` (33 lines, inline `Theme` push). Operators
   working late, on light terminals, or with accessibility needs need
   switchable themes. The current setup also cannot be inspected
   without reading code.

2. **Report visibility** — `make qa` / `make integration` /
   `make advisory` produce `outputs/qa_report.md` / `outputs/integration_report.md`
   / `outputs/advisory_report.md` (append-only, timestamped tables + tails).
   `database_integrity_check.py` produces `outputs/database_integrity_report.md`
   (18-check registry, ERROR/WARNING counts, per-item detail). None of
   this is reachable from the TUI — an operator must `cat` the file,
   then manually open it. The TUI already runs these checks (index
   monitor, `c`/`C` deep-check); the reports they produce should be
   one key away.

### 1.1 Corrections to the request as stated

These were clarified during evaluation (verified on disk, not from memory):

- **No `verify_report.txt` exists** — the file is
  `outputs/verify_notes_report.md`. The reports lane should alias verb
  `verify:` → `outputs/verify_notes_report.md`.
- **All seven reports are append-only `.md` since 2026-09-17.**
  Gate family (`outputs/qa_report.md`, `outputs/advisory_report.md`,
  `outputs/integration_report.md`, `outputs/maint_report.md`), `outputs/perf_report.md`,
  `outputs/database_integrity_report.md`, `outputs/verify_notes_report.md` — every file
  is multi-run; the parser splits runs and defaults to most recent.
- **"qa/db/integrity" maps to `outputs/qa_report.md` +
  `outputs/database_integrity_report.md`** (no separate `db_report.txt`
  exists). The same gate-table parser also covers `advisory` /
  `integration` / `maint` for free — include the family.
- **Goal is markdown reports — EXECUTED 2026-09-17.** All seven
  reports are now `.md` following the report template
  (`doc/templates/report.md`): `# <header>` per run,
  `**Generated:**` meta line, `| col |` tables, `## <step> (STATUS)`
  detail sections, uniform across writers.
- **Enrichment reports also consolidated** (2026-09-17):
  `outputs/metrics_report.md` (yfinance enrich),
  `outputs/relations_report.md` (enrich_relations) — same `#`-run +
  `##`-section markdown shape, append-only. Do NOT parse in the lane
  yet (no verbs for them).

## 2. Design

### 2.1 Theme system (S1)

The current code has **two separate theming layers** that must be handled independently:

1. **Textual CSS** (`search_tui_app.py:241-260` — widget chrome: border colours, focus rings, panel sizes, zebra stripes). The `CSS` class attribute on `SearchApp`. Controls how the app frame looks.
2. **Rich Theme** (`search_tui_app.py:352-374` — 16 tokens, hard-coded github-dark palette pushed onto `self.console`). Controls how markdown renders in the `RichLog` preview pane.

Refinement: each theme ships as `{css: "...", rich: {...tokens}}`. Switching calls `self.CSS = new_css; self.refresh(stylesheets=True)` (Textual CSS swap) + `_apply_rich_theme(name)` (rich `Theme` push on `self.console`). Both layers swap atomically. Default theme: `github-dark` (current behaviour).

- **`helpers/misc/search_tui.py: THEMES`** — dict name → `{css, rich}`. Four presets ship:
  - `github-dark` (current default; translate lines 357–370 rich tokens + current CSS)
  - `github-light` (white bg; H1 `#24292f`, H2 `#0969da`, code `#24292f on #ddf4ff`, link `#0969da`; CSS panels light)
  - `solarized-dark` (8-base palette; headings desaturated, amber/yellow accents; CSS `$background`-tinted)
  - `high-contrast` (white bg + yellow/black text; bold headings; max-weight borders — accessibility; `$error`/`$warning` tokens only)

- **Persistence** — theme name → `Path.home() / ".config" / "search_tui" / "theme"` (1 line); falls back to repo-local `memory/search_tui_theme` if present. `SearchApp` reads on mount; `t` cycles; `T` opens theme picker screen.

- **Theme picker screen** (`ThemeScreen(ModalScreen)`) — 4 rows (one per theme), each showing theme name + palette description + sample CSS snippet. Current theme highlighted (checkmark). Enter/click applies + persists. Follows IndexMonitor pattern: DataTable + Static footer + worker-free apply (theme swap is synchronous).

### 2.2 QA/integrity/perf report lane (S2)

A sixth lane `"reports"` over **seven** report files the repo already writes,
all append-across-runs `.md` with `# <header>` run blocks (parser
defaults to most recent; `--all` returns chronological list):

| Report | Source | Parse target |
|--------|--------|--------------|
| `outputs/qa_report.md` | `make qa` → `tests/run_gate_report.py:write_report` (append + flock) | gate name, timestamp, step table (label/time/status), per-step tail |
| `outputs/advisory_report.md` | `make advisory` (same writer) | same shape (note: ty-tests non-blocking) |
| `outputs/integration_report.md` | `make integration` (same writer) | same shape |
| `outputs/maint_report.md` | `make maint[-full]` → `helpers/maintenance/maint.py:_write_report` (append) | same shape (verdict: `steps ok · maint PASS/FAIL (aborted)`; tails for FAILED steps only) |
| `outputs/perf_report.md` | `make perf` → `tests/run_perf_benchmarks.py` (append) | benchmark name/time/budget/status, per-run |
| `outputs/database_integrity_report.md` | `database_integrity_check.py:write_report_file` (append + flock) | `#`-delimited runs; within a run, `## SECTION (severity)` rows + `key=value` summaries |
| `outputs/verify_notes_report.md` | `verify_notes.py:generate_report` (append) | `#`-delimited runs; within a run, metric table + error buckets |

**Adapters** (terminal-free, in `search_tui.py`, unit-tested against
real fixture files):

- `parse_gate_report(path) → list[RunBlock]` — one `RunBlock` per
  `# make <gate> — <kind> report` header; steps within (label/time/status),
  summary row (`**N/M passed** · **gate FAIL**`), per-step tails. Blocks in
  file order; caller picks most recent or iterates all. Also parses
  `maint` blocks (`# make maint[-full]`, `steps ok` verdict row).

- `parse_perf_report(path) → list[RunBlock]` — same shape; steps are
  benchmarks with time + budget + status (`✓ OK`/`✗ FAIL`).

- `parse_integrity_report(path) → list[CheckRow]` — one row per
  `## SECTION (severity)` header: check name, severity (ERROR/WARNING),
  summary (`total=... errors=0 warnings=3`), detail lines below.
  Adversarial: severity comes from the heading parens, not `_CHECKS`
  (which has different casing).

- `parse_verify_report(path) → dict` — summary row (Total/Errors/
  Warnings), error bucket list, summary text.

- `parse_report_summary(path) → str` — one-line summary for the
  status bar / sidebar (gate verdict + error count).

**Report screen** (in `search_tui_app.py`, `ReportScreen(ModalScreen)`):

Follows the `IndexMonitor` skeleton exactly: `ModalScreen` →
compose `DataTable` + `Static` footer → `on_mount` fills →
worker threads → `_set_state` cell updates → `on_data_table_row_selected`
calls `event.stop()`.

- Two panes: **summary** (left, DataTable) + **detail** (right, RichLog).
- Summary columns: `check/check-or-step`, `severity`, `summary`, `status`.
- Rows colour-coded: ERROR red, WARNING amber, OK green, FAIL −, SKIP ⌀.
- `enter` drills into the selected check/step → renders its detail
  section from the report file in the detail pane (markdown-formatted).
- `r` reruns the check via subprocess (same argv as Makefile target;
  `APPLY=1` flag for applicable targets — advisory reads, not writes).
- `i` toggle index-monitor; `q`/`escape` close.
- **Comprehensive view (default for all reports)**:
  - Gate/perf reports (multi-run): **all runs listed** in the
    DataTable, most recent first, colour-coded by verdict
    (✓ OK green, − FAIL red, ⌀ SKIP grey).
    Enter drills into the selected run → step table + tails in detail pane.
  - Integrity/verify reports (multi-run since 2026-09-17): **latest run's
    checks** listed with severity colouring; run picker for older runs.
    Enter → section detail.
  - Status bar top line: `"8/9 passed · gate FAIL"` (gate reports) or
    `"16/18 clean · 0 errors · 2 warnings"` (integrity) — derived from
    the parsed content, not hard-coded.

### 2.3 Format specs for parsers

Each report has a distinct delimiter / row format. Parsers must handle these exactly:

**Gate blocks** — `run_gate_report.py:write_report` (markdown since 2026-09-17):
- Run delimiter: `^#\s+make\s+(\S+)\s+—\s+(gate|maint)\s+report\s*$`
  (report name from group 1); meta line `**Generated:** <ts> ·
  **Python:** X[  jobs=N]`. A headerless leading chunk (stale content)
  is skipped, never parsed as a run.
- Row: `^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$` →
  label / time-or-`—` / status (`✓ OK`, `✗ FAIL`, `✗ FAIL (non-gating)`, `⌀ SKIP`).
- Verdict row: `| **N/M passed** | | **gate (PASS|FAIL)** |`; maint variant:
  `| **N/M steps ok** | | **maint (PASS|FAIL[^)]*)** |` (`FAIL (aborted)` kept).
- Tails: `^## (.+) \((OK|FAILED)\)$`; detail = following lines until the
  next `#`-header (first ~12 joined; full tail stays in the file).

**Perf blocks** — `run_perf_benchmarks.py` (markdown since 2026-09-17):
- Run delimiter: `^#\s+make\s+perf\s+—\s+benchmark\s+report\s*$`;
  meta `**Generated:** <ts> · **Python:** X`.
- Row: `^\|\s*(.+?)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)s\s*\|\s*(.+?)\s*\|$` →
  label / time / budget / status (`✓ OK` / `✗ FAIL`).
- Verdict row: `| **N/M passed** | | | |` (no PASS/FAIL word — derive from ratio).

**Integrity** — `database_integrity_check.py:write_report_file`:
- Split on `^## (.+)$`; per-section verdict from `-> errors=(\d+)`;
  file verdict = sum of section errors (0 → PASS). One Hit per section,
  detail = the `key=value` summary line.

**Verify** — `verify_notes.py:generate_report`:
- Header counts via table rows: `| (Total Files Checked|Errors|Warnings) | (\d+) |`
- Error items: `^### (.+) \((\d+)\)$` for buckets,
  `^- (.+): (.+)$` for items under `## ERRORS` / `## WARNINGS`.
  One Hit per issue (label=bucket, detail=`file: desc`), capped by lane limit.
  Verdict PASS iff errors == 0.

### 2.4 Architecture note: report_parse.py vs inlining

To keep `search_tui.py` from bloating (it already has 579 lines of adapters),
a dedicated `helpers/misc/report_parse.py` module is recommended:

- Pure-parser module (no `search_tui` import) defining `ReportRun` / `ReportRow` dataclasses.
- `search_tui._run_reports` maps `ReportRun`/`ReportRow` → `Hit` (keeps the terminal-free adapter pattern).
- Unit-testable in isolation via `tests/test_report_parse.py` (synthetic multi-run texts incl. overwrite single-run shape, verb/filter matrix).

This is optional — adapters in `search_tui.py` (as proposed in §2.2) work correctly; `report_parse.py` is a future cleanup if adapter count grows.

### 2.5 Query language for the reports lane

Mirrors ripwire verbs:

- `qa:` `advisory:` `integration:` `maint:` `perf:` `integrity:` `verify:` → select a report;
- verb alone → latest run's rows;
- verb + text → matches across **all** runs of that report (`section` = run stamp);
- bare text → matches across latest runs of every report;
- empty query → one overview Hit per report (latest verdict + `passed/total`) = the comprehensive view.

`Hit` mapping for reports lane: `path` = report filename, `line` = 1-based file offset
(table row / tail start / section header) so preview + enter jump into the file;
existing preview renders `.txt` as a numbered context slice with zero app changes.

### 2.6 Additional features (proposed, not designed in detail)

Identified during evaluation; can be added after S1–S2:

1. **Run action for make rows** — `R` to execute the selected make target
   in a suspend-and-run like `_open`, with gate-report files then re-parsed
   (they just changed on disk).
2. **Multi-lane fan-out** — `*:` prefix (or `ctrl+enter`) runs the query over
   all lanes and groups rows by lane.
3. **Result pinning / yank-all** — `Y` copies all rows as `path:line` list
   for feeding follow-up tool calls (today `Y` copies one path).
4. **Preview for tail rows** — `v`/enter toggle to expand the full stored
   tail inline (saves the jump-to-file round trip for short tails).
5. **Perf trend sparkline** — per-benchmark `time/budget` ratio across runs
   rendered as a sparkline column turns the lane into a regression detector.
6. **Empty-query semantics per lane** — today empty query → "empty query".
   Make empty query show lane-appropriate recents (reports: overview;
   code: recent symbols from evlog; docs: index stats).
7. **Evlog rotation** — `/tmp/search_tui.log` appends forever — cap at N lines on mount.
8. **Database screen (DECIDED 2026-09-17, option 2 native)** — from the
   TUI DB assessment (`doc/local/evaluations/tui_db_assessment.md`):
   harlequin's three panes (schema/SQL/results) are the UX pattern, but
   harlequin itself stays out — it is a whole Textual app (no embedding),
   its SQLite catalog mislabels `research.db`'s 27 relations, and it is
   not in `.venv`. Instead a native `DbScreen` modal clones the
   three-pane layout: schema tree from `sqlite_master` /
   `information_schema` (correct by construction), SQL input, results
   grid. `research.db` via stdlib sqlite + house `connect(read_only=True)`,
   `sources.duckdb` via the already-vendored `duckdb` — zero new deps
   (harlequin suspend-launch parked, not removed: viable later as a
   glow-style optional external for DuckDB).
   2026-09-17 addendum: `sqlit` 1.6.4 trialed (`bench_data/dbtui/README.md`
   §sqlit) — correct SQLite catalog where harlequin was broken, DuckDB
   supported, same Textual 8.2.8, headless JSON query. Still launcher-only
   (full app, cannot embed) and read-write-only (no `mode=ro`; snapshots
   only). sqlit replaces harlequin as the preferred external companion;
   steal-list for the modal: dropdown completions, file-backed query
   history, results filter.

### 2.7 Keybindings

| Key | Action |
|-----|--------|
| `t` | cycle theme (github-dark → github-light → solarized-dark → high-contrast → github-dark) |
| `T` | open theme picker screen (see + name) |
| `r` (in reports) | rerun selected report check |
| `i` (in reports) | toggle index monitor |
| `R` (reports) | refresh all reports (rerun `make qa` — warns, reads only in TUI) |
| `enter` (in reports summary) | drill into selected run/check → detail pane |

### 2.8 Persistence

- Theme name → `Path.home() / ".config" / "search_tui" / "theme"` (1 line: theme name).
- Report paths → remembered last 4 in a JSON file at same dir
  (`recent_reports.json`) so the report screen opens directly to
  `outputs/database_integrity_report.md` if it exists, else `outputs/qa_report.md`.

## 3. Slices

1. **S1 theme** — `THEMES` dict (name → `{css, rich}`) + `_apply_theme()` in `search_tui.py`; theme persist/load in `search_tui_app.py`; `t` cycle, `T` picker screen; `high-contrast` accessibility theme.
2. **S2 report adapters** — `parse_gate_report`, `parse_perf_report` (`RunBlock` list), `parse_integrity_report` (`CheckRow` list), `parse_verify_report` (dict), `parse_report_summary` in `search_tui.py` + `tests/test_search_tui.py` (real fixture files from the repo, no live runs).
3. **S3 report screen** — `ReportScreen(ModalScreen)` in `search_tui_app.py` (IndexMonitor skeleton: DataTable + footer + worker threads + `event.stop()`); comprehensive view is default; drill-down via detail pane; `r` rerun; `i` monitor.
4. **S4 wiring** — Makefile `search-tui` unchanged (no new deps); `pyproject` unchanged; `doc/procedures/search-tui.md` updated; README section updated with `t`/`T`/`r`/`i` bindings.
5. **S5 tests** — theme switch test (cycle + persist), report adapter tests (6 real report files × parser + `--all` flag), report screen smoke (mocked app).

## 4. Risks

- `rich.theme.Theme` tokens are string-keyed; theme maps must use keys that exist in rich's default style table (the current 16-token set is safe). New theme keys fail silently (already excepted at lines 373–374) — tests assert all THEME token keys are valid.
- Append-across-runs reports (`outputs/qa_report.md`, `outputs/advisory_report.md`, `outputs/integration_report.md`, `outputs/maint_report.md`, `outputs/perf_report.md`) have multiple blocks; `parse_gate_report`/`parse_perf_report` return chronological `RunBlock` list; default = most recent.
- `outputs/database_integrity_report.md` and `outputs/verify_notes_report.md` append per run
  since 2026-09-17 (multi-run like the gates); integrity returns the latest
  run's sections, verify's dict describes the latest run (rows overwrite).
- `outputs/database_integrity_report.md` is gitignored (`.gitignore`); the report screen warns ("not in git") and offers `make qa` path if absent.
- `parse_integrity_report` severity comes from heading parens (`## SECTION (severity)`), not `_CHECKS` registry (which has different casing like "Note Tags" vs "NOTE TAGS").
- Rerunning `make qa` from the TUI is advisory-only (writes the report but is read-only w.r.t. DB/code); binding is `R` + confirmation prompt.

## 5. Deferred

- Persistent theme CSS variables — CSS swapped wholesale per theme; per-token CSS customisation needs a CSS template engine.
- Historical trend chart — gate/perf reports are append-only; per-gate pass-rate or timing time-series is a future chart widget (needs `textual` chart or ASCII sparkline).
- Embedding-based note severity scoring — notes flagged by integrity checks could get a severity badge in the docs lane; gated on the severity-lane abstraction (S2).

## 6. Execution Results

### 6.1 Report consolidation — file manifest (2026-09-17, `tui_extends`)

All run reports now live in `outputs/` (gitignored wholesale, like
`bench_data`). Every writer creates the dir at write time
(`mkdir parents=True`). Stale root-level files deleted (fresh start —
these were local-only, no history worth migrating).

| Report | Writer | Change |
|--------|--------|--------|
| `outputs/qa_report.md` | `tests/run_gate_report.py` (`write_report`, `main`) | path + markdown table/tails (partway, kept); dead `markdown_table_lines` removed |
| `outputs/advisory_report.md` | same writer, gate `advisory` | path only |
| `outputs/integration_report.md` | same writer, gate `integration` | path only |
| `outputs/maint_report.md` | `helpers/maintenance/maint.py` (`REPORT_PATH`, `_write_report`) | path + full markdown migration (`# make maint[-full]`, `\| Step \|` table, `## <step> (FAILED)` tails); `FAIL (aborted)` verdict kept; fixed F841 (`n` unused) |
| `outputs/perf_report.md` | `tests/run_perf_benchmarks.py` (`REPORT`) | path + markdown table (partway, kept) |
| `outputs/database_integrity_report.md` | `helpers/misc/database_integrity_check.py` (`write_report_file`) | path + append + `#` header + `flock` (was `write_text` overwrite); content already markdown-shaped |
| `outputs/verify_notes_report.md` | `helpers/validators/verify_notes.py` (`generate_report`, `verify_all`) | path + append (was `"w"` overwrite) + trailing blank line; content already markdown-shaped (partway, kept) |
| `outputs/metrics_report.md` | `helpers/maintenance/enrich_from_yfinance.py` (`REPORT_PATH`, `write_report`) | path + full markdown rewrite (metric/industry/failure tables) + overwrite→append |
| `outputs/relations_report.md` | `helpers/maintenance/enrich_relations.py` (`REPORT_PATH`, `EMBEDDINGS_REPORT_PATH`, `write_report`, holders/embeddings/gf/finhub/terminal appends, `load_gf_targets`) | path + full markdown rewrite (`#` run blocks, `##` sections, pipe tables) + main writer overwrite→append; **reader updated** (`##` headers, `\| name \| ticker \|` rows, `- (unlisted)` rows) |

Supporting changes (same patch):

- `Makefile` — all help/target echoes point at `outputs/<name>.md` (mojo `bench_report.txt` untouched).
- `.gitignore` — `outputs/` wholesale ignore; stale per-file entries removed.
- Tests — `test_maint.py` (new `#` header asserts), `test_run_gate_report.py` (tmp names), `test_integration_maint_chain.py` (tmp name), `test_enrich_relations.py` (markdown fixture, `##` marker asserts, section-split asserts, tmp names). New: `test_generate_report_appends_across_runs` pattern already covered by maint equivalent; verify append covered by writer change + live runs.
- Docs — `doc/procedures/maintenance.md`, `doc/design/findata.md` (report moved out of the findata tree), `README.md` (2 spots), `doc/templates/report.md` (seed line).
- Adapters (`helpers/misc/search_tui.py`) — gate/perf split on `#` headers (maint included via `(gate|maint)`); headerless leading chunks skipped; integrity returns latest run's sections; verify dict is last-run-wins. Legacy dual-format parsing deliberately reverted (local-only files, fresh start).

Deliberately NOT moved:

- `findata/Misc/quote_triage_report.md`, `findata/Misc/_pending_triage_report.md` — pipeline state queues, not run reports.
- `Mojo/bench/bench_report.txt` — Mojo subsystem bench, separate lane.
- `metrics_report.txt` / `relations_report.txt` historical mentions in `doc/improvements/archive/*` and `doc/improvements/completed.md` — history, left alone.

Verification (2026-09-17): 149 tests (maint/gate/search_tui/verify/integration_maint) + 137 enrich tests pass; ruff/ty/md-lint clean; `make search-fresh APPLY=1` green; live `verify_notes` + `integrity_check` runs created `outputs/` with correct headers.

### 6.2 Sixth-lane wiring — executed (2026-09-17)

`LANES += ("reports",)` + `_run_reports` in `helpers/misc/search_tui.py`,
key `6` + placeholder in `helpers/misc/search_tui_app.py`, empty-query
allowed for the lane in `run_lane`, 7 lane tests in
`tests/test_search_tui.py` (overview / verb-latest / verb+text-all-runs /
bare-text / verify issues / unknown-verb-as-text / empty root).
Design points: `_REPORTS` registry (verb → `outputs/` file → kind);
line numbers located by re-scanning the file (adapter dataclasses
untouched, no churn in the partway tests); `parse_report_summary`
reused for overview rows; maint routed through the gate parser.

### 6.3 Theme system — executed (2026-09-17)

Deviated from §2.1's CSS-string swap: Textual 8.2 ships a built-in
theme registry, so each palette is `THEMES[name]` data
(`variables` → `textual.theme.Theme` kwargs, `rich` → preview tokens,
`description`) in `search_tui.py`, registered at mount and switched
via `self.theme` — the app's single CSS block resolves `$panel` /
`$accent` / … from the active theme with zero CSS churn. Rich preview
swaps pop-then-push (never stacks). Persistence:
`~/.config/search_tui/theme` (1 line; `memory/search_tui_theme`
fallback); `t` cycles, `T` opens `ThemeScreen` (IndexMonitor skeleton,
row keys = theme names, enter applies + persists). 4 theme tests
(cycle order, token-set uniformity, persist roundtrip, live pilot).

### 6.4 ReportScreen — executed (2026-09-17)

`ReportScreen(ModalScreen)` in `search_tui_app.py` (IndexMonitor
skeleton: DataTable + footer, `event.stop()` on enter): summary table
(item / severity / summary, severity-coloured cells) + markdown detail
pane. Gate/perf list runs most-recent-first (cap 5/report) with their
steps; integrity lists latest checks; verify lists latest issues.
Enter drills the row's file slice (run span, narrowed to the row's
section, cap 60 lines); `r` reruns the row's report via
`report_rerun_argv` in a worker then refills; `i` pushes the index
monitor. Entry: `V` (comprehensive, or preselected from a reports-lane
overview row — enter on overview rows opens it filtered). New
terminal-free helpers in `search_tui.py`: `report_run_spans`,
`report_rerun_argv`, `REPORT_NAMES`, `report_verb_for_path`. 3 tests
(spans/verb, rerun argv, live pilot smoke: open → 8 rows → drill →
   esc, no rerun).

### 6.5 DbScreen — executed (2026-09-17)

Native three-pane database modal per §2.6 item 8 (operator chose native
over harlequin suspend-launch). Terminal-free adapters in
`helpers/misc/search_tui.py`: `DB_STORES` registry (`research` →
`memory/research.db` SQLite, `sources` → `memory/data/sources.duckdb`
DuckDB), `db_schema` (tables/views + ordered columns, alphabetical),
`db_run` (never raises — errors, the 200-row cap, and the missing-file
case are `DbResult` data), `db_store_path` (call-time root resolution
so `REPO_ROOT` monkeypatching reaches the adapters — def-time defaults
froze the live root and sent the first pilot at the live DB). Safety:
per-query short-lived read-only connections (house `connect(read_only=True,
wal=False)` for SQLite, `duckdb.connect(read_only=True)`), SQLite
2 s progress-handler abort, all queries in a worker thread. `DbScreen`
in `search_tui_app.py` (ReportScreen skeleton: `Tree` + `Input` +
`DataTable` + status `Static` + `Footer`, `event.stop()` inherited from
modal scope): enter on a tree row runs a capped `SELECT`, enter in the
SQL box runs it, `s` switches stores, safety line states the posture.
Entry: `d` (`action_db_screen`). 7 tests (schema/run/cap/timeout/ro-write/
empty/unknown-store on tmp fixtures + hermetic pilot: tree → run →
switch → dismiss). Docs: `doc/procedures/search-tui.md` database
section; design history/future updated.

### 6.6 DbScreen v2 — executed (2026-09-17)

Operator trial notes, same patch: (1) SQL editor is now a
syntax-highlighted multiline `TextArea` (`language="sql"`; `tui` extra
gains `tree-sitter` + `tree-sitter-sql`, deptry DEP002-ignored like the
other runtime-loaded deps) — F5 runs, enter is a newline, Tab completes
the cursor-line token via pure `db_complete`; (2) per-store query
history (`~/.config/search_tui/db_history_<store>.txt`, cap 200,
dedupe-consecutive) with P/N recall (ctrl+p is Textual's command palette); (3) vim-style subset —
hjkl movement in tree/results, `y`/`Y` yank cell/row; (4) results —
`/` focuses a live fuzzy filter over loaded rows, `v` inspects the row
as `col: value` lines, working-set honesty kept (200-row cap, no fake
millions). 60 tests in-file (pure matcher/history/filter units +
pilots: F5 key path, live filter, inspect, recall, vim move).
