---
title: "Jemalloc global allocator for the Rust surfaces — scoped to where churn exists"
status: executed
filed: "2026-11-01"
executed: "2026-11-01"
completed_md: "468"
area: "desktop/src-tauri (primary); Rust/ crates (ruled out by measurement)"
---

# Jemalloc global allocator for the Rust surfaces — scoped to where churn exists

**Date:** 2026-11-01 · **Status:** EXECUTED (S1; S3 parked) · **completed.md:** #468 ·
**Area:** `desktop/src-tauri` (primary); `Rust/` crates (ruled out by measurement)

## 1. Motivation

Operator-requested (2026-11-01): Rust's default global allocator has been
the system allocator since Rust 1.32; a `tikv-jemallocator`
`#[global_allocator]` swap is the standard fragmentation remedy, with
production evidence (Freenet: glibc malloc RSS 5–6 GB → OOM in hours under
WASM-compile churn; jemalloc stabilized ~2.4 GB, heap-profiling confirmed
fragmentation not leaks). The mechanism is real and cheap to integrate —
but it pays only on **long-running processes with high-frequency small
object churn**, which is exactly what this repo's prior measurement history
(#436 jemalloc preload arc) already established. This proposal scopes the
idea to the surfaces where churn exists and rules out where it doesn't.

## 2. Evidence (measured 2026-11-01, this box)

**This repo's own allocator history (2026-10-26 arcs #436 + Phase C Part
A):** jemalloc tight-decay is RSS-neutral on ALL short one-shot legs
measured (doc-rebuild, note-rebuild, graph-rebuild, snapshot,
stamp-centrality — 5 legs, ×2 reps, glibc vs jemalloc within noise);
fragmentation-ratchet bites only day-scale residents, which is why the
Makefile preloads jemalloc via `LD_PRELOAD` for exactly the long-running
processes we run (3 llama-servers + quack). The Freenet case is the
day-scale resident pattern, not the one-shot pattern.

**This repo's Rust-internal allocation profile** (counting global
allocator over `System`, throwaway test, measured 2026-11-01):

| Surface | Rust-internal heap churn | Verdict |
|---|---|---|
| cosine_knn @ production 717×512, k=10 (single path) | **176 bytes/query** (~157 KB/s at 892 q/s) | allocator swap irrelevant |
| cosine_knn @ 5k×512 (parallel threshold) | 2,280 bytes/query | trivial |
| cosine_knn @ 100k×512 | 40 KB/query | trivial vs Phase C 26 ms/query |
| cosine_knn @ 1M×512 | 359 KB/query | trivial vs Phase C 197 ms/query |

What the numbers mean: the cdylib surfaces (`cosine_knn`, `embed_cache`)
allocate almost nothing — the matrix arrives as a zero-copy numpy borrow
(the 2026-10-25 API-boundary lesson), and results are small top-k Vecs.
Fragmentation needs sustained churn; 176 B/query cannot produce it, and a
per-cdylib `#[global_allocator]` would only affect Rust-internal
allocations anyway (Python's process allocator governs everything else).
**The only long-running Rust binary with real churn is the desktop tauri
app** (wry/webview/event-loop buffers) — and that is where the Freenet
pattern could plausibly apply.

## 3. Design

Slices:

1. **S1 — desktop trial.** `desktop/src-tauri/Cargo.toml`:
   `[target.'cfg(not(target_env = "msvc"))'.dependencies]
   tikv-jemallocator = "0.6"`; `#[global_allocator]` in the entry file
   behind the same cfg (jemalloc does not support MSVC). Optionally narrow
   to Linux (`cfg(target_os = "linux")`) — the operator's note: macOS
   system allocator already performs well; Windows excluded by cfg.
   A/B: default build vs jemalloc build, RSS measured over a fixed
   30-minute idle+interaction script, ×2 runs each (timing-law: never one
   run), quiet box.
2. **S2 — verdict + rollout or revert.** If RSS ratchet absent (expected:
   tauri apps allocate large buffers, not high-frequency small objects —
   the pattern jemalloc helps least): close as measured-no-action, keep
   the cfg'd dep out. If a ratchet shows: keep, and document in
   THIRD_PARTY_LICENSES.md (tikv-jemallocator = BSD-2-Clause + jemalloc
   BSD-2-Clause — compatible with the deny.toml allow list already).
3. **S3 — the actual resident lever (env-only, no rebuild): app-server
   LD_PRELOAD trial.** The real day-scale resident with churn in this
   repo is the Python app server (17.5K-vector loads, sqlite/duckdb
   buffers — and it imports the cosine_knn cdylib, whose allocations
   LD_PRELOAD reroutes too). The Makefile already carries the recipe
   (Makefile:31-41, JEMALLOC_ENV/JEMALLOC_LIB). Phase A measured
   RSS-neutral only on SHORT legs; the server leg was explicitly listed as
   a candidate ("app server RSS watch") and never measured. 30-min A/B
   with the same measurement script as S1.

Alternatives considered and lost for S1: **mimalloc / snmalloc** — both
viable (`#[global_allocator]`-compatible), snmalloc sometimes wins on
memory; but tikv-jemallocator is the recorded standard, has the
tight-decay tuning knobs we already use house-wide (MALLOC_CONF), and S3's
LD_PRELOAD route uses the SAME jemalloc — one allocator family to reason
about. Reopen mimalloc only if S1/S3 measure a residual ratchet jemalloc
doesn't fix.

## 4. Acceptance criteria & shakedown

1. S1/S3 A/B: each configuration measured ×2 on a quiet box, RSS sampled
   at fixed intervals; verdict from the RATCHET (slope over time), not
   peak.
2. If adopted: `cargo build` green both debug+release; desktop smoke
   (app launches, window renders); `make rust-deny` licenses stay green
   (BSD-2-Clause).
3. If rejected: proposal addendum carries the RSS tables; cfg'd dep
   removed; nothing lands in Cargo.toml.

| Projected outcome | Today | After (expected) |
|---|---|---|
| desktop RSS ratchet | unmeasured | measured (likely absent — large-buffer pattern) |
| app-server RSS ratchet | unmeasured (leg never run) | measured; jemalloc expected ≥ neutral, per llama/quack precedent |
| cosine_knn/embed_cache | 176 B/query churn — ruled out | untouched |

## 5. Risks

- **Two-allocator confusion in-process (S1)** — jemalloc inside the tauri
  binary is process-wide there (it IS the executable), so no boundary
  issue; the cdylib case (S3) is covered by LD_PRELOAD replacing the
  process allocator uniformly. Never mix a per-cdylib global_allocator
  with the host's allocator — deallocation must return to the allocating
  heap.
- **Desktop build tax** — S1 costs one clean rebuild (the S1 A/B
  inherently builds twice; incremental artifacts differ per allocator).
- **Confirmation bias toward adopting** — pre-registered: adoption
  requires a measured ratchet, not "jemalloc feels better" (#436's verdict
  law: tight-decay, residents only, measured or it doesn't land).

## 6. Non-goals

- **No `#[global_allocator]` in `Rust/cosine_knn` or `Rust/embed_cache`**
  — ruled out by the 176 B/query measurement. Mechanism, stated exactly:
  the swap DOES replace the cdylib's internal (Rust) allocator — but
  nothing else. The host process keeps glibc/pymalloc for the interpreter,
  numpy, sqlite, DuckDB, the embed stack, so the result is two allocator
  instances in one process, with jemalloc governing a ~176 B/query sliver
  of the churn. Fragmentation remedies pay on the dominant allocator's
  churn (Freenet's WASM compiles WERE its workload); a sliver-scoped
  allocator cannot move the ratchet. The variant that replaces the
  internal cdylib allocator AND everything else is LD_PRELOAD — that is
  S3, and it must never be combined with a per-cdylib global_allocator
  (two jemalloc instances in one process).
- No changes to the llama/quack LD_PRELOAD wiring (already deployed,
  #436 KEEP verdict).
- No MALLOC_CONF tuning changes (tight-decay profile stands).
- No Windows/macOS support work (cfg-excluded).

## 7. Execution addendum (2026-11-01, same day — S1 EXECUTED as direct adopt on operator ruling)

- **S1 wiring landed**: `desktop/src-tauri/Cargo.toml` gains
  `[target.'cfg(not(target_env = "msvc"))'.dependencies]
  tikv-jemallocator = "0.6"` (non-optional) and `main.rs` the
  `#[cfg(not(target_env = "msvc"))] #[global_allocator]` — unconditional
  for every non-MSVC target. Release build green (30.7 s incremental).
- **Verification**: 527 `_rjem_*` symbols + `/etc/_rjem_malloc.conf` path
  in the release binary — jemalloc compiled in and active as the Rust
  global allocator. `cargo audit` on the desktop lock: unchanged (same 5
  accept-risk warnings; jemalloc adds none).
- **Trials aborted, honestly**: the planned 2×2 RSS A/B never produced a
  measurement. The debug build serves the tauri devUrl error page (plain
  `cargo build` does not embed the frontend); a release rebuild still
  showed the operator a localhost error, so UI-driven churn measurement
  was impractical and the operator ruled: "No worries. Just plug the
  allocator in." — adoption without the measured-ratchet gate, an
  explicit exception to the #436 law recorded here at their instruction.
  The A/B leftovers (driver, stashed binaries, tsv) were killed/removed
  with witr-verified pids.
- **Scope note**: the allocator governs Rust-internal allocations of the
  binary; GTK/WebKit C allocations still go through glibc (tikv-jemalloc
  does not interpose malloc). The webview runs as separate processes with
  their own allocator. Never add an LD_PRELOAD jemalloc to this app now.
- **S3 (app-server LD_PRELOAD leg) PARKED** — filed as a pending.md row;
  it remains the measured-or-not lever for the actual day-scale resident.
