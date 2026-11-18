---
title: "Signal-sweep forward-return memoization — kill a 42x redundant anchor walk"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "541"
area: tooling
---

# Signal-sweep forward-return memoization

## 0. Finding

`helpers/research/signal_sweep.py::sweep` took **55.077 s** on the live
store (2026-11-18 baseline, `bench_data/hunt_python_lanes_2026-11-18.md`).
cProfile (`/mnt/data/tmp/opencode/profile_sweep.py`) attributed it to:

| call | ncalls | cumtime | share |
|---|---|---|---|
| `_forward_return` | 2,749,949 | 36.0 s | 65% |
| `edges_as_of` | 36 | 10.9 s | 20% |
| `load_prices` | 1 | 4.7 s | 9% |
| `_spearman` | 1,728 | 1.8 s | 3% |

`_forward_return(ticker, t, h)` depends ONLY on those three arguments,
yet it is recomputed for every mode (`all`/`dated`), every horizon, every
sector peer, and both the raw and abnormal targets. Distinct keys are
~65k — a **~42× redundancy**. Separately, `edges_as_of(con, t)` was called
twice per quarter (once per mode) though mode is a post-filter over the
same as-of set.

The earlier assumption that `_spearman`'s pure-Python ranking was the
bottleneck was WRONG (1.8 s, 3%) — profiling corrected it before any code
changed. This is a numpy-first-style fix: no new dependency, no kernel,
just eliminate repeated work.

## 1. Change (targeted, perf-only)

- `_forward_return(prices, idx_map, ticker, t, h_bars, cache=None)` —
  optional memo dict keyed by `(ticker, t, h_bars)`. Anchor walk
  (`max(earlier)`) folds in for free. Default `None` keeps the uncached
  behavior for any external caller.
- `_abnormal(..., fwd_cache=None)` threads the cache through to both the
  subject and peer lookups.
- `sweep` allocates ONE `fwd_cache` for the whole run and one
  `quarter_edges = edges_as_of(con, t)` per quarter, reusing the latter
  for both modes (dated mode still applies its `valid_from <= t` filter).

No query-visible semantics change: the cache is internal to one sweep
call; identical inputs produce identical outputs. **No eval-gate bullet
required** (perf-only, no query-visible semantics).

## 2. Verification

- Deterministic baseline captured from the PRE-change code
  (`/mnt/data/tmp/opencode/sweep_baseline.json`; 55.077 s, 90 summary
  rows, 36 n_by_date rows).
- Post-change (`/mnt/data/tmp/opencode/verify_sweep.py`): **18.903 s,
  2.91× faster, 0 parity diffs** across all 90 summary rows and 36 date
  rows (floats compared within 1e-12).
- `tests/test_signal_sweep.py` (4 tests): cache == uncached across
  hit/miss/out-of-window/missing-ticker cases; cache reuse returns the
  memoized value even after source mutation; `_abnormal` cached ==
  uncached; `_smoke()` closed-form values. 4 passed.
- ruff check + format clean.

## 3. Wiring — Make targets + smoke check (R14 close-out)

The goldfish review (`outputs/reviews/review_report_e3147ef3c..4b6f50912.md:54`,
R14 in `archive/tooling/range_review_followups.md:152`) recorded
"signal_sweep.py zero callers — smoke test or Make target, or park with a
verdict"; it was parked as recorded. This change closes that nit by
wiring two entry points (closes R14's signal_sweep bullet):

- `helpers/research/signal_sweep.py smoke` — new subcommand running
  `_smoke()`: a hermetic self-check of the math chain (bar index →
  cached forward return → abnormal target → Spearman) on fixed synthetic
  data with closed-form expected values (fwd 0.2/0.5, abnormal 0.1,
  Spearman ±1, n<8 → None). No live DB, deterministic, exits 0/1.
- `make signal-sweep-smoke` — runs the above (fast, no live DB).
- `make signal-sweep ARGS="--horizons 4,13"` — full report-only sweep
  (the standing quarterly instrument; ARGS forwarded).
- Both added to `.PHONY` and the `help` listing; recipes verified with
  `make -n` and `make signal-sweep-smoke` (rc=0). No query-visible
  semantics change, no gate.

## 4. Posture / risks

- The cache is per-`sweep`-call and keyed on the three true inputs; it
  cannot go stale within a call because prices and dates are immutable
  there. A caller reusing `prices` across calls must pass a fresh cache
  (the default `None` means no cross-call state).
- `_forward_return` is private (no external importers found via ripgrep)
  but the new `cache` param is appended and defaulted, so it is
  backward-compatible.
- `edges_as_of` reuse is exact: for `mode == "all"` the full set was used
  before; for `mode == "dated"` the same list comprehension now runs over
  the cached quarter set. No change in filtered rows.
- Gates parked (operator m01133) — targeted tests + ruff only, no
  `make qa`/`make perf`.

## 5. Outcome (2026-11-18, executed)

**Shipped as proposed.** `_forward_return(..., cache=None)` memoizes by
`(ticker, t, h_bars)` — the three true inputs — with the anchor walk
folding in for free; `_abnormal(..., fwd_cache=None)` threads it through
subject and peer lookups; `sweep` allocates ONE `fwd_cache` for the run
and ONE `quarter_edges = edges_as_of(con, t)` per quarter reused by both
modes.

**Measured:** the proposal's post-change figure was 18.903 s vs the
55.077 s baseline (2.91×, 0 parity diffs across 90 summary rows and 36
date rows). Confirmed live at landing: `signal_sweep.py sweep --horizons
4,13` → **WALL 17.10 s**, i.e. 3.2× on that run.

**R14 closed.** `signal_sweep.py` had zero callers, parked as recorded in
the goldfish review. It now has three entry points: the `smoke`
subcommand (`_smoke()` — hermetic, fixed synthetic data, closed-form
expectations fwd 0.2/0.5, abnormal 0.1, Spearman ±1, n<8 → None),
`make signal-sweep-smoke` (fast, no live DB) and
`make signal-sweep ARGS="--horizons 4,13"` (the standing quarterly
instrument). Both in `.PHONY` and the `help` listing.

**Verification:** `tests/test_signal_sweep.py` 4 passed — cache ==
uncached across hit/miss/out-of-window/missing-ticker, cache reuse
returns the memoized value even after source mutation, `_abnormal`
cached == uncached, and the smoke closed-form values. `smoke` exits 0
with the expected table. Gates parked per operator m01133 — targeted
tests + ruff only.
