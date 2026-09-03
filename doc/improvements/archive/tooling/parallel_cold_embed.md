---
title: Parallel cold embed — process pool for the bge-small llama.cpp path
status: executed
filed: '2026-08-29'
executed: '2026-08-29'
completed_md: '173'
area: embeddings / `helpers/core/local_embedder.py
---

# Parallel cold embed — process pool for the bge-small llama.cpp path

**Date:** 2026-08-29 · **Status:** EXECUTED 2026-08-29 (completed.md #173) ·
**Area:** embeddings / `helpers/core/local_embedder.py` + `helpers/core/embed_cache.py`

> **Measured outcome (2026-08-29):** cold note_search 16m13s → **6m01s
> (2.70×**, 292 ms/doc eff.); cold company `--maint` ~11–15 min →
> **4m46s** (266 ms/doc eff.); warm cycles unchanged (1,237 hits / 0
> misses; vectors byte-identical). Both runs landed inside the bench's
> predicted 267–292 ms/doc window — the mechanism behaved exactly as
> measured; the bench's headline 3.7× was against the contended pinned
> single-worker baseline, production's own serial baseline (793 ms/doc)
> yields 2.7× end-to-end.

## 1. Motivation

Two cold-ingest walls share one root cause — every text goes through
`local_embedder._embed` → `create_embedding(input=[text])`, a batch-1
llama.cpp forward on a 4-core desktop (i5-6500, 4C/4T, Skylake):

| Cold path (cost table, `doc/procedures/embeddings.md`) | Corpus | Measured |
|---|---|---|
| note_search full refresh | 1,227 docs | **16m13s** (0.79 s/doc) |
| company_embeddings populate | 1,068 companies | **~11–15 min** |

Warm cycles are already seconds (content-hash cache hit rates ~100%) —
this proposal touches ONLY the miss path. Cold fires on first apply,
model swap, and the first rebuild after `snapshot-restore` (the embed
store is excluded from snapshots by design).

## 2. Evidence (measured 2026-08-28/29, this box, llama-cpp-python 0.3.35)

Per-doc cost at 512-token truncation (production shape), bge-small q8:

| Configuration | ms/doc | Verdict |
|---|---|---|
| In-process, 1 call/text (status quo) | 310–322 | baseline |
| In-process, `n_threads=2` | 335 | threads don't help |
| In-process, `n_threads=4` (default) | 322 | threads don't help |
| Sequence packing via `n_batch` 512→4096 | 0.83–1.06× | **dead** — measured, do not re-audit |
| Spawned worker, unpinned | 312 | floating is fine solo |
| Spawned worker, pinned to 1 core (desktop load ~2) | ~989 | core contention |
| **Pool N=4, workers pinned to distinct cores** | **267 eff. (3.7×)** | **adopt** |
| Pool N=8, pinned | 269 eff. | plateau at 4 workers |
| Pool N≥4, **unpinned** | 1,833–2,008 eff. | **collapse** — see §6 |

Stability: the 3.7× pinned result reproduced across two independent
sessions (agent shell load ~2–5; user shell load ~2–6) and two EPP
profiles (`performance`, `balance_performance`); the governor is
`powersave`/intel_pstate-HWP throughout and switching EPP changed
nothing measurable.

Determinism: vectors from `n_threads=1` vs default were byte-identical
(max |Δ| = 0.0 over 8×384 dims); same weights + same text ⇒ same vector
in any worker count. The pooled cache therefore needs no migration.

Model load ~0.1 s/worker; pool spawn+init ≈ 1–8 s one-time (amortized
over thousands of docs).

## 3. Design

**D1 — `local_embedder.embed_documents_parallel(texts, workers=4)`**
(spawn `multiprocessing.Pool`, one-time initializer):
- initializer: load `Llama(embedding=True, n_ctx=512, n_threads=1)`,
  then `os.sched_setaffinity(0, {core})` with `core = worker_index %
  os.cpu_count()` — **pinning is mandatory** (§6);
- worker task: per-text `create_embedding` over its chunk, returns
  `(index, vector)`; parent reassembles in input order;
- `workers <= 1` or a single text → in-process `embed_documents`
  (current path, untouched);
- empty/whitespace texts filtered by the caller BEFORE dispatch (the
  per-text contract "empty raises" stays, at the caller boundary).

**D2 — cache integration.** `helpers/core/embed_cache.cached_embed_batch`
gains the parallel embedder as its miss-path engine (hashes computed in
the parent; only misses dispatched to the pool; cache rows written by
the parent — single writer, no new locking). `CachedEmbed` per-text
wrapper is unchanged (query side and warm paths never spawn a pool).

**D3 — callers.**
- `helpers/graph/embeddings.py` (company populate / `--maint`): already
  batch-shaped via `cached_embed_batch` — picks up D2 with no call-site
  change;
- `helpers/maintenance/rebuild_note_search.py`: the per-doc
  `CachedEmbed.__call__` loop becomes two-phase (collect rows →
  `cached_embed_batch` for embeddings → FTS insert), reusing the company
  side's pattern;
- `helpers/maintenance/rebuild_doc_search.py` /
  `rebuild_script_search.py`: same two-phase switch (they already use
  `CachedEmbed`);
- warm behavior unchanged: 0 misses ⇒ no pool spawn, existing seconds-
  scale cycles stay byte-identical (stable-write discipline).

**D4 — surface.** No new CLI: `--workers` env-style default 4 via
`EMBED_POOL_WORKERS` (0/1 disables); documented in
`doc/procedures/embeddings.md` cost table + this file. Not wired into
`make perf` (cold-path only; perf budgets measure warm steady state).

## 4. Verification

- **Parity gate:** embed a real 32-doc sample through (a) current
  per-text path, (b) pool of 4 — assert byte-identical vectors (same
  assert style as the bench evidence; determinism already demonstrated).
- **Unit tests** (fake-local monkeypatch pattern, hermetic):
  `embed_documents_parallel` chunk/order correctness with a fake embedder
  injected into workers (pool size 1 + 4); empty-text filtering; cache
  hit-rate accounting unchanged (hits/misses/dirty counters);
  `cached_embed_batch` writes identical rows pool vs sequential;
  rebuild_note_search two-phase produces identical FTS rows.
- **Timing record:** one real cold run each for note_search and company
  populate after landing; update the embeddings.md cost table
  (expected: 16m13s → ~4.5 min; ~11–15 min → ~3–4 min; idle box
  potentially ~2 min at ~78 ms/doc effective).
- Standard gates: ruff, `make types`, `make types-tests`, targeted
  pytest (test_embed_cache, test_embeddings, rebuild_note_search tests).

## 5. Costs & risks

- Effort: ~half a day (D1+D2 small; D3 is three mechanical call-site
  switches + tests).
- Memory: 4 × (35 MB model + context) — trivial.
- The box is saturated during a cold run (4 pinned busy cores); desktop
  interactivity degrades for the duration — acceptable for a maintenance
  path, worth one line in the procedure doc.
- Spawn portability: `spawn` context (not fork) so no inherited-llama
  state; requires the bench-verified initializer pattern (module-level
  DOCS-style globals are NOT how production passes data — chunks are
  pickled task args).
- Failure mode: a worker crash surfaces as `BrokenProcessPool` in the
  parent — fail loud, no partial cache writes (misses are written only
  after the pool returns).

## 6. Measured-not-adopted / open mystery (record; re-audit only with new evidence)

- **Unpinned pool collapse (N≥4 → 24× per-worker slowdown, reproducible
  at load ~1.3):** cause NOT isolated — ggml has no affinity code in
  this build (verified: no affinity strings in the binding source or
  `libllama.so`), and solo unpinned workers run at full speed (312
  ms/doc). Something in concurrent unpinned ggml compute serializes;
  per-core pinning sidesteps it entirely. If ever revisited: sample
  per-thread CPU/PSR during an unpinned N=4 run.
- **Sequence packing (`n_batch` ≥ 4096):** 0.83–1.06× — llama-cpp-python
  0.3.35 does pack multiple sequences per decode, but per-seq compute
  dominates; no gain.
- **In-worker threads (2/4):** flat to worse (small matmuls).
- **8/16 workers:** plateau at 4 (4C/4T, 1T/worker).
- **EPP / governor tuning:** no measurable effect on this workload
  (`performance` vs `balance_performance` identical within noise);
  intel_pstate `powersave` governor is HWP-dynamic, not a low-clock lock.

## 7. Deferred at current scale (companion record — do not re-audit without trigger)

From the 2026-08-29 repo-wide "where does the time go" review
(maint_report.txt 66s maint-full; make perf 22/22 green):

| Item | Today | Trigger to revisit |
|---|---|---|
| Mojo KNN escape-hatch shared lib | sqlite-vec 11.8 ms @ 1.2k docs | sqlite-vec ABI/packaging pain, or corpus ~100× — if fired, engine choice is pre-measured (2026-09-02, `doc/local/mojo/mojo_pilot.md` § "Linalg CPU kernels"): single query → numpy gemv (what `FlatKNN`/`embed_matrix` already ship, measured optimal at every scale incl. K=768); batched queries → one MAX GEMM n≈8–16, 2–2.5× over numpy, matrices already 64B-aligned; n≥64 → numpy sgemm wins |
| Mojo GPU KNN kernels | crossover 17–67 MB resident | ≥50 MB resident vectors (~34k docs) — note 2026-09-02: batched CPU GEMM (MAX engine, n≈8–16) is now a measured intermediate rung before any GPU work (`doc/local/mojo/mojo_pilot.md` § "Linalg CPU kernels"); GPU crossover itself unchanged |
| Mojo fused derive-sweep kernel | derive_* ≈ 5–6 s per maint-full (~17% of post-#174 32.3 s maint-full) | regex precondition RESOLVED 2026-08-29 via the Python `regex` bridge (`tooling/mojo_regex_via_python_interop.md` in this archive, EXECUTED as #180 — moots the community port); scale trigger still unmet (derive_* at minutes scale) |
| ~~Incremental 2nd snapshot in maint-full~~ | CLOSED 2026-08-29 by #174 — maint-full takes ONE tail snapshot (66 s → 32.3 s); see `doc/improvements/archive/database/maint_full_single_snapshot.md` | — |
| recompute-graph metric fan-out | saves ~4–5 s | maint-full budget pressure |
| ~~Parallel per-table Parquet export~~ | CLOSED 2026-08-29 at per-DB granularity by #175 (one thread per DB, binary + parquet branches); per-table parallelism still deferred | same trigger — per-table only if profiled worth it |
| yfinance worker pool (cold metrics-rebuild) | one-off 10–12 min, rate-limit risk | routine full refreshes or ticker growth |
| q4_k_m quant swap (~2×/doc, small quality cost) | reserve lever | post-pool, if cold paths still hurt |
| Paddle fallback engine optimization | fallback-only path | scanned-PDF volume |

## 8. References

- `doc/procedures/embeddings.md` §Cost reference (16m13s / ~15–20 min
  rows this proposal targets)
- completed.md #124 (A1 sqlite-vec KNN), #166 (embed store consolidation —
  pooled cache this hooks into), #164 (note_search drift checks)
- `doc/local/mojo_pilot.md` §migration verdict (why no runtime Mojo; the
  SIMD KNN numbers behind the deferred escape-hatch row)
- Bench evidence inlined in §2; throwaway harnesses (spawn Pool, pinned
  initializer, per-doc latency print) — ~40 lines, reproduced in the
  parity test rather than kept as scripts.
