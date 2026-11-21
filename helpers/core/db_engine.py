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

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from helpers.core.db import DEFAULT_DB_PATH, _sqlite_connect  # noqa: PLC0415  # private house sqlite opener (S1)
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
        # duckdb 2.0: connection.execute() returns the connection, which is
        # not iterable — iterate the materialized rows instead.
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        """sqlite3.Cursor-compatible rowcount.

        duckdb's execute() returns the connection (not a cursor); its
        ``rowcount`` reflects the LAST statement run on that connection,
        so read it immediately (callers do, e.g. the INSERT OR IGNORE
        new-edge/new-incidence accounting in derive_hyperedges)."""
        return int(self._raw.rowcount)


class _DuckDBConnCursor:
    """sqlite3.Cursor-shaped handle over the shared DuckDB connection.

    DuckDB has no cursor objects, but app.py readers call ``conn.cursor()``
    then reuse the cursor for successive ``execute`` calls; this shim keeps
    that pattern working (``fetchone``/``fetchall``/iteration off the last
    executed statement).
    """

    def __init__(self, conn: _DuckDBConn) -> None:
        self._conn = conn
        self._last: _DuckDBCursor | None = None
        # sqlite3.Cursor surface: 7-tuples whose [0] is the column name
        # (consumers do ``[d[0] for d in cur.description]``), None before
        # the first execute — same contract as sqlite3.
        self.description: tuple[tuple, ...] | None = None

    def execute(self, sql: str, parameters: Any = ()) -> _DuckDBConnCursor:
        self._last = self._conn.execute(sql, parameters)
        cols = self._last._cols if self._last is not None else ()
        self.description = (
            tuple((c, None, None, None, None, None, None) for c in cols) if cols else None
        )
        return self

    def executemany(self, sql: str, parameters: Any = ()) -> _DuckDBConnCursor:
        """sqlite3.Cursor-compatible executemany (app writers use it on
        the shared connection cursor)."""
        self._last = self._conn.executemany(sql, parameters)
        cols = self._last._cols if self._last is not None else ()
        self.description = (
            tuple((c, None, None, None, None, None, None) for c in cols) if cols else None
        )
        return self

    @property
    def rowcount(self) -> int:
        """sqlite3.Cursor-compatible rowcount (last executed statement)."""
        return self._last.rowcount if self._last is not None else -1

    def fetchone(self):
        return self._last.fetchone() if self._last is not None else None

    def fetchall(self) -> list[DuckDBRow]:
        return self._last.fetchall() if self._last is not None else []

    def __iter__(self):
        return iter(self.fetchall())


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

    def executemany(self, sql: str, parameters: Any = ()) -> _DuckDBCursor:
        """sqlite3.Connection-compatible executemany (bulk writers — the
        research-store writer lanes use it on the canonical DuckDB conn)."""
        cur = self.raw.executemany(sql, parameters)
        cols = tuple(d[0] for d in self.raw.description) if self.raw.description else ()
        return _DuckDBCursor(cur, cols)

    def executescript(self, sql: str) -> _DuckDBConn:
        """sqlite3.Connection-compatible executescript: run a multi-statement
        script. DuckDB executes semicolon-separated statements in one call;
        the script runs in the current transaction (sqlite3's executescript
        commits first — callers here use it for idempotent DDL, so the
        difference is invisible for the canonical research-store writers)."""
        self.raw.execute(sql)
        return self

    def cursor(self) -> _DuckDBConnCursor:
        """Return a sqlite3.Cursor-shaped handle over this connection."""
        return _DuckDBConnCursor(self)

    def commit(self) -> None:
        """sqlite3.Connection-compatible commit (DuckDB auto-commits; the
        call is a no-op that keeps writer helpers engine-neutral)."""
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> _DuckDBConn:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # sqlite3.Connection parity: a `with conn:` block commits on clean
        # exit (rolls back on exception) and leaves the connection OPEN —
        # callers routinely keep using the conn after the block. Closing
        # here would silently kill every later statement (observed:
        # derive_hyperedges S0 prune loop → "Connection already closed!").
        if exc_type is None:
            self.commit()
        else:
            try:
                self.raw.rollback()
            except Exception:  # noqa: S110
                pass


class ResearchEngine:
    """Engine selector for the research store.

    ``FINDATA_RESEARCH_ENGINE`` — default ``duckdb`` since the S6 cutover
    (2026-11-21, operator order: nothing resolves to the sqlite default
    anymore):

    - ``duckdb`` / ``auto`` / unset — canonical-first: research-store paths
      route through the funnel; a bare research-store ``.db`` resolves to
      its ``.duckdb`` sibling when it exists (SQLite fallback when the
      sibling is absent — a fresh clone or a rollback box without the
      converted store). A pathless call honors ``FINDATA_RESEARCH_DB_PATH``.
    - ``sqlite`` — explicit rollback: connect SQLite paths directly via the
      unchanged ``helpers.core.db.connect`` (no sibling redirect for the
      research store). ``.duckdb`` paths still route to DuckDB by
      extension (the historical funnel behavior).
    - Unknown values degrade to the sqlite branch — a selector typo reads
      the rollback store, not the wrong canonical.
    """

    SQLITE = "sqlite"
    AUTO = "auto"
    DUCKDB = "duckdb"

    def __init__(self, value: str | None) -> None:
        self.name = (value or self.DUCKDB).strip().lower() or self.DUCKDB

    @classmethod
    def from_env(cls) -> ResearchEngine:
        return cls(os.environ.get("FINDATA_RESEARCH_ENGINE"))

    @property
    def opt_in(self) -> bool:
        return self.name in (self.AUTO, self.DUCKDB)

    def resolve_path(self, db_path: Path | str | None) -> Path:
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        if db_path is None and self.name == self.DUCKDB:
            # No explicit path (app.py readers et al.): let the operator
            # point the selector at a converted artifact; the default path
            # itself still resolves through the gated sibling redirect
            # below.
            override = os.environ.get("FINDATA_RESEARCH_DB_PATH")
            if override:
                path = Path(override)
        if (
            self.name == self.DUCKDB
            and path.suffix.lower() != ".duckdb"
            and is_research_store_path(db_path)
        ):
            # Sibling redirect, gated to the research store (the S6 hijack
            # law now holds at the funnel itself, not only in
            # research_store_connect): a fixture ``research.db`` whose
            # sibling ``.duckdb`` is another lane's cache must never be
            # hijacked onto it, duckdb-first default or not.
            duck = path.with_suffix(".duckdb")
            if duck.exists():
                return duck
        return path


def is_research_store_path(db_path: Path | str | None) -> bool:
    """True for the production research store (default or selector override).

    The S6 sibling redirect inside :meth:`ResearchEngine.resolve_path` (a
    bare ``.db`` → its converted ``.duckdb``) is designed for THESE paths.
    Callers that accept arbitrary ``.db`` paths (csr ``--db``, the graph
    lane's fixture stores) must gate the redirect on this — a tmp fixture
    named ``research.db`` whose sibling ``.duckdb`` is some other lane's
    cache (the graph-rebuild shim shape) must not be hijacked onto it.
    """
    override = os.environ.get("FINDATA_RESEARCH_DB_PATH")
    canonical = [DEFAULT_DB_PATH]
    if override:
        canonical.append(Path(override))
    try:
        target = Path(db_path).resolve() if db_path is not None else DEFAULT_DB_PATH.resolve()
        return target in {c.resolve() for c in canonical}
    except OSError:  # unresolvable path — fall back to literal comparison
        return str(db_path) in {str(c) for c in canonical}


def research_store_connect(db_path: Path | str | None, **kw):
    """Open a store through the funnel with the sibling redirect gated to
    the research store only (see :func:`is_research_store_path`).

    The selector's engine still decides for ``.duckdb`` paths and for the
    research store itself; any other ``.db`` path is pinned to SQLite so
    its sibling (frequently the calling lane's own cache) is never
    mistaken for a converted store. ``engine=`` in ``kw`` overrides the
    pin entirely, as with :func:`connect_funnel`.
    """
    sel = ResearchEngine.from_env()
    if (
        sel.opt_in
        and not str(Path(db_path) if db_path is not None else DEFAULT_DB_PATH)
        .lower()
        .endswith(".duckdb")
        and not is_research_store_path(db_path)
    ):
        sel = ResearchEngine(ResearchEngine.SQLITE)
    return connect_funnel(db_path, engine=sel, **kw)


def connect_funnel(
    db_path: Path | str | None = None,
    *,
    row_factory=sqlite3.Row,
    enable_fk: bool = True,
    wal: bool = True,
    read_only: bool = False,
    engine: ResearchEngine | str | None = None,
):
    """Open a connection to ``db_path`` routing by engine + file extension.

    The ``FINDATA_RESEARCH_ENGINE`` selector — default ``duckdb`` since the
    S6 cutover, ``sqlite`` the explicit rollback — decides the engine for
    research-store paths; ``engine=`` overrides the env var for a single
    call. DuckDB paths always route to DuckDB. The SQLite branch delegates
    to ``helpers.core.db.connect`` unchanged.

    Args (mirror ``helpers.core.db.connect``):
        db_path: Path to the store. Defaults to ``memory/research.db``.
        engine: ``ResearchEngine`` (or raw string) override for the opt-in
            selector; ``None`` reads ``FINDATA_RESEARCH_ENGINE`` from env.
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
    if isinstance(engine, ResearchEngine):
        sel = engine
    elif isinstance(engine, str):
        sel = ResearchEngine(engine)
    else:
        sel = ResearchEngine.from_env()
    path = sel.resolve_path(db_path)
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
    """True when ``conn`` is a DuckDB connection (raw or funnel proxy).

    duckdb 2.0 raw connections report ``type(conn).__module__ == '_duckdb'``
    (older builds use ``'duckdb'``) — both are accepted. Importing duckdb
    just to run ``isinstance`` is deliberately avoided (this module is
    imported by SQLite-only consumers that may not have duckdb installed).
    """
    if isinstance(conn, (_DuckDBConn, _DuckDBConnCursor)):
        return True
    return type(conn).__module__ in ("duckdb", "_duckdb")


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
    # Positional projection, not dict(r): the conn may be row_factory=None
    # (csr.py and every raw-tuple lane) — dict(tuple_row) raises
    # TypeError. PRAGMA table_info columns are cid/name/type/notnull/
    # dflt_value/pk in that order on both sqlite3.Row and bare tuples.
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": r[3],
            "dflt_value": r[4],
            "pk": r[5],
        }
        for r in conn.execute(f"PRAGMA table_info({ident})").fetchall()
    ]


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


def table_exists(conn: Any, name: str, *, kind: str = "table") -> bool:
    """Engine-agnostic ``sqlite_master`` existence probe.

    kind: ``table`` (base tables only), ``view``, or ``any`` (table or
    view). SQLite probes ``sqlite_master`` verbatim (same semantics as the
    historical ``SELECT 1 FROM sqlite_master WHERE name=? AND type=?``);
    DuckDB reads ``information_schema`` (DuckDB has no sqlite_master — the
    converted store drops it with the rest of the engine internals).
    """
    _safe_ident(name)  # validation only — both branches bind, never interpolate
    if kind not in ("table", "view", "any"):
        raise ValueError(f"kind must be table|view|any, got {kind!r}")
    if is_duckdb_conn(conn):
        if kind == "table":
            where = "table_type = 'BASE TABLE'"
        elif kind == "view":
            where = "table_type = 'VIEW'"
        else:
            where = "table_type IN ('BASE TABLE', 'VIEW')"
        row = conn.execute(
            f"SELECT 1 FROM information_schema.tables WHERE table_name = ? AND {where}",  # noqa: S608  # {where} is a schema-constant literal (table/view branch)
            (name,),
        ).fetchone()
        return row is not None
    if kind == "table":
        cond = "type='table'"
    elif kind == "view":
        cond = "type='view'"
    else:
        cond = "type IN ('table','view')"
    row = conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE name = ? AND {cond}",  # noqa: S608  # cond is a fixed literal above
        (name,),
    ).fetchone()
    return row is not None


def foreign_key_check(conn: Any) -> list[tuple]:
    """Engine-agnostic FK-orphan scan (the ``PRAGMA foreign_key_check``
    replacement; reentry §3.2 disposition).

    Returns a list of violations — empty means clean. Consumers only
    ``len()`` it today, but the shapes are engine-honest:

    - SQLite: ``PRAGMA foreign_key_check`` rows verbatim
      ``(table, rowid, parent, fkid)`` — declared-FK enforcement scan.
    - DuckDB: one tuple per FK constraint that has orphans
      ``(child_table, constraint_name, parent_table, orphan_count)`` —
      built from ``information_schema.referential_constraints`` +
      ``key_column_usage`` (DuckDB has no PRAGMA foreign_key_check), a
      first-party LEFT JOIN per constraint.
    """
    if not is_duckdb_conn(conn):
        return [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
    # DuckDB: generic scan over the declared referential constraints.
    try:
        constraints = conn.execute(
            "SELECT constraint_name, unique_constraint_name "
            "FROM information_schema.referential_constraints ORDER BY constraint_name"
        ).fetchall()
    except Exception:  # noqa: BLE001 — no constraints visible → nothing to scan
        return []
    violations: list[tuple] = []
    for cname, ucname in constraints:
        if ucname is None:
            continue
        child = conn.execute(
            "SELECT table_name FROM information_schema.key_column_usage "
            "WHERE constraint_name = ? AND position_in_unique_constraint IS NULL",
            (cname,),
        ).fetchone()
        parent = conn.execute(
            "SELECT table_name FROM information_schema.key_column_usage "
            "WHERE constraint_name = ? AND position_in_unique_constraint IS NOT NULL",
            (ucname,),
        ).fetchone()
        if child is None or parent is None:
            continue
        child_cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE constraint_name = ? AND position_in_unique_constraint IS NULL "
                "ORDER BY ordinal_position",
                (cname,),
            ).fetchall()
        ]
        parent_cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE constraint_name = ? AND position_in_unique_constraint IS NOT NULL "
                "ORDER BY ordinal_position",
                (ucname,),
            ).fetchall()
        ]
        if not child_cols or len(child_cols) != len(parent_cols):
            continue
        child_ident = _safe_ident(child[0])
        parent_ident = _safe_ident(parent[0])
        on = " AND ".join(
            f"c.{_safe_ident(cc)} = p.{_safe_ident(pc)}" for cc, pc in zip(child_cols, parent_cols)
        )
        where = " AND ".join(f"p.{_safe_ident(pc)} IS NULL" for pc in parent_cols)
        orphans = conn.execute(
            f"SELECT COUNT(*) FROM {child_ident} c LEFT JOIN {parent_ident} p ON {on} WHERE {where}"  # noqa: S608  # identifiers ride _safe_ident(); {where}/{on} are built from _safe_ident()
        ).fetchone()[0]
        if orphans:
            violations.append((child[0], cname, parent[0], int(orphans)))
    return violations


def quick_check(conn: Any) -> list[str]:
    """Engine-agnostic store-sanity check (the ``PRAGMA quick_check`` /
    ``integrity_check`` replacement; reentry §3.2 disposition).

    - SQLite: ``PRAGMA quick_check`` verbatim (``['ok']`` when clean).
    - DuckDB: first-party structural scan — every base table and view is
      count-probed (unreadable/corrupt structures raise into the issue
      list) and the FK surface is scanned via :func:`foreign_key_check`.
      Returns ``['ok']`` when clean, mirroring the SQLite shape.
    """
    if not is_duckdb_conn(conn):
        return [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]
    issues: list[str] = []
    tables = conn.execute(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    for name, _ttype in tables:
        try:
            conn.execute(f"SELECT COUNT(*) FROM {_safe_ident(name)}").fetchone()  # noqa: S608  # {name} rides _safe_ident()
        except Exception as e:  # noqa: BLE001 — each probe is independent evidence
            issues.append(f"{name}: unreadable ({type(e).__name__}: {str(e)[:120]})")
    fk = foreign_key_check(conn)
    for child, cname, parent, n in fk:
        issues.append(f"foreign key {cname}: {n} orphan row(s) in {child} -> {parent}")
    return issues or ["ok"]
