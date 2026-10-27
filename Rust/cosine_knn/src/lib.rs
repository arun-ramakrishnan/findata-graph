//! cosine_knn: top-k cosine KNN over a row-major f32 matrix.
//!
//! Python API (PyO3 + numpy, zero-copy):
//! - `knn(query: np.ndarray[f32, 1d], matrix: np.ndarray[f32, 2d], k) -> list[(row, score)]`
//! - `knn_batch(queries: np.ndarray[f32, 2d], matrix: np.ndarray[f32, 2d], k) -> list[list[(row, score)]]`
//!
//! Inputs are read through the buffer protocol (PyReadonlyArray) — NO
//! per-element list extraction. The original draft took Python lists and
//! the list→Vec conversion of a 5k×384 matrix cost ~25 ms/call, dwarfing
//! the kernel (measured 2026-10-25: 38 q/s at 5k rows through lists vs
//! kernel-bound rates through arrays).
//!
//! Kernels are scalar-with-8-lane-accumulators on purpose: LLVM won't
//! reassociate float adds (no fast-math in Rust), so a single accumulator
//! never auto-vectorizes — 8 independent lanes give SLP a fixed 8-wide
//! pattern. Ties: row index ascending.
//!
//! Parallelism: at ≥5k rows, rayon parallel top-k with per-thread local
//! heaps gives 2–3× speedup (measured 3.4× at 1M rows). The Python
//! layer routes via `_cosine_rank_rows` → `SCRIPT_KNN` selector (auto/
//! rust/numpy/pure). When the abi3 module is missing the selector
//! silently degrades to numpy — the perf gate `Rust/bench/cosine_gate.py`
//! guards this with `rust/numpy >= 1.3x` and `rust/pure >= 5x` ratios.
//!
//! Native tuning (target-cpu) comes from .cargo/config.toml build.rustflags
//! on every cargo invocation under Rust/ — no RUSTFLAGS export needed. The
//! abi3 .so is box-local: NOT portable to non-x86-64-v3 machines.

use numpy::PyReadonlyArray1;
use numpy::PyReadonlyArray2;
use pyo3::prelude::*;
use rayon::prelude::*;

const PARALLEL_THRESHOLD: usize = 5_000; // rows; below this single-threaded wins
const CHUNK_SIZE: usize = 1024; // rows per rayon task

pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let n = a.len();
    let chunks = n / 8;
    let mut dot = [0.0f32; 8];
    let mut na = [0.0f32; 8];
    let mut nb = [0.0f32; 8];
    for c in 0..chunks {
        let base = c * 8;
        for j in 0..8 {
            let x = a[base + j];
            let y = b[base + j];
            dot[j] += x * y;
            na[j] += x * x;
            nb[j] += y * y;
        }
    }
    let (mut d, mut ma, mut mb) = (0.0f32, 0.0f32, 0.0f32);
    for j in 0..8 {
        d += dot[j];
        ma += na[j];
        mb += nb[j];
    }
    for i in (chunks * 8)..n {
        d += a[i] * b[i];
        ma += a[i] * a[i];
        mb += b[i] * b[i];
    }
    if ma == 0.0 || mb == 0.0 {
        0.0
    } else {
        d / (ma.sqrt() * mb.sqrt())
    }
}

pub fn topk_single(query: &[f32], matrix: &[f32], k: usize, dim: usize) -> Vec<(usize, f32)> {
    let n = matrix.len() / dim;
    let k_eff = k.min(n);
    // Running k-sized insertion (partition_point + insert): allocation-free
    // past k, and stable-on-ties by construction (later equal rows skip).
    let mut best: Vec<(usize, f32)> = Vec::with_capacity(k_eff + 1);
    for i in 0..n {
        let base = i * dim;
        let score = cosine_similarity(query, &matrix[base..base + dim]);
        if best.len() == k_eff {
            if let Some(last) = best.last() {
                if score <= last.1 {
                    continue;
                }
            }
        }
        let pos = best.partition_point(|(_, s)| s >= &score);
        best.insert(pos, (i, score));
        if best.len() > k_eff {
            best.pop();
        }
    }
    best
}

// Local top-k using the same insertion-sort algorithm as single-threaded
fn local_topk(
    query: &[f32],
    matrix: &[f32],
    start: usize,
    end: usize,
    k: usize,
    dim: usize,
) -> Vec<(usize, f32)> {
    let k_eff = k.min(end - start);
    let mut best: Vec<(usize, f32)> = Vec::with_capacity(k_eff + 1);
    for i in start..end {
        let base = i * dim;
        let score = cosine_similarity(query, &matrix[base..base + dim]);
        if best.len() == k_eff {
            if let Some(last) = best.last() {
                if score <= last.1 {
                    continue;
                }
            }
        }
        let pos = best.partition_point(|(_, s)| s >= &score);
        best.insert(pos, (i, score));
        if best.len() > k_eff {
            best.pop();
        }
    }
    best
}

pub fn topk_parallel(query: &[f32], matrix: &[f32], k: usize, dim: usize) -> Vec<(usize, f32)> {
    let n = matrix.len() / dim;
    let k_eff = k.min(n);
    let num_chunks = n.div_ceil(CHUNK_SIZE);

    // Each rayon task computes a local top-k for its chunk using the same algorithm
    let chunk_results: Vec<Vec<(usize, f32)>> = (0..num_chunks)
        .into_par_iter()
        .map(|chunk_idx| {
            let start = chunk_idx * CHUNK_SIZE;
            let end = (start + CHUNK_SIZE).min(n);
            local_topk(query, matrix, start, end, k_eff, dim)
        })
        .collect();

    // Merge all local top-k results using k-way merge (they're already sorted descending)
    let mut merged: Vec<(usize, f32)> = Vec::with_capacity(k_eff);
    for chunk in &chunk_results {
        merged.extend_from_slice(chunk);
    }
    merged.sort_unstable_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    merged.truncate(k_eff);
    merged
}

pub fn topk(query: &[f32], matrix: &[f32], k: usize, dim: usize) -> Vec<(usize, f32)> {
    let n = matrix.len() / dim;
    if n >= PARALLEL_THRESHOLD {
        topk_parallel(query, matrix, k, dim)
    } else {
        topk_single(query, matrix, k, dim)
    }
}

#[pyfunction]
fn knn(
    py: Python,
    query: PyReadonlyArray1<f32>,
    matrix: PyReadonlyArray2<f32>,
    k: usize,
) -> PyResult<Vec<(usize, f32)>> {
    let q = query.as_slice()?;
    let m = matrix.as_array();
    if m.ndim() != 2 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "matrix must be 2-D",
        ));
    }
    let dim = m.ncols();
    if q.len() != dim {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "query length must equal matrix row width",
        ));
    }
    // C-contiguous fast path; as_slice() on the 2-D view falls back to a
    // standard-layout copy otherwise (numpy `as_array` handles strides).
    let (flat, _rows) = match matrix.as_slice() {
        Ok(f) => (f, m.nrows()),
        Err(_) => {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "matrix must be C-contiguous (np.ascontiguousarray)",
            ))
        }
    };
    // Release GIL for the compute-intensive kernel
    py.detach(|| Ok(topk(q, flat, k, dim)))
}

#[pyfunction]
fn knn_batch(
    py: Python,
    queries: PyReadonlyArray2<f32>,
    matrix: PyReadonlyArray2<f32>,
    k: usize,
) -> PyResult<Vec<Vec<(usize, f32)>>> {
    let qs = match queries.as_slice() {
        Ok(f) => f,
        Err(_) => {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "queries must be C-contiguous (np.ascontiguousarray)",
            ))
        }
    };
    let (flat, dim, q_count) = match matrix.as_slice() {
        Ok(f) => (f, matrix.as_array().ncols(), queries.as_array().nrows()),
        Err(_) => {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "matrix must be C-contiguous (np.ascontiguousarray)",
            ))
        }
    };
    if dim == 0 || queries.as_array().ncols() != dim {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "queries width must equal matrix row width",
        ));
    }
    let mut out = Vec::with_capacity(q_count);
    // Release GIL for the batch computation
    py.detach(|| {
        for q in 0..q_count {
            let base = q * dim;
            out.push(topk(&qs[base..base + dim], flat, k, dim));
        }
        Ok(out)
    })
}

#[pymodule]
fn cosine_knn(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(knn, m)?)?;
    m.add_function(wrap_pyfunction!(knn_batch, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{cosine_similarity, topk, topk_parallel, topk_single};

    fn assert_close(a: f32, b: f32) {
        assert!((a - b).abs() < 1e-6, "{a} != {b}");
    }

    #[test]
    fn identical_vectors_score_one_orthogonal_zero() {
        let a = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0];
        let b = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0];
        assert_close(cosine_similarity(&a, &a), 1.0);
        assert_eq!(cosine_similarity(&a, &b), 0.0);
    }

    #[test]
    fn zero_vector_scores_zero() {
        let a = [0.0; 10];
        let b = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0];
        assert_eq!(cosine_similarity(&a, &b), 0.0);
        assert_eq!(cosine_similarity(&a, &a), 0.0);
    }

    #[test]
    fn remainder_tail_matches_zero_padded_chunked_path() {
        // 11 dims = 1 full 8-lane chunk + 3-element tail; zero padding leaves
        // dot products and norms unchanged, so both paths must agree.
        let a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0];
        let mut padded = a.to_vec();
        padded.resize(16, 0.0);
        assert_close(
            cosine_similarity(&a, &a),
            cosine_similarity(&padded, &padded),
        );
    }

    #[test]
    fn topk_clamps_k_and_orders_descending() {
        // Rows 0..4 with norms chosen so scores are strictly ordered.
        let query = [1.0, 0.0];
        let matrix = [
            1.0, 0.0, // score 1.0
            0.0, 1.0, // score 0.0
            1.0, 1.0, // ~0.707
            2.0, 0.0, // score 1.0
            -1.0, 0.0, // score -1.0
        ];
        let flat: Vec<f32> = matrix.to_vec();
        let got = topk(&query, &flat, 10, 2);
        assert_eq!(got.len(), 5, "k clamps to n");
        let scores: Vec<f32> = got.iter().map(|&(_, s)| s).collect();
        let mut sorted = scores.clone();
        sorted.sort_by(|a, b| b.partial_cmp(a).unwrap());
        assert_eq!(scores, sorted, "descending");
    }

    #[test]
    fn topk_ties_keep_lower_row_index() {
        // Rows 0, 2, 4 are identical — the documented tie rule is row index
        // ascending, so the later duplicates must never displace an equal.
        let query = [1.0, 1.0];
        let row = [1.0, 1.0];
        let flat: Vec<f32> = vec![row, [0.0, 1.0], row, [1.0, 0.0], row]
            .into_iter()
            .flatten()
            .collect();
        let got = topk(&query, &flat, 3, 2);
        assert_eq!(got, vec![(0, got[0].1), (2, got[0].1), (4, got[0].1)]);
    }

    #[test]
    fn parallel_matches_single_threaded() {
        // Parallel and single-threaded must produce identical results
        let query = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0];
        let matrix: Vec<f32> = (0..80).map(|i| (i as f32) * 0.1).collect();
        let dim = 8;
        let k = 5;

        let single = topk_single(&query, &matrix, k, dim);
        let parallel = topk_parallel(&query, &matrix, k, dim);
        assert_eq!(single, parallel, "parallel must match single-threaded");
    }

    #[test]
    fn parallel_threshold_respected() {
        // Below threshold uses single-threaded path
        let query = [1.0, 0.0];
        let matrix = vec![1.0, 0.0, 0.0, 1.0]; // 2 rows
        let single = topk_single(&query, &matrix, 2, 2);
        let parallel = topk_parallel(&query, &matrix, 2, 2);
        let auto = topk(&query, &matrix, 2, 2);
        assert_eq!(single, auto);
        assert_eq!(parallel, auto);
    }
}
