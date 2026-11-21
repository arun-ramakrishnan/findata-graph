#!/usr/bin/env python3
"""
DuckDB-native KNN support for the note_search hybrid ranking.

The FTS5 "note_search" table stores per-doc embeddings as JSON text and
historically ranked them by computing cosine similarity row-by-row in Python
over the BM25 candidate page. A1 (2026-08-17) added a native-float mirror
answering global top-K queries in C; since the pure-DuckDB arc
(doc/improvements/proposals/duckdb_pure_stack.md S2, 2026-11-05) that mirror
lives in a DuckDB sidecar -- memory/note_vectors.duckdb,
note_search_vec(row_key, embedding FLOAT[dims]) plus a persistent HNSW
cosine index -- replacing the retired sqlite-vec virtual table.

Why a mirror table (not a migration): the JSON column stays the source of
truth (snapshot export, rebuild incremental diffing, and the Python fallback
path all keep working untouched); the mirror is a derived index, exactly like
the FTS5 shadow tables. It is excluded from Parquet snapshots and the
db_maint twin backup for the same reason -- it is rebuilt, not shipped: a
cold rebuild reads the JSON column and takes seconds at corpus scale
(~15k section rows), unlike the embed cache beside it in
memory/embed_store.db (content-addressed re-embed work -- that one stays
backed up).

Never in research.db itself, and never a sqlite extension table again: the
old extension mirror had to leave research.db because DuckDB's SQLite
scanner cannot catalog-scan a database containing extension virtual
tables ("no such module" on ATTACH -- same regression class as spellfix1
tables). The DuckDB sidecar has no such constraint and needs no extension
load at query time; the HNSW index requires
SET hnsw_enable_experimental_persistence=true on every connection that
touches it (DuckDB 2.0.0).

Three entry points (signatures unchanged from the extension era):

- vec_available(conn)       -- table present + non-empty (read path gate)
- knn_similarities(...)     -- global top-K, returns {row_key: similarity}
- sync_vec_table(conn, ...) -- write path for rebuild_note_search (full
  refresh, incremental upsert/delete, or lazy backfill from the JSON column)

Concurrency: the mirror is ONE DuckDB file, so the many-RO-or-one-writer
rule applies. Readers flock "<db>.io.lock" shared around their open
(helpers.misc.duckdb_lock). Writers never write in place: the mirror is
built into a pid-tagged temp (copy-on-write from the current file for
incremental deltas, from scratch for full refresh) and atomically
os.replace'd under the exclusive flock -- readers keep serving the old
inode during the whole build window, exactly like the convo_search swap
rebuild.

Everything here is best-effort: any failure (missing store, transient lock,
dims mismatch) returns the "unavailable" value so callers fall back to the
pre-A1 Python cosine path. Hybrid search must never 500 because a vector
index is absent.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

# Repo root: helpers/core/vec_search.py -> parents[2]. Must be on sys.path
# BEFORE "from helpers.core.db import ..." so the script works as a
# subprocess (python3 helpers/core/vec_search.py) the same way it works
# under pytest. (Mirrors the rebuild_note_search.py bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from collections.abc import Iterable, Sequence  # noqa: E402  # after sys.path bootstrap

from helpers.core.vec_codec import load_vec  # noqa: E402  # after sys.path bootstrap

VEC_TABLE = "note_search_vec"

# DuckDB sidecar hosting the mirror (pure-DuckDB arc S2). Re-targetable
# module attribute: tests MUST redirect it (conftest autouse does globally,
# alongside EMBED_DB_PATH) the way they retarget DOC_DB; production defaults
# to the repo store.
VEC_INDEX_DB_PATH = _REPO_ROOT / "memory" / "note_vectors.duckdb"

# Consolidated embed store -- the pooled content-hash cache
# (helpers/core/embed_cache.py). Moved to DuckDB 2026-11-15 (note_search
# carve-out S0 / embed-store fold): the cache is a NATIVE duckdb store —
# no sqlite ATTACH shim (``_attach_vec_db``/``vecdb`` retired with the
# S1–S5 sidecar arc). ``EMBED_SQLITE_DB_PATH`` is the retired legacy
# file, kept as rollback insurance and the one-time migration source.
EMBED_DB_PATH = _REPO_ROOT / "memory" / "embed_store.duckdb"
EMBED_SQLITE_DB_PATH = _REPO_ROOT / "memory" / "embed_store.db"


def _is_duckdb_conn(conn) -> bool:
    """True for a live duckdb connection (adopted as the cache handle).

    Accepts a raw ``duckdb.DuckDBPyConnection``, the ``_DuckDBConn``
    proxy (helpers.core.db_engine), and the quack-wire ``WireConnection``
    (helpers.core.duckdb_shared) that writers receive from the canonical
    connect() path."""
    try:
        import duckdb
    except ImportError:
        return False
    if isinstance(conn, duckdb.DuckDBPyConnection):
        return True
    raw = getattr(conn, "raw", None)
    if raw is not None and isinstance(raw, duckdb.DuckDBPyConnection):
        return True
    # WireConnection delegates to its inner duckdb conn via _con.
    inner = getattr(raw, "_con", None)
    return inner is not None and isinstance(inner, duckdb.DuckDBPyConnection)


def open_cache_conn(*, read_only: bool = False):
    """Open the consolidated duckdb embed cache.

    RW by default (maintenance writers); ``read_only=True`` for GC /
    snapshot reads. Parents are created for RW opens only. Raises the
    driver exception; callers degrade to uncached embedding.
    """
    from helpers.core.duckdb_shared import memory_cap_config

    path = Path(EMBED_DB_PATH)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    import duckdb

    return duckdb.connect(str(path), read_only=read_only, **memory_cap_config())


def _resolve_cache_conn(conn):
    """Native duckdb cache connection for a given host connection.

    - duckdb host  -> the host itself (one engine, one file handle)
    - sqlite host  -> a dedicated duckdb cache conn: the shared file
      cache for file-backed mains, an anonymous in-memory duckdb store
      for :memory: mains (hermetic tests, no tmp pollution)
    - anything else -> best-effort file cache
    """
    if _is_duckdb_conn(conn):
        return conn
    try:
        main_file = None
        for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
            if name == "main":
                main_file = file
                break
    except Exception:  # noqa: BLE001  # foreign conn types -> file cache
        return open_cache_conn()
    if not main_file:
        import duckdb

        return duckdb.connect(":memory:")
    return open_cache_conn()


# Persistent HNSW needs the experimental-persistence flag on EVERY
# connection that touches the index -- DDL and queries alike (DuckDB 2.0.0).
_HNSW_PRAGMAS = (
    "INSTALL fts",
    "LOAD fts",
    "INSTALL vss",
    "LOAD vss",
    "SET hnsw_enable_experimental_persistence = true",
)

# HNSW build knob (matches the convo lane's idx_convo_search_emb).
HNSW_EF_CONSTRUCTION = 128

# Dims of an existing mirror column, parsed from the catalog
# ("FLOAT[512]" etc.).
_DIM_RE = re.compile(r"FLOAT\[(\d+)\]")


def qualified() -> str:
    """Table name (kept for caller compatibility with the sqlite era)."""
    return VEC_TABLE


def _open_rw(path: Path):
    import duckdb

    from helpers.core.duckdb_shared import memory_cap_config

    con = duckdb.connect(str(path), **memory_cap_config())
    for pragma in _HNSW_PRAGMAS:
        con.execute(pragma)
    return con


def _open_ro(path: Path):
    """Reader entry: flock shared around the open, then DuckDB RO.

    The mirror is never served by the resident quack server, so this is a
    plain local flock + read-only open (open_read_only's served-wire branch
    would be dead code here).
    """
    import duckdb

    from helpers.misc.duckdb_lock import io_lock

    from helpers.core.duckdb_shared import memory_cap_config

    with io_lock(path, exclusive=False):
        con = duckdb.connect(str(path), read_only=True, **memory_cap_config())
    for pragma in _HNSW_PRAGMAS:
        con.execute(pragma)
    return con


# --- cached mirror reader -------------------------------------------------
# The vector mirror is rewritten copy-on-write (temp file + os.replace), so a
# cached reader must notice the swap: os.replace gives the new file a fresh
# inode, hence (st_ino, st_mtime_ns, st_size) is the swap signal.
#
# The cache is PER THREAD: a DuckDB connection is not safe for concurrent use
# from several threads, and every worker thread then pays the extension-load
# cost (INSTALL/LOAD fts+vss) ONCE instead of on every query.
_TLS = threading.local()


class _MirrorReader:
    """Cached read-only handle on the vector mirror."""

    __slots__ = ("path", "sig", "con", "dims", "rows")

    def __init__(
        self, path: Path, sig: tuple[int, int, int], con, dims: int | None, rows: int
    ) -> None:
        self.path = path
        self.sig = sig
        self.con = con
        self.dims = dims
        self.rows = rows


def _mirror_sig(path: Path) -> tuple[int, int, int] | None:
    """Identity of the mirror file, or None when it is absent."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _dims_from_con(con) -> int | None:
    """Dims of the mirror's embedding column, read from an open catalog."""
    try:
        row = con.execute(
            "SELECT data_type FROM duckdb_columns "
            "WHERE table_name = ? AND column_name = 'embedding'",
            (VEC_TABLE,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - best-effort gate
        return None
    if not row or not row[0]:
        return None
    m = _DIM_RE.search(str(row[0]))
    return int(m.group(1)) if m else None


def _reader(path: Path) -> _MirrorReader | None:
    """Thread-local cached reader on the mirror (None when unavailable).

    Re-opens when the mirror file was swapped (copy-on-write rebuild), so a
    long-lived process never serves a stale snapshot.
    """
    sig = _mirror_sig(path)
    if sig is None:
        return None
    cached = getattr(_TLS, "reader", None)
    if cached is not None and cached.path == path and cached.sig == sig:
        return cached
    # Close the stale handle BEFORE opening the new one: DuckDB reuses the
    # live instance for the same path + config, and that instance keeps
    # serving the PRE-SWAP inode -- a copy-on-write rewrite would stay
    # invisible until every handle to the old file is closed (verified on
    # DuckDB 2.0.0: a fresh read-only connect while the old handle was open
    # returned the OLD rows; closing first returned the new ones).
    if cached is not None:
        try:
            cached.con.close()
        except Exception:  # noqa: BLE001, S110 - best-effort close (stale-reader swap window)
            pass
        _TLS.reader = None
    try:
        con = _open_ro(path)
    except Exception:  # noqa: BLE001 - best-effort
        return None
    dims = _dims_from_con(con)
    try:
        rows = con.execute(f"SELECT COUNT(*) FROM {VEC_TABLE}").fetchone()[0]  # noqa: S608  # VEC_TABLE is a module constant
    except Exception:  # noqa: BLE001
        con.close()
        return None
    reader = _MirrorReader(path, sig, con, dims, int(rows))
    _TLS.reader = reader
    return reader


def _mirror_path(conn: sqlite3.Connection) -> Path:
    """Resolve the mirror sidecar for this connection.

    Single path for file-backed and in-memory mains alike: hermeticity in
    tests comes from the conftest module-attribute redirect (same style as
    DOC_DB), not from where the main file lives.
    """
    return Path(VEC_INDEX_DB_PATH)


def _mirror_dims(path: Path) -> int | None:
    """Dims of the existing mirror column from the catalog (None if absent).

    Served from the cached reader when possible: the catalog read used to
    open (and extension-load) a connection on every call.
    """
    reader = _reader(path)
    if reader is not None:
        return reader.dims
    if not path.exists():
        return None
    try:
        con = _open_ro(path)
    except Exception:  # noqa: BLE001 - best-effort gate
        return None
    try:
        return _dims_from_con(con)
    finally:
        con.close()


def stored_dims(conn: sqlite3.Connection) -> int | None:
    """Dims of the existing mirror column (None if absent).

    Read-path gate for hybrid search: the query vector must live in the same
    vector space as the mirrored rows. An embedding-model swap (pseudo 64 ->
    bge 384) changes the column; callers compare this against len(q_vec) and
    degrade to BM25-only on mismatch instead of computing garbage cosine.
    """
    return _mirror_dims(_mirror_path(conn))


def _fetch_json_rows(
    conn: sqlite3.Connection,
) -> list[tuple[str, str]]:
    """(row_key, embedding_json) for every embeddable note_search row.

    Keys are COMPOSITE section row keys "{file_path}#{anchor}" (note
    sectioning, 2026-09-06) -- the mirror's key column keeps its historical
    "file_path" name but holds row keys since sections multiplied rows
    per file. Pre-sectioning tables (the deploy gap before the sectioned
    rebuild runs) have no anchor column -- key those by bare file_path, the
    shape their mirror was always built with.
    """
    has_anchor = conn.execute(
        "SELECT 1 FROM pragma_table_info('note_search') WHERE name = 'anchor'"
    ).fetchone()
    key_sql = "file_path || '#' || anchor" if has_anchor else "file_path"
    return conn.execute(
        f"SELECT {key_sql}, embedding FROM note_search "  # noqa: S608  # key_sql is a module-local constant pair
        "WHERE embedding IS NOT NULL AND embedding != ''"
    ).fetchall()


def _upsert_rows_from_json(
    conn: sqlite3.Connection,
    dims: int,
    upsert_rows: Iterable[tuple[str, str | None]],
) -> list[tuple[str, list[float]]]:
    """Decode (row_key, embedding_json) deltas into mirror-ready rows.

    A None/invalid embedding or a dims mismatch yields NO row -- the caller
    deletes the key first, mirroring the "searchable but unembeddable" FTS
    behaviour.
    """
    out: list[tuple[str, list[float]]] = []
    for row_key, embedding_json in upsert_rows:
        if not embedding_json:
            continue
        vec = load_vec(embedding_json)
        if not vec or len(vec) != dims:
            continue
        out.append((row_key, vec))
    return out


def _vec_literal(vec: Sequence[float], dims: int) -> str:
    """Inline float-literal vector cast to the mirror's sized type.

    Bound params are avoided in KNN/order-by positions; inline literals are
    the proven form (SurrealDB/DuckDB trial forensics, 2026-11-05).
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]::FLOAT[" + str(int(dims)) + "]"


def _create_schema(con, dims: int) -> None:  # noqa: ANN001 - duckdb connection
    """(Re)create the mirror table at "dims" (no index yet).

    Index is created AFTER the data load (bulk insert into an indexed table
    would maintain it per row); wrong-dim vectors fail loudly at the CAST.
    """
    con.execute(f"DROP TABLE IF EXISTS {VEC_TABLE}")
    con.execute(
        f"CREATE TABLE {VEC_TABLE} (row_key VARCHAR PRIMARY KEY, embedding FLOAT[{int(dims)}])"
    )


def _create_index(con) -> None:  # noqa: ANN001 - duckdb connection
    con.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{VEC_TABLE}_emb ON {VEC_TABLE} "
        "USING HNSW (embedding) WITH (metric='cosine', "
        f"ef_construction={HNSW_EF_CONSTRUCTION})"
    )


def _write_mirror(  # noqa: C901  # copy-on-write: incremental vs fresh branches are linear, splitting hides the swap invariant
    conn: sqlite3.Connection,
    dims: int,
    *,
    upsert_rows: Iterable[tuple[str, str | None]] = (),
    delete_paths: Iterable[str] = (),
    delete_files: Iterable[str] = (),
    full: bool = False,
) -> int:
    """Build the mirror into a pid-tagged temp and atomically swap it in.

    Copy-on-write: an existing sidecar is copied to the temp first so
    incremental deltas only rewrite what changed; full=True (or a dims
    mismatch -- an embedding-model swap pins the OLD dims, and stale vectors
    must never keep serving KNN) starts from an empty table re-mirrored from
    the JSON column. Readers keep the old inode until os.replace.
    Returns the number of mirror rows written this call.
    """
    from helpers.misc.duckdb_lock import io_lock

    path = _mirror_path(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Resolve the incremental-vs-fresh decision BEFORE the exclusive window:
    # _mirror_dims takes a shared io_lock, and flock on a second fd of the
    # same lockfile while holding it exclusive self-deadlocks (locks are
    # per open-file-description, not per process).
    existing = path.exists() and not full and _mirror_dims(path) == dims
    written = 0
    with io_lock(path, exclusive=True):
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{path.name}.tmp{os.getpid()}.", dir=str(path.parent)
        )
        os.close(fd)
        tmp = Path(tmp_name)
        tmp.unlink()  # duckdb creates the file itself; mkstemp only reserved the name
        con = None
        try:
            if existing:
                shutil.copy2(path, tmp)
                con = _open_rw(tmp)
            else:
                # Fresh build (full refresh, first build, or a dims swap:
                # the existing column pins the old vector space -- drop and
                # re-mirror; the mirror is derived, never a source of truth).
                con = _open_rw(tmp)
                _create_schema(con, dims)
                existing = False
            if existing:
                for row_key in delete_paths:
                    con.execute(
                        f"DELETE FROM {VEC_TABLE} WHERE row_key = ?",  # noqa: S608  # VEC_TABLE is a module constant
                        (row_key,),
                    )
                for fp in delete_files:
                    # Prefix delete via starts_with -- LIKE would treat '_' in
                    # paths as a wildcard.
                    con.execute(
                        f"DELETE FROM {VEC_TABLE} "  # noqa: S608  # VEC_TABLE is a module constant
                        "WHERE starts_with(row_key, ?)",
                        (fp + "#",),
                    )
                upsert_rows = list(upsert_rows)
                ready = _upsert_rows_from_json(conn, dims, upsert_rows)
                # Delete every REQUESTED key, not just the decodable ones:
                # an upsert whose new embedding is None/invalid must drop the
                # stale vector (searchable-but-unembeddable), not leave it
                # serving KNN.
                for row_key, _embedding_json in upsert_rows:
                    con.execute(
                        f"DELETE FROM {VEC_TABLE} WHERE row_key = ?",  # noqa: S608  # VEC_TABLE is a module constant
                        (row_key,),
                    )
                if ready:
                    con.executemany(
                        f"INSERT INTO {VEC_TABLE} (row_key, embedding) VALUES (?, ?)",  # noqa: S608  # VEC_TABLE is a module constant
                        [(rk, v) for rk, v in ready],
                    )
                    written = len(ready)
            else:
                rows = _fetch_json_rows(conn)
                payload = []
                for row_key, embedding_json in rows:
                    vec = load_vec(embedding_json)
                    if vec and len(vec) == dims:
                        payload.append((row_key, vec))
                if payload:
                    con.executemany(
                        f"INSERT INTO {VEC_TABLE} (row_key, embedding) VALUES (?, ?)",  # noqa: S608  # VEC_TABLE is a module constant
                        payload,
                    )
                written = len(payload)
            _create_index(con)
            con.close()
            con = None
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - best-effort: JSON column stays truth
            if con is not None:
                try:
                    con.close()
                except Exception:  # noqa: BLE001,S110 - already closing
                    pass
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # noqa: S110 - best-effort cleanup
                pass
            return 0
    return written


def backfill_from_fts(conn: sqlite3.Connection, dims: int) -> int:
    """Populate the mirror from note_search's JSON embedding column.

    Returns the number of rows written (0 when nothing to do or on failure).
    Used both by the rebuild write path and lazily on first hybrid search
    after a snapshot restore (the mirror is snapshot-excluded by design).
    """
    try:
        _fetch_json_rows(conn)
    except sqlite3.Error:
        return 0
    return _write_mirror(conn, dims, full=True)


def vec_available(conn: sqlite3.Connection, dims: int, *, lazy_backfill: bool = False) -> bool:
    """True when the KNN path can serve queries on this connection.

    lazy_backfill=True (used by the search read path) builds the mirror
    from the JSON column when absent -- a one-time cost on the first hybrid
    query after a rebuild-from-scratch or snapshot restore.
    """
    path = _mirror_path(conn)
    reader = _reader(path)
    if reader is None or reader.dims != dims:
        if not lazy_backfill:
            return False
        written = backfill_from_fts(conn, dims)
        # The backfill rewrites the mirror copy-on-write -> re-open the reader.
        reader = _reader(path)
        if reader is None:
            return False
        if written == 0 and reader.dims != dims:
            return False
    return reader.rows > 0


# The sqlite-vec era hard-capped a single KNN query at k=4096, which at
# 14.5k section rows silently pushed every whole-corpus hybrid search onto
# the flat-matrix fallback (note sectioning, 2026-09-06). The DuckDB mirror
# has no such cap: k=None runs a vectorized whole-table cosine scan and
# k=<int> uses the HNSW index -- no silent truncation at any corpus size.


def _whole_corpus_similarities(con, vec: list[float], dims: int) -> dict[str, float] | None:
    """Whole-corpus cosine map (the k=None leg), best first.

    The query vector is BOUND as a FLOAT[dim] parameter instead of inlined:
    a 512-float text literal was re-parsed by DuckDB on every query (~11 ms
    per call). The bound parameter computes the identical value -- same cast,
    same function -- and the returned map and its order are bit-identical to
    the inline-literal form (verified on the live 19,356-row mirror).

    No ORDER BY in SQL: the previous implementation sorted in python, and
    app.py's RRF ranks depend on that order (ties keep scan order). A STABLE
    numpy argsort over the scan order reproduces it exactly.
    """
    sql = (
        f"SELECT row_key, array_cosine_similarity(embedding, $1::FLOAT[{int(dims)}]) AS sim"  # noqa: S608 - dims int()-validated, VEC_TABLE constant; $1 binds the vector
        f" FROM {VEC_TABLE}"
    )
    try:
        res = con.execute(sql, [vec]).fetchnumpy()
        import numpy as np
    except Exception:  # noqa: BLE001 - no numpy (or the fetch failed): pure python
        sql_lit = (
            "SELECT row_key, array_cosine_similarity(embedding, " + _vec_literal(vec, dims) + ") "
            "FROM {table}"
        ).format(table=VEC_TABLE)
        hits = con.execute(sql_lit).fetchall()
        hits.sort(key=lambda r: r[1], reverse=True)
        out: dict[str, float] = {}
        for row_key, sim in hits:
            try:
                out[str(row_key)] = float(sim)  # raw cosine, no clamp
            except TypeError, ValueError:
                continue
        return out
    keys = res["row_key"].tolist()
    sims = res["sim"]
    if np.isnan(sims).any():
        # A NULL embedding yields a NULL similarity; the old python sort
        # raised on it and degraded the whole KNN path. Keep degrading.
        return None
    order = np.argsort(-sims, kind="stable")
    values = sims.tolist()
    return {str(keys[i]): values[i] for i in order}


def knn_similarities(
    conn: sqlite3.Connection,
    q_vec: Sequence[float],
    k: int | None,
    dims: int,
) -> dict[str, float] | None:
    """Cosine neighbours of "q_vec"; k=None covers the whole corpus.

    Returns {row_key: similarity} (similarity = 1 - cosine distance, the
    raw cosine -- negative for anti-correlated docs, matching the Python
    path's contract), best first, or None when the KNN path is
    unavailable -- the caller then falls back to the flat-matrix map and
    finally the Python cosine loop.

    k=None (the hybrid-search usage) sizes the scan to the table so
    EVERY indexed doc carries its exact similarity: the re-ranked BM25 page
    is a subset of the corpus, and a bounded k would zero out page docs that
    fall outside the global top-k, silently changing their scores. DuckDB's
    vectorized whole-table cosine handles this at corpus scale in
    milliseconds. An explicit int k uses the HNSW index top-k; asking for
    more rows than exist returns all of them.
    """
    if len(q_vec) != dims:
        return None
    if not vec_available(conn, dims, lazy_backfill=True):
        return None
    if k is not None:
        k = max(int(k), 1)
    reader = _reader(_mirror_path(conn))
    if reader is None:
        return None
    con = reader.con
    vec = [float(x) for x in q_vec]
    try:
        if k is None:
            out = _whole_corpus_similarities(con, vec, dims)
        else:
            # The HNSW leg keeps the inline literal (the proven form for
            # ORDER-BY positions, 2026-11-05 trial forensics) -- binding the
            # vector here risks the index plan.
            vec_lit = _vec_literal(vec, dims)
            hits = con.execute(
                "SELECT row_key, array_cosine_distance(embedding, " + vec_lit + ") AS dist "
                f"FROM {VEC_TABLE} "
                "ORDER BY array_cosine_distance(embedding, " + vec_lit + ") ASC "
                "LIMIT ?",
                (int(k),),
            ).fetchall()
            # Convert distance to similarity AFTER the top-k to preserve HNSW routing
            hits = [(rk, 1.0 - dist) for rk, dist in hits]
            out = {}
            for row_key, sim in hits:
                try:
                    out[str(row_key)] = float(sim)  # raw cosine, no clamp
                except TypeError, ValueError:
                    continue
    except Exception:  # noqa: BLE001 - best-effort
        return None
    return out


def sync_vec_table(
    conn: sqlite3.Connection,
    dims: int,
    *,
    upsert_rows: Iterable[tuple[str, str | None]] = (),
    delete_paths: Iterable[str] = (),
    delete_files: Iterable[str] = (),
    full: bool = False,
) -> int:
    """Write path for rebuild_note_search: keep the mirror in sync.

    Keys are composite section row keys "{file_path}#{anchor}" (note
    sectioning, 2026-09-06).

    - full=True        -- drop everything and re-mirror the FTS embedding
      column (used by the non-incremental rebuild).
    - upsert_rows      -- (row_key, embedding_json) pairs whose FTS row
      changed; a None/invalid embedding removes the mirror row (mirrors the
      "searchable but unembeddable" FTS behaviour).
    - delete_paths     -- exact row keys that disappeared.
    - delete_files     -- FILE paths whose every section row must go
      (prefix delete on "{file}#"; used when a changed file may have
      dropped sections).

    Returns the number of mirror rows written. Best-effort: any sidecar or
    schema failure returns 0 without raising -- the JSON column path in
    rebuild/app remains the source of truth.
    """
    try:
        written = _write_mirror(
            conn,
            dims,
            upsert_rows=upsert_rows,
            delete_paths=delete_paths,
            delete_files=delete_files,
            full=full,
        )
    except sqlite3.Error:
        return 0
    if not full and written == 0:
        # Incremental with an empty delta (first run after adding the
        # mirror, or right after a snapshot restore) can leave a bare
        # table -- mirror the whole FTS embedding column once so stats
        # report real coverage.
        path = _mirror_path(conn)
        if _mirror_dims(path) == dims:
            try:
                con = _open_ro(path)
            except Exception:  # noqa: BLE001
                return written
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {VEC_TABLE}").fetchone()[0]  # noqa: S608  # VEC_TABLE is a module constant
            except Exception:  # noqa: BLE001
                return written
            finally:
                con.close()
            if n == 0:
                written = backfill_from_fts(conn, dims)
    return written


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual diagnostic
    """CLI: report mirror health for a research.db path (default memory/)."""
    import argparse

    p = argparse.ArgumentParser(description="DuckDB note_search mirror status")
    p.add_argument("db", nargs="?", default=str(Path("memory") / "research.db"))
    p.add_argument("--dims", type=int, default=512)
    args = p.parse_args(argv)
    from helpers.core.db import connect

    conn = connect(args.db)
    try:
        ok = vec_available(conn, args.dims, lazy_backfill=False)
        n = 0
        if ok:
            con = _open_ro(_mirror_path(conn))
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {VEC_TABLE}").fetchone()[0]  # noqa: S608  # VEC_TABLE is a module constant
            finally:
                con.close()
        fts = conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
        print("mirror: " + ("ok" if ok else "unavailable"))
        print(f"vec rows: {n}  (fts rows: {fts})")
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
