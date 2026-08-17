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
        "embedding UNINDEXED, tokenize='porter unicode61')"
    )
    return conn


def _seed_fts(conn, docs):
    """docs: list of (file_path, vector | None)."""
    for fp, vec in docs:
        emb = json.dumps(vec) if vec is not None else None
        conn.execute(
            "INSERT INTO note_search (doc_type, file_path, title, sector, "
            "content, embedding) VALUES ('company', ?, ?, 'S', 'body text', ?)",
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
            f"SELECT COUNT(*) FROM {VS.VEC_TABLE}"  # noqa: S608  # identifier is the VEC_TABLE module constant
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
            assert got[fp] == pytest.approx(_cos(q, vec), abs=1e-6)
        # Ordering: a.md (near-parallel) > b.md > c.md (orthogonal).
        order = sorted(got, key=lambda fp: got[fp], reverse=True)
        assert order[:2] == ["a.md", "b.md"]

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
            f"SELECT COUNT(*) FROM {VS.VEC_TABLE}"  # noqa: S608  # identifier is the VEC_TABLE module constant
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
            upsert_rows=[("a.md", json.dumps(new_a))],
            delete_paths=["c.md"],
        )
        assert written == 1
        got = VS.knn_similarities(fts_conn, [0.0, 1.0, 0.0, 0.0], k=5, dims=DIMS)
        assert got is not None
        assert set(got) == {"a.md", "b.md"}
        assert got["a.md"] == pytest.approx(1.0, abs=1e-6)  # new vector serves

    def test_none_embedding_drops_vec_row(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        VS.sync_vec_table(fts_conn, DIMS, upsert_rows=[("b.md", None)])
        got = VS.knn_similarities(fts_conn, [1.0, 0, 0, 0], k=5, dims=DIMS)
        assert got is not None
        assert "b.md" not in got

    def test_malformed_json_skipped_not_raised(self, fts_conn):
        _seed_fts(fts_conn, list(VECS.items()))
        VS.sync_vec_table(fts_conn, DIMS, full=True)
        # Corrupt a.md's JSON in FTS, then run a full sync: it must survive.
        fts_conn.execute("UPDATE note_search SET embedding='{bad' WHERE file_path='a.md'")
        fts_conn.commit()
        written = VS.sync_vec_table(fts_conn, DIMS, full=True)
        assert written == 2  # b.md + c.md; a.md skipped
        got = VS.knn_similarities(fts_conn, [1.0, 0, 0, 0], k=5, dims=DIMS)
        assert got is not None and "a.md" not in got

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
