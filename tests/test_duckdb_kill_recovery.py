"""Kill-mid-write recovery tests for synthetic DuckDB stores only.

Covers the core hazard: our DuckDB writers can be SIGKILLed before
their writes finish, and the next open must recover to a sane committed
state — not a half-applied, quietly corrupt store.

No production DB copies, no live stores; only synthetic schemas lifted
from our own DDL constants.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TOTAL_ROWS = 200
BATCH_ROWS = 20
SLEEP_PER_BATCH_S = 0.05
REOPEN_GUARD_MS = 2000


def _row_hash(rows: list[tuple]) -> str:
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: tuple(str(x) for x in r)):
        h.update(str(row).encode())
    return h.hexdigest()


def _spawn_writer(
    shape: str, mode: str, db: Path, marker: Path, schema_sql: Path
) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WRITER,
            shape,
            mode,
            str(db),
            str(marker),
            str(schema_sql),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


_WRITER = r"""
import sys, time, duckdb
from pathlib import Path

shape = sys.argv[1]
mode = sys.argv[2]
db = Path(sys.argv[3])
marker = Path(sys.argv[4])
schema_sql = Path(sys.argv[5])

con = duckdb.connect(str(db))
con.execute(schema_sql.read_text())

total = 200
batch = 20
sleep_per = 0.05

def convo_rows(lo, hi):
    return [
        ("test", f"s{i}", f"p{i}", f"/tmp/{i}.parquet", i, None, "user", "agent", "model", "text", 10, f"snippet-{i}", None)
        for i in range(lo, hi)
    ]

def fact_turn_rows(lo, hi):
    return [
        ("kill", "s0", f"t{i}", None, None, 10, 1, 0, 0, 0, 42, False, "ok")
        for i in range(lo, hi)
    ]

def insert_rows(lo, hi):
    if shape == "convo_search":
        rows = convo_rows(lo, hi)
        con.executemany(
            "INSERT OR IGNORE INTO convo_search (harness, session_id, part_id, file_path, row_no, ts, role, agent, model, part_type, text_len, snippet, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    else:
        rows = fact_turn_rows(lo, hi)
        con.executemany(
            "INSERT OR IGNORE INTO fact_turn (source, session_id, turn_id, ts, day, duration_ms, model_requests, model_retries, tool_calls, tool_errors, tokens, context_exceeded, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

marker.write_text(f"{mode}\n")
if mode == "autocommit":
    for lo in range(0, total, batch):
        hi = min(lo + batch, total)
        insert_rows(lo, hi)
        con.commit()
        time.sleep(sleep_per)
else:
    con.execute("BEGIN")
    for lo in range(0, total, batch):
        hi = min(lo + batch, total)
        insert_rows(lo, hi)
        time.sleep(sleep_per)
    con.execute("COMMIT")
marker.unlink(missing_ok=True)
con.close()
"""


def _schema_sql(shape: str) -> str:
    if shape == "convo_search":
        from helpers.maintenance.rebuild_convo_search import CONVO_SEARCH_DDL

        return CONVO_SEARCH_DDL
    from helpers.analytics.agent_traces import SCHEMA

    return SCHEMA


def _prepare_db(db: Path, shape: str) -> None:
    import duckdb

    con = duckdb.connect(str(db))
    try:
        con.execute(_schema_sql(shape))
        con.commit()
    finally:
        con.close()


def _reopen(db: Path, shape: str) -> tuple[int, int, float]:
    import duckdb

    t0 = time.perf_counter()
    con = duckdb.connect(str(db), read_only=True)
    try:
        if shape == "convo_search":
            rows = con.execute(
                "SELECT harness, part_id, session_id, file_path, row_no, ts, role, agent, model, part_type, text_len, snippet, embedding FROM convo_search"
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT source, session_id, turn_id, ts, day, duration_ms, model_requests, model_retries, tool_calls, tool_errors, tokens, context_exceeded, status FROM fact_turn"
            ).fetchall()
        if shape == "convo_search":
            dupe_row = con.execute(
                "SELECT COUNT(*) FROM (SELECT harness, part_id FROM convo_search GROUP BY harness, part_id HAVING COUNT(*) > 1)"
            ).fetchone()
        else:
            dupe_row = con.execute(
                "SELECT COUNT(*) FROM (SELECT source, session_id, turn_id FROM fact_turn GROUP BY source, session_id, turn_id HAVING COUNT(*) > 1)"
            ).fetchone()
        assert dupe_row is not None
        dupe_count = dupe_row[0]
        return len(rows), dupe_count, (time.perf_counter() - t0) * 1000
    finally:
        con.close()


def _assert_no_dupes(db: Path, shape: str) -> None:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        if shape == "convo_search":
            dupe_row = con.execute(
                "SELECT COUNT(*) FROM (SELECT harness, part_id FROM convo_search GROUP BY harness, part_id HAVING COUNT(*) > 1)"
            ).fetchone()
        else:
            dupe_row = con.execute(
                "SELECT COUNT(*) FROM (SELECT source, session_id, turn_id FROM fact_turn GROUP BY source, session_id, turn_id HAVING COUNT(*) > 1)"
            ).fetchone()
        assert dupe_row is not None
        dupes = dupe_row[0]
        assert dupes == 0, f"duplicate business keys in {db}"
    finally:
        con.close()


def _rows(db: Path, shape: str) -> list[tuple]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        if shape == "convo_search":
            return con.execute(
                "SELECT harness, session_id, part_id, file_path, row_no, ts, role, agent, model, part_type, text_len, snippet, embedding FROM convo_search ORDER BY row_no"
            ).fetchall()
        return con.execute(
            "SELECT source, session_id, turn_id, ts, day, duration_ms, model_requests, model_retries, tool_calls, tool_errors, tokens, context_exceeded, status FROM fact_turn ORDER BY turn_id"
        ).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("shape", ["convo_search", "agent_traces"])
def test_kill_mid_write_reopens_cleanly(tmp_path: Path, shape: str) -> None:
    schema_sql = tmp_path / f"{shape}.sql"
    schema_sql.write_text(_schema_sql(shape))
    mid_write_kills = 0
    for idx, delay in enumerate([0.05, 0.15, 0.30, 0.50, 0.80]):
        mode = "autocommit" if idx % 2 == 0 else "staged"
        db = tmp_path / f"{shape}-{mode}-{idx}.duckdb"
        marker = tmp_path / f"{shape}-{mode}-{idx}.writing"
        _prepare_db(db, shape)
        proc = _spawn_writer(shape, mode, db, marker, schema_sql)
        time.sleep(delay)
        proc.kill()
        proc.wait(timeout=10)
        rows, dupes, ms = _reopen(db, shape)
        _assert_no_dupes(db, shape)
        assert dupes == 0
        assert ms < REOPEN_GUARD_MS
        assert rows <= TOTAL_ROWS
        if marker.exists():
            mid_write_kills += 1
            if mode == "staged":
                assert rows in (0, TOTAL_ROWS)
    assert mid_write_kills >= 1, "no SIGKILL landed while the writer was mid-write"


@pytest.mark.parametrize("shape", ["convo_search", "agent_traces"])
def test_rerun_converges_after_kill(tmp_path: Path, shape: str) -> None:
    baseline = tmp_path / f"{shape}-baseline.duckdb"
    baseline_marker = tmp_path / f"{shape}-baseline.writing"
    schema_sql = tmp_path / f"{shape}.sql"
    schema_sql.write_text(_schema_sql(shape))
    subprocess.run(
        [
            sys.executable,
            "-c",
            _WRITER,
            shape,
            "autocommit",
            str(baseline),
            str(baseline_marker),
            str(schema_sql),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        timeout=30,
        check=True,
    )
    killed = tmp_path / f"{shape}-killed.duckdb"
    marker = tmp_path / f"{shape}-killed.writing"
    _prepare_db(killed, shape)
    proc = _spawn_writer(shape, "autocommit", killed, marker, schema_sql)
    time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=10)
    subprocess.run(
        [
            sys.executable,
            "-c",
            _WRITER,
            shape,
            "autocommit",
            str(killed),
            str(marker),
            str(schema_sql),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        timeout=30,
        check=True,
    )
    assert _row_hash(_rows(killed, shape)) == _row_hash(_rows(baseline, shape))
