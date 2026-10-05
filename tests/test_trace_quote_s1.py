#!/usr/bin/env python3
"""trace_quote S1 tests — citation grammar, resolver, and output shapes.

Synthetic fixtures only: no real transcript content enters tracked files
(data-discretion rule). Store is built into a temp duckdb per run; the
live-store shakedown (one citation per anchor kind resolves) is verified
manually against memory/data/agent_traces.duckdb.

Proposal: doc/improvements/archive/tooling/trace_quote_citations.md (slice S1).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "helpers"))

from helpers.misc import trace_quote as tq  # noqa: E402


# --------------------------------------------------------------------------- #
# Synthetic store fixture
# --------------------------------------------------------------------------- #


def _make_store() -> Path:
    """Build a synthetic trace store: star schema + minimal rows per harness.

    The ids chosen are synthetic and unique per harness; timestamps are set so
    near-miss ordering (ts DESC) is deterministic.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    Path(path).unlink()  # duckdb creates the db on first write; no stub files
    con = duckdb.connect(str(path))
    con.execute(tq.SCHEMA if hasattr(tq, "SCHEMA") else _SCHEMA)
    _seed(con)
    con.close()
    return Path(path)


def _empty_store() -> Path:
    """Valid but empty trace store (tables only)."""
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    Path(path).unlink()
    con = duckdb.connect(str(path))
    con.execute(_SCHEMA)
    con.close()
    return Path(path)


# Minimal subset of the star schema needed by the resolver (mirrors the real
# layout so resolution behaves identically).
_SCHEMA = """\
CREATE TABLE fact_turn (
    source TEXT NOT NULL, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
    ts TIMESTAMP, day DATE, duration_ms BIGINT, model_requests BIGINT,
    model_retries BIGINT, tool_calls BIGINT, tool_errors BIGINT, tokens BIGINT,
    context_exceeded BOOLEAN, status TEXT,
    PRIMARY KEY (source, session_id, turn_id)
);
CREATE TABLE fact_tool_call (
    source TEXT NOT NULL, tool_call_id TEXT NOT NULL, session_id TEXT,
    turn_id TEXT, tool_name TEXT, ts TIMESTAMP, day DATE, duration_ms BIGINT,
    ttf_output_ms BIGINT, exit_code BIGINT, status TEXT, output_bytes BIGINT,
    stdout_bytes BIGINT, stderr_bytes BIGINT, truncated BOOLEAN, cancelled BOOLEAN,
    read_only BOOLEAN, destructive BOOLEAN, approval_status TEXT, retry_count BIGINT,
    error_type TEXT, error_code TEXT, side_effect_scope TEXT,
    PRIMARY KEY (source, tool_call_id)
);
CREATE TABLE fact_model_request (
    source TEXT NOT NULL, request_id TEXT NOT NULL, session_id TEXT,
    turn_id TEXT, trace_id TEXT, span_id TEXT, attempt_index BIGINT,
    ts TIMESTAMP, day DATE, provider TEXT, model TEXT, variant TEXT,
    agent TEXT, mode TEXT, task_type TEXT, query_source TEXT, status TEXT,
    finish_reason TEXT, ttft_ms BIGINT, duration_ms BIGINT, input BIGINT,
    output BIGINT, reasoning BIGINT, cache_read BIGINT, cache_write BIGINT,
    tool_call_count BIGINT, retry_count BIGINT, context_exceeded BOOLEAN,
    cancelled BOOLEAN, error_type TEXT, error_code TEXT, cost_usd DOUBLE,
    cost_basis TEXT, PRIMARY KEY (source, request_id)
);
CREATE TABLE dim_session (
    source TEXT NOT NULL, session_id TEXT NOT NULL, ts_start TIMESTAMP,
    day DATE, ts_end TIMESTAMP, directory TEXT, title TEXT, model TEXT,
    agent TEXT, mode TEXT, adds BIGINT, dels BIGINT, files BIGINT,
    compactions BIGINT, input BIGINT, output BIGINT, reasoning BIGINT,
    cache_read BIGINT, cache_write BIGINT, cost_usD DOUBLE, trace_id TEXT,
    PRIMARY KEY (source, session_id)
);
CREATE TABLE fact_event (
    source TEXT NOT NULL, trace_id TEXT, span_id TEXT, parent_span_id TEXT,
    ts TIMESTAMP, event_name TEXT, duration_ms BIGINT, status TEXT, context JSON
);
"""


def _seed(con: duckdb.DuckDBPyConnection) -> None:
    # prime: turn + its model request + tool call chain
    con.execute("""INSERT INTO fact_turn VALUES
        ('prime', 'sess-001', 't1a', TIMESTAMP '2026-09-01T12:00:10', '2026-09-01', 500, 1, 0, 2, 0, 1000, NULL, 'completed'),
        ('prime', 'sess-001', 't1b', TIMESTAMP '2026-09-01T12:05:00', '2026-09-01', 500, 0, 0, 1, 0, 800, NULL, 'completed')""")
    con.execute("""INSERT INTO fact_model_request VALUES
        ('prime', 'r1a', 'sess-001', 't1a', NULL, NULL, 1, TIMESTAMP '2026-09-01T12:00:11', '2026-09-01', 'A', 'foo', NULL, NULL, NULL, NULL, NULL, 'completed', NULL, NULL, 100, 100, 200, NULL, NULL, NULL, 1, 0, NULL, NULL, NULL, NULL, NULL, 'harness')""")
    con.execute("""INSERT INTO fact_tool_call VALUES
        ('prime', 'tc1a', 'sess-001', 't1a', 'read_file', TIMESTAMP '2026-09-01T12:00:12', '2026-09-01', 10, 5, 0, 'completed', 50, 50, 0, FALSE, FALSE, FALSE, FALSE, 'auto', 0, NULL, NULL, NULL),
        ('prime', 'tc1b', 'sess-001', 't1a', 'write_file', TIMESTAMP '2026-09-01T12:00:15', '2026-09-01', 10, 5, 0, 'completed', 60, 60, 0, FALSE, FALSE, FALSE, FALSE, 'auto', 0, NULL, NULL, NULL)""")
    # opencode: tool call chain with parents + adjacent
    con.execute("""INSERT INTO fact_tool_call VALUES
        ('opencode', 'tc2a', 'sess-002', 'msg_001', 'list_dir', TIMESTAMP '2026-09-01T12:10:00', '2026-09-01', 10, 5, 0, 'completed', 40, 40, 0, FALSE, FALSE, FALSE, FALSE, 'auto', 0, NULL, NULL, NULL),
        ('opencode', 'tc2b', 'sess-002', 'msg_001', 'edit_file', TIMESTAMP '2026-09-01T12:10:05', '2026-09-01', 10, 5, 0, 'completed', 60, 60, 0, FALSE, FALSE, FALSE, FALSE, 'auto', 0, NULL, NULL, NULL),
        ('opencode', 'tc2c', 'sess-002', 'msg_001', 'run_cmd', TIMESTAMP '2026-09-01T12:10:10', '2026-09-01', 10, 5, 1, 'failed', 70, 70, 0, FALSE, FALSE, FALSE, FALSE, 'auto', 0, NULL, NULL, NULL)""")
    con.execute("""INSERT INTO fact_turn VALUES
        ('opencode', 'sess-002', 'msg_001', TIMESTAMP '2026-09-01T12:09:50', '2026-09-01', 500, 0, 0, 3, 0, 500, NULL, 'completed')""")
    # zcode: turn without model requests (edge case)
    con.execute("""INSERT INTO fact_turn VALUES
        ('zcode', 'sess-003', 't3a', TIMESTAMP '2026-09-01T12:20:00', '2026-09-01', 500, 0, 0, 0, 0, 0, NULL, 'completed')""")
    # events: single-hop chain, plus a multi-hop chain
    con.execute("""INSERT INTO fact_event VALUES
        ('opencode', NULL, 'evt_001', 'ses_root', TIMESTAMP '2026-09-01T12:10:01', 'message.updated.1', NULL, 'ok', '{"agent":"build","id":"msg-001","role":"user"}'),
        ('prime', NULL, 'evt_002', 'evt_003', TIMESTAMP '2026-09-01T12:30:00', 'step.start', NULL, 'ok', '{"step":"compute"}'),
        ('prime', NULL, 'evt_003', 'ses_root', TIMESTAMP '2026-09-01T12:29:55', 'delegation.spawn', NULL, 'ok', '{"op":"spawn"}'),
        ('prime', NULL, 'evt_002', 'evt_009', TIMESTAMP '2026-09-01T12:35:00', 'step.start', NULL, 'ok', '{"step":"other"}')""")


# --------------------------------------------------------------------------- #
# Token parsing
# --------------------------------------------------------------------------- #


class TestTokenParse:
    def test_parse_all_anchor_kinds(self):
        for harness in tq.HARNESSES:
            for kind in ("turn", "tool", "req", "event"):
                token = f"agent-trace:{harness}#{kind}:id123"
                assert tq.parse_token(token) == (harness, kind, "id123", token)

    def test_parse_rejects_invalid_harness(self):
        assert tq.parse_token("agent-trace:unknown#turn:abc") is None
        assert tq.parse_token("agent-trace:open#turn:abc") is None

    def test_parse_rejects_malformed_syntax(self):
        assert tq.parse_token("agent-trace:prime#badkind:abc") is None
        assert tq.parse_token("agent-trace:prime#turn") is None
        assert tq.parse_token("not-a-token") is None
        assert tq.parse_token("prime#turn:abc") is None

    def test_parse_allows_special_chars_in_id(self):
        citation = "agent-trace:prime#turn:0f938d6b"
        assert tq.parse_token(citation) == ("prime", "turn", "0f938d6b", citation)


# --------------------------------------------------------------------------- #
# Resolution + output shapes
# --------------------------------------------------------------------------- #

STORE = None


@pytest.fixture(scope="module")
def store() -> Path:
    global STORE
    STORE = _make_store()
    return STORE


def _run_cli(citation: str, store: Path, json: bool = False) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "helpers" / "misc" / "trace_quote.py"),
        citation,
        "--db",
        str(store),
    ]
    if json:
        cmd.append("--json")
    return subprocess.run(cmd, capture_output=True, text=True)


class TestResolveTurn:
    def test_turn_resolves(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#turn:t1a")
        assert res.found
        assert res.kind == "turn"
        assert res.row is not None
        assert res.row["turn_id"] == "t1a"
        assert res.row["session_id"] == "sess-001"
        assert res.context["model_requests"][0]["request_id"] == "r1a"
        assert len(res.context["tool_calls"]) == 2

    def test_turn_text_shape(self, store):
        out = _run_cli("agent-trace:prime#turn:t1a", store).stdout
        assert "resolved:" in out
        assert "turn_id=t1a" in out
        assert "session_id=sess-001" in out
        assert "context: 1 model request(s), 2 tool call(s)" in out
        assert "req r1a: foo (A) completed" in out

    def test_turn_json_shape(self, store):
        out = _run_cli("agent-trace:prime#turn:t1a", store, json=True).stdout
        obj = json.loads(out)
        assert obj["resolved"] is True
        assert obj["kind"] == "turn"
        assert "model_requests" in obj["context"]
        assert "tool_calls" in obj["context"]
        assert all("request_id" in m for m in obj["context"]["model_requests"])
        assert all("tool_call_id" in t for t in obj["context"]["tool_calls"])


class TestResolveTool:
    def test_tool_resolves_with_parent(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:opencode#tool:tc2a")
        assert res.found
        assert res.context["parent_turn"]["turn_id"] == "msg_001"
        assert res.context["adjacent_tool_calls"][0]["role"] == "next"
        assert res.context["adjacent_tool_calls"][0]["tool_call_id"] == "tc2b"

    def test_tool_text_shape(self, store):
        out = _run_cli("agent-trace:opencode#tool:tc2b", store).stdout
        assert "resolved:" in out
        assert "parent turn msg_001" in out
        assert "~ previous: tool tc2a" in out
        assert "~ next: tool tc2c" in out

    def test_tool_json_shape(self, store):
        out = _run_cli("agent-trace:opencode#tool:tc2c", store, json=True).stdout
        obj = json.loads(out)
        assert obj["context"]["adjacent_tool_calls"][0]["tool_call_id"] == "tc2b"


class TestResolveReq:
    def test_req_resolves_with_parent_turn(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#req:r1a")
        assert res.found
        assert res.context["parent_turn"]["turn_id"] == "t1a"

    def test_req_text_shape(self, store):
        out = _run_cli("agent-trace:prime#req:r1a", store).stdout
        assert "resolved:" in out
        assert "parent turn t1a" in out

    def test_req_json_shape(self, store):
        out = _run_cli("agent-trace:prime#req:r1a", store, json=True).stdout
        obj = json.loads(out)
        assert obj["context"]["parent_turn"]["turn_id"] == "t1a"
        assert obj["context"]["parent_turn"]["session_id"] == "sess-001"


class TestResolveEvent:
    def test_event_resolves(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:opencode#event:evt_001")
        assert res.found
        assert res.context["event_matches"] == 1
        assert len(res.context["rows"][0]["parent_span_chain"]) == 2
        assert res.context["rows"][0]["parent_span_chain"][0]["event_name"] == "message.updated.1"

    def test_event_parent_chain_multi_hop(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#event:evt_002")
        assert res.found
        chain = [c["event_name"] for c in res.context["rows"][0]["parent_span_chain"]]
        assert chain == ["step.start", "delegation.spawn", "(session anchor)"]

    def test_event_json_shape(self, store):
        out = _run_cli("agent-trace:opencode#event:evt_001", store, json=True).stdout
        obj = json.loads(out)
        ec = json.loads(obj["context"]["rows"][0]["context"])
        assert ec["agent"] == "build"
        assert ec["role"] == "user"

    def test_event_duplicate_span_ids_prints_all(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#event:evt_002")
        assert res.found
        assert res.context["event_matches"] == 2
        assert json.loads(res.context["rows"][0]["context"])["step"] == "compute"
        assert json.loads(res.context["rows"][1]["context"])["step"] == "other"


class TestResolutionEdgeCases:
    def test_turn_no_model_requests(self, store):
        # zcode turn t3a has model_requests=0 and no related rows
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:zcode#turn:t3a")
        assert res.found
        assert res.context["model_requests"] == []
        assert res.context["tool_calls"] == []

    def test_event_missing_session_anchor(self, store):
        # evt_999 does not exist -> unresolved, not an exception
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#event:evt_999")
        assert not res.found
        assert len(res.near_misses) > 0

    def test_unknown_turn_near_misses(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#turn:zzzzzzzz")
        assert not res.found
        assert len(res.near_misses) > 0
        assert res.near_misses[0]["turn_id"]  # deterministic ordering: most recent first

    def test_bad_kind_rejected(self, store):
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#foo:abc")
        assert not res.found

    def test_cli_exit_code_resolved(self, store):
        r = _run_cli("agent-trace:prime#turn:t1a", store)
        assert r.returncode == 0

    def test_cli_exit_code_unresolved(self, store):
        r = _run_cli("agent-trace:prime#turn:zzzzzzzz", store)
        assert r.returncode == 1

    def test_cli_exit_code_missing_store(self):
        r = _run_cli("agent-trace:prime#turn:t1a", Path("/nonexistent/store.duckdb"))
        assert r.returncode == 1
        assert "trace store not found" in r.stderr

    def test_cli_exit_code_bad_token(self):
        store = _empty_store()
        try:
            r = _run_cli("not-a-token", store)
            assert r.returncode == 1
        finally:
            store.unlink()

    def test_unresolved_prints_hint(self):
        store = _empty_store()
        try:
            out = _run_cli("agent-trace:prime#badkind:abc", store).stdout
            assert "hint:" in out
        finally:
            store.unlink()

    def test_json_unresolved_payload(self):
        store = _empty_store()
        try:
            out = _run_cli("agent-trace:prime#turn:abc", store, json=True).stdout
            obj = json.loads(out)
            assert obj["resolved"] is False
            assert "hint" in obj
        finally:
            store.unlink()


# --------------------------------------------------------------------------- #
# Mutation test: stripping the near-miss path should go RED
# --------------------------------------------------------------------------- #


class TestMutationNearMiss:
    def test_near_miss_suggestion_path_exists(self, store):
        # Mutation check: if the near-miss query is stripped in production,
        # this assertion fails (test goes RED), proving the suggestion path
        # is exercised and not dead code.
        res = tq.resolve(duckdb.connect(str(store)), "agent-trace:prime#turn:zzzzzzzz")
        assert not res.found
        assert len(res.near_misses) >= 1
        assert isinstance(res.near_misses[0], dict)
        assert "turn_id" in res.near_misses[0]


# --------------------------------------------------------------------------- #
# Sweep mode (citations S3): resolve agent-trace: tokens under a doc tree
# --------------------------------------------------------------------------- #


def _sweep_fixture() -> tuple[Path, Path]:
    """Create a seeded store + a doc tree with valid + invalid citations."""
    store = _make_store()
    d = Path(tempfile.mkdtemp(prefix="sweep_"))
    (d / "valid.md").write_text(
        "See agent-trace:prime#turn:t1a and agent-trace:opencode#tool:tc2a.\n"
    )
    (d / "event.md").write_text("agent-trace:opencode#event:evt_001 triggered this\n")
    (d / "broken.md").write_text("agent-trace:prime#turn:zzzzzzzz could not be verified\n")
    return store, d


class TestSweepCollect:
    def test_sweep_collects_tokens_across_files(self, store):
        d = Path(tempfile.mkdtemp(prefix="sweep_"))
        (d / "a.md").write_text(
            "agent-trace:prime#turn:t1a and agent-trace:prime#req:r1a\n"
            "agent-trace:opencode#tool:tc2c\n"
        )
        (d / "b.md").write_text(
            "agent-trace:zcode#turn:t3a nested\n"
            "agent-trace:prime#event:evt_002 (dup, same doc)\n"
            "agent-trace:prime#event:evt_002 again\n"
        )
        try:
            hits = tq.sweep_tokens(d)
            tokens = [h[2] for h in hits]
            assert len(hits) == 6  # 5 unique tokens, evt_002 appears twice
            assert "agent-trace:prime#turn:t1a" in tokens
            assert "agent-trace:opencode#tool:tc2c" in tokens
            assert "agent-trace:zcode#turn:t3a" in tokens
            assert tokens.count("agent-trace:prime#event:evt_002") == 2
        finally:
            shutil.rmtree(d)

    def test_sweep_ignores_non_markdown(self, store):
        d = Path(tempfile.mkdtemp(prefix="sweep_"))
        (d / "a.md").write_text("agent-trace:prime#turn:t1a\n")
        (d / "b.txt").write_text("agent-trace:prime#turn:t1a\n")
        (d / "c.py").write_text('print("agent-trace:prime#turn:t1a")\n')
        try:
            hits = tq.sweep_tokens(d)
            tokens = [h[2] for h in hits]
            assert tokens.count("agent-trace:prime#turn:t1a") == 2
        finally:
            shutil.rmtree(d)

    def test_sweep_skips_unreadable(self, store):
        d = Path(tempfile.mkdtemp(prefix="sweep_"))
        p = d / "ok.md"
        p.write_text("agent-trace:prime#turn:t1a\n")
        p.chmod(0o000)
        try:
            hits = tq.sweep_tokens(d)
            tokens = [h[2] for h in hits]
            # unreadable files are skipped gracefully (logged on stderr);
            # the sweep stays non-blocking and does not raise.
            assert "agent-trace:prime#turn:t1a" not in tokens
            assert len(hits) == 0
        finally:
            p.chmod(0o644)
            shutil.rmtree(d)


class TestSweepResolve:
    def test_sweep_resolves_valid_citations_green(self, store):
        store, d = _sweep_fixture()
        try:
            con = duckdb.connect(str(store))
            report = tq.sweep(con, d)
            assert report["tokens"] == 4
            assert report["resolved"] == 3
            assert report["unresolved"] == 1
            for r in report["results"]:
                assert r["location"]["file"]
                assert r["location"]["line"]
                assert r["resolved"] in (True, False)
            assert all(r["resolved"] for r in report["results"] if "zzzz" not in r["token"])
        finally:
            store.unlink()
            shutil.rmtree(d)

    def test_sweep_fails_red_on_broken_token(self, store):
        store, d = _sweep_fixture()
        try:
            con = duckdb.connect(str(store))
            report = tq.sweep(con, d)
            broken = [r for r in report["results"] if not r["resolved"]]
            assert len(broken) == 1
            assert "zzzzzzzz" in broken[0]["token"]
            assert "near_misses" in broken[0]
        finally:
            store.unlink()
            shutil.rmtree(d)

    def test_sweep_json_shape(self, store):
        store, d = _sweep_fixture()
        try:
            con = duckdb.connect(str(store))
            report = tq.sweep(con, d)
            out = json.dumps(report, indent=2, default=str)
            obj = json.loads(out)
            assert obj["tokens"] == 4
            assert "results" in obj
            assert all("token" in r and "resolved" in r for r in obj["results"])
        finally:
            store.unlink()
            shutil.rmtree(d)

    def test_sweep_mutation_pair_strip_near_misses_go_red(self, store):
        # Mutation check: if the near-miss suggestion path is stripped in
        # production, broken tokens lose their suggestions and this test
        # still flags them (RED) via the `resolved == False` assertion; the
        # additional `near_misses in broken[0]` proves the suggestion path is
        # exercised and not dead code.
        store, d = _sweep_fixture()
        try:
            con = duckdb.connect(str(store))
            report = tq.sweep(con, d)
            broken = [r for r in report["results"] if not r["resolved"]]
            assert len(broken) == 1
            assert broken[0]["near_misses"] is not None
        finally:
            store.unlink()
            shutil.rmtree(d)


class TestSweepCli:
    def test_sweep_cli_resolved_exit_0(self, store):
        d = Path(tempfile.mkdtemp(prefix="sweep_"))
        (d / "a.md").write_text("agent-trace:prime#turn:t1a\n")
        try:
            r = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "helpers" / "misc" / "trace_quote.py"),
                    "--sweep",
                    str(d),
                    "--db",
                    str(store),
                ],
                capture_output=True,
                text=True,
            )
            assert r.returncode == 0
            assert "[OK]" in r.stdout
        finally:
            shutil.rmtree(d)

    def test_sweep_cli_unresolved_exit_1(self, store):
        d = Path(tempfile.mkdtemp(prefix="sweep_"))
        (d / "a.md").write_text("agent-trace:prime#turn:zzzzzzzz\n")
        try:
            r = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "helpers" / "misc" / "trace_quote.py"),
                    "--sweep",
                    str(d),
                    "--db",
                    str(store),
                ],
                capture_output=True,
                text=True,
            )
            assert r.returncode == 1
            assert "[FAIL]" in r.stdout
            assert "near matches:" in r.stdout
        finally:
            shutil.rmtree(d)

    def test_sweep_cli_json(self, store):
        d = Path(tempfile.mkdtemp(prefix="sweep_"))
        (d / "a.md").write_text("agent-trace:prime#turn:t1a\n")
        try:
            r = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "helpers" / "misc" / "trace_quote.py"),
                    "--sweep",
                    str(d),
                    "--db",
                    str(store),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            obj = json.loads(r.stdout)
            assert obj["mode"] == "sweep"
            assert obj["tokens"] == 1
            assert obj["results"][0]["resolved"] is True
        finally:
            shutil.rmtree(d)
