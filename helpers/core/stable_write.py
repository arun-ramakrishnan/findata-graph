#!/usr/bin/env python3
"""Prefix-scoped stable replace for derived tables.

Shared writer primitive (extracted from derive_insights 2026-08-22) used
by the derive-* CLI family: quotes / company_metrics (derive_insights)
and events (derive_events). Semantically equivalent to
DELETE-prefix-then-INSERT-all, but rows are multiset-matched on content
first so a no-op derive cycle keeps every unchanged row's id and
created_at — the snapshot blobs change only when content actually changes
(the embed_cache stable-write pattern).
"""
from __future__ import annotations

from typing import Any


def stable_prefix_replace(conn: Any, table: str, prefix: str,
                          cols: tuple[str, ...], insert_sql: str,
                          new_rows: list[tuple]) -> int:
    """Prefix-scoped replace preserving id/created_at of unchanged rows.

    Hand-seeded rows outside ``prefix`` are untouched; stale derived rows
    are removed; the final derived row set is exactly ``new_rows``. Rows
    are multiset-matched on content first: unchanged rows keep their
    id AND created_at, stale rows are deleted by id, and only genuinely
    new rows are inserted.

    ``table`` must have ``id`` and ``source_ref`` columns; ``cols`` are
    the content columns compared for matching and must appear in
    ``insert_sql``'s parameter order. Returns the number of derived rows
    in the table after the call.
    """
    collist = ", ".join(cols)
    rows = conn.execute(
        f"SELECT id, {collist} FROM {table} WHERE source_ref LIKE ?",  # noqa: S608  # schema-constant identifiers; prefix is a ? bind
        (prefix + "%",),
    ).fetchall()
    pool: dict[tuple, list[int]] = {}
    for r in rows:
        pool.setdefault(tuple(r[1:]), []).append(r[0])
    to_insert: list[tuple] = []
    kept = 0
    for content in new_rows:
        ids = pool.get(content)
        if ids:
            ids.pop()
            kept += 1
        else:
            to_insert.append(content)
    stale_ids = [i for ids in pool.values() for i in ids]
    if stale_ids:
        conn.executemany(f"DELETE FROM {table} WHERE id = ?",  # noqa: S608  # schema-constant table name
                         [(i,) for i in stale_ids])
    if to_insert:
        conn.executemany(insert_sql, to_insert)
    return kept + len(to_insert)
