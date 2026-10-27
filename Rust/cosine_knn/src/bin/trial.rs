//! Trial B: cosine similarity / KNN (mirrors helpers/graph/query.py
//! `similar_notes` / `rebuild_script_search.py::_cosine_leg`).
//!
//! Computes cosine similarity between a query vector (f32) and every row of
//! an N x 384 f32 matrix, returns top-K. Three implementations:
//!   1. scalar   — plain loops (like Python baseline per-row)
//!   2. simd     — f32x4-packed dot on x86_64 (4-wide chunks, scalar tail)
//!   3. blocked  — cache-blocked scalar pass (reuses query vector in L1)
//!
//! Benchmarks top-K over N queries vs N rows; reports queries/s and
//! speedup vs. the scalar path. Self-contained (std arch detection only).

use std::hint::black_box;
use std::time::Instant;

const DIM: usize = 384;
const TOPK: usize = 10;

/// Dot product of two f32 vectors (scalar).
fn dot_scalar(a: &[f32], b: &[f32]) -> f32 {
    let mut s = 0.0_f32;
    for i in 0..DIM {
        s += a[i] * b[i];
    }
    s
}

/// Top-K (best single match) over all rows for one query, scalar.
fn topk_scalar(query: &[f32], qnorm: f32, rows: &[Vec<f32>], norms: &[f32]) -> (usize, f32) {
    let mut best_idx = 0usize;
    let mut best = f32::NEG_INFINITY;
    for (i, row) in rows.iter().enumerate() {
        let denom = qnorm * norms[i];
        let c = if denom == 0.0 {
            f32::NEG_INFINITY
        } else {
            dot_scalar(query, row) / denom
        };
        if c > best {
            best = c;
            best_idx = i;
        }
    }
    black_box(best_idx);
    black_box(best);
    (best_idx, best)
}

/// Cache-blocked scalar pass: process rows in blocks so the query vector
/// stays hot in registers / L1.
fn topk_blocked(
    query: &[f32],
    qnorm: f32,
    rows: &[Vec<f32>],
    norms: &[f32],
    block: usize,
) -> (usize, f32) {
    let mut best_idx = 0usize;
    let mut best = f32::NEG_INFINITY;
    for block_start in (0..rows.len()).step_by(block) {
        let block_end = (block_start + block).min(rows.len());
        for (i, row) in rows
            .iter()
            .enumerate()
            .skip(block_start)
            .take(block_end - block_start)
        {
            let denom = qnorm * norms[i];
            let c = if denom == 0.0 {
                f32::NEG_INFINITY
            } else {
                dot_scalar(query, row) / denom
            };
            if c > best {
                best = c;
                best_idx = i;
            }
        }
    }
    black_box(best_idx);
    black_box(best);
    (best_idx, best)
}

/// Norm of a vector.
fn norm(v: &[f32]) -> f32 {
    let mut s = 0.0_f32;
    for x in v {
        s += x * x;
    }
    s.sqrt()
}

/// Dot product with f32x4-packed 4-wide chunks (SSE/AVX2 portable), scalar tail.
#[cfg(target_arch = "x86_64")]
fn dot_simd(a: &[f32], b: &[f32]) -> f32 {
    let chunks = a.len() / 4;
    let mut vsum = unsafe { core::arch::x86_64::_mm_setzero_ps() };
    for i in 0..chunks {
        let av = unsafe { core::arch::x86_64::_mm_loadu_ps(a.as_ptr().add(i * 4)) };
        let bv = unsafe { core::arch::x86_64::_mm_loadu_ps(b.as_ptr().add(i * 4)) };
        vsum =
            unsafe { core::arch::x86_64::_mm_add_ps(vsum, core::arch::x86_64::_mm_mul_ps(av, bv)) };
    }
    let mut sum = [0.0_f32; 4];
    unsafe {
        sum[0] = core::arch::x86_64::_mm_cvtss_f32(vsum);
        sum[1] =
            core::arch::x86_64::_mm_cvtss_f32(core::arch::x86_64::_mm_shuffle_ps(vsum, vsum, 0x55));
        sum[2] =
            core::arch::x86_64::_mm_cvtss_f32(core::arch::x86_64::_mm_shuffle_ps(vsum, vsum, 0xaa));
        sum[3] =
            core::arch::x86_64::_mm_cvtss_f32(core::arch::x86_64::_mm_shuffle_ps(vsum, vsum, 0xff));
    }
    let mut s = sum.iter().sum::<f32>();
    let tail = &a[chunks * 4..];
    for i in 0..tail.len() {
        s += tail[i] * b[chunks * 4 + i];
    }
    s
}

/// Top-K over all rows for one query, simd dot.
#[cfg(target_arch = "x86_64")]
fn topk_simd(query: &[f32], qnorm: f32, rows: &[Vec<f32>], norms: &[f32]) -> (usize, f32) {
    let mut best_idx = 0usize;
    let mut best = f32::NEG_INFINITY;
    for (i, row) in rows.iter().enumerate() {
        let denom = qnorm * norms[i];
        let c = if denom == 0.0 {
            f32::NEG_INFINITY
        } else {
            dot_simd(query, row) / denom
        };
        if c > best {
            best = c;
            best_idx = i;
        }
    }
    black_box(best_idx);
    black_box(best);
    (best_idx, best)
}

/// Deterministic f32 matrix: N x DIM, row i derived from i.
fn gen_matrix(n: usize) -> Vec<Vec<f32>> {
    (0..n)
        .map(|i| {
            (0..DIM)
                .map(|j| ((i * 31 + j * 7) as f32).sin().cos())
                .collect()
        })
        .collect()
}

fn main() {
    println!("Trial B — cosine similarity / KNN (Rust)");
    println!("dim={DIM} top-k={TOPK} (mirrors query.py similar_notes)");
    println!();

    for &n_queries in &[1usize, 4, 16] {
        for &n_rows in &[1024usize, 4096, 16384] {
            let matrix = gen_matrix(n_rows);
            let queries: Vec<Vec<f32>> = (0..n_queries)
                .map(|i| {
                    (0..DIM)
                        .map(|j| ((i * 13 + j * 3) as f32).cos().sin())
                        .collect()
                })
                .collect();
            let norms: Vec<f32> = matrix.iter().map(|v| norm(v)).collect();
            let qnorms: Vec<f32> = queries.iter().map(|v| norm(v)).collect();

            // warmup
            let _ = topk_scalar(&queries[0], qnorms[0], &matrix, &norms);

            // scalar
            let t0 = Instant::now();
            for qi in 0..n_queries {
                let _ = topk_scalar(&queries[qi], qnorms[qi], &matrix, &norms);
            }
            let t_scalar = t0.elapsed().as_secs_f64();
            let qps_scalar = n_queries as f64 / t_scalar;

            // simd (x86_64 only)
            let (t_simd, qps_simd) = if cfg!(target_arch = "x86_64") {
                let t0 = Instant::now();
                for qi in 0..n_queries {
                    let _ = topk_simd(&queries[qi], qnorms[qi], &matrix, &norms);
                }
                let t = t0.elapsed().as_secs_f64();
                (t, n_queries as f64 / t)
            } else {
                (0.0, 0.0)
            };

            // blocked scalar
            let block = if n_rows <= 4096 { 1024 } else { 2048 };
            let t0 = Instant::now();
            for qi in 0..n_queries {
                let _ = topk_blocked(&queries[qi], qnorms[qi], &matrix, &norms, block);
            }
            let t_blocked = t0.elapsed().as_secs_f64();
            let qps_blocked = n_queries as f64 / t_blocked;

            println!(
                "queries={n_queries:<3} rows={n_rows:<6}  scalar={qps_scalar:>9.1} q/s  \
                 simd={qps_simd:>9.1} q/s  blocked={qps_blocked:>9.1} q/s  \
                 simd/scalar={}x  blocked/scalar={}x",
                if t_simd > 0.0 {
                    format!("{:.2}", qps_simd / qps_scalar)
                } else {
                    "n/a".to_string()
                },
                qps_blocked / qps_scalar
            );
        }
        println!();
    }

    println!("NOTE: Python baseline (Rust/bench/cosine_bench.py)");
    println!("should run numpy/scipy cosine over the same matrix for");
    println!("direct speedup comparison.");
}
