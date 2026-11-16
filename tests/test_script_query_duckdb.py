#!/usr/bin/env python3
"""DuckDB-native script_search sidecar (duckdb_sidecar_fold S3).

Exercises the engine-agnostic rebuild/query split introduced for the fold:
- ``rebuild_script_duckdb`` builds the `.duckdb` store (table + meta/info
  stamps + native FTS index over composed ``searchtext`` + the mojo/ts
  content-addressed doc cache tables);
- ``connect_script_db`` routes `.duckdb` → DuckDB, everything else → SQLite;
- ``search_scripts`` dispatches to the native ``match_bm25`` +
  Python-highlight path and returns the same dict contract as the FTS5
  path, with the kind/area axis as plain SQL predicates;
- script_index_ready/stale + stored model/dims work on the DuckDB store.

Hermetic: tmp helpers/tests/Makefile corpus + tmp `.duckdb` sidecar,
fake embedder (same shape as tests/test_rebuild_script_search.py fixtures).
Mojo/TS roots derive from the helpers root's parent — the tmp tree has
none, so nothing is swept from the live repo.
"""

from __future__ import annotations

import pytest

from helpers.maintenance import rebuild_script_search as rss

from helpers.core.duckdb_shared import fts_topk

pytestmark = [pytest.mark.integration]

_HELPERS_PY = (
    "def pack_f32(vec):\n"
    "    return b''\n"
    "\n"
    "def load_vec(blob):\n"
    "    return [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]\n"
)
_TESTS_PY = "def test_pack_roundtrip():\n    assert True\n"
_MAKEFILE = "qa: ## Gate: full QA battery (ruff, md-lint, types, pytest)\n\t@echo ok\n"


@pytest.fixture
def corpus(tmp_path):
    """Tmp repo-shaped corpus: helpers/, tests/, Makefile (no Mojo/frontend
    trees — the tmp base has none, so nothing live is swept)."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "pack.py").write_text(_HELPERS_PY, encoding="utf-8")
    (helpers / "query.py").write_text(_HELPERS_PY, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_pack.py").write_text(_TESTS_PY, encoding="utf-8")
    (tests / "test_query.py").write_text(_TESTS_PY, encoding="utf-8")
    (tmp_path / "Makefile").write_text(_MAKEFILE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def seeded(tmp_path, corpus, monkeypatch):
    monkeypatch.setattr(rss, "HELPERS_ROOT", corpus / "helpers")
    monkeypatch.setattr(rss, "TESTS_ROOT", corpus / "tests")
    monkeypatch.setattr(rss, "APP_PY", corpus / "app.py")
    monkeypatch.setattr(rss, "MAKEFILE", corpus / "Makefile")
    db = tmp_path / "script_search.duckdb"
    monkeypatch.setattr(rss, "SCRIPT_DB", db)
    monkeypatch.setattr(rss, "SCRIPT_DUCKDB_DB", db)
    return db


@pytest.fixture
def fake_local(monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        v = [0.0] * 8
        if "dup" in text.lower():
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
    c1 = rss.connect_script_db(ddb)
    c2 = rss.connect_script_db(sdb)
    try:
        import duckdb
        import sqlite3

        assert isinstance(c1, duckdb.DuckDBPyConnection)
        assert isinstance(c2, sqlite3.Connection)
    finally:
        c1.close()
        c2.close()


def test_rebuild_duckdb_builds_store_and_index(seeded):
    db = seeded
    stats = rss.rebuild_script_duckdb(db, embed_fn=_fake_embed_8)
    assert stats["total_units"] == 5  # 2 helpers + 2 tests + Makefile
    assert stats["total_rows"] == 6  # Makefile unit yields 2 rows (2 targets)
    assert stats["indexed"] == 6
    assert stats["embedded"] == 6
    assert stats["by_kind"]["script"] == 2
    assert stats["by_kind"]["test"] == 2
    assert stats["by_kind"]["make"] == 2
    conn = rss.connect_script_db(db)
    try:
        assert rss.script_index_ready(conn) is True
        assert rss.script_index_stale(conn) is False
        assert rss._script_stored_embed_dims(conn) == 8
        schemas = {
            r[0]
            for r in conn.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        }
        assert "fts_main_script_search" in schemas
        # the mojo/ts content-addressed cache tables exist (empty here)
        assert conn.execute("SELECT COUNT(*) FROM script_search_mojo_doc").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM script_search_ts_doc").fetchone()[0] == 0
    finally:
        conn.close()


def test_search_hybrid_returns_expected_shape(seeded, fake_local):
    db = seeded
    rss.rebuild_script_duckdb(db, embed_fn=fake_local.embed_document)
    conn = rss.connect_script_db(db)
    try:
        out = rss.search_scripts(conn, "dup pack", limit=3)
        assert out["mode"] == "hybrid"
        assert out["results"], "query 'dup pack' must match at least one row"
        hit = out["results"][0]
        assert hit["path"].endswith("helpers/pack.py") or hit["kind"] == "script"
        assert set(hit) == {
            "path",
            "title",
            "kind",
            "area",
            "purpose",
            "snippet",
            "score",
            "similarity",
        }
    finally:
        conn.close()


def test_search_bm25_only_mode(seeded, fake_local):
    db = seeded
    rss.rebuild_script_duckdb(db, embed_fn=fake_local.embed_document)
    conn = rss.connect_script_db(db)
    try:
        out = rss.search_scripts(conn, "make", limit=3, hybrid=False)
        assert out["mode"] == "bm25"
        assert out["results"], "lexical 'make' must match the Makefile row"
        assert all(r["similarity"] is None for r in out["results"])
        assert any(r["kind"] == "make" for r in out["results"])
    finally:
        conn.close()


def test_kind_and_area_filters(seeded, fake_local):
    db = seeded
    rss.rebuild_script_duckdb(db, embed_fn=fake_local.embed_document)
    conn = rss.connect_script_db(db)
    try:
        tests_only = rss.search_scripts(conn, "dup", limit=5, kind="test")
        assert {r["kind"] for r in tests_only["results"]} == {"test"}
        # area for helpers subdirs is the first path segment ("helpers")
        area_filtered = rss.search_scripts(conn, "dup", limit=5, area="helpers")
        assert {r["area"] for r in area_filtered["results"]} == {"helpers"}
    finally:
        conn.close()


def test_stale_detects_new_unit(seeded):
    db = seeded
    rss.rebuild_script_duckdb(db, embed_fn=_fake_embed_8)
    (db.parent / "helpers" / "new.py").write_text(_HELPERS_PY, encoding="utf-8")
    conn = rss.connect_script_db(db)
    try:
        assert rss.script_index_stale(conn) is True
    finally:
        conn.close()


def test_check_mode_reports_without_stamping(seeded):
    db = seeded
    stats = rss.rebuild_script_duckdb(db, write=False)
    assert stats["total_units"] == 5
    assert stats["total_rows"] == 0  # nothing stored yet
    assert stats["index_stale"] is True
    assert stats["stale_new"]  # every source file reads as new
    conn = rss.connect_script_db(db)
    try:
        # --check never resolves the model nor writes the stamp
        assert rss._script_stored_embed_model(conn) is None
    finally:
        conn.close()


def test_legacy_sqlite_path_still_works(tmp_path, corpus, monkeypatch):
    """The rollback path: same pipeline through the legacy FTS5 store."""
    monkeypatch.setattr(rss, "HELPERS_ROOT", corpus / "helpers")
    monkeypatch.setattr(rss, "TESTS_ROOT", corpus / "tests")
    monkeypatch.setattr(rss, "APP_PY", corpus / "app.py")
    monkeypatch.setattr(rss, "MAKEFILE", corpus / "Makefile")
    db = tmp_path / "script_search.db"
    monkeypatch.setattr(rss, "SCRIPT_DB", db)
    stats = rss.rebuild(db, embed_fn=_fake_embed_8)
    assert stats["total_rows"] == 6  # 4 py units + Makefile (2 target rows)
    conn = rss.connect_script_db(db)
    try:
        out = rss.search_scripts(conn, "make", limit=3)
        assert out["results"], "legacy path must stay queryable"
    finally:
        conn.close()


def _fake_embed_8(text: str) -> list[float]:
    """8-dim fake embedder (short vectors keep the store tiny)."""
    return [0.5] * 8


def test_topk_path_with_kind_filter(seeded, fake_local):
    """SQL top-k path (resolvable term, exact dict stem) + kind filter."""
    db = seeded
    rss.rebuild_script_duckdb(db, embed_fn=fake_local.embed_document)
    conn = rss.connect_script_db(db)
    try:
        rows, mode = fts_topk(
            conn,
            fts_table="fts_main_script_search",
            base_table="script_search",
            tokens=["pack"],
            limit=10,
        )
        assert mode == "topk", "expected SQL top-k path, got macro fallback"
        assert rows, "topk returned no rows for resolvable term"
        out = rss.search_scripts(conn, "pack", limit=5, kind="script")
        assert out["results"], "topk path returned nothing for resolvable term"
        assert {r["kind"] for r in out["results"]} == {"script"}
        assert {r["path"] for r in out["results"]} == {"helpers/pack.py", "helpers/query.py"}
    finally:
        conn.close()
