---
title: "DuckDB transient-lock retry — classify and back off instead of failing the leg"
status: proposed
filed: "2026-09-29"
executed: null
completed_md: null
area: "helpers/graph/query.py, helpers/misc/, tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# DuckDB transient-lock retry — classify and back off instead of failing the leg

**Date:** 2026-09-29 · **Status:** EXECUTED — S1–S4 DELIVERED 2026-09-30 ·
AWAITING `make qa` (parked with the session's other gates) ·
**Area:** `helpers/graph/query.py`, `helpers/misc/`, `tests`

> **Disposition: EXECUTED 2026-09-30** (S1 was delivered 2026-09-29 in
> service of xdist_shared_graph_cache S3; S2–S4 landed the next day).
> `helpers/misc/duckdb_lock.py` now carries all three artefacts: the
> conjunctive `is_transient_lock_error`, the pinned 50→800 ms ladder,
> `connect_with_lock_retry`, plus the S3 mechanism — `io_lock`
> (`<db>.io.lock` flocks: `LOCK_SH` readers queue at open, `LOCK_EX`
> writers hold the whole compute window) and `open_read_only` (the
> reader entry: queue, then ladder-retry the open, then release —
> DuckDB's own lock protects the connection from that moment). Eleven
> unit tests, including the two S4 cross-process classes run against
> REAL subprocesses. The S3 caller audit in
> `../archive/graph/duckdb_concurrency_model_correction.md` hardened the case: 24 read-only
> openers across the estate are uncoordinated (no flock, no build fallback, no
> retry), and `convo_query.py:88` is now a **confirmed** live-writer site
> against the unlocked `rebuild_convo_search.py:491` hold — both discharged
> by this delivery.
>
> DBX is cited as provenance only; nothing here depends on adopting it. Scope
> note: this helps an opener racing a foreign writer — it does **not** address
> pytest timeouts, and it would not have prevented the per-worker cache disk
> exhaustion in `xdist_shared_graph_cache.md` (those files are deliberately
> separate and never collide).

## Motivation

DuckDB embedded file mode permits any number of read-only holders **or** exactly
one process in total — never a mixture. A read-write holder therefore blocks
every reader, in any access mode. Measured 2026-09-29 on duckdb 1.5.6
(matrix in `doc/local/evaluations/dbx_assessment.md`):

| Holder | Opener | Result |
|---|---|---|
| read-write | read-only | **FAIL** — `Could not set lock on file …: Conflicting lock is held` |
| read-only | read-write | **FAIL** — same |
| read-only | read-only | **OK** |
| read-write | read-write | **FAIL** — same |

We already hit this. `doc/improvements/archive/tooling/gate_xdist_phase2.md:113`
records `query.connect()`'s lock covering *openings, not lifetimes*, producing
`Conflicting lock is held` across the xdist gate. That was fixed twice over —
by per-worker caches (`tests/conftest.py:575-596`) and by flock-serialising the
cold build path behind `<cache>.build.lock` (`helpers/graph/query.py:452-479`,
regression-tested cross-process with 6 subprocesses in
`tests/test_graph_disk.py:188`).

This is not a DuckDB quirk we are fighting — it is the documented contract. The
official concurrency page states that in-process mode offers exactly two
mutually exclusive process modes:

> "**Read-write mode:** one process can both read and write to the database.
> **Read-only mode:** multiple processes can read from the database, but no
> processes can write (`access_mode = 'READ_ONLY'`)."

The operator's standing ruling is that **single-writer / multiple-readers is
sufficient**, which is exactly this model and exactly what our estate already
is. The problem is not the model — it is that our one writer holds the file for
tens of seconds while the only lock we take covers just the open.

**What is still unfixed is the residual case the flock does not cover:** a
read-only opener racing a writer that did *not* come through `connect()` — a
`db_maint` backup, `stamp_centrality`, an integrity check, a manual CLI. The
flock serialises `connect()`-mediated builds only. Against any other writer the
read-only open fails immediately, and a transient, self-resolving condition is
reported as a hard error.

This proposal adds classification plus bounded backoff for transient locks, and
proper lock coordination for long ones, so neither is surfaced as a hard error.
The classifier and ladder are reimplemented from DBX (`t8y2/dbx`, master
`497d7c9e`, Apache-2.0) as a **provenance citation only** — DBX itself was
evaluated and **not adopted**, so nothing here depends on it; see the verdict in
`doc/local/evaluations/dbx_assessment.md`. The borrowed artefact is the idea
(a conjunctive transient classifier plus a bounded ladder), roughly 29 lines,
rewritten in Python against our own openers.

A single-writer CLI is already the right shape and is not the problem. The
problem is that our one writer holds the file for tens of seconds while the only
lock we take covers just the open.

## Non-goals

- **No worker isolation.** A subprocess does not help — the conflict is
  cross-process and a third process cannot read a file another process holds
  read-write. DBX needs a worker for uniformity across its three driver tiers,
  not because DuckDB requires it.
- **No engine or access-mode change.** Read-only remains read-only.
- **No `eval-gate` bullet.** This alters no query-visible semantics (no
  rosters, crosswalks, hierarchies, or extractor rules), so
  `helpers/misc/ontology_eval_gate.py` is not required by the proposals README.

## Slices

### S1 — a classifier and a ladder, in one shared helper — **delivered 2026-09-29**

New `helpers/misc/duckdb_lock.py`:

- `is_transient_lock_error(msg) -> bool` — matches **both** a file-open phrase
  *and* a lock phrase. The conjunction is the load-bearing detail: a
  disjunction would retry genuine "file does not exist" and "permission denied"
  failures, converting fast errors into 1.55 s of pointless backoff.
  Phrases: file-open `cannot open file` / `could not set lock` /
  `file is already open`; lock `conflicting lock` /
  `being used by another process` / `process cannot access the file` /
  `sharing violation` / `resource temporarily unavailable`.
- `lock_retry_delay(attempt) -> Duration | None` — 50, 100, 200, 400, 800 ms,
  then `None` (give up). Total worst-case wait 1.55 s.
- A `connect_with_lock_retry(fn)` wrapper so every caller opts in explicitly
  rather than by accident.

Our own archived failure string matches both halves (`could not set lock` +
`Conflicting lock is held`), so the classifier is validated against a string we
have actually seen rather than a synthetic one.

### S2 — wire it into the openers that can race a foreign writer

In priority order: `helpers/graph/query.py` `connect()` (read-only path) →
`helpers/misc/convo_query.py:88` → `helpers/misc/gate_query.py` →
`helpers/graph/stats.py:465` → `helpers/graph/context_pack.py` →
`helpers/misc/database_integrity_check.py:1803`.

`helpers/graph/algorithms.py:90` `duckdb_connect` already delegates to
`query.connect` at call time, so it inherits S2 for free — no separate change.

Deliberately **not** applied to: the cold build path (already flock-serialised),
or any code holding the connection across a long transaction (retrying an open
is correct; retrying mid-transaction is not).

**DELIVERED 2026-09-30.** All six listed sites wired, plus three justified
additions found by a full `duckdb.connect` census of the estate:

- `query.py` ×3: `connect_read_only`, `_open_read_only_connection` (the
  `connect(read_only=True)` warm path), and **`_is_warm`** (addition — a
  transient conflict in the warmth probe must not masquerade as a cold cache
  and send the build path at a foreign-writer-held file).
- `convo_query.connect` and `rebuild_convo_search._rebuild_check_mode` — via
  the S3 `open_read_only` entry (queue + ladder, one call).
- `gate_query.connect`, `stats.py` graph probe,
  `database_integrity_check` cache reconciliation (the ladder runs BEFORE the
  "cache unreadable" drift-ERROR classification, so a transient conflict no
  longer falsifies a drift verdict).
- Additions: `search_tui` schema-view + SQL lane, `gc_embed_cache` duckdb
  refs (both via `open_read_only`), `model_analytics` ×3 reader sites
  (retry-only wrapper over `helpers.core.db.connect`, which stays the opener —
  its shared prep must not be bypassed).
- Inherit for free, as predicted: `context_pack` (via `connect_read_only`),
  `algorithms` (via `query.connect`). The cold build path and
  `update_extensions` (in-memory) deliberately unwired.

### S3 — coordinate long holds, do not retry them

The ladder is the wrong tool for every writer we have that runs longer than a
second or two. Our code already documents this for the one file that locks:

`stamp_centrality_cache` holds a read-write connection for **27.5 s** measured
2026-09-26 (six minutes before the S6 diet) — `query.py:1071` — and
`query.py:1078` records a live incident at that duration ("6-min stamp, exit 0,
zero tables"). A 1.55 s ladder cannot cover a 27.5 s hold, and pretending
otherwise would convert a hard failure into a slower hard failure.

Worse, the flock does not help, and the code says so.
`_open_rw` (`query.py:451-479`) takes `LOCK_EX` at `:455` and returns the
connection at `:477` — **still inside the `try`** — so `LOCK_UN` at `:479` fires
before the caller does any work. `query.py:1079-1080` states it outright: *"The
connect() flock does not help — it serialises the OPEN, not the stamp's
minutes-long compute window."*

#### The estate is three disjoint writers, and only one of them locks

A DuckDB file lock is per-file, so the writer families never contend *with each
other* — the contention is always writer-vs-readers-of-the-same-file. Measured
2026-09-29:

| File | Writer | Lock | Hold | Readers |
|---|---|---|---|---|
| `graph.duckdb` | `query.connect` / `stamp_centrality_cache` | `.build.lock`, open only | ~27.5 s | `stats`, `context_pack`, `algorithms`, Mojo bench, `app.py` |
| `convo_search.duckdb` (860 MB) | `rebuild_convo_search.py:491` — plain `duckdb.connect`, no flag | **none** | **~2 min** | `convo_query`, `search_tui` lane 4, `db_maint`, `gc_embed_cache`, `harvest_conversations` |
| `agent_traces.duckdb`, `model_usage.duckdb` | `bench_data/code/agent_traces.py` | **none** | ETL window | `model_analytics.py` |

`helpers/graph/query.py` is the **only** file in the estate that takes a lock
at all. The sharpest gap is `convo_search.duckdb`: a ~2-minute read-write hold
with no lock, against five readers — including `convo_query`, one of the four
query CLIs in `AGENTS.md`, and the tool this proposal's own research relied on.

#### The mechanism

Split the two classes honestly:

| Hold | Example | Correct mechanism |
|---|---|---|
| sub-second / transient | parallel build, `db_maint` backup, a second CLI | **S1/S2 ladder** — bounded backoff |
| tens of seconds | `stamp-centrality` (~27.5 s), `rebuild_convo_search` (~2 min) | **flock coordination** — block, do not retry |

For the flock cases: readers take `LOCK_SH` on a per-file `<db>.io.lock`; long
writers hold `LOCK_EX` across the **whole** compute window rather than just the
open. Readers then *queue* instead of failing, which is correct for a CLI and
impossible today.

This also stops the inode-swap guard at `query.py:1083-1094` from being the
primary defence against a concurrent `rebuild()` swap — with `LOCK_EX` held
across the stamp, the swap cannot interleave. Keep the guard as a backstop and
say so in its comment.

Per-file, in priority order: `convo_search.duckdb` (unprotected, longest hold,
most readers) → `graph.duckdb` (partially protected, wrong scope) → the
`bench_data` stores.

**DELIVERED 2026-09-30** — `io_lock(db_path, *, exclusive)` +
`open_read_only(db_path)` in `helpers/misc/duckdb_lock.py`, wired per file:

| File | Writer window (LOCK_EX) | Readers |
|---|---|---|
| `convo_search.duckdb` | `rebuild_convo_search.rebuild` — the WHOLE ~2 min window, DuckDB closed before flock release; `harvest_conversations.Store` writer branch holds it for the Store lifetime (released in `close()`) | `convo_query.connect`, harvest check branch, `_rebuild_check_mode` — queue at open, then ladder |
| `graph.duckdb` | `stamp_centrality_cache` — LOCK_EX spans the whole stamp window (the inode-swap guard kept as backstop, comment says so); `_rebuild_via_swap` takes it around `os.replace` (serialises the swap instant against the stamp — the ABBA ordering is safe because the swap's other lock is the pid-tagged temp build.lock, never contended); `db_maint` CHECKPOINT/VACUUM window | `query.py` readers: retry only (their lifetimes vary — `app.py` holds long-lived connections; a lifetime LOCK_SH would convert the writer's hard fail into an unbounded hang, and DuckDB already gives the opened reader full protection) |
| `agent_traces.duckdb`, `model_usage.duckdb` | `agent_traces.py` main — LOCK_EX spans the whole load/report command window | `model_analytics` readers — ladder only (short reads; same open-only reasoning) |

`db_maint`'s `_duckdb_zstd_backup` checkpoint window sits under io.lock SH:
a concurrent long writer makes the backup QUEUE instead of skipping, and a
queued writer waits behind the checkpoint. Residual, recorded: reader opens
that predate a writer's flock acquisition are absorbed by the ladder, so a
reader holding a DuckDB connection across a writer's ENTIRE window still
defeats the writer's open (1.55 s then the original error) — true for any
long-lived reader (`search_tui` interactive session against a rebuild). That
is exactly today's failure mode, strictly improved; closing it fully needs
lifetime reader flocks, declined for the app.py hang risk above.

#### Why the holds are long — the operator's "a lock held that long looks broken" challenge (2026-09-30)

The flock added here holds nothing longer than before: in DuckDB embedded
mode an open RW connection holds the file lock for the connection's WHOLE
lifetime, unavoidably — that was always true, and before this change readers
simply FAILED during the window. The io.lock only converts that hard fail
into a queue. The long holders, and what their hold actually is:

| Holder | Hold | What the time actually is |
|---|---|---|
| `rebuild_convo_search` (~2 min) | lifetime of its RW connection, DDL → bulk insert | ~90% is the EMBED phase (50k+ texts through granite) — compute that never needs the DB open |
| `stamp_centrality_cache` (~27.5 s) | the connection must be open the whole time | the compute IS in-DB: Onager centrality runs as SQL inside DuckDB over the materialised graph |
| `agent_traces` ETL, `db_maint` CHECKPOINT/VACUUM | seconds | actual write work |

The operator is right that minutes-long holds are a smell, and the estate
already owns the better pattern: `_rebuild_via_swap` builds the graph cache
into a temp file and `os.replace`s it in — the live file's RW hold is the
swap instant, which is why the graph REBUILD never needed this proposal's
coordination. Two follow-ups would shrink every remaining window (recorded,
not implemented — each is its own measured change):

1. **stamp via swap**: stamp centrality into a temp copy of the cache and
   swap it in — the live `graph.duckdb` hold drops to the swap instant.
   This is the high-value one: the operator's observation is correct that
   graph.duckdb is the only store with FREQUENT reader overlap (gate and
   advisory steps read the cache while maint re-stamps); convo/bench
   coordination only engages while those occasional writers run, because
   io.lock is per-file and the stores are otherwise unrelated.
2. **convo compute-then-write**: restructure the rebuild to read stored
   hashes via a RO open, close, embed, then open RW only for the bulk
   insert — the RW window falls from ~2 min to the Arrow-insert seconds.

With both landed, the io.lock degrades to a thin safety net for foreign
writers (a manual CLI, a backup) rather than queueing infrastructure.
**Pursued as `database_contention_window_minimization.md` (filed
2026-09-30, after the operator's ruling that locks be held minimally and
the estate-wide pathway scan — sqlite and duckdb, plus the cross-store
nesting map — came back bounded at these two windows).**

### S4 — tests that measure the matrix, not the prose

- A cross-process test mirroring the failing row: one process holds the file
  read-write, a second opens read-only, and the open must **succeed** after
  backoff. Modelled on `test_parallel_ro_connects_serialize_the_build`, which
  already proves the RO/RO row with 6 subprocesses.
- The S3 case: a reader arriving while a long writer holds `LOCK_EX` must
  **block and then succeed**, not raise. This is the test that distinguishes
  coordination from retry, and it fails against today's code.
- A negative test asserting a non-lock error (`file does not exist`,
  `permission denied`) raises **immediately** and is not retried. Without this
  the conjunction is untested and a future `or` regression ships silently.
- A test pinning the ladder values, so the schedule cannot drift unnoticed.

**DELIVERED 2026-09-30** — `tests/test_duckdb_lock.py` now 11 tests: the two
cross-process classes run REAL subprocesses (an RW holder for 0.5 s vs a
retrying RO opener — asserts success AND `elapsed >= 0.4`, proving the ladder
waited; an io.lock EX window held 2 s vs a queued `open_read_only` — asserts
success AND `elapsed >= 1.5`, which a ladder-only world cannot satisfy), plus
the S1 set (conjunction halves incl. the archived string, pinned schedule,
retry, fail-fast negative, exhaustion). The ladder and negative tests predate
this session; the two subprocess classes are the new measurement.

## Risks

- **Masking a real deadlock.** Bounded at 1.55 s and lock-phrase-gated, so a
  genuine writer holding the file surfaces as the original error, just 1.55 s
  later. S3's negative test guards the gate.
- **Two locks, one file.** The flock and the DuckDB lock solve different
  problems (build serialisation vs. foreign writers). Both stay; this proposal
  only covers the gap the flock leaves.

## Acceptance

- [x] S1 helper lands with the conjunction and the ladder pinned by test
      (`helpers/misc/duckdb_lock.py` + `tests/test_duckdb_lock.py`, 8
      tests: conjunction halves, pinned schedule, retry, fail-fast
      negative, exhaustion)
- [x] S2 wired at all six openers (+3 census additions, recorded in the
      slice); no behaviour change when no writer is present (all suites
      green unchanged)
- [x] S3 long writers hold `LOCK_EX` across the compute window (convo
      rebuild, harvest Store, graph stamp + swap instant, db_maint
      checkpoint, bench ETL); readers queue (`open_read_only`) or retry,
      per the lifetime table in the slice
- [x] S4 green: RW-holder/RO-opener retry, the blocking-reader case,
      the negative test, the ladder test (`tests/test_duckdb_lock.py`,
      11 tests — the two cross-process classes use real subprocesses)
- [ ] `make qa` clean (blocking); `make advisory` clean (parked with the
      session's gates; run once on the operator's go, then archive)
- [x] No new dependency; the ladder is `time.sleep`

## Follows

`doc/local/evaluations/dbx_assessment.md`. Related: P2 corrects the docstring
invariant this proposal's measurement falsified.
