---
title: "Rust port of cosine similarity / KNN for query layer"
area: graph
status: executed
filed: "2026-10-25"
completed_md: "440"
executed: "2026-10-25"
---

# Rust port of cosine similarity / KNN (`query.py::similar_notes`, `rebuild_script_search.py::_cosine_leg`, `app.py::_hybrid_search_results`)

## Motivation

Cosine similarity over the note-embedding matrix (384-d, thousands of rows) is the core of semantic search. Python's pure loop is ~40 q/s at 1K rows; numpy is ~200 q/s. Rust SIMD (f32x4) achieves 3,500–11,000 q/s — **~120–270× speedup**.

## Trial results (Rust/cosine_knn)

| queries | rows | Rust scalar (q/s) | Rust SIMD (q/s) | Rust blocked (q/s) | Python pure (q/s) | Python numpy (q/s) | SIMD vs Py pure |
|---------|------|------------------|-----------------|--------------------|-------------------|--------------------|-----------------|
| 1       | 1,024| 2,357            | 10,916          | 2,061              | ~40               | ~200               | ~270×           |
| 1       | 4,096| 539              | 1,963           | 524                | ~10               | ~10                | ~180×           |
| 1       | 16,384| 142             | 479             | 140                | ~2                | ~3                 | ~120×           |
| 4       | 1,024| 2,155            | 11,370          | 2,273              | ~40               | ~2,000             | ~280×           |
| 4       | 4,096| 507              | 1,900           | 446                | ~10               | ~90                | ~190×           |
| 4       | 16,384| 123             | 362             | 116                | ~3                | ~3                 | ~120×           |

- f32x4 SIMD (auto-vectorized + explicit `std::simd`) consistently 3–5× scalar.
- Blocked variant handles L2 cache for >4K rows.
- Python numpy scales with batch size; Rust SIMD wins at all scales.

## Proposed slices

### S1: `cosine_knn` Rust crate (this trial)
- Pure Rust: no external deps, `std::simd` (stable since Rust 1.77, `feature(stdsimd)`).
- Public API: `cosine_knn(query: &[f32], matrix: &[f32], k: usize, dim: usize) -> Vec<(usize, f32)>` returning top-k (index, score).
- CLI binary `Rust/cosine_knn` for subprocess or benchmarking.

### S2: Python binding via PyO3/maturin
- `cosine_knn_py` module with `cosine_knn(query: list[float], matrix: list[float], k: int, dim: int) -> list[tuple[int, float]]`.
- Matched dtype: f32 / 384-d / row-major contiguous.
- Maturin wheel in CI; `--use-rust-knn` flag in `query.py` / `rebuild_script_search.py` / `app.py` for A/B.

### S3: Production integration
- Replace `query.py::similar_notes`, `rebuild_script_search.py::_cosine_leg`, `app.py::_hybrid_search_results` with Rust call.
- Keep fallback to Python/numpy when wheel unavailable.
- Benchmark on live corpus: `make search-fresh` / `make bench-graph` / `gate_query timing --leg search`.

## Compatibility notes
- Vectors stored as f32 BLOB (1536 B for 384-d) via `helpers/core/vec_codec.py::pack_f32` / `unpack_f32` — already little-endian f32, matches Rust `f32::from_le_bytes`.
- Cache key `(text_hash, model)` unchanged; `CachedEmbed` struct compatible.

## Open questions
- PyO3 vs subprocess: maturin wheel adds `manylinux` build complexity; subprocess simpler for first cut. This proposal assumes maturin for production (S2).
- SIMD feature gate: `std::simd` requires `feature(stdsimd)` on nightly or stable 1.77+; target machine must support AVX2/NEON. Fallback to scalar at runtime if unsupported.

## S2/S3 execution results (2026-10-25, second session)

**Landed**: the crate's PyO3 surface was rebuilt clean (the draft's 9
compile errors were: pyfunction/pymodule name collision cascading into
pyo3 macro internals, and `std::simd` — nightly-only `portable_simd`, the
"stable since 1.77" note above was wrong). Kernels are scalar with
8-lane accumulators (LLVM won't reassociate float adds — no fast-math in
Rust; single accumulators never vectorize). API is numpy-array in
(zero-copy, `numpy` crate 0.22) after measuring that the original
list→Vec<f32> boundary cost ~25 ms/call at 5k×384 and dominated
everything. Build: `RUSTFLAGS="-C target-feature=+avx2,+fma" cargo
build --release -p cosine_knn`, install by copying
`target/release/libcosine_knn.so` →
`.venv/lib/python3.14/site-packages/cosine_knn.abi3.so` (box-local, NOT
portable to non-AVX2 machines; no maturin needed for a venv-local wheel).

**Wired**: `_cosine_leg` (rebuild_script_search.py — the ONLY real
production loop; `similar_notes` is DuckDB SQL and app.py's hybrid is
sqlite-vec with the Python loop as cold fallback, so neither is a port
target) now scores through `_cosine_rank_rows` with the `SCRIPT_KNN`
selector: **auto → rust if the module imports, else numpy, else the pure
loop** (follows-availability, same doctrine as the embedder selector;
explicit `numpy`/`rust`/`pure` overrides). The fast path builds the
matrix zero-copy from RAW f32 blobs (`np.frombuffer(b"".join(...))`)
when every row is the post-migration binary shape — the list→array
conversion of 705×512 python floats had measured ~10 ms/query and made
both kernels conversion-bound. The module import is DYNAMIC
(`importlib.import_module`) because it is optional/box-local — deptry
stays green with no config change. Parity pinned by
tests/test_cosine_knn_backends.py (9 tests: three-backend exact-row
parity, zero-norm semantics, selector routing, live-blob end-to-end).

**Measured (live script corpus, 705×512, 2026-10-25)**:

| backend | ms/query | q/s | vs pure |
|---|---|---|---|
| pure loop (was production) | 33.3 | 30 | 1× |
| numpy (BLAS, zero-copy in) | 2.76 | 363 | 12× |
| **rust abi3 (auto default)** | **1.12** | **892** | **30×** |

Shape-dependent: on a 5k×384 synthetic numpy's BLAS matvec leads (3,100
vs 367 q/s) — hence the override. The earlier second-session note
("numpy first, Rust ~5×") was corrected by the bytes fast path: with
conversion cost gone, Rust's single fused pass wins at production shape.

**Open**: the script eval bank floor-failed during validation
(intent MRR 0.938 < 0.94) — IDENTICAL under the pure backend, i.e. NOT
this change: the script corpus grew 548→705 rows this morning (Rust
trial crates + tests + docs indexed) and two intent questions lost
rank-1 to the new near-duplicates. RESOLVED same day: operator
sanctioned the re-baseline — `INTENT_FLOOR_MRR` 0.94 → 0.93 in
helpers/bench/script_eval_bank.py (0.938 re-measured stable; rationale
in the docstring), bank green, ident 1.000/8-8.

## References
- Trial code: `Rust/cosine_knn/`
- Baseline: `Rust/bench/cosine_bench.py` (pure + numpy)
- Full analysis: `doc/local/notes/rust_ports.md`

## Second-session assessment (2026-10-25 — baseline correction + binding direction)

**The production baseline is the pure-Python loop, and the first fix is
numpy, not Rust.** `rebuild_script_search.py:1607 _cosine_leg` computes
`sum(x*x ...)` norms and decodes blobs per row inside
`for rid, emb in conn.execute(...)` — a row-by-row Python loop over every
stored embedding. The "~40 q/s pure Python" column IS the live path.
Consequences for the headline:

- The **~120–270× figure is vs pure Python**. Versus a *proper numpy
  matvec* (one matrix-vector product, existing dep, no new artifacts) the
  realistic gap at 1K rows is **~5×** (10.9k vs ~2k q/s) — real, but a
  different decision.
- **Amended order of work**: (a) numpy-vectorize `_cosine_leg` (and the
  `similar_notes` / `_hybrid_search_results` analogs) FIRST — expected
  ~50× for zero new moving parts; (b) re-baseline; (c) Rust SIMD only if
  the fixed baseline still leaves a hot spot worth ~5× more.
- Also profile the **SQLite streaming blob decode** (`conn.execute`
  per-row fetch + `unpack_f32`) — at these sizes the row-iteration and
  decode may cost as much as the arithmetic; a single concatenated-matrix
  read may beat both numpy and Rust per-row forms.

**Binding direction (amends S2)**: this surface is query-path PER-CALL —
subprocess is the WRONG shape here (protocol overhead exceeds compute at
ms-class query times); if Rust proceeds past the fixed numpy baseline,
**in-process (PyO3) is correct**. Note: a no-callback PyO3 API
(matrix in → top-k out) is genuinely easy — the embed_cache binding's 8
errors came from its per-miss Python-callback design, NOT from PyO3
itself (see the companion proposal's second-session section).
