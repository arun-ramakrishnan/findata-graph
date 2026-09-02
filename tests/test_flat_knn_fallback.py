#!/usr/bin/env python3
"""S2d tests — flat-matrix KNN fallback + rebuild refresh hook (corpus_embeddings_scaling).

Hermetic: synthetic stores in tmp_path; no research.db, no vec0, no MAX.
"""

from __future__ import annotations

import json
import math
import sqlite3

import numpy as np

from app import _flat_knn_map
from helpers.core.embed_matrix import EmbedMatrixStore
from helpers.maintenance.rebuild_note_search import _refresh_embed_matrix


def _synth_store(tmp_path, n=30, dims=64, seed=0):
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal((n, dims)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    ids = [f"findata/Companies/S{i // 10}/Note_{i:03d}.md" for i in range(n)]
    store = EmbedMatrixStore(tmp_path / "m.f32", tmp_path / "m.json")
    store.build(ids, emb, model="note_search")
    return store, ids, emb


def _python_cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def test_flat_knn_map_exact_cosine_scores(tmp_path):
    store, ids, emb = _synth_store(tmp_path)
    q = np.random.default_rng(5).standard_normal(emb.shape[1]).astype(np.float32)
    got = _flat_knn_map(q.tolist(), ids[:4], store=store)
    assert got is not None and len(got) == len(ids)
    for i in (0, 7, 29):
        assert abs(got[ids[i]] - _python_cosine(q.tolist(), emb[i].tolist())) < 1e-5
    # sorted desc
    vals = list(got.values())
    assert vals == sorted(vals, reverse=True)


def test_flat_knn_map_stale_page_returns_none(tmp_path):
    store, ids, _emb = _synth_store(tmp_path)
    stale = ids[:3] + ["findata/Companies/S9/NEW_NOTE.md"]  # not in the matrix
    assert _flat_knn_map([0.1] * 64, stale, store=store) is None


def test_flat_knn_map_missing_matrix_returns_none(tmp_path):
    empty = EmbedMatrixStore(tmp_path / "absent.f32", tmp_path / "absent.json")
    assert _flat_knn_map([0.1] * 64, ["findata/x.md"], store=empty) is None


def _mini_note_search_conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE note_search (doc_type TEXT, file_path TEXT, title TEXT,"
        " sector TEXT, content TEXT, embedding TEXT)"
    )
    conn.executemany("INSERT INTO note_search VALUES (?,?,?,?,?,?)", rows)
    return conn


def test_refresh_embed_matrix_hook_builds_and_gates(tmp_path):
    import helpers.core.embed_matrix as emm

    old = (emm._MATRIX_PATH, emm._META_PATH)
    emm._MATRIX_PATH, emm._META_PATH = tmp_path / "m.f32", tmp_path / "m.json"
    try:
        rng = np.random.default_rng(1)
        emb = rng.standard_normal((6, 32)).astype(np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        rows = [
            ("company", f"findata/N{i}.md", f"N{i}", "s", "c", json.dumps(emb[i].tolist()))
            for i in range(6)
        ]
        conn = _mini_note_search_conn(rows)
        assert _refresh_embed_matrix(conn) == 6  # build
        assert _refresh_embed_matrix(conn) == 0  # hash-gated no-op
        emb2 = emb.copy()
        emb2[2] = -emb2[2]
        conn.execute(
            "UPDATE note_search SET embedding=? WHERE file_path=?",
            (json.dumps(emb2[2].tolist()), "findata/N2.md"),
        )
        assert _refresh_embed_matrix(conn) == 1  # exactly the changed row
    finally:
        emm._MATRIX_PATH, emm._META_PATH = old
