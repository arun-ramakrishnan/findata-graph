#!/usr/bin/env python3
"""Standing Mojo bench harness — runs every Mojo benchmark leg and appends
the results to ``Mojo/bench/bench_report.txt`` (mirrors the perf_report.txt
convention of tests/run_perf_benchmarks.py). Invoked by ``make mojo-bench``
(which first builds every Mojo/bin binary via mojo-build).

Legs (each a subprocess with its own time budget; ``--leg`` to filter):

  cosine-knn     Mojo/bench/bench_cosine_knn.py — 4-way comparison table
                 (py_math / py_json / sqlite-vec / mojo_simd) + cross-
                 validations, at corpus scales xN (MOJO_BENCH_SCALE/REPS)
  analyzer       Mojo/bin/analyzer — compute-tier table (scalar vs SIMD,
                 1M samples; GPU tier compile-eliminated off-GPU hosts)
  pool-4x        Mojo/bin/bench_pool x4 workers in parallel — process-pool
                 scaling of the same SIMD kernel (synthetic 1500x384
                 matrix, deterministic seed, no DB dependency)
  regex-bridge   Mojo/bin/mojo_regex_probe — Python `regex` interop probe
                 (needs .venv on PATH for the bridge; ~0.5s)
  yaml-corpus    Mojo/bin/corpus_sweep yaml — vendored mojo-yaml parses
                 every findata note frontmatter (regenerates
                 /tmp/note_paths.txt first; expects FAIL: 0; pure Mojo,
                 no bridge)
  db-access      Mojo/bin/db_access_probe — SQLite (FTS5 + relational,
                 research.db) and DuckDB (graph.duckdb) from Mojo via the
                 Python drivers; rows consumed Mojo-side (checksum parity
                 + time ratio vs native, ~10 s)
  db-integrity   Mojo/bin/integrity_check — the Mojo PORT of
                 database_integrity_check.py (golden parity + section
                 timings vs the python original, ~5 s)
  graph-algos    Mojo/bin/graph_algos_probe — the make graph-algos
                 surface via the ORIGINAL python modules (Onager DuckDB
                 extension table functions + the repo's full FTS5
                 surface: note_search/doc_search/script_search + vec0
                 KNN). SQL executed Mojo-side, checksum + canonical
                 parity GATED (any mismatch fails the leg; ~30 s)
  regex-corpus   Mojo/bin/corpus_sweep regex — every findall battery
                 pattern over every note BODY, Mojo-bridge-driven vs
                 native Python (PARITY on match count; ~15 s both sides)

Usage: .venv/bin/python3 Mojo/bench/run_bench.py [--scales 1,4,16]
          [--reps 3] [--leg NAME ...] [--list] [--keep-going]
Exit 0 when every leg passes, 1 otherwise. NOT wired into `make perf` —
a Mojo toolchain dependency does not belong in a regression gate.

Perf gating (2026-08-30, mirrors tests/run_perf_benchmarks.py): each leg
carries a `gate` — a measured wall-clock ceiling checked SEPARATELY from
rc — and a `budget`, which stays a pure kill-timeout. A leg that finishes
rc-clean but over its gate reports OVER_BUDGET and fails the harness, so
a 3x slowdown passes neither the table nor `make mojo-bench`. Gates are
steady-state numbers calibrated from bench_report.txt observations on the
dev host with ~2x headroom; budget remains the generous safety net
(parities/parity-gates may be slower on a loaded box without tripping
rc, but the OVER_BUDGET row will say so).
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN = REPO_ROOT / "Mojo" / "bin"
REPORT = REPO_ROOT / "Mojo" / "bench" / "bench_report.txt"
PATHS_FILE = Path("/tmp/note_paths.txt")  # noqa: S108


def _loadavg() -> str:
    try:
        raw = Path("/proc/loadavg").read_text().split()
        return raw[0]
    except OSError:
        return "?"


def _run(
    label: str, cmd: list[str], budget: float, env: dict | None = None
) -> tuple[float, bool, str]:
    """Run one subprocess leg; return (seconds, ok, full output).

    ENTIRE stdout+stderr, no truncation: leg tables live at the TOP of
    e.g. bench_cosine_knn.py's output — the report must keep everything.
    """
    t0 = time.perf_counter()
    # env= MERGES onto os.environ (replacing it wholesale would strip the
    # bridge's libpython discovery paths)
    run_env = None
    if env:
        run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(  # noqa: S603  # list-form, repo-local constants only
            cmd,
            capture_output=True,
            text=True,
            timeout=budget,
            cwd=REPO_ROOT,
            env=run_env,
            check=False,
        )
        dt = time.perf_counter() - t0
        out = (proc.stdout + "\n" + proc.stderr).strip()
        return dt, proc.returncode == 0, (out or "(no output)")
    except subprocess.TimeoutExpired:
        dt = time.perf_counter() - t0
        return dt, False, f"TIMEOUT after {budget:.0f}s"


def _dump_matrix(path: Path, rows: int, dims: int) -> None:
    """Deterministic synthetic corpus (LCG); no DB dependency."""
    state = 0x2545F491
    buf = bytearray()
    for _ in range(rows * dims):
        state = (state * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
        buf += struct.pack("<f", ((state >> 33) / 2**31 - 1.0) * 0.5 + 0.5)
    path.write_bytes(buf)


def _leg_pool() -> tuple[float, bool, str]:
    """Spawn 4 bench_pool workers over a shared synthetic matrix."""
    budget = 90.0
    t0 = time.perf_counter()
    rows, dims, nqueries, nworkers, reps = 1500, 384, 64, 4, 3
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as fh:
        mat = Path(fh.name)
    try:
        _dump_matrix(mat, rows, dims)
        cmds = [
            [
                str(BIN / "bench_pool"),
                str(mat),
                str(rows),
                str(dims),
                str(nqueries),
                str(w),
                str(nworkers),
                str(reps),
            ]
            for w in range(nworkers)
        ]
        with ThreadPoolExecutor(max_workers=nworkers) as ex:
            outs = list(
                ex.map(
                    lambda c: subprocess.run(  # noqa: S603
                        c, capture_output=True, text=True, timeout=budget, check=False
                    ),
                    cmds,
                )
            )
        dt = time.perf_counter() - t0
        ok = all(p.returncode == 0 for p in outs)
        lines = []
        for idx, p in enumerate(outs):
            body = (p.stdout + p.stderr).strip()
            lines.append(f"worker {idx}: {body if body else f'rc={p.returncode}'}")
        return dt, ok, "\n".join(lines) + f"\n(wall for {nworkers} parallel workers)"
    except subprocess.TimeoutExpired:
        return time.perf_counter() - t0, False, f"TIMEOUT after {budget:.0f}s"
    finally:
        mat.unlink(missing_ok=True)


def _regen_paths() -> str | None:
    """Refresh /tmp/note_paths.txt (all findata docs); error text or None."""
    regen = subprocess.run(  # noqa: S603  # sys.executable + repo-local import
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.'); "
            "from helpers.maintenance.rebuild_note_search import _iter_findata_docs; "
            "print('\\n'.join(str(p) for _t, p, _r in _iter_findata_docs()))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        check=False,
    )
    if regen.returncode != 0:
        return (regen.stdout + "\n" + regen.stderr).strip() or "(regen produced no output)"
    PATHS_FILE.write_text(regen.stdout)
    return None


def _corpus_sweep(phase: str, budget: float) -> tuple[float, bool, str]:
    """One phase of Mojo/bin/corpus_sweep (paths file regenerated first)."""
    err = _regen_paths()
    if err:
        return 0.0, False, err
    return _run(f"corpus:{phase}", [str(BIN / "corpus_sweep"), phase], budget)


def _leg_yaml() -> tuple[float, bool, str]:
    """Vendored mojo-yaml parses every note frontmatter (FAIL: 0)."""
    return _corpus_sweep("yaml", 90.0)


def _venv_env(extra: dict | None = None) -> dict:
    """os.environ + .venv/bin on PATH — the bridge resolves libpython from
    the python3 on PATH at runtime; without this the db legs abort with
    `symbol not found: Py_Initialize` when the driver is invoked without
    the venv active (graph-algos has always self-set this; db-access and
    db-integrity only worked because the operator ran with the venv on
    PATH — found 2026-09-04 running the driver bare)."""
    env = dict(os.environ, PATH=f"{REPO_ROOT / '.venv' / 'bin'}:{os.environ.get('PATH', '')}")
    if extra:
        env.update(extra)
    return env


def _leg_db_access() -> tuple[float, bool, str]:
    """DB access from Mojo through the Python drivers (sqlite3 + duckdb
    via the bridge): FTS5 search, relational slices, DuckDB scans —
    every row consumed on the Mojo side (repr checksum) vs the identical
    native loop. 6/6 checksum parity expected — GATED: any mismatch
    exits 1 (2026-09-03 retrofit, matching graph-algos)."""
    return _run("db-access", [str(BIN / "db_access_probe")], 120.0, env=_venv_env())


def _leg_db_integrity() -> tuple[float, bool, str]:
    """Mojo PORT of the repo's largest DB-access program
    (database_integrity_check.py, 2,108 lines) — FULL 17-check surface
    (source: Mojo/src/common/integrity_check.mojo). Data via the Python
    drivers through the bridge; check logic in Mojo. MOJO_INTEGRITY_PARITY=1
    makes the tool run the ORIGINAL checker live (fixture) and diff all 89
    canonical keys — GOLDEN PARITY required and GATED: any mismatch exits
    1 (2026-09-03 retrofit, matching graph-algos). ~3 s."""
    return _run(
        "db-integrity",
        [str(BIN / "integrity_check")],
        120.0,
        env=_venv_env({"MOJO_INTEGRITY_PARITY": "1"}),
    )


def _leg_graph_algos() -> tuple[float, bool, str]:
    """The make graph-algos surface from Mojo via the ORIGINAL python
    modules (phase 1 of the graph-algos port — proposal
    doc/improvements/proposals/mojo_graph_algos_port.md): the Onager
    DuckDB community extension (temp-table materialisation + table
    functions incl. seed => 42 louvain), the sqlite ATTACH + vss
    extensions, the repo's full FTS5 surface (note_search / doc_search /
    script_search shapes + the sqlite-vec vec0 KNN mirror), all 14
    metrics driven end-to-end, and the CLI --all --no-apply run.
    Mojo-side SQL execute + checksum/canonical parity is GATED (operator
    decision 2026-08-30): any mismatch exits 1 and fails this leg.
    ~30 s. Needs .venv/bin on PATH (bridge libpython discovery).
    Precondition: warm graph cache (make graph-rebuild if
    memory/graph.duckdb is stale — connect() would otherwise rebuild
    the shared cache file)."""
    env = dict(os.environ, PATH=f"{REPO_ROOT / '.venv' / 'bin'}:{os.environ.get('PATH', '')}")
    return _run("graph-algos", [str(BIN / "graph_algos_probe")], 120.0, env=env)


def _leg_regex_corpus() -> tuple[float, bool, str]:
    """Whole-corpus regex scan: Mojo-bridge-driven vs native Python.

    The binary prints both sides' stats and a PARITY verdict on the
    match count.
    """
    return _corpus_sweep("regex", 150.0)


LEGS: dict[str, dict] = {}  # filled by _build_legs(scales, reps)


def _build_legs(scales: str, reps: int) -> dict[str, dict]:
    env = dict(os.environ, PATH=f"{REPO_ROOT / '.venv' / 'bin'}:{os.environ.get('PATH', '')}")
    # gate = measured wall-clock ceiling (perf gate); budget = kill timeout.
    # Gates calibrated 2026-08-30 from the bench_report.txt run at
    # loadavg 4.68 (times: cosine 7.1, analyzer 0.45, pool 0.27, bridge 2.6,
    # yaml 0.12, regex-corpus 26.6, db-access 16.2, db-integrity 2.0,
    # graph-algos 31.2) with ~2-3x headroom. Note yaml/regex-corpus gate
    # only covers the corpus_sweep BINARY — /tmp/note_paths.txt regen time
    # is deliberately outside the measured window (it's I/O, not the
    # kernel under test).
    return {
        "cosine-knn": dict(
            budget=150.0,
            gate=20.0,
            fn=lambda: _run(
                "cosine",
                [
                    sys.executable,
                    str(REPO_ROOT / "Mojo" / "bench" / "bench_cosine_knn.py"),
                    "--scales",
                    scales,
                    "--reps",
                    str(reps),
                ],
                150.0,
            ),
        ),
        "analyzer": dict(
            budget=60.0, gate=3.0, fn=lambda: _run("analyzer", [str(BIN / "analyzer")], 60.0)
        ),
        "pool-4x": dict(budget=90.0, gate=5.0, fn=_leg_pool),
        "regex-bridge": dict(
            budget=60.0,
            gate=8.0,
            fn=lambda: _run("regex", [str(BIN / "mojo_regex_probe")], 60.0, env=env),
        ),
        "yaml-corpus": dict(budget=120.0, gate=5.0, fn=_leg_yaml),
        "regex-corpus": dict(budget=150.0, gate=60.0, fn=_leg_regex_corpus),
        "db-access": dict(budget=120.0, gate=40.0, fn=_leg_db_access),
        "db-integrity": dict(budget=120.0, gate=10.0, fn=_leg_db_integrity),
        "graph-algos": dict(budget=120.0, gate=60.0, fn=_leg_graph_algos),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scales", default="1,4,16")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--leg", action="append", choices=None, help="run only this leg (repeatable)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    args = ap.parse_args()

    legs = _build_legs(args.scales, args.reps)
    if args.list:
        for name, spec in legs.items():
            print(f"{name:14s} gate {spec['gate']:.0f}s  (timeout {spec['budget']:.0f}s)")
        return 0
    todo = [n for n in legs if not args.leg or n in args.leg]

    print(
        f"Mojo bench harness — legs: {', '.join(todo)}  "
        f"(loadavg {_loadavg()}, scales {args.scales}, reps {args.reps})"
    )
    results: list[tuple[str, float, float, bool, str]] = []
    for name in todo:
        spec = legs[name]
        print(f"  running {name} ...", flush=True)
        dt, rc_ok, tail = spec["fn"]()
        # Perf gate: rc-clean but over the measured-time ceiling → OVER_BUDGET
        # (still a failure; rc failures keep their plain FAIL + tail detail).
        over = rc_ok and dt > spec["gate"]
        results.append((name, dt, spec["gate"], rc_ok and not over, tail))

    lines = [
        "",
        f"Mojo bench {time.strftime('%Y-%m-%d %H:%M')}  "
        f"loadavg={_loadavg()}  scales={args.scales} reps={args.reps}",
        "",
    ]
    lines.append("Leg               Time (s)    Gate   Status")
    lines.append("-" * 52)
    for name, dt, gate, ok, tail in results:
        lines.append(f"  {name:.<15s} {dt:7.2f}  {gate:7.1f}s   {'✓' if ok else '✗ FAIL'}")
    for name, dt, budget, ok, tail in results:
        lines.append(f"--- {name} ({dt:.2f}s) ---")
        lines.append(tail)
    text = "\n".join(lines) + "\n"
    print(text)
    with REPORT.open("a") as fh:
        fh.write(text)
    n_fail = sum(1 for r in results if not r[3])
    print(
        f"{'✗' if n_fail else '✓'} {len(results) - n_fail}/{len(results)} legs passed "
        f"(appended to {REPORT.relative_to(REPO_ROOT)})"
    )
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
