#!/usr/bin/env python3
"""Python baseline for rust_trials Trial A (embedding-cache fingerprint pipeline).

Mirrors helpers/core/embed_cache.py: SHA-256(text) -> cache key (text_hash, model),
SQLite KV (text_hash, model) -> f32 BLOB (384-d = 1536 B), full pipeline:
hash + per-row INSERT OR REPLACE (WAL commit per row like _store_vec) + lookup.

Same synthetic corpus sizes as the Rust trial: 256 / 1024 / 4096 texts.

Usage: .venv/bin/python3 helpers/misc/embed_cache_bench.py
"""

from __future__ import annotations

import hashlib
import sys
import time
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from helpers.core.db import connect

DIM = 384
MODEL = "granite-embedding-97m-r2"


def sha256_hex(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def pack_f32(vec) -> bytes:
    return array("f", vec).tobytes()


def gen_texts(n: int) -> list[str]:
    sectors = ["Agriculture", "Packaging", "Technology", "Finance", "Energy"]
    texts = []
    for i in range(n):
        length = 200 + (i % 600)
        body = "".join(chr(ord("a") + ((i * 31 + j * 7) % 26)) for j in range(length))
        texts.append(f"Company {i:05d}. {sectors[i % len(sectors)]}. {body}")
    return texts


def bench_sha256(texts):
    t0 = time.perf_counter()
    total = 0
    for t in texts:
        _ = sha256_hex(t.encode())
        total += len(t)
    return total, time.perf_counter() - t0


def bench_sqlite_insert(texts):
    with connect(":memory:") as conn:
        conn.execute(
            "CREATE TABLE embed_cache (text_hash TEXT NOT NULL, model TEXT NOT NULL, "
            "embedding BLOB NOT NULL, source TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY (text_hash, model))"
        )
        t0 = time.perf_counter()
        count = 0
        for i, t in enumerate(texts):
            h = sha256_hex(t.encode())
            vec = [(i % 999) * 0.001] * DIM
            conn.execute(
                "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) "
                "VALUES (?, ?, ?, ?)",
                (h, MODEL, pack_f32(vec), "companies"),
            )
            conn.commit()  # per-row commit like _store_vec
            count += 1
        return count, time.perf_counter() - t0


def bench_sqlite_lookup(texts):
    with connect(":memory:") as conn:
        conn.execute(
            "CREATE TABLE embed_cache (text_hash TEXT NOT NULL, model TEXT NOT NULL, "
            "embedding BLOB NOT NULL, source TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY (text_hash, model))"
        )
        for i, t in enumerate(texts):
            h = sha256_hex(t.encode())
            conn.execute(
                "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) "
                "VALUES (?, ?, ?, ?)",
                (h, MODEL, pack_f32([float(i)] * DIM), "companies"),
            )
        t0 = time.perf_counter()
        found = 0
        for t in texts:
            h = sha256_hex(t.encode())
            row = conn.execute(
                "SELECT embedding FROM embed_cache WHERE text_hash=? AND model=?", (h, MODEL)
            ).fetchone()
            if row is not None:
                found += 1
        return found, time.perf_counter() - t0


def bench_full_pipeline(texts):
    with connect(":memory:") as conn:
        conn.execute(
            "CREATE TABLE embed_cache (text_hash TEXT NOT NULL, model TEXT NOT NULL, "
            "embedding BLOB NOT NULL, source TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY (text_hash, model))"
        )
        t0 = time.perf_counter()
        inserted = 0
        found = 0
        for i, t in enumerate(texts):
            h = sha256_hex(t.encode())
            vec = [(i % 997) * 0.002] * DIM
            conn.execute(
                "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) "
                "VALUES (?, ?, ?, ?)",
                (h, MODEL, pack_f32(vec), "companies"),
            )
            conn.commit()
            inserted += 1
            row = conn.execute(
                "SELECT 1 FROM embed_cache WHERE text_hash=? AND model=?", (h, MODEL)
            ).fetchone()
            if row is not None:
                found += 1
        return inserted, found, time.perf_counter() - t0


def main() -> None:
    print("Python baseline — embedding-cache fingerprint pipeline (Trial A)")
    print(f"model={MODEL} dim={DIM} (mirrors local_embedder.py)")
    print()
    for n in (256, 1024, 4096):
        texts = gen_texts(n)
        bytes_total, sha_t = bench_sha256(texts)
        print(f"== n={n} texts (avg {bytes_total // n} B/text)")
        print(
            f"  sha256:        {bytes_total:>8} B in {sha_t:.4}s  "
            f"({bytes_total / sha_t / 1_000_000:.0f} MB/s)"
        )
        ins, ins_t = bench_sqlite_insert(texts)
        print(
            f"  sqlite insert: {ins:>8} rows in {ins_t:.4}s  "
            f"({ins / ins_t:,.0f} rows/s)"
        )
        fnd, look_t = bench_sqlite_lookup(texts)
        print(
            f"  sqlite lookup: {fnd:>8} rows in {look_t:.4}s  "
            f"({fnd / look_t:,.0f} rows/s)"
        )
        ins2, fnd2, pipe_t = bench_full_pipeline(texts)
        print(
            f"  full pipeline: {ins2:>8} insert + {fnd2:>8} lookup in {pipe_t:.4}s  "
            f"({(ins2 + fnd2) / pipe_t:,.0f} ops/s)"
        )
        print()


if __name__ == "__main__":
    main()
