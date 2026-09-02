#!/usr/bin/env python3
"""Worker for `extract_relations` ProcessPool — always importable.

`extract_relations.py` is often run as `__main__` (`python3 helpers/...py`), so
`_extract_batch` defined there pickles as `__main__._extract_batch_arg` and
ForkServer children fail `AttributeError`. This module is *never* `__main__`,
so `helpers.graph._extract_worker._extract_batch_arg` is stable for pickle
whether the parent is `__main__` file or `-m` package.

Thin shim: imports the real implementation from `extract_relations` at child
startup (cheap) and delegates — avoids duplicating regex tables.
"""

from __future__ import annotations


# Reuse the real implementation — import at call time to avoid circular at import.
def _extract_batch(file_paths: list[str], entity_names: list[str]):  # type: ignore[no-untyped-def]
    # Local import so this module stays importable without side-effects on load.
    from helpers.graph.extract_relations import _extract_batch as _real

    return _real(file_paths, entity_names)


def _extract_batch_arg(args: tuple[list[str], list[str]]):  # type: ignore[no-untyped-def]
    return _extract_batch(args[0], args[1])
