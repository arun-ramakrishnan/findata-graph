#!/usr/bin/env python3
"""
Embedding management for the FinData knowledge graph.

Populates ``company_embeddings`` in the SQLite source-of-truth database
(``memory/research.db``) from company/note text. The embeddings are then
materialised into DuckDB by ``helpers/graph/query.py::_materialise_embeddings()``
on the next ``connect()`` / ``rebuild`` cycle, and queried via
``semantic_neighbors()``.

Two modes:

- deterministic pseudo-embeddings (default): hash vectors for dry-run/testing.
- LOCAL REAL EMBEDDINGS (2026-08-20, local_embeddings proposal): bge-small-
  en-v1.5 via llama.cpp through ``helpers/core/local_embedder.py``. Same 384
  dims as the live table, so the swap is schema-transparent. No network at
  run time; the model file (gitignored) is fetched once per the local_embedder
  docstring.

(The earlier real-API path — OpenAI text-embedding-3-small — was never
invoked anywhere and was removed 2026-08-17; see completed.md #115.)

CLEAR-THEN-POPULATE DISCIPLINE: never mix model labels in the table — cosine
similarity across different models' vector spaces is garbage. Both populate
entry points refuse to write when rows carrying a DIFFERENT model label are
present; run ``--clear`` first. stats() reports the warning when it happens.

CACHED POPULATE (2026-08-21, company_embeddings_maint proposal): populate_local
goes through the shared (sha256(text), model) sidecar cache (helpers/core/
embed_cache.py) — unchanged companies are cache hits, changed ones re-embed
in one batch call, and this path SEEDS the cache, so later refreshes
(``--maint`` in maint-full) are warm (reads + hashes). Deleted companies are
GCed after each populate. ``--maint`` never upgrades the table: WARNING +
exit 0 (no writes) when the embedder is unavailable or the table isn't
bge-populated yet — the upgrade is the user-held apply.

Usage:
    python3 helpers/graph/embeddings.py                      # populate all companies (pseudo)
    python3 helpers/graph/embeddings.py --company "CEAT"     # single company (pseudo)
    python3 helpers/graph/embeddings.py --model bge-small-en-v1.5   # local real embeddings
    python3 helpers/graph/embeddings.py --clear              # wipe existing embeddings
    python3 helpers/graph/embeddings.py --stats              # counts + model labels
    python3 helpers/graph/embeddings.py --maint              # maint-full entry: best-effort
                                                              # cached refresh; WARNING + exit 0
                                                              # (no writes) when unavailable or
                                                              # not yet bge-populated

The SQLite table schema:
    CREATE TABLE company_embeddings (
        company_name TEXT PRIMARY KEY,   -- FK to entities.name
        embedding    FLOAT[N],           -- N-dimensional embedding vector
        model        TEXT,                -- e.g. "dry-run-v1" or "text-embedding-3-small"
        created_at   DATETIME,            -- when this embedding was generated
        CHECK (array_length(embedding) = N)
    );

The DuckDB side (query.py) joins this to v_node.id via the company_name,
materialising FLOAT[] rows for array_cosine_similarity etc.
"""

import ast
import argparse
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

# Project root for imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect as db_connect, DEFAULT_DB_PATH, bump_generation


def _ensure_schema(conn: sqlite3.Connection, dims: int) -> None:
    """Create the company_embeddings table if it doesn't exist.

    The CHECK constraint enforces a fixed embedding dimension so the DuckDB
    materialisation (CAST to FLOAT[N]) type-checks. If the table exists with
    a different dimension, it is dropped first.
    """
    if dims < 1:
        raise ValueError(f"dims must be >= 1, got {dims}")

    # Check if table exists and has a different dimension
    r = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='company_embeddings'"
    ).fetchone()

    if r:
        existing_sql = r[0]
        if f"= {dims}" not in existing_sql and f"={dims}" not in existing_sql.replace(" ", ""):
            # Dimension mismatch — drop and recreate
            conn.execute("DROP TABLE company_embeddings")

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS company_embeddings (
            company_name TEXT PRIMARY KEY,
            embedding    FLOAT[{dims}],
            model        TEXT NOT NULL,
            created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
            CHECK (json_array_length(embedding) = {dims})
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_emb_company ON company_embeddings(company_name)")


def _pseudo_embedding(text: str, dims: int, seed: int = 42) -> list[float]:
    """Generate a deterministic pseudo-embedding from text via SHA-256.

    This is NOT a real embedding — it's a deterministic hash-based vector
    that produces reproducible results for testing the VSS pipeline without
    LLM API costs. Each dimension is independently derived from a window
    of the hash, normalized to [-1, 1].

    In production, replace this with real embeddings from OpenAI, Cohere,
    or another provider.
    """
    if dims < 1:
        raise ValueError(f"dims must be >= 1, got {dims}")

    # Expand the hash into enough bytes for all dimensions
    # Each float needs ~4 bytes, so request ceil(dims * 4) bytes
    needed = dims * 4
    h = hashlib.sha256(f"{seed}:{text}".encode()).digest()
    # Extend if we need more bytes
    while len(h) < needed:
        h += hashlib.sha256(h).digest()

    vec = []
    for i in range(dims):
        # Extract 4 bytes and convert to a signed float in [-1, 1]
        b = h[i * 4 : (i + 1) * 4]
        val = int.from_bytes(b, byteorder="little", signed=True)
        # Map to [-1, 1] using tanh to avoid outliers
        vec.append(math.tanh(val / (2**31)))

    # L2-normalize the vector
    norm = math.sqrt(sum(x**2 for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _get_company_text(conn: sqlite3.Connection, company_name: str) -> str:
    """Extract the text content of a company's markdown note for embedding.

    Reads the markdown file referenced by the entity's file_path, strips
    YAML frontmatter, and returns the concatenated title + body text.
    Falls back to the company name + sector if the file is missing.
    """
    r = conn.execute(
        "SELECT file_path, sector_classification FROM entities WHERE name = ?", (company_name,)
    ).fetchone()

    if not r:
        return company_name

    file_path, sector = r

    # Try to read the markdown file
    full_path = PROJECT_ROOT / file_path
    if full_path.exists():
        try:
            with open(full_path) as f:
                content = f.read()
            # Strip YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2]
            return f"{company_name}. {sector or ''}. {content[:5000]}"
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass

    return f"{company_name}. {sector or ''}"


def _ensure_single_model(conn: sqlite3.Connection, model: str) -> None:
    """Clear-then-populate discipline: refuse to write ``model`` rows while
    rows with a DIFFERENT model label exist (cross-model cosine is garbage).

    Raises SystemExit with the remediation instead of mixing silently."""
    foreign = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT model FROM company_embeddings WHERE model != ?",
            (model,),
        ).fetchall()
    ]
    if foreign:
        raise SystemExit(
            f"company_embeddings holds rows from {foreign} but this run would "
            f"write model {model!r}. Run with --clear first — cosine across "
            "different models' vector spaces is meaningless."
        )


def populate_local(conn: sqlite3.Connection, company: str | None = None) -> int:
    """Populate embeddings with the local bge-small-en-v1.5 model.

    Goes through the shared Q3 content-hash cache (helpers/core/
    embed_cache.py): unchanged companies are cache hits (no embed), changed
    ones re-embed via ONE batch call and update the cache — so this path
    seeds the cache, making every later refresh (e.g. ``--maint``) warm.
    Also GCs rows whose company no longer exists in ``entities``.

    Returns the number of rows inserted/updated. Raises SystemExit when the
    local embedder is unavailable or the table holds foreign-model rows.
    """
    from helpers.core import local_embedder
    from helpers.core.embed_cache import cached_embed_batch

    if not local_embedder.available():
        raise SystemExit(
            "local embedder unavailable — see the download command in "
            "helpers/core/local_embedder.py's docstring."
        )
    _ensure_schema(conn, local_embedder.DIM)
    _ensure_single_model(conn, local_embedder.MODEL_ID)

    if company:
        names = [company]
    else:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM entities WHERE entity_type = 'company' ORDER BY name"
            ).fetchall()
        ]

    # Batch embed through the cache: misses go through the pinned spawn
    # pool (parallel_cold_embed proposal, 2026-08-29 — cold populate
    # ~15 min -> ~4 min; warm cycles have ~0 misses and never spawn it).
    # Index side is embed_document — never the BGE query prefix; see
    # local_embedder.
    texts = [_get_company_text(conn, n) for n in names]
    vecs, cache_stats = cached_embed_batch(
        conn,
        texts,
        local_embedder.MODEL_ID,
        local_embedder.embed_documents_parallel,
        source="company",
    )
    # Stable-write upsert (maint_full_zero_churn F2): an unchanged vector
    # writes NOTHING — INSERT OR REPLACE here used to delete+reinsert every
    # row each cycle, restamping created_at on all of them and forcing a
    # pointless snapshot churn. changed counts rows actually written.
    count = 0
    for name, vec in zip(names, vecs):
        vec_str = "[" + ", ".join(repr(v) for v in vec) + "]"
        cur = conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model, created_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(company_name) DO UPDATE SET "
            "    embedding  = excluded.embedding, "
            "    model      = excluded.model, "
            "    created_at = excluded.created_at "
            "WHERE company_embeddings.embedding IS NOT excluded.embedding "
            "   OR company_embeddings.model     IS NOT excluded.model",
            (name, vec_str, local_embedder.MODEL_ID),
        )
        count += cur.rowcount

    # Stale-vector hygiene: INSERT OR REPLACE never removes, so deleted
    # companies would keep ghost rows (mirrors the rebuild's deleted-file
    # handling in note_search).
    gc = conn.execute(
        "DELETE FROM company_embeddings WHERE company_name NOT IN (SELECT name FROM entities)"  # noqa: S608  # no interpolation
    ).rowcount

    conn.commit()
    # B4 (sql_capability_unlocks): company_embeddings is invisible to the
    # entities/graph_edges generation triggers, so this writer bumps the
    # generation manually — flipping _is_warm so a DuckDB whose v_embeddings
    # projection reads this table rebuilds on the next connect. ONLY when
    # a row actually changed or GC removed rows: the upsert above filters
    # byte-identical vectors, so an all-hits no-GC cycle writes nothing and
    # must not cost the ~2s rebuild (previously a cache MISS alone bumped,
    # even when the re-embed reproduced the identical vector).
    if count or gc:
        bump_generation(conn)
    if cache_stats["hits"] or cache_stats["misses"]:
        print(
            f"embed cache: {cache_stats['hits']} hits, {cache_stats['misses']} misses",
            file=sys.stderr,
        )
    if gc:
        print(f"gc: removed {gc} stale company-embedding row(s)", file=sys.stderr)
    return count


def maint_refresh(conn: sqlite3.Connection) -> int:
    """``--maint`` entry point: best-effort cached refresh for maint-full.

    Three-way gate (company_embeddings_maint proposal §3.3) — never fails
    the housekeeping run, never auto-upgrades the table:

    - local embedder unavailable -> one WARNING, exit 0 (company embeddings
      stay as-is rather than silently regressing to pseudo).
    - table's model labels are not exactly [bge-small-en-v1.5] (empty table,
      pre-apply pseudo rows, or a mixed/broken state) -> one WARNING naming
      the remediation, exit 0. The upgrade to bge is the user-held apply
      (doc/procedures/embeddings.md); maint only keeps an already-applied
      table fresh.
    - otherwise -> cached populate + GC (seconds on a no-change cycle).
    """
    from helpers.core import local_embedder

    if not local_embedder.available():
        print(
            "WARNING: local bge-small embedder unavailable — company-embeddings "
            "refresh skipped (table left as-is). Setup: "
            "helpers/core/local_embedder.py module docstring.",
            file=sys.stderr,
        )
        return 0

    models = stats(conn)["models"]
    if models != [local_embedder.MODEL_ID]:
        if not models:
            print(
                "WARNING: company_embeddings is empty — run the local-embeddings "
                "apply first (doc/procedures/embeddings.md); maint never "
                "auto-populates. Refresh skipped.",
                file=sys.stderr,
            )
        else:
            print(
                f"WARNING: company_embeddings holds model labels {models}, not "
                f"[{local_embedder.MODEL_ID!r}] — run --clear + the local-"
                "embeddings apply (maint never auto-upgrades). Refresh skipped.",
                file=sys.stderr,
            )
        return 0

    count = populate_local(conn)
    print(f"company-embeddings --maint: refreshed {count} row(s)", file=sys.stderr)
    return 0


def populate_dry_run(conn: sqlite3.Connection, dims: int = 64, company: str | None = None) -> int:
    """Populate embeddings using deterministic pseudo-embeddings.

    Returns the number of rows inserted/updated.
    """
    _ensure_schema(conn, dims)
    model = f"dry-run-v{dims}"
    _ensure_single_model(conn, model)

    if company:
        names = [company]
    else:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM entities WHERE entity_type = 'company' ORDER BY name"
            ).fetchall()
        ]

    count = 0
    for name in names:
        text = _get_company_text(conn, name)
        vec = _pseudo_embedding(text, dims)
        # Convert list to SQLite-compatible array literal
        vec_str = "[" + ", ".join(repr(v) for v in vec) + "]"
        conn.execute(
            "INSERT OR REPLACE INTO company_embeddings (company_name, embedding, model) "
            "VALUES (?, ?, ?)",
            (name, vec_str, model),
        )
        count += 1

    conn.commit()
    return count


def clear(conn: sqlite3.Connection) -> int:
    """Delete all embeddings. Returns the number of rows deleted."""
    count = conn.execute("DELETE FROM company_embeddings").rowcount
    conn.commit()
    return count


def stats(conn: sqlite3.Connection) -> dict:
    """Return stats about the embeddings table."""
    try:
        r = conn.execute("SELECT COUNT(*) FROM company_embeddings").fetchone()
        total = r[0] if r else 0
    except sqlite3.OperationalError:
        return {"total": 0, "models": [], "sample_dim": None}

    if total == 0:
        return {"total": 0, "models": [], "sample_dim": None}

    models = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT model FROM company_embeddings ORDER BY model"
        ).fetchall()
    ]

    out = {"total": total, "models": models}

    if len(models) > 1:
        out["warning"] = (
            "mixed model labels present — cosine across models is meaningless; "
            "rerun --clear + repopulate with ONE model"
        )

    # Sample dimension from first row (SQLite stores FLOAT[N] as a JSON array string)
    r2 = conn.execute("SELECT embedding FROM company_embeddings LIMIT 1").fetchone()
    if r2:
        try:
            sample_dim = len(ast.literal_eval(r2[0]))
        except Exception:
            sample_dim = None
    else:
        sample_dim = None
    out["sample_dim"] = sample_dim
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate and persist company embeddings for VSS vector search."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model label. The special value 'bge-small-en-v1.5' "
        "populates via the LOCAL real embedder "
        "(helpers/core/local_embedder.py); anything else is a "
        "pseudo-embedding label (default: dry-run-v{dims})",
    )
    parser.add_argument(
        "--dims",
        type=int,
        default=None,
        help="Embedding dimensions (default: 64 dry-run, 1536 API)",
    )
    parser.add_argument(
        "--company", default=None, help="Single company name (default: all companies)"
    )
    parser.add_argument(
        "--clear", action="store_true", help="Delete all existing embeddings before populating"
    )
    parser.add_argument("--stats", action="store_true", help="Print embedding stats and exit")
    parser.add_argument(
        "--maint",
        action="store_true",
        help="Best-effort cached refresh for maint-full: exits 0 "
        "with a WARNING (no writes) when the embedder is "
        "unavailable or the table isn't bge-populated — "
        "maint never auto-upgrades; otherwise a warm cached "
        "refresh + GC of deleted companies",
    )

    args = parser.parse_args(argv)

    conn = db_connect(str(DEFAULT_DB_PATH))

    if args.stats:
        s = stats(conn)
        print(json.dumps(s, indent=2))
        if "warning" in s:
            print(f"WARNING: {s['warning']}", file=sys.stderr)
        conn.close()
        return 0

    if args.maint:
        rc = maint_refresh(conn)
        conn.close()
        return rc

    if args.clear:
        n = clear(conn)
        print(f"Cleared {n} embedding rows", file=sys.stderr)

    from helpers.core import local_embedder

    if args.model == local_embedder.MODEL_ID:
        # Local real path: dims + label come from the module, not the CLI.
        print(
            f"Generating local embeddings ({local_embedder.MODEL_ID}, "
            f"dims={local_embedder.DIM})...",
            file=sys.stderr,
        )
        count = populate_local(conn, company=args.company)
    else:
        dims = args.dims or 64
        model = args.model or f"dry-run-v{dims}"
        print(f"Generating pseudo-embeddings (dims={dims}, model={model})...", file=sys.stderr)
        count = populate_dry_run(conn, dims=dims, company=args.company)

    print(f"Inserted/updated {count} embeddings", file=sys.stderr)
    print(f"Stats: {json.dumps(stats(conn))}", file=sys.stderr)

    # Note: after populating, run `make graph-rebuild` to materialise into DuckDB
    print("\nNext: run 'make graph-rebuild' to materialise embeddings into DuckDB", file=sys.stderr)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
