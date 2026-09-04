"""64B-aligned numpy allocation (consolidation: single source of truth).

Canonical home for the over-allocate-and-slice idiom previously defined
in max_real_matmul.py and inlined twice in flat_knn.py (resident matrix
+ query buffer).
"""

from __future__ import annotations

import numpy as np


def aligned_array(shape, dtype=np.float32, alignment=64):
    """numpy array whose data pointer is exactly `alignment`-byte aligned.

    The MAX CPU kernels issue vmovaps (32B-required) loads directly off the
    host input, so a 16-mod-32 numpy buffer segfaults on execute. We
    over-allocate and slice to a 64B boundary, keeping the base alive so the
    view stays valid for the zero-copy handoff.
    """
    itemsize = np.dtype(dtype).itemsize
    n = int(np.prod(shape))
    buf = np.empty(n + alignment // itemsize, dtype=dtype)
    off = (-buf.ctypes.data) % alignment
    view = buf[off // itemsize : off // itemsize + n].reshape(shape)
    return view, buf
