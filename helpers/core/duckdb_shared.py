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
import re
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import duckdb
from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)

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


def _endpoint_reachable(
    uri: str,
    *,
    timeout: float = 0.5,
) -> bool:
    """True when the wire endpoint (host:port) accepts a TCP connection.

    Namespace-aware liveness fallback: in a PID namespace (container/CI/
    subagent sandbox) ``os.kill(pid, 0)`` cannot see servers outside the
    namespace, so ``_pid_alive`` lies. When the pid probe is inconclusive,
    a reachable host:port is treated as served. Failures return False.
    """
    try:
        rest = uri.split(":", 1)[1]
        host, port = rest.rsplit(":", 1)
        port = int(port)
    except IndexError, ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def load_discovery(p: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Parse + liveness-check the discovery file: {alias: entry} with
    entry {uri, token, pid, path}. Dead servers are dropped (their
    entries are stale state, not routes); a wholly unreadable file
    yields {}. Malformed legacy single-server files return {} too —
    callers then fall back to file mode, and the next `quack-start`
    rewrites the schema."""
    path = Path(p) if isinstance(p, str) else (p or discovery_path())
    try:
        disc = jsonio.loads(path.read_text())
    except OSError, jsonio.JSONDecodeError:
        return {}
    if not isinstance(disc, dict):
        return {}
    return {
        str(alias): dict(entry)
        for alias, entry in disc.items()
        if isinstance(entry, dict)
        and "uri" in entry
        and "pid" in entry
        and (_pid_alive(entry.get("pid")) or _endpoint_reachable(str(entry["uri"])))
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
        data = jsonio.loads(os.read(fd, 1 << 20) or b"{}")
    except jsonio.JSONDecodeError:
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


def raw_discovery(p: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Parse the discovery file WITHOUT the liveness filter.

    ``load_discovery`` drops entries whose pid looks dead (namespace-visible
    servers raise ``ProcessLookupError``). S1's fallback needs the raw
    declaration to know a store is *meant* to be served even when the pid
    probe lies — so it can attach over the wire instead of surfacing a
    file-lock conflict.
    """
    path = Path(p) if isinstance(p, str) else (p or discovery_path())
    try:
        disc = jsonio.loads(path.read_text())
    except OSError, jsonio.JSONDecodeError:
        return {}
    return disc if isinstance(disc, dict) else {}


def served_entry_unfiltered(
    path: Path | str, *, discovery: Path | None = None
) -> dict[str, Any] | None:
    """The raw discovery entry for ``path`` (no liveness check), or None."""
    return served_entry(raw_discovery(discovery), path)


def _attach_wire(entry: dict[str, Any], alias: str) -> duckdb.DuckDBPyConnection:
    """Fresh in-memory client shell with the served store attached as
    `alias` and made active (unqualified SQL resolves remotely — the
    `USE rdb` semantics validated 2026-10-21). CREATE SECRET/ATTACH are
    client-side statements; literals via `sql_lit` keep one escaping
    discipline for everything that could ever touch the wire.

    The shell is wrapped in `WireConnection`: the extension drops bound
    parameters on table-reading queries, so parameters are inlined as
    literals before they reach the wire (see `inline_params`)."""
    con = duckdb.connect(**memory_cap_config())
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

    Unsigned extensions are deliberately NOT permitted on a shared con:
    no caller loads onager over one (materialization runs on onager's own
    in-memory con; the graph file-mode build and the wire server child
    carry the flag explicitly where their lanes need it — see
    doc/security/unsigned-extensions.md).
    """
    p = Path(path)
    disc = load_discovery(discovery)
    if disc:
        entry = served_entry(disc, p)
        if entry is not None:
            return _attach_wire(entry, str(entry.get("alias") or p.stem))
    return duckdb.connect(str(p), read_only=read_only, **memory_cap_config())


# Repo-wide DuckDB memory cap: default is ~70% of RAM (11.2 GiB on the
# 16 GB box), which can drive the machine to swap under concurrent FTS /
# materialization legs. Measured trial (2026-11-11, duckdb_read_side_tuning
# trial): 5 GB (and even 512 MB) is performance-neutral on every read leg,
# so cap all real connections here. Operator cap
# 2026-11-23: 2 GB max per connection (the 2026-11-23 gate-arc swap storm
# was driven by multi-worker materialization; each worker's claim is now
# capped while read legs stay neutral).
# Enforced 2026-11-23 (operator: "restrict its memory to 2GB max"): every
# `duckdb.connect` under helpers/ and app.py passes `**memory_cap_config()`
# — in-memory instances included, since a bare `duckdb.connect()` claims
# the 11.2 GiB default above. Guard: tests/test_static_checks.py
# `test_no_uncapped_duckdb_connect_outside_allowlist`. No category is
# exempt — benches and research one-shots included (operator ruling
# 2026-11-23: they have driven the box to swap before). The one bare script
# that cannot import this package (helpers/misc/parquet_textconv.py, a git
# textconv driver) passes the same limit inline; the guard checks that
# literal against MEMORY_LIMIT. Test-side connects are capped by
# tests/conftest.py, which installs MEMORY_LIMIT as the default. Capped
# connections also spill to the system temp dir, never a cwd-relative `.tmp`
# (operator ruling 2026-11-23: "everyone should use TMPDIR, not .tmp") —
# see temp_directory().
MEMORY_LIMIT = "2GB"


def temp_directory() -> str:
    """Spill directory for capped connections (operator ruling 2026-11-23:
    "everyone should use TMPDIR, not .tmp").

    DuckDB defaults ``temp_directory`` to a cwd-relative ``.tmp`` for
    in-memory databases, so an in-memory instance pushed past its cap would
    litter ``<cwd>/.tmp``; this resolves the first set of TMPDIR/TEMP/TMP
    and falls back to :func:`tempfile.gettempdir`."""
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value:
            return value
    return tempfile.gettempdir()


def memory_cap_config() -> dict[str, Any]:
    """``duckdb.connect`` kwargs enforcing the repo memory cap and the
    TMPDIR spill rule (:func:`temp_directory`).

    ``FINDATA_DUCKDB_THREADS=<n>`` (env) additionally caps DuckDB's internal
    thread pool — the perf lane sets it so a benchmark leg cannot saturate
    all cores (see ``helpers.core.parallelism``). Unset (the default) keeps
    DuckDB's own core-count default everywhere.
    """
    config: dict[str, Any] = {"memory_limit": MEMORY_LIMIT, "temp_directory": temp_directory()}
    threads_env = os.environ.get("FINDATA_DUCKDB_THREADS", "")
    if threads_env.isdigit() and int(threads_env) > 0:
        config["threads"] = int(threads_env)
    return {"config": config}


def unsigned_ext_kwargs() -> dict[str, Any]:
    """``duckdb.connect`` kwargs permitting locally-built (unsigned)
    extensions.

    RETIRED as of 2026-11-20: the vendored onager build was superseded by
    the signed upstream 2.0.1 distribution (``INSTALL onager FROM
    community;``), which loads flaglessly. Kept as a thin compatibility
    shim returning the empty config — no code path needs the flag now.
    """
    return {}


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


def _is_sql_identifier(v: str) -> bool:
    """True when ``v`` is a bare SQL identifier safe to interpolate.

    The FTS helpers take ``fts_table``/``base_table``/``id_column`` as
    arguments and splice them into SQL, so the S608 suppressions for this
    module rest on this being enforced — not on callers happening to pass
    module constants. Anything containing a quote, dot, space or other
    punctuation is rejected and the helper falls back to the macro path.
    """
    return bool(v) and v.replace("_", "").isalnum() and not v[0].isdigit()


def fts_query_tokens_duckdb(q: str) -> list[str]:
    """Free-text query -> tokens the way the DuckDB native FTS analyzer
    sees them (fts_query_token_parity S1).

    The analyzer's behavior was pinned empirically 2026-11-18 by indexing
    a fixture corpus with the SAME PRAGMA call the rebuilds use
    (``PRAGMA create_fts_index('<table>', '<id>', '<col>')`` — default
    stemmer/stopwords) and dumping the resulting ``dict``:

    - separators: any non-alphanumeric run (``-``, ``&``, ``.``, ``_``,
      ``/``, ``'``)
    - alphanumeric runs split at the letter/digit boundary
      (``FY2026`` -> ``fy`` + ``2026``; the digit part is dropped)
    - digit-only tokens dropped
    - 1-character tokens dropped (``R&D`` -> ``r``/``d`` -> both gone)

    This splitter mirrors the mechanical rules and deliberately does NOT
    embed a stopword list or a stemmer: :func:`fts_topk` resolves each
    token against the store's own ``dict`` through its stem-guess ladder,
    and its contract is already "any unresolvable token -> the macro
    fallback". A token the analyzer would have stopped out simply fails to
    resolve and lands in the fallback, unchanged. Dropping 1-char query
    tokens is what keeps ``R&D`` from turning into an ``r OR d`` scan over
    a dict that does carry bare ``r``/``d`` from other rows.

    Why it matters: ``fts_query_tokens`` (rebuild_doc_search) splits
    whitespace only, so ``cattle-feed`` arrives at ``fts_topk`` as one
    token that cannot exist in a dict where the analyzer split it — every
    punctuated query takes the whole-table macro scan (measured 27.7 ms vs
    3.5 ms on doc, and the macro overmatches: 159 rows for ``cattle-feed``
    vs 3 containing "cattle"). Splitting the same way the analyzer does
    lets those queries resolve on the top-k path.

    The FTS5-path tokenizer keeps its own function: the legacy surface
    keeps its quoted-OR unicode61 semantics and its own pinned results.
    """
    if not q:
        return []
    out: list[str] = []
    for run in re.findall(r"[A-Za-z]+|\d+", q):
        if run.isdigit():
            continue  # analyzer drops digit-only tokens (2026, 50)
        if len(run) < 2:
            continue  # analyzer drops 1-char tokens (r, d, a, b, x, y)
        out.append(run.lower())
    return out


def fts_topk(  # noqa: C901  # native-FTS top-k: stem-guess ladder + SQL assembly
    conn: duckdb.DuckDBPyConnection,
    *,
    fts_table: str,
    base_table: str,
    tokens: list[str],
    limit: int,
    offset: int = 0,
    where_extra: str = "",
    params_extra: list[Any] | None = None,
    id_column: str = "id",
) -> tuple[list[tuple[Any, float]] | None, str]:
    """DuckDB native-FTS top-k over the posting tables (macro-free).

    The FTS extension stores its inverted index in schema-qualified
    backing tables (``fts_main_<table>.terms/dict/docs/stats``). The
    ``match_bm25`` macro-predicate form cannot be pushed down in DuckDB
    2.0.0 — EXPLAIN ANALYZE shows a per-row sequential scan, 30–80×
    slower than sqlite FTS5 (measurements 2026-11-16). This helper
    scores BM25 (k1=1.2, b=0.75, DuckDB FTS defaults) directly from the
    postings:

      score(doc) = Σ_terms ln((N − df + 0.5)/(df + 0.5) + 1)
                     · (tf·(k1+1)) / (tf + k1·(1 − b + b·dl/avgdl))

    Term resolution mirrors the index's porter stemming (``cache`` →
    ``cach``): exact dict match per stem guess first, prefix fallback for
    long tokens only. Any unresolvable token (stopword, short token,
    schema mismatch) returns ``(None, "macro")`` so callers fall back to
    the macro path unchanged — the helper never silently narrows recall.

    ``where_extra`` is spliced verbatim after ``WHERE 1=1`` (must
    reference the base-table alias ``ss``, e.g. ``" AND ss.kind = ?"``);
    ``params_extra`` supplies its bound values.

    Returns (rows, "topk") with rows = [(id, score)] ordered by score
    desc, or (None, "macro") to trigger the caller's fallback. ``id``
    keeps the base-table key's native type (BIGINT for scripts/doc/note;
    VARCHAR for convo's composite ``part_id``).
    """
    if not tokens or not all(t for t in tokens):
        return None, "macro"
    if not _is_sql_identifier(fts_table) or not _is_sql_identifier(base_table):
        return None, "macro"
    try:
        n = conn.execute(
            "SELECT count(*) FROM duckdb_tables() "
            "WHERE schema_name = ? AND table_name IN ('terms','dict','docs','stats')",
            [fts_table],
        ).fetchone()
        if n is None or n[0] != 4:
            return None, "macro"
    except duckdb.Error:
        return None, "macro"

    termids: list[int] = []
    for tok in tokens:
        resolved: list[int] | None = None
        for guess in _stem_guesses(tok):
            rows = conn.execute(
                f"SELECT termid FROM {fts_table}.dict WHERE term = ?", [guess]
            ).fetchall()
            if rows:
                resolved = [r[0] for r in rows]
                break
        if resolved is None:
            if len(tok) >= 4:
                rows = conn.execute(
                    f"SELECT termid FROM {fts_table}.dict WHERE term LIKE ?",
                    [tok + "%"],
                ).fetchall()
                if rows:
                    resolved = [r[0] for r in rows]
            if resolved is None:
                return None, "macro"
        termids.extend(resolved)
    termids = list(dict.fromkeys(termids))
    if not termids:
        return None, "macro"

    tlist = ", ".join("?" for _ in termids)
    params: list[Any] = list(termids)
    if params_extra:
        params.extend(params_extra)
    params.append(offset + limit)
    sql = f"""
    WITH qterms AS (SELECT unnest([{tlist}]) AS termid),
         post AS (
           SELECT t.docid, t.termid, COUNT(*) AS tf
           FROM {fts_table}.terms t JOIN qterms USING (termid)
           GROUP BY t.docid, t.termid
         ),
         scored AS (
           SELECT p.docid,
                  SUM( ln((s.num_docs - d.df + 0.5)/(d.df + 0.5) + 1)
                       * (p.tf * 2.2)
                         / (p.tf + 1.2 * (1 - 0.75 + 0.75 * dc.len / s.avgdl)) ) AS score
           FROM post p
           JOIN {fts_table}.dict d USING (termid)
           JOIN {fts_table}.docs dc USING (docid)
           CROSS JOIN {fts_table}.stats s
           GROUP BY p.docid
         )
    SELECT dc.name AS {id_column}, sc.score
    FROM scored sc
    JOIN {fts_table}.docs dc USING (docid)
    JOIN {base_table} ss ON ss.{id_column} = dc.name
    WHERE 1=1{where_extra}
    ORDER BY sc.score DESC
    LIMIT ?"""
    try:
        rows = conn.execute(sql, params).fetchall()
    except duckdb.Error:
        return None, "macro"
    return [(r[0], float(r[1])) for r in rows], "topk"


def fts_topk_count(  # noqa: C901  # native-FTS count: same stem-guess ladder
    conn: duckdb.DuckDBPyConnection,
    *,
    fts_table: str,
    base_table: str,
    tokens: list[str],
    where_extra: str = "",
    params_extra: list[Any] | None = None,
    id_column: str = "id",
    count_col: str = "id",
) -> int | None:
    """Count distinct rows in the top-k match space (no LIMIT).

    Mirrors :func:`fts_topk`'s term resolution and scoring CTE — same
    ``(None, "macro")``-style contract via ``None`` on any unresolvable
    token or schema mismatch. Used to preserve the legacy "total counts
    the OR space" contract (note search) without a second macro scan.
    """
    if not tokens or not all(t for t in tokens):
        return None
    if not _is_sql_identifier(fts_table) or not _is_sql_identifier(base_table):
        return None
    try:
        n = conn.execute(
            "SELECT count(*) FROM duckdb_tables() "
            "WHERE schema_name = ? AND table_name IN ('terms','dict','docs','stats')",
            [fts_table],
        ).fetchone()
        if n is None or n[0] != 4:
            return None
    except duckdb.Error:
        return None

    termids: list[int] = []
    for tok in tokens:
        resolved: list[int] | None = None
        for guess in _stem_guesses(tok):
            rows = conn.execute(
                f"SELECT termid FROM {fts_table}.dict WHERE term = ?", [guess]
            ).fetchall()
            if rows:
                resolved = [r[0] for r in rows]
                break
        if resolved is None:
            if len(tok) >= 4:
                rows = conn.execute(
                    f"SELECT termid FROM {fts_table}.dict WHERE term LIKE ?",
                    [tok + "%"],
                ).fetchall()
                if rows:
                    resolved = [r[0] for r in rows]
            if resolved is None:
                return None
        termids.extend(resolved)
    termids = list(dict.fromkeys(termids))
    if not termids:
        return None

    tlist = ", ".join("?" for _ in termids)
    params: list[Any] = list(termids)
    if params_extra:
        params.extend(params_extra)
    sql = f"""
    WITH qterms AS (SELECT unnest([{tlist}]) AS termid),
         post AS (
           SELECT t.docid, t.termid, COUNT(*) AS tf
           FROM {fts_table}.terms t JOIN qterms USING (termid)
           GROUP BY t.docid, t.termid
         ),
         scored AS (
           SELECT p.docid,
                  SUM( ln((s.num_docs - d.df + 0.5)/(d.df + 0.5) + 1)
                       * (p.tf * 2.2)
                         / (p.tf + 1.2 * (1 - 0.75 + 0.75 * dc.len / s.avgdl)) ) AS score
           FROM post p
           JOIN {fts_table}.dict d USING (termid)
           JOIN {fts_table}.docs dc USING (docid)
           CROSS JOIN {fts_table}.stats s
           GROUP BY p.docid
         )
    SELECT COUNT(DISTINCT ss.{count_col})
    FROM scored sc
    JOIN {fts_table}.docs dc USING (docid)
    JOIN {base_table} ss ON ss.{id_column} = dc.name
    WHERE 1=1{where_extra}"""
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return int(row[0])
    except duckdb.Error:
        return None


def _is_vowel_char(ch: str, prev: str) -> bool:
    """Porter vowel test: y counts as a vowel after a consonant."""
    if ch in "aeiou":
        return True
    return ch == "y" and prev != "" and prev not in "aeiou"


def _porters_measure(word: str) -> int:
    """Porter measure m: number of VC sequences in the word's CV-form.

    Guards the m>1 suffix classes to stemmer parity (measured 2026-11-18
    via the fixture oracle: ``general→gener`` strips, ``central→central``
    does not; ``alignment→align`` strips, ``document→document`` does not;
    ``edition→edit`` strips, ``action→action`` does not).
    """
    forms: list[str] = []
    prev = ""
    for ch in word:
        if not ch.isalpha():
            continue
        forms.append("V" if _is_vowel_char(ch.lower(), prev) else "C")
        prev = ch.lower()
    return sum(1 for i in range(len(forms) - 1) if forms[i] == "V" and forms[i + 1] == "C")


def _undouble_consonant(w: str) -> str | None:
    """Gemination undo after ed/ing strips (planned→plan); ss is kept
    (assessed→assess, guessing→guess — measured)."""
    if len(w) >= 3 and w[-1] == w[-2] and w[-1] not in "sz":
        return w[:-1]
    return None


def _stem_class_strips(w: str, lo: str, s: set[str]) -> None:
    """M-guarded suffix classes + the -ility/-ion refinements, added into
    `s` (2026-11-21 lint-audit split out of _stem_step for the C901
    budget; same measured rules, set-add order irrelevant)."""
    L = len(w)
    # m-guarded class strips
    for suf in (
        "ization",
        "ication",
        "ational",
        "ation",
        "iveness",
        "ement",
        "izement",
        "ency",
        "ence",
        "ility",
        "ity",
        "ical",
        "ance",
        "iciti",
        "ment",
        "ness",
        "ize",
        "ise",
        "ate",
        "ator",
        "al",
        "ic",
        "ative",
        "ibility",
        "ability",
        "able",
        "ible",
        "ly",
        "er",
        "ism",
        "ent",
        "ive",
        "ful",
        "ous",
        "ivity",
    ):
        if lo.endswith(suf) and L > len(suf) + 1:
            stem = w[: -len(suf)]
            if _porters_measure(stem) > 1 or (
                suf in ("ness", "ful") and _porters_measure(stem) > 0
            ):
                s.add(stem)
    # -ility family whose stem ends in the -ble l (scalab-ility→scalabl,
    # visib-ility→visibl, stabil-ility→stabil)
    if lo.endswith("ility") and L > 6:
        stem = w[:-5]
        if _porters_measure(stem) > 1:
            s.add(stem)
            s.add(stem + "l")
    # ion strips only with s/t before (edition→edit, position→posit;
    # nation/action/section keep — measured)
    if lo.endswith("ion") and L > 4 and lo[-4] in "st":
        stem = w[:-3]
        if _porters_measure(stem) > 1:
            s.add(stem)


def _stem_step(w: str) -> set[str]:
    """One pass of suffix rules -> candidate set (superset generator).

    Each class is pinned by the fixture-oracle batteries of 2026-11-18
    (see tests/test_fts_topk_parity.py::TestStemLadderParity). Guards:
    Porter m>1 unless the oracle measured otherwise (ness/ful strip at
    m>0: freshness→fresh, business→busi, truth stays under m>1 for
    fresh but business strips — the real stemmer strips both).
    """
    s: set[str] = set()
    L = len(w)
    lo = w.lower()
    # plurals: ies→i AND y (studies→studi via ies→i, activity→activities
    # via ies→y chaining into -ity)
    if L > 4 and lo.endswith("ies"):
        s.add(w[:-3] + "i")
        s.add(w[:-3] + "y")
    if L > 4 and lo.endswith("sses"):
        s.add(w[:-2])
    if L > 3 and lo.endswith("es"):
        s.add(w[:-2])
        s.add(w[:-1])
    if L > 3 and lo.endswith("s") and not lo.endswith("ss"):
        s.add(w[:-1])
    # y→i (day→dai, query→queri) plus y-drop (agency→agenc)
    if L > 2 and lo.endswith("y"):
        s.add(w[:-1] + "i")
        s.add(w[:-1])
    # ed/ing with e-restore candidate (stored→store; created→creat)
    if L > 3 and lo.endswith("ing"):
        s.add(w[:-3])
        s.add(w[:-3] + "e")
    if L > 3 and lo.endswith("ed"):
        s.add(w[:-2])
        s.add(w[:-2] + "e")
    _stem_class_strips(w, lo, s)
    # final-e drop (cache→cach); node/store keep their e under the
    # stemmer's *o rule, so this candidate is a harmless superset extra
    if L > 3 and lo.endswith("e"):
        s.add(w[:-1])
    return s


def _stem_guesses(tok: str) -> list[str]:
    """Stemmer-parity candidate ladder (iterative, depth 3).

    Replaces the six-suffix single-pass ladder (2026-11-18): the old
    ladder missed 101/400 sampled corpus words (28%) — every morphological
    class outside ies/ing/ed/es/s/e took the macro detour. The generator
    applies the measured suffix classes iteratively (chained rules:
    awareness→aware→awar, typically→typical→typic, activities→activity
    →activ) and keeps a SUPERSET of the true stem — a wrong extra guess
    merely fails its dict lookup, never narrows recall. Measured parity:
    0/400 real-vocabulary misses on the same sample (legacy: 112/400),
    0/183 battery words (tests pin the per-class bank).
    """
    out = {tok}
    frontier = {tok}
    for _ in range(3):
        nxt: set[str] = set()
        for w in frontier:
            nxt |= _stem_step(w)
            d = _undouble_consonant(w)
            if d:
                nxt.add(d)
        nxt -= out
        if not nxt:
            break
        out |= nxt
        frontier = nxt
    return sorted(out)
