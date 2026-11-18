---
title: "Four measured latency slices on the live search and integrity paths"
status: executed
filed: "2026-11-18"
executed: "2026-11-19"
completed_md: "545"
area: "helpers/core + helpers/misc + Mojo/bench + Rust/bench"
---

# Four measured latency slices on the live search and integrity paths

**Date:** 2026-11-18 · **Status:** EXECUTED 2026-11-19 (archived 2026-11-19, completed.md #545) ·
**Area:** `helpers/core/vec_search.py`, `helpers/core/vss_index.py`,
`helpers/misc/database_integrity_check.py`, `Mojo/src/common/integrity_check.mojo`,
`Mojo/bench/bench_cosine_knn.py`, `Rust/bench/cosine_gate.py`

## 1. Where this proposal comes from

The Mojo/Rust idea sweep of 2026-11-18 filed 10 candidate ideas (M1–M5, R1–R5) and
trialled every premise against the live stores before any of them was allowed to
become a proposal. Six were killed, one was superseded, two stay parked. The two
findings that survived are **not** kernel work: the production cosine path is
overhead-dominated, and the one hot QA check is loop-dominated. Both fixes are
exactness-preserving and need no new dependency.

Trial artefacts (durable, gitignored):
`/mnt/data/tmp/mojo_ideas.md`, `/mnt/data/tmp/rust_ideas.md` (per-idea verdicts),
`/mnt/data/tmp/trials/t_m2c_where_does_time_go.py` (the 504 ms decomposition),
`/mnt/data/tmp/trials/t_m2b_live_cosine.py`, `/mnt/data/tmp/trials/t_r2_cosine_scale.py`,
`/mnt/data/tmp/trials/t_r2b_cosine_threads.py`, `/mnt/data/tmp/trials/t_r4_report.md`,
`/mnt/data/tmp/trials/r4_mojo_parity.out`, `/mnt/data/tmp/trials/t_m1{,b,c}_*.py`.

## 2. Evaluated and REJECTED / DEFERRED (the ledger)

This section records what was considered and dropped, so a later sweep does not
re-litigate it. Every line was measured or searched against the live stores on
2026-11-18 unless marked otherwise.

### 2.1 The ten sweep ideas — verdict

| idea | verdict | the evidence that killed it |
|---|---|---|
| **M1** Mojo BFS promotion | **REJECTED** | Mojo is 1.07–1.47× **slower** than Rust at every scale tested (live bipartite 25,248 nodes / 119,314 arcs → synthetic 120k / 600k / 2,400,000 nodes): 1.21× / 1.47× / 1.11× slower, parity **exact** (0 mismatches on reachable count + hop histogram, every round). No crossover exists, so the "~10M" promotion rung has no evidence behind it. |
| **M2** Mojo SIMD cosine live lane | **REJECTED as a lane** | On the live 19,356×512 corpus the Mojo SIMD kernel is 62.4 ms wall (4.84 ms compute + 20.1 ms load) — **third** behind numpy matvec (4.5 ms) and Rust rayon (8.6 ms). The "~50 ms class" trigger half fired (production path 764.9 ms) but the trigger points at a Python fix, not a kernel swap. |
| **M3** MAX-engine small-batch GEMM | **REJECTED / PARKED** | No searched corpus reaches 1e5 (max 19,356). `import max` resolves to an **empty namespace package**; `import max.engine` succeeds but does **not** expose `Inner_matmul_default` (the entry point named at pending.md:762-780). numpy sgemm already runs the live shapes in 19–140 ms. |
| **M4** A–K unlock (Mojo pilot) | **DEFERRED (rule holds)** | Searched resident working sets: company_embeddings 2.5 MB, note_search_vec 39.6 MB, script_search 1.7 MB — all under the 50 MB gate. embed_cache is 490.8 MB but it is a CACHE that is never searched. GPU: `/dev/nvidiactl` absent, `clinfo -l` shows only an Intel HD 530 iGPU. A–K stay parked (doc/local/mojo/mojo_pilot.md:946). |
| **M5** Mojo graph-algos phase 2 | **DEFERRED** | All 14 canonical phase-1 targets are already owned — 11 Onager, 1 Rust graph_kernels (betweenness via the L1b Brandes fold), 2 Python/SciPy lanes with Rust core-BFS acceleration. **Zero uncovered.** Live CSR working set ~0.55 MiB vs the >50 MB gate. Note the drift: the live CLI `--all` now reaches **16** cases, not 14 (helpers/graph/algorithms.py:1611-1630). |
| **R1** Extend vendored Onager with personalised-PPR | **REJECTED (superseded)** | The three Onager bugs are still live (personalisation column ignored → byte-identical output; hardcoded restart node 1 → "Personalization node 1 not found in graph"; weight column must be BIGINT) — but Rust graph_kernels **already owns the production path exactly**: production `personalized_pagerank_scores` vs the rust kernel maxdiff **0.000e+00**, 43.1 ms vs scipy 5,305.7 ms = **123.1×**, and `personalized_pagerank_vec` (arbitrary teleport vector) is strictly more general than the ignored column could ever be. |
| **R2** Phase-C rayon cosine lane | **REJECTED as stated** | rayon is **already** active in the shipped path (PARALLEL_THRESHOLD=5000, CHUNK_SIZE=1024, topk → topk_parallel, GIL released). The rust-ports memory's "817 ms single-thread @1M" does **not** reproduce — 139 ms measured. Rust is 0.46–0.49× numpy at 250k–1M rows and the ratio is **flat**: a per-row kernel gap vs BLAS sgemv, not threading. "More rayon" is the wrong fix. |
| **R3** Trigger-gated algorithm shelf | **DEFERRED (no trigger fired)** | Every item HOLD: s-walk (dyadic path API exists, but the trigger needs a HYPEREDGE-traversing path ask; `calc_prop_true_dyad_paths_per_spl` has no code hit), random_walk_density (the only "diffusion" conversations are ML denoising, not information diffusion), community lanes 2-4 (trigger consumed historically and answered "no at current scale"), layer_reducibility (>2× edge-growth gate unmet: live ratios 1.00–1.35×), D17 igraph (Leiden / weighted paths / maxflow already landed), scale ladder (22,130 nodes vs the ~1M T1 rung). |
| **R4a** Rust FTS5 / vec0 probe | **REJECTED** | DuckDB 2.0.0 already ships the fts/vss/sqlite_scanner extension binaries (LOAD fts 29.9 ms, LOAD vss 80.3 ms) and the production FTS path is **already DuckDB-native**. There is **no DuckDB extension named `vec0`** — the current ANN surface is vss/HNSW. At the live 19,356-row mirror the 3.85× "ANN win" is the **top-k query shape**, not ANN traversal: EXPLAIN still shows a Seq Scan. A Rust rewrite removes no Python startup for the current CLI deployment. |
| **R4b** Rust port of the DB integrity check | **REJECTED** | The check is **already ported to a compiled language**: `Mojo/src/common/integrity_check.mojo` is a full port of `helpers/misc/database_integrity_check.py` (completed.md #182), golden parity **92/92**, rc=0. The compiled port is ~1.17× **slower** than Python on this corpus (9,088 ms instrumented / 15,694 ms parity-run wall vs Python 7,771 ms / 13,413 ms warm), because it reproduces the same Python candidate loop (`names` section = 9,387 ms of 15,694 ms). The bottleneck is the loop, not the language. |
| **R5** Batch-all-sources design law | **ACCEPTED AS DOCTRINE (not a proposal)** | Proven, not assumed: batched 1,000 sources 161.9 ms vs a per-source loop 546.8 ms → **385 µs/source** of dispatch overhead, 3.38× (S=100: 437 µs/src, 4.15×). It is a rule for the next kernel, not a piece of work. |

### 2.2 Other candidates evaluated in the same pass and dropped

| candidate | verdict | why it was dropped |
|---|---|---|
| HNSW / ANN adoption for note_search | **DEFERRED** | pending.md:745-761 stands: at the 168,479×384 convo-store scale ANN never beats brute (171.7 vs 116.6 ms); at the live 19,356-row mirror the win is the query shape (EXPLAIN: Seq Scan). S1 removes overhead instead of changing the index decision. |
| `FLOAT[]` → `FLOAT[512]` migration | **DEFERRED** | Required for HNSW key typing; out of scope while ANN stays deferred. |
| Numba prange GEMV rung | **DEFERRED (unreproducible)** | numba is not installed in this venv, so the memory's leg cannot be re-measured here. |
| Rust/embed_cache FTS + vec0 crate | **REJECTED** | The duckdb crate already supports read-only open + `LOAD fts`/`LOAD vss` (scratch crate compiled offline against the cached libduckdb v2.0.0) — but it would change nothing (see R4a). |
| Rust integer / half-precision cosine | **DEFERRED** | Prior measured as storage-only; it does not address the production shape's overhead. |
| jemalloc / allocator change | **REJECTED** | Measured RSS-neutral on short one-shot legs; the cdylib allocator's jurisdiction ends at the cdylib boundary. |
| Mojo A2 / motif kernel spikes | **DEFERRED (parked)** | The A2 lane's numpy-first fix landed (helpers/graph/a2_candidates.py, ~250×); the Mojo spike stays unwired. |
| Mojo heterogeneous co-execution (pilot idea E) | **DEFERRED** | The only candidate above the 1.82× ceiling, but it needs a discrete GPU — this box has an iGPU only. |
| Hypergraph motif opt-in (M-lane follow-on) | **DEFERRED** | The live "do the motif opt-in" ask is the hypergraph motif lane, not the dyadic igraph seat; no measured wall yet. |
| New Mojo/Rust kernel for company_embeddings cosine | **REJECTED** | The defect is a per-call Python `sum()` loop (143.8 ms) — a caching fix, not a kernel (becomes S1.5). |

### 2.3 What the rejections changed

The sweep did not just discard the Mojo/Rust framings — it produced the one live
finding that becomes this proposal, and it corrected two stale beliefs that would
otherwise have driven the next decision:

1. **The production cosine bottleneck is overhead, not maths** (445 of 504 ms) — this
   is S1, and it needs no Mojo and no Rust.
2. **The hot QA check is loop-dominated, not SQL- or language-dominated** (SQL 31 ms
   of a 4,025 ms method; the existing compiled port is slower than Python) — this is
   S3.
3. **A stale standing harness was hiding the drift** (BLOB vs JSON, 384 vs 512) — S2.
4. **The perf gate could not see the regime where Rust loses cosine** — S4.

## 3. Measured baseline (live stores, 2026-11-18)

### S1 — the production note_search cosine path

Corpus: `memory/note_vectors.duckdb` → `note_search_vec` = **19,356 rows × 512 f32**.
Call path: `helpers/core/vec_search.py:474 knn_similarities` →
`vec_available()` (:442) → `_open_ro()` → DuckDB cosine → fetchall → Python sort.
Production caller: `app.py:937 _hybrid_search_results` (hybrid-search RRF leg).

| step | wall |
|---|---|
| `vec_available()` probe (mirror dims + COUNT(*) on a fresh connection) | **251.3 ms** |
| `_open_ro(mirror)` — a NEW DuckDB connection per call | 1.7 ms |
| the SQL itself, on a PERSISTENT connection | 59.9 ms (19,356 rows) |
| fetchall 19,356 rows + sort IN PYTHON | ~190 ms |
| **TOTAL `knn_similarities(k=None)`** | **504.3 ms** |
| `knn_similarities(k=10)` | 476.1 ms |
| the same ORDER BY LIMIT 10 on a persistent connection | 46.9 ms |
| numpy matvec, rows pre-normalized | **1.5 ms** |
| numpy matvec, norms computed per call | 166.2 ms |

**~445 of the 504 ms is overhead, not maths.** `app.py:961-964` records the historical
figure as "~7 ms whole-corpus KNN" on the then-live 1,227-doc index (2026-08-17); the
corpus is now 15.8× larger, so ~110 ms is the honest scale-up — the measured 504 ms
is ~4.6× above even that.

Second surface, same defect class: `helpers/core/vss_index.py:140 _best_vss_match`
runs a Python `sum()` dot loop over the 1,209-row `company_embeddings` table =
**143.8 ms per call**; the run-index path already exists at
`helpers/core/vss_index.py:193 build_vss_run_index` (953.3 ms build) and
`:279 _index_best_match` (6.10 ms query).

### S2 — the standing cosine bench harness is broken on the live store

`python3 Mojo/bench/bench_cosine_knn.py --scales 1 --reps 3` →
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xf2 in position 0: invalid
continuation byte` at `Mojo/bench/bench_cosine_knn.py:108` (it JSON-decodes the
embedding column, which is now a BLOB), and it hardcodes `DIMS = 384`
(`Mojo/bench/bench_cosine_knn.py:55`) against a live 512. The leg is wired into
`Mojo/bench/run_bench.py:292` and referenced by `Makefile.mojo:81`.

### S3 — the one hot QA check is loop-dominated

Live `memory/research.db`, Python suite: **7,771.226 ms** total (23 registry
checks 7,487.816 ms + entity validation 283.410 ms), validation 100%, exit 0.

| check | ms |
|---|---|
| **fuzzy_duplicate_names** | **3,824.315** (dominant) |
| company_metrics | 1,109.551 |
| cache_consistency | 731.460 |
| relations | 433.153 |
| note_tags | 296.374 |
| provenance_coverage | 215.431 |
| derived_names | 171.112 |
| orphan_companies | 159.620 |
| normalization | 155.530 |
| index_prune_plan | 133.695 |
| others | < 60 each |

`helpers/misc/database_integrity_check.py:1386 check_fuzzy_duplicate_names`: its SQL
is **31.250 ms** over 25,200 company rows but the method takes 4,024.546 ms — ~99% is
the Python candidate loop at :1421-1441 (an inverted token index, then *every*
candidate sharing any token goes through `_fuzzy_names_match`).

The Mojo port reproduces the same shape: its `names` section is **9,386.751 ms of the
15,693.641 ms** total wall (`r4_mojo_parity.out`), and the whole port is ~1.17×
**slower** than Python. The check is hot: a QA gate step
(`tests/run_gate_report.py:254`) whose failure blocks the gate (failed QA runs 4211
on 2026-11-14 and 4161 on 2026-11-13).

### S4 — the cosine gate only covers the shape where Rust wins

`Rust/bench/cosine_gate.py` guards ONE shape: `ROWS = 717`, `DIM = 512` (:73-74),
where rust/numpy measured 2.4–2.6×, with `MIN_VS_NUMPY = 1.3` (:81). The shapes
where Rust actually loses are unguarded:

| shape | rust | numpy | rust/numpy |
|---|---|---|---|
| 250,000 × 384 | 40.7 ms | 19.9 ms | 0.49× |
| 500,000 × 384 | 75.5 ms | 34.8 ms | 0.46× |
| 1,000,000 × 384 | 148.2 ms | 68.5 ms | 0.46× |
| 19,356 × 512 (live note mirror) | 8.6 ms | 4.5 ms | 0.52× |

The ratio is flat — a per-row kernel gap versus BLAS sgemv, not threading: rayon is
already active (`Rust/cosine_knn/src/lib.rs:34 PARALLEL_THRESHOLD = 5_000`, :35
`CHUNK_SIZE = 1024`, :153 topk → topk_parallel, :190 `py.detach` releases the GIL)
and the memory's "817 ms single-thread @1M" does not reproduce (139 ms measured).
Top-1 matches exactly at every size.

## 4. Slices

### S1 — production note_search cosine path: spend 60 ms, not 504 ms

**S1.1 — stop paying the availability probe on every call.**
`vec_available()` is called on every `knn_similarities()` invocation and re-derives
the mirror dims, opens a connection and COUNT(*)s the table: **251.3 ms**.
Replace with a process-level cached probe keyed on (mirror path, dims, mirror
mtime+size), invalidated when the mirror is rewritten by the rebuild path. The
degradation contract must survive: `knn_similarities` returns `None` when the KNN
path is unavailable so the caller falls back (app.py:967 "hybrid must degrade,
never 500").

**S1.2 — reuse the read-only DuckDB connection** instead of open/close per call
(1.7 ms each, plus the extension-load cost it hides), reopening when the mirror's
mtime/size changes and falling back to the current per-call open if the reopen fails.

**S1.3 — sort in SQL.** For `k=None` the whole corpus is needed (every page doc
must keep its exact similarity — the RRF re-rank semantics at app.py:956-964), but
the Python `hits.sort()` over 19,356 rows is not: push the ORDER BY into the query.
For `k=int` the ORDER BY already exists (:516-521) — only the fetchall cost remains.

**As built (2026-11-19) — the ORDER-BY half of this slice was superseded.** SQL
`ORDER BY` on similarity alone is not stable: equal similarities would break ties
arbitrarily, and that tie order is load-bearing (`app.py:785 _cosine_positions`
sorts by similarity, so equal sims inherit dict insertion order → RRF ranks).
The sort stayed client-side as a numpy **stable** argsort over scan order, with
the query vector moved to a bound parameter (bit-identical, faster than the
inline literal). Full argument + numbers: §10 "Design deviation from §4 S1.3".

**S1.4 (optional, opt-in) — the numpy run-index.** The pattern already exists at
`helpers/core/vss_index.py:193-250`; a pre-normalized matrix for the note mirror
answers the whole-corpus leg in **1.5 ms**. Allowed to serve only if a parity
assertion (max |Δsim| ≤ 1e-6 vs the DuckDB leg over N sampled live queries) passes;
otherwise it stays off and S1.1–S1.3 still deliver the 8× win.

**S1.5 — the same fix on the company_embeddings surface.** Replace the per-call
Python `sum()` loop (`helpers/core/vss_index.py:140-153`, 143.8 ms/call) with the
cached-matrix path the file already implements.

**Acceptance criteria**
- `knn_similarities(k=None)` median **≤ 60 ms** on the live 19,356-row mirror
  (from 504.3 ms); `knn_similarities(k=10)` **≤ 50 ms** (from 476.1 ms).
- Result parity: identical key set; for `k=int` the returned similarities
  bit-identical to the pre-change implementation; for `k=None` max |Δsim| ≤ 1e-6 over
  200 sampled live queries, and the RRF ordering in `_hybrid_search_results` unchanged.
- The degradation tests still pass: `tests/test_vec_search.py:134`, :138, :212, :238
  (unavailable, dims mismatch, unwritable mirror, dims change).
- `_best_vss_match` on company_embeddings **≤ 1 ms** per call after the first build
  (from 143.8 ms).

### S2 — repair the standing cosine bench harness

Derive `DIMS` from the mirror instead of hardcoding 384, decode BLOB embeddings via
`np.frombuffer(blob, "<f4")` with a JSON fallback for legacy rows, and **fail loudly**
on a shape mismatch — today the row is silently `continue`d
(`Mojo/bench/bench_cosine_knn.py:108-112`), which is exactly how the 384→512 drift
hid. Print the source shape in the leg's output table so a store re-embed is visible.

**Acceptance criteria**
- `python3 Mojo/bench/bench_cosine_knn.py --scales 1 --reps 3` runs green against the
  live note mirror and the script mirror (no UnicodeDecodeError, no silently skipped
  rows).
- The 4-way table's python/numpy numbers agree with
  `/mnt/data/tmp/trials/t_r2_cosine_scale.py` and `t_m2b_live_cosine.py` within the
  documented spread; the shape it measured is printed.
- `Mojo/bench/run_bench.py`'s `cosine-knn` leg stays green.

### S3 — prune the fuzzy-name candidate loop, keep every pair

**S3.1 (Python, first).** In `helpers/misc/database_integrity_check.py:1386-1446`:
anchor the inverted token index on RARE tokens (low document frequency) and apply a
cheap prefilter (token-set overlap / Jaccard floor) before the expensive
`_fuzzy_names_match` call, and memoise `_meaningful_tokens` per name. The prefilter
must be a **proven necessary condition** of `_fuzzy_names_match`, not a heuristic —
otherwise the pair list changes.

**S3.2 (Mojo, second).** Mirror the same pruning in
`Mojo/src/common/integrity_check.mojo` (the `_fuzzy_names_match` port at :252) so
the two implementations stay parity-checked against the same goldens.

**Acceptance criteria**
- `fuzzy_duplicate_names` **≤ 1,000 ms** (from 3,824.315 ms) with an **IDENTICAL
  pair list** — diffed pair-by-pair against the pre-change implementation over the
  live DB (live count: 8,764 pairs), not merely the same count.
- Full Python suite **≤ 4,000 ms** (from 7,771.226 ms), still 100% validation, exit 0.
- Mojo port `names` section **≤ 3,000 ms** (from 9,386.751 ms), golden parity still
  **92/92** (`Makefile.mojo:73-77` mojo-parity leg).
- The check stays advisory (WARNING, never gate-failing) and the gate stays green.

### S4 — pin the cosine shapes where Rust loses

Make `Rust/bench/cosine_gate.py` multi-shape with a per-shape bar table: the
production shape (717 × 512) keeps `>= 1.3×` vs numpy and `>= 5×` vs pure; the
live-shape (19,356 × 512) and scale shapes (250k / 500k / 1M × 384) get a documented
floor (e.g. `>= 0.40×`) that pins today's measured 0.46–0.52× gap so a real collapse
(e.g. 0.2×) is caught. Print every shape's ratio in the leg output.

**Acceptance criteria**
- The leg runs both shape classes inside its existing 15.0 s budget
  (`tests/run_perf_benchmarks.py:502`) and prints a per-shape table.
- A deliberate 2× slowdown of the Rust kernel at the scale shape fails the leg; the
  current build passes.
- The leg still SKIPs cleanly (exit 0) when the `cosine_knn` abi3 module is absent,
  and the qa hard pin (`SCRIPT_KNN_STRICT=1`) is untouched.

**Note:** S4 changes no kernel. Its value is honesty: the gate currently cannot see
the regime where Rust is not the right tool for cosine (numpy/BLAS wins at ≥100k
rows), and that regime is exactly where a future "optimise Rust cosine" decision
would be made.

**As built (2026-11-19) — the illustrative `0.40×` floor did not survive contact.**
The floor is not a chosen number sitting below a fixed loss regime; it is
constrained from both sides by the healthy band, and the band moves with Rayon
thread count. A gate needs `floor ∈ (max_healthy/2, min_healthy]`: above half the
widest healthy ratio so a 2× slowdown lands below it, below the narrowest so a
healthy build never trips it. At Rayon 4 (the original pin) medians span
0.42–0.77×, so `0.40×` failed **5 of 5 healthy runs**. Pinning Rayon to one worker
narrows the band to 0.17–0.24× and makes `0.14×` both passable and falsifiable.
Two further changes came with it: median-of-rounds instead of min-of-5 (a min
estimator latches onto a fast-lane stall), and top-1 parity compared by **score**
rather than id (the tiled fixture puts several rows within float32 ulps of the
maximum, so the winning id is not a stable contract — measured `score_delta`
1.5e-08 while the ids did agree at the pin). See §10 for the numbers.

## 5. Non-goals

- **No new kernel is adopted.** S1's fastest instrument at the production shape is
  numpy (4.5 ms); no dependency, crate, or Mojo source is added.
- **No ANN/HNSW adoption** — the pending.md:745-761 verdict (brute stays shipped)
  is unchanged; S1 removes overhead, it does not change the index decision.
- **No `FLOAT[]` → `FLOAT[512]` migration** (pending.md:738-742) — out of scope.
- **No MAX-engine work** (M3 rejected), **no Mojo BFS promotion** (M1 rejected),
  **no graph-algos phase 2** (M5 deferred), **no Onager PPR extension**
  (R1 superseded), **no Rust FTS/vec0 or integrity port** (R4 rejected — the check
  is already ported to Mojo). Section 2 is the ledger; those ideas do not return.
- **No change to the integrity check's semantics, key set, or severity.**
- **No query-visible semantic change is intended.** S1/S3 must be exactness-
  preserving. If any slice changes a returned value beyond float noise, the eval
  gate runs first (`helpers/misc/ontology_eval_gate.py` over the frozen question set,
  ontology_governance S2) and the change is stated in the execution record.

## 6. Risks / mitigations

- **S1 — a cached probe could serve a stale mirror.** Key the cache on
  (path, dims, mtime, size); the rebuild path rewrites the mirror and changes both.
  Mitigation: the existing tests for upsert/delete/dims-change
  (`tests/test_vec_search.py:151`, :168, :187, :238) plus a new mirror-rewrite case.
- **S1 — float drift could reorder near-ties in the RRF fusion.** Parity assertion
  (max |Δsim| ≤ 1e-6) over sampled live queries + the existing hybrid-search tests.
- **S1 — a persistent connection can hold the mirror against the rebuilder.** Open
  read-only (already), reopen on mtime change, degrade to the per-call open on
  failure.
- **S3 — pruning could drop a true pair.** The pair list must be IDENTICAL, verified
  by a diff (not a count); the prefilter must be a proven necessary condition of
  `_fuzzy_names_match`.
- **S2/S3/S4 — touching standing bench/gate legs can destabilise the gate.** Targeted
  runs only; per AGENTS.md no `make qa`/`make pytest`/full-gate reruns, and the
  operator owns the patch/commit lifecycle.
- **S2 — the harness's published numbers change** because the shape is now 512 and
  the store format is BLOB. State the shape change in the execution record.

## 7. Verification plan

1. **S1** — re-run the same bench script for before/after on the live store:
   `.venv/bin/python3 /mnt/data/tmp/trials/t_m2c_where_does_time_go.py`; targeted
   tests: `.venv/bin/python3 -m pytest tests/test_vec_search.py tests/test_rebuild_note_search.py -q`.
2. **S2** — `.venv/bin/python3 Mojo/bench/bench_cosine_knn.py --scales 1 --reps 3`
   against the live mirrors; the `cosine-knn` leg via `Mojo/bench/run_bench.py`.
3. **S3** — `.venv/bin/python3 helpers/misc/database_integrity_check.py` on the live
   DB (per-section timings + pair-list diff vs the pre-change run);
   `Mojo/bin/integrity_check --parity` for the 92/92 golden parity.
4. **S4** — `.venv/bin/python3 Rust/bench/cosine_gate.py` (both shape classes) and
   the perf leg via `tests/run_perf_benchmarks.py`.
5. **Docs** — after the doc edits: `make search-fresh APPLY=1` then
   `make search-fresh` (rc=0).
6. Leave the tree dirty for the operator; no stg/git lifecycle ops (AGENTS.md).

## 8. pending.md annotations to apply on execution

The house rule is to annotate deferred items when a proposal lands (README,
archival checklist item 3). These are the exact entries this proposal touches:

- **pending.md:781-790** (Rust/PyO3 rung; "cosine-shaped Rust stays closed
  0.47–0.49×") → append the 2026-11-18 re-measurement: 0.46–0.49× confirmed FLAT;
  the cause is the per-row kernel vs BLAS sgemv, **not** threading (rayon already
  active; the memory's 817 ms single-thread figure does not reproduce — 139 ms);
  the gate now also covers the losing shapes (S4).
- **pending.md:745-761** (HNSW/ANN deferred) → append: at the live 19,356-row mirror
  the 3.85× "ANN win" is the **top-k query shape**, not ANN traversal (EXPLAIN still
  shows a Seq Scan); brute stays shipped; S1 removes the production path's per-call
  overhead instead of changing the index decision.
- **pending.md:762-780** (MAX engine rung) → append: re-measured 2026-11-18 —
  `max.engine` imports but `Inner_matmul_default` is absent; the max searched
  corpus is 19,356 (< the 1e5 trigger); numpy sgemm runs the live shapes in
  19–140 ms → the rung stays parked.

## 9. Decision requested

Approve all four slices in one arc, **S1 first** (it carries the user-visible
8–300× win on the hybrid-search path and needs neither Mojo nor Rust), with S2–S4
as small riders — or approve S1 alone and defer the rest.

**DECISION: approved 2026-11-18 ("approve all 4 for implementation"). Executed
2026-11-19. Landing record and post-change measurements follow.**

## 10. Execution record (2026-11-19)

Every number below is a post-change measurement on the same live stores and the
same 4-core box as §3, taken with `.venv/bin/python3` on the arc branch.

### S1 — production note_search cosine path

| metric | before | after | bar |
|---|---|---|---|
| `knn_similarities(k=None)` | 504.3 ms | **50.28 ms** (re-measured 51.79 ms) | ≤ 60 ms |
| `knn_similarities(k=10)` | 476.1 ms | **21.4 ms** (re-measured 15.46 ms) | ≤ 50 ms |
| 200-query parity vs pre-change | — | 0 degraded, 0 key-set, 0 order, max \|Δsim\| **0.000e+00** | ≤ 1e-6 |

Cold reader open is 177.2 ms; the first `k=10` call in a process pays 198.9 ms.

**Design deviation from §4 S1.3 (documented in code).** The proposal said "sort in
SQL". Implemented as a bound parameter plus a numpy **stable** argsort instead,
because SQL `ORDER BY` is not stable and would have changed the tie order that
`app.py:785 _cosine_positions` depends on (it sorts `knn` by similarity, so equal
similarities inherit dict insertion order, which feeds RRF ranks). Parameter
binding was verified safe on DuckDB 2.0.0 — `EXPLAIN` still shows
`hnsw_index_scan=True`, and the parameter form is faster than the inline literal
(3.0 ms vs 14.5 ms at k=10). The `k=int` leg keeps the inline literal
(`helpers/core/vec_search.py:369-375`).

**Root cause of the one regression this slice hit.** Six tests failed after the
cache landed: the mirror kept serving a deleted row after an incremental sync.
DuckDB reuses the live database instance for the same path+config, so the cached
read-only connection held the pre-swap inode and the copy-on-write rewrite
(`_write_mirror` temp file + `os.replace`) was invisible to it. Evidence:
`/mnt/data/tmp/trials/t_duckdb_instance_cache2.py`. Fix: `_reader()` closes the
stale cached connection before opening the new one. `tests/test_vec_search.py` +
`tests/test_rebuild_note_search.py` → 58 passed.

### S1.5 — company_embeddings matrix tier

`_best_vss_match` 38.33 ms/call (the §3 figure of 143.8 ms was a stale baseline) →
**0.07 ms/call** with a DB-identity cache key (re-measured 0.147 ms median), **13.4 ms**
median through the sha1 content digest when no DB fingerprint is supplied. Parity
over 30 queries (20 random + 10 real vectors): 0 name mismatches, max \|Δscore\|
**7.772e-16**; the `entity_set` path is identical. `tests/test_get_tickers.py` →
52 passed.

### S2 — the standing cosine bench harness

Repaired: reads the live BLOB embeddings and derives dimensions from the store
instead of hardcoding 384. It now runs against the live mirrors:
`note 19,356×512` python 1373.9 ms / numpy 6.395 ms / rust 7.984 ms /
mojo 54.2 ms, and `script 865×512` python 60.4 / numpy 0.216 / rust 0.638 /
mojo 11.5 ms. **Published shape changes from 384 to 512 dims** (risk §6 item).

### S3 — fuzzy duplicate-names, Python leg

| metric | before | after | bar |
|---|---|---|---|
| `check_fuzzy_duplicate_names` | 3,814–3,879 ms | **269–278 ms** | ≤ 1,000 ms |
| pair list | 8,764 | **8,764, membership-identical** | identical |
| whole `check_integrity()` | 7,771 ms | **3,406 ms** (median of 5: 4,017 / 3,428 / 3,406 / 3,396 / 3,378) | ≤ 4,000 ms |
| validation rate / exit | 100% / 0 | **100% / 0** | unchanged |

The old output *order* is nondeterministic across runs (`PYTHONHASHSEED`-randomised
token-set iteration), so "identical pair list" was verified membership-based
(sorted), which is the strongest claim the baseline supports.

Equivalence argument (this is what makes the pruning safe, not a heuristic): with
`ci`, `cj` the token frozensets, `_fuzzy_names_match` is true iff
`(|ci|==1 and |cj|==1 and ci==cj)` or `(|ci|>=2 and |ci&cj|>=2 and
(ci⊆cj or cj⊆ci) and (ci&cj)-_GENERIC_WORDS ≠ ∅)`. So the only relation needed is
`supersets[i] = {j : tokens(j) ⊇ tokens(i)}`, computed as an intersection over
per-token postings; its transpose supplies the subset direction. The old code
unioned every posting and re-tested each candidate (O(n²)).

### S3b — the same pruning mirrored in the Mojo port

`Mojo/src/common/integrity_check.mojo:1093-1173`: comma-joined token strings
replaced by `List[List[String]]` reuse, the inverted index by
`Dict[String, List[Int]]`, quadratic candidate dedup by `Dict[Int, Bool]`, and
linear pair-membership scans by `Dict[String, Bool]`. Rendered pairs, suppression
rules, and the `\x01` as-found ordering key are unchanged.

| metric | before | after | bar |
|---|---|---|---|
| `names` section | 5,488.4 ms | **1,521.1 ms** (repeat 2,060.5 ms) | ≤ 3,000 ms |
| golden parity | 92/92 | **92/92** | 92/92 |

### S4 — the multi-shape cosine gate

The gate as first written **failed 5 of 5 runs on a healthy build** (live-note
0.23–0.48×, scale 0.38–0.63× against a 0.40× floor). The floor was not too high —
it was *unfalsifiable*, sitting inside the noise band.

The mechanism is a two-sided feasibility rule. A regression gate needs its floor
inside `(max_healthy / 2, min_healthy]`: above half the widest healthy ratio (so a
deliberate 2× slowdown lands below it) and below the narrowest healthy ratio (so a
healthy build never trips it). **What makes the window usable is a NARROW healthy
band.** Rayon thread count is what widens it — medians across fresh processes:

| Rayon | live-note | 250k | 1000k | feasible window |
|---|---|---|---|---|
| 1 | 0.170–0.218 | 0.187–0.201 | 0.185–0.243 | **(0.122, 0.170]** usable |
| 4 | 0.422–0.615 | 0.521–0.768 | 0.557–0.737 | (0.384, 0.422] ~9% wide |

At Rayon 4 the band is wide enough that a floor passing an idle box fails a busy
one and a floor catching a 2× regression fails an idle one — there is no value
that does both, which is exactly why the original 0.40× failed. The fix pins
Rayon to one worker, making the measurement a single-core **kernel** comparison
(the honest question: is the Rust inner loop competitive?).

Two further defects fixed:

- **min-of-5 → median of per-round ratios.** A min estimator latches onto a
  transient stall on the fast lane and inflates the ratio.
- **top-1 compared by score, not id.** The cheap scale fixture tiles a 4,096-row
  base, so several rows sit within float32 ulps of the maximum; the winning id is
  not a stable contract. The gate now asserts Rust returns a row carrying the
  maximal similarity (tolerance `1e-5`) — measured `score_delta` 1.5e-08 while the
  ids agreed. The production shape keeps the exact full-ranking comparison.

Post-change, three consecutive runs: **3/3 PASS** (production 2.77–2.90×, live-note
0.18–0.19×, scale 0.18–0.20×, all against the 0.14× floor; production vs pure
43.5–47.7×). Sensitivity, by wrapping the kernel to burn CPU proportional to each
call: **1.0× → rc 0; 2.0× → rc 1; 3.0× → rc 1.** Leg wall 11.4–12.1 s inside its
15.0 s budget; the SKIP path still exits 0 with the abi3 module absent, and the
`SCRIPT_KNN_STRICT=1` qa pin is untouched.

### Perf leg (`make perf`) — the effect on the standing harness

These are **leg wall-clock** (process start + CLI), not the in-process figures §10
reports above. Baselines below come from `gate_query timing` over the indexed
184 perf runs, not from a hand-picked sample — three cherry-picked "clean" runs
would have supported a wrong conclusion in two places here.

| leg | pre-slice median (last 10 runs) | pre-slice range | post-slice | budget |
|---|---|---|---|---|
| `integrity_check` | 6.48 s | 5.24–11.38 s | **4.47 / 4.33 / 4.70 s** | 10.0 s |
| `static_checks` | 5.51 s | 4.42–10.88 s | **7.46 / 6.79 / 5.94 s** | 7.0 s |
| `cosine_knn_ratios` | 2.64 s | 2.19–3.18 s | **11.91 / 12.35 / 12.72 s** | 15.0 s |
| `derive_insights` | 11.98 s | 5.18–36.25 s | **20.32 / 17.59 / 20.54 s** | 9.0 s |

**`integrity_check` improved, and by more than a single sample suggested.** 4.47–4.70 s
against a 6.48 s pre-slice median (−1.9 s). S3 accounts for ~3.2 s of in-process
time; the leg also carries process + CLI cost. This is the one leg the arc clearly
helped.

**`static_checks` got slower, not faster — and my first draft of this section said
otherwise.** Post-slice it reads 7.46 / 6.79 / 5.94 s against a 5.51 s pre-slice
median, and the two higher samples sit at or over the 7.0 s budget. The cause is
not the arc's slices: it is the two static-check failures the slices introduced
(below), which made the checker report and fail while they were live. The final
green run at 5.94 s is on par with baseline. Worth watching, because the margin
under 7.0 s is small.

**`cosine_knn_ratios` grew 4.7× — S4 gave it four shapes instead of one.** 2.64 s
pre-slice median → 11.91–12.72 s. The leg passes against its unchanged 15.0 s
budget but **now consumes 80–85 % of it on an idle box** — the margin is gone. That
is genuine debt the arc creates: the outer budget guards harness liveness, not the
ratio bars (which are the real gate), and a busy box tripped this leg twice during
verification (11.91 s and 12.35 s under load1 5.30 / 4.62, both tagged CONTENDED).
Raising it to 25.0 s would restore the margin; left unchanged here as a
gate-tuning decision for the operator, not a slice deliverable.

**Resolved 2026-11-19 (operator call):** the redundant 500,000-row middle
shape was dropped from `Rust/bench/cosine_gate.py` (`SCALE_ROWS` now
250k/1M — the measured ratio is flat 0.18–0.20x across scales, so the
middle point added ~1.7 s with no new signal) and the outer budget was
raised 15.0 s → 25.0 s in `tests/run_perf_benchmarks.py`, per the house
measured×2 rule (trimmed leg 10.1–12.1 s wall, 3/3 PASS). The inner
ratio bars are unchanged.

**`derive_insights` is a pre-existing regression, and it is worse than the
"standing budget gap" my first draft called it.** The series shows a clean step:
every run from 2026-11-13 onward lands at 15.49–36.25 s, every run before it at
5.18–10.30 s, against a 9.0 s budget. Measured directly just now on a load-3.02
box: **16.47 / 15.85 / 16.33 s** — reproducible, not contention. The step lands
between the 2026-11-11 13:56 run (5.18 s) and 2026-11-13 12:08 (21.17 s), whose
only intervening changes to the leg's inputs are the 2026-11-13 `db_sync` snapshot
refreshes (`25e908d96` "DDL dropped view tables; hyper_edges row bump",
`deb89bccf`) and the `ddafb6c14` metrics-grain shape change. The last edit to
`helpers/graph/derive_insights.py` itself is `8e491861b` (2026-11-11), which
predates the step. **The cause is not established here** — it is not this arc's
doing (the step predates the slices by six days), but the leg has been red for six
days and deserves its own investigation, starting from corpus growth against the
11-13 snapshot.

**RESOLVED 2026-11-19 (later session) — the corpus-growth hypothesis above was
wrong, and the cause is now established.** The tracked vault grew only 1433→1445
files / 30.81→30.85 MB (+0.1%) across the step window. The real mechanism: the
per-file scan's ProcessPool. On the live box, the pooled path costs **30.76 s wall
at 3.61 s CPU** (default auto = min(4, cpu) and explicit `--workers 4` alike)
against **7.85 s wall / 7.20 s CPU serial** (`--workers 1`) — fork + child-import
+ IPC overhead dominates a scan whose entire CPU cost is ~7 s at the current
131-file corpus. No `BrokenProcessPool` warning fires, so the pooled runs simply
pay the overhead every time. Fix: serial is now the default (`scan(workers=1)`;
`--workers N` opts into the pool), docstring + `--workers` help record the
measurement, and the leg's calibration comment in `tests/run_perf_benchmarks.py`
was updated. Verified: default run 8.77 s wall (was 30.8 s), byte-identical
output, `tests/test_derive_insights.py` 280 passed. The 6 commits in the step
window (2026-11-12 14:00–14:06, incl. `8e491861b`'s duckdb_shared 5 GB cap) remain
the suspects for *why* the pool's fork cost ballooned; the fix removes the leg's
dependence on that question.

**Two static-check failures were introduced by the arc and fixed during this
verification pass:** `Proposal lifecycle` (the README live bullet spelled
`completed.md` inside backticks, which the live-list ref scanner reads as a
pointer to a file in `proposals/`) and `SQLite helper usage`
(`Mojo/bench/bench_cosine_knn.py:199` — the repaired S2 harness opened the legacy
`memory/script_search.db` with a raw `sqlite3.connect(mode=ro URI)`; now
`connect(_SCRIPT_DB_LEGACY, read_only=True)`, which is the same `mode=ro` URI plus
the house pragmas. Verified: 840 legacy rows read, writes still raise
`OperationalError`). Both are fixed in the final tree.

### Verification run

`tests/test_vec_search.py`, `tests/test_rebuild_note_search.py`,
`tests/test_get_tickers.py`, `tests/test_database_integrity_check.py`,
`tests/test_cosine_knn_backends.py` → **216 passed in 34.57s**. The wider blast
radius of the three touched modules (`tests/test_api_search.py`,
`test_db_maint.py`, `test_snapshot_db.py`, `test_embeddings.py`,
`test_embed_cache.py`, `test_validators.py`, `test_cli_guards_advisory.py`,
`test_performance.py`) → **245 passed in 20.51s**.

`ruff check .` clean. `ruff format --check .` initially flagged five of the landed
files — the slice diff, as committed, was not format-canonical — so they were
formatted (`make format` on those five paths); `tests/test_lint_gates.py` → **5
passed**, and the gate was re-run post-format with the same verdicts (production
2.82×, live-note 0.17×, scale 0.20× vs the 0.14× floor, rust/pure 44.45×).

All four acceptance probes were re-run against the final tree and reproduce:
`k=None` 51.79 ms, `k=10` 15.46 ms, 200-query parity 0 order/key-set/degraded
mismatches with max \|Δsim\| 0.000e+00; `_best_vss_match` 0.147 ms median with a
cache key over 30 queries, 0 name mismatches, max \|Δscore\| 7.772e-16.

The perf leg was run three times during verification: 23/35, 16/35 and 34/35
(`gate_query recent --gate perf`). The two weak runs were the arc's own static-check
failures plus heavy contention (load1 4.62 and 5.30 against 4 cores, which tags
every subsequent row CONTENDED by design — `tests/run_perf_benchmarks.py:124`
`_LOAD_RUN_CEIL = os.cpu_count()`). The final run is 34/35, its single failure the
pre-existing `derive_insights` step regression described above.

Pre-slice runs, for reference, were just as mixed: 31/31 on 2026-11-09 21:01, but
29/33 on 11-13 and 30/34 on 11-14. A red perf run on this box is the norm, not a
signal that these slices broke something.

`make qa` and the advisory gate were not run (AGENTS.md: full gates once per arc, on
the operator's go); the perf harness was run because its legs are the direct
verification for S3's runtime claim.
