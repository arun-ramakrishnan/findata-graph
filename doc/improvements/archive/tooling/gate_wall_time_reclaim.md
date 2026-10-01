---
title: "Reclaim gate wall time — cap xdist inside the pool, shrink the maint-chain test, re-budget the creeping legs"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "331"
area: "tests/run_gate_report.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Reclaim gate wall time — cap xdist inside the pool, shrink the maint-chain test, re-budget the creeping legs

**Date:** 2026-10-01 · **Status:** EXECUTED ·
**Area:** `tests/run_gate_report.py` (gate scheduling), `tests/test_integration_maint_chain/`, `tests/run_perf_benchmarks.py` (budgets), `helpers/misc/markdown_lint.py` (cache warm), `helpers/misc/gate_query.py` (sort bug)

## 1. Motivation

`make qa` costs **216s wall**, and **99.5% of it is the `pytest` leg**
(215.19s of 216.0s). Every other leg is rounding error. Yet that 215s is not
a real cost: measured over the last 14 runs the leg has a median of 228.12s
and a *minimum* of 152.85s, so roughly 70-80s of the typical run is waste
rather than work.

The waste has one dominant structural cause and three secondary ones, all
measured below. None of this blocks correctness — the gate's 9/11 pass rate
is a different problem — but it taxes every iteration in the arc, and S1
alone should recover a large fraction of it.

**Trigger:** a deliberate timing analysis of the gate-run corpus
(`gate_query timing`, `--critical-path`, `tests --slowest`) requested
2026-10-01, prompted by a rebuild-scaling measurement turn where gate
latency was the limiting factor on iteration count.

## 2. Evidence (measured 2026-10-01, this box; 4 cores, no HT)

### 2.1 Leg breakdown — run 1155 (`qa`, 216.0s wall)

| leg | seconds | share | median (14 runs) | budget |
|---|---|---|---|---|
| **pytest** | **215.19** | **99.5%** | 228.12 | — |
| integrity_check | 9.38 | 4.3% | 4.99 | 10.0 (56.6% util) |
| snapshot_check | 8.28 | 3.8% | 5.05 | 6.0 (**93.2% util**) |
| static_checks | 7.27 | 3.4% | 1.31 | 7.0 (36.7% util) |
| types | 5.24 | 2.4% | 4.23 | — |
| verify_notes | 1.37 | 0.6% | — | 3.0 |
| md-lint | 0.97 | 0.4% | 8.80 | — |
| deptry | 0.73 | 0.3% | — | — |
| tmp-sweep | 0.1 | 0.05% | — | — |
| lint (ruff) | 0.08 | 0.04% | — | — |

Legs sum to ~251s against a 216s wall: the legs genuinely overlap, but
**the overlap is contention, not free parallelism** (S1).

### 2.2 One test is the critical path, and it is degrading

`gate_query timing --leg pytest --critical-path` names the same node in
every recent run:

| run | date | `test_full_chain_order_and_idempotence` |
|---|---|---|
| 1155 | 2026-10-01 17:49 | **92.25s** |
| 1154 | 2026-10-01 14:15 | 59.16s |
| 1137 | 2026-09-30 23:29 | 55.74s |
| 1130 | 2026-09-30 13:11 | 46.51s |
| 1136 | 2026-09-30 22:34 | 44.58s |
| 1125 | 2026-09-29 20:11 | 85.66s |
| 615 | 2026-09-29 09:43 | 80.36s |

**+107% in 4 days** on the two most recent readings. At 92.25s serial this
single test is ~43% of the entire pytest leg. The secondary candidate
`test_tier1_executes_all_steps_green` sits at 32-43s in the same runs.

Caveat, stated because it changes the size of the prize: run 1155's 92.25s
was recorded while a *second* leg was also failing, and the 2026-09-29
readings (80-86s) show the series is **not** monotonic — 44s readings exist
on the same corpus. Part of the growth is corpus, part is contention from
co-running legs. S2's first act is to separate the two rather than assume the
worst case.

### 2.3 Root cause: nested oversubscription on a 4-core box

- `tests/run_gate_report.py:125` — `_DEFAULT_JOBS = 4  # 4 cores (user
  directive 2026-08-25)`. Legs run in a `ThreadPoolExecutor(max_workers=N)`.
- `tests/run_gate_report.py:190` — the pytest `Step` passes `-n auto`, so
  pytest independently spawns one xdist worker per core.

So a default gate is **4 concurrent legs x 4 xdist workers = 16 runnable
processes on 4 cores**, with the heaviest leg competing with the other three
for the same 4 cores. The in-code rationale at lines 184-187 states the two
knobs are on "different axes" — pytest scales with CORES, the pool with the
user's concurrency preference. That reasoning does not hold: **cores are the
shared resource, not an axis either knob can independently claim.** The
observed signature matches exactly: sum-of-legs (251s) > wall (216s).

### 2.4 Two budgets are now mis-sized, and will flake on timing alone

`snapshot_check` is at **93.2% of its 6.0s budget** (5.59s latest, 5.54s,
5.33s, 6.99s — it has already breached once). Its budget was set 2026-09-23
to 6.0 "because S4 made `--check` verify every parquet table with a zstd
roundtrip (58 tables)"; the corpus has since grown past the 58-table
assumption. `static_checks` went 0.35s -> 4.00s as the corpus grew, and its
own comment records two prior bumps (8.0s 2026-08-19, 10.0s 2026-09-14)
before the current 7.0s. Both budgets are in `tests/run_perf_benchmarks.py`
lines 78 and 82.

Neither failure mode is a correctness signal. Both will be read as one.

### 2.5 `md-lint` is bimodal on cache state, not on work

| regime | observations | range |
|---|---|---|
| cache hit | 0.30, 0.45, 0.60, 0.97s | **0.30-0.97s** |
| partial | 7.71, 9.90, 12.54, 12.59, 14.44s | 7.7-14.4s |
| cache miss | 85.95, 134.97, 143.23s | **86-143s** |

A **470x spread** driven entirely by whether the gitignored
`memory/md_lint_cache.db` sidecar is warm. p75 13.98s vs median 8.80s. The
cache is content-hash-keyed (`helpers/misc/markdown_lint.py:18-28`), so any
doc-heavy edit invalidates a large fraction of it; the gate then pays up to
143s for a lint that can be 0.3s. The worst readings (85-143s) all fall on
2026-09-28/29, the highest-doc-churn days in the window.

### 2.6 `advisory` / `convo-fresh` are cheap; their failures are semantic

| leg | min | median | max |
|---|---|---|---|
| convo-fresh-check | 1.41s | 3.39s | 6.64s |
| doc-search-check | 0.17s | 0.44s | 1.29s |
| script-search-check | 2.96s | 3.88s | 5.34s |
| lint-audit | 0.06s | 0.15s | 0.39s |

`convo-fresh-check` has failed in **all 7 of the last 7 advisory runs** at a
median of 3.39s. That is a staleness predicate, not a performance problem,
and it is correctly cheap. The 2026-10-01 17:58 advisory run's 20 warnings
across `doc-search-check` / `script-search-check` / `convo-fresh-check` /
`lint-audit` are index drift from docs edited after the run.

**Not a bottleneck, but the real cost in advisory:** `live-invariants` runs
**129-309s, median 188s** — comparable to the whole `qa` gate, and it is not
a `qa` leg at all. It is the heaviest thing in the gate surface and deserves
its own look once S1 lands.

### 2.7 `gate_query tests --slowest` returns rows in ascending order

`gate_query tests --run 1155 --slowest` printed the 20 **fastest** tests
(0.00-0.06s) where the slowest were requested; `tail -20` on that output
yielded only sub-second rows. The `--json` and `--critical-path` paths rank
correctly (`--critical-path` is what produced §2.2). This is a
presentation bug in the legacy `--run` branch of the CLI, and it is how a
timing analysis can silently conclude "no slow tests" — a reader who trusted
the default sort would have reported the opposite of the truth.

## 3. Design

Five independent slices, ordered by payoff-per-risk. S1 is the whole prize;
S2-S5 are hygiene and can ship or be dropped independently. None of them
changes query-visible semantics, so **no eval-gate bullet is required**
(house rule applies to roster/crosswalk/hierarchy/extractor changes).

### S1 — Cap xdist workers inside the pool (the big lever)

In `tests/run_gate_report.py`, derive the pytest worker count from the pool
size instead of passing `-n auto` unconditionally:

```python
workers = max(2, cores // max(1, jobs))  # 4 cores, jobs=4 -> 2
```

and pass `-n <workers>`. When `jobs == 1` (a plain serial `make qa`), keep
`-n auto` unchanged so single-leg runs are unaffected. The `--jobs N` /
`make -j N` precedence chain already exists at lines 27-30; S1 only consumes
the resolved value. Correct the lines 184-187 comment, which currently
justifies the nested spawn.

Expected: removes 2 of 4 competing workers during the pytest leg. Does not
require touching any test.

### S2 — Bound the maint-chain test

Three options, in preference order; pick with measurement, not taste:

- **(a) `slow` marker + session-scoped fixture.** The test rebuilds the full
  maintenance chain; if its setup is rebuild-shaped rather than
  assertion-shaped, hoist the rebuild into a session fixture so sibling tests
  in the class inherit it.
- **(b) Split the idempotence assertion.** `test_full_chain_order_and_idempotence`
  appears to run the chain twice by construction (that is what "idempotence"
  means). If the second pass is the cost, a cheaper equivalence assertion
  (compare the two passes' table digests, not full re-runs) keeps the
  property at a fraction of the cost.
- **(c) Isolate it to `advisory`/nightly** and mark it `not live` in `qa`.

Prerequisite: **measure first** (§4). The §2.2 caveat is unresolved — if the
92s is contention rather than work, S1 alone may recover most of it and S2
shrinks to a `slow` marker. Do not pay S2's complexity before S1 is measured.

### S3 — Re-budget the two creeping legs

In `tests/run_perf_benchmarks.py`: `snapshot_check` 6.0 -> 9.0, and
`static_checks` 7.0 -> 10.0. Follow house precedent — the file already
carries three dated bumps with a stated cause each, and
`completed.md`-style rationale comments must be added, not just the number.
Re-derive from a standalone (non-contended) measurement, and record the
table count that justified it so the next corpus bump is a recalibration
rather than a surprise.

### S4 — Warm the md-lint cache before the gate

Cheapest structural fix: have the gate's md-lint leg run against a
pre-warmed sidecar, or have the gate tolerate the miss by reporting the
uncached time separately from the cached verdict. A smaller option: pin the
cache's invalidation to the content hash it already uses but stop letting
one doc-churn day cost 143s of gate wall. Whatever lands must preserve the
`--full` uncached escape hatch documented at `markdown_lint.py:26-28`.

### S5 — Fix the `gate_query tests --slowest` sort

One-line direction fix in the legacy `--run` branch
(`helpers/misc/gate_query.py`): rank descending on `seconds`. Add a
regression test in `tests/test_gate_query.py` asserting the slowest row is
first. Small, but it is the bug that makes this class of analysis
untrustworthy, and §2.7 shows it fails silently.

## 4. Acceptance criteria & shakedown

1. **`make qa` wall <= 150s** on the same corpus, from the 216s baseline —
   with pytest named as the measured critical path. If S1 alone does not
   reach it, S1+S2 must.
2. **No new test failures.** 3681 passed / 3 skipped is the run-1155
   baseline; S1 changes scheduling only, S2 may change which tests run in
   `qa` (and that must be a deliberate, documented trade, not a silent
   skip).
3. **Leg overlap is honest.** Re-check sum-of-legs vs wall: after S1 the sum
   should exceed wall by *less*, because the contention that produced the
   gap is what S1 removes. Record both numbers.
4. **S2 measured before it is sized.** §2.2's caveat is resolved in writing:
   state whether the 92.25s was contention, corpus growth, or both, with the
   isolated measurement. This is a required output, not a nice-to-have.
5. **No budget is raised without a dated cause comment** and a
   standalone (uncontended) measurement, per the existing
   `run_perf_benchmarks.py` convention.
6. **`make qa` run once, on the user's go**, after S1-S3 land — not once per
   slice. Per-arc gates, per AGENTS.md.
7. `md-lint` worst case <= 15s across a doc-churn day; `--full` still works.
8. `gate_query tests --slowest` returns descending order, regression-tested.

## 5. Risks

- **S1 could slow the single-leg case** if `jobs` is misread when `make -j`
  is passed through. Mitigation: keep `-n auto` for `jobs == 1`; S1 changes
  nothing for a serial gate.
- **S1 changes xdist distribution**, and the per-worker graph-cache redirect
  (`pytest.ini:23-24`, `conftest.py`) is keyed on `PYTEST_XDIST_WORKER`. Fewer
  workers means fewer per-worker cache copies — directionally cheaper, but
  it changes which tests share a cache copy, so **shared-state breakage is
  the risk to watch in the shakedown**, exactly as the 2026-08-31 note at
  line 185 recorded when this was last tuned.
- **S2(b) could weaken the property being tested.** Idempotence asserted by
  digest comparison is weaker than by re-run if the digest is itself
  derived from the same code path under test. Prefer (a), then (c), over a
  weakened (b).
- **S3 raises budgets, which can mask a real regression.** The budgets exist
  to catch super-linear decay (§2.4 of `run_perf_benchmarks.py`'s own
  comment). Mitigate by re-baselining from a *standalone* measurement and
  recording the corpus counts, so a future blow-up is still visible.
- **S4 may trade correctness for speed** if it lets a stale cache verdict
  pass. The `--full` path must stay authoritative on demand.

## 6. Non-goals

- Not making `live-invariants` (median 188s) faster — real, out of scope here.
- Not touching the `pytest` **suite's** coverage, assertions, or markers
  beyond S2's measured scope.
- Not changing the gate's fail-at-end semantics or the flock on the report
  file (`run_gate_report.py:37-38`).
- Not re-baselining the other ~20 perf budgets that are currently green.

## 7. Open question carried into execution

**Was the maint-chain test's 4-day climb real growth, or contention?**
Every other finding here is settled by measurement. This one is not, and S2's
scope depends on the answer. If S1 recovers 60s of the 92s, S2 should shrink
to a `slow` marker and the aggressive options should not be built. The
`gate_query compare` of runs 1136 -> 1155 (same corpus lineage, different
contention) is the cheapest first probe.

## Appendix — how these numbers were obtained

```bash
.venv/bin/python3 helpers/misc/gate_query.py latest
.venv/bin/python3 helpers/misc/gate_query.py timing --leg pytest --last 14
.venv/bin/python3 helpers/misc/gate_query.py timing --leg pytest --last 8 --critical-path
.venv/bin/python3 helpers/misc/gate_query.py timing --leg md-lint --last 14
.venv/bin/python3 helpers/misc/gate_query.py recent --gate advisory --last 7
.venv/bin/python3 helpers/misc/gate_query.py tests --run 1155 --slowest   # §2.7: ascending
.venv/bin/python3 helpers/misc/gate_query.py tests --slowest --last 6 --json
```

Source of the per-leg budgets: `tests/run_perf_benchmarks.py` lines 78
(`static_checks`, 7.0) and 82 (`snapshot_check`, 6.0). Gate scheduling:
`tests/run_gate_report.py` lines 27-30 (`--jobs` / `make -j` precedence),
125 (`_DEFAULT_JOBS = 4`), 184-190 (the pytest `-n auto` step and its
rationale comment). Host: 4 cores, no HT.

## 8. Pre-execution review corrections (2026-10-01, same-day measurement pass)

The filed design's evidence layer survived verification (every cited line
number, the §2.2 critical-path table, the §2.5 bimodality, and the §2.7
sort bug all reproduced exactly), but four findings changed the plan:

1. **§2.2's "creep" is co-run contention INSIDE the suite, not corpus
   growth.** The maint-chain module runs **55.5s standalone** (5 tests,
   idle box); the same tests recorded 92.25s under `make test`'s 4 xdist
   workers. The suite packs at **3.9x** on 4 workers (735s serial / 187s
   wall), so the §7 open question resolves: the 44-92s spread was worker
   contention and co-tenant load, not the corpus. S2's aggressive options
   are therefore not built.
2. **§2.3's fix was inverted — capping xdist would have SLOWED the gate.**
   The formula as filed yields 2 workers for the default gate, halving a
   suite that runs at near-perfect packing, to fix a ~28s drag window
   (215.19s contended vs **187.05s standalone** `make test`, measured
   same day). S1 is revised to the sequencing fix: `Step.exclusive` —
   the pytest leg runs AFTER the pool drains, alone, at full `-n auto`.
   The "different axes" framing at :184-187 is corrected in place.
3. **§2.4 conflated contexts.** The perf budgets bind on standalone
   `make perf` runs, not gate legs. The perf report shows
   `static_checks` standalone at 2.54-3.44s — the proposed 7.0 -> 10.0
   bump is DROPPED — while `snapshot_check` breached standalone at
   6.99s, so its 6.0 -> 9.0 bump is executed with the dated cause.
4. **§2.5's cold regime is the cache bootstrap, not doc churn.** The
   86-143s readings all fall on 2026-09-28/29 (first full-corpus build);
   post-churn runs this same day re-linted ~50 files in ~1s. S4 is
   dropped as built on a one-time artifact. The cache's real pressure
   cost was elsewhere: **temp on the 7.1G tmpfs** — see S6 below.

## 9. Execution Results

### S1 (revised), S2 (scoped), S3, S5, S6 executed 2026-10-01; S4 dropped

- **S6 (new — the TMPDIR default).** `Makefile` now defaults
  `TMPDIR := /mnt/data/tmp/findata` (mkdir'd, `/tmp` fallback) so pytest
  basetemp, bench scratch and backup staging leave the 7.1G tmpfs for
  219G disk. The operator set TMPDIR interactively on 2026-09-28, but
  non-interactive shells (harness sessions) never inherited it — both
  `/tmp/pytest-of-*` and `/mnt/data/tmp/pytest-of-*` were live. Four
  xdist workers each holding multi-hundred-MB temp projects on RAM-backed
  /tmp is the pressure source behind §2.2's inflation; the conftest
  hygiene docstring's "disk under /mnt/data/tmp where the operator set
  it" is now the default rather than a shell-local hope.
- **S1 (revised):** `Step.exclusive` in `tests/run_gate_report.py` — qa's
  pytest leg runs after the pool drains, sequentially, at full `-n auto`;
  the :184-187 comment corrected. Regression test
  `test_exclusive_step_runs_after_pool_drains` pins the no-overlap
  property. Expected: pytest leg loses the ~28s cheap-leg drag; the cheap
  legs pay ~15-20s of previously-shadowed time — net wall ~10-15s, plus
  an uncontended leg whose junit timings become analysis-grade.
- **S2 (scoped):** `test_unshimmed_step_fails_loudly` prepends the fake
  step instead of appending — the abort contract is proven at the first
  step (mid-chain later-steps-never-run stays with
  `test_step_failure_aborts_chain`). Saves the full TIER1 run it used to
  pay (~9.9s standalone, ~15s junit under co-run). Module standalone:
  55.5s -> 49.9s. Fixture and the idempotence double-run untouched —
  they ARE the property under test.
- **S3:** `snapshot_check` 6.0 -> 9.0 with dated cause comment
  (`run_perf_benchmarks.py`); `static_checks` re-budget dropped (see §8.3).
- **S5:** `gate_query tests --run N --slowest` now orders
  `seconds DESC` (the branch had no ORDER BY — pytest execution order,
  fastest first). Regression test `test_tests_slowest_ranks_descending`
  seeds a junit whose heaviest test is last in execution order and pins
  the descending output. Live-verified on run 1155: the 92.25s
  maint-chain test now leads.
- **Verification:** maint-chain + gate-report suites 26 passed;
  test_gate_query 33 passed; ruff format/check clean. `make qa` /
  `make perf` wall re-measure deferred to the operator's gate go
  (acceptance §4.1's target restated honestly: ~185-205s wall from the
  216s baseline, dominated by the suite's 187s uncontended floor — the
  filed ≤150s was never reachable by scheduling alone).

## 10. `gate_query refresh` ingestion — the per-run 36s tax (found 2026-10-01, evening)

The operator hit this live: "gate_query refresh is super slow when we have
new tests to index" — with **gate meaning the qa/advisory/perf combo**, i.e.
the moments 2-3 runs land at once. Reproduced and fixed same day.

### 10.1 Why an incremental indexer was inserting thousands of rows

The indexer is incremental along TWO different axes, and only one of them
is byte-offset:

- **Report blocks** (`*_report.md`) are append-only — `parse_state`
  tracks a byte offset per source and refresh reads only new bytes. This
  axis is genuinely incremental and was never the problem.
- **Per-test rows are per-RUN, not per-byte.** A run's junit XML is
  written atomically at run end (pytest replaces the whole file; it is
  not appendable), so there is nothing to byte-diff. When a new run
  block lands, `_store_run` does `DELETE FROM tests/test_facts WHERE
  run_id = ?` and re-inserts the junit's ENTIRE testcase list — for qa,
  ~3,686 rows into `tests` and ~3,686 into `test_facts`.

That model is correct (a junit's rows only make sense as a whole-run
snapshot; the DELETE+reinsert also gives re-ingestion idempotence). What
was wrong was the execution: **one single-row `INSERT` per testcase.**
DuckDB single-row inserts cost ~0.6 ms each (measured: 3,000 rows = 1.78s
row-at-a-time vs 0.02s batched — ~90x), so one new qa run paid ~7,400 of
them.

### 10.2 Measured

Synthetic corpus: one new qa run block + a 3,686-testcase junit, quiet
box, timed end-to-end through `gate_query.refresh()`:

| | refresh (1 new qa run) |
|---|---|
| before (row-at-a-time) | **35.94s** |
| after (Arrow-batched) | **0.45s** (~80x) |
| incremental no-op (nothing new) | 0.00s (unchanged) |

A gate combo (qa + advisory + perf) queues 2-3 runs per refresh, so the
operator-visible cost was 40-70s of "indexing new tests" per cycle,
compounding with any concurrent DuckDB writer holding the single-writer
lock.

### 10.3 The fix

`_insert_rows_batch(con, table, columns, rows)` in
`helpers/misc/gate_query.py`: rows go in through a pyarrow table
registered as a temp view, then one `INSERT INTO ... SELECT` — pyarrow is
already a repo dependency (`hyper_arrow`, `query`), so no new dep. Both
the `tests` and `test_facts` loops in `_store_run` now batch; the `-ra`
text fallback loop is untouched (it inserts single rows only for FAILED
tests — a handful). Verified: identical row counts (3,686/3,686), all 33
`tests/test_gate_query.py` round-trips green.

### 10.4 The invariant this leaves behind (do not re-mine this seam)

- **Any loop that executes a parameterised INSERT once per row inside a
  DuckDB writer is a defect at this repo's data scale** (~3.7k test rows
  per qa run, growing). Batch through Arrow — or at minimum
  `executemany` — or the cost returns the next time the corpus doubles.
- **Per-run re-ingestion is the contract, per-row execution was the
  bug.** If a future schema change makes junit ingestion incremental
  (e.g. content-hash per testcase), revisit — until then, full-run
  DELETE + one batched INSERT is the intended shape.
- The `refresh` summary line ("indexed: N files, M new runs") is the
  place a regression would show up as wall time, not as a count — a
  timing regression here is invisible to every existing gate. The
  cheapest guard, if one is ever wanted: assert refresh of a single
  qa-shaped run stays under ~2s in `test_gate_query.py` (deliberately
  NOT added now — timing asserts in unit suites flake on loaded boxes;
  the 80x headroom makes accidental regression unlikely to hide).
