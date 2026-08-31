import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
)

load_dotenv()

app = Flask(__name__)

# Cache for /api/sectors sector_entity file reads (read 42 markdown files +
# YAML-parse on every request otherwise). Keyed on (mtime_ns, size) per file,
# so any content change on disk auto-invalidates — no generation coupling,
# and immune to test-DB swapping because the key is purely file-content based.
_SECTOR_ENTITY_CACHE: tuple[tuple[tuple[int, int], ...], list[dict]] | None = None
_SECTOR_ENTITY_LOCK = threading.Lock()

# Configure logging. In production gunicorn handles stdout handlers; in dev
# we attach a basic one. Level overridable via LOG_LEVEL.
if not app.logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
app.logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# --- Static File Routes ---
@app.route("/points_and_figures/images/<path:filename>")
def serve_points_and_figures_images(filename):
    """Serve images from Points_And_Figures directory"""
    return send_from_directory(
        os.path.join("findata", "Points_And_Figures", "images"),
        filename,
        mimetype="image/jpeg",  # Default to JPEG, will work for most images
    )


@app.route("/")
def index():
    """Main landing page - FinData Knowledge Graph Viewer"""
    return render_template("findata.html")


# --- FinData Viewer Routes ---


def get_db_connection():
    """Get database connection.

    Uses the shared `helpers.core.db.connect` helper which enables FK
    enforcement and WAL mode. Keeps the sqlite3.Row factory so handlers can
    access columns by name.
    """
    from helpers.core.db import connect
    return connect()


def _super_sector_hierarchy() -> list[dict]:
    """Build the super-sector -> child-sectors hierarchy (Bundle M4).

    Returns a list of ``{"name": <super_sector>, "sectors": [...]}`` dicts
    sourced from the `belongs_to` edges. Ordered by super-sector name. If the
    hierarchy hasn't been built yet (no super_sector entities), returns [].
    """
    conn = get_db_connection()
    try:
        super_sectors = [
            r[0] for r in conn.execute(
                "SELECT name FROM entities WHERE entity_type='super_sector' "
                "ORDER BY name"
            ).fetchall()
        ]
        if not super_sectors:
            return []
        result = []
        for ss in super_sectors:
            children = [
                r[0] for r in conn.execute(
                    "SELECT source FROM graph_edges "
                    "WHERE target=? AND edge_type='belongs_to' "
                    "ORDER BY source", (ss,)
                ).fetchall()
            ]
            result.append({"name": ss, "sectors": children})
        return result
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Graph DB connection (DuckDB, long-lived per design §9)                        #
# --------------------------------------------------------------------------- #
# Lazy singleton: opened on first /api/graph/* request so an import-time
# DuckDB install failure cannot break the OCR / findata paths (which
# use only SQLite). On failure the exception is cached and re-raised on every
# subsequent call — surfaces as a clean 500 to the client — but only for
# _GRAPH_ERROR_TTL seconds, after which the next call retries connect() so a
# transient blip (mid-copy file, extension download, momentary lock) doesn't
# brick the endpoints until process restart.
#
# Callers should NOT hold a reference; always reach through this helper so the
# /api/graph/refresh endpoint can swap the cached connection in place.
_graph_con: Any = None
_graph_con_error: Exception | None = None
# Wall-clock time the current cached error was recorded (time.monotonic()).
# Used by the TTL auto-recover path in get_graph_connection(): once older than
# _GRAPH_ERROR_TTL, the next request retries connect() instead of re-raising
# the stale error. None when there is no cached error.
_graph_error_at: float | None = None
_GRAPH_ERROR_TTL = 60.0  # seconds; tradeoff between retry cost and recovery latency

# Serializes init/reset of _graph_con across Flask worker threads. DuckDB
# connections are not safe to share across threads for concurrent execute, and
# two threads racing the lazy-init branch would both call connect(). The lock
# covers init AND reset so a refresh can't null the connection mid-query in
# another thread. Query execution itself is NOT locked — concurrent reads on a
# read-write connection are fine; only the init/reset swap needs guarding.
_graph_lock = threading.Lock()

# Cached ETag for /api/graph/* responses (Bundle C4). Derived from the DuckDB
# cache's _build_meta.built_at — the cache is immutable between explicit
# POST /api/graph/refresh calls (which bump built_at), so an ETag keyed on it
# lets browsers absorb repeat traffic as a free 304. None = not yet computed
# or cache unreadable; cleared by _reset_graph_connection() so a refresh
# immediately produces a fresh ETag.
_graph_etag: str | None = None


def get_graph_connection():
    """Return the long-lived DuckDB graph connection (design §9).

    Opens lazily on first call. If opening fails, caches the exception and
    re-raises on subsequent calls WITHOUT retrying — but only for
    _GRAPH_ERROR_TTL seconds. After the TTL elapses, the next call retries
    connect() so transient blips auto-recover without operator action.
    """
    global _graph_con, _graph_con_error, _graph_error_at
    # Fast path: connection is live. No lock needed for the read — the worst
    # case under a race is a thread sees None and takes the slow path, which is
    # correct (it'll re-init under the lock below).
    if _graph_con is not None:
        return _graph_con
    with _graph_lock:
        # Re-check inside the lock — another thread may have inited.
        if _graph_con is not None:
            return _graph_con
        # Cached error still within TTL? Re-raise without retrying.
        if (_graph_con_error is not None
                and _graph_error_at is not None
                and time.monotonic() - _graph_error_at < _GRAPH_ERROR_TTL):
            raise _graph_con_error
        # Either no cached error, or it's stale — attempt (re)connect.
        try:
            from helpers.graph.query import connect as duckdb_connect
            # read_only=True: every /api/graph/* handler only QUERIES the
            # cache, and a read-write DuckDB open demands exclusivity against
            # ALL other connections (even read-only ones — that single detail
            # raced live-invariants against the RO-holding parallel advisory
            # steps on 2026-08-26). connect() falls back to its RW build path
            # automatically when the cache is cold/stale, so read-only is
            # safe for readers and eliminates the cross-process contention.
            _graph_con = duckdb_connect(read_only=True)
            _graph_con_error = None
            _graph_error_at = None
            return _graph_con
        except Exception as e:
            _graph_con_error = e
            _graph_error_at = time.monotonic()
            app.logger.error("graph connection init failed: %s", e)
            raise


def _graph_build_etag() -> str | None:
    """Stable ETag for /api/graph/* responses, derived from the DuckDB cache's
    ``_build_meta.built_at`` (Bundle C4).

    The DuckDB cache is immutable between explicit POST /api/graph/refresh
    calls — refresh rebuilds the whole cache and bumps built_at via
    _mark_warm. So an ETag keyed on built_at lets browsers absorb repeat
    traffic as a free 304, and a refresh provably invalidates every cached
    response.

    Returns None if the cache is cold/unreadable — callers skip caching
    rather than letting a caching-layer failure break the request. The value
    is cached in the module global ``_graph_etag`` and cleared by
    _reset_graph_connection() so a refresh produces a fresh ETag on the next
    request.

    The ETag is weak (``W/``) because multiple gunicorn workers may serve
    byte-different JSON (key ordering) for semantically-identical content.

    Reads built_at from the live ``_graph_con`` when it's already open (the
    common case in production). Since 2026-08-26 the singleton itself opens
    read-only (deadlock fix vs parallel make runs), so even the cold-start
    fallback's separate read-only open coexists safely with it — DuckDB
    allows any number of concurrent read-only openers; it only forbids
    mixing configs.
    """
    global _graph_etag
    if _graph_etag is not None:
        return _graph_etag
    r = None
    try:
        if _graph_con is not None:
            # Reuse the live read-write connection (production common case).
            r = _graph_con.execute(
                "SELECT value FROM _build_meta WHERE key='built_at'"
            ).fetchone()
        else:
            # Cold start: no singleton yet, so a read-only connection is safe.
            import duckdb
            from helpers.graph.query import DUCKDB_PATH
            con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
            try:
                r = con.execute(
                    "SELECT value FROM _build_meta WHERE key='built_at'"
                ).fetchone()
            finally:
                con.close()
    except Exception as e:
        # Cold cache, missing _build_meta, or DuckDB unavailable. Don't let
        # caching break the request — just skip the ETag.
        app.logger.debug("graph ETag derivation skipped: %s", e)
        return None
    if r is None:
        return None
    _graph_etag = f'W/"graph-{r[0]}"'
    return _graph_etag


def _reset_graph_connection() -> None:
    """Close and discard the cached graph connection (and cached init error).

    Used by the /api/graph/refresh admin endpoint after the SQLite source has
    been updated (e.g. by parse_newsletter --apply + derive-relations) so the
    next /api/graph/* request sees fresh data. Also closes the file handle
    (DuckDB single-writer contract — see doc/design/graph_design.txt §8) so the
    subsequent rebuild() can reopen the file read-write.
    """
    global _graph_con, _graph_con_error, _graph_error_at, _graph_etag
    with _graph_lock:
        if _graph_con is not None:
            # Guard getattr: unit tests seed a bare object() sentinel that has
            # no .close(); real DuckDB connections do.
            close = getattr(_graph_con, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as e:
                    app.logger.warning("graph connection close failed: %s", e)
            _graph_con = None
        _graph_con_error = None
        _graph_error_at = None
        # Drop the cached ETag so the next response derives a fresh one from
        # the rebuilt cache's built_at (Bundle C4). Without this, a refresh
        # would keep serving the old ETag and clients could get stale 304s.
        _graph_etag = None
        # P1.3: also evict the module-level DuckDB cache in helpers.graph.query
        # so the next get_graph_connection() sees the fresh generation.
        try:
            from helpers.graph.query import clear_graph_cache
            clear_graph_cache()
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass


def _resolve_entity_or_404(name: str) -> str:
    """Case-insensitive entity-name lookup → canonical name. 404 if unknown.

    All /api/graph/<name> routes go through this so URL casing doesn't matter
    (`/api/graph/peers/ceat` resolves to `CEAT`). The canonical name is what
    the graph wrappers need: their SQL WHERE clauses match on exact string
    equality against the materialised names.
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT name FROM entities WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404, description=f"Entity not found: {name}")
    return row["name"]


def _resolve_entity_with_type_or_404(name: str) -> tuple[str, str]:
    """Same as _resolve_entity_or_404 but also returns entity_type.

    Used by routes that need to branch on company-vs-sector behaviour
    (notably /api/graph/neighbors, which renders a different bundle for each).
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT name, entity_type FROM entities WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404, description=f"Entity not found: {name}")
    return row["name"], row["entity_type"]


def _parse_as_of_or_400():
    """Parse the ?as_of= query param, returning None if absent.

    Accepts 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD'. Returns 400 on bad shapes
    so the client gets a clean error rather than a 500 from SQL.
    """
    raw = request.args.get("as_of", "").strip()
    if not raw:
        return None
    from helpers.graph.query import _normalise_as_of
    try:
        return _normalise_as_of(raw)
    except ValueError as e:
        abort(400, description=str(e))


def parse_yaml_frontmatter(content):
    """Parse YAML frontmatter from markdown content"""
    if content.startswith("---\n"):
        try:
            end_index = content.find("\n---\n", 4)
            if end_index != -1:
                yaml_content = content[4:end_index]
                return yaml.safe_load(yaml_content), content[end_index + 5 :]
        except yaml.YAMLError as e:
            # Malformed frontmatter — log and fall through to raw content rather
            # than masking every exception (the prior bare `except:` would also
            # swallow KeyboardInterrupt/SystemExit).
            app.logger.warning("YAML frontmatter parse error: %s", e)
    return {}, content


@app.route("/entity/<path:entity_path>")
def entity_detail_page(entity_path):
    """Separate page for entity details"""
    return render_template("entity_detail.html", entity_path=entity_path)


@app.route("/findata")
def findata_viewer():
    """Main FinData viewer page"""
    return render_template("findata.html")


@app.route("/api/entities")
def api_entities():
    """API endpoint to get all entities with filtering"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get query parameters
    entity_type = request.args.get("type", "")
    sector = request.args.get("sector", "")
    marketcap = request.args.get("marketcap", "")
    search = request.args.get("search", "")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    # Filtering uses the normalized entity_tags table (indexed, no fuzzy LIKE).
    # The sector/marketcap params arrive as the human form (e.g. 'Banking',
    # 'large_cap'); we match against the lowercase canonical tag values.
    where = ["1=1"]
    params = []

    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)

    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM entity_tags t "
            "WHERE t.entity_name = entities.name AND t.tag = ?)"
        )
        params.append("sector/" + sector.lower())

    if marketcap:
        where.append(
            "EXISTS (SELECT 1 FROM entity_tags t "
            "WHERE t.entity_name = entities.name AND t.tag = ?)"
        )
        params.append("market_cap/" + marketcap.lower())

    if search:
        # match name OR any sector tag (case-insensitive)
        where.append(
            "(LOWER(entities.name) LIKE LOWER(?) OR "
            "EXISTS (SELECT 1 FROM entity_tags t "
            "WHERE t.entity_name = entities.name AND LOWER(t.tag) LIKE LOWER(?)))"
        )
        params.extend([f"%{search}%", f"sector/%{search}%"])

    where_clause = " AND ".join(where)

    # Data query
    cursor.execute(
        f"SELECT name, entity_type, sector_classification, file_path "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        f"FROM entities WHERE {where_clause} ORDER BY name LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    rows = cursor.fetchall()
    entities = []

    # Bulk-fetch tags for all returned entities from the normalized entity_tags table.
    tags_by_entity = {}
    if rows:
        names = [r[0] for r in rows]
        placeholders = ",".join("?" * len(names))
        cur = conn.cursor()
        cur.execute(
            f"SELECT entity_name, tag FROM entity_tags WHERE entity_name IN ({placeholders})",  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            names,
        )
        for ename, tag in cur.fetchall():
            tags_by_entity.setdefault(ename, []).append(tag)

    for row in rows:
        # Bundle C2: market_cap is derived from the market_cap/* tag (the
        # column was dropped — tag is source of truth). tags_by_entity is
        # already fetched above, so this adds zero queries.
        tags = tags_by_entity.get(row[0], [])
        mc_tag = next((t for t in tags if t.startswith("market_cap/")), None)
        market_cap = mc_tag.split("/", 1)[1] if mc_tag else None
        entity = {
            "name": row[0],
            "entity_type": row[1],
            "sector_classification": row[2],
            "market_cap": market_cap,
            "enhanced_tags": sorted(tags),
            "file_path": row[3],
        }
        entities.append(entity)

    # Total count reuses the same WHERE clause.
    cursor.execute(f"SELECT COUNT(*) FROM entities WHERE {where_clause}", params)  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
    total_count = cursor.fetchone()[0]

    conn.close()

    return jsonify(
        {
            "entities": entities,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }
    )


def _scored_rows(rows, q_vec, knn: dict[str, float] | None) -> list[tuple[int, Any, float]]:
    """Per-row cosine similarity, from the A1 KNN map when available.

    scored: list of (orig_index, row, similarity) — tuples keep the Row type
    through the re-rank (a dict would widen the row slot to the union of all
    value types). orig_index = BM25 position (rows arrive rank-sorted).

    A1 path: similarity straight off the whole-corpus KNN map; docs without
    a vec row (missing/invalid embedding) get 0.0, same contract as the
    Python path below.
    """
    import json
    import math

    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    out: list[tuple[int, Any, float]] = []
    for orig_index, row in enumerate(rows):
        if knn is not None:
            sim = knn.get(row[1], 0.0)
        else:
            embedding = row[4]
            vec = None
            if embedding:
                try:
                    vec = json.loads(embedding)
                except (TypeError, ValueError):
                    vec = None
            sim = _cosine(q_vec, vec) if (q_vec and vec) else 0.0
        out.append((orig_index, row, sim))
    return out


def _cosine_positions(
    rows, knn: dict[str, float] | None, scored: list[tuple[int, Any, float]]
) -> dict[int, int]:
    """Cosine-leg position per page index: 0 = most similar.

    A1 path (knn is not None): position = the doc's GLOBAL KNN rank. Page
    docs not in the map (only missing-embedding docs, since k covers the
    whole corpus) rank after every KNN hit; orig_index keeps their relative
    order deterministic. Their BM25 leg is unaffected.

    Fallback path: position within the page-local Python cosine ranking.
    """
    if knn is not None:
        knn_order = sorted(knn, key=lambda fp: knn[fp], reverse=True)
        knn_rank = {fp: pos for pos, fp in enumerate(knn_order)}
        worst = len(knn_order)
        return {
            idx: knn_rank.get(row[1], worst + idx) for idx, row in enumerate(rows)
        }
    cosine_order = sorted(scored, key=lambda t: t[2], reverse=True)
    return {idx: i for i, (idx, _r, _s) in enumerate(cosine_order)}


def _hybrid_search_results(conn, rows, query: str, limit: int, offset: int) -> list[dict]:
    """RRF-fuse BM25 rank with vector cosine over an FTS5 candidate page.

    ``rows`` are the FTS5 result rows (already ordered by BM25 `rank`) with the
    shape (doc_type, file_path, title, sector, embedding, rank, snippet). The
    page was fetched as the top ``limit+offset`` candidates so a global
    re-rank + slice preserves pagination semantics.

    Fusion is Reciprocal Rank Fusion (RRF): each doc's position in the BM25
    ranking and in the cosine ranking contribute ``1/(k + position)``, summed.
    This avoids score-calibration between two incommensurable scorers. Docs
    with a missing/invalid embedding get cosine-position 0 (ranked worst) but
    still contribute their BM25 score — a degraded embedding must not drop a
    strong lexical match off the page.

    A1 (sqlite-vec, 2026-08-17): the cosine leg comes from a single KNN
    query over the vec0 mirror table (helpers/core.vec_search) instead of
    a Python loop that JSON-decodes and dot-products every page row.
    ``k=None`` sizes the KNN to the whole corpus so every page doc keeps
    its EXACT similarity value (float32 quantization differs from the
    Python cosine by <1e-7). One deliberate semantic refinement: the
    cosine leg's RANK is the doc's GLOBAL cosine rank, not its rank within
    the BM25 page — a doc's vector similarity no longer depends on which
    docs BM25 happened to surface. Benchmarked on the live 1,227-doc
    index: ~7ms whole-corpus KNN vs ~0.7ms for the page-bound Python loop;
    the ~7ms was accepted by the user (2026-08-17) for the global-rank
    semantics and the future-ready vector infrastructure (real embeddings,
    KNN-as-candidate generation). Any KNN-path failure (package missing,
    extension won't load, table absent) falls back to the original
    page-local Python cosine — hybrid must degrade, never 500.

    The query is embedded on the SAME side of the asymmetry as the index was:
    rebuild_note_search.query_embedder() resolves to embed_query of the local
    bge-small model when available (the index used embed_document of it), and
    to the pseudo-embedder otherwise. A vector-space mismatch — index rebuilt
    with a different model than the query side now resolves to — is detected
    via the stored dims and DEGRADES to BM25-only rather than computing
    zip-truncated garbage cosine over mismatched dimensions.
    """
    try:
        from helpers.maintenance.rebuild_note_search import (
            query_embedder,
            stored_embed_dims,
        )

        embed_q, _dims = query_embedder()
        q_vec = embed_q(query)
        idx_dims = stored_embed_dims(conn)
        if idx_dims is not None and idx_dims != len(q_vec):
            q_vec = None  # index/query vector spaces differ -> BM25 only
    except Exception:  # noqa: S110  # embedding unavailable -> fall back to BM25
        q_vec = None

    # A1: whole-corpus KNN first (k=None -> exact per-doc similarities);
    # None result = unavailable -> Python cosine loop below.
    knn: dict[str, float] | None = None
    if q_vec is not None:
        try:
            from helpers.core.vec_search import knn_similarities

            knn = knn_similarities(conn, q_vec, None, len(q_vec))
        except Exception:  # noqa: S110  # KNN unavailable -> Python cosine below
            knn = None

    scored = _scored_rows(rows, q_vec, knn)
    cos_pos = _cosine_positions(rows, knn, scored)

    k = 60  # standard RRF constant
    fused = []
    for idx, row, sim in scored:
        rrf = (1.0 / (k + idx + 1)) + (1.0 / (k + cos_pos[idx] + 1))
        fused.append((rrf, row, sim))
    fused.sort(key=lambda t: t[0], reverse=True)

    results = []
    for _rrf, row, sim in fused[offset: offset + limit]:
        results.append({
            "doc_type": row[0],
            "file_path": row[1],
            "title": row[2],
            "sector": row[3],
            "snippet": row[6],
            "similarity": round(sim, 6),
        })
    return results


@app.route("/api/search")
def api_search():
    """Free-text search across ALL findata/ markdowns via the note_search FTS5 index.

    Unlike /api/entities (which matches entity name + sector tag only), this
    searches the full *body* text of company notes, sector notes, super-sector
    notes, AND the newsletter corpora (The_Chatter, Points_And_Figures,
    The_PlotLines). It finds content the name/tag search structurally cannot —
    e.g. "shrimp feed" resolves to Avanti Feeds + a Points & Figures edition.

    Query params:
        q     (required) free-text query; FTS5 MATCH syntax (quoted phrases ok).
        type  (optional) filter to one doc_type: company | sector |
              super_sector | chatter | points_and_figures | plotlines.
        limit (default 20), offset (default 0).
        hybrid (optional) "1"/"true" — RRF-fuse the BM25 ranking with vector
              cosine similarity against each row's stored embedding. The query
              is embedded with the same pseudo-embedder the rebuild used by
              default, so out of the box this re-ranks by *lexical* proximity
              of the hash vectors; with real embeddings (embed_fn injected into
              rebuild_note_search) it becomes genuinely semantic. Degrades to
              pure FTS ranking when the embedding column is absent.

    Returns a polymorphic hit list (results include non-entity newsletters, so
    the key is `results`, not `entities`). Each hit carries a snippet() with
    <mark>-highlighted matches and a `similarity` (null unless hybrid=true).

    Errors: 400 on empty/ malformed q; 503 if the FTS index hasn't been built.
    """
    q = request.args.get("q", "").strip()
    doc_type = request.args.get("type", "").strip()
    hybrid = request.args.get("hybrid", "").strip().lower() in ("1", "true", "yes", "on")
    try:
        limit = int(request.args.get("limit", 20))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit/offset must be integers"}), 400

    if not q:
        return jsonify({"error": "missing required param 'q'"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Guard: the note_search table may not exist yet (DB predates the FTS
        # feature, or rebuild hasn't run). Surface a 503 rather than a 500.
        exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_search'"
        ).fetchone()
        if not exists:
            return jsonify({
                "error": "search index not built; run "
                         "helpers/maintenance/rebuild_note_search.py",
            }), 503

        if hybrid:
            # Hybrid needs the embedding column. A pre-embedding schema (old
            # rebuild, or a DB built before this feature) lacks it — downgrade
            # to plain FTS ranking rather than 500 on the column reference.
            has_embedding = cursor.execute(
                "SELECT 1 FROM pragma_table_info('note_search') "
                "WHERE name = 'embedding'"
            ).fetchone()
            if not has_embedding:
                app.logger.info(
                    "search hybrid requested but note_search lacks embedding "
                    "column; degrading to FTS-only"
                )
                hybrid = False

        # Build the WHERE. FTS5 MATCH against the whole-table index; an optional
        # SQL-level AND on doc_type narrows to one corpus (simpler + as fast as
        # the column-filter MATCH syntax, and avoids quoting pitfalls).
        where = ["note_search MATCH ?"]
        params: list = [q]
        if doc_type:
            where.append("doc_type = ?")
            params.append(doc_type)
        where_clause = " AND ".join(where)

        # FTS5 MATCH raises on malformed queries (stray AND/OR, unbalanced
        # quotes). Catch and return a 400 so a bad query never 500s.
        try:
            # Hybrid mode pulls the embedding column + FTS rank so we can
            # RRF-fuse cosine with BM25 in Python. Plain mode stays a single
            # SQL round-trip (unchanged behaviour).
            select_cols = (
                "doc_type, file_path, title, sector, embedding, rank, "
                "snippet(note_search, 4, '<mark>', '</mark>', '…', 12)"
                if hybrid else
                "doc_type, file_path, title, sector, "
                "snippet(note_search, 4, '<mark>', '</mark>', '…', 12)"
            )
            cursor.execute(
                f"SELECT {select_cols} "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                f"FROM note_search WHERE {where_clause} "
                f"ORDER BY rank LIMIT ? OFFSET ?",
                params + ([limit + offset, 0] if hybrid else [limit, offset]),
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"SELECT COUNT(*) FROM note_search WHERE {where_clause}", params  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            )
            total_count = cursor.fetchone()[0]
        except Exception as exc:  # sqlite3.OperationalError on bad MATCH syntax
            app.logger.info("search MATCH error for q=%r: %s", q, exc)
            return jsonify({
                "error": f"invalid query syntax: {exc}",
            }), 400

        if hybrid and rows:
            results = _hybrid_search_results(conn, rows, q, limit, offset)
        else:
            results = [
                {
                    "doc_type": row[0],
                    "file_path": row[1],
                    "title": row[2],
                    "sector": row[3],
                    "snippet": row[4],
                    "similarity": None,
                }
                for row in rows
            ]
        return jsonify(
            {
                "results": results,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
            }
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# /api/docs — browse + search the design/improvement docs under doc/           #
# --------------------------------------------------------------------------- #
# The docs corpus lives on disk (markdown + plain-text), NOT in the research
# DB, so the catalog/content routes read the filesystem directly. Search is
# served from the doc_search sidecar index (memory/doc_search.db — FTS5 +
# per-section embeddings, helpers/maintenance/rebuild_doc_search.py) when it
# is present and fresh, degrading to the #107 linear scan otherwise; the
# sidecar is gitignored and structurally never touches research.db or the
# published snapshots (doc/local/ privacy).

_DOC_ROOT = Path(__file__).resolve().parent / "doc"
_DOC_EXTS = {".md", ".txt"}


def _iter_doc_files():
    """Yield (rel_path, full_path) for every browseable doc under doc/.

    Uses the project-root doc/ directory. Directory symlinks are followed
    (helpers.core.fs_walk — cycle-safe), so a git worktree's gitignored
    doc/local symlink lists identically to main's real directory, keeping
    the #107 walk+sort contract with rebuild_doc_search._iter_doc_files
    (both share that walker). Non-doc extensions and broken symlinks are
    skipped. Returns a stable (sorted) listing.
    """
    from helpers.core.fs_walk import iter_tree_files

    if not _DOC_ROOT.is_dir():
        return
    # Sort by POSIX string, NOT by Path: Path comparison is tuple-of-parts,
    # so doc/okf/frontmatter_keys.md ('schema' < 'schema.md', prefix rule)
    # would sort BEFORE doc/design/db_schema.md — contradicting the plain-string order
    # clients (and the API's own "sorted by path" contract) expect.
    for rel in sorted(
        p.relative_to(_DOC_ROOT).as_posix()
        for p in iter_tree_files(_DOC_ROOT)
        if p.suffix.lower() in _DOC_EXTS
    ):
        full = _DOC_ROOT / rel
        yield rel, full


def _doc_title(rel_path: str, full_path: Path) -> str:
    """Derive a human-readable title for a doc file.

    Priority: first Markdown heading line (`# ...`) → the filename stem
    (underscores → spaces). Plain-text files usually have no headings, so the
    filename stem is the sensible fallback.
    """
    try:
        for line in full_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
            # First non-empty line of a .txt file is often the title.
            if stripped and full_path.suffix.lower() == ".txt":
                return stripped[:120]
            if stripped:
                break
    except OSError:
        pass
    return Path(rel_path).stem.replace("_", " ")


def _resolve_doc_path(rel_path: str) -> Path | None:
    """Resolve a doc path to a safe absolute path inside doc/.

    Accepts BOTH forms: doc/-relative ("procedures/embeddings.md", the
    #107 catalog convention) and repo-rooted ("doc/procedures/
    embeddings.md", the doc_search index/CLI convention) — a leading
    `<doc-root-name>/` segment is stripped before resolution.

    Guards against path traversal (`../`, absolute paths, symlink escapes):
    the resolved path must stay within _DOC_ROOT and be a real file.
    """
    if not rel_path or "\x00" in rel_path:
        return None
    strip = _DOC_ROOT.name + "/"
    if rel_path.startswith(strip):
        rel_path = rel_path[len(strip):]
    candidate = (_DOC_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(_DOC_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_file() or not candidate.exists():
        return None
    return candidate


@app.route("/api/docs")
def api_docs():
    """Catalog of the design/improvement docs under doc/.

    Query params:
        q (optional) — substring filter on the path; case-insensitive.

    Returns: {"docs": [{"path", "name", "section", "title", "size_bytes",
    "mtime"}]} sorted by path. `path` is REPO-ROOTED (e.g.
    "doc/improvements/completed.md") so it resolves from the repo root —
    same convention as the doc_search index and CLI. `section` stays
    subdir-relative-to-doc/ ("" for top-level files).
    """
    q = request.args.get("q", "").strip().lower()
    docs = []
    for rel_path, full in _iter_doc_files():
        rooted = f"{_DOC_ROOT.name}/{rel_path}"
        if q and q not in rooted.lower():
            continue
        try:
            st = full.stat()
        except OSError:
            continue
        docs.append({
            "path": rooted,
            "name": full.name,
            "section": str(Path(rel_path).parent) if Path(rel_path).parent != Path(".") else "",
            "title": _doc_title(rel_path, full),
            "size_bytes": st.st_size,
            "mtime": int(st.st_mtime),
        })
    return jsonify({"docs": docs})


@app.route("/api/docs/content")
def api_docs_content():
    """Raw content of one doc (markdown or plain text).

    Query params:
        path (required) — doc/-relative ("procedures/embeddings.md") or
        repo-rooted ("doc/procedures/embeddings.md"); both resolve.

    Returns: {"path", "name", "section", "title", "content", "size_bytes",
    "mtime"} — `path` echoed in the canonical repo-rooted form. 404 on
    unknown or out-of-tree paths.

    The body is served raw and rendered client-side with marked.js (the
    frontend already loads it) so the browse view shows faithful formatting.
    """
    rel_path = request.args.get("path", "").strip()
    full = _resolve_doc_path(rel_path)
    if full is None:
        return jsonify({"error": f"no such doc: {rel_path!r}"}), 404
    try:
        content = full.read_text(encoding="utf-8")
        st = full.stat()
    except OSError:
        return jsonify({"error": "unable to read doc"}), 500
    doc_rel = full.relative_to(_DOC_ROOT.resolve())
    return jsonify({
        "path": f"{_DOC_ROOT.name}/{doc_rel.as_posix()}",
        "name": full.name,
        "section": str(doc_rel.parent) if str(doc_rel.parent) != "." else "",
        "title": _doc_title(doc_rel.as_posix(), full),
        "content": content,
        "size_bytes": st.st_size,
        "mtime": int(st.st_mtime),
    })


def _snippet(text: str, q: str, radius: int = 140) -> str:
    """First-match context window around the query, <mark>-wrapped.

    Mirrors the FTS5 snippet convention (literal `<mark>...</mark>` tags) so
    the frontend can reuse its existing highlightSnippet() escaping logic.
    Multi-word queries anchor the window on the first word that actually
    appears in the text.
    """
    words = [w for w in q.split() if w]
    anchor = next((w for w in words if w.lower() in text.lower()), None)
    if anchor is None:
        return text[: radius * 2].replace("\n", " ").strip()
    pos = text.lower().find(anchor.lower())
    start = max(0, pos - radius)
    end = min(len(text), pos + len(anchor) + radius)
    snippet = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    # Wrap the literal anchor substring in <mark> (first occurrence),
    # case-insensitively so "DuckDB" still matches a "duckdb" query.
    import re as _re

    snippet = _re.sub(
        _re.escape(anchor),
        lambda m: f"<mark>{m.group(0)}</mark>",
        snippet,
        count=1,
        flags=_re.IGNORECASE,
    )
    return snippet


def _docs_index_search(query: str, limit: int, offset: int, hybrid_on: bool):
    """Serve /api/docs/search from the doc_search sidecar; None -> scan.

    Read-only: opens its own short-lived connection to memory/doc_search.db
    (module attrs are read at call time so tests can retarget them). Request
    handlers never write — a missing, stale, or corrupt index degrades to
    the #107 filesystem scan instead of failing.
    """
    try:
        from helpers.maintenance import rebuild_doc_search as rds

        if not Path(rds.DOC_DB).exists():
            return None
        conn = rds.connect_doc_db()
    except Exception:  # noqa: S110  # no sidecar -> scan fallback
        return None
    try:
        if not rds.doc_index_ready(conn) or rds.doc_index_stale(conn):
            return None
        return rds.search_docs(conn, query, limit=limit, offset=offset,
                               hybrid=hybrid_on)
    except Exception:  # noqa: S110  # corrupt index must never 500 the search
        return None
    finally:
        conn.close()


@app.route("/api/docs/search")
def api_docs_search():  # noqa: C901
    """Search the doc/ corpus — hybrid BM25 + cosine over the doc_search
    sidecar index (proposal: doc/improvements/proposals/
    doc_search_embeddings.md) when present and fresh, degrading to the #107
    filesystem scan otherwise.

    Query params:
        q (required) — free-text query. Tokens are FTS-quoted on the index
          path, so punctuation can never produce a syntax error; the scan
          path is a case-insensitive substring walk with naive word scoring.
        limit (default 25) — max results (clamped 1..100).
        hybrid (default on; hybrid=0 forces the lexical leg only — the
          eval BM25 baseline).

    Returns: {"query", "mode", "stale", "results": [{"path", "name",
    "section", "title", "section_title", "anchor", "snippet", "score"}]}
    sorted by descending score. mode is "hybrid" | "bm25" | "scan"; stale=
    true marks an index that exists but no longer matches doc/ (served by
    the scan). Index-path results are section-level with at most 2 chunks
    per file per page (diversification): each row deep-links by its anchor
    line. Tokens are OR-joined — question-shaped queries must not require
    every token to co-occur in one chunk.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing required param 'q'"}), 400
    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    limit = max(1, min(limit, 100))
    hybrid_on = request.args.get("hybrid", "1") not in ("0", "false")

    indexed = _docs_index_search(q, limit, 0, hybrid_on)
    if indexed is not None:
        return jsonify({
            "query": q,
            "mode": indexed["mode"],
            "stale": False,
            "results": indexed["results"][:limit],
        })

    # Fallback: the #107 filesystem scan. stale=true when an index exists
    # but no longer matches doc/ (probe best-effort — this path must not 500).
    stale = False
    try:
        from helpers.maintenance import rebuild_doc_search as rds

        if Path(rds.DOC_DB).exists():
            probe = rds.connect_doc_db()
            try:
                stale = rds.doc_index_ready(probe) and rds.doc_index_stale(probe)
            finally:
                probe.close()
    except Exception:  # noqa: S110  # probe failure must not 500 the search
        stale = False

    words = [w.lower() for w in q.split() if w]
    ranked: list[tuple[int, str, dict[str, str | int | None]]] = []
    for rel_path, full in _iter_doc_files():
        try:
            content = full.read_text(encoding="utf-8")
        except OSError:
            continue
        low = content.lower()
        title = _doc_title(rel_path, full)
        title_low = title.lower()
        score = 0
        for w in words:
            score += 3 * low.count(f" {w} ")          # word match in body
            score += 2 * low.count(f" {w}")            # word-boundary substring
            score += 5 * title_low.count(w)            # in the title
            if w in rel_path.lower():
                score += 4                              # in the path
        if score <= 0:
            continue
        section = str(Path(rel_path).parent) if Path(rel_path).parent != Path(".") else ""
        doc_item = {
            "path": f"{_DOC_ROOT.name}/{rel_path}",
            "name": full.name,
            "section": section,
            "title": title,
            "section_title": "",
            "anchor": None,
            "snippet": _snippet(content, q),
            "score": score,
            "similarity": None,
        }
        # Carry the rank key (score, title_lower) as typed locals in the tuple
        # — dict-indexing (d["score"]) widens to `int | str` unions under ty.
        ranked.append((score, title.lower(), doc_item))
    # Sort by (desc score, asc title).
    ranked.sort(key=lambda t: (-t[0], t[1]))
    docs_out = [d for _score, _title, d in ranked[:limit]]
    return jsonify({"query": q, "mode": "scan", "stale": stale, "results": docs_out})


@app.route("/api/entity/<path:entity_path>")
def api_entity_detail(entity_path):
    """API endpoint to get entity details including markdown content"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Try to find entity by file_path or name. Bundle Q1: rewritten from a
    # single ``WHERE file_path = ? OR name = ?`` (which defeated both indexes
    # and did a full SCAN) to a UNION of two indexed SELECTs. The file_path
    # branch uses idx_entities_file_path; the name branch uses the PK.
    cursor.execute(
        """
        SELECT name, entity_type, sector_classification, file_path
        FROM entities WHERE file_path = ?
        UNION
        SELECT name, entity_type, sector_classification, file_path
        FROM entities WHERE name = ?
        LIMIT 1
    """,
        (entity_path, entity_path),
    )

    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Entity not found"}), 404

    # Tags now live in the normalized entity_tags table.
    tags = [
        r[0]
        for r in conn.execute(
            "SELECT tag FROM entity_tags WHERE entity_name = ? ORDER BY tag",
            (row[0],),
        ).fetchall()
    ]

    # Bundle C2: market_cap derived from the market_cap/* tag (column dropped).
    mc_tag = next((t for t in tags if t.startswith("market_cap/")), None)
    market_cap = mc_tag.split("/", 1)[1] if mc_tag else None

    entity = {
        "name": row[0],
        "entity_type": row[1],
        "sector_classification": row[2],
        "market_cap": market_cap,
        "enhanced_tags": tags,
        "file_path": row[3],
    }

    conn.close()

    # Read markdown content
    try:
        project_root = Path(__file__).parent.resolve()
        full_path = (project_root / entity["file_path"]).resolve()
        # Defense in depth: file_path comes from the (trusted) DB, but verify
        # the resolved path stays under the project root before opening it.
        try:
            if not full_path.is_relative_to(project_root):
                return jsonify({"error": "Invalid file path"}), 400
        except (OSError, ValueError):
            return jsonify({"error": "Invalid file path"}), 400
        if full_path.exists():
            with open(full_path, encoding="utf-8") as f:
                content = f.read()

            frontmatter, markdown_content = parse_yaml_frontmatter(content)
            entity["frontmatter"] = frontmatter
            entity["content"] = markdown_content
            entity["raw_content"] = content

            # Update enhanced_tags to include tags from YAML frontmatter as well
            yaml_tags = []
            if "tags" in frontmatter and isinstance(frontmatter["tags"], list):
                yaml_tags = frontmatter["tags"]

            # Combine database tags with YAML frontmatter tags, avoiding duplicates
            combined_tags = (
                entity["enhanced_tags"].copy() if entity["enhanced_tags"] else []
            )
            for tag in yaml_tags:
                if tag not in combined_tags:
                    combined_tags.append(tag)

            entity["enhanced_tags"] = combined_tags
        else:
            entity["content"] = "File not found"
            entity["raw_content"] = "File not found"
    except Exception as e:
        entity["content"] = f"Error reading file: {str(e)}"
        entity["raw_content"] = f"Error reading file: {str(e)}"

    return jsonify(entity)


@app.route("/api/sectors")
def api_sectors():
    """API endpoint to get all sectors"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT sector_classification
        FROM entities
        WHERE sector_classification IS NOT NULL
        AND entity_type = 'company'
        ORDER BY sector_classification
    """)

    sectors = [row[0] for row in cursor.fetchall()]

    # Also get sector entities
    cursor.execute("""
        SELECT name, file_path
        FROM entities
        WHERE entity_type = 'sector'
        ORDER BY name
    """)

    sector_entities = []
    global _SECTOR_ENTITY_CACHE
    with _SECTOR_ENTITY_LOCK:
        cursor.execute("""
            SELECT name, file_path
            FROM entities
            WHERE entity_type = 'sector'
            ORDER BY name
        """)
        sector_rows = cursor.fetchall()
        # Content signature: (mtime_ns, size) per existing file. Computing it
        # is cheap (~1ms for ~42 files) and any on-disk edit auto-invalidates.
        sig = []
        for row in sector_rows:
            full_path = Path(__file__).parent / row[1]
            if full_path.exists():
                st = full_path.stat()
                sig.append((st.st_mtime_ns, st.st_size))
        sig = tuple(sig)
        if _SECTOR_ENTITY_CACHE is not None and _SECTOR_ENTITY_CACHE[0] == sig:
            sector_entities = _SECTOR_ENTITY_CACHE[1]
        else:
            for row in sector_rows:
                try:
                    full_path = Path(__file__).parent / row[1]
                    if full_path.exists():
                        with open(full_path, encoding="utf-8") as f:
                            content = f.read()
                        frontmatter, markdown_content = parse_yaml_frontmatter(content)
                        sector_entities.append(
                            {
                                "name": row[0],
                                "file_path": row[1],
                                "frontmatter": frontmatter,
                                "content": markdown_content,
                            }
                        )
                except Exception:
                    sector_entities.append(
                        {
                            "name": row[0],
                            "file_path": row[1],
                            "frontmatter": {},
                            "content": "Error reading file",
                        }
                    )
            _SECTOR_ENTITY_CACHE = (sig, sector_entities)

    conn.close()

    # Bundle M4: super-sector hierarchy. Returns the 9 super-sector groupings
    # with their child sectors, sourced from the belongs_to edges. Additive —
    # the existing `classifications` (flat sector list) and `sector_entities`
    # keys are unchanged for backward compatibility.
    super_sectors = _super_sector_hierarchy()

    return jsonify({
        "classifications": sectors,
        "sector_entities": sector_entities,
        "super_sectors": super_sectors,
    })


@app.route("/api/stats")
def api_stats():
    """API endpoint to get database statistics"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get entity counts by type
    cursor.execute("SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type")
    entity_counts = dict(cursor.fetchall())

    # Get sector counts
    cursor.execute("""
        SELECT sector_classification, COUNT(*)
        FROM entities
        WHERE sector_classification IS NOT NULL
        AND entity_type = 'company'
        GROUP BY sector_classification
        ORDER BY COUNT(*) DESC
        LIMIT 10
    """)
    top_sectors = dict(cursor.fetchall())

    # Get market cap counts. Bundle C2: the entities.market_cap column was
    # dropped; the market_cap/* tag is the source of truth. Strip the tag
    # prefix and group by the resulting value.
    cursor.execute("""
        SELECT cap, COUNT(*) FROM (
          SELECT e.name,
                 substr(MIN(t.tag), length('market_cap/')+1) AS cap
          FROM entities e
          JOIN entity_tags t ON t.entity_name = e.name AND t.tag LIKE 'market_cap/%'
          WHERE e.entity_type = 'company'
          GROUP BY e.name
        ) GROUP BY cap
    """)
    market_cap_counts = dict(cursor.fetchall())

    conn.close()

    return jsonify(
        {
            "entity_counts": entity_counts,
            "top_sectors": top_sectors,
            "market_cap_counts": market_cap_counts,
            "total_entities": sum(entity_counts.values()),
        }
    )


# --------------------------------------------------------------------------- #
# Graph API routes (DuckDB, design §9 / §12 Phase 3 item 2)                    #
# --------------------------------------------------------------------------- #
# These wrap helpers/graph/query.py for browser consumption. The connection is
# held in get_graph_connection() and reused across requests (design §9). All
# <path:name> params are resolved case-insensitively to the canonical entity
# name via _resolve_entity_or_404 before being handed to the graph layer.


@app.after_request
def _graph_cache_headers(response):
    """Add ETag + Cache-Control to GET /api/graph/* responses (Bundle C4).

    The DuckDB cache is immutable between explicit POST /api/graph/refresh
    calls, so the architecture is HTTP-cache-friendly by design. An ETag
    derived from _build_meta.built_at lets browsers absorb repeat traffic as
    a free 304; refresh bumps built_at, invalidating every cached response.

    Policy: ``Cache-Control: no-cache`` — the browser MUST revalidate every
    request (sends If-None-Match), but the server returns 304 when the ETag
    matches. This favours correctness over stale-serving: data can change
    only via refresh, and a 304 is as cheap as a stale hit would be.

    Scoped to /api/graph/* GET 200s only. Non-graph routes, errors, and
    write methods are passed through untouched. If the ETag can't be derived
    (cold cache), the response gets Cache-Control but no ETag (worst case:
    no 304s, identical to pre-C4 behaviour).
    """
    if (request.method == "GET"
            and request.path.startswith("/api/graph/")
            and response.status_code == 200):
        response.headers["Cache-Control"] = "no-cache"
        etag = _graph_build_etag()
        if etag is not None:
            response.headers["ETag"] = etag
            # If-None-Match match check. Our ETag is weak (W/"..."); the bare
            # token between the quotes is what contains_weak() compares.
            bare = etag.split('"', 2)[1] if '"' in etag else etag
            if request.if_none_match.contains_weak(bare):
                # Strip the body; 304 must be empty per RFC 7232 §4.1.
                empty = make_response("", 304)
                empty.headers["ETag"] = etag
                empty.headers["Cache-Control"] = "no-cache"
                return empty
    return response


def _entity_file_path(name: str) -> str | None:
    """Return file_path for an entity name, or None. Used so the UI can link
    from a graph node straight to /entity/<path>."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT file_path FROM entities WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    return row["file_path"] if row else None


@app.route("/api/graph/peers/<path:name>")
def api_graph_peers(name: str):
    """Companies that compete with `name` (competes_with, symmetric)."""
    company = _resolve_entity_or_404(name)
    try:
        from helpers.graph.query import peers
        result = peers(get_graph_connection(), company)
    except Exception as e:
        app.logger.exception("graph peers failed for %r", company)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({"company": company, "peers": result})


@app.route("/api/graph/neighbors/<path:name>")
def api_graph_neighbors(name: str):
    """Ego-network bundle for `name`. Branches on entity_type:

      - company → sector, peers, JV partners, group siblings, acquisitions,
        parent (subsidiary_of), suppliers, customers.
      - sector  → member companies (with optional market_cap filter), size,
        and market-cap distribution. The UI renders the sector as the focal
        node with one edge per member.

    Query params:
      - `as_of` (optional YYYY / YYYY-MM / YYYY-MM-DD): filters out edges
        that weren't valid at the given date. NULL valid_from is treated
        as always-valid. Today only `acquired` edges carry valid_from;
        structural edges survive.
      - `market_cap` (sector focal only): narrows members.

    One round trip gives the UI everything it needs to render the focal node
    plus its 1-hop neighbourhood."""
    canonical, entity_type = _resolve_entity_with_type_or_404(name)
    as_of = _parse_as_of_or_400()

    if entity_type == "sector":
        return _sector_neighbors_bundle(canonical)
    # Bundle M4: super_sector → its child sectors; sub_sector → its parent
    # sector. Falls through to the company bundle for any other type.
    if entity_type == "super_sector":
        return _super_sector_neighbors_bundle(canonical)
    if entity_type == "sub_sector":
        return _sub_sector_neighbors_bundle(canonical)
    # D4: theme → its exposed member companies (cross-sector). Themes cut
    # across sectors, so membership is via the exposed_to edge, not part_of.
    if entity_type == "theme":
        return _theme_neighbors_bundle(canonical)
    return _company_neighbors_bundle(canonical, as_of=as_of)


def _company_neighbors_bundle(company: str, as_of: str | None = None):
    """Build the ego-network bundle for a company focal node.

    `as_of` (ISO date or None) threads the temporal filter into every
    wrapper call.

    Uses the coalesced single-round-trip `company_neighbors_bundle` (Bundle C1)
    instead of seven serial GRAPH_TABLE queries: ~9ms vs ~45ms on the live
    graph. The response shape is unchanged.
    """
    try:
        from helpers.graph.query import company_neighbors_bundle
        con = get_graph_connection()
        bundle = company_neighbors_bundle(con, company, as_of=as_of)
    except Exception as e:
        app.logger.exception("graph neighbors failed for %r", company)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "entity_type": "company",
        "company": company,
        "as_of": as_of,
        "file_path": _entity_file_path(company),
        **bundle,
    })


def _sector_neighbors_bundle(sector: str):
    """Build the ego-network bundle for a sector focal node.

    Returns the sector's member companies plus aggregate stats. Caps the
    members list at a sensible size to keep the response payload manageable
    for large sectors (Automotive has 86 members; rendering all of them as
    a cytoscape ego-network works but feels cluttered — the UI can opt to
    show a subset and link to /api/graph/sector/<name> for the full list).

    Bundle K2: members + market_cap come from ONE DuckDB GRAPH_TABLE
    (sector_members_with_market_cap), not two hops. The market-cap bucketize
    is done in Python from that single result set, preserving the
    "sum(buckets) == member_count" invariant the old cross-DB hop documented
    (DuckDB graph edges vs the SQLite column can drift — Bundle E5).
    """
    market_cap = request.args.get("market_cap") or None
    try:
        from helpers.graph.query import sector_members_with_market_cap
        con = get_graph_connection()
        pairs = sector_members_with_market_cap(con, sector, market_cap=market_cap)
    except Exception as e:
        app.logger.exception("graph sector-members failed for %r", sector)
        return jsonify({"error": f"graph query failed: {e}"}), 500

    members = [name for name, _ in pairs]
    # Bucketize market_cap from the same DuckDB result set (one source, one
    # trip). None/empty -> 'unknown' to match the old SQLite GROUP BY shape.
    market_cap_counts: dict[str, int] = {}
    for _, cap in pairs:
        key = cap or "unknown"
        market_cap_counts[key] = market_cap_counts.get(key, 0) + 1

    return jsonify({
        "entity_type": "sector",
        "sector": sector,
        "file_path": _entity_file_path(sector),
        "members": members,
        "member_count": len(members),
        "market_cap_counts": market_cap_counts,
    })


def _theme_neighbors_bundle(theme: str):
    """Ego-network bundle for a cross-sector theme focal node (D4).

    Returns the theme's exposed member companies. Themes cut across the GICS
    hierarchy (China+1 = Electronics + EMS + Pharma API + Textiles), so
    membership comes from the exposed_to edge, not part_of. Unlike a sector,
    a theme has no market_cap dimension — members are simply companies whose
    notes carry the theme's signal. Use the /api/graph/sector/<name> shape for
    a sector; this is the cross-sector analogue.
    """
    try:
        from helpers.graph.query import theme_members
        con = get_graph_connection()
        members = theme_members(con, theme)
    except Exception as e:
        app.logger.exception("graph theme-members failed for %r", theme)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "entity_type": "theme",
        "theme": theme,
        "file_path": _entity_file_path(theme),
        "members": members,
        "member_count": len(members),
    })


def _super_sector_neighbors_bundle(super_sector: str):
    """Ego-network bundle for a super_sector focal node (Bundle M4).

    Returns the super-sector's child sectors (via the belongs_to hierarchy).
    No company members directly — those are reached 2 hops down (sector ->
    company); callers compose via /api/graph/sector/<name> per child.
    """
    try:
        from helpers.graph.query import sectors_in_super
        con = get_graph_connection()
        children = sectors_in_super(con, super_sector)
    except Exception as e:
        app.logger.exception(
            "graph super-sector-children failed for %r", super_sector
        )
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "entity_type": "super_sector",
        "super_sector": super_sector,
        "file_path": _entity_file_path(super_sector),
        "sectors": children,
        "sector_count": len(children),
    })


def _sub_sector_neighbors_bundle(sub_sector: str):
    """Ego-network bundle for a sub_sector focal node (Bundle M4).

    Returns the parent sector this sub-category belongs to. A sub_sector is
    a facet within a sector (e.g. Metals -> "Iron and Steel"), so its only
    hierarchy edge points upward to the sector.
    """
    try:
        # belongs_to for a sub_sector points to its parent SECTOR (not a
        # super_sector). Read the SQLite edge directly — the DuckDB
        # super_sector_of helper expects a sector source, but a sub_sector's
        # belongs_to target is a sector, so we read it explicitly here.
        conn = get_db_connection()
        parent = conn.execute(
            "SELECT target FROM graph_edges "
            "WHERE source=? AND edge_type='belongs_to' LIMIT 1",
            (sub_sector,)
        ).fetchone()
        parent_name = parent[0] if parent else None
        conn.close()
    except Exception as e:
        app.logger.exception(
            "graph sub-sector-parent failed for %r", sub_sector
        )
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "entity_type": "sub_sector",
        "sub_sector": sub_sector,
        "parent_sector": parent_name,
    })


@app.route("/api/events/<path:name>")
def api_events(name: str):
    """D7 — the timeline for one entity, ordered by event_date.

    Returns every event row for ``name`` (acquisitions, JVs, guidance,
    management changes) as a date-ordered list — the temporal spine the
    roadmap proposed. Dated events come first (oldest -> newest); undated
    events (many management changes / some guidance) sort last by id so they
    remain visible rather than silently dropped. Optional ``?event_type=``
    narrows to one type. Resolves the name case-insensitively against
    entities.name (works for any entity that has events, though events are
    company-scoped today)."""
    canonical, entity_type = _resolve_entity_with_type_or_404(name)
    event_type = request.args.get("event_type", "").strip() or None

    conn = get_db_connection()
    try:
        params: list = [canonical]
        type_clause = ""
        if event_type:
            type_clause = " AND event_type = ?"
            params.append(event_type)
        rows = conn.execute(
            f"""
            SELECT event_type, event_date, period, date_precision, magnitude,
                   counterparty, source_quote, as_of_edition
            FROM events
            WHERE entity = ?
            {type_clause}
            ORDER BY event_date IS NULL, event_date, id
            """,  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            params,
        ).fetchall()
    finally:
        conn.close()

    events = [{
        "event_type": r["event_type"],
        "event_date": r["event_date"],
        "period": r["period"],
        "date_precision": r["date_precision"],
        "magnitude": r["magnitude"],
        "counterparty": r["counterparty"],
        "source_quote": r["source_quote"],
        "as_of_edition": r["as_of_edition"],
    } for r in rows]
    return jsonify({
        "entity": canonical,
        "entity_type": entity_type,
        "file_path": _entity_file_path(canonical),
        "event_count": len(events),
        "events": events,
    })


@app.route("/api/graph/shortest")
def api_graph_shortest():
    """Shortest path between two entities across all edge types (undirected).

    Query params: a, b (both required), max_hops (optional, default 5),
    as_of (optional YYYY / YYYY-MM / YYYY-MM-DD — temporal filter)."""
    a = request.args.get("a", "").strip()
    b = request.args.get("b", "").strip()
    if not a or not b:
        return jsonify({"error": "both 'a' and 'b' query params are required"}), 400
    # Validate max_hops BEFORE entity resolution so a malformed value is
    # reported as 400 regardless of whether the entities exist.
    try:
        max_hops = int(request.args.get("max_hops", "5"))
    except ValueError:
        return jsonify({"error": "max_hops must be an integer"}), 400
    # Cap at the graph diameter (8, sql_capability_unlocks B3): with the BFS
    # implementation cost is linear per hop, so the cap's job is semantic —
    # beyond the diameter every pair is reachable and "shortest path" stops
    # discriminating. No existing caller passes an explicit max_hops.
    if max_hops < 1 or max_hops > 8:
        return jsonify({"error": "max_hops must be between 1 and 8 (the graph diameter)"}), 400
    as_of = _parse_as_of_or_400()
    a_canon = _resolve_entity_or_404(a)
    b_canon = _resolve_entity_or_404(b)
    try:
        from helpers.graph.query import shortest_path
        path = shortest_path(get_graph_connection(), a_canon, b_canon, max_hops,
                             as_of=as_of)
    except Exception as e:
        app.logger.exception("graph shortest failed a=%r b=%r", a_canon, b_canon)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    if path is None:
        return jsonify({
            "source": a_canon, "target": b_canon, "path": None, "hops": None,
            "as_of": as_of,
        })
    return jsonify({
        "source": a_canon,
        "target": b_canon,
        "path": [{"name": n, "hop": h} for n, h in path],
        "hops": path[-1][1] if path else 0,
        "as_of": as_of,
    })


@app.route("/api/graph/semantic/<path:name>")
def api_graph_semantic(name: str):
    """Semantic neighbours for `name` via vector embeddings (VSS).

    Finds companies with embeddings most similar to `name` using cosine
    similarity over the DuckDB `v_embeddings` table (populated by
    `helpers/graph/embeddings.py`). Mirrors the `semantic-neighbors` CLI
    command (deferred N5 item).

    Query params:
      - `k` (default 10): number of neighbours to return (clamped >= 0).
      - `metric` (default "cosine"): "cosine" or "ip".
      - `cross_sector` (default false): exclude same-sector companies.

    Returns 404 if the entity is unknown. Returns an empty `neighbors` list
    when embeddings aren't populated or the reference company has no
    embedding (matches the CLI's no-results behaviour, not an error).
    """
    company = _resolve_entity_or_404(name)
    try:
        k = int(request.args.get("k", "10"))
    except ValueError:
        return jsonify({"error": "k must be an integer"}), 400
    if k < 0:
        return jsonify({"error": "k must be non-negative"}), 400
    metric = request.args.get("metric", "cosine")
    if metric not in ("cosine", "ip"):
        return jsonify({"error": "metric must be 'cosine' or 'ip'"}), 400
    cross_sector = request.args.get("cross_sector", "false").lower() in ("1", "true", "yes")
    try:
        from helpers.graph.query import semantic_neighbors
        results = semantic_neighbors(
            get_graph_connection(), company, k=k,
            metric=metric, cross_sector=cross_sector,
        )
    except Exception as e:
        app.logger.exception("graph semantic failed for %r", company)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "company": company,
        "k": k,
        "metric": metric,
        "cross_sector": cross_sector,
        "neighbors": [
            {"name": n, "sector": s, "similarity": sim}
            for n, s, sim in results
        ],
    })


@app.route("/api/graph/similar/<path:note_path>")
def api_graph_similar(note_path: str):
    """Notes most similar to a note, by embedding cosine (v_note_embeddings).

    sql_capability_unlocks A4: read-only GET over the similar_notes
    wrapper. `note_path` is a findata-relative markdown path, e.g.
    `Companies/Agriculture/Avanti_Feeds.md`.

    Query params:
      - `k` (default 10): number of neighbours (clamped >= 0).
      - `doc_type` (optional): restrict candidates to one doc_type
        ('company', 'sector', 'chatter', ...).

    Returns 404 for an unknown/unembedded note (parity with the other
    graph routes); empty `neighbors` when the note is the only one.
    """
    try:
        k = int(request.args.get("k", "10"))
    except ValueError:
        return jsonify({"error": "k must be an integer"}), 400
    if k < 0:
        return jsonify({"error": "k must be non-negative"}), 400
    doc_type = request.args.get("doc_type") or None
    # Accept both a findata-relative and a repo-relative path form; the
    # wrapper keys on the exact note_search file_path ('findata/...').
    if not note_path.startswith("findata/"):
        note_path = f"findata/{note_path}"
    try:
        from helpers.graph.query import similar_notes
        results = similar_notes(
            get_graph_connection(), note_path, k=k, doc_type=doc_type
        )
    except Exception as e:
        app.logger.exception("graph similar failed for %r", note_path)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    if results is None:
        return jsonify({"error": f"no embedded note for path: {note_path}"}), 404
    return jsonify({
        "note": note_path,
        "k": k,
        "doc_type": doc_type,
        "neighbors": [
            {"file_path": p, "title": t, "similarity": sim}
            for p, t, sim in results
        ],
    })


@app.route("/api/graph/edition_companies")
def api_graph_edition_companies():
    """Companies most similar to an edition (newsletter) note.

    sql_capability_unlocks A4: read-only GET over the edition_companies
    wrapper — the edge-free reverse of cited_in. `edition` is resolved by
    exact title, full path, or filename stem (with or without .md).

    Query params:
      - `edition` (required): edition title or file stem.
      - `k` (default 10): number of companies (clamped >= 0).

    Returns 404 for an unresolvable edition.
    """
    edition = request.args.get("edition", "").strip()
    if not edition:
        return jsonify({"error": "'edition' query param is required"}), 400
    try:
        k = int(request.args.get("k", "10"))
    except ValueError:
        return jsonify({"error": "k must be an integer"}), 400
    if k < 0:
        return jsonify({"error": "k must be non-negative"}), 400
    try:
        from helpers.graph.query import edition_companies
        results = edition_companies(get_graph_connection(), edition, k=k)
    except Exception as e:
        app.logger.exception("graph edition_companies failed for %r", edition)
        return jsonify({"error": f"graph query failed: {e}"}), 500
    if results is None:
        return jsonify({"error": f"no edition note matches: {edition}"}), 404
    return jsonify({
        "edition": edition,
        "k": k,
        "companies": [
            {"file_path": p, "title": t, "similarity": sim}
            for p, t, sim in results
        ],
    })


@app.route("/api/graph/near-duplicates")
def api_graph_near_duplicates():
    """Near-duplicate note pairs above a cosine threshold (QA tripwire).

    graph_docs_ui_redesign S1: read-only GET over
    helpers.graph.query.near_duplicate_notes — the pairwise self-join over
    v_note_embeddings (~1s at ~1k docs). On-demand only: the UI must not
    prefetch this; it renders behind a loading state.

    Query params:
      - min_sim (default 0.9): cosine threshold, 0 < v <= 1.
      - doc_type (default 'company'): restricts BOTH sides of each pair.
      - limit (default 100, clamped 1-500): max pairs returned.

    200 with an empty `pairs` list when nothing clears the threshold or the
    embeddings table is absent/empty (wrapper degrades to []).
    """
    try:
        min_sim = float(request.args.get("min_sim", "0.9"))
    except ValueError:
        return jsonify({"error": "min_sim must be a number"}), 400
    if not 0.0 < min_sim <= 1.0:
        return jsonify({"error": "min_sim must be in (0, 1]"}), 400
    doc_type = request.args.get("doc_type", "").strip() or "company"
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    if limit < 1 or limit > 500:
        return jsonify({"error": "limit must be between 1 and 500"}), 400
    try:
        from helpers.graph.query import near_duplicate_notes
        results = near_duplicate_notes(
            get_graph_connection(), min_sim=min_sim, doc_type=doc_type,
            limit=limit,
        )
    except Exception as e:
        app.logger.exception("graph near-duplicates failed")
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "doc_type": doc_type,
        "min_sim": min_sim,
        "pairs": [
            {"path_a": pa, "path_b": pb, "title_a": ta, "title_b": tb,
             "similarity": sim}
            for pa, pb, ta, tb, sim in results
        ],
    })


@app.route("/api/graph/suggestions")
def api_graph_suggestions():
    """Link-prediction suggestions — read-only projection.

    graph_docs_ui_redesign S1: wraps helpers.graph.suggest_relations.
    suggest_relations() NEVER touches findata/_pending_relations.txt; the
    sidecar append stays CLI-only (all-writes-explicit doctrine).

    Query params:
      - method (default 'jaccard'): one of jaccard / adamic-adar /
        common-neighbors / pref-attach / resource-alloc.
      - top (default 25, clamped 1-100): max suggestions returned.
      - min_score (default 0.3): filter threshold, 0 <= v <= 1.
      - companies_only (default on): restrict both endpoints to companies.

    Returns 503 when the graph cache is cold/unavailable (the wrapper needs
    the materialised e_* tables).
    """
    method = request.args.get("method", "jaccard").strip().lower()
    if method not in ("jaccard", "adamic-adar", "common-neighbors",
                      "pref-attach", "resource-alloc"):
        return jsonify({
            "error": f"unknown method {method!r}",
            "valid_methods": ["jaccard", "adamic-adar", "common-neighbors",
                              "pref-attach", "resource-alloc"],
        }), 400
    try:
        top = int(request.args.get("top", "25"))
    except ValueError:
        return jsonify({"error": "top must be an integer"}), 400
    if top < 1 or top > 100:
        return jsonify({"error": "top must be between 1 and 100"}), 400
    try:
        min_score = float(request.args.get("min_score", "0.3"))
    except ValueError:
        return jsonify({"error": "min_score must be a number"}), 400
    if not 0.0 <= min_score <= 1.0:
        return jsonify({"error": "min_score must be in [0, 1]"}), 400
    companies_only = request.args.get("companies_only", "1").strip().lower() in (
        "1", "true", "yes", "on")
    try:
        from helpers.graph.suggest_relations import suggest_relations
        results = suggest_relations(
            get_graph_connection(), method=method, top=top,
            min_score=min_score, companies_only=companies_only,
        )
    except Exception as e:
        app.logger.exception("graph suggestions failed")
        return jsonify({"error": f"graph query failed: {e}"}), 500
    return jsonify({
        "method": method,
        "top": top,
        "suggestions": [
            {"source": s.source, "target": s.target, "score": s.score,
             "edition": s.edition}
            for s in results
        ],
    })


@app.route("/api/analytics/<name>")
def api_analytics(name: str):
    """One named analytics report over the git-tracked Parquet snapshot.

    graph_docs_ui_redesign S1: wraps helpers.graph.analytics.fetch.
    name is one of summary / edge-growth / sector-growth / top-entities /
    coverage / temporal; anything else → 404 (JSON parity with the
    /api/* handlers). temporal (C3) is composite: fetch returns
    list[Report] — serialized as {"reports": [...]} with a top-level
    "titles" convenience list; single reports keep the flat shape.

    Reads snapshots/parquet/ only (read-only); no ETag hook (outside
    /api/graph/*) — reports are cold-opened rarely.
    """
    from helpers.graph.analytics import REPORTS, fetch
    if name not in REPORTS:
        return jsonify({
            "error": f"unknown report {name!r}",
            "valid_reports": sorted(REPORTS),
        }), 404
    try:
        report = fetch(name)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        app.logger.exception("analytics report %r failed", name)
        return jsonify({"error": f"analytics query failed: {e}"}), 500
    if isinstance(report, list):
        return jsonify({
            "titles": [r.title for r in report],
            "reports": [{"title": r.title, "headers": r.headers,
                         "rows": r.rows, "note": r.note} for r in report],
        })
    return jsonify({
        "title": report.title,
        "headers": report.headers,
        "rows": report.rows,
        "note": report.note,
    })


@app.route("/api/graph/sector/<path:name>")
def api_graph_sector(name: str):
    """If `name` is a sector: return its member companies.
    If `name` is a company: return its sector.
    The path segment is resolved against entities.name case-insensitively."""
    # Try sector first — sector names are unambiguous and a sector name never
    # collides with a company name in this dataset.
    conn = get_db_connection()
    try:
        sector_row = conn.execute(
            "SELECT name FROM entities WHERE name = ? COLLATE NOCASE "
            "AND entity_type = 'sector'",
            (name,),
        ).fetchone()
        company_row = None if sector_row else conn.execute(
            "SELECT name FROM entities WHERE name = ? COLLATE NOCASE "
            "AND entity_type = 'company'",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if sector_row is None and company_row is None:
        abort(404, description=f"Entity not found: {name}")
    try:
        from helpers.graph.query import sector_members, sector_of
        con = get_graph_connection()
        if sector_row is not None:
            canonical = sector_row["name"]
            return jsonify({
                "sector": canonical,
                "members": sector_members(con, canonical),
            })
        if company_row is None:
            abort(404, description=f"Entity not found: {name}")
        canonical = company_row["name"]
        return jsonify({
            "company": canonical,
            "sector": sector_of(con, canonical),
        })
    except Exception as e:
        app.logger.exception("graph sector failed for %r", name)
        return jsonify({"error": f"graph query failed: {e}"}), 500


@app.route("/api/graph/stats")
def api_graph_stats():
    """Graph-wide stats mirroring helpers/graph/stats.py.

    All-SQLite (no DuckDB needed). Used by the Graph tab's header summary."""
    conn = get_db_connection()
    try:
        entity_counts = dict(conn.execute(
            "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type"
        ).fetchall())
        edge_rows = conn.execute(
            "SELECT edge_type, COUNT(*) FROM graph_edges GROUP BY edge_type "
            "ORDER BY COUNT(*) DESC"
        ).fetchall()
        edges_by_type = {row["edge_type"]: row[1] for row in edge_rows}
        total_edges = sum(edges_by_type.values())
        # Sector-size distribution across company-bearing sectors.
        sector_sizes = conn.execute(
            "SELECT sector_classification, COUNT(*) AS n FROM entities "
            "WHERE entity_type = 'company' AND sector_classification IS NOT NULL "
            "GROUP BY sector_classification ORDER BY n DESC"
        ).fetchall()
        sizes = [row["n"] for row in sector_sizes]
        # C6: single "graph health" CTE — one WITH block, one round-trip.
        # Named CTEs share table scans (companies, edges, tags) and add a
        # conflicting_market_cap counter that didn't exist before (companies
        # with >1 distinct market_cap tag — the latent A1/A2 trigger).
        row = conn.execute(
            """
            WITH
            mc_conflicts AS (
                SELECT COUNT(*) AS n FROM (
                    SELECT entity_name
                    FROM entity_tags
                    WHERE tag LIKE 'market_cap/%'
                    GROUP BY entity_name
                    HAVING COUNT(DISTINCT tag) > 1
                )
            ),
            edge_issues AS (
                SELECT
                    SUM(CASE WHEN source = target THEN 1 ELSE 0 END) AS self_loops,
                    SUM(CASE WHEN NOT EXISTS (
                                SELECT 1 FROM entities e WHERE e.name = graph_edges.source)
                            OR NOT EXISTS (
                                SELECT 1 FROM entities e WHERE e.name = graph_edges.target)
                            THEN 1 ELSE 0 END) AS orphan_edges
                FROM graph_edges
            ),
            company_issues AS (
                SELECT
                    SUM(CASE WHEN (ticker IS NULL OR ticker = '') THEN 1 ELSE 0 END) AS no_ticker,
                    SUM(CASE WHEN NOT EXISTS (
                                SELECT 1 FROM graph_edges ge
                                WHERE ge.edge_type = 'part_of' AND ge.source = entities.name)
                            THEN 1 ELSE 0 END) AS orphan_companies
                FROM entities
                WHERE entity_type = 'company'
            )
            SELECT
                ci.orphan_companies,
                ci.no_ticker,
                ei.self_loops,
                ei.orphan_edges,
                mc.n AS conflicting_market_cap,
                (SELECT MAX(last_updated) FROM entities) AS most_recent_entity,
                (SELECT MAX(computed_at) FROM graph_analytics) AS most_recent_analytics
            FROM company_issues ci, edge_issues ei, mc_conflicts mc
            """
        ).fetchone()
        orphan_companies = row["orphan_companies"]
        no_ticker = row["no_ticker"]
        self_loops = row["self_loops"]
        orphan_edges = row["orphan_edges"]
        conflicting_market_cap = row["conflicting_market_cap"]
        most_recent_entity = row["most_recent_entity"]
        most_recent_analytics = row["most_recent_analytics"]
    finally:
        conn.close()
    stale = bool(
        most_recent_entity
        and most_recent_analytics
        and most_recent_entity > most_recent_analytics
    )

    # Phase 2 (doc/improvements/archive/graph/graph_algos.txt): whole-graph
    # structural metrics via Onager on the app's cached graph connection.
    # Advisory and fully degradable — if the graph layer is unavailable the
    # block is null and the SQLite-side payload above stays authoritative.
    structure = None
    try:
        from helpers.graph.algorithms import graph_metrics
        structure = graph_metrics(con=get_graph_connection())
    except Exception:
        structure = None

    return jsonify({
        "structure": structure,
        "entities": {
            "total": sum(entity_counts.values()),
            "by_type": entity_counts,
        },
        "edges": {
            "total": total_edges,
            "by_type": edges_by_type,
        },
        "sectors": {
            "count": len(sector_sizes),
            "top": [{"sector": row["sector_classification"], "n": row["n"]}
                    for row in sector_sizes[:10]],
            "size_distribution": {
                "min": min(sizes) if sizes else 0,
                "max": max(sizes) if sizes else 0,
                "mean": round(sum(sizes) / len(sizes), 1) if sizes else 0,
            },
        },
        "hygiene": {
            "orphan_companies": orphan_companies,
            "no_ticker": no_ticker,
            "self_loops": self_loops,
            "orphan_edges": orphan_edges,
            "conflicting_market_cap": conflicting_market_cap,
        },
        "staleness": {
            "stale": stale,
            "most_recent_entity_update": most_recent_entity,
            "most_recent_analytics_compute": most_recent_analytics,
        },
    })


# Relationship-type semantics for the graph cloud + relationship cloud card.
# Mirrors the edge-type table in doc/design/graph_design.txt §4. Every live edge type
# is listed; `symmetric` drives arrow rendering, `semantics` feeds the tooltip.
_EDGE_SEMANTICS: dict[str, dict[str, object]] = {
    "co_mentioned_in": {
        "symmetric": True,
        "semantics": "Newsletter co-mention (derived)",
    },
    "part_of": {
        "symmetric": False,
        "semantics": "Company → sector (legacy pair)",
    },
    "has_company": {
        "symmetric": False,
        "semantics": "Sector → company (legacy pair)",
    },
    "exposed_to": {
        "symmetric": False,
        "semantics": "Company → theme (cross-sector)",
    },
    "belongs_to": {
        "symmetric": False,
        "semantics": "Sector → super-sector / sub-sector → sector",
    },
    "subsidiary_of": {
        "symmetric": False,
        "semantics": "Subsidiary → parent",
    },
    "jv_with": {
        "symmetric": True,
        "semantics": "Company ↔ company (JV)",
    },
    "acquired": {
        "symmetric": False,
        "semantics": "Acquirer → acquired (temporal)",
    },
    "competes_with": {
        "symmetric": True,
        "semantics": "Company ↔ company (peers)",
    },
    "supplier_to": {
        "symmetric": False,
        "semantics": "Supplier → customer",
    },
    "customer_of": {
        "symmetric": False,
        "semantics": "Customer → supplier",
    },
    "same_group": {
        "symmetric": True,
        "semantics": "Company ↔ company (promoter group)",
    },
    "cited_in": {
        "symmetric": False,
        "semantics": "Company/sector → edition (OKF provenance)",
    },
    "semantic_peer": {
        "symmetric": True,
        "semantics": "Company ↔ company (embedding neighbours, cosine)",
    },
    "invested_in": {
        "symmetric": False,
        "semantics": "Institution → company (reported holder stake)",
    },
}


@app.route("/api/graph/cloud")
def api_graph_cloud():
    """Whole-graph cloud for the Graph tab's cloud mode.

    Returns EVERY entity and EVERY typed edge (SQLite-only, no DuckDB needed),
    plus a ``relationship_types`` summary (edge_type → count + symmetric +
    semantics) for the relationship cloud card.

    Query params:
        edge_type (optional) — restrict the edge set to one relationship
          type; only entities incident to those edges are returned (so the
          cloud stays focused when isolating a relationship).

    Returns:
        {"nodes": [{"id", "label", "entity_type"}],
         "edges": [{"source", "target", "edge_type"}],
         "relationship_types": [{"edge_type", "count", "symmetric", "semantics"}],
         "total_nodes": int, "total_edges": int}
    """
    edge_type = request.args.get("edge_type", "").strip() or None
    conn = get_db_connection()
    try:
        if edge_type:
            edge_rows = conn.execute(
                "SELECT source, target, edge_type FROM graph_edges "
                "WHERE edge_type = ?",
                (edge_type,),
            ).fetchall()
        else:
            edge_rows = conn.execute(
                "SELECT source, target, edge_type FROM graph_edges"
            ).fetchall()
        entity_rows = conn.execute(
            "SELECT name, entity_type FROM entities"
        ).fetchall()
        # Relationship-type counts (full edge set — the summary card always
        # shows the whole corpus, even when the canvas is filtered).
        count_rows = conn.execute(
            "SELECT edge_type, COUNT(*) AS n FROM graph_edges "
            "GROUP BY edge_type ORDER BY n DESC"
        ).fetchall()
    finally:
        conn.close()

    entity_types = {row[0]: row[1] for row in entity_rows}

    nodes = []
    seen: set[str] = set()
    edges = []
    for row in edge_rows:
        source, target, et = row[0], row[1], row[2]
        edges.append({"source": source, "target": target, "edge_type": et})
        for name in (source, target):
            if name not in seen:
                seen.add(name)
                nodes.append({
                    "id": name,
                    "label": name,
                    "entity_type": entity_types.get(name, "unknown"),
                })
    # Sort by type so the legend/rendering order is stable.
    nodes.sort(key=lambda n: (n["entity_type"], n["id"]))

    relationship_types = [
        {
            "edge_type": row[0],
            "count": row[1],
            "symmetric": bool(_EDGE_SEMANTICS.get(row[0], {}).get("symmetric", False)),
            "semantics": str(_EDGE_SEMANTICS.get(row[0], {}).get(
                "semantics", "custom / derived edge type")),
        }
        for row in count_rows
    ]

    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "relationship_types": relationship_types,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    })


# Metrics whose values are scalar floats (sortable, rankable). The label
# metrics (louvain_community, weakly_connected_component) carry int labels
# that group rather than rank — handled separately below.
_SCALAR_GRAPH_METRICS = {
    "pagerank", "degree_centrality", "betweenness_centrality",
    "local_clustering_coefficient", "closeness_centrality",
    "eigenvector_centrality",  # last two added in Bundle G1
    # graph_docs_ui_redesign S1: the four unserved centralities computed &
    # persisted by `make recompute-graph`; all store {"value": float}, so the
    # scalar branch below serves them unchanged.
    "harmonic_centrality", "katz_centrality", "laplacian_centrality",
    "local_reaching_centrality",
}
# Label metrics: value is an int community/component id. Ranked/grouped, not
# sorted by value.
_LABEL_GRAPH_METRICS = {"louvain_community", "weakly_connected_component"}
# Structured-payload metrics: value JSON is not {"value": float}; each gets
# its own response shape in the handler below (graph_docs_ui_redesign §4.3).
_PAYLOAD_GRAPH_METRICS = {"link_prediction", "voterank"}
# Union — the full allowlist served by /api/graph/metrics/<metric>.
_GRAPH_METRIC_ALLOWLIST = (
    _SCALAR_GRAPH_METRICS | _LABEL_GRAPH_METRICS | _PAYLOAD_GRAPH_METRICS
)


@app.route("/api/graph/metrics/<metric>")
def api_graph_metrics(metric: str):  # noqa: C901
    """Serve a computed graph metric from graph_analytics (Bundle J3).

    graph_analytics held pagerank/betweenness/louvain/wcc but no route read
    it — the API exposed structural queries only. This endpoint surfaces the
    computed centrality/community scores so consumers (UI, scripts) can ask
    "top-N by PageRank" or "which community is X in?" without hitting the CLI.

    All-SQLite (no DuckDB needed) — graph_analytics is refreshed by
    `make recompute-graph`, not by the DuckDB cache rebuild.

    Path param: metric (one of the allowlist below; 400 otherwise).
    Query params:
      - top=N  : limit scalar-metric rankings to the top N entities (default
                 10, max 500). Ignored for label metrics.
      - entity=<name> : optional — return only the row(s) for this entity
                        (case-insensitive). For label metrics, still returns
                        the entity's label only (use the ranking shape).

    Response shape (scalar metrics):
      {"metric": ..., "total": N, "ranked": [{"entity": ..., "value": float}, ...]}
    Response shape (label metrics):
      {"metric": ..., "total": N, "groups": [
         {"label": int, "size": int, "members": [name, ...]}, ...]}
    Response shape (voterank, graph_docs_ui_redesign S1):
      {"metric": ..., "total": N, "seeds": [name, ...]}
    Response shape (link_prediction, graph_docs_ui_redesign S1):
      {"metric": ..., "total": N, "entities": [
         {"entity": ..., "method": ..., "edge_types": [...], "best_score": float,
          "candidates": [{"name": ..., "score": ...}, ...]}, ...]}
    """
    # Normalise + validate the metric name. graph_analytics stores metric
    # names in lowercase; accept case-insensitively for URL friendliness.
    metric_lc = metric.lower()
    if metric_lc not in _GRAPH_METRIC_ALLOWLIST:
        return jsonify({
            "error": f"unknown metric {metric!r}",
            "valid_metrics": sorted(_GRAPH_METRIC_ALLOWLIST),
        }), 400

    # Parse top= (scalar metrics only). Capped at 500 to bound payload size.
    top = 10
    raw_top = request.args.get("top")
    if raw_top is not None:
        try:
            top = int(raw_top)
        except ValueError:
            return jsonify({"error": "top must be an integer"}), 400
        if top < 1 or top > 500:
            return jsonify({"error": "top must be between 1 and 500"}), 400

    entity_filter = request.args.get("entity", "").strip() or None

    conn = get_db_connection()
    try:
        import json as _json
        if metric_lc in _PAYLOAD_GRAPH_METRICS:
            # graph_docs_ui_redesign S1: structured payloads persisted by
            # algorithms._persist_link_prediction / _persist_voterank.
            rows = conn.execute(
                "SELECT entity_name, value FROM graph_analytics WHERE metric = ?",
                (metric_lc,),
            ).fetchall()
            if metric_lc == "voterank":
                # Graph-valued: every seed row carries the same ordered
                # {"seeds": [...]} list — serve it once.
                seeds: list[str] = []
                for r in rows:
                    try:
                        parsed = _json.loads(r["value"])
                    except (ValueError, TypeError):
                        continue
                    cand = parsed.get("seeds")
                    if isinstance(cand, list) and len(cand) > len(seeds):
                        seeds = [s for s in cand if isinstance(s, str)]
                return jsonify({
                    "metric": metric_lc,
                    "total": len(seeds),
                    "seeds": seeds,
                })
            # link_prediction: node-keyed rows, each carrying that node's
            # candidate list. Ranked by best candidate score desc.
            entities: list[dict] = []
            for r in rows:
                try:
                    parsed = _json.loads(r["value"])
                except (ValueError, TypeError):
                    continue
                cands = [
                    {"name": c.get("name"), "score": c.get("score")}
                    for c in parsed.get("candidates", [])
                    if isinstance(c, dict)
                ]
                if entity_filter and r["entity_name"].lower() != entity_filter.lower():
                    continue
                entities.append({
                    "entity": r["entity_name"],
                    "method": parsed.get("method"),
                    "edge_types": parsed.get("edge_types", []),
                    "best_score": max(
                        (c["score"] for c in cands
                         if isinstance(c.get("score"), (int, float))),
                        default=0.0,
                    ),
                    "candidates": cands,
                })
            entities.sort(key=lambda e: e["best_score"], reverse=True)
            return jsonify({
                "metric": metric_lc,
                "total": len(entities),
                "entities": entities,
            })
        if metric_lc in _SCALAR_GRAPH_METRICS:
            # value JSON is {"value": float}. Bundle V2: push the json_extract
            # + CAST + ORDER BY into SQL (was: per-row json.loads in Python +
            # Python sort). json_extract returns the value; CAST(...AS REAL)
            # filters non-numeric; ORDER BY ... DESC does the sort in SQLite.
            # Malformed JSON → json_extract returns NULL → CAST fails →
            # filtered out by the `v IS NOT NULL` guard (matches the old
            # try/except + isinstance check).
            if entity_filter:
                rows = conn.execute(
                    """
                    SELECT entity_name,
                           CAST(json_extract(value, '$.value') AS REAL) AS v
                    FROM graph_analytics
                    WHERE metric = ? AND entity_name = ? COLLATE NOCASE
                      AND json_extract(value, '$.value') IS NOT NULL
                    """,
                    (metric_lc, entity_filter),
                ).fetchall()
                ranked = [{"entity": r["entity_name"], "value": float(r["v"])}
                          for r in rows]
                total = len(ranked)
            else:
                # B3: COUNT(*) OVER() piggybacks the total on the same scan
                # the ranked query already does — one round-trip instead of
                # two (avoids re-scanning graph_analytics for a bare COUNT).
                rows = conn.execute(
                    """
                    SELECT entity_name,
                           CAST(json_extract(value, '$.value') AS REAL) AS v,
                           COUNT(*) OVER () AS total
                    FROM graph_analytics
                    WHERE metric = ?
                      AND json_extract(value, '$.value') IS NOT NULL
                    ORDER BY CAST(json_extract(value, '$.value') AS REAL) DESC
                    LIMIT ?
                    """,
                    (metric_lc, top),
                ).fetchall()
                ranked = [{"entity": r["entity_name"], "value": float(r["v"])}
                          for r in rows]
                total = rows[0]["total"] if rows else 0
            return jsonify({
                "metric": metric_lc,
                "total": total,
                "ranked": ranked,
            })
        else:
            # Label metric: value JSON is {"community": int} or
            # {"componentId": int} (+ "modularity" for louvain, G2). Group by
            # label; the per-label modularity (if present) is surfaced once
            # at the top level, not duplicated per group.
            label_key = "community" if metric_lc == "louvain_community" else "componentId"
            rows = conn.execute(
                "SELECT entity_name, value FROM graph_analytics "
                "WHERE metric = ? ORDER BY entity_name",
                (metric_lc,),
            ).fetchall()
            groups: dict[int, list[str]] = {}
            modularity = None
            for r in rows:
                try:
                    parsed = _json.loads(r["value"])
                except (ValueError, TypeError):
                    continue
                label = parsed.get(label_key)
                if isinstance(label, int):
                    groups.setdefault(label, []).append(r["entity_name"])
                if modularity is None and "modularity" in parsed:
                    modularity = parsed["modularity"]
            group_list = [
                {"label": label, "size": len(members), "members": members}
                for label, members in sorted(
                    groups.items(), key=lambda kv: (-len(kv[1]), kv[0])
                )
            ]
            payload = {
                "metric": metric_lc,
                "total": sum(len(m) for m in groups.values()),
                "groups": group_list,
            }
            if modularity is not None:
                payload["modularity"] = modularity
            return jsonify(payload)
    finally:
        conn.close()


@app.route("/api/graph/co-mentions")
def api_graph_co_mentions():
    """C1: Top entities by co-mention frequency.

    Query params:
      - top=N: number of entities (default 20, max 500).

    Returns: {"ranked": [{"entity": ..., "co_mentions": int}, ...]}
    """
    from helpers.graph.query import co_mention_top
    top = 20
    raw_top = request.args.get("top")
    if raw_top is not None:
        try:
            top = max(1, min(500, int(raw_top)))
        except ValueError:
            return jsonify({"error": "top must be an integer"}), 400
    conn = get_db_connection()
    try:
        return jsonify({"ranked": co_mention_top(top, conn=conn)})
    finally:
        conn.close()


@app.route("/api/graph/bridges")
def api_graph_bridges():
    """C3: Cross-sector bridges — M&A and JV activity between sector pairs.

    Returns: {"bridges": [{"edge_type": ..., "sector_a": ..., "sector_b": ...,
             "count": int}, ...]}
    """
    from helpers.graph.query import cross_sector_bridges
    conn = get_db_connection()
    try:
        return jsonify({"bridges": cross_sector_bridges(conn=conn)})
    finally:
        conn.close()


@app.route("/api/graph/edges-by-year")
def api_graph_edges_by_year():
    """C4: Temporal edge formation — M&A and JV activity by year.

    Returns: {"timeline": [{"year": "YYYY", "edge_type": ..., "count": int}, ...]}
    """
    from helpers.graph.query import edges_by_year
    conn = get_db_connection()
    try:
        return jsonify({"timeline": edges_by_year(conn=conn)})
    finally:
        conn.close()


@app.route("/api/graph/refresh", methods=["POST"])
def api_graph_refresh():
    """Rebuild the disk-based DuckDB cache and reset the long-lived connection.

    Call after parse_newsletter --apply / derive-relations / stub-batch runs
    so the disk cache picks up new edges. Equivalent to ``make graph-rebuild``
    but also drops the cached connection so the next request re-opens against
    the freshly-built file. Idempotent; safe to call repeatedly.

    Order matters: we reset (close) the long-lived connection FIRST so the
    DuckDB file is free for rebuild to open it read-write. DuckDB allows
    either one read-write OR many read-only connections per file, never
    both — if we rebuilt before resetting, the Flask connection's read-
    write lock would block the rebuild.

    Returns 200 on success. On rebuild failure (e.g. the SQLite file is
    mid-write) returns 500 with the error in the body — the connection is
    still reset, so the next request will trigger a cold rebuild, but the
    operator gets an honest failure signal rather than a false success.
    """
    _reset_graph_connection()
    try:
        from helpers.graph.query import rebuild
        rebuild()
    except Exception as e:
        app.logger.error("graph rebuild failed: %s", e)
        return jsonify({"status": "error", "message": f"rebuild failed: {e}"}), 500
    return jsonify({"status": "ok", "message": "graph rebuilt and connection reset"})


# --------------------------------------------------------------------------- #
# Security headers                                                             #
# --------------------------------------------------------------------------- #
# SEC-3 hardening (private security review, Phase 3):
# all scripts/styles are vendored under /static/vendor/ (same-origin), so the
# CSP can be closed to 'self'. Two deliberate deviations from the strictest
# form, both documented:
#   - style-src 'unsafe-inline': the templates use inline style="" attributes
#     (display toggles) and the syntax highlighters emit per-token inline
#     styles. Blocking those would break the UI; no external styles exist.
#   - img-src data: lightbox/thumbnail placeholders use data: URIs.
@app.after_request
def _security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# --------------------------------------------------------------------------- #
# JSON error handlers                                                          #
# --------------------------------------------------------------------------- #
# Scope to /api/ paths so browser navigation 404s (e.g. /entity/Unknown) keep
# rendering HTML — only JSON-consuming clients get JSON. For non-API paths we
# fall back to a minimal HTML page (Flask's default 404 handler is replaced
# once a custom @app.errorhandler(404) is registered, so we have to render the
# fallback ourselves).

_DEFAULT_404_HTML = (
    "<!DOCTYPE html><html><head><title>404 Not Found</title></head>"
    "<body><h1>Not Found</h1>"
    "<p>The requested URL was not found on the server.</p>"
    "</body></html>"
)


@app.errorhandler(404)
def _api_not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": e.description or "not found"}), 404
    return _DEFAULT_404_HTML, 404


@app.errorhandler(400)
def _api_bad_request(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": e.description or "bad request"}), 400
    # Minimal HTML fallback for non-API paths (browser navigation etc.).
    # e.description can carry request-derived text (e.g. abort(400,
    # description=str(e))), so it MUST be escaped before interpolation.
    from markupsafe import escape

    return (
        "<!DOCTYPE html><html><head><title>400 Bad Request</title></head>"
        f"<body><h1>Bad Request</h1><p>{escape(e.description or 'bad request')}</p></body></html>",
        400,
    )


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")  # noqa: S104  # containerized deploy intentionally binds all interfaces
    port = int(os.getenv("FLASK_PORT", 5200))
    debug_mode = os.getenv("FLASK_DEBUG", "False").lower() in ["true", "1", "t"]
    app.run(host=host, port=port, debug=debug_mode)
