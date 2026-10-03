---
title: "Add kill-mid-write recovery tests for the DuckDB stores"
status: executed
filed: "2026-10-03"
executed: "2026-10-03"
completed_md: "338"
area: "tests/, helpers/maintenance/"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Add kill-mid-write recovery tests for the DuckDB stores

**Date:** 2026-10-03 · **Status:** EXECUTED ·
**Area:** `tests/`, `helpers/maintenance/`

## 1. Motivation

Our three operational DuckDB stores (`memory/data/agent_traces.duckdb`,
`memory/convo_search.duckdb`, `outputs/gate_runs.duckdb`) all have
writers that are **killed mid-write by design**: the harvest writer's
flock releases on process death (`harvest_conversations.py:161`), gate
runs die mid-ingest, and the operator kills long rebuilds. WAL recovery
on next open is therefore a routine path, not a corner case — and
DuckDB 1.5.6 shipped fixes on exactly that path (checkpoint-marker
recovery; WAL handle closed before rename; see
`doc/local/notes/duckdb_features.md`).

Two facts make this a gap rather than a hypothetical:

- `harvest_conversations.py:184` states a crash-safety invariant in
  prose — "Staged until commit() — a crashed harvest must not leave
  watermarks" — that **no test exercises**. The only kill-adjacent code
  in `tests/` is Popen spies in `test_maint.py` (dry-run mocks); no test
  SIGKILLs a real writer.
- The engine behavior we depend on is upstream-volatile: two of 1.5.6's
  fixes are on the crash-recovery path. An engine regression here would
  surface as silent store corruption, not a loud gate failure.

Trigger: the duckdb-jepsen evaluation
(`doc/local/evaluations/jepsen_assessment.md`, 2026-10-03) — its one
nemesis (process kill) is the exact hazard our writers live with, but
its Clojure/Elle tooling is wrong for this repo. This proposal ports
the methodology natively.

## 2. Evidence (measured 2026-10-03, this box)

Synthetic store with our `part_index` shape (composite PK, INSERT OR
IGNORE), writer subprocess SIGKILLed at jittered delays, then reopened
read-only. DuckDB 1.5.6, disk TMPDIR (`/mnt/data/tmp/findata`):

| run | kill after (s) | WAL present | reopen (ms) | rows | PK dupes | verdict |
|---|---|---|---|---|---|---|
| 0 | 0.15 | Y | 16 | 0 | 0 | OK, killed mid-write |
| 1 | 0.27 | Y | 28 | 0 | 0 | OK, killed mid-write |
| 2 | 0.39 | Y | 15 | 0 | 0 | OK, killed mid-write |
| 3 | 0.51 | Y | 23 | 5 | 0 | OK, killed mid-write |
| 4 | 0.63 | Y | 30 | 26 | 0 | OK, killed mid-write |
| 5 | 0.75 | Y | 44 | 45 | 0 | OK, killed mid-write |

What the numbers say: 6/6 kills left a WAL, all reopens succeeded, WAL
replay cost 15-44 ms, zero PK duplicates, and the surviving rows were a
committed prefix (5/26/45 rows — per-statement autocommit granularity,
not whole batches). The engine passes today; the point of this arc is
to pin that behavior so regressions are loud. The probe used autocommit
executemany; our real writers stage-then-commit
(`harvest_conversations.py:184`), which is the all-or-nothing variant —
S1 tests both shapes.

## 3. Design

New file `tests/test_duckdb_kill_recovery.py`, hypothesis-seeded,
subprocess writers, synthetic stores ONLY (real DDL constants lifted or
imported from `rebuild_convo_search.py` / `agent_traces` DDL — no
production DB copies, respecting the `production_db_copy_audit`
chokepoint).

- **S1 — kill + reopen invariants (the core).** For each store shape
  (`convo_search` composite-PK append path; `agent_traces` fact-table
  PK shape), spawn a writer subprocess, SIGKILL at a jittered delay,
  then assert: reopen succeeds; no duplicate PKs; row count is a
  prefix of the intended write sequence (autocommit shape) or exactly
  0-or-committed (staged-transaction shape, matching the
  "crashed harvest must not leave watermarks" invariant). Each test
  performs ≥5 kills with different delays and asserts ≥1 landed
  mid-write (a run where the writer always finished first is a
  not-kill, not a pass).
- **S2 — rerun convergence.** After the kill, run the same writer to
  completion against the recovered store; assert the final row set
  equals a clean single-run baseline byte-for-byte (INSERT OR IGNORE
  idempotence contract holds across arbitrary kill points).
- **S3 — WAL replay cost guard (soft).** Assert reopen after kill stays
  under a generous bound (probe says 15-44 ms; guard at 2 s) so a
  pathological replay regression fails loudly instead of slowing every
  harvest silently.

No S4 concurrent-writer variant: single-writer is the accepted
architecture (arc #315 S3); Quack/multi-writer is out of scope and
tracked in `jepsen_assessment.md` instead.

## 4. Acceptance criteria & shakedown

1. `pytest tests/test_duckdb_kill_recovery.py` green, and its kill
   assertions provably fire (temporarily doubling write volume keeps
   ≥1 mid-write kill; a zero-kill run fails the test by design).
2. `make qa` stays green; the new tests add < 15 s wall (probe: 6
   kills ≈ 5 s including interpreter startups) and write only under
   pytest's TMPDIR-following basetemp (Makefile:28-32 — disk, not
   tmpfs).
3. Timing-shaped per house rules: 3 consecutive green runs of the new
   file before landing; no single-run acceptances.
4. Eval gate: not applicable — no query-visible semantics change (no
   roster/crosswalk/hierarchy/extractor rules touched); stated here so
   the omission is deliberate.

| Projected outcome | Today | After |
|---|---|---|
| kill-recovery coverage of live stores | 0 tests | 2 store shapes × (kill+reopen+convergence) |
| crash-recovery regression signal | silent corruption | loud targeted failure |
| qa wall-time cost | — | +<15 s |

## 5. Risks

- **Kill-timing flakes** (writer finishes before SIGKILL on a slow box)
  — mitigated by seeded jitter, ≥5 attempts, and the ≥1-mid-write-kill
  assertion (distinguishes "didn't test it" from "tested and passed").
- **Orphan temp stores** — fixture-scoped tmp_path cleanup; basetemp is
  reaped by tmp_sweep anyway.
- **Subprocess spawn cost in the gate** — capped by small N and the
  <15 s budget; runs under xdist like the rest of qa.
- **Engine-behavior coupling** — the test pins 1.5.x recovery
  semantics; if DuckDB 2.0 changes WAL behavior, this file failing is
  the *desired* early signal inside that arc, not noise.

## 6. Non-goals

- No production-store copies or live-DB mutation — synthetic schemas
  only.
- No concurrent-writer, Quack, or client-server testing (#315 S3
  ruling; revisit triggers in `jepsen_assessment.md`).
- No SQLite-side kill-recovery (different engine; its WAL mode is
  exercised by existing suites) and no Mojo bench paths.
- No new CI infrastructure, no Clojure/JVM toolchain.

## Appendix — raw measurement log

Probe script (2026-10-03, `.venv/bin/python3`, DuckDB 1.5.6,
`/mnt/data/tmp/findata/jepsen-probe/`): writer subprocess executes
`CREATE TABLE part_index (harness VARCHAR, part_id VARCHAR, n INTEGER,
PRIMARY KEY (harness, part_id))` then 50 batches × 200 rows of
`INSERT OR IGNORE` via executemany (autocommit); parent SIGKILLs at the
listed delay; reopen is `duckdb.connect(db, read_only=True)` + counts.

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-03 | run_probe.py (6 kills, 0.15-0.75 s delays) | table in §2 | 6/6 WAL, 0 dupes, 15-44 ms replay |
| 2026-10-03 | `duckdb.__version__` | 1.5.6 | venv engine |
