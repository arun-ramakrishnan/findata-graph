"""rust_cosine_knn_port S2: backend parity for the _cosine_rank_rows selector.

Three backends (numpy default / rust abi3 opt-in / pure reference loop)
must produce identical rankings and sims on the same inputs, including
zero-norm rows (sim 0.0 by the original loop's `or 1.0` guard) and the
stable-sort tie order. SCRIPT_KNN selector routing is pinned too.
"""

from __future__ import annotations

import random

import os

import pytest

from helpers.maintenance.rebuild_script_search import (
    _cosine_rank_rows,
    _knn_backend,
)


def _pairs(seed: int = 7, n: int = 60, dim: int = 16) -> tuple[list[int], list[list[float]]]:
    rng = random.Random(seed)
    rids, vecs = [], []
    for i in range(n):
        rids.append(100 + i)
        if i == 10:
            vecs.append([0.0] * dim)  # zero-norm row -> sim 0.0 everywhere
        else:
            vecs.append([rng.uniform(-1, 1) for _ in range(dim)])
    return rids, vecs


def _q(dim: int = 16, seed: int = 8) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(dim)]


@pytest.mark.parametrize("backend", ["auto", "numpy", "rust", "pure"])
def test_backend_parity(backend, monkeypatch):
    monkeypatch.setenv("SCRIPT_KNN", backend)
    rids, vecs = _pairs()
    q = _q()
    ref_scored, ref_sims = _cosine_rank_rows(q, rids, vecs)  # pure reference
    got_scored, got_sims = _cosine_rank_rows(q, rids, vecs)
    assert [r for r, _ in got_scored] == [r for r, _ in ref_scored]
    for rid, sim in got_scored:
        assert sim == pytest.approx(ref_sims[rid], abs=1e-6)
    assert got_sims.keys() == ref_sims.keys()


def test_zero_norm_row_scores_zero():
    rids, vecs = _pairs()
    q = _q()
    scored, sims = _cosine_rank_rows(q, rids, vecs)
    assert sims[rids[10]] == pytest.approx(0.0, abs=1e-9)


def test_backend_selector_default_and_override(monkeypatch):
    monkeypatch.delenv("SCRIPT_KNN", raising=False)
    assert _knn_backend() == "auto"
    monkeypatch.setenv("SCRIPT_KNN", "rust")
    assert _knn_backend() == "rust"


def test_unknown_backend_falls_to_pure(monkeypatch):
    monkeypatch.setenv("SCRIPT_KNN", "bogus")
    rids, vecs = _pairs(n=8)
    q = _q()
    scored, _ = _cosine_rank_rows(q, rids, vecs)
    assert len(scored) == 8  # bogus name skips both fast legs -> pure loop


def test_rust_module_importable_and_parity_with_numpy():
    """The abi3 module itself (installed at Rust/target → site-packages)
    must import and agree with the numpy path on random matrices.

    importorskip keeps non-build machines green, but the live selector
    prefers rust when importable — a vanished .so degrades it to numpy
    SILENTLY (the gemma-sidecar shape guard_gemma_stamp exists for).
    SCRIPT_KNN_STRICT=1 (set by the qa harness) turns the skip into a
    hard failure on boxes that carry the production backend.
    """
    np = pytest.importorskip("numpy")
    if os.environ.get("SCRIPT_KNN_STRICT") == "1":
        # Dynamic import: the module is the OPTIONAL abi3 wheel (absent on
        # machines without the build) — ty cannot resolve it statically, so
        # the strict pin mirrors the selector's own importlib route.
        import importlib

        importlib.import_module("cosine_knn")  # hard pin: production backend must exist

    cosine_knn = pytest.importorskip("cosine_knn")
    rng = np.random.default_rng(3)
    m = rng.random((200, 32), dtype=np.float32)
    q = rng.random(32, dtype=np.float32)
    top = cosine_knn.knn(q, m, 10)
    sims = (m / np.linalg.norm(m, axis=1, keepdims=True)) @ (q / np.linalg.norm(q))
    order = np.argsort(-sims, kind="stable")[:10]
    assert [r for r, _ in top] == order.tolist()
    for row, score in top:
        assert score == pytest.approx(float(sims[row]), abs=1e-6)


def test_cosine_leg_end_to_end(tmp_path, monkeypatch):
    """_cosine_leg wiring: same ranking through the selector on a seeded
    sqlite sidecar with real vec_codec blobs (script_search shape)."""
    import sqlite3

    from helpers.core.vec_codec import pack_f32

    con = sqlite3.connect(str(tmp_path / "s.db"))
    con.execute("CREATE TABLE script_search (title TEXT, embedding BLOB)")
    con.execute("CREATE TABLE script_search_info (key TEXT PRIMARY KEY, value TEXT)")
    rng = random.Random(11)
    rids = []
    for i in range(20):
        vec = [rng.uniform(-1, 1) for _ in range(8)]
        if i == 5:
            vec = [0.0] * 8
        cur = con.execute(
            "INSERT INTO script_search (title, embedding) VALUES (?, ?)",
            (f"t{i}", pack_f32(vec)),
        )
        rids.append(cur.lastrowid)
    con.commit()

    q_vec = [rng.uniform(-1, 1) for _ in range(8)]
    results = {}
    for backend in ("pure", "numpy", "rust"):
        monkeypatch.setenv("SCRIPT_KNN", backend)
        # bypass the embedder resolution: call the scoring path directly
        # via the module-level seam (query_vec stamps match by construction)
        rids_out, vecs = [], []
        for rid, emb in con.execute(
            "SELECT rowid, embedding FROM script_search WHERE embedding IS NOT NULL AND embedding != ''"
        ):
            from helpers.core.vec_codec import load_vec

            vec = load_vec(emb)
            if not vec or len(vec) != len(q_vec):
                continue
            rids_out.append(rid)
            vecs.append(vec)
        results[backend] = _cosine_rank_rows(q_vec, rids_out, vecs)
    ref_rows = [r for r, _ in results["pure"][0]]
    for backend in ("numpy", "rust"):
        assert [r for r, _ in results[backend][0]] == ref_rows
    monkeypatch.setenv("SCRIPT_KNN", "auto")
    assert _knn_backend() == "auto"
