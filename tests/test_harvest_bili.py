"""Hermetic tests for the bili harvest lane (S1-S3)."""

import json
import sqlite3

from helpers.maintenance.harvest_conversations import (
    SessionWriter,
    Store,
    _bili_drift,
    _harvest_bili,
)


def _fake_index(path, blocks, namespace=None):
    con = sqlite3.connect(path)
    if namespace is not None:
        con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, namespace TEXT)")
    con.execute(
        "CREATE TABLE blocks (block_id TEXT, session_id TEXT, ord INT,"
        " start_seq INT, end_seq INT, start_ref TEXT, end_ref TEXT, tier INT,"
        " summary_hash TEXT, one_hash TEXT, supersedes_block_id TEXT,"
        " active INT, created_at INT, covered_hash TEXT, payload_json TEXT)"
    )
    for i, (bid, sid, active, topic, summary) in enumerate(blocks):
        payload = json.dumps({"blockId": bid, "tier": 1, "topic": topic, "summary": summary})
        con.execute(
            "INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                bid,
                sid,
                i,
                0,
                0,
                None,
                None,
                1,
                f"hash-{bid}",
                None,
                None,
                active,
                1700000000000 + i,
                None,
                payload,
            ),
        )
    if namespace is not None:
        for sid in {b[1] for b in blocks}:
            con.execute("INSERT INTO sessions VALUES (?,?)", (sid, namespace))
    con.commit()
    con.close()


def test_bili_indexes_all_blocks_regardless_of_active(tmp_path, monkeypatch):
    """Inactive blocks hold unique topics (trial 4/10 ACTIVE-only) — the lane
    must not filter on `active`."""
    idx = tmp_path / "index.db"
    _fake_index(
        idx,
        [
            ("b1", "ses_x", 0, "old topic", "old verdict"),
            ("b2", "ses_x", 1, "new topic", "new verdict"),
        ],
        namespace="opencode",
    )
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    out = _harvest_bili(store, writer)
    assert "2 parts" in out
    assert set(writer.touched[("opencode", "ses_x")]) == {"ses_x#b1", "ses_x#b2"}
    assert _bili_drift(store) == []
    store.close()


def test_bili_second_pass_quiet_and_flip_reharvests(tmp_path, monkeypatch):
    """Fingerprint quiet on match; a pure active-flip re-harvests (S3)."""
    idx = tmp_path / "index.db"
    _fake_index(idx, [("b1", "ses_x", 1, "t", "s")], namespace="opencode")
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    _harvest_bili(store, writer)
    writer2 = SessionWriter(tmp_path / "corpus")
    assert "quiet" in _harvest_bili(store, writer2)
    con = sqlite3.connect(idx)
    con.execute("UPDATE blocks SET active = 0 WHERE block_id = 'b1'")
    con.commit()
    con.close()
    writer3 = SessionWriter(tmp_path / "corpus")
    out = _harvest_bili(store, writer3)
    assert "1 parts" in out
    row = writer3.touched[("opencode", "ses_x")]["ses_x#b1"]
    assert json.loads(row["meta"])["active"] is False
    store.close()


def test_bili_rows_merge_into_the_owning_lane_session_file(tmp_path, monkeypatch):
    """Routing must UNION with the owning lane's rows, never replace them.

    The owning lane already wrote this session; the bili blocks join it.
    flush() only writes the touched buffer, so marking the session `fresh`
    here would drop the other lane's parts on the floor — which is why a
    namespace-only fingerprint change must not bump the shape version `v`.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from helpers.maintenance.harvest_conversations import _SCHEMA

    idx = tmp_path / "index.db"
    _fake_index(idx, [("b1", "ses_x", 1, "topic", "summary")], namespace="dsh")
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)

    # Pre-existing rows from the dsh lane for the SAME session.
    out = tmp_path / "corpus" / "dsh" / "conversations"
    out.mkdir(parents=True)
    other = {
        "part_id": "ses_x:22",
        "message_id": "m22",
        "session_id": "ses_x",
        "ts": _dt(1700000000000),
        "role": "assistant",
        "agent": "dsh",
        "model": "",
        "part_type": "text",
        "text": "other lane row",
        "meta": "{}",
        "source": "dsh:live",
    }
    pq.write_table(pa.Table.from_pylist([other], schema=_SCHEMA), out / "ses_x.parquet")

    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    _harvest_bili(store, writer)
    writer.flush()
    rows = pq.read_table(out / "ses_x.parquet").to_pylist()
    assert {r["part_id"] for r in rows} == {"ses_x:22", "ses_x#b1"}
    store.close()


def _dt(ms):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def test_bili_missing_store_is_quiet_skip(tmp_path, monkeypatch):
    """Machines without the proxy stay green (absent source, not drift)."""
    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._bili_index_path",
        lambda: tmp_path / "nope.db",
    )
    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._bili_root",
        lambda: tmp_path / "no-such-store",
    )
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    assert "SKIP" in _harvest_bili(store, writer)
    assert _bili_drift(store) == []
    store.close()


def test_bili_lost_index_is_not_a_quiet_skip(tmp_path, monkeypatch):
    """billion-context installed but sessions/index.db gone = drift, not absence.

    Under `storage.unified=shadow` (0.1.197+) the index is a derived
    artifact double-ingested from the primary store, so a storage migration
    or shadow reset deletes it. Reporting that as the designed quiet skip
    would truncate the lane's history while the gate stayed green.
    """
    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._bili_index_path",
        lambda: tmp_path / "sessions" / "index.db",
    )
    (tmp_path / "billion-context").mkdir()
    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._bili_root",
        lambda: tmp_path / "billion-context",
    )
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    out = _harvest_bili(store, writer)
    assert "LOST" in out and "SKIP" not in out
    drift = _bili_drift(store)
    assert len(drift) == 1 and "LOST" in drift[0]
    store.close()


def test_bili_records_the_owning_namespace(tmp_path, monkeypatch):
    """dsh- and opencode-hosted sessions are distinguishable in the lane.

    `dsh` has its own corpus directory and query lane, so a bili block
    belonging to a dsh session must not be silently indistinguishable from
    an opencode one.
    """
    idx = tmp_path / "index.db"
    _fake_index(idx, [("b1", "ses_d", 1, "t", "s")], namespace="dsh")
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    _harvest_bili(store, writer)
    # routed to the OWNING harness, not to a bili-only directory
    assert set(writer.touched) == {("dsh", "ses_d")}
    row = writer.touched[("dsh", "ses_d")]["ses_d#b1"]
    meta = row["meta"]
    assert meta is not None
    assert json.loads(meta)["namespace"] == "dsh"
    assert row["source"] == "bili-index/dsh"
    # namespace rides the fingerprint, so a re-owned session re-harvests
    wm = store.watermark("bili:ses_d")
    assert wm is not None
    assert json.loads(wm)["ns"] == "dsh"
    store.close()


def test_bili_namespace_absent_table_degrades_to_unknown(tmp_path, monkeypatch):
    """An index without a `sessions` table must not abort the lane."""
    idx = tmp_path / "index.db"
    _fake_index(idx, [("b1", "ses_x", 1, "t", "s")])  # no sessions table
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    assert "1 parts" in _harvest_bili(store, writer)
    assert set(writer.touched) == {("unknown", "ses_x")}
    row = writer.touched[("unknown", "ses_x")]["ses_x#b1"]
    assert json.loads(row["meta"])["namespace"] == "unknown"
    assert row["source"] == "bili-index/unknown"
    store.close()


def test_bili_version_bump_writes_fresh_without_merging(tmp_path, monkeypatch):
    """A key-shape migration must not union both generations: a version bump
    writes the session file fresh (live lesson: 72 corpus rows indexed as
    57 + 57 stale doublons)."""
    import pyarrow.parquet as pq

    idx = tmp_path / "index.db"
    # No sessions table -> namespace 'unknown', a harness the bili lane owns,
    # which is the only case where the clean-rewrite guard applies.
    _fake_index(idx, [("b1", "ses_x", 1, "t", "s")])
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    _harvest_bili(store, writer)
    writer.flush()
    store.commit()
    f = tmp_path / "corpus" / "unknown" / "conversations" / "ses_x.parquet"
    assert pq.read_table(f).num_rows == 1
    # Plant a stale v1-shape row (bare block id) as the live migration saw.
    import pyarrow as pa

    t = pq.read_table(f)
    stale = t.slice(0, 1).to_pylist()
    stale[0]["part_id"] = "b1"
    t2 = pa.Table.from_pylist(t.to_pylist() + stale, schema=t.schema)
    pq.write_table(t2, f)
    assert pq.read_table(f).num_rows == 2
    # Simulate a v1 watermark (bare per-block dict, no version envelope).
    store.set_watermark("bili:ses_x", json.dumps({"b1": ["hash-b1", 1]}))
    store.commit()
    writer2 = SessionWriter(tmp_path / "corpus")
    _harvest_bili(store, writer2)
    writer2.flush()
    got = pq.read_table(f).to_pylist()
    assert len(got) == 1 and got[0]["part_id"] == "ses_x#b1"
    store.close()


def test_coverage_reports_every_lane_including_absent_bili(tmp_path, monkeypatch):
    from helpers.maintenance.harvest_conversations import _coverage

    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._bili_index_path",
        lambda: tmp_path / "nope.db",
    )
    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._bili_root",
        lambda: tmp_path / "no-such-store",
    )
    store = Store(tmp_path / "meta.duckdb")
    lines = _coverage(store)
    store.close()
    assert len(lines) == 5
    assert lines[4] == "bili: SKIP missing index.db (no billion-context on this machine)"
