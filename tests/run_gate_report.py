#!/usr/bin/env python3
"""Gate runner — runs the qa / integration / advisory gate steps, streams
each step's output live, prints a perf-style summary table, and appends the
run to ``<gate>_report.txt`` (same philosophy as perf_report.txt: append-only
history, one timestamped table per run, plus output tails for analysis).

The step lists and exit-code semantics below mirror the previous inline
Makefile recipes exactly:

- ``qa``         — aborts at the first failing step (make's default).
- ``integration``— single pytest step.
- ``advisory``   — runs every step regardless of failures (make -k) and the
                   leading ``ty check tests`` line is non-gating (was ``|| true``).

pytest's ``-ra`` (pytest.ini addopts) puts the short test summary — fails,
xfails with reasons, skips — at the end of the pytest output, so a modest
tail is kept for the pytest steps even on success; every other step appends
a tail only when it fails.

Invoked by ``make qa`` / ``make integration`` / ``make advisory``.  Like
tests/run_perf_benchmarks.py, run it through make (or the venv python
directly) so children resolve to the project venv.

Usage::

    python3 tests/run_gate_report.py qa|integration|advisory
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections import deque
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
_CAPTURE_LINES = 400  # rolling in-memory cap while streaming


@dataclass(frozen=True)
class Step:
    label: str
    args: tuple[str, ...]
    nonblocking: bool = False       # recorded, never fails the gate (advisory's ty-tests)
    tail_on_success: bool = False   # pytest steps: keep the -ra short summary


@dataclass(frozen=True)
class Gate:
    steps: tuple[Step, ...]
    fail_fast: bool


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
    # Mirrors the old `qa:` recipe: lint + types + deptry + static +
    # pytest + notes + integrity + snapshot, stopping at the first failure.
    "qa": Gate(
        steps=(
            Step("lint", (_RUFF, "check", ".")),
            Step("types", (_TY, "check", "helpers", "app.py")),
            Step("deptry", (_DEPTRY, ".")),
            Step("static_checks", (_PY, "helpers/validators/static_checks.py")),
            Step("pytest", (_PY, "-m", "pytest", "-m", "not live"), tail_on_success=True),
            Step("verify_notes", (_PY, "helpers/validators/verify_notes.py")),
            Step("integrity_check", (_PY, "helpers/misc/database_integrity_check.py")),
            Step("snapshot_check", (_PY, "helpers/maintenance/snapshot_db.py", "--check")),
        ),
        fail_fast=True,
    ),
    "integration": Gate(
        steps=(
            Step("pytest-integration", (_PY, "-m", "pytest", "-m", "integration", "-v"),
                 tail_on_success=True),
        ),
        fail_fast=False,
    ),
    # Mirrors the old `advisory:` recipe: the ty-tests line never blocks
    # (was `|| true`); every other step runs even after a failure (was
    # `make -k`); integration re-enters this runner so advisory runs also
    # append to integration_report.txt.
    "advisory": Gate(
        steps=(
            Step("ty-tests",
                 (_TY, "check", "tests",
                  "--extra-search-path", "helpers",
                  "--extra-search-path", "helpers/core",
                  "--extra-search-path", "helpers/maintenance",
                  "--extra-search-path", "helpers/misc",
                  "--config-file", "ty.tests.toml",
                  "--exit-zero-on-warning"),
                 nonblocking=True),
            Step("live-invariants", (_MAKE, "live-invariants")),
            Step("frontend-check", (_MAKE, "frontend-check")),
            Step("graph-algos", (_MAKE, "graph-algos")),
            Step("analytics", (_MAKE, "analytics")),
            Step("suggest-relations", (_MAKE, "suggest-relations")),
            Step("integration",
                 (_PY, str(Path(__file__).resolve()), "integration"),
                 tail_on_success=True),
            Step("lint-audit", (_RUFF, "check", "--select", "S,UP,C901", ".")),
        ),
        fail_fast=False,
    ),
}


def run_step(step: Step) -> Result:
    """Run one gate step: stream its output live, capture a rolling tail."""
    print(f"\n--- {step.label}: {' '.join(step.args)}")
    capture: deque[str] = deque(maxlen=_CAPTURE_LINES)
    t0 = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603  # list-form call; shell=False (default); args are GATES constants
        list(step.args), cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        sys.stdout.write(raw)
        sys.stdout.flush()
        capture.append(raw.rstrip("\n"))
    rc = proc.wait()
    return Result(step, time.perf_counter() - t0, rc, list(capture))


def run_gate(gate: Gate) -> list[Result]:
    results: list[Result] = []
    abort = False
    for step in gate.steps:
        if abort:
            results.append(Result(step, skipped=True))
            continue
        result = run_step(step)
        results.append(result)
        if gate.fail_fast and result.rc != 0 and not step.nonblocking:
            abort = True
    return results


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


def write_report(report_path: Path, gate_name: str, results: list[Result]) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(report_path, "a") as f:
        f.write(f"=== make {gate_name}  {ts}  (Python {sys.version.split()[0]}) ===\n")
        f.write("\n".join(table_lines(results)) + "\n")
        for r in results:
            if r.skipped:
                continue
            if r.rc == 0 and not r.step.tail_on_success:
                continue
            why = "FAILED" if r.rc != 0 else "success tail (-ra summary)"
            f.write(f"--- {r.step.label} · last {min(_TAIL_LINES, len(r.tail))} lines ({why}) ---\n")
            f.write("\n".join(r.tail[-_TAIL_LINES:]) + "\n")
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in GATES:
        print(f"usage: python3 tests/run_gate_report.py {{{'|'.join(GATES)}}}",
              file=sys.stderr)
        return 2
    name = argv[0]
    results = run_gate(GATES[name])
    print("\n".join(table_lines(results)))
    report = REPO_ROOT / f"{name}_report.txt"
    write_report(report, name, results)
    print(f"appended to {report.relative_to(REPO_ROOT)}")
    return 0 if overall_ok(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
