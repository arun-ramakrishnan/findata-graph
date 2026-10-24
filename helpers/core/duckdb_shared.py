#!/usr/bin/env python3
"""Shared-store connection routing for duckdb 2.0 quack mode
(duckdb_20_quack Slice B — doc/improvements/archive/database/duckdb_20_quack.md #428).

Each resident quack server (started via `make quack-start`) holds ONE
store open read-write and serves it over the quack wire on its own
port; the discovery file (`memory/.quack.json`) is the single
coordination point — a JSON object keyed by alias, one entry per
server: {alias: {uri, token, pid, path, started_at}}. The one-db-per-
server shape is forced by the wire protocol: quack_serve exposes ONLY
the primary database to clients (measured 2026-10-22 — a second
server-side ATTACH was invisible over the wire, and the client's
attach alias is a local label, so a shared server silently routes
every client to its primary).

- `connect_shared(path, read_only=True)` — readers attach over the
  wire when the store is served, else plain file connect. Same rows,
  same SQL; unqualified names resolve remotely after the internal
  `USE`. `read_only=False` also routes over the wire when served (the
  server serializes writers); unserved it is a plain rw file connect.
- `assert_not_served(path)` — swap-replace builders call this BEFORE
  building: a served store must never be os.replace'd in file mode, or
  the server keeps serving a stale inode (the swap replaces the
  directory entry, not the server's open handle).
- `sql_lit(v)` — literal-SQL emission. Prepared-statement parameters
  do not cross the quack wire (measured 2026-10-22), so wire writes
  must inline literals; this is the one audited escaping point.

Default-off: with no live discovery entries every helper is an
identity passthrough and existing file-mode behaviour is unchanged.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from pathlib import Path
from typing import Any, cast

import duckdb

DEFAULT_DISCOVERY = Path(__file__).resolve().parents[2] / "memory" / ".quack.json"


def discovery_path() -> Path:
    """Discovery file location (env-overridable for tests)."""
    return Path(os.environ.get("QUACK_DISCOVERY", str(DEFAULT_DISCOVERY)))


def _pid_alive(pid: object) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours — still a live foreign server
    except OSError:
        return False
    return True


def load_discovery(p: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Parse + liveness-check the discovery file: {alias: entry} with
    entry {uri, token, pid, path}. Dead servers are dropped (their
    entries are stale state, not routes); a wholly unreadable file
    yields {}. Malformed legacy single-server files return {} too —
    callers then fall back to file mode, and the next `quack-start`
    rewrites the schema."""
    path = Path(p) if isinstance(p, str) else (p or discovery_path())
    try:
        disc = json.loads(path.read_text())
    except OSError, json.JSONDecodeError:
        return {}
    if not isinstance(disc, dict):
        return {}
    return {
        str(alias): dict(entry)
        for alias, entry in disc.items()
        if isinstance(entry, dict)
        and "uri" in entry
        and "pid" in entry
        and _pid_alive(entry.get("pid"))
    }


def read_discovery_locked(
    p: Path | str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Open the discovery file under an exclusive fcntl lock and return
    (fd, parsed-json). The fd stays locked — call
    `write_discovery_locked(fd, obj)` to replace the contents and
    release, or just close(fd) to release without writing. Callers are
    the per-store server children (one read-modify-write each) and the
    start/stop wrappers."""
    path = Path(p) if isinstance(p, str) else (p or discovery_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        data = json.loads(os.read(fd, 1 << 20) or b"{}")
    except json.JSONDecodeError:
        data = {}
    return fd, data if isinstance(data, dict) else {}


def write_discovery_locked(fd: int, obj: dict[str, Any]) -> None:
    """Replace the discovery file contents (fd held locked by
    read_discovery_locked) and release the lock."""
    payload = json.dumps(obj, indent=1, sort_keys=True)
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, payload.encode())
    os.fsync(fd)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _entries(disc: dict[str, Any]) -> list[dict[str, Any]]:
    """Valid entries with a resolved path, any alias order."""
    out: list[dict[str, Any]] = []
    for alias, entry in disc.items():
        path = str(entry.get("path", ""))
        if not path:
            continue
        entry = dict(entry)
        entry.setdefault("alias", alias)
        entry["resolved"] = str(Path(path).resolve())
        out.append(entry)
    return out


def served_entry(disc: dict[str, Any], path: Path | str) -> dict[str, Any] | None:
    """The discovery entry whose store is `path`, if any."""
    want = str(Path(path).resolve())
    for entry in _entries(disc):
        if entry["resolved"] == want:
            return entry
    return None


def _attach_wire(entry: dict[str, Any], alias: str) -> duckdb.DuckDBPyConnection:
    """Fresh in-memory client shell with the served store attached as
    `alias` and made active (unqualified SQL resolves remotely — the
    `USE rdb` semantics validated 2026-10-21). CREATE SECRET/ATTACH are
    client-side statements; literals via `sql_lit` keep one escaping
    discipline for everything that could ever touch the wire.

    The shell is wrapped in `WireConnection`: the extension drops bound
    parameters on table-reading queries, so parameters are inlined as
    literals before they reach the wire (see `inline_params`)."""
    con = duckdb.connect()
    con.execute(
        f"CREATE SECRET (TYPE quack, TOKEN {sql_lit(entry['token'])}, SCOPE {sql_lit(entry['uri'])})"
    )
    con.execute(f'ATTACH {sql_lit(entry["uri"])} AS "{alias}" (TYPE quack)')
    con.execute(f'USE "{alias}"')
    return cast("duckdb.DuckDBPyConnection", WireConnection(con))


def connect_shared(
    path: Path | str,
    read_only: bool = True,
    *,
    discovery: Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Connect to a duckdb store the shared way: over the quack wire
    when a live server serves it, else plain file mode.

    Both modes route. `read_only=True` attaches read-capable; when the
    store is served, `read_only=False` also attaches over the wire —
    the server serializes writers, so a small-batched writer lane
    (harvest apply, watermark upserts) is exactly the wire's win case.
    What must NEVER go over this helper is a swap-replace build: use
    `assert_not_served` there (os.replace swaps the directory entry,
    not the server's open handle — §2.6 split-brain rule).
    """
    p = Path(path)
    disc = load_discovery(discovery)
    if disc:
        entry = served_entry(disc, p)
        if entry is not None:
            return _attach_wire(entry, str(entry.get("alias") or p.stem))
    return duckdb.connect(str(p), read_only=read_only, **unsigned_ext_kwargs())


def unsigned_ext_kwargs() -> dict[str, Any]:
    """``duckdb.connect`` kwargs permitting locally-built (unsigned)
    extensions — the vendored onager build (vendor/onager/,
    helpers/graph/onager.py) is the resident case. duckdb 2.0 locks
    ``allow_unsigned_extensions`` once a database is open, so this rides
    the connect config; harmless where no extension ever loads.
    """
    return {"config": {"allow_unsigned_extensions": True}}


def is_served(path: Path | str, *, discovery: Path | None = None) -> bool:
    """True when a live discovery entry maps `path` to a server.
    Read lanes use this to skip file-mode io-lock queueing that would
    be pointless (and deadlock-adjacent) against a server that does not
    participate in the flock."""
    disc = load_discovery(discovery)
    return bool(disc) and served_entry(disc, path) is not None


def assert_not_served(path: Path | str, *, discovery: Path | None = None) -> None:
    """Refuse file-mode writes on a served store — the split-brain
    guard (§2.6: mixed file+server access is impossible by lock, and a
    swap-replace would leave the server serving a stale inode). Raises
    RuntimeError pointing at the remediation; no-op when unserved."""
    p = Path(path)
    disc = load_discovery(discovery)
    if not disc:
        return
    entry = served_entry(disc, p)
    if entry is None:
        return
    raise RuntimeError(
        f"{p} is served by the quack server (pid {entry.get('pid')}, uri "
        f"{entry.get('uri')}) — file-mode writes would split-brain the store. "
        "Stop it first (`make quack-stop`) or route the write over the wire."
    )


def _skip_quoted(sql: str, i: int, quote: str) -> int:
    """Index just past the quoted run starting at `i` (quote is ' or ").
    Doubled quotes inside the run are literal content, not terminators."""
    n = len(sql)
    j = i + 1
    while j < n:
        if sql[j] == quote:
            if j + 1 < n and sql[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def _scan_placeholders(sql: str) -> list[tuple[int, int, str]]:
    """Spans of parameter placeholders outside string literals, quoted
    identifiers and comments: (start, end, key) where key is "?" or the
    bare name/number after "$"."""
    spans: list[tuple[int, int, str]] = []
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]
        if ch == "'" or ch == '"':
            i = _skip_quoted(sql, i, ch)
            continue
        if sql.startswith("--", i):
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "?":
            spans.append((i, i + 1, "?"))
            i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j > i + 1:
                spans.append((i, j, sql[i + 1 : j]))
                i = j
                continue
            i += 1
            continue
        i += 1
    return spans


def inline_params(sql: str, parameters: Any) -> str:
    """Return `sql` with its bound parameters inlined as SQL literals.

    The quack wire drops bound parameters: a table-reading query sent
    with `execute(sql, [value])` fails with 'Invalid Input Error:
    Values were not provided for the following parameters: 1'
    (measured 2026-10-22 against the served graph store; a
    parameterless query on the same table works, and a parameter on a
    constant-only query works too). Callers keep the familiar
    placeholder style; the values are rendered with `sql_lit`, so the
    escaping discipline stays in one place.

    Placeholders: `?` (positional, in order), `$1`/`$2` (1-based into a
    sequence) and `$name` (into a dict). A placeholder with no matching
    value raises ValueError — never a silent literal-free query.
    """
    spans = _scan_placeholders(sql)
    if not spans:
        return sql
    if isinstance(parameters, dict):
        values = _dict_values(spans, parameters, sql)
    else:
        values = _seq_values(spans, list(parameters), sql)
    out = sql
    for (start, end, _key), value in sorted(zip(spans, values), key=lambda pair: -pair[0][0]):
        out = out[:start] + sql_lit(value) + out[end:]
    return out


def _dict_values(spans: list[tuple[int, int, str]], parameters: dict, sql: str) -> list[Any]:
    """Values for named/1-based placeholders out of a dict."""
    values: list[Any] = []
    for _start, _end, key in spans:
        if key.isdigit():
            if int(key) not in parameters:
                raise ValueError(f"no value for parameter ${key} in {sql!r}")
            values.append(parameters[int(key)])
        elif key not in parameters:
            raise ValueError(f"no value for parameter ${key} in {sql!r}")
        else:
            values.append(parameters[key])
    return values


def _seq_values(spans: list[tuple[int, int, str]], seq: list[Any], sql: str) -> list[Any]:
    """Values for `?` (in order) and `$1`/`$2` (1-based) placeholders."""
    values: list[Any] = []
    pos = 0
    for _start, _end, key in spans:
        if key == "?":
            if pos >= len(seq):
                raise ValueError(f"not enough parameters for {sql!r}")
            values.append(seq[pos])
            pos += 1
        elif key.isdigit():
            idx = int(key) - 1
            if idx < 0 or idx >= len(seq):
                raise ValueError(f"not enough parameters for {sql!r}")
            values.append(seq[idx])
        else:
            raise ValueError(f"named parameter ${key} needs a dict of values")
    return values


class WireConnection:
    """Transparent proxy over a quack-wire connection that inlines
    bound parameters as literals (see `inline_params`).

    Everything else is delegated to the real connection, so the proxy
    is interchangeable with it for callers: `execute` returns the real
    connection, so `con.execute(...).fetchall()` keeps working.
    """

    __slots__ = ("_con",)

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def execute(self, sql: str, parameters: Any = None, **kwargs: Any) -> Any:
        if parameters is not None:
            sql = inline_params(sql, parameters)
        return self._con.execute(sql, **kwargs)

    def executemany(self, sql: str, parameters: Any, **kwargs: Any) -> Any:
        for row in parameters:
            self.execute(sql, row, **kwargs)
        return self._con

    def __getattr__(self, name: str) -> Any:
        return getattr(self._con, name)


def sql_lit(v: Any) -> str:
    """Render a Python value as a SQL literal (quack wire writes ship
    literal SQL only — prepared params do not cross the wire). Strings
    escape single quotes by doubling; bytes become blob literals;
    None → NULL; bool → TRUE/FALSE; float nan/inf spelled out."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "'NaN'::DOUBLE"
        if math.isinf(v):
            return "'Infinity'::DOUBLE" if v > 0 else "'-Infinity'::DOUBLE"
        return repr(v)
    if isinstance(v, bytes):
        return "'" + "".join(f"\\x{b:02x}" for b in v) + "'::BLOB"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    raise TypeError(f"no literal rendering for {type(v).__name__}")


def wait_healthy(
    discovery: Path | str | None = None,
    deadline_s: float = 60,
    *,
    path: Path | str | None = None,
) -> bool:
    """Block until every live discovery entry (or just the one serving
    `path`) answers a trivial read over the wire, or the deadline
    passes. Used by the start wrapper's readiness loop."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        disc = load_discovery(discovery)
        entries = _entries(disc)
        if path is not None:
            want = str(Path(path).resolve())
            entries = [e for e in entries if e["resolved"] == want]
        if entries and all(_probe(e) for e in entries):
            return True
        time.sleep(0.5)
    return False


def _probe(entry: dict[str, Any]) -> bool:
    try:
        con = _attach_wire(entry, str(entry.get("alias") or "probe"))
        try:
            con.execute("SELECT 1").fetchall()
            return True
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — readiness loop, retry until deadline
        return False
