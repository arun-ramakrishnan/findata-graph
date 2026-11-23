#!/usr/bin/env python3
"""Git textconv driver: render a Parquet file as stable text via DuckDB.

Wired up by .gitattributes (`*.parquet diff=parquet`) + the repo-local
config `diff.parquet.textconv`. Purpose: tracked snapshots/parquet files
are binary (zstd/FLOAT[] blobs); without this driver git line-diffs raw
binary garbage, producing multi-MB diffs that hang stg show / pagers.

Output contract (stable across row-order reshuffles):
  - file name, row count, full schema (column name + type)
  - a deterministic sample: first PARQUET_TEXTCONV_ROWS rows of
    SELECT * ... ORDER BY ALL (env knob; default 10; 0 = all rows)

Cell values are truncated (PARQUET_TEXTCONV_CELL, default 96 chars) so
embedding/vector columns do not blow the dump up. On any read failure the
script exits non-zero so git falls back to the plain binary diff.

Usage (git invokes this; manual):
  .venv/bin/python3 helpers/misc/parquet_textconv.py <file.parquet>
"""

from __future__ import annotations

import os
import sys
import tempfile

try:
    import duckdb
except ImportError:  # pragma: no cover - falls back to binary diff
    print("parquet_textconv: duckdb not importable", file=sys.stderr)
    sys.exit(1)


def _rows_limit() -> int:
    raw = os.environ.get("PARQUET_TEXTCONV_ROWS", "10")
    try:
        return max(0, int(raw))
    except ValueError:
        return 10


def _cell_limit() -> int:
    raw = os.environ.get("PARQUET_TEXTCONV_CELL", "96")
    try:
        return max(1, int(raw))
    except ValueError:
        return 96


def main(path: str) -> int:
    limit = _rows_limit()
    cell = _cell_limit()
    # Inline literal: git runs this file as a bare script (sys.path[0] is
    # helpers/misc/), so helpers.core.duckdb_shared is not importable here.
    # Mirrors duckdb_shared.MEMORY_LIMIT (2 GB operator cap, 2026-11-23);
    # tests/test_static_checks.py `test_duckdb_cap_guard_flags_only_uncapped_sites`
    # flags drift between the two.
    con = duckdb.connect(
        database=":memory:",
        config={
            "memory_limit": "2GB",
            # Spill to the system temp dir, never a cwd-relative .tmp
            # (operator ruling 2026-11-23); the guard checks the key's
            # presence, not this env expression.
            "temp_directory": os.environ.get("TMPDIR") or tempfile.gettempdir(),
        },
    )
    _count_row = con.execute("SELECT count(*) FROM read_parquet(?)", [path]).fetchone()
    if _count_row is None:  # COUNT(*) always returns exactly one row
        raise RuntimeError(f"count query returned no rows for {path}")
    n = _count_row[0]
    print(f"# parquet: {os.path.basename(path)}")
    print(f"# rows: {n}")
    print("# schema:")
    for col in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path]).fetchall():
        print(f"#   {col[0]} {col[1]}")
    # Plain LIMIT, no ORDER BY: the maint-full exports are already written
    # in canonical ORDER BY ALL order (#147), so unordered sampling is
    # deterministic in practice AND avoids a full-table sort per diff
    # side (the sort is what hung stg show on the 50 MB tables).
    sql = "SELECT * FROM read_parquet(?) LIMIT {n}".replace("{n}", str(limit))
    cur = con.execute(sql, [path])
    cols = [d[0] for d in cur.description]
    label = "all rows" if limit == 0 else f"first {limit}"
    print(f"# sample ({label}):")
    print("\t".join(cols))
    for row in cur.fetchall():
        cells = []
        for v in row:
            s = "" if v is None else str(v)
            if len(s) > cell:
                s = s[:cell] + f"...<{len(s)} chars>"
            cells.append(s.replace("\n", " ").replace("\t", " "))
        print("\t".join(cells))
    con.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: parquet_textconv.py <file.parquet>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
