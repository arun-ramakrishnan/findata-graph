//! Structural properties of the all-pairs histogram kernel — same
//! discipline as cosine_knn's properties.rs: cheap invariants on
//! generated multigraphs (self-loops and duplicate entries included,
//! because scipy CSR construction sums duplicates but the kernel only
//! cares about reachability), plus closed-form families where every
//! histogram slot has an analytic value.

use graph_kernels::{all_pairs_hist, all_pairs_hist_seq, limited_bfs_hists, stats_from_hist};
use proptest::prelude::*;

/// Naive per-source reference BFS (out-neighbours, first-touch wins) —
/// deliberately unvectorised and independent of the kernel's code path.
fn ref_bfs(indptr: &[i32], indices: &[i32], src: usize) -> Vec<i32> {
    let m = indptr.len() - 1;
    let mut dist = vec![-1i32; m];
    let mut queue = std::collections::VecDeque::new();
    dist[src] = 0;
    queue.push_back(src);
    while let Some(v) = queue.pop_front() {
        let lo = indptr[v] as usize;
        let hi = indptr[v + 1] as usize;
        for &nb in &indices[lo..hi] {
            let nn = nb as usize;
            if dist[nn] < 0 {
                dist[nn] = dist[v] + 1;
                queue.push_back(nn);
            }
        }
    }
    dist
}

/// Reference ordered-pair histogram from the naive BFS.
fn ref_hist(indptr: &[i32], indices: &[i32]) -> Vec<u64> {
    let m = indptr.len() - 1;
    let mut hist = vec![0u64; m.max(2)];
    for s in 0..m {
        for d in ref_bfs(indptr, indices, s) {
            if d > 0 {
                hist[d as usize] += 1;
            }
        }
    }
    hist
}

/// with replacement (duplicates possible) and self-loops allowed —
/// the kernel must be invariant to both. Plain Strategy fn, the
/// cosine_knn properties.rs style (no prop_compose! dependency).
fn arb_csr() -> impl Strategy<Value = (Vec<i32>, Vec<i32>)> {
    (2usize..60).prop_flat_map(|n| {
        proptest::collection::vec(
            proptest::collection::vec(0..n as i32, 0..=(n / 3).max(1)),
            n,
        )
        .prop_map(move |rows| {
            let mut indptr = vec![0i32];
            let mut indices = Vec::new();
            for row in rows {
                indices.extend_from_slice(&row);
                indptr.push(indices.len() as i32);
            }
            (indptr, indices)
        })
    })
}

proptest! {
    #[test]
    fn kernel_matches_naive_reference_both_lanes(ip_ix in arb_csr()) {
        let (ip, ix) = ip_ix;
        let expect = ref_hist(&ip, &ix);
        assert_eq!(all_pairs_hist(&ip, &ix, false), expect);
        assert_eq!(all_pairs_hist(&ip, &ix, true), expect);
        assert_eq!(all_pairs_hist_seq(&ip, &ix), expect);
    }

    #[test]
    fn stats_reducible_from_reference(ip_ix in arb_csr()) {
        let (ip, ix) = ip_ix;
        let expect = ref_hist(&ip, &ix);
        let got = stats_from_hist(&all_pairs_hist(&ip, &ix, false));
        let total: u64 = expect.iter().sum();
        if total == 0 {
            assert_eq!(got, (0, 0.0, 0, 0));
        } else {
            let diameter = expect.iter().rposition(|&c| c > 0).unwrap() as i64;
            // median = np.median of the vals>0 multiset
            let mut acc = 0u64;
            let (k1, k2) = ((total - 1) / 2, total / 2);
            let (mut os1, mut os2) = (0i64, 0i64);
            for (d, &c) in expect.iter().enumerate() {
                acc += c;
                if os1 == 0 && acc > k1 { os1 = d as i64; }
                if os2 == 0 && acc > k2 { os2 = d as i64; }
            }
            assert_eq!(got.0, diameter);
            assert_eq!(got.1, (os1 as f64 + os2 as f64) / 2.0);
            assert_eq!(got.2, expect[diameter as usize]);
            assert_eq!(got.3, total);
        }
    }

    #[test]
    fn disconnected_components_total_is_sum_of_c_i_choose_2_ordered(
        sizes in prop::collection::vec(1usize..15, 1..8)) {
        // blocks of path components, sizes given; no cross edges
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        let mut base = 0usize;
        for &s in &sizes {
            for i in 0..s {
                if i > 0 { ix.push((base + i - 1) as i32); }
                if i + 1 < s { ix.push((base + i + 1) as i32); }
                ip.push(ix.len() as i32);
            }
            base += s;
        }
        let hist = all_pairs_hist(&ip, &ix, false);
        let total: u64 = hist.iter().sum();
        let expect: u64 = sizes.iter().map(|&c| (c as u64) * (c as u64 - 1)).sum();
        prop_assert_eq!(total, expect);
    }

    #[test]
    fn path_histogram_closed_form(n in 65usize..300) {
        // path graph: ordered pairs at distance d = 2*(n-d);
        // regression family for the 64-slot histogram panic
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for v in 0..n {
            if v > 0 { ix.push((v - 1) as i32); }
            if v + 1 < n { ix.push((v + 1) as i32); }
            ip.push(ix.len() as i32);
        }
        let hist = all_pairs_hist(&ip, &ix, false);
        prop_assert_eq!(hist.len(), n);
        for (d, &c) in hist.iter().enumerate() {
            if d == 0 { prop_assert_eq!(c, 0); }
            else { prop_assert_eq!(c, (2 * (n - d)) as u64); }
        }
        let (diam, _med, ties, total) = stats_from_hist(&hist);
        prop_assert_eq!(diam, (n - 1) as i64);
        prop_assert_eq!(ties, 2);
        prop_assert_eq!(total, (n * (n - 1)) as u64);
    }

    /// Random bipartite incidence: n_e entities (low ids) each linked to
    /// 1..=3 random hubs (high ids); kernel trail histograms must equal
    /// the naive full-dist-array reference for every entity source and
    /// every limit, mirroring the scipy parity the CLI A/B pins.
    #[test]
    fn trail_hists_match_naive_on_random_bipartite(
        n_e in 2usize..40,
        n_hubs in 1usize..40,
        seed in any::<u64>(),
        limit in 1usize..12,
    ) {
        use std::collections::VecDeque;
        let n = n_e + n_hubs;
        // xorshift from seed — deterministic per case, no rand dep
        let mut s = seed | 1;
        let mut nxt = move || {
            s ^= s << 13; s ^= s >> 7; s ^= s << 17; s
        };
        let mut adj: Vec<Vec<i32>> = vec![Vec::new(); n];
        for e in 0..n_e {
            let deg = 1 + (nxt() as usize) % 3.min(n_hubs);
            for _ in 0..deg {
                let h = n_e + (nxt() as usize) % n_hubs;
                adj[e].push(h as i32);
                adj[h].push(e as i32);
            }
        }
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for a in &adj {
            ix.extend_from_slice(a);
            ip.push(ix.len() as i32);
        }
        // naive reference (scipy dijkstra semantics, independent path)
        let width = limit / 2 + 1;
        for src in 0..n_e {
            let mut dist = vec![-1i32; n];
            let mut q = VecDeque::new();
            dist[src] = 0; q.push_back(src);
            while let Some(v) = q.pop_front() {
                let d = dist[v];
                if d as usize >= limit { continue; }
                for &w in &adj[v] {
                    if dist[w as usize] == -1 {
                        dist[w as usize] = d + 1; q.push_back(w as usize);
                    }
                }
            }
            let mut row_ref = vec![0u64; width];
            let mut reach_ref = 0u32;
            for (u, &d) in dist.iter().enumerate() {
                if d > 0 && d % 2 == 0 && u < n_e {
                    row_ref[d as usize / 2] += 1; reach_ref += 1;
                }
            }
            let (reach, hists) =
                limited_bfs_hists(&ip, &ix, &[src as i32], n_e, limit);
            prop_assert_eq!(reach[0], reach_ref);
            prop_assert_eq!(&hists[..], &row_ref[..]);
        }
    }
}
