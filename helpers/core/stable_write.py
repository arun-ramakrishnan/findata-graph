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

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplaceResult:
    """Outcome of a prefix-scoped replace, computable without writing.

    ``inserted`` rows are genuinely new, ``kept`` rows matched an existing
    row's content (id/created_at preserved), ``deleted`` rows are the stale
    derived rows removed for going stale. A converged scan reports
    ``(inserted=0, kept=N, deleted=0)``; the DB churn is
    ``inserted + deleted``.
    """

    inserted: int
    kept: int
    deleted: int

    @property
    def total(self) -> int:
        """Derived rows under the prefix after the replace (kept + inserted)."""
        return self.inserted + self.kept


def _plan(
    conn: Any,
    table: str,
    prefix: str,
    cols: tuple[str, ...],
    new_rows: list[tuple],
) -> tuple[ReplaceResult, list[tuple], list[int]]:
    """Multiset-match ``new_rows`` against the table, without writing.

    Returns ``(result, to_insert, stale_ids)``; the diff path uses only the
    result, the replace executes the two write lists. Single source of truth
    so dry-run and apply always agree on the reported breakdown.
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
    result = ReplaceResult(inserted=len(to_insert), kept=kept, deleted=len(stale_ids))
    return result, to_insert, stale_ids


def stable_prefix_diff(
    conn: Any,
    table: str,
    prefix: str,
    cols: tuple[str, ...],
    new_rows: list[tuple],
) -> ReplaceResult:
    """Read-only parting of the derived table under ``prefix`` vs ``new_rows``.

    Same content multiset match as ``stable_prefix_replace``, no writes —
    the honest dry-run counterpart: a converged scan reports ``(0, N, 0)``,
    a stale one shows the rows that would turn over.
    """
    result, _, _ = _plan(conn, table, prefix, cols, new_rows)
    return result


def stable_prefix_replace(
    conn: Any,
    table: str,
    prefix: str,
    cols: tuple[str, ...],
    insert_sql: str,
    new_rows: list[tuple],
) -> ReplaceResult:
    """Prefix-scoped replace preserving id/created_at of unchanged rows.

    Hand-seeded rows outside ``prefix`` are untouched; stale derived rows
    are removed; the final derived row set is exactly ``new_rows``. Rows
    are multiset-matched on content first: unchanged rows keep their
    id AND created_at, stale rows are deleted by id, and only genuinely
    new rows are inserted.

    ``table`` must have ``id`` and ``source_ref`` columns; ``cols`` are
    the content columns compared for matching and must appear in
    ``insert_sql``'s parameter order. Returns the ``ReplaceResult``
    breakdown; ``result.total`` is the derived row count under the prefix.
    """
    result, to_insert, stale_ids = _plan(conn, table, prefix, cols, new_rows)
    if stale_ids:
        conn.executemany(
            f"DELETE FROM {table} WHERE id = ?",  # noqa: S608  # schema-constant table name
            [(i,) for i in stale_ids],
        )
    if to_insert:
        conn.executemany(insert_sql, to_insert)
    return result
