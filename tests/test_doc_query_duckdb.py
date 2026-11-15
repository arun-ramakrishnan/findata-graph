#!/usr/bin/env python3
"""DuckDB-native doc_search sidecar (duckdb_sidecar_fold S1).

Exercises the engine-agnostic rebuild/query split introduced for the fold:
- ``rebuild_duckdb`` builds the `.duckdb` store (table + meta/info stamps
  + native FTS index over composed ``searchtext``);
- ``connect_doc_db`` routes `.duckdb` → DuckDB, everything else → SQLite;
- ``search_docs`` dispatches to the native ``match_bm25`` + Python-highlight
  path and returns the same dict contract as the FTS5 path;
- doc_index_ready/stale + stored_embed_model/dims work on the DuckDB store.

Hermetic: tmp doc tree + tmp `.duckdb` sidecar, fake embedder (same shape
as tests/test_doc_query.py fixtures).
"""

from __future__ import annotations

import pytest

from helpers.maintenance import rebuild_doc_search as rds

pytestmark = [pytest.mark.integration]

_GUIDE = (
    "# Repo Guide\n"
    "\n"
    "## Cache Design\n"
    "how the cache warms up and persists rows\n"
    "\n"
    "## Other\n"
    "unrelated beta prose\n"
)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    doc_root = tmp_path / "doc"
    doc_root.mkdir()
    (doc_root / "guide.md").write_text(_GUIDE, encoding="utf-8")
    db = tmp_path / "doc_search.duckdb"
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_DUCKDB_DB", db)
    return db


@pytest.fixture
def fake_local(monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        v = [0.0] * 8
        if "cache" in text.lower():
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
    c1 = rds.connect_doc_db(ddb)
    c2 = rds.connect_doc_db(sdb)
    try:
        import duckdb

        assert isinstance(c1, duckdb.DuckDBPyConnection)
        import sqlite3

        assert isinstance(c2, sqlite3.Connection)
    finally:
        c1.close()
        c2.close()


def test_rebuild_duckdb_builds_store_and_index(seeded, fake_local):
    stats = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    assert stats["total_files"] == 1
    assert stats["total_rows"] == 3
    assert stats["indexed"] == 3
    conn = rds.connect_doc_db(seeded)
    try:
        assert rds.doc_index_ready(conn) is True
        assert rds.doc_index_stale(conn, root=rds.DOC_ROOT) is False
        assert rds.stored_embed_dims(conn) == 8
        # native FTS index present (shadow schema created by create_fts_index)
        schemas = {
            r[0]
            for r in conn.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        }
        assert "fts_main_doc_search" in schemas
    finally:
        conn.close()


def test_search_hybrid_returns_expected_shape(seeded, fake_local):
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    conn = rds.connect_doc_db(seeded)
    try:
        out = rds.search_docs(conn, "cache design", limit=5, hybrid=True)
        assert out["mode"] == "hybrid"
        assert len(out["results"]) >= 1
        hit = out["results"][0]
        assert hit["path"] == "doc/guide.md"
        assert hit["title"] == "Repo Guide"
        assert "<mark>Cache</mark>" in hit["snippet"]
        assert hit["similarity"] is not None
    finally:
        conn.close()


def test_search_bm25_only_mode(seeded, fake_local):
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    conn = rds.connect_doc_db(seeded)
    try:
        out = rds.search_docs(conn, "beta prose", limit=5, hybrid=False)
        assert out["mode"] == "bm25"
        assert len(out["results"]) == 1
        assert out["results"][0]["similarity"] is None
    finally:
        conn.close()


def test_stale_detects_new_file(seeded, fake_local):
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    (rds.DOC_ROOT / "new.md").write_text("# New\n\n## Body\ntext\n")
    conn = rds.connect_doc_db(seeded)
    try:
        assert rds.doc_index_stale(conn, root=rds.DOC_ROOT) is True
    finally:
        conn.close()
