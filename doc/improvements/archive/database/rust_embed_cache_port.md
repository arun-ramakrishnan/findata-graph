---
title: "Rust port of embedding-cache SHA-256 + SQLite pipeline"
area: database
status: executed
filed: "2026-10-25"
completed_md: null
executed: "2026-10-25"
archived: "2026-10-25"
---

# Rust port of `helpers/core/embed_cache.py`

## Motivation

The embedding-cache fingerprint pipeline (`helpers/core/embed_cache.py`) is the hottest compute path after embedding generation itself. It runs SHA-256 on every text chunk and SQLite `INSERT OR REPLACE` per row with a per-row `COMMIT` WAL. Python's `hashlib` + `sqlite3` adds per-row Python→C transition overhead.

## Trial results (Rust/embed_cache)

| Size | sha256 Rust+SHA-NI | sha256 Py (OpenSSL) | insert Rust | insert Py | lookup Rust | lookup Py | full-pipeline Rust | full-pipeline Py |
|------|-------------------|---------------------|-------------|-----------|-------------|-----------|--------------------|------------------|
| 256  | 138 MB/s          | 218 MB/s            | 113k r/s    | 29k r/s   | 279k r/s    | 147k r/s  | 179k ops/s         | 53k ops/s        |
| 1,024| 155 MB/s          | 265 MB/s            | 110k r/s    | 34k r/s   | 202k r/s    | 144k r/s  | 183k ops/s         | 63k ops/s        |
| 4,096| 180 MB/s          | 226 MB/s            | 90k r/s     | 27k r/s   | 194k r/s    | 111k r/s  | 131k ops/s         | 51k ops/s        |

- Rust wins **every row except raw SHA-256** (~138–180 MB/s vs Python/OpenSSL ~218–265 MB/s).
- SQLite insert/lookup: Rust ~3–4× faster per row (rusqlite bundled vs stdlib sqlite3).
- Full pipeline: Rust ~2.5–3.4× faster at all sizes. **~3× end-to-end advantage holds** — commit-bound but Rust's driver advantage dominates.

## Why Python wins raw SHA-256

Python's `hashlib` wraps OpenSSL's hardware SHA-NI path. Rust's `sha2` crate (even with `features = ["asm"]` enabling SHA-NI) is software; to match OpenSSL you need the `openssl` Rust crate with a version supporting the `v3_5` feature + `libssl-dev`/`pkg-config` system deps.

## Proposed slices

### S1: Pure-Rust `embed_cache` crate (this trial)
- Replace `helpers/core/embed_cache.py` with a Python-callable Rust module via PyO3/maturin, or a standalone CLI (`Rust/embed_cache`) + subprocess.
- Keep the same cache key `(text_hash, model)`, same schema `embeddings(text_hash, model, dim, vec, updated_at)`.
- API: `cached_embed_batch(texts: list[str], model: str) -> (vectors, stats)` matching Python.

### S2: OpenSSL binding for SHA-256 parity
- Add `openssl` crate with SHA-NI (`features = ["v3_5"]` on a version that supports it).
- Requires `libssl-dev` on build machines (already satisfied on host).
- Benchmark: expect SHA-256 parity, ~3× full-pipeline maintained.

### S3: Production integration
- Maturin wheel build in CI; publish to internal index.
- Switch `helpers/core/local_embedder.py` and `helpers/graph/embeddings.py` to call the Rust path.
- Add `--use-rust-cache` flag for A/B during rollout.
- Drop Python `embed_cache.py` after validation.

## Open questions
- PyO3 vs subprocess boundary — maturin adds `manylinux` wheel complexity; subprocess is simpler for first cut.
- Cache invalidation: `purge_foreign_models` already present; ensure Rust path calls same SQL.

## References
- Trial code: `Rust/embed_cache/`
- Baseline: `Rust/bench/embed_cache_bench.py`
- Full analysis: `doc/local/notes/rust_ports.md`

## Second-session assessment (2026-10-25 — S1 boundary reshape)

The PyO3 S1 as drafted is **blocked at the right place to learn from**: all
8 compilation errors sit on ONE seam — the per-miss **Python embed-fn
callback** (`cached_embed_batch`/`ln_batch` take an embed function; every
cache miss must call back from Rust into Python for the sidecar HTTP
embed). The `Bound<'_, PyDict>` lifetime errors, the inner-field access on
`Bound<RustConnection>`, the `MutexGuard` derefs, and the
`allow_threads`/Ungil failure (`Py<PyAny>` not `Sync` — it IS the callback
handle) are all that seam. Fixing them individually yields a build with
the seam permanent: GIL/lifetime discipline across a per-miss FFI
callback, plus the maturin/wheel/venv tax — for the component that is not
the bottleneck.

**Reshape (amends S1):**

1. **Hits-only seam**: Rust does
   `hash+lookup_batch(texts, model) -> (hit_vectors, miss_indices)`.
   NO callback. Python keeps miss resolution — sidecar HTTP + the
   stamp/prefix contract + per-row-commit crash resilience (contract,
   from the 2026-10-07 OOM lesson). All 8 errors evaporate by design.
2. **Delivery = the already-working trial binary**, subprocess PER BATCH
   (~5 ms startup amortized over hundreds of rows), JSON/npz over stdio.
   No maturin, no wheel, no venv delta — resolves this proposal's own
   open question above in favor of subprocess FOR THIS WORKLOAD.
3. **S3 re-gated on measurement**: instrument one real rebuild leg for
   HTTP-vs-local time split BEFORE any production switchover. Every real
   workload is HTTP-dominated on misses (0.5–5 s/row embeds vs
   20–60 µs/row local) and warm all-hits passes are ms-class at Python's
   53k ops/s — the 3× local win likely lands on <5% of wall. Expected
   outcome: Rust path lives as the bulk-warm tool; `embed_cache.py`
   stays for interactive paths.

The trial crates/benches stand as-is — good work; only the integration
boundary changes.

## 2026-10-25 — reshaped S1 + measured S3, decision recorded

**What landed (S1 reshaped):** `helpers/core/embed_cache.py` now supports an opt-in hits-only Rust lookup behind `FINDATA_RUST_EMBED_CACHE_LOOKUP=1`, with `FINDATA_RUST_EMBED_CACHE_TIMING=1` emitting per-call timing (`backend`, `cache_lookup_seconds`, `miss_embed_callback_seconds`) through the same `cached_embed_batch` stats path. Rust builds `hash+lookup_batch -> (hit_vectors, miss_indices)` via `Rust/embed_cache/src/bin/embed-cache-lookup.rs` (untracked CLI; subprocess per batch ~5 ms startup, amortized over hundreds of rows). No PyO3, no maturin wheel, no callback seam — all 8 original compilation errors evaporate by design. Python keeps miss resolution (sidecar HTTP embed + stamp/prefix contract + per-row-commit crash resilience). See `doc/improvements/proposals/database/rust_embed_cache_port.md:59-93`.

**S3 measurement:** replayed the exact same 295-part convo batch (19 hits / 276 misses / 270 unique misses) against a temporary cache copy with the run’s new keys removed, so Python saw the pre-run cache state. Python lookup = **0.460s**; Rust lookup = **0.0885s** (~5.2× faster, ~0.372s saved). But the real rebuild ran ~128.45s total, dominated by miss embedding (123.68s callback). Lookup saving is only **0.29% of wall time**. Every real workload is HTTP-dominated on misses (0.5–5 s/row embeds vs 20–60 µs/row local); warm all-hits passes are ms-class at Python’s 53k ops/s.

**Decision:** keep **Python as the default**, Rust opt-in for bulk-warm runs. That matches the measurement gate at `doc/improvements/proposals/database/rust_embed_cache_port.md:84-90` ("instrument ... BEFORE any production switchover") and the expected outcome ("Rust path lives as the bulk-warm tool; embed_cache.py stays for interactive paths").

**Not done (still open, intentionally):** full Rust cutover (`S3` production integration slices: maturin wheel build in CI, switch `helpers/core/local_embedder.py`/`helpers/graph/embeddings.py`, `--use-rust-cache` flag, drop Python after validation) and `S2` OpenSSL SHA-NI parity are parked — the local cache lookup is now provably not the bottleneck, so switching default adds complexity for a <1% wall win on this workload.

**S2 SETTLED 2026-10-26 (measured; prerequisites appeared):** `libssl-dev` 3.5.5 + pkg-config present, `openssl` crate 0.10.81 links via openssl-sys. `Rust/embed_cache/src/bin/sha_s2.rs` (reusable `Hasher`, same corpus as `trial.rs`) reaches **parity with Python hashlib at ≥4 KB payloads** (259 vs 266 MB/s ≈ 3%, inside box self-variance) and closes most of the gap at small payloads (203–224 vs 261 MB/s at ~350 B; residual = per-call EVP setup + digest alloc). Premise correction: this host (i5-6500, Skylake) has **no SHA-NI silicon** — `sha2`'s `asm` was on its software fallback all along, and OpenSSL's edge is AVX2 multi-buffer software, so the "hardware SHA-NI" framing in the S2 item was never accurate here. Full table in `Rust/embed_cache/README.md` + `doc/local/notes/rust_ports.md`. The backup-only ruling is unchanged (SHA ~1% of pipeline wall).

**Artifacts:** `helpers/core/embed_cache.py` (timing env + stats path), `Rust/embed_cache/src/bin/embed-cache-lookup.rs` (untracked), timing replay log `/mnt/data/tmp/opencode/rust-convo-fresh-20261025-1148.log:210-222`.

## 2026-10-25 — git search warm-cache trial (final findings)

`make git-fresh APPLY=1 ARGS="--full"` with `FINDATA_RUST_EMBED_CACHE_LOOKUP=1`
ran clean (exit 0, 447/447 indexed, all cache hits). Standalone warm-cache
trial (`/mnt/data/tmp/opencode/git_embed_trial.py`, read-only, no cache
writes) on the same 447 git texts:

| Backend | Hits | Misses | Lookup time | Save |
|---|---|---|---|---|
| Rust (opt-in) | 447 | 0 | 0.1176s | — |
| Python default | 447 | 0 | 0.2586s | 0.141s (2.2×) |

Rust is ~2× faster on lookups but the absolute saving is ~0.14s per full
git rebuild — again because miss embedding (model inference) dominates
wall time, not lookup. Confirms the S3 measurement gate: lookup is not the
bottleneck anywhere at scale.

**Coverage:** All four production embed callers now route through
`cached_embed_batch` (`rebuild_git_search.py:715`, `rebuild_convo_search.py:479`,
`rebuild_note_search.py:912`, `helpers/graph/embeddings.py:351`) so
`FINDATA_RUST_EMBED_CACHE_LOOKUP=1` is uniformly available. The only
remaining holdout is `rebuild_memory_search.py:587` (`CachedEmbed`,
per-text Python lookup) — a 172-row cohort where the refactor pain isn't
worth it; intentionally left as-is.

**Status: DONE.** S1 reshaped (opt-in hits-only Rust lookup), S3 measured
(convo + git warm-cache trials), Python default kept, Rust opt-in
available for bulk-warm runs. Full cutover (S2 OpenSSL SHA-NI, S3 maturin
wheel / default switch / drop Python) remains parked — provably not the
bottleneck on this workload.
