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
