#!/usr/bin/env python3
"""Typed trace contracts S2-S4 tests — synthetic fixtures only.

Proposal: doc/improvements/archive/tooling/typed_trace_contracts.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

DuckDBPyConnection = duckdb.DuckDBPyConnection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "helpers"))

from bench_data.code import agent_traces, trace_contracts  # noqa: E402

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "trace_contracts"


@pytest.mark.parametrize("harness", ["zcode", "opencode", "prime"])
def test_synthetic_fixture_rows_pass_contract(harness: str) -> None:
    data = json.loads((FIXTURE_DIR / f"{harness}.json").read_text())
    trace_contracts.reset_validation()
    for table, rows in data.items():
        ok = trace_contracts.validate_rows(table, rows, harness)
        assert ok == rows, f"{harness}/{table}: synthetic row rejected"
    summary = trace_contracts.validation_summary()
    assert summary["rows_rejected"] == 0
    assert summary["unknown_field_count"] == 0
    assert summary["missing_required_count"] == 0


def test_unknown_field_is_rejected_not_widened() -> None:
    row = json.loads((FIXTURE_DIR / "opencode.json").read_text())["fact_turn"][0]
    row = dict(row, smuggled_field="nope")
    trace_contracts.reset_validation()
    assert trace_contracts.validate_rows("fact_turn", [row], "opencode") == []
    summary = trace_contracts.validation_summary()
    assert summary["unknown_field_count"] == 1
    assert summary["rows_rejected"] == 1


def test_missing_required_field_is_rejected() -> None:
    row = json.loads((FIXTURE_DIR / "opencode.json").read_text())["fact_turn"][0]
    row.pop("session_id")
    trace_contracts.reset_validation()
    assert trace_contracts.validate_rows("fact_turn", [row], "opencode") == []
    summary = trace_contracts.validation_summary()
    assert summary["missing_required_count"] == 1
    assert summary["rows_rejected"] == 1


def test_unknown_kind_counted() -> None:
    trace_contracts.reset_validation()
    assert trace_contracts.validate_rows("not_a_fact", [{"x": 1}], "opencode") == []
    summary = trace_contracts.validation_summary()
    assert summary["unknown_kind_count"] == 1
    assert summary["rows_rejected"] == 1


def _memory_store() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(agent_traces.SCHEMA)
    agent_traces._ensure_load_log_outcome_columns(con)
    return con


def test_insert_all_rejects_bad_and_keeps_good() -> None:
    # S2 validation (validate_rows): bad rows are rejected, good rows kept.
    good = json.loads((FIXTURE_DIR / "opencode.json").read_text())["fact_turn"][0]
    bad = dict(good, smuggled_field="nope")
    trace_contracts.reset_validation()
    kept = trace_contracts.validate_rows("fact_turn", [good, bad], "opencode")
    assert kept == [good]
    summary = trace_contracts.validation_summary()
    assert summary["rows_rejected"] == 1
    assert summary["rows_ok"] == 1
    assert summary["unknown_field_count"] == 1
    # S1 _insert_all stays lenient (no row-level rejection): extra fields are
    # dropped, so a bad row still lands — it needs a distinct PK to avoid the
    # collision the strict version relied on.
    con = _memory_store()
    bad_lenient = dict(good, smuggled_field="nope", session_id=f"{good['session_id']}-bad")
    trace_contracts.reset_validation()
    trace_contracts._insert_all(con, "fact_turn", [good, bad_lenient], "opencode")
    assert con.execute("SELECT count(*) FROM fact_turn").fetchone()[0] == 2
    con.close()


def test_mutation_required_field_missing_is_RED() -> None:
    """S3: drop a required field -> contract test RED."""
    row = json.loads((FIXTURE_DIR / "opencode.json").read_text())["fact_turn"][0]
    del row["session_id"]
    trace_contracts.reset_validation()
    assert trace_contracts.validate_rows("fact_turn", [row], "opencode") == []
    summary = trace_contracts.validation_summary()
    assert summary["rows_rejected"] == 1
    assert summary["missing_required_count"] == 1


def test_mutation_round_trip() -> None:
    """S3: RED mutation pair - drop required -> RED; restore -> GREEN."""
    row = json.loads((FIXTURE_DIR / "opencode.json").read_text())["fact_turn"][0]
    del row["session_id"]
    trace_contracts.reset_validation()
    assert trace_contracts.validate_rows("fact_turn", [row], "opencode") == []
    assert trace_contracts.validation_summary()["rows_rejected"] == 1
    row["session_id"] = "synthetic"
    trace_contracts.reset_validation()
    assert trace_contracts.validate_rows("fact_turn", [row], "opencode") == [row]
    assert trace_contracts.validation_summary()["rows_rejected"] == 0


def test_loader_wires_validation_end_to_end() -> None:
    """S2: an ingest run actually exercises validate_rows at the boundary and
    populates the load_log outcome columns with real counts."""
    con = _memory_store()
    good = dict(json.loads((FIXTURE_DIR / "opencode.json").read_text())["fact_turn"][0])
    good["turn_id"] = "synthetic-good"
    bad = dict(good, smuggled_field="nope", turn_id="synthetic-bad")

    def _fake_parser(con, since):
        # Simulate what the real parsers do: validate (warn+count) then insert.
        trace_contracts.reset_validation()
        rows = trace_contracts.validate_rows(
            "fact_turn", [good, bad], "opencode", keep_rejected=True
        )
        trace_contracts._insert_all(con, "fact_turn", rows, "opencode")
        return len(rows)

    trace_contracts.register_parser("s2_e2e")(_fake_parser)
    try:
        trace_contracts.load(con, "s2_e2e")
    finally:
        del trace_contracts._REGISTRY["s2_e2e"]

    summary = trace_contracts.validation_summary()
    assert summary["rows_ok"] == 1
    assert summary["rows_rejected"] == 1
    assert summary["unknown_field_count"] == 1
    assert con.execute("SELECT COUNT(*) FROM fact_turn").fetchone()[0] == 2
    # mirror main()'s load_log write so the S4 SQL leg can read it
    from datetime import UTC, datetime

    con.execute(
        "INSERT INTO load_log VALUES (?, ?, ?, 'ok', ?, ?, ?, ?)",
        [
            "s2_e2e",
            datetime.now(UTC),
            2,
            "e2e",
            summary["rows_ok"],
            summary["rows_rejected"],
            summary["unknown_field_count"],
        ],
    )
    assert con.execute(
        "SELECT source, rows_ok, rows_rejected, unknown_fields FROM load_log WHERE source = 's2_e2e'"
    ).fetchone() == ("s2_e2e", 1, 1, 1)
    con.close()


def test_load_log_gains_outcome_columns_and_validation_leg() -> None:
    con = _memory_store()
    con.execute("INSERT INTO load_log VALUES ('opencode', now(), 10, 'ok', 'd', 8, 2, 1)")
    cols = {r[1] for r in con.execute("PRAGMA table_info('load_log')").fetchall()}
    assert {"rows_ok", "rows_rejected", "unknown_fields"} <= cols
    rows = agent_traces.validation_rows(con)
    assert rows == [
        {
            "source": "opencode",
            "loads": 1,
            "rows": 10,
            "ok": 1,
            "rows_ok": 8,
            "rows_rejected": 2,
            "unknown_fields": 1,
        }
    ]
    con.close()

    con.close()
