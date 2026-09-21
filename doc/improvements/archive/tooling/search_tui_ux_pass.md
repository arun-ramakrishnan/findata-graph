---
title: "search_tui UX pass — busy indicators, live report panels, index-monitor feedback"
status: executed
filed: "2026-09-21"
executed: "2026-09-22"
completed_md: "265"
area: "helpers/misc"
---

# search_tui UX pass — busy indicators, live report panels, index-monitor feedback

**Date:** 2026-09-21 · **Status:** EXECUTED 2026-09-22 ·
**Area:** `helpers/misc/search_tui.py`,
`helpers/misc/search_tui_app.py`, `tests/test_search_tui.py`,
`doc/procedures/search-tui.md`

Umbrella for the search_tui UX scope: Parts A (busy indicators), B
(report panel) and C (screen indicators + theming) executed in one
working-tree arc, 2026-09-21/22.

## 1. Motivation

The TUI is functionally complete but silent about work in flight, and
its report panel rotted silently:

- A lane search (embedding lanes take seconds) leaves stale rows on
  screen with no signal anything is running — busy state was one
  static status string (`querying …`).
- The IndexMonitor showed frozen "checking…"/"rebuilding…" strings for
  operations with 900 s / 3600 s subprocess timeouts, no elapsed
  feedback, and `exclusive=False` workers let a double `R` press
  double-spawn parallel embedder rebuilds — the exact RAM/CPU spike
  the sequential design exists to avoid.
- The report panel went "unknown": the gate-latency arcs added
  `**Started:** … · **Elapsed:** Ns ·` between `**Generated:**` and
  `**Python:**` in qa/advisory/integration/maint headers;
  `_GATE_META_RE` demanded Python right after Generated, so every new
  run parsed with an empty timestamp → `unknown run` fallbacks.

Design language for the busy indicators is adopted from
[ratatui-spinner](https://github.com/ratatui/ratatui-spinner) (studied
2026-09-21): stateless, application-owned tick spinners whose frames
embed into table cells. The crate is Rust for Ratatui 0.30 and
unpublished; this repo's TUI is Textual 8.2.8 + rich 15, so the pattern
is ported natively — zero new dependencies.

## 2. Evidence (measured 2026-09-21, this box)

| Check | Result | Verdict |
|---|---|---|
| `Widget.loading` in textual 8.2.8 | present — auto-mounted `LoadingIndicator` overlay | A1 is one property set |
| rich 15 spinner presets | 73; `dots` == ratatui-spinner `FluxFrames::CLASSIC` exactly (10/10) | frame tuple ports verbatim |
| `rg` over `search_tui*.py` pre-arc | 0 indicator surface (status text only) | baseline |
| qa/advisory last runs pre-fix | `timestamp=''` → "unknown run"; maint (old header) parsed | B1 baseline |
| post-fix parse of live files | qa `14:07:25 · 8 jobs · 126.1s`; advisory `147.5s`; perf `35.4s`; maint old-format `None` | B1 verified |
| `run_test` headless timers | `set_interval` fires during `pilot.pause` | animation testable |

## 3. Design — slice ledger

| Slice | Scope | Status |
|---|---|---|
| A1 | query/call-chain busy overlay (`_w_table.loading`, `_gen`-guarded) | executed |
| A2 | IndexMonitor row spinners (`FLUX_CLASSIC` port, `set_interval` clock, `_set_state` claim/release funnel) | executed |
| A3 | live `#idx-note` (phase + monotonic elapsed; hint restored idle) | executed |
| A4 | busy-guard `_claim` on c/C/r/R (double-press no-op + notify) | executed |
| A5 | tests + procedure doc lines | executed |
| B1 | timed-header parse fix (`_GATE_META_RE` bridge + `_ELAPSED_RE`) and elapsed surfaced in the report main table + reports lane | executed |
| B2 | worktree report copies listed separately (`qa@graph_algos` display names, compact where labels `qa:6098` / `graph_algos/qa:6098`, run elapsed fills the score column); `V` scopes to the selected row's file | executed |
| B3 | report panel as a run tree: runs as collapsed top-level nodes (enter expands/drills), steps/issues/checks nested under their run; rerun guarded on worktree copies | executed |
| B4 | verify + integrity run histories: multi-run parsing (`parse_verify_runs`, `parse_integrity_runs` with ISO-T normalization) and compact verdict labels — was latest-run-only | executed |
| B5 | writer append-at-tail pinned by tests: verify, integrity, perf (writer extracted from `main`), metrics; gate runner already covered (incl. concurrent double-append) | executed |
| C1 | DbScreen long-query indicator — results-grid loading overlay (the A1 pattern won over a ProgressBar: one busy language everywhere) | executed |
| C2 | ReportScreen `r` rerun feedback — loading overlay on the run tree, cleared on land/crash | executed |
| C3 | theme-aware colors — severity styles resolve the active Textual theme's error/warning/success tokens (green = pass; the loading overlay already follows $primary/$boost) | executed |
| C4 | live-testing fixes: enter on a run node double-toggled (Textual auto_expand + manual toggle = the flash) — manual toggle removed; integrity colors followed the DECLARED level (everything red) — `integrity_verdict` colors by actual `errors=/warnings=` counters | executed |

Part A mechanics: `_set_busy(on)` toggles the LoadingIndicator overlay
on the results table at query/chain start; `_apply_results` clears it
after the `gen != self._gen` guard (stale workers never touch the newer
query's state). The monitor owns one animation clock
(`set_interval(1/12)`, idle ticks early-return); `_busy` maps index →
phase, `_spin` rewrites busy cells as `{frame} {phase}…`; `_claim`
marks names synchronously so two presses in one pump cycle cannot
double-spawn.

Part B mechanics: `_GATE_META_RE` bridges `.*?` to an optional
`**Python:**`/`jobs=` group (old and new shapes parse); `_ELAPSED_RE`
captures `**Elapsed:** Ns`; `RunBlock.elapsed` (default None) carries
it; ReportScreen run headers show `── qa · <ts> · 126s ──` and
reports-lane sections carry `· 126s`.

Worktree outputs (B2): every `outputs/wt/<name>/outputs/*.md` copy is
discovered by `report_files()` and listed as its own source — the
where column and `@worktree` display names differentiate, and `V` on a
row scopes the panel to that file. Rerun stays main-repo-only (running
`make qa` here would regenerate the main copy, not the worktree's).

Alternatives considered: whole-table overlay on `#idx-table` (hides
per-row state); indeterminate ProgressBar on the main search (heavy
for a one-line status); PyO3-wrapping ratatui-spinner (wrong runtime);
rich `Spinner` renderable (no gain over the ported tuple); making
`_REPORTS` recursive over `outputs/wt/**` (mixes worktree runs into
the main-run history).

## 4. Acceptance criteria & shakedown

A (met in working tree):

1. During a slow monkeypatched lane query, `#results.loading` True
   mid-flight, False after `_apply_results`; stale-gen applies do not
   clear the newer busy state.
2. Busy monitor rows match `^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] (queued|checking|rebuilding)…`
   and settle to verdicts; `#idx-note` restores its hint when idle.
3. Double `R` (and `c` during a rebuild) invokes each rebuild argv at
   most once; nothing-pending presses notify.
4. `pytest tests/test_search_tui.py` green (66 tests at time of
   writing).

B (met in working tree):

5. New-format headers parse: timestamp + jobs + elapsed captured
   (qa/advisory/integration/maint + perf); old-format headers still
   parse with `elapsed is None`.
6. Elapsed surfaces in the report main table run headers and the
   reports lane sections.

C (on take): per-slice criteria defined with the slice; C1/C2 follow
the A1 overlay pattern unless measurement says otherwise.

No eval-gate bullet applies: presentation only; no roster, crosswalk,
hierarchy, or extractor semantics change (ontology_governance S2
scope).

| Projected outcome | Before | After |
|---|---|---|
| search busy signal | static text | table overlay + text |
| index ops feedback | frozen strings | animated rows + elapsed note |
| double-`R` rebuilds | parallel embedder spawn | guarded, notify |
| report panel runs | "unknown run" on new headers | ts + jobs + elapsed shown |
| new dependencies | 0 | 0 |

## 5. Risks

- **Repaint cost** (A2) — idle ticks early-return; ≤3 rows at 12 fps.
- **`LoadingIndicator` swallows input** by design — table-only overlay;
  focus stays on the query line; pinned by test.
- **Worker cancellation stranding `loading`** — generation guard owns
  the flag; covered by the stale-gen test.
- **Report format drift again** (B1) — the regex now tolerates extra
  `· **field:** value` segments between Generated and Python; a
  producer that moves Elapsed after Python would still parse
  (timestamp anchors the line). Parser/producer drift is inherent;
  the fixture tests pin both shapes.

## 6. Non-goals

Rerunning worktree report copies from the main repo (regenerate them in
their worktree); any Rust front-end or ratatui crate dependency;
theme/CSS rework beyond default overlay styling. (Report writers'
append behavior is now pinned by tests — writer semantics unchanged.)

## Appendix — raw measurement log

| Run | Command / check | Result | Notes |
|---|---|---|---|
| 2026-09-21 | textual 8.2.8 introspection | `Widget.loading` present; `ProgressBar` total `float \| None` | overlay path, no new dep |
| 2026-09-21 | rich `SPINNERS` vs `FluxFrames` | `dots` == CLASSIC exact | port target: verbatim tuple |
| 2026-09-21 | clone/read ratatui-spinner v0.4.18 | stateless tick widgets; `Into<Text>` cells; ratatui 0.30; unpublished | pattern source only |
| 2026-09-21 | pre-fix parse of `outputs/*.md` | qa/advisory `timestamp=''`; maint ok | B1 baseline |
| 2026-09-21 | post-fix parse of live files | qa `14:07:25 · jobs=8 · 126.1s`; advisory `147.5s`; perf `35.4s` | B1 verified |
| 2026-09-21 | targeted suite | 66/66 green; ruff + ty clean on touched files | A5 + B1 tests |
