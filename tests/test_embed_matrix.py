#!/usr/bin/env python3
"""S2b/S2c tests — aligned f32 embedding matrix (proposal corpus_embeddings_scaling).

Hermetic: synthetic embeddings in tmp_path; no research.db / MAX dependency
(the MAX path is exercised in the proposal's acceptance runs, not pytest —
it costs a ~2.5 s JIT compile per session).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from helpers.core.embed_matrix import EmbedMatrixStore, _stride_floats


def _synth(n: int, dims: int, seed: int = 0) -> tuple[list[str], np.ndarray]:
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal((n, dims)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)  # unit rows like note_search
    ids = [f"findata/Companies/S{i:03d}/Note_{i:03d}.md" for i in range(n)]
    return ids, emb


def _numpy_topk(emb: np.ndarray, ids: list[str], q: np.ndarray, k: int) -> list[tuple[str, float]]:
    q = q / np.linalg.norm(q)
    s = emb @ q
    idx = np.argsort(-s, kind="stable")[:k]
    return [(ids[i], float(s[i])) for i in idx]


@pytest.mark.parametrize("dims", [128, 384, 100])  # 100 forces padded stride (400B -> 448B)
def test_build_load_aligned_topk_parity(tmp_path, dims):
    store = EmbedMatrixStore(tmp_path / "m.f32", tmp_path / "m.json")
    ids, emb = _synth(50, dims)
    stats = store.build(ids, emb, model="synth")
    assert stats["rebuild"] and stats["count"] == 50
    em = store.load()
    assert em.aligned and em.matrix.ctypes.data % 64 == 0
    assert em.matrix.shape == (50, dims)
    rng = np.random.default_rng(7)
    for _ in range(5):
        q = rng.standard_normal(dims).astype(np.float32)
        got = em.top_k(q, 10)
        want = _numpy_topk(emb, ids, q, 10)
        assert [i for i, _ in got] == [i for i, _ in want]  # exact id parity
        assert all(np.isclose(a[1], b[1], atol=1e-5) for a, b in zip(got, want))
    # self-retrieval: querying with row i's embedding must return row i first
    for i in (0, 17, 49):
        assert em.top_k(emb[i], 1)[0][0] == ids[i]


def test_refresh_rewrites_only_changed_rows(tmp_path):
    store = EmbedMatrixStore(tmp_path / "m.f32", tmp_path / "m.json")
    ids, emb = _synth(40, 128)
    store.build(ids, emb)
    before_bytes = (tmp_path / "m.f32").read_bytes()

    emb2 = emb.copy()
    emb2[3] = -emb2[3]  # flip 3 rows
    emb2[11] = emb2[11][::-1].copy()  # permute (scale-then-renorm would be a no-op on unit rows)
    emb2[39] += 1.0
    emb2[39] /= np.linalg.norm(emb2[39])
    stats = store.refresh(ids, emb2)
    assert stats["rebuild"] is False and stats["rewritten"] == 3

    after_bytes = (tmp_path / "m.f32").read_bytes()
    stride_bytes = _stride_floats(128) * 4
    changed_rows = {3, 11, 39}
    for i in range(40):
        seg = slice(i * stride_bytes, (i + 1) * stride_bytes)
        if i in changed_rows:
            assert before_bytes[seg] != after_bytes[seg], f"row {i} should have changed"
        else:
            assert before_bytes[seg] == after_bytes[seg], f"row {i} must be byte-identical"

    em = store.load()
    assert np.allclose(em.matrix[3], emb2[3], atol=1e-6)
    assert np.allclose(em.matrix[0], emb[0], atol=1e-6)


def test_refresh_noop_when_unchanged(tmp_path):
    store = EmbedMatrixStore(tmp_path / "m.f32", tmp_path / "m.json")
    ids, emb = _synth(20, 128)
    store.build(ids, emb)
    stats = store.refresh(ids, emb)
    assert stats["rewritten"] == 0 and stats["rebuild"] is False


def test_id_set_or_model_change_forces_rebuild(tmp_path):
    store = EmbedMatrixStore(tmp_path / "m.f32", tmp_path / "m.json")
    ids, emb = _synth(20, 128)
    store.build(ids, emb, model="a")
    assert store.refresh(ids, emb, model="b")["rebuild"] is True  # model swap
    rebuilt = store.refresh(ids[:-1], emb[:-1])  # id set AND default model tag differ
    assert rebuilt["rebuild"] is True
    meta = json.loads((tmp_path / "m.json").read_text())
    assert meta["model"] == "note_search" and len(meta["ids"]) == 19


def test_stride_pads_to_64b():
    assert _stride_floats(384) == 384  # 1536B rows already 64B-aligned
    assert _stride_floats(128) == 128  # 512B
    assert _stride_floats(100) == 112  # 400B -> 448B


# ---------------------------------------------------------------------------
# bulk_data_lanes S3 — matrix_from_rows (BLOB-native builder, revived lane)
# ---------------------------------------------------------------------------


def test_matrix_from_rows_blob_fast_path():
    """All-BLOB rows take the concat+frombuffer fast path (post-#223
    storage) and match the per-row decode exactly."""
    from helpers.core.embed_matrix import matrix_from_rows
    from helpers.core.vec_codec import pack_f32

    ids, emb = _synth(12, 64)
    rows = [(i, pack_f32(v.tolist())) for i, v in zip(ids, emb)]
    out_ids, out = matrix_from_rows(rows)
    assert out_ids == ids
    assert out.shape == (12, 64) and out.dtype == np.float32
    assert np.array_equal(out, emb)


def test_matrix_from_rows_text_fallback():
    """Legacy TEXT JSON rows still decode (tolerant load_vec) and a mixed
    corpus falls back per-row without losing rows."""
    from helpers.core.embed_matrix import matrix_from_rows
    from helpers.core.vec_codec import pack_f32

    ids, emb = _synth(6, 32)
    rows = [
        (ids[0], json.dumps(emb[0].tolist())),  # legacy TEXT
        (ids[1], pack_f32(emb[1].tolist())),  # BLOB
        (ids[2], json.dumps(emb[2].tolist())),
        (ids[3], pack_f32(emb[3].tolist())),
        (ids[4], json.dumps(emb[4].tolist())),
        (ids[5], pack_f32(emb[5].tolist())),
    ]
    out_ids, out = matrix_from_rows(rows)
    assert out_ids == ids
    assert np.allclose(out, emb, atol=1e-6)


def test_matrix_from_rows_empty_fails_loud():
    """Zero usable vectors must raise, never hand back an empty matrix —
    the silence mode that froze this lane through the #223 migration."""
    import pytest

    from helpers.core.embed_matrix import matrix_from_rows

    with pytest.raises(ValueError, match="0 usable vectors"):
        matrix_from_rows([])
    with pytest.raises(ValueError, match="0 usable vectors"):
        matrix_from_rows([("k", b"\x00"), ("k2", None)])


def test_refresh_over_blob_corpus_rewrites_changed_rows(tmp_path):
    """End-to-end: build + hash-gated refresh over BLOB-sourced rows (the
    revived production shape — rows come from note_search as f32 BLOBs)."""
    from helpers.core.embed_matrix import matrix_from_rows
    from helpers.core.vec_codec import pack_f32

    store = EmbedMatrixStore(tmp_path / "m.f32", tmp_path / "m.json")
    ids, emb = _synth(10, 64)
    rows = [(i, pack_f32(v.tolist())) for i, v in zip(ids, emb)]
    out_ids, out = matrix_from_rows(rows)
    assert store.build(out_ids, out, model="synth")["count"] == 10

    emb2 = emb.copy()
    emb2[4] = -emb2[4]
    rows2 = [(i, pack_f32(v.tolist())) for i, v in zip(ids, emb2)]
    _, out2 = matrix_from_rows(rows2)
    stats = store.refresh(out_ids, out2, model="synth")
    assert stats["rebuild"] is False and stats["rewritten"] == 1
