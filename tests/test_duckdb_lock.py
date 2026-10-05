"""helpers/misc/duckdb_lock.py — transient-lock classification + ladder.

Spec: ``doc/improvements/archive/tooling/duckdb_transient_lock_retry.md`` S1+S3 —
the CONJUNCTION is load-bearing (a disjunction would retry missing-file
and permission-denied failures). First call site: the xdist shared-cache
open (xdist_shared_graph_cache S3). The classifier is validated against
the archived failure string the estate actually produced, not a
synthetic one. The S4 cross-process class runs REAL subprocesses: an
RW holder vs a retrying RO opener (the matrix's failing row), and an
io.lock EX window vs a queued reader (coordination, not retry).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers.misc.duckdb_lock import (
    connect_with_lock_retry,
    is_transient_lock_error,
    lock_retry_delay,
)

# verbatim shape of the archived gate_xdist_phase2 failure
ARCHIVED = 'IO Error: Could not set lock on file "/m/graph.duckdb": Conflicting lock is held'


class TestClassifier:
    def test_archived_failure_string_matches(self):
        assert is_transient_lock_error(ARCHIVED)

    @pytest.mark.parametrize(
        "msg",
        [
            # file-open phrase, no lock phrase
            'IO Error: Permission denied: cannot open file "/m/x.duckdb"',
            # lock phrase, no file-open phrase
            "Conflicting lock is held",
            # neither
            "No such file or directory",
            "",
        ],
    )
    def test_half_matches_are_not_transient(self, msg):
        assert not is_transient_lock_error(msg)


def test_ladder_is_pinned():
    """50→800 ms then give up; the schedule must not drift unnoticed."""
    assert [lock_retry_delay(i) for i in range(7)] == [
        0.05,
        0.1,
        0.2,
        0.4,
        0.8,
        None,
        None,
    ]


def test_retry_then_success():
    calls = {"n": 0}
    slept: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(ARCHIVED)
        return "ok"

    assert connect_with_lock_retry(flaky, sleep=slept.append) == "ok"
    assert calls["n"] == 3
    assert slept == [0.05, 0.1]


def test_non_lock_error_raises_immediately():
    """The conjunction's teeth: a missing file must fail fast, not burn
    1.55 s of ladder on an unresolvable condition."""
    slept: list[float] = []

    def boom():
        raise OSError('No such file or directory: cannot open file "/m/x"')

    with pytest.raises(OSError, match="No such file"):
        connect_with_lock_retry(boom, sleep=slept.append)
    assert slept == []


def test_exhaustion_surfaces_the_original_error():
    slept: list[float] = []

    def always():
        raise OSError(ARCHIVED)

    with pytest.raises(OSError, match="Conflicting lock"):
        connect_with_lock_retry(always, sleep=slept.append)
    assert slept == [0.05, 0.1, 0.2, 0.4, 0.8]


# --------------------------------------------------------------------------- #
# S4: the matrix measured, not the prose — real cross-process conflicts        #
# --------------------------------------------------------------------------- #

_RW_HOLDER = """
import duckdb, sys, time
con = duckdb.connect(sys.argv[1])  # read-write holder
con.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
print("held", flush=True)
time.sleep(float(sys.argv[2]))
con.close()
"""

_EX_HOLDER = """
import duckdb, sys, time
from helpers.misc.duckdb_lock import io_lock
with io_lock(sys.argv[1], exclusive=True):
    con = duckdb.connect(sys.argv[1])  # the long writer's window
    con.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
    con.close()  # DuckDB closed BEFORE the flock releases
"""


def _spawn_hold(db, script: str, seconds: float) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", script, str(db), str(seconds)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )


class TestCrossProcess:
    def test_rw_holder_ro_opener_retries_then_succeeds(self, tmp_path):
        """The matrix's failing row, measured: one process holds the file
        RW, a second opens RO and must SUCCEED after bounded backoff —
        not raise. Fails against pre-S2 code (the open raises on the
        first attempt)."""
        import duckdb

        duckdb_path = tmp_path / "graph.duckdb"
        duckdb.connect(str(duckdb_path)).close()  # file must exist
        proc = _spawn_hold(duckdb_path, _RW_HOLDER, 0.5)
        try:
            assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
            t0 = time.perf_counter()
            con = connect_with_lock_retry(lambda: duckdb.connect(str(duckdb_path), read_only=True))
            elapsed = time.perf_counter() - t0
            try:
                count_row = con.execute("SELECT COUNT(*) FROM t").fetchone()
                assert count_row is not None and count_row[0] == 0
            finally:
                con.close()
            assert elapsed >= 0.4  # the ladder actually waited out the holder
        finally:
            proc.wait(timeout=30)

    def test_reader_blocks_on_io_lock_window_then_succeeds(self, tmp_path):
        """The S3 case, measured: a reader arriving while a long writer
        holds LOCK_EX must BLOCK and then succeed — the behaviour that
        distinguishes coordination from retry (and fails against a
        ladder-only world, since the 1.55 s cap cannot cover the hold)."""
        import duckdb

        from helpers.misc.duckdb_lock import open_read_only

        duckdb_path = tmp_path / "convo.duckdb"
        duckdb.connect(str(duckdb_path)).close()
        proc = _spawn_hold(duckdb_path, _EX_HOLDER, 2.0)
        try:
            assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
            t0 = time.perf_counter()
            con = open_read_only(duckdb_path)  # queues on <db>.io.lock
            elapsed = time.perf_counter() - t0
            try:
                count_row = con.execute("SELECT COUNT(*) FROM t").fetchone()
                assert count_row is not None and count_row[0] == 0
            finally:
                con.close()
            assert elapsed >= 1.5  # queued out the writer's window, not retried
        finally:
            proc.wait(timeout=30)
