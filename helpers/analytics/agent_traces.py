#!/usr/bin/env python3
"""Agent behavioral-trace analytics — one local DuckDB store (raw observations).

Companion to model_analytics.py (spend accounting). That store answers "what
did I spend"; this one answers "how do the agents actually behave": latency,
TTFT, retries, error taxonomy, tool economics, agentic depth, per-turn
attribution. Design + store decision: doc/local/engineering/capture_traces.md
§5 (proposal: doc/improvements/proposals/agent_traces_store.md).

REFRESH MODEL — same contract as model_analytics.py: `load <source>` REPLACES
that source's rows in the refresh window (delete day >= since + reinsert),
it does not append. Old days are immutable once loaded; the current day is
not (a live session keeps writing). --full reloads the whole source. A reload
yields identical row counts (stable-id PKs backstop the window). Trace rows
are RAW OBSERVATIONS, many-to-one against usage rows — never fold them into
model_usage.duckdb's fact_usage; join the stores with ATTACH instead.

Sources:
  zcode   ~/.zcode/cli/db/db.sqlite — model_usage / turn_usage / tool_usage
          / session tables, typed SQL, zero JSON parsing (capture_traces §2.1).
  rollout ~/.zcode/cli/rollout/model-io-*.jsonl — per-attempt normalized
          step responses into fact_model_step (capture_traces §2.3;
          requestId space is disjoint from model_usage.id, so this never
          merges into fact_model_request).

Schema notes (deliberate deviations from capture_traces.md §5.1):
  - day DATE columns on the fact tables (LOCAL day, per the model_usage.md
    date conventions — zcode/opencode are local) serve both the window
    deletes and the ATTACH bridge to mu.fact_usage.
  - fact_turn PK is (source, session_id, turn_id): zcode's native turn_id is
    globally unique (171/171 verified 2026-09-25), but opencode/prime turns
    are DERIVED and only unique within a session.
  - fact_model_request grain = one row per request ATTEMPT (model_usage
    carries id + attempt_index; retry_count was 0 across the whole window).
  - cost_usd: zcode is MATERIALIZED at load time from the published rate
    table (S5, 2026-09-26; cost_basis='rate:zai_usage_query.COST_RATES_PER_M')
    because zcode has no harness cost card. opencode/prime carry the harness
    figure (cost_basis='harness'). The bridge query still derives spend from
    mu.fact_usage, which is the authoritative quota-wide source.
  - TOKEN CONVENTION: `input` means FRESH/uncached input everywhere, so
    `input + cache_read` is the context the provider served. This needs an
    explicit normalization because the harnesses disagree — see the S5 note
    on _Z_REQUESTS.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import duckdb
import pyarrow as pa

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))  # repo root, for helpers.core.db
from helpers.core.db import connect  # noqa: E402  (path insert must precede)

from helpers.analytics import trace_contracts  # noqa: E402

DB_PATH = REPO / "memory" / "data" / "agent_traces.duckdb"
ZCODE_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
USAGE_DB = REPO / "memory" / "data" / "model_usage.duckdb"
OC_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
# Recovery snapshots (overlapping, deduped on stable id; live loaded LAST so
# it wins). The feeds the 2026-09-25 inventory documented were later removed
# from disk — the globs simply match nothing until they are re-staged.
OC_SNAPSHOTS = sorted((REPO / "memory" / "data" / "telemetry_backup").glob("opencode-*.db"))
PRIME_DIR = Path.home() / ".prime" / "agent"
PRIME_BACKUP = REPO / "memory" / "data" / "prime_sessions_backup"
ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_model_request (
    source           TEXT NOT NULL,
    request_id       TEXT NOT NULL,
    session_id       TEXT,
    turn_id          TEXT,
    trace_id         TEXT,
    span_id          TEXT,
    attempt_index    BIGINT,
    ts               TIMESTAMP,
    day              DATE,
    provider         TEXT,
    model            TEXT,
    variant          TEXT,
    agent            TEXT,
    mode             TEXT,
    task_type        TEXT,
    query_source     TEXT,
    status           TEXT,
    finish_reason    TEXT,
    ttft_ms          BIGINT,
    duration_ms      BIGINT,
    input            BIGINT,
    output           BIGINT,
    reasoning        BIGINT,
    cache_read       BIGINT,
    cache_write      BIGINT,
    tool_call_count  BIGINT,
    retry_count      BIGINT,
    context_exceeded BOOLEAN,
    cancelled        BOOLEAN,
    error_type       TEXT,
    error_code       TEXT,
    error_message    TEXT,
    cost_usd         DOUBLE,
    cost_basis       TEXT,
    PRIMARY KEY (source, request_id)
);
CREATE TABLE IF NOT EXISTS fact_turn (
    source          TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    turn_id         TEXT NOT NULL,
    ts              TIMESTAMP,
    day             DATE,
    duration_ms     BIGINT,
    model_requests  BIGINT,
    model_retries   BIGINT,
    tool_calls      BIGINT,
    tool_errors     BIGINT,
    tokens          BIGINT,
    context_exceeded BOOLEAN,
    status          TEXT,
    PRIMARY KEY (source, session_id, turn_id)
);
CREATE TABLE IF NOT EXISTS fact_tool_call (
    source            TEXT NOT NULL,
    tool_call_id      TEXT NOT NULL,
    session_id        TEXT,
    turn_id           TEXT,
    tool_name         TEXT,
    ts                TIMESTAMP,
    day               DATE,
    duration_ms       BIGINT,
    ttf_output_ms     BIGINT,
    exit_code         BIGINT,
    status            TEXT,
    output_bytes      BIGINT,
    stdout_bytes      BIGINT,
    stderr_bytes      BIGINT,
    truncated         BOOLEAN,
    cancelled         BOOLEAN,
    read_only         BOOLEAN,
    destructive       BOOLEAN,
    approval_status   TEXT,
    retry_count       BIGINT,
    error_type        TEXT,
    error_code        TEXT,
    side_effect_scope TEXT,
    PRIMARY KEY (source, tool_call_id)
);
CREATE TABLE IF NOT EXISTS dim_session (
    source      TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    ts_start    TIMESTAMP,
    day         DATE,
    ts_end      TIMESTAMP,
    directory   TEXT,
    title       TEXT,
    model       TEXT,
    agent       TEXT,
    mode        TEXT,
    adds        BIGINT,
    dels        BIGINT,
    files       BIGINT,
    compactions BIGINT,
    input       BIGINT,
    output      BIGINT,
    reasoning   BIGINT,
    cache_read  BIGINT,
    cost_usd    DOUBLE,
    trace_id    TEXT,
    PRIMARY KEY (source, session_id)
);
CREATE TABLE IF NOT EXISTS fact_event (
    source          TEXT NOT NULL,
    trace_id        TEXT,
    span_id         TEXT,
    parent_span_id  TEXT,
    ts              TIMESTAMP,
    event_name      TEXT,
    duration_ms     BIGINT,
    status          TEXT,
    context         JSON
);
CREATE TABLE IF NOT EXISTS fact_file_edit (
    source         TEXT NOT NULL,
    session_id     TEXT,
    ts             TIMESTAMP,
    day            DATE,
    snapshot_hash  TEXT,
    path           TEXT,
    PRIMARY KEY (source, snapshot_hash, path)
);
-- fact_log lived only in pre-SCHEMA live stores (no CREATE anywhere in
-- helpers/) — every fresh store hit CatalogException on `--range all`
-- (store_range) and the reliability leg. Column set mirrors the live
-- store exactly (zcode_rollout_step_ingest S3 shakedown, 2026-10-06).
CREATE TABLE IF NOT EXISTS fact_log (
    source          TEXT NOT NULL,
    trace_id        TEXT,
    span_id         TEXT,
    parent_span_id  TEXT,
    ts              TIMESTAMP,
    event_name      TEXT,
    level           TEXT,
    component       TEXT,
    message         TEXT,
    context         JSON
);
CREATE TABLE IF NOT EXISTS fact_model_step (
    source          TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    session_id      TEXT,
    turn_id         TEXT,
    trace_id        TEXT,
    attempt_index   BIGINT,
    ts              TIMESTAMP,
    day             DATE,
    completed_ts    TIMESTAMP,
    provider        TEXT,
    model           TEXT,
    query_source    TEXT,
    status          TEXT,
    finish_reason   TEXT,
    duration_ms     BIGINT,
    input           BIGINT,
    output          BIGINT,
    cache_read      BIGINT,
    cache_write     BIGINT,
    total_tokens    BIGINT,
    text_chars      BIGINT,
    reasoning_chars BIGINT,
    tool_call_count BIGINT,
    tools_json      TEXT,
    text_head       TEXT,
    reasoning_head  TEXT,
    messages_kind   TEXT,
    message_offset  BIGINT,
    message_count   BIGINT,
    tool_names      TEXT,
    error_name      TEXT,
    error_message   TEXT,
    PRIMARY KEY (source, request_id)
);
CREATE TABLE IF NOT EXISTS load_log (
    source      TEXT NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    rows        BIGINT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT,
    rows_ok     BIGINT,
    rows_rejected BIGINT,
    unknown_fields BIGINT
);
"""


def _ensure_load_log_outcome_columns(con: duckdb.DuckDBPyConnection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info('load_log')").fetchall()}
    for col, typ in [
        ("rows_ok", "BIGINT"),
        ("rows_rejected", "BIGINT"),
        ("unknown_fields", "BIGINT"),
    ]:
        if col not in cols:
            con.execute(f"ALTER TABLE load_log ADD COLUMN {col} {typ}")  # noqa: S608  # col/typ from the module-level (name, type) tuple list; no user input


def _ensure_model_request_error_message(con: duckdb.DuckDBPyConnection) -> None:
    """trace_error_forensics S1 migration: ADD COLUMN is metadata-only, but it
    APPENDS — a migrated store keeps error_message physically last while fresh
    stores (SCHEMA above) carry it after error_code. Harmless: every read and
    insert is name-keyed (_insert_all names columns; no SELECT *)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info('fact_model_request')").fetchall()}
    if "error_message" not in cols:
        con.execute("ALTER TABLE fact_model_request ADD COLUMN error_message TEXT")


# --------------------------------------------------------------- loading ----

# Window deletes key on day (LOCAL) — see module docstring. With no `since`
# the whole source is replaced (first load / --full): day >= NULL would
# delete nothing, so the None case drops the day clause entirely.
_load_log_extra: list[str] = []  # per-load detail, consumed by main()


# -------------------------------------------------------------------------- #
# Windowed delete: moved to bench_data.code.trace_contracts to keep load
# dispatch (delete-then-insert) in one place.
# -------------------------------------------------------------------------- #
from helpers.analytics.trace_contracts import _delete_window  # noqa: E402

# ---------------------------------------------- S5: zcode cost materialization
# The rate table that already backs model_usage.duckdb's zai rows
# (cost_basis='rate'), copied from bench_data/code/zai_usage_query.py
# COST_RATES_PER_M so the two stores cannot drift. Verified 2026-09-26: this
# formula reproduces mu's zai cost to $0.000000 (glm-5.3 $675.0540,
# glm-5.3-flash $45.0471).
#
# The split matters: `input` is fresh/uncached and `cache_read` is cached, and
# the rates differ ~5.4x for glm-5.3. Charging inclusive input at the uncached
# rate (as the store did before the token-convention fix) double-bills cached
# context.
_Z_COST_RATES_PER_M: dict[str, dict[str, float]] = {
    "glm-5.3": {"cached": 0.26, "uncached": 1.40, "output": 4.40},
    "glm-5.3-flash": {"cached": 0.03, "uncached": 0.15, "output": 0.50},
}
_Z_COST_BASIS = "rate:zai_usage_query.COST_RATES_PER_M"


def _z_cost_sql_expr(model_expr: str, input_expr: str, cache_expr: str, out_expr: str) -> str:
    """SQL CASE assigning per-request cost from the rate table, or NULL when
    the model has no published rate (so an unpriced model is visibly
    unpriced, never $0.00).

    Each term is individually parenthesized. This is not defensive
    decoration: `input_expr` is a difference
    (`input_tokens - cache_read_input_tokens`), and without parens SQL's
    left-to-right precedence silently rewrites
    `a + input - cache * rate` into `a + input - (cache * rate)`, which
    produced a NEGATIVE cost for GLM-5.3 before this was caught."""
    arms = []
    for code, r in _Z_COST_RATES_PER_M.items():
        arms.append(
            f"WHEN lower({model_expr}) = '{code}' THEN ("
            f"({cache_expr}) * {r['cached']} + ({input_expr}) * {r['uncached']}"
            f" + ({out_expr}) * {r['output']}) / 1e6"
        )
    return "CASE " + " ".join(arms) + " END"


def _z_cost_sql() -> tuple[str, str]:
    """(cost_expr, cost_basis_expr) for the zcode request extract, written in
    SQLite dialect (the extract runs against db.sqlite, not DuckDB).

    Reads the NORMALIZED columns: `input` is fresh/uncached, `cache_read` is
    the cached re-read. cost_basis is NULL exactly when cost_usd is, so an
    unpriced model never claims a rate was applied."""
    cost = _z_cost_sql_expr(
        "model_id",
        "input_tokens - cache_read_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    basis = f"CASE WHEN ({cost}) IS NULL THEN NULL ELSE '{_Z_COST_BASIS}' END"
    return cost, basis


# One row per LLM request attempt. ts/day computed on the sqlite side (LOCAL,
# matching the zcode day convention); booleans arrive as 0/1 and are cast on
# insert.
# {window} is '' (full) or a started_at lower bound in epoch ms, so an
# incremental run only re-inserts the window it just deleted (PK backstop).
_Z_REQUESTS = """
SELECT id, session_id, turn_id, trace_id, span_id, attempt_index,
       datetime(started_at/1000,'unixepoch','localtime'),
       date(started_at/1000,'unixepoch','localtime'),
       provider_id, model_id, variant, agent, mode, task_type, query_source,
       status, finish_reason, time_to_first_token_ms, duration_ms,
       -- TOKEN CONVENTION (S5, 2026-09-26). zcode's `input_tokens` is
       -- INCLUSIVE of cache reads: provider_total_tokens == input_tokens +
       -- output_tokens, and input_tokens >= cache_read_input_tokens in
       -- 3160/3160 rows. opencode uses the opposite convention
       -- (total == input + output + reasoning + cache.read, 2672/2672 rows).
       -- The store keeps ONE meaning -- `input` is fresh/uncached, and
       -- input + cache_read is the context the provider served -- so zcode
       -- is normalized here. Without this, every cross-source token sum
       -- double-counts zcode's cached context.
       input_tokens - cache_read_input_tokens, output_tokens, reasoning_tokens,
       cache_read_input_tokens, cache_creation_input_tokens,
       tool_call_count, retry_count, context_exceeded, cancelled_by_user,
       error_type, error_code, error_message,
       -- S5: cost materialized from the published rate table. NULL for an
       -- unpriced model, never 0.00; cost_basis is NULL in the same case.
       __COST__ AS cost_usd,
       __COSTBASIS__ AS cost_basis
FROM model_usage{window}
"""

_Z_REQUEST_COLS = [
    "request_id",
    "session_id",
    "turn_id",
    "trace_id",
    "span_id",
    "attempt_index",
    "ts",
    "day",
    "provider",
    "model",
    "variant",
    "agent",
    "mode",
    "task_type",
    "query_source",
    "status",
    "finish_reason",
    "ttft_ms",
    "duration_ms",
    "input",
    "output",
    "reasoning",
    "cache_read",
    "cache_write",
    "tool_call_count",
    "retry_count",
    "context_exceeded",
    "cancelled",
    "error_type",
    "error_code",
    "error_message",
    "cost_usd",
    "cost_basis",
]

_Z_TURNS = """
SELECT session_id, turn_id,
       datetime(started_at/1000,'unixepoch','localtime'),
       date(started_at/1000,'unixepoch','localtime'),
       duration_ms, model_request_count, model_retry_count, tool_call_count,
       tool_error_count, computed_total_tokens, context_exceeded, status
FROM turn_usage{window}
"""

_Z_TURN_COLS = [
    "session_id",
    "turn_id",
    "ts",
    "day",
    "duration_ms",
    "model_requests",
    "model_retries",
    "tool_calls",
    "tool_errors",
    "tokens",
    "context_exceeded",
    "status",
]

_Z_TOOLS = """
SELECT tool_call_id, session_id, turn_id, tool_name,
       datetime(started_at/1000,'unixepoch','localtime'),
       date(started_at/1000,'unixepoch','localtime'),
       duration_ms, time_to_first_output_ms, exit_code, status, output_bytes,
       stdout_bytes, stderr_bytes, truncated, cancelled_by_user, read_only,
       destructive, approval_status, retry_count, error_type, error_code,
       side_effect_scope
FROM tool_usage{window}
"""

_Z_TOOL_COLS = [
    "tool_call_id",
    "session_id",
    "turn_id",
    "tool_name",
    "ts",
    "day",
    "duration_ms",
    "ttf_output_ms",
    "exit_code",
    "status",
    "output_bytes",
    "stdout_bytes",
    "stderr_bytes",
    "truncated",
    "cancelled",
    "read_only",
    "destructive",
    "approval_status",
    "retry_count",
    "error_type",
    "error_code",
    "side_effect_scope",
]

_Z_SESSIONS = """
SELECT id,
       datetime(time_created/1000,'unixepoch','localtime'),
       date(time_created/1000,'unixepoch','localtime'),
       datetime(time_updated/1000,'unixepoch','localtime'),
       directory, title, summary_additions, summary_deletions, summary_files,
       trace_id
FROM session{window}
"""

_Z_SESSION_COLS = [
    "session_id",
    "ts_start",
    "day",
    "ts_end",
    "directory",
    "title",
    "adds",
    "dels",
    "files",
    "trace_id",
]

# (query, target table, explicit column list for the insert-select, bool cols)
_ZCODE_EXTRACTS = [
    (_Z_REQUESTS, "fact_model_request", _Z_REQUEST_COLS, {"context_exceeded", "cancelled"}),
    (_Z_TURNS, "fact_turn", _Z_TURN_COLS, {"context_exceeded"}),
    (
        _Z_TOOLS,
        "fact_tool_call",
        _Z_TOOL_COLS,
        {"truncated", "cancelled", "read_only", "destructive"},
    ),
    (_Z_SESSIONS, "dim_session", _Z_SESSION_COLS, set()),
]


def _register(con: duckdb.DuckDBPyConnection, name: str, rows: list[tuple], n_cols: int) -> None:
    """Register a fetched row batch as an arrow view. All columns land as
    TEXT/INT64/NULL-typed; the insert-select does the real casting (booleans
    from 0/1, timestamps from 'YYYY-MM-DD HH:MM:SS' strings)."""
    if rows:
        cols = list(zip(*rows))
    else:  # keep the view shape so an empty window still inserts nothing
        cols = [[] for _ in range(n_cols)]
    con.register(name, pa.table({f"c{i}": list(col) for i, col in enumerate(cols)}))


@trace_contracts.register_parser("zcode")
def _parse_zcode(con, since=None):
    """zcode telemetry DB (capture_traces §2.1); returns {table: [row_dict]}.

    dim_session is a column-subset extract: zcode's session table carries
    session identity, edit deltas and trace_id only, so model/agent/mode,
    compactions, input/output/reasoning/cache_read/cost_usd land NULL."""
    window = ""
    if since is not None:
        ms = int(datetime(since.year, since.month, since.day).timestamp() * 1000)
        window = f" WHERE started_at >= {ms}"
    session_window = f" WHERE time_created >= {window.split('>= ')[1]}" if window else ""
    src = connect(str(ZCODE_DB), read_only=True)
    try:
        cost_expr, basis_expr = _z_cost_sql()
        out = {}
        for i, (query, table, cols, bools) in enumerate(_ZCODE_EXTRACTS):
            sql = (
                query.replace("__COSTBASIS__", basis_expr)
                .replace("__COST__", cost_expr)
                .format(window=session_window if table == "dim_session" else window)
            )
            rows = [tuple(r) for r in src.execute(sql).fetchall()]  # noqa: S608  # sql built from the module _ZCODE_EXTRACTS templates with constant .replace/.format; no user input
            out[table] = [{c: v for c, v in zip(cols, row)} for row in rows]  # noqa: S608  # cols/row from the same query: header + its own fetchall(), lengths match by construction
        # Normalize to full contract keys so required-field validation can
        # distinguish "column omitted by extract" (NULL-filled) from a
        # genuinely dropped required key in the raw shape.
        for table, rows in out.items():
            if table in trace_contracts.CONTRACTS:
                cols = trace_contracts.table_cols(table)
                out[table] = [{c: r.get(c) for c in cols} for r in rows]
        # Delete the incremental window per table (day-windowed) before
        # re-inserting, mirroring the old zcode load semantics.
        for _, table, _, _ in _ZCODE_EXTRACTS:
            _delete_window(con, table, "zcode", since)
        total = 0
        for table, rows in out.items():
            rows = trace_contracts.validate_rows(table, rows, "zcode", keep_rejected=True)
            trace_contracts._insert_all(con, table, rows, "zcode")
            total += len(rows)
        return total
    finally:
        src.close()


load_zcode = _parse_zcode


_REQ_COLS = [
    "request_id",
    "session_id",
    "turn_id",
    "trace_id",
    "span_id",
    "attempt_index",
    "ts",
    "day",
    "provider",
    "model",
    "variant",
    "agent",
    "mode",
    "task_type",
    "query_source",
    "status",
    "finish_reason",
    "ttft_ms",
    "duration_ms",
    "input",
    "output",
    "reasoning",
    "cache_read",
    "cache_write",
    "tool_call_count",
    "retry_count",
    "context_exceeded",
    "cancelled",
    "error_type",
    "error_code",
    "cost_usd",
    "cost_basis",
]
_TURN_COLS = [
    "session_id",
    "turn_id",
    "ts",
    "day",
    "duration_ms",
    "model_requests",
    "model_retries",
    "tool_calls",
    "tool_errors",
    "tokens",
    "context_exceeded",
    "status",
]
_TOOL_COLS = [
    "tool_call_id",
    "session_id",
    "turn_id",
    "tool_name",
    "ts",
    "day",
    "duration_ms",
    "ttf_output_ms",
    "exit_code",
    "status",
    "output_bytes",
    "stdout_bytes",
    "stderr_bytes",
    "truncated",
    "cancelled",
    "read_only",
    "destructive",
    "approval_status",
    "retry_count",
    "error_type",
    "error_code",
    "side_effect_scope",
]
_EDIT_COLS = ["session_id", "ts", "day", "snapshot_hash", "path"]
_EVENT_COLS = [
    "trace_id",
    "span_id",
    "parent_span_id",
    "ts",
    "event_name",
    "duration_ms",
    "status",
    "context",
]
_SESSION_COLS = [
    "session_id",
    "ts_start",
    "day",
    "ts_end",
    "directory",
    "title",
    "model",
    "agent",
    "mode",
    "adds",
    "dels",
    "files",
    "compactions",
    "input",
    "output",
    "reasoning",
    "cache_read",
    "cost_usd",
    "trace_id",
]

# opencode event families that embed full message/file-diff payloads — pure
# event-mirror noise (934 MB of `context`): nothing queries them, and they
# re-rotate on every load. Keep the row for taxonomy, drop the blob.
_EVENT_NOISE = {"message.updated.1", "message.part.updated.1"}


def _event_noise_head(data: object) -> str | None:
    """Redacted stand-in for a noise event: ids + shape, no payload bytes.
    Big `info.summary.diffs` bodies (multi-MB) are the guzzler; keep only
    message identity and model/agent, so the event is still traceable.
    sqlite hands back `event.data` as a JSON *string*, so parse first; a
    payload we cannot read degrades to None (row kept, context dropped)."""
    if isinstance(data, (str, bytes, bytearray)):
        try:
            data = json.loads(data)
        except ValueError, TypeError:
            return None
    if not isinstance(data, dict):
        return None
    info = data.get("info")
    info = info if isinstance(info, dict) else data
    model = info.get("model")
    head = {
        "id": info.get("id"),
        "sessionID": info.get("sessionID") or data.get("sessionID"),
        "agent": info.get("agent"),
        "model": model.get("modelID") if isinstance(model, dict) else None,
        "role": info.get("role"),
    }
    return json.dumps(head, sort_keys=True) if any(head.values()) else None


def _oc_dbs() -> list[Path]:
    """Snapshot DBs first, live DB last so it wins on stable-id dedupe."""
    return [*OC_SNAPSHOTS, OC_DB]


@trace_contracts.register_parser("opencode")
def _parse_opencode(con, since=None):  # noqa: C901  # moved verbatim; split needs a parity harness (c901 deferred class)
    """Load opencode.db (capture_traces §2.3) into the trace tables; returns
    {table: [row_dict]}.

    Dedupe rule (corrected 2026-09-25 against the live data): opencode and
    zcode session ids NEVER overlap ('ses_' vs 'sess_' prefixes), so the
    doc's session-id dedupe cannot fire — the real overlap is the
    zai-coding-plan provider, whose sessions are the ZCode CLI's own storage
    and whose request telemetry already arrives via the zcode source. This
    loader EXCLUDES zai-provider sessions entirely and reports the skip
    count in load_log (the assertion capture_traces §5.1 mandates).
    Requests land at assistant-MESSAGE grain (house pattern from
    model_analytics); turns are DERIVED per assistant message. Day
    convention: LOCAL (model_usage.md date conventions)."""
    tz = datetime.now().astimezone().tzinfo

    def ms_local(ms: int | None) -> tuple[datetime | None, date | None]:
        if not ms:
            return None, None
        dt = datetime.fromtimestamp(ms / 1000, tz=tz).replace(tzinfo=None)
        return dt, dt.date()

    msgs: dict[str, tuple] = {}
    tools: dict[str, tuple] = {}
    edits: dict[tuple, tuple] = {}
    sessions: dict[str, tuple] = {}
    events: dict[str, tuple] = {}
    steps: dict[str, int] = {}
    toerrs: dict[str, int] = {}
    tcount: dict[str, int] = {}
    skipped_zai = 0

    for db in _oc_dbs():
        if not Path(db).is_file():
            continue
        src = connect(str(db), read_only=True)
        try:
            excluded = {
                r[0]
                for r in src.execute(
                    """SELECT id FROM session_v2
                       WHERE json_extract(model,'$.providerID')='zai-coding-plan'
                       UNION SELECT id FROM session
                       WHERE json_extract(model,'$.providerID')='zai-coding-plan'"""
                )
            }
            skipped_zai += len(excluded)
            sess_cols = """id, time_created, time_updated, directory, slug,
                agent, model, summary_additions, summary_deletions,
                summary_files, tokens_input, tokens_output, tokens_reasoning,
                tokens_cache_read, cost"""
            sess: dict[str, tuple] = {}
            for tab in ("session", "session_v2"):  # v2 rows win
                for r in src.execute(f"SELECT {sess_cols} FROM {tab}"):  # noqa: S608  # constants + fixed columns, not user input
                    sess[r[0]] = r
            for sid, s in sess.items():
                if sid in excluded:
                    continue
                ts_start, day = ms_local(s[1])
                if since and day and day < since:
                    continue
                mj = json.loads(s[6]) if s[6] else {}
                sessions[sid] = (
                    sid,
                    ts_start,
                    day,
                    ms_local(s[2])[0],
                    s[3],
                    s[4],
                    mj.get("id"),
                    s[5],
                    None,
                    s[7],
                    s[8],
                    s[9],
                    None,
                    s[10],
                    s[11],
                    s[12],
                    s[13],
                    s[14],
                    None,
                )
            for mid, sid, tc, tu, data in src.execute(
                """SELECT id, session_id, time_created, time_updated,
                   data FROM message"""
            ):
                d = json.loads(data)
                if sid in excluded or d.get("role") != "assistant":
                    continue
                ts, day = ms_local((d.get("time") or {}).get("created"))
                if since and day and day < since:
                    continue
                t = d.get("tokens") or {}
                cache = t.get("cache") or {}
                tm = d.get("time") or {}
                dur = (
                    tm.get("completed", 0) - tm.get("created", 0)
                    if tm.get("completed") and tm.get("created")
                    else None
                )
                msgs[mid] = (
                    mid,
                    sid,
                    None,
                    None,
                    None,
                    None,
                    ts,
                    day,
                    d.get("providerID"),
                    d.get("modelID"),
                    None,
                    d.get("agent"),
                    d.get("mode"),
                    None,
                    None,
                    "completed" if t else "error",
                    d.get("finish"),
                    None,
                    dur,
                    t.get("input"),
                    t.get("output"),
                    t.get("reasoning"),
                    cache.get("read"),
                    cache.get("write"),
                    None,
                    None,
                    None,
                    None,
                    "missing_usage" if not t else None,
                    None,
                    d.get("cost"),
                    "harness" if d.get("cost") is not None else None,
                )
            for pid, mid, sid, tc, tu, data in src.execute(
                """SELECT id, message_id, session_id, time_created,
                   time_updated, data FROM part"""
            ):
                if sid in excluded:
                    continue
                d = json.loads(data)
                ptype = d.get("type")
                ts, day = ms_local(tc)
                if ptype == "tool":
                    if since and day and day < since:
                        continue
                    st = d.get("state") or {}
                    out = st.get("output")
                    err = st.get("status") == "error"
                    tools[pid] = (
                        pid,
                        sid,
                        mid,
                        d.get("tool"),
                        ts,
                        day,
                        (tu - tc) if (tu and tc) else None,
                        None,
                        None,
                        st.get("status"),
                        len(out) if isinstance(out, str) else None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "tool_execution_failed" if err else None,
                        None,
                        None,
                    )
                    if err:
                        toerrs[mid] = toerrs.get(mid, 0) + 1
                    tcount[mid] = tcount.get(mid, 0) + 1
                elif ptype == "patch":
                    if since and day and day < since:
                        continue
                    for path in d.get("files") or []:
                        edits[(d.get("hash"), path)] = (sid, ts, day, d.get("hash"), path)
                elif ptype == "step-finish" and mid:
                    steps[mid] = steps.get(mid, 0) + 1
                elif ptype == "compaction":
                    if sid in sessions:
                        prev = sessions[sid]
                        sessions[sid] = prev[:12] + ((prev[12] or 0) + 1,) + prev[13:]
            for eid, agg, created, etype, data in src.execute(
                """SELECT id, aggregate_id, created, type, data
                   FROM event"""
            ):
                if agg in excluded:
                    continue
                ts, _ = ms_local(created)
                if since and ts and ts.date() < since:
                    continue
                if etype in _EVENT_NOISE:
                    events[eid] = (None, eid, agg, ts, etype, None, None, _event_noise_head(data))
                else:
                    events[eid] = (None, eid, agg, ts, etype, None, None, str(data))
        finally:
            src.close()

    req_rows = []
    for mid, r in msgs.items():
        r = list(r)
        r[24] = steps.get(mid, 0)  # tool_call_count slot: steps first
        req_rows.append(tuple(r))
    turn_rows = []
    for mid, r in msgs.items():
        turn_rows.append(
            (
                r[1],
                mid,
                r[6],
                r[7],
                r[18],
                steps.get(mid, 0),
                0,
                tcount.get(mid, 0),
                toerrs.get(mid, 0),
                (r[19] or 0) + (r[20] or 0) + (r[22] or 0) if (r[19] or r[20] or r[22]) else None,
                None,
                r[15],
            )
        )

    def _as_session(row: tuple) -> dict:
        (
            session_id,
            ts_start,
            day,
            ts_end,
            directory,
            title,
            model,
            agent,
            mode,
            adds,
            dels,
            files,
            compactions,
            input,
            output,
            reasoning,
            cache_read,
            cost_usd,
            trace_id,
        ) = row
        return {
            "session_id": session_id,
            "ts_start": ts_start,
            "day": day,
            "ts_end": ts_end,
            "directory": directory,
            "title": title,
            "model": model,
            "agent": agent,
            "mode": mode,
            "adds": adds,
            "dels": dels,
            "files": files,
            "compactions": compactions,
            "input": input,
            "output": output,
            "reasoning": reasoning,
            "cache_read": cache_read,
            "cost_usd": cost_usd,
            "trace_id": trace_id,
        }

    extracted = {
        "fact_model_request": [
            {
                "request_id": r[0],
                "session_id": r[1],
                "turn_id": r[2],
                "trace_id": r[3],
                "span_id": r[4],
                "attempt_index": r[5],
                "ts": r[6],
                "day": r[7],
                "provider": r[8],
                "model": r[9],
                "variant": r[10],
                "agent": r[11],
                "mode": r[12],
                "task_type": r[13],
                "query_source": r[14],
                "status": r[15],
                "finish_reason": r[16],
                "ttft_ms": r[17],
                "duration_ms": r[18],
                "input": r[19],
                "output": r[20],
                "reasoning": r[21],
                "cache_read": r[22],
                "cache_write": r[23],
                "tool_call_count": r[24],
                "retry_count": r[25],
                "context_exceeded": r[26],
                "cancelled": r[27],
                "error_type": r[28],
                "error_code": r[29],
                "cost_usd": r[30],
                "cost_basis": r[31],
            }
            for r in req_rows
        ],
        "fact_turn": [
            {
                "session_id": r[0],
                "turn_id": r[1],
                "ts": r[2],
                "day": r[3],
                "duration_ms": r[4],
                "model_requests": r[5],
                "model_retries": r[6],
                "tool_calls": r[7],
                "tool_errors": r[8],
                "tokens": r[9],
                "context_exceeded": r[10],
                "status": r[11],
            }
            for r in turn_rows
        ],
        "fact_tool_call": [
            {
                "tool_call_id": r[0],
                "session_id": r[1],
                "turn_id": r[2],
                "tool_name": r[3],
                "ts": r[4],
                "day": r[5],
                "duration_ms": r[6],
                "ttf_output_ms": r[7],
                "exit_code": r[8],
                "status": r[9],
                "output_bytes": r[10],
                "stdout_bytes": r[11],
                "stderr_bytes": r[12],
                "truncated": r[13],
                "cancelled": r[14],
                "read_only": r[15],
                "destructive": r[16],
                "approval_status": r[17],
                "retry_count": r[18],
                "error_type": r[19],
                "error_code": r[20],
                "side_effect_scope": r[21],
            }
            for r in tools.values()
        ],
        "fact_file_edit": [
            {
                "session_id": r[0],
                "ts": r[1],
                "day": r[2],
                "snapshot_hash": r[3],
                "path": r[4],
            }
            for r in edits.values()
        ],
        "fact_event": [
            {
                "trace_id": r[0],
                "span_id": r[1],
                "parent_span_id": r[2],
                "ts": r[3],
                "event_name": r[4],
                "duration_ms": r[5],
                "status": r[6],
                "context": r[7],
            }
            for r in events.values()
        ],
        "dim_session": [_as_session(r) for r in sessions.values()],
    }
    # Delete the incremental window before re-inserting, mirroring the old
    # opencode load window semantics: day-windowed for the four main tables
    # (fact_file_edit is a full replace; fact_event is ts-windowed / null).
    _delete_window(con, "fact_model_request", "opencode", since)
    _delete_window(con, "fact_turn", "opencode", since)
    _delete_window(con, "fact_tool_call", "opencode", since)
    _delete_window(con, "dim_session", "opencode", since)
    con.execute("DELETE FROM fact_file_edit WHERE source = 'opencode'")
    con.execute(
        """DELETE FROM fact_event WHERE source = 'opencode'
                   AND (ts >= ? OR ts IS NULL)""",
        [datetime(since.year, since.month, since.day)] if since else ["1970-01-01"],
    )
    total = 0
    for table, rows in extracted.items():
        rows = trace_contracts.validate_rows(table, rows, "opencode", keep_rejected=True)
        trace_contracts._insert_all(con, table, rows, "opencode")
        total += len(rows)
    _load_log_extra.clear()
    detail = (
        f"requests={len(req_rows)} turns={len(turn_rows)} "
        f"tools={len(tools)} edits={len(edits)} events={len(events)} "
        f"sessions={len(sessions)} skipped_zai_sessions={skipped_zai} "
        f"dbs={[Path(d).name for d in _oc_dbs() if Path(d).is_file()]}"
    )
    _load_log_extra.append(detail)
    return total


load_opencode = _parse_opencode


def _refresh_backup(store: Path) -> None:
    """Last-good recovery copy into db-backup/ after a successful load —
    the house sidecar pattern (rebuild_common.last-good semantics), using
    snapshot_db's CHECKPOINT+zstd technique. Best-effort: a failed backup
    logs a WARNING and never fails the load. Only the default store path
    is ever backed up (a --db scratch run must not clobber the recovery
    point)."""
    if store.resolve() != DB_PATH.resolve():
        return
    import logging

    from helpers.maintenance.snapshot_db import create_duckdb_snapshot

    try:
        create_duckdb_snapshot(
            store,
            REPO / "db-backup" / f"{store.stem}_backup.duckdb.zst",
            logging.getLogger("analytics-backup"),
        )
    except Exception as exc:  # noqa: BLE001  (backup must not fail the load)
        print(f"WARNING: db-backup refresh failed: {exc}", file=sys.stderr)


def _prime_files() -> dict[str, Path]:
    """Session stem -> file; backup fills purged sessions, live overrides
    (a stem present in both means the live file is the fresher copy)."""
    stems: dict[str, Path] = {}
    for f in sorted(PRIME_BACKUP.glob("*.jsonl")):
        stems[f.stem] = f
    for f in sorted((PRIME_DIR / "sessions").glob("*.jsonl")):
        stems[f.stem] = f
    return stems


@trace_contracts.register_parser("prime")
def _parse_prime(con, since=None):  # noqa: C901  # moved verbatim; split needs a parity harness (c901 deferred class)
    """Load prime-rlm sessions JSONL + rlm-ledger + agent logs
    (capture_traces §2.4); returns {table: [row_dict]}. Reads the staged
    backup feed too — purged sessions survive ONLY there. Day convention: UTC
    (model_usage.md date conventions). Latency is DERIVED (assistant record
    timestamp minus its parent's) — prime records are appended at
    completion, so this is approximate. A missing usage block on a genuine
    assistant message is a FAILED call (error_type 'missing_usage'), never a
    zero-token call."""

    def iso(s: str | None) -> datetime | None:
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

    req_rows: list[tuple] = []
    turn_rows: list[tuple] = []
    sess_rows: list[tuple] = []
    event_rows: list[tuple] = []
    stems = _prime_files()
    for stem, f in sorted(stems.items()):
        records = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        sess_rec = next((r for r in records if r.get("type") == "session"), {})
        sid = sess_rec.get("id") or stem
        ts_by_id: dict[str, datetime | None] = {}
        per_user: dict[str, dict] = {}
        assistant_turn: dict[str, str] = {}
        order: list[str] = []
        model = None
        usage_sum = [0, 0, 0, 0]
        cost_sum = 0.0
        for r in records:
            if r.get("timestamp"):
                ts_by_id[r["id"]] = iso(r["timestamp"])
            if r.get("type") != "message":
                if r.get("type") == "model_change":
                    model = (r.get("model") or {}).get("id", model)
                continue
            m = r.get("message") or {}
            role = m.get("role")
            if role == "user":
                per_user[r["id"]] = {
                    "ts": iso(r.get("timestamp")),
                    "reqs": 0,
                    "tools": 0,
                    "tokens": 0,
                    "last": None,
                    "errs": 0,
                }
                order.append(r["id"])
            elif role == "assistant":
                model = m.get("model", model)
                usage = m.get("usage") or {}
                cost = (usage.get("cost") or {}).get("total")
                ts = iso(r.get("timestamp"))
                day = ts.date() if ts else None
                parent_ts = ts_by_id.get(r.get("parentId"))
                dur = (
                    (ts - parent_ts).total_seconds() * 1000
                    if ts and parent_ts and ts >= parent_ts
                    else None
                )
                status = "completed" if usage else "error"
                turn_id = r.get("parentId")
                if turn_id not in per_user:
                    per_user[turn_id] = {
                        "ts": parent_ts,
                        "reqs": 0,
                        "tools": 0,
                        "tokens": 0,
                        "last": None,
                        "errs": 0,
                    }
                    order.append(turn_id)
                assistant_turn[r["id"]] = turn_id
                pu = per_user[turn_id]
                pu["reqs"] += 1
                pu["tokens"] += usage.get("totalTokens", 0) or 0
                pu["last"] = ts
                if not usage:
                    pu["errs"] += 1
                if usage:
                    usage_sum[0] += usage.get("input", 0) or 0
                    usage_sum[1] += usage.get("output", 0) or 0
                    usage_sum[2] += usage.get("cacheRead", 0) or 0
                    usage_sum[3] += usage.get("cacheWrite", 0) or 0
                if cost:
                    cost_sum += cost
                req_rows.append(
                    (
                        r["id"],
                        sid,
                        turn_id,
                        None,
                        None,
                        None,
                        ts,
                        day,
                        m.get("provider"),
                        m.get("model"),
                        None,
                        None,
                        None,
                        None,
                        None,
                        status,
                        None,
                        None,
                        dur,
                        usage.get("input"),
                        usage.get("output"),
                        None,
                        usage.get("cacheRead"),
                        usage.get("cacheWrite"),
                        None,
                        None,
                        None,
                        None,
                        None if usage else "missing_usage",
                        None,
                        cost,
                        "harness" if cost is not None else None,
                    )
                )
            elif role == "toolResult":
                # a toolResult answers the assistant whose id is parentId
                turn_id = assistant_turn.get(r.get("parentId"))
                if turn_id in per_user:
                    per_user[turn_id]["tools"] += 1
        if since:
            req_rows = [r for r in req_rows if r[7] and r[7] >= since]
        for tid, pu in per_user.items():
            if pu["reqs"] == 0 and pu["tools"] == 0:
                continue
            day = pu["ts"].date() if pu["ts"] else None
            if since and (day is None or day < since):
                continue
            dur = (
                (pu["last"] - pu["ts"]).total_seconds() * 1000 if pu["last"] and pu["ts"] else None
            )
            turn_rows.append(
                (
                    sid,
                    tid,
                    pu["ts"],
                    day,
                    dur,
                    pu["reqs"],
                    0,
                    pu["tools"],
                    pu["errs"],
                    pu["tokens"] or None,
                    None,
                    "completed",
                )
            )
        last_ts = max((t for t in ts_by_id.values() if t), default=None)
        ts_start = iso(sess_rec.get("timestamp"))
        if since and ts_start and ts_start.date() < since:
            continue
        sess_rows.append(
            (
                sid,
                ts_start,
                ts_start.date() if ts_start else None,
                last_ts,
                sess_rec.get("cwd"),
                None,
                model,
                None,
                None,
                None,
                None,
                None,
                None,
                usage_sum[0] or None,
                usage_sum[1] or None,
                None,
                usage_sum[2] or None,
                cost_sum or None,
                None,
            )
        )

    # delegation ledger + reliability logs -> fact_event
    ledger = PRIME_DIR / "rlm-ledger" / "20d0e37e665dc969.jsonl"
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            event_rows.append(
                (
                    None,
                    None,
                    None,
                    iso(r.get("at")),
                    f"delegation.{r.get('op')}",
                    None,
                    None,
                    json.dumps(r),
                )
            )
    for lg in (PRIME_DIR / "logs").glob("agent.jsonl*"):
        for line in lg.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("level") not in ("warn", "error"):
                continue
            event_rows.append(
                (
                    None,
                    None,
                    None,
                    iso(r.get("ts")),
                    f"log.{r.get('level')}",
                    None,
                    None,
                    json.dumps(r),
                )
            )
    if since:
        cutoff = datetime(since.year, since.month, since.day)
        event_rows = [r for r in event_rows if r[3] and r[3] >= cutoff]

    extracted = {
        "fact_model_request": [
            {
                "request_id": r[0],
                "session_id": r[1],
                "turn_id": r[2],
                "trace_id": r[3],
                "span_id": r[4],
                "attempt_index": r[5],
                "ts": r[6],
                "day": r[7],
                "provider": r[8],
                "model": r[9],
                "variant": r[10],
                "agent": r[11],
                "mode": r[12],
                "task_type": r[13],
                "query_source": r[14],
                "status": r[15],
                "finish_reason": r[16],
                "ttft_ms": r[17],
                "duration_ms": r[18],
                "input": r[19],
                "output": r[20],
                "reasoning": r[21],
                "cache_read": r[22],
                "cache_write": r[23],
                "tool_call_count": r[24],
                "retry_count": r[25],
                "context_exceeded": r[26],
                "cancelled": r[27],
                "error_type": r[28],
                "error_code": r[29],
                "cost_usd": r[30],
                "cost_basis": r[31],
            }
            for r in req_rows
        ],
        "fact_turn": [
            {
                "session_id": r[0],
                "turn_id": r[1],
                "ts": r[2],
                "day": r[3],
                "duration_ms": r[4],
                "model_requests": r[5],
                "model_retries": r[6],
                "tool_calls": r[7],
                "tool_errors": r[8],
                "tokens": r[9],
                "context_exceeded": r[10],
                "status": r[11],
            }
            for r in turn_rows
        ],
        "dim_session": [
            {
                "session_id": r[0],
                "ts_start": r[1],
                "day": r[2],
                "ts_end": r[3],
                "directory": r[4],
                "title": r[5],
                "model": r[6],
                "agent": r[7],
                "mode": r[8],
                "adds": r[9],
                "dels": r[10],
                "files": r[11],
                "compactions": r[12],
                "input": r[13],
                "output": r[14],
                "reasoning": r[15],
                "cache_read": r[16],
                "cost_usd": r[17],
                "trace_id": r[18],
            }
            for r in sess_rows
        ],
        "fact_event": [
            {
                "trace_id": r[0],
                "span_id": r[1],
                "parent_span_id": r[2],
                "ts": r[3],
                "event_name": r[4],
                "duration_ms": r[5],
                "status": r[6],
                "context": r[7],
            }
            for r in event_rows
        ],
    }
    # Delete the incremental window before re-inserting, mirroring the old
    # prime load window semantics (day-windowed).
    _delete_window(con, "fact_model_request", "prime", since)
    _delete_window(con, "fact_turn", "prime", since)
    _delete_window(con, "dim_session", "prime", since)
    total = 0
    for table, rows in extracted.items():
        rows = trace_contracts.validate_rows(table, rows, "prime", keep_rejected=True)
        trace_contracts._insert_all(con, table, rows, "prime")
        total += len(rows)
    detail = (
        f"requests={len(req_rows)} turns={len(turn_rows)} "
        f"sessions={len(sess_rows)} events={len(event_rows)} "
        f"files={len(stems)}"
    )
    _load_log_extra.append(detail)
    return total


load_prime = _parse_prime


# Rollout step ingest (zcode_rollout_step_ingest S1) ---------------------------
_TEXT_HEAD = 8192  # response text / reasoning heads: analytics proxies, never bodies
_TOOL_INPUT_HEAD = 1024
_TOOLS_JSON_CAP = 16 * 1024
_TOOL_NAMES_CAP = 4096
_ERROR_CAP = 2048
_ROLLOUT_SOURCE = "zcode_rollout"


def _rollout_iso(ts):
    """Parse a rollout ISO-8601 timestamp; None when missing/unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _rollout_int(value, default=None):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


@trace_contracts.register_parser("rollout")
def _parse_rollout(con, since=None, directory=None):
    """zcode rollout model-I/O (capture_traces §2.3) into fact_model_step.

    One row per `model_io` record (one request attempt = one step).
    Identity joins the existing zcode rows on session_id/turn_id, but the
    requestId space is disjoint from model_usage.id — these rows must
    never merge into fact_model_request (or every aggregate leg counts
    zcode twice). Token columns follow the store contract: `input` is
    fresh (inputTokens − cacheReadTokens); `total_tokens` is the
    provider-reported total, kept untouched as the parity anchor.
    Records without a parseable startedAt are skipped: they cannot be
    windowed, so the PK backstop could not clean them on re-run.
    """
    d = Path(directory) if directory is not None else ROLLOUT_DIR
    if not d.is_dir():
        return 0
    rows = []
    for f in sorted(d.glob("model-io-*.jsonl")):
        with open(f, errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or not r.get("requestId"):
                    continue
                ts = _rollout_iso(r.get("startedAt"))
                if ts is None:
                    continue
                day = ts.astimezone().date()  # local day, per model_usage.md conventions
                if since is not None and day < since:
                    continue
                resp = r.get("response")
                resp = resp if isinstance(resp, dict) else {}
                req = r.get("request")
                req = req if isinstance(req, dict) else {}
                model = r.get("model")
                model = model if isinstance(model, dict) else {}
                usage = resp.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                cache_read = usage.get("cacheReadTokens") or 0
                text = resp.get("text") or ""
                reasoning = resp.get("reasoningText") or ""
                tcs = resp.get("toolCalls") or []
                tools = []
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    arg = tc.get("input")
                    tools.append(
                        {
                            "name": tc.get("name"),
                            "input": json.dumps(arg)[:_TOOL_INPUT_HEAD]
                            if arg is not None
                            else None,
                        }
                    )
                tool_names = ",".join(
                    n for n in (req.get("toolNames") or []) if isinstance(n, str)
                )[:_TOOL_NAMES_CAP]
                err = r.get("error")
                err = err if isinstance(err, dict) else {}
                stack_head = " | ".join((err.get("stack") or "").splitlines()[1:4])
                err_msg = err.get("message") or ""
                rows.append(
                    {
                        "request_id": r["requestId"],
                        "session_id": r.get("sessionId"),
                        "turn_id": r.get("turnId"),
                        "trace_id": r.get("traceId"),
                        "attempt_index": _rollout_int(r.get("attempt"), 1),
                        "ts": ts,
                        "day": day,
                        "completed_ts": _rollout_iso(r.get("completedAt")),
                        "provider": model.get("providerId"),
                        "model": model.get("modelId"),
                        "query_source": r.get("querySource"),
                        "status": "completed" if usage else "error",
                        "finish_reason": resp.get("finishReason"),
                        "duration_ms": _rollout_int(r.get("durationMs")),
                        "input": (usage.get("inputTokens") or 0) - cache_read
                        if usage.get("inputTokens") is not None
                        else None,
                        "output": usage.get("outputTokens"),
                        "cache_read": usage.get("cacheReadTokens"),
                        "cache_write": usage.get("cacheWriteTokens"),
                        "total_tokens": usage.get("totalTokens"),
                        "text_chars": len(text),
                        "reasoning_chars": len(reasoning),
                        "tool_call_count": len(tools),
                        "tools_json": json.dumps(tools)[:_TOOLS_JSON_CAP],
                        "text_head": text[:_TEXT_HEAD],
                        "reasoning_head": reasoning[:_TEXT_HEAD],
                        "messages_kind": req.get("messagesKind"),
                        "message_offset": _rollout_int(req.get("messageOffset")),
                        "message_count": _rollout_int(req.get("messageCount")),
                        "tool_names": tool_names,
                        "error_name": err.get("name"),
                        "error_message": (err_msg + (" | " + stack_head if stack_head else ""))[
                            :_ERROR_CAP
                        ]
                        or None,
                    }
                )
    cols = trace_contracts.table_cols("fact_model_step")
    rows = [{c: r.get(c) for c in cols} for r in rows]
    _delete_window(con, "fact_model_step", _ROLLOUT_SOURCE, since)
    rows = trace_contracts.validate_rows(
        "fact_model_step", rows, _ROLLOUT_SOURCE, keep_rejected=True
    )
    trace_contracts._insert_all(con, "fact_model_step", rows, _ROLLOUT_SOURCE)
    return len(rows)


load_rollout = _parse_rollout


# ------------------------------------------------------------- reporting ----


def store_range(con: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    """Full span of loaded data across every day-carrying table — the default
    range. S10 (2026-09-26): the union previously spanned only the three
    request/turn/tool tables, so `--range all` under-reported the store.
    Every table that can date a row must appear, or its days are unreachable
    by any range. Three shapes differ: `fact_log` and `fact_event` carry only
    `ts` (`fact_event.ts` is NULL for the redacted opencode noise rows, which
    min/max skip — prime's real `ts` still bounds the table), and `load_log`
    carries `started_at`. The day-carrying `day` column is preferred where it
    exists since it is the same value the legs filter on.
    Measured effect: the default start moved 2026-09-11 -> 2026-09-05 (six
    days of prime events) and then -> **2026-08-27**, which is `fact_log`'s
    earliest day; a 15-day stretch of opencode/prime log rows was reachable by
    no range at all. The end is unchanged at 2026-09-26 (`load_log`).
    `fact_model_step` is deliberately absent (zcode_rollout_step_ingest S3):
    it is a parallel second observation of the same request stream, so its
    days are covered by `fact_model_request`/`load_log`, and including it
    would let a rollout load move the default span — `report` stays
    byte-identical across a rollout load (AC#3).
    """
    r = con.execute("""
        SELECT min(d), max(d) FROM (
            SELECT min(day) AS d FROM fact_model_request
            UNION ALL SELECT min(day) FROM fact_tool_call
            UNION ALL SELECT min(day) FROM fact_turn
            UNION ALL SELECT min(day) FROM fact_file_edit
            UNION ALL SELECT min(day) FROM dim_session
            UNION ALL SELECT min(date(ts)) FROM fact_event
            UNION ALL SELECT min(date(ts)) FROM fact_log
            UNION ALL SELECT min(date(started_at)) FROM load_log
            UNION ALL SELECT max(day) FROM fact_model_request
            UNION ALL SELECT max(day) FROM fact_tool_call
            UNION ALL SELECT max(day) FROM fact_turn
            UNION ALL SELECT max(day) FROM fact_file_edit
            UNION ALL SELECT max(day) FROM dim_session
            UNION ALL SELECT max(date(ts)) FROM fact_event
            UNION ALL SELECT max(date(ts)) FROM fact_log
            UNION ALL SELECT max(date(started_at)) FROM load_log)""").fetchone()
    if not r or not r[0]:
        t = date.today()
        return t, t
    return r[0], r[1]


def parse_range(
    spec: str,
    con: duckdb.DuckDBPyConnection | None = None,
) -> tuple[date, date]:
    """Range spec: 'all' (store span) | Nd | Nw | Nm | date | date..date."""
    today = date.today()
    if spec == "all":
        if con is None:
            raise SystemExit("range 'all' needs the db connection")
        return store_range(con)
    if spec.endswith("d"):
        return today - timedelta(days=int(spec[:-1]) - 1), today
    if spec.endswith("w"):
        return today - timedelta(weeks=int(spec[:-1])), today
    if spec.endswith("m"):
        return today - timedelta(days=30 * int(spec[:-1])), today
    if ".." in spec:
        a, b = spec.split("..")
        return date.fromisoformat(a), date.fromisoformat(b)
    d = date.fromisoformat(spec)
    return d, d


def _day_where(
    spec: str,
    con: duckdb.DuckDBPyConnection,
) -> tuple[str, list]:
    start, end = parse_range(spec, con)
    return "day BETWEEN ? AND ?", [start, end]


def model_latency_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT model,
               count(*) AS reqs,
               round(sum(duration_ms)/1000.0, 1) AS total_s,
               round(quantile_cont(duration_ms, 0.5)/1000.0, 2) AS p50_s,
               round(quantile_cont(duration_ms, 0.95)/1000.0, 2) AS p95_s,
               round(max(duration_ms)/1000.0, 1) AS max_s,
               round(quantile_cont(ttft_ms, 0.5)/1000.0, 2) AS ttft_p50_s,
               round(quantile_cont(ttft_ms, 0.95)/1000.0, 2) AS ttft_p95_s,
               round(sum(output)::DOUBLE / nullif(sum(duration_ms)/1000.0, 0), 1)
                   AS out_tok_s,
               sum(CASE WHEN error_type IS NOT NULL THEN 1 ELSE 0 END) AS errs,
               sum(retry_count) AS retries,
               sum(CASE WHEN cancelled THEN 1 ELSE 0 END) AS cancelled
        FROM fact_model_request WHERE {where}
        GROUP BY model ORDER BY reqs DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "model",
        "reqs",
        "total_s",
        "p50_s",
        "p95_s",
        "max_s",
        "ttft_p50_s",
        "ttft_p95_s",
        "out_tok_s",
        "errs",
        "retries",
        "cancelled",
    ]
    return [dict(zip(cols, r)) for r in rows]


def tool_economics_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT source, tool_name,
               count(*) AS calls,
               round(quantile_cont(duration_ms, 0.5)/1000.0, 2) AS p50_s,
               round(quantile_cont(duration_ms, 0.95)/1000.0, 2) AS p95_s,
               round(max(duration_ms)/1000.0, 1) AS max_s,
               round(avg(output_bytes), 0) AS avg_out_bytes,
               round(100.0 * sum(CASE WHEN error_type IS NOT NULL THEN 1
                                      ELSE 0 END) / count(*), 1) AS err_pct,
               sum(CASE WHEN cancelled THEN 1 ELSE 0 END) AS cancelled
        FROM fact_tool_call WHERE {where}
        GROUP BY source, tool_name ORDER BY calls DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "source",
        "tool",
        "calls",
        "p50_s",
        "p95_s",
        "max_s",
        "avg_out_bytes",
        "err_pct",
        "cancelled",
    ]
    return [dict(zip(cols, r)) for r in rows]


def agentic_depth_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    where, params = _day_where(spec, con)
    r = con.execute(
        f"""
        SELECT count(*) AS turns,
               round(avg(model_requests), 1) AS mean_reqs_per_turn,
               quantile_cont(model_requests, 0.5) AS p50_reqs,
               max(model_requests) AS max_reqs,
               round(avg(tool_calls), 1) AS mean_tools_per_turn,
               max(tool_calls) AS max_tools,
               round(sum(duration_ms)/1000.0/60.0, 1) AS total_min
        FROM fact_turn WHERE {where}""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchone()
    if r is None:
        return []
    cols = [
        "turns",
        "mean_reqs_per_turn",
        "p50_reqs",
        "max_reqs",
        "mean_tools_per_turn",
        "max_tools",
        "total_min",
    ]
    return [dict(zip(cols, r))]


def _has_step_table(con: duckdb.DuckDBPyConnection) -> bool:
    """S2 (zcode_rollout_step_ingest): fact_model_step exists (post-S1
    stores). Pre-S1 stores skip the step reads so old databases keep
    reporting exactly as before."""
    return "fact_model_step" in {r[0] for r in con.execute("SHOW TABLES").fetchall()}


def error_taxonomy_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    where, params = _day_where(spec, con)
    reqs = con.execute(
        f"""
        SELECT 'request' AS kind, coalesce(source, '') AS source,
               coalesce(error_type, '(none)') AS error_type,
               coalesce(error_code, '') AS error_code, count(*) AS n,
               coalesce(NULLIF(any_value(finish_reason) FILTER (
                   WHERE finish_reason IS NOT NULL), ''), '') AS sample
        FROM fact_model_request WHERE {where} AND error_type IS NOT NULL
        GROUP BY 1, 2, 3, 4""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    tools = con.execute(
        f"""
        SELECT 'tool:' || tool_name AS kind, coalesce(source, '') AS source,
               coalesce(error_type, '(none)') AS error_type,
               coalesce(error_code, '') AS error_code, count(*) AS n, ''
        FROM fact_tool_call WHERE {where} AND error_type IS NOT NULL
        GROUP BY 1, 2, 3, 4""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = ["kind", "source", "error_type", "error_code", "n", "sample"]
    rows = [dict(zip(cols, r)) for r in [*reqs, *tools]]
    # S2 (zcode_rollout_step_ingest): rollout step errors are request-grain
    # (the model never answered), so they fit these columns natively —
    # kind 'step', source 'zcode_rollout'. Guarded for pre-S1 stores.
    if _has_step_table(con):
        where_s, params_s = _day_where(spec, con)
        rows += [
            dict(zip(cols, r))
            for r in con.execute(
                f"""
                SELECT 'step' AS kind, 'zcode_rollout' AS source,
                       coalesce(error_name, '(none)') AS error_type,
                       '' AS error_code, count(*) AS n,
                       coalesce(left(any_value(error_message), 60), '') AS sample
                FROM fact_model_step WHERE {where_s} AND status = 'error'
                GROUP BY 1, 2, 3, 4""",  # noqa: S608  # constants + fixed columns, not user input
                params_s,
            ).fetchall()
        ]
    return sorted(rows, key=lambda d: -d["n"])


def top_turns_rows(con: duckdb.DuckDBPyConnection, spec: str, limit: int = 8) -> list[dict]:
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT day, source, session_id, turn_id, model_requests, tool_calls,
               round(duration_ms/1000.0, 1) AS dur_s, tokens
        FROM fact_turn WHERE {where}
        ORDER BY model_requests DESC LIMIT {int(limit)}""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "day",
        "source",
        "session_id",
        "turn_id",
        "model_requests",
        "tool_calls",
        "dur_s",
        "tokens",
    ]
    return [dict(zip(cols, r)) for r in rows]


def spend_per_tool_hour_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """ATTACH-bridge leg: usage-store cost per day ÷ tool-seconds per day.
    Requires model_usage.duckdb to exist; returns [] when it doesn't.

    S2 (2026-09-26): the tool-seconds side used to be pinned to
    `source = 'zcode'`, so opencode/prime tool time never entered the
    denominator and their cost in `mu` was dropped by the join instead of
    being divided by their own tool time. Now every source with tool rows
    contributes, and `mu.source` is reported alongside so a cross-source day
    is legible."""
    if not USAGE_DB.is_file():
        return []
    start, end = parse_range(spec, con)
    con.execute(f"ATTACH IF NOT EXISTS '{USAGE_DB}' AS mu (READ_ONLY)")  # noqa: S608  # USAGE_DB is the module constant at :65
    rows = con.execute(
        """
        SELECT u.day,
               sum(u.cost_usd) AS cost_usd,
               round(sum(t.tool_s)/3600.0, 2) AS tool_hours,
               CASE WHEN sum(t.tool_s) >= 60
                    THEN round(sum(u.cost_usd) / (sum(t.tool_s)/3600.0), 2)
               END AS usd_per_tool_hour,
               string_agg(DISTINCT u.source, ',' ORDER BY u.source) AS usage_sources
        FROM mu.fact_usage u
        JOIN (SELECT day, sum(duration_ms)/1000.0 AS tool_s
              FROM fact_tool_call WHERE day BETWEEN ? AND ?
              GROUP BY day) t ON t.day = u.day
        WHERE u.day BETWEEN ? AND ?
        GROUP BY u.day ORDER BY u.day""",
        [start, end, start, end],
    ).fetchall()
    cols = ["day", "cost_usd", "tool_hours", "usd_per_tool_hour", "usage_sources"]
    return [dict(zip(cols, r)) for r in rows]


def file_hotspot_rows(con: duckdb.DuckDBPyConnection, spec: str, limit: int = 12) -> list[dict]:
    """Lane 4: file-level edit attribution (opencode patch parts)."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT path, count(*) AS edits, min(day) AS first_day, max(day) AS last_day
        FROM fact_file_edit WHERE {where}
        GROUP BY path ORDER BY edits DESC, path LIMIT {int(limit)}""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    return [dict(zip(["path", "edits", "first_day", "last_day"], r)) for r in rows]


def reasoning_ratio_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """Lane 4: reasoning-vs-output token ratio per model.

    S2 (zcode_rollout_step_ingest): zcode's token `reasoning` is a
    provider-side structural zero (plan_routing states it), so the token
    table alone reports content-blind 0% for zcode. The rollout steps
    carry reasoning *content* (`reasoning_chars`), merged here per model
    — same grain, no source-set change on the token side. Chars are not
    tokens; the render labels them as such."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT model, count(*) AS reqs, sum(output) AS output,
               sum(reasoning) AS reasoning,
               round(100.0 * sum(reasoning)::DOUBLE / nullif(sum(output), 0), 1)
                   AS reason_pct
        FROM fact_model_request WHERE {where}
        GROUP BY model ORDER BY reason_pct DESC NULLS LAST""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    out = [dict(zip(["model", "reqs", "output", "reasoning", "reason_pct"], r)) for r in rows]
    if _has_step_table(con):
        steps = con.execute(
            f"""
            SELECT model, count(*) AS step_reqs,
                   sum(CASE WHEN reasoning_chars > 0 THEN 1 ELSE 0 END)
                       AS steps_with_reasoning,
                   round(avg(reasoning_chars), 0) AS avg_reason_chars,
                   round(avg(text_chars), 0) AS avg_text_chars
            FROM fact_model_step WHERE {where} AND model IS NOT NULL
            GROUP BY model""",  # noqa: S608  # constants + fixed columns, not user input
            params,
        ).fetchall()
        by_model = {d["model"]: d for d in out}
        for model, step_reqs, with_reas, avg_reas, avg_text in steps:
            row = by_model.get(model)
            step_cols = {
                "step_reqs": step_reqs,
                "steps_with_reasoning": with_reas,
                "avg_reason_chars": int(avg_reas) if avg_reas is not None else None,
                "avg_text_chars": int(avg_text) if avg_text is not None else None,
            }
            if row is None:
                row = {
                    "model": model,
                    "reqs": 0,
                    "output": None,
                    "reasoning": None,
                    "reason_pct": None,
                }
                by_model[model] = row
                out.append(row)
            row.update(step_cols)
    for d in out:
        d.setdefault("step_reqs", None)
    return out


def context_lifecycle_rows(
    con: duckdb.DuckDBPyConnection, spec: str, limit: int = 10
) -> list[dict]:
    """Lane 4: context growth per session — peak cache_read is the best
    context-size proxy the traces carry; compactions come from oc parts."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT r.source, r.session_id, count(*) AS reqs,
               round(max(r.cache_read)/1e6, 1) AS peak_ctx_mtok,
               max(s.compactions) AS compactions,
               min(r.day) AS first_day, max(r.day) AS last_day
        FROM fact_model_request r
        LEFT JOIN dim_session s ON s.source = r.source
                               AND s.session_id = r.session_id
        WHERE r.{where}
        GROUP BY r.source, r.session_id
        ORDER BY peak_ctx_mtok DESC NULLS LAST LIMIT {int(limit)}""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    return [
        dict(
            zip(
                [
                    "source",
                    "session_id",
                    "reqs",
                    "peak_ctx_mtok",
                    "compactions",
                    "first_day",
                    "last_day",
                ],
                r,
            )
        )
        for r in rows
    ]


def _step_compaction_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S2: per-day rollout request-context shape. `full` carries the whole
    context; `tail`/`delta` are harness-compacted (bounded retention), so a
    rising tail share is the compaction cadence the peak-cache curve cannot
    show. Day grain — rendered as its own block, never merged into the
    per-session peak table above."""
    if not _has_step_table(con):
        return []
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT day, count(*) AS steps,
               sum(CASE WHEN messages_kind = 'full' THEN 1 ELSE 0 END) AS full,
               sum(CASE WHEN messages_kind = 'delta' THEN 1 ELSE 0 END) AS delta,
               sum(CASE WHEN messages_kind = 'tail' THEN 1 ELSE 0 END) AS tail,
               round(100.0 * sum(CASE WHEN messages_kind = 'tail' THEN 1 ELSE 0 END)
                     / count(*), 1) AS tail_pct
        FROM fact_model_step WHERE {where}
        GROUP BY day ORDER BY day""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    return [dict(zip(["day", "steps", "full", "delta", "tail", "tail_pct"], r)) for r in rows]


def delegation_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """Lane 4: subagent/delegation lineage — zcode subagent traffic plus the
    prime-rlm spawn/delete ledger."""
    where, params = _day_where(spec, con)
    z = con.execute(
        f"""
        SELECT count(*) AS reqs, coalesce(sum(output), 0) AS out_toks
        FROM fact_model_request WHERE {where} AND query_source = 'subagent'""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchone() or (0, 0)  # count(*) without GROUP BY always yields one row
    ev = con.execute("""
        SELECT event_name, count(*) FROM fact_event
        WHERE source = 'prime' AND event_name LIKE 'delegation.%'
        GROUP BY 1 ORDER BY 1""").fetchall()
    rows = [{"kind": f"zcode subagent requests (out-toks: {z[1]})", "n": z[0]}]
    rows += [{"kind": r[0], "n": r[1]} for r in ev]
    return rows


# ------------------------------------------- S1/S2/S3/S4/S6-S9 legs (2026-09-26)
# Added by doc/improvements/proposals/trace_analyzer_legs.md: report legs over
# columns the store already loads. Two honesty rules ride along, both from the
# proposal's review: never present a partial cost/token total as if it were the
# whole, and never print a column that is structurally zero as if it were a
# measurement (see §2.4 constant-column list).

_UNMATERIALIZED_COST = "not-materialized"


def reliability_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S1: harness warn/error ledger (`fact_log`) by day, component and level.

    The table was loaded since 2026-09-25 with no reader at all, which hid 527
    prime `ai.provider` "provider stream failure" rows and 1419 model-resolution
    warnings. Messages are truncated for display, not filtered: the count is the
    signal, the text is a label."""
    start, end = parse_range(spec, con)
    rows = con.execute(
        """
        SELECT day, component, level, count(*) AS n,
               count(DISTINCT message) AS distinct_msgs,
               left(any_value(message), 60) AS sample
        FROM (SELECT date(ts) AS day, component, level, message FROM fact_log
              WHERE ts BETWEEN ? AND ? + interval 1 day)
        GROUP BY 1, 2, 3 ORDER BY n DESC""",
        [start, end],
    ).fetchall()
    return [
        dict(zip(["day", "component", "level", "n", "distinct_msgs", "sample"], r)) for r in rows
    ]


def spend_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S2: in-store cost + token economics per day and source.

    `fact_model_request.cost_usd` is materialized for every source. zcode's was
    NULL until S5: its `db.sqlite` has no cost column, so the loader now prices
    each request from `zai_usage_query.COST_RATES_PER_M` and records
    `cost_basis='rate:zai_usage_query.COST_RATES_PER_M'`. Per the proposal's
    review the leg MUST still label partial coverage rather than present a
    partial total as total spend, so `cost_coverage` is kept: an unmaterialized
    cost reads `not-materialized`, not `$0.00`. `cost_basis` says how a number
    was derived."""
    start, end = parse_range(spec, con)
    rows = con.execute(
        """
        SELECT day, source, count(*) AS reqs,
               sum(input) AS input, sum(cache_read) AS cache_read,
               sum(output) AS output, sum(reasoning) AS reasoning,
               count(cost_usd) AS costed_reqs,
               round(sum(cost_usd), 4) AS cost_usd,
               coalesce(any_value(cost_basis), 'unspecified') AS cost_basis
        FROM fact_model_request
        WHERE day BETWEEN ? AND ? AND source IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2""",
        [start, end],
    ).fetchall()
    cols = [
        "day",
        "source",
        "reqs",
        "input",
        "cache_read",
        "output",
        "reasoning",
        "costed_reqs",
        "cost_usd",
        "cost_basis",
    ]
    out = []
    for r in (dict(zip(cols, r)) for r in rows):
        r["cost_coverage"] = (
            f"{r['costed_reqs']}/{r['reqs']}" if r["costed_reqs"] else _UNMATERIALIZED_COST
        )
        out.append(r)
    return out


def side_effects_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S3: governance view of tool calls — what touched what, and how often it
    needed approval. These four columns were loaded for every zcode call and
    read by nothing."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT source, tool_name,
               coalesce(side_effect_scope, '(unrecorded)') AS scope,
               coalesce(read_only, false) AS read_only,
               coalesce(destructive, false) AS destructive,
               coalesce(approval_status, '(none)') AS approval,
               count(*) AS calls,
               sum(CASE WHEN error_type IS NOT NULL THEN 1 ELSE 0 END) AS errs
        FROM fact_tool_call WHERE {where}
        GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY calls DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = ["source", "tool", "scope", "read_only", "destructive", "approval", "calls", "errs"]
    return [dict(zip(cols, r)) for r in rows]


def load_health_rows(con: duckdb.DuckDBPyConnection, limit: int = 6) -> list[dict]:
    """S4: per-source load lineage — when each source last landed, how many
    rows, and whether it succeeded. Makes a stale store visible instead of
    silently reported on."""
    rows = con.execute("""
        SELECT source, max(started_at) AS last_load,
               count(*) AS loads,
               sum(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok,
               sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed,
               max(rows) AS last_rows
        FROM load_log GROUP BY source ORDER BY last_load DESC""").fetchall()
    cols = ["source", "last_load", "loads", "ok", "failed", "last_rows"]
    return [dict(zip(cols, r)) for r in rows][: int(limit)]


def validation_rows(con: duckdb.DuckDBPyConnection, limit: int = 20) -> list[dict]:
    """S4: per-harness validation drift from load_log outcome columns.
    S3 (zcode_rollout_step_ingest): the rollout row also carries the
    token-parity invariant — input + cache_read + output must equal the
    provider-reported total_tokens (0 = clean). Any other value is
    parser drift to fix, never normalized away."""
    rows = con.execute("""
        SELECT source, count(*) AS loads,
               sum(rows) AS rows,
               sum(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok,
               sum(coalesce(rows_ok, 0)) AS rows_ok,
               sum(coalesce(rows_rejected, 0)) AS rows_rejected,
               sum(coalesce(unknown_fields, 0)) AS unknown_fields
        FROM load_log GROUP BY source ORDER BY rows_rejected DESC, unknown_fields DESC""").fetchall()
    cols = ["source", "loads", "rows", "ok", "rows_ok", "rows_rejected", "unknown_fields"]
    out = [dict(zip(cols, r)) for r in rows][: int(limit)]
    if _has_step_table(con):
        # Error steps carry NULL usage — sum() skips them; parity is over
        # steps that actually report tokens. Keyed on the rollout row only
        # (load_log uses the loader name 'rollout'; the fact rows carry
        # source 'zcode_rollout') — the invariant does not exist for the
        # request-grain sources.
        row = con.execute(
            "SELECT sum(input + cache_read + output - total_tokens) FROM fact_model_step"
        ).fetchone()
        parity = row[0] if row else None
        for r in out:
            if r["source"] == "rollout":
                r["token_parity"] = parity
    return out


# trace_error_forensics S3 (capture_traces.md §9.4.2, upstream-verified
# 2026-10-02 against github.com/zai-org/ZCode): the never/always-zero
# request-fact columns carry THREE different causes, and the §9.4.1
# "provider-absent" label was right for only reasoning and cache_write.
# The labels ride the leg so a reader never re-derives them; the live
# counts beside them are the early warning if one ever turns nonzero.
_DISPOSITIONS: dict[str, dict[str, str]] = {
    "reasoning": {
        "zcode": "provider-absent (normalized payload has no reasoning key; folded into output)",
        "opencode": "alive (step-finish parts carry tokens.reasoning)",
        "prime": "absent (source does not carry it)",
    },
    "cache_write": {
        "zcode": "provider-absent in substance (cacheWriteTokens key present, always 0: implicit caching, reads only)",
        "opencode": "none observed",
        "prime": "none observed",
    },
    "retry_count": {
        "*": "harness-wired, none observed (client-side retry-scheduled counter)",
    },
    "context_exceeded": {
        "*": "harness-wired, none observed (failure classification)",
    },
    "error_code": {
        "*": "class-gated, never fired (set only for harness CoreError)",
    },
    "error_message": {
        "zcode": "populated (non-completed requests only; S11 residue closed)",
        "opencode": "NULL by design (source does not carry it)",
        "prime": "NULL by design (source does not carry it)",
    },
}
_TEXT_DISPOSITION_COLS = {"error_code", "error_message"}


def column_disposition_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """S3: per-column zero/population counts beside their §9.4.2 dispositions."""
    rows: list[dict] = []
    for col, per_source in _DISPOSITIONS.items():
        if col in _TEXT_DISPOSITION_COLS:
            expr = f"{col} IS NOT NULL AND {col} <> ''"
        else:
            expr = f"{col} IS NOT NULL AND {col} <> 0"
        counts = {
            src: (total, nonzero)
            for src, total, nonzero in con.execute(
                f"SELECT source, COUNT(*), SUM(CASE WHEN {expr} THEN 1 ELSE 0 END) "  # noqa: S608  # constants + fixed columns, not user input
                f"FROM fact_model_request GROUP BY source"
            ).fetchall()
        }
        for src in sorted(counts):
            total, nonzero = counts[src]
            label = per_source.get(src) or per_source.get("*") or "unlabeled"
            rows.append(
                {
                    "column": col,
                    "source": src,
                    "nonzero": nonzero,
                    "total": total,
                    "disposition": label,
                }
            )
    return rows


def failure_forensics_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S6: tool failures by exit code, not just by recorded `error_type`.

    Measured 2026-09-26 (after the 60-day zcode reload, 6531 calls): 93 rows
    carry an error_type, 96 have a non-zero `exit_code`, and the two sets are
    DISJOINT (union 189) — an error_type-only taxonomy reports half the
    failures. `exit_code` coverage is 3661/6531; the rest never recorded one,
    which `unclassified` counts."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT source, tool_name,
               CASE WHEN exit_code IS NULL THEN 'unclassified'
                    WHEN exit_code = 0 THEN 'clean'
                    ELSE 'exit ' || exit_code END AS outcome,
               count(*) AS calls,
               sum(coalesce(stderr_bytes, 0)) AS stderr_bytes
        FROM fact_tool_call WHERE {where}
        GROUP BY 1, 2, 3 ORDER BY calls DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = ["source", "tool", "outcome", "calls", "stderr_bytes"]
    return [dict(zip(cols, r)) for r in rows]


def _step_error_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S2: rollout step errors by harness error name (quota overruns, stream
    failures — the model never answered). Step grain, so it renders as its
    own block: folding these into the tool table above would print
    structurally-zero tool columns as measurements."""
    if not _has_step_table(con):
        return []
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT error_name, count(*) AS n, min(day) AS first_day,
               max(day) AS last_day,
               coalesce(left(any_value(error_message), 80), '') AS sample
        FROM fact_model_step WHERE {where} AND status = 'error'
        GROUP BY error_name ORDER BY n DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    return [dict(zip(["error_name", "n", "first_day", "last_day", "sample"], r)) for r in rows]


def plan_routing_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S5: which zcode plan/variant is actually being burned.

    This is the leg the plan-routing hole was filed for: `provider` x `variant`
    had no cost attached, so the effort ladder (variant is the effort knob)
    had no outcome signal. Cost is now materialized at load time from the
    published rate table, so cost-per-request and latency can be read per
    plan.

    The effort ladder is recorded but has NO reasoning signal: zcode's
    `reasoning_tokens` is 0 in 3160/3160 source rows and no reasoning key
    exists in any sampled `raw_usage_json`, so the provider does not report
    it. `reasoning` is therefore 0 here by construction — that is the
    provider's answer, not a loader gap. The ladder spans high 1752 / max 1396
    / low 7 / disabled 5.

    Scoped to zcode deliberately. `provider` is overloaded across sources and a
    combined table would conflate two taxonomies: for zcode it holds the
    *plan* (`account:zai-individual-coding-plan`, `builtin:zai-coding-plan`,
    …) which is what routing means, while for opencode/prime it holds the
    *model vendor* (`openrouter`, `opencode`, `Atria`, `Zhipu`, `Inception`).
    `variant` is zcode-only in any case. Other harnesses' cost is S2's
    per-source total; mixing them in here would bury the plan answer under
    2,306 rows that carry no plan and no effort.

    Cost basis is `rate` (published per-1M rates), which is a different
    basis from opencode/prime's `harness` figure; the leg prints the basis
    so the two are never silently added together."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT provider, coalesce(variant, '(none)') AS variant, model,
               count(*) AS reqs,
               count(cost_usd) AS priced,
               round(sum(cost_usd), 4) AS cost_usd,
               round(avg(cost_usd), 6) AS avg_cost,
               round(avg(duration_ms) / 1000.0, 2) AS avg_s,
               round(quantile_cont(duration_ms / 1000.0, 0.5), 2) AS p50_s,
               sum(input) AS fresh_in, sum(cache_read) AS cached,
               sum(output) AS output, sum(reasoning) AS reasoning,
               any_value(cost_basis) AS cost_basis
        FROM fact_model_request
        WHERE {where} AND source = 'zcode' AND provider IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY cost_usd DESC NULLS LAST, reqs DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "provider",
        "variant",
        "model",
        "reqs",
        "priced",
        "cost_usd",
        "avg_cost",
        "avg_s",
        "p50_s",
        "fresh_in",
        "cached",
        "output",
        "reasoning",
        "cost_basis",
    ]
    out = []
    for r in (dict(zip(cols, r)) for r in rows):
        r["cost_coverage"] = f"{r['priced']}/{r['reqs']}" if r["priced"] else _UNMATERIALIZED_COST
        out.append(r)
    return out


def session_economics_rows(
    con: duckdb.DuckDBPyConnection, spec: str, limit: int = 10
) -> list[dict]:
    """S7: per-session cost, token split and code churn.

    Two cost columns, because they measure different things and S5 widened
    the gap. `session_cost_usd` is what the harness wrote on the session row
    (zcode's `session` table carries no cost, so it is NULL there and its
    $0.77 total silently covered only opencode/prime). `req_cost_usd` is
    summed from fact_model_request, which after S5 covers every source.
    `session_cost_coverage` says which of the session's requests the session
    figure actually accounts for, so a partial session cost can never be read
    as the session's cost.

    `rc` is keyed on (source, session_id), not session_id alone: nothing
    guarantees a harness's id space is disjoint from another's, and a shared id
    would silently merge two sessions' costs. No collision exists today (0
    session_ids appear under more than one source), so this is hardening, not
    a fix. `rc` is deliberately unfiltered by day — a session's cost is its
    lifetime cost — while the outer `WHERE` bounds which sessions are listed."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        WITH rc AS (
            SELECT source, session_id, count(*) AS reqs,
                   count(cost_usd) AS reqs_priced,
                   round(sum(cost_usd), 4) AS req_cost,
                   sum(input) AS fresh_in, sum(cache_read) AS cached
            FROM fact_model_request WHERE source IS NOT NULL
            GROUP BY 1, 2)
        SELECT s.source, s.session_id, s.day, s.title, s.directory,
               s.cost_usd AS session_cost_usd,
               rc.req_cost AS req_cost_usd,
               coalesce(rc.reqs, 0) AS reqs,
               coalesce(rc.reqs_priced, 0) AS reqs_priced,
               s.input, s.output, s.reasoning, s.cache_read,
               s.adds, s.dels, s.files, s.compactions
        FROM dim_session s
        LEFT JOIN rc ON rc.session_id = s.session_id AND rc.source = s.source
        WHERE s.day BETWEEN ? AND ?
        ORDER BY coalesce(rc.req_cost, 0) DESC, s.session_id
        LIMIT {int(limit)}""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "source",
        "session_id",
        "day",
        "title",
        "directory",
        "session_cost_usd",
        "req_cost_usd",
        "reqs",
        "reqs_priced",
        "input",
        "output",
        "reasoning",
        "cache_read",
        "adds",
        "dels",
        "files",
        "compactions",
    ]
    out = []
    for r in (dict(zip(cols, r)) for r in rows):
        if r["session_cost_usd"] is None:
            r["session_cost_coverage"] = _UNMATERIALIZED_COST
        elif r["reqs"]:
            r["session_cost_coverage"] = (
                f"session-recorded, {r['reqs_priced']}/{r['reqs']} reqs priced"
            )
        else:
            r["session_cost_coverage"] = "session-recorded, no request rows"
        out.append(r)
    return out


def overhead_tax_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S8: where the tokens actually go, split by `query_source`.

    `query_source` is a zcode-only column, so every opencode+prime request
    lands in the NULL bucket — 460.2M input+cache tokens as of 2026-09-26,
    more than every labelled bucket except `main_turn`. Those rows are not
    missing data, they are a *different taxonomy*: they carry materialized
    cost and no query_source. So the bucket is named by its actual membership
    (`sources` shows who is in it) and kept in the denominator — dropping it
    would silently halve the corpus.

    Token basis is input+cache_read: a re-read of cached context is still
    context the provider served, and input-only understates it ~2x here."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT coalesce(query_source, '(no query_source)') AS bucket,
               string_agg(DISTINCT source, ',' ORDER BY source) AS sources,
               count(*) AS reqs,
               sum(input + cache_read) AS context_tokens,
               sum(input) AS fresh_input,
               sum(cache_read) AS cached,
               sum(output) AS output,
               count(cost_usd) AS costed_reqs,
               round(sum(cost_usd), 4) AS cost_usd
        FROM fact_model_request WHERE {where}
        GROUP BY 1 ORDER BY context_tokens DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "bucket",
        "sources",
        "reqs",
        "context_tokens",
        "fresh_input",
        "cached",
        "output",
        "costed_reqs",
        "cost_usd",
    ]
    out = []
    for r in (dict(zip(cols, r)) for r in rows):
        # Same honesty rule as S2: a bucket with no materialized cost reports
        # NULL plus coverage `not-materialized`, not a $0.00 reading "free".
        r["cost_coverage"] = (
            f"{r['costed_reqs']}/{r['reqs']}" if r["costed_reqs"] else _UNMATERIALIZED_COST
        )
        out.append(r)
    return out


def turn_quality_rows(con: duckdb.DuckDBPyConnection, spec: str) -> list[dict]:
    """S9: did turns go wrong? `agentic_depth_rows` counts tool calls per turn
    but never the error rate, which `fact_turn.tool_errors` carries for every
    row.

    `tokens` here is an EXPENDITURE aggregate (the harness's own
    `computed_total_tokens` = sum over the turn's sequential requests, each
    re-reading the growing cached context), NOT a context size — the 2026-09-11
    22M turn was 134 requests at 325K each with peak single-request
    cache_read 0.9% of the total. Do not read it as an overrun
    (`capture_traces.md` §9.6)."""
    where, params = _day_where(spec, con)
    rows = con.execute(
        f"""
        SELECT source,
               count(*) AS turns,
               sum(CASE WHEN tool_errors > 0 THEN 1 ELSE 0 END) AS with_tool_err,
               round(100.0 * sum(CASE WHEN tool_errors > 0 THEN 1 ELSE 0 END)
                     / count(*), 2) AS tool_err_pct,
               sum(tool_errors) AS tool_errors,
               sum(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
               sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errored,
               round(quantile_cont(tool_errors, 0.5), 1) AS p50_tool_errs,
               max(tool_errors) AS max_tool_errs
        FROM fact_turn WHERE {where} GROUP BY source ORDER BY turns DESC""",  # noqa: S608  # constants + fixed columns, not user input
        params,
    ).fetchall()
    cols = [
        "source",
        "turns",
        "with_tool_err",
        "tool_err_pct",
        "tool_errors",
        "cancelled",
        "errored",
        "p50_tool_errs",
        "max_tool_errs",
    ]
    return [dict(zip(cols, r)) for r in rows]


def _fmt(value, spec: str = ".1f") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:{spec}}"
    return str(value)


def report(  # noqa: C901  # moved verbatim; split needs a parity harness (c901 deferred class)
    con: duckdb.DuckDBPyConnection, spec: str, as_json: bool, only: str | None = None
) -> None:
    """Render the report legs. `only` is a comma-separated leg-name filter
    (the S1-S9 growth made the unfiltered text output unwieldy). An unknown
    name filters to nothing rather than erroring; main() warns about it first
    against REPORT_LEGS, so a typo is visible without losing the whole run."""
    legs = {
        "model_latency": model_latency_rows(con, spec),
        "tool_economics": tool_economics_rows(con, spec),
        "agentic_depth": agentic_depth_rows(con, spec),
        "error_taxonomy": error_taxonomy_rows(con, spec),
        "failure_forensics": failure_forensics_rows(con, spec),
        "top_turns": top_turns_rows(con, spec),
        "turn_quality": turn_quality_rows(con, spec),
        "file_hotspots": file_hotspot_rows(con, spec),
        "side_effects": side_effects_rows(con, spec),
        "reasoning_ratio": reasoning_ratio_rows(con, spec),
        "overhead_tax": overhead_tax_rows(con, spec),
        "spend": spend_rows(con, spec),
        "plan_routing": plan_routing_rows(con, spec),
        "context_lifecycle": context_lifecycle_rows(con, spec),
        "session_economics": session_economics_rows(con, spec),
        "delegation": delegation_rows(con, spec),
        "reliability": reliability_rows(con, spec),
        "spend_per_tool_hour": spend_per_tool_hour_rows(con, spec),
        "load_health": load_health_rows(con),
        "validation": validation_rows(con),
        "column_dispositions": column_disposition_rows(con),
    }
    if only:
        wanted = {n.strip() for n in only.split(",") if n.strip()}
        legs = {k: v for k, v in legs.items() if k in wanted}
    if as_json:
        import json

        print(json.dumps({"range": spec, **legs}, indent=2, default=str))
        return
    start, end = parse_range(spec, con)
    print(f"== agent traces  {start}..{end} ==")
    if "model_latency" in legs:
        print("\n== model latency (LLM requests) ==")
        for r in legs["model_latency"]:
            print(
                f"  {r['model']:<24} {r['reqs']:>5} reqs  p50 {_fmt(r['p50_s'], '.2f')}s"
                f"  p95 {_fmt(r['p95_s'])}s  max {_fmt(r['max_s'])}s"
                f"  ttft p50 {_fmt(r['ttft_p50_s'], '.2f')}s"
                f"  out {_fmt(r['out_tok_s'])} tok/s  errs {r['errs']}"
                f"  retries {r['retries']}  cancelled {r['cancelled']}"
            )
    if "tool_economics" in legs:
        print("\n== tool economics ==")
        for r in legs["tool_economics"]:
            print(
                f"  {r['source']:<8} {r['tool']:<18} {r['calls']:>5} calls"
                f"  p50 {_fmt(r['p50_s'], '.2f')}s  p95 {_fmt(r['p95_s'])}s"
                f"  max {_fmt(r['max_s'])}s  err {r['err_pct']}%"
                f"  avg out {_fmt(r['avg_out_bytes'], '.0f')}B"
                f"  cancelled {r['cancelled']}"
            )
    if "agentic_depth" in legs:
        d_rows = legs["agentic_depth"]
        print("\n== agentic depth ==")
        if not d_rows:
            print("  (no turns in range)")
        else:
            d = d_rows[0]
            print(
                f"  turns {d['turns']}  mean reqs/turn {d['mean_reqs_per_turn']}"
                f"  p50 {d['p50_reqs']}  max {d['max_reqs']}"
                f"  mean tools/turn {d['mean_tools_per_turn']}  max {d['max_tools']}"
                f"  total {_fmt(d['total_min'])} min"
            )
    if "error_taxonomy" in legs:
        print("\n== error taxonomy ==")
        for r in legs["error_taxonomy"][:12]:
            print(
                f"  {r['source']:<8} {r['kind']:<22} {r['error_type']:<24}"
                f" {r['error_code']:<8} n={r['n']}"
            )
        if not legs["error_taxonomy"]:
            print("  (no errors in range)")
    if "failure_forensics" in legs:
        print("\n== tool failure forensics (exit-code view; error-typed rows are disjoint) ==")
        for r in legs["failure_forensics"]:
            if r["outcome"] in ("clean", "unclassified") and r["calls"] < 5:
                continue
            print(
                f"  {r['source']:<8} {r['tool']:<18} {r['outcome']:<14}"
                f" {r['calls']:>5} calls  stderr {_fmt(r['stderr_bytes'], '.0f')}B"
            )
        if not legs["failure_forensics"]:
            print("  (no tool rows in range)")
        serr = _step_error_rows(con, spec)  # S2: step grain, own block
        if serr:
            print("  -- step errors: model never answered (rollout, not tool outcomes) --")
            for e in serr:
                print(
                    f"  {str(e['error_name']):<28} {e['n']:>3} steps"
                    f"  {e['first_day']}..{e['last_day']}  {e['sample']}"
                )
    if "top_turns" in legs:
        print("\n== top turns by model requests (tokens = expenditure, not context) ==")
        for r in legs["top_turns"]:
            print(
                f"  {r['source']:<8} {r['day']}"
                f"  {str(r['session_id'])[:16]}…  reqs {r['model_requests']:>3}"
                f"  tools {r['tool_calls']:>3}  {_fmt(r['dur_s'])}s  tokens {r['tokens']}"
            )
    if "turn_quality" in legs:
        print("\n== turn quality (tool-error rate per turn) ==")
        for r in legs["turn_quality"]:
            print(
                f"  {r['source']:<8} turns {r['turns']:>5}  with tool err"
                f" {r['with_tool_err']:>3} ({_fmt(r['tool_err_pct'], '.2f')}%)"
                f"  total tool errs {r['tool_errors']:>3}  p50 {r['p50_tool_errs']}"
                f"  max {r['max_tool_errs']}  cancelled {r['cancelled']}"
                f"  errored {r['errored']}"
            )
    if "file_hotspots" in legs:
        print("\n== file-edit hotspots (opencode patches) ==")
        for r in legs["file_hotspots"]:
            print(f"  {r['edits']:>3}x  {r['path']}  ({r['first_day']}..{r['last_day']})")
        if not legs["file_hotspots"]:
            print("  (no patch rows in range)")
    if "side_effects" in legs:
        print("\n== side effects & approval (zcode-governed calls) ==")
        for r in legs["side_effects"]:
            print(
                f"  {r['source']:<8} {r['tool']:<18} scope {r['scope']:<16}"
                f" read_only {str(r['read_only']):<5} destructive {str(r['destructive']):<5}"
                f" approval {r['approval']:<8} calls {r['calls']:>5}  errs {r['errs']}"
            )
        if not legs["side_effects"]:
            print("  (no tool rows in range)")
    if "reasoning_ratio" in legs:
        print("\n== reasoning vs output per model ==")
        for r in legs["reasoning_ratio"]:
            print(
                f"  {str(r['model']):<34} {r['reqs']:>5} reqs  reasoning"
                f" {r['reasoning']}  output {r['output']}  = {r['reason_pct']}%"
            )
            if r.get("step_reqs"):  # S2: rollout step content (chars, not tokens)
                print(
                    f"  {'':<34} steps {r['step_reqs']:>5}"
                    f"  ({r['steps_with_reasoning']} w/ reasoning,"
                    f" avg {r['avg_reason_chars']} reasoning /"
                    f" {r['avg_text_chars']} text chars)"
                )
    if "overhead_tax" in legs:
        print("\n== token tax by query_source (context = input + cache_read) ==")
        for r in legs["overhead_tax"]:
            print(
                f"  {r['bucket']:<18} {r['sources']:<17} {r['reqs']:>5} reqs"
                f"  context {r['context_tokens']:>13,}"
                f"  (fresh {r['fresh_input']:,} / cached {r['cached']:,})"
                f"  output {r['output']:>9,}"
                f"  cost {r['cost_usd']} [{r['cost_coverage']}]"
            )
    if "spend" in legs:
        print("\n== spend by day and source (in-store cost; unmaterialized sources labelled) ==")
        for r in legs["spend"]:
            print(
                f"  {r['day']}  {r['source']:<9} {r['reqs']:>5} reqs"
                f"  in {r['input']:>11,}  cached {r['cache_read']:>11,}"
                f"  out {r['output']:>9,}  cost {r['cost_usd']}"
                f"  [{r['cost_coverage']} reqs, basis {r['cost_basis']}]"
            )
        if not legs["spend"]:
            print("  (no requests in range)")
    if "plan_routing" in legs:
        print("\n== plan routing (zcode plan x variant; cost from published rates) ==")
        for r in legs["plan_routing"]:
            print(
                f"  {r['provider']:<34} {r['variant']:<9} {r['model']:<16}"
                f" {r['reqs']:>5} reqs  cost {_fmt(r['cost_usd'], '.4f'):>9}"
                f"  avg {_fmt(r['avg_cost'], '.4f')}"
                f"  p50 {_fmt(r['p50_s'])}s"
                f"  in {r['fresh_in']:,} / cached {r['cached']:,}"
                f" / out {r['output']:,}"
                f"  [{r['cost_coverage']}, {r['cost_basis']}]"
            )
        if not legs["plan_routing"]:
            print("  (no zcode rows with a provider in range)")
    if "context_lifecycle" in legs:
        print("\n== context lifecycle (peak cache-read per session, top 10) ==")
        for r in legs["context_lifecycle"]:
            print(
                f"  {r['source']:<8} {str(r['session_id'])[:20]}…  {r['reqs']:>4} reqs"
                f"  peak ctx {r['peak_ctx_mtok']} Mtok  compactions {r['compactions']}"
                f"  {r['first_day']}..{r['last_day']}"
            )
        comp = _step_compaction_rows(con, spec)  # S2: day grain, own block
        if comp:
            print("  -- request-context shape per day (rollout; tail/delta = compacted) --")
            for c in comp:
                print(
                    f"  {c['day']}  steps {c['steps']:>4}  full {c['full']:>4}"
                    f"  delta {c['delta']:>4}  tail {c['tail']:>4}"
                    f"  tail {c['tail_pct']}%"
                )
    if "session_economics" in legs:
        print(
            "\n== session economics (request-derived cost is complete; session cost is partial) =="
        )
        for r in legs["session_economics"]:
            print(
                f"  {r['source']:<8} {str(r['session_id'])[:20]}…  {r['day']}"
                f"  req-cost {r['req_cost_usd']}  session-cost {r['session_cost_usd']}"
                f"  [{r['session_cost_coverage']}]"
                f"  in {r['input']} / out {r['output']} / cached {r['cache_read']}"
                f"  +{r['adds']}/-{r['dels']} over {r['files']} files"
                f"  compactions {r['compactions']}"
            )
        if not legs["session_economics"]:
            print("  (no sessions in range)")
    if "delegation" in legs:
        print("\n== delegation ==")
        for r in legs["delegation"]:
            print(f"  {r['kind']}: {r['n']}")
    if "reliability" in legs:
        print("\n== reliability (harness warn/error ledger) ==")
        for r in legs["reliability"]:
            print(
                f"  {r['day']}  {r['level']:<5} {r['component']:<34}"
                f" n={r['n']:<5} distinct={r['distinct_msgs']:<5} {r['sample']}"
            )
        if not legs["reliability"]:
            print("  (no log rows in range)")
    if "spend_per_tool_hour" in legs:
        print("\n== spend per tool-hour (ATTACH model_usage.duckdb) ==")
        for r in legs["spend_per_tool_hour"]:
            rate = (
                f"${_fmt(r['usd_per_tool_hour'], '.2f')}/h"
                if r["usd_per_tool_hour"] is not None
                else "n/a (<1 min tool time)"
            )
            print(
                f"  {r['day']}  ${_fmt(r['cost_usd'], '.2f')}  tool-hours"
                f" {_fmt(r['tool_hours'], '.2f')}  {rate}"
                f"  [{r['usage_sources']}]"
            )
        if not legs["spend_per_tool_hour"]:
            print("  (model_usage.duckdb not available or no overlapping days)")
    if "load_health" in legs:
        print("\n== load health (store freshness) ==")
        for r in legs["load_health"]:
            print(
                f"  {r['source']:<9} last {r['last_load']}  loads {r['loads']}"
                f"  ok {r['ok']}  failed {r['failed']}  last rows {r['last_rows']}"
            )
        if not legs["load_health"]:
            print("  (no load_log rows)")
    if "validation" in legs:
        print("\n== validation drift (load_log outcome columns) ==")
        for r in legs["validation"]:
            line = (
                f"  {r['source']:<9} loads {r['loads']}  rows {r['rows']}"
                f"  ok {r['ok']}  accepted {r['rows_ok']}  rejected {r['rows_rejected']}"
                f"  unknown fields {r['unknown_fields']}"
            )
            if r.get("token_parity") is not None:
                line += f"  token parity {r['token_parity']}"
                if r["token_parity"] != 0:
                    line += "  DRIFT"
            print(line)
        if not legs["validation"]:
            print("  (no load_log rows)")
    if "column_dispositions" in legs:
        print(
            "\n== column dispositions (capture_traces.md §9.4.2, upstream-verified 2026-10-02) =="
        )
        for r in legs["column_dispositions"]:
            print(
                f"  {r['column']:<18} {r['source']:<9} nonzero {r['nonzero']:>5}"
                f"/{r['total']:<6} {r['disposition']}"
            )
        if not legs["column_dispositions"]:
            print("  (store empty)")


# ------------------------------------------------------------------ main ----

LOADERS = {name: trace_contracts.load for name in ("zcode", "opencode", "prime", "rollout")}

# Leg names accepted by `report --legs`, in render order. Kept beside the
# loader registry so `--help` and an unknown-name warning have one source.
REPORT_LEGS = (
    "model_latency",
    "tool_economics",
    "agentic_depth",
    "error_taxonomy",
    "failure_forensics",
    "top_turns",
    "turn_quality",
    "file_hotspots",
    "side_effects",
    "reasoning_ratio",
    "overhead_tax",
    "spend",
    "plan_routing",
    "context_lifecycle",
    "session_economics",
    "delegation",
    "reliability",
    "spend_per_tool_hour",
    "load_health",
    "validation",
    "column_dispositions",
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Agent behavioral-trace analytics (local DuckDB store)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="  load   zcode (opencode/prime land in S3/S4)\n"
        "  report  latency / tool economics / depth / errors legs",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("load", help="refresh a source into the local store")
    lp.add_argument("source", choices=[*LOADERS, "all"])
    lp.add_argument(
        "--days",
        type=int,
        default=2,
        help="incremental: refresh only the last N days, leaving older "
        "rows untouched (default 2). Use --full for a complete reload.",
    )
    lp.add_argument(
        "--full", action="store_true", help="ignore --days; delete and reload the whole source"
    )
    lp.add_argument("--db", type=Path, default=DB_PATH)
    rp = sub.add_parser("report", help="query the stored data")
    rp.add_argument(
        "--range",
        default="all",
        help="all (store span, default) | Nd | Nw | Nm | YYYY-MM-DD | YYYY-MM-DD..YYYY-MM-DD",
    )
    rp.add_argument(
        "--json", action="store_true", help="emit JSON (for piping into jq / other tools)"
    )
    rp.add_argument(
        "--legs",
        help="comma-separated leg names to include (default: all). "
        "Run without --legs to see the full list; unknown names "
        "are reported and ignored.",
    )
    rp.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args()

    # S3 (duckdb_transient_lock_retry): the bench ETL/report window holds
    # the file RW for its whole duration — LOCK_EX on <db>.io.lock makes
    # a concurrent analytics reader queue instead of colliding; the open
    # retries short foreign holders on the bounded ladder.
    from helpers.misc.duckdb_lock import connect_with_lock_retry, io_lock

    with io_lock(args.db, exclusive=True):
        con = connect_with_lock_retry(lambda: duckdb.connect(str(args.db)))
        con.execute(SCHEMA)
        _ensure_load_log_outcome_columns(con)
        _ensure_model_request_error_message(con)
        load_ok = False
        from helpers.maintenance.maint_timing import RunTimer

        _timer = RunTimer(
            "agent_traces", mode=f"load:{args.source}" if args.cmd == "load" else args.cmd
        )
        _loaded_total = 0
        try:
            if args.cmd == "load":
                srcs = list(LOADERS) if args.source == "all" else [args.source]
                for s in srcs:
                    started = datetime.now(UTC)
                    since = date.today() - timedelta(days=args.days) if not args.full else None
                    try:
                        _p0 = datetime.now(UTC)
                        n = trace_contracts.load(con, s, since)
                        _loaded_total += n or 0
                        _timer.record_phase(f"load:{s}", _p0, datetime.now(UTC), extra=f"rows={n}")
                        detail = "; ".join(_load_log_extra) or f"since={since}"
                        vs = trace_contracts.validation_summary()
                        con.execute(
                            "INSERT INTO load_log VALUES (?, ?, ?, 'ok', ?, ?, ?, ?)",
                            [
                                s,
                                started,
                                n,
                                detail,
                                vs["rows_ok"],
                                vs["rows_rejected"],
                                vs["unknown_field_count"],
                            ],
                        )
                        print(f"  loaded {s}: {n} rows (since={since})")
                    except Exception as exc:
                        detail = "; ".join(_load_log_extra) or ""
                        vs = trace_contracts.validation_summary()
                        con.execute(
                            "INSERT INTO load_log VALUES (?, ?, ?, 'error', ?, ?, ?, ?)",
                            [
                                s,
                                started,
                                -1,
                                f"{detail} {exc}"[:200],
                                vs["rows_ok"],
                                vs["rows_rejected"],
                                vs["unknown_field_count"],
                            ],
                        )
                        print(f"  FAILED {s}: {exc}", file=sys.stderr)
                        raise
                load_ok = True
            else:
                if args.legs:
                    bad = [
                        n.strip()
                        for n in args.legs.split(",")
                        if n.strip() and n.strip() not in REPORT_LEGS
                    ]
                    if bad:
                        print(f"WARNING: unknown leg(s) ignored: {', '.join(bad)}", file=sys.stderr)
                report(con, args.range, as_json=args.json, only=args.legs)
        finally:
            con.close()
    if args.cmd == "load":
        _timer.finish(0, f"load:{args.source} rows={_loaded_total}")
    if args.cmd == "load" and load_ok:
        _refresh_backup(args.db)


if __name__ == "__main__":
    main()
