"""S3 ring + citation + F4 export contract (worktree_fresh_trample_guard F5).

The guard predicate itself is pinned in test_stale_apply_guard.py; this
file pins what the finding called out as uncovered: the db_bump_log ring
(append/trim/never-raises), the --check citation shapes (signed delta,
restored-vs-silent distinction, unlogged gap), and the F4 export rule
(apply_* rows never reach db_meta.parquet).
"""

import sqlite3

from helpers.core.db import _log_bump, bump_generation, connect
from helpers.maintenance.snapshot_db import _bump_log_citation, _sqlite_table_arrow


def _db(tmp_path, *, name="ring.db", with_log_table=False, log_rows=(), extra_meta=()):
    """A tmp index with a db_meta generation row, optional ring contents."""
    db = tmp_path / name
    con = connect(db)
    con.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT INTO db_meta (key, value) VALUES ('generation', '100')")
    for k, v in extra_meta:
        con.execute("INSERT INTO db_meta (key, value) VALUES (?, ?)", (k, v))
    if with_log_table:
        con.execute(
            "CREATE TABLE db_bump_log (ts TEXT NOT NULL, caller TEXT NOT NULL,"
            " worktree TEXT NOT NULL, generation INTEGER NOT NULL)"
        )
        con.executemany(
            "INSERT INTO db_bump_log (ts, caller, worktree, generation) VALUES (?, ?, ?, ?)",
            log_rows,
        )
    con.commit()
    con.close()
    return db


# --------------------------------------------------------------------------- #
# Ring: append / trim / never-raises                                           #
# --------------------------------------------------------------------------- #


def test_bump_appends_ring_row_with_caller_and_generation(tmp_path):
    db = _db(tmp_path)
    con = connect(db)
    try:
        assert bump_generation(con) == 101
        row = con.execute("SELECT ts, caller, worktree, generation FROM db_bump_log").fetchone()
    finally:
        con.close()
    assert row is not None
    assert row["generation"] == 101
    assert ".py:" in row["caller"]  # file:line, not "unknown"
    assert (
        row["ts"].startswith("2026") and len(row["ts"]) == 19
    )  # "%Y-%m-%d %H:%M:%S" shape, not just any dashed string


def test_ring_trims_to_last_100(tmp_path):
    db = _db(tmp_path)
    con = connect(db)
    try:
        for _ in range(105):
            _log_bump(con, 1)
        (n,) = con.execute("SELECT COUNT(*) FROM db_bump_log").fetchone()
        (lo,) = con.execute("SELECT MIN(rowid) FROM db_bump_log").fetchone()
    finally:
        con.close()
    assert n == 100
    assert lo == 6  # first five trimmed


def test_log_bump_never_raises():
    """A broken diagnostic path must not turn a good bump into an exception."""

    class _Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("locked")

    _log_bump(_Broken(), 1)  # ty: ignore[invalid-argument-type] — deliberate duck-typed broken conn


# --------------------------------------------------------------------------- #
# Citation shapes                                                              #
# --------------------------------------------------------------------------- #


def test_citation_names_caller_worktree_generation(tmp_path):
    db = _db(
        tmp_path,
        with_log_table=True,
        log_rows=[("2026-10-12 17:52:11", "rebuild_note_search.py:1156", "graph_algos", 99)],
    )
    line = _bump_log_citation(db, live="101", snap="99")
    assert "source moved +2" in line
    assert "2026-10-12 17:52:11" in line
    assert "rebuild_note_search.py:1156" in line
    assert "graph_algos" in line


def test_citation_negative_delta_has_own_sign(tmp_path):
    """F3: snapshot ahead of live (post-restore) must not render '+-N'."""
    db = _db(
        tmp_path,
        with_log_table=True,
        log_rows=[("2026-10-12 17:52:11", "rebuild_note_search.py:1156", "", 101)],
    )
    line = _bump_log_citation(db, live="98", snap="101")
    assert "source moved -3" in line
    assert "+-" not in line


def test_citation_distinguishes_restored_from_trigger_driven(tmp_path):
    """F3: a restored (ring-less) index must not read as a trigger bump."""
    restored = _db(tmp_path, name="restored.db")
    assert "no bump log table" in _bump_log_citation(restored, live="5", snap="4")

    silent = _db(tmp_path, with_log_table=True)
    assert "present but silent" in _bump_log_citation(silent, live="5", snap="4")


def test_citation_flags_unlogged_gap(tmp_path):
    """Cited generation trails live: later bumps went unlogged (triggers)."""
    db = _db(
        tmp_path,
        with_log_table=True,
        log_rows=[("2026-10-12 17:52:11", "rebuild_note_search.py:1156", "", 99)],
    )
    assert "unlogged" in _bump_log_citation(db, live="101", snap="99")


# --------------------------------------------------------------------------- #
# F4: apply_* rows excluded at export, generation kept                         #
# --------------------------------------------------------------------------- #


def test_export_drops_apply_stamp_but_keeps_generation(tmp_path):
    db = _db(
        tmp_path,
        extra_meta=[
            ("apply_head", "deadbeef"),
            ("apply_worktree", "graph_algos"),
            ("apply_ts", "2026-10-12 19:00:00"),
        ],
    )
    con = connect(db, read_only=True)
    try:
        table = _sqlite_table_arrow(con, "db_meta")
    finally:
        con.close()
    keys = table.column("key").to_pylist()
    assert "generation" in keys
    assert not [k for k in keys if k.startswith("apply_")]
