#!/usr/bin/env python3
"""Read-only analytics over the git-tracked Parquet snapshot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.maintenance.analytics.query import connect_snapshots, list_tables, table_rows  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analytics over the Parquet snapshot")
    ap.add_argument("--query", "-q", help="SQL query to run (tables are <side>__<name>)")
    ap.add_argument("--list", action="store_true", help="list available tables")
    ap.add_argument("--stats", action="store_true", help="show row counts")
    args = ap.parse_args(argv)

    con = connect_snapshots()
    try:
        if args.list:
            for t in list_tables(con):
                print(t)
            return 0
        if args.stats:
            for t in list_tables(con):
                print(f"{t}: {table_rows(con, t):,}")
            return 0
        if args.query:
            for row in con.execute(args.query).fetchall():
                print(row)
            return 0
        ap.print_help()
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
