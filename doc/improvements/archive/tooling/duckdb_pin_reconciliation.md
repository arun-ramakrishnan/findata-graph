---
title: "Reconcile the DuckDB lock behind 1.5.6 and gate the 2.0 evaluation"
status: executed
filed: "2026-09-29"
executed: "2026-09-29"
completed_md: "315"
area: "uv.lock, requirements, doc/"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Reconcile the DuckDB lock behind 1.5.6 and gate the 2.0 evaluation

**Date:** 2026-09-29 · **Status:** DONE — AWAITING ARCHIVAL ·
**Area:** `uv.lock`, `requirements`, `doc/`

> **Disposition: DONE.** S1, S2 and S3 are all delivered (see the ticked
> acceptance boxes). The only unticked box is the operator's `make qa` /
> `make search-fresh` run, which is deliberately not mine to perform. The work
> is finished. Archived 2026-09-30 (completed.md #315).

## Motivation

DuckDB **1.5.6 shipped 2026-09-28** (PyPI upload date), one day before this
filing. Three facts are now true at once, and two of them are stale:

| Artifact | Says | Reality |
|---|---|---|
| installed venv | **1.5.6** | current |
| `uv.lock` (`name = "duckdb"`) | **1.5.5** | **stale** — the lock is behind the environment |
| `doc/improvements/archive/graph/duckpgq_retirement.md:21,30,84` | pinned to **1.5.4** | **stale** — that pin was retired |

Two things must be said about `uv.lock` before treating this as a repo problem:

1. **It is not in version control.** `.gitignore:108` ignores it, so there is
   no committed lock to drift from — it is per-machine state. Nothing here
   needs a commit.
2. **The drift was still real locally:** `make install-dev` runs
   `uv sync --extra dev` (Makefile:252), which installs *from the lock*. A
   stale 1.5.5 in the lock would have **silently downgraded** this venv from
   1.5.6 on the next install.

The stale docs are not a live pin either. `pyproject.toml:34-35` carries
`# duckdb itself is unpinned (decision D8)` with a bare `"duckdb"` requirement.
The 1.5.4 pin existed only because duckpgq had no build for 1.5.5 (HTTP 404 on
`INSTALL`); `duckpgq_retirement.md:84-85` records unpinning as the fix for "the
release-train race". So tracking 1.5.6 automatically is the **intended**
posture, and the only genuine inconsistency was the lock lagging the venv.

This mattered beyond hygiene: every concurrency measurement taken for
`duckdb_transient_lock_retry.md` and
`../graph/duckdb_concurrency_model_correction.md`
ran on the **installed 1.5.6**, not the locked 1.5.5 — so until S1 the measured
environment was not what `uv sync` would reproduce.

## Slices

### S1 — re-resolve the lock, confirm the venv matches — **done 2026-09-29**

Re-lock so `uv.lock` records 1.5.6, then verify a clean sync reproduces the
measured environment. If re-locking pulls anything *other* than the DuckDB
patch bump, stop and report rather than absorbing an unrelated upgrade.

Executed locally:

- `uv lock` alone left 1.5.5 in place — **uv preserves an existing resolution
  by default**; the effective command was
  `uv lock --upgrade-package duckdb` → `Updated duckdb v1.5.5 -> v1.5.6`.
- **Guard check:** the only *version change* was duckdb. The `Added …` lines
  from the first pass were dev-extra entries declared in `pyproject.toml:126-130`
  (`textual>=8.2`, `tree-sitter`, `tree-sitter-sql`, `tqdm`) that the old lock
  had been missing entirely — completions, not absorbed upgrades. A repeat
  `uv lock` is now idempotent (`Resolved 127 packages`, no changes).
- Verified agreement: `uv sync --extra dev --dry-run` no longer lists duckdb,
  so the venv (1.5.6) and the lock (1.5.6) match and a future `make install-dev`
  will not downgrade it.
- **No commit is involved** — `.gitignore:108` ignores `uv.lock`, so this is
  per-machine state that cannot drift in the repo.

**Side observation, deliberately out of scope:** that same dry-run would
`uninstall 42 / install 24` packages. The venv is substantially out of sync
with the lock beyond duckdb (42 undeclared packages present, 24 lock entries
absent). Running `make install-dev` would prune them. Not this proposal's
business — noted so nobody triggers it by accident.

### S2 — correct the retired 1.5.4 pin in the docs — **done 2026-09-29**

`duckpgq_retirement.md` said 1.5.4 in three places and
`doc/local/evaluations/duckdb_consolidation_assessment.md` reasoned about a
1.5.4/1.5.5 matrix that no longer describes the running system. Annotated as
historical-at-the-time rather than rewriting the decision record — the 404 was
real, and the reasoning should stay legible.

- `duckpgq_retirement.md`: blockquote at the head of Motivation; the two
  remaining `1.5.4` mentions are execution-log history (Phase 4 record, a
  behaviour comparison) and are already framed as past events.
- `duckdb_consolidation_assessment.md`: the duckpgq blocker row now reads
  *resolved and moot* (retired 2026-08-14, DuckDB unpinned, so there is no pin
  left to block); the process-model row now records the accepted
  single-writer/multiple-readers ruling; the 404 claim row in the measured-reality
  table points at the retirement decision as the surviving reason.

### S3 — record the Rust-driver coupling only (deferred work)

**Deferred at the operator's direction (2026-09-29):** client-server, Quack,
DuckLake, and every other multi-writer route is out of scope. Single-writer /
multiple-readers is the accepted architecture and matches DuckDB's documented
in-process model, so there is no 2.0 gate to write for it. This slice records
one fact that would otherwise be re-derived by anyone touching Rust later.

**The Rust driver lags the engine.** Official Rust client docs put the latest
stable at **1.5.5** (2026-07-22), and the crate version *encodes* the engine:
DuckDB 1.5.5 is `~1.10505.0`. There is no 1.5.6 crate, so an unpinned Python
engine and a pinned Rust driver cannot both track latest. Inert today — nothing
in this repo depends on `duckdb-rs`; the desktop uses rusqlite. Carry two facts
into any future decision:

- Depend with a **tilde** so crate patch releases arrive without silently moving
  the bundled engine.
- **The `bundled` feature omits ICU** (crates.io 10 MB limit), and the docs warn
  that *"some date/time operations (like `now() - interval '1 day'` or `ts::date`
  casts) will fail"*. Our graph is time-series — `valid_from`, `period`,
  `listed_on_index` — so this would surface as a **runtime query failure, not a
  compile error**. The escape hatch `bundled-cmake,icu` needs a git checkout and
  is unavailable from crates.io; `INSTALL icu; LOAD icu;` at runtime is the
  other option. Our Python engine is unaffected (verified: `icu` `loaded=True`).

## Non-goals

- **No version bump as policy.** DuckDB stays unpinned per D8. This proposal
  reconciles artifacts; it does not re-pin anything.
- **No 2.0 adoption work.** S3 writes the gate, not the migration.
- **No Rust DuckDB driver.** Blocked on the desktop question in
  `dbx_assessment.md`, which should be answered first — it may cancel the
  thread entirely.

## Risks

- Re-locking can pull unrelated transitive upgrades. Mitigated by S1's
  stop-and-report condition.
- A doc edit touching `doc/local/**` (a symlink to the main checkout) is
  visible from every worktree; keep it to annotation, not rewrites.

## Acceptance

- [x] S1 `uv.lock` records 1.5.6; a clean sync reproduces the venv
- [x] S2 the three 1.5.4 mentions annotated as historical, reasoning preserved
- [x] S3 the `duckdb-rs` version-encoding and ICU coupling recorded; the 2.0
      gate dropped as out of scope
- [ ] `make md-lint` clean; `make search-fresh` run by the operator **after** other
      sessions land (not in this change — the doc index is shared and currently
      mid-flight)

## Follows

`doc/local/evaluations/dbx_assessment.md`,
`doc/local/evaluations/duckdb_consolidation_assessment.md`.
