"""Hermetic tests for the opencode v2 lanes (S1/S2/S3/S5) + analytics S4.

Synthetic sqlite opencode DBs in tmp_path; module constants monkeypatched.
No live db, snapshot mount, or model touched. Fixture DBs carry both the
v1 (message/part) and v2 (session_v2/session_message/event) schemas so
lane interaction — not just lane output — is asserted.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.maintenance import harvest_conversations as hc  # noqa: E402
from helpers.analytics import model_analytics as ma  # noqa: E402

V2_SCHEMA = """
CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
  workspace_id TEXT, parent_id TEXT, slug TEXT NOT NULL, directory TEXT NOT NULL,
  path TEXT, title TEXT, version TEXT NOT NULL, cost REAL DEFAULT 0 NOT NULL,
  agent TEXT, model TEXT, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL);
CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
  type TEXT NOT NULL, seq INTEGER NOT NULL, time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE event (id TEXT PRIMARY KEY, aggregate_id TEXT NOT NULL,
  seq INTEGER NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL,
  created INTEGER DEFAULT 0 NOT NULL);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
  time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL,
  session_id TEXT NOT NULL, time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL, data TEXT NOT NULL);
"""

AST = {
    "agent": "build",
    "model": {"providerID": "opencode", "id": "glm-5.3-flash", "variant": "default"},
    "content": [
        {"type": "reasoning", "text": "thinking it over",
         "time": {"created": 2000, "completed": 2001}},
        {"type": "text", "text": "done deal"},
        {"type": "tool", "id": "call_1", "name": "bash",
         "state": {"status": "completed", "input": {"command": "ls"},
                   "content": [{"type": "text", "text": "out"}],
                   "metadata": {"exit": 0}},
         "time": {"created": 2002, "completed": 2003}},
        {"type": "mystery", "text": "future block kind"},
    ],
    "finish": "stop",
    "cost": 0.01,
    "tokens": {"input": 10, "output": 5, "reasoning": 2,
               "cache": {"read": 0, "write": 0}},
    "time": {"created": 1999, "completed": 2004},
}


def _v2_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(V2_SCHEMA)
    con.execute(
        "INSERT INTO session_v2 VALUES ('s9', 'p', NULL, NULL, 'slug', '/repo', "
        "NULL, 'Nine', '2.0.26', 0, 'build', "
        "'{\"providerID\": \"opencode\", \"id\": \"glm-5.3-flash\"}', 1000, 3000)"
    )
    con.execute(
        "INSERT INTO session_message VALUES ('m-u1', 's9', 'user', 0, 1000, 1000, ?)",
        (json.dumps({"text": "hello v2", "time": {"created": 1000}}),),
    )
    con.execute(
        "INSERT INTO session_message VALUES ('m-a1', 's9', 'assistant', 1, 1999, 2004, ?)",
        (json.dumps(AST),),
    )
    con.execute(
        "INSERT INTO session_message VALUES ('m-c1', 's9', 'compaction', 2, 2500, 2500, ?)",
        (json.dumps({"status": "completed", "reason": "manual",
                     "summary": "the big summary"}),),
    )
    con.execute(
        "INSERT INTO session_message VALUES ('m-s1', 's9', 'synthetic', 3, 2600, 2600, ?)",
        (json.dumps({"text": "synthetic note"}),),
    )
    con.execute(
        "INSERT INTO session_message VALUES ('m-i1', 's9', 'idle', 4, 2700, 2700, ?)",
        (json.dumps({"time": {"created": 2700}, "outcome": "succeeded"}),),
    )
    # events: one dropped type twice (last wins), one kept type (never
    # emitted by S2), one step-finish with usage for the analytics half.
    for i, (pid, part, t) in enumerate([
        ("prt_1", {"id": "prt_1", "sessionID": "s9", "messageID": "m-a1",
                   "type": "patch", "hash": "h1", "files": ["a.py"]}, 1500),
        ("prt_1", {"id": "prt_1", "sessionID": "s9", "messageID": "m-a1",
                   "type": "patch", "hash": "h2", "files": ["a.py", "b.py"]}, 1501),
        ("prt_2", {"id": "prt_2", "sessionID": "s9", "messageID": "m-a1",
                   "type": "text", "text": "dup of v2 text"}, 1502),
        ("prt_3", {"id": "prt_3", "sessionID": "s9", "messageID": "m-a1",
                   "type": "step-finish", "reason": "stop", "cost": 0.02,
                   "tokens": {"total": 17, "input": 10, "output": 5, "reasoning": 2,
                              "cache": {"read": 0, "write": 0}}}, 1503),
    ]):
        con.execute(
            "INSERT INTO event VALUES (?, 's9', ?, 'message.part.updated.1', ?, ?)",
            (f"e{i}", i, json.dumps({"sessionID": "s9", "part": part}), t),
        )
    con.commit()
    con.close()


@pytest.fixture()
def v2_env(tmp_path, monkeypatch):
    db = tmp_path / "live/opencode.db"
    _v2_db(db)
    (tmp_path / "nosnaps").mkdir(exist_ok=True)
    monkeypatch.setattr(hc, "_opencode_sources", lambda: [(db, "opencode:live", False)])
    monkeypatch.setattr(hc, "_prime_files", lambda: [])
    monkeypatch.setattr(hc, "_zcode_groups", lambda: {})
    monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path / "nosnaps")
    return tmp_path, db


def _corpus_rows(tmp_path):
    import pyarrow.parquet as pq

    rows = {}
    for f in (tmp_path / "corpus").rglob("*.parquet"):
        for r in pq.read_table(f).to_pylist():
            rows[r["part_id"]] = r
    return rows


def test_v2_lane_content_model_and_skips(v2_env):
    tmp_path, _db = v2_env
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "opencode:live:v2" in stats["lanes"]
    assert "opencode:live:evt" in stats["lanes"]
    rows = _corpus_rows(tmp_path)
    # user text, assistant blocks (mystery block skipped), compaction, synthetic
    assert rows["m-u1"]["text"] == "hello v2"
    assert rows["m-u1"]["role"] == "user"
    assert rows["m-a1#0"]["part_type"] == "reasoning"
    assert rows["m-a1#2"]["part_type"] == "tool"
    assert json.loads(rows["m-a1#2"]["meta"])["tool"] == "bash"
    assert "m-a1#3" not in rows
    assert rows["m-c1"]["part_type"] == "compaction"
    assert "big summary" in rows["m-c1"]["text"]
    assert rows["m-s1"]["part_type"] == "text"
    assert "m-i1" not in rows
    # model facet: plain non-empty strings, never dicts (G3 trap)
    for r in rows.values():
        assert r["model"] and not r["model"].startswith("{")
    assert rows["m-a1#0"]["model"] == "glm-5.3-flash"
    # event lane: patch last-wins, step-finish present, text never emitted
    assert json.loads(rows["prt_1"]["meta"])["hash"] == "h2"
    assert rows["prt_3"]["part_type"] == "step-finish"
    assert "prt_2" not in rows


def test_v2_rerun_quiet_no_dupes(v2_env):
    tmp_path, _db = v2_env
    hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    before = _corpus_rows(tmp_path)
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert "opencode total new: 0" in stats["lanes"]
    assert _corpus_rows(tmp_path) == before


def test_v2_drift_names_lane(v2_env):
    tmp_path, db = v2_env
    hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    out = hc.harvest(check=True, corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert out["check"] and not out["index_stale"], out["stale_new"]
    # v1 frozen, v2 advancing: only the v2 lane fires
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO session_message VALUES ('m-u2', 's9', 'user', 5, 9000, 9000, ?)",
        (json.dumps({"text": "later hello", "time": {"created": 9000}}),),
    )
    con.commit()
    con.close()
    out = hc.harvest(check=True, corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert out["index_stale"]
    assert any(":v2" in d for d in out["stale_new"]), out["stale_new"]
    assert not any(d.startswith("changed opencode:live (") for d in out["stale_new"])


def test_v2_backfill_seed_skips_harvested(v2_env):
    """Per-session seeding: rows older than the corpus parquet are skipped
    (no duplicates under fresh ids), newer rows ingest."""
    tmp_path, db = v2_env
    hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    # a new v2 row older than the lane watermark but newer than nothing:
    # corpus already covers the session, so only strictly-newer rows land.
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO session_message VALUES ('m-u0', 's9', 'user', -1, 500, 500, ?)",
        (json.dumps({"text": "predates everything", "time": {"created": 500}}),),
    )
    con.commit()
    con.close()
    stats = hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    rows = _corpus_rows(tmp_path)
    assert "m-u0" not in rows
    assert "opencode total new: 0" in stats["lanes"]


def test_missing_snapshot_root_is_drift(v2_env, monkeypatch):
    tmp_path, _db = v2_env
    hc.harvest(corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    monkeypatch.setattr(hc, "SNAP_ROOT", Path("/nonexistent-timeshift-root"))
    out = hc.harvest(check=True, corpus_root=tmp_path / "corpus", db=tmp_path / "c.duckdb")
    assert out["index_stale"]
    assert any("missing snapshot root" in d for d in out["stale_new"])


def test_oc_model_value_shapes():
    assert hc._oc_model_value("glm-5.3-flash") == "glm-5.3-flash"
    assert hc._oc_model_value({"modelId": "zm"}) == "zm"
    assert hc._oc_model_value({"providerID": "opencode", "id": "big-pickle"}) == "big-pickle"
    assert hc._oc_model_value('{"providerID": "opencode", "modelID": "x"}') == "x"
    assert hc._oc_model_value("") == ""
    assert hc._oc_model_value(None) == ""


def test_oc_messages_v2_union(tmp_path):
    db = tmp_path / "oc.db"
    _v2_db(db)
    # a v1 assistant message alongside the v2 rows
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO message VALUES ('m-old', 's9', 1000, 1000, ?)",
        (json.dumps({"role": "assistant", "providerID": "openrouter",
                     "modelID": "or-model", "tokens": {"input": 1, "output": 2,
                     "reasoning": 0, "cache": {"read": 0, "write": 0}},
                     "cost": 0.03}),),
    )
    con.commit()
    con.close()
    rows = {r[3]: r for r in ma._oc_messages(str(db))}
    assert "m-old" in rows and "m-a1" in rows
    day, prov, model, _mid, inp, _c, outp, _r, cost = rows["m-a1"]
    assert prov == "opencode" and model == "glm-5.3-flash"
    assert (inp, outp, cost) == (10, 5, 0.01)
    assert rows["m-old"][1:3] == ("openrouter", "or-model")
