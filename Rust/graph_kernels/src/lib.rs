//! graph_kernels: exact BFS statistics over CSR graphs — the graph
//! stats kernel lane (proposal rust_bfs_kernels_stats_trails S1/S2a,
//! filed 2026-11-09).
//!
//! Python API (PyO3 + numpy, zero-copy):
//! - `all_pairs_bfs_stats(indptr: np.int32[n+1], indices: np.int32[nnz])`
//!   `-> (diameter: int, median: float, ties: int, total: int, hist: np.uint64[m])`
//! - `personalized_pagerank(indptr, indices, seed: int, alpha, tol, max_iter)`
//!   `-> np.float64[n]` — S2 prototype: power-iteration PPR, scipy-lane
//!   parity 1.7e-13 on the live projection, ~10× faster per seed
//!   (2026-11-14, completed.md #522 follow-up).
//!
//! Semantics — byte-for-byte the numbers `helpers/graph/stats.py`
//! derives from a full scipy all-pairs matrix (`_chain_dist_stats`):
//! - hist[d] counts ORDERED pairs (a, b), a != b, with dist(a, b) = d
//!   under BFS over out-neighbours only; callers pass symmetric CSR for
//!   undirected graphs (same orientation contract as the incumbent
//!   frontier BFS in stats.py).
//! - self-distance zeros and unreachable pairs contribute nothing;
//! - median = mean of order statistics k1 = (total-1)//2 and
//!   k2 = total//2 over the integer vals>0 population — np.median of
//!   the same multiset;
//! - diameter = max d with hist[d] > 0 (0 when no edges), ties =
//!   hist[diameter], total = hist.sum() (all ordered, i.e. NOT halved;
//!   callers halve for canonical a<b counting where their mode does).
//!
//! Performance (i5-6500, 4C/4T, 2026-11-09 survey): live ALL projection
//! n=19,130 / nnz=73,676 exact all-pairs = 1.16 s vs 37.6 s incumbent
//! (scipy shortest_path sampling + iFUB per component); scratch = one
//! i32 dist + two u32 frontier buffers per worker (~0.3 MB across 4
//! threads) vs the sampled path's 1.75 GB dist+pred materialisation.
//!
//! Histogram sizing is a pinned lesson (tests/properties.rs): the
//! distance histogram must be m.max(2) slots — a path graph reaches
//! distance m-1, and the first survey probe draft's fixed 64-slot
//! histogram panicked at exactly m=64 ("index out of bounds: the len is
//! 64 but the index is 64"), a shape the live graph (diameter 13) can
//! never expose. Structural families, not live data, catch this class.
//!
//! The abi3 .so is box-local (target-cpu=native arrives via
//! Rust/.cargo/config.toml build.rustflags); `make rust-install` copies
//! it into venv site-packages. Python callers degrade to the incumbent
//! lane on ImportError (selector pattern, cosine_knn S4).

use numpy::{IntoPyArray, PyArray1, PyArray2, PyReadonlyArray1};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

/// Roots below this run the sequential loop — rayon pool handoff costs
/// more than the sweeps at toy sizes (tests, unit graphs).
pub const PARALLEL_THRESHOLD: usize = 1_024;
/// Roots per rayon task; each task owns scratch (stamp + two frontiers).
const ROOT_CHUNK: usize = 256;

/// Per-sweep scratch for [`bfs_hist`]: the epoch stamp array plus the two
/// frontier buffers. One struct so the sweep signature stays under
/// clippy's argument ceiling and the caller owns exactly one lifetime.
pub struct BfsScratch {
    stamp: Vec<u32>,
    frontier: Vec<u32>,
    next: Vec<u32>,
    epoch: u32,
}

impl BfsScratch {
    fn new(m: usize) -> Self {
        Self {
            stamp: vec![0u32; m],
            frontier: Vec::with_capacity(m),
            next: Vec::with_capacity(m),
            epoch: 0,
        }
    }

    /// Advance to the next root's epoch, re-zeroing the stamps only at
    /// u32::MAX wrap (never in practice) so a stale stamp cannot
    /// false-match a fresh epoch.
    fn next_root(&mut self) {
        self.epoch = self.epoch.wrapping_add(1);
        if self.epoch == 0 {
            self.stamp.fill(0);
            self.epoch = 1;
        }
    }
}

/// One BFS sweep from `src`, folding per-distance ordered-pair counts
/// into `hist`. Visit marking is by `scratch.epoch` (the caller advances
/// it per root), so there is NO per-root O(m) reset — the old
/// `dist.fill(-1)` per root cost ~5–6% wall on the 22.3k-node all-pairs
/// bench (6 interleaved rounds, 2026-11-14) and the stored depth was
/// never read (the histogram folds `next.len()` per level). Duplicate CSR
/// entries and self-loops are harmless — the stamp!=epoch visit check
/// sees each node once. Stamps arrive zeroed (0 = never-visited).
///
/// Scratch travels as one `&mut` (`BfsScratch`) rather than four loose
/// params: clippy's too_many_arguments fired at 8/7 when `epoch` joined
/// the signature, and the four buffers are one lifetime anyway.
fn bfs_hist(
    indptr: &[i32],
    indices: &[i32],
    src: usize,
    scratch: &mut BfsScratch,
    hist: &mut [u64],
) {
    let epoch = scratch.epoch;
    scratch.stamp[src] = epoch;
    scratch.frontier.clear();
    scratch.frontier.push(src as u32);
    let mut level: usize = 0;
    while !scratch.frontier.is_empty() {
        level += 1;
        if level >= hist.len() {
            // Unreachable on well-formed CSR (max distance is m-1 and
            // hist is sized m.max(2)); guard rather than trust callers.
            break;
        }
        for &v in scratch.frontier.iter() {
            let lo = indptr[v as usize] as usize;
            let hi = indptr[v as usize + 1] as usize;
            for &nb in &indices[lo..hi] {
                let nn = nb as usize;
                if scratch.stamp[nn] != epoch {
                    scratch.stamp[nn] = epoch;
                    scratch.next.push(nb as u32);
                }
            }
        }
        hist[level] += scratch.next.len() as u64;
        std::mem::swap(&mut scratch.frontier, &mut scratch.next);
        scratch.next.clear();
    }
}

/// Sequential all-pairs sweep — the reference lane and the small-graph
/// path. Exposed so tests pin seq/par bit-identity above threshold.
pub fn all_pairs_hist_seq(indptr: &[i32], indices: &[i32]) -> Vec<u64> {
    let m = indptr.len() - 1;
    let hist_len = m.max(2);
    let mut hist = vec![0u64; hist_len];
    let mut scratch = BfsScratch::new(m);
    for src in 0..m {
        scratch.next_root();
        bfs_hist(indptr, indices, src, &mut scratch, &mut hist);
    }
    hist
}

/// Exact all-pairs distance histogram over every root. Sequential below
/// `PARALLEL_THRESHOLD` (or `force_parallel` for tests pinning seq/par
/// parity); otherwise rayon over root chunks, each folding a chunk-local
/// histogram that is summed once at the end — sum order differs from the
/// sequential loop but u64 addition is associative/commutative, so the
/// histogram is bit-identical either way.
pub fn all_pairs_hist(indptr: &[i32], indices: &[i32], force_parallel: bool) -> Vec<u64> {
    let m = indptr.len() - 1;
    let hist_len = m.max(2);
    if !force_parallel && m < PARALLEL_THRESHOLD {
        all_pairs_hist_seq(indptr, indices)
    } else {
        let chunks = m.div_ceil(ROOT_CHUNK);
        let local: Vec<Vec<u64>> = (0..chunks)
            .into_par_iter()
            .map(|ci| {
                let mut local = vec![0u64; hist_len];
                let mut scratch = BfsScratch::new(m);
                let lo = ci * ROOT_CHUNK;
                let hi = (lo + ROOT_CHUNK).min(m);
                for src in lo..hi {
                    scratch.next_root();
                    bfs_hist(indptr, indices, src, &mut scratch, &mut local);
                }
                local
            })
            .collect();
        let mut hist = vec![0u64; hist_len];
        for h in &local {
            for (g, v) in hist.iter_mut().zip(h.iter()) {
                *g += v;
            }
        }
        hist
    }
}

/// (diameter, median, ties at diameter, total) from an ordered-pair
/// histogram — the `_chain_dist_stats` reductions, zero-copy callers
/// notwithstanding.
pub fn stats_from_hist(hist: &[u64]) -> (i64, f64, u64, u64) {
    let total: u64 = hist.iter().sum();
    if total == 0 {
        return (0, 0.0, 0, 0);
    }
    let diameter = hist.iter().rposition(|&c| c > 0).unwrap_or(0) as i64;
    // np.median over the integer population: mean of order statistics
    // k1 = (total-1)//2 and k2 = total//2 (0-based).
    let order_stat = |k: u64| -> i64 {
        let mut acc: u64 = 0;
        for (d, &c) in hist.iter().enumerate() {
            acc += c;
            if acc > k {
                return d as i64;
            }
        }
        diameter // defensive; unreachable when k < total
    };
    let k1 = (total - 1) / 2;
    let k2 = total / 2;
    let median = (order_stat(k1) as f64 + order_stat(k2) as f64) / 2.0;
    (diameter, median, hist[diameter as usize], total)
}

/// Exact all-pairs BFS statistics over an int32 CSR graph.
///
/// `indptr` (n+1, non-decreasing, last == len(indices)) and `indices`
/// (neighbour lists, out-direction; duplicates and self-loops ignored)
/// arrive zero-copy through the buffer protocol. Returns
/// (diameter, median, ties at diameter, total ordered pairs, hist) with
/// the semantics documented at the crate root.
#[pyfunction]
#[allow(clippy::type_complexity)] // 5-tuple of Python scalars + one array — the API shape
fn all_pairs_bfs_stats<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
) -> PyResult<(i64, f64, u64, u64, Bound<'py, PyArray1<u64>>)> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    let (diameter, median, ties, total, hist) = py.detach(|| {
        let hist = all_pairs_hist(ip, ix, false);
        let stats = stats_from_hist(&hist);
        (stats.0, stats.1, stats.2, stats.3, hist)
    });
    Ok((diameter, median, ties, total, hist.into_pyarray(py)))
}

/// Per-source LIMITED BFS hop histograms over entity nodes (the
/// hyper_trails lane). For each source: frontier BFS capped at
/// `limit` bipartite steps; entities (node ids < `n_entities`)
/// reached at EVEN distance L in 2..=limit increment hist[L/2] — hops
/// are L/2, the source itself (distance 0) is excluded, and mid-hop
/// hub endpoints (odd distances) are never counted. Mirrors
/// `_trail_bfs` in helpers/graph/hyper_trails.py exactly.
///
/// Returns (reachable per source, flat row-major histograms of width
/// limit/2 + 1). Sources are independent → rayon over them; one
/// batched call replaces per-source subprocess or scipy passes (the
/// Wall-2 Mojo lesson). Visits are marked by a zeroed stamp array
/// (S6): the old `dist` vec was write-only in this loop (depth lives
/// in `level`), and a thread-local epoch-stamp variant measured 5%
/// SLOWER than per-source alloc + zero fill at every scale tested
/// (22k/100k/300k nodes) — the memset is not the bottleneck, so the
/// stamp is allocated per source like the dist was.
pub fn limited_bfs_hists(
    indptr: &[i32],
    indices: &[i32],
    sources: &[i32],
    n_entities: usize,
    limit: usize,
) -> (Vec<u32>, Vec<u64>) {
    let m = indptr.len() - 1;
    let width = limit / 2 + 1;
    let rows: Vec<(u32, Vec<u64>)> = sources
        .par_iter()
        .map(|&src| {
            let src = src as usize;
            let mut row = vec![0u64; width];
            let mut row_reach: u32 = 0;
            // Stamp marks visits (0 = unvisited); depth is `level`, so
            // no per-node dist store is needed. Per-source alloc, like
            // the dist it replaces — TLS/epoch reuse measured slower.
            let mut stamp = vec![0u32; m];
            let mut frontier: Vec<u32> = Vec::with_capacity(m);
            let mut next: Vec<u32> = Vec::with_capacity(m);
            stamp[src] = 1;
            frontier.push(src as u32);
            let mut level: usize = 0;
            while !frontier.is_empty() && level < limit {
                next.clear();
                for &v in &frontier {
                    let lo = indptr[v as usize] as usize;
                    let hi = indptr[v as usize + 1] as usize;
                    for &w in &indices[lo..hi] {
                        let wu = w as usize;
                        if stamp[wu] == 0 {
                            stamp[wu] = 1;
                            next.push(w as u32);
                        }
                    }
                }
                level += 1;
                if level.is_multiple_of(2) {
                    let h = level / 2;
                    for &w in &next {
                        if (w as usize) < n_entities {
                            row[h] += 1;
                            row_reach += 1;
                        }
                    }
                }
                std::mem::swap(&mut frontier, &mut next);
            }
            (row_reach, row)
        })
        .collect();
    let reach: Vec<u32> = rows.iter().map(|r| r.0).collect();
    // One memcpy of the whole histogram block (rows are contiguous
    // after collect) instead of three copies + per-row cloning.
    let mut hists = vec![0u64; rows.len() * width];
    for (i, r) in rows.iter().enumerate() {
        hists[i * width..(i + 1) * width].copy_from_slice(&r.1);
    }
    (reach, hists)
}

/// Batched limited-BFS trail histograms (S3, rust_bfs_kernels_stats_trails).
///
/// One call computes every source's (reachable entities, hop histogram)
/// inside the kernel — rayon across sources, scratch per source. Width
/// of each histogram row is limit/2 + 1 (hop 0 is always empty: the
/// source is excluded). Raises ValueError on malformed CSR, sources out
/// of range, n_entities > n, or negative limit.
#[pyfunction]
#[allow(clippy::type_complexity)] // numpy-bound 2-tuple return — the API shape
fn limited_bfs_sources<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
    sources: PyReadonlyArray1<'py, i32>,
    n_entities: i64,
    limit: i64,
) -> PyResult<(Bound<'py, PyArray1<u32>>, Bound<'py, PyArray2<u64>>)> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    let srcs: &[i32] = sources.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    if n_entities < 0 || n_entities as usize > n {
        return Err(PyValueError::new_err("n_entities must be in 0..=n"));
    }
    if limit < 0 {
        return Err(PyValueError::new_err("limit must be non-negative"));
    }
    if srcs.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("sources out of range"));
    }
    let (reach, hists) =
        py.detach(|| limited_bfs_hists(ip, ix, srcs, n_entities as usize, limit as usize));
    let width = limit as usize / 2 + 1;
    // Zero-copy return: move the flat buffer into the ndarray backing
    // store (no Vec<Vec>, no memcpy — from_owned_array reuses the Vec).
    let n_src = srcs.len();
    let arr = ndarray::Array2::from_shape_vec((n_src, width), hists)
        .map_err(|e| PyValueError::new_err(format!("hist shape: {e}")))?;
    Ok((reach.into_pyarray(py), PyArray2::from_owned_array(py, arr)))
}

/// Personalized PageRank on CSR — S2 prototype (power iteration).
///
/// Mirrors the scipy exact lane `personalized_pagerank_scores`
/// (`helpers/graph/scipy_bridge.py`): `P = D^-1 A` row-stochastic over
/// the BINARY adjacency (this kernel takes only topology — unweighted
/// CSR, same contract as the BFS entries), dangling rows teleport to
/// `seed`, teleport vector `v` one-hot on `seed`. Iterates
/// `x' = alpha * P^T x + (1-alpha) v` from `x0 = v` until the L1 delta
/// drops below `tol` or `max_iter` is hit; sums stay 1 by construction.
/// Duplicates in a CSR row are summed consistently (they inflate both
/// the row's entries and its degree), self-loops are just entries.
/// Validation mirrors `limited_bfs_sources`; ValueError on malformed
/// CSR, out-of-range seed, alpha outside (0, 1), tol <= 0,
/// max_iter < 1. Returns float64 scores (len n).
#[pyfunction]
fn personalized_pagerank<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
    seed: i64,
    alpha: f64,
    tol: f64,
    max_iter: i64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    if !(alpha > 0.0 && alpha < 1.0) {
        return Err(PyValueError::new_err("alpha must be in (0, 1)"));
    }
    if tol <= 0.0 {
        return Err(PyValueError::new_err("tol must be positive"));
    }
    if max_iter < 1 {
        return Err(PyValueError::new_err("max_iter must be >= 1"));
    }
    if seed < 0 || seed as usize >= n {
        return Err(PyValueError::new_err("seed out of range"));
    }
    let seed = seed as usize;
    let x = py.detach(|| ppr_power_iterate_seed(ip, ix, seed, alpha, tol, max_iter as usize));
    Ok(ndarray::Array1::from(x).into_pyarray(py))
}

/// Personalized PageRank with a SET teleport vector (S5, 2026-11-14).
///
/// Same math as `personalized_pagerank` but the teleport distribution
/// `v` is arbitrary (normalized to sum 1 here — zeros allowed, negative
/// entries rejected). Dangling rows redistribute their mass ∝ `v`, the
/// natural generalization of the one-hot lane (P[d, :] = v), which
/// reduces to P[d, seed] = 1 when v is one-hot. Use: "the relational
/// neighborhood of every entity a note/document mentions" — the note
/// picks where teleport mass lands without polluting the walk.
#[pyfunction]
fn personalized_pagerank_vec<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
    teleport: PyReadonlyArray1<'py, f64>,
    alpha: f64,
    tol: f64,
    max_iter: i64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    let v: &[f64] = teleport.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&x| x < 0 || x as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    if !(alpha > 0.0 && alpha < 1.0) {
        return Err(PyValueError::new_err("alpha must be in (0, 1)"));
    }
    if tol <= 0.0 {
        return Err(PyValueError::new_err("tol must be positive"));
    }
    if max_iter < 1 {
        return Err(PyValueError::new_err("max_iter must be >= 1"));
    }
    if v.len() != n {
        return Err(PyValueError::new_err(
            "teleport length must equal the node count",
        ));
    }
    if v.iter().any(|&x| !x.is_finite() || x < 0.0) {
        return Err(PyValueError::new_err(
            "teleport entries must be finite and non-negative",
        ));
    }
    let vsum: f64 = v.iter().sum();
    if vsum <= 0.0 {
        return Err(PyValueError::new_err("teleport must have positive mass"));
    }
    let v: Vec<f64> = v.iter().map(|&x| x / vsum).collect();
    let x = py.detach(|| ppr_power_iterate(ip, ix, &v, alpha, tol, max_iter as usize));
    Ok(ndarray::Array1::from(x).into_pyarray(py))
}

/// Batched one-hot PPR (S1, 2026-11-14) — rayon across seeds, one
/// solve per row, zero-copy (k, n) float64 return.
///
/// Row i is the `personalized_pagerank` solution for `seeds[i]` — the
/// multi-seed sweep primitive (sector scans, population batches,
/// ppr_results refresh). Result is k*n float64s; the call refuses
/// k*n > 2^28 (~2 GB) — chunk population sweeps (n ~20k → k <= 8,000
/// per call) instead of raising the cap.
#[pyfunction]
#[allow(clippy::type_complexity)]
fn personalized_pagerank_batch<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
    seeds: PyReadonlyArray1<'py, i32>,
    alpha: f64,
    tol: f64,
    max_iter: i64,
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    let srcs: &[i32] = seeds.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&x| x < 0 || x as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    if !(alpha > 0.0 && alpha < 1.0) {
        return Err(PyValueError::new_err("alpha must be in (0, 1)"));
    }
    if tol <= 0.0 {
        return Err(PyValueError::new_err("tol must be positive"));
    }
    if max_iter < 1 {
        return Err(PyValueError::new_err("max_iter must be >= 1"));
    }
    if srcs.iter().any(|&s| s < 0 || s as usize >= n) {
        return Err(PyValueError::new_err("seeds out of range"));
    }
    let k = srcs.len();
    if (k as u64) * (n as u64) > 1u64 << 28 {
        return Err(PyValueError::new_err(format!(
            "result {k}x{n} exceeds the 2^28-cell cap — chunk the batch"
        )));
    }
    let rows: Vec<Vec<f64>> = py.detach(|| {
        srcs.par_iter()
            .map(|&s| ppr_power_iterate_seed(ip, ix, s as usize, alpha, tol, max_iter as usize))
            .collect()
    });
    let flat: Vec<f64> = rows.into_iter().flatten().collect();
    let arr = ndarray::Array2::from_shape_vec((k, n), flat)
        .map_err(|e| PyValueError::new_err(format!("batch shape: {e}")))?;
    Ok(PyArray2::from_owned_array(py, arr))
}

/// One-hot convenience over the vector core.
fn ppr_power_iterate_seed(
    ip: &[i32],
    ix: &[i32],
    seed: usize,
    alpha: f64,
    tol: f64,
    max_iter: usize,
) -> Vec<f64> {
    let n = ip.len() - 1;
    let mut v = vec![0.0f64; n];
    v[seed] = 1.0;
    ppr_power_iterate(ip, ix, &v, alpha, tol, max_iter)
}

/// Power-iteration core over an arbitrary teleport vector (pure,
/// testable without Python). `v` must be normalized (sum 1).
fn ppr_power_iterate(
    ip: &[i32],
    ix: &[i32],
    v: &[f64],
    alpha: f64,
    tol: f64,
    max_iter: usize,
) -> Vec<f64> {
    let n = ip.len() - 1;
    // Row degrees of the binary adjacency; dangling rows redistribute.
    let deg: Vec<f64> = ip.windows(2).map(|w| (w[1] - w[0]) as f64).collect();
    let mut x = v.to_vec();
    let mut nx = vec![0.0f64; n];
    let base = 1.0 - alpha;
    for _ in 0..max_iter {
        nx.fill(0.0);
        // nx = P^T x, scattered row-wise; dangling row i sends x[i]
        // back ∝ the teleport vector (P[i, :] = v) — reduces to the
        // one-hot lane's P[i, seed] = 1 when v is one-hot.
        let mut dangling_mass = 0.0f64;
        for (i, &xi) in x.iter().enumerate() {
            if xi == 0.0 {
                continue;
            }
            if deg[i] == 0.0 {
                dangling_mass += xi;
            } else {
                let inv = 1.0 / deg[i];
                let lo = ip[i] as usize;
                let hi = ip[i + 1] as usize;
                for &j in &ix[lo..hi] {
                    nx[j as usize] += xi * inv;
                }
            }
        }
        if dangling_mass > 0.0 {
            for (j, slot) in nx.iter_mut().enumerate() {
                *slot += dangling_mass * v[j];
            }
        }
        let mut delta = 0.0;
        for (j, slot) in x.iter_mut().enumerate() {
            let nv = alpha * nx[j] + base * v[j];
            delta += (nv - *slot).abs();
            *slot = nv;
        }
        if delta < tol {
            break;
        }
    }
    x
}

// --------------------------------------------------------------------- //
// L1 fold kernels: Brandes core fold + core BFS distances
// (proposals rust_betweenness_kernel / rust_closeness_kernel, executed
// 2026-11-18). Both port ONLY the BFS-shaped work; the Python lanes keep
// the peel/tree decomposition and every score assembly.
// --------------------------------------------------------------------- //

/// Sources per rayon task for the L1 kernels; each task owns a scratch
/// struct plus three local accumulators (n f64 each).
const SOURCE_CHUNK: usize = 256;

/// The four read-only inputs every Brandes fold call needs: the full
/// projection CSR (both directions), the per-attachment tree weight
/// `kw`, and the 0/1 core mask that restricts the BFS to the 2-core.
struct BrandesGraph<'a> {
    indptr: &'a [i32],
    indices: &'a [i32],
    kw: &'a [f64],
    is_core: &'a [u8],
}

/// Per-source scratch for [`brandes_source_fold`]: path counts, the two
/// dependency accumulators, the frontier buffers and the reverse-level
/// edge lists. Everything a source touches is recorded in `touched`, so
/// the reset is O(visited) instead of O(n) — a 22k-node `fill` per
/// source would dominate the fold at 11k sources.
struct BrandesScratch {
    dist: Vec<i32>,
    sigma: Vec<f64>,
    delta_u: Vec<f64>,
    delta_k: Vec<f64>,
    touched: Vec<u32>,
    frontier: Vec<u32>,
    next: Vec<u32>,
    edges: Vec<(u32, u32)>,
    level_bounds: Vec<usize>,
}

impl BrandesScratch {
    fn new(n: usize) -> Self {
        Self {
            dist: vec![-1; n],
            sigma: vec![0.0; n],
            delta_u: vec![0.0; n],
            delta_k: vec![0.0; n],
            touched: Vec::with_capacity(n),
            frontier: Vec::with_capacity(n),
            next: Vec::with_capacity(n),
            edges: Vec::new(),
            level_bounds: Vec::new(),
        }
    }

    fn clear(&mut self) {
        for &x in &self.touched {
            let i = x as usize;
            self.dist[i] = -1;
            self.sigma[i] = 0.0;
            self.delta_u[i] = 0.0;
            self.delta_k[i] = 0.0;
        }
        self.touched.clear();
        self.frontier.clear();
        self.next.clear();
        self.edges.clear();
        self.level_bounds.clear();
    }
}

/// One source's two-accumulator Brandes fold over the CORE-INDUCED graph.
///
/// Mirrors `helpers/graph/l1_betweenness.py::_brandes_source` op-for-op
/// (pinned by the parity harness and `tests/properties.rs`): level-
/// synchronous BFS whose expansion keeps only neighbours with
/// `is_core[w] != 0`, `sigma` accumulated in CSR/edge order, then the
/// reverse-level dependency fold for both accumulators. Writes
/// `delta_u`/`delta_k` (zeroed on entry by [`BrandesScratch::clear`]) and
/// returns `kk_world` — the sum of `kw` over reachable attachments other
/// than the source, which the caller weights by `kw[s]` at `v = s`.
///
/// Edge order is load-bearing, not incidental: `np.bincount` sums each
/// bin in input order, so the level edge list is built frontier-order ×
/// CSR-neighbour-order and the dependency fold re-sums each `v` group in
/// that same order. With `force_parallel = false` the results are
/// therefore bit-identical to the numpy lane at `jobs = 1`.
fn brandes_source_fold(g: &BrandesGraph<'_>, s: usize, sc: &mut BrandesScratch) -> f64 {
    let indptr = g.indptr;
    let indices = g.indices;
    let kw = g.kw;
    let is_core = g.is_core;
    sc.clear();
    sc.dist[s] = 0;
    sc.sigma[s] = 1.0;
    sc.touched.push(s as u32);
    sc.frontier.push(s as u32);
    let mut level: i32 = 0;
    loop {
        level += 1;
        let start = sc.edges.len();
        sc.next.clear();
        for &v in &sc.frontier {
            let lo = indptr[v as usize] as usize;
            let hi = indptr[v as usize + 1] as usize;
            for &w in &indices[lo..hi] {
                let wu = w as usize;
                // numpy: keep = is_core[w_arr]; newly = dist[w_arr] < 0,
                // both evaluated before this level's dist update.
                if is_core[wu] != 0 && sc.dist[wu] < 0 {
                    sc.edges.push((v, w as u32));
                    sc.next.push(w as u32);
                }
            }
        }
        if sc.edges.len() == start {
            break;
        }
        // numpy's fresh = np.flatnonzero(...) is ASCENDING node id; the
        // next level's edge order (np.repeat over the frontier) depends
        // on it.
        sc.next.sort_unstable();
        sc.next.dedup();
        for &w in &sc.next {
            let i = w as usize;
            sc.dist[i] = level;
            sc.sigma[i] = 0.0;
            sc.touched.push(w);
        }
        for &(v, w) in &sc.edges[start..] {
            sc.sigma[w as usize] += sc.sigma[v as usize];
        }
        sc.level_bounds.push(sc.edges.len());
        std::mem::swap(&mut sc.frontier, &mut sc.next);
    }
    for li in (0..sc.level_bounds.len()).rev() {
        let lo = if li == 0 { 0 } else { sc.level_bounds[li - 1] };
        let hi = sc.level_bounds[li];
        let edges = &sc.edges[lo..hi];
        let mut i = 0usize;
        while i < edges.len() {
            let v = edges[i].0 as usize;
            let sv = sc.sigma[v];
            let mut su = 0.0f64;
            let mut sk = 0.0f64;
            while i < edges.len() && edges[i].0 as usize == v {
                let w = edges[i].1 as usize;
                let ratio = sv / sc.sigma[w];
                su += ratio * (1.0 + sc.delta_u[w]);
                sk += ratio * (kw[w] + sc.delta_k[w]);
                i += 1;
            }
            sc.delta_u[v] += su;
            sc.delta_k[v] += sk;
        }
    }
    // reach = dist >= 0 minus the source itself; kw is an integer
    // attachment count, so this sum is exact in any order.
    let mut kk_world = 0.0f64;
    for &x in &sc.touched {
        let i = x as usize;
        if i != s {
            kk_world += kw[i];
            if kw[i] > 0.0 {
                sc.delta_k[i] += kw[i];
            }
        }
    }
    kk_world
}

/// Fold one source chunk into the three accumulators (`_brandes_chunk`).
/// Accumulation runs over `touched` only: every untouched `delta_*` slot
/// is exactly 0.0, so `x + 0.0` is a no-op and the per-element order of
/// the (one add per source) accumulation is preserved.
fn brandes_chunk(
    g: &BrandesGraph<'_>,
    sources: &[i32],
    u_out: &mut [f64],
    tu_out: &mut [f64],
    kk_out: &mut [f64],
) {
    let kw = g.kw;
    let n = g.indptr.len() - 1;
    let mut sc = BrandesScratch::new(n);
    for &s in sources {
        let s = s as usize;
        let kk_world = brandes_source_fold(g, s, &mut sc);
        // Standard Brandes never credits the source's own dependency
        // entry; KK's source-side self term comes from kk_world instead.
        sc.delta_u[s] = 0.0;
        sc.delta_k[s] = 0.0;
        for &x in &sc.touched {
            let i = x as usize;
            u_out[i] += sc.delta_u[i];
            tu_out[i] += sc.delta_k[i];
        }
        let k_s = kw[s];
        if k_s > 0.0 {
            for &x in &sc.touched {
                let i = x as usize;
                kk_out[i] += k_s * sc.delta_k[i];
            }
            kk_out[s] += k_s * kk_world;
        }
    }
}

/// Two-accumulator Brandes over every source in `sources`.
///
/// `U`/`TU`/`KK` follow the Python lane's disjoint unordered coverage
/// (U halved and KK halved on assembly; TU not). Sequential below
/// `PARALLEL_THRESHOLD` (or `force_parallel` for tests pinning seq/par
/// parity); otherwise rayon over `SOURCE_CHUNK` source chunks, each with
/// its own scratch + accumulators, summed once in chunk order.
pub fn brandes_fold(
    indptr: &[i32],
    indices: &[i32],
    kw: &[f64],
    is_core: &[u8],
    sources: &[i32],
    force_parallel: bool,
) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let g = BrandesGraph {
        indptr,
        indices,
        kw,
        is_core,
    };
    let n = indptr.len() - 1;
    if !force_parallel && sources.len() < PARALLEL_THRESHOLD {
        let mut u = vec![0.0; n];
        let mut tu = vec![0.0; n];
        let mut kk = vec![0.0; n];
        brandes_chunk(&g, sources, &mut u, &mut tu, &mut kk);
        return (u, tu, kk);
    }
    let parts: Vec<(Vec<f64>, Vec<f64>, Vec<f64>)> = sources
        .chunks(SOURCE_CHUNK)
        .collect::<Vec<_>>()
        .par_iter()
        .map(|chunk| {
            let mut u = vec![0.0; n];
            let mut tu = vec![0.0; n];
            let mut kk = vec![0.0; n];
            brandes_chunk(&g, chunk, &mut u, &mut tu, &mut kk);
            (u, tu, kk)
        })
        .collect();
    let mut u = vec![0.0; n];
    let mut tu = vec![0.0; n];
    let mut kk = vec![0.0; n];
    for (pu, ptu, pkk) in parts {
        for i in 0..n {
            u[i] += pu[i];
            tu[i] += ptu[i];
            kk[i] += pkk[i];
        }
    }
    (u, tu, kk)
}

/// BFS distances from `src` over the core-induced CSR, `-1` unreachable.
/// Mirrors `helpers/graph/l1_closeness.py::core_bfs` (integer distances,
/// so the two lanes agree exactly).
fn core_bfs_into(
    indptr: &[i32],
    indices: &[i32],
    src: usize,
    dist: &mut [i32],
    frontier: &mut Vec<u32>,
    next: &mut Vec<u32>,
) {
    dist.fill(-1);
    dist[src] = 0;
    frontier.clear();
    frontier.push(src as u32);
    let mut level: i32 = 0;
    while !frontier.is_empty() {
        level += 1;
        next.clear();
        for &v in frontier.iter() {
            let lo = indptr[v as usize] as usize;
            let hi = indptr[v as usize + 1] as usize;
            for &w in &indices[lo..hi] {
                let wu = w as usize;
                if dist[wu] < 0 {
                    dist[wu] = level;
                    next.push(w as u32);
                }
            }
        }
        std::mem::swap(frontier, next);
    }
}

/// Batched core BFS distances: one row per source, row width = n.
///
/// Sources are independent → rayon over them (`par_chunks_mut` writes the
/// flat row-major output in place, no per-row Vec to collect).
pub fn core_bfs_distances(indptr: &[i32], indices: &[i32], sources: &[i32]) -> Vec<i32> {
    let n = indptr.len() - 1;
    let mut out = vec![-1i32; sources.len() * n];
    out.par_chunks_mut(n)
        .zip(sources.par_iter())
        .for_each(|(row, &s)| {
            let mut frontier: Vec<u32> = Vec::with_capacity(n);
            let mut next: Vec<u32> = Vec::with_capacity(n);
            core_bfs_into(indptr, indices, s as usize, row, &mut frontier, &mut next);
        });
    out
}

/// Two-accumulator Brandes fold over a batch of core sources.
///
/// `indptr`/`indices` are the FULL projection CSR (both directions) and
/// `is_core` (0/1, one entry per node) restricts the BFS to the 2-core,
/// exactly like the numpy lane. `kw` is the per-attachment tree weight.
/// Returns `(U, TU, KK)`, each `float64[n]`, to be assembled by the
/// caller as `U/2 + TU + KK/2`. `force_parallel=True` runs the rayon lane
/// even for small batches (tests pin seq/par parity).
#[pyfunction]
#[pyo3(signature = (indptr, indices, kw, is_core, sources, force_parallel=false))]
#[allow(clippy::type_complexity)] // 3 numpy arrays — the API shape
fn brandes_fold_sources<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
    kw: PyReadonlyArray1<'py, f64>,
    is_core: PyReadonlyArray1<'py, u8>,
    sources: PyReadonlyArray1<'py, i32>,
    force_parallel: bool,
) -> PyResult<(
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
)> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    let kwv: &[f64] = kw.as_slice()?;
    let icv: &[u8] = is_core.as_slice()?;
    let srcs: &[i32] = sources.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    if kwv.len() != n {
        return Err(PyValueError::new_err("kw must have one entry per node"));
    }
    if icv.len() != n {
        return Err(PyValueError::new_err(
            "is_core must have one entry per node",
        ));
    }
    if srcs.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("sources out of range"));
    }
    let (u, tu, kk) = py.detach(|| brandes_fold(ip, ix, kwv, icv, srcs, force_parallel));
    Ok((
        ndarray::Array1::from(u).into_pyarray(py),
        ndarray::Array1::from(tu).into_pyarray(py),
        ndarray::Array1::from(kk).into_pyarray(py),
    ))
}

/// Batched core-BFS distance rows for a source batch.
///
/// Returns `int32[n_sources, n]` row-major; `-1` marks unreachable.
/// `indptr`/`indices` are the CORE-INDUCED CSR in the ORIGINAL node id
/// space (rows of non-core nodes are empty), which is what
/// `helpers/graph/l1_closeness.py::build_core_adjacency` produces.
/// Distances are integers, so this lane agrees with the numpy one bit for
/// bit; the caller batches sources (e.g. 256/row-chunk) to bound the
/// matrix at 135 MB on the live 11.4k-core / 1.5k-source shape.
#[pyfunction]
fn core_bfs_sources_batch<'py>(
    py: Python<'py>,
    indptr: PyReadonlyArray1<'py, i32>,
    indices: PyReadonlyArray1<'py, i32>,
    sources: PyReadonlyArray1<'py, i32>,
) -> PyResult<Bound<'py, PyArray2<i32>>> {
    let ip: &[i32] = indptr.as_slice()?;
    let ix: &[i32] = indices.as_slice()?;
    let srcs: &[i32] = sources.as_slice()?;
    if ip.is_empty() {
        return Err(PyValueError::new_err(
            "indptr must carry at least the [0] terminator",
        ));
    }
    if ip.windows(2).any(|w| w[0] > w[1]) {
        return Err(PyValueError::new_err("indptr must be non-decreasing"));
    }
    let n = ip.len() - 1;
    if *ip.last().unwrap() as usize != ix.len() {
        return Err(PyValueError::new_err("indptr[-1] must equal len(indices)"));
    }
    if ix.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("indices out of range"));
    }
    if srcs.iter().any(|&v| v < 0 || v as usize >= n) {
        return Err(PyValueError::new_err("sources out of range"));
    }
    let flat = py.detach(|| core_bfs_distances(ip, ix, srcs));
    let arr = ndarray::Array2::from_shape_vec((srcs.len(), n), flat)
        .map_err(|e| PyValueError::new_err(format!("dist shape: {e}")))?;
    Ok(PyArray2::from_owned_array(py, arr))
}

#[pymodule]
fn graph_kernels(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(all_pairs_bfs_stats, m)?)?;
    m.add_function(wrap_pyfunction!(limited_bfs_sources, m)?)?;
    m.add_function(wrap_pyfunction!(personalized_pagerank, m)?)?;
    m.add_function(wrap_pyfunction!(personalized_pagerank_vec, m)?)?;
    m.add_function(wrap_pyfunction!(personalized_pagerank_batch, m)?)?;
    m.add_function(wrap_pyfunction!(brandes_fold_sources, m)?)?;
    m.add_function(wrap_pyfunction!(core_bfs_sources_batch, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{all_pairs_hist, all_pairs_hist_seq, stats_from_hist};

    fn path_csr(n: usize) -> (Vec<i32>, Vec<i32>) {
        // undirected path 0-1-...-(n-1)
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for v in 0..n {
            if v > 0 {
                ix.push((v - 1) as i32);
            }
            if v + 1 < n {
                ix.push((v + 1) as i32);
            }
            ip.push(ix.len() as i32);
        }
        (ip, ix)
    }

    #[test]
    fn path_graph_reaches_distance_m_minus_one() {
        // The histogram-panic regression: n = 65 exceeds the survey
        // draft's 64-slot histogram; sizing is m.max(2) now.
        let (ip, ix) = path_csr(65);
        let hist = all_pairs_hist(&ip, &ix, false);
        let (d, med, ties, total) = stats_from_hist(&hist);
        assert_eq!(d, 64);
        assert_eq!(ties, 2); // the two endpoints
                             // median over the vals>0 multiset (hist[d] = 2*(65-d), total
                             // 4160): order stats 2079/2080 land at d=19 (cum(18)=1998,
                             // cum(19)=2090) — np.median of the same multiset, not the
                             // midpoint of the distance range.
        assert_eq!(med, 19.0);
        assert_eq!(total, 65 * 64);
    }

    #[test]
    fn empty_and_singleton_shapes() {
        // no edges, 5 isolates: total 0 everywhere, diameter 0
        let ip = vec![0i32; 6];
        let hist = all_pairs_hist(&ip, &[], false);
        assert_eq!(stats_from_hist(&hist), (0, 0.0, 0, 0));
        // single node
        let ip = vec![0i32, 0];
        let hist = all_pairs_hist(&ip, &[], false);
        assert_eq!(stats_from_hist(&hist), (0, 0.0, 0, 0));
    }

    #[test]
    fn star_graph_ordered_pair_counts() {
        // center 0, leaves 1..=n-1: hist[1] = 2*(n-1) ordered,
        // hist[2] = (n-1)*(n-2) ordered
        let n = 10usize;
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for l in 1..n {
            ix.push(l as i32);
        }
        ip.push(ix.len() as i32);
        for _ in 1..n {
            ix.push(0);
            ip.push(ix.len() as i32);
        }
        let hist = all_pairs_hist(&ip, &ix, false);
        assert_eq!(hist[1], (2 * (n - 1)) as u64);
        assert_eq!(hist[2], ((n - 1) * (n - 2)) as u64);
        let (d, _med, ties, total) = stats_from_hist(&hist);
        assert_eq!(d, 2);
        assert_eq!(ties as usize, (n - 1) * (n - 2));
        assert_eq!(total as usize, n * (n - 1));
    }

    #[test]
    fn parallel_matches_sequential_on_path() {
        // path length above PARALLEL_THRESHOLD, forced both ways
        let (ip, ix) = path_csr(1_100);
        let seq = all_pairs_hist_seq(&ip, &ix); // forced seq despite m > threshold
        let par = all_pairs_hist(&ip, &ix, true);
        assert_eq!(seq, par);
    }

    /// Independent reference for the trail lane: VecDeque BFS, full dist
    /// array, filter at the end — the scipy dijkstra semantics (limit,
    /// even distances only, entities only, self excluded) re-derived.
    fn ref_limited(
        indptr: &[i32],
        indices: &[i32],
        src: usize,
        n_e: usize,
        limit: usize,
    ) -> (u32, Vec<u64>) {
        let m = indptr.len() - 1;
        let width = limit / 2 + 1;
        let mut dist = vec![-1i32; m];
        let mut q = std::collections::VecDeque::new();
        dist[src] = 0;
        q.push_back(src);
        while let Some(v) = q.pop_front() {
            let d = dist[v];
            if d as usize >= limit {
                continue; // do not expand past the limit boundary
            }
            let lo = indptr[v] as usize;
            let hi = indptr[v + 1] as usize;
            for &w in &indices[lo..hi] {
                let wu = w as usize;
                if dist[wu] == -1 {
                    dist[wu] = d + 1;
                    q.push_back(w as usize);
                }
            }
        }
        let mut row = vec![0u64; width];
        let mut reach = 0u32;
        for (u, &d) in dist.iter().enumerate() {
            if d > 0 && d % 2 == 0 && u < n_e {
                row[d as usize / 2] += 1;
                reach += 1;
            }
        }
        (reach, row)
    }

    /// Entity-hub-entity-hub-...-entity chain: k entities + (k-1) hubs,
    /// hubs numbered after the entities. The classic trail shape.
    fn bipartite_chain(k: usize) -> (Vec<i32>, Vec<i32>, usize) {
        let n_e = k;
        let n = k + (k - 1);
        let mut adj: Vec<Vec<i32>> = vec![Vec::new(); n];
        for i in 0..k - 1 {
            let hub = n_e + i;
            adj[i].push(hub as i32);
            adj[hub].push(i as i32);
            adj[i + 1].push(hub as i32);
            adj[hub].push((i + 1) as i32);
        }
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for a in &adj {
            ix.extend_from_slice(a);
            ip.push(ix.len() as i32);
        }
        (ip, ix, n_e)
    }

    #[test]
    fn trail_hists_match_reference_across_limits() {
        let (ip, ix, n_e) = bipartite_chain(6);
        for limit in [0usize, 1, 2, 3, 4, 5, 10, 40] {
            let width = limit / 2 + 1;
            let sources: Vec<i32> = (0..n_e as i32).collect();
            let (reach, hists) = super::limited_bfs_hists(&ip, &ix, &sources, n_e, limit);
            for (si, &s) in sources.iter().enumerate() {
                let (r_ref, row_ref) = ref_limited(&ip, &ix, s as usize, n_e, limit);
                assert_eq!(reach[si], r_ref, "limit {limit} source {si}");
                let row = &hists[si * width..(si + 1) * width];
                assert_eq!(row, &row_ref[..], "limit {limit} source {si}");
            }
        }
    }

    #[test]
    fn trail_hists_limit_one_and_zero_empty() {
        // limit 1: one expansion to the hub level (odd, uncounted) —
        // parity with scipy limit=1: no even-distance entity inside.
        let (ip, ix, n_e) = bipartite_chain(6);
        let sources: Vec<i32> = vec![0, 3, 5];
        let (reach, hists) = super::limited_bfs_hists(&ip, &ix, &sources, n_e, 1);
        assert!(reach.iter().all(|&r| r == 0));
        assert!(hists.iter().all(|&h| h == 0));
        let (reach0, hists0) = super::limited_bfs_hists(&ip, &ix, &sources, n_e, 0);
        assert!(reach0.iter().all(|&r| r == 0));
        assert_eq!(hists0.len(), sources.len()); // width 1, all zeros
    }
}

#[cfg(test)]
mod ppr_tests {
    use super::ppr_power_iterate_seed;

    fn csr(adj: &[&[usize]]) -> (Vec<i32>, Vec<i32>) {
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for a in adj {
            ix.extend(a.iter().map(|&v| v as i32));
            ip.push(ix.len() as i32);
        }
        (ip, ix)
    }

    #[test]
    fn ppr_sums_to_one_and_converges() {
        // undirected triangle + isolated node 3 (dangling)
        let (ip, ix) = csr(&[&[1, 2], &[0, 2], &[0, 1], &[]]);
        let x = ppr_power_iterate_seed(&ip, &ix, 0, 0.85, 1e-12, 500);
        let s: f64 = x.iter().sum();
        assert!((s - 1.0).abs() < 1e-9, "sum {s}");
        assert!(x.iter().all(|&v| v >= 0.0 && v.is_finite()));
        // dangling node 3's mass teleports back to seed 0: the exact
        // solution has x[3] = 0 (nothing flows INTO a dangling node).
        assert!(x[3].abs() < 1e-11, "dangling score {}", x[3]);
    }

    #[test]
    fn ppr_star_seed_concentrates() {
        // star: hub 0 connected to leaves 1..5, seed at hub
        let (ip, ix) = csr(&[&[1, 2, 3, 4, 5], &[0], &[0], &[0], &[0], &[0]]);
        let x = ppr_power_iterate_seed(&ip, &ix, 0, 0.85, 1e-12, 500);
        // symmetric: leaves all tie
        for w in x[1..6].windows(2) {
            assert!((w[0] - w[1]).abs() < 1e-12, "leaf tie {x:?}");
        }
        assert!(x[0] > x[1], "hub {} > leaf {}", x[0], x[1]);
    }

    #[test]
    fn ppr_two_components_isolated() {
        // edges 0-1 and 2-3, no link between: mass from seed 0 never
        // reaches {2,3}
        let (ip, ix) = csr(&[&[1], &[0], &[3], &[2]]);
        let x = ppr_power_iterate_seed(&ip, &ix, 0, 0.85, 1e-12, 500);
        assert!(x[2].abs() < 1e-11 && x[3].abs() < 1e-11);
        assert!(x[0] + x[1] > 0.999999);
    }
}

#[cfg(test)]
mod ppr_vec_tests {
    use super::{ppr_power_iterate, ppr_power_iterate_seed};

    fn csr(adj: &[&[usize]]) -> (Vec<i32>, Vec<i32>) {
        let mut ip = vec![0i32];
        let mut ix = Vec::new();
        for a in adj {
            ix.extend(a.iter().map(|&v| v as i32));
            ip.push(ix.len() as i32);
        }
        (ip, ix)
    }

    #[test]
    fn ppr_linear_in_teleport_vector_no_dangling() {
        // On a dangling-free graph P is v-independent, so PPR is linear
        // in v: x(v) = sum_i v_i * x(e_i). (With dangling rows the
        // operator itself depends on v — redistribution ∝ v — and
        // linearity does NOT hold; that convention is forced by
        // one-hot scipy parity.)
        let (ip, ix) = csr(&[&[1, 2], &[0, 3], &[0, 3], &[1, 2]]);
        let n = ip.len() - 1;
        let ones: Vec<Vec<f64>> = (0..n)
            .map(|s| ppr_power_iterate_seed(&ip, &ix, s, 0.85, 1e-13, 2000))
            .collect();
        let w = [0.30, 0.0, 0.45, 0.25];
        let mut v = vec![0.0; n];
        v.copy_from_slice(&w);
        let x = ppr_power_iterate(&ip, &ix, &v, 0.85, 1e-13, 2000);
        for j in 0..n {
            let want: f64 = w.iter().zip(&ones).map(|(&wi, o)| wi * o[j]).sum();
            assert!(
                (x[j] - want).abs() < 1e-9,
                "node {j}: vec {} vs linear mix {}",
                x[j],
                want
            );
        }
    }

    #[test]
    fn ppr_dangling_set_teleport_splits_mass() {
        // Star 0 -> {1, 2}; 1 and 2 dangle, returning mass ∝ v.
        // Flipping the teleport order must flip x[0]/x[1] dominance —
        // the observable of ∝-v redistribution (uniform redistribution
        // would break one-hot scipy parity, so it is not an option).
        let (ip, ix) = csr(&[&[1, 2], &[], &[]]);
        let v_a = [0.6, 0.4, 0.0];
        let v_b = [0.4, 0.6, 0.0];
        let xa = ppr_power_iterate(&ip, &ix, &v_a, 0.85, 1e-13, 2000);
        let xb = ppr_power_iterate(&ip, &ix, &v_b, 0.85, 1e-13, 2000);
        for x in [&xa, &xb] {
            assert!((x.iter().sum::<f64>() - 1.0).abs() < 1e-9, "{x:?}");
        }
        // Hand-solved fixed point for v_a: a = 0.6/1.51, c = 0.425a
        // (node 0 -> {1,2} even split; 1, 2 dangle back ∝ v).
        assert!((xa[0] - 0.6 / 1.51).abs() < 1e-9, "xa[0]={}", xa[0]);
        assert!(
            (xa[2] - 0.425 * (0.6 / 1.51)).abs() < 1e-9,
            "xa[2]={}",
            xa[2]
        );
        // Scores move with their teleport share.
        assert!(xa[0] > xb[0] && xa[1] < xb[1], "{xa:?} vs {xb:?}");
    }

    #[test]
    fn ppr_one_hot_path_unchanged() {
        // The refactored core must reproduce the original one-hot
        // semantics exactly on a graph with dangling nodes.
        let (ip, ix) = csr(&[&[1, 2], &[0, 2], &[0, 1], &[]]);
        let x = ppr_power_iterate_seed(&ip, &ix, 0, 0.85, 1e-12, 500);
        let s: f64 = x.iter().sum();
        assert!((s - 1.0).abs() < 1e-9);
        assert!(x[3].abs() < 1e-11, "dangling score {}", x[3]);
        assert!(x[0] > x[1], "{x:?}");
        assert!((x[1] - x[2]).abs() < 1e-12, "triangle symmetric {x:?}");
        // one-hot v through the vec core: identical numbers.
        let mut v = vec![0.0; 4];
        v[0] = 1.0;
        let xv = ppr_power_iterate(&ip, &ix, &v, 0.85, 1e-12, 500);
        for j in 0..4 {
            assert!((x[j] - xv[j]).abs() < 1e-14, "node {j}");
        }
    }
}
