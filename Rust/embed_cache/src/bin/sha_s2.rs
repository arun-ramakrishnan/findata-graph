//! Trial A / S2 — OpenSSL SHA-NI parity for the embed-cache fingerprint path.
//!
//! Settles the S2 open item parked in the embed_cache trial (2026-10-25):
//! raw SHA-256 was the one row Rust lost (sha2 0.10 + `asm` = 138–180 MB/s
//! vs Python hashlib / OpenSSL ~218–265 MB/s). This bin adds the `openssl`
//! crate — bound to the system libssl (3.5.5, SHA-NI silicon) via
//! pkg-config — and benches it over the SAME deterministic corpus harness
//! as `trial.rs`, so rows are directly comparable to the recorded table.

use openssl::hash::{hash, Hasher, MessageDigest};
use sha2::{Digest, Sha256};
use std::hint::black_box;
use std::time::Instant;

/// Trial A fingerprint path: SHA-256 + hex (what embed_cache stores).
fn sha256_hex_sha2(text: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text);
    hex::encode(hasher.finalize())
}

fn sha256_hex_openssl(text: &[u8]) -> String {
    let digest = hash(MessageDigest::sha256(), text).unwrap();
    hex::encode(digest)
}

/// Reusable-Hasher variant: new + update + finish (matches hashlib object churn).
fn sha256_hex_openssl_hasher(text: &[u8]) -> String {
    let mut h = Hasher::new(MessageDigest::sha256()).unwrap();
    h.update(text).unwrap();
    hex::encode(h.finish().unwrap())
}

/// Same generator as trial.rs::gen_texts — deterministic, avg ~500 B/text.
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

fn bench_hex_digest(texts: &[String], f: &dyn Fn(&[u8]) -> String) -> (u64, f64) {
    let t0 = Instant::now();
    let mut total_bytes: u64 = 0;
    for t in texts {
        let h = f(t.as_bytes());
        black_box(h);
        total_bytes += t.len() as u64;
    }
    let elapsed = t0.elapsed().as_secs_f64();
    (total_bytes, elapsed)
}

fn main() {
    println!(
        "Trial A/S2 — raw SHA-256 parity: sha2(asm) vs openssl crate (system {})",
        openssl::version::version()
    );
    println!("corpus = trial.rs gen_texts (deterministic, hex digest incl.)");
    println!();

    println!("--- OpenSSL reference (openssl speed sha256) ---");
    let _ = std::process::Command::new("openssl")
        .arg("speed")
        .arg("sha256")
        .spawn()
        .map(|mut child| child.wait());
    println!();

    for &n in &[256usize, 1024, 4096] {
        let texts = gen_texts(n);
        println!("== n={n} texts");
        for (name, f) in [
            (
                "sha256 sha2+asm   ",
                &sha256_hex_sha2 as &dyn Fn(&[u8]) -> String,
            ),
            ("sha256 openssl    ", &sha256_hex_openssl),
            ("sha256 ossl-hasher", &sha256_hex_openssl_hasher),
        ] {
            let (bytes, secs) = bench_hex_digest(&texts, f);
            println!(
                "  {name}: {:>9} B in {:.4}s  ({:.0} MB/s)",
                bytes,
                secs,
                bytes as f64 / secs / 1_000_000.0
            );
        }
    }
}
