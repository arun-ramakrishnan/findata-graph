---
title: "Rust: Rayon Parallel cosine_knn + Divan Benchmarks"
status: executed
area: performance
created: 2026-10-27
completed_md: 2026-10-27
author: opencode
related:
  - "#446"  # rust_audit initial gates
  - "pending_improvs.md row 15"  # HNSW blocker
  - "doc/improvements/archive/database/numba_jit_cosine_trial.md"  # numba crossover at ≥500k rows
---

## Summary

Added **rayon-based parallel top-k** to `cosine_knn` (threshold ≥5k rows) and **divan benchmarks** for reproducible Rust-level performance tracking. The parallel path gives **3× speedup at 100k+ rows** while correctly staying single-threaded at production scale (717 rows).

## Changes

### 1. Rayon Parallel Kernel (`Rust/cosine_knn/src/lib.rs`)
- New public functions: `topk_single`, `topk_parallel`, `cosine_similarity`
- Auto-router `topk()` dispatches to parallel at `PARALLEL_THRESHOLD = 5_000` rows
- Per-chunk local top-k (1024 rows/task) with k-way merge
- Identical insertion-sort algorithm in both paths → bitwise-identical results

### 2. Divan Benchmarks (`Rust/cosine_knn/benches/divan_bench.rs`)
```bash
cargo bench --bench divan_bench -p cosine_knn
```

### 3. Perf Gate (existing, unchanged)
`Rust/bench/cosine_gate.py` wired as `make perf` leg `cosine_knn_ratios`:
- Asserts `rust/numpy ≥ 1.3×`, `rust/pure ≥ 5×` at 717×512
- Guards silent degradation when abi3 module missing (selector falls back to numpy)

## Numbers (divan, median, this hardware)

| Benchmark | Median | Notes |
|-----------|--------|-------|
| `bench_production_scale` (717×512) | **460 µs** | Single-threaded path (auto) |
| `bench_parallel_threshold` (5k×512) | **1.75 ms** | Parallel 1.7× faster than single (3.4 ms) |
| `bench_100k` (100k×512) | **26 ms** | Parallel 3.0× faster than single (70 ms) |
| `bench_1m` (1M×512) | **197 ms** | Parallel 3.3× faster than single (695 ms) |
| `bench_cosine_similarity` (512) | **574 ns** | Kernel primitive |

### Single vs Parallel at Same Scale (divan)

| Scale | Single | Parallel | Speedup |
|-------|--------|----------|---------|
| 5k rows | 3.43 ms | 2.05 ms | **1.7×** |
| 100k rows | 70.2 ms | 22.6 ms | **3.1×** |
| 1M rows | 696 ms | 209 ms | **3.3×** |

### Perf Gate (Python-level, `Rust/bench/cosine_gate.py`)

```
rust              1.064 ms/query  (   940 q/s)
numpy             2.890 ms/query  (   346 q/s)
pure             45.353 ms/query  (    22 q/s)
rust vs numpy      2.72x   bar >= 1.3x   ✅
rust vs pure       43.0x   bar >= 5.0x   ✅
```

Note: Python gate includes ~45% overhead (blob decode, list build) vs raw kernel 0.6 ms. The 2.72× is production path, not pure kernel.

## Why This Matters

| Deferred Item | Status | Connection |
|---------------|--------|------------|
| Numba `prange` crossover (≥500k rows, 1.94×) | Trigger has shape | **Rust rayon now covers same scale** (3× at 100k, 3.3× at 1M) |
| HNSW ANN index | Blocker evaporated (DuckDB 2.0 routes L2 via HNSW) | Complementary: ANN for scale, exact KNN for precision |
| Mojo GPU KNN | Frozen (retired) | Rayon = CPU parallel alternative |
| MAX engine n=2–16 GEMM | Not fired (1,209 embeddings) | Rayon handles ≥5k rows |

## Trigger for Revisit

- **Corpus ≥100k rows** → parallel path active (current: `script_search` = 717, `note_search_vec` = 17k via sqlite-vec)
- **Perf gate regression** → `cosine_knn_ratios` catches silent abi3 fallback (ratio collapses to ~1.0)
- **DuckDB 2.0 HNSW cosine support** → would obsolete exact KNN at scale if planner routes `array_cosine_similarity`

## Files Touched

- `Rust/cosine_knn/Cargo.toml` — `[[bench]]` + `dev-dependencies.divan`
- `Rust/cosine_knn/benches/divan_bench.rs` — 13 benchmarks
- `Rust/cosine_knn/src/lib.rs` — `pub` exports, `div_ceil`, clippy clean

## Verification

```
cargo test --workspace                    # 8 passed
cargo clippy --workspace --all-targets -- -D warnings  # clean
cargo bench --bench divan_bench -p cosine_knn  # runs 13 benches
pytest tests/test_cosine_knn_backends.py  # 9 passed
pytest tests/test_local_embedder.py ...   # 192 passed
pytest tests/test_run_perf_benchmarks.py  # 19 passed
python3 Rust/bench/cosine_gate.py         # ratios 2.72× / 43× ✅
```

## Notes

- The 5k threshold was chosen empirically: single-threaded wins below it (overhead > gain)
- `cosine_knn` has **no criterion** — divan replaced the need (simpler, no `[[bench]]` harness conflicts with PyO3 cdylib)
- Production `script_search` at 717 rows stays on single-threaded fast path (460 µs)
- `note_search_vec` (17k rows) uses sqlite-vec pipeline, not this crate — but if migrated, parallel would engage
