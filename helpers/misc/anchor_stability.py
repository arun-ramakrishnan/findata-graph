#!/usr/bin/env python3
"""S2 anchor-stability census: which identity columns survive a full
re-ingest unchanged (harness-native) vs re-number on rebuild.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

REPO = Path("/home/arun/Research/findata-graph")
DB_PATH = REPO / "memory" / "data" / "agent_traces.duckdb"
AGENT_TRACES = REPO / "bench_data" / "code" / "agent_traces.py"

ID_COLS = {
    "dim_session": ("source", "session_id"),
    "fact_turn": ("source", "turn_id"),
    "fact_tool_call": ("source", "tool_call_id"),
    "fact_model_request": ("source", "request_id"),
    "fact_event": ("source", "span_id"),
    "fact_file_edit": ("source", "snapshot_hash"),
}


def snapshot(con):
    snap = {}
    for table, cols in ID_COLS.items():
        try:
            rows = con.execute(
                f"SELECT {cols[0]}, {cols[1]} FROM {table}"  # noqa: S608  # fixed ID_COLS, not user input
            ).fetchall()
            snap[(table, cols[1])] = {f"{r[0]}|{r[1]}" for r in rows}
        except duckdb.CatalogException:
            pass
    return snap


def load_all():
    subprocess.run(  # noqa: S603  # argv is repo-owned, no untrusted input
        [sys.executable, str(AGENT_TRACES), "load", "all", "--full"],
        cwd=REPO,
        check=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
    )


def run_census(db=DB_PATH):
    con = duckdb.connect(str(db))
    before = snapshot(con)
    con.close()
    load_all()
    con = duckdb.connect(str(db))
    after = snapshot(con)
    con.close()
    rows = []
    for table, cols in ID_COLS.items():
        col = cols[1]
        kept = before.get((table, col), set()) & after.get((table, col), set())
        total = len(before.get((table, col), set()))
        rate = len(kept) / total if total else None
        rows.append((table, col, total, len(after.get((table, col), set())), len(kept), rate))
    return before, after, rows


if __name__ == "__main__":
    before, after, rows = run_census()
    print("| table | col | before | after | kept | match_rate |")
    print("|---|---|---|---|---|---|")
    for table, col, total, after_n, kept, rate in rows:
        if total == 0:
            continue
        print(f"| {table} | {col} | {total} | {after_n} | {kept} | {rate:.2%} |")
    print()
    stable = [(t, c) for t, c, total, a, k, r in rows if r is not None and r == 1.0]
    print("STABLE (legal anchor set per S2):", stable)
