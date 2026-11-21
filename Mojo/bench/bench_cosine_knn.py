#!/usr/bin/env python3
"""Standing cosine-KNN harness for the live note and script mirrors.

This harness measures the same corpus through four independent numeric lanes:

  python  — the app.py pure-Python fallback (dot product and two norms)
  numpy   — pre-normalized-row matvec (the proposed run-index shape)
  rust    — the shipped ``cosine_knn`` extension (rayon lane)
  mojo    — the SIMD whole-corpus scan in ``Mojo/src/bench/bench_cosine.mojo``

The production DuckDB note path is retained as a separate correctness check.
Every source row is decoded: binary embeddings use little-endian float32 BLOBs,
legacy JSON rows are accepted, and a shape mismatch is a hard error.  No row is
silently skipped.  The measured source shape is printed in every table so a
re-embed or dimension drift is visible.

Usage::

    .venv/bin/python3 Mojo/bench/bench_cosine_knn.py --scales 1 --reps 3

Exit 0 when every lane agrees and the production note check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb  # noqa: E402  # after sys.path bootstrap
from helpers.core.db import connect  # noqa: E402
from helpers.core.vec_search import knn_similarities  # noqa: E402

# Built by `make mojo-build` (Makefile.mojo) — the driver does not compile.
_MOJO_BIN = _REPO_ROOT / "Mojo" / "bin" / "bench_cosine"
_SCRIPT_DB = _REPO_ROOT / "memory" / "script_search.duckdb"
_SCRIPT_DB_LEGACY = _REPO_ROOT / "memory" / "script_search.db"
_RESEARCH_DB = _REPO_ROOT / "memory" / "research.db"
_SCRATCH_ROOT = Path(os.environ.get("TMPDIR", "/mnt/data/tmp"))


@dataclass(frozen=True)
class Corpus:
    name: str
    source: str
    keys: list[str]
    vectors: list[np.ndarray]
    matrix: np.ndarray

    @property
    def dims(self) -> int:
        return int(self.matrix.shape[1])

    @property
    def rows(self) -> int:
        return int(self.matrix.shape[0])

    @property
    def shape(self) -> str:
        return f"{self.rows}x{self.dims}"


def _cosine(a: list[float], b: list[float]) -> float:
    # Verbatim math of the app.py fallback (_scored_rows._cosine) — keep
    # in sync if that ever changes; the whole point is measuring IT.
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _scan(vectors: list[np.ndarray], query: list[float]) -> tuple[int, float]:
    best_idx, best_score = -1, -2.0
    for i, vector in enumerate(vectors):
        score = _cosine(query, vector.tolist())
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx, best_score


def _best_of(fn: Any, reps: int) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def _decode_blob(raw: bytes, location: str) -> np.ndarray:
    """Decode a BLOB/bytes embedding (2026-11-20 gate triage: extracted from
    _decode_embedding to keep it under the C901 complexity budget)."""
    if not raw:
        raise ValueError(f"{location}: empty embedding")
    stripped = raw.lstrip()
    if stripped.startswith((b"[", b"{")):
        # Legacy stores may still contain a JSON vector as text/BLOB. A raw
        # f32 blob can also start with either byte, so only use JSON when
        # the complete value actually parses.
        try:
            return np.asarray(json.loads(raw.decode("utf-8")), dtype=np.float32)
        except UnicodeDecodeError, json.JSONDecodeError:
            pass
    if len(raw) % 4 != 0:
        raise ValueError(f"{location}: binary embedding length {len(raw)} is not f32-aligned")
    return np.frombuffer(raw, dtype="<f4")


def _decode_embedding(value: Any, location: str) -> np.ndarray:
    """Decode one stored embedding without hiding malformed input."""
    if isinstance(value, np.ndarray):
        vector = value.astype(np.float32, copy=False)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        vector = _decode_blob(bytes(value), location)
    elif isinstance(value, str):
        vector = np.asarray(json.loads(value), dtype=np.float32)
    else:
        # DuckDB FLOAT[N] values arrive as list/tuple.
        vector = np.asarray(value, dtype=np.float32)

    if vector.ndim != 1:
        raise ValueError(f"{location}: expected a 1-D embedding, got shape {vector.shape}")
    if vector.size == 0:
        raise ValueError(f"{location}: empty embedding")
    return vector


def _decode_rows(rows: list[tuple[Any, Any]], source: str) -> tuple[list[str], list[np.ndarray]]:
    """Decode every row and fail on the first shape mismatch."""
    keys: list[str] = []
    vectors: list[np.ndarray] = []
    dims: int | None = None
    for row_index, (key, raw_embedding) in enumerate(rows):
        location = f"{source} row {row_index}"
        vector = _decode_embedding(raw_embedding, location)
        if dims is None:
            dims = int(vector.size)
        elif vector.size != dims:
            raise ValueError(
                f"{location}: embedding shape {vector.shape[0]} does not match "
                f"the source dimension {dims}"
            )
        keys.append(str(key))
        vectors.append(vector)
    if not vectors:
        raise ValueError(f"{source}: no embedded rows found")
    return keys, vectors


def _load_note_corpus() -> Corpus:
    """Load the BLOB-backed note rows consumed by the app fallback."""
    conn = connect(_RESEARCH_DB, read_only=True)
    try:
        rows = conn.execute(
            "SELECT file_path, embedding FROM note_search ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    keys, vectors = _decode_rows(rows, "note_search")
    return Corpus(
        name="note",
        source="memory/research.db:note_search (BLOB f32)",
        keys=keys,
        vectors=vectors,
        matrix=np.ascontiguousarray(np.stack(vectors), dtype=np.float32),
    )


def _load_script_corpus() -> Corpus:
    """Load the live DuckDB script mirror, with legacy SQLite fallback."""
    if _SCRIPT_DB.exists():
        conn = duckdb.connect(str(_SCRIPT_DB), read_only=True)
        try:
            rows = conn.execute(
                "SELECT rel_path, embedding FROM script_search ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        source = "memory/script_search.duckdb:script_search (BLOB f32)"
    elif _SCRIPT_DB_LEGACY.exists():
        # read_only=True is the mode=ro URI behind the house helper (the legacy
        # store is frozen; a read-write open would create/rewrite it).
        conn = connect(_SCRIPT_DB_LEGACY, read_only=True)
        try:
            rows = conn.execute(
                "SELECT rel_path, embedding FROM script_search ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()
        source = "memory/script_search.db:script_search (legacy SQLite BLOB f32)"
    else:
        raise FileNotFoundError(f"script mirror not found: {_SCRIPT_DB} or {_SCRIPT_DB_LEGACY}")
    keys, vectors = _decode_rows(rows, "script_search")
    return Corpus(
        name="script",
        source=source,
        keys=keys,
        vectors=vectors,
        matrix=np.ascontiguousarray(np.stack(vectors), dtype=np.float32),
    )


def _write_dump(path: Path, matrix: np.ndarray, block_reps: int) -> None:
    matrix.astype("<f4", copy=False).tofile(path)
    if block_reps > 1:
        with path.open("ab") as fh:
            block = matrix.astype("<f4", copy=False).tobytes()
            for _ in range(block_reps - 1):
                fh.write(block)


def _ensure_mojo_bin() -> str | None:
    if _MOJO_BIN.exists():
        return str(_MOJO_BIN)
    print(
        f"WARNING: {_MOJO_BIN} missing — run `make mojo-build`; mojo legs skipped",
        file=sys.stderr,
    )
    return None


def _run_mojo(
    mojo_bin: str, matrix: Path, query: Path, rows: int, dims: int, reps: int
) -> tuple[dict[str, str], float]:
    t0 = time.perf_counter()
    result = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are locally constructed constants (repo-local binary + dump paths)
        [mojo_bin, str(matrix), str(query), str(rows), str(dims), str(reps)],
        capture_output=True,
        text=True,
    )
    wall = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"mojo bench failed: {result.stderr.strip()}")
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out, wall


def _startup_legs(
    mojo_bin: str | None, matrix: Path, query: Path, rows: int, dims: int
) -> list[tuple[str, float]]:
    """Fixed cost per CLI invocation — the real price of Python-side
    maintenance scripts is import tax, not the numeric loops."""

    def wall(cmd: list[str]) -> float:
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            subprocess.run(cmd, capture_output=True)  # noqa: S603  # list-form call; shell=False (default); cmd is [sys.executable | repo-local mojo binary, constants])
            best = min(best, time.perf_counter() - t0)
        return best

    legs = [
        ("python -c pass (interpreter start)", wall([sys.executable, "-c", "pass"])),
        (
            "python -c 'import numpy' (import tax)",
            wall([sys.executable, "-c", "import numpy"]),
        ),
    ]
    if mojo_bin:
        legs.append(
            (
                "mojo binary, full run (start+load+KNN, reps=1)",
                wall([mojo_bin, str(matrix), str(query), str(rows), str(dims), "1"]),
            )
        )
    return legs


def _normalized_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1)
    return matrix / np.maximum(norms, 1e-12)[:, None]


def _scaled_matrix(
    corpus: Corpus, normalized: np.ndarray, scale: int
) -> tuple[np.ndarray, np.ndarray]:
    """scale=1 reuses the corpus as-is; else tile both matrices (f32)."""
    if scale == 1:
        return corpus.matrix, normalized
    return (
        np.ascontiguousarray(np.tile(corpus.matrix, (scale, 1)), dtype=np.float32),
        np.ascontiguousarray(np.tile(normalized, (scale, 1)), dtype=np.float32),
    )


def _bench_scale(
    corpus: Corpus,
    scale: int,
    normalized: np.ndarray,
    reps: int,
    mojo_bin: str | None,
    matrix_path: Path,
    query_path: Path,
    python_idx: int,
    python_score: float,
    query_python: list[float],
    query_norm: np.ndarray,
    query_raw: np.ndarray,
) -> tuple[list[str], bool]:
    """Run and cross-validate one scale leg (2026-11-21 lint-audit split out
    of _bench_corpus to fit the C901 budget). Returns (table row, all-ok)."""
    matrix, normalized_matrix = _scaled_matrix(corpus, normalized, scale)
    rows = matrix.shape[0]
    ok = True

    def python_lane() -> tuple[int, float]:
        return _scan(corpus.vectors * scale, query_python)

    def numpy_lane() -> tuple[int, float]:
        scores = normalized_matrix @ query_norm
        idx = int(np.argmax(scores))
        return idx, float(scores[idx])

    t_python = _best_of(python_lane, reps)
    t_numpy = _best_of(numpy_lane, reps)
    numpy_idx, numpy_score = numpy_lane()

    rust_idx = -1
    rust_score = float("nan")
    t_rust = float("nan")
    try:
        import cosine_knn

        def rust_lane() -> tuple[int, float]:
            hits = cosine_knn.knn(query_raw, matrix, 10)
            return int(hits[0][0]), float(hits[0][1])

        rust_lane()
        t_rust = _best_of(rust_lane, reps)
        rust_idx, rust_score = rust_lane()
    except ImportError:
        print("rust leg unavailable: cosine_knn extension is not installed", file=sys.stderr)

    mojo_idx = -1
    mojo_score = float("nan")
    t_mojo = float("nan")
    mojo_detail = "—"
    if mojo_bin:
        _write_dump(matrix_path, matrix, 1)
        mojo_out, t_mojo = _run_mojo(mojo_bin, matrix_path, query_path, rows, corpus.dims, reps)
        mojo_idx = int(mojo_out["top1_idx"])
        mojo_score = float(mojo_out["top1_score"])
        load_ms = float(mojo_out.get("load_ms", "nan"))
        compute_ms = float(mojo_out.get("compute_ms", "nan"))
        mojo_detail = f"{t_mojo * 1e3:.1f}ms ({compute_ms:.2f}+{load_ms:.1f})"

    expected_idx = python_idx
    for label, idx, score in (
        ("numpy", numpy_idx, numpy_score),
        ("rust", rust_idx, rust_score),
        ("mojo", mojo_idx, mojo_score),
    ):
        if idx < 0:
            continue
        if idx != expected_idx:
            ok = False
            print(
                f"VALIDATION FAIL {corpus.name} scale={scale}: {label} top1 "
                f"({idx}, {score}) != python ({expected_idx}, {python_score})",
                file=sys.stderr,
            )
        elif abs(score - python_score) > 1e-4:
            ok = False
            print(
                f"VALIDATION FAIL {corpus.name} scale={scale}: {label} score "
                f"{score} != python score {python_score}",
                file=sys.stderr,
            )

    row = [
        corpus.name,
        corpus.shape,
        f"x{scale}",
        str(rows),
        f"{t_python * 1e3:.1f}ms",
        f"{t_numpy * 1e3:.3f}ms",
        f"{t_rust * 1e3:.3f}ms" if math.isfinite(t_rust) else "—",
        mojo_detail,
    ]
    return row, ok


def _bench_corpus(
    corpus: Corpus,
    scales: list[int],
    reps: int,
    mojo_bin: str | None,
    scratch: Path,
) -> tuple[list[list[str]], bool]:
    query_raw = corpus.matrix[0].copy()
    query_python = query_raw.tolist()
    query_norm = query_raw / max(float(np.linalg.norm(query_raw)), 1e-12)
    query_norm = np.ascontiguousarray(query_norm, dtype=np.float32)
    normalized = _normalized_rows(corpus.matrix)

    python_idx, python_score = _scan(corpus.vectors, query_python)
    matrix_path = scratch / f"{corpus.name}_matrix.f32"
    query_path = scratch / f"{corpus.name}_query.f32"
    query_norm.astype("<f4", copy=False).tofile(query_path)

    headers = [
        "source",
        "shape",
        "scale",
        "rows",
        "python",
        "numpy",
        "rust",
        "mojo",
    ]
    table_rows: list[list[str]] = []
    ok = True

    for scale in scales:
        row, scale_ok = _bench_scale(
            corpus,
            scale,
            normalized,
            reps,
            mojo_bin,
            matrix_path,
            query_path,
            python_idx,
            python_score,
            query_python,
            query_norm,
            query_raw,
        )
        table_rows.append(row)
        ok = ok and scale_ok

    widths = [max(len(h), *(len(row[i]) for row in table_rows)) for i, h in enumerate(headers)]

    def render(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

    print(f"source: {corpus.source}")
    print(f"corpus: {corpus.shape} ({corpus.dims} dimensions), query = row 0")
    print()
    print(render(headers))
    print("|" + "|".join("-" * (width + 2) for width in widths) + "|")
    for row in table_rows:
        print(render(row))
    print()
    return table_rows, ok


def _check_note_production(corpus: Corpus) -> tuple[bool, float]:
    """Validate the production DuckDB note path against the BLOB source."""
    conn = connect(_RESEARCH_DB, read_only=True)
    try:
        query = corpus.matrix[0].tolist()
        knn_similarities(conn, query, None, corpus.dims)  # warm-up
        t0 = time.perf_counter()
        result = knn_similarities(conn, query, None, corpus.dims)
        elapsed = time.perf_counter() - t0
    finally:
        conn.close()
    if not result:
        return False, elapsed
    best_key, best_score = max(result.items(), key=lambda item: item[1])
    query_key = corpus.keys[0]
    key_ok = best_key == query_key or best_key.startswith(query_key + "#")
    score_ok = abs(best_score - 1.0) <= 1e-4
    if not key_ok:
        print(
            f"VALIDATION FAIL note production top1 key {best_key!r} does not match {query_key!r}",
            file=sys.stderr,
        )
    return key_ok and score_ok, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales", default="1,4,16", help="corpus replication factors (comma list)"
    )
    parser.add_argument("--reps", type=int, default=5, help="best-of reps for the numeric lanes")
    parser.add_argument(
        "--source",
        choices=("note", "script", "both"),
        default="both",
        help="live mirror to exercise (default: both)",
    )
    args = parser.parse_args()
    scales = [int(s) for s in args.scales.split(",")]
    if not scales or any(scale < 1 for scale in scales):
        parser.error("--scales values must be positive integers")
    if args.reps < 1:
        parser.error("--reps must be positive")

    sources = ("note", "script") if args.source == "both" else (args.source,)
    corpora = [
        corpus
        for corpus in (
            _load_note_corpus() if "note" in sources else None,
            _load_script_corpus() if "script" in sources else None,
        )
        if corpus is not None
    ]

    mojo_bin = _ensure_mojo_bin()
    ok = True
    _SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="knn_bench_", dir=_SCRATCH_ROOT) as tmp:
        scratch = Path(tmp)
        for corpus in corpora:
            print()
            _, corpus_ok = _bench_corpus(corpus, scales, args.reps, mojo_bin, scratch)
            ok = ok and corpus_ok

        note_corpus = next((c for c in corpora if c.name == "note"), None)
        if note_corpus is not None:
            production_ok, production_seconds = _check_note_production(note_corpus)
            ok = ok and production_ok
            print(
                f"production note DuckDB check: {production_seconds * 1e3:.1f}ms "
                f"[{'OK' if production_ok else 'FAIL'}]"
            )

        if corpora:
            first = corpora[0]
            matrix_path = scratch / f"{first.name}_matrix.f32"
            query_path = scratch / f"{first.name}_query.f32"
            _write_dump(matrix_path, first.matrix, 1)
            query = np.ascontiguousarray(
                first.matrix[0] / max(float(np.linalg.norm(first.matrix[0])), 1e-12),
                dtype=np.float32,
            )
            query.astype("<f4", copy=False).tofile(query_path)
            print()
            print("Fixed cost per invocation (best of 3):")
            for label, seconds in _startup_legs(
                mojo_bin, matrix_path, query_path, first.rows, first.dims
            ):
                print(f"  {label}: {seconds * 1e3:.0f}ms")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
