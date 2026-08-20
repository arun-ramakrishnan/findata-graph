# Tests for helpers/core/embed_cache.py
"""Unit tests for the shared (sha256(text), model) embedding cache.

The per-text ``CachedEmbed`` wrapper is exercised end-to-end through the
rebuild_note_search tests (cold/warm/model-label/pre-warm); these tests pin
the BATCH interface the company-embeddings populate uses, plus the shared
module's own contracts (model keying, degradation, length guard).
"""

import sqlite3

from helpers.core.embed_cache import EMBED_CACHE_TABLE, cached_embed_batch


def _conn(tmp_path):
    return sqlite3.connect(str(tmp_path / "cache_test.db"))


def _fake_embed():
    """Embedder that records its calls and maps text -> a recognizable vector."""
    calls: list[list[str]] = []

    def fn(texts):
        calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]

    return fn, calls


def _cache_rows(conn):
    return conn.execute(
        f"SELECT text_hash, model FROM {EMBED_CACHE_TABLE}"  # noqa: S608  # constant table name
    ).fetchall()


class TestCachedEmbedBatch:
    def test_cold_embeds_all_in_one_batch_and_seeds(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        assert st == {"hits": 0, "misses": 2, "dirty": 2}
        assert calls == [["aa", "bbb"]]  # ONE batch call for all misses
        assert [v[0] for v in vecs] == [2.0, 3.0]
        assert len(_cache_rows(conn)) == 2

    def test_warm_serves_from_cache_without_embedding(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        v1, _ = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        v2, st = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        assert st == {"hits": 2, "misses": 0, "dirty": 0}
        assert len(calls) == 1  # still only the cold call
        assert v1 == v2  # cache round-trips vectors faithfully

    def test_partial_miss_reembeds_only_the_missing_texts(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        vecs, st = cached_embed_batch(conn, ["aa", "cccc"], "m1", fn)
        assert st["hits"] == 1
        assert st["misses"] == 1
        assert calls[-1] == ["cccc"]  # only the new text went to the embedder
        assert vecs[1][0] == 4.0

    def test_cache_is_keyed_by_model_label(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa"], "m1", fn)
        _, st = cached_embed_batch(conn, ["aa"], "m2", fn)
        assert st["misses"] == 1  # other model's vector must never be served
        models = {row[1] for row in _cache_rows(conn)}
        assert models == {"m1", "m2"}

    def test_empty_text_list_never_calls_the_embedder(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, [], "m1", fn)
        assert vecs == []
        assert st == {"hits": 0, "misses": 0, "dirty": 0}
        assert calls == []

    def test_short_embedder_reply_raises(self, tmp_path):
        conn = _conn(tmp_path)

        def bad_fn(texts):
            return [[0.0, 1.0] for _ in texts[:-1]]  # one vector short

        import pytest

        with pytest.raises(ValueError, match="batch embedder returned"):
            cached_embed_batch(conn, ["a", "b"], "m1", bad_fn)

    def test_no_sidecar_degrades_to_uncached(self, tmp_path, monkeypatch):
        import helpers.core.vec_search as VS

        def boom(conn):
            raise sqlite3.Error("no sidecar for you")

        monkeypatch.setattr(VS, "_attach_vec_db", boom)
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, ["aa"], "m1", fn)
        assert st == {"hits": 0, "misses": 1, "dirty": 0}
        assert calls == [["aa"]]  # still embedded, just uncached
        assert vecs == [[2.0, 1.0]]

    def test_corrupted_cache_row_counts_as_a_miss(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa"], "m1", fn)
        # Corrupt the stored vector so it can't parse.
        conn.execute(
            f"UPDATE {EMBED_CACHE_TABLE} SET embedding = 'not-json'",  # noqa: S608  # constant table name
        )
        conn.commit()
        _, st = cached_embed_batch(conn, ["aa"], "m1", fn)
        assert st["misses"] == 1
        assert st["hits"] == 0
        assert len(calls) == 2  # re-embedded
