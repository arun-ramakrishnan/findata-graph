#!/usr/bin/env python3
"""Lane fan-out defaults: leave one core free for the desktop.

The box is a shared desktop (2026-11-25 operator steer: "reduce it to 2/3
and leave one free for other tasks"): lanes that fan out to every core
make the whole machine unusable for the duration and (in the perf lane)
turn wall-clock benchmarks into noise. ``default_workers`` is the single
place that resolves "how many workers for this fan-out": cores minus one,
floored at 1, optionally capped, overridable per-run via
``FINDATA_WORKER_CAP``.
"""

from __future__ import annotations

import os


def default_workers(*, floor: int = 1, cap: int | None = None) -> int:
    """Worker count for a fan-out: ``cpu_count - 1`` (one core free),
    floored at *floor*, capped by *cap* and by ``FINDATA_WORKER_CAP``.

    On the 4-core desktop this returns 3; ``FINDATA_WORKER_CAP=2`` (env)
    tightens a single run without touching code.
    """
    cores = os.cpu_count() or 1
    workers = max(floor, cores - 1)
    env_cap = os.environ.get("FINDATA_WORKER_CAP", "")
    if env_cap.isdigit() and int(env_cap) > 0:
        workers = min(workers, int(env_cap))
    if cap is not None:
        workers = min(workers, cap)
    return max(floor, workers)
