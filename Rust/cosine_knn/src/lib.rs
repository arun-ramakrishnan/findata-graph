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
//! Build with `RUSTFLAGS="-C target-feature=+avx2,+fma"` (box-local wheel;
//! the abi3 .so is NOT portable to non-AVX2 machines).

use numpy::PyReadonlyArray1;
use numpy::PyReadonlyArray2;
use pyo3::prelude::*;

fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
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

fn topk(query: &[f32], matrix: &[f32], k: usize, dim: usize) -> Vec<(usize, f32)> {
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

#[pyfunction]
fn knn(
    query: PyReadonlyArray1<f32>,
    matrix: PyReadonlyArray2<f32>,
    k: usize,
) -> PyResult<Vec<(usize, f32)>> {
    let q = query.as_slice()?;
    let m = matrix.as_array();
    if m.ndim() != 2 {
        return Err(pyo3::exceptions::PyValueError::new_err("matrix must be 2-D"));
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
    Ok(topk(q, flat, k, dim))
}

#[pyfunction]
fn knn_batch(
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
    for q in 0..q_count {
        let base = q * dim;
        out.push(topk(&qs[base..base + dim], flat, k, dim));
    }
    Ok(out)
}

#[pymodule]
fn cosine_knn(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(knn, m)?)?;
    m.add_function(wrap_pyfunction!(knn_batch, m)?)?;
    Ok(())
}
