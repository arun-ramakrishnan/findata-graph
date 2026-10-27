# Trial B — cosine similarity / KNN (Rust vs Python)

Reproduces `helpers/graph/query.py::similar_notes` / `rebuild_script_search.py::_cosine_leg`:
- Cosine similarity between a query f32 vector (384-d) and every row of an N×384 matrix
- Return best match (top-K=1)
- Three implementations: scalar (plain loops), simd (f32x4-packed dot, x86_64), blocked (cache-blocked scalar)

## Results

| queries | rows | Rust scalar (q/s) | Rust SIMD (q/s) | Rust blocked (q/s) | Python pure (q/s) | Python numpy (q/s) | SIMD vs Py pure |
|---------|------|------------------|-----------------|--------------------|-------------------|--------------------|-----------------|
| 1 | 1,024 | 1,794 | 10,101 | 2,315 | ~40 | ~200 | ~250× |
| 1 | 4,096 | 473 | 1,772 | 426 | ~10 | ~10 | ~180× |
| 1 | 16,384 | 119 | 353 | 119 | ~2 | ~3 | ~120× |
| 4 | 1,024 | 1,892 | 10,766 | 1,966 | ~40 | ~2,000 | ~270× |
| 4 | 4,096 | 506 | 1,900 | 446 | ~10 | ~90 | ~190× |
| 4 | 16,384 | 123 | 362 | 116 | ~3 | ~3 | ~120× |
| 16 | 1,024 | 2,110 | 10,965 | 1,916 | ~40 | ~1,000 | ~270× |
| 16 | 4,096 | 408 | 1,237 | 473 | ~10 | ~300 | ~120× |
| 16 | 16,384 | 125 | 411 | 122 | ~2 | ~7 | ~200× |

Observations:
- **Rust SIMD (f32x4 dot) gives 3–5.7× over Rust scalar** — biggest at small matrices (cache-resident), still ~3× at 16K rows (memory-bound).
- **Rust scalar beats Python pure by ~30-60×** — compiled loops vs Python generator arithmetic.
- **Rust SIMD beats Python numpy by ~120-270×** — numpy's BLAS dot doesn't help here because the operation is a per-row cosine (N dots of 384-d), not one big matmul; overhead dominates.
- **Blocked scalar does NOT help** (0.88–1.16×) — each row touched once per query, no cross-row reuse to exploit.

## Conclusion for Trial B

A Rust port of the cosine/KNN path is **highly worthwhile**: SIMD f32x4 gives 3-5.7× over the already-30×-faster Rust scalar, and the whole thing is ~120-270× faster than the Python pure path. This is the compute hotspot to port.

## Files

Paths as of the 2026-10-25 port (this trial lived at `rust_trials/` before
it was promoted into the workspace crate):

- `Rust/cosine_knn/Cargo.toml` — pyo3 0.30 (extension-module, abi3-py39) + numpy 0.30
- `Rust/cosine_knn/src/lib.rs` — the SHIPPED abi3 module: `cosine_similarity`
  (8-lane accumulators + remainder tail), `topk`, and the two `#[pyfunction]`s
  `knn(query, matrix, k)` / `knn_batch(queries, matrix, k)`. Consumed by
  `helpers/maintenance/rebuild_script_search.py::_cosine_rank_rows` behind the
  `SCRIPT_KNN` selector (`auto` → rust → numpy → pure).
- `Rust/cosine_knn/src/bin/trial.rs` — the original trial harness: `topk_scalar`,
  `topk_simd`, `topk_blocked`, `gen_matrix`
- `Rust/cosine_knn/README.md` — this file
- `Rust/bench/cosine_bench.py` — Python baseline (pure + numpy)
- `Rust/bench/cosine_gate.py` — the PERF GATE (`make perf` leg
  `cosine_knn_ratios`): asserts rust ≥ 1.3× numpy and ≥ 5× the pure loop at the
  live 717×512 script-search shape, so a silent fall back to numpy (rankings
  stay correct, only the ratio collapses) cannot pass unnoticed

Run: `cd Rust && cargo run --release -p cosine_knn`
Baseline: `.venv/bin/python3 Rust/bench/cosine_bench.py`
Gate: `.venv/bin/python3 Rust/bench/cosine_gate.py`

## Cost model (2026-10-27, measured)

The ratios in the table above are trial-shaped (384-d, one query). At the
LIVE production shape (717 rows × 512 dim, `embeddinggemma-2-q8_512`) the
honest kernel-only win over numpy is ~0.6 ms vs ~0.9–1.0 ms per query, not
the 120–270× headline: that headline compares SIMD against the pure Python
loop, and numpy's real cost at this size is `np.linalg.norm(M, axis=1)`
(0.39 ms) and the all-rows list+dict return shape (0.32 ms), not the matvec
(0.03 ms). Through the production entry point the end-to-end gap is 2.4×
(1.07 vs 2.54 ms/query) because ~45% of the rust path is Python-side blob
decode and list building. Both kernels are issue/bandwidth-bound here, which
is why `target-cpu=native` moves nothing measurable (see `cosine_gate.py`
docstring) and why the gate measures ratios rather than absolute q/s.
