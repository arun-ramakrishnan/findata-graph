"""Hermetic tests for the snapshot lanes (S1/S2/S3/S4/S6).

Covers the timeshift-snapshot work in harvest_conversations:
S1 the image registry (a scalar count cannot express coverage, and an absent
backup mount is NORMAL - never drift), S2 the dsh snapshot glob, S3 the live-id
probe reading the modern tables and failing closed, S4 the probe-safe read-only
open, S6 the per-image zcode provenance label.

No live harness, no model, no real /mnt/store/timeshift mount is touched: the
mount root is always a tmp_path tree, and Path.home is retargeted wherever a
lane resolves it per call.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.maintenance import harvest_conversations as hc  # noqa: E402


class _Store:
    """Minimal watermark store: enough for the census helpers (no duckdb)."""

    def __init__(self, wm: dict[str, str] | None = None) -> None:
        self._wm = dict(wm or {})
        self._pending: list[tuple[str, str]] = []

    def watermark(self, key: str) -> str | None:
        return self._wm.get(key)

    def set_watermark(self, key: str, value: str) -> None:
        self._wm[key] = value
        self._pending.append((key, value))


def _image(root: Path, stamp: str) -> Path:
    d = root / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- S1: the image registry ---------------------------------------------------


def test_snap_stamps_absent_mount_is_empty(tmp_path, monkeypatch):
    """A backup mount that is not mounted lists no images, quietly."""
    monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path / "unmounted")
    assert hc._snap_stamps() == []


def test_snap_stamps_lists_dated_images_only(tmp_path, monkeypatch):
    root = tmp_path / "snaps"
    _image(root, "2026-09-26_20-00-02")
    _image(root, "2026-11-07_17-00-06")
    (root / "not-an-image.txt").write_text("x")
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    assert hc._snap_stamps() == ["2026-09-26_20-00-02", "2026-11-07_17-00-06"]


def test_absent_mount_is_not_drift(tmp_path, monkeypatch):
    """The operator constraint: an unmounted backup mount never goes red."""
    monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path / "unmounted")
    store = _Store({hc._SNAP_SEEN_KEY: json.dumps(["2026-09-26_20-00-02"])})
    assert hc._snap_lane_drift(store) == []
    # ...and a mount-less harvest records nothing rather than stamping "0".
    assert hc._record_snap_seen(store) == ["2026-09-26_20-00-02"]
    assert store.watermark(hc._SNAP_COUNT_KEY_LEGACY) is None


def test_growth_is_drift_rotation_is_not(tmp_path, monkeypatch):
    root = tmp_path / "snaps"
    _image(root, "2026-09-26_20-00-02")
    _image(root, "2026-11-07_17-00-06")
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    store = _Store({hc._SNAP_SEEN_KEY: json.dumps(["2026-09-26_20-00-02"])})
    assert hc._snap_lane_drift(store) == ["new snapshot 2026-11-07_17-00-06"]

    store.set_watermark(hc._SNAP_SEEN_KEY, json.dumps(hc._record_snap_seen(store)))
    assert hc._snap_lane_drift(store) == []

    # an image rotating away stays recorded: no re-announcement loop
    (root / "2026-11-07_17-00-06").rmdir()
    assert hc._snap_lane_drift(store) == []
    assert "2026-11-07_17-00-06" in hc._snap_seen(store)


def test_pre_registry_corpus_announces_once_not_per_image(tmp_path, monkeypatch):
    """A corpus harvested before the registry existed gets ONE line."""
    root = tmp_path / "snaps"
    _image(root, "2026-09-26_20-00-02")
    _image(root, "2026-10-26_20-00-01")
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    legacy = _Store({hc._SNAP_COUNT_KEY_LEGACY: "2"})
    assert hc._snap_lane_drift(legacy) == ["new snapshots 2 visible, none recorded as harvested"]
    # a corpus that never harvested at all announces nothing
    assert hc._snap_lane_drift(_Store()) == []


def test_corrupt_registry_does_not_gate_forever(tmp_path, monkeypatch):
    root = tmp_path / "snaps"
    _image(root, "2026-11-07_17-00-06")
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    store = _Store({hc._SNAP_SEEN_KEY: "{not json"})
    assert hc._snap_seen(store) == []
    assert hc._snap_lane_drift(store) == []  # no legacy marker: stay quiet


def test_coverage_names_the_mount_state(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "_opencode_sources", lambda: [])
    monkeypatch.setattr(hc, "_prime_files", lambda: [])
    monkeypatch.setattr(hc, "_zcode_groups", lambda: {})
    monkeypatch.setattr(hc, "_dsh_logs", lambda: [])
    monkeypatch.setattr(hc, "_bili_index_state", lambda: "missing")
    monkeypatch.setattr(hc, "_bili_index_path", lambda: tmp_path / "none.db")

    absent = tmp_path / "unmounted"
    monkeypatch.setattr(hc, "SNAP_ROOT", absent)
    lines = hc._coverage(_Store())
    assert any(ln.startswith("snapshots: mount absent") for ln in lines), lines

    root = tmp_path / "snaps"
    _image(root, "2026-09-26_20-00-02")
    _image(root, "2026-11-07_17-00-06")
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    lines = hc._coverage(_Store({hc._SNAP_SEEN_KEY: json.dumps(["2026-09-26_20-00-02"])}))
    assert any("2 image(s)" in ln and "1 unharvested" in ln for ln in lines), lines


# --- S2: the dsh snapshot lane ------------------------------------------------


def _dsh_log(path: Path, records: list[dict]) -> None:
    import zstandard

    path.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    with open(path, "wb") as fh:
        with cctx.stream_writer(fh) as w:
            for r in records:
                w.write((json.dumps(r) + chr(10)).encode())


def _dsh_snap_sessions(root: Path, stamp: str) -> Path:
    return _image(root, stamp) / "localhost" / "home" / "arun" / ".dsh" / "sessions"


def test_dsh_snapshot_logs_are_tagged_and_v4_preferred(tmp_path, monkeypatch):
    root = tmp_path / "snaps"
    snap = _dsh_snap_sessions(root, "2026-11-07_17-00-06")
    gone = snap / "proj" / "session-gone"
    _dsh_log(gone / "session.v4.jsonl.zstd", [{"type": "user/message", "seq": 1}])
    _dsh_log(gone / "session.jsonl.zstd", [{"type": "user/message", "seq": 1}])
    v0_only = snap / "proj" / "session-v0"
    _dsh_log(v0_only / "session.jsonl.zstd", [{"type": "user/message", "seq": 1}])

    home = tmp_path / "home"
    live = home / ".dsh" / "sessions" / "proj" / "session-live"
    _dsh_log(live / "session.v4.jsonl.zstd", [{"type": "user/message", "seq": 1}])

    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)

    by_sid = {sid: (p.name, src) for p, sid, src in hc._dsh_logs()}
    assert by_sid["session-gone"] == (
        "session.v4.jsonl.zstd",
        "dsh-snap:2026-11-07_17-00-06",
    )
    assert by_sid["session-v0"] == ("session.jsonl.zstd", "dsh-snap:2026-11-07_17-00-06")
    assert by_sid["session-live"] == ("session.v4.jsonl.zstd", "dsh:live")
    # one entry per session dir - v4 and v0 never both get ingested
    assert len([s for s in by_sid if s == "session-gone"]) == 1


def test_dsh_snapshot_logs_are_not_in_the_live_index(tmp_path, monkeypatch):
    """Snapshot sessions were never in ~/.dsh/**/session-search.db: not lag."""
    root = tmp_path / "snaps"
    snap = _dsh_snap_sessions(root, "2026-11-07_17-00-06")
    _dsh_log(
        snap / "proj" / "session-gone" / "session.v4.jsonl.zstd",
        [{"type": "user/message", "seq": 9}],
    )
    home = tmp_path / "home"
    idx = home / ".dsh" / "profiles" / "web" / "session-search.db"
    idx.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(idx)
    con.execute("CREATE TABLE persisted_docs (session_id TEXT, seq REAL)")
    con.commit()
    con.close()

    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert hc._dsh_index_lag() == []


def test_dsh_absent_snapshot_mount_keeps_the_live_lane(tmp_path, monkeypatch):
    home = tmp_path / "home"
    live = home / ".dsh" / "sessions" / "proj" / "session-live"
    _dsh_log(live / "session.v4.jsonl.zstd", [{"type": "user/message", "seq": 1}])
    monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path / "unmounted")
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert [(sid, src) for _p, sid, src in hc._dsh_logs()] == [("session-live", "dsh:live")]


# --- S3: the live-id probe ----------------------------------------------------


def _oc_live_db(home: Path, *, session_v2=(), legacy=(), messages=()) -> Path:
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE session_message (id TEXT, session_id TEXT)")
    con.executemany("INSERT INTO session VALUES (?)", [(i,) for i in legacy])
    con.executemany("INSERT INTO session_v2 VALUES (?)", [(i,) for i in session_v2])
    con.executemany(
        "INSERT INTO session_message VALUES (?, ?)",
        [(f"m{i}", s) for i, s in enumerate(messages)],
    )
    con.commit()
    con.close()
    return db


def test_live_ids_read_the_modern_tables(tmp_path, monkeypatch):
    """The v2 cutover emptied `session`; the probe must not answer {} then."""
    home = tmp_path / "home"
    _oc_live_db(home, session_v2=["ses_a", "ses_b"], messages=["ses_c", "ses_a"])
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert hc._oc_live_ids() == {"ses_a", "ses_b", "ses_c"}


def test_live_ids_legacy_schema_still_answers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = home / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    con.execute("INSERT INTO session VALUES ('ses_old')")
    con.commit()
    con.close()
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert hc._oc_live_ids() == {"ses_old"}


def test_live_ids_fail_closed_on_an_empty_store(tmp_path, monkeypatch):
    """An empty union is indistinguishable from purge-everything: say None."""
    home = tmp_path / "home"
    _oc_live_db(home)
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert hc._oc_live_ids() is None


def test_live_ids_ignore_snapshots(tmp_path, monkeypatch):
    """A snapshot id set answers what existed THEN, never what exists now."""
    home = tmp_path / "home"
    _oc_live_db(home, session_v2=["ses_live"])
    root = tmp_path / "snaps"
    snap_db = (
        _image(root, "2026-07-26_19-00-02")
        / "localhost"
        / "home"
        / "arun"
        / ".local"
        / "share"
        / "opencode"
        / "opencode.db"
    )
    snap_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(snap_db)
    con.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    con.execute("INSERT INTO session VALUES ('ses_july')")
    con.commit()
    con.close()
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    monkeypatch.setattr(Path, "home", lambda: home, raising=False)
    assert hc._oc_live_ids() == {"ses_live"}


def test_live_ids_absent_store_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home", raising=False)
    assert hc._oc_live_ids() is None


# --- S4: the probe-safe read-only open ---------------------------------------


def test_probe_connect_reads_a_wal_database(tmp_path):
    """WAL is the live shape (opencode.db is WAL): the probe must read it."""
    db = tmp_path / "wal.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x TEXT)")
    con.execute("INSERT INTO t VALUES ('hi')")
    con.commit()
    con.close()  # closing checkpoints the WAL away

    probe = hc._probe_connect(db)
    try:
        assert probe.execute("SELECT x FROM t").fetchone()[0] == "hi"
    finally:
        probe.close()


# --- S6: per-image zcode provenance ------------------------------------------


def test_zcode_src_names_the_image(tmp_path, monkeypatch):
    root = tmp_path / "snaps"
    monkeypatch.setattr(hc, "SNAP_ROOT", root)
    old = _image(root, "2026-08-26_19-00-01") / "localhost/home/arun/.zcode/cli/rollout/f.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("")
    new = _image(root, "2026-11-07_17-00-06") / "localhost/home/arun/.zcode/cli/rollout/f.jsonl"
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("")
    live = tmp_path / "home" / ".zcode" / "cli" / "rollout" / "f.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("")

    assert hc._zcode_src_for([old]) == "zcode-snap:2026-08-26_19-00-01"
    # newest image wins when one session files span several
    assert hc._zcode_src_for([old, new]) == "zcode-snap:2026-11-07_17-00-06"
    # live wins over any snapshot: the same rule the other lanes use
    assert hc._zcode_src_for([old, live]) == "zcode:live"
    assert hc._zcode_src_for([]) == "zcode-snap:unknown"


def test_zcode_rows_carry_the_label_verbatim(tmp_path, monkeypatch):
    """Regression (2026-11-10): the row builders once composed
    `f"zcode:{src_tag}"` around a tag that had become a FULL label, so an S6
    snapshot row landed in the corpus as `zcode:zcode-snap:<stamp>` — which
    `_is_snapshot_src` does not recognise as snapshot-backed (it tests
    `startswith("zcode-snap")`) and which no `--source zcode-snap:` query
    matches. The builders must use the tag verbatim.
    """
    src = "zcode-snap:2026-11-07_17-00-06"
    best = {
        "requestId": "req-1",
        "startedAt": "2026-11-07T11:00:00Z",
        "request": {"messages": [{"role": "user", "content": "hello"}]},
        "response": {"responseId": "resp-1", "text": "hi", "reasoningText": ""},
    }
    rows = hc._zcode_req_rows(best, "s1", src, False)
    assert rows, "no req rows built"
    assert {r["source"] for r in rows} == {src}
    resps = {"resp-1": ("2026-11-07T11:00:01Z", "hi", "", "m", [])}
    rows = hc._zcode_resp_rows(resps, {}, "s1", src)
    assert rows, "no resp rows built"
    assert {r["source"] for r in rows} == {src}
    assert hc._is_snapshot_src(src, "zcode")
    assert not hc._is_snapshot_src(f"zcode:{src}", "zcode")


def test_zcode_src_under_is_prefix_safe(tmp_path):
    root = tmp_path / "snaps"
    inside = root / "2026-11-07_17-00-06" / "f.jsonl"
    outside = tmp_path / "snaps-other" / "f.jsonl"
    assert hc._is_under(inside, root)
    assert not hc._is_under(outside, root)
    # a sibling whose NAME merely prefixes the root is not inside it
    assert not hc._is_under(tmp_path / "snaps-other", root)
