"""
Tests for helpers/core/vec_search.py — the sqlite-vec KNN mirror table (A1).

Covers the three entry points' contracts:
- vec_available gating (extension missing -> False, never raises)
- knn_similarities: correctness vs a float64 cosine ground truth, k > rows,
  dims mismatch, lazy backfill from the FTS JSON column
- sync_vec_table: full refresh, incremental upsert/delete, None-embedding
  rows drop their vec entry, malformed JSON skipped

These tests seed a real FTS5 note_search-shaped table (mirroring
test_api_search._SCHEMA) so the backfill path reads production shape.
"""

import json
import math
import sqlite3
import struct

import pytest

from helpers.core import vec_search as VS

DIMS = 4


def _pack(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@pytest.fixture()
def fts_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE VIRTUAL TABLE note_search USING fts5("
        "doc_type, file_path UNINDEXED, title, sector, content, "
        "embedding UNINDEXED, section_title, anchor UNINDEXED, "
        "tokenize='porter unicode61')"
    )
    return conn


def k(fp: str) -> str:
    """Composite row key for a seeded doc (anchor '1')."""
    return f"{fp}#1"


def _seed_fts(conn, docs):
    """docs: list of (file_path, vector | None) — one section row each
    (anchor '1'), so vec keys are the composite {file_path}#1."""
    for fp, vec in docs:
        emb = json.dumps(vec) if vec is not None else None
        conn.execute(
            "INSERT INTO note_search (doc_type, file_path, title, sector, "
            "content, embedding, section_title, anchor) "
            "VALUES ('company', ?, ?, 'S', 'body text', ?, '', '1')",
            (fp, fp, emb),
        )
    conn.commit()


VECS = {
    "a.md": [1.0, 0.0, 0.0, 0.0],
    "b.md": [0.9, 0.1, 0.0, 0.0],
    "c.md": [0.0, 0.0, 0.0, 1.0],
}


class TestVecAvailable:
    def test_false_without_table(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        # No vec table yet and lazy_backfill=False -> unavailable.
        assert VS.vec_available(fts_conn, DIMS, lazy_backfill=False) is False

    def test_lazy_backfill_creates_and_populates(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()) + [("d.md", None)])
        assert VS.vec_available(fts_conn, DIMS, lazy_backfill=True) is True
        n = fts_conn.execute(
            f"SELECT COUNT(*) FROM {VS.qualified()}"  # noqa: S608  # schema-qualified via the module helper
        ).fetchone()[0]
        assert n == 3  # d.md has no embedding -> not mirrored

    def test_empty_fts_means_unavailable(self, fts_conn):
        # Table exists but zero rows -> nothing to serve -> False.
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        assert VS.vec_available(fts_conn, DIMS, lazy_backfill=True) is False


class TestKnnSimilarities:
    def test_matches_float64_cosine_ground_truth(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        q = [0.95, 0.05, 0.0, 0.0]
        got = VS.knn_similarities(fts_conn, q, k=3, dims=DIMS)
        assert got is not None
        # Similarity values agree with Python cosine to 6 decimals (float32
        # quantization is <1e-7 at these magnitudes).
        for fp, vec in VECS.items():
            assert got[k(fp)] == pytest.approx(_cos(q, vec), abs=1e-6)
        # Ordering: a.md (near-parallel) > b.md > c.md (orthogonal).
        order = sorted(got, key=lambda fp: got[fp], reverse=True)
        assert order[:2] == [k("a.md"), k("b.md")]

    def test_k_larger_than_rows_returns_all(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        got = VS.knn_similarities(fts_conn, [1.0, 0, 0, 0], k=99, dims=DIMS)
        assert got is not None and len(got) == 3

    def test_none_when_unavailable(self, fts_conn):
        # No vec table, no lazy backfill possible on an empty FTS.
        assert VS.knn_similarities(fts_conn, [1, 0, 0, 0], k=3, dims=DIMS) is None

    def test_dims_mismatch_returns_none(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        assert VS.knn_similarities(fts_conn, [1.0, 0.0], k=3, dims=DIMS) is None


class TestSyncVecTable:
    def test_full_refresh_is_idempotent(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        assert VS.sync_vec_table(fts_conn, DIMS, full=True) == 3
        assert VS.sync_vec_table(fts_conn, DIMS, full=True) == 3  # no dupes
        n = fts_conn.execute(
            f"SELECT COUNT(*) FROM {VS.qualified()}"  # noqa: S608  # schema-qualified via the module helper
        ).fetchone()[0]
        assert n == 3

    def test_incremental_upsert_and_delete(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        # a.md gets a new vector; c.md is deleted from disk.
        new_a = [0.0, 1.0, 0.0, 0.0]
        written = VS.sync_vec_table(
            fts_conn,
            DIMS,
            upsert_rows=[(k("a.md"), json.dumps(new_a))],
            delete_paths=[k("c.md")],
        )
        assert written == 1
        got = VS.knn_similarities(fts_conn, [0.0, 1.0, 0.0, 0.0], k=5, dims=DIMS)
        assert got is not None
        assert set(got) == {k("a.md"), k("b.md")}
        assert got[k("a.md")] == pytest.approx(1.0, abs=1e-6)  # new vector serves

    def test_none_embedding_drops_vec_row(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        VS.sync_vec_table(fts_conn, DIMS, upsert_rows=[(k("b.md"), None)])
        got = VS.knn_similarities(fts_conn, [1.0, 0, 0, 0], k=5, dims=DIMS)
        assert got is not None
        assert k("b.md") not in got

    def test_malformed_json_skipped_not_raised(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        # Corrupt a.md's JSON in FTS, then run a full sync: it must survive.
        fts_conn.execute("UPDATE note_search SET embedding='{bad' WHERE file_path='a.md'")
        fts_conn.commit()
        written = VS.sync_vec_table(fts_conn, DIMS, full=True)
        assert written == 2  # b.md + c.md; a.md skipped
        got = VS.knn_similarities(fts_conn, [1.0, 0, 0, 0], k=5, dims=DIMS)
        assert got is not None and k("a.md") not in got

    def test_wrong_length_vector_not_mirrored(self, fts_conn):
        _seed_fts(fts_conn, [("short.md", [1.0, 2.0])])  # 2 dims, not 4
        assert VS.sync_vec_table(fts_conn, DIMS, full=True) == 0


class TestFallbackRobustness:
    def test_missing_package_degrades(self, fts_conn, monkeypatch):
        _seed_fts(fts_conn, list(VECS.items()))
        monkeypatch.setattr(VS, "_EXTENSION_PATH", None)
        # Every entry point reports unavailable instead of raising.
        assert VS.vec_available(fts_conn, DIMS, lazy_backfill=True) is False
        assert VS.knn_similarities(fts_conn, [1, 0, 0, 0], k=3, dims=DIMS) is None
        assert VS.sync_vec_table(fts_conn, DIMS, full=True) == 0


class TestStoredDimsAndRecreate:
    """local_embeddings (2026-08-20): stored_dims feeds app.py's vector-space
    gate, and a dims change (model swap) must recreate the vec0 table — an
    IF-NOT-EXISTS CREATE alone would leave a FLOAT[N] table the new dims
    can never write into, silently serving stale vectors to KNN."""

    def test_stored_dims_none_without_table(self, fts_conn):
        assert VS.stored_dims(fts_conn) is None

    def test_stored_dims_after_sync(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        assert VS.stored_dims(fts_conn) == DIMS

    def test_dims_change_recreates_table(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        assert VS.sync_vec_table(fts_conn, DIMS, full=True) == 3
        assert VS.stored_dims(fts_conn) == DIMS

        # Model swap: the FTS JSON column becomes 2-dim; sync at the new dims.
        for fp in VECS:
            fts_conn.execute(
                "UPDATE note_search SET embedding = ? WHERE file_path = ?",
                (json.dumps([0.6, 0.8]), fp),
            )
        fts_conn.commit()
        written = VS.sync_vec_table(fts_conn, 2, full=True)
        assert written == 3  # all rows re-mirrored at the new width
        assert VS.stored_dims(fts_conn) == 2
        got = VS.knn_similarities(fts_conn, [1.0, 0.0], k=3, dims=2)
        assert got is not None and len(got) == 3


class TestEmbedStoreConsolidation:
    """Pins for the consolidated embed store (embed_store_consolidation,
    2026-08): one shared file for cache + mirror, with the :memory:-main
    hermeticity branch preserved."""

    def test_file_backed_mains_share_one_store(self, tmp_path):
        """Two connections opening DIFFERENT index dbs land their vec/cache
        state in the SAME store file (per-test EMBED_DB_PATH via conftest)."""
        from helpers.core.embed_cache import CachedEmbed

        a = sqlite3.connect(str(tmp_path / "index_a.db"))
        b = sqlite3.connect(str(tmp_path / "index_b.db"))
        ce_a = CachedEmbed(lambda t: [0.1] * 4, "m", a, source="doc")
        assert ce_a._ok
        vec = ce_a("shared text")  # cohort doc seeds the pooled key
        a.commit()
        ce_b = CachedEmbed(lambda t: [0.2] * 4, "m", b, source="script")
        b.commit()
        assert ce_b._ok
        # Same hash, written by cohort A -> served to connection B.
        assert ce_b("shared text") == vec and ce_b.hits == 1
        con = sqlite3.connect(str(VS.EMBED_DB_PATH))
        try:
            # Direct connection: the pooled table is unqualified main here
            # ('vecdb' exists only as an ATTACH alias at runtime).
            rows = dict(
                con.execute("SELECT source, COUNT(*) FROM embed_cache GROUP BY source").fetchall()
            )
        finally:
            con.close()
        assert rows == {"doc": 1}

    def test_in_memory_main_gets_anonymous_store_not_live(self, tmp_path):
        """The :memory:-main hermeticity branch: even with a POPULATED real
        store on disk at EMBED_DB_PATH, an in-memory main attaches an
        anonymous sidecar and never touches the file (the consolidation
        trap this pins)."""
        VS.EMBED_DB_PATH = tmp_path / "live" / "embed_store.db"
        (tmp_path / "live").mkdir()
        seeded = sqlite3.connect(str(VS.EMBED_DB_PATH))
        seeded.execute(
            "CREATE TABLE embed_cache (text_hash TEXT, model TEXT, "
            "embedding TEXT, source TEXT DEFAULT '', PRIMARY KEY(text_hash, model))"
        )
        seeded.execute("INSERT INTO embed_cache VALUES ('h', 'm', '[]', 'x')")
        seeded.commit()
        seeded.close()

        mem = sqlite3.connect(":memory:")
        VS._attach_vec_db(mem)
        attached_files = [
            r[2] for r in mem.execute("PRAGMA database_list").fetchall() if r[1] == "vecdb"
        ]
        assert attached_files == [""]  # anonymous :memory:, not the live path
        mem.execute(
            "CREATE TABLE vecdb.embed_cache (text_hash TEXT, model TEXT, "
            "embedding TEXT, source TEXT DEFAULT '')"
        )
        mem.execute("INSERT INTO vecdb.embed_cache VALUES ('junk', '', '', '')")
        mem.commit()

        after = sqlite3.connect(str(VS.EMBED_DB_PATH))
        try:
            n = after.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]
        finally:
            after.close()
        assert n == 1  # the live store was not mutated by the memory conn
