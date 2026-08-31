"""Shared edge-writer for the derive-* family.

Both `derive_co_mentions.apply_edges` and `derive_themes.apply_edges` are
the same INSERT-OR-IGNORE-into-``graph_edges`` loop, differing only in
``edge_type`` and ``symmetric``. This module holds that loop once; the two
public ``apply_edges`` wrappers delegate here.

Note: `extract_relations.apply_edges` is a *different* shape (returns
``ApplyEdgesResult``, tracks FK failures + suppressed edges, cyclo 13) and
is intentionally NOT folded in — see mcp_tool_eval.txt §D.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from collections.abc import Iterable

# Bootstrap so this module is importable both as a package import and when a
# caller script runs it indirectly with only the script's dir on sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402


def apply_typed_edges(
    edges: Iterable[tuple[str, str, dict, str]],
    *,
    edge_type: str,
    symmetric: int,
    conn=None,
    dry_run: bool = True,
) -> int:
    """Insert ``(source, target, properties, source_ref)`` tuples into
    ``graph_edges`` with ``INSERT OR IGNORE``.

    Args:
        edges: Iterable of ``(source, target, properties, source_ref)``.
        edge_type: The ``graph_edges.edge_type`` value to set.
        symmetric: The ``graph_edges.symmetric`` flag (0 or 1).
        conn: Reuse an existing SQLite connection. If None, opens a fresh one.
        dry_run: If True (default), no rows are written; the function still
            counts how many would be inserted (i.e. not already present).

    Returns:
        Number of rows actually inserted (``dry_run=False``) or that would be
        inserted (``dry_run=True``). Rows skipped due to the
        ``UNIQUE(source, target, edge_type)`` constraint are not counted.

    Idempotent via the ``UNIQUE(source, target, edge_type)`` constraint, so
    re-running is safe. ``dry_run=True`` (default) counts what would be
    inserted without writing — the derive-* convention.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()

    inserted = 0
    try:
        # Bundle U3: in dry-run mode, bulk-fetch existing (source, target)
        # pairs for this edge_type ONCE instead of per-edge SELECT (was N
        # round-trips; now 1). Checked in-memory during the loop.
        existing: set[tuple[str, str]] | None = None
        if dry_run:
            existing = {
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT source, target FROM graph_edges "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                    f"WHERE edge_type = '{edge_type}'"
                ).fetchall()
            }
        # Bundle U2: wrap the loop in `with conn:` for atomic commit/rollback.
        with conn:
            for source, target, props, source_ref in edges:
                if dry_run:
                    if (source, target) not in (existing or set()):
                        inserted += 1
                    continue
                props_json = json.dumps(props, ensure_ascii=False, sort_keys=True)
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO graph_edges
                        (source, target, edge_type, properties, source_ref, symmetric)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (source, target, edge_type, props_json, source_ref, symmetric),
                )
                inserted += cur.rowcount
    finally:
        if own_conn:
            conn.close()
    return inserted
