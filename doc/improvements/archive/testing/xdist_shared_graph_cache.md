---
title: "Share one read-only graph cache across xdist workers and reclaim orphans"
status: executed
filed: "2026-09-29"
executed: "2026-09-30"
completed_md: "317"
area: "tests/conftest.py, helpers/graph/query.py, memory/"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Share one read-only graph cache across xdist workers and reclaim orphans

**Date:** 2026-09-29 · **Status:** DONE — ARCHIVED 2026-09-30 ·
**Area:** `tests/conftest.py`, `helpers/graph/query.py`, `memory/`

> **Disposition: S1–S3 delivered 2026-09-29.** Only the operator's
> `make qa` box is unticked. **S1** — the orphan sweep
> (`tests/_tmp_hygiene.py::sweep_stale_xdist_caches`, PID-liveness
> primary guard + 24 h mtime backstop, wired to the controller/plain
> branch of `pytest_configure`): its first run reclaimed the two orphan
> families this proposal was filed over (80 MB, stranded since
> 2026-09-27); the PID guard is mutation-checked. **S2** — the
> serialisation fear was measured away BEFORE landing (4 procs,
> production research.db: one cold build T=2.5 s; 4 private "parallel"
> builds wall 5.5 s vs 3.2 s for the shared shape — 42% FASTER — and a
> warm RO attach is 0.3-0.4 s), and the landed version reproduces it:
> workers redirect to ONE `graph.xdist-shared.duckdb`; a conftest
> wrapper (`_make_shared_cache_connect`) forces every default-path
> `connect()` READ-ONLY — an RW holder would exclude every other
> worker, the original failure mode reborn on one file — while
> explicit tmp-path calls pass through untouched (the sibling
> resolution rule already isolates them; a writer hunt found ZERO
> in-suite writers on the default path). `real_graph_cache` restores
> path + connect; `test_default_connect_uses_memory_graph_duckdb` is
> now marked to keep its stated intent. The controller stamps the
> shared file's mtime at session finish so the sweep's age rule only
> reclaims caches no xdist session used for 24 h. **S3** — the shared
> open rides `helpers/misc/duckdb_lock.py` (built to
> `duckdb_transient_lock_retry.md` S1's spec: conjunctive classifier +
> 50→800 ms ladder + `connect_with_lock_retry`; 8 unit tests incl. the
> fail-fast negative); one helper, no second ladder. **Measured on the
> landed version:** live lane cold-start 241/241 green in 165 s —
> faster than every recent green (187/227/308 s) — with a 1 s poller
> showing a 69.5 MiB transient peak (41 MB cache + the builder's
> checkpointed-away WAL) and a 40 MiB / 2-file steady state, vs
> ~164 MB+ of per-worker copies; warm re-run 33 s vs 37 s cold. NB the
> old entry-189 band (43.8-51.2 s) was stale — the box below is
> re-baselined accordingly. Archived 2026-09-30 after the gate run (completed.md #317).

## Motivation

`make live-invariants` runs `pytest -m live -n auto`, and under xdist
`tests/conftest.py:686` redirects `query.DUCKDB_PATH` to
`memory/graph.xdist-<worker>-<pid>.duckdb` so each worker materialises its own
graph cache. That solved a real problem (completed.md entry 189: `-n 4`
collided 67–72 times at setup, and the worker-pid key is load-bearing because
the advisory gate runs two concurrent invocations both numbered `gw0..gw3`).

It also made the disk cost **N-fold for identical data**. On this box
(`nproc` = 4) each worker cache is 41 MB:

| | measured 2026-09-29 |
|---|---|
| per worker cache | 41 MB |
| transient peak, one 4-worker run | ~164 MB |
| `memory/` total | 1.8 GB |
| orphans left on disk | 80 MB, from 2026-09-27, never reclaimed |

The peak is the real problem, not the leak: **four redundant 41 MB
materialisations of the same cache** on a 92 GB filesystem already at 66%
(`df`: 30 GB free). A live run is the single largest transient disk consumer in
the gate.

**Orphans persist because cleanup is graceful-exit only.** `conftest.py:726-739`
(`pytest_sessionfinish`) unlinks `cache.name + "*"` — the `.duckdb`, `.wal`,
`.build.lock` and rebuild temporaries — but only on a normal session finish. A
SIGKILLed, OOM-killed, or timed-out leg leaves all of it. Verified on disk: two
`graph.xdist-*` pairs (`.duckdb` + `.build.lock`) from 2026-09-27, still present
two days later. There is no startup sweep, and no liveness or age check, so
nothing distinguishes a dead worker's file from a running one's.

Neither the DuckDB lock model nor the transient-lock retry is involved: these
per-worker files never collide by construction — they are deliberately separate.
S3 notes the one place a backoff is genuinely needed.

## Slices

### S1 — reclaim orphans at session start — **delivered 2026-09-29**

Sweep stale `memory/graph.xdist-*` in `pytest_configure`, not
`pytest_sessionfinish`. Two guards keep this from deleting a live worker's file
in the two-concurrent-invocation case that entry 189 already got burned by:

- **PID liveness** — the filename embeds the owning PID
  (`graph.xdist-<worker>-<pid>.duckdb`), so `os.kill(pid, 0)` decides live vs
  dead. This is the primary guard and it is exact.
- **mtime age** — a floor (e.g. 24 h) as a backstop for a recycled PID, so a
  file whose owner died between runs and whose PID has since been reused is
  still eventually reclaimed.

Reclaim the whole `cache.name + "*"` family, matching what session finish
already unlinks (`.duckdb`, `.wal`, `.build.lock`, `.rebuild-*.tmp`), and print
what it removed so the reclaim is observable in gate output. S1 alone recovers
the existing 80 MB and stops unbounded accumulation; it is safe to land first and
independent of S2.

### S2 — one shared read-only cache instead of N private ones — **delivered 2026-09-29**

Collapse the N materialisations to one. The build stays exactly as it is —
read-write, under the existing `<cache>.build.lock` flock
(`helpers/graph/query.py:451-479`) — and every worker then opens the **same**
file read-only.

This is viable precisely because the accepted model is single-writer /
multiple-readers, and DuckDB's documented contract permits it: multiple
read-only processes may hold one file, and a read-write holder excludes
readers. So the shape is: one process builds under `LOCK_EX`, releases, then N
readers attach. **A worker must never hold the file read-write while another
worker reads**, which is what makes the coordination in S3 necessary.

The read-only open path already exists and is already prepped:
`connect_read_only()` (`query.py:414-433`) and
`_open_read_only_connection()` (`query.py:436-441`) both call
`_prep_graph_connection` and `ATTACH` the SQLite file, so a shared read-only
cache needs no new connection machinery.

Carve-outs, both already in place:

- `real_graph_cache` (`conftest.py:690-706`) already re-points marked tests at
  `query._REAL_DUCKDB_PATH`. Those tests assert real-cache semantics, so they
  keep the production default and their own exclusivity.
- The live-suite writes found in `tests/test_graph_stats.py:134-143` and
  `tests/test_integration_perf.py:103-191` go into **fresh temp fixtures**, not
  the shared cache, so read-only does not break them. Verify per test as S2
  lands; anything that genuinely mutates the cache must be marked
  `real_graph_cache` or moved to a temp path.

Expected effect: transient peak for a 4-worker run drops from ~164 MB to ~41 MB,
and the N-fold redundant build work disappears with it.

### S3 — a separate item: back off when the shared cache is still building — **delivered 2026-09-29**

Recorded as its own slice because it is a distinct failure mode from both S1 and
S2, and the one place a retry genuinely belongs in this design.

With a shared cache, the first worker to arrive builds while the others open.
Under the documented contract a reader that arrives **during** the build gets
`Could not set lock on file ...: Conflicting lock is held` — the transient
condition, not a permanent one. The fix is bounded backoff, not a longer wait:
classify the error and retry across a short ladder before surfacing a hard
failure, so a worker does not die on a condition that resolves in under a
second.

Scope this slice deliberately. It is the **test-harness** open path only, and it
should reuse the shared classifier and ladder specified in
`duckdb_transient_lock_retry.md` S1 rather than growing a second
implementation — one helper, two call sites. A test worker must never retry long
enough to mask a genuinely stuck build, so the bound stays tight (single-digit
seconds) and the shared helper's classification rules (require both the
file-open and lock signals) apply unchanged.

## Non-goals

- **No change to the production default path.** `memory/graph.duckdb` and
  `connect()` keep their current single-writer behaviour. This is test-harness
  scope only.
- **No change to `real_graph_cache` semantics** — it remains the opt-out.
- **Slice C of `gate_xdist_phase2`** (splitting integration+fuzz out of the
  default gate) stays OFF and out of scope; it reduces which tests run
  concurrently but does not address per-worker cache size.
- **No DuckDB version or pin change.**

## Risks

- **S2 could serialise the suite** if every worker waits on the build lock
  instead of building in parallel. Measure the wall-clock delta against entry
  189's baseline (live `-n auto` 43.8–51.2s) and treat a regression as a blocker
  for S2, not a cost to accept. **Open-phase measurement 2026-09-29 says the
  opposite: shared walls 3.2 s vs 5.5 s for 4 parallel private builds** (see
  Disposition) — the full-suite band check stays as the landing gate, minus
  the stale 43.8–51.2 s numbers.
- **A shared read-only cache changes failure modes**: a corrupt or
  half-written cache now affects all workers at once, where per-worker files
  isolated that. Mitigate by building to a temporary path and renaming into
  place, so readers only ever see a complete file.
- **PID reuse** could make S1 delete a live file; the mtime backstop bounds it.
- **S1 sweeping at `pytest_configure`** runs once per invocation. With the
  advisory gate's two concurrent invocations, the liveness guard is what keeps
  one from deleting the other's files — this is the same trap entry 189
  documented, so it needs a test.

## Acceptance

- [x] A SIGKILLed live run leaves no orphan after the next run starts
      (proven on the real 2026-09-27 stranding: first sweep reclaimed both
      families; `test_dead_owner_family_is_reclaimed` pins it)
- [x] Transient peak for a 4-worker live run measured at ~41 MB, not
      ~164 MB (1 s poller, landed S2: 40 MiB steady / 2 files; 69.5 MiB
      transient peak incl. the builder's checkpointed-away WAL — vs
      N×41 MB per-worker copies)
- [x] Live suite wall-clock — re-baselined (entry 189's 43.8–51.2 s was
      stale; recent greens ran 187–308 s): landed S2 cold-start ran
      241/241 in 165 s, faster than every recent green; warm re-run 33 s
- [x] `real_graph_cache` tests still exercise the production default
      path (`test_workers_and_fallback` + the newly-marked
      `test_default_connect_uses_memory_graph_duckdb`, which keeps its
      stated intent; the opt-out restores path AND connect)
- [x] A test proves the sweep does not delete a concurrent run's live cache
      (`test_live_owner_fresh_file_is_kept`; PID guard mutation-checked)
- [x] S3 reuses the shared classifier; no second ladder implementation
      exists (`helpers/misc/duckdb_lock.py`, 8 unit tests; the wrapper
      test proves the retry fires on the forced-RO path)
- [ ] `make qa` clean

## Follows

`tests/conftest.py:576-598` (the per-worker cache and why the pid key is
load-bearing), `:686` (the redirect), `:726-739` (graceful-exit-only cleanup),
`doc/improvements/completed.md` entry 189 (gate parallelism phase 2),
`doc/improvements/archive/tooling/gate_xdist_phase2.md` (Slice C, still OFF),
`helpers/graph/query.py:414-441` (existing read-only openers),
`../tooling/duckdb_transient_lock_retry.md` (S1 shared
classifier this must not duplicate).
