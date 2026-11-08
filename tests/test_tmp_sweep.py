"""Tests for the temp-dir / rebuild-residue hygiene sweep (tmpdir_sanitization S7).

Two ownership surfaces are pinned here: the `$TMPDIR` globs the sweep has
always had, and the `memory/*.duckdb.rebuild-*.tmp` residue added 2026-11-08
after a SIGKILLed rebuild left 69 MB behind (the `finally` unlink in
helpers/graph/query.py `_rebuild_via_swap` never ran).
"""

from __future__ import annotations

import os
from pathlib import Path

from helpers.maintenance import tmp_sweep as ts

REPO_ROOT = Path(__file__).resolve().parents[1]


def _age(path: Path, hours: float) -> None:
    """Backdate an entry so the age guard sees it as stale."""
    old = path.stat().st_mtime - hours * 3600
    os.utime(path, (old, old))


def test_repo_candidates_finds_rebuild_residue(tmp_path):
    """The exact artifact a killed rebuild left on 2026-11-07 is reclaimable."""
    memory = tmp_path / "memory"
    memory.mkdir()
    residue = memory / "graph.xdist-shared.duckdb.rebuild-2162957-3.tmp"
    residue.write_bytes(b"x" * 32)
    wal = memory / "graph.xdist-shared.duckdb.rebuild-2162957-3.tmp.wal"
    wal.write_bytes(b"")
    lock = memory / "graph.xdist-shared.duckdb.rebuild-2162957-3.tmp.build.lock"
    lock.write_bytes(b"")
    for p in (residue, wal, lock):
        _age(p, 48)

    found = ts.repo_candidates(memory, min_age_hours=24.0)
    assert found == sorted([residue, wal, lock], key=lambda p: p.name)


def test_repo_candidates_spares_live_rebuild(tmp_path):
    """An in-flight rebuild's tmp is mtime-hot and must survive the sweep."""
    memory = tmp_path / "memory"
    memory.mkdir()
    live = memory / "graph.xdist-shared.duckdb.rebuild-999-0.tmp"
    live.write_bytes(b"x")
    assert ts.repo_candidates(memory, min_age_hours=24.0) == []


def test_repo_candidates_ignores_live_stores(tmp_path):
    """Only rebuild residue matches — the standing stores/DuckDB files never do."""
    memory = tmp_path / "memory"
    memory.mkdir()
    for name in ("graph.duckdb", "graph.duckdb.wal", "graph.duckdb.io.lock", "notes.duckdb"):
        p = memory / name
        p.write_bytes(b"x")
        _age(p, 72)
    assert ts.repo_candidates(memory, min_age_hours=24.0) == []


def test_repo_candidates_ignores_subdirectories(tmp_path):
    """The sweep is a flat glob over memory/ — never recursive."""
    memory = tmp_path / "memory"
    nested = memory / "data" / "deep"
    nested.mkdir(parents=True)
    stray = nested / "graph.duckdb.rebuild-1-0.tmp"
    stray.write_bytes(b"x")
    _age(stray, 72)
    assert ts.repo_candidates(memory, min_age_hours=24.0) == []


def test_main_reaps_memory_residue_and_reports_it(tmp_path, capsys):
    """`main --apply` removes the residue and names it in the report."""
    tempdir = tmp_path / "tmp"
    tempdir.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    residue = memory / "graph.xdist-shared.duckdb.rebuild-7-1.tmp"
    residue.write_bytes(b"y" * 64)
    _age(residue, 48)

    rc = ts.main(
        ["--apply", "--tempdir", str(tempdir), "--memory-dir", str(memory), "--min-age-hours", "24"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert not residue.exists()
    assert str(residue) in out
    assert "reclaimable" not in out  # --apply reports "removed"


def test_main_check_signals_dirty_memory_dir(tmp_path):
    """`--check` is the machine signal: rc 1 when residue is reclaimable."""
    tempdir = tmp_path / "tmp"
    tempdir.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    residue = memory / "graph.duckdb.rebuild-3-0.tmp"
    residue.write_bytes(b"z")
    _age(residue, 48)
    assert ts.main(["--check", "--tempdir", str(tempdir), "--memory-dir", str(memory)]) == 1


def test_main_check_clean_when_memory_dir_absent(tmp_path):
    """A checkout without memory/ (fresh clone) is clean, not an error."""
    tempdir = tmp_path / "tmp"
    tempdir.mkdir()
    missing = tmp_path / "no-such-memory"
    assert ts.main(["--check", "--tempdir", str(tempdir), "--memory-dir", str(missing)]) == 0


def test_default_memory_dir_points_at_this_repo():
    """The CLI default resolves to THIS checkout's memory/, not a cwd guess."""
    assert ts.REPO_ROOT == REPO_ROOT
    assert ts.REPO_ROOT / "memory" == REPO_ROOT / "memory"
