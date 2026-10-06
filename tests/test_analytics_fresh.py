#!/usr/bin/env python3
"""analytics_fresh tests — drift verdicts on synthetic fixtures only.

No live harness paths are touched: every case builds tmp sqlite/duckdb
stores and a tmp prime dir, then asserts the check verdict. The
live-store shakedown (`make analytics-fresh`, then APPLY=1) is manual.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "helpers"))

from helpers.misc import analytics_fresh as af  # noqa: E402


def _ms_local(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d).timestamp() * 1000)


def _make_zcode(path: Path, day: tuple[int, int, int]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE model_usage (started_at BIGINT)")
    con.execute("INSERT INTO model_usage VALUES (?)", (_ms_local(*day),))
    con.commit()
    con.close()


def _make_opencode(path: Path, day: tuple[int, int, int]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE session (time_created BIGINT)")
    con.execute("CREATE TABLE session_v2 (time_created BIGINT)")
    con.execute("INSERT INTO session VALUES (?)", (_ms_local(*day),))
    con.commit()
    con.close()


def _make_prime(directory: Path, day: tuple[int, int, int]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "sess.jsonl"
    p.write_text("{}\n")
    ts = datetime(*day, 12, tzinfo=UTC).timestamp()
    os.utime(p, (ts, ts))


def _make_traces(path: Path, rows: list[tuple[str, str]]) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE fact_model_request (source TEXT, day DATE)")
    con.execute("CREATE TABLE fact_model_step (source TEXT, day DATE)")
    for source, day in rows:
        table = "fact_model_step" if source == "zcode_rollout" else "fact_model_request"
        con.execute(f"INSERT INTO {table} VALUES (?, ?)", (source, day))  # noqa: S608  # constants + fixed columns, not user input
    con.close()


def _make_usage(path: Path, rows: list[tuple[str, str]]) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE fact_usage (source TEXT, day DATE)")
    for source, day in rows:
        con.execute("INSERT INTO fact_usage VALUES (?, ?)", (source, day))
    con.close()


def _fixture(tmp_path: Path, hday: str, sday: str) -> tuple[Path, Path, Path, Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    yd, md, dd = (int(x) for x in hday.split("-"))
    ys, ms, ds = (int(x) for x in sday.split("-"))
    zcode, opencode = tmp_path / "db.sqlite", tmp_path / "oc.db"
    prime = tmp_path / "prime"
    rollout = tmp_path / "rollout"
    traces, usage = tmp_path / "traces.duckdb", tmp_path / "usage.duckdb"
    _make_zcode(zcode, (yd, md, dd))
    _make_opencode(opencode, (yd, md, dd))
    _make_prime(prime, (yd, md, dd))
    rollout.mkdir(parents=True, exist_ok=True)
    (rollout / "model-io-sess-x.jsonl").write_text(
        json.dumps({"requestId": "r1", "startedAt": f"{hday}T12:00:00.000Z"}) + "\n"
    )
    _make_traces(
        traces, [("zcode", sday), ("opencode", sday), ("prime", sday), ("zcode_rollout", sday)]
    )
    _make_usage(usage, [("prime-rlm", sday), ("opencode", sday), ("zai", sday)])
    return zcode, opencode, prime, rollout, traces, usage


def test_fresh_when_frontiers_match(tmp_path: Path) -> None:
    zcode, opencode, prime, rollout, traces, usage = _fixture(tmp_path, "2026-09-20", "2026-09-20")
    harness = af.harness_frontiers(zcode, opencode, prime, rollout)
    assert harness["prime"] == date(2026, 9, 20)
    assert harness["rollout"] == date(2026, 9, 20)
    rows, drift = af.check(harness, af.store_frontiers(traces, usage))
    assert not drift
    assert all(r[4] == "fresh" for r in rows)


def test_stale_on_completed_day_gap(tmp_path: Path) -> None:
    zcode, opencode, prime, rollout, traces, usage = _fixture(tmp_path, "2026-09-20", "2026-09-18")
    rows, drift = af.check(
        af.harness_frontiers(zcode, opencode, prime, rollout),
        af.store_frontiers(traces, usage),
    )
    assert drift
    assert sum(1 for r in rows if r[4] == "STALE") == 6


def test_same_day_lag_is_not_drift(tmp_path: Path) -> None:
    today = date.today().isoformat()
    zcode, opencode, prime, rollout, traces, usage = _fixture(tmp_path, today, "2020-01-01")
    rows, drift = af.check(
        af.harness_frontiers(zcode, opencode, prime, rollout),
        af.store_frontiers(traces, usage),
    )
    assert not drift
    assert all(r[4] == "fresh" for r in rows)


def test_missing_harness_is_note_not_drift(tmp_path: Path) -> None:
    zcode, opencode, prime, rollout, traces, usage = _fixture(tmp_path, "2026-09-20", "2026-09-20")
    rows, drift = af.check(
        af.harness_frontiers(zcode, opencode, prime, tmp_path / "no-rollout"),
        af.store_frontiers(traces, usage),
    )
    assert not drift
    assert [r[4] for r in rows if r[1] == "zcode_rollout"] == ["no-harness"]


def test_missing_store_is_drift_and_zai_never_verdict(tmp_path: Path) -> None:
    zcode, opencode, prime, rollout, traces, usage = _fixture(tmp_path, "2026-09-20", "2026-09-20")
    missing = tmp_path / "nope.duckdb"
    rows, drift = af.check(
        af.harness_frontiers(zcode, opencode, prime, rollout),
        af.store_frontiers(missing, usage),
    )
    assert drift
    assert all(r[4] == "STALE" for r in rows if r[0] == "agent_traces")
    assert "zai" not in {r[1] for r in rows}


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fresh = _fixture(tmp_path, "2026-09-20", "2026-09-20")
    stale = _fixture(tmp_path / "s", "2026-09-20", "2026-09-18")

    def _args(fx: tuple) -> list[str]:
        return [
            "--zcode-db",
            str(fx[0]),
            "--opencode-db",
            str(fx[1]),
            "--prime-dir",
            str(fx[2]),
            "--rollout-dir",
            str(fx[3]),
            "--agent-traces",
            str(fx[4]),
            "--model-usage",
            str(fx[5]),
        ]

    assert af.main(_args(fresh)) == 0
    capsys.readouterr()
    assert af.main(_args(stale)) == 1
