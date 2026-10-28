#!/usr/bin/env python3
"""
Rust quack-query wrapper — mirrors helpers/core/embed_cache.py pattern.

Protocol: JSON stdin/stdout, protocol=1.
Falls back to Python duckdb on any defect.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


_RUST_QUACK_QUERY_BIN = os.environ.get(
    "FINDATA_RUST_QUACK_QUERY_BIN",
    str(
        Path(__file__).resolve().parents[2] / "Rust" / "target" / "release" / "quack-query-wrapper"
    ),
)

_RUST_QUACK_QUERY_TIMING = os.environ.get("FINDATA_RUST_QUACK_QUERY_TIMING") == "1"
_RUST_QUACK_QUERY_WARNED = False


def _warn_once(msg: str) -> None:
    global _RUST_QUACK_QUERY_WARNED
    if not _RUST_QUACK_QUERY_WARNED:
        print(f"[quack_query] {msg}", file=sys.stderr)
        _RUST_QUACK_QUERY_WARNED = True


def _parse_response(  # noqa: C901 — linear guard-ladder validator; one raise per defect class
    response: dict, expected_rows: int | None = None
) -> dict:
    """Validate response shape; raises ValueError on any defect."""
    if response.get("protocol") != 1:
        raise ValueError("unsupported response protocol")

    columns = response.get("columns", [])
    rows = response.get("rows", [])
    row_count = response.get("row_count", 0)

    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("columns/rows must be lists")
    if row_count != len(rows):
        raise ValueError(f"row_count {row_count} != len(rows) {len(rows)}")
    if expected_rows is not None and row_count != expected_rows:
        raise ValueError(f"expected {expected_rows} rows, got {row_count}")

    for i, col in enumerate(columns):
        if not isinstance(col, dict) or "name" not in col or "type_name" not in col:
            raise ValueError(f"invalid column {i}: {col}")

    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError(f"row {i} length mismatch: {len(row)} vs {len(columns)}")
        for j, val in enumerate(row):
            if val is not None and not isinstance(val, (bool, int, float, str)):
                raise ValueError(f"row {i} col {j} invalid type: {type(val)}")

    return {"columns": columns, "rows": rows, "row_count": row_count}


def _rust_quack_query(
    uri: str,
    token: str,
    sql: str,
    params: list[Any],
    expected_rows: int | None = None,
) -> dict | None:
    """Try Rust quack-query; return None to signal Python fallback."""
    bin_path = Path(_RUST_QUACK_QUERY_BIN)
    if not bin_path.is_file():
        _warn_once(f"Rust binary not found at {bin_path}, falling back to Python")
        return None

    request = {
        "protocol": 1,
        "uri": uri,
        "token": token,
        "sql": sql,
        "params": params,
    }

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 — repo-built first-party binary at a constant path; stdin payload is self-constructed
            [str(bin_path)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        dt = time.perf_counter() - t0
        if _RUST_QUACK_QUERY_TIMING:
            print(f"[quack_query] Rust call took {dt * 1000:.1f}ms", file=sys.stderr)

        if proc.returncode != 0:
            _warn_once(
                f"Rust binary exited with {proc.returncode}: {proc.stderr[:200]}, falling back"
            )
            return None

        response = json.loads(proc.stdout)
        return _parse_response(response, expected_rows)

    except subprocess.TimeoutExpired:
        _warn_once("Rust binary timed out, falling back to Python")
        return None
    except (json.JSONDecodeError, ValueError) as e:
        _warn_once(f"Rust response invalid: {e}, falling back")
        return None
    except Exception as e:
        _warn_once(f"Rust call failed: {e}, falling back")
        return None


def quack_query(
    uri: str,
    token: str,
    sql: str,
    params: list[Any] | None = None,
    expected_rows: int | None = None,
) -> dict:
    """
    Public API: runs query via Rust if available, else Python duckdb.
    Returns dict with columns, rows, row_count.
    """
    if params is None:
        params = []

    # Try Rust first
    result = _rust_quack_query(uri, token, sql, params, expected_rows)
    if result is not None:
        return result

    # Python fallback
    import duckdb

    conn = duckdb.connect()

    # Inline params (quack wire drops bound params)
    def sql_lit(v: Any) -> str:
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        raise TypeError(f"unsupported param type: {type(v)}")

    inlined = sql
    for i, p in enumerate(params):
        inlined = inlined.replace("?", sql_lit(p), 1)

    secret_sql = f"CREATE SECRET (TYPE quack, TOKEN '{token.replace("'", "''")}', SCOPE '{uri}')"
    attach_sql = f"ATTACH '{uri}' AS g (TYPE quack)"
    conn.execute(secret_sql)
    conn.execute(attach_sql)
    rows = conn.execute(inlined).fetchall()
    columns = (
        [{"name": d[0], "type_name": str(d[1])} for d in conn.description]
        if conn.description
        else []
    )

    return {"columns": columns, "rows": rows, "row_count": len(rows)}


if __name__ == "__main__":
    # CLI test
    import sys

    if len(sys.argv) < 4:
        print("Usage: quack_query.py <uri> <token> <sql> [param...]", file=sys.stderr)
        sys.exit(1)
    uri, token, sql = sys.argv[1], sys.argv[2], sys.argv[3]
    params = sys.argv[4:]
    result = quack_query(uri, token, sql, params)
    print(json.dumps(result, default=str))
