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


def test_rebuild_builds_doc_vectors_mirror(seeded, fake_local):
    """S6: the rebuild writes a colocated doc_vectors mirror so the hybrid
    cosine leg can run vectorized (same rows as doc_search's embedded set).
    Index-free since 2026-11-22 (search_lane_hnsw_rollout S0): the cosine
    leg scores the whole mirror for fusion ranks, so no HNSW index can
    route — pin the absence so the decorative index stays gone."""
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    conn = rds.connect_doc_db(seeded)
    try:
        n_vec = conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]
        n_emb = conn.execute(
            "SELECT COUNT(*) FROM doc_search WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        assert n_vec == n_emb == 3
        dims = conn.execute("SELECT embedding FROM doc_vectors LIMIT 1").fetchone()[0]
        assert len(dims) == 8
        idx = conn.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name='doc_vectors'"
        ).fetchall()
        assert idx == [], idx
    finally:
        conn.close()


def test_cosine_leg_uses_mirror_and_falls_back(seeded, fake_local):
    """S6: the SQL mirror path and the Python scan agree on the ranking; a
    pre-S6 store (no mirror) still answers via the fallback."""
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    conn = rds.connect_doc_db(seeded)
    try:
        qv = fake_local.embed_query("cache design")
        scored_sql, sims_sql, rank_sql = rds._cosine_leg_duckdb(conn, qv)
        assert rank_sql is not None
        assert len(scored_sql) == 3
        # Python path (fallback) rank — recompute by dropping the mirror
        conn.execute("DROP TABLE doc_vectors")
        scored_py, sims_py, rank_py = rds._cosine_leg_duckdb(conn, qv)
        assert rank_py is not None
        assert [rid for rid, _ in scored_py] == [rid for rid, _ in scored_sql]
        for rid, sim in scored_sql:
            assert abs(sim - sims_py.get(rid, -2.0)) < 1e-9
    finally:
        conn.close()


# --- duckdb_rebuild_incremental_carry S1/S5: carry + delta + shadow --------


def test_incremental_noop_carries(seeded, fake_local):
    first = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    assert first["mode"] == "full"

    calls: list[str] = []
    real = fake_local.embed_document

    def counting(text: str) -> list[float]:
        calls.append(text)
        return real(text)

    def _ids():
        conn = rds.connect_doc_db(seeded)
        try:
            return {r[0] for r in conn.execute("SELECT id FROM doc_search").fetchall()}
        finally:
            conn.close()

    ids_before = _ids()
    second = rds.rebuild_duckdb(seeded, embed_fn=counting)
    assert second["mode"] == "incremental"
    assert second["carried_files"] == 1
    assert second["changed_files"] == 0
    assert second["deleted_files"] == 0
    assert calls == []  # mtime fast path (or hash fallback): zero embed calls
    assert _ids() == ids_before  # carried rows keep byte-identical ids
    assert second["total_rows"] == first["total_rows"]


def test_incremental_delta_new_file_visible_in_fts(seeded, fake_local):
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    (rds.DOC_ROOT / "extra.md").write_text(
        "# Extra\n\n## Zebra Section\n\nunique zeta token prose\n", encoding="utf-8"
    )
    stats = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    assert stats["mode"] == "incremental"
    assert stats["carried_files"] == 1
    assert stats["changed_files"] == 1
    assert stats["total_rows"] == 5  # 3 carried + preamble + zebra section

    conn = rds.connect_doc_db(seeded)
    try:
        new_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM doc_search WHERE file_path LIKE '%extra.md'"
            ).fetchall()
        ]
        assert len(new_ids) == 2
        # S3 law: the one-shot FTS shadow must SEE the delta - the new
        # rows are indexed under their base-table ids (docs.name).
        for sid in new_ids:
            seen = conn.execute(
                "SELECT COUNT(*) FROM fts_main_doc_search.docs WHERE name = ?", (sid,)
            ).fetchone()[0]
            assert seen == 1, f"row {sid} missing from the FTS shadow"
        n_docs = conn.execute("SELECT COUNT(*) FROM fts_main_doc_search.docs").fetchone()[0]
        assert n_docs == stats["total_rows"]
    finally:
        conn.close()


def test_incremental_deleted_file(seeded, fake_local):
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    (rds.DOC_ROOT / "guide.md").unlink()
    stats = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    assert stats["mode"] == "incremental"
    assert stats["deleted_files"] == 1
    assert stats["total_rows"] == 0

    conn = rds.connect_doc_db(seeded)
    try:
        assert conn.execute("SELECT COUNT(*) FROM fts_main_doc_search.docs").fetchone()[0] == 0
    finally:
        conn.close()


def test_incremental_mtime_drift_same_hash_carries(seeded, fake_local):
    import os

    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    p = rds.DOC_ROOT / "guide.md"
    p.write_text(_GUIDE, encoding="utf-8")  # rewrite: new inode mtime, same content
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5.0))
    stats = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    assert stats["mode"] == "incremental"
    assert stats["carried_files"] == 1  # hash-verified carry, no re-embed
    assert stats["changed_files"] == 0
    # meta mtime healed by the apply -> the next no-op takes the fast path
    again = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    assert again["carried_files"] == 1 and again["changed_files"] == 0


def test_forced_full_still_works(seeded, fake_local):
    rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document)
    (rds.DOC_ROOT / "extra.md").write_text("# Extra\n\n## New\n\nprose\n", encoding="utf-8")
    stats = rds.rebuild_duckdb(seeded, embed_fn=fake_local.embed_document, incremental=False)
    assert stats["mode"] == "full"
    assert stats["total_rows"] == 5
    assert stats["carried_files"] == 0
