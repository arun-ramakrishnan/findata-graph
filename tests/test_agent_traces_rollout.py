#!/usr/bin/env python3
"""zcode_rollout_step_ingest S1 tests — rollout parser mapping and load semantics.

Synthetic fixtures only: no real transcript content enters tracked files
(data-discretion rule). The parser runs against a tmp rollout dir and a
tmp duckdb; the live-store shakedown (`load rollout --full` == parsed
record count, token parity, idempotent re-run) is AC#1, verified manually.

Proposal: doc/improvements/proposals/zcode_rollout_step_ingest.md (S1).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.analytics import agent_traces, trace_contracts  # noqa: E402

LONG_TEXT = "lorem ipsum dolor sit amet " * 500  # ~13.5K chars: exercises the head cap


def _step(**kw: object) -> dict:
    rec = {
        "type": "model_io",
        "requestId": "req-0001",
        "sessionId": "sess-aaa",
        "turnId": "turn-1",
        "traceId": "trace-1",
        "attempt": 1,
        "querySource": "main_turn",
        "startedAt": "2026-09-28T12:00:00.000Z",
        "completedAt": "2026-09-28T12:00:07.000Z",
        "durationMs": 7000,
        "model": {"modelId": "GLM-5.3-Flash", "providerId": "account:zai-start-plan"},
        "request": {
            "messages": [],
            "toolNames": ["Read", "Bash"],
            "messageCount": 42,
            "messagesKind": "tail",
            "messageOffset": 20,
        },
        "response": {
            "finishReason": "tool-calls",
            "text": "done",
            "reasoningText": "because",
            "toolCalls": [{"id": "call_1", "name": "Read", "input": {"file_path": "/x"}}],
            "usage": {
                "inputTokens": 1000,
                "outputTokens": 50,
                "totalTokens": 1050,
                "cacheReadTokens": 200,
                "cacheWriteTokens": 0,
            },
        },
    }
    rec.update(kw)
    return rec


def _error_step() -> dict:
    rec = _step(
        requestId="req-0002",
        startedAt="2026-09-29T12:00:00.000Z",
        completedAt="2026-09-29T12:00:01.000Z",
    )
    rec["response"] = {"toolCalls": []}
    rec["error"] = {
        "name": "TerminalStreamChunkError",
        "message": "exceed quota limit",
        "stack": "TerminalStreamChunkError: exceed quota limit\n    at a\n    at b",
    }
    return rec


def _fixture_dir(tmp_path: Path) -> Path:
    d = tmp_path / "rollout"
    d.mkdir()
    with open(d / "model-io-sess-aaa.jsonl", "w") as fh:
        fh.write(json.dumps(_step()) + "\n")
        fh.write(
            json.dumps(
                _step(
                    requestId="req-0003",
                    response={
                        "finishReason": "stop",
                        "text": LONG_TEXT,
                        "reasoningText": LONG_TEXT,
                        "toolCalls": [],
                        "usage": {
                            "inputTokens": 500,
                            "outputTokens": 25,
                            "totalTokens": 525,
                            "cacheReadTokens": 100,
                            "cacheWriteTokens": 0,
                        },
                    },
                )
            )
            + "\n"
        )
        fh.write(json.dumps(_error_step()) + "\n")
        fh.write("not json\n")
        fh.write(json.dumps({"type": "model_io"}) + "\n")  # no requestId: skipped
        fh.write(json.dumps(_step(requestId="req-0004", startedAt=None)) + "\n")  # no ts
    return d


def _con(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute(agent_traces.SCHEMA)
    return con


def _row(con: duckdb.DuckDBPyConnection, rid: str) -> dict:
    cols = trace_contracts.table_cols("fact_model_step")
    vals = con.execute(
        f"SELECT {', '.join(cols)} FROM fact_model_step WHERE source='zcode_rollout'"  # noqa: S608  # constants + fixed columns, not user input
        " AND request_id=?",
        [rid],
    ).fetchone()
    return dict(zip(cols, vals))


def test_full_load_maps_fields(tmp_path: Path) -> None:
    con, d = _con(tmp_path), _fixture_dir(tmp_path)
    trace_contracts.reset_validation()
    assert agent_traces._parse_rollout(con, None, directory=d) == 3
    assert trace_contracts.validation_summary()["rows_rejected"] == 0

    r1 = _row(con, "req-0001")
    assert r1["session_id"] == "sess-aaa"
    assert r1["input"] == 800  # 1000 − 200 cacheRead (store convention)
    assert r1["output"] == 50 and r1["cache_read"] == 200
    assert r1["total_tokens"] == 1050  # parity anchor untouched
    assert r1["status"] == "completed" and r1["finish_reason"] == "tool-calls"
    assert r1["tool_call_count"] == 1 and r1["messages_kind"] == "tail"
    assert r1["message_offset"] == 20 and r1["message_count"] == 42
    assert r1["day"] == date(2026, 9, 28)
    tools = json.loads(r1["tools_json"])
    assert tools[0]["name"] == "Read"

    r3 = _row(con, "req-0003")
    assert r3["text_chars"] == len(LONG_TEXT)  # exact count ...
    assert len(r3["text_head"]) == agent_traces._TEXT_HEAD  # ... capped head
    assert len(r3["reasoning_head"]) == agent_traces._TEXT_HEAD

    r2 = _row(con, "req-0002")
    assert r2["status"] == "error" and r2["finish_reason"] is None
    assert r2["error_name"] == "TerminalStreamChunkError"
    assert "exceed quota limit" in r2["error_message"]
    assert r2["input"] is None and r2["total_tokens"] is None  # no usage, no zeros


def test_idempotent_rerun_and_window_replace(tmp_path: Path) -> None:
    con, d = _con(tmp_path), _fixture_dir(tmp_path)
    assert agent_traces._parse_rollout(con, None, directory=d) == 3
    assert agent_traces._parse_rollout(con, None, directory=d) == 3  # re-run: same count
    n = con.execute("SELECT count(*) FROM fact_model_step WHERE source='zcode_rollout'").fetchone()[
        0
    ]
    assert n == 3  # window-delete + reinsert, no PK duplicates

    # Incremental window: only 09-29 reloaded, 09-28 rows untouched.
    assert agent_traces._parse_rollout(con, date(2026, 9, 29), directory=d) == 1
    assert _row(con, "req-0001")["status"] == "completed"  # old row survived
    n = con.execute("SELECT count(*) FROM fact_model_step WHERE source='zcode_rollout'").fetchone()[
        0
    ]
    assert n == 3


def test_missing_directory_loads_zero(tmp_path: Path) -> None:
    con = _con(tmp_path)
    assert agent_traces._parse_rollout(con, None, directory=tmp_path / "nope") == 0


def test_registered_in_loaders() -> None:
    assert "rollout" in agent_traces.LOADERS
    assert trace_contracts.get_parser("rollout") is agent_traces._parse_rollout
