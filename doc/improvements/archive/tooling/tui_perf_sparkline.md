---
title: "Search TUI perf-trend sparkline — the deferred chart widget's dependency now ships"
status: executed
filed: "2026-10-18"
area: "helpers/misc"
executed: "2026-10-19"
completed_md: "422"
---

# Search TUI perf-trend sparkline — the deferred chart widget's dependency now ships

**Date:** 2026-10-18 · **Status:** PROPOSED

## 1. TL;DR

The search-TUI historical trend chart was deferred on a missing
dependency: "needs `textual` chart or ASCII sparkline"
(`search_tui_enhancements.md:287`). That blocker is gone — `textual
8.2.8` is the installed version and `from textual.widgets import
Sparkline` imports clean (verified 2026-10-18). The data source landed
after the deferral was written too: `gate_query timing --leg` already
serves per-leg history (e.g. `graph_link_prediction`: median 2.43 s,
p25 2.30, p75 2.50, max 2.57 across the last 5 runs), and the TUI's
reports lane already parses the append-only gate reports (#243). The
deferred item's own note (`:221`) names the use: a per-benchmark
time/budget ratio sparkline "turns the lane into a regression
detector."

## 2. Evidence (measured 2026-10-18)

```python
from textual.widgets import Sparkline  # OK — textual 8.2.8 installed
```

- Deferred blocker: `search_tui_enhancements.md` §5 line 287
  ("Historical trend chart … needs `textual` chart or ASCII sparkline").
- Named use case: §"Perf trend sparkline" at `:221` — per-benchmark
  time/budget ratio across runs as a sparkline column.
- Data: `helpers/misc/gate_query.py timing --leg <leg>` (run-indexed
  test facts retained since #285; incremental refresh 0.65 s median).
- Reports lane: the TUI already renders `outputs/*_report.md` runs
  (#243) — the sparkline rides the same lane, no new data plumbing.

## 3. Slices

- **S1 Perf lane sparkline** — per-leg `time/budget` ratio across the
  last N runs rendered as a `Sparkline` column in the reports/perf
  view; flat-line legs (no history) render a placeholder, not an empty
  widget.
- **S2 QA pass-rate history** — pass-rate sparkline per gate from the
  same run-indexed facts (`gate_query recent`).
- **S3 Tests** — widget construction from the normalized facts
  fixture; no live-gate dependency in tests (per-run artifacts are
  retained, so the fixture is a frozen copy).

## 4. Acceptance

- Targeted `tests/test_search_tui.py` additions; lane renders from the
  retained per-run artifacts with zero live-gate calls.
- No theme work (themes landed #243); widget inherits the active theme.

## 5. Risks & non-goals

- Textual widget API drift is the only dependency risk — pin behavior
  in tests, no version bump in this arc.
- Not a charting framework introduction; Sparkline only, ASCII fallback
  where the widget cannot render.

## 6. References

- `search_tui_enhancements.md` §1.1/§5/§6.1 (reports lane, deferral,
  manifest), completed.md #243 (lane), #285/#286 (retained run facts),
  `gate_query timing` (data source).

## 7. Execution results (2026-10-19)

Shipped as filed, on the last arc before the full gate run (TUI work was
parked to the end by the operator).

**S1 — perf trend.** `helpers/misc/search_tui.py` gained `_parse_seconds`
(`2.0s` → 2.0, `150ms` → 0.15, bare `31.5` → 31.5, `n/a` → None),
`sparkline_text(values, *, width=None)` (8 block glyphs, `·` for a
single sample, `""` for none), `perf_ratio_history(blocks, last_n=10)`
and `perf_sparkline_rows`. The report screen mounts a `#rep-spark`
`Vertical` strip (hidden when no perf report is in scope) holding up to
6 worst-latest benchmarks, each a `Static` caption plus a Textual
`Sparkline`; if the widget cannot be constructed the caption alone
remains — the ASCII fallback the proposal promised, not an empty strip.

**S2 — gate trend.** `gate_pass_rates(blocks)` reads the run's own score
line (`8/9 passed`) and falls back to counting `✓ OK` steps; an
unmeasurable run yields `None`, never `0.0` (a zero in a sparkline reads
as a catastrophic regression). Gate run rows now carry
`<pass>% · <glyph series up to that run>` alongside date/elapsed.

**Acceptance evidence.**
- `tests/test_search_tui.py` 75 → **87 passed** (~26.8s): budget
  parsing and glyph scaling, `·` for one sample, empty history,
  worst-first ordering, score-line-over-step-count, `None` for a
  headless run, and a live Textual test (`SearchApp` + `ReportScreen`
  over a synthetic two-run gate report) asserting `89%` with a real
  glyph on the newest row and `100%` with the placeholder on the single-
  sample row.
- Zero live-gate calls: every series is derived from the same parsed
  `RunBlock`s the tree already builds from `outputs/*_report.md`.
- Live production smoke: 26 benchmarks carry ratios
  (`route_graph_stats` peaks at 1.05× — a real overrun — latest 0.74×;
  `static_checks` 0.41–0.74; `shortest_path_bfs` 0.62–0.75);
  `outputs/qa_report.md` holds 120 runs, latest 1.0 / 0.75 / 0.83.

**Docs.** `doc/design/tui_design.md` §3 documents the trend contract and
why a single sample draws `·`; §9 records the arc; §10 drops the shipped
item. `search_tui_enhancements.md` §2.6 item 5 is marked SHIPPED and the
§5 historical-trend deferral is annotated DONE (kept, not deleted — the
deferral wording is the record of why it waited).
