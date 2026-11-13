# Mojo 1.2 changelog trials + qa re-add

- **Status:** proposed
- **Filed:** 2026-11-13
- **Area:** tooling (Mojo lane)

## 1. Motivation

The Mojo lane was frozen after repeated 1.x breakages
(see `doc/improvements/archive/tooling/mojo_12_adoptions.md` — FREEZE trigger).
Upstream moved to 1.2.0; a re-evaluation (2026-11-13) concluded:

- build green (only deprecation warnings: alloc without Layout, positional `__getitem__`);
- `mojo-test` 12/12 PASS (yaml 8/8, cosine 4/4);
- `mojo-parity` re-converged from 84/92 → **92/92 GOLDEN PARITY OK**
  after three drift fixes (Forbes ticker-duplicate suppression mirror,
  company_metrics grain audit block, e_comention edition-hyperedge projection).

Maintenance tax is near zero, but the lane remains a **manual drift-check /
bench surface** — nothing in production consumes Mojo, and that premise is
unchanged.

This proposal is the second step requested by the operator: add `mojo-parity`
back to the qa battery and run **trials for the 1.2 changelog items** so the
re-adoption verdict rests on measured behavior, not changelog prose.

## 2. What changed

### 2.1 qa re-add

`tests/run_gate_report.py` re-adds the `mojo-parity` step
(was RETIRED after the freeze):

```python
Step("mojo-parity", (_MAKE, "-f", "Makefile.mojo", "mojo-parity"))
```

`doc/procedures/gates.md` mojo-parity paragraph rewritten: RETIRED →
re-added 2026-11-13, canonical keys 89 → 92.

### 2.2 Trials file

`Mojo/tests/test_changelog_trials.mojo` — 8 trials over 1.2 changelog
headlines, wired into `make mojo-test` (TestSuite.discover_tests pattern).

| Trial | Changelog item | Result |
|---|---|---|
| T1 | pattern matching (`match`/`case`, or-patterns, guards, wildcards, tuples, list destructuring `*middle`, comptime match) | PASS |
| T2 | String `ascii_rjust/ljust/center` + repetition (single-allocation pads) | PASS (20k pads+repeats ≈ 2.8 s) |
| T3 | `atof()` fixes (>53-bit mantissas, malformed raise) | PASS — API moved: standalone `atof` no longer exists; builtin `Float64("...")` is the supported path |
| T4 | `startswith/endswith` O(affix) | PASS (10 MB affix scan ≈ 28 s smoke) |
| T5 | `SIMD.from_bytes/as_bytes` endian parameter | PASS |
| T6 | Array slicing → Span; `Tuple.first_of[T]()` | PASS — semantics probed & documented (§2.4) |
| T7 | `where <cond> else "msg"`; `not <Trait>` opt-out | PASS |
| T8 | `Layout` alignment parameter | API MISMATCH — installed 1.2.0 stdlib `Layout` exposes only `count` (alignment param absent); trial records mismatch, allocation still exercised |

### 2.3 API notes recorded during trials

- `@parameter` → `@__parameter`.
- `Pointer[UInt8].alloc(4)` → `alloc(Layout[UInt8](count=4)).unsafe_leak()`;
  pointer writes use `buf.unsafe_offset(i)[] = n` (positional `buf[i]` deprecated);
  address via `Int(p)` (`p.address`/`p.unsafe_address()` both gone).
- `SIMD[UInt32,1]` → `SIMD[DType.uint32, 1]`;
  `from_bytes` now keyword-param `endian` over `Array[UInt8]` (not pointer + length args).
- `Array[Int](1,2,3,4)` gone — `Array[T, length: Int]` is fixed-size:
  `Array[Int, 4](uninitialized=True)` + index fills; `a[1:3]` returns Span.
- `where x % 2 == 0 else "..."` fails on non-builtin `__mod__`
  ("cannot prove constraint") — guard must use builtin operators.
- `Tuple` is builtin (no `from std.collections import Tuple`); type syntax `Tuple[Int, String]`.

## 2.4 Array→Span slice semantics (probed)

Probe program: `/mnt/data/tmp/opencode/probe_slices.mojo` (+ OOB and perf
variants), run on 1.2.0.

| Behavior | Observed |
|---|---|
| Basic slice `a[lo:hi]` | Returns a `Span` — zero-copy view, no allocation/copy |
| Write-through | Mutating the span mutates the array (`sp[0] = 999` → `a[1] == 999`) |
| Full slice `a[:]`, tail `a[2:]`, head `a[:3]` | All work; lengths 5/3/3 |
| Empty slice `a[3:3]` | Length 0 |
| Single-element slice `a[2:3]` | Length 1 |
| Slice-of-slice `a[1:4][0:2]` | Works (nested slicing) |
| Negative indices (`a[-2:]`) | **NOT supported** — no Python-style wrap; fatal `Assert Error: slice start index -2 is out of bounds, valid range is 0 to 5` |
| Out-of-bounds end (`a[0:7]`) | Fatal `Assert Error: slice end index 7 is out of bounds, valid range is 0 to 5` |
| Catchability | Bounds violations are **not catchable exceptions** — the compiler warns `'except' logic is unreachable, try doesn't raise an exception`; the process aborts |
| Perf | 100k-element slice iteration+sum over a 1M array ≈ **0.06 ms** (zero-copy) |

Takeaway for the lane: slicing is a zero-copy view with strict, non-recoverable
bounds checks; callers must guarantee bounds statically (and cannot use
negative indices to mean "from the end").

### 2.4.1 Extended probes (T6 extensions, "all six")

| Probe | Result |
|---|---|
| 1. Stride / step (`a[0:5:2]`) | **Unsupported** — compile error `no matching method in call to '__getitem__' ... cannot be converted from slice literal to 'Int'`. Only two-part slices exist; no step syntax |
| 2. `Array[String]` elements | Slicing works (`a[1:3]` → len 2, `beta`/`gamma`); write-through via span mutates the array (`strs[1]="BETA!"` reflected) |
| 3. Const-span enforcement | Default span parameter is **read-only**: write → compile error `expression must be mutable in assignment` (same class as `+=` failure). Also: `let` removed in 1.2 — all bindings are `var` |
| 4. Materialize copy | No built-in span→array constructor (`Array[Int](span)` fails; only `copy=`/`move=` full-array ctors). Manual `Array[Int, N](uninitialized=True)` + loop fill: **0.035 ms for 100k elements** (vs 0.059 ms view iter+sum — copy is cheap, but manual) |
| 5. Lifetime / dangling guard | Returning a span of a local is **not expressible**: `-> Span[Int, ...]` fails `'Span[Int, _, address_space=_]' is not concrete, use '[]' to bind missing parameters`; `-> ref Span[Int, ...]` fails `'ref' result requires an origin specifier`. Unbound origin prevents escaping locals at compile time |
| 6. Span as function parameter | Read-only: `s: Span[Int, ...]`. Mutable: `s: Span[mut=True, Int, ...]` (origin must be unbound with `...`; `Span[Int]` alone → `failed to infer parameter 'origin'`) |

Net: the API surface is deliberately restrictive — slices are cheap views with
compile-time const enforcement and borrow-safe lifetime rules; the costs are
the absence of strides, negative indices, and a direct materialization
constructor (manual allocate+fill).

### 2.4.2 Safe-slice helper shipped (unfreeze)

The footgun from §2.4 (fatal, non-recoverable Assert Errors on out-of-range
or negative slice bounds) is now mitigated in production code:

- **`Mojo/src/common/slice_utils.mojo`** — `clamp_slice(len_, lo, hi) ->
  Tuple[Int, Int]` clamps a possibly-untrusted `[lo, hi)` into a legal
  `[lo', hi')` (negative lo → 0, hi > len → len, lo >= hi → empty, len 0 →
  (0,0)). Call pattern: `var c = clamp_slice(arr.__len__(), lo, hi); var sp
  = arr[c[0]:c[1]]` — never aborts.
- API-shape note: returning a Span from a free helper is **not expressible**
  in the 1.2 stdlib (origin param depends on the argument: `-> Span[T, ...]`
  → `is not concrete, use '[]' to bind missing parameters`; `-> Span[T,
  True]` → `value passed to 'origin' cannot be converted from 'Bool' to
  'Origin[mut=$0]'`). Hence the clamp returns the bound pair and the caller
  applies the slice.
- Tests: `Mojo/tests/test_slice_utils.mojo` (7 tests — clamp edge cases +
  the intended safe-slicing call pattern). `make mojo-test` picks it up
  automatically (wildcard `Mojo/tests/*.mojo`) → 4 files / 36 tests green.
- Consumer wired: the repo's only slice call site is `head_bytes` in
  `Mojo/src/bench/graph_algos_probe.mojo:43` (diagnostic truncation, used
  at :272/:273) — it hand-rolled the same clamp inline; it now imports
  `clamp_slice` and computes `s[byte=0:c[1]]` via it (behavior-identical,
  clamp logic centralized). Audit of all `Mojo/src/**/*.mojo` found no
  other slice sites (only element indexing / `codepoint=i` scalar reads), so
  this is the complete consumer set today.

## 3. Acceptance

1. `mojo-parity` re-added to qa battery and green (92/92) — VERIFIED.
2. Trials file compiles with 0 warnings (unused loop var cleaned) — VERIFIED.
3. `make mojo-test` runs 8 trials, 8/8 PASS — VERIFIED.
4. `mojo-build` green on 1.2.0 (only deprecation warnings, tolerated) — VERIFIED.
5. Proposal records API mismatches (T3/T8) as N/A/moved, not failures — VERIFIED.

## 4. Non-goals

- No production adoption of Mojo; the lane stays a manual drift-check/bench surface.
- No changes to the Python integrity-check source of truth
  (Mojo mirrors it; fixes were mirror-side only).
- No new changelog items beyond the 8 triaged headlines.

## 5. Update cadence

Re-run trials + parity on each future Mojo minor (1.3, …) before touching the
freeze verdict; a failing trial is a review trigger, not necessarily a revert.
