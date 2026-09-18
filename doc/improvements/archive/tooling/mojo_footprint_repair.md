---
title: "Mojo footprint repair — mojo format parses every tracked Mojo source"
status: executed
filed: "2026-09-19"
executed: "2026-09-19"
completed_md: "247"
area: "Mojo — src/bench, src/common (toolchain/grammar alignment)"
---

# Mojo footprint repair — mojo format parses every tracked Mojo source

**Date:** 2026-09-19 · **Status:** EXECUTED (completed.md #247) ·
**Area:** src/bench/bench_scale.mojo, src/common/integrity_check.mojo,
mojo toolchain pin

**Follows:** surfaced by the nic2008_seed_table close-out qa run
(completed.md #246) — not caused by it (0 .mojo files in that patch).

## 1. Motivation

`make qa` runs red on every invocation:
`tests/test_lint_gates.py::test_mojo_format_footprint_clean` asserts
`mojo format` is a no-op on a copy of every tracked Mojo source, and the
formatter currently cannot PARSE two of them:

- `src/bench/bench_scale.mojo:25` — `alias has_sync_parallelize = False`
- `src/common/integrity_check.mojo:171` — `alias STOPWORDS_S = "the,of,and,..."`

17/19 files format clean; the two failures are grammar drift between the
pinned toolchain and these constructs (Mojo 1.x renamed `alias` to
`comptime`). Until repaired, every gate report carries a failure that
drowns real regressions.

**Scope growth (2026-09-19, operator directive "mojo is going thru major
upgrades — add that task to this arc"):** execution surfaced a second
drift class. `make mojo-build` fails to COMPILE sources the formatter
accepts — `std.gpu` no longer exists in Mojo 1.1.0
(`src/bench/analyzer.mojo:29` GPU-tier imports), and
`std.runtime.asyncrt` was privatized to `std.runtime._asyncrt`
(TaskGroup users: taskgroup_fanout, spinlock_counter,
bench_cosine_max_parallel). The arc absorbs the full toolchain
alignment, not just the format footprint.

## 2. Approach

1. Diagnose: empirical ground truth over the pinned compiler itself —
   error-message probing for module existence plus module paths read
   from the installed `std.mojoc` package (offline; no web fetchers).
2. Migrate the drift constructs (STOPWORDS_S value byte-identical —
   integrity_check consumes it as a literal):
   - `alias X = v` → `comptime X = v` (3 decls: bench_scale,
     integrity_check ×2).
   - `from std.gpu import global_idx, thread_idx, block_idx` →
     `from max.gpu import ...` (analyzer.mojo).
   - `from std.runtime.asyncrt import TaskGroup` →
     `from std.runtime._asyncrt import TaskGroup` (3 files).
3. Regression: `mojo format` footprint no-op green, `make mojo-build`
   green (all binaries compile), `make mojo-test` green, and a
   grep-audit that no other tracked .mojo uses the retired constructs
   (`rg -n "alias [A-Z_]+ = \"|std\.gpu|runtime\.asyncrt" src/`).

## 3. Gates

- `make qa` 10/10 (footprint test green — first clean run since the
  drift). Close-out runs ONCE after all three pending patches are done
  (gates parked per operator directive).
- `make mojo-build`, `make mojo-test`, `make mojo-format` — all three
  verified green in-tree on 2026-09-19 (build compiles every source;
  test suite passes; format is a byte-identical no-op on all 6 touched
  files).
- Arc is single-mechanism and small; no eval-gate bullet (no
  query-visible semantics change).

## 4. Non-goals

No behavior change to any migrated source's logic; toolchain pin
stays at Mojo 1.1.0 (all drift constructs proved migratable — no bump
needed). Import-path changes (`max.gpu`, `std.runtime._asyncrt`) are
compile-surface only; runtime semantics (GPU tier, TaskGroup fan-out)
unchanged.

## 5. References

- Mojo 1.1.0 changelog: <https://mojolang.org/releases/v1.1.0/> —
  confirms both migrated paths (`std.gpu` now private as `std._gpu`,
  public home `max.gpu`; `std.runtime.asyncrt` private with the task
  API removed — `initialize_runtime`/`parallelism_level` moved to
  `std.runtime`, which we do not use).
- MAX v26.6 release notes: <https://max.modular.com/releases/v26.6/> —
  `max.gpu` completes its mirror of `std.gpu` ("a complete entry point
  for accelerator programming").

Audit against both changelogs (2026-09-19): the tree is otherwise clean
— all other 1.1.0 removals/renames (`fn`, `@parameter if/for`,
`Atomic[DType]`, memory `unsafe_` renames, origin aliases,
`.mojopkg`) have zero hits in our sources; vendored mojo-yaml already
uses the surviving `unsafe_deinit_pointee` spelling. Two deprecation
candidates left for a future touch: `unsafe_ptr()` at
`analyzer.mojo:62` and the `Span(unsafe_ptr=...)` kwarg at
`cosine.mojo:58` (both still compile; `.ptr()` is the forward path).
