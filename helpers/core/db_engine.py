#!/usr/bin/env python3
"""Engine-agnostic connection funnel (duckdb_research_swap_steps.md Step 5).

Routes by file extension:

- ``*.duckdb`` → DuckDB via ``duckdb_shared.connect_shared`` (quack wire
  when the store is served, plain file mode otherwise).
- everything else (``*.db``, ``*.sqlite``, bare paths) → SQLite via the
  existing ``helpers.core.db.connect`` unchanged.

This module is a ROUTER, not a semantic shim: DuckDB-specific behavior is
never hidden behind it (proposal §5 risk line) — the funnel only makes the
*connection* and *introspection* surfaces common so a lane can be pointed
at either engine without rewriting its plumbing.

Introspection shims map the §3.2 PRAGMA disposition (reentry doc,
114 sites at filing; today's live census: 150 sites) to
``information_schema`` / meta-table equivalents:

- ``table_info``  (×25 → information_schema.columns here)
- ``column_names`` (derived from table_info)
- ``schema_version`` (×10 user_version → ``db_meta.schema_version`` meta
  key on BOTH engines — the canonical version source for research.db)

Not provided yet (consumer-sweep items, phase 5 of the reentry):
``foreign_keys``/``defer_foreign_keys`` (write-reorder work),
``foreign_key_check``/``integrity_check``/``quick_check`` (first-party
checker — ``helpers/misc/swap_fk_integrity.py`` already exists for the
DuckDB side), ``optimize``/``journal_mode``/… (no DuckDB analog; DELETE).

Row access: SQLite connections keep their ``sqlite3.Row`` factory
(default). DuckDB rows are plain tuples, so when a ``row_factory`` is
requested (default, like ``helpers.core.db.connect``) the funnel returns a
thin ``_DuckDBConn`` proxy whose rows also support ``row["name"]`` access —
matching the sqlite.Row surface. Pass ``row_factory=None`` to get the raw
DuckDB connection (tuple rows, zero overhead).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from helpers.core.db import DEFAULT_DB_PATH, connect as _sqlite_connect
from helpers.core.duckdb_shared import connect_shared as _duckdb_connect_shared

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DuckDBRow(tuple):
    """Tuple subclass that also accepts column-name indexing (sqlite.Row
    surface). ``_cols`` holds the column names for the enclosing result.
    (No ``__slots__``: tuple subclasses cannot have non-empty slots.)"""

    _cols: tuple[str, ...]

    def __new__(cls, values: tuple, cols: tuple[str, ...]) -> DuckDBRow:
        obj = super().__new__(cls, values)
        obj._cols = cols
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                idx = self._cols.index(key)
            except ValueError:
                raise KeyError(key) from None
            return super().__getitem__(idx)
        return super().__getitem__(key)

    def keys(self) -> tuple[str, ...]:
        return self._cols


class _DuckDBCursor:
    """Cursor proxy wrapping a raw DuckDB cursor: wraps result rows in
    ``DuckDBRow`` so name-indexing works; preserves tuple access."""

    def __init__(self, raw_cursor: Any, cols: tuple[str, ...]) -> None:
        self._raw = raw_cursor
        self._cols = cols

    def fetchone(self):
        row = self._raw.fetchone()
        return DuckDBRow(row, self._cols) if row is not None else None

    def fetchall(self) -> list[DuckDBRow]:
        return [DuckDBRow(r, self._cols) for r in self._raw.fetchall()]

    def __iter__(self):
        for row in self._raw:
            yield DuckDBRow(row, self._cols)


class _DuckDBConn:
    """Connection proxy: raw DuckDB connection + named-row cursors. Only
    the surface Step 5 consumers need is implemented; everything else is
    reachable through ``.raw`` if a caller ever needs the native object."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def execute(self, sql: str, parameters: Any = ()) -> _DuckDBCursor:
        cur = self.raw.execute(sql, parameters)
        cols = tuple(d[0] for d in self.raw.description) if self.raw.description else ()
        return _DuckDBCursor(cur, cols)

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> _DuckDBConn:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def connect_funnel(
    db_path: Path | str | None = None,
    *,
    row_factory=sqlite3.Row,
    enable_fk: bool = True,
    wal: bool = True,
    read_only: bool = False,
):
    """Open a connection to ``db_path`` routing by file extension.

    ``*.duckdb`` → DuckDB (quack wire if served, else file mode);
    anything else → SQLite via ``helpers.core.db.connect`` unchanged.

    Args (mirror ``helpers.core.db.connect``):
        db_path: Path to the store. Defaults to ``memory/research.db``
            (SQLite). A ``.duckdb`` suffix selects the DuckDB branch.
        row_factory: SQLite: set on the connection as today. DuckDB:
            ``None`` returns the raw connection (tuple rows); otherwise a
            ``_DuckDBConn`` proxy with name-indexable rows is returned.
        enable_fk / wal: SQLite branch only (DuckDB has no per-connection
            FK toggle; journaling is engine-managed).
        read_only: Passed to both branches. DuckDB ``read_only=True`` is
            strict (writes raise); SQLite uses the ``mode=ro`` URI.

    Returns:
        SQLite: ``sqlite3.Connection`` (exactly as today).
        DuckDB: raw ``duckdb.DuckDBPyConnection`` when ``row_factory`` is
        None, else the ``_DuckDBConn`` proxy.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if path.suffix.lower() == ".duckdb":
        raw = _duckdb_connect_shared(path, read_only=read_only)
        if row_factory is None:
            return raw
        return _DuckDBConn(raw)
    return _sqlite_connect(
        path,
        row_factory=row_factory,
        enable_fk=enable_fk,
        wal=wal,
        read_only=read_only,
    )


def is_duckdb_conn(conn: Any) -> bool:
    """True when ``conn`` is a DuckDB connection (raw or funnel proxy)."""
    if isinstance(conn, _DuckDBConn):
        return True
    return type(conn).__module__ == "duckdb"


def _safe_ident(name: str) -> str:
    """Validate a table/column name before interpolating into a PRAGMA /
    information_schema query (PRAGMA args cannot be bound)."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid identifier for introspection: {name!r}")
    return name


def table_info(conn: Any, table: str) -> list[dict[str, Any]]:
    """Engine-agnostic ``PRAGMA table_info`` equivalent.

    Returns a list of dicts keyed ``cid``/``name``/``type``/``notnull``/
    ``dflt_value``/``pk`` (sqlite's table_info shape).

    - SQLite: ``PRAGMA table_info(<ident>)`` verbatim.
    - DuckDB: ``information_schema.columns`` (ordinal_position-1 → cid;
      is_nullable → notnull; column_default → dflt_value; pk is not
      exposed by information_schema — reported as 0).
    """
    ident = _safe_ident(table)
    if is_duckdb_conn(conn):
        rows = conn.execute(
            "SELECT ordinal_position - 1 AS cid, column_name AS name, "
            "data_type AS type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = ? "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [
            {
                "cid": int(r[0]),
                "name": r[1],
                "type": r[2],
                "notnull": 1 if r[3] == "NO" else 0,
                "dflt_value": r[4],
                "pk": 0,  # not exposed by information_schema
            }
            for r in rows
        ]
    return [dict(r) for r in conn.execute(f"PRAGMA table_info({ident})").fetchall()]


def column_names(conn: Any, table: str) -> list[str]:
    """Column names of ``table`` in ordinal order (both engines)."""
    return [c["name"] for c in table_info(conn, table)]


def schema_version(conn: Any) -> int | None:
    """Version ``db_meta.schema_version`` (canonical version source).

    Works on both engines; returns None when the meta table is absent.
    This is the Step 5 replacement for the SQLite-only
    ``PRAGMA user_version`` surface (reentry §3.2 disposition: user_version
    ×10 → meta table).
    """
    try:
        row = conn.execute("SELECT value FROM db_meta WHERE key = 'schema_version'").fetchone()
    except Exception:  # noqa: BLE001 — table absent on either engine
        return None
    if row is None or row[0] is None:
        return None
    try:
        return int(row[0])
    except TypeError, ValueError:
        return None
