#!/usr/bin/env python3
"""Typed trace contracts + per-harness parser registry (S1), validated ingest
(S2), and load-outcome counters (S2/S4).

Frozen dataclass fields per table mirror the star schema exactly (the loader
schema lives here; the DuckDB CREATE TABLEs in agent_traces.py are the source
of truth for column order/types and are kept in sync by the parity gate).

Parsers return {table: [{col: value, ...}]} — a row is a dict keyed by column
name (source EXCLUDED); the contract's full schema order governs insertion, and
a row may omit columns entirely (they land as NULL). That subset case covers
zcode's dim_session extract (identity, edit deltas, trace_id only); opencode
and prime populate every column. Insert dispatch unifies the zcode arrow-view
path and the opencode/prime arrow path behind one call site.

Acceptance gate (S1): full-rebuild parity — per-table row counts and
content checksums identical before/after the refactor, verified twice.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any  # noqa: UP035  # typing import kept verbatim from the frozen legacy copy
from collections.abc import Callable

import duckdb
import pyarrow as pa

DuckDBPyConnection = duckdb.DuckDBPyConnection
# Parser = harness loader: (connection, since) -> total rows inserted,
# or via the dispatcher `trace_contracts.load`. Parsers return {table:
# [{col: value, ...}]} — source excluded; row dicts may omit columns
# (they land as NULL, covering zcode's dim_session subset).
Parser = Callable[[DuckDBPyConnection, date | None], int]

REPO = Path(__file__).resolve().parents[1]

# Per-load validation outcome, consumed by agent_traces into load_log.
_VALIDATION_COUNTS: dict[str, int] = {
    "rows_ok": 0,
    "rows_rejected": 0,
    "unknown_kind_count": 0,
    "unknown_field_count": 0,
    "missing_required_count": 0,
    "type_error_count": 0,
}


def reset_validation() -> None:
    for k in _VALIDATION_COUNTS:
        _VALIDATION_COUNTS[k] = 0


def validation_summary() -> dict[str, int]:
    return dict(_VALIDATION_COUNTS)


def _count(key: str, n: int = 1) -> None:
    _VALIDATION_COUNTS[key] += n


# Harness name -> loader; populated by `@register_parser`-decorated parsers.
_REGISTRY: dict[str, Parser] = {}


def register_parser(harness: str) -> Callable[[Parser], Parser]:
    """Register a harness parser under its harness name."""

    def decorator(fn: Parser) -> Parser:
        _REGISTRY[harness] = fn
        return fn

    return decorator


def get_parser(harness: str) -> Parser:
    try:
        return _REGISTRY[harness]
    except KeyError:
        raise KeyError(f"no parser registered for harness {harness!r}") from None


@dataclass(frozen=True)
class ColumnSpec:
    """One star-schema column as the loader sees it (source excluded)."""

    name: str
    sql_type: str
    required: bool = True
    bool_cast: bool = False  # CAST(cN AS BOOLEAN)
    ts_cast: bool = False  # CAST(cN AS TIMESTAMP)
    day_cast: bool = False  # CAST(cN AS DATE)


# -------------------------------------------------------------------------- #
# Star-schema contracts (mirrors the CREATE TABLE block in agent_traces.py)
# -------------------------------------------------------------------------- #

CONTRACTS: dict[str, list[ColumnSpec]] = {
    "dim_session": [
        ColumnSpec("session_id", "TEXT", required=True),
        ColumnSpec("ts_start", "TIMESTAMP", ts_cast=True),
        ColumnSpec("day", "DATE", day_cast=True),
        ColumnSpec("ts_end", "TIMESTAMP", ts_cast=True),
        ColumnSpec("directory", "TEXT", required=True),
        ColumnSpec("title", "TEXT", required=True),
        ColumnSpec("model", "TEXT", required=True),
        ColumnSpec("agent", "TEXT", required=True),
        ColumnSpec("mode", "TEXT", required=True),
        ColumnSpec("adds", "BIGINT", required=True),
        ColumnSpec("dels", "BIGINT", required=True),
        ColumnSpec("files", "BIGINT", required=True),
        ColumnSpec("compactions", "BIGINT", required=True),
        ColumnSpec("input", "BIGINT", required=True),
        ColumnSpec("output", "BIGINT", required=True),
        ColumnSpec("reasoning", "BIGINT", required=True),
        ColumnSpec("cache_read", "BIGINT", required=True),
        ColumnSpec("cost_usd", "DOUBLE", required=True),
        ColumnSpec("trace_id", "TEXT", required=False),
    ],
    "fact_turn": [
        ColumnSpec("session_id", "TEXT", required=True),
        ColumnSpec("turn_id", "TEXT", required=True),
        ColumnSpec("ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("day", "DATE", day_cast=True),
        ColumnSpec("duration_ms", "BIGINT", required=True),
        ColumnSpec("model_requests", "BIGINT", required=True),
        ColumnSpec("model_retries", "BIGINT", required=True),
        ColumnSpec("tool_calls", "BIGINT", required=True),
        ColumnSpec("tool_errors", "BIGINT", required=True),
        ColumnSpec("tokens", "BIGINT", required=True),
        ColumnSpec("context_exceeded", "BOOLEAN", bool_cast=True),
        ColumnSpec("status", "TEXT", required=True),
    ],
    "fact_tool_call": [
        ColumnSpec("tool_call_id", "TEXT", required=True),
        ColumnSpec("session_id", "TEXT", required=True),
        ColumnSpec("turn_id", "TEXT", required=True),
        ColumnSpec("tool_name", "TEXT", required=True),
        ColumnSpec("ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("day", "DATE", day_cast=True),
        ColumnSpec("duration_ms", "BIGINT", required=True),
        ColumnSpec("ttf_output_ms", "BIGINT", required=True),
        ColumnSpec("exit_code", "BIGINT", required=True),
        ColumnSpec("status", "TEXT", required=True),
        ColumnSpec("output_bytes", "BIGINT", required=True),
        ColumnSpec("stdout_bytes", "BIGINT", required=True),
        ColumnSpec("stderr_bytes", "BIGINT", required=True),
        ColumnSpec("truncated", "BOOLEAN", bool_cast=True),
        ColumnSpec("cancelled", "BOOLEAN", bool_cast=True),
        ColumnSpec("read_only", "BOOLEAN", bool_cast=True),
        ColumnSpec("destructive", "BOOLEAN", bool_cast=True),
        ColumnSpec("approval_status", "TEXT", required=True),
        ColumnSpec("retry_count", "BIGINT", required=True),
        ColumnSpec("error_type", "TEXT", required=True),
        ColumnSpec("error_code", "TEXT", required=True),
        ColumnSpec("side_effect_scope", "TEXT", required=True),
    ],
    "fact_model_request": [
        ColumnSpec("request_id", "TEXT", required=True),
        ColumnSpec("session_id", "TEXT", required=True),
        ColumnSpec("turn_id", "TEXT", required=True),
        ColumnSpec("trace_id", "TEXT", required=True),
        ColumnSpec("span_id", "TEXT", required=True),
        ColumnSpec("attempt_index", "BIGINT", required=True),
        ColumnSpec("ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("day", "DATE", day_cast=True),
        ColumnSpec("provider", "TEXT", required=True),
        ColumnSpec("model", "TEXT", required=True),
        ColumnSpec("variant", "TEXT", required=True),
        ColumnSpec("agent", "TEXT", required=True),
        ColumnSpec("mode", "TEXT", required=True),
        ColumnSpec("task_type", "TEXT", required=True),
        ColumnSpec("query_source", "TEXT", required=True),
        ColumnSpec("status", "TEXT", required=True),
        ColumnSpec("finish_reason", "TEXT", required=True),
        ColumnSpec("ttft_ms", "BIGINT", required=True),
        ColumnSpec("duration_ms", "BIGINT", required=True),
        ColumnSpec("input", "BIGINT", required=True),
        ColumnSpec("output", "BIGINT", required=True),
        ColumnSpec("reasoning", "BIGINT", required=True),
        ColumnSpec("cache_read", "BIGINT", required=True),
        ColumnSpec("cache_write", "BIGINT", required=True),
        ColumnSpec("tool_call_count", "BIGINT", required=True),
        ColumnSpec("retry_count", "BIGINT", required=True),
        ColumnSpec("context_exceeded", "BOOLEAN", bool_cast=True),
        ColumnSpec("cancelled", "BOOLEAN", bool_cast=True),
        ColumnSpec("error_type", "TEXT", required=True),
        ColumnSpec("error_code", "TEXT", required=True),
        # trace_error_forensics S1: zcode-only at the source (non-completed
        # requests); opencode/prime rows NULL-fill via the parser's
        # normalize-to-contract-keys pass. required=False keeps validate_rows
        # quiet on completed rows (data discretion: populate-count census in
        # capture_traces.md §9.4.2).
        ColumnSpec("error_message", "TEXT", required=False),
        ColumnSpec("cost_usd", "DOUBLE", required=True),
        ColumnSpec("cost_basis", "TEXT", required=True),
    ],
    "fact_event": [
        ColumnSpec("trace_id", "TEXT", required=True),
        ColumnSpec("span_id", "TEXT", required=True),
        ColumnSpec("parent_span_id", "TEXT", required=True),
        ColumnSpec("ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("event_name", "TEXT", required=True),
        ColumnSpec("duration_ms", "BIGINT", required=True),
        ColumnSpec("status", "TEXT", required=True),
        ColumnSpec("context", "JSON", required=True),
    ],
    "fact_file_edit": [
        ColumnSpec("session_id", "TEXT", required=True),
        ColumnSpec("ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("day", "DATE", day_cast=True),
        ColumnSpec("snapshot_hash", "TEXT", required=True),
        ColumnSpec("path", "TEXT", required=True),
    ],
    # zcode_rollout_step_ingest S1: one row per rollout model-I/O attempt.
    # Rollout-only table (source='zcode_rollout'): the requestId space is
    # disjoint from model_usage.id, so these rows must never merge into
    # fact_model_request. Token contract follows the store (§8 doctrine):
    # `input` is fresh (inputTokens − cacheReadTokens); `total_tokens` is
    # the provider-reported total, kept untouched as the parity anchor.
    # Content columns are capped heads (analytics proxies, never bodies).
    "fact_model_step": [
        ColumnSpec("request_id", "TEXT", required=True),
        ColumnSpec("session_id", "TEXT", required=True),
        ColumnSpec("turn_id", "TEXT", required=True),
        ColumnSpec("trace_id", "TEXT", required=True),
        ColumnSpec("attempt_index", "BIGINT", required=True),
        ColumnSpec("ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("day", "DATE", day_cast=True),
        ColumnSpec("completed_ts", "TIMESTAMP", ts_cast=True),
        ColumnSpec("provider", "TEXT", required=True),
        ColumnSpec("model", "TEXT", required=True),
        ColumnSpec("query_source", "TEXT", required=True),
        ColumnSpec("status", "TEXT", required=True),
        ColumnSpec("finish_reason", "TEXT", required=True),
        ColumnSpec("duration_ms", "BIGINT", required=True),
        ColumnSpec("input", "BIGINT", required=True),
        ColumnSpec("output", "BIGINT", required=True),
        ColumnSpec("cache_read", "BIGINT", required=True),
        ColumnSpec("cache_write", "BIGINT", required=True),
        ColumnSpec("total_tokens", "BIGINT", required=True),
        ColumnSpec("text_chars", "BIGINT", required=True),
        ColumnSpec("reasoning_chars", "BIGINT", required=True),
        ColumnSpec("tool_call_count", "BIGINT", required=True),
        ColumnSpec("tools_json", "TEXT", required=True),
        ColumnSpec("text_head", "TEXT", required=True),
        ColumnSpec("reasoning_head", "TEXT", required=True),
        ColumnSpec("messages_kind", "TEXT", required=True),
        ColumnSpec("message_offset", "BIGINT", required=True),
        ColumnSpec("message_count", "BIGINT", required=True),
        ColumnSpec("tool_names", "TEXT", required=True),
        ColumnSpec("error_name", "TEXT", required=True),
        ColumnSpec("error_message", "TEXT", required=True),
    ],
}


def table_cols(table: str) -> list[str]:
    """Column names (source excluded) for the given table."""
    return [c.name for c in CONTRACTS[table]]


def column_specs(table: str) -> list[ColumnSpec]:
    return list(CONTRACTS[table])


def _type_ok(spec: ColumnSpec, value: Any) -> bool:
    if value is None:
        return True
    if spec.sql_type == "TEXT" or spec.sql_type == "JSON":
        return (
            isinstance(value, (str, dict, list))
            if spec.sql_type == "JSON"
            else isinstance(value, str)
        )
    if spec.sql_type == "BIGINT":
        return isinstance(value, int) and not isinstance(value, bool)
    if spec.sql_type == "DOUBLE":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec.sql_type == "BOOLEAN":
        return isinstance(value, (bool, int, str))
    if spec.sql_type in {"TIMESTAMP", "DATE"}:
        return isinstance(value, (str, date, datetime))
    return True


def validate_rows(
    table: str,
    rows: list[dict[str, Any]],
    source: str | None = None,
    keep_rejected: bool = False,
) -> list[dict[str, Any]]:
    """Return the rows that satisfy `table`'s contract.

    Strict mode (keep_rejected=False, default): unknown kinds/fields, missing
    required fields and bad-typed fields are rejected AND counted, never
    silently widened — only the ok rows are returned.

    Warn+count mode (keep_rejected=True): every violation is warned and
    counted but all rows are returned so a lenient insert still lands them
    (the insert path drops unknown fields and NULL-fills missing ones). Used
    by the ingest parsers; the reject-by-default flip comes only after a
    clean census per the S2 warn+count policy.
    """
    if table not in CONTRACTS:
        _count("unknown_kind_count", len(rows))
        _count("rows_rejected", len(rows))
        print(
            f"WARNING: {source or 'unknown'}: rejected {len(rows)} rows for unknown kind {table!r}",
            file=sys.stderr,
        )
        return [] if not keep_rejected else list(rows)
    cols = {c.name for c in CONTRACTS[table]}
    ok_rows: list[dict[str, Any]] = []
    rejected = 0
    unknown_fields = 0
    missing_required = 0
    type_errors = 0
    for row in rows:
        ok = True
        unknown = sorted(set(row) - cols)
        if unknown:
            unknown_fields += len(unknown)
            rejected += 1
            _count("unknown_field_count", len(unknown))
            _count("rows_rejected")
            ok = False
        missing = [c.name for c in CONTRACTS[table] if c.required and c.name not in row]
        if missing:
            missing_required += len(missing)
            rejected += 1
            _count("missing_required_count", len(missing))
            _count("rows_rejected")
            ok = False
        bad_types = [
            c.name for c in CONTRACTS[table] if c.name in row and not _type_ok(c, row[c.name])
        ]
        if bad_types:
            type_errors += len(bad_types)
            rejected += 1
            _count("type_error_count", len(bad_types))
            _count("rows_rejected")
            ok = False
        if ok:
            ok_rows.append(row)
            _count("rows_ok")
    if rejected:
        print(
            f"WARNING: {source or 'unknown'} {table}: rejected {rejected} rows "
            f"(unknown_fields={unknown_fields} missing_required={missing_required} type_errors={type_errors})",
            file=sys.stderr,
        )
    return ok_rows if not keep_rejected else list(rows)


# -------------------------------------------------------------------------- #
# Load dispatch and delete window (moved out of agent_traces.py)
# -------------------------------------------------------------------------- #


def _delete_window(con: DuckDBPyConnection, table: str, source: str, since: date | None) -> None:
    """Delete the given source's rows in `table` for the incremental window:
    the whole table if `since` is None, rows with day >= since otherwise."""
    if since is None:
        con.execute(f"DELETE FROM {table} WHERE source = '{source}'")  # noqa: S608  # constants + fixed columns, not user input
    else:
        con.execute(f"DELETE FROM {table} WHERE source = '{source}' AND day >= ?", [since])  # noqa: S608  # constants + fixed columns, not user input


def load(con: DuckDBPyConnection, source: str, since: date | None = None) -> int:
    """Run the registered `source` parser, which performs its own source
    window delete and then the unified insert; return total rows inserted."""
    parser = get_parser(source)
    reset_validation()
    return parser(con, since)


# -------------------------------------------------------------------------- #
# Unified insert (replaces the zcode _register-view insert and _insert_raw)
# -------------------------------------------------------------------------- #


def _insert_all(
    con: DuckDBPyConnection,
    table: str,
    rows: list[dict[str, Any]],
    source: str,
) -> None:
    """Insert `rows` into `table` for `source`, casting bool/ts/day by the
    contract. `rows` are dicts keyed by column name (source excluded); the
    contract's full schema order governs insertion, and a dict may omit
    columns entirely (they land as NULL) — this covers the zcode
    dim_session subset case (identity, edits, trace_id only). The three
    harness parsers (zcode/opencode/prime) call `validate_rows(...,
    keep_rejected=True)` before this call: in warn+count mode the violation
    is warned and counted but all rows still land here, so unknown fields are
    dropped, missing required fields NULL-fill, and bad types survive only if
    DuckDB's CAST coerces them — the same lenient outcome as the original
    `_insert_raw` (str-coerce + DuckDB CAST), and the basis for the S1
    parity gate. Strict reject (keep_rejected=False) is reserved for future
    reject-by-default flip after a clean census per the S2 policy."""
    if not rows:
        return
    cols = table_cols(table)
    spec = column_specs(table)
    ordered = [[row.get(c) for c in cols] for row in rows]
    if any(all(r[i] is None for r in ordered) for i in range(len(cols))):
        all_null = {i for i in range(len(cols)) if all(r[i] is None for r in ordered)}
    else:
        all_null = set()
    data: dict[str, list] = {}
    for i in range(len(cols)):
        vals = [r[i] for r in ordered]
        if i in all_null:
            vals = [None for _ in vals]
        data[f"c{i}"] = vals
    view = f"{table}_batch"
    con.register(view, pa.table(data))
    selects = []
    for j, col in enumerate(cols):
        expr = f"c{j}"
        if spec[j].bool_cast:
            expr = f"CAST(c{j} AS BOOLEAN)"
        elif spec[j].ts_cast:
            expr = f"CAST(c{j} AS TIMESTAMP)"
        elif spec[j].day_cast:
            expr = f"CAST(c{j} AS DATE)"
        selects.append(expr)
    con.execute(
        f"INSERT INTO {table} (source, {', '.join(cols)}) "  # noqa: S608  # constants + fixed columns, not user input
        f"SELECT '{source}', {', '.join(selects)} FROM {view}"
    )
    con.unregister(view)
