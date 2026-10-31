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


def test_size_watch_ratio_contract():
    """Advisory growth watch vs the 2026-09-29 filing baseline
    (production_db_copy_audit S3, landed 2026-10-19): ACT at 2×, warn at
    1.5×, quiet below that; stores with no baseline get one prompt line;
    and the row shape matches what run_sqlite/run_duckdb emit
    (after_bytes post-sweep, bytes on the census-only/duckdb paths)."""
    mib = 1048576
    rows = [
        # 384M vs 184.6M filing = 2.08x — the live ACT case.
        {
            "store": "memory/embed_store.db",
            "engine": "sqlite",
            "after_bytes": int(384 * mib),
        },
        # 257M vs 102.0M = 2.52x — the other live ACT case.
        {
            "store": "memory/convo_search_fts.db",
            "engine": "duckdb",
            "bytes": int(257 * mib),
        },
        # 40M vs 24.7M = 1.62x — warn band.
        {
            "store": "memory/doc_search.db",
            "engine": "sqlite",
            "after_bytes": int(40 * mib),
        },
        # ~1.0x — quiet.
        {"store": "memory/corpus.db", "engine": "sqlite", "after_bytes": int(54 * mib)},
        # No baseline — one prompt line, not a crash, not silence.
        {"store": "memory/git_search.db", "engine": "sqlite", "bytes": int(26 * mib)},
        # No baseline AND 0 bytes — fully silent.
        {"store": "memory/data/sources.duckdb", "engine": "duckdb", "bytes": 0},
    ]
    lines = ss.size_watch(rows)
    assert sum("embed_store" in ln for ln in lines) == 1
    assert "ACT" in next(ln for ln in lines if "embed_store" in ln)
    assert "ACT" in next(ln for ln in lines if "convo_search_fts" in ln)
    assert "warn" in next(ln for ln in lines if "doc_search" in ln)
    assert not any("corpus" in ln for ln in lines)
    assert "no filing baseline" in next(ln for ln in lines if "git_search" in ln)
    assert not any("sources.duckdb" in ln for ln in lines)
    # 0-byte stores with a baseline stay silent too (nothing measured).
    assert not ss.size_watch([{"store": "memory/corpus.db", "engine": "sqlite", "bytes": 0}])
    # The watch is advisory by contract: it must never change the
    # exit-code inputs.
    assert ss.exit_code(rows) == 0


def test_size_watch_primaries_absent():
    """Primaries are db_maint-owned and never sweep rows — the baseline
    dict must not carry entries for them (dead keys would silently
    promise coverage the sweep does not have)."""
    assert "memory/research.db" not in ss._FILING_MB
    assert "memory/graph.duckdb" not in ss._FILING_MB
