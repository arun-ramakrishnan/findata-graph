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
    python3 helpers/maintenance/rebuild_note_search.py --check    # staleness verdict, no writes

--check writes no research.db rows, but DOES warm the sidecar embedding
cache (derived state, Q3) — running --check first is a legitimate way to
pre-pay the one-off cold embed cost before the applying rebuild.

--check reports the drift breakdown (changed/new/deleted file paths, same
shape as rebuild_doc_search / rebuild_script_search) so gate output is
actionable on its own.

Exit codes: 0 success/fresh, 1 DB not found / fatal error OR --check
detected drift (the house --check gate doctrine).
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
# Note-sectioning (note_section_search proposal, 2026-09-06): one row per
# H2 SECTION (plus the preamble), mirroring the doc_search pattern — the
# 512-token bge cap truncated 79% of whole-note vectors to note heads
# (39% token mass); per-section rows fit the window. Row identity is
# (file_path, anchor); title/sector stay note-level in every row.
NOTE_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS note_search USING fts5("
    "doc_type, "  # 0
    "file_path UNINDEXED, "  # 1
    "title, "  # 2
    "sector, "  # 3
    "content, "  # 4 — section body (preamble chunk for heading-less docs)
    "embedding UNINDEXED, "  # 5
    "section_title, "  # 6 — '' for the preamble chunk
    "anchor UNINDEXED, "  # 7 — line number of the section H2 ('1' preamble)
    "tokenize = 'porter unicode61'"
    ")"
)

# Columns we expect in note_search. Used by _migrate_schema to detect a stale
# (pre-sectioning / pre-embedding) table and drop it so the new DDL applies.
_NOTE_SEARCH_COLUMNS = {
    "doc_type",
    "file_path",
    "title",
    "sector",
    "content",
    "embedding",
    "section_title",
    "anchor",
}

# Section body cap for the embedding text base (mirrors doc_search's
# _EMBED_BODY_CAP). 8000 chars ≈ 2k tokens = granite's ctx window
# (embed_full_reembed S6, 2026-09-06): the 1,450 long sections keep their
# tails visible instead of truncating at bge's 512-token/4000-char shape.
# H2 section composition itself is unchanged — only the window grew.
_SECTION_EMBED_CAP = 8000

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
    except TypeError, ValueError:
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


def _embedding_text(title: str, sector: str, section_title: str, content: str) -> str:
    """Section-level embedding text basis — MUST stay identical across the
    per-doc and batch paths or the pooled cache keys diverge. Mirrors
    rebuild_doc_search: title + sector + section heading + capped section
    body, so the vector is dominated by the headings (heading-typed
    queries) while the section body finally fits the 512-token window."""
    return f"{title}\n{sector}\n{section_title}\n{content[:_SECTION_EMBED_CAP]}"


def _embedding_json(
    embed_fn, title: str, sector: str, section_title: str, content: str
) -> str | None:
    """Embed one section row and serialize to a JSON string, or None on
    failure."""
    try:
        vec = embed_fn(_embedding_text(title, sector, section_title, content))
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


def _carry_row(
    row: tuple, dtype: str, rel_posix: str, ent_by_path: dict[str, tuple[str, str | None]]
) -> bool:
    """P2.2: can the stored note_search row be carried verbatim?

    Entity docs take title/sector from the entities table, so they must
    also match their current entities-row — a DB-side rename/reclassify
    without touching the file returns False and gets reprocessed (the
    content-hash diff then upserts it instead of carrying staleness).
    """
    if dtype in ("company", "sector", "super_sector"):
        norm, sec = ent_by_path.get(rel_posix, ("", None))
        return (norm or "") == row[2] and (sec or "") == row[3]
    return True


def _emit_row(
    rows: list[tuple],
    deferred: list[tuple[int, str]] | None,
    dtype: str,
    rel_posix: str,
    title: str,
    sector: str,
    section_title: str,
    anchor: str,
    body: str,
    embed_fn,
) -> None:
    """Append one SECTION row — single emission point for both modes.

    Two-phase mode (``deferred`` is a list): row gets a None embedding and
    (row_index, text) lands in the caller's deferred list for the batch
    embed pass. Per-doc mode: embedding computed inline via embed_fn."""
    if deferred is not None:
        rows.append((dtype, rel_posix, title, sector, body, None, section_title, anchor))
        deferred.append((len(rows) - 1, _embedding_text(title, sector, section_title, body)))
    else:
        rows.append(
            (
                dtype,
                rel_posix,
                title,
                sector,
                body,
                _embedding_json(embed_fn, title, sector, section_title, body),
                section_title,
                anchor,
            )
        )


def _note_sections(text: str, entity_doc: bool) -> list[tuple[str, int, str]]:
    """Split a note into (section_title, anchor_line, body) chunks on H2
    headers — the exact rebuild_doc_search._split_sections semantics
    (deeper headings belong to the parent; the preamble is its own chunk).
    Newsletters split BEFORE _clean_body (its whitespace collapse would
    break the '^## ' prefix match applied afterwards)."""
    from helpers.maintenance.rebuild_doc_search import _split_sections

    if entity_doc:
        return _split_sections(text)
    return [(sec, anchor, _clean_body(body)) for sec, anchor, body in _split_sections(text)]


def _try_carry(
    rel_posix: str,
    abs_path,
    dtype: str,
    reuse: dict[str, tuple[float, list[tuple]]],
    ent_by_path: dict[str, tuple[str, str | None]],
    carried: set[str] | None,
) -> bool:
    """P2.2 verbatim-carry attempt: the stored rows carry over when the
    file's mtime is untouched AND every row's entity title/sector still
    match (a DB-side rename/reclassify must never carry stale even when
    the file's mtime is)."""
    try:
        mtime = abs_path.stat().st_mtime
    except OSError:
        return False
    if mtime != reuse[rel_posix][0]:
        return False
    doc_rows = reuse[rel_posix][1]
    if not all(_carry_row(row, dtype, rel_posix, ent_by_path) for row in doc_rows):
        return False
    if carried is not None:
        carried.add(rel_posix)
    return True


def _collect_rows(
    conn,
    embed_fn=None,
    reuse: dict[str, tuple[float, list[tuple]]] | None = None,
    carried: set[str] | None = None,
    deferred: list[tuple[int, str]] | None = None,
) -> list[tuple]:
    """Collect one 6-tuple row per doc: (dtype, rel_path, title, sector,
    body, embedding_json).

    ``deferred`` switches embedding to two-phase batch mode: rows are
    collected with a None embedding and (row_index, text) pairs appended
    to the caller's list — the caller then batch-embeds via
    cached_embed_batch (pinned pool; parallel_cold_embed proposal) and
    patches the rows. embed_fn is unused in that mode. Reuse-carried rows
    are untouched by deferral (they bring their old embedding)."""
    """Build the full FTS row set by reading files + one bulk entity lookup.

    Returns a list of (doc_type, file_path, title, sector, content, embedding)
    tuples. embedding is a JSON string (or None if embedding failed/unavailable).
    Entity docs (company/sector/super_sector) get their canonical title +
    sector_classification from the entities table via a single file_path->row
    map (avoids N+1). Newsletters have no entity row; their title is the H1.

    P2.2 fast path (incremental no-op cycles): ``reuse`` maps file_path to
    (stored_mtime, existing row tuple). When the file's stat mtime matches,
    the stored row is carried over VERBATIM — no read, no body clean, no
    cache lookup, no vector JSON round-trip (the stored embedding is already
    correct for unchanged content); the path is recorded in ``carried`` so
    the diff loop can skip re-hashing it. Entity docs additionally verify
    their entities-table title/sector, so a DB-side rename/reclassify is
    never carried stale even when the file's mtime is untouched. mtime is
    the change key on this path otherwise (same-mtime content edits are
    the full rebuild's job — the non-incremental mode stays the
    self-healing convergence pass).
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
        if reuse is not None and rel_posix in reuse:
            if _try_carry(rel_posix, abs_path, dtype, reuse, ent_by_path, carried):
                rows.extend(reuse[rel_posix][1])
                continue
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sector = ""
        if dtype in ("company", "sector", "super_sector"):
            fm_title, body = _strip_frontmatter(text)
            norm_name, sector = ent_by_path.get(rel_posix, ("", None))
            # Prefer the DB normalized_name (resolvable handle); fall back to
            # the YAML title if the entity isn't in the DB (shouldn't happen
            # for entity docs, but be defensive).
            title = norm_name or fm_title or ""
            chunks = _note_sections(body, entity_doc=True)
        else:
            # Newsletter: no frontmatter, no entity row. Title = H1.
            title = _newsletter_title(text) or abs_path.stem
            chunks = _note_sections(text, entity_doc=False)
        for section_title, anchor, sec_body in chunks:
            _emit_row(
                rows,
                deferred,
                dtype,
                rel_posix,
                title,
                sector or "",
                section_title,
                str(anchor),
                sec_body.strip(),
                embed_fn,
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


def _rows_by_path(rows: list[tuple]) -> dict[str, list[tuple]]:
    """Group section rows by file_path — the FILE stays the diff/fingerprint
    unit (note_search_meta stays file-keyed); only rows multiply."""
    grouped: dict[str, list[tuple]] = {}
    for r in rows:
        grouped.setdefault(r[1], []).append(r)
    return grouped


def vec_row_key(row: tuple) -> str:
    """Composite vec0/matrix key for a section row: "{file_path}#{anchor}".
    The FILE must not contain '#' (findata paths don't) — one separator,
    unambiguous split."""
    return f"{row[1]}#{row[7]}"


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
    """Drop a stale (pre-sectioning or pre-embedding) note_search so the new
    DDL applies. FTS5 virtual tables can't ALTER TABLE ADD COLUMN, so the
    sectioning columns require DROP + recreate. Since the rebuild fully
    repopulates the table every run (DELETE + reinsert), dropping is safe —
    the shadow tables are recreated by CREATE VIRTUAL TABLE.

    Returns True if a migration (drop) happened, else False.
    """
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='note_search'"
    ).fetchone()
    if not sql:
        return False  # no table yet — fresh create will have the new schema
    if "section_title" in sql[0] and "embedding" in sql[0]:
        return False  # already current
    conn.execute("DROP TABLE note_search")
    return True


def _refresh_embed_matrix(conn) -> int | None:  # type: ignore[no-untyped-def]
    """S2d (corpus_embeddings_scaling): best-effort refresh of the aligned
    f32 matrix (memory/embed_matrix.f32) after note_search embeddings move.
    Same derived-state class as the vec0 mirror — rebuilt here, never
    load-bearing; any failure is silent (the search-side staleness gate
    falls back to the Python cosine, not to stale matrix results)."""
    try:
        import json as _json

        import numpy as _np

        from helpers.core.embed_matrix import EmbedMatrixStore

        # Pre-sectioning tables (the deploy gap before the sectioned rebuild
        # runs) have no anchor column — key those by bare file_path.
        has_anchor = conn.execute(
            "SELECT 1 FROM pragma_table_info('note_search') WHERE name = 'anchor'"
        ).fetchone()
        key_sql = "file_path || '#' || anchor" if has_anchor else "file_path"
        rows = conn.execute(
            f"SELECT {key_sql}, embedding FROM note_search"  # noqa: S608  # key_sql is a schema-conditional constant
            " WHERE embedding IS NOT NULL ORDER BY file_path" + (", anchor" if has_anchor else "")
        ).fetchall()
        if not rows:
            return None
        ids = [r[0] for r in rows]
        emb = _np.array([_json.loads(r[1]) for r in rows], dtype=_np.float32)
        return EmbedMatrixStore().refresh(ids, emb)["rewritten"]
    except Exception:  # noqa: S110  # matrix refresh is best-effort, never gate the rebuild
        return None


def _stamp_note_model(conn, model_label: str) -> None:
    """Record the note_search embedding model in db_meta (A1, apply path only).

    note_search rows carry no model column, so db_meta is the only SQL-side
    home; query.py::_is_warm compares it against the DuckDB _build_meta
    stamp to catch same-dims model swaps (a dims probe alone can't see a
    384->384 swap). Never called from --check: the stamp must describe the
    table's CONTENT, and --check writes no rows.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', ?)",
        (model_label,),
    )
    conn.commit()


def rebuild(  # noqa: C901  # noqa anchor moved to the statement's diagnostic line (ruff-format split)
    db_path: Path,
    write: bool = True,
    incremental: bool = False,
    embed_fn=None,
) -> dict:
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
            cache = (
                CachedEmbed(embed_fn, model_label, conn, source="note")
                # Pseudo embedding is a hash — caching it would only bloat
                # the sidecar; only the real model costs CPU per doc.
                if (model_label != f"dry-run-v{_PSEUDO_DIMS}")
                else None
            )
            if cache is not None:
                embed_fn = cache
        # P2.2 fast path: in incremental mode, preload the meta table and
        # the existing rows so _collect_rows can carry over mtime-unchanged
        # files without reading/cleaning/embedding them.
        existing: dict[str, tuple[float, str]] = {}
        reuse: dict[str, tuple[float, list[tuple]]] | None = None
        carried: set[str] | None = None
        if incremental:
            existing = {
                r[0]: (r[1], r[2])
                for r in conn.execute("SELECT file_path, mtime, content_hash FROM note_search_meta")
            }
            # Section rows: reuse maps file_path -> (mtime, [section rows]) —
            # a file carries over only when ALL its rows' entity title/sector
            # still match (_carry_row per row).
            reuse: dict[str, tuple[float, list[tuple]]] = {}
            for r in conn.execute(
                "SELECT doc_type, file_path, title, sector, content, embedding, "
                "section_title, anchor FROM note_search"
            ):
                prev = existing.get(r[1])
                if prev is not None:
                    reuse.setdefault(r[1], (prev[0], []))[1].append(tuple(r))
            carried = set()
        # Two-phase batch mode (parallel_cold_embed proposal, 2026-08-29):
        # with the real model + cache, rows are collected WITHOUT
        # embeddings; the texts then go through cached_embed_batch ->
        # local_embedder.embed_documents_parallel (pinned spawn pool;
        # cold 16m13s -> ~4-5 min, warm cycles unchanged: ~0 misses means
        # the pool never spawns). Injected embed_fn (tests) and the pseudo
        # fallback keep the per-doc path.
        deferred: list[tuple[int, str]] | None = [] if cache is not None else None
        print("[notes] phase: walking findata + collecting rows...", file=sys.stderr, flush=True)
        rows = _collect_rows(
            conn, embed_fn=embed_fn, reuse=reuse, carried=carried, deferred=deferred
        )
        print(
            f"[notes] phase: collected {len(rows)} rows"
            + (f", {len(deferred or [])} to embed" if deferred is not None else ""),
            file=sys.stderr,
            flush=True,
        )
        if deferred is None:
            if cache is not None:
                stats["embed_cache_hits"] = cache.hits
                stats["embed_cache_misses"] = cache.misses
                if cache.dirty:
                    # Commit cache rows NOW, not with the later note_search
                    # transaction: --check returns before that transaction,
                    # and uncommitted inserts would roll back on close
                    # (observed in the warm measurement: hits=0 after a
                    # --check pre-warm). The cache is content-addressed, so
                    # committing early is safe even if the FTS write later
                    # fails.
                    conn.commit()
        else:
            from helpers.core.embed_cache import cached_embed_batch
            from helpers.core import local_embedder

            # Empty/whitespace texts stay un-embedded (same degrade as the
            # per-doc path's except-None) instead of poisoning the batch.
            idxs = [i for i, text in deferred if text.strip()]
            texts = [text for _i, text in deferred if text.strip()]
            if model_label is None:  # unreachable: batch mode implies resolve
                raise RuntimeError("batch embed without a model label")
            print(
                f"[notes] phase: embedding {len(texts)} section bases (serial per-text)...",
                file=sys.stderr,
                flush=True,
            )
            try:
                vec_list, cstats = cached_embed_batch(
                    conn,
                    texts,
                    model_label,
                    local_embedder.embed_documents_parallel,
                    source="note",
                )
                by_idx = dict(zip(idxs, vec_list))
                rows = [
                    r if i not in by_idx else r[:5] + (json.dumps(by_idx[i]),) + r[6:]
                    for i, r in enumerate(rows)
                ]
                stats["embed_cache_hits"] = cstats["hits"]
                stats["embed_cache_misses"] = cstats["misses"]
            except Exception as e:  # best-effort: a broken pool must never
                # break the rebuild — docs stay lexical-searchable (mirrors
                # _embedding_json's per-doc degrade, just coarser).
                print(
                    f"WARNING: batch embed failed ({e}); docs remain searchable without vectors",
                    file=sys.stderr,
                )
                stats["embed_cache_hits"] = 0
                stats["embed_cache_misses"] = len(deferred)

        # Per-doc_type counts for the report.
        from collections import Counter

        by_type = Counter(r[0] for r in rows)
        stats["by_type"] = dict(by_type)
        stats["total_docs"] = len(rows)
        stats["embedded"] = sum(1 for r in rows if r[5])
        stats["migrated"] = migrated

        # Freshness verdict (2026-08-26): exact diff of the on-disk corpus
        # vs the stored meta — same fingerprint semantics as the
        # incremental diff loop below (mtime + content hash over
        # title|sector|content), so --check reports exactly the drift an
        # incremental run would apply. Always computed (cheap: one stat +
        # one blake2b per doc); --check prints it and main() turns drift
        # into exit 1 (house --check gate doctrine, as rebuild_doc_search
        # / rebuild_script_search already do — note-search-check in the
        # advisory gate previously passed silently even when stale).
        stored_meta = existing or {
            r[0]: (r[1], r[2])
            for r in conn.execute("SELECT file_path, mtime, content_hash FROM note_search_meta")
        }
        rows_by_path = _rows_by_path(rows)
        current_meta: dict[str, tuple[float, str]] = {}
        for _dtype, abs_path, rel in _iter_findata_docs():
            rel_posix = f"findata/{rel.as_posix()}"
            doc_rows = rows_by_path.get(rel_posix)
            if not doc_rows:
                continue
            title, sector = doc_rows[0][2], doc_rows[0][3]
            joined = "\x00".join(r[4] for r in doc_rows)
            current_meta[rel_posix] = _file_fingerprint(abs_path, title, sector, joined)
        on_disk = set(current_meta)
        stale_new = sorted(fp for fp in on_disk if fp not in stored_meta)
        stale_deleted = sorted(fp for fp in stored_meta if fp not in on_disk)
        # Content-hash level: mtime is deliberately excluded from the
        # verdict (it is only the carry fast path) — shared index DBs
        # across git worktrees/checkouts see mtime skew on identical
        # content (2026-08-30). The hash covers title+sector+content, so
        # entity DB changes (sector reclass) still invalidate.
        stale_changed = sorted(
            fp for fp in on_disk if fp in stored_meta and stored_meta[fp][1] != current_meta[fp][1]
        )
        stats["stale_new"] = stale_new
        stats["stale_changed"] = stale_changed
        stats["stale_deleted"] = stale_deleted
        stats["index_stale"] = bool(stale_new or stale_changed or stale_deleted)

        if not write:
            print(f"(--check mode: would index {len(rows)} docs)", file=sys.stderr)
            _print_staleness(stats)
            return stats

        if not incremental:
            # maint_full_zero_churn (F2 sibling): capture the pre-rebuild
            # content so the B4 bump below fires only when it actually
            # changed — the full branch bumped unconditionally, flipping
            # _is_warm and churning db_meta on every no-op maint-full cycle.
            from collections import Counter as _Counter

            # tuple() each row: sqlite3.Row never == a plain tuple, so the
            # multiset compare would report a phantom change every cycle.
            existing_rows = [
                tuple(r)
                for r in conn.execute(
                    "SELECT doc_type, file_path, title, sector, content, embedding, "
                    "section_title, anchor FROM note_search"
                )
            ]
            content_changed = _Counter(existing_rows) != _Counter(rows)
            # Full rebuild inside one transaction: DELETE + executemany insert.
            with conn:
                conn.execute("DELETE FROM note_search")
                if rows:
                    conn.executemany(
                        "INSERT INTO note_search "
                        "(doc_type, file_path, title, sector, content, embedding, "
                        "section_title, anchor) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                # Refresh meta for incremental next run
                conn.execute("DELETE FROM note_search_meta")
                meta_rows = []
                for dtype, abs_path, rel in _iter_findata_docs():
                    rel_posix = f"findata/{rel.as_posix()}"
                    doc_rows = rows_by_path.get(rel_posix)
                    if not doc_rows:
                        continue
                    title, sector = doc_rows[0][2], doc_rows[0][3]
                    joined = "\x00".join(r[4] for r in doc_rows)
                    mtime, chash = _file_fingerprint(abs_path, title, sector, joined)
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
            print("[notes] phase: syncing vec0 mirror...", file=sys.stderr, flush=True)
            stats["vec_rows"] = sync_vec_table(conn, embed_dims, full=True)
            # S2d: keep the aligned f32 matrix in step (hash-gated row rewrites)
            print("[notes] phase: refreshing f32 matrix...", file=sys.stderr, flush=True)
            stats["matrix_rows"] = _refresh_embed_matrix(conn)
            # B4 (sql_capability_unlocks): note_search is invisible to the
            # entities/graph_edges generation triggers (FTS5 can't carry
            # them), so the apply path bumps manually — a warm DuckDB whose
            # v_note_embeddings projection reads this table goes cold on the
            # next connect. ONLY when the rebuild actually changed content
            # (mirrors the incremental branch's to_upsert/to_delete guard).
            # --check/sidecar-only paths never reach here.
            # A1: the model stamp rides along (same-only-SQL-side-home rule).
            if model_label is not None:
                _stamp_note_model(conn, model_label)
            if content_changed:
                stats["generation_bumped"] = bump_generation(conn)
            return stats
        else:
            # P2.1 incremental: diff against meta, only touch changed/deleted files
            # (existing / reuse / carried were preloaded above for the fast path)
            # Section rows: the FILE stays the diff unit — to_upsert carries a
            # file's full section-row list (a changed file re-emits all its
            # sections; unchanged section texts hit the content-hash embed
            # cache, so only genuinely changed sections cost CPU).
            seen_on_disk = set(rows_by_path.keys())
            to_upsert: list[tuple[list[tuple], float, str]] = []
            to_delete: list[str] = []
            # Also handle deleted files (in meta but not on disk)
            for fp in list(existing.keys()):
                if fp not in seen_on_disk:
                    to_delete.append(fp)
            # Re-iterate with abs_path to compute fingerprint and diff
            for dtype, abs_path, rel in _iter_findata_docs():
                rel_posix = f"findata/{rel.as_posix()}"
                doc_rows = rows_by_path.get(rel_posix)
                if not doc_rows:
                    continue
                prev = existing.get(rel_posix)
                if prev is not None and carried is not None and rel_posix in carried:
                    # DB-carried rows whose mtime still matches: the content
                    # is by construction the stored one — skip the re-hash.
                    try:
                        if abs_path.stat().st_mtime == prev[0]:
                            continue
                    except OSError:
                        pass
                title, sector = doc_rows[0][2], doc_rows[0][3]
                joined = "\x00".join(r[4] for r in doc_rows)
                mtime, chash = _file_fingerprint(abs_path, title, sector, joined)
                if prev is None or prev[1] != chash:
                    # Hash-only guard: an mtime drift with identical content
                    # (worktree/checkout skew) must NOT re-upsert — it would
                    # bump the DB generation and cool the warm DuckDB cache
                    # for no content change.
                    to_upsert.append((doc_rows, mtime, chash))
            # Apply delta in one transaction
            with conn:
                for fp in to_delete:
                    conn.execute("DELETE FROM note_search WHERE file_path = ?", (fp,))
                    conn.execute("DELETE FROM note_search_meta WHERE file_path = ?", (fp,))
                for doc_rows, mtime, chash in to_upsert:
                    fpath = doc_rows[0][1]
                    # Whole-file replace: removed sections must not survive.
                    conn.execute("DELETE FROM note_search WHERE file_path = ?", (fpath,))
                    conn.executemany(
                        "INSERT INTO note_search (doc_type, file_path, title, sector, "
                        "content, embedding, section_title, anchor) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        doc_rows,
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
            # A1: mirror the same delta into the vec0 table. Keys are
            # composite "{file_path}#{anchor}" row keys; every upserted file
            # is prefix-deleted first so dropped sections leave no stale
            # vectors behind.
            stats["vec_rows"] = sync_vec_table(
                conn,
                embed_dims,
                upsert_rows=[
                    (vec_row_key(r), r[5]) for doc_rows, _m, _c in to_upsert for r in doc_rows
                ],
                # prefix delete per changed/deleted FILE (dropped sections
                # must not survive); exact delete_paths stays for callers
                # that know true row keys.
                delete_files=[doc_rows[0][1] for doc_rows, _m, _c in to_upsert] + to_delete,
            )
            # S2d: same delta through the aligned matrix (full refresh is
            # hash-gated — unchanged rows are no-ops; a delete changes the
            # id set and forces one clean rebuild).
            stats["matrix_rows"] = _refresh_embed_matrix(conn)
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


def _print_staleness(stats: dict) -> None:
    """--check verdict: FRESH, or the drift breakdown + remediation
    (mirrors rebuild_doc_search / rebuild_script_search --check shape)."""
    new = stats.get("stale_new", [])
    changed = stats.get("stale_changed", [])
    deleted = stats.get("stale_deleted", [])
    if not (new or changed or deleted):
        print(f"index state: FRESH ({stats.get('total_docs', 0)} docs unchanged)", file=sys.stderr)
        return
    print(
        f"index state: STALE — {len(changed)} changed, {len(new)} new, {len(deleted)} deleted",
        file=sys.stderr,
    )
    drift = (
        [(fp, "changed") for fp in changed]
        + [(fp, "new") for fp in new]
        + [(fp, "deleted") for fp in deleted]
    )
    for fp, kind in drift[:10]:
        print(f"  {kind:8s} {fp}", file=sys.stderr)
    if len(drift) > 10:
        print(f"  … and {len(drift) - 10} more", file=sys.stderr)
    print("refresh: python3 helpers/maintenance/rebuild_note_search.py", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Path to research.db (default: memory/research.db).",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Count indexable docs without writing (for CI / dry-run).",
    )
    p.add_argument(
        "--incremental",
        action="store_true",
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
            print("(schema migrated: note_search recreated with embedding column)", file=sys.stderr)
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
