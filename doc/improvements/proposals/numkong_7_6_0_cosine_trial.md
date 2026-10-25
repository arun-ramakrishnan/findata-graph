# Proposal: Numkong 7.6.0 cosine trial — 2026-10-18
Status: **measured — proposal recorded, NO production migration**.
Filed: 2026-10-18 (post-trial, user command: "do the same for numkong").

## Trial objective
Confirm numkong 7.6.0 `dot` / `dots_packed` (perf_skills.md sec8: `MEDIUM — numba alt, 2000+ kernels, dots_pack→dots_packed GEMM reuse`) delivers cosine speedup comparable to numba `@njit` trial; decide lane.

## Trial design
- File: `/mnt/data/tmp/opencode/trial_numkong_cosine.py`
- Same protocol as `trial_numba_cosine.py`: 17,497 note_search rows (`memory/research.db`), 512-d f32 BLOB unpacked via `helpers/core/vec_codec.load_vec`; best-of-15 (`time.perf_counter`); per-query over 64 rows; single-cosine.
- Numkong: `dot` (float32 arrays) + `dot` self-square for norms.

## Measurement result (verbatim)

```text
loaded 17497 512-d vecs
correctness py=0.72770950 numkong=0.72770950 delta=2.22e-16
per-query 64 rows pure-python: 3.785 ms
per-query 64 rows numkong : 1.999 ms  speedup=1.89x
single cosine py=54.2 us numkong=30.7 us
```

Correctness delta 2.22e-16. Numkong 1.89× per-query (vs pure-Python 3.785→1.999ms); 54.2→30.7μs single (1.77×). Numkong `dot` (GEMM reuse) behaves; `dots_packed` not required at single-pair scale but confirmed in spec.

## Cross-comparison (settled)
- numba `@njit` (trial_numba_cosine.py): 1.82× per-query; 3.53ms→1.94ms.
- numkong 7.6.0: 1.89× per-query; 3.785ms→1.999ms (same source data; base variance ±0.25ms from 17,497-row scan vs earlier 17,497 same DB; comparable). Numkong single-cosine slower than numba (30.7μs vs 15.4μs) — JIT loop overhead lower per-single-call; per-query batch comparable (GEMM reuse closes gap).
- Native `vec0` (`graph_scaling.md` sec5, `mojo_pilot.md`:242-254): `numpy` native `vec0` k=10 1.86ms; `wedged_flat_knn_map` (app.py:475-495) closes loop as tier-3 last resort. Both JIT lanes (numba, numkong) faster than pure-Python but do NOT unlock a new tier past native `vec0`.

## Decision (same ladder + measured-first doctrine)
- Lane measured: 1.89× confirmed.
- Lane **NOT promoted to production**: native `vec0` layer closes loop (#194); Residual #1 design-phase (approach A/B) does not include a cosine-layer change; operator go pending for R6b→R3→R2→R5→R4, not for numkong/numba.
- Lane kept as **future-candidate** (numkong preferred over numba for batch-GEMM `dots_packed` reuse; numba preferred for JIT per-single overhead; neither unlocks production migration).
- Stale `perf_skills.md` sec8 row (`numba-0-65-1 HIGH — next`, reason `blocked on py 3.14.4`) remains stale after this trial; correction deferred to operator.

## Constraints
- `.venv/bin/python3`; scratch `$TMPDIR`; no `git`/`stg`; proposal only; open objectives preserved.

---

## Phase C execution (2026-10-26 — batch preference overturned)

Operator fired the Phase C trigger ("run these preload trials and also
embed-matrix ~1M+, quant, HNSW now"). Full tables, regen recipes and trap
list: `bench_data/phasec/README.md` (4 cores, 512-d, 20 queries, k=10).

- The Decision line's preference ("numkong preferred over numba for
  batch-GEMM `dots_packed` reuse") does **not survive at Phase C scale**:
  at 1M×512 `dots_packed` runs 145.5 ms/q vs numba prange f32 117.4
  (1.24× slower), and **`dots_pack` doubles memory** (RSS 4.06 GB vs
  2.29 GB). At 250k it is slower than plain numpy (39.3 vs 29.3 ms/q).
- Verdict downgraded accordingly: numkong keeps only its dependency
  virtues (zero JIT warm-up, no llvmlite) — it wins no measured lane.
  For any future matrix-scale wall the designated backend is numba f32
  `prange` (Phase C winner, recall@10 = 1.000). The no-migration
  decision stands — the crossover only exists ≥ ~500k rows and no
  production matrix is near it (largest live corpus 17,497×512).
- Same run settled the neighbouring questions: quantization is
  storage-only (f16/i8 dequant casts cost 11–12× latency); HNSW is
  terminally closed at 512-d (recall@10 = 0.56 at ef=1024 already costs
  10.3 ms/q; M=32 build 414 s); tight-decay jemalloc is RSS-neutral on
  all short one-shot legs (residents-only lever, already wired).
