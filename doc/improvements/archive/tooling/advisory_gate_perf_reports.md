---
title: "Advisory gate perf + report format — tier-walk longest-chains, index-free chain views, loadgroup xdist, ended/elapsed timestamps in every report"
status: executed
filed: "2026-09-20"
executed: 2026-09-20
completed_md: "255"
area: "helpers/graph/stats.py, tests/run_gate_report.py, Makefile, gate-report writers (perf/maint/verify_notes/integrity), tests/test_analytics.py"
---

# Advisory gate perf + report format

**Date:** 2026-09-20 · **Status:** EXECUTED (retrospective — operator-directed
turn; the work predates this doc, filed at archival per the completed.md
convention that every arc carries a proposal reference).

## 1. Problem

`make advisory` live-invariants regressed 60–86s → 95–226s wall (variance
across runs, Sep 17→19 logs). Diagnosis, fully quantified:

- `print_stats()` render profiled at 61.4s solo; **62.6s of 65.1s inside
  `longest_chains`**, 61.6s of that in its own Python frame.
- The top-K distant-pair selection materialised **all n² ordered pairs**
  (`np.argsort` over the full rank matrix + tuple comprehension, keeping 5):
  6.29M tuples at n=2,508.
- idx_fill (#253) grew the edge-touched universe **1,722 → 2,508** (+46%
  nodes ⇒ +112% pairs, quadratic) and polluted the chains semantically:
  the #1 "chain" hopped `usa -[listed_in]-> Adobe` and
  `NIFTY SME EMERGE -[listed_on_index]-> <constituent>`.
- Aggravator: the module's `pytest.mark.xdist_group("graph_stats_render")`
  was **inert** — nothing passed `--dist=loadgroup`, so 3 workers each paid
  a full render and xdist scheduling luck set the wall (95–226s).
- `make qa` was NOT affected (its pytest runs `-m "not live"`); its noisy
  76–139s spread is machine-load variance.

## 2. Fixes

1. **Tier-walk pair selection** (`stats.py`): unweighted BFS ⇒ integer
   distances; walk `d = diameter … 1`, lift each tier's pairs with one
   numpy scan (np.nonzero row-major = the old argsort's within-tier order;
   exact mode scans the canonical a<b triangle — both orientations of a
   pair always receive the same verdict in the greedy node-disjoint
   selection). No candidate list beyond the walked tiers.
2. **`listed_on_index` excluded from BOTH chain views** via
   `_CHAIN_FORBIDDEN = frozenset(_CHAINS_EXCLUDE) | {"listed_on_index"}`;
   the node universe query drops it too. Universe 2,508 → 1,735. Same
   doctrine as `EDGE_TYPES_EXCLUDED_FROM_CENTRALITY` (#254) and the
   louvain amendment: index membership is not a relationship.
3. **`--dist=loadgroup`** on the Makefile `live-invariants` target and the
   advisory gate's live-invariants step — the existing xdist_group markers
   now actually group.

Effect: render **61s → ~1.0s** (59×); `make live-invariants` **227 tests in
60.4s stable** (was 95–226s). `longest_chains` output identical in shape
(16 lines; components/diameter/median recomputed on the smaller universe;
chains relationship-driven).

## 3. Report format (operator directive: ending + elapsed time everywhere, console summary into the reports)

- `run_gate_report.py`: meta line gains `**Started:**` + `**Elapsed:**`
  (`**Generated:**` kept — search_tui's `_PERF_META_RE` parses it); the
  perf-style pass/fail console table is embedded verbatim in every report
  block inside a ```text fence.
- Same Started/Ended/Elapsed treatment for the other report writers:
  `run_perf_benchmarks.py` (perf_report), `verify_notes.py`
  (verify_notes_report), `database_integrity_check.py` (integrity report;
  `ended`/`elapsed_s` into the results dict, header via `.get` guards),
  `maint.py` (maint_report; kwargs with back-compat defaults).
  `enrich_from_yfinance.py` already logged Duration.

## 4. Collateral fixes surfaced by the first post-arc `make qa`

- **Day-bomb test**: `test_analytics.py` `t_snap` hardcoded
  `last_updated = 2026-08-20`; it crossed the 30-day staleness boundary on
  2026-09-20 and flipped S1 into `stale>30d == 2`. Fixture now computes
  fresh (−5d) / stale (−250d) dates relative to `date.today()`.
- **Env trap**: `textual` lives in the `tui` extra but
  `helpers/misc/search_tui.py` imports it — `uv sync --extra dev --extra
  mojo` alone makes the ty gate fail (unresolved-import) and 6 TUI tests
  skip. Green gate needs `--extra tui` too.
- **Move residue**: stale `__pycache__` bytecode kept printing pre-move
  absolute paths in pytest tracebacks/skip lines; purged repo-wide
  (cosmetic).

## 5. Gates

- `make qa` **10/10 PASS** (2026-09-20 09:48): 3296 passed / 3 pix2text
  skips / 0 failures; types, static_checks, verify_notes, integrity,
  snapshot all green. Full gate run by the operator.
- `make live-invariants` 227 passed in 60.4s; integration gate 50.7s PASS.
- ruff check/format clean; lint gates 4/4; 224 targeted tests.

## 6. Non-goals / notes

- `graph_centrality_persistent_cache.md` stays live (proposed).
- `max_exact=3000` sample cap untouched — with index edges out of the
  universe, n=1,735 sits far below it again.
- Invocation trap for the gate runner: it must run under the project venv
  python (make does the PATH export); bare `python3` resolves to
  `~/.local/bin/python3` and every step fails with "No module named
  pytest" (one such FAIL block lives in outputs/integration_report.md,
  09:35:20 — benign).
