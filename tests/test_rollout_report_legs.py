#!/usr/bin/env python3
"""zcode_rollout_step_ingest S2/S3 tests — report-leg step extensions.

Synthetic fixtures only (no harness data enters tracked files). Each leg
is asserted on a tmp store: step signals merge/appear, the pre-S1
figures they extend are byte-identical with and without the step table,
and the S3 invariants hold (token parity on the validation leg;
aggregate legs byte-identical under a step load).

Proposal: doc/improvements/proposals/zcode_rollout_step_ingest.md (S2+S3).
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.analytics import agent_traces  # noqa: E402

D1 = "2026-09-28"
D2 = "2026-09-29"

REQ_COLS = "(source, request_id, session_id, model, output, reasoning, day, error_type)"
STEP_COLS = (
    "(source, request_id, session_id, turn_id, model, status, finish_reason,"
    " reasoning_chars, text_chars, messages_kind, error_name, day)"
)


def _con(tmp_path: Path, with_steps: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(tmp_path / f"t_{with_steps}.duckdb"))
    con.execute(agent_traces.SCHEMA)
    con.execute(
        f"INSERT INTO fact_model_request {REQ_COLS} VALUES "  # noqa: S608  # constants + fixed columns, not user input
        f"('zcode', 'req-a', 'sess', 'M1', 100, 0, '{D1}', NULL),"
        f"('opencode', 'req-b', 'sess', 'M2', 200, 50, '{D1}', NULL),"
        f"('zcode', 'req-c', 'sess', 'M1', 0, 0, '{D1}', 'rate_limited')"
    )
    if with_steps:
        con.execute(
            f"INSERT INTO fact_model_step {STEP_COLS} VALUES "  # noqa: S608  # constants + fixed columns, not user input
            f"('zcode_rollout', 's1', 'sess', 't1', 'M1', 'completed', 'tool-calls',"
            f" 500, 100, 'tail', NULL, '{D1}'),"
            f"('zcode_rollout', 's2', 'sess', 't1', 'M1', 'completed', 'stop',"
            f" 0, 0, 'full', NULL, '{D1}'),"
            f"('zcode_rollout', 's3', 'sess', 't2', 'M1', 'error', NULL,"
            f" 0, 0, 'tail', 'TerminalStreamChunkError', '{D2}')"
        )
    else:
        con.execute("DROP TABLE fact_model_step")
    return con


def _by_model(rows: list[dict]) -> dict:
    return {r["model"]: r for r in rows}


def test_reasoning_merge_keeps_token_figures(tmp_path: Path) -> None:
    rows = agent_traces.reasoning_ratio_rows(_con(tmp_path), "2026-09-28..2026-09-29")
    m1, m2 = _by_model(rows)["M1"], _by_model(rows)["M2"]
    assert (m1["reasoning"], m1["output"], m1["reason_pct"]) == (0, 100, 0.0)
    assert m1["step_reqs"] == 3 and m1["steps_with_reasoning"] == 1  # error step counts
    assert m1["avg_reason_chars"] == 167 and m1["avg_text_chars"] == 33
    assert (m2["reasoning"], m2["reason_pct"]) == (50, 25.0)
    assert m2["step_reqs"] is None


def test_reasoning_without_step_table(tmp_path: Path) -> None:
    rows = agent_traces.reasoning_ratio_rows(
        _con(tmp_path, with_steps=False), "2026-09-28..2026-09-29"
    )
    assert _by_model(rows)["M1"]["step_reqs"] is None


def test_taxonomy_gains_step_rows(tmp_path: Path) -> None:
    rows = agent_traces.error_taxonomy_rows(_con(tmp_path), "2026-09-28..2026-09-29")
    kinds = {(r["kind"], r["source"], r["error_type"]): r["n"] for r in rows}
    assert kinds[("step", "zcode_rollout", "TerminalStreamChunkError")] == 1
    assert kinds[("request", "zcode", "rate_limited")] == 1


def test_compaction_and_step_errors(tmp_path: Path) -> None:
    con = _con(tmp_path)
    comp = {
        str(r["day"]): r for r in agent_traces._step_compaction_rows(con, "2026-09-28..2026-09-29")
    }
    assert (comp[D1]["steps"], comp[D1]["full"], comp[D1]["tail"]) == (2, 1, 1)
    assert comp[D2]["tail_pct"] == 100.0
    err = agent_traces._step_error_rows(con, "2026-09-28..2026-09-29")
    assert len(err) == 1 and err[0]["error_name"] == "TerminalStreamChunkError"
    assert err[0]["n"] == 1
    assert (str(err[0]["first_day"]), str(err[0]["last_day"])) == (D2, D2)
    # tool-grain forensics untouched by step data:
    assert agent_traces.failure_forensics_rows(con, "2026-09-28..2026-09-29") == []
    # pre-S1 stores: helpers empty, taxonomy has no step rows:
    con2 = _con(tmp_path, with_steps=False)
    assert agent_traces._step_compaction_rows(con2, "2026-09-28..2026-09-29") == []
    assert agent_traces._step_error_rows(con2, "2026-09-28..2026-09-29") == []
    assert {
        r["kind"] for r in agent_traces.error_taxonomy_rows(con2, "2026-09-28..2026-09-29")
    } == {"request"}


STEP_TOKEN_COLS = (
    "(source, request_id, session_id, turn_id, model, status, finish_reason,"  # noqa: S105  # column-list constant, not a credential
    " input, output, cache_read, total_tokens, messages_kind, error_name, day)"
)
LOAD_LOG_ROW = (
    "INSERT INTO load_log VALUES"
    " ('rollout', TIMESTAMP '2026-09-29 08:00:00', 2, 'ok', 'since=None', 2, 0, 0),"
    " ('zcode', TIMESTAMP '2026-09-29 08:00:00', 2, 'ok', 'since=None', 2, 0, 0)"
)


def test_validation_gains_token_parity(tmp_path: Path) -> None:
    con = _con(tmp_path)
    con.execute("DELETE FROM fact_model_step")  # drop the fixture steps; reinsert with tokens
    con.execute(LOAD_LOG_ROW)
    con.execute(
        f"INSERT INTO fact_model_step {STEP_TOKEN_COLS} VALUES "  # noqa: S608  # constants + fixed columns, not user input
        f"('zcode_rollout', 's1', 'sess', 't1', 'M1', 'completed', 'tool-calls',"
        f" 800, 50, 200, 1050, 'tail', NULL, '{D1}'),"
        f"('zcode_rollout', 's2', 'sess', 't2', 'M1', 'completed', 'stop',"
        f" 400, 25, 100, 525, 'full', NULL, '{D2}')"
    )
    rows = {r["source"]: r for r in agent_traces.validation_rows(con)}
    assert rows["rollout"]["token_parity"] == 0
    assert "token_parity" not in rows["zcode"]  # invariant is rollout-only

    # drift is visible, never normalized away: s3's provider total lies
    con.execute(
        f"INSERT INTO fact_model_step {STEP_TOKEN_COLS} VALUES "  # noqa: S608  # constants + fixed columns, not user input
        f"('zcode_rollout', 's3', 'sess', 't3', 'M1', 'completed', 'stop',"
        f" 400, 25, 100, 999, 'full', NULL, '{D2}')"
    )
    rows = {r["source"]: r for r in agent_traces.validation_rows(con)}
    assert rows["rollout"]["token_parity"] == -474


def test_token_parity_absent_pre_s1(tmp_path: Path) -> None:
    con = _con(tmp_path, with_steps=False)
    con.execute(LOAD_LOG_ROW)
    rows = agent_traces.validation_rows(con)
    assert rows and all("token_parity" not in r for r in rows)


# Legs that did NOT opt into the step source (S2: reasoning_ratio,
# error_taxonomy, failure_forensics, context_lifecycle's rollout block;
# S3: validation's token_parity). A future leg that reads fact_model_step
# must be consciously added to this exclusion set — that failure IS the
# double-count guard (proposal AC#3).
INVARIANT_LEGS = ",".join(
    name
    for name in agent_traces.REPORT_LEGS
    if name
    not in {
        "reasoning_ratio",
        "error_taxonomy",
        "failure_forensics",
        "context_lifecycle",
        "validation",
    }
)


def _capture_report(con: duckdb.DuckDBPyConnection) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        agent_traces.report(con, "all", as_json=False, only=INVARIANT_LEGS)
    return buf.getvalue()


def test_aggregate_legs_byte_invariant_under_step_load(tmp_path: Path) -> None:
    con = _con(tmp_path)
    con.execute("DELETE FROM fact_model_step")  # pre-load state: table exists, empty
    # token/cost columns populated — the request-grain renderers assume
    # real rows carry them (fixture rows without would crash, not drift)
    con.execute("UPDATE fact_model_request SET input = 1000, cache_read = 200, cost_usd = 0.01")
    before = _capture_report(con)
    con.execute(
        f"INSERT INTO fact_model_step {STEP_COLS} VALUES "  # noqa: S608  # constants + fixed columns, not user input
        f"('zcode_rollout', 's1', 'sess', 't1', 'M1', 'completed', 'tool-calls',"
        f" 500, 100, 'tail', NULL, '{D1}'),"
        f"('zcode_rollout', 's2', 'sess', 't1', 'M1', 'completed', 'stop',"
        f" 0, 0, 'full', NULL, '{D2}')"
    )
    after = _capture_report(con)
    assert before == after
