"""Pins for ``tests/_live_store.py`` — the one gate for live legs.

Two things are pinned: the resolver agrees with the house door on every
engine/override/sibling combination, and no test or bench script names the
retired ``memory/research.db`` as its live path outside the allowlist below
(each entry is hermetic or an explicitly open port). Before this pin the
cutover retired the file and eleven live legs skipped or exited 0 on it,
silently (see ``tests/_live_store.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from helpers.core import db_engine
from tests._live_store import live_store_path, require_live_store

TESTS = Path(__file__).resolve().parent

#: Files that may still spell the retired name, and why.
_ALLOWED: dict[str, str] = {
    # Hermetic fixtures: the legacy name is the ROOT of a per-worker copy that
    # conftest redirects; nothing opens the live tree through it.
    "test_graph.py": "hermetic per-worker copy (DB_PATH); module gate via live_store_path()",
    "test_graph_disk.py": "hermetic per-worker copy (DB_PATH) + legacy-string pins",
    "_tmp_hygiene.py": "hermetic template donor root",
    "test_static_checks.py": "hermetic tmp stores + OPEN: five SQLite-dialect schema guards",
    # Not gated on existence: the house door redirects the path, so these run.
    "test_context_pack.py": "passes the path through; redirected by the house door",
    "bench_rebuild_scale.py": "manual SQLite->DuckDB rebuild bench (no perf leg)",
    # Hermetic tmp project roots that merely spell the layout.
    "test_integration_parse_newsletter.py": "hermetic tmp project root (root = tmp_path)",
}

#: The two code spellings of the live path.
_RETIRED = re.compile(r'Path\("memory/research\.db"\)|"memory" / "research\.db"')


def _box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, sqlite: bool, duck: bool) -> Path:
    """A fake repo ``memory/`` with the requested stores; points the door at it."""
    monkeypatch.delenv("FINDATA_RESEARCH_DB_PATH", raising=False)
    monkeypatch.delenv("FINDATA_RESEARCH_ENGINE", raising=False)
    legacy = tmp_path / "memory" / "research.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    if sqlite:
        legacy.touch()
    if duck:
        legacy.with_suffix(".duckdb").touch()
    monkeypatch.setattr(db_engine, "DEFAULT_DB_PATH", legacy)
    return legacy


def test_resolves_to_the_duckdb_sibling_even_with_a_stray_legacy_file(tmp_path, monkeypatch):
    legacy = _box(tmp_path, monkeypatch, sqlite=True, duck=True)
    assert live_store_path() == legacy.with_suffix(".duckdb")


def test_resolves_to_the_duckdb_sibling_when_the_legacy_file_is_gone(tmp_path, monkeypatch):
    legacy = _box(tmp_path, monkeypatch, sqlite=False, duck=True)
    assert live_store_path() == legacy.with_suffix(".duckdb")


def test_rollback_box_without_the_converted_store_reads_the_sqlite_store(tmp_path, monkeypatch):
    legacy = _box(tmp_path, monkeypatch, sqlite=True, duck=False)
    assert live_store_path() == legacy


def test_no_store_means_none_not_an_error(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch, sqlite=False, duck=False)
    assert live_store_path() is None


def test_explicit_sqlite_rollback_does_not_redirect(tmp_path, monkeypatch):
    legacy = _box(tmp_path, monkeypatch, sqlite=True, duck=True)
    monkeypatch.setenv("FINDATA_RESEARCH_ENGINE", "sqlite")
    assert live_store_path() == legacy


def test_env_override_wins_when_no_path_is_given(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch, sqlite=True, duck=True)
    other = tmp_path / "elsewhere" / "research.duckdb"
    other.parent.mkdir()
    other.touch()
    monkeypatch.setenv("FINDATA_RESEARCH_DB_PATH", str(other))
    assert live_store_path() == other


def test_env_override_to_a_missing_store_is_none(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch, sqlite=True, duck=True)
    monkeypatch.setenv("FINDATA_RESEARCH_DB_PATH", str(tmp_path / "gone.duckdb"))
    assert live_store_path() is None


def test_require_live_store_skips_when_absent(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch, sqlite=False, duck=False)
    with pytest.raises(pytest.skip.Exception, match="live research store not present"):
        require_live_store()


def test_no_leg_names_the_retired_store_outside_the_allowlist():
    offenders: list[str] = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue  # this file carries the pattern and the hermetic fake repo
        why = _ALLOWED.get(path.name)
        hits = [
            n
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _RETIRED.search(line)
        ]
        if hits and why is None:
            offenders.append(f"{path.name}:{hits[0]}")
    assert not offenders, (
        "live path spelled as the retired memory/research.db — gate through "
        "tests/_live_store.live_store_path() instead (or allowlist with a reason): "
        + ", ".join(offenders)
    )


def test_allowlist_entries_are_real():
    """Every allowlist entry must still exist and still carry the spelling."""
    stale = [
        name
        for name, _ in _ALLOWED.items()
        if not _RETIRED.search((TESTS / name).read_text(encoding="utf-8"))
    ]
    assert not stale, f"allowlist entries without the retired spelling (drop them): {stale}"
