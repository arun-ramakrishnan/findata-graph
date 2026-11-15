#!/usr/bin/env python3
"""DuckDB-native memory_search sidecar (duckdb_sidecar_fold S2).

Exercises the engine-agnostic rebuild/query split introduced for the fold:
- ``rebuild_memory_duckdb`` builds the `.duckdb` store (table + meta/info
  stamps + native FTS index over composed ``searchtext``);
- ``connect_memory_db`` routes `.duckdb` → DuckDB, everything else → SQLite;
- ``search_memories`` dispatches to the native ``match_bm25`` +
  Python-highlight path and returns the same dict contract as the FTS5
  path, with the kind/history axis as plain SQL predicates;
- memory_index_ready/stale + stored model/dims work on the DuckDB store.

Hermetic: tmp doctrine pools (zcode/prime/opencode + one snapshot image)
+ tmp `.duckdb` sidecar, fake embedder (same shape as
tests/test_memory_snapshot_lane.py fixtures).
"""

from __future__ import annotations

import json

import pytest

from helpers.maintenance import rebuild_memory_search as rms

pytestmark = [pytest.mark.integration]

_ZCODE = (
    "---\n"
    "name: user-workflow\n"
    "description: Operator contract for gates\n"
    "  and staging discipline\n"
    "---\n"
    "\n"
    "The operator owns stgit patch structure; agents edit files.\n"
)
_ZCODE_OLD = (
    "---\n"
    "name: user-workflow\n"
    "description: Operator contract for gates\n"
    "  and staging discipline\n"
    "---\n"
    "\n"
    "The operator owns stgit patch structure; SNAPSHOT WORDING.\n"
)
_OC_RECORD = (
    "ts=2026-09-24T00:00:00Z type=preference scope=findata-graph/gates "
    'content="TARGETED tests only after each fix." tags=gates\n'
)


def _fake_embed(text: str) -> list[float]:
    return [0.5] * 8


@pytest.fixture
def pools(tmp_path):
    """Tmp live pools + a one-image snapshot mount."""
    zcode = tmp_path / "zcode-projects"
    mem = zcode / "scope-main" / "memory"
    mem.mkdir(parents=True)
    (mem / "user-workflow.md").write_text(_ZCODE, encoding="utf-8")
    prime = tmp_path / "prime" / "harness" / "harness_state.json"
    prime.parent.mkdir(parents=True)
    prime.write_text(
        json.dumps(
            {"entries": {"memory": {"live_only": {"title": "live", "content": "live only"}}}}
        ),
        encoding="utf-8",
    )
    oc = tmp_path / "opencode-mem"
    oc.mkdir()
    (oc / "2026-09-24.logfmt").write_text(_OC_RECORD, encoding="utf-8")
    snap_root = tmp_path / "snap-root"
    image = rms._snap_home(snap_root, "2026-08-26_19-00-01")
    zdir = image / ".zcode" / "cli" / "memories" / "projects" / "scope-main" / "memory"
    zdir.mkdir(parents=True)
    (zdir / "user-workflow.md").write_text(_ZCODE_OLD, encoding="utf-8")
    return {
        "zcode_projects": zcode,
        "prime_state": prime,
        "opencode_dir": oc,
        "snap_root": snap_root,
    }


@pytest.fixture
def seeded(tmp_path, pools, monkeypatch):
    db = tmp_path / "memory_search.duckdb"
    monkeypatch.setattr(rms, "MEMORY_DUCKDB_DB", db)
    monkeypatch.setattr(rms, "MEMORY_DB", db)
    return db, pools


@pytest.fixture
def fake_local(monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        v = [0.0] * 8
        if "stgit" in text.lower() or "patch" in text.lower():
            v[2] = 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    return LE


def test_connect_routes_by_suffix(tmp_path):
    ddb = tmp_path / "x.duckdb"
    sdb = tmp_path / "x.db"
    c1 = rms.connect_memory_db(ddb)
    c2 = rms.connect_memory_db(sdb)
    try:
        import duckdb

        assert isinstance(c1, duckdb.DuckDBPyConnection)
        import sqlite3

        assert isinstance(c2, sqlite3.Connection)
    finally:
        c1.close()
        c2.close()


def test_rebuild_duckdb_builds_store_and_index(seeded):
    db, pools = seeded
    stats = rms.rebuild_memory_duckdb(db, embed_fn=_fake_embed, **pools)
    assert stats["total_units"] == 4  # 1 zcode + 1 prime + 1 opencode + 1 snap
    assert stats["history_units"] == 1
    assert stats["total_rows"] == 4
    assert stats["indexed"] == 4
    assert stats["embedded"] == 4
    conn = rms.connect_memory_db(db)
    try:
        assert rms.memory_index_ready(conn) is True
        assert rms.memory_index_stale(conn, **pools) is False
        assert rms._stored_embed_dims(conn) == 8
        # native FTS index present (shadow schema created by create_fts_index)
        schemas = {
            r[0]
            for r in conn.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        }
        assert "fts_main_memory_search" in schemas
    finally:
        conn.close()


def test_search_hybrid_returns_expected_shape(seeded, fake_local):
    db, pools = seeded
    rms.rebuild_memory_duckdb(db, embed_fn=fake_local.embed_document, **pools)
    conn = rms.connect_memory_db(db)
    try:
        out = rms.search_memories(conn, "stgit patch", limit=3)
        assert out["mode"] == "hybrid"
        assert len(out["results"]) >= 1
        hit = out["results"][0]
        assert hit["name"] == "user-workflow"
        assert hit["kind"] == "zcode"
        assert "<mark>" in hit["snippet"]
        assert hit["similarity"] is not None
        # dict contract shared with the FTS5 path
        assert set(hit) == {
            "name",
            "title",
            "kind",
            "path",
            "purpose",
            "snippet",
            "score",
            "similarity",
        }
    finally:
        conn.close()


def test_search_bm25_only_mode(seeded, fake_local):
    db, pools = seeded
    rms.rebuild_memory_duckdb(db, embed_fn=fake_local.embed_document, **pools)
    conn = rms.connect_memory_db(db)
    try:
        out = rms.search_memories(conn, "targeted tests", limit=3, hybrid=False)
        assert out["mode"] == "bm25"
        assert len(out["results"]) == 1
        assert out["results"][0]["similarity"] is None
        assert out["results"][0]["kind"] == "opencode"
    finally:
        conn.close()


def test_kind_and_history_filters(seeded, fake_local):
    db, pools = seeded
    rms.rebuild_memory_duckdb(db, embed_fn=fake_local.embed_document, **pools)
    conn = rms.connect_memory_db(db)
    try:
        # history="only" surfaces the snapshot row, not the live twin
        only = rms.search_memories(conn, "snapshot wording", limit=5, history="only")
        assert only["results"], "snapshot-only wording must be reachable via history=only"
        assert {r["kind"] for r in only["results"]} == {"zcode-snap"}
        # default excludes history (cosine extras may fill the page with
        # near-zero-sim live rows; the contract is only that NO snap row wins)
        live = rms.search_memories(conn, "snapshot wording", limit=5)
        assert "zcode-snap" not in {r["kind"] for r in live["results"]}
        # explicit kind wins
        zc = rms.search_memories(conn, "stgit patch", limit=5, kind="zcode")
        assert {r["kind"] for r in zc["results"]} == {"zcode"}
    finally:
        conn.close()


def test_stale_detects_new_unit(seeded):
    db, pools = seeded
    rms.rebuild_memory_duckdb(db, embed_fn=_fake_embed, **pools)
    (pools["zcode_projects"] / "scope-main" / "memory" / "new.md").write_text(
        _ZCODE.replace("user-workflow", "brand-new"), encoding="utf-8"
    )
    conn = rms.connect_memory_db(db)
    try:
        assert rms.memory_index_stale(conn, **pools) is True
    finally:
        conn.close()


def test_check_mode_reports_without_stamping(seeded):
    db, pools = seeded
    stats = rms.rebuild_memory_duckdb(db, write=False, **pools)
    assert stats["mode"] == "full"
    assert stats["total_units"] == 4
    assert stats["total_rows"] == 0  # nothing stored yet
    assert stats["index_stale"] is True
    assert stats["stale_new"]  # every source file reads as new
    conn = rms.connect_memory_db(db)
    try:
        # --check never resolves the model nor writes the stamp
        assert rms._stored_embed_model(conn) is None
    finally:
        conn.close()


def test_legacy_sqlite_path_still_works(tmp_path, pools, monkeypatch):
    """The rollback path: same pipeline through the legacy FTS5 store."""
    db = tmp_path / "memory_search.db"
    monkeypatch.setattr(rms, "MEMORY_DB", rms._MEMORY_SQLITE_DB)
    stats = rms.rebuild(db, embed_fn=_fake_embed, **pools)
    assert stats["total_rows"] == 4
    conn = rms.connect_memory_db(db)
    try:
        out = rms.search_memories(conn, "stgit patch", limit=3)
        assert out["results"], "legacy path must stay queryable"
        assert out["results"][0]["name"] == "user-workflow"
    finally:
        conn.close()
