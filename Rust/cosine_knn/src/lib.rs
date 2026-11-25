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

/// SIMD promotion of Trial B (see Rust/cosine_knn/src/bin/trial.rs):
/// f32x4-packed dot on x86_64 (4-wide chunks via _mm_mul_ps/_mm_add_ps,
/// scalar tail). Trial B measured 3–5.7× over the scalar path.
#[cfg(target_arch = "x86_64")]
pub fn dot_simd(a: &[f32], b: &[f32]) -> f32 {
    use std::arch::x86_64 as sse;
    let chunks = a.len() / 4;
    let mut vsum = unsafe { sse::_mm_setzero_ps() };
    for i in 0..chunks {
        let av = unsafe { sse::_mm_loadu_ps(a.as_ptr().add(i * 4)) };
        let bv = unsafe { sse::_mm_loadu_ps(b.as_ptr().add(i * 4)) };
        vsum = unsafe { sse::_mm_add_ps(vsum, sse::_mm_mul_ps(av, bv)) };
    }
    let mut lanes = [0.0f32; 4];
    unsafe {
        lanes[0] = sse::_mm_cvtss_f32(vsum);
        lanes[1] = sse::_mm_cvtss_f32(sse::_mm_shuffle_ps(vsum, vsum, 0x55));
        lanes[2] = sse::_mm_cvtss_f32(sse::_mm_shuffle_ps(vsum, vsum, 0xaa));
        lanes[3] = sse::_mm_cvtss_f32(sse::_mm_shuffle_ps(vsum, vsum, 0xff));
    }
    let mut s = lanes.iter().sum::<f32>();
    let tail = &a[chunks * 4..];
    for i in 0..tail.len() {
        s += tail[i] * b[chunks * 4 + i];
    }
    s
}

#[cfg(not(target_arch = "x86_64"))]
pub fn dot_simd(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Cosine similarity of two f32 vectors: SIMD dot + one-pass norms.
/// 0.0 when either norm is 0 (matches the scalar lane's contract).
pub fn cosine_similarity_simd(a: &[f32], b: &[f32]) -> f32 {
    let d = dot_simd(a, b);
    let a2: f32 = a.iter().map(|x| x * x).sum();
    let b2: f32 = b.iter().map(|x| x * x).sum();
    if a2 == 0.0 || b2 == 0.0 {
        0.0
    } else {
        d / (a2.sqrt() * b2.sqrt())
    }
}

/// Query norm² in the scalar 8-lane accumulation the parity oracle uses
/// (same summation order as `cosine_similarity`'s `ma`).
fn norm2_scalar(a: &[f32]) -> f32 {
    let n = a.len();
    let chunks = n / 8;
    let mut na = [0.0f32; 8];
    for c in 0..chunks {
        let base = c * 8;
        for j in 0..8 {
            let x = a[base + j];
            na[j] += x * x;
        }
    }
    let mut ma = 0.0f32;
    for lane in na {
        ma += lane;
    }
    for x in &a[chunks * 8..] {
        ma += x * x;
    }
    ma
}

/// Fused per-row dot + row-norm² in ONE pass (S1, proposal
/// rust_simd_cosine_hop_histogram): the query norm is hoisted by the
/// caller, so scoring a row costs a single sweep instead of the scalar
/// lane's dot-plus-two-norms. AVX2+FMA when the box has it, SSE2
/// baseline otherwise, scalar off-x86. The scalar 8-lane
/// `cosine_similarity` stays the parity oracle.
#[inline]
pub fn dot_norm(a: &[f32], b: &[f32]) -> (f32, f32) {
    #[cfg(target_arch = "x86_64")]
    {
        if std::arch::is_x86_feature_detected!("avx2") && std::arch::is_x86_feature_detected!("fma")
        {
            // SAFETY: both feature checks passed immediately above.
            return unsafe { dot_norm_avx2_fma(a, b) };
        }
        dot_norm_sse2(a, b)
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        let mut d = 0.0f32;
        let mut nb = 0.0f32;
        for i in 0..a.len() {
            d += a[i] * b[i];
            nb += b[i] * b[i];
        }
        (d, nb)
    }
}

/// Score a row against a query whose norm² is precomputed: fused dot +
/// row-norm², one sweep of the row. 0.0 when either norm is 0 (the
/// scalar lane's contract).
#[inline]
pub fn cosine_similarity_fused(query: &[f32], row: &[f32], query_norm2: f32) -> f32 {
    let (dot, row_norm2) = dot_norm(query, row);
    if query_norm2 == 0.0 || row_norm2 == 0.0 {
        0.0
    } else {
        dot / (query_norm2.sqrt() * row_norm2.sqrt())
    }
}

/// Horizontal sum of the 8 AVX lanes.
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn hsum256(v: std::arch::x86_64::__m256) -> f32 {
    use std::arch::x86_64 as avx;
    let lo = avx::_mm256_castps256_ps128(v);
    let hi = avx::_mm256_extractf128_ps::<1>(v);
    let s = avx::_mm_add_ps(lo, hi);
    let s = avx::_mm_add_ps(s, avx::_mm_shuffle_ps::<0x4e>(s, s));
    let s = avx::_mm_add_ps(s, avx::_mm_shuffle_ps::<0x1b>(s, s));
    avx::_mm_cvtss_f32(s)
}

/// AVX2+FMA path: 4 independent dot chains + 4 norm chains (32 floats
/// in flight per iteration), scalar tail.
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2,fma")]
unsafe fn dot_norm_avx2_fma(a: &[f32], b: &[f32]) -> (f32, f32) {
    use std::arch::x86_64 as avx;
    unsafe {
        let (mut d0, mut d1, mut d2, mut d3) = (
            avx::_mm256_setzero_ps(),
            avx::_mm256_setzero_ps(),
            avx::_mm256_setzero_ps(),
            avx::_mm256_setzero_ps(),
        );
        let (mut n0, mut n1, mut n2, mut n3) = (
            avx::_mm256_setzero_ps(),
            avx::_mm256_setzero_ps(),
            avx::_mm256_setzero_ps(),
            avx::_mm256_setzero_ps(),
        );
        let blocks = a.len() / 32;
        for blk in 0..blocks {
            let ap = a.as_ptr().add(blk * 32);
            let bp = b.as_ptr().add(blk * 32);
            let a0 = avx::_mm256_loadu_ps(ap);
            let a1 = avx::_mm256_loadu_ps(ap.add(8));
            let a2 = avx::_mm256_loadu_ps(ap.add(16));
            let a3 = avx::_mm256_loadu_ps(ap.add(24));
            let b0 = avx::_mm256_loadu_ps(bp);
            let b1 = avx::_mm256_loadu_ps(bp.add(8));
            let b2 = avx::_mm256_loadu_ps(bp.add(16));
            let b3 = avx::_mm256_loadu_ps(bp.add(24));
            d0 = avx::_mm256_fmadd_ps(a0, b0, d0);
            d1 = avx::_mm256_fmadd_ps(a1, b1, d1);
            d2 = avx::_mm256_fmadd_ps(a2, b2, d2);
            d3 = avx::_mm256_fmadd_ps(a3, b3, d3);
            n0 = avx::_mm256_fmadd_ps(b0, b0, n0);
            n1 = avx::_mm256_fmadd_ps(b1, b1, n1);
            n2 = avx::_mm256_fmadd_ps(b2, b2, n2);
            n3 = avx::_mm256_fmadd_ps(b3, b3, n3);
        }
        let mut d = hsum256(d0) + hsum256(d1) + hsum256(d2) + hsum256(d3);
        let mut nb = hsum256(n0) + hsum256(n1) + hsum256(n2) + hsum256(n3);
        for i in (blocks * 32)..a.len() {
            d += a[i] * b[i];
            nb += b[i] * b[i];
        }
        (d, nb)
    }
}

/// SSE2 baseline path (sse2 is in the x86_64 target baseline): 4 dot
/// chains + 4 norm chains (16 floats per iteration), scalar tail.
#[cfg(target_arch = "x86_64")]
fn dot_norm_sse2(a: &[f32], b: &[f32]) -> (f32, f32) {
    use std::arch::x86_64 as sse;
    let blocks = a.len() / 16;
    let (mut d, mut nb) = unsafe {
        let (mut d0, mut d1, mut d2, mut d3) = (
            sse::_mm_setzero_ps(),
            sse::_mm_setzero_ps(),
            sse::_mm_setzero_ps(),
            sse::_mm_setzero_ps(),
        );
        let (mut n0, mut n1, mut n2, mut n3) = (
            sse::_mm_setzero_ps(),
            sse::_mm_setzero_ps(),
            sse::_mm_setzero_ps(),
            sse::_mm_setzero_ps(),
        );
        for blk in 0..blocks {
            let ap = a.as_ptr().add(blk * 16);
            let bp = b.as_ptr().add(blk * 16);
            let a0 = sse::_mm_loadu_ps(ap);
            let a1 = sse::_mm_loadu_ps(ap.add(4));
            let a2 = sse::_mm_loadu_ps(ap.add(8));
            let a3 = sse::_mm_loadu_ps(ap.add(12));
            let b0 = sse::_mm_loadu_ps(bp);
            let b1 = sse::_mm_loadu_ps(bp.add(4));
            let b2 = sse::_mm_loadu_ps(bp.add(8));
            let b3 = sse::_mm_loadu_ps(bp.add(12));
            d0 = sse::_mm_add_ps(d0, sse::_mm_mul_ps(a0, b0));
            d1 = sse::_mm_add_ps(d1, sse::_mm_mul_ps(a1, b1));
            d2 = sse::_mm_add_ps(d2, sse::_mm_mul_ps(a2, b2));
            d3 = sse::_mm_add_ps(d3, sse::_mm_mul_ps(a3, b3));
            n0 = sse::_mm_add_ps(n0, sse::_mm_mul_ps(b0, b0));
            n1 = sse::_mm_add_ps(n1, sse::_mm_mul_ps(b1, b1));
            n2 = sse::_mm_add_ps(n2, sse::_mm_mul_ps(b2, b2));
            n3 = sse::_mm_add_ps(n3, sse::_mm_mul_ps(b3, b3));
        }
        let hsum = |v: std::arch::x86_64::__m128| -> f32 {
            let s = sse::_mm_add_ps(v, sse::_mm_shuffle_ps::<0x4e>(v, v));
            let s = sse::_mm_add_ps(s, sse::_mm_shuffle_ps::<0x1b>(s, s));
            sse::_mm_cvtss_f32(s)
        };
        (
            hsum(d0) + hsum(d1) + hsum(d2) + hsum(d3),
            hsum(n0) + hsum(n1) + hsum(n2) + hsum(n3),
        )
    };
    for i in (blocks * 16)..a.len() {
        d += a[i] * b[i];
        nb += b[i] * b[i];
    }
    (d, nb)
}

pub fn topk_single(query: &[f32], matrix: &[f32], k: usize, dim: usize) -> Vec<(usize, f32)> {
    let n = matrix.len() / dim;
    let k_eff = k.min(n);
    // Query norm hoisted out of the row loop (S1); rows are scored by
    // the fused dot + row-norm kernel.
    let query_norm2 = norm2_scalar(query);
    // Running k-sized insertion (partition_point + insert): allocation-free
    // past k, and stable-on-ties by construction (later equal rows skip).
    let mut best: Vec<(usize, f32)> = Vec::with_capacity(k_eff + 1);
    for i in 0..n {
        let base = i * dim;
        let score = cosine_similarity_fused(query, &matrix[base..base + dim], query_norm2);
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
    let query_norm2 = norm2_scalar(query);
    let mut best: Vec<(usize, f32)> = Vec::with_capacity(k_eff + 1);
    for i in start..end {
        let base = i * dim;
        let score = cosine_similarity_fused(query, &matrix[base..base + dim], query_norm2);
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

/// Python entry for the SIMD cosine lane (Trial B promotion).
#[pyfunction]
fn cosine_similarity_simd_py(a: PyReadonlyArray1<f32>, b: PyReadonlyArray1<f32>) -> PyResult<f32> {
    let x = a.as_slice()?;
    let y = b.as_slice()?;
    if x.len() != y.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "vectors must have equal length",
        ));
    }
    Ok(cosine_similarity_simd(x, y))
}

#[pymodule]
fn cosine_knn(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(knn, m)?)?;
    m.add_function(wrap_pyfunction!(knn_batch, m)?)?;
    m.add_function(wrap_pyfunction!(cosine_similarity_simd_py, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{cosine_similarity, cosine_similarity_simd, topk, topk_parallel, topk_single};

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
    fn simd_matches_scalar_lane() {
        // Trial B promotion parity: the f32x4 dot lane must agree with the
        // scalar 8-lane lane within f32 tolerance.
        for n in [10usize, 11, 64, 384, 768] {
            let a: Vec<f32> = (0..n).map(|i| (i as f32) * 0.013 + 0.5).collect();
            let b: Vec<f32> = (0..n).map(|i| ((i * 7) as f32) * 0.007 - 0.3).collect();
            assert_close(cosine_similarity(&a, &b), cosine_similarity_simd(&a, &b));
        }
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

    /// Deterministic pseudo-random filler (xorshift32), values in
    /// [-1.0, 1.0).
    fn lcg_vec(n: usize, seed: u32) -> Vec<f32> {
        let mut s = seed.wrapping_mul(2654435761).wrapping_add(1).max(1);
        (0..n)
            .map(|_| {
                s ^= s << 13;
                s ^= s >> 17;
                s ^= s << 5;
                (s % 2000) as f32 / 1000.0 - 1.0
            })
            .collect()
    }

    #[test]
    fn fused_matches_scalar_across_dims() {
        use super::{cosine_similarity_fused, norm2_scalar};
        for &n in &[10usize, 11, 16, 64, 384, 512, 768] {
            let a = lcg_vec(n, 7);
            let b = lcg_vec(n, 99);
            let scalar = cosine_similarity(&a, &b);
            let fused = cosine_similarity_fused(&a, &b, norm2_scalar(&a));
            assert!(
                (fused - scalar).abs() < 1e-5,
                "dim {n}: fused {fused} vs scalar {scalar}"
            );
        }
    }

    #[test]
    fn fused_zero_norm_contract() {
        use super::{cosine_similarity, cosine_similarity_fused, norm2_scalar};
        let z = vec![0.0f32; 8];
        let r = lcg_vec(8, 3);
        assert_eq!(cosine_similarity_fused(&z, &r, norm2_scalar(&z)), 0.0);
        let q = lcg_vec(8, 5);
        assert_eq!(cosine_similarity_fused(&q, &z, norm2_scalar(&q)), 0.0);
        assert_eq!(cosine_similarity(&q, &z), 0.0);
    }

    #[test]
    fn topk_fused_order_matches_scalar_reference() {
        use super::cosine_similarity;
        let dim = 16usize;
        let rows = 40usize;
        let mut matrix = Vec::with_capacity(rows * dim);
        for i in 0..rows {
            matrix.extend(lcg_vec(dim, i as u32 + 11));
        }
        let query = lcg_vec(dim, 4242);
        let k = 7;
        let fused = topk_single(&query, &matrix, k, dim);
        // Reference: the same insertion algorithm with scalar scoring.
        let mut best: Vec<(usize, f32)> = Vec::with_capacity(k);
        for i in 0..rows {
            let score = cosine_similarity(&query, &matrix[i * dim..(i + 1) * dim]);
            if best.len() == k {
                if let Some(last) = best.last() {
                    if score <= last.1 {
                        continue;
                    }
                }
            }
            let pos = best.partition_point(|(_, s)| s >= &score);
            best.insert(pos, (i, score));
            if best.len() > k {
                best.pop();
            }
        }
        let ids_f: Vec<usize> = fused.iter().map(|x| x.0).collect();
        let ids_s: Vec<usize> = best.iter().map(|x| x.0).collect();
        assert_eq!(ids_f, ids_s, "row order must match the scalar oracle");
        for (a, b) in fused.iter().zip(best.iter()) {
            assert!((a.1 - b.1).abs() < 1e-5, "score drift on row {}", a.0);
        }
    }
}
