---
title: "Snapshot freshness pre-gate — fail fast on generation drift"
status: executed
filed: "2026-09-21"
executed: "2026-09-21"
completed_md: "264"
area: "helpers/maintenance"
---

# Snapshot freshness pre-gate — fail fast on generation drift

**Date:** 2026-09-21 · **Status:** EXECUTED ·
**Area:** `helpers/maintenance/snapshot_db.py`,
`tests/run_gate_report.py` (qa recipe)

## 1. Motivation

Generation drift has reddened two gates in one day (qa snapshot_check,
perf snapshot_check bench): the live DBs move past the snapshot
(112784 → 112785 after centrality stamping) and the failure surfaces
as MISMATCH lines 7 s into verification. The generation comparison
already exists (P2.4) but runs *after* the expensive table-count
verification. A generation-only pre-gate fails in milliseconds with
the exact remediation instead.

## 2. Evidence (measured 2026-09-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| full `--check` in qa | 7.34 s, then MISMATCH on drift | baseline (slow signal) |
| live generation read (`_build_meta`, read-only) | 21 ms | pre-gate reads are O(1) |
| snapshot-side generation (parquet point-read) | unmeasured, same class | measure in build |

## 3. Design

- `snapshot_db.py --quick`: compare generations for the sqlite pair
  (`db_meta` live vs `db_meta.parquet`) and the DuckDB pair
  (`_build_meta` live vs snapshot); print OK/MISMATCH per pair plus
  `run make snapshot` remediation; rc 1 on any mismatch or unreadable
  side (fail-closed: a missing snapshot IS stale — snapshots are
  git-tracked, so fresh clones have them).
- qa recipe: prepend a `snapshot-fresh` step before `snapshot_check`
  (mirrors the `sync-sector-links --check` doctrine: check-only gates
  with exact remediation). Perf bench list untouched (run-all means
  no time is saved there; the qa signal + manual use carry the value).
- MISMATCH lines in the full `--check` gain the same remediation
  pointer (2-line change — helps even when the full verify runs).

Alternatives considered: auto re-snapshot on drift (rejected —
snapshotting mid-WIP would bake half-done state into tracked
parquet; capture stays an explicit operator act); skipping when
snapshot absent (rejected — fail-closed, see above).

Slices: S1 `--quick` + tests (seeded drift both directions);
S2 qa step + MISMATCH remediation lines.

## 4. Acceptance criteria & shakedown

1. `--quick` rc 0 on a fresh tree; rc 1 with remediation text on a
   generation-bumped live DB (seeded in tmp, both pairs).
2. qa table shows the `snapshot-fresh` step green; full `--check`
   behavior unchanged (existing suites green).
3. `make search-fresh` converges (doc touch only if the remediation
   text lands in a doc — it doesn't; code + tests only).

| Projected outcome | Today | After |
|---|---|---|
| drift signal latency in qa | ~7 s + MISMATCH archaeology | 0.57 s + `run make snapshot` (measured best-of-3) |
| full `--check` semantics | — | unchanged |

## 5. Risks

- **False stale on exotic layouts** (missing meta tables) —
  mitigation: fail-closed is intended there too (unverifiable ==
  stale); message names the unreadable side.
- **qa step-order churn** — mitigation: prepend only, no reorder of
  existing steps; run-all semantics untouched.

## 6. Non-goals

Auto-capture; changing what `--check` verifies; perf bench-list
changes; touching the snapshot format.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-21 | `make qa` snapshot_check step | 7.34 s green (fresh tree) | baseline cost of the full verify |
| 2026-09-21 | live `_build_meta` generation read | 21 ms read-only | pre-gate O(1) claim |
| 2026-09-21 | drift incidents | qa red + perf bench red (112784 vs 112785) | both diagnosed as generation-only drift, content OK |
| 2026-09-21 | S1–S2 build | `--quick` (sqlite + duckdb pairs, fail-closed) + 3 tests; qa `snapshot-fresh` step prepended; MISMATCH remediation pointer; `make snapshot-fresh` target | `--quick` 0.57 s end-to-end best-of-3 vs 7.34 s full `--check` (~13× faster signal); 22 snapshot tests green; parameterized `COPY TO ?` silently writes nowhere — fixtures use f-string + noqa per house pattern |
