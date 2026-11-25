---
title: "Perf-harness revamp: run perf reliably on a shared desktop"
status: executed
filed: 2026-11-25
executed: "2026-11-25"
completed_md: "571"
area: tests/run_perf_benchmarks.py + tests/bench_* + helpers/core/parallelism.py
---

# Perf-harness revamp: run perf reliably on a shared desktop

`make perf` has been effectively unrunnable for weeks without box luck. Every
failure observed in the 2026-11-24/25 arc traces to one of: (a) dry-run legs
opening the live store writable and losing lock races to any concurrent
holder; (b) two perf runs (or a perf run and a gate) racing the same stores;
(c) ambient desktop load refusing the run or corrupting mid-run timings;
(d) single-run budget verdicts flipping on marginal noise; (e) legs whose
fan-out saturates all 4 cores, making the desktop unusable for the duration.

## S0 — groundwork (LANDED 2026-11-25, recorded here for the arc trail)

- Read-only reader flips: `helpers/core/parse_newsletter.py` (dry-run opens
  `read_only=not args.apply`), `helpers/maintenance/enrich_from_yfinance.py`
  (same shape), `helpers/graph/derive_co_mentions.py:208` (pure-reader path;
  `apply_edition_sets` stays the CLI-only writer). Both rc-failing perf legs
  verified green directly.
- Exclusive perf-run lock: `tests/run_perf_benchmarks.py` main() takes a
  non-blocking flock on `outputs/.perf_run.lock` (holder line written);
  a second concurrent run refuses with exit 3 naming the holder.
- Lock-conflict retry in `run_one`: one retry after 15 s when a leg dies with
  `Conflicting lock is held`, re-measured from zero; persistent conflict
  stays FAIL(rc). Writer legs (`sync_tags --apply`) survive transient holders.
- One-core-free fan-out: `helpers/core/parallelism.py` `default_workers()`
  (cores − 1, floor 1, `FINDATA_WORKER_CAP` env override) wired into the
  `extract_relations` CLI pool fan-out (was hardcoded `min(4, cpu_count)`)
  and `query.py` full-universe stats (was `jobs=4`).
  `FINDATA_DUCKDB_THREADS` env knob added to `memory_cap_config()` for
  perf-lane use (unset = DuckDB's own default everywhere).

## S1 — ephemeral store clone for writer legs

The only perf leg that writes the live store is `sync_tags --apply`. At run
start, copy `memory/research.duckdb` (+ `.wal` when present, ~290 MB,
seconds) to `outputs/.perf_clone/research.duckdb`; point the writer leg at
the clone via its existing `--db` flag; delete the clone at run end. Readers
keep the live store read-only (realism) with the S0 retry as backstop.

- Acceptance: the `sync_tags` leg completes green while an unrelated process
  holds a live-store lock; the clone is removed on both clean and failed
  runs; the measured wall is within noise of a live-store run.
- Non-goal: cloning for reader legs (RO opens coexist with holders already).

## S2 — built-in quiet-window wait

The ad-hoc pattern (background waiter polling load1 before `make perf`)
becomes a flag: after taking the run lock, the harness polls the existing
`_contention_reason()` probe every 30 s for up to `--wait-minutes N`
(default 5) before refusing; `--wait-minutes 0` reproduces today's instant
refusal. The Makefile target passes through untouched.

- Acceptance: a run launched under load waits, then proceeds (or refuses
  after the window naming the wait); two concurrent runs still serialize on
  the S0 lock, not on the wait loop.

## S3 — median-of-N for noise-prone legs

Single-run verdicts flip on marginal noise (`extract_relations` 5.03 s vs a
5.0 s budget in the 2026-11-25 run). Legs with a budget under 10 s run 3
repetitions; the median wall is judged against the budget; `--repeats 1`
restores the old behavior. perf.json rows gain a `reps` field.

- Acceptance: a leg over budget in exactly 1 of 3 reps passes on median;
  the report shows the median; total added wall for today's roster ≤ ~40 s.
- Non-goal: medians for the long legs (l1_kernels 75 s, db_engine_10k ~65 s)
  — their wall dwarfs ambient noise already.

## S4 — memory_query regression diagnosis

`memory_query` measured 7.58 s vs its 3.0 s budget (2.5×) in the
2026-11-25 run. Diagnose (profile the leg; candidates: index growth from
this arc's memory writes, embed lookups, conn warm path) and either fix the
regression or re-baseline with evidence in this proposal.

- Acceptance: root cause recorded with numbers; leg inside budget or the
  budget row carries the re-baseline rationale.

## Risks

- Clone staleness: `sync_tags` measures against run-start data, not live
  data — acceptable for a timing leg (the write shape is what is measured).
- Median-of-3 triples small-leg walls; bounded by the ≤ 10 s budget gate.
- `default_workers` slows the extract-relation integration lanes ~25% on the
  4-core desktop — accepted by the operator for desktop usability.

## Status log

- 2026-11-25 filed. S0 executed same day (see the arc trail:
  `doc/improvements/completed.md` will carry the landing entry when the
  proposal completes).
- 2026-11-25 S1 EXECUTED: `tests/run_perf_benchmarks.py` gained
  `_STORE_CLONE_DIR = outputs/.perf_clone`, `_prepare_store_clone()` (copies
  db + `.wal` sibling, `shutil.copy2`, drops a stale `.wal` when the source
  has none), `_drop_store_clone()` (rmtree), `_expand_clone()` (`{clone_db}`
  placeholder). The clone is taken after the run lock, before `t0` — no leg
  timing includes the copy. `sync_tags` leg argv is now
  `["helpers/core/sync_tags.py", "--apply", "--db", "{clone_db}"]`.
  Measured: clone copy 0.19 s; the leg runs green against the clone
  (2.05 s first-open WAL replay on a fresh clone vs 1.4 s live — S3's
  median amortizes the cold first rep). 47 pins.
- 2026-11-25 S2 EXECUTED: `--wait-minutes` (float, default 5.0). One start
  probe always runs (the old refusal; `--wait-minutes 0` = instant
  refusal); a contended start then polls `_contention_reason()` every 30 s
  while holding the run lock, up to the budget, then refuses with the last
  reason. Console prints the wait and the refusal lines.
- 2026-11-25 S3 EXECUTED: `--repeats` (int, default 3, 1 = old behavior).
  Legs with budget < `_MEDIAN_BUDGET_MAX` (10.0) run N reps; a SKIP rep
  returns immediately; rc-clean reps (OK and OVER_BUDGET) form the median
  set and the status is recomputed from the median wall; the tail rides the
  rep closest to the median; only an all-rc-fail leg surfaces a failure.
  Per-rep logs land as `perf.<label>#repK.log`. The 7-tuple results schema
  is UNCHANGED — `perf.json` rows do NOT gain a `reps` field (deviation
  from the design sketch; the per-rep logs carry the evidence instead).
- 2026-11-25 S4 EXECUTED (diagnosis, no code change): quiet-box reruns of
  the exact leg command are 1.63 / 1.68 / 1.66 s against the 3.0 s budget —
  the leg is healthy. The 5–9 s readings (7.58 s in the 08:44 run,
  8.95 s on 11-23) are box-state artifacts, not index growth or embed
  lookups; the leg is FTS-only with an RO sidecar conn. The systematic fix
  for this class is S2 (wait for a quiet window) + S3 (median-of-3
  absorbs a single-spike rep). Budget stays 3.0.
- Validation state: 55 pins in `tests/test_run_perf_benchmarks.py` (lock
  refusal + clone + wait + median + footer/skip contracts), ruff/format/ty
  clean on both files. A real-run validation with the new defaults was
  launched the same day; its rows land in the perf report and the gate-arc
  record.
