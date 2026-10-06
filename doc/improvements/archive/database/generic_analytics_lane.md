---
title: "Generic analytics lane over parquet snapshots"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "356"
area: "helpers/maintenance/analytics/"
---

# Generic analytics lane over parquet snapshots

## Motivation

The repo already exports its full data to snapshots/parquet/ (zstd
compressed). The schema is captured in snapshots/parquet/_schema.duckdb.sql.

Currently there is no dedicated analytics lane that lets users query these
snapshots via DuckDB — every analytics query must either:
- Open the live stores (risking locks/contending with writers), or
- Import manually via ad-hoc code

A generic analytics lane that mounts the parquet files and exposes a
clean SQL interface would:
- Give analysts a safe, read-only sandbox (live stores untouched)
- Provide the same interface as the TUI's DB lane (consistency)
- Let the Tauri desktop app use the same layer (shared capability)

## Scope

In scope:
- Add helpers/maintenance/analytics/ with a lightweight DuckDB runner
- Add make analytics target that mounts snapshots + runs ad-hoc queries
- Provide a simple CLI for common analytics (row counts, top-N, etc.)

Out of scope:
- Building a full TUI (that is a separate lane with different constraints)
- Supporting non-parquet sources (SQLite, DuckDB live stores)

## Implementation

### 1. Directory structure

```text
helpers/maintenance/analytics/
├── cli.py           # entrypoint for make analytics
├── query.py         # mounting utilities
└── README.md        # usage guide
```

### 2. Mount logic

```python
# helpers/maintenance/analytics/query.py
import duckdb
from pathlib import Path


def connect_snapshots(repo_root: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Mount all parquet snapshots into DuckDB and return a connection."""
    con = duckdb.connect()
    repo = repo_root or Path(__file__).resolve().parents[3]
    snap = repo / "snapshots" / "parquet"

    # Mount each source table from parquet
    for f in snap.glob("*.parquet"):
        name = f.stem  # e.g. v_node, e_belongs_to, etc.
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{f}')")

    return con
```

### 3. CLI

```python
# helpers/maintenance/analytics/cli.py
import argparse, duckdb
from query import connect_snapshots
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Analytics over snapshots")
    ap.add_argument("--query", "-q", help="SQL query to run")
    ap.add_argument("--list", action="store_true", help="list available tables")
    ap.add_argument("--stats", action="store_true", help="show row counts")
    args = ap.parse_args(argv)

    con = connect_snapshots()

    if args.list:
        for t in con.execute("SHOW TABLES").fetchall():
            print(t[0])
        return

    if args.stats:
        for (t,) in con.execute("SHOW TABLES").fetchall():
            count = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"{t}: {count:,}")
        return

    if args.query:
        for row in con.execute(args.query).fetchall():
            print(row)
        return

    ap.print_help()
```

### 4. Makefile target

```makefile
analytics: ## Run analytics over parquet snapshots (read-only)
    @python3 helpers/maintenance/analytics/cli.py --stats
```

## Acceptance criteria

1. make analytics shows row counts for all source tables
2. make analytics --query "SELECT count(*) FROM e_belongs_to" returns the expected count
3. No live stores are opened (verified by lsof / witr check)
4. Existing tests pass

## Metrics

- Mount time: <0.5 s (for ~15 tables at 100k rows each)
- Query latency: <50 ms for row counts (parquet columnar scans)
- Disk footprint: ~150 MB compressed (the snapshot)

## Timeline

- Mount + CLI: 45 min
- Makefile + docs: 15 min
- Testing: 10 min

Total: ~1 hour
