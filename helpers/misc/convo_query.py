#!/usr/bin/env python3
"""convo_query — hybrid search over the harvested conversation corpus.

The conversation-history counterpart of doc_query / script_query /
gate_query: BM25 (SQLite FTS5 porter sidecar) fused with cosine
(granite 384-d vectors in ``memory/convo_search.duckdb``) by reciprocal
rank fusion, over the pointer index built by
``helpers/maintenance/rebuild_convo_search.py``. Hits carry the parquet
pointer (file:row) — the corpus stays the archive of record, the index
never stores full bodies. Trial-verified contract (convo_search
proposal §3.6): FTS ~1 ms, cosine sub-second linear at corpus scale,
lanes complementary.

Exit codes: 0 on answered (even empty), 1 when the index is missing or
stale beyond the served-answer doctrine's WARNING.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REPO = _REPO_ROOT
DEFAULT_DB = REPO / "memory/convo_search.duckdb"
FTS_DB_NAME = "convo_search_fts.db"
# Compression summaries are not a peer harness: billion-context is a
# harness-specific plugin (2026-10-17), so harvest_conversations.py routes
# its blocks into the OWNING harness's session file (source='bili-index/*')
# instead of a `bili/` corpus directory. There is no bili directory to
# Compression summaries are not a peer harness: billion-context is a
# harness-specific plugin (2026-10-17), so harvest_conversations.py routes
# its blocks into the OWNING harness's session file (source='bili-index/*')
# instead of a `bili/` corpus directory. There is no bili directory to
# filter on, so it is deliberately absent here.
#
# `proxy` IS a directory (memory/data/harness/proxy/conversations/) and was
# simply missing from this list, which made `--harness proxy` an argparse
# error even though 83 bili rows carry that harness value — the only way to
# reach them was `--source bili-index/proxy`. Same rule as the others: the
# list mirrors the corpus namespaces, nothing more.
HARNESSES = ["opencode", "prime-rlm", "zcode", "dsh", "proxy"]
HEAD = 160

# Rank prior. Tool traffic is ~30k of 53,423 indexed rows — but NOT under
# one part_type: assistant tool CALLS are part_type='tool' (17,049) while
# their OUTPUT arrives as role='toolResult', part_type='text' (12,622).
# Keying the prior on part_type alone therefore demotes nothing, which is
# exactly what the first live run showed. So both columns decide.
#
# It is a NUDGE, not a ban: a tool row is often the best answer for a query
# about code (a grep hit found by meaning), so it is demoted, not dropped.
# `--kinds` is the hard lever.
TOOL_ROLES = ("tool", "toolResult")
TOOL_PRIOR = 0.45
PART_PRIOR: dict[str, float] = {
    "text": 1.0,
    "reasoning": 1.0,
    "req-msg": 0.6,
    "step-finish": 0.3,
    # A compression summary is prose written FOR retrieval, not incidental
    # tool noise, so the neutral 0.6 fallback would demote it below every
    # assistant text row. This does NOT fix the rank problem by itself —
    # measured 2026-11-10, the best summary for 'mem mtime gate' sits at
    # overall cosine rank 4,511, far outside the fused candidate pool, so a
    # weight cannot promote what was never a candidate. It only stops the
    # prior from demoting a row the user explicitly asked for.
    "compression-summary": 1.0,
}
DEFAULT_PRIOR = 0.6
# `--kinds` accepts part_types AND roles; a row is kept when EITHER matches
#
# `compression-summary` is a part_type, not a role: 1,607 rows carry it (all
# agent='bili'), and without it here `--kinds compression-summary` was an
# argparse error — the kind the whole bili lane exists for could not be
# named on the command line.
KINDS = sorted(
    {
        "text",
        "reasoning",
        "req-msg",
        "step-finish",
        "user",
        "assistant",
        "compression-summary",
        *TOOL_ROLES,
    }
)


def _prior_for(role: str, part_type: str) -> float:
    if part_type == "tool" or (role or "") in TOOL_ROLES:
        return TOOL_PRIOR
    return PART_PRIOR.get(part_type, DEFAULT_PRIOR)


# (harness, part_id) — part ids are only unique WITHIN a harness namespace.
Key = tuple[str, str]

# bili compression summaries are one part_type, not a harness: 1,607 rows
# spread over opencode/dsh/proxy (see HARNESSES above).
SUMMARY_PART = "compression-summary"


@dataclass(frozen=True)
class RowFilters:
    """Exact-match row constraints that must reach INSIDE the candidate legs.

    Why a leg-level filter and not a post-fusion one: the fused pool is
    `max(limit*4, 20)` rows (convo_query.search), so anything that is not
    already in those 20 can never be promoted by a re-weight. Measured
    2026-11-10, the best-matching bili summary for 'mem mtime gate' sat at
    overall cosine rank 4,511 with a *good* 0.63 cosine — it was simply
    never a candidate. Filtering in the leg means the pool is drawn from
    the filtered population instead of being whittled down after the fact.

    `--kinds` stays a post-fusion filter by design (it accepts roles as
    well as part_types and is a display preference); `--part-type` is the
    exact leg-level one.
    """

    agent: str | None = None
    part_type: str | None = None
    bili_active: bool | None = None
    bili_tier: int | None = None

    def any(self) -> bool:
        return (
            self.agent is not None
            or self.part_type is not None
            or self.bili_active is not None
            or self.bili_tier is not None
        )


@dataclass
class ConvoConnections:
    """The two-index handle: DuckDB pointers/vectors + FTS5 sidecar."""

    ddb: duckdb.DuckDBPyConnection
    sconn: sqlite3.Connection
    # Store path — the cache key for the exact-KNN matrix (S1 of proposal
    # convo_search_drop_hnsw_numpy_copy_compact). None for connections
    # built by hand (tests): the leg then skips the cache and reloads.
    db_path: Path | None = None

    def close(self) -> None:
        for c in (self.ddb, self.sconn):
            try:
                c.close()
            except Exception:  # noqa: S110  # close is best-effort
                pass


def connect(db_path: Path) -> ConvoConnections:
    # S2+S3 (duckdb_transient_lock_retry): queue on <db>.io.lock while a
    # long writer (rebuild_convo_search, ~2 min) holds LOCK_EX, retry
    # short foreign holders on the ladder; the flock releases at open —
    # DuckDB's own per-file lock protects the connection from then on.
    from helpers.misc.duckdb_lock import open_read_only

    con = open_read_only(db_path)
    # S3 (duckdb_pure_stack): the native FTS schema is the only index the
    # read path still needs. LOAD is session-scoped and legal on a
    # read-only connection; failure degrades to the sqlite fallback leg,
    # never raises (pre-S3 stores have no native schema).
    #
    # The vss/HNSW lines are GONE by design (proposal
    # convo_search_drop_hnsw_numpy_copy_compact S1): the cosine leg is an
    # exact numpy scan over the Arrow-loaded matrix, so no ANN index is
    # consulted. A store that still carries a persisted HNSW index from
    # the pre-2026-11-09 era opens fine without vss loaded (probed
    # 2026-11-09) — it is simply dead weight until the next --compact.
    try:
        con.execute("LOAD fts")
    except duckdb.Error:  # noqa: S110  # missing extension: leg degrades
        pass
    sconn = sqlite3.connect(  # noqa: S608  # FTS sidecar is a plain sqlite file, read-only URI
        f"file:{db_path.parent / FTS_DB_NAME}?mode=ro", uri=True
    )
    return ConvoConnections(con, sconn, Path(db_path))


def _stored_dims(con: ConvoConnections) -> int | None:
    # fetchall (not fetchone): a partially-consumed result blocks the
    # connection's relation API, and _load_matrix runs a .sql() relation on
    # this same connection right after this read.
    try:
        rows = con.ddb.execute("SELECT value FROM convo_meta WHERE key = 'embed_dims'").fetchall()
    except duckdb.CatalogException:
        return None
    return int(rows[0][0]) if rows else None


def _stored_model(con: ConvoConnections) -> str | None:
    """Index-side model label from convo_meta, or None when the sidecar
    predates the stamp (dims-only back-compat, not a reject)."""
    try:
        rows = con.ddb.execute("SELECT value FROM convo_meta WHERE key = 'embed_model'").fetchall()
    except duckdb.CatalogException:
        return None
    return rows[0][0] if rows else None


def _fts_hits(
    con: ConvoConnections, query: str, limit: int, harness: str | None = None
) -> list[tuple[Key, float]]:
    """Lexical bm25 hits: native DuckDB FTS first (S3), sqlite FTS5 fallback.

    Pre-S3 stores raise CatalogException on the native schema and take
    the unchanged sqlite leg. The duckdb form MUST filter IS NOT NULL:
    without it, once true matches fall below the limit, NULL-score rows
    leak into the tail (probe 2026-11-05) and a zero-match query returns
    arbitrary rows as hits.
    """
    if not query.strip():
        return []
    try:
        return _fts_hits_duckdb(con, query, limit, harness)
    except duckdb.CatalogException:
        return _fts_hits_sqlite(con, query, limit, harness)


def _fts_hits_duckdb(
    con: ConvoConnections, query: str, limit: int, harness: str | None = None
) -> list[tuple[Key, float]]:
    """Native match_bm25 over the stored full text (higher-is-better)."""
    q = " ".join(f'"{w.replace(chr(34), chr(34) * 2)}"' for w in query.split())
    sc = "fts_main_convo_search.match_bm25(part_id, ?)"
    sql = f"SELECT harness, part_id, {sc} FROM convo_search WHERE {sc} IS NOT NULL "  # noqa: S608  # sc is a constant score-expression string
    params: list = [q, q]
    if harness:
        # Filter in the leg, not after fusion: a post-fusion filter on a
        # fixed pool starves narrow harnesses on generic terms (bili lane:
        # "context" has 6839 corpus hits, 10 bili — top-20 fusion holds no
        # bili rows to filter down to).
        sql += "AND harness = ? "
        params.append(harness)
    sql += f"ORDER BY {sc} DESC LIMIT ?"
    params.extend([q, limit])
    rows = con.ddb.execute(sql, params).fetchall()
    return [((h, pid), -float(s)) for h, pid, s in rows]


def _fts_hits_sqlite(
    con: ConvoConnections, query: str, limit: int, harness: str | None = None
) -> list[tuple[Key, float]]:
    """Token-quoted FTS5 bm25 (lower-is-better rank → negate to score)."""
    q = " ".join(f'"{w.replace(chr(34), chr(34) * 2)}"' for w in query.split())
    sql = "SELECT harness, part_id, rank FROM convo_fts WHERE convo_fts MATCH ? "
    params: list = [q]
    if harness:
        sql += "AND harness = ? "
        params.append(harness)
    sql += "ORDER BY rank LIMIT ?"
    params.append(limit)
    rows = con.sconn.execute(sql, params).fetchall()
    return [((harness, pid), -float(rank)) for harness, pid, rank in rows]


# --- exact KNN leg (S1: proposal convo_search_drop_hnsw_numpy_copy_compact) ---
#
# The cosine leg used to be a DuckDB `array_cosine_distance` query routed
# through a persisted HNSW index. That index was the store's bloat engine:
# every incremental write rewrote the whole ~370 MB blob, stranding the
# blocks it vacated (measured 2026-11-09: +693 MB per 250-row wave with the
# index, flat without; DROP INDEX + CHECKPOINT reclaims 4.40 GB -> 3.71 GB
# in 0.9 s, while CHECKPOINT with the index reclaimed 0 bytes). It was also
# approximate: recall@10 0.887 mean, 0.30 min against exact cosine.
#
# So the index is gone and the leg is an EXACT numpy scan over the
# Arrow-loaded matrix, cached per process. Measured on the 203,921 x 384
# live corpus (DuckDB 2.0.0, pyarrow 26.0.1): Arrow fetch 264 ms, flatten
# 41 ms, matmul 86 ms, argsort 25 ms — and the fetch is paid once per
# process because the matrix is immutable between rebuilds.
#
# Invalidation is by (path, mtime_ns, size, dims): a rebuild or a --compact
# swaps the inode (os.replace) or rewrites the file, so the next query
# reloads without any explicit cache-busting call.
_MATRIX_CACHE_MAX = 2  # LRU entries; one store is ~320 MB of float32
_MATRIX_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_MATRIX_FALLBACK_WARNED = False


def warm_matrix(con: ConvoConnections) -> bool:
    """Pre-load the exact-KNN matrix so subsequent searches use it.

    Only worth calling from a process that will issue MANY queries: the load
    is ~0.9-1.7 s of Arrow transfer for 204k x 384. Every CLI invocation is
    one-shot and takes the in-database scan leg instead, so this exists for
    long-lived lanes (the TUI's in-process expand path, bench harnesses).
    Returns True when the matrix is resident afterwards.
    """
    dims = _stored_dims(con)
    if not dims:
        return False
    return _load_matrix(con, dims) is not None


def _convo_columns(con: ConvoConnections) -> set[str]:
    """Column names the store actually carries (migrations land over time)."""
    return {r[0] for r in con.ddb.execute("DESCRIBE convo_search").fetchall()}


def _matrix_cache_key(con: ConvoConnections, dims: int) -> tuple | None:
    """(path, mtime_ns, size, dims) or None when the path is unknown."""
    path = getattr(con, "db_path", None)
    if path is None:
        return None
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size, int(dims))


def _fetch_scan_tables(con: ConvoConnections):
    """Meta + embeddings Arrow tables, row-aligned by scan order.

    Two queries, not one: a wide select that mixes the FIXED_SIZE_LIST
    vector column with VARCHAR metadata is dramatically slower to
    materialize than the sum of its parts (measured on the 204k store:
    864 ms combined vs 161 ms embeddings-only + 18 ms meta-only). The
    row ORDER is identical (no ORDER BY, same scan order), which is what
    lets the row masks line up with the matrix.
    """
    try:
        cols = _convo_columns(con)
    except duckdb.Error:  # noqa: S110  # table absent: the caller test-reports it
        return None
    # Columns arrive in migrations (provenance 2026-11-09, bili_* 2026-11-10),
    # so the select is built from what THIS store actually has rather than
    # assuming the newest schema — a pre-migration store must still answer
    # a plain query instead of raising CatalogException on an unknown column.
    want = ["harness", "part_id"]
    want += [
        c for c in ("provenance", "agent", "part_type", "bili_active", "bili_tier") if c in cols
    ]
    try:
        meta = con.ddb.sql(
            f"SELECT {', '.join(want)} FROM convo_search WHERE embedding IS NOT NULL"  # noqa: S608  # want is a closed list of known column names
        ).to_arrow_table()
        emb_tbl = con.ddb.sql(
            "SELECT embedding FROM convo_search WHERE embedding IS NOT NULL"
        ).to_arrow_table()
    except Exception:  # noqa: BLE001  # any read shape we cannot plan -> SQL leg
        return None
    if emb_tbl.num_rows == 0 or meta.num_rows != emb_tbl.num_rows:
        # A row-count disagreement means the two scans saw different
        # snapshots (concurrent write) — the masks would silently misalign.
        return None
    return meta, emb_tbl


def _matrix_entry(tbl, emb_tbl, n_rows: int, dims: int, key) -> dict | None:
    """Validate the Arrow shape and build the cacheable matrix entry.

    Degrades to None (SQL leg) on any shape we cannot treat as a dense
    matrix: variable-length lists, ragged rows, or a stored width that
    disagrees with convo_meta's embed_dims.
    """
    import pyarrow as pa

    emb = emb_tbl.column("embedding").combine_chunks()
    if not pa.types.is_fixed_size_list(emb.type):
        # variable-length list: an all-NULL tail or ragged rows would make the
        # reshape a lie — take the SQL leg instead of guessing.
        return None
    width = int(emb.type.list_size)
    if width != int(dims):
        # The stored vectors and convo_meta disagree; the old SQL leg raised a
        # DuckDB cast error here. Degrade to the lexical leg instead of dying.
        if key is not None:
            log.warning(
                "convo_search embedding width %s != embed_dims %s — cosine leg disabled",
                width,
                dims,
            )
        return None
    import numpy as np
    import pyarrow.compute as pc

    mat = emb.flatten().to_numpy(zero_copy_only=False).reshape(n_rows, width)
    mat = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1)
    return {
        "table": tbl,
        "mat": mat,
        "norms": norms,
        "harness": tbl.column("harness"),
        "part_id": tbl.column("part_id"),
        "provenance": tbl.column("provenance") if "provenance" in tbl.column_names else None,
        # Filter columns, absent (None) on a pre-migration store. `agent` and
        # `part_type` date from the first build; the bili pair is S3 of
        # proposal bili_first_class_convo_search.
        "agent": tbl.column("agent") if "agent" in tbl.column_names else None,
        "part_type": tbl.column("part_type") if "part_type" in tbl.column_names else None,
        "bili_active": (tbl.column("bili_active") if "bili_active" in tbl.column_names else None),
        "bili_tier": tbl.column("bili_tier") if "bili_tier" in tbl.column_names else None,
        "pc": pc,
    }


def _load_matrix(con: ConvoConnections, dims: int, *, cache_only: bool = False) -> dict | None:
    """Whole-store embedding matrix + row keys, Arrow -> numpy (cached).

    Returns None when numpy/pyarrow is unavailable or the store cannot be
    read this way; the caller then takes the SQL leg. The returned dict
    keeps the Arrow table alive because `mat` is a zero-copy view into
    its buffers.
    """
    try:
        import numpy  # noqa: F401  # availability guard: the helpers below import unguarded
        import pyarrow.compute  # noqa: F401
    except ImportError:  # pragma: no cover - both are declared runtime deps
        return None
    key = _matrix_cache_key(con, dims)
    if key is None:
        # No stable identity for this store (in-memory / served attach) — we
        # could never cache it, so never pay to build it.
        return None
    hit = _MATRIX_CACHE.get(key)
    if hit is not None:
        _MATRIX_CACHE.move_to_end(key)
        return hit
    if cache_only:
        return None
    fetched = _fetch_scan_tables(con)
    if fetched is None:
        return None
    tbl, emb_tbl = fetched
    entry = _matrix_entry(tbl, emb_tbl, emb_tbl.num_rows, dims, key)
    if entry is None:
        return None
    if key is not None:
        _MATRIX_CACHE[key] = entry
        _MATRIX_CACHE.move_to_end(key)
        while len(_MATRIX_CACHE) > _MATRIX_CACHE_MAX:
            _MATRIX_CACHE.popitem(last=False)
    return entry


def _source_mask(pc, entry: dict, source: str):
    """Source-filter mask; None when the provenance column is absent."""
    prov = entry["provenance"]
    if prov is None:
        return None  # column absent: nothing can match
    if source == "*":
        return pc.fill_null(pc.not_equal(prov, ""), False).to_numpy(zero_copy_only=False)
    return pc.fill_null(pc.match_substring(prov, source), False).to_numpy(zero_copy_only=False)


def _rf_masks(pc, entry: dict, rf: RowFilters) -> list | None:
    """Exact-match filter-group masks; None when a needed column is absent."""
    masks = []
    for col, want in (
        ("agent", rf.agent),
        ("part_type", rf.part_type),
    ):
        if want is None:
            continue
        arr = entry.get(col)
        if arr is None:
            return None  # column absent: nothing can match
        masks.append(pc.fill_null(pc.equal(arr, want), False).to_numpy(zero_copy_only=False))
    if rf.bili_active is not None:
        arr = entry.get("bili_active")
        if arr is None:
            return None
        # NULL means "not a bili row / payload without the field" — it
        # must NOT satisfy either --bili-active 0 or 1.
        masks.append(
            pc.fill_null(pc.equal(arr, bool(rf.bili_active)), False).to_numpy(zero_copy_only=False)
        )
    if rf.bili_tier is not None:
        arr = entry.get("bili_tier")
        if arr is None:
            return None
        masks.append(
            pc.fill_null(pc.equal(pc.cast(arr, "int64"), int(rf.bili_tier)), False).to_numpy(
                zero_copy_only=False
            )
        )
    return masks


def _row_mask(
    entry: dict,
    harness: str | None,
    source: str | None,
    rf: RowFilters | None = None,
):
    """Boolean row mask for the leg's optional filters, None when unfiltered.

    `source` matches by substring, which is exactly what `search()`'s
    post-filter (`source in prov`) and the SQL leg's `LIKE '%<escaped>%'`
    both reduce to (`_like_pattern` escapes the source's own % and _, so the
    wrapped pattern is a literal containment test). "*" means non-empty.

    `rf` is the exact-match `RowFilters` group (--agent/--part-type/
    --bili-active/--bili-tier). It applies HERE, before the top-k selection,
    so the candidate pool is drawn from the filtered population — the whole
    point of S1: a bili summary at overall cosine rank 4,511 becomes rank ≤20
    *within* the summary population and therefore a real candidate.
    Returns False (not None) when a filter can match nothing at all.
    """
    import numpy as np

    pc = entry["pc"]
    mask = None

    def _conj(m):
        nonlocal mask
        mask = m if mask is None else (mask & m)

    if harness:
        _conj(
            pc.fill_null(pc.equal(entry["harness"], harness), False).to_numpy(zero_copy_only=False)
        )
    if source:
        sm = _source_mask(pc, entry, source)
        if sm is None:
            return False  # column absent: nothing can match
        _conj(sm)
    if rf is not None and rf.any():
        rms = _rf_masks(pc, entry, rf)
        if rms is None:
            return False  # column absent: nothing can match
        for m in rms:
            _conj(m)
    if mask is not None:
        mask = np.asarray(mask, dtype=bool)
    return mask


def _cos_hits_numpy(
    con: ConvoConnections,
    qv: list[float],
    limit: int,
    dims: int,
    harness: str | None,
    source: str | None,
    *,
    cache_only: bool = False,
    rf: RowFilters | None = None,
) -> list[tuple[Key, float]] | None:
    """Exact top-N cosine over the in-memory matrix; None = leg unavailable.

    cache_only returns None instead of materializing the matrix. The automatic
    dispatch sets it: paying the ~313 MB load to answer ONE query is a
    regression against the in-database scan, and only a process that intends
    to ask repeatedly should pay it up front (see warm_matrix).
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return None
    entry = _load_matrix(con, dims, cache_only=cache_only)
    if entry is None:
        return None
    q = np.asarray(qv, dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return []
    q = q / qn
    scores = entry["mat"] @ q  # rows are unit-norm in the live corpus
    norms = entry["norms"]
    if not np.allclose(norms, 1.0, atol=1e-3):
        scores = scores / np.where(norms == 0.0, 1.0, norms)
    mask = _row_mask(entry, harness, source, rf)
    if mask is False:
        return []
    if mask is not None:
        idx = np.flatnonzero(np.asarray(mask, dtype=bool))
        if idx.size == 0:
            return []
        sub = scores[idx]
        k = min(int(limit), int(idx.size))
        order = np.argsort(-sub, kind="stable")[:k]
        sel = idx[order]
    else:
        sel = np.argsort(-scores, kind="stable")[: int(limit)]
    if sel.size == 0:
        return []
    dist = 1.0 - scores[sel]  # array_cosine_distance for unit vectors
    out: list[tuple[Key, float]] = []
    harnesses = entry["harness"].take(_pa_indices(sel)).to_pylist()
    part_ids = entry["part_id"].take(_pa_indices(sel)).to_pylist()
    for h, pid, d in zip(harnesses, part_ids, dist, strict=True):
        out.append(((h, pid), 1.0 - float(d) * float(d) / 2.0))
    return out


def _pa_indices(sel):
    import pyarrow as pa

    return pa.array([int(i) for i in sel], type=pa.int64())


def _cos_hits_sql(
    con: ConvoConnections,
    qv: list[float],
    limit: int,
    dims: int,
    harness: str | None,
    source: str | None,
    rf: RowFilters | None = None,
) -> list[tuple[Key, float]]:
    """Exact in-DuckDB cosine scan — the fallback when numpy is unavailable.

    `array_cosine_distance` is a core DuckDB function (no vss extension
    needed). With the HNSW index gone this is a plain full scan, so it is
    exact too; it costs ~118 ms/query against the in-memory path's ~86 ms
    but needs no 313 MB resident matrix.

    The `rf` filters pre-filter in SQL for the same reason they pre-filter
    the numpy mask: a post-fusion filter can only subtract from a pool that
    was drawn without it (see RowFilters).
    """
    # cast width MUST match the stored vectors (384 for the house embedder,
    # _PSEUDO_DIMS for the degraded path) — a hardcoded 384 breaks pseudo mode.
    cast = f"FLOAT[{int(dims)}]"
    sql = (
        "SELECT harness, part_id, array_cosine_distance("  # noqa: S608  # cast width is int(dims) from the stored index; vector + limit are bound
        f"embedding::{cast}, ?::{cast}) dist "
        "FROM convo_search WHERE embedding IS NOT NULL "
    )
    params: list = [list(qv)]
    if harness:
        sql += "AND harness = ? "
        params.append(harness)
    if source:
        # Pre-filter in SQL on the leg that CAN: `convo_search` carries
        # provenance, so this is exact and free. The FTS leg cannot do the same
        # — `convo_fts` lives in a separate sqlite file with no provenance
        # column — which is why the search() caller over-fetches that leg.
        sql += "AND provenance LIKE ? ESCAPE '\\' "
        params.append(_like_pattern(source))
    sql += _rf_sql(rf, params)
    sql += "ORDER BY dist ASC LIMIT ?"
    params.append(limit)
    rows = con.ddb.execute(sql, params).fetchall()
    # Convert distance to similarity: cos = 1 - d*d/2 for unit vectors
    return [((h, pid), 1.0 - float(dist) * float(dist) / 2.0) for h, pid, dist in rows]


def _rf_sql(rf: RowFilters | None, params: list) -> str:
    """AND-clause for the exact-match filters; appends its own bound params.

    Exact equality, never LIKE: these are closed vocabularies (agent names,
    part_types, booleans, small tier ints). NULL never matches here, which is
    the required behaviour — a NULL `bili_active` means "not a bili row", and
    `--bili-active 0` must not sweep those in.
    """
    if rf is None or not rf.any():
        return ""
    out = ""
    for col, want in (("agent", rf.agent), ("part_type", rf.part_type)):
        if want is not None:
            out += f"AND {col} = ? "  # noqa: S608  # col is a closed literal set
            params.append(want)
    if rf.bili_active is not None:
        out += "AND bili_active = ? "
        params.append(bool(rf.bili_active))
    if rf.bili_tier is not None:
        out += "AND bili_tier = ? "
        params.append(int(rf.bili_tier))
    return out


def _cos_hits(
    con: ConvoConnections,
    query: str,
    limit: int,
    dims: int,
    query_vec=None,
    harness: str | None = None,
    source: str | None = None,
    rf: RowFilters | None = None,
) -> list[tuple[Key, float]]:
    from helpers.core import local_embedder as le
    from helpers.maintenance import rebuild_common as rbc

    qv: list[float] | None = None
    if query_vec is not None:
        qv = rbc.check_query_vector(_stored_model(con), dims, query_vec)
    if qv is None:
        if le.available():
            qv = le.embed_query(query)
        else:
            from helpers.graph.embeddings import _pseudo_embedding

            qv = _pseudo_embedding(query, dims)
    if len(qv) != dims:
        return []
    # Dispatch order is deliberate: the numpy leg WINS only when the matrix is
    # already resident in this process. Materializing it costs a ~313 MB Arrow
    # transfer, which dominates a one-shot invocation (measured 2026-11-09 on
    # the 204k store: 3.5-4.5 s end-to-end vs 1.5 s for the in-DB scan). The
    # CLI lane — and therefore every master_query/TUI call, which subprocesses
    # this file — is always one-shot, so it takes the SQL leg. A long-lived
    # caller (search_tui_app in-process, bench harnesses) calls
    # warm_matrix() once and gets the 30-90 ms numpy path from then on.
    hits = _cos_hits_numpy(con, qv, limit, dims, harness, source, cache_only=True, rf=rf)
    if hits is not None:
        return hits
    return _cos_hits_sql(con, qv, limit, dims, harness, source, rf)


def _like_pattern(source: str) -> str:
    """`orphan:` -> `%orphan:%`; `*` -> any non-empty provenance."""
    if source == "*":
        return "%"
    return "%" + source.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


from helpers.core.rank_fusion import anchored_fusion as _anchored_fusion  # noqa: E402  (algorithms_adoption_sweep S7 — the convo named variant lives in the one RRF home; frozen-bank verdict 2026-10-27, #442: granite 25/57, gemma 27/57)


def _fetch_rows(con: ConvoConnections, keys: list[Key]) -> dict[Key, dict]:
    if not keys:
        return {}
    where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(keys))
    params = [v for k in keys for v in k]
    rows = con.ddb.execute(
        "SELECT harness, session_id, part_id, file_path, row_no, ts, role, "  # noqa: S608  # where is a fixed OR-pattern of bound placeholders; all values parameterized
        "agent, model, part_type, text_len, snippet, provenance FROM convo_search "
        f"WHERE {where}",
        params,
    ).fetchall()
    names = (
        "harness",
        "session_id",
        "part_id",
        "file_path",
        "row_no",
        "ts",
        "role",
        "agent",
        "model",
        "part_type",
        "text_len",
        "snippet",
        # corpus `source`, indexed as `provenance`: `opencode:live`,
        # `opencode-snap:<ts>`, or `orphan:<harness>` for a session the harness
        # dropped. '' on a pre-migration row (see _provenance_migration).
        "provenance",
    )
    result = {}
    for r in rows:
        rec = dict(zip(names, r))
        # S6(b) convention: an absent model is recorded as 'unrecorded' so it
        # is never mistaken for a missing model column. Blank rows are permanent
        # (their source parquets carry no model field) — see
        # doc/improvements/archive/convo_search/convo_reasoning_capture.md §3.6.
        if not (rec.get("model") or "").strip():
            rec["model"] = "unrecorded"
        result[(rec["harness"], rec["part_id"])] = rec
    return result


def _rf_keys(con: ConvoConnections, keys: list[Key], rf: RowFilters) -> set[Key]:
    """Keys among `keys` that satisfy the exact-match filters.

    One OR-ed query for the whole candidate set (same shape as _kinds_of).
    A store too old to have the bili columns answers with none of them —
    the SELECT lists only the columns this store actually carries.
    """
    if not keys:
        return set()
    try:
        cols = _convo_columns(con)
    except duckdb.Error:
        return set(keys)
    want = [c for c in ("agent", "part_type", "bili_active", "bili_tier") if c in cols]
    if not want:
        return set(keys)  # nothing to filter on: claim nothing, drop nothing
    where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(keys))
    rows = con.ddb.execute(
        f"SELECT harness, part_id, {', '.join(want)} FROM convo_search WHERE {where}",  # noqa: S608  # want is a closed list of known columns; the OR-pattern is fixed
        [v for k in keys for v in k],
    ).fetchall()
    keep: set[Key] = set()
    for r in rows:
        rec = dict(zip(["harness", "part_id", *want], r, strict=True))
        if rf.agent is not None and rec.get("agent") != rf.agent:
            continue
        if rf.part_type is not None and rec.get("part_type") != rf.part_type:
            continue
        if rf.bili_active is not None and rec.get("bili_active") is not rf.bili_active:
            continue
        if rf.bili_tier is not None and rec.get("bili_tier") != rf.bili_tier:
            continue
        keep.add((rec["harness"], rec["part_id"]))
    return keep


def _partition_summaries(
    con: ConvoConnections, ranked: list[tuple[Key, float]]
) -> list[tuple[Key, float]]:
    """Summaries first, preserving within-group order; NOT a re-rank.

    `--summary-first` promises an ordering, so it must be a partition: a
    score multiplier would silently reorder both groups and could not even
    promote a summary that is outside the fused pool (measured 2026-11-10:
    best summary at overall cosine rank 4,511 for 'mem mtime gate').
    """
    if not ranked:
        return ranked
    kinds_of = _kinds_of(con, [k for k, _ in ranked])
    first = [kv for kv in ranked if kinds_of.get(kv[0], ("", ""))[1] == SUMMARY_PART]
    rest = [kv for kv in ranked if kinds_of.get(kv[0], ("", ""))[1] != SUMMARY_PART]
    return first + rest


def _kinds_of(con: ConvoConnections, keys: list[Key]) -> dict[Key, tuple[str, str]]:
    """(role, part_type) per candidate — one OR-ed query for the whole pool.

    The FTS sidecar stores no part_type, so the type comes from here.
    """
    if not keys:
        return {}
    where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(keys))
    rows = con.ddb.execute(
        f"SELECT harness, part_id, role, part_type FROM convo_search WHERE {where}",  # noqa: S608
        [v for k in keys for v in k],
    ).fetchall()
    return {(h, p): (role or "", t or "") for h, p, role, t in rows}


def _apply_prior(
    con: ConvoConnections, ranked: list[tuple[Key, float]], kinds: list[str] | None
) -> list[tuple[Key, float]]:
    """Re-weight the fused ranking by row kind, then hard-filter if asked."""
    if not ranked:
        return []
    kinds_of = _kinds_of(con, [k for k, _ in ranked])
    allowed = set(kinds) if kinds else None
    out = []
    for key, score in ranked:
        role, ptype = kinds_of.get(key, ("", ""))
        if allowed is not None and ptype not in allowed and role not in allowed:
            continue
        out.append((key, score * _prior_for(role, ptype)))
    return sorted(out, key=lambda kv: (-kv[1], kv[0]))


def search(
    con: ConvoConnections,
    query: str,
    limit: int = 5,
    hybrid: bool = True,
    harness: str | None = None,
    kinds: list[str] | None = None,
    query_vec=None,
    source: str | None = None,
    rf: RowFilters | None = None,
    summary_first: bool = False,
) -> dict:
    """Hybrid (default) or bm25-only search; returns the result dicts.

    ``kinds`` restricts hits by part_type (e.g. ``["text", "reasoning"]``
    for pure conversation). When it is set the candidate pool is
    over-fetched, otherwise a tool-heavy result set would starve the
    filter. ``query_vec`` (shared_query_vector): a parent-fanned-out
    embedding used instead of a local model load when its stamp matches;
    on mismatch the leg embeds locally as before.

    `rf` is the exact-match filter group and reaches INSIDE both legs — see
    RowFilters for why post-fusion filtering cannot serve the bili lane.

    `summary_first` is a PARTITION, not a boost: the compression summaries
    keep their within-group order and are placed ahead of everything else,
    each group still ordered by the fused score. A multiplier was measured
    and rejected (2026-11-10): the best summary sits at overall cosine rank
    4,511, so no post-fusion weight can lift it into a 20-row pool. A
    partition is honest about what it does — it answers "show me the
    summaries for this" without claiming they are the *closest* rows.
    """
    dims = _stored_dims(con)
    # A filtered leg selects from the filtered population, so the pool stays
    # limit-sized: over-fetching would only cost latency. `kinds` still needs
    # its 3x because it filters AFTER fusion.
    pool = max(limit * 4, 20) * (3 if kinds else 1)
    # The FTS leg lives in a separate sqlite file with no provenance column, so
    # it cannot pre-filter and has to over-fetch instead. `orphan:` is ~10% of
    # the corpus (11,711 of 121,007 rows), so a 25x pool still lands ~60
    # candidates for a 5-hit page; the cosine leg filters in SQL exactly.
    fts_pool = pool * (25 if source else 1)
    fts = _fts_hits(con, query, fts_pool, harness)
    if not hybrid or not dims:
        ranked = fts
        mode = "bm25"
    else:
        cos = _cos_hits(con, query, pool, dims, query_vec, harness=harness, source=source, rf=rf)
        ranked = _anchored_fusion(fts, cos)
        mode = "hybrid"
    ranked = _apply_prior(con, ranked, kinds)
    if rf is not None and rf.any():
        # Safety net, not the primary filter: both cosine legs already
        # pre-filtered, but the FTS leg can only post-filter (its sidecar
        # carries no agent/part_type — see _fts_hits). Subtract, never
        # starve: the cosine pool was drawn from the filtered population.
        keep = _rf_keys(con, [k for k, _ in ranked], rf)
        ranked = [(k, s) for k, s in ranked if k in keep]
    if summary_first:
        ranked = _partition_summaries(con, ranked)
        # Provenance is a post-filter, not a pre-filter: the candidate pool is
        # over-fetched for `kinds` but a provenance filter would need its own
        # over-fetch to avoid starving on an uncommon label (e.g. `orphan:`),
        # so filter after the fact and accept that a narrow label returns fewer
        # than `limit` on a small corpus.
        by_key = _fetch_rows(con, [k for k, _ in ranked])
        if harness:
            ranked = [(k, s) for k, s in ranked if by_key.get(k, {}).get("harness") == harness]
        if source:
            want = source == "*"

            def _keep(rec: dict) -> bool:
                prov = rec.get("provenance") or ""
                return bool(prov) if want else source in prov

            ranked = [(k, s) for k, s in ranked if _keep(by_key.get(k, {}))]
    top = ranked[:limit]
    by_key = _fetch_rows(con, [k for k, _ in top])
    results = []
    for rank, (key, score) in enumerate(top, 1):
        r = by_key.get(key)
        if not r:
            continue
        results.append(
            {
                "rank": rank,
                "score": round(score, 4),
                "mode": mode,
                "harness": r["harness"],
                "session_id": r["session_id"],
                "ts": r["ts"].isoformat(sep=" ") if r["ts"] else "",
                "role": r["role"],
                "part_type": r["part_type"],
                "text_len": r["text_len"],
                "model": r.get("model") or "unrecorded",
                "head": r["snippet"][:HEAD],
                "provenance": r["provenance"],
                "pointer": f"{r['file_path']}:{r['part_id']}",
            }
        )
    return {"mode": mode, "results": results}


def _stale_warning(con: ConvoConnections) -> str | None:
    try:
        rows = con.ddb.execute("SELECT COUNT(*) FROM convo_search").fetchall()
        if rows and rows[0][0]:
            return None
    except duckdb.CatalogException:
        pass
    return "convo_search index missing/empty — build it: make convo-fresh APPLY=1"


def expand(pointer: str, max_chars: int = 4000) -> dict:
    """Resolve a corpus pointer to the full corpus row.

    S4 (convo_search_dead_block_compaction): pointers are part-addressed
    — ``file.parquet:<part_id>`` — because harness compactions renumber
    rows while part_ids stay stable, so a positional pointer would rot
    between index refreshes. Legacy ``file.parquet:<row_no>`` pointers
    (older logs/reports) still resolve positionally.

    The index stores only an 8 KiB snippet; the body never enters the
    db. This is the read path the TUI preview uses.
    """
    # Anchor the split on ".parquet", not rpartition(":"): part_id itself
    # contains a colon (`{sid}:{seq}[#{i}]`), so the LAST colon of the
    # pointer sits inside the part id, and the path never does.
    marker = ".parquet"
    cut = pointer.find(marker)
    if cut == -1:
        raise ValueError(f"not a corpus pointer: {pointer!r}")
    rel = pointer[: cut + len(marker)]
    tail = pointer[cut + len(marker) :]
    tail = tail[1:] if tail.startswith(":") else tail
    if not tail:
        raise ValueError(f"not a corpus pointer: {pointer!r}")
    p = Path(rel)
    if not p.is_absolute():
        p = REPO / p
    if not p.exists():
        raise FileNotFoundError(f"corpus file gone: {p}")
    import pyarrow.parquet as pq

    tbl = pq.read_table(p)
    if tail.isdigit():
        # legacy positional pointer (pre-S4): row_no into the parquet
        n = int(tail)
        if not 0 <= n < tbl.num_rows:
            raise IndexError(f"row {n} out of range for {p.name} ({tbl.num_rows} rows)")
        rec = tbl.slice(n, 1).to_pylist()[0]
    else:
        # part-addressed pointer (S4): part_ids are stable across
        # compactions, so filter instead of slicing by position
        if "part_id" not in tbl.column_names:
            raise ValueError(f"corpus file has no part_id column: {p.name}")
        idx = tbl.column_names.index("part_id")
        matches = [i for i, v in enumerate(tbl.column(idx).to_pylist()) if v == tail]
        if not matches:
            raise IndexError(f"part {tail!r} not found in {p.name} ({tbl.num_rows} rows)")
        rec = tbl.slice(matches[0], 1).to_pylist()[0]
    text = rec.get("text") or ""
    # the corpus carries no harness column — the segment before
    # `conversations/` is the namespace (same rule as the index builder)
    parts = p.parts
    harness = parts[parts.index("conversations") - 1] if "conversations" in parts else "unknown"
    return {
        **rec,
        "harness": harness,
        # the corpus carries no `text_len` column (that lives only in the
        # DuckDB index) — derive it here so both call sites can rely on it
        "text_len": len(text),
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "pointer": pointer,
    }


def render_hits(results: list[dict]) -> None:
    for r in results:
        print(
            f"{r['rank']:>2}. [{r['score']:.3f} {r['mode']}] "
            f"{r['harness']}/{r['part_type']}/{r['role'] or '-'} "
            f"{r['ts'][:16]} len={r['text_len']} model={r.get('model') or 'unrecorded'}"
        )
        print(f"    {r['head'].replace(chr(10), ' ')}")
        # Provenance only when it says something: `opencode:live` is the
        # uninteresting case, `orphan:` and `opencode-snap:` are not.
        prov = r.get("provenance") or ""
        if prov and prov != "opencode:live":
            print(f"    provenance: {prov}")
        print(f"    {r['pointer']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", help="free-text query; punctuation is safe")
    p.add_argument("--limit", type=int, default=5, help="max hits (default 5)")
    p.add_argument("--db", default=str(DEFAULT_DB), help="convo_search duckdb path")
    p.add_argument("--bm25", action="store_true", help="lexical leg only (skip cosine)")
    p.add_argument(
        "--query-vector",
        default=None,
        metavar="JSON",
        help="shared query embedding (shared_query_vector: parent fanned-out "
        '{"model","dims","vector"} — skips this leg\'s own model load; '
        "absent/garbled = embed locally as before)",
    )
    p.add_argument("--harness", default=None, choices=HARNESSES, help="filter hits to one harness")
    p.add_argument(
        "--source",
        default=None,
        metavar="PATTERN",
        help="keep only hits whose provenance matches PATTERN — 'orphan:' for "
        "sessions the harness deleted, 'opencode-snap:' for snapshot-backed "
        "retention, 'opencode:live' for what the harness still has. "
        "'*' matches any non-empty provenance (i.e. a migrated index).",
    )
    p.add_argument(
        "--kinds",
        default=None,
        help=f"comma-separated part_types to keep, e.g. text,reasoning (all: {','.join(KINDS)})",
    )
    p.add_argument(
        "--agent",
        default=None,
        metavar="NAME",
        help="exact agent filter, e.g. bili (leg-level: the candidate pool is "
        "drawn from the filtered population, not filtered afterwards)",
    )
    p.add_argument(
        "--part-type",
        default=None,
        metavar="TYPE",
        help="exact part_type filter, e.g. compression-summary (leg-level, "
        "unlike --kinds which is a post-fusion display filter that also "
        "accepts roles)",
    )
    p.add_argument(
        "--bili-active",
        default=None,
        metavar="{0,1}",
        help="bili lifecycle filter: 1 = meta.active true, 0 = superseded; "
        "omit for both. NULL (non-bili or payload without the field) never "
        "matches either value",
    )
    p.add_argument(
        "--bili-tier",
        default=None,
        type=int,
        metavar="N",
        help="keep only bili rows whose meta.tier == N",
    )
    p.add_argument(
        "--summary-first",
        action="store_true",
        help="partition the hits: compression summaries first (their own "
        "order preserved), everything else after — not a score boost",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the raw result dicts as JSON",
    )
    p.add_argument(
        "--expand",
        default=None,
        metavar="POINTER",
        help="print the full body of a file.parquet:<row> pointer",
    )
    args = p.parse_args(argv)

    if args.expand:
        rec = expand(args.expand)
        if args.as_json:
            print(json.dumps(rec, indent=2, default=str))
        else:
            head = (
                f"{rec['harness']}/{rec['part_type']}/{rec['role'] or '-'} "
                f"{rec['ts']} session={rec['session_id']} ({rec['text_len']} chars)"
            )
            print(head, file=sys.stderr)
            print(rec["text"] + ("\n[truncated]" if rec["truncated"] else ""))
        return 0

    con = connect(Path(args.db))
    try:
        warning = _stale_warning(con)
        if warning:
            print(warning, file=sys.stderr)
            return 1
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] if args.kinds else None
        rf = RowFilters(
            agent=args.agent,
            part_type=args.part_type,
            bili_active=None if args.bili_active is None else bool(int(args.bili_active)),
            bili_tier=args.bili_tier,
        )
        if kinds:
            unknown = [k for k in kinds if k not in KINDS]
            if unknown:
                p.error(f"unknown part_type(s) {unknown}; known: {','.join(KINDS)}")
        from helpers.maintenance import rebuild_common as rbc

        out = search(
            con,
            args.query,
            limit=max(1, min(args.limit, 50)),
            hybrid=not args.bm25,
            harness=args.harness,
            kinds=kinds,
            query_vec=rbc.parse_query_vector_flag(args.query_vector),
            source=args.source,
            rf=rf,
            summary_first=args.summary_first,
        )
    finally:
        con.close()

    if args.as_json:
        print(json.dumps({"mode": out["mode"], "results": out["results"]}, indent=2))
        return 0
    if not out["results"]:
        print(f"(no hits for {args.query!r}; mode={out['mode']})", file=sys.stderr)
        return 0
    print(f"# {len(out['results'])} hit(s), mode={out['mode']}", file=sys.stderr)
    render_hits(out["results"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
