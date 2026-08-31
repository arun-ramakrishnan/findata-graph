#!/usr/bin/env python3
"""Lint/format gates — python footprint via ruff, Mojo footprint via `mojo format`.

Makes the two style gates pytest-visible so they run under the ``make qa``
pytest leg, and adds the one property the raw ``make lint`` invocation cannot
express: NOTHING in the tracked tree escapes the linter.

1. ``test_ruff_python_footprint_clean`` — ``ruff check .`` passes (the exact
   ``make lint`` command; E/F real-bug rules only, per pyproject.toml).
2. ``test_ruff_sees_every_tracked_python_file`` — every ``git ls-files
   '*.py'`` file is passed to ruff EXPLICITLY. Ruff respects .gitignore and
   its built-in exclude list on directory walks, but files named on the
   command line are linted regardless (``--force-exclude`` is NOT set), so an
   tracked file that had fallen into an excluded directory would fail here.
   That exclusion-blind-spot class is exactly how the doc/local hardlink
   surprise happened for the search indexes (#184/#185) — this pins the
   lint equivalent shut. The Mojo harness Python (Mojo/bench/*.py) is part
   of this set, so the Mojo footprint's Python is linted like any other.
3. ``test_mojo_format_footprint_clean`` — byte-identical `mojo format`
   copy-check over Mojo/src/** + Mojo/tests/**. This toolchain's
   ``mojo format`` has no ``--check`` flag, so the gate formats a COPY in
   tmp_path and diffs — the tree is never mutated by the test. One-time
   normalization landed 2026-08-30 (``make mojo-fmt``); the gate keeps it
   canonical. Vendored Mojo (Mojo/vendor/) is third-party code and is
   deliberately NOT gated.

Perf/wall-clock budgets deliberately do NOT live here: the 2026-08-14
history in test_performance.py stands — wall-clock belongs to the make
runners (``make perf`` for Python, ``make mojo-bench`` for Mojo legs,
which gate measured time per leg since 2026-08-30). Only complexity-class
scaling assertions belong in pytest.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUFF = REPO_ROOT / ".venv" / "bin" / "ruff"
MOJO = REPO_ROOT / ".venv" / "bin" / "mojo"
GIT = shutil.which("git")
VENDOR = REPO_ROOT / "Mojo" / "vendor"


def _mojo_sources() -> list[Path]:
    """Mojo/src/*/*.mojo (both packages) + Mojo/tests/*.mojo, sorted."""
    return sorted(
        set((REPO_ROOT / "Mojo").glob("src/*/*.mojo"))
        | set((REPO_ROOT / "Mojo").glob("tests/*.mojo"))
    )


# ---------------------------------------------------------------------------
# 1 + 2 — ruff over the python footprint
# ---------------------------------------------------------------------------
def test_ruff_python_footprint_clean():
    """`ruff check .` (the make lint command) passes from the repo root."""
    r = subprocess.run(  # noqa: S603  # repo-local venv binary, no shell
        [str(RUFF), "check", "."],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert r.returncode == 0, f"ruff found violations:\n{r.stdout}\n{r.stderr}"


def test_ruff_sees_every_tracked_python_file():
    """Every git-tracked .py file is ruff-clean when named explicitly.

    Explicit paths bypass ruff's gitignore/built-in exclusions, so this is
    the no-blind-spots guarantee: a tracked file inside an excluded dir
    (or ignored via a future .gitignore pattern) still gets linted — and
    must still pass.
    """
    assert GIT, "git not found on PATH — probe broken?"
    out = subprocess.run(  # noqa: S603
        [GIT, "ls-files", "--", "*.py"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    tracked = [REPO_ROOT / line for line in out.stdout.splitlines() if line]
    assert tracked, "git ls-files returned no python files — probe broken?"
    missing = [str(p) for p in tracked if not p.is_file()]
    assert not missing, f"tracked but absent from worktree: {missing}"
    r = subprocess.run(  # noqa: S603
        [str(RUFF), "check", "--no-cache", *map(str, tracked)],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert r.returncode == 0, (
        f"ruff flagged {len(tracked)} explicitly-named tracked files:\n"
        f"{r.stdout}\n{r.stderr}"
    )


# ---------------------------------------------------------------------------
# 3 — mojo format over the Mojo footprint
# ---------------------------------------------------------------------------
def test_mojo_format_footprint_clean(tmp_path):
    """`mojo format` on a COPY is byte-identical to every Mojo source.

    The toolchain has no `--check`, so: copy each source into tmp_path,
    format the copies in one invocation, compare bytes. Failure output
    names the divergent files and the fix command.
    """
    assert MOJO.is_file(), "mojo toolchain missing at .venv/bin/mojo"

    sources = _mojo_sources()
    assert sources, "no Mojo sources found — glob broken?"

    copies: list[Path] = []
    for src in sources:
        rel = src.relative_to(REPO_ROOT)
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copies.append(dst)

    r = subprocess.run(  # noqa: S603
        [str(MOJO), "format", *map(str, copies)],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert r.returncode == 0, f"mojo format failed:\n{r.stdout}\n{r.stderr}"

    diverged = [
        src.relative_to(REPO_ROOT).as_posix()
        for src, copy in zip(sources, copies)
        if src.read_bytes() != copy.read_bytes()
    ]
    assert not diverged, (
        "mojo format would change these files (run: make mojo-fmt):\n  "
        + "\n  ".join(diverged)
    )
