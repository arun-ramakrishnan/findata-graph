"""tests/_tmp_hygiene.py — keep-1 pruning + shared-template wiring.

The tmpfs-amplification fix (deferred item executed 2026-09-27): these
pin the pruning contract (quiet roots go, live roots and the current
run stay) without needing a real xdist run.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from tests._tmp_hygiene import prune_old_pytest_roots


def _mkroot(parent: Path, name: str, age_s: float) -> Path:
    root = parent / name
    (root / "popen-gw0").mkdir(parents=True)
    past = time.time() - age_s
    os.utime(root / "popen-gw0", (past, past))
    os.utime(root, (past, past))
    return root


def test_quiet_roots_are_pruned_current_survives(tmp_path):
    current = _mkroot(tmp_path, "pytest-9", 0)
    old = _mkroot(tmp_path, "pytest-7", 3600)
    pruned = prune_old_pytest_roots(current)
    assert pruned == [old]
    assert current.exists()
    assert not old.exists()


def test_recent_roots_are_left_alone(tmp_path):
    """A sibling touched inside the quiet window is presumed a live run."""
    current = _mkroot(tmp_path, "pytest-9", 0)
    live = _mkroot(tmp_path, "pytest-8", 60)  # 1 min ago — inside 30 min window
    assert prune_old_pytest_roots(current) == []
    assert live.exists()


def test_child_activity_counts_as_alive(tmp_path):
    """Root mtime stale but a worker dir touched now → alive."""
    current = _mkroot(tmp_path, "pytest-9", 0)
    busy = _mkroot(tmp_path, "pytest-8", 3600)
    (busy / "popen-gw1").mkdir()
    assert prune_old_pytest_roots(current) == []
    assert busy.exists()


def test_symlink_and_foreign_entries_untouched(tmp_path):
    current = _mkroot(tmp_path, "pytest-9", 0)
    _mkroot(tmp_path, "pytest-8", 3600)
    link = tmp_path / "pytest-current"
    link.symlink_to(current)
    prune_old_pytest_roots(current)
    assert link.is_symlink()


def test_no_siblings_is_a_noop(tmp_path):
    current = _mkroot(tmp_path, "pytest-9", 0)
    assert prune_old_pytest_roots(current) == []
    assert current.exists()
