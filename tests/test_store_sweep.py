"""Store-sweep unit tests — per-store isolation + exit-code contract.

Covers the 2026-10-14 deep-audit fixes (store_wide_reclaim S2.0): a busy
sqlite store is a ``skipped`` row, never a crash (A1); a corrupt store is
an ``error`` row that fails the run; busy skips never fail the exit code,
but genuine errors and a non-ok ``quick_check`` do (A3 — the #380
anti-pattern is reporting success while having done less than claimed).
SQLite takes NO io_lock: WAL + busy_timeout bound every conflict (ruling
recorded in the module docstring).
"""

import sqlite3

import pytest

from helpers.maintenance import store_sweep as ss


@pytest.fixture
def sweep_repo(tmp_path, monkeypatch):
    """REPO retarget: run_sqlite resolves stores relative to REPO."""
    monkeypatch.setattr(ss, "REPO", tmp_path)
    (tmp_path / "memory").mkdir()
    return tmp_path


def _seed(p, rows=2000):
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE t (x TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [("pad" * 100,) for _ in range(rows)])
    con.commit()
    con.execute("DELETE FROM t")
    con.commit()
    con.close()


def test_busy_store_skipped_not_crash(sweep_repo):
    """A plain (non-flock) writer holding the store must yield a skipped
    row — the pre-fix sweep died here with OperationalError (audit A1):
    one busy store killed main() and every later store went unswept."""
    p = sweep_repo / "memory" / "busy.db"
    _seed(p)
    holder = sqlite3.connect(str(p))
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES ('blocker')")
    try:
        row = ss.run_sqlite(p, dry_run=False, lg=ss._logger())
        assert row["action"] == "skipped (database is locked)"
    finally:
        holder.rollback()
        holder.close()


def test_absent_store_is_absent_row_not_crash(sweep_repo):
    """A registered-but-missing store yields an ``absent`` row, never a raise —
    fresh/partial checkouts legitimately lack generated sidecars (review M1:
    the pre-fix sweep died here with FileNotFoundError from unguarded stat)."""
    p = sweep_repo / "memory" / "gone.db"
    row = ss.run_sqlite(p, dry_run=True, lg=ss._logger())
    assert row["action"] == "absent"
    assert ss.exit_code([row]) == 0


def test_dirty_store_reclaimed_clean_exit(sweep_repo):
    """The acting path: freelist reclaimed, quick_check ok, exit-code 0."""
    p = sweep_repo / "memory" / "dirty.db"
    _seed(p)
    row = ss.run_sqlite(p, dry_run=False, lg=ss._logger())
    assert row["action"] in ("vacuum", "incremental", "incremental+vacuum")
    assert row["freelist_after"] == 0
    assert row["quick"] == "ok"
    assert ss.exit_code([row]) == 0


def test_corrupt_store_is_error_row(sweep_repo):
    """A non-database file is an ``error`` row, not a crash and not a
    skip — it must fail the run's exit code."""
    p = sweep_repo / "memory" / "corrupt.db"
    p.write_bytes(b"not a database at all" * 64)
    row = ss.run_sqlite(p, dry_run=False, lg=ss._logger())
    assert row["action"].startswith("error")
    assert ss.exit_code([row]) == 1


def test_exit_code_contract():
    ok = {"engine": "sqlite", "action": "vacuum", "quick": "ok"}
    dry = {"engine": "sqlite", "action": "census-only"}
    busy = {"engine": "sqlite", "action": "skipped (database is locked)"}
    bad_quick = {"engine": "sqlite", "action": "vacuum", "quick": "row 3 malformed"}
    err = {"engine": "duckdb", "action": "error (InvalidInputException)"}
    # Busy skips are the designed degradation — never a failure.
    assert ss.exit_code([ok, dry, busy]) == 0
    # An integrity alarm must not pass silently; nor a per-store error.
    assert ss.exit_code([bad_quick]) == 1
    assert ss.exit_code([err]) == 1


def test_unregistered_stores_cross_check(sweep_repo, monkeypatch):
    """The registry-vs-disk cross-check behind the "every store" claim:
    a store file outside the registry (and outside the primaries) is
    flagged for adoption — never silently swept (#380)."""
    (sweep_repo / "memory" / "known.db").write_bytes(b"x")
    (sweep_repo / "memory" / "stray.db").write_bytes(b"x")
    (sweep_repo / "memory" / "research.db").write_bytes(b"x")
    monkeypatch.setattr(ss, "SIDECAR_SQLITE", ("memory/known.db",))
    monkeypatch.setattr(ss, "SIDECAR_DUCKDB", ())
    assert [p.name for p in ss.unregistered_stores()] == ["stray.db"]
