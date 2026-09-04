#!/usr/bin/env python3
"""MAX-backed flat exact KNN over the aligned f32 matrix (S2c scale leg).

Lives in Mojo/bench/ (not helpers/core) deliberately: the `max` package is
a dev/optional dependency — the Mojo toolchain group — so the MAX engine
must not be imported from Tier-1 helpers. `helpers/core/embed_matrix.py`
owns the aligned store + the numpy `top_k` default; this module is the
opt-in scale leg (the benched `19.7 ms @ 100M elements` pattern,
doc/local/mojo/mojo_pilot.md § MAX evaluation).

Usage (Mojo/bench is not a Python package — sibling import):
    import sys; sys.path.insert(0, "."); sys.path.insert(0, "Mojo/bench")
    from helpers.core.embed_matrix import EmbedMatrixStore
    from flat_knn import FlatKNN
    em = EmbedMatrixStore().load()
    fk = FlatKNN(em)                       # compile once (~2.5 s warm JIT cache)
    hits = fk.top_k(query_vec, k=10)       # [(file_path, score), ...]

Alignment contract (SEGV-proven 2026-09-02): both operands handed to MAX
must be 64-byte aligned — the matrix is materialized here as a resident
aligned copy (passing the raw np.memmap view lets MAX's DLPack import copy
into a 16-mod-32 mimalloc buffer → vmovaps SIGSEGV, observed intermittently
before this fix; ×5 stable after).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.embed_matrix import EmbedMatrix  # noqa: E402  # post-bootstrap (mirrors helpers/graph/*.py)
from aligned_array import aligned_array  # noqa: E402  # shared 64B-alignment helper (same dir)


class FlatKNN:
    """MAX opt-in — compile once, exact top-k at benched rates.

    Requires `stride == dims` (contiguous rows for MAX's zero-copy
    handoff); padded strides raise rather than risking a silent copy.
    """

    def __init__(self, em: EmbedMatrix) -> None:
        if int(em.meta["stride"]) != int(em.meta["dims"]):
            raise RuntimeError("FlatKNN requires stride == dims (contiguous rows)")
        from max.dtype import DType
        from max.engine import InferenceSession
        from max.graph import DeviceRef, Graph, ops
        from max.graph.type import TensorType

        self._em = em
        rows, dims = em.matrix.shape
        self._dims = dims
        # Resident 64B-aligned copy — do NOT hand MAX the mmap view and hope
        # its DLPack import stays zero-copy (see module docstring). At 100M
        # scale this buffer IS the serving working set (381 MB @ 260k×384).
        resident, buf = aligned_array((rows, dims))
        resident[:] = em.matrix
        self._resident, self._keepalive = resident, buf
        device = DeviceRef.CPU()
        lhs = TensorType(DType.float32, [rows, dims], device)
        rhs = TensorType(DType.float32, [dims, 1], device)
        with Graph("flat_knn", input_types=[lhs, rhs]) as g:
            a, b = g.inputs[0].tensor, g.inputs[1].tensor
            g.output(ops.matmul(a, b))
        self._model = InferenceSession().load(g)

    def top_k(self, query: np.ndarray, k: int = 10) -> list[tuple[str, float]]:
        em = self._em
        q = np.asarray(query, dtype=np.float32).ravel()
        n = float(np.linalg.norm(q))
        if n == 0 or not np.isfinite(n):
            return []
        # 64B-aligned query buffer — the vmovaps contract (bench-proven 8/8
        # segfault on 16-mod-32 inputs).
        qbuf, _qkeep = aligned_array((self._dims,))
        qbuf[:] = q / n
        scores = self._model.execute(self._resident, qbuf.reshape(-1, 1))[0].to_numpy().ravel()
        k = min(k, len(scores))
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx], kind="stable")]
        return [(em.ids[i], float(scores[i])) for i in idx]
