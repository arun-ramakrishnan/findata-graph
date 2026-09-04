#!/usr/bin/env python3
"""Tests for the opt-in `--with-analytics` hook in helpers/core/parse_newsletter.py.

Stage 6 must:
  - Be skipped by default (no analytics subprocess spawned).
  - Be refused when passed without --apply (guard exits non-zero).
  - Invoke `helpers/graph/algorithms.py --all --apply` only when both
    --apply and --with-analytics are set, and only after Stage 5 succeeds.

We don't execute the full main() — instead we drive main() with a fake
newsletter and intercept subprocess.run so no real writes happen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from helpers.core import parse_newsletter as pn  # noqa: E402


# --------------------------------------------------------------------------- #
# Guard: --with-analytics without --apply exits non-zero before any work.     #
# --------------------------------------------------------------------------- #
def test_with_analytics_requires_apply(monkeypatch, fake_newsletter):
    """The CLI guard refuses --with-analytics unless --apply is also set."""

    # Prevent any real capture/validation from running.
    monkeypatch.setattr(pn, "capture_images", lambda *_a, **_k: True)
    monkeypatch.setattr(pn, "extract_companies", lambda _content: [])
    monkeypatch.setattr(pn, "classify", lambda _c, _e: ([], [], []))
    monkeypatch.setattr(pn, "get_existing_entity_names", lambda _conn: set())
    monkeypatch.setattr(pn, "get_sector_dirs", lambda: set())
    monkeypatch.setattr(
        pn, "emit_worklist", lambda *a, **k: PROJECT_ROOT / "_test_scratch" / "wl.json"
    )
    monkeypatch.setattr(pn, "run_validation", lambda _apply: True)
    monkeypatch.setattr(pn, "run_graph_analytics", lambda: True)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "parse_newsletter.py",
            str(fake_newsletter),
            "--with-analytics",  # but no --apply
        ],
    )
    with pytest.raises(SystemExit) as exc:
        pn.main()
    assert exc.value.code != 0


# ---------------------------------------------------------------------------
# Stage 6 dispatch: subprocess.run for algorithms.py is only invoked when     #
# both flags are set.                                                         #
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_newsletter(tmp_path, monkeypatch):
    """Create a fake newsletter .md *inside the project tree* so that
    `md_path.relative_to(PROJECT_ROOT)` in main() works."""
    scratch = PROJECT_ROOT / "_test_scratch"
    scratch.mkdir(exist_ok=True)
    fake_md = scratch / "Dummy.md"
    fake_md.write_text("# Dummy\n", encoding="utf-8")
    yield fake_md
    # Clean up both the fake md and any worklist main() wrote.
    for p in scratch.glob("*"):
        p.unlink(missing_ok=True)
    if scratch.exists() and not any(scratch.iterdir()):
        scratch.rmdir()


def test_stage6_skipped_without_with_analytics_flag(monkeypatch, fake_newsletter):
    """Default --apply run does NOT invoke run_graph_analytics."""
    captured = {"called": False}

    def _boom():
        captured["called"] = True
        raise AssertionError("run_graph_analytics must not be called")

    monkeypatch.setattr(pn, "capture_images", lambda *_a, **_k: True)
    monkeypatch.setattr(pn, "extract_companies", lambda _content: [])
    monkeypatch.setattr(pn, "classify", lambda _c, _e: ([], [], []))
    monkeypatch.setattr(pn, "get_existing_entity_names", lambda _conn: set())
    monkeypatch.setattr(pn, "get_sector_dirs", lambda: set())
    monkeypatch.setattr(
        pn, "emit_worklist", lambda *a, **k: PROJECT_ROOT / "_test_scratch" / "wl.json"
    )
    monkeypatch.setattr(pn, "run_validation", lambda _apply: True)
    monkeypatch.setattr(pn, "run_graph_analytics", _boom)

    monkeypatch.setattr(sys, "argv", ["parse_newsletter.py", str(fake_newsletter), "--apply"])
    with pytest.raises(SystemExit) as exc:
        pn.main()
    assert exc.value.code == 0
    assert captured["called"] is False


def test_stage6_invoked_when_both_flags_set(monkeypatch, fake_newsletter):
    """--apply --with-analytics routes through run_graph_analytics."""
    captured = {"called": False}

    def _stub():
        captured["called"] = True
        return True

    monkeypatch.setattr(pn, "capture_images", lambda *_a, **_k: True)
    monkeypatch.setattr(pn, "extract_companies", lambda _content: [])
    monkeypatch.setattr(pn, "classify", lambda _c, _e: ([], [], []))
    monkeypatch.setattr(pn, "get_existing_entity_names", lambda _conn: set())
    monkeypatch.setattr(pn, "get_sector_dirs", lambda: set())
    monkeypatch.setattr(
        pn, "emit_worklist", lambda *a, **k: PROJECT_ROOT / "_test_scratch" / "wl.json"
    )
    monkeypatch.setattr(pn, "run_validation", lambda _apply: True)
    monkeypatch.setattr(pn, "run_graph_analytics", _stub)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "parse_newsletter.py",
            str(fake_newsletter),
            "--apply",
            "--with-analytics",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        pn.main()
    assert exc.value.code == 0
    assert captured["called"] is True


# --------------------------------------------------------------------------- #
# run_graph_analytics delegates to the algorithms script with the right argv. #
# --------------------------------------------------------------------------- #
def test_run_graph_analytics_invokes_algorithms_script(monkeypatch):
    """run_graph_analytics shells out to algorithms.py --all --apply."""
    captured = {"argv": None, "cwd": None}

    class _FakeResult:
        returncode = 0

    def _fake_run(argv, cwd=None, **_kw):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        return _FakeResult()

    monkeypatch.setattr(pn.subprocess, "run", _fake_run)
    ok = pn.run_graph_analytics()
    assert ok is True
    assert captured["argv"] is not None
    assert captured["argv"][:2] == ["python3", str(pn.ALGORITHMS)]
    assert captured["argv"][2:] == ["--all", "--apply"]
    assert captured["cwd"] == str(pn.PROJECT_ROOT)


def test_run_graph_analytics_returns_false_on_nonzero(monkeypatch):
    """A non-zero exit from algorithms.py is surfaced (but does not raise)."""

    class _FakeResult:
        returncode = 2

    monkeypatch.setattr(pn.subprocess, "run", lambda *a, **_k: _FakeResult())
    assert pn.run_graph_analytics() is False


# --------------------------------------------------------------------------- #
# Module-level wiring: ALGORITHMS points at the real script path.             #
# --------------------------------------------------------------------------- #
def test_algorithms_script_path_exists():
    assert pn.ALGORITHMS.name == "algorithms.py"
    assert pn.ALGORITHMS.exists(), f"missing: {pn.ALGORITHMS}"
