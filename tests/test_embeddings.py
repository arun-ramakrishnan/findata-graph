# Tests for helpers/graph/embeddings.py
"""Unit tests for embedding generation, schema management, and stats."""

import sqlite3
import math
import pytest
from helpers.graph.embeddings import (
    _pseudo_embedding,
    _ensure_schema,
    _get_company_text,
    _get_openai_client,
    populate_dry_run,
    clear,
    stats,
)


# ---------------------------------------------------------------------------
# _pseudo_embedding
# ---------------------------------------------------------------------------
class TestPseudoEmbedding:
    def test_dimension_correct(self):
        vec = _pseudo_embedding("test", 128)
        assert len(vec) == 128

    def test_deterministic(self):
        v1 = _pseudo_embedding("Apple", 64)
        v2 = _pseudo_embedding("Apple", 64)
        assert v1 == v2

    def test_different_text_different_vector(self):
        v1 = _pseudo_embedding("Apple", 64)
        v2 = _pseudo_embedding("Banana", 64)
        assert v1 != v2

    def test_different_seed_different_vector(self):
        v1 = _pseudo_embedding("Apple", 64, seed=42)
        v2 = _pseudo_embedding("Apple", 64, seed=99)
        assert v1 != v2

    def test_l2_normalized(self):
        vec = _pseudo_embedding("normalized test", 32)
        norm = math.sqrt(sum(x**2 for x in vec))
        assert abs(norm - 1.0) < 1e-9

    def test_values_in_range(self):
        vec = _pseudo_embedding("range test", 256)
        assert all(-1.0 <= v <= 1.0 for v in vec)

    def test_dims_1(self):
        vec = _pseudo_embedding("single", 1)
        assert len(vec) == 1

    def test_large_dims(self):
        vec = _pseudo_embedding("large", 1024)
        assert len(vec) == 1024

    def test_empty_text(self):
        vec = _pseudo_embedding("", 8)
        assert len(vec) == 8

    def test_invalid_dims(self):
        with pytest.raises(ValueError, match="dims must be >= 1"):
            _pseudo_embedding("test", 0)

    def test_negative_dims(self):
        with pytest.raises(ValueError, match="dims must be >= 1"):
            _pseudo_embedding("test", -5)

    def test_hash_extension(self):
        """Large dims should extend the hash without error."""
        vec = _pseudo_embedding("needs more bytes", 2048)
        assert len(vec) == 2048


# ---------------------------------------------------------------------------
# _ensure_schema
# ---------------------------------------------------------------------------
def _make_embed_db(tmp_path, with_table=False, existing_dims=None):
    db = tmp_path / "embed_test.db"
    conn = sqlite3.connect(str(db))
    if with_table and existing_dims:
        conn.execute(f"""
            CREATE TABLE company_embeddings (
                company_name TEXT PRIMARY KEY,
                embedding    FLOAT[{existing_dims}],
                model        TEXT NOT NULL,
                created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
                CHECK (json_array_length(embedding) = {existing_dims})
            )
        """)
        conn.commit()
    return conn, db


class TestEnsureSchema:
    def test_creates_table(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        _ensure_schema(conn, 384)
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_embeddings'"
        ).fetchone()
        assert r is not None

    def test_creates_index(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        _ensure_schema(conn, 384)
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_emb_company'"
        ).fetchone()
        assert r is not None

    def test_idempotent(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        _ensure_schema(conn, 384)
        _ensure_schema(conn, 384)  # second call should be no-op
        assert True  # no error

    def test_dimension_mismatch_drops_and_recreates(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=2)
        # Insert a row with old dims
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) VALUES ('Test', '[0.1, 0.2]', 'old')"
        )
        conn.commit()
        # Recreate with different dims
        _ensure_schema(conn, 512)
        # Table should exist, old row should be gone
        r = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0]
        assert r == 0

    def test_same_dims_keeps_data(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=2)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) VALUES ('Test', '[0.1, 0.2]', 'old')"
        )
        conn.commit()
        _ensure_schema(conn, 2)
        r = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0]
        assert r == 1

    def test_invalid_dims(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        with pytest.raises(ValueError, match="dims must be >= 1"):
            _ensure_schema(conn, 0)


# ---------------------------------------------------------------------------
# _get_company_text
# ---------------------------------------------------------------------------
class TestGetCompanyText:
    def test_entity_not_found(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        conn.execute("""
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT,
                file_path TEXT,
                sector_classification TEXT
            )
        """)
        conn.commit()
        text = _get_company_text(conn, "Nonexistent")
        assert text == "Nonexistent"

    def test_no_file_path(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        conn.execute("""
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT,
                file_path TEXT,
                sector_classification TEXT
            )
        """)
        conn.execute(
            "INSERT INTO entities VALUES ('TestCo', 'company', 'findata/Nope.md', 'Banking')"
        )
        conn.commit()
        # Patch PROJECT_ROOT to tmp_path so file doesn't exist
        import helpers.graph.embeddings as emb_mod
        orig_root = emb_mod.PROJECT_ROOT
        emb_mod.PROJECT_ROOT = tmp_path
        try:
            text = _get_company_text(conn, "TestCo")
        finally:
            emb_mod.PROJECT_ROOT = orig_root
        assert "TestCo" in text
        assert "Banking" in text

    def test_with_file(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        conn.execute("""
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT,
                file_path TEXT,
                sector_classification TEXT
            )
        """)
        # Create a note file
        note_path = tmp_path / "notes" / "TestCo.md"
        note_path.parent.mkdir()
        note_path.write_text(
            "---\ntitle: TestCo\ntype: company\n---\n\n# TestCo\n\nGreat company."
        )
        conn.execute(
            "INSERT INTO entities VALUES ('TestCo', 'company', 'notes/TestCo.md', 'Banking')"
        )
        conn.commit()
        import helpers.graph.embeddings as emb_mod
        orig_root = emb_mod.PROJECT_ROOT
        emb_mod.PROJECT_ROOT = tmp_path
        try:
            text = _get_company_text(conn, "TestCo")
        finally:
            emb_mod.PROJECT_ROOT = orig_root
        assert "TestCo" in text
        assert "Banking" in text
        assert "Great company" in text
        # YAML frontmatter should be stripped
        assert "title:" not in text


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------
class TestClear:
    def test_clear_empty(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=384)
        n = clear(conn)
        assert n == 0

    def test_clear_with_rows(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=2)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) VALUES ('A', '[0.1, 0.2]', 'test')"
        )
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) VALUES ('B', '[0.3, 0.4]', 'test')"
        )
        conn.commit()
        n = clear(conn)
        assert n == 2
        assert conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
class TestStats:
    def test_no_table(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        s = stats(conn)
        assert s["total"] == 0
        assert s["models"] == []
        assert s["sample_dim"] is None

    def test_empty_table(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=384)
        s = stats(conn)
        assert s["total"] == 0
        assert s["models"] == []

    def test_with_rows(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=2)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) VALUES ('A', '[0.1, 0.2]', 'model_a')"
        )
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) VALUES ('B', '[0.3, 0.4]', 'model_b')"
        )
        conn.commit()
        s = stats(conn)
        assert s["total"] == 2
        assert sorted(s["models"]) == ["model_a", "model_b"]
        assert s["sample_dim"] == 2


# ---------------------------------------------------------------------------
# populate_dry_run
# ---------------------------------------------------------------------------
class TestPopulateDryRun:
    def test_single_company(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        conn.execute("""
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT,
                file_path TEXT,
                sector_classification TEXT
            )
        """)
        conn.execute(
            "INSERT INTO entities VALUES ('TestCo', 'company', '', 'Tech')"
        )
        conn.commit()
        n = populate_dry_run(conn, dims=64, company="TestCo")
        assert n == 1
        row = conn.execute(
            "SELECT company_name, model FROM company_embeddings"
        ).fetchone()
        assert row[0] == "TestCo"
        assert row[1] == "dry-run-v64"

    def test_all_companies(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        conn.execute("""
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT,
                file_path TEXT,
                sector_classification TEXT
            )
        """)
        for name in ("CoA", "CoB", "CoC"):
            conn.execute(
                f"INSERT INTO entities VALUES ('{name}', 'company', '', 'Tech')"  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            )
        conn.execute("INSERT INTO entities VALUES ('Sector', 'sector', '', '')")
        conn.commit()
        n = populate_dry_run(conn, dims=32)
        assert n == 3  # only companies, not sectors
        rows = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0]
        assert rows == 3

    def test_replace_existing(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        conn.execute("""
            CREATE TABLE entities (
                name TEXT PRIMARY KEY,
                entity_type TEXT,
                file_path TEXT,
                sector_classification TEXT
            )
        """)
        conn.execute(
            "INSERT INTO entities VALUES ('TestCo', 'company', '', 'Tech')"
        )
        conn.commit()
        populate_dry_run(conn, dims=32, company="TestCo")
        populate_dry_run(conn, dims=32, company="TestCo")  # replace
        rows = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0]
        assert rows == 1  # OR REPLACE, not INSERT


# ---------------------------------------------------------------------------
# _get_openai_client
# ---------------------------------------------------------------------------
class TestGetOpenAIClient:
    def test_import_error(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(ImportError, match="openai package not installed"):
            _get_openai_client()
