#!/usr/bin/env python3
"""Aligned f32 embedding matrix — the 100M-element KNN substrate (S2b/S2c).

Proposal ``doc/improvements/proposals/corpus_embeddings_scaling.md``: the
embedding substrate (``research.db note_search`` JSON column, vec0 mirror in
``memory/embed_store.db``) has no mmap-able **aligned** float32 matrix, which
is the contract the 2026-09-02 MAX bench proved required — the JIT CPU kernel
reads host inputs zero-copy with ``vmovaps`` loads that need 32-byte
alignment (misaligned inputs segfaulted 8/8; aligned passed 8/8).

Design:
  * ``memory/embed_matrix.f32`` — row-major ``N × dims`` float32, one row per
    note, rows padded so every row starts 64B-aligned (``stride`` floats;
    ``dims=384`` needs no padding: ``384*4 = 1536 ≡ 0 mod 64``).
  * ``memory/embed_matrix.json`` — meta: model tag, dims, count, stride,
    ids (note_search file_path order), per-row ``blake2b(embedding, 8)``
    hashes.
  * ``EmbedMatrixStore.build()`` full write; ``.refresh()`` hash-gated —
    rewrites ONLY changed rows in place (fixed stride → random access);
    model-tag or id-set mismatch ⇒ full rebuild (same semantics as the
    ``embed_cache`` (sha256+model) key: a model swap re-embeds once).
  * ``EmbedMatrix.matrix`` — zero-copy strided view of the mmap
    (``[count, dims]``); ``.aligned`` asserts the 64B contract.
  * ``top_k(query, k)`` — flat EXACT scan (numpy matvec; embeddings are
    L2-normalized in note_search, cosine == dot). ``FlatKNN`` is the MAX
    opt-in (compile once ~2.5 s, then the benched 19.7 ms @ 100M pattern);
    requires ``stride == dims`` (contiguous rows for the zero-copy handoff)
    and aligns the query into a 64B buffer.

Derived state exactly like ``embed_store.db``: safe to delete, rebuilt from
``note_search``; excluded from snapshots.

Usage:
    from helpers.core.embed_matrix import EmbedMatrixStore
    store = EmbedMatrixStore()
    store.build(ids, embeddings, model="note_search")   # or .refresh(...)
    hits = store.load().top_k(query_vec, k=10)          # [(file_path, score), ...]
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MATRIX_PATH = _REPO_ROOT / "memory" / "embed_matrix.f32"
_META_PATH = _REPO_ROOT / "memory" / "embed_matrix.json"
_ALIGNMENT = 64  # vmovaps needs 32; 64 keeps every row SSE/AVX/AMX-friendly


def _stride_floats(dims: int) -> int:
    """Row stride in floats so each row start is _ALIGNMENT-byte aligned."""
    row_bytes = dims * 4
    stride_bytes = -(-row_bytes // _ALIGNMENT) * _ALIGNMENT  # ceil to 64
    return stride_bytes // 4


def _row_hash(vec: np.ndarray) -> str:
    return hashlib.blake2b(
        np.ascontiguousarray(vec, dtype=np.float32).tobytes(), digest_size=8
    ).hexdigest()


@dataclass
class EmbedMatrix:
    """Zero-copy view over the aligned matrix file + its meta."""

    meta: dict[str, Any]
    mm: np.memmap
    ids: list[str]
    matrix: np.ndarray = field(init=False)  # [count, dims] strided view

    def __post_init__(self) -> None:
        stride = int(self.meta["stride"])
        dims = int(self.meta["dims"])
        count = int(self.meta["count"])
        self.matrix = self.mm.reshape(count, stride)[:, :dims]
        if self.matrix.ctypes.data % _ALIGNMENT:
            raise RuntimeError(
                f"embed_matrix: base pointer {self.matrix.ctypes.data:#x} not {_ALIGNMENT}B-aligned"
                " — MAX vmovaps contract violated (SEGV risk)"
            )

    @property
    def aligned(self) -> bool:
        return self.matrix.ctypes.data % _ALIGNMENT == 0

    def top_k(self, query: np.ndarray, k: int = 10) -> list[tuple[str, float]]:
        """Exact flat cosine top-k. Rows are unit-norm (note_search); the query
        is normalized defensively. Returns [(id, score)] sorted desc."""
        q = np.asarray(query, dtype=np.float32).ravel()
        n = np.linalg.norm(q)
        if n == 0 or not np.isfinite(n):
            return []
        scores = self.matrix @ (q / n)
        k = min(k, len(scores))
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx], kind="stable")]
        return [(self.ids[i], float(scores[i])) for i in idx]


@dataclass
class EmbedMatrixStore:
    # default_factory resolves the module globals at CONSTRUCTION time
    # (tests monkeypatch them; a class-level default would freeze the binding
    # and a `Path | None` field poisons every downstream attribute type).
    matrix_path: Path = field(default_factory=lambda: _MATRIX_PATH)
    meta_path: Path = field(default_factory=lambda: _META_PATH)

    def build(
        self, ids: list[str], embeddings: np.ndarray, model: str = "note_search"
    ) -> dict[str, Any]:
        """Full write of matrix + meta. `embeddings` is [N, dims] float32."""
        emb = np.ascontiguousarray(embeddings, dtype=np.float32)
        if emb.ndim != 2 or emb.shape[0] != len(ids):
            raise ValueError(f"embeddings {emb.shape} does not match {len(ids)} ids")
        count, dims = emb.shape
        stride = _stride_floats(dims)
        self.matrix_path.parent.mkdir(parents=True, exist_ok=True)
        with self.matrix_path.open("wb") as f:
            if stride == dims:
                emb.tofile(f)
            else:
                padded = np.zeros((count, stride), dtype=np.float32)
                padded[:, :dims] = emb
                padded.tofile(f)
        meta = {
            "model": model,
            "dims": dims,
            "count": count,
            "stride": stride,
            "alignment": _ALIGNMENT,
            "ids": list(ids),
            "row_hashes": [_row_hash(emb[i]) for i in range(count)],
        }
        self.meta_path.write_text(json.dumps(meta))
        return {"count": count, "rewritten": count, "rebuild": True, "model": model, "dims": dims}

    def refresh(
        self, ids: list[str], embeddings: np.ndarray, model: str = "note_search"
    ) -> dict[str, Any]:
        """Hash-gated incremental refresh — rewrite ONLY changed rows in place.
        Model-tag / id-set / dims mismatch ⇒ full rebuild."""
        emb = np.ascontiguousarray(embeddings, dtype=np.float32)
        if emb.ndim != 2 or emb.shape[0] != len(ids):
            raise ValueError(f"embeddings {emb.shape} does not match {len(ids)} ids")
        if not self.meta_path.exists() or not self.matrix_path.exists():
            return self.build(ids, emb, model)
        meta = json.loads(self.meta_path.read_text())
        if (
            meta.get("model") != model
            or meta.get("ids") != list(ids)
            or meta.get("dims") != emb.shape[1]
        ):
            return self.build(ids, emb, model)
        stride, dims = int(meta["stride"]), int(meta["dims"])
        hashes = meta["row_hashes"]
        changed = [i for i in range(len(ids)) if _row_hash(emb[i]) != hashes[i]]
        if changed:
            mm = np.memmap(self.matrix_path, dtype=np.float32, mode="r+", shape=(len(ids), stride))
            try:
                for i in changed:
                    mm[i, :dims] = emb[i]
                    hashes[i] = _row_hash(emb[i])
            finally:
                mm.flush()
                del mm
            meta["row_hashes"] = hashes
            self.meta_path.write_text(json.dumps(meta))
        return {
            "count": len(ids),
            "rewritten": len(changed),
            "rebuild": False,
            "model": model,
            "dims": dims,
        }

    def load(self) -> EmbedMatrix:
        meta = json.loads(self.meta_path.read_text())
        mm = np.memmap(
            self.matrix_path,
            dtype=np.float32,
            mode="r",
            shape=(int(meta["count"]), int(meta["stride"])),
        )
        return EmbedMatrix(meta=meta, mm=mm, ids=list(meta["ids"]))


def from_note_search(db_path: str | Path = "memory/research.db") -> tuple[list[str], np.ndarray]:
    """Read the live embedding source — note_search (file_path, embedding JSON).

    Routes through helpers.core.db.connect (B2 guard: no direct stdlib
    connect outside the allowlist); read_only — this is a
    diagnostic/refresh reader."""
    from helpers.core.db import connect

    conn = connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT file_path, embedding FROM note_search WHERE embedding IS NOT NULL ORDER BY file_path"
        ).fetchall()
    finally:
        conn.close()
    ids = [r[0] for r in rows]
    emb = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
    return ids, emb
