#!/usr/bin/env python3
"""
Rebuild the `doc_search` FTS5 index over the repo's own doc/ corpus.

The doc/ tree is the repo's institutional memory — architecture.md,
graph_design.txt, the archived proposals, the completed.md run log, the
procedures, and the gitignored doc/local/ assessments. The #107 doc
browser (app.py /api/docs*) reads it from the filesystem with a naive
substring scan: no stemming, no ranking model, no semantics, and no way
for an agent session to query it without the Flask app running. This
script gives the corpus the same content-addressable treatment the notes
vault has (proposal: doc/improvements/archive/tooling/doc_search_embeddings.md):

- FTS5 BM25 over section-level chunks (one row per `##` section, plus the
  preamble as its own row) — whole-doc embeddings would truncate at the
  embedder's 512-token context and render everything past the first few
  KB of the 166 KB run log invisible to the vector leg.
- Per-row JSON embedding (local bge-small when available, deterministic
  pseudo fallback — same resolution as rebuild_note_search) for hybrid
  RRF ranking, reusing the shared (sha256(text), model) sidecar cache.

RESIDENCE — own sidecar DB, never research.db: doc/local/ is private
("never for git") and the published form of the database is the
git-tracked snapshots/parquet/ export. Keeping the index (which stores
doc/local/ PLAINTEXT in the FTS content column) inside research.db
would make its privacy manifest-dependent — one future export-allowlist
edit away from leaking. memory/doc_search.db is gitignored via memory/,
never snapshotted, never attached to DuckDB, never touched by db_maint:
locality is structural. Deleting it costs one warm rebuild. No
bump_generation either — nothing downstream derives from this table.

Usage:
    python3 helpers/maintenance/rebuild_doc_search.py            # rebuild, exit 0
    python3 helpers/maintenance/rebuild_doc_search.py --db PATH  # alternate sidecar
    python3 helpers/maintenance/rebuild_doc_search.py --check    # freshness report
    python3 helpers/maintenance/rebuild_doc_search.py --incremental

--check writes no doc_search rows, but DOES warm the sidecar embedding
cache — the documented pre-pay behaviour of rebuild_note_search applies
verbatim. It also reports an exact (hash-level) freshness verdict:
FRESH, or the changed/new/deleted breakdown plus the refresh command,
exiting 1 on drift (the house --check gate doctrine — enforced by the
rebuild_doc_search entry in make perf / tests/run_perf_benchmarks.py,
which fails on drift or missing sidecar). Unlike rebuild_note_search
there is no "DB not found" error: the sidecar is created on first run
(that is its job).

Exit codes: 0 success/fresh, 1 fatal error OR --check detected drift.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

# Repo root: helpers/maintenance/rebuild_doc_search.py -> parents[2]. Must be
# on sys.path BEFORE the `from helpers.core.embed_cache import ...` below so
# the script works as a subprocess (make maint-full) the same way it works
# under pytest. (Mirrors the rebuild_note_search.py bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.embed_cache import CachedEmbed  # noqa: E402

# Module-level and monkeypatchable (the VAULT_ROOT lesson: import-bound root
# constants silently point tests at the live vault — tests MUST retarget both).
DOC_ROOT = _REPO_ROOT / "doc"
DOC_DB = _REPO_ROOT / "memory" / "doc_search.db"
# Local-only recovery point (gitignored db-backup/ — NEVER the git-tracked
# snapshots/parquet export: the sidecar carries doc/local/ plaintext and
# must stay structurally un-publishable). Monkeypatchable for tests.
BACKUP_DIR = _REPO_ROOT / "db-backup"

DOC_EXTS = {".md", ".txt"}

# FTS5 DDL, mirroring note_search's shape (title/sector -> title/section_title;
# UNINDEXED file_path + anchor are deep-link handles, never tokenized).
# FTS5 can't ALTER TABLE ADD COLUMN, so a schema change requires DROP +
# recreate (see _migrate_schema).
DOC_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS doc_search USING fts5("
    "title, "                 # 0  file-level title (#107 _doc_title derivation)
    "section_title, "         # 1  '## ' header text of this chunk ('' = preamble)
    "file_path UNINDEXED, "   # 2  doc/-relative POSIX path
    "anchor UNINDEXED, "      # 3  1-based line number of the section header
    "content, "               # 4  full section body (header line included)
    "embedding UNINDEXED, "   # 5  JSON vector for hybrid ranking; not tokenized
    "tokenize = 'porter unicode61'"
    ")"
)
_DOC_SEARCH_COLUMNS = {"title", "section_title", "file_path", "anchor", "content", "embedding"}

# Per-file fingerprint for the incremental mode (P2.1 analogue). Docs have no
# DB-side inputs (unlike note entity docs), so the raw file text IS the change
# key — mtime first, blake2b(text) as the same-mtime-edit gate.
DOC_SEARCH_META_DDL = (
    "CREATE TABLE IF NOT EXISTS doc_search_meta ("
    " file_path TEXT PRIMARY KEY,"
    " mtime REAL NOT NULL,"
    " content_hash TEXT NOT NULL"
    ")"
)

# Model stamp home (the db_meta.note_embed_model analogue, but inside the
# sidecar — research.db must stay untouched). --check never writes it: the
# stamp must describe the table's CONTENT.
DOC_SEARCH_INFO_DDL = (
    "CREATE TABLE IF NOT EXISTS doc_search_info ("
    " key TEXT PRIMARY KEY,"
    " value TEXT NOT NULL"
    ")"
)

# How much of each chunk's body feeds the embedder. bge-small truncates at
# ~512 tokens (~2K chars) anyway; 4K keeps a little headroom for the
# title/section prefix without paying JSON-serialization weight per row.
_EMBED_BODY_CAP = 4000

_H2 = "## "


def connect_doc_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the doc_search sidecar via the house
    connection helper (standard pragmas: Row factory, WAL, busy_timeout —
    so concurrent app.py readers never collide with a maint rebuild)."""
    path = Path(db_path) if db_path is not None else DOC_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    from helpers.core.db import connect as _db_connect

    return _db_connect(path)


def _iter_doc_files(root: Path | None = None):
    """Yield (repo_rooted_rel, abs_path) for every indexable doc under doc/.

    Same walk + sort contract as app.py::_iter_doc_files (#107), but the
    yielded path is REPO-ROOTED (the doc-root dir name + '/' + rel, e.g.
    ``doc/procedures/embeddings.md``): every consumer — CLI output, API
    results, eval labels — can resolve it directly from the repo root
    without knowing the corpus root convention. The root's own name is
    used so monkeypatched tmp roots in tests behave identically.
    """
    root = Path(root) if root is not None else DOC_ROOT
    if not root.is_dir():
        return
    prefix = root.name
    for rel in sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in DOC_EXTS
    ):
        yield f"{prefix}/{rel}", root / rel


def _doc_title(text: str, rel_path: str) -> str:
    """File-level title, from the file's own text (no second read).

    Mirrors app.py::_doc_title (#107): first '#' heading line; for .txt the
    first non-empty line (capped 120 chars); else the filename stem with
    underscores -> spaces.
    """
    is_txt = rel_path.lower().endswith(".txt")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped and is_txt:
            return stripped[:120]
        if stripped:
            break
    return Path(rel_path).stem.replace("_", " ")


def _split_sections(text: str) -> list[tuple[str, int, str]]:
    """Split into (section_title, anchor_line, body) chunks on '## ' headers.

    '###' and deeper do NOT split — they belong to the parent '##' section.
    The preamble before the first header is its own chunk ('' title, anchor
    1); its body carries the H1 so the file title stays searchable. The
    header line is included in its chunk's body so FTS matches on section
    headings. Whitespace-only chunks are dropped (an empty file therefore
    yields zero rows).
    """
    chunks: list[tuple[str, int, str]] = []
    title = ""
    anchor = 1
    lines: list[str] = []

    def _flush() -> None:
        body = "\n".join(lines)
        if body.strip() or title:
            chunks.append((title, anchor, body))

    for lineno, line in enumerate(text.split("\n"), start=1):
        if line.startswith(_H2):
            _flush()
            title = line[len(_H2):].strip()
            anchor = lineno
            lines = [line]
        else:
            lines.append(line)
    _flush()
    return chunks


# Embedding resolution: identical semantics to rebuild_note_search (index side
# embed_document / query side embed_query, pseudo fallback with a one-time
# WARNING) so `make maint-full` is green on machines without the model file
# and both legs of hybrid search always share a vector space.
_PSEUDO_DIMS = 64

_pseudo_warned = False


def resolve_embedder() -> tuple[Callable[[str], list[float]], int, str]:
    """Index-side embedder: (embed_fn(text) -> list[float], dims, model_label)."""
    global _pseudo_warned
    from helpers.core import local_embedder

    if local_embedder.available():
        return local_embedder.embed_document, local_embedder.DIM, local_embedder.MODEL_ID
    if not _pseudo_warned:
        print(
            "WARNING: local bge-small embedder unavailable — using 64-dim "
            "pseudo-embeddings (hybrid ranking stays lexical-ish). Setup: "
            "helpers/core/local_embedder.py module docstring.",
            file=sys.stderr,
        )
        _pseudo_warned = True

    def _pseudo(text: str) -> list[float]:
        from helpers.graph.embeddings import _pseudo_embedding

        return _pseudo_embedding(text, _PSEUDO_DIMS)

    return _pseudo, _PSEUDO_DIMS, f"dry-run-v{_PSEUDO_DIMS}"


def query_embedder() -> tuple[Callable[[str], list[float]], int]:
    """Query-side counterpart for hybrid search: same availability gate,
    embed_query semantics (BGE retrieval prefix). Callers enforce the
    same-model rule via stored_embed_dims()."""
    from helpers.core import local_embedder

    if local_embedder.available():
        return local_embedder.embed_query, local_embedder.DIM

    def _pseudo(text: str) -> list[float]:
        from helpers.graph.embeddings import _pseudo_embedding

        return _pseudo_embedding(text, _PSEUDO_DIMS)

    return _pseudo, _PSEUDO_DIMS


def stored_embed_dims(conn: sqlite3.Connection) -> int | None:
    """Dims of the first stored doc_search embedding, or None when empty.

    Hybrid-search gate (same contract as rebuild_note_search.stored_embed_dims):
    a mismatch between the stored vector space and the query side means every
    cosine would be zip-truncated garbage — the caller degrades to BM25-only.
    """
    try:
        row = conn.execute(
            "SELECT embedding FROM doc_search "
            "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: S110  # missing table / corrupt index -> None
        return None
    if not row or not row[0]:
        return None
    try:
        vec = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return len(vec) if isinstance(vec, list) and vec else None


def _default_embed(text: str) -> list[float]:
    """Default per-chunk embedder: whatever resolve_embedder() picks."""
    fn, _dims, _label = resolve_embedder()
    return fn(text)


def _embedding_json(embed_fn, title: str, section_title: str, content: str) -> str | None:
    """Embed one chunk and serialize to JSON, or None on failure.

    Text basis mirrors _embedding_json in rebuild_note_search: title +
    section + capped body, so the vector is dominated by the headings for
    heading-typed queries while still capturing the body.
    """
    try:
        vec = embed_fn(f"{title}\n{section_title}\n{content[:_EMBED_BODY_CAP]}")
    except Exception:  # noqa: S110  # best-effort; missing rows stay searchable
        return None
    if not vec:
        return None
    return json.dumps(vec)


def _file_rows(abs_path: Path, rel: str, embed_fn) -> tuple[list[tuple], str | None]:
    """Read one doc file into its FTS row tuples + its raw-text fingerprint.

    Returns (rows, content_hash); (rows=[], hash=None) on read error — the
    caller treats unreadable files as absent (mirrors rebuild_note_search).
    """
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], None
    title = _doc_title(text, rel)
    rows = []
    for section_title, anchor, body in _split_sections(text):
        rows.append((
            title,
            section_title,
            rel,
            anchor,
            body,
            _embedding_json(embed_fn, title, section_title, body),
        ))
    # Raw text IS the change key for this corpus (no DB-side inputs).
    chash = hashlib.blake2b(text.encode("utf-8", errors="replace"), digest_size=8).hexdigest()
    return rows, chash


def _stamp_model(conn: sqlite3.Connection, model_label: str, dims: int) -> None:
    """Record the embedding model + dims in doc_search_info (apply path only)."""
    conn.execute(DOC_SEARCH_INFO_DDL)
    conn.executemany(
        "INSERT OR REPLACE INTO doc_search_info (key, value) VALUES (?, ?)",
        [("embed_model", model_label), ("embed_dims", str(dims))],
    )


def _backup_file(src: Path, dest: Path) -> bool:
    """One-file sidecar copy via the sqlite3 backup API (WAL-safe; handles
    FTS5 shadow tables the way snapshot_db.py does). Returns success.

    Connections come from the house helper with FK/WAL off — this is a
    byte-transfer, not a working session, and the dest artifact should
    stay plain-journal (no -wal sidecar next to the backup)."""
    from helpers.core.db import connect as _db_connect

    try:
        src_conn = _db_connect(src, enable_fk=False, wal=False)
        dest_conn = _db_connect(dest, enable_fk=False, wal=False)
        try:
            src_conn.backup(dest_conn)
        finally:
            src_conn.close()
            dest_conn.close()
        return True
    except sqlite3.Error:
        return False


def _backup_last_good_index(db_path: Path) -> None:
    """Last-good recovery copy of the INDEX into db-backup/.

    Runs AFTER a successful FULL rewrite (last-good-state semantics: a
    failed rebuild rolls back and the previous run's backup survives; the
    first rebuild's backup is the first good index, not the empty
    pre-state). Not run for --check / --incremental. Best-effort with a
    WARNING — a failed backup must not fail the rebuild. db-backup/ is
    gitignored local scratch, matching the research_backup.db pattern of
    db_maint — the git-tracked parquet snapshot deliberately does NOT
    cover this DB (doc/local/ plaintext locality).

    The embed cache used to ride along as a paired ``<index>_vec.db`` twin;
    since the embed_store consolidation it lives in the shared store and is
    covered centrally instead (db_maint._backup_embed_store + the snapshot
    gzip stream) — never here.
    """
    backups: list[tuple[Path, Path]] = [
        (db_path, Path(BACKUP_DIR) / "doc_search_backup.db"),
    ]
    try:
        Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    except OSError:
        print(f"WARNING: cannot create backup dir {BACKUP_DIR} "
              "(recovery point skipped; rebuild continues)", file=sys.stderr)
        return
    for src, dest in backups:
        if not src.exists():
            continue
        if _backup_file(src, dest):
            continue
        print(f"WARNING: could not back up {src.name} to {dest} "
              "(recovery point skipped; rebuild continues)", file=sys.stderr)


def _migrate_schema(conn: sqlite3.Connection) -> bool:
    """Drop a stale doc_search so the new DDL applies (FTS5 can't ALTER TABLE
    ADD COLUMN; the full rebuild repopulates anyway). True if dropped."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='doc_search'"
    ).fetchone()
    if not row:
        return False
    if all(col in row[0] for col in _DOC_SEARCH_COLUMNS):
        return False
    conn.execute("DROP TABLE doc_search")
    return True


def rebuild(  # noqa: C901
    db_path: Path | None = None, write: bool = True, incremental: bool = False,
    embed_fn=None, root: Path | None = None,
) -> dict:
    """Rebuild the doc_search FTS index. Returns a stats dict."""
    db_path = Path(db_path) if db_path is not None else DOC_DB
    conn = connect_doc_db(db_path)
    stats: dict = {}
    try:
        migrated = _migrate_schema(conn)
        conn.execute(DOC_SEARCH_DDL)
        conn.execute(DOC_SEARCH_META_DDL)
        conn.execute(DOC_SEARCH_INFO_DDL)
        # Resolve the embedder once; internally-resolved embedders get the
        # shared Q3 sidecar cache (attached as <sidecar>_vec.db — a separate
        # cache file from the notes one, which is free: the corpora share no
        # text). Injected test embed_fn stay raw; pseudo hashes are never
        # cached (caching a hash would only bloat the sidecar).
        embed_dims = _PSEUDO_DIMS
        model_label: str | None = None
        if embed_fn is None:
            embed_fn, embed_dims, model_label = resolve_embedder()
            stats["embed_model"] = model_label
            if model_label != f"dry-run-v{_PSEUDO_DIMS}":
                embed_fn = CachedEmbed(embed_fn, model_label, conn, source="doc")

        # Incremental preload: meta fingerprints + stored rows (file-granular —
        # a file owns N section rows, carried verbatim on an mtime match).
        existing: dict[str, tuple[float, str]] = {}
        reuse: dict[str, list[tuple]] | None = None
        carried: set[str] = set()
        if incremental:
            existing = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT file_path, mtime, content_hash FROM doc_search_meta")}
            reuse = {}
            by_file: dict[str, list[tuple]] = {}
            for r in conn.execute(
                "SELECT title, section_title, file_path, anchor, content, embedding "
                "FROM doc_search"
            ):
                by_file.setdefault(r[2], []).append(tuple(r))
            for fp, rows in by_file.items():
                prev = existing.get(fp)
                if prev is not None:
                    reuse[fp] = rows

        rows_by_file: dict[str, list[tuple]] = {}
        hash_by_file: dict[str, str] = {}
        for rel, abs_path in _iter_doc_files(root):
            if reuse is not None and rel in reuse:
                try:
                    mtime = abs_path.stat().st_mtime
                except OSError:
                    mtime = None
                if mtime is not None and mtime == existing[rel][0]:
                    # Stored rows are correct by construction for unchanged
                    # content — no read, no chunk, no embed, no re-hash.
                    rows_by_file[rel] = reuse[rel]
                    carried.add(rel)
                    continue
            rows, chash = _file_rows(abs_path, rel, embed_fn)
            if chash is None:
                continue
            rows_by_file[rel] = rows
            hash_by_file[rel] = chash

        if isinstance(embed_fn, CachedEmbed):
            stats["embed_cache_hits"] = embed_fn.hits
            stats["embed_cache_misses"] = embed_fn.misses
            if embed_fn.dirty:
                # Commit cache rows NOW (the --check pre-warm lesson): the
                # cache is content-addressed, so committing early is safe
                # even if the FTS write later fails.
                conn.commit()

        all_rows = [r for rows in rows_by_file.values() for r in rows]
        stats["total_files"] = len(rows_by_file)
        stats["total_rows"] = len(all_rows)
        stats["embedded"] = sum(1 for r in all_rows if r[5])
        stats["migrated"] = migrated

        # Freshness verdict: exact diff of the corpus vs the STORED index
        # (hash-level, not just the mtime probe the read path uses). Always
        # computed — cheap — so stats carry it; --check prints it and main()
        # turns drift into exit 1 (the house --check gate doctrine).
        stored_meta = existing or {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT file_path, mtime, content_hash FROM doc_search_meta")}
        on_disk = set(rows_by_file)
        stale_new = sorted(fp for fp in on_disk if fp not in stored_meta)
        stale_deleted = sorted(fp for fp in stored_meta if fp not in on_disk)
        stale_changed = sorted(
            fp for fp in on_disk
            if fp in stored_meta
            and fp not in carried  # carried = mtime match = unchanged by construction
            and (stored_meta[fp][0] != _mtime_of(root, fp)
                 or stored_meta[fp][1] != hash_by_file[fp])
        )
        stats["stale_new"] = stale_new
        stats["stale_changed"] = stale_changed
        stats["stale_deleted"] = stale_deleted
        stats["index_stale"] = bool(stale_new or stale_changed or stale_deleted)

        if not write:
            print(
                f"(--check mode: would index {stats['total_files']} files / "
                f"{stats['total_rows']} section rows)",
                file=sys.stderr,
            )
            _print_staleness(stats)
            return stats

        if not incremental:
            from collections import Counter

            # Zero-churn sibling (the maint_full_zero_churn lesson): bump
            # nothing when the content multiset is unchanged. tuple() each
            # stored row — sqlite3.Row never == a plain tuple.
            stored = [tuple(r) for r in conn.execute(
                "SELECT title, section_title, file_path, anchor, content, embedding "
                "FROM doc_search")]
            content_changed = Counter(stored) != Counter(all_rows)
            with conn:
                conn.execute("DELETE FROM doc_search")
                if all_rows:
                    conn.executemany(
                        "INSERT INTO doc_search "
                        "(title, section_title, file_path, anchor, content, embedding) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        all_rows,
                    )
                conn.execute("DELETE FROM doc_search_meta")
                conn.executemany(
                    "INSERT OR REPLACE INTO doc_search_meta (file_path, mtime, content_hash) "
                    "VALUES (?, ?, ?)",
                    [
                        (rel, _mtime_of(root, rel), hash_by_file[rel])
                        for rel in rows_by_file
                    ],
                )
                if model_label is not None:
                    _stamp_model(conn, model_label, embed_dims)
            stats["indexed"] = conn.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0]
            stats["mode"] = "full"
            stats["content_changed"] = content_changed
            # Last-good-state recovery point (local db-backup/ only).
            _backup_last_good_index(db_path)
            return stats

        # Incremental: diff against meta at file granularity. A file is
        # reprocessed when its mtime moved (the carry fast path already
        # absorbed the mtime-unchanged ones) OR its content hash differs
        # (same-mtime edit) OR it has no meta yet. The hash comparison also
        # covers zero-row files (e.g. empty placeholders), which have no
        # stored rows to carry and would otherwise re-upsert every cycle.
        seen = set(rows_by_file)
        to_delete = [fp for fp in existing if fp not in seen]
        to_upsert = []
        for rel, rows in rows_by_file.items():
            if rel in carried:
                continue
            prev = existing.get(rel)
            mtime = _mtime_of(root, rel)
            if prev is not None and prev[0] == mtime and prev[1] == hash_by_file[rel]:
                continue
            to_upsert.append(rel)
        with conn:
            for fp in to_delete:
                conn.execute("DELETE FROM doc_search WHERE file_path = ?", (fp,))
                conn.execute("DELETE FROM doc_search_meta WHERE file_path = ?", (fp,))
            for rel in to_upsert:
                conn.execute("DELETE FROM doc_search WHERE file_path = ?", (rel,))
                if rows_by_file[rel]:
                    conn.executemany(
                        "INSERT INTO doc_search "
                        "(title, section_title, file_path, anchor, content, embedding) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        rows_by_file[rel],
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO doc_search_meta (file_path, mtime, content_hash) "
                    "VALUES (?, ?, ?)",
                    (rel, _mtime_of(root, rel), hash_by_file[rel]),
                )
            if (to_upsert or to_delete) and model_label is not None:
                _stamp_model(conn, model_label, embed_dims)
        stats["indexed"] = conn.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0]
        stats["mode"] = "incremental"
        stats["upserts"] = len(to_upsert)
        stats["deletes"] = len(to_delete)
        return stats
    finally:
        conn.close()


def _print_staleness(stats: dict) -> None:
    """--check verdict: FRESH, or the drift breakdown + remediation.

    Mirrors the sync_sector_wikilinks --check shape: name the drift and
    the exact refresh command so gate output is actionable on its own.
    """
    new = stats.get("stale_new", [])
    changed = stats.get("stale_changed", [])
    deleted = stats.get("stale_deleted", [])
    if not (new or changed or deleted):
        print(f"index state: FRESH ({stats.get('total_files', 0)} files unchanged)",
              file=sys.stderr)
        return
    print(
        f"index state: STALE — {len(changed)} changed, {len(new)} new, "
        f"{len(deleted)} deleted",
        file=sys.stderr,
    )
    drift = ([(fp, "changed") for fp in changed]
             + [(fp, "new") for fp in new]
             + [(fp, "deleted") for fp in deleted])
    for fp, kind in drift[:10]:
        print(f"  {kind:8s} {fp}", file=sys.stderr)
    if len(drift) > 10:
        print(f"  … and {len(drift) - 10} more", file=sys.stderr)
    print("refresh: python3 helpers/maintenance/rebuild_doc_search.py", file=sys.stderr)


def _mtime_of(root: Path | None, rel: str) -> float:
    """Stat mtime for a stored (repo-rooted) path under the corpus root.

    Stored paths carry the root dir as their first segment ("doc/x.md") —
    drop it before joining, or the stat targets root/doc/x.md and every
    fingerprint lands at 0.0 (which reads as permanently stale).
    """
    base = Path(root) if root is not None else DOC_ROOT
    parts = PurePosixPath(rel).parts[1:] or (rel,)
    try:
        return base.joinpath(*parts).stat().st_mtime
    except OSError:
        return 0.0


# --- query core (shared by app.py /api/docs/search and helpers/misc/doc_query) ---

# RRF constant, identical to app.py::_hybrid_search_results.
_RRF_K = 60

# Diversification cap: max section-chunks per file in a result page.
_MAX_PER_FILE = 2

# Stripped from every token before quoting: '"' would close the phrase
# early; '\x00' terminates it from FTS5's C-string side ("unterminated
# string" — fuzz-found). search_docs' except-sqlite3.Error guard already
# degrades gracefully, but the generator's contract is stronger: its
# output is ALWAYS a valid MATCH expression.
_FT_TOKEN = re.compile(r'["\x00]')


def fts_match_expr(q: str) -> str:
    """Free-text query -> safe FTS5 MATCH expression.

    Each whitespace token is double-quoted (inner quotes stripped) and the
    tokens are OR-joined. Quoting keeps user/agent input like "duckpgq
    retirement (Phase E)" from being parsed as FTS5 syntax, and OR (not
    AND) matches the question-shaped queries this surface exists for —
    "why did we not adopt langgraph" must not require every stopword to
    co-occur in one chunk. BM25's idf weighting is the precision leg:
    rare tokens dominate the ranking over ubiquitous ones.
    """
    tokens = [f'"{_FT_TOKEN.sub("", t)}"' for t in q.split() if t.strip()]
    return " OR ".join(tokens)


def doc_index_ready(conn: sqlite3.Connection) -> bool:
    """True when the doc_search table exists (i.e. at least one rebuild ran)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='doc_search'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def doc_index_stale(conn: sqlite3.Connection, root: Path | None = None) -> bool:
    """True when doc/ differs from doc_search_meta (file set or mtimes).

    The read-path gate for /api/docs/search: a stale index degrades to the
    #107 filesystem scan instead of serving outdated rankings. ~53 stats —
    cheap enough to run per request. Any error counts as stale (safe side).
    """
    try:
        meta = {r[0]: r[1] for r in conn.execute(
            "SELECT file_path, mtime FROM doc_search_meta")}
    except sqlite3.Error:
        return True
    if not meta:
        return True
    on_disk = set()
    for rel, abs_path in _iter_doc_files(root):
        on_disk.add(rel)
        prev = meta.get(rel)
        if prev is None:
            return True
        try:
            if abs_path.stat().st_mtime != prev:
                return True
        except OSError:
            return True
    return bool(meta.keys() - on_disk)


def search_docs(conn: sqlite3.Connection, q: str, limit: int = 25, offset: int = 0,  # noqa: C901
                *, hybrid: bool = True) -> dict:
    """Hybrid BM25 + cosine search over doc_search. Never raises.

    Returns {"mode": "hybrid"|"bm25", "results": [...]}. Degradation:
    a dims mismatch or embedder failure drops the cosine leg (mode bm25);
    the CALLER handles missing/stale tables (scan fallback) — this
    function requires doc_index_ready(conn).

    Candidate generation (the eval-driven difference from /api/search):
    the union of the BM25 page (limit+offset rows) and the top cosine
    rows, RRF-fused. The vector leg is a co-equal RETRIEVER here, not
    just a re-ranker — with OR-joined tokens (question-shaped queries),
    BM25 alone can bury the answer chunk under frequent-token matches,
    and a cosine-only re-rank of that page cannot rescue it. Cosine-only
    candidates get a plain head-of-content snippet (no lexical match to
    mark).
    """
    expr = fts_match_expr(q)
    if not expr:
        return {"mode": "bm25", "results": []}
    try:
        # Column-weighted BM25: title + section_title matches count double
        # against body matches (pool-independent, unlike a post-hoc boost)
        # — a file whose TITLE says "completed work log" must not lose its
        # best chunk to a body-dense competitor (the eval's completed.md
        # lesson). Weights map to (title, section_title, file_path,
        # anchor, content, embedding); the UNINDEXED columns take 0.
        page = conn.execute(
            "SELECT rowid, title, section_title, file_path, anchor, embedding, rank, "
            "snippet(doc_search, 4, '<mark>', '</mark>', ' … ', 16) AS snip "
            "FROM doc_search WHERE doc_search MATCH ? "
            "ORDER BY bm25(doc_search, 2.0, 2.0, 0.0, 0.0, 1.0, 0.0) LIMIT ?",
            (expr, limit + offset),
        ).fetchall()
    except sqlite3.Error:
        return {"mode": "bm25", "results": []}

    # Cosine leg: whole-table Python scan (no vec0 mirror at this scale —
    # ~600 rows x 384 dims is sub-millisecond) gated on the stored dims.
    # hybrid=False (the eval BM25 baseline / the ?hybrid=0 opt-out) skips it.
    q_vec: list[float] | None = None
    if hybrid:
        try:
            embed_q, _dims = query_embedder()
            candidate = embed_q(q)
            idx_dims = stored_embed_dims(conn)
            if idx_dims is not None and idx_dims == len(candidate):
                q_vec = candidate
        except Exception:  # noqa: S110  # embedder unavailable -> BM25 only
            q_vec = None

    cos_rank: dict[int, int] | None = None
    sims: dict[int, float] = {}
    scored: list[tuple[int, float]] = []
    if q_vec is not None:
        norm_q = sum(x * x for x in q_vec) ** 0.5 or 1.0
        for rid, emb in conn.execute(
            "SELECT rowid, embedding FROM doc_search "
            "WHERE embedding IS NOT NULL AND embedding != ''"
        ):
            try:
                vec = json.loads(emb)
            except (TypeError, ValueError):
                continue
            if not isinstance(vec, list) or len(vec) != len(q_vec):
                continue
            norm_v = sum(x * x for x in vec) ** 0.5 or 1.0
            sim = sum(a * b for a, b in zip(q_vec, vec)) / (norm_q * norm_v)
            scored.append((rid, sim))
            sims[rid] = sim
        scored.sort(key=lambda t: t[1], reverse=True)
        cos_rank = {rid: pos for pos, (rid, _s) in enumerate(scored)}

    # Candidate union: (bm25_pos, row, snippet). Page rows carry the
    # term-aware FTS snippet; cosine-only rows carry a content head.
    candidates: list[tuple[int, sqlite3.Row, str]] = [
        (pos, row, row[7]) for pos, row in enumerate(page)
    ]
    if cos_rank is not None:
        page_rids = {row[0] for row in page}
        rows_by_rid = {
            r[0]: r for r in conn.execute(
                "SELECT rowid, title, section_title, file_path, anchor, content "
                "FROM doc_search"
            )
        }
        extra_pos = 0
        for rid, _sim in scored[: limit + offset]:
            if rid in page_rids:
                continue
            row = rows_by_rid.get(rid)
            if row is None:
                continue
            head = " ".join((row[5] or "").split())[:200]
            candidates.append((len(page) + extra_pos, row, head))
            extra_pos += 1

    worst = len(cos_rank) if cos_rank else 0
    fused = []
    for bm25_pos, row, snippet in candidates:
        if cos_rank is not None:
            rrf = (1.0 / (_RRF_K + bm25_pos + 1)) + (
                1.0 / (_RRF_K + cos_rank.get(row[0], worst + bm25_pos) + 1)
            )
        else:
            rrf = 1.0 / (_RRF_K + bm25_pos + 1)
        fused.append((rrf, row, snippet))
    fused.sort(key=lambda t: t[0], reverse=True)

    # Diversification: at most _MAX_PER_FILE chunks per file in the final
    # order. A knowledge query wants the best DISTINCT documents; without
    # the cap, one multi-section file can flood the page (and the fused
    # head is pool-independent, so pagination stays consistent).
    from collections import Counter

    per_file: Counter = Counter()
    emitted: list[tuple[float, sqlite3.Row, str]] = []
    for rrf, row, snippet in fused:
        fp = row[3]
        if per_file[fp] >= _MAX_PER_FILE:
            continue
        per_file[fp] += 1
        emitted.append((rrf, row, snippet))
        if len(emitted) >= offset + limit:
            break

    results = []
    for rrf, row, snippet in emitted[offset:]:
        path = row[3]
        # Stored paths are repo-rooted ("<docroot>/<rel>"); `section` keeps
        # the #107 semantics of subdir-relative-to-doc/ (drop the root
        # segment: doc/local/x.md -> "local", doc/x.md -> "").
        parts = PurePosixPath(path).parts
        parent = "/".join(parts[1:-1]) if len(parts) > 2 else ""
        item = {
            "path": path,
            "name": PurePosixPath(path).name,
            "section": parent,
            "title": row[1],
            "section_title": row[2],
            "anchor": row[4],
            "snippet": snippet,
            "score": round(rrf, 6),
            # Always present (null when no cosine leg) — the TS contract
            # checker requires declared fields on every hit.
            "similarity": round(sims[row[0]], 6) if row[0] in sims else None,
        }
        results.append(item)
    return {"mode": "hybrid" if cos_rank is not None else "bm25", "results": results}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--db", default=str(DOC_DB),
        help="Path to the doc_search sidecar (default: memory/doc_search.db).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Dry-run: count files/rows, report index freshness "
             "(changed/new/deleted), no writes. Exits 1 when stale.",
    )
    p.add_argument(
        "--incremental", action="store_true",
        help="Incremental rebuild (only re-index changed/deleted files).",
    )
    args = p.parse_args(argv)

    try:
        stats = rebuild(
            Path(args.db), write=not args.check, incremental=args.incremental
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"doc_search: {stats.get('total_files', 0)} files / "
        f"{stats.get('total_rows', 0)} section rows "
        f"({stats.get('embed_model', 'n/a')})",
        file=sys.stderr,
    )
    if not args.check:
        print(f"indexed {stats.get('indexed', 0)} rows", file=sys.stderr)
        if stats.get("migrated"):
            print("(schema migrated: doc_search recreated)", file=sys.stderr)
        emb = stats.get("embedded")
        if emb is not None:
            print(f"embedded {emb} rows", file=sys.stderr)
            if "embed_cache_hits" in stats:
                print(
                    f"embed cache: {stats['embed_cache_hits']} hits, "
                    f"{stats['embed_cache_misses']} misses",
                    file=sys.stderr,
                )
        if stats.get("index_stale"):
            print(
                f"index was STALE before this rebuild: "
                f"{len(stats.get('stale_changed', []))} changed, "
                f"{len(stats.get('stale_new', []))} new, "
                f"{len(stats.get('stale_deleted', []))} deleted — now fresh",
                file=sys.stderr,
            )
        return 0
    return 1 if stats.get("index_stale") else 0


if __name__ == "__main__":
    sys.exit(main())
