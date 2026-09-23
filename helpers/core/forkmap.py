"""Fork-context pool map for numpy-array lanes.

The two L1 lanes (scipy_bridge, l1_betweenness) fan large-array work out
to a process pool and need THREE things the plain ``ProcessPoolExecutor``
convention does not give on Python 3.14+:

1. the ``fork`` start method, EXPLICITLY — 3.14 switched the default to
   forkserver, which re-imports worker modules per process (scipy/numpy
   boot, ~2-3 s for a 4-way pool: invisible in the LLM-bound house jobs,
   fatal to a ~3 s leg);
2. copy-on-write inheritance of the parent's arrays (fork only);
3. order preservation — results come back in chunk order, so callers
   keep contract/row order by construction.

House rule for users: the worker must be a MODULE-LEVEL callable
(partial-bound args are pickled per chunk; keep them small).
"""

from __future__ import annotations

import multiprocessing as mp
from collections.abc import Callable, Sequence
from typing import Any


def fork_map(
    worker: Callable[..., Any],
    chunks: Sequence[Any],
    jobs: int,
) -> list[Any]:
    """Map a module-level ``worker`` over ``chunks`` in a fork ``Pool``.

    Results are returned in chunk order (``Pool.map`` guarantees it).
    ``jobs`` is clamped to the chunk count; ``jobs <= 1`` or a single
    chunk runs in-process.
    """
    jobs = max(1, min(int(jobs), len(chunks)))
    if jobs <= 1 or len(chunks) <= 1:
        return [worker(c) for c in chunks]
    ctx = mp.get_context("fork")  # explicit; never the 3.14 forkserver default
    with ctx.Pool(jobs) as pool:
        return list(pool.map(worker, chunks))
