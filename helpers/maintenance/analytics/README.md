# Analytics over the Parquet snapshot

Read-only SQL lane over the git-tracked snapshot
(`snapshots/parquet/{duckdb,sqlite,sources}/*.parquet`). Every table is
mounted as a DuckDB view named `<side>__<stem>` — no live store is
opened, so this is a safe sandbox for ad-hoc analysis.

## Usage

```bash
make analytics-parquet                      # row counts for every table
python3 helpers/maintenance/analytics/cli.py --list   # list view names
python3 helpers/maintenance/analytics/cli.py --stats  # row counts
python3 helpers/maintenance/analytics/cli.py -q "SELECT count(*) FROM duckdb__e_group"
```

`query.py:connect_snapshots()` returns the in-memory DuckDB connection;
`list_tables()` / `table_rows()` enumerate and count views.
