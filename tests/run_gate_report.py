#!/usr/bin/env python3
"""Gate runner — runs the qa / integration / advisory gate steps, streams
each step's output live, prints a perf-style summary table, and appends the
run to ``<gate>_report.txt`` (same philosophy as perf_report.txt: append-only
history, one timestamped table per run, plus output tails for analysis).

The step lists and exit-code semantics below mirror the previous inline
Makefile recipes exactly:

- ``qa`` / ``advisory`` / ``integration`` — every step runs regardless of
  failures; the summary table + report tails show ALL failures at the end
  (user directive 2026-08-25: "even for qa, find the failure in the end and
  look at the logs" — replaced make's abort-at-first-failure semantics).
  The advisory ``ty check tests`` line stays non-gating (was ``|| true``).

EVERY step appends its output tail to the report — passing steps included
(user directive 2026-08-25: "all make steps log to output file"; a passing
live-invariants run's warnings used to stream to console and never reach
advisory_report.txt). pytest's ``-ra`` (pytest.ini addopts) additionally
puts the short test summary at the end of its output, inside the tail.

Invoked by ``make qa`` / ``make integration`` / ``make advisory``.  Like
tests/run_perf_benchmarks.py, run it through make (or the venv python
directly) so children resolve to the project venv.

Parallel mode (2026-08-25; user directive — 4 cores): steps run
concurrently in a thread pool, default ``_DEFAULT_JOBS`` (4). Resolution
order: explicit ``--jobs N`` > the user's ``make -j N`` (parsed from
MAKEFLAGS) > default 4. Verified safe for these gates: the DuckDB cache is
WARM during gate runs (no cross-process writes; two concurrent
``query.connect()`` opens on the warm file were tested OK) and SQLite is
ATTACHed READ_ONLY everywhere. Concurrent pytest steps get per-step
``cache_dir`` overrides (``.pytest_cache/<label>``) so they never race on
the shared ``.pytest_cache``. ALL steps always run (no cancellation);
console lines get ``[label]`` prefixes in parallel mode; the report keeps
clean per-step tails, and ``write_report`` appends each whole block under
an exclusive flock — concurrent gate runners on the same report file
(e.g. two concurrent ``make integration`` runners) serialize instead of
interleaving.

Usage::

    python3 tests/run_gate_report.py qa|integration|advisory
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Binaries are resolved once, at import (keeps the ruff S607 audit quiet and
# pins the venv binaries when launched through make, whose PATH export the
# runner inherits).
_PY = sys.executable
_RUFF = shutil.which("ruff") or "ruff"
_TY = shutil.which("ty") or "ty"
_DEPTRY = shutil.which("deptry") or "deptry"
_MAKE = shutil.which("make") or "make"

_TAIL_LINES = 60      # lines appended to the report per step
# ty-tests digest budget: concise diagnostics are 1 line each; 120 covers
# the worst historical burst (91 diagnostics, 2026-08-25) with margin.
_TY_TESTS_TAIL_LINES = 120
_CAPTURE_LINES = 400  # rolling in-memory cap while streaming
_DEFAULT_JOBS = 4     # 4 cores (user directive 2026-08-25); override: --jobs N / make -j N


@dataclass(frozen=True)
class Step:
    label: str
    args: tuple[str, ...]
    nonblocking: bool = False       # recorded, never fails the gate (advisory's ty-tests)
    # Per-step report-tail override (None -> _TAIL_LINES). Steps whose
    # output is a diagnostic DIGEST (ty-tests concise: 1 line/diagnostic)
    # raise it so every diagnostic lands in the report, not just the last
    # few (2026-08-26 logging fix; full-format ty diagnostics are ~10-14
    # lines each and drowned the 60-line default tail).
    tail_lines: int | None = None


@dataclass(frozen=True)
class Gate:
    steps: tuple[Step, ...]


@dataclass
class Result:
    step: Step
    seconds: float | None = None
    rc: int | None = None
    tail: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.rc == 0:
            return "OK"
        return "FAIL (non-gating)" if self.step.nonblocking else "FAIL"


GATES: dict[str, Gate] = {
    # Was the `qa:` recipe (abort-at-first-failure) — now run-all
    # (2026-08-25): every step executes; failures surface together in the
    # summary table + report tails.
    "qa": Gate(
        steps=(
            Step("lint", (_RUFF, "check", ".")),
            Step("types", (_TY, "check", "helpers", "app.py")),
            Step("deptry", (_DEPTRY, ".")),
            Step("static_checks", (_PY, "helpers/validators/static_checks.py")),
            # -n auto (not the gate's -j): pytest workers scale with CORES,
            # steps with the user's concurrency preference — different axes.
            # The suite is the qa critical path (113s serial -> ~65s on 4
            # workers, 2026-08-31 shakedown: 2423 passed, no shared-state
            # breakage).
            Step("pytest", (_PY, "-m", "pytest", "-m", "not live", "-n", "auto")),
            Step("verify_notes", (_PY, "helpers/validators/verify_notes.py")),
            Step("integrity_check", (_PY, "helpers/misc/database_integrity_check.py")),
            Step("snapshot_check", (_PY, "helpers/maintenance/snapshot_db.py", "--check")),
        ),
    ),
    "integration": Gate(
        steps=(
            Step("pytest-integration",
                 (_PY, "-m", "pytest", "-m", "integration", "-v", "-n", "auto")),
        ),
    ),
    # The advisory recipe: the ty-tests line never blocks (was `|| true`);
    # every step runs even after a failure (all gates are run-all now).
    # ty-tests delegates to the `types-tests` make target — the single
    # source of truth for the extra-search-path flag list + ty.tests.toml
    # config (standalone-invocable after a feature).
    #
    # NO integration step (user directive 2026-08-31, the advisory-side
    # resolution of the old Slice C): qa's `-m "not live"` already runs
    # the 551 integration tests under the GATING gate; re-running them
    # here only doubled execution and its 4 workers stretched
    # live-invariants ~50s → 76-87s. Integration stays explicitly
    # runnable via `make integration`.
    "advisory": Gate(
        steps=(
            Step("ty-tests", (_MAKE, "types-tests", "TYPES_TESTS_FMT=concise"),
                 nonblocking=True, tail_lines=_TY_TESTS_TAIL_LINES),
            # live-invariants under -n auto (gate_xdist_phase2 Slice A):
            # conftest redirects the default graph cache to a per-worker
            # copy, so the live suite spreads across workers without
            # cross-process DuckDB lock collisions (serial 72.5s -> ~50s).
            # Tests asserting real-cache semantics carry `real_graph_cache`.
            Step("live-invariants", (_PY, "-m", "pytest", "-m", "live",
                                     "-n", "auto")),
            Step("frontend-check", (_MAKE, "frontend-check")),
            Step("graph-algos", (_MAKE, "graph-algos")),
            Step("analytics", (_MAKE, "analytics")),
            Step("suggest-relations", (_MAKE, "suggest-relations")),
            Step("doc-search-check",
                 (_PY, "helpers/maintenance/rebuild_doc_search.py", "--check")),
            Step("script-search-check",
                 (_PY, "helpers/maintenance/rebuild_script_search.py", "--check")),
            Step("note-search-check",
                 (_PY, "helpers/maintenance/rebuild_note_search.py", "--check")),
            Step("lint-audit", (_RUFF, "check", "--select", "S,UP,C901", ".")),
        ),
    ),
}


def run_step(step: Step, *, jobs: int = 1,
             out_lock: threading.Lock | None = None) -> Result:
    """Run one gate step: stream its output live, capture a rolling tail.

    jobs > 1: every streamed line is prefixed ``[label]`` under a shared
    writer lock (readable interleaving), and pytest steps get a per-step
    ``cache_dir`` so concurrent pytest processes never race on the shared
    ``.pytest_cache`` (report tails stay unprefixed).
    """
    args = list(step.args)
    if jobs > 1 and "pytest" in args:
        args += ["-o", f"cache_dir=.pytest_cache/{step.label}"]
    lock = out_lock if out_lock is not None else threading.Lock()

    def _emit(line: str) -> None:
        with lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    _emit(f"--- {step.label}: {' '.join(args)}")
    capture: deque[str] = deque(maxlen=_CAPTURE_LINES)
    t0 = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603  # list-form call; shell=False (default); args are GATES constants or runner-derived cache_dir flags
        args, cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if jobs == 1:
            _emit(line)
        else:
            _emit(f"[{step.label}] {line}")
        capture.append(line)
    rc = proc.wait()
    return Result(step, time.perf_counter() - t0, rc, list(capture))


def run_gate(gate: Gate, jobs: int = 1) -> list[Result]:
    """Run the gate's steps sequentially (jobs=1) or concurrently (jobs>1).

    ALL steps always run — no abort-at-first-failure, sequential or
    parallel (user directive 2026-08-25: run everything, find the failures
    at the end in the summary table + report tails). Results are returned
    in gate step order regardless of completion order.
    """
    if jobs <= 1:
        return [run_step(s) for s in gate.steps]

    lock = threading.Lock()
    by_step: dict[str, Result] = {}
    with ThreadPoolExecutor(max_workers=min(jobs, len(gate.steps))) as pool:
        futures: dict[Future, Step] = {
            pool.submit(run_step, s, jobs=jobs, out_lock=lock): s
            for s in gate.steps
        }
        for fut in as_completed(futures):
            by_step[futures[fut].label] = fut.result()
    return [by_step[s.label] for s in gate.steps]


def _jobs_from_makeflags() -> int | None:
    """User's ``make -j N`` override, parsed from MAKEFLAGS (else None).

    ``make advisory -j 8`` puts ``-j8`` in MAKEFLAGS; bare ``make -j``
    (unlimited) appears as ``-j`` with no count and ``--jobserver-auth``
    present — treat that as "use every core". Never called directly by
    tests outside a make context (MAKEFLAGS unset → None).
    """
    mf = os.environ.get("MAKEFLAGS", "")
    m = re.search(r"(?:^|\s)-j(\d+)", mf)
    if m and int(m.group(1)) > 0:
        return int(m.group(1))
    if re.search(r"(?:^|\s)-j(?=\s|$)", mf) or "--jobserver-auth" in mf:
        return os.cpu_count() or _DEFAULT_JOBS
    return None


def overall_ok(results: list[Result]) -> bool:
    return all(r.skipped or r.rc == 0 or r.step.nonblocking for r in results)


def table_lines(results: list[Result]) -> list[str]:
    lines = ["", "Step                                  Time (s)   Status", "-" * 70]
    for r in results:
        secs = f"{r.seconds:8.2f}" if r.seconds is not None else "       -"
        flag = "✓" if (not r.skipped and r.rc == 0) else ("−" if r.skipped else "✗")
        lines.append(f"  {r.step.label:.<34s} {secs}   {flag} {r.status}")
    lines.append("-" * 70)
    ok = sum(1 for r in results if not r.skipped and r.rc == 0)
    verdict = "PASS" if overall_ok(results) else "FAIL"
    lines.append(f"  {ok}/{len(results)} passed  ·  gate {verdict}")
    return lines


def write_report(report_path: Path, gate_name: str, results: list[Result],
                 jobs: int = 1) -> None:
    """Append one report block; whole-block under an exclusive flock.

    Parallel-safety (2026-08-25): the in-process report is written once at
    the end from per-step captures, so a single runner can never interleave
    its own block. The flock covers the CROSS-process case — two gate
    runners appending to the same file (e.g. `make advisory`'s nested
    integration sub-runner racing a concurrently-invoked
    `make integration`; both write integration_report.txt). Locking the fd
    serializes whole blocks: a waiter's block starts only after the holder
    closes the file (POSIX flock releases on close).
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(report_path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(f"=== make {gate_name}  {ts}  (Python {sys.version.split()[0]})"
                    + (f"  jobs={jobs}" if jobs > 1 else "") + " ===\n")
            f.write("\n".join(table_lines(results)) + "\n")
            for r in results:
                if r.skipped:
                    continue
                why = "FAILED" if r.rc != 0 else "OK"
                keep = r.step.tail_lines or _TAIL_LINES
                f.write(f"--- {r.step.label} · last {min(keep, len(r.tail))} lines ({why}) ---\n")
                f.write("\n".join(r.tail[-keep:]) + "\n")
            f.write("\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def resolve_jobs(cli_jobs: int | None) -> int:
    """--jobs N > the user's make -j N (MAKEFLAGS) > _DEFAULT_JOBS (4)."""
    if cli_jobs is not None:
        return max(1, cli_jobs)
    return _jobs_from_makeflags() or _DEFAULT_JOBS


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cli_jobs: int | None = None
    rest: list[str] = []
    it = iter(argv)
    for a in it:
        if a == "--jobs":
            try:
                cli_jobs = int(next(it))
            except (StopIteration, ValueError):
                print("usage: --jobs N", file=sys.stderr)
                return 2
        else:
            rest.append(a)
    if len(rest) != 1 or rest[0] not in GATES:
        print(f"usage: python3 tests/run_gate_report.py {{{'|'.join(GATES)}}}"
              " [--jobs N]", file=sys.stderr)
        return 2
    name = rest[0]
    jobs = resolve_jobs(cli_jobs)
    print(f"(gate {name}: jobs={jobs})")
    results = run_gate(GATES[name], jobs)
    print("\n".join(table_lines(results)))
    report = REPO_ROOT / f"{name}_report.txt"
    write_report(report, name, results, jobs)
    print(f"appended to {report.relative_to(REPO_ROOT)}")
    return 0 if overall_ok(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
