#!/usr/bin/env python3
"""Standing harness: Python vs Mojo for the note-embedding cosine-KNN
workload (lives in Mojo/bench/, driven by `make mojo-bench`).

Context (doc/local/mojo_pilot.md): every production numeric path in this
repo is already native (sqlite-vec KNN, DuckDB VSS, onager, llama.cpp,
MuPDF); the only pure-Python vector math left is the app.py hybrid-search
cosine fallback. This bench measures exactly that workload four ways:

  a. py_math    — the fallback math verbatim (dot + two norms per row,
                  generator-expression sums), vectors pre-parsed
  b. py_json    — same + per-row json.loads (the fallback's real cost:
                  embeddings live as JSON text in the note_search table)
  c. sqlite-vec — the production native path (knn_similarities, C KNN
                  over the vec0 mirror in memory/embed_store.db)
  d. mojo_simd  — Mojo/src/bench/bench_cosine.mojo binary: SIMD
                  (hardware-width f32 lanes) whole-corpus scan over a
                  float32 dump of the same corpus

Corpus scales x1/x4/x16 (whole-corpus row replication) project corpus
growth; the sqlite-vec leg runs at x1 only (it queries the live table).

NOT wired into `make perf` — a Mojo toolchain dependency does not
belong in a regression gate.

Usage: python3 Mojo/bench/bench_cosine_knn.py [--scales 1,4,16] [--reps 5]
       (or just: make mojo-bench [MOJO_BENCH_SCALE=1,4,16] [MOJO_BENCH_REPS=5])
Exit 0 when all cross-validations pass, 1 otherwise.
"""
from __future__ import annotations

import array
import argparse
import itertools
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import sqlite3  # noqa: E402  # after sys.path bootstrap (typing only)
from collections.abc import Callable  # noqa: E402

from helpers.core.db import connect  # noqa: E402
from helpers.core.vec_search import knn_similarities  # noqa: E402

DIMS = 384
# Built by `make mojo-build` (Makefile.mojo) — the driver does not compile.
_MOJO_BIN = _REPO_ROOT / "Mojo" / "bin" / "bench_cosine"


def _cosine(a: list[float], b: list[float]) -> float:
    # Verbatim math of the app.py fallback (_scored_rows._cosine) — keep
    # in sync if that ever changes; the whole point is measuring IT.
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _scan(vecs: list[list[float]], q: list[float]) -> tuple[int, float]:
    best_idx, best_score = -1, -2.0
    for i, v in enumerate(vecs):
        score = _cosine(q, v)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx, best_score


def _scan_json(jsons: list[str], q: list[float]) -> tuple[int, float]:
    best_idx, best_score = -1, -2.0
    for i, s in enumerate(jsons):
        score = _cosine(q, json.loads(s))
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx, best_score


def _best_of(fn: Callable[[], object], reps: int) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def _load_corpus(
    conn: sqlite3.Connection,
) -> tuple[list[str], list[str], list[list[float]]]:
    rows = conn.execute(
        "SELECT file_path, embedding FROM note_search ORDER BY rowid"
    ).fetchall()
    file_paths: list[str] = []
    jsons: list[str] = []
    vecs: list[list[float]] = []
    for file_path, embedding in rows:
        if not embedding:
            continue
        vec = json.loads(embedding)
        if len(vec) != DIMS:
            continue
        file_paths.append(str(file_path))
        jsons.append(str(embedding))
        vecs.append(vec)
    return file_paths, jsons, vecs


def _write_dump(path: Path, vecs: list[list[float]], block_reps: int) -> None:
    base = array.array("f", itertools.chain.from_iterable(vecs))
    with open(path, "wb") as fh:
        for _ in range(block_reps):
            base.tofile(fh)


def _ensure_mojo_bin() -> str | None:
    if _MOJO_BIN.exists():
        return str(_MOJO_BIN)
    print(f"WARNING: {_MOJO_BIN} missing — run `make mojo-build`; "
          "mojo legs skipped", file=sys.stderr)
    return None


def _run_mojo(
    mojo_bin: str, matrix: Path, query: Path, rows: int, reps: int
) -> dict[str, str]:
    result = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are locally constructed constants (repo-local binary + dump paths)
        [mojo_bin, str(matrix), str(query), str(rows), str(DIMS), str(reps)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mojo bench failed: {result.stderr.strip()}")
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _startup_legs(mojo_bin: str | None, matrix: Path, query: Path,
                  rows: int) -> list[tuple[str, float]]:
    """Fixed cost per CLI invocation — the real price of Python-side
    maintenance scripts is import tax, not the numeric loops."""
    def wall(cmd: list[str]) -> float:
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            subprocess.run(cmd, capture_output=True)  # noqa: S603  # list-form call; shell=False (default); cmd is [sys.executable | repo-local mojo binary, constants]
            best = min(best, time.perf_counter() - t0)
        return best

    legs = [
        ("python -c pass (interpreter start)", wall([sys.executable, "-c", "pass"])),
        ("python -c 'import pandas' (import tax)", wall([sys.executable, "-c", "import pandas"])),
    ]
    if mojo_bin:
        legs.append((
            "mojo binary, full run (start+load+KNN, reps=1)",
            wall([mojo_bin, str(matrix), str(query), str(rows), str(DIMS), "1"]),
        ))
    return legs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="1,4,16",
                        help="corpus replication factors (comma list)")
    parser.add_argument("--reps", type=int, default=5,
                        help="best-of reps for the Python/sqlite legs")
    args = parser.parse_args()
    scales = [int(s) for s in args.scales.split(",")]

    conn = connect()
    try:
        file_paths, jsons, vecs = _load_corpus(conn)
        # sqlite-vec leg (x1 only): the production native path.
        _ = knn_similarities(conn, vecs[0], None, DIMS)  # warm-up (ATTACH etc.)
        vec_best = _best_of(lambda: knn_similarities(conn, vecs[0], None, DIMS),
                            args.reps)
        vec_knn = knn_similarities(conn, vecs[0], None, DIMS)
    finally:
        conn.close()

    if not vecs:
        print("no embedded notes found in note_search", file=sys.stderr)
        return 1
    q = vecs[0]
    rows_n = len(vecs)

    py_idx, py_score = _scan(vecs, q)

    mojo_bin = _ensure_mojo_bin()
    if mojo_bin is None:
        print("WARNING: mojo toolchain unavailable — mojo legs skipped",
              file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="knn_bench_") as tmp:
        tmp_dir = Path(tmp)
        matrix = tmp_dir / f"matrix_{rows_n}x1.f32"
        query = tmp_dir / f"query_{rows_n}.f32"
        _write_dump(query, [q], 1)

        ok = True
        print(f"corpus: {rows_n} docs x {DIMS} dims (bge-small f32), "
              f"query = doc[0] embedding (top-1 must be itself, score ~1.0)")
        print()
        headers = ["scale", "rows", "py_math", "py_json", "sqlite-vec",
                   "mojo_simd", "mojo vs py_math", "mojo vs py_json"]
        table_rows: list[list[str]] = []
        for scale in scales:
            scaled_vecs = vecs * scale
            scaled_jsons = jsons * scale
            mat_path = tmp_dir / f"matrix_{rows_n}x{scale}.f32"
            _write_dump(mat_path, vecs, scale)

            t_math = _best_of(lambda: _scan(scaled_vecs, q), args.reps)
            t_json = _best_of(lambda: _scan_json(scaled_jsons, q), args.reps)
            t_vec = vec_best if scale == 1 else None

            t_mojo: float | None = None
            if mojo_bin:
                mojo_out = _run_mojo(mojo_bin, mat_path, query, rows_n * scale, 50)
                t_mojo = float(mojo_out["compute_ms"]) / 1e3
                m_idx, m_score = int(mojo_out["top1_idx"]), float(mojo_out["top1_score"])
                if m_idx != py_idx or abs(m_score - py_score) > 1e-4:
                    ok = False
                    print(f"VALIDATION FAIL scale={scale}: mojo top1 ({m_idx}, "
                          f"{m_score}) != python ({py_idx}, {py_score})",
                          file=sys.stderr)

            vec_cell = f"{t_vec * 1e3:.2f}ms" if t_vec is not None else "—"
            mojo_cell = f"{t_mojo * 1e3:.3f}ms" if t_mojo is not None else "—"
            sp_math = f"{t_math / t_mojo:.0f}x" if t_mojo else "—"
            sp_json = f"{t_json / t_mojo:.0f}x" if t_mojo else "—"
            table_rows.append([f"x{scale}", str(rows_n * scale),
                               f"{t_math * 1e3:.2f}ms", f"{t_json * 1e3:.2f}ms",
                               vec_cell, mojo_cell, sp_math, sp_json])

        # Aligned grid: pad every cell to its column's widest entry.
        widths = [max(len(h), *(len(r[i]) for r in table_rows))
                  for i, h in enumerate(headers)]

        def _render(cells: list[str]) -> str:
            return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

        print(_render(headers))
        print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
        for row in table_rows:
            print(_render(row))

        # sqlite-vec correctness: production top-1 must be the query doc itself.
        if vec_knn is None:
            print("NOTE: sqlite-vec unavailable — native leg skipped in validation")
        else:
            vec_top1 = next(iter(vec_knn))
            vec_ok = vec_top1 == file_paths[py_idx]
            ok = ok and vec_ok
            print()
            print(f"validation: python top1 idx={py_idx} score={py_score:.6f} "
                  f"({file_paths[py_idx]})")
            print(f"validation: sqlite-vec top1 = {vec_top1} "
                  f"[{'OK' if vec_ok else 'MISMATCH'}]")
            print(f"validation: mojo top1 matches python at every scale "
                  f"[{'OK' if ok else 'FAIL'}]")

        print()
        print("Fixed cost per invocation (best of 3):")
        for label, seconds in _startup_legs(mojo_bin, matrix, query, rows_n):
            print(f"  {label}: {seconds * 1e3:.0f}ms")

        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
