---
title: "Skylake iGPU eval — HD 530 measured on real corpus data; routing unchanged, GPU recipe banked"
status: executed
filed: "2026-09-20"
executed: "2026-09-20"
completed_md: "257"
area: "Mojo/src/gpu, Mojo/vendor/mojo-intel-gpu, Mojo/bench, doc/local/perf"
---

# Skylake iGPU eval — HD 530 measured on real corpus data; routing unchanged, GPU recipe banked

**Date:** 2026-09-20 · **Status:** EXECUTED (born-archived — filed
retrospectively on completion) ·
**Area:** `Mojo/src/gpu/`, `Mojo/vendor/mojo-intel-gpu/`,
`Mojo/bench/bench_lquick_knn.py`, `doc/local/perf/skylake_eval.md`
(full untracked log), demo sidecar (machine-local)

## 1. Motivation

The Mojo pilot (2026-09-02) left an open thesis: the HD 530 iGPU (Gen9,
24 EU @ 1050 MHz, unified memory) beat CPU SIMD on large resident buffers
in toy runs (268 MB: GPU 11.1 ms stable vs CPU 20–60 ms noisy), but no
real findata-graph workload had ever been measured. External research
triage (13 items, second batch) added claims to test: work-group sizing
per the Gen9 guide, adaptive heterogeneous CPU/GPU splits, thread-pool
behavior. The decision at stake: should production routing (KNN, reduce,
graphs, scans) consider the GPU at today's corpus scale (16,560×384 =
25.4 MB embed matrix, 36,582-edge graph, 24.9 MB text corpus)?

## 2. Evidence (measured 2026-09-20, this box, solo unless noted)

| Exp | Question | Result | Verdict |
|---|---|---|---|
| D residency | resident buffer ×100 passes vs CPU | GPU 1.79–1.85 ms vs CPU 2.59–2.66 ms = 1.40–1.48×, rel err 2e-07% | multi-pass amortizes the floor |
| A roofline | win vs compute intensity | L1 sum 1.44× / L2 x⁴+x² 1.57× / L3 transcendental 16.5×; GPU wall FLAT (1.72–1.73 ms) | grows with ALU-per-byte; floor masks kernel (true ratios higher) |
| B tuning | beat the 1.82× copy-ceiling | f4@group-64 1.307 ms = **1.89×** vs CPU 2.47 ms | group-64/float4 optimum; g256 default left ~0.5 ms on the table |
| E hetero | CPU+GPU halves > GPU alone | 25/75: 2.55 / 50/50: 2.05 / 75/25: 1.42 ms — every split loses to GPU-only 1.31 ms (best 0.92×) | REFUTED — one shared DRAM bus; CPU leg inflates 1.4–2× under contention |
| C zero-copy | zeMemAllocShared pay? | upload 10.6 vs 22.3 ms = **2.12×** (H2D eliminated); kernel wall identical; D2H skipped | ADOPT allocate_shared as default buffer path |
| L KNN control | E's CPU-leg baseline | numpy serial 0.92–1.0 ms/query stable; FlatKNN 1.4–1.7; ProcessPool 2–19 ms (session-unstable, IPC-dominated) | numpy serial is the CPU leg; TaskGroup clean (2.66×, no #521/#2398 repro) |
| F/G graphs | GPU BFS/APSP at 36k edges | CSR 0.59 MB ≈ 30× below the 17 MB floor; native CPU BFS sub-100 µs | closed — revisit ~2M+ edges |
| J/H/I domain | oneDNN-GPU / regex / hypergraph | oneDNN Debian build CPU-only (`gpu,runtime:none`) AND Gen9 dropped upstream in v3.4; regex buffer 24.9 MB + no state-table engine; hypergraph 0.048 MB | all closed by gates |

Mechanisms (why): (1) the **~1.15 ms dispatch floor** is size-invariant
and paid per pass — it explains D (amortize), E (split doesn't shrink it),
F/G (below floor = no game); (2) **unified memory cuts both ways** —
zero-copy is perfect (C) AND co-execution cannot add bandwidth (E);
(3) the **roofline is steep** — FMA-bound caps at ~1.5–1.9×, transcendental
hits 16.5×+ (Skylake CPU has no SIMD transcendentals; the GPU does).

## 3. Design (as executed)

Three phases + end-block, 5–10 min quicks first, 30-min runs deferred to
the end (per-candidate budget rule):

1. **Phase 1** — D (multi-pass residency on the real matrix), L-quick
   (CPU-leg control), L-gate (`parallelize` isolation).
2. **Phase 2** — F/G (graph CSR triage, closed without a kernel), A
   (compute-intensity sweep: 3 OpenCL kernels `roofline_l1/l2/l3` + CPU
   simd32 companions OP-for-OP), B (work-group {16..256} × vector width
   {f4,f8,f16} × unroll sweep).
3. **Phase 3** — J/H/I domain quicks, all resolved by measured gates.
4. **End-block** — E (heterogeneous splits {25/50/75}%), C
   (`zeMemAllocShared` — required shim work: loader binding + context
   `allocate_shared`/`free_shared`), L-short (N=512 completion point).

Infrastructure banked: kernels + harnesses moved to `Mojo/src/gpu/`
(7 `.mojo` + 3 `.cl` + 3 pinned `.spv`), patched shim vendored at
`Mojo/vendor/mojo-intel-gpu/` (upstream andomeder/mojo-intel-gpu @
ee877f6 + zeMemAllocShared delta; local-only per the vendor-dir
convention), `Makefile.mojo` vendor import flag (all 7 harnesses compile
under `make mojo-build`; smoke-verified `svm_real` runs from the main
repo), `bench_lquick_knn.py` gains `--n/--reps`. Compile pipeline pinned:
`clang-21 -cl-std=CL3.0 -target spirv64 -emit-llvm -c` → `llvm-spirv-21`.

## 4. Acceptance criteria (as run)

1. Every GPU/CPU comparison: sums verified against the known full-matrix
   sum (-4049.255781) and sumsq (16560.000005), rel err < 1e-06%.
2. ≥100 passes per timing (≥3 reps for KNN legs); min/avg/max reported —
   the pilot's jitter lesson (avg-vs-avg is not evidence).
3. Solo runs for headline numbers; the one non-solo leg (L-short pool) is
   labeled load-confounded and its verdict rests on the solo L-quick data.
4. Gates: `make qa` 10/10 after the arc (operator-run); md-lint, ruff,
   lint-audit, live-invariants green (live-invariant flake root-caused and
   fixed in passing: TTL-cached synthetic connect error leaked across
   tests — `tests/test_api_graph_live.py` now resets after the 500-path
   test).

## 5. Verdict

**Production routing UNCHANGED — by measurement, not by default.** No
current workload crosses the honest >50 MB bar (matrix 25.4 MB, graph
0.59 MB, corpus 24.9 MB); CPU stays right everywhere it already runs.
The GPU is nonetheless proven and cheap to reach: shim vendored, recipe
pinned (f4@group-64, `allocate_shared` zero-copy default, `.spv` +
compile pipeline archived), with a measured answer for every future
"should this go to the GPU?" question at this scale.

## 6. Risks / caveats

- **Gen9 is end-of-life upstream** — oneDNN dropped it in v3.4; a J retry
  needs oneDNN v3.3.x built with `ONEDNN_GPU_RUNTIME=OCL` (Intel GPUs
  only) and would still be dispatch-floor-bound at 25 MB.
- **Dispatch floor is driver-bound** (~1.15 ms launch+sync on this L0
  stack); a driver update could shift every crossover number.
- **`.spv` binaries are pinned artifacts** — regenerating needs
  clang-21/llvm-spirv-21 exactly (llvm-spirv-21 was once removed from
  this box; install record in the sidecar `drivers/` archive).

## 7. Non-goals

No production routing changes, no new kernels wired into findata-graph
code paths, no SVM/F/G follow-ups at current scale, no oneDNN-GPU build
(operator declined after the Gen9-drop finding).

## 8. Revisit triggers

- **Corpus ≥ ~33k chunks** → matrix >50 MB → the 1.8–1.9× band becomes
  honest production territory.
- **ALU-heavy kernel arrives** (normalization, projections, embedding
  math) → the 16.5× roofline tail.
- **Discrete GPU lands** → E's hetero split revives (separate buses);
  re-triage F/G.
- **Graph ≥ ~2M edges** → CSR crosses the floor.

## Appendix — raw measurement log

| Date | Run | Result | Notes |
|---|---|---|---|
| 2026-09-20 | D gpu_vs_simd_real ×100 | GPU 1.79–1.85 / CPU 2.59–2.66 ms | 1.40–1.48×; variance = launch+sync |
| 2026-09-20 | A roofline L1/L2/L3 | 1.72 / 1.73 / 1.72 ms GPU vs 2.47 / 2.72 / 28.46 ms CPU | 1.44× / 1.57× / 16.5×; GPU flat ⇒ floor-bound |
| 2026-09-20 | B f4 group sweep | g16 1.408 / g32 1.335 / g64 1.307 / g128 1.428 / g256 1.799 ms | optimum g64; f16 regresses, unroll no-pay |
| 2026-09-20 | E splits gpu/cpu | 25/75: 2.55 / 50/50: 2.05 / 75/25: 1.42 ms | all ≥ GPU-only 1.31; CPU leg inflates 1.4–2× |
| 2026-09-20 | C shared vs staged | upload 10.6 vs 22.3 ms; wall 1.159 vs 1.286 ms | sums equal both legs (4.4e-07%) |
| 2026-09-20 | L-quick N={1,8,64} | numpy 1.0/0.98/1.05; pool 2.1–3.2; FlatKNN 1.5–1.7 ms/q | numpy wins; prediction refuted |
| 2026-09-20 | L-gate parallelize | serial 2.78 → parallel 1.04 ms | 2.66×; #521/#2398 do not reproduce |
| 2026-09-20 | L-short N=512 | numpy 0.923; FlatKNN 1.403; pool 17–19 ms/q | non-solo, load-labeled; pool unstable |
| 2026-09-20 | F/G CSR export | 0.59 MB (6,811 V / 36,582 E) | ~30× below 17 MB floor |
| 2026-09-20 | J/H/I gates | `gpu,runtime:none`; 24.9 MB; 0.048 MB | oneDNN Gen9 dropped v3.4; all closed |

Full log with harness architecture, per-run bands, and the external
research triage: `doc/local/perf/skylake_eval.md` (machine-local,
untracked — this file is the tracked decision record).
