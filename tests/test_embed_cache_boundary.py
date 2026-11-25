"""Decode-boundary pins for the shared embedding cache (S2).

The Python read path decodes stored vectors through
`helpers.core.vec_codec.load_vec` — the single choke point that must
treat a corrupt/stale BLOB as a cache MISS (never a crash, never a
wrong-length vector), and accept the legacy TEXT-JSON form. The Rust
hit-lookup sidecar pins the same boundaries on its side
(`Rust/embed_cache/src/lib.rs` `decode_embedding`); these pins cover
the Python codec plus the cache-layer dispatch (`cached_embed_batch`):
what happened to a row must be visible only through the miss/hit split.
"""

from __future__ import annotations

import numpy as np
import pytest

from helpers.core.vec_codec import dims_from_blob, load_vec, pack_f32
from helpers.core import embed_cache as EC


class TestVecCodecBoundary:
    def test_blob_length_not_multiple_of_four_is_none(self):
        assert load_vec(b"\x00\x01\x02") is None
        assert load_vec(b"\x00" * 5) is None
        assert load_vec(b"\x00" * 6) is None

    def test_empty_blob_is_empty_vector(self):
        # Python tolerance: empty BLOB decodes to a zero-dim vector (the
        # array codec succeeds on zero bytes), unlike the Rust sidecar,
        # which treats empty blobs as misses. Pin the asymmetry.
        assert load_vec(b"") == []

    def test_exact_f32_blob_roundtrips(self):
        vec = [1.25, -2.0, 0.0, 3.5]
        blob = pack_f32(vec)
        assert dims_from_blob(blob) == 4
        assert load_vec(blob) == pytest.approx(vec)

    def test_padding_trailing_bytes_do_not_change_dimension_report(self):
        # 5 bytes: dims_from_blob reports 1 (floor), but load_vec must
        # still refuse the blob (len % 4 != 0).
        blob = pack_f32([1.0]) + b"\x00"
        assert dims_from_blob(blob) == 1
        assert load_vec(blob) is None

    def test_legacy_text_json_vector_is_accepted(self):
        assert load_vec("[3.0, 4.0]") == pytest.approx([3.0, 4.0])
        assert load_vec("[1.5]") == pytest.approx([1.5])

    def test_malformed_json_is_none(self):
        assert load_vec("[3.0,") is None
        assert load_vec("not json") is None
        assert load_vec("") is None

    def test_json_dict_is_none(self):
        assert load_vec('{"a": 1}') is None

    def test_none_and_empty_values_are_none(self):
        assert load_vec(None) is None
        assert load_vec("") is None
        assert load_vec(b"") == []


class TestCachedEmbedDecodeBoundary:
    """The cache layer must route decode failures to the miss set.

    Uses a native duckdb host connection so the cache read/write both
    stay on the hermetic temp file (sqlite hosts resolve to the shared
    file-backed cache, which is not test-scoped).
    """

    @pytest.fixture()
    def conn(self, tmp_path):
        import duckdb

        db = duckdb.connect(str(tmp_path / "embed.duckdb"))
        db.execute(EC.EMBED_CACHE_DDL)
        yield db
        db.close()

    def _seed(self, db, text, model, embedding):
        import hashlib

        h = hashlib.sha256(text.encode()).hexdigest()
        db.execute(
            f"INSERT OR REPLACE INTO {EC.EMBED_CACHE_TABLE} "  # noqa: S608  # fixed internal table constant
            "(text_hash, model, embedding) VALUES (?, ?, ?)",
            (h, model, embedding),
        )
        db.commit()

    def _embed(self, texts):
        return [[0.0] * 2 for _ in texts]

    def test_corrupt_blob_counts_as_miss(self, conn):
        self._seed(conn, "a", "m", b"\x00\x01\x02")  # 3 bytes
        vecs, stats = EC.cached_embed_batch(conn, ["a"], "m", self._embed)
        assert stats["misses"] == 1
        assert stats["hits"] == 0
        assert vecs == [[0.0, 0.0]]

    def test_empty_blob_counts_as_miss(self, conn):
        # The cache lookup treats an empty BLOB as a miss (the raw value
        # is falsy), even though the codec alone would decode it to [].
        self._seed(conn, "b", "m", b"")
        vecs, stats = EC.cached_embed_batch(conn, ["b"], "m", self._embed)
        assert stats["misses"] == 1
        assert stats["hits"] == 0
        assert vecs == [[0.0, 0.0]]

    def test_packed_blob_is_a_hit(self, conn):
        self._seed(conn, "c", "m", EC.pack_f32([3.0, 4.0]))
        vecs, stats = EC.cached_embed_batch(
            conn, ["c"], "m", lambda texts: (_ for _ in ()).throw(AssertionError("must not embed"))
        )
        assert stats["misses"] == 0
        assert stats["hits"] == 1
        assert len(vecs) == 1
        assert np.allclose(vecs[0], [3.0, 4.0])

    def test_mixed_hits_and_corrupt_misses(self, conn):
        self._seed(conn, "good", "m", EC.pack_f32([1.0, 2.0]))
        self._seed(conn, "bad", "m", b"\x00\x01\x02")  # 3 bytes
        vecs, stats = EC.cached_embed_batch(conn, ["good", "bad"], "m", self._embed)
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert len(vecs) == 2
        assert np.allclose(vecs[0], [1.0, 2.0])
        assert np.allclose(vecs[1], [0.0, 0.0])
