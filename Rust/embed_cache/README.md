# Trial A — embedding-cache fingerprint pipeline (Rust vs Python)

Reproduces `helpers/core/embed_cache.py` behavior:
- SHA-256(text) → cache key `(text_hash, model)`
- SQLite KV: `(text_hash, model) → f32 BLOB` (384-d = 1536 B)
- Full pipeline: hash + insert-or-replace + lookup per text

Rust build uses `sha2` crate with `features = ["asm"]` → SHA-NI on x86_64 CPUs that support it, software fallback otherwise.

## Results

| Size | sha256 Rust+SHA-NI | sha256 Py (OpenSSL) | insert Rust | insert Py | lookup Rust | lookup Py | full-pipeline Rust | full-pipeline Py |
|------|-------------------|---------------------|-------------|-----------|-------------|-----------|--------------------|------------------|
| 256 | 138 MB/s | 218 MB/s | 113k r/s | 29k r/s | 279k r/s | 147k r/s | 179k ops/s | 53k ops/s |
| 1,024 | 155 MB/s | 265 MB/s | 110k r/s | 34k r/s | 202k r/s | 144k r/s | 183k ops/s | 63k ops/s |
| 4,096 | 180 MB/s | 226 MB/s | 90k r/s | 27k r/s | 194k r/s | 111k r/s | 131k ops/s | 51k ops/s |

OpenSSL reference (`openssl speed sha256`, OpenSSL 3.5.5): ~392 MB/s at 8-KB blocks, ~354 MB/s at 1-KB blocks. Python's `hashlib` wraps OpenSSL; Rust's `sha2` crate (even with SHA-NI asm) is a software implementation, so OpenSSL leads on raw hashing.

## Verdict (corrected)

**Rust wins every row except raw SHA-256.** Full pipeline: Rust ~2.5–3.4× faster than Python at all sizes. The per-row `COMMIT` WAL pattern dominates at scale, but Rust's rusqlite insert/lookup is ~3–4× faster per row, so the pipeline advantage holds.

**SHA-256 row:** Python/OpenSSL still leads (~226–265 MB/s vs Rust ~138–180 MB/s). To close this gap:

- Enable `sha2` crate's `asm` feature (already done — uses SHA-NI on supported CPUs; software fallback otherwise). This raised Rust from ~128→180 MB/s but doesn't match OpenSSL.
- For full hardware acceleration, bind directly to OpenSSL via the `openssl` Rust crate (requires `libssl-dev` / pkg-config). The crate version `v3_5` feature is unavailable in this sandbox's dependency set; install `libssl-dev` and upgrade the `openssl` crate version to close the SHA-256 gap.

**Bottom line:** A Rust port of `embed_cache.py` **is worthwhile** — ~3× faster end-to-end on the full pipeline even without beating Python on raw SHA-256. If you need to win SHA-256 too, add the OpenSSL binding as a second step.

## Files

- `rust_trials/embed_cache/Cargo.toml` — sha2 0.10 (with `asm`), rusqlite 0.33 (bundled), hex 0.4
- `rust_trials/embed_cache/src/bin/trial.rs` — `bench_sha256`, `bench_sqlite_insert`, `bench_sqlite_lookup`, `bench_full_pipeline`; prints `openssl speed sha256` reference at startup
- `helpers/misc/embed_cache_bench.py` — Python baseline (same corpus sizes)
- `rust_trials/embed_cache/README.md` — this file

Run: `cd Rust && cargo run --release -p embed_cache`
Baseline: `.venv/bin/python3 Rust/bench/embed_cache_bench.py`

## S1 — opt-in hits-only batch lookup

`embed-cache-lookup` is a JSON-stdio CLI for the shared `embed_cache` table.
It hashes each input, looks up `(text_hash, model)` read-only, and returns
`hit_vectors` with input indices plus `miss_indices`. It reads both the current
little-endian f32 BLOB format and legacy JSON text vectors; malformed rows are
reported as misses. It does not embed or write cache rows.

Build with `cd Rust && cargo build --release -p embed_cache --bin embed-cache-lookup`.
The Python batch cache uses it only when
`FINDATA_RUST_EMBED_CACHE_LOOKUP=1`; otherwise the established Python lookup is
unchanged. The adapter auto-detects the release/debug binary under
`Rust/target/`, or accepts an explicit executable path through
`FINDATA_RUST_EMBED_CACHE_BIN`. If the CLI fails, Python falls back to its
existing cache/miss path. File-backed `vecdb` stores are required; in-memory
stores always use Python.

## S2 — openssl-crate parity trial (2026-10-26)

Settles the parked S2 item above. Prerequisites now satisfied on this host
(`libssl-dev` 3.5.5, pkg-config; `openssl` crate 0.10.81). Same corpus and
harness as `trial.rs`, hashed with the reusable `Hasher` (new/update/finish —
mirrors hashlib's per-call churn), timed the same minute as a Python hashlib
best-of run:

| n texts | sha2+asm | openssl one-shot | openssl `Hasher` | Python hashlib |
|---------|----------|------------------|------------------|----------------|
| 256 | 168–174 MB/s | 43–50 MB/s | 203–224 MB/s | **261 MB/s** |
| 1,024 | 186 MB/s | 212 MB/s | 233 MB/s | **267 MB/s** |
| 4,096 | 180 MB/s | 236 MB/s | 259 MB/s | **266 MB/s** |

Premise correction: this host (i5-6500, Skylake) has **no SHA-NI**
(`grep sha_ni /proc/cpuinfo` empty) — `sha2`'s `asm` ran its software
fallback all along, and OpenSSL's edge is **AVX2 multi-buffer software**
(`openssl speed sha256`: 393 MB/s at 8-KB blocks), not silicon.

**Verdict:** parity with hashlib at ≥4 KB payloads (≈3%, inside box
self-variance); small-payload residual (~20% at ~350 B) is per-call EVP
setup + digest alloc, <1% of pipeline wall. embed_cache stays
backup-only — raw SHA-256 was never the bottleneck — but the S2 gap is
now measured-settled instead of parked on prerequisites.

Build: `cd Rust && cargo build --release -p embed_cache --bin sha_s2 && ./target/release/sha_s2`
