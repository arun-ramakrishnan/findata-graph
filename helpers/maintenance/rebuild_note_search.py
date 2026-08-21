#!/usr/bin/env python3
"""
Rebuild the `note_search` FTS5 full-text-search index over findata/ markdowns.

The knowledge graph stores structured data (entities, edges, tags) in SQLite,
but the richest prose — company overviews, business models, product portfolios,
and the newsletter corpora (The_Chatter, Points_And_Figures, The_PlotLines) —
lives only in the markdown files on disk. This script builds a standalone FTS5
inverted index over ALL findata/**/*.md documents so that free-text queries like
"shrimp feed", "drip irrigation", or "diesel engine" can find the companies,
sectors, and newsletter editions that discuss them — content that the existing
/api/entities search (name + sector-tag only) cannot reach.

WHY STANDALONE FTS (not external-content mode): external-content FTS indexes DB
columns only, but (a) note *body* text exists only in files, never in a column,
and (b) the 103 newsletter files have ZERO entity rows. The user's goal (cover
companies + sectors + newsletters) therefore requires reading files at rebuild
time. This is the content-search use case re-evaluated in doc/improvements/sqlite_improvs.txt S4,
distinct from the (still-rejected) name-typeahead use case.

The table (one row per markdown document):

    CREATE VIRTUAL TABLE note_search USING fts5(
        doc_type,            -- company|sector|super_sector|chatter|points_and_figures|plotlines
        file_path UNINDEXED, -- findata/... path; deep-link target, not indexed
        title,               -- entities.title / normalized_name, or newsletter H1
        sector,              -- entities.sector_classification for entity docs; '' otherwise
        content,             -- body text (frontmatter stripped; OCR/HTML noise dropped)
        embedding UNINDEXED, -- JSON vector for hybrid ranking (cosine x BM25); not indexed
        tokenize = 'porter unicode61'
    );

porter unicode61: case-folds + stem-folds ("batteries" matches "battery").

`embedding` is a JSON-encoded float vector stored per row (UNINDEXED = kept in
the table but never tokenized). It enables hybrid ranking: /api/search?hybrid=true
embeds the query, computes cosine similarity against each candidate row's
embedding, and fuses the BM25 rank with the cosine rank (RRF). Since 2026-08-20
(local_embeddings proposal) the default embedder is the local bge-small-en-v1.5
model (helpers/core/local_embedder.py) when available; the deterministic
pseudo-embedding remains as the offline fallback (see resolve_embedder).

Full rebuild each run (DELETE + reinsert) -> idempotent, self-correcting.
Mirrors the sync_tags.py full-rebuild pattern. Captured automatically by
snapshots (SQLite online backup handles virtual tables + shadow tables).

Usage:
    python3 helpers/maintenance/rebuild_note_search.py            # rebuild, exit 0
    python3 helpers/maintenance/rebuild_note_search.py --db PATH  # alternate DB
    python3 helpers/maintenance/rebuild_note_search.py --check    # count only, no writes

--check writes no research.db rows, but DOES warm the sidecar embedding
cache (derived state, Q3) — running --check first is a legitimate way to
pre-pay the one-off cold embed cost before the applying rebuild.

Exit codes: 0 success, 1 DB not found / fatal error.
"""

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

# Repo root: helpers/maintenance/rebuild_note_search.py -> parents[2]. Must be
# on sys.path BEFORE the `from helpers.core.db import connect` below so the
# script works as a subprocess (make maint-full) the same way it works under
# pytest. (Mirrors the sync_tags.py bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect, bump_generation  # noqa: E402
from helpers.core.vec_search import sync_vec_table  # noqa: E402
from helpers.core.frontmatter import split_frontmatter_with_title as _strip_frontmatter  # noqa: E402

DEFAULT_DB = _REPO_ROOT / "memory" / "research.db"
FINDATA = _REPO_ROOT / "findata"

# FTS5 DDL. standalone table (no content='' external-content link) so we can
# index body text read from files + the newsletter corpora (which have no
# entity rows). file_path is UNINDEXED so path strings aren't searched;
# embedding is UNINDEXED too — stored per row for hybrid cosine ranking but
# never tokenized (hybrid ranking, N5 item). FTS5 doesn't support ALTER TABLE
# ADD COLUMN, so a schema change requires DROP + recreate (see _migrate_schema).
NOTE_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS note_search USING fts5("
    "doc_type, "            # 0
    "file_path UNINDEXED, "  # 1
    "title, "               # 2
    "sector, "              # 3
    "content, "             # 4
    "embedding UNINDEXED, " # 5
    "tokenize = 'porter unicode61'"
    ")"
)

# Columns we expect in note_search. Used by _migrate_schema to detect a stale
# (pre-embedding) table and drop it so the new DDL takes effect.
_NOTE_SEARCH_COLUMNS = {"doc_type", "file_path", "title", "sector", "content", "embedding"}

# Path prefix (under findata/) -> doc_type. Order matters: longer prefixes
# first so 'Super_Sectors' isn't shadowed by a hypothetical 'S' rule. The
# newsletter dirs map to distinct types so callers can filter per-publication.
_DOC_TYPE_BY_PREFIX = [
    ("Companies/", "company"),
    ("Sectors/", "sector"),
    ("Super_Sectors/", "super_sector"),
    ("The_Chatter/", "chatter"),
    ("Points_And_Figures/", "points_and_figures"),
    ("The_PlotLines/", "plotlines"),
]

# Files / dirs to skip. images/ holds OCR artefacts; image_map.md is an image
# manifest, not prose. Both would inject noise (base64-ish alt text, paths)
# into the index.
_SKIP_PARTS = {"images"}
_SKIP_NAMES = {"image_map.md"}

# Regexes for stripping non-prose noise from newsletter bodies (which lack
# frontmatter and wrap content in HTML divs / OCR image embeds).
_HTML_DIV_OPEN = re.compile(r"<div[^>]*>")
_IMG_EMBED = re.compile(r"!\[\[[^\]]*\]\]")
_HTML_IMG_TAG = re.compile(r"<img[^>]*/?>")
_H1_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.M)
# P2 perf: compiled whitespace-collapse (was inline re.sub per call in _clean_body).
_WS_RE = re.compile(r"\s+")


def _doc_type_for(rel_path: Path) -> str | None:
    """Map a findata-relative path to its doc_type, or None if unmapped."""
    rel = rel_path.as_posix()  # forward slashes for prefix matching
    for prefix, dtype in _DOC_TYPE_BY_PREFIX:
        if rel.startswith(prefix):
            return dtype
    return None


def _clean_body(body: str) -> str:
    """Drop non-prose noise (HTML wrappers, image embeds) and collapse
    whitespace. Applied to newsletter bodies (entity notes rarely have these)."""
    body = _HTML_DIV_OPEN.sub("", body)
    body = _IMG_EMBED.sub("", body)
    body = _HTML_IMG_TAG.sub("", body)
    return _WS_RE.sub(" ", body).strip()


def _newsletter_title(text: str) -> str:
    """Extract the H1 title from a newsletter file (e.g.
    '# The Chatter: Anchor and Ambitions' -> 'The Chatter: Anchor and
    Ambitions'). Falls back to the filename stem."""
    m = _H1_TITLE.search(text)
    return m.group(1).strip() if m else ""


# Embedding resolution (local_embeddings proposal, 2026-08-20): the index
# uses the real local bge-small model when the backend + pinned model file
# are present (helpers/core/local_embedder.py), else falls back to the
# deterministic 64-dim pseudo-embedding with a one-time WARNING — a missing
# model must never break `make maint`. The QUERY side of hybrid search
# (app.py) resolves through query_embedder() so both sides always come from
# the same model; vec_search.stored_dims gates the read path against a
# rebuilt-with-a-different-model index.
_PSEUDO_DIMS = 64

_pseudo_warned = False


def resolve_embedder() -> tuple[Callable[[str], list[float]], int, str]:
    """Index-side embedder: (embed_fn(text) -> list[float], dims, model_label).

    Real local model when available, pseudo fallback otherwise. Resolved
    once per rebuild and threaded through _collect_rows; the label is
    recorded in the stats report.
    """
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
    """Query-side counterpart for hybrid search (app.py): same availability
    gate, embed_query semantics (BGE retrieval prefix). Must resolve to the
    same model the index was built with — callers enforce that via
    stored_embed_dims()."""
    from helpers.core import local_embedder

    if local_embedder.available():
        return local_embedder.embed_query, local_embedder.DIM

    def _pseudo(text: str) -> list[float]:
        from helpers.graph.embeddings import _pseudo_embedding

        return _pseudo_embedding(text, _PSEUDO_DIMS)

    return _pseudo, _PSEUDO_DIMS


def stored_embed_dims(conn) -> int | None:
    """Dims of the first stored note_search embedding (source of truth), or
    None when the index is empty/unembedded.

    Hybrid-search gate: the query vector must share the vector space of the
    STORED rows, whatever model produced them (e.g. index built with bge-384,
    query side now resolving to pseudo-64 because the model file is gone).
    A mismatch means every cosine — KNN or the Python fallback — is zip-
    truncated garbage, so the caller degrades to BM25-only instead."""
    try:
        row = conn.execute(
            "SELECT embedding FROM note_search "
            "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
        ).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        vec = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return len(vec) if isinstance(vec, list) and vec else None


def _default_embed(text: str) -> list[float]:
    """Default per-doc embedder: whatever resolve_embedder() picks. Kept as a
    named function for tests and external callers."""
    fn, _dims, _label = resolve_embedder()
    return fn(text)


# --- Q3 vector cache (§4.4 of the local_embeddings proposal) -----------------
# Measured 2026-08-20: with the real bge model a FULL note_search refresh
# costs minutes of CPU (maint step 6 rebuilds full every run), busting the
# maint budget. Remedy per the proposal: a (sha256(text), model) -> vector
# cache so unchanged docs never re-embed. It lives in the vec SIDECAR
# (<db>_vec.db, schema vecdb) — derived, snapshot-excluded, lazily rebuilt
# state, exactly like the vec0 mirror; a new table in research.db would
# collide with the schema-drift guards and DuckDB scanner expectations.
# Extracted to helpers/core/embed_cache.py (2026-08-21) when the company-
# embeddings populate became the second consumer; the sidecar table keeps
# its note_search_emb_cache name (renaming would orphan warm caches).
from helpers.core.embed_cache import CachedEmbed  # noqa: E402


def _embedding_json(embed_fn, title: str, sector: str, content: str) -> str | None:
    """Embed one doc and serialize to a JSON string, or None on failure.

    Text basis mirrors _get_company_text in embeddings.py: title + sector +
    body. A short deterministic prefix on the body keeps the vector dominated
    by the title/sector for name-typed queries while still capturing the body.
    """
    try:
        vec = embed_fn(f"{title}\n{sector}\n{content[:8000]}")
    except Exception:  # noqa: S110  # best-effort; missing/empty rows stay searchable
        return None
    if not vec:
        return None
    return json.dumps(vec)


def _iter_findata_docs():
    """Yield (doc_type, abs_path, rel_path) for every indexable markdown doc."""
    for p in sorted(FINDATA.rglob("*.md")):
        if any(part in _SKIP_PARTS for part in p.parts):
            continue
        if p.name in _SKIP_NAMES:
            continue
        rel = p.relative_to(FINDATA)
        dtype = _doc_type_for(rel)
        if dtype is None:
            continue  # unmapped area (e.g. a stray top-level file)
        yield dtype, p, rel


def _collect_rows(conn, embed_fn=None) -> list[tuple]:
    """Build the full FTS row set by reading files + one bulk entity lookup.

    Returns a list of (doc_type, file_path, title, sector, content, embedding)
    tuples. embedding is a JSON string (or None if embedding failed/unavailable).
    Entity docs (company/sector/super_sector) get their canonical title +
    sector_classification from the entities table via a single file_path->row
    map (avoids N+1). Newsletters have no entity row; their title is the H1.
    """
    embed_fn = embed_fn or _default_embed
    # Bulk entity lookup: file_path -> (title via normalized_name, sector).
    # title fallback chain: entities.title doesn't exist as a column; the
    # note's YAML title is human form. Use normalized_name (the DB key) as the
    # FTS title for entity docs — it's the resolvable handle the API/graph use.
    ent_by_path: dict[str, tuple[str, str | None]] = {}
    for r in conn.execute(
        "SELECT file_path, normalized_name, sector_classification "
        "FROM entities WHERE file_path IS NOT NULL"
    ):
        ent_by_path[r["file_path"]] = (r["normalized_name"] or "", r["sector_classification"])

    rows = []
    for dtype, abs_path, rel in _iter_findata_docs():
        rel_posix = f"findata/{rel.as_posix()}"
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if dtype in ("company", "sector", "super_sector"):
            fm_title, body = _strip_frontmatter(text)
            norm_name, sector = ent_by_path.get(rel_posix, ("", None))
            # Prefer the DB normalized_name (resolvable handle); fall back to
            # the YAML title if the entity isn't in the DB (shouldn't happen
            # for entity docs, but be defensive).
            title = norm_name or fm_title or ""
            rows.append(
                (dtype, rel_posix, title, sector or "", body.strip(),
                 _embedding_json(embed_fn, title, sector or "", body.strip()))
            )
        else:
            # Newsletter: no frontmatter, no entity row. Title = H1.
            title = _newsletter_title(text) or abs_path.stem
            body = _clean_body(text)
            rows.append(
                (dtype, rel_posix, title, "", body,
                 _embedding_json(embed_fn, title, "", body))
            )
    return rows


# P2.1: incremental meta table — stores per-file fingerprint to avoid
# re-reading/indexing unchanged docs. Populated on full rebuild, consulted
# on incremental rebuild.
NOTE_SEARCH_META_DDL = (
    "CREATE TABLE IF NOT EXISTS note_search_meta ("
    " file_path TEXT PRIMARY KEY,"
    " mtime REAL NOT NULL,"
    " content_hash TEXT NOT NULL"
    ")"
)

def _file_fingerprint(abs_path: Path, title: str, sector: str, content: str) -> tuple[float, str]:
    """Return (mtime, hash) for incremental check (P2.1).

    Hash covers title+sector+content so entity DB changes (sector reclass)
    invalidate even when file mtime is unchanged.  P2 perf: hashlib moved to
    module-level import (was per-file).
    """
    try:
        mtime = abs_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    # Fast hash — blake2b over title|sector|content (first 8 hex chars enough for change detection)
    h = hashlib.blake2b(f"{title}\x00{sector}\x00{content}".encode(), digest_size=8).hexdigest()
    return mtime, h


def _migrate_schema(conn) -> bool:
    """Drop a stale (pre-embedding) note_search so the new DDL applies.

    FTS5 virtual tables can't ALTER TABLE ADD COLUMN, so adding the
    `embedding` column requires DROP + recreate. Since the rebuild fully
    repopulates the table every run (DELETE + reinsert), dropping is safe —
    the shadow tables are recreated by CREATE VIRTUAL TABLE.

    Returns True if a migration (drop) happened, else False.
    """
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='note_search'"
    ).fetchone()
    if not sql:
        return False  # no table yet — fresh create will have the new schema
    if "embedding" in sql[0]:
        return False  # already current
    conn.execute("DROP TABLE note_search")
    return True

def _stamp_note_model(conn, model_label: str) -> None:
    """Record the note_search embedding model in db_meta (A1, apply path only).

    note_search rows carry no model column, so db_meta is the only SQL-side
    home; query.py::_is_warm compares it against the DuckDB _build_meta
    stamp to catch same-dims model swaps (a dims probe alone can't see a
    384->384 swap). Never called from --check: the stamp must describe the
    table's CONTENT, and --check writes no rows.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS db_meta "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', ?)",
        (model_label,),
    )
    conn.commit()


def rebuild(db_path: Path, write: bool = True, incremental: bool = False,  # noqa: C901
            embed_fn=None) -> dict:
    """Rebuild the note_search FTS index. Returns a stats dict."""
    conn = connect(db_path)
    stats: dict = {}
    try:
        migrated = _migrate_schema(conn)
        conn.execute(NOTE_SEARCH_DDL)
        conn.execute(NOTE_SEARCH_META_DDL)
        # Resolve the embedder once: real local model when available, pseudo
        # fallback otherwise. The resolved dims flow into the vec0 mirror so
        # the KNN table always matches the JSON column's vector space.
        # Internally-resolved embedders get the Q3 sidecar cache (unchanged
        # docs never re-embed); injected test embed_fn stay raw.
        embed_dims = _PSEUDO_DIMS
        cache = None
        model_label = None
        if embed_fn is None:
            embed_fn, embed_dims, model_label = resolve_embedder()
            stats["embed_model"] = model_label
            cache = CachedEmbed(embed_fn, model_label, conn) if (
                # Pseudo embedding is a hash — caching it would only bloat
                # the sidecar; only the real model costs CPU per doc.
                model_label != f"dry-run-v{_PSEUDO_DIMS}"
            ) else None
            if cache is not None:
                embed_fn = cache
        rows = _collect_rows(conn, embed_fn=embed_fn)
        if cache is not None:
            stats["embed_cache_hits"] = cache.hits
            stats["embed_cache_misses"] = cache.misses
            if cache.dirty:
                # Commit cache rows NOW, not with the later note_search
                # transaction: --check returns before that transaction, and
                # uncommitted inserts would roll back on close (observed in
                # the warm measurement: hits=0 after a --check pre-warm).
                # The cache is content-addressed, so committing early is
                # safe even if the FTS write later fails.
                conn.commit()

        # Per-doc_type counts for the report.
        from collections import Counter
        by_type = Counter(r[0] for r in rows)
        stats["by_type"] = dict(by_type)
        stats["total_docs"] = len(rows)
        stats["embedded"] = sum(1 for r in rows if r[5])
        stats["migrated"] = migrated

        if not write:
            print(f"(--check mode: would index {len(rows)} docs)", file=sys.stderr)
            return stats

        if not incremental:
            # Full rebuild inside one transaction: DELETE + executemany insert.
            with conn:
                conn.execute("DELETE FROM note_search")
                if rows:
                    conn.executemany(
                        "INSERT INTO note_search "
                        "(doc_type, file_path, title, sector, content, embedding) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                # Refresh meta for incremental next run
                conn.execute("DELETE FROM note_search_meta")
                # P2 perf: build a dict for O(1) lookup instead of O(N²) inner
                # scan, and avoid re-iterating _iter_findata_docs (already
                # iterated in _collect_rows).  We do need abs_path per file for
                # the fingerprint, so build a path map once.
                rows_by_path = {r[1]: r for r in rows}
                meta_rows = []
                for dtype, abs_path, rel in _iter_findata_docs():
                    rel_posix = f"findata/{rel.as_posix()}"
                    r = rows_by_path.get(rel_posix)
                    if r is None:
                        continue
                    _, _, title, sector, content, _emb = r
                    mtime, chash = _file_fingerprint(abs_path, title, sector, content)
                    meta_rows.append((rel_posix, mtime, chash))
                if meta_rows:
                    conn.executemany(
                        "INSERT OR REPLACE INTO note_search_meta (file_path, mtime, content_hash) VALUES (?, ?, ?)",
                        meta_rows,
                    )
            # Sanity: row count matches.
            n = conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
            stats["indexed"] = n
            stats["mode"] = "full"
            # A1: mirror the embedding column into the sqlite-vec vec0 table
            # (after the FTS commit — sync is idempotent and best-effort; the
            # JSON column stays the source of truth).
            stats["vec_rows"] = sync_vec_table(conn, embed_dims, full=True)
            # B4 (sql_capability_unlocks): note_search is invisible to the
            # entities/graph_edges generation triggers (FTS5 can't carry
            # them), so the apply path bumps manually — a warm DuckDB whose
            # v_note_embeddings projection reads this table goes cold on the
            # next connect. --check/sidecar-only paths never reach here.
            # A1: the model stamp rides along (same-only-SQL-side-home rule).
            if model_label is not None:
                _stamp_note_model(conn, model_label)
            stats["generation_bumped"] = bump_generation(conn)
            return stats
        else:
            # P2.1 incremental: diff against meta, only touch changed/deleted files
            # Load existing meta
            existing = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT file_path, mtime, content_hash FROM note_search_meta")}
            # Build map file_path -> (dtype, abs_path, row_tuple)
            rows_by_path = {r[1]: r for r in rows}
            # Track which file_paths we saw on disk
            seen_on_disk = set(rows_by_path.keys())
            to_upsert: list[tuple] = []
            to_delete: list[str] = []
            # Also handle deleted files (in meta but not on disk)
            for fp in list(existing.keys()):
                if fp not in seen_on_disk:
                    to_delete.append(fp)
            # Re-iterate with abs_path to compute fingerprint and diff
            for dtype, abs_path, rel in _iter_findata_docs():
                rel_posix = f"findata/{rel.as_posix()}"
                row = rows_by_path.get(rel_posix)
                if row is None:
                    continue
                _, _, title, sector, content, _emb = row
                mtime, chash = _file_fingerprint(abs_path, title, sector, content)
                prev = existing.get(rel_posix)
                if prev is None or prev[0] != mtime or prev[1] != chash:
                    # P2 perf: stash (row, mtime, chash) so the apply loop
                    # below doesn't recompute the fingerprint a second time.
                    to_upsert.append((row, mtime, chash))
            # Apply delta in one transaction
            with conn:
                for fp in to_delete:
                    conn.execute("DELETE FROM note_search WHERE file_path = ?", (fp,))
                    conn.execute("DELETE FROM note_search_meta WHERE file_path = ?", (fp,))
                for row, mtime, chash in to_upsert:
                    dtype, fpath, title, sector, content, _emb = row
                    conn.execute("DELETE FROM note_search WHERE file_path = ?", (fpath,))
                    conn.execute(
                        "INSERT INTO note_search (doc_type, file_path, title, sector, content, embedding) VALUES (?, ?, ?, ?, ?, ?)",
                        row,
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO note_search_meta (file_path, mtime, content_hash) VALUES (?, ?, ?)",
                        (fpath, mtime, chash),
                    )
            n = conn.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
            stats["indexed"] = n
            stats["mode"] = "incremental"
            stats["upserts"] = len(to_upsert)
            stats["deletes"] = len(to_delete)
            # A1: mirror the same delta into the vec0 table (file_path ->
            # embedding JSON; None embedding removes the vec row).
            stats["vec_rows"] = sync_vec_table(
                conn,
                embed_dims,
                upsert_rows=[(r[1], r[5]) for r, _m, _c in to_upsert],
                delete_paths=to_delete,
            )
            # B4: same writer-side bump as the full branch, but only when the
            # incremental pass actually changed rows — an empty delta leaves
            # the generation (and the warm DuckDB) untouched.
            if to_upsert or to_delete:
                if model_label is not None:
                    _stamp_note_model(conn, model_label)
                stats["generation_bumped"] = bump_generation(conn)
            return stats
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--db", default=str(DEFAULT_DB),
        help="Path to research.db (default: memory/research.db).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Count indexable docs without writing (for CI / dry-run).",
    )
    p.add_argument(
        "--incremental", action="store_true",
        help="Incremental rebuild (only re-index changed/deleted files, P2.1).",
    )
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = _REPO_ROOT / db_path
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    stats = rebuild(db_path, write=not args.check, incremental=args.incremental)
    by_type = stats.get("by_type", {})
    breakdown = ", ".join(f"{t}={by_type[t]}" for t in sorted(by_type))
    print(
        f"note_search: {stats.get('total_docs', 0)} docs ({breakdown})",
        file=sys.stderr,
    )
    if not args.check:
        print(f"indexed {stats.get('indexed', 0)} rows", file=sys.stderr)
        if stats.get("migrated"):
            print("(schema migrated: note_search recreated with embedding column)",
                  file=sys.stderr)
        emb = stats.get("embedded")
        if emb is not None:
            print(f"embedded {emb} rows", file=sys.stderr)
            if "embed_cache_hits" in stats:
                print(
                    f"embed cache: {stats['embed_cache_hits']} hits, "
                    f"{stats['embed_cache_misses']} misses",
                    file=sys.stderr,
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
