"""Hermetic tests for the dsh session-log lane (S1/S3).

Synthetic ~/.dsh trees in tmp_path (zstd-encoded by the vendored
zstandard dep — the same decoder the lane uses); `_dsh_home`
monkeypatched. No live harness, snapshot mount, or model touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import zstandard

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.maintenance import harvest_conversations as hc  # noqa: E402
from helpers.analytics import model_analytics as ma  # noqa: E402
from helpers.analytics import agent_traces as at  # noqa: E402

USER = {
    "content": [{"type": "text", "text": "do the thing"}],
    "source": {"kind": "user"},
    "role": "user",
    "id": "u1",
}
AST = {
    "role": "assistant",
    "content": [{"type": "reasoning", "text": "thinking"}, {"type": "text", "text": "done"}],
    "source": {"kind": "model", "provider": "zai-coding-cn", "model": "glm-5.3-flash"},
}


def _log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    with open(path, "wb") as fh:
        with cctx.stream_writer(fh) as w:
            for r in records:
                w.write((json.dumps(r) + "\n").encode())


def _sess_dir(root: Path, slug: str, sid: str) -> Path:
    d = root / ".dsh" / "sessions" / slug / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _v4_records() -> list[dict]:
    return [
        {"type": "session", "seq": 0, "time": 1000, "data": {"id": "s1", "cwd": "/repo"}},
        {"type": "user/message", "seq": 10, "time": 2000, "data": {"message": USER}},
        {
            "type": "assistant/message",
            "seq": 18,
            "time": 3000,
            "data": {
                "turn": 1,
                "step": 1,
                "message": AST,
                "usage": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7},
            },
        },
        {
            "type": "tool/call",
            "seq": 29,
            "time": 4000,
            "data": {
                "turn": 1,
                "step": 1,
                "callId": "call_1",
                "name": "bash",
                "arguments": '{"command": "ls"}',
            },
        },
        {
            "type": "tool/result",
            "seq": 36,
            "time": 5000,
            "data": {
                "turn": 1,
                "step": 1,
                "toolCallId": "call_1",
                "message": {"role": "tool", "content": [{"type": "text", "text": "out"}]},
                "isError": False,
            },
        },
        {
            "type": "turn/end",
            "seq": 40,
            "time": 6000,
            "data": {"turn": 1, "reason": {"kind": "completed"}},
        },
    ]


@pytest.fixture()
def dsh_env(tmp_path, monkeypatch):
    d = _sess_dir(tmp_path, "proj", "session-aaaa")
    _log(d / "session.v4.jsonl.zstd", _v4_records())
    monkeypatch.setattr(hc, "_dsh_home", lambda: tmp_path / ".dsh" / "sessions")
    monkeypatch.setattr(hc, "_dsh_index_path", lambda: None)
    monkeypatch.setattr(hc, "_prime_files", lambda: [])
    monkeypatch.setattr(hc, "_zcode_groups", lambda: {})
    monkeypatch.setattr(hc, "_opencode_sources", lambda: [])
    monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path / "nosnaps")
    (tmp_path / "nosnaps").mkdir(exist_ok=True)
    return tmp_path


def _corpus_rows(tmp_path):
    import pyarrow.parquet as pq

    rows = {}
    for f in (tmp_path / "corpus").rglob("*.parquet"):
        for r in pq.read_table(f).to_pylist():
            rows[r["part_id"]] = r
    return rows


def test_dsh_lane_mapping(dsh_env):
    tmp_path = dsh_env
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "dsh total new: 4" in stats["lanes"]
    rows = _corpus_rows(tmp_path)
    assert rows["session-aaaa:10"]["text"] == "do the thing"
    assert rows["session-aaaa:10"]["role"] == "user"
    assert rows["session-aaaa:18#0"]["part_type"] == "reasoning"
    assert rows["session-aaaa:18#1"]["model"] == "glm-5.3-flash"
    tool = rows["session-aaaa:29"]
    assert tool["part_type"] == "tool" and tool["text"] == "out"
    meta = json.loads(tool["meta"])
    assert (meta["status"], meta["tool"]) == ("completed", "bash")
    # structural markers are not rows
    assert not [p for p in rows if p.startswith("session-aaaa:40")]


def test_dsh_rerun_quiet_then_delta(dsh_env):
    tmp_path = dsh_env
    hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "dsh total new: 0" in stats["lanes"]
    d = tmp_path / ".dsh" / "sessions" / "proj" / "session-aaaa"
    recs = _v4_records() + [
        {
            "type": "user/message",
            "seq": 50,
            "time": 9000,
            "data": {"message": dict(USER, id="u2", content=[{"type": "text", "text": "again"}])},
        },
    ]
    _log(d / "session.v4.jsonl.zstd", recs)
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "dsh total new: 1" in stats["lanes"]
    assert _corpus_rows(tmp_path)["session-aaaa:50"]["text"] == "again"


def test_dsh_v0_fallback_and_overlap(dsh_env):
    tmp_path = dsh_env
    # v0 alongside v4: v4 wins, no double ingest
    d = tmp_path / ".dsh" / "sessions" / "proj" / "session-aaaa"
    _log(d / "session.jsonl.zstd", _v4_records())
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "dsh total new: 4" in stats["lanes"]
    # v0-only session: fallback reads it
    d2 = _sess_dir(tmp_path, "proj", "session-bbbb")
    _log(d2 / "session.jsonl.zstd", _v4_records())
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "dsh total new: 4" in stats["lanes"]
    assert "session-bbbb:10" in _corpus_rows(tmp_path)


def test_dsh_multiframe_log_reads_all_frames(dsh_env):
    """Upstream discussion #8033: logs are multi-frame appended zstd —
    one-shot decompression sees only the first frame. The lane streams."""
    import zstandard

    tmp_path = dsh_env
    d = tmp_path / ".dsh" / "sessions" / "proj" / "session-multi"
    d.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    frames = b""
    for rec in _v4_records()[:3]:
        frames += cctx.compress((json.dumps(rec) + "\n").encode())
    (d / "session.v4.jsonl.zstd").write_bytes(frames)
    recs = list(hc._dsh_read_log(d / "session.v4.jsonl.zstd"))
    assert len(recs) == 3, f"multiframe read got {len(recs)}/3"


def _dsh_index_fixture(home: Path) -> None:
    """Synthetic derived index: session-aaaa indexed through seq 10."""
    import sqlite3

    idx_dir = home / ".dsh" / "profiles" / "web"
    idx_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(idx_dir / "session-search.db")
    con.execute("CREATE TABLE persisted_docs (session_id TEXT, seq REAL)")
    con.execute("INSERT INTO persisted_docs VALUES ('session-aaaa', 10)")
    con.commit()
    con.close()


def _dsh_log_fixture(home: Path, sid: str, seqs: list[int]) -> None:
    import zstandard

    d = home / ".dsh" / "sessions" / "proj" / sid
    d.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    with open(d / "session.v4.jsonl.zstd", "wb") as fh:
        with cctx.stream_writer(fh) as w:
            for s in seqs:
                w.write(
                    (
                        json.dumps({"type": "user/message", "seq": s, "time": s, "data": {}}) + "\n"
                    ).encode()
                )


def test_dsh_index_lag_and_missing(tmp_path, monkeypatch):
    """S4: a present-but-behind index fires lagging/missing lines."""
    home = tmp_path / "home"
    _dsh_index_fixture(home)
    _dsh_log_fixture(home, "session-aaaa", [5, 50])  # indexed through 10
    _dsh_log_fixture(home, "session-bbbb", [7])  # absent from index
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    lines = hc._dsh_index_lag()
    assert any(ln.startswith("dsh-index:lagging session-aaaa") for ln in lines), lines
    assert any(ln.startswith("dsh-index:missing session-bbbb") for ln in lines), lines


def test_dsh_index_absent_is_quiet(tmp_path, monkeypatch):
    """S4: no backend configured (no index file) -> no lines, never drift."""
    home = tmp_path / "home"
    _dsh_log_fixture(home, "session-aaaa", [5])
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert hc._dsh_index_lag() == []


def test_dsh_drift_and_prefix_isolation(dsh_env):
    tmp_path = dsh_env
    hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    out = hc.harvest(check=True, corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert out["check"] and not out["index_stale"], out["stale_new"]
    # session ids can never collide with the other harnesses' lanes
    assert "session-aaaa".startswith("session-")
    assert not "session-aaaa".startswith(("ses_", "sess_"))


def _dsh_analytic_env(tmp_path, monkeypatch):
    d = tmp_path / ".dsh" / "sessions" / "proj" / "session-aaaa"
    d.mkdir(parents=True, exist_ok=True)
    import zstandard

    cctx = zstandard.ZstdCompressor()
    recs = [
        {"type": "session", "seq": 0, "time": 1000, "data": {"id": "session-aaaa"}},
        {
            "type": "model/selection",
            "seq": 4,
            "time": 1500,
            "data": {"provider": "zai-coding-cn", "model": "glm-5.3-flash"},
        },
        {
            "type": "user/message",
            "seq": 10,
            "time": 2000,
            "data": {"message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        },
        {
            "type": "assistant/message",
            "seq": 18,
            "time": 3000,
            "data": {
                "turn": 1,
                "step": 1,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "yo"}],
                    "source": {
                        "kind": "model",
                        "provider": "zai-coding-cn",
                        "model": "glm-5.3-flash",
                    },
                },
                "usage": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7},
            },
        },
        {
            "type": "tool/call",
            "seq": 29,
            "time": 4000,
            "data": {"turn": 1, "step": 1, "callId": "call_1", "name": "bash", "arguments": "{}"},
        },
        {
            "type": "tool/result",
            "seq": 36,
            "time": 5000,
            "data": {
                "turn": 1,
                "step": 1,
                "toolCallId": "call_1",
                "message": {"role": "tool", "content": [{"type": "text", "text": "out"}]},
                "isError": False,
            },
        },
    ]
    with open(d / "session.v4.jsonl.zstd", "wb") as fh:
        with cctx.stream_writer(fh) as w:
            for r in recs:
                w.write((json.dumps(r) + "\n").encode())
    monkeypatch.setattr(ma, "DSH_SESSIONS", tmp_path / ".dsh" / "sessions")
    monkeypatch.setattr(Path, "home", lambda: tmp_path, raising=False)
    return tmp_path


def test_load_dsh_usage(tmp_path, monkeypatch):
    _dsh_analytic_env(tmp_path, monkeypatch)
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        con.execute(ma.SCHEMA)
        n = ma.load_dsh(con, None)
        rows = con.execute(
            "SELECT provider, model, reqs, fresh_in, output, cost_basis "
            "FROM fact_usage WHERE source='dsh'"
        ).fetchall()
        assert n == 1 and len(rows) == 1
        assert rows[0] == ("Zhipu", "glm-5.3-flash", 1, 5, 2, "local")
    finally:
        con.close()


def test_parse_dsh_grains(tmp_path, monkeypatch):
    _dsh_analytic_env(tmp_path, monkeypatch)
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        con.execute(at.SCHEMA)
        total = at._parse_dsh(con, None)
        assert total > 0
        reqs = con.execute(
            "SELECT request_id, provider, model, input, output "
            "FROM fact_model_request WHERE source='dsh'"
        ).fetchall()
        assert len(reqs) == 1
        assert reqs[0][1:3] == ("zai-coding-cn", "glm-5.3-flash")
        assert (reqs[0][3], reqs[0][4]) == (5, 2)
        tools = con.execute(
            "SELECT tool_name, status FROM fact_tool_call WHERE source='dsh'"
        ).fetchall()
        assert tools == [("bash", "completed")]
        sess = con.execute(
            "SELECT session_id, model FROM dim_session WHERE session_id LIKE 'session-%'"
        ).fetchall()
        assert sess and sess[0][1] == "glm-5.3-flash"
    finally:
        con.close()
