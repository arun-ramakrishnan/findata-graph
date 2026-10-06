#!/usr/bin/env python3
"""Mount the Parquet snapshot as DuckDB views — read-only sandbox."""

from __future__ import annotations

from pathlib import Path

import duckdb

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOTS = _REPO_ROOT / "snapshots" / "parquet"
SIDES = ("duckdb", "sqlite", "sources")


def connect_snapshots(repo_root: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Mount every snapshot table as a ``<side>__<table>`` view."""
    con = duckdb.connect()
    root = Path(repo_root) / "snapshots" / "parquet" if repo_root else DEFAULT_SNAPSHOTS
    for side in SIDES:
        side_dir = root / side
        if not side_dir.is_dir():
            continue
        for f in sorted(side_dir.glob("*.parquet")):
            view = f"{side}__{f.stem}"
            con.execute(
                f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{f.as_posix()}')"  # noqa: S608  # view/path from schema constants
            )
    return con


def list_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [row[0] for row in con.execute("SHOW TABLES").fetchall()]


def table_rows(con: duckdb.DuckDBPyConnection, view: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {view}").fetchone()  # noqa: S608  # view name from parquet stems
    return row[0] if row else 0
