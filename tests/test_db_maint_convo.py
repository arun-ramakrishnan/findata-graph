#!/usr/bin/env python3
"""Tests for the convo_search backup registrations in db_maint.

Two artifacts, two reasons (see the method docstrings):
- the index (duckdb, native FTS inside) — rebuildable, but a cold
  rebuild embeds 50k+ texts and the pool lane took 2h10m;
- the harvested corpus — the archive of record for history the
  harnesses delete, with no rebuild path at all.

Hermetic: the sources are synthetic files under tmp_path, and the
module-level REPO / CORPUS_ROOT the methods read at call time are
retargeted with monkeypatch.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from helpers.core.zstd_io import decompress_file  # noqa: E402
from helpers.maintenance import harvest_conversations as hc  # noqa: E402
from helpers.maintenance import rebuild_convo_search as rcs  # noqa: E402
from helpers.maintenance import db_maint as dm  # noqa: E402
from helpers.maintenance.db_maint import DBMaintainer  # noqa: E402


def _maintainer(tmp_path: Path) -> DBMaintainer:
    db = tmp_path / "live/research.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(db).execute("CREATE TABLE IF NOT EXISTS t (a)")
    return DBMaintainer(db, tmp_path / "db-backup/research_backup.db")


def _make_index(tmp_path: Path) -> Path:
    """A tiny convo_search duckdb (native index lives inside) under tmp REPO."""
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    idx = mem / "convo_search.duckdb"
    con = duckdb.connect(str(idx))
    con.execute("CREATE TABLE convo_search (harness VARCHAR, part_id VARCHAR)")
    con.execute("INSERT INTO convo_search VALUES ('opencode', 'p1')")
    con.close()
    return idx


def test_backup_convo_search_writes_primary_only(tmp_path, monkeypatch):
    """The convo_search PRIMARY stays backed up (vectors included — the
    2h10m cold re-embed insurance); the FTS5 sidecar is gone (native FTS
    lives inside the store, duckdb_sidecar_fold S5), so nothing else is
    expected in the backup dir."""
    from helpers.maintenance import db_maint

    monkeypatch.setattr(rcs, "REPO", tmp_path)
    m = _maintainer(tmp_path)
    _make_index(tmp_path)

    total = m._backup_convo_search()
    assert total > 0
    out = tmp_path / "db-backup"
    assert (out / "convo_search_backup.duckdb.zst").exists()

    # the duckdb copy must be a real, openable database
    restored = tmp_path / "restored.duckdb"
    decompress_file(out / "convo_search_backup.duckdb.zst", restored)
    rows = (
        duckdb.connect(str(restored), read_only=True)
        .execute("SELECT harness, part_id FROM convo_search")
        .fetchall()
    )
    assert rows == [("opencode", "p1")]
    assert db_maint.DBMaintainer is DBMaintainer


def test_backup_convo_search_skips_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rcs, "REPO", tmp_path)
    m = _maintainer(tmp_path)
    assert m._backup_convo_search() == 0
    assert not (tmp_path / "db-backup/convo_search_backup.duckdb.zst").exists()


def test_backup_convo_corpus_tars_every_session(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from compression import zstd

    corpus = tmp_path / "data/harness"
    monkeypatch.setattr(hc, "CORPUS_ROOT", corpus)
    m = _maintainer(tmp_path)
    table = pa.table({"part_id": ["p1"], "text": ["hi"]})
    for harness, sessions in (("opencode", ("s1", "s2")), ("zcode", ("z1",))):
        d = corpus / harness / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        for sid in sessions:
            pq.write_table(table, d / f"{sid}.parquet")

    size = m._backup_convo_corpus()
    assert size > 0
    zst = tmp_path / "db-backup/convo_corpus_backup.tar.zst"
    assert zst.exists()
    with zstd.open(zst, "rb") as raw:
        with tarfile.open(fileobj=raw, mode="r|") as tar:
            names = sorted(Path(m.name).name for m in tar)
    assert names == ["s1.parquet", "s2.parquet", "z1.parquet"]


def test_backup_memory_sidecars_covers_all_and_hides_secrets(tmp_path):
    """Thesis: everything under memory/ + memory/data/ is in db-backup —
    except secrets (never multiplied) and transient WAL/lock files."""
    from compression import zstd

    m = _maintainer(tmp_path)
    mem = tmp_path / "live"
    (mem / "data/rpt_raw").mkdir(parents=True, exist_ok=True)
    covered = {
        "research.db": b"x",
        "graph.duckdb": b"x",
        "embed_store.db": b"x",
        "corpus.duckdb": b"x",
        "doc_search.db": b"x",
        "script_search.duckdb": b"x",
        "convo_search.duckdb": b"x",
        "data/sources.duckdb": b"x",
        "data/agent_traces.duckdb": b"x",
        "data/model_usage.duckdb": b"x",
    }
    for rel, blob in covered.items():
        (mem / rel).write_bytes(blob)
    sidecars = {
        "embed_matrix.f32": b"m",
        "embed_matrix.json": b"{}",
        "graph_layout.json": b"{}",
        "yf_relations_fetch_cache.json": b"[]",
        "data/wikidata_qids.parquet": b"pq",
        "data/shp_worklist.csv": b"a,b",
        "data/rpt_raw/drop.parquet": b"raw",
    }
    for rel, blob in sidecars.items():
        p = mem / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    # corpus has its own tar (T5) — must not be duplicated into the sidecar tar
    (mem / "data/harness/opencode/conversations").mkdir(parents=True, exist_ok=True)
    (mem / "data/harness/opencode/conversations/s1.parquet").write_bytes(b"c")
    # …but the OTHER files under data/harness/ are irreplaceable operator
    # artifacts with no other backup (prime-rlm memory_trail.md + the
    # pre-consolidation harness_state tarball) — the sweep must take them
    (mem / "data/harness/prime-rlm").mkdir(parents=True, exist_ok=True)
    (mem / "data/harness/prime-rlm/memory_trail.md").write_bytes(b"digest")
    (mem / "data/harness/prime-rlm/prime-memory-preconsolidation.tar.gz").write_bytes(b"gz")
    # deny-listed
    denied = {
        ".env": b"SECRET=1",
        "goog_svc_account.json": b"{}",
        "research.db-wal": b"w",
        "graph.duckdb.build.lock": b"",
    }
    for rel, blob in denied.items():
        (mem / rel).write_bytes(blob)

    size = m._backup_memory_sidecars()
    assert size > 0
    zst = tmp_path / "db-backup/memory_sidecars_backup.tar.zst"
    with zstd.open(zst, "rb") as raw:
        with tarfile.open(fileobj=raw, mode="r|") as tar:
            # arcnames are relative to memory/, so compare against rel paths
            names = {t.name for t in tar}

    assert set(sidecars) <= names, f"sidecars missing from the tar: {set(sidecars) - names}"
    assert not (names & set(covered)), "dedicated backups must not be duplicated"
    assert not (names & set(denied)), f"deny-listed files leaked: {names & set(denied)}"
    assert "data/harness/opencode/conversations/s1.parquet" not in names, "corpus has its own tar"
    harness_artifacts = {
        "data/harness/prime-rlm/memory_trail.md",
        "data/harness/prime-rlm/prime-memory-preconsolidation.tar.gz",
    }
    assert harness_artifacts <= names, (
        "harness operator artifacts must be swept — nothing else backs them up"
    )
    assert len(names) == len(set(names)), "the memory/ + memory/data/ walks overlap"


def test_backup_memory_sidecars_skips_when_all_covered(tmp_path):
    m = _maintainer(tmp_path)
    mem = tmp_path / "live"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "research.db").write_bytes(b"x")
    assert m._backup_memory_sidecars() == 0
    assert not (tmp_path / "db-backup/memory_sidecars_backup.tar.zst").exists()


def test_backup_convo_corpus_skips_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "CORPUS_ROOT", tmp_path / "data/harness")
    m = _maintainer(tmp_path)
    assert m._backup_convo_corpus() == 0
    assert not (tmp_path / "db-backup/convo_corpus_backup.tar.zst").exists()


# --------------------------------------------------------------------------- #
# F4 dead-block probe (convo_search_dead_block_compaction S2)                  #
# --------------------------------------------------------------------------- #
class TestF4DeadBlockProbe:
    """The duckdb sidecar loop measures block anatomy over its own
    connection (a nested open would deadlock on its exclusive flock)
    and raises the F4 alert above DUCKDB_DEAD_PCT / DUCKDB_DEAD_BYTES."""

    def test_sidecar_row_carries_anatomy_and_alert_fires(self, tmp_path, monkeypatch, caplog):
        import logging as _logging

        from helpers.maintenance import db_maint as dm

        idx = _make_index(tmp_path)
        # retarget the registered estate at the tmp store
        monkeypatch.setattr(dm, "SIDECAR_DUCKDB", ((str(idx), False),))
        monkeypatch.setattr(dm, "REPO_ROOT", tmp_path)
        m2 = dm.DBMaintainer(
            tmp_path / "live/research.db",
            tmp_path / "db-backup/research_backup.db",
            backup_sidecars=False,
        )
        with caplog.at_level(_logging.WARNING, logger=m2.logger.name):
            rows = m2.maintain_registered_sidecars()
        duck_rows = [r for r in rows if r.get("engine") == "duckdb"]
        assert len(duck_rows) == 1
        row = duck_rows[0]
        assert row["action"] == "checkpoint"
        assert row["dead_blocks"] == 0  # fresh store: fully compact
        assert row["dead_pct"] == 0.0
        # threshold-down: the alert must fire (P3.6 shape, advisory only)
        monkeypatch.setattr(type(m2), "DUCKDB_DEAD_PCT", -1.0)
        with caplog.at_level(_logging.WARNING, logger=m2.logger.name):
            m2.maintain_registered_sidecars()
        assert any("F4 alert" in r.message for r in caplog.records)

    def test_report_prints_dead_note(self, tmp_path, monkeypatch, capsys):
        from helpers.maintenance.db_maint import _print_report

        r = {
            "settings": {},
            "before": {
                "file_size": 1,
                "pages": 1,
                "freelist": 0,
                "wasted_pct": 0.0,
                "wasted_bytes": 0,
            },
            "after": {
                "file_size": 1,
                "pages": 1,
                "freelist": 0,
                "wasted_pct": 0.0,
                "wasted_bytes": 0,
            },
            "stat_staleness_before": {},
            "stat_staleness_after": {},
            "backup": {"path": "b", "size": 1},
            "sidecars": [
                {
                    "store": "memory/convo_search.duckdb",
                    "engine": "duckdb",
                    "action": "checkpoint",
                    "dead_pct": 36.5,
                    "dead_bytes": 694_000_000,
                    "secs": 0.1,
                },
                {
                    "store": "memory/embed_store.db",
                    "engine": "sqlite",
                    "action": "checkpoint",
                    "wasted_pct": 0.5,
                    "secs": 0.1,
                },
            ],
            "indexes": [],
            "integrity_check": "ok",
            "foreign_key_violations": 0,
        }
        _print_report(r)
        out = capsys.readouterr().out
        assert "dead 36.5% (661.8 MB)" in out  # _fmt_bytes is binary
        assert "wasted 0.5%" in out


# --------------------------------------------------------------------------- #
# Proxy session store (billion-context pure-SQLite default)                    #
# --------------------------------------------------------------------------- #
def _fake_proxy_index(tmp_path: Path) -> Path:
    """A tiny stand-in for the proxy's sessions/index.db."""
    idx = tmp_path / "proxy/index.db"
    idx.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(idx)
    con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, namespace TEXT)")
    con.execute("CREATE TABLE blocks (block_id TEXT, session_id TEXT, payload_json TEXT)")
    con.execute("INSERT INTO sessions VALUES (?, ?)", ("s1", "proxy"))
    con.execute("INSERT INTO blocks VALUES (?, ?, ?)", ("b1", "s1", "{}"))
    con.commit()
    con.close()
    return idx


def test_backup_bili_index_copies_proxy_primary_store(tmp_path, monkeypatch):
    """The proxy store is the SOLE durable copy of proxy sessions under the
    pure-SQLite default (0.1.214+), so it gets a recovery copy like every
    other primary with no rebuild path. The source is opened read-only —
    the live proxy owns that file."""
    idx = _fake_proxy_index(tmp_path)
    monkeypatch.setattr(hc, "bili_index_path", lambda: idx)
    m = _maintainer(tmp_path)
    size = m._backup_bili_index()
    out = tmp_path / "db-backup/bili_index_backup.db.zst"
    assert out.exists()
    assert size == out.stat().st_size > 0
    restored = tmp_path / "restored.db"
    decompress_file(out, restored)
    con = sqlite3.connect(restored)
    try:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert con.execute("SELECT namespace FROM sessions").fetchone()[0] == "proxy"
    finally:
        con.close()


def test_backup_bili_index_skips_when_proxy_absent(tmp_path, monkeypatch):
    """Machines without the proxy stay green — absence is not drift."""
    monkeypatch.setattr(hc, "bili_index_path", lambda: tmp_path / "nope/index.db")
    m = _maintainer(tmp_path)
    assert m._backup_bili_index() == 0
    assert not (tmp_path / "db-backup/bili_index_backup.db.zst").exists()


def test_backup_bili_index_falls_back_to_online_backup_when_vacuum_into_fails(
    tmp_path, monkeypatch
):
    """A malformed/odious source must still yield a recovery copy: if the
    VACUUM INTO rung fails, the leg falls back to the online backup API
    instead of silently skipping (2026-11-20 corruption incident regression
    guard)."""
    idx = _fake_proxy_index(tmp_path)
    monkeypatch.setattr(hc, "bili_index_path", lambda: idx)

    class _EvilConn:
        """Delegates to a real read-only conn but refuses VACUUM INTO."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("VACUUM INTO"):
                raise sqlite3.DatabaseError("database disk image is malformed")
            return self._inner.execute(sql, params)

        def backup(self, target, **kw):
            return self._inner.backup(target, **kw)

        def close(self):
            return self._inner.close()

    real_connect = dm.connect

    def evil_connect(path, **kw):
        return _EvilConn(real_connect(path, **kw))

    monkeypatch.setattr(dm, "connect", evil_connect)
    m = _maintainer(tmp_path)
    size = m._backup_bili_index()
    out = tmp_path / "db-backup/bili_index_backup.db.zst"
    assert out.exists()
    assert size == out.stat().st_size > 0
    restored = tmp_path / "restored.db"
    decompress_file(out, restored)
    con = sqlite3.connect(restored)
    try:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert con.execute("SELECT namespace FROM sessions").fetchone()[0] == "proxy"
    finally:
        con.close()
