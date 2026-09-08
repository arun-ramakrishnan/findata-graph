#!/usr/bin/env python3
"""Shared CLI scaffold for the derive_* graph scripts.

derive_cited_in / derive_themes / derive_events / derive_co_mentions grew
parallel copies of the same argparse block (--apply/--verbose/--corpus/
--stale-only) and the S1c stale-gate (MAX(created_at) of the derived rows
→ notes_stale_since → skip with a labelled line). This module owns those
pieces; each script keeps its own description, corpus-load strategy,
derive body and report (S6, code_duplication_consolidation). The gate
returns ``(skip, db_max)`` instead of printing, because each script's
skip line carries its own label and unit wording — those strings are
pinned by tests and by the maint-full log reader's eye. Per-script help
strings are bundled into :class:`DeriveArgsSpec` (S1,
cli_param_bundling_doc_anchor_repair, 2026-09-09).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from helpers.core.corpus import notes_stale_since  # noqa: E402  # S1c shared stale gate


@dataclass(frozen=True)
class DeriveArgsSpec:
    """Per-script argparse strings for :func:`add_derive_args`."""

    apply_help: str
    stale_help: str
    corpus: bool = True
    verbose_help: str = "Print every edge in addition to the summary."


def add_derive_args(p: argparse.ArgumentParser, spec: DeriveArgsSpec) -> None:
    """The --apply/--verbose[/--corpus]/--stale-only argparse block."""
    p.add_argument("--apply", action="store_true", help=spec.apply_help)
    p.add_argument("--verbose", "-v", action="store_true", help=spec.verbose_help)
    if spec.corpus:
        p.add_argument(
            "--corpus",
            action="store_true",
            help="S1b: use helpers.core.corpus shared walk (maint --full) — "
            "one walk for all derivations.",
        )
    p.add_argument("--stale-only", action="store_true", help=spec.stale_help)


def stale_gate(conn, *, max_sql: str, watch_paths: Sequence) -> tuple[bool, object]:
    """S1c --stale-only gate shared by the derive scripts.

    ``max_sql`` is the scalar query for the newest derived timestamp
    (``SELECT MAX(created_at) FROM …`` — table differs per script);
    a best-effort SQL failure falls through to a full derive (the
    original per-script try/except semantics). Returns
    ``(should_skip, db_max)`` — the caller prints its own skip line
    embedding ``db_max`` and exits 0 when ``should_skip``.
    """
    try:
        db_max = conn.execute(max_sql).fetchone()[0]
    except Exception:  # noqa: BLE001 — best-effort gate; fall through to full derive
        return False, None
    if notes_stale_since(db_max, watch_paths):
        return True, db_max
    return False, db_max


def dump_edges_verbose(edges: Iterable[tuple]) -> None:
    """The ``source\\ttarget\\t{props}`` verbose dump shared by the edge
    derivers (cited_in, themes, co_mentions). Events keep their own
    event-row dump shape."""
    for row in edges:
        source, target, props = row[0], row[1], row[2]
        print(f"{source}\t{target}\t{json.dumps(props, ensure_ascii=False)}")
