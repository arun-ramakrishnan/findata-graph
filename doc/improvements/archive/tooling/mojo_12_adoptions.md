---
title: "Mojo 1.2 toolchain adoption census — pattern matching, numpy N-D bridge, compile speedups"
status: deferred
filed: "2026-10-24"
executed: "2026-10-24"
completed_md: "437"
area: "Mojo toolchain (Mojo/src, Mojo/tests, Makefile.mojo, doc/local/mojo)"
---

# Mojo 1.2 toolchain adoption census

**Date:** 2026-10-24 · **Status:** proposed (adoptables are small and
mostly "use when writing new code" — no dedicated port arc proposed)

## 1. Motivation

The Mojo toolchain has moved two full release cycles since the pilot's
1.0.0 pin, and the repo's Mojo facts predate both:

- **1.0.0 / MAX 26.5.0** (11 Aug 2026) — the evaluated + adopted baseline
  (completed.md #183–#188 production interop arc; pilot doc 2026-08-28).
- **1.1.0 / MAX 26.6.0** (17 Sep 2026) — already absorbed: the pilot was
  re-migrated and "FULLY RESTORED on Mojo 1.1.0 + MAX 26.6.0" on
  2026-09-20 (footprint repair #247 kept the pin there). 1.1 REMOVED the
  `fn`/`alias`/`__comptime_assert` keywords and `@parameter if`/`for`.
- **1.2.0 / MAX 26.7.0** (22 Oct 2026) — landed via the operator's venv
  sync 2026-10-23 07:20, silently. Verified green the same way it
  arrived: qa `mojo-format` + `mojo-parity` and advisory `mojo-test` all
  passed on 1.2.0 (2026-10-24 gate runs).

Compatibility census (2026-10-24, this proposal's evidence):

- `rg` over `Mojo/src` + `Mojo/tests` for every removed spelling from
  both cycles (`fn`, `alias`, `@parameter if|for`, `ImmutOrigin`,
  `UnsafeMaybeUninit`, `MutStringSpan`, `from std.gpu`,
  `is_big_endian`, `as_string_slice`, `steal_data`, `std.hashlib`) —
  **zero hits** (one comment-level mention only). No code action.
- `.venv/bin/mojo --version` → `Mojo 1.2.0 (2016d0cd)`; `max --version`
  → `26.7.0`.

## 2. The standing negatives — re-verified, unchanged

Both shaping constraints of the house Mojo setup were re-verified
against the 1.2 CLI (`--help`-level evidence, not memory):

1. **No `mojo test` CLI** (still `run | build | repl | debug |
   precompile | format | doc | demangle`). The per-file `mojo run`
   harness (`make mojo-test`, one process per test file) remains
   necessary.
2. **No `mojo format --check`** (exit 1 on the flag). The tmp-copy +
   byte-diff format gate (tests/test_lint_gates.py) remains necessary.
3. **Concurrency surface untouched**: neither release notes touch
   `std.concurrent` / `std.runtime.asyncrt` — still no `parallelize`,
   channels, or condvars. `TaskGroup` remains the sole sanctioned
   in-process fan-out; `mojo_concurrency.md` doctrine unchanged.

## 3. Adoptable items (with disposition)

| # | Item | Cycle | Value here | Disposition |
|---|------|-------|-----------|-------------|
| 1 | **Pattern matching**: `match`/`case` with literals, or-patterns, tuples, guards, struct patterns, `if case` / `while case`, list destructuring, `comptime match` (type switch) | 1.2 | Moderate; our Mojo is kernels/benches/bridges, not parsers — no existing call site argues for a port | **Use when writing new code**; no retro-port — **PROVEN on this toolchain 2026-10-24** (scratch probes, see §3b) |
| 2 | **Numpy N-D bridge**: `copy_to_numpy_tensor()` / `from_numpy_tensor()` (borrowing `NumPyView`) | 1.1 | Could tidy bench-leg marshaling; the `aligned_array()` pattern already works and the memmap→DLPack SEGV trap is documented around it | **Optional / hold** — do not touch proven benches for cosmetics |
| 3 | **Faster compilation** (jemalloc on Linux, ~10–13% on large files) | 1.2 | Already realized by the sync; modestly helps `mojo doc` indexing (~3.5 s/file in the script_search sidecar) and gate legs (mojo-parity 35 s) | **Free win — nothing to do** |
| 4 | **`Atomic[T]` value-typed** (`Atomic[Float32]`, not `Atomic[DType.float32]`) | 1.1 | Cosmetic for `spinlock_counter.mojo` | **Opportunistic** — fold in if that file is touched anyway |
| 5 | Stability marks on `String` / `SIMD` / `List` (1.1); CPU/GPU-unified `std.math` incl. `log()` float64 ≤1 ulp (1.2) | 1.1+1.2 | Reassurance; no GPU target on this box (HD 530 iGPU) so the math unification is moot | **No action** |
| 6 | 1.2 string perf (`startswith`/`endswith` O(affix), ascii padding ≤86×, faster `atof`) and `SIMD.from_bytes`/`as_bytes` `Endian` API | 1.2 | No hot string/bytes path in our kernels | **No action** |

### 3b. Pattern-matching probe evidence (2026-10-24, scratch files, `mojo run` on 1.2.0)

- **Proven**: `match`/`case` with or-patterns (`case 301 | 302:`), guards
  (`case c if c >= 400:`), wildcard; list patterns with rest capture
  (`case [first, *middle, last]:` over `List[Int]`); `if case .Some(v) = opt:`
  on `Optional[Int]` including the `.None` fall-through. Guidance is
  evidence-backed, not upstream-assumed.
- **Traps hit (new-code rules)**:
  - `fn` is REMOVED in 1.2 (def-only) — probe error confirms the 1.1
    removal applies to fresh code, not just old code.
  - `from collections import Optional` fails — module path gone;
    `Optional` is prelude, import nothing.
  - `Array[Int]()` has no default constructor (candidates require
    `uninitialized`/`fill`/length params) — use `List[Int]()` + `append`,
    or a sized/fill `Array` init.
  - The **destructuring-assignment** form (`var [a, *b, c] = vals`)
    parsed but failed to bind names ("use of unknown declaration") even
    where the equivalent match-pattern works — observed once on 1.2.0;
    treat the `match`-pattern form as the proven route until upstream
    clarifies.
- Reference: pattern grammar table at
  <https://mojolang.org/docs/reference/compound-statements.md>
  (or-patterns, `==` value patterns, struct/enum-case/list patterns,
  `var`/`ref`/`as` binding forms).

## 4. Acceptance

No eval-gate bullet (no query-visible semantics change). Acceptance for
any slice actually executed: the three Mojo gate legs green on the
target toolchain — qa `mojo-format` + `mojo-parity`, advisory
`mojo-test` — plus `make search-fresh` convergence after any `doc/`
edit.

## 5. Non-goals

- No port arc for items already proven on 1.0 (bridges, cosine kernel,
  integrity_check port, TaskGroup fanout probes).
- No re-opening the GPU lane: `max.gpu` absorbing `std.gpu` (1.1) and
  the 1.2 accelerator predicates (`is_nvidia_gpu()` etc.,
  `std.sys.current_accelerator()`) don't change this machine's
  no-official-target status; the community L0 shim path is already
  restored (pilot doc §Update 2026-09-20).
- No pixi/conda revisit (channel-wide dead, pilot doc §pixi).

## FREEZE (2026-10-26, operator ruling: "Mojo is increasingly unviable")

The Mojo lane is FROZEN — no further ports, no mirroring obligation.

- **Survey finding that decided it**: nothing in production consumes
  Mojo. No Makefile target, helper, or app.py path invokes
  `Mojo/bin/*`; the only runners of Mojo code are the gate/bench
  machinery itself. The graph read path went to DuckDB hyper
  projections (#438) and the one real perf-wall kernel went to Rust
  (`Rust/cosine_knn`, #440).
- **Consequence**: `mojo-parity` retired from qa and `mojo-test` from
  advisory (tests/run_gate_report.py, same day) — the parity leg was
  the maintenance contract for the second implementation of
  `database_integrity_check.py` (Python is the source of truth), and
  every Python-side semantic change re-paid the port by hand; the
  2026-10-26 person-exemption port (mojo-parity 78/88 -> 88/88) was the
  last one.
- **What stays**: the `make mojo-{build,parity,test,bench,format}`
  targets as manual surfaces, `Mojo/` source as the historical record,
  the qa format gate in test_lint_gates (free; catches accidental edits
  to frozen code), the pyproject `mojo` optional-dep group, and this
  document as the toolchain/bench-traps reference.
- **Unfreeze trigger** (if ever): a measured wall that neither the
  native Python layer nor Rust closes, with mojo-parity re-converged
  BEFORE any new port work starts.
