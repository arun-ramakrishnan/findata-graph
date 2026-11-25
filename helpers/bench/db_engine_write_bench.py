#!/usr/bin/env python3
"""db_engine write-path benchmark (S6 perf leg).

Measures the two write shapes the DuckDB cutover made hot:

1. executemany row inserts through the house ``_DuckDBConn`` proxy
   (the pre-set-based per-row cost class);
2. the full-table rewrite used by the corpus-cache fix: DROP/CREATE
   + one INSERT .. SELECT from a registered Arrow relation.

Runs against a scratch DuckDB file (``{scratch}``), not the live
stores. Not wired into ``make perf`` by source — registered as a
``BENCHMARKS`` leg with a quiet-box baseline x2 budget.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.core.db_engine import _DuckDBConn  # noqa: E402
from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="db_engine write-path benchmark")
    p.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("TMPDIR", "/tmp")) / "db_engine_bench.duckdb",  # noqa: S108  # scratch target for the perf leg
    )
    p.add_argument("--rows", type=int, default=10_000)
    args = p.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    raw = duckdb.connect(str(args.db), **memory_cap_config())
    conn = _DuckDBConn(raw)

    conn.execute(
        "CREATE TABLE IF NOT EXISTS bench_rows (id BIGINT, payload TEXT, created_at TIMESTAMP)"
    )
    conn.execute("DELETE FROM bench_rows")
    rows = [(i, f"row-{i}", "2026-11-24 12:00:00") for i in range(args.rows)]

    t0 = time.perf_counter()
    conn.executemany(
        "INSERT INTO bench_rows (id, payload, created_at) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    t_em = time.perf_counter() - t0

    # full-table rewrite: DROP/CREATE + one INSERT .. SELECT from Arrow
    import pyarrow as pa

    t0 = time.perf_counter()
    conn.execute("DROP TABLE IF EXISTS bench_rows")
    conn.execute("CREATE TABLE bench_rows (id BIGINT, payload TEXT, created_at TIMESTAMP)")
    arrow_tbl = pa.Table.from_pylist(
        [
            {"id": i, "payload": f"bulk-{i}", "created_at": "2026-11-24 12:00:00"}
            for i in range(args.rows)
        ]
    )
    try:
        conn.raw.unregister("_bench_bulk")
    except Exception:  # noqa: S110  # best-effort cleanup; arrow table may not exist yet
        pass
    conn.raw.register("_bench_bulk", arrow_tbl)
    conn.execute("INSERT INTO bench_rows SELECT * FROM _bench_bulk")
    conn.commit()
    t_rewrite = time.perf_counter() - t0

    print(f"executemany_inserts_s: {t_em:.4f}")
    print(f"bulk_rewrite_s: {t_rewrite:.4f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
