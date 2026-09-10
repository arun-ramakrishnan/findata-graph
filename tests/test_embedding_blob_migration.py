#!/usr/bin/env python3
"""embedding_blob_migration tests — codec, tolerant readers, writers, and
the one-shot migration on tmp fixtures (nothing live)."""

from __future__ import annotations

import json
import sqlite3


from helpers.core.vec_codec import dims_from_blob, load_vec, pack_f32, unpack_f32


VEC = [0.25, -1.5, 3.125, 0.0, 0.5]  # all exactly f32-representable


class TestVecCodec:
    def test_pack_unpack_roundtrip(self):
        assert unpack_f32(pack_f32(VEC)) == VEC

    def test_dims_from_blob(self):
        assert dims_from_blob(pack_f32(VEC)) == 5

    def test_load_vec_blob(self):
        assert load_vec(pack_f32(VEC)) == VEC

    def test_load_vec_text(self):
        assert load_vec(json.dumps(VEC)) == VEC

    def test_load_vec_none_and_garbage(self):
        assert load_vec(None) is None
        assert load_vec("") is None
        assert load_vec("not json") is None
        assert load_vec(3.14) is None


class TestWritersEmitBlob:
    def test_doc_search_helper_returns_bytes(self):
        from helpers.maintenance.rebuild_doc_search import _embedding_f32

        out = _embedding_f32(lambda t: VEC, "T", "S", "C")
        assert isinstance(out, bytes)
        assert unpack_f32(out) == VEC

    def test_doc_search_helper_none_on_failure(self):
        from helpers.maintenance.rebuild_doc_search import _embedding_f32

        def boom(_):
            raise RuntimeError("no embedder")

        assert _embedding_f32(boom, "T", "S", "C") is None

    def test_note_search_helper_returns_bytes(self):
        from helpers.maintenance.rebuild_note_search import _embedding_f32

        out = _embedding_f32(lambda t: VEC, "T", "Sec", "S", "C")
        assert isinstance(out, bytes)
        assert unpack_f32(out) == VEC


def _make_fts_db(path, table="note_search"):
    conn = sqlite3.connect(str(path))
    conn.execute(
        f"CREATE VIRTUAL TABLE {table} USING fts5(file_path, doc_type, title, embedding UNINDEXED)"
    )
    conn.executemany(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
        [
            ("a.md", "company", "A", json.dumps(VEC)),
            ("b.md", "company", "B", json.dumps(VEC)),
            ("c.md", "company", "C", None),
        ],
    )
    conn.commit()
    return conn


class TestMigration:
    def test_fts_inplace_pack_lossless_and_idempotent(self, tmp_path):
        from helpers.maintenance.migrate_embedding_blob import _migrate_table

        conn = _make_fts_db(tmp_path / "n.db")
        try:
            n = _migrate_table(conn, "note_search")
            assert n == 2  # NULL row untouched
            rows = conn.execute(
                "SELECT file_path, embedding FROM note_search ORDER BY file_path"
            ).fetchall()
            for fp, emb in rows:
                if emb is None:
                    assert fp == "c.md"
                else:
                    assert isinstance(emb, bytes)
                    assert unpack_f32(emb) == VEC
            assert _migrate_table(conn, "note_search") == 0  # idempotent
        finally:
            conn.close()

    def test_company_swap_new_ddl(self, tmp_path):
        from helpers.maintenance.migrate_embedding_blob import _swap_company_embeddings

        conn = sqlite3.connect(str(tmp_path / "c.db"))
        try:
            conn.execute(
                """
                CREATE TABLE company_embeddings (
                    company_name TEXT PRIMARY KEY,
                    embedding    FLOAT[384],
                    model        TEXT NOT NULL,
                    created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
                    CHECK (json_array_length(embedding) = 5)
                )
                """
            )
            conn.execute(
                "INSERT INTO company_embeddings (company_name, embedding, model) "
                "VALUES ('Alpha', ?, 'm')",
                (json.dumps(VEC),),
            )
            conn.commit()
            n = _swap_company_embeddings(conn)
            assert n == 1
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='company_embeddings'"
            ).fetchone()[0]
            assert "json_array_length" not in ddl
            assert "BLOB" in ddl
            emb = conn.execute(
                "SELECT embedding FROM company_embeddings WHERE company_name='Alpha'"
            ).fetchone()[0]
            assert isinstance(emb, bytes)
            assert unpack_f32(emb) == VEC
        finally:
            conn.close()
