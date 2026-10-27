//! Divan benchmarks for cosine_knn
//! Run with: cargo bench --bench divan_bench -p cosine_knn

use cosine_knn::{cosine_similarity, topk, topk_parallel, topk_single};
use divan::{black_box, Bencher};

fn setup_matrix(rows: usize, dim: usize) -> (Vec<f32>, Vec<f32>) {
    let matrix: Vec<f32> = (0..rows * dim).map(|i| (i as f32).sin()).collect();
    let query: Vec<f32> = (0..dim).map(|i| (i as f32).cos()).collect();
    (query, matrix)
}

#[divan::bench]
fn bench_production_scale(bencher: Bencher) {
    let (query, matrix) = setup_matrix(717, 512);
    bencher.bench(|| black_box(topk(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_parallel_threshold(bencher: Bencher) {
    let (query, matrix) = setup_matrix(5_000, 512);
    bencher.bench(|| black_box(topk(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_100k(bencher: Bencher) {
    let (query, matrix) = setup_matrix(100_000, 512);
    bencher.bench(|| black_box(topk(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_1m(bencher: Bencher) {
    let (query, matrix) = setup_matrix(1_000_000, 512);
    bencher.bench(|| black_box(topk(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_cosine_similarity(bencher: Bencher) {
    let a: Vec<f32> = (0..512).map(|i| (i as f32).sin()).collect();
    let b: Vec<f32> = (0..512).map(|i| (i as f32).cos()).collect();
    bencher.bench(|| black_box(cosine_similarity(&a, &b)));
}

#[divan::bench]
fn bench_single_threaded_path(bencher: Bencher) {
    let (query, matrix) = setup_matrix(717, 512);
    bencher.bench(|| black_box(topk_single(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_parallel_path(bencher: Bencher) {
    let (query, matrix) = setup_matrix(100_000, 512);
    bencher.bench(|| black_box(topk_parallel(&query, &matrix, 10, 512)));
}

// Compare single vs parallel at same scale
#[divan::bench]
fn bench_1m_single(bencher: Bencher) {
    let (query, matrix) = setup_matrix(1_000_000, 512);
    bencher.bench(|| black_box(topk_single(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_1m_parallel(bencher: Bencher) {
    let (query, matrix) = setup_matrix(1_000_000, 512);
    bencher.bench(|| black_box(topk_parallel(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_100k_single(bencher: Bencher) {
    let (query, matrix) = setup_matrix(100_000, 512);
    bencher.bench(|| black_box(topk_single(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_100k_parallel(bencher: Bencher) {
    let (query, matrix) = setup_matrix(100_000, 512);
    bencher.bench(|| black_box(topk_parallel(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_5k_single(bencher: Bencher) {
    let (query, matrix) = setup_matrix(5_000, 512);
    bencher.bench(|| black_box(topk_single(&query, &matrix, 10, 512)));
}

#[divan::bench]
fn bench_5k_parallel(bencher: Bencher) {
    let (query, matrix) = setup_matrix(5_000, 512);
    bencher.bench(|| black_box(topk_parallel(&query, &matrix, 10, 512)));
}

fn main() {
    divan::main();
}
