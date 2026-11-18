---
title: "Configurable DuckDB lock backoff for long holders (60 s / 3 attempts) on read CLIs"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "540"
area: tooling
---

# Configurable DuckDB lock backoff for long holders

**Filed:** 2026-11-18 · **Status:** EXECUTED same day · **completed.md:** #540

## Motivation

A reader CLI frequently fails with:

```text
ERROR: IO Error: Could not set lock on file ".../memory_search.duckdb":
Conflicting lock is held in .../python3.14 (PID 130020) by user arun.
```

`helpers/misc/duckdb_lock.py` already classifies this exactly (conjunction of
a file-open phrase and a lock phrase) and retries on the ladder
`_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)` — **1.55 s total** (`:69-70`).
A long writer (a sidecar rebuild, ~2 min) outlives that ladder, so the leg
still fails. The S3 `io_lock` flock entry (`open_read_only`, `:165`) covers
cooperating writers, but adoption is partial:

- `helpers/misc/convo_query.py:169` uses `open_read_only` (queue + ladder) ✓
- `helpers/misc/note_query.py:66` raw `duckdb.connect` ✗
- `helpers/misc/master_query.py:186` raw `ds.connect_shared` ✗
- `helpers/maintenance/rebuild_{memory,doc,script,git,}_search.py` `connect_*_db`
  functions raw `duckdb.connect` — these are the *writers*, deliberately
  excluded (flock-serialised cold build; retrying a mid-transaction open is
  wrong), per archived proposal S2 non-goals.

Archived ruling (`doc/improvements/archive/tooling/duckdb_transient_lock_retry.md`,
status EXECUTED, completed #319) S2 wired six reader sites; these reader CLIs
were missed. This proposal extends the S1 ladder without changing its default.

## Slices

### S1 — parameterise the ladder (default unchanged)

`connect_with_lock_retry(open_fn, *, delays=_RETRY_DELAYS_S, sleep=time.sleep)`:
default keeps the 1.55 s schedule, so every existing caller and every test
pin in `tests/test_duckdb_lock.py:56` is untouched. Add a named extended
schedule `_EXTENDED_RETRY_DELAYS_S = (2.0, 10.0, 48.0)` (3 retries; 60 s
cumulative; each delay ≤ 60 s) and thread `delays` through
`open_read_only(db_path, *, delays=...)`.

### S2 — wire the reader CLIs

Reader opens routed through the extended ladder:

- `helpers/maintenance/rebuild_memory_search.py`: new `connect_memory_read_only`
  (duckdb → `open_read_only(delays=_EXTENDED_RETRY_DELAYS_S)`; legacy → house
  sqlite read-only). This is the **reported failing store** (`memory_search.duckdb`).
  `memory_query.py` and `search_tui.py` reader lanes now call it; writers keep
  `connect_memory_db` (unwired by design).
- `helpers/misc/note_query.py`: duckdb open → `open_read_only(delays=...)`;
  auto-loaded native FTS kept (DuckDB 2.0.0, no explicit `LOAD fts`).
- `helpers/misc/master_query.py` `_run_maint` leg → `open_read_only(db,
  delays=_EXTENDED_RETRY_DELAYS_S)` (operator-directed 2026-11-18). Its store
  (`outputs/gate_runs.duckdb`) can race a concurrent gate refresh; the served
  path is preserved because `open_read_only` checks `is_served` first. Writers
  (`gate_query.connect`) are deliberately unwired.

### S3 — tests

`tests/test_duckdb_lock.py`: pin the extended schedule values; assert a
transient conflict is retried at 2/10/48 s and a non-lock failure raises on
the first attempt; assert the default schedule is still 1.55 s. No gate arc
(gates parked per operator 2026-11-18); targeted `pytest tests/test_duckdb_lock.py`
and ruff only.

### S4 — memory_search writer coordination (2026-11-18)

The writer side is now coordinated too (was S2's "deliberately excluded"):

- `helpers/maintenance/rebuild_memory_search.py` new
  `_memory_writer_conn(db_path)` (context manager): an UNSERVED store takes
  `LOCK_EX` on `<db>.io.lock` across the whole write window (mirroring
  `rebuild_convo_search.py:229`) so reader lanes queue on their `LOCK_SH`
  instead of failing with `Conflicting lock is held`; the open itself retries
  the extended ladder (`delays=_EXTENDED_RETRY_DELAYS_S` — a writer waiting
  out a manual-CLI / db_maint-backup holder that never took the flock, the
  convo rebuild's own pattern). A SERVED store skips the flock (the quack
  server serializes writes over the wire) and keeps the plain open.
- `rebuild_memory_duckdb` opens through `_memory_writer_conn` instead of a
  raw `connect_memory_db`; `_check_freshness_memory_duckdb` switched from the
  writer connect to the reader path (`connect_memory_read_only` — a `--check`
  must never take the writer flock nor fail on a rebuild window).
- Absent-store reads: `connect_memory_read_only` answers with an empty
  in-memory handle (no table → `ready_fn` False → the CLI's "not built" hint)
  instead of raising `sqlite3.OperationalError` / `duckdb.IOException` — the
  reported failure mode when `memory_query.py` runs before the first rebuild.
- Tests: `tests/test_rebuild_memory_search.py::TestWriterCoordination`
  (reader queues behind the flock during a rebuild window; writer waits out a
  child-process foreign holder; transient open retried; served store skips the
  flock). Targeted: `tests/test_rebuild_memory_search.py` +
  `tests/test_db.py` + `tests/test_duckdb_lock.py` +
  `tests/test_memory_query_duckdb.py` all green; no gate arc (gates parked
  per operator 2026-11-18); ruff clean.

## Risks

- A 60 s ladder can delay a legitimate failure by 60 s. Mitigation: the
  classifier is a conjunction, so non-lock errors (missing file, permission
  denied) still raise immediately; only live-holder conflicts wait.
- Writers holding a foreign lock for >60 s still fail — S3 flock coordination
  remains the correct tool for fully cooperative long holds; this ladder is
  the fallback for holders that never took the flock.
- Same-process foreign DuckDB holders cannot be waited out at all (DuckDB
  refuses a second same-process open of a held file — "same database file with
  a different configuration"), so S4's writer tests use a child process for
  the foreign-holder case. Production writers are separate processes
  (`make search-fresh`), so the lock ladder applies.

## Non-goals

- Changing the global default ladder (would break the 1.55 s pin and could
  introduce 60 s hangs in gate/integrity callers that expect fast failure).
- Retrying writer opens or mid-transaction operations. (S4 supersedes this
  for the memory_search sidecar rebuild only: the writer open retries the
  transient ladder, the transaction itself is never retried.)

## 5. Outcome (2026-11-18, executed)

**Shipped exactly as sliced.** S1 `helpers/misc/duckdb_lock.py`:74-76 adds
`_EXTENDED_RETRY_DELAYS_S = (2.0, 10.0, 48.0)` (60 s cumulative) beside
the untouched `_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)` (1.55 s);
`connect_with_lock_retry(*, delays=...)` at :100 and
`open_read_only(*, delays=...)` at :178 thread it through, so the default
schedule is unchanged and no existing caller or test pin moved.

S2 wired the four missed reader CLIs:
`rebuild_memory_search.py:184 connect_memory_read_only` (the reported
failing store) used by `memory_query.py:90` and `search_tui.py:481`;
`note_query.py:70 open_read_only(db_path, delays=_EXTENDED_RETRY_DELAYS_S)`;
`master_query.py:188` same for the `_run_maint` leg. Writers stayed
unwired per the S2 non-goal.

S3 `tests/test_duckdb_lock.py` pins the schedule
(`== (2.0, 10.0, 48.0)`, `sum(...) == 60.0`), asserts a transient
conflict retries at exactly `[2.0, 10.0, 48.0]`, and that a non-lock
failure raises on the first attempt — while the 1.55 s default pin stays.

**S4 landed beyond the proposal.** The writer side is now coordinated
too: `rebuild_memory_search.py:1449 _memory_writer_conn(db_path)` takes
`LOCK_EX` on `<db>.io.lock` across the whole write window when the store
is UNSERVED (mirroring `rebuild_convo_search.py:229`), so readers queue
on their `LOCK_SH` instead of failing; open itself retries the extended
ladder. A SERVED store skips the flock (the quack server serializes over
the wire). `rebuild_memory_duckdb` opens through it;
`_check_freshness_memory_duckdb` (:1594) uses the READER path, so a
`--check` never takes the writer flock. Absent-store reads now answer
with an empty in-memory handle instead of raising — the reported "run
memory_query before the first rebuild" failure.

**Verification:** `tests/test_duckdb_lock.py` + `tests/test_rebuild_memory_search.py`
green (48 passed with the signal-sweep suite). Gates parked per operator
m01133 — targeted tests + ruff only.
