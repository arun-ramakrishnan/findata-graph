//! Trial A: embedding-cache fingerprint pipeline.
//! Reproduces helpers/core/embed_cache.py behavior in Rust:
//!
//! - SHA-256(content-hash key) of every text
//! - SQLite KV: (text_hash, model) -> f32 BLOB embedding
//!
//! Benchmarks sha256 + insert-or-replace + lookup throughput vs. a
//! matching Python baseline (run separately). Self-contained: no llama.cpp,
//! no network, deterministic synthetic text corpus.

use rusqlite::{params, Connection, OpenFlags};
use sha2::{Digest, Sha256};
use std::hint::black_box;
use std::time::Instant;

const EMBED_CACHE_DDL: &str =
    "CREATE TABLE IF NOT EXISTS embed_cache (text_hash TEXT NOT NULL, model TEXT NOT NULL, embedding BLOB NOT NULL, source TEXT NOT NULL DEFAULT '', PRIMARY KEY (text_hash, model))";
const DIM: usize = 384;

fn sha256_hex(text: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text);
    let result = hasher.finalize();
    hex::encode(result)
}

fn pack_f32(vec: &[f32]) -> Vec<u8> {
    vec.iter().flat_map(|v| v.to_le_bytes()).collect()
}

fn unpack_f32(bytes: &[u8]) -> Vec<f32> {
    bytes
        .as_chunks::<4>()
        .0
        .iter()
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

/// Generate N synthetic texts resembling company-overview sections
/// (name + sector + ~200-800 char body).
fn gen_texts(n: usize) -> Vec<String> {
    let mut texts = Vec::with_capacity(n);
    let sectors = [
        "Agriculture",
        "Packaging",
        "Technology",
        "Finance",
        "Energy",
    ];
    for i in 0..n {
        let len = 200 + (i % 600);
        let mut body = String::with_capacity(len);
        for j in 0..len {
            body.push((b'a' + ((i * 31 + j * 7) % 26) as u8) as char);
        }
        texts.push(format!(
            "Company {:05}. {}. {}",
            i,
            sectors[i % sectors.len()],
            body
        ));
    }
    texts
}

fn bench_sha256(texts: &[String]) -> (u64, f64) {
    let t0 = Instant::now();
    let mut total_bytes: u64 = 0;
    for t in texts {
        let h = sha256_hex(t.as_bytes());
        black_box(h);
        total_bytes += t.len() as u64;
    }
    let elapsed = t0.elapsed().as_secs_f64();
    (total_bytes, elapsed)
}

fn bench_sqlite_insert(texts: &[String], model: &str) -> (u64, f64) {
    let db = Connection::open_with_flags(
        ":memory:",
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
    )
    .unwrap();
    db.execute_batch(EMBED_CACHE_DDL).unwrap();
    let mut stmt = db.prepare_cached(
        "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
    ).unwrap();

    let t0 = Instant::now();
    let mut inserted: u64 = 0;
    for (i, t) in texts.iter().enumerate() {
        let h = sha256_hex(t.as_bytes());
        let vec: Vec<f32> = (0..DIM).map(|_| (i as f32).sin()).collect();
        let blob = pack_f32(&vec);
        stmt.execute(params![h, model, blob, "companies"]).unwrap();
        inserted += 1;
    }
    let elapsed = t0.elapsed().as_secs_f64();
    (inserted, elapsed)
}

fn bench_sqlite_lookup(texts: &[String], model: &str) -> (u64, f64) {
    let db = Connection::open_with_flags(
        ":memory:",
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
    )
    .unwrap();
    db.execute_batch(EMBED_CACHE_DDL).unwrap();
    // prepopulate
    {
        let mut stmt = db.prepare_cached(
            "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
        ).unwrap();
        for (i, t) in texts.iter().enumerate() {
            let h = sha256_hex(t.as_bytes());
            let vec: Vec<f32> = vec![i as f32; DIM];
            let blob = pack_f32(&vec);
            stmt.execute(params![h, model, blob, "companies"]).unwrap();
        }
    }
    let mut stmt = db
        .prepare_cached("SELECT embedding FROM embed_cache WHERE text_hash = ? AND model = ?")
        .unwrap();

    let t0 = Instant::now();
    let mut found: u64 = 0;
    for t in texts {
        let h = sha256_hex(t.as_bytes());
        let row = stmt.query_row(params![h, model], |r| {
            let blob: Vec<u8> = r.get(0)?;
            Ok(unpack_f32(&blob).len())
        });
        if row.is_ok() {
            found += 1;
        }
    }
    let elapsed = t0.elapsed().as_secs_f64();
    (found, elapsed)
}

fn bench_full_pipeline(texts: &[String], model: &str) -> (u64, u64, f64) {
    let db = Connection::open_with_flags(
        ":memory:",
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
    )
    .unwrap();
    db.execute_batch(EMBED_CACHE_DDL).unwrap();
    let mut insert_stmt = db.prepare_cached(
        "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
    ).unwrap();
    let mut select_stmt = db
        .prepare_cached("SELECT embedding FROM embed_cache WHERE text_hash = ? AND model = ?")
        .unwrap();

    let t0 = Instant::now();
    let mut inserted: u64 = 0;
    let mut found: u64 = 0;
    for (i, t) in texts.iter().enumerate() {
        let h = sha256_hex(t.as_bytes());
        let vec: Vec<f32> = (0..DIM).map(|_| (i as f32).cos()).collect();
        insert_stmt
            .execute(params![h, model, pack_f32(&vec), "companies"])
            .unwrap();
        inserted += 1;
        // lookup right after insert (mirrors CachedEmbed miss then store)
        let row = select_stmt.query_row(params![h, model], |r| {
            let _: Vec<u8> = r.get(0)?;
            Ok(())
        });
        if row.is_ok() {
            found += 1;
        }
    }
    let elapsed = t0.elapsed().as_secs_f64();
    (inserted, found, elapsed)
}

fn main() {
    println!("Trial A — embedding-cache fingerprint pipeline (Rust)");
    println!("model=granite-embedding-97m-r2 dim={DIM} (mirrors local_embedder.py)");
    println!("sha2 crate with \"asm\" feature: uses SHA-NI on x86_64 CPUs that support it,");
    println!("software fallback otherwise. See `openssl speed sha256` below for the");
    println!("OpenSSL (hardware-accelerated) reference.");
    println!();

    // ---- OpenSSL reference: run `openssl speed sha256` once ----
    println!("--- OpenSSL reference (openssl speed sha256) ---");
    let _ = std::process::Command::new("openssl")
        .arg("speed")
        .arg("sha256")
        .spawn()
        .map(|mut child| child.wait());
    println!();

    let sizes = [256usize, 1024, 4096];
    for &n in &sizes {
        let texts = gen_texts(n);
        let model = "granite-embedding-97m-r2";

        let (bytes, sha_time) = bench_sha256(&texts);
        println!("== n={n} texts (avg {} B/text)", bytes / n as u64);
        println!(
            "  sha256:        {:>8} B in {:.4}s  ({:.0} MB/s)",
            bytes,
            sha_time,
            bytes as f64 / sha_time / 1_000_000.0
        );

        let (ins, ins_time) = bench_sqlite_insert(&texts, model);
        println!(
            "  sqlite insert: {:>8} rows in {:.4}s  ({:.0} rows/s)",
            ins,
            ins_time,
            ins as f64 / ins_time
        );

        let (fnd, look_time) = bench_sqlite_lookup(&texts, model);
        println!(
            "  sqlite lookup: {:>8} rows in {:.4}s  ({:.0} rows/s)",
            fnd,
            look_time,
            fnd as f64 / look_time
        );

        let (ins2, fnd2, pipe_time) = bench_full_pipeline(&texts, model);
        println!(
            "  full pipeline: {:>8} insert + {:>8} lookup in {:.4}s  ({:.0} ops/s)",
            ins2,
            fnd2,
            pipe_time,
            (ins2 + fnd2) as f64 / pipe_time
        );
        println!();
    }

    println!("NOTE: Run the Python baseline (Rust/bench/embed_cache_bench.py)");
    println!("alongside) with the same sizes and compare ops/s.");
}
