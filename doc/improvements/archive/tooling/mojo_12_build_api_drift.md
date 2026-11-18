---
title: "Fix `make mojo-build`: migrate removed GPU-accelerator detection APIs to default_accelerator()"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "537"
area: tooling
---

# Fix `make mojo-build` — removed GPU-accelerator detection APIs

**Filed:** 2026-11-18 · **Status:** PROPOSED (code implemented + verified same day; awaiting operator archive + completed.md number)

## Summary

`make mojo-build` is RED on this host (Mojo 1.2.0 stable, 2016d0cd). Two
sources fail to compile because three accelerator-detection functions were
removed from `std.sys` / `std.sys.info` in Mojo 1.2.0 (release 2026-10-22):
`has_nvidia_gpu_accelerator()`, `has_amd_gpu_accelerator()`,
`has_apple_gpu_accelerator()`. All other 27 sources build, including the new
`bfs_bipartite_all_batch.mojo`. The fix is a mechanical API migration to the
replacement `std.sys.default_accelerator()` predicate API; no behavior change
on this (accelerator-free) host.

## Evidence (2026-11-18, `make -f Makefile.mojo -k mojo-build`)

Two distinct failing files, six errors:

```text
Mojo/src/bench/analyzer.mojo:22:5: error: module 'info' does not contain 'has_nvidia_gpu_accelerator'
Mojo/src/bench/analyzer.mojo:23:5: error: module 'info' does not contain 'has_amd_gpu_accelerator'
Mojo/src/bench/analyzer.mojo:24:5: error: module 'info' does not contain 'has_apple_gpu_accelerator'
Mojo/src/gpu/analyzer_intel.mojo:23:5: error: module 'info' does not contain 'has_nvidia_gpu_accelerator'
Mojo/src/gpu/analyzer_intel.mojo:24:5: error: module 'info' does not contain 'has_amd_gpu_accelerator'
Mojo/src/gpu/analyzer_intel.mojo:25:5: error: module 'info' does not contain 'has_apple_gpu_accelerator'
```

Both files import the three dead names (analyzer.mojo:21-25, analyzer_intel.mojo:21-26)
and call them in an "Accelerator census" print block (analyzer.mojo:164-177,
analyzer_intel.mojo:166-179). `has_accelerator()` (std.sys) and
`num_logical_cores()` (std.sys.info) still work — verified by a scratch probe
(`4 cores, accelerator: False`).

Replacement API (Mojo 1.2.0 release notes, "Breaking changes" / stdlib docs):
`std.sys.default_accelerator()` returns a `CompilationTarget` with predicate
methods `.is_nvidia_gpu()`, `.is_amd_gpu()`, `.is_apple_gpu()`. Canonical pattern:

```mojo
from std.sys import default_accelerator
comptime if default_accelerator().is_nvidia_gpu():
    ...
```

For a device context, `ctx.target.is_nvidia_gpu()` is the equivalent.

## Slices

**S1 — migrate `Mojo/src/bench/analyzer.mojo`.**
Replace the three names in the import block with `default_accelerator` from
`std.sys`; rewrite the census print to call
`default_accelerator().is_nvidia_gpu()` / `.is_amd_gpu()` / `.is_apple_gpu()`.
Build and run the binary to confirm identical output (census all False on
this host). Keep the `comptime if has_accelerator()` GPU tier unchanged.

**S2 — migrate `Mojo/src/gpu/analyzer_intel.mojo`.**
Same migration at its import block and census print. This file uses the
vendored `Mojo/vendor/mojo-intel-gpu` shim (Level Zero) — build with the
`MOJO_IMPORT_FLAGS` from Makefile.mojo (the standard `make mojo-build` path)
to confirm the shim still composes with the new API.

**S3 — verify full build + parity.**
`make mojo-build` → all 29 binaries green; `make mojo-test`; `make mojo-parity`
→ 92/92 keys. Confirm `make -f Makefile.mojo -k mojo-build` produces zero
errors (the keep-going sweep). Record the results here and flip status to
EXECUTED with the completed.md number.

## Acceptance criteria

- `make -f Makefile.mojo -k mojo-build` → zero errors, all 29 targets built.
- `make mojo-test` and `make mojo-parity` (92/92) green.
- No behavior change on an accelerator-free host; GPU-tier code still guarded
  by `has_accelerator()` / `default_accelerator().is_*_gpu()`.

## Risk / doctrine notes

- No query-visible semantics change → no `ontology_eval_gate` bullet required.
- Mechanical API drift fix, not an unfreeze of the Mojo lane
  (`mojo_12_adoptions.md` freeze UNAFFECTED).
- Part of the recurring "re-run trials each minor" doctrine
  (`mojo_12_changelog_trials.md:156-159`); this is the 1.2.0 build break,
  distinct from the 8 trials T1–T8 already recorded there.
- Operator ruling m01133: park all gates until further notice — this arc does
  NOT run `make qa`/`perf`; only targeted Mojo build/test/parity checks.

## Status 2026-11-18 — EXECUTED

- S1: `Mojo/src/bench/analyzer.mojo` — import block collapsed to
  `from std.sys import default_accelerator, has_accelerator, simd_width_of`
  + `from std.sys.info import num_logical_cores`; census print now calls
  `default_accelerator().is_nvidia_gpu()/.is_amd_gpu()/.is_apple_gpu()`.
  Verified: builds clean, runs, census `4 cores / False / False / False`.
- S2: `Mojo/src/gpu/analyzer_intel.mojo` — same migration; builds clean WITH
  the vendored mojo-intel-gpu shim (all MOJO_IMPORT_FLAGS present).
- S3: `make -f Makefile.mojo -k mojo-build` → ZERO errors, all 29 binaries
  (incl. `bfs_bipartite_all_batch`) built; `make mojo-test` → 5 files, all
  passed (yaml suite 8/8); `make mojo-parity` → 92/92 keys, GOLDEN PARITY OK.
- `mojo format` on both files: left unchanged (gate-clean).
- No `make qa`/`perf` per m01133. No git/stg/patch ops by the agent; changes
  left dirty for operator staging. completed.md number assigned by operator at
  archive time (this proposal stays in `proposals/` with
  `status: executed` until the operator archives it and mints the number).
