//! Property tests for the cosine_knn kernels (proptest — the Hypothesis
//! analogue on record since rust_language_tooling #446's tool-mapping
//! verdict; suite filed as `rust_proptest_properties`, 2026-11-01).
//!
//! Each property GENERALIZES an existing unit test in lib.rs `mod tests`:
//! - `topk_scores_descending_and_match_recompute` ← topk_clamps_k_and_orders_descending
//! - `identical_rows_return_ascending_row_indices` ← topk_ties_keep_lower_row_index
//!   and `single_parallel_parity` / `tie_parity_at_scale` ← parallel_matches_single_threaded
//! - `router_matches_single_at_threshold_boundary` ← parallel_threshold_respected
//! - `zero_rows_score_zero` ← zero_vector_scores_zero
//! - `cosine_bounded_and_symmetric` ← identical_vectors_score_one_orthogonal_zero
//! - `output_length_is_min_k_n` ← topk_clamps_k_and_orders_descending
//!
//! Value strategy: small finite f32 (i8 → f32). f32::ANY admits NaN/inf,
//! which would panic the parallel path's `partial_cmp().unwrap()` sort —
//! out of scope here (Python side rejects non-finite blobs upstream).

use cosine_knn::{cosine_similarity, topk, topk_parallel, topk_single};
use proptest::prelude::*;

/// Small finite f32 values: i8 scaled — plenty for cosine semantics,
/// no NaN/inf, exact in f32 arithmetic for dims ≤ 64.
fn vals(len: usize) -> impl Strategy<Value = Vec<f32>> {
    proptest::collection::vec(any::<i8>(), len)
        .prop_map(|v| v.into_iter().map(|x| x as f32 / 8.0).collect::<Vec<f32>>())
}

/// n rows × dim cols flattened row-major.
fn matrix(n: usize, dim: usize) -> impl Strategy<Value = Vec<f32>> {
    vals(n * dim)
}

fn dim_strategy() -> impl Strategy<Value = usize> {
    // 1..=24 covers full 8-lane chunks (8/16/24) and remainder tails.
    1usize..=24
}

fn is_descending(scores: &[f32]) -> bool {
    scores.windows(2).all(|w| w[0] >= w[1])
}

proptest! {
    #![proptest_config(proptest::test_runner::Config::with_cases(256))]

    /// Generalizes topk_clamps_k_and_orders_descending: returned scores are
    /// descending AND each equals a directly recomputed cosine.
    #[test]
    fn topk_scores_descending_and_match_recompute(
        n in 1usize..=64,
        dim in dim_strategy(),
        k in 1usize..=70usize,
        q in vals(24usize),
        m in matrix(64usize, 24usize),
    ) {
        let query = &q[..dim];
        let flat = &m[..n * dim];
        let got = topk_single(query, flat, k, dim);
        prop_assert_eq!(got.len(), k.min(n));
        prop_assert!(is_descending(&got.iter().map(|&(_, s)| s).collect::<Vec<_>>()),
            "scores not descending: {:?}", got);
        for (row, score) in &got {
            let expected = cosine_similarity(query, &flat[row * dim..(row + 1) * dim]);
            prop_assert!((expected - score).abs() < 1e-5,
                "row {}: stored {} != recomputed {}", row, score, expected);
        }
    }

    /// Generalizes topk_ties_keep_lower_row_index: ALL rows identical →
    /// every score equal → the documented tie rule (row index ascending)
    /// forces exactly rows 0..k_eff in order, in BOTH paths.
    #[test]
    fn identical_rows_return_ascending_row_indices(
        n in 1usize..=64,
        dim in dim_strategy(),
        k in 1usize..=70usize,
        row in vals(24usize),
    ) {
        let flat: Vec<f32> = row[..dim].repeat(n);
        for (name, got) in [
            ("single", topk_single(&row[..dim], &flat, k, dim)),
            ("parallel", topk_parallel(&row[..dim], &flat, k, dim)),
        ] {
            let expected: Vec<usize> = (0..k.min(n)).collect();
            let rows: Vec<usize> = got.iter().map(|&(r, _)| r).collect();
            prop_assert_eq!(rows, expected, "{} tie order violated", name);
        }
    }

    /// Generalizes parallel_matches_single_threaded to random shapes:
    /// the two paths must agree EXACTLY (bitwise), not approximately.
    #[test]
    fn single_parallel_parity(
        n in 1usize..=512usize,
        dim in dim_strategy(),
        k in 1usize..=64usize,
        q in vals(24usize),
        m in matrix(512usize, 24usize),
    ) {
        let query = &q[..dim];
        let flat = &m[..n * dim];
        prop_assert_eq!(topk_single(query, flat, k, dim), topk_parallel(query, flat, k, dim),
            "parity broken at n={} dim={} k={}", n, dim, k);
    }

    /// The scale-shape parity killer: n crosses the 5k threshold into
    /// multi-chunk territory with ALL rows identical and k large — every
    /// chunk's local top-k is full of equal scores, so the merge sort's
    /// tie handling is fully exercised (small-k random matrices leave
    /// the merge nearly tie-free and can pass vacuously).
    #[test]
    fn tie_parity_at_scale(
        n in 5_000usize..=6_000usize,
        dim in 8usize..=16,
        k in 64usize..=512usize,
        row in vals(16usize),
    ) {
        let flat: Vec<f32> = row[..dim].repeat(n);
        prop_assert_eq!(topk_single(&row[..dim], &flat, k, dim),
                        topk_parallel(&row[..dim], &flat, k, dim),
                        "tie parity broken at n={} k={}", n, k);
    }

    /// Generalizes parallel_threshold_respected: the auto router must
    /// select the single-threaded result exactly at both sides of the
    /// 5k boundary (4_999 / 5_000 / 5_001).
    #[test]
    fn router_matches_single_at_threshold_boundary(
        n in prop::sample::select(vec![4_999usize, 5_000, 5_001]),
        k in 1usize..=32usize,
        q in vals(16usize),
        m in matrix(5_001usize, 16usize),
    ) {
        let dim = 16usize;
        let query = &q[..dim];
        let flat = &m[..n * dim];
        prop_assert_eq!(topk(query, flat, k, dim), topk_single(query, flat, k, dim),
            "router at n={} disagrees with single-threaded path", n);
    }

    /// Generalizes zero_vector_scores_zero: any all-zero row scores 0.0
    /// regardless of the query — including a zero query (0.0 == 0.0 rule).
    #[test]
    fn zero_rows_score_zero(
        n in 2usize..=32usize,
        dim in dim_strategy(),
        zero_idx in 0usize..=31usize,
        q in vals(24usize),
        m in matrix(32usize, 24usize),
    ) {
        let z = zero_idx % n;
        let query: Vec<f32> = q[..dim].to_vec();
        let mut flat = m[..n * dim].to_vec();
        for c in 0..dim {
            flat[z * dim + c] = 0.0;
        }
        let got = topk_single(&query, &flat, n, dim);
        let (_, score) = got.iter().find(|&&(r, _)| r == z).unwrap();
        prop_assert_eq!(*score, 0.0f32, "zero row {} scored {}", z, score);
    }

    /// Generalizes identical_vectors_score_one_orthogonal_zero: cosine is
    /// in [-1, 1] (fp tolerance) and symmetric.
    #[test]
    fn cosine_bounded_and_symmetric(
        dim in dim_strategy(),
        a in vals(24usize),
        b in vals(24usize),
    ) {
        let (a, b) = (&a[..dim], &b[..dim]);
        let ab = cosine_similarity(a, b);
        prop_assert!((-1.0 - 1e-5..=1.0 + 1e-5).contains(&ab), "cos {} out of range", ab);
        let ba = cosine_similarity(b, a);
        prop_assert!((ab - ba).abs() < 1e-6, "asymmetric: {} vs {}", ab, ba);
    }

    /// k-clamp at both extremes and in between: output length is always
    /// min(k, n) — including k = 0 (empty) and k far above n.
    #[test]
    fn output_length_is_min_k_n(
        n in 1usize..=48usize,
        k in 0usize..=96usize,
        dim in dim_strategy(),
        q in vals(24usize),
        m in matrix(48usize, 24usize),
    ) {
        let query = &q[..dim];
        let flat = &m[..n * dim];
        prop_assert_eq!(topk_single(query, flat, k, dim).len(), k.min(n));
        prop_assert_eq!(topk_parallel(query, flat, k, dim).len(), k.min(n));
    }
}
