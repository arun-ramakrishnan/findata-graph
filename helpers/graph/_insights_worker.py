#!/usr/bin/env python3
"""Worker for `derive_insights` ProcessPool — always importable.

`derive_insights.py` is often run as `__main__`
(`python3 helpers/graph/derive_insights.py ...`), so `_scan_one_file`
defined there would pickle as `__main__._scan_one_file` and ForkServer
children would fail `AttributeError`. This module is *never* `__main__`,
so `helpers.graph._insights_worker._scan_chunk_arg` is stable for pickle
whether the parent is a `__main__` file or a `-m` package.

Payloads and results are plain types only (str / dict / list): the child
returns `dataclasses.asdict` dicts and the parent rebuilds
`Quote(**d)` / `Metric(**d)`, so cross-process class identity between the
parent's `__main__.Quote` and the child's
`helpers.graph.derive_insights.Quote` never matters.

Thin shim: imports the real implementation from `derive_insights` at
child startup (cheap) and delegates — avoids duplicating regex tables.
"""

from __future__ import annotations


def _scan_chunk(file_paths: list[str], resolver_map: dict):  # type: ignore[no-untyped-def]
    """Scan one stride-chunk of newsletter files.

    Return ``[(path_str, quote_dicts, metric_dicts), ...]`` in input
    order — the parent re-interleaves chunks into path order so pool
    output is identical to serial down to row order.
    """
    # Local imports so this module stays importable without side-effects on load.
    from dataclasses import asdict
    from pathlib import Path

    from helpers.graph.derive_insights import _scan_one_file

    out: list[tuple[str, list[dict], list[dict]]] = []
    for fp in file_paths:
        q_batch, m_batch = _scan_one_file(Path(fp), resolver_map)
        out.append((fp, [asdict(q) for q in q_batch], [asdict(m) for m in m_batch]))
    return out


def _scan_chunk_arg(args: tuple[list[str], dict]):  # type: ignore[no-untyped-def]
    """Single-argument wrapper for ProcessPoolExecutor.map (takes a tuple)."""
    return _scan_chunk(args[0], args[1])
