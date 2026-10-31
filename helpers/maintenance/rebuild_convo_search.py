#!/usr/bin/env python3
"""Rebuild the `convo_search` index over the harvested conversation corpus.

The index is POINTERS, not payloads: one DuckDB table
(``memory/convo_search.duckdb``) keyed (harness, part_id) with the
parquet file/row pointer, digest metadata, an 8 KiB snippet and the
granite 384-d embedding; lexical recall lives in the SQLite FTS5
sidecar (``memory/convo_search_fts.db``, porter). The corpus itself is
the parquet archive written by ``harvest_conversations.py`` — the index
is fully rebuildable from it and never stores full bodies (convo_search
proposal, 2026-09-27; trial-measured: FTS ~1 ms, cosine linear-scan
sub-second at corpus scale, batch≈serial per-text on 1 KiB texts, so
the cold embed rides the pinned pool via ``embed_documents_parallel``
behind the shared ``embed_cache``).

Staleness contract mirrors the search-fresh family: per-corpus-file
blake2b fingerprint; ``--check`` reports new/changed/deleted drift and
exits 1 without writing; default rebuilds EVERYTHING (cheap after the
first pass — the content-addressed cache absorbs unchanged snippets);
``--incremental`` reprocesses only changed files.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    # Repo root on sys.path BEFORE helpers.* imports so the script works
    # as a bare `python3 helpers/maintenance/rebuild_convo_search.py` (the
    # rebuild_*_search convention, Makefile search-fresh shape).
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb  # noqa: E402  # after the sys.path shim (bare-script convention)

from helpers.core import duckdb_shared as ds  # noqa: E402  (bare-script entry point; bootstrap above)
from helpers.misc.duckdb_lock import connect_with_lock_retry, io_lock, open_read_only  # noqa: E402

REPO = _REPO_ROOT
CORPUS_DIR_GLOB = "memory/data/harness/*/conversations/*.parquet"
FTS_DB_NAME = "convo_search_fts.db"
SNIPPET_CAP = 8 * 1024
MIN_TEXT_LEN = 1
EMBED_DIMS = 384
_PSEUDO_DIMS = 64
# Protocol markers, not conversation: opencode writes a `step-finish` part
# per step whose text is "tool-calls"/"stop" (15,449 corpus rows, and the
# single digest "tool-calls" repeated 14,375 times). The CORPUS keeps
# everything — it is the archive of record — but indexing them spends
# embeddings on the word "stop" and pollutes lexical recall.
SKIP_PART_PREFIXES = ("step-",)

_pseudo_warned = False

CONVO_SEARCH_DDL = """
CREATE TABLE IF NOT EXISTS convo_search (
  harness VARCHAR, session_id VARCHAR, part_id VARCHAR,
  file_path VARCHAR, row_no BIGINT,
  ts TIMESTAMP, role VARCHAR, agent VARCHAR, model VARCHAR, part_type VARCHAR,
  text_len BIGINT, snippet VARCHAR, embedding FLOAT[],
  provenance VARCHAR DEFAULT '',
  PRIMARY KEY (harness, part_id)
)
"""
CONVO_META_DDL = "CREATE TABLE IF NOT EXISTS convo_meta (key VARCHAR PRIMARY KEY, value VARCHAR)"
FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS convo_fts USING fts5("
    "text, harness UNINDEXED, part_id UNINDEXED, session_id UNINDEXED, "
    "tokenize='porter unicode61')"
)


def fts_db_for(db_path: Path) -> Path:
    return db_path.parent / FTS_DB_NAME


HARVEST_META_DDL = (
    "CREATE TABLE IF NOT EXISTS harvest_meta ("
    "source VARCHAR PRIMARY KEY, watermark VARCHAR, updated_ts TIMESTAMP)"
)
PART_INDEX_DDL = (
    "CREATE TABLE IF NOT EXISTS part_index ("
    "harness VARCHAR, part_id VARCHAR, session_id VARCHAR, "
    "PRIMARY KEY (harness, part_id))"
)


def _carry_harvest_state(new_con: duckdb.DuckDBPyConnection, old_path: Path) -> None:
    """Mirror harvest_meta + part_index into the fresh swap store.

    The swap builds only the index tables; without this, every swap
    silently dropped the harvest watermarks and the next harvest re-read
    the whole corpus (full re-ingest, re-embedding ~139k rows), and the
    schema dump drifted until the next write-harvest re-created the
    tables. Best-effort: when the old store is unreadable (partial file,
    version skew) the state is skipped with a warning — a missing
    watermark degrades to a slower harvest, never to a wrong one.

    Wire-safe: the old store may be read via the Slice B funnel (a
    resident server holds it rw, excluding file-mode opens) even though
    the swap itself refuses to run served."""
    new_con.execute(HARVEST_META_DDL)
    new_con.execute(PART_INDEX_DDL)
    if not old_path.exists():
        return
    try:
        from helpers.misc.duckdb_lock import open_read_only

        old = open_read_only(old_path)
        try:
            wm = old.execute("SELECT source, watermark, updated_ts FROM harvest_meta").fetchall()
            parts = old.execute("SELECT harness, part_id, session_id FROM part_index").fetchall()
        finally:
            old.close()
    except Exception as exc:  # noqa: BLE001 — degraded carry is not fatal
        print(
            f"  [swap] harvest state not carried ({type(exc).__name__}: {exc}); "
            "next harvest re-reads sources"
        )
        return
    if wm:
        new_con.executemany("INSERT OR REPLACE INTO harvest_meta VALUES (?, ?, ?)", wm)
    if parts:
        new_con.executemany("INSERT OR REPLACE INTO part_index VALUES (?, ?, ?)", parts)
    print(f"  [swap] carried harvest state: {len(wm)} watermark(s), {len(parts)} part(s)")


def _corpus_files() -> list[Path]:
    return sorted(REPO.glob(CORPUS_DIR_GLOB))


def _provenance_migration(db_path: Path) -> bool:
    """Add `provenance` to an index built before the column existed.

    Returns True when the column was just added, so the caller can force a full
    rebuild. `CREATE TABLE IF NOT EXISTS` cannot do this — it is a no-op on a
    table that already exists, which is why the DDL change alone would have left
    every pre-existing row reading NULL with no error raised anywhere.

    A missing index file needs no migration: it is built from the DDL.
    """
    ds.assert_not_served(db_path)  # in-place DDL write: forbidden against a served store
    if not db_path.exists():
        return False
    try:
        con = duckdb.connect(str(db_path))
    except duckdb.Error:
        return False
    try:
        cols = {r[0] for r in con.execute("DESCRIBE convo_search").fetchall()}
    except duckdb.Error:
        return False  # table absent: the fresh build gets the column from the DDL
    finally:
        con.close()
    if "provenance" in cols:
        return False
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "ALTER TABLE convo_search ADD COLUMN IF NOT EXISTS provenance VARCHAR DEFAULT ''"
        )
    finally:
        con.close()
    return True


def _file_hash(f: Path) -> str:
    return hashlib.blake2b(f.read_bytes(), digest_size=8).hexdigest()


def _backfill_provenance(db_path: Path, files: list[Path]) -> tuple[int, int]:
    """Fill `provenance` on existing rows from their parquet's `source`.

    Joined on `(file_path, row_no)`, NOT per-file: 19 of the 271 corpus files
    carry MIXED provenance (a session harvested live and also covered by a
    snapshot has both `opencode:live` and `opencode-snap:<ts>` in one file),
    so a per-file UPDATE either writes a lie or skips the file and leaves its
    ~2,670 rows empty. `row_no` is the enumerate index over the parquet's rows,
    which is exactly what `_read_file` stored, so the join is positional and
    needs no re-embedding.

    Returns (files, rows).
    """
    import pyarrow.parquet as pq

    # Wire-routed write (Slice B wave 2): when the store is served this
    # UPDATE flows over the quack wire and the server serializes it;
    # unserved it is the plain file connect. The swap guard does not
    # apply — this is an in-place relabel, not an os.replace.
    with (
        io_lock(db_path, exclusive=True) if not ds.is_served(db_path) else contextlib.nullcontext()
    ):
        con = ds.connect_shared(str(db_path), read_only=False)
        try:
            files_touched = 0
            for f in files:
                rel = str(f.relative_to(REPO))
                try:
                    srcs = pq.read_table(f, columns=["source"]).column("source").to_pylist()
                except Exception:  # noqa: S112  # unreadable file: --check reports it; index refresh continues
                    continue
                if not any(srcs):
                    continue
                # Wire clients cannot bind parameters (Slice B learning):
                # emit literals for both modes — sql_lit renders identically
                # for the file connect.
                rel_lit = ds.sql_lit(rel)
                rows = con.execute(
                    f"SELECT row_no, provenance FROM convo_search WHERE file_path = {rel_lit}"  # noqa: S608  — rel_lit via ds.sql_lit
                ).fetchall()
                if not rows:
                    continue
                by_no = dict(rows)
                # one UPDATE per distinct source in the file, not per row
                buckets: dict[str, list[int]] = {}
                for row_no, src in enumerate(srcs):
                    if row_no in by_no and src:
                        buckets.setdefault(src, []).append(row_no)
                if not buckets:
                    continue
                files_touched += 1
                for src, nos in buckets.items():
                    ph = ",".join(str(int(n)) for n in nos)
                    con.execute(
                        f"UPDATE convo_search SET provenance = {ds.sql_lit(src)} "  # noqa: S608  — src/rel_lit via ds.sql_lit, row_no int-cast
                        f"WHERE file_path = {rel_lit} AND row_no IN ({ph})"
                    )
            total_row = con.execute(
                "SELECT count(*) FROM convo_search WHERE provenance <> ''"
            ).fetchone()
            total = int(total_row[0]) if total_row else 0
        finally:
            con.close()
    return (files_touched, total)


def _backfill_models(db_path: Path, files: list[Path]) -> tuple[int, int]:
    """Fill `model` on existing rows from their parquet's `model`.

    Sibling of `_backfill_provenance` for the same in-place-repair blind
    spot: the model migrations (`--migrate-zcode-models` /
    `--migrate-opencode-models`) rewrite corpus parquets with text and
    part_ids untouched, so no incremental rebuild ever notices a changed
    facet — the corpus said one thing, the index another (measured
    2026-10-18: corpus 0 dict-string model rows, index 5,327). Same
    positional `(file_path, row_no)` join, no re-embedding; only rows
    whose index value differs are written, so the count is honest.

    Returns (files, rows).
    """
    import pyarrow.parquet as pq

    files_touched = 0
    rows_touched = 0
    with io_lock(db_path, exclusive=True):
        con = connect_with_lock_retry(lambda: duckdb.connect(str(db_path)))
        try:
            for f in files:
                rel = str(f.relative_to(REPO))
                try:
                    models = pq.read_table(f, columns=["model"]).column("model").to_pylist()
                except Exception:  # noqa: S112  # unreadable file: --check reports it; index refresh continues
                    continue
                rows = con.execute(
                    "SELECT row_no, model FROM convo_search WHERE file_path = ?",
                    [rel],
                ).fetchall()
                if not rows:
                    continue
                by_no = dict(rows)
                # one UPDATE per distinct model value in the file, not per row
                buckets: dict[str, list[int]] = {}
                for row_no, m in enumerate(models):
                    if row_no in by_no and m and by_no[row_no] != m:
                        buckets.setdefault(m, []).append(row_no)
                if not buckets:
                    continue
                files_touched += 1
                for m, nos in buckets.items():
                    ph = ",".join("?" * len(nos))
                    con.execute(
                        f"UPDATE convo_search SET model = ? "  # noqa: S608  # constant template; only the placeholder count varies
                        f"WHERE file_path = ? AND row_no IN ({ph})",
                        [m, rel, *nos],
                    )
                    rows_touched += len(nos)
        finally:
            con.close()
    return (files_touched, rows_touched)


def _read_file(f: Path) -> tuple[str, list[dict]]:
    """One parquet file → (blake2b, index rows); non-text parts dropped."""
    import pyarrow.parquet as pq

    rel = str(f.relative_to(REPO))
    # memory/data/harness/<harness>/conversations/<session>.parquet — the
    # corpus carries no harness column; the segment before `conversations`
    # IS the namespace (works for any corpus root layout).
    parts = Path(rel).parts
    harness = parts[parts.index("conversations") - 1] if "conversations" in parts else "unknown"
    rows = []
    for row_no, r in enumerate(pq.read_table(f).to_pylist()):
        if str(r.get("part_type") or "").startswith(SKIP_PART_PREFIXES):
            continue  # protocol markers — kept in the corpus, not indexed
        text = r.get("text") or ""
        if len(text.strip()) < MIN_TEXT_LEN:
            continue
        ts = r.get("ts")
        rows.append(
            {
                "harness": harness,
                "session_id": r.get("session_id") or "",
                "part_id": r["part_id"],
                "file_path": rel,
                "row_no": row_no,
                "ts": ts.replace(tzinfo=None) if ts is not None else None,
                "role": r.get("role") or "",
                "agent": r.get("agent") or "",
                "model": r.get("model") or "",
                "part_type": r.get("part_type") or "text",
                "text_len": len(text),
                "snippet": text[:SNIPPET_CAP],
                "provenance": r.get("source") or "",
            }
        )
    return _file_hash(f), rows


def _read_files(files: list[Path]) -> dict[str, tuple[str, list[dict]]]:
    return {str(f.relative_to(REPO)): _read_file(f) for f in files}


def _stored_hashes(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    try:
        return dict(
            con.execute("SELECT key, value FROM convo_meta WHERE key LIKE 'file:%'").fetchall()
        )
    except duckdb.CatalogException:
        return {}


def _strip_prefix(hashes: dict[str, str]) -> dict[str, str]:
    return {k.removeprefix("file:"): v for k, v in hashes.items()}


def _diff(current: dict[str, str], stored: dict[str, str]) -> dict[str, list[str]]:
    cur, old = set(current), set(stored)
    return {
        "new": sorted(cur - old),
        "changed": sorted(f for f in cur & old if current[f] != stored[f]),
        "deleted": sorted(old - cur),
    }


def _keys_of_file(con: duckdb.DuckDBPyConnection, rel: str) -> list[tuple[str, str]]:
    """(harness, part_id) of every indexed row from one corpus file."""
    return [
        (h, p)
        for h, p in con.execute(
            "SELECT harness, part_id FROM convo_search WHERE file_path = ?", [rel]
        ).fetchall()
    ]


def _delete_duckdb_keys(
    con: duckdb.DuckDBPyConnection, keys: list[tuple[str, str]], chunk: int = 400
) -> None:
    """Delete index rows by composite key, one OR-ed statement per chunk.

    T8: 3.6 ms/key with one statement per key; a chunked statement keeps
    the delta cheap without depending on a secondary index.
    """
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(part))
        con.execute(
            f"DELETE FROM convo_search WHERE {where}",  # noqa: S608  # constant template
            [v for k in part for v in k],
        )


def _indexed_state(
    con: duckdb.DuckDBPyConnection, rel: str
) -> dict[tuple[str, str], tuple[str, int, int]]:
    """(harness, part_id) → (snippet, text_len, row_no) indexed for a file.

    ``row_no`` is part of the fingerprint on purpose, and it is the
    compaction guard. The pointers ARE row numbers into the parquet, so
    if a harness compacts/rewrites a session (parts reordered or
    dropped), every SUBSEQUENT part keeps its id and text but moves to a
    different row. Comparing only the text would skip those rows and
    leave pointers resolving to the wrong part — silent corruption. With
    row_no in the tuple, a compaction flags the whole tail as changed
    and it is re-indexed: correct, and cheap because compaction is rare
    (tolerated skew, deliberately).
    """
    return {
        (h, p): (snip, tlen, rno)
        for h, p, snip, tlen, rno in con.execute(
            "SELECT harness, part_id, snippet, text_len, row_no FROM convo_search "
            "WHERE file_path = ?",
            [rel],
        ).fetchall()
    }


def _fts_delete_keys(
    sconn: sqlite3.Connection, keys: list[tuple[str, str]], chunk: int = 400
) -> None:
    """Delete FTS rows for many keys in ONE statement, chunked.

    T8 (2026-09-27): this used to be ``executemany`` per key. `harness`
    and `part_id` are UNINDEXED columns in FTS5, so every statement
    full-scanned the content table — 2,000 statements over 53k rows cost
    **137 s** (68.8 ms/key), which was the entire runtime of an
    incremental rebuild (measured). One OR-ed statement scans once.

    The chunk keeps the OR-chain under SQLite's expression-depth limit
    (SQLITE_MAX_EXPR_DEPTH=1000), so ~400 pairs per statement: a
    5k-row session file is ~13 statements instead of 5,000.
    """
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        where = " OR ".join(["(harness = ? AND part_id = ?)"] * len(part))
        sconn.execute(
            f"DELETE FROM convo_fts WHERE {where}",  # noqa: S608  # built from a constant template
            [v for k in part for v in k],
        )


def _fts_sync(
    sconn: sqlite3.Connection,
    drop_keys: list[tuple[str, str]],
    rows: list[dict],
    full: bool,
) -> None:
    sconn.execute(FTS_DDL)
    if full:
        # a full rebuild must not leave rows the DuckDB side dropped
        sconn.execute("DELETE FROM convo_fts")
    if drop_keys:
        _fts_delete_keys(sconn, drop_keys)
    sconn.executemany(
        "INSERT INTO convo_fts VALUES (?, ?, ?, ?)",
        [(r["snippet"], r["harness"], r["part_id"], r["session_id"]) for r in rows],
    )
    sconn.commit()


def _embed(texts: list[str]) -> tuple[list[list[float]], dict, int, str]:
    """Cache-aware pool embed; degrades to pseudo vectors without the model."""
    global _pseudo_warned
    from helpers.maintenance import rebuild_common as rbc

    embed_fn, dims, model_label, _pseudo_warned = rbc.resolve_embedder(_pseudo_warned, _PSEUDO_DIMS)
    if not texts:
        return [], {"hits": 0, "misses": 0}, dims, model_label
    if dims != EMBED_DIMS:
        return (
            [embed_fn(t) for t in texts],
            {"hits": 0, "misses": len(texts)},
            dims,
            model_label,
        )
    import helpers.core.local_embedder as le

    vecs, cstats = _cached_batch(texts, model_label, le)
    return vecs, cstats, dims, model_label


def _cached_batch(texts: list[str], model_label: str, le) -> tuple[list[list[float]], dict]:
    """cached_embed_batch against the FTS sidecar's sqlite conn (vec store host)."""
    from helpers.core.embed_cache import cached_embed_batch

    from helpers.core.db import connect as db_connect

    sconn = db_connect(_FTS_DB_PATH)
    try:
        # No automatic model-difference purge (2026-10-08): a prior-model
        # row is rollback insurance, dead-text eviction is `make embed-gc`.
        return cached_embed_batch(
            sconn,
            texts,
            model_label,
            le.embed_documents_parallel,
            source="convo",
        )
    finally:
        sconn.close()


_FTS_DB_PATH: Path = REPO / "memory" / FTS_DB_NAME


_STAGE = "convo_stage_arrow"

# Column order must match CONVO_SEARCH_DDL exactly.
_STAGE_SCHEMA_FIELDS = (
    ("harness", "string"),
    ("session_id", "string"),
    ("part_id", "string"),
    ("file_path", "string"),
    ("row_no", "int64"),
    ("ts", "ts"),
    ("role", "string"),
    ("agent", "string"),
    ("model", "string"),
    ("part_type", "string"),
    ("text_len", "int64"),
    ("snippet", "string"),
    ("embedding", "listf32"),
    ("provenance", "string"),
)


def _bulk_insert(con: duckdb.DuckDBPyConnection, rows: list[dict], vecs: list[list[float]]) -> None:
    """INSERT the staged rows as ONE Arrow batch instead of 52k row binds.

    T6 (2026-09-27): the per-row ``executemany`` was the real cost of a
    rebuild, not the embed — the first cold run spent 45+ min AFTER its
    last vector, compute-bound at 85% CPU and 1.8 GB RSS, binding 52.7k
    rows of (text, 384 floats) one tuple at a time. Arrow hands DuckDB
    columnar buffers with no per-row Python object churn; DuckDB casts
    them to the table's types. Upsert semantics are unchanged
    (``INSERT OR REPLACE`` on the (harness, part_id) primary key).
    """
    import pyarrow as pa

    cols: dict[str, list] = {name: [] for name, _ in _STAGE_SCHEMA_FIELDS}
    for i, r in enumerate(rows):
        for name, _kind in _STAGE_SCHEMA_FIELDS:
            if name == "embedding":
                cols[name].append(vecs[i] if i < len(vecs) else None)
            else:
                cols[name].append(r.get(name))
    schema = pa.schema(
        [
            pa.field("harness", pa.string()),
            pa.field("session_id", pa.string()),
            pa.field("part_id", pa.string()),
            pa.field("file_path", pa.string()),
            pa.field("row_no", pa.int64()),
            pa.field("ts", pa.timestamp("us")),
            pa.field("role", pa.string()),
            pa.field("agent", pa.string()),
            pa.field("model", pa.string()),
            pa.field("part_type", pa.string()),
            pa.field("text_len", pa.int64()),
            pa.field("snippet", pa.string()),
            # f32 == the table's FLOAT and the cache's packing, so this is a
            # reinterpretation, not a lossy conversion
            pa.field("embedding", pa.list_(pa.float32())),
            # corpus `source`: `opencode:live`, `opencode-snap:<ts>`, or
            # `orphan:<harness>` for a session the harness no longer has. Named
            # `provenance` here because `source` collides with the parquet
            # reader's own vocabulary and the index is queried by humans.
            pa.field("provenance", pa.string()),
        ]
    )
    table = pa.Table.from_pydict(
        {name: cols[name] for name, _ in _STAGE_SCHEMA_FIELDS}, schema=schema
    )
    con.execute("SELECT 1").fetchall()  # duckdb2-drain: pending result blocks register()
    con.register(_STAGE, table)
    try:
        con.execute(
            f"INSERT OR REPLACE INTO convo_search "  # noqa: S608  # constant table names
            f"SELECT {', '.join(n for n, _ in _STAGE_SCHEMA_FIELDS)} FROM {_STAGE}"
        )
    finally:
        con.unregister(_STAGE)


class _Phases:
    """Cumulative phase timer. Every rebuild reports where its seconds
    went, because the two defects that cost the most here (T6's 45-min
    row-by-row insert, T8's 137 s per-key FTS delete) were both invisible
    in a single total."""

    def __init__(self) -> None:
        import time
        from datetime import datetime

        self._t0 = time.perf_counter()
        self._born = datetime.now()
        self.marks: list[tuple[str, float]] = []
        self._walls: list = []

    def mark(self, name: str) -> None:
        import time
        from datetime import datetime

        from helpers.maintenance.maint_timing import current as _active_timer

        at = time.perf_counter() - self._t0
        now = datetime.now()
        prev_at = self.marks[-1][1] if self.marks else 0.0
        prev_wall = self._walls[-1] if self._walls else self._born
        self.marks.append((name, at))
        self._walls.append(now)
        timer = _active_timer()
        if timer is not None:
            timer.record_phase(name, prev_wall, now, elapsed_s=round(at - prev_at, 3))

    def as_dict(self) -> dict[str, float]:
        out: dict[str, float] = {}
        prev = 0.0
        for name, at in self.marks:
            out[name] = round(at - prev, 2)
            prev = at
        return out


def _rebuild_check_mode(db_path: Path, files: list[Path], timer: _Phases) -> dict:
    """Check mode: report corpus-vs-index drift without writing."""
    current = {str(f.relative_to(REPO)): _file_hash(f) for f in files}
    if not db_path.exists():
        diff = {"new": sorted(current), "changed": [], "deleted": []}
    else:
        con = open_read_only(db_path)  # S2/S3: queue + retry, never race the writer
        try:
            diff = _diff(current, _strip_prefix(_stored_hashes(con)))
        finally:
            con.close()
    stale = {
        "stale_new": diff["new"],
        "stale_changed": diff["changed"],
        "stale_deleted": diff["deleted"],
    }
    timer.mark("check")
    return {
        "indexed": 0,
        "mode": "check",
        "index_stale": bool(diff["new"] or diff["changed"] or diff["deleted"]),
        "timings": timer.as_dict(),
        **stale,
    }


def _rebuild_full_mode(
    con, files: list[Path], stored: dict
) -> tuple[list[dict], dict, list[tuple[str, str]], dict]:
    """Full rebuild mode: read all files, diff, and prepare rows."""
    target = _read_files(files)
    current = {rel: h for rel, (h, _rows) in target.items()}
    diff = _diff(current, stored)
    # Wholesale delete is deliberate (T8 lesson, converse of the delta path):
    # a full rebuild clears the index — and, via _fts_sync(full=True), the FTS
    # sidecar — instead of reconciling row-by-row against it.
    con.execute("DELETE FROM convo_search")
    con.execute("DELETE FROM convo_meta")
    drop_keys: list[tuple[str, str]] = []
    keep = [r for _h, rr in target.values() for r in rr]
    return keep, current, drop_keys, diff


def _rebuild_incremental_mode(
    con, files: list[Path], stored: dict
) -> tuple[list[dict], dict, list[tuple[str, str]], dict]:
    """Incremental rebuild mode: delta-only processing.

    Delta-only is the T8b fix, not a style choice: re-inserting unchanged
    rows costs ~11 s of FTS re-tokenization per run for text that never
    changed. Harness parts are append-only, so row_no shifts ONLY on a
    compaction — which is exactly why row_no is part of the change
    fingerprint below (a compaction re-shifts every later row while the
    text stays identical, and only the fingerprint catches it).
    """
    current = {str(f.relative_to(REPO)): _file_hash(f) for f in files}
    diff = _diff(current, stored)
    wanted = set(diff["new"]) | set(diff["changed"])
    target = _read_files([f for f in files if str(f.relative_to(REPO)) in wanted])
    drop_keys: list[tuple[str, str]] = []
    keep: list[dict] = []
    for rel in sorted(diff["deleted"]):
        con.execute("DELETE FROM convo_search WHERE file_path = ?", [rel])
    for rel, (_h, file_rows) in target.items():
        if rel not in stored:
            keep.extend(file_rows)
            continue
        seen = _indexed_state(con, rel)
        new_keys = {(r["harness"], r["part_id"]): r for r in file_rows}
        rewritten: set[tuple[str, str]] = set()
        for key, was in seen.items():
            row = new_keys.get(key)
            if row is None:
                drop_keys.append(key)
            elif was != (row["snippet"], row["text_len"], row["row_no"]):
                drop_keys.append(key)
                rewritten.add(key)
        keep.extend(r for key, r in new_keys.items() if key not in seen or key in rewritten)
    return keep, current, drop_keys, diff


def _rebuild_embed_and_persist(
    con,
    rows: list[dict],
    drop_keys: list[tuple[str, str]],
    current: dict,
    incremental: bool,
    timer: _Phases,
) -> tuple[dict, str, int]:
    """Embed rows, persist to DuckDB and FTS, return (cstats, model_label, dims).

    Retained as the single-write-window variant used by tests; the CLI
    rebuild() uses the three-window flow (contention-window
    minimization S3) that keeps the embed phase OUTSIDE any lock.
    """
    vecs, cstats, dims, model_label = _embed([r["snippet"] for r in rows])
    timer.mark("embed")
    if drop_keys:
        _delete_duckdb_keys(con, drop_keys)
    timer.mark("duckdb_delete")
    if rows:
        _bulk_insert(con, rows, vecs)
    timer.mark("duckdb_insert")
    from helpers.core.db import connect as db_connect

    sconn = db_connect(_FTS_DB_PATH)
    try:
        _fts_sync(sconn, drop_keys, rows, full=not incremental)
    finally:
        sconn.close()
    timer.mark("fts_sync")
    con.executemany(
        "INSERT OR REPLACE INTO convo_meta VALUES (?, ?)",
        [(f"file:{rel}", h) for rel, h in current.items()]
        + [("embed_model", model_label), ("embed_dims", str(dims))],
    )
    return cstats, model_label, dims


def rebuild_via_swap(db_path: Path, incremental: bool = False) -> dict:
    """Full rebuild into a pid-tagged temp, then atomic ``os.replace`` swap.

    The in-place DELETE+reinsert cycle leaves 67% of the file as dead /
    fragmented space; DuckDB 1.5.6 VACUUM reclaims 0 bytes here. Building
    a fresh compact DB in ``<path>.rebuild-<pid>.tmp`` and swapping it in
    reclaims it (graph lane's ``_rebuild_via_swap`` pattern).

    Lock contract (S3 preserved): embed runs with NO live-db connection;
    the only live-file exclusive window is the final swap instant. FTS
    sync (independent sqlite store) runs after the swap, so a failed FTS
    leaves the fresh DuckDB live with a stale FTS — the next ``--check``
    catches it and a re-run redoes the sync.
    """
    ds.assert_not_served(db_path)  # os.replace would swap the inode under a live server
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    global _FTS_DB_PATH
    _FTS_DB_PATH = fts_db_for(db_path)
    timer = _Phases()

    files = _corpus_files()

    tmp = db_path.with_name(f"{db_path.name}.rebuild-{os.getpid()}.tmp")
    tmp_wal = tmp.with_name(tmp.name + ".wal")
    tmp_lock = tmp.with_name(tmp.name + ".build.lock")
    tmp.unlink(missing_ok=True)
    tmp_wal.unlink(missing_ok=True)
    tmp_lock.unlink(missing_ok=True)

    try:
        target = _read_files(files)
        keep = [r for _h, rr in target.values() for r in rr]
        current = {rel: h for rel, (h, _rows) in target.items()}
        timer.mark("hash+read")

        vecs, cstats, dims, model_label = _embed([r["snippet"] for r in keep])
        timer.mark("embed")

        con = duckdb.connect(str(tmp))
        try:
            con.execute(CONVO_SEARCH_DDL)
            con.execute(CONVO_META_DDL)
            if keep:
                _bulk_insert(con, keep, vecs)
            con.executemany(
                "INSERT OR REPLACE INTO convo_meta VALUES (?, ?)",
                [(f"file:{rel}", h) for rel, h in current.items()]
                + [("embed_model", model_label), ("embed_dims", str(dims))],
            )
            _carry_harvest_state(con, db_path)
        finally:
            con.close()
        timer.mark("duckdb_write")

        if tmp_wal.exists():
            raise RuntimeError(f"rebuild temp not cleanly closed: {tmp_wal} still exists")

        with io_lock(db_path, exclusive=True):
            os.replace(tmp, db_path)
        timer.mark("swap")

        from helpers.core.db import connect as db_connect

        sconn = db_connect(_FTS_DB_PATH)
        try:
            _fts_sync(sconn, [], keep, full=True)
        finally:
            sconn.close()
        timer.mark("fts_sync")

        total_row = (
            duckdb.connect(str(db_path), read_only=True)
            .execute("SELECT COUNT(*) FROM convo_search")
            .fetchone()
        )
        total = total_row[0] if total_row else 0
    finally:
        tmp.unlink(missing_ok=True)
        tmp_wal.unlink(missing_ok=True)
        tmp_lock.unlink(missing_ok=True)

    timings = timer.as_dict()
    timings["total"] = round(sum(timings.values()), 2)
    result = {
        "indexed": len(keep),
        "mode": "swap",
        "timings": timings,
        "total": total,
        "embedded": cstats["misses"],
        "embed_cache_hits": cstats["hits"],
        "embed_cache_misses": cstats["misses"],
        "index_stale": False,
        "stale_new": [],
        "stale_changed": [],
        "stale_deleted": [],
    }
    if "timing" in cstats:
        result["embed_cache_timing"] = cstats["timing"]
    return result


def _ensure_provenance_backfill(db_path: Path, files: list[Path]) -> None:
    """Backfill the provenance column right after the migration added it.

    The column was just added, so every EXISTING row reads '' instead of
    its corpus source. An incremental run would only re-read changed
    files and leave the rest permanently empty — a silent under-fill that
    looks exactly like "this session has no provenance".

    Backfill it from the parquet instead of forcing a full rebuild: one
    UPDATE per file (the index is keyed by file_path, one parquet per
    session) costs seconds, where a full re-embed of ~200k rows is a
    45-minute job that would change nothing but the vector column.
    """
    if not _provenance_migration(db_path):
        return
    n_files, n_rows = _backfill_provenance(db_path, files)
    print(
        f"provenance: column added — backfilled {n_rows} row(s) from {n_files} parquet file(s)",
        file=sys.stderr,
    )


def _apply_write_window(
    db_path: Path,
    stored: dict[str, str],
    drop_keys: list[tuple[str, str]],
    rows: list[dict],
    vecs: list[list[float]],
) -> tuple[int, bool]:
    """Write window 3: TOCTOU re-verify, then delete + bulk insert.

    Returns ``(total_rows, changed)`` — ``changed=True`` means the stored-hash
    fingerprint moved mid-embed (a concurrent rebuild applied a diff inside
    our embed gap); nothing was written on this attempt and the caller must
    redo from the read window (the fresh diff comes back empty for an
    identical corpus, making the redo a clean no-op).
    """
    with io_lock(db_path, exclusive=True):
        con = connect_with_lock_retry(lambda: duckdb.connect(str(db_path)))
        try:
            if _strip_prefix(_stored_hashes(con)) != stored:
                print(
                    f"convo rebuild: hash state changed mid-embed ({db_path}); re-reading the diff",
                    file=sys.stderr,
                )
                return 0, True
            if drop_keys:
                _delete_duckdb_keys(con, drop_keys)
            if rows:
                _bulk_insert(con, rows, vecs)
            total_row = con.execute("SELECT COUNT(*) FROM convo_search").fetchone()
            return (total_row[0] if total_row else 0), False
        finally:
            # DuckDB closes BEFORE the flock releases — a queued
            # reader's post-flock open is then collision-free.
            con.close()


def rebuild(
    db_path: Path, write: bool = True, incremental: bool = False, swap: bool = False
) -> dict:
    """The S2 core. ``--check`` (write=False) never resolves the embedder.

    Contention-window minimization S3 — three intent-scoped windows with
    the embed phase OUTSIDE any lock:

    1. read window (io.lock EX, seconds): DDL + stored hashes + diff;
    2. embed (NO convo-db connection — its cache writes land on the
       shared embed_store.db, which must never nest inside this db's
       window);
    3. write window (io.lock EX, seconds): TOCTOU re-verify of the
       stored hashes, then delete + bulk insert; close;
    4. FTS sync on the independent sqlite store, THEN a tiny meta window
       (meta is written only after FTS succeeded — the old one-window
       failure ordering preserved: a failed FTS leaves meta stale and
       the next run redoes).
    """
    if write:
        ds.assert_not_served(db_path)  # in-place write windows: forbidden against a served store
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    global _FTS_DB_PATH
    _FTS_DB_PATH = fts_db_for(db_path)
    timer = _Phases()

    files = _corpus_files()
    if not write:
        return _rebuild_check_mode(db_path, files, timer)
    if swap:
        return rebuild_via_swap(db_path, incremental=incremental)

    _ensure_provenance_backfill(db_path, files)

    rows: list[dict] = []
    vecs: list[list[float]] = []
    stored: dict[str, str] = {}
    keep: list[dict] = []
    current: dict[str, str] = {}
    drop_keys: list[tuple[str, str]] = []
    diff: dict[str, list[str]] = {}
    cstats: dict = {}
    model_label = ""
    dims = 0
    total = 0
    for _attempt in (1, 2):
        # -- 1. read window ------------------------------------------------
        with io_lock(db_path, exclusive=True):
            con = connect_with_lock_retry(lambda: duckdb.connect(str(db_path)))
            try:
                con.execute(CONVO_SEARCH_DDL)
                con.execute(CONVO_META_DDL)
                stored = _strip_prefix(_stored_hashes(con))
                if incremental:
                    keep, current, drop_keys, diff = _rebuild_incremental_mode(con, files, stored)
                else:
                    keep, current, drop_keys, diff = _rebuild_full_mode(con, files, stored)
            finally:
                con.close()
        timer.mark("hash+read")

        # -- 2. embed, lockless (its cache lives in the shared embed_store) -
        rows = keep
        vecs, cstats, dims, model_label = _embed([r["snippet"] for r in rows])
        timer.mark("embed")

        # -- 3. write window (TOCTOU re-verify BEFORE any write) -----------
        total, changed = _apply_write_window(db_path, stored, drop_keys, rows, vecs)
        if changed:
            continue
        timer.mark("duckdb_write")
        break
    else:
        raise RuntimeError(
            f"convo rebuild lost the interleave race twice on {db_path}; "
            "rerun when concurrent rebuild traffic settles"
        )

    # -- 4. FTS sync: independent sqlite store, OUTSIDE the duckdb window --
    from helpers.core.db import connect as db_connect

    sconn = db_connect(_FTS_DB_PATH)
    try:
        _fts_sync(sconn, drop_keys, rows, full=not incremental)
    finally:
        sconn.close()
    timer.mark("fts_sync")

    # -- meta window: only after FTS succeeded (failure ordering preserved) -
    with io_lock(db_path, exclusive=True):
        con = connect_with_lock_retry(lambda: duckdb.connect(str(db_path)))
        try:
            con.executemany(
                "INSERT OR REPLACE INTO convo_meta VALUES (?, ?)",
                [(f"file:{rel}", h) for rel, h in current.items()]
                + [("embed_model", model_label), ("embed_dims", str(dims))],
            )
        finally:
            con.close()
    timer.mark("meta")

    timings = timer.as_dict()
    timings["total"] = round(sum(timings.values()), 2)
    result = {
        "indexed": len(rows),
        "mode": "incremental" if incremental else "full",
        "timings": timings,
        "total": total,
        "embedded": cstats["misses"],
        "embed_cache_hits": cstats["hits"],
        "embed_cache_misses": cstats["misses"],
        "index_stale": False,
        "stale_new": diff["new"],
        "stale_changed": diff["changed"],
        "stale_deleted": diff["deleted"],
    }
    if "timing" in cstats:
        result["embed_cache_timing"] = cstats["timing"]
    return result


def summary(stats: dict) -> str:
    if stats.get("mode") == "check":
        return (
            f"convo_search check: {len(stats.get('stale_new', []))} new, "
            f"{len(stats.get('stale_changed', []))} changed, "
            f"{len(stats.get('stale_deleted', []))} deleted"
        )
    t = stats.get("timings") or {}
    phase = " ".join(f"{k}={v}s" for k, v in t.items() if k != "total")
    text = (
        f"convo_search: {stats.get('indexed', 0)} parts indexed "
        f"({stats.get('mode')}, total {stats.get('total', 0)})"
        + (f"\n  phases: {phase}" if phase else "")
    )
    cache_timing = stats.get("embed_cache_timing")
    if cache_timing:
        text += (
            f"\n  embed cache timing: backend={cache_timing['backend']} "
            f"lookup={cache_timing['cache_lookup_seconds']}s "
            f"miss-embed-callback={cache_timing['miss_embed_callback_seconds']}s"
        )
    return text


def main(argv: list[str] | None = None) -> int:
    from helpers.maintenance import rebuild_common as rbc

    spec = rbc.RebuildCliSpec(
        description=__doc__.splitlines()[0],
        default_db=str(REPO / "memory/convo_search.duckdb"),
        db_help="convo_search duckdb path",
        check_help="report corpus-vs-index drift, exit 1, no writes",
        incremental_help="reprocess only new/changed corpus files",
        swap_help="atomic pid-tagged temp + os.replace swap (compacts the duckdb; graph-lane pattern)",
        rebuild_fn=rebuild,
        summary=summary,
        migrated_msg="",
    )
    return rbc.run_rebuild_cli(argv, spec)


if __name__ == "__main__":
    sys.exit(main())
