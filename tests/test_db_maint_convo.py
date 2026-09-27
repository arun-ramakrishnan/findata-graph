#!/usr/bin/env python3
"""Tests for the convo_search backup registrations in db_maint.

Two artifacts, two reasons (see the method docstrings):
- the index pair (duckdb + FTS sidecar) — rebuildable, but a cold
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
from helpers.maintenance.db_maint import DBMaintainer  # noqa: E402


def _maintainer(tmp_path: Path) -> DBMaintainer:
    db = tmp_path / "live/research.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(db).execute("CREATE TABLE IF NOT EXISTS t (a)")
    return DBMaintainer(db, tmp_path / "db-backup/research_backup.db")


def _make_index(tmp_path: Path) -> tuple[Path, Path]:
    """A tiny convo_search duckdb + its FTS5 sidecar under tmp REPO."""
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    idx = mem / "convo_search.duckdb"
    con = duckdb.connect(str(idx))
    con.execute("CREATE TABLE convo_search (harness VARCHAR, part_id VARCHAR)")
    con.execute("INSERT INTO convo_search VALUES ('opencode', 'p1')")
    con.close()
    fts = mem / "convo_search_fts.db"
    s = sqlite3.connect(fts)
    s.execute(rcs.FTS_DDL)
    s.execute("INSERT INTO convo_fts VALUES ('hello world', 'opencode', 'p1', 's1')")
    s.commit()
    s.close()
    return idx, fts


def test_backup_convo_search_writes_both_zst(tmp_path, monkeypatch):
    from helpers.maintenance import db_maint

    monkeypatch.setattr(rcs, "REPO", tmp_path)
    m = _maintainer(tmp_path)
    _make_index(tmp_path)

    total = m._backup_convo_search()
    assert total > 0
    out = tmp_path / "db-backup"
    assert (out / "convo_search_backup.duckdb.zst").exists()
    assert (out / "convo_search_fts_backup.db.zst").exists()

    # the duckdb copy must be a real, openable database
    restored = tmp_path / "restored.duckdb"
    decompress_file(out / "convo_search_backup.duckdb.zst", restored)
    rows = (
        duckdb.connect(str(restored), read_only=True)
        .execute("SELECT harness, part_id FROM convo_search")
        .fetchall()
    )
    assert rows == [("opencode", "p1")]

    # the sidecar copy must carry the FTS content
    fts_copy = tmp_path / "restored_fts.db"
    decompress_file(out / "convo_search_fts_backup.db.zst", fts_copy)
    assert sqlite3.connect(fts_copy).execute("SELECT COUNT(*) FROM convo_fts").fetchone()[0] == 1
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
        "corpus.db": b"x",
        "doc_search.db": b"x",
        "script_search.db": b"x",
        "convo_search.duckdb": b"x",
        "convo_search_fts.db": b"x",
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
        "corpus.db-shm": b"s",
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
