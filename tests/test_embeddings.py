# Tests for helpers/graph/embeddings.py
"""Unit tests for embedding generation, schema management, and stats."""

import sqlite3
import math
import pytest
from helpers.graph.embeddings import (
    _pseudo_embedding,
    _ensure_schema,
    _get_company_text,
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
        _ensure_schema(conn, 64)
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_embeddings'"
        ).fetchone()
        assert r is not None

    def test_creates_index(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        _ensure_schema(conn, 64)
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_emb_company'"
        ).fetchone()
        assert r is not None

    def test_idempotent(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        _ensure_schema(conn, 64)
        _ensure_schema(conn, 64)  # second call should be no-op
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
        note_path.write_text("---\ntitle: TestCo\ntype: company\n---\n\n# TestCo\n\nGreat company.")
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
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=64)
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
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=64)
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
        conn.execute("INSERT INTO entities VALUES ('TestCo', 'company', '', 'Tech')")
        conn.commit()
        n = populate_dry_run(conn, dims=64, company="TestCo")
        assert n == 1
        row = conn.execute("SELECT company_name, model FROM company_embeddings").fetchone()
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
        conn.execute("INSERT INTO entities VALUES ('TestCo', 'company', '', 'Tech')")
        conn.commit()
        populate_dry_run(conn, dims=32, company="TestCo")
        populate_dry_run(conn, dims=32, company="TestCo")  # replace
        rows = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0]
        assert rows == 1  # OR REPLACE, not INSERT


# ---------------------------------------------------------------------------
# local_embeddings (2026-08-20): populate_local + clear-then-populate guard
# ---------------------------------------------------------------------------
from helpers.graph.embeddings import populate_local  # noqa: E402


def _add_entities(conn):
    conn.execute("""
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT,
            file_path TEXT,
            sector_classification TEXT
        )
    """)
    conn.execute("INSERT INTO entities VALUES ('TestCo', 'company', '', 'Tech')")
    conn.execute("INSERT INTO entities VALUES ('Sector', 'sector', '', '')")
    conn.commit()


class TestPopulateLocal:
    def test_inserts_model_id_rows(self, tmp_path, monkeypatch):
        import ast

        from helpers.core import local_embedder as LE

        conn, _ = _make_embed_db(tmp_path)
        _add_entities(conn)
        monkeypatch.setattr(LE, "available", lambda: True)
        monkeypatch.setattr(
            LE,
            "embed_documents",
            lambda texts: [[0.25] * LE.DIM for _ in texts],
        )
        n = populate_local(conn)
        assert n == 1  # companies only, not sectors
        row = conn.execute("SELECT model, embedding FROM company_embeddings").fetchone()
        assert row[0] == LE.MODEL_ID
        assert len(ast.literal_eval(row[1])) == LE.DIM

    def test_unavailable_exits_with_setup_hint(self, tmp_path):
        # conftest pins available() -> False: the CLI must fail loudly with
        # the setup pointer, not silently write pseudo vectors.
        conn, _ = _make_embed_db(tmp_path)
        _add_entities(conn)
        with pytest.raises(SystemExit, match="local_embedder"):
            populate_local(conn)


class TestModelPurityGuard:
    def test_populate_local_blocked_by_foreign_rows(self, tmp_path, monkeypatch):
        from helpers.core import local_embedder as LE

        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=384)
        vec = "[" + ", ".join("0.5" for _ in range(384)) + "]"
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) "
            "VALUES ('X', ?, 'dry-run-v64')",
            (vec,),
        )
        conn.commit()
        monkeypatch.setattr(LE, "available", lambda: True)
        with pytest.raises(SystemExit, match="--clear"):
            populate_local(conn)

    def test_populate_dry_run_blocked_by_local_rows(self, tmp_path):
        from helpers.graph.embeddings import _ensure_schema

        conn, _ = _make_embed_db(tmp_path)
        _ensure_schema(conn, 64)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) "
            "VALUES ('X', ?, 'bge-small-en-v1.5')",
            ("[" + ", ".join("0.5" for _ in range(64)) + "]",),
        )
        conn.commit()
        with pytest.raises(SystemExit, match="--clear"):
            populate_dry_run(conn, dims=64)

    def test_same_model_rerun_allowed(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path)
        _add_entities(conn)
        populate_dry_run(conn, dims=64)
        populate_dry_run(conn, dims=64)  # same label: no guard trip
        assert conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0] == 1


class TestStatsMixedWarning:
    def test_mixed_models_flagged(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=2)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) "
            "VALUES ('A', '[0.1, 0.2]', 'model_a')"
        )
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) "
            "VALUES ('B', '[0.3, 0.4]', 'bge-small-en-v1.5')"
        )
        conn.commit()
        s = stats(conn)
        assert "warning" in s
        assert "mixed" in s["warning"]

    def test_single_model_no_warning(self, tmp_path):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=2)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) "
            "VALUES ('A', '[0.1, 0.2]', 'bge-small-en-v1.5')"
        )
        conn.commit()
        assert "warning" not in stats(conn)


# ---------------------------------------------------------------------------
# company_embeddings_maint (2026-08-21): cached populate + GC + --maint gate
# ---------------------------------------------------------------------------
from helpers.graph.embeddings import maint_refresh  # noqa: E402


def _fake_local(monkeypatch):
    """Pin the local embedder as available with a recording batch embedder.

    Returns the call log (one entry per embed_documents invocation, each the
    list of texts that invocation embedded)."""
    from helpers.core import local_embedder as LE

    calls: list[list[str]] = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.25] * LE.DIM for _ in texts]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_documents", fake_embed)
    return calls


def _add_two_companies(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT,
            file_path TEXT,
            sector_classification TEXT
        )
    """)
    conn.execute("INSERT INTO entities VALUES ('CoA', 'company', '', 'Tech')")
    conn.execute("INSERT INTO entities VALUES ('CoB', 'company', '', 'Energy')")
    conn.commit()


class TestPopulateLocalCached:
    def test_cold_seeds_cache_then_warm_hits_without_embedding(self, tmp_path, monkeypatch, capsys):
        from helpers.core import local_embedder as LE
        from helpers.core.vec_search import _attach_vec_db

        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        calls = _fake_local(monkeypatch)

        n1 = populate_local(conn)
        assert n1 == 2
        assert len(calls) == 1 and len(calls[0]) == 2  # one batch, both texts
        assert "0 hits, 2 misses" in capsys.readouterr().err

        _attach_vec_db(conn)  # idempotent: populate already attached it
        seeded = conn.execute(
            "SELECT COUNT(*) FROM vecdb.embed_cache WHERE model = ?",
            (LE.MODEL_ID,),
        ).fetchone()[0]
        assert seeded == 2

        n2 = populate_local(conn)
        assert n2 == 0  # stable write: identical vectors wrote nothing
        assert len(calls) == 1  # warm: NO new embed call at all
        assert "2 hits, 0 misses" in capsys.readouterr().err

    def test_warm_rerun_preserves_created_at(self, tmp_path, monkeypatch):
        """maint_full_zero_churn F2: a no-op cycle must not restamp
        created_at (previously INSERT OR REPLACE re-defaulted every row)."""
        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        _fake_local(monkeypatch)

        populate_local(conn)
        before = conn.execute(
            "SELECT company_name, created_at FROM company_embeddings ORDER BY company_name"
        ).fetchall()

        populate_local(conn)

        after = conn.execute(
            "SELECT company_name, created_at FROM company_embeddings ORDER BY company_name"
        ).fetchall()
        assert after == before

    def test_changed_text_reembeds_exactly_that_company(self, tmp_path, monkeypatch):
        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        calls = _fake_local(monkeypatch)

        populate_local(conn)
        # CoA's text basis changes (sector feeds _get_company_text).
        conn.execute("UPDATE entities SET sector_classification = 'Banking' WHERE name = 'CoA'")
        conn.commit()

        populate_local(conn)
        assert len(calls) == 2
        assert len(calls[1]) == 1  # only CoA re-embedded
        assert "Banking" in calls[1][0]

    def test_gcs_rows_of_deleted_companies(self, tmp_path, monkeypatch, capsys):
        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        _fake_local(monkeypatch)

        populate_local(conn)
        assert conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0] == 2

        conn.execute("DELETE FROM entities WHERE name = 'CoB'")
        conn.commit()
        capsys.readouterr()  # clear

        n = populate_local(conn)
        assert n == 0  # stable write: CoA's identical vector wrote nothing; only the GC removed CoB
        remaining = [
            r[0] for r in conn.execute("SELECT company_name FROM company_embeddings").fetchall()
        ]
        assert remaining == ["CoA"]
        assert "gc: removed 1" in capsys.readouterr().err


class TestMaintRefresh:
    def test_unavailable_warns_and_writes_nothing(self, tmp_path, capsys):
        # conftest's autouse pin leaves the embedder unavailable here.
        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)

        rc = maint_refresh(conn)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING" in err and "unavailable" in err
        # No table was created — the gate must not even build schema.
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_embeddings'"
        ).fetchone()
        assert r is None

    def test_empty_table_warns_apply_first(self, tmp_path, monkeypatch, capsys):
        from helpers.core import local_embedder as LE

        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        _ensure_schema(conn, LE.DIM)  # table exists, zero rows
        _fake_local(monkeypatch)

        rc = maint_refresh(conn)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING" in err and "empty" in err and "apply" in err
        assert (
            conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()[0] == 0
        )  # never auto-populates

    def test_pseudo_table_never_auto_upgrades(self, tmp_path, monkeypatch, capsys):
        conn, _ = _make_embed_db(tmp_path, with_table=True, existing_dims=384)
        conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model) "
            "VALUES ('X', ?, 'dry-run-v64')",
            ("[" + ", ".join("0.5" for _ in range(384)) + "]",),
        )
        conn.commit()
        _fake_local(monkeypatch)

        rc = maint_refresh(conn)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING" in err and "--clear" in err
        row = conn.execute("SELECT model FROM company_embeddings").fetchone()
        assert row[0] == "dry-run-v64"  # untouched

    def test_applied_table_refreshes_warm(self, tmp_path, monkeypatch, capsys):
        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        calls = _fake_local(monkeypatch)

        populate_local(conn)  # the user-held apply equivalent (seeds cache)
        capsys.readouterr()  # clear

        rc = maint_refresh(conn)
        assert rc == 0
        err = capsys.readouterr().err
        assert "refreshed 0 row(s)" in err  # stable write: no-op cycle reports zero work
        assert "2 hits, 0 misses" in err  # warm: served by the seeded cache
        assert len(calls) == 1  # no new embedding happened

    def test_cli_maint_wiring(self, tmp_path, monkeypatch):
        import helpers.graph.embeddings as emb

        conn, db = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        monkeypatch.setattr(emb, "DEFAULT_DB_PATH", db)
        monkeypatch.setattr(emb, "db_connect", lambda _p: conn)

        assert emb.main(["--maint"]) == 0  # unavailable gate -> WARNING + 0

    def test_populate_bumps_generation_only_on_change(self, tmp_path, monkeypatch):
        """B4 (sql_capability_unlocks) + maint_full_zero_churn F2: the bump
        fires only when a row ACTUALLY changed (the upsert filters identical
        vectors) or GC removed rows — not on cache misses whose re-embed
        reproduces the stored bytes. Tolerates bare fixtures without db_meta
        (no consumer to invalidate there)."""
        from helpers.core import local_embedder as LE
        from helpers.core.db import get_generation

        conn, _ = _make_embed_db(tmp_path)
        _add_two_companies(conn)
        _fake_local(monkeypatch)
        # Bare fixture: no db_meta -> bump is a no-op, populate still works.
        populate_local(conn)
        assert get_generation(conn) is None

        # With db_meta present: a genuinely changed row (stored vector
        # corrupted; the re-embed reproduces different bytes) bumps.
        conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO db_meta VALUES ('generation', '100')")
        conn.execute(
            "UPDATE company_embeddings SET embedding = ? WHERE company_name = 'CoA'",
            ("[" + ", ".join("0.0" for _ in range(LE.DIM)) + "]",),
        )
        conn.commit()
        populate_local(conn)
        assert get_generation(conn) == 101

        # Warm re-populate (all hits, identical vectors, no GC) leaves the
        # generation alone.
        populate_local(conn)
        assert get_generation(conn) == 101
