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

GEMMA ADOPTION (2026-10-08, company_embeddings_gemma_trial, completed.md
#368): the real lane is SELECTOR-based — EmbeddingGemma-2 (512d, sidecar,
``_gemma_basis`` doc-side prefix) when available, granite-embedding-97m-r2
(384d, in-process) otherwise; ``COMPANY_EMBEDDER=granite`` forces granite
and ``guard_gemma_stamp`` refuses a granite rebuild of a gemma-stamped
table (the maint path degrades to a WARNING instead). The vss query side
is stamp-keyed in ``helpers/core/vss_index._pick_embedder``.

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

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

# Project root for imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect as db_connect, DEFAULT_DB_PATH, bump_generation
from helpers.core.vec_codec import pack_f32


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
            embedding    BLOB NOT NULL,
            model        TEXT NOT NULL,
            created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
            CHECK (length(embedding) = {dims} * 4)
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


_H2_RE = re.compile(r"^##\s", re.M)
_H_RE = re.compile(r"^#{1,6}\s", re.M)


def _overview_body(content: str, cap: int = 1500) -> str:
    """First ``##`` section body (the Company Overview prose), falling back
    to the opening ``cap`` chars when the note has no ``##`` section.

    S7 base experiment (embed_full_reembed, 2026-09-06): the overview
    carries the sector-coherent signal for semantic neighbors — the full
    note's long tail dilutes it (granite 17/30 vs 13/30 same-sector
    >=3/5 on the 30-seed probe, matching bge's 17/30). The H1 title and
    ticker/sector/metadata line before the first ``##`` are skipped:
    name and sector travel in the base prefix already."""
    m = _H2_RE.search(content)
    if m:
        rest = content[m.end() :]
        nxt = _H_RE.search(rest)
        body = rest[: nxt.start()] if nxt else rest
        return body.strip()[:cap]
    return content[:cap].strip()


def _company_text_pair(conn: sqlite3.Connection, company_name: str) -> tuple[str, str]:
    """``(granite_basis, gemma_basis)`` for one company, identical content
    resolution in both lanes.

    Reads the markdown file referenced by the entity's file_path, strips
    YAML frontmatter, and returns name + sector + the overview body (see
    _overview_body). Falls back to the company name + sector if the file
    is missing. The granite lane is BYTE-IDENTICAL to the pre-gemma
    _get_company_text (cache keys must not drift); the gemma lane wraps
    the same title/sector/body in the shared doc-side prefix
    (rebuild_script_search._gemma_basis, cap unchanged at 1500).
    """
    from helpers.maintenance.rebuild_script_search import _gemma_basis

    r = conn.execute(
        "SELECT file_path, sector_classification FROM entities WHERE name = ?", (company_name,)
    ).fetchone()

    if not r:
        return company_name, company_name

    file_path, sector = r
    sector_or = sector or ""

    # Exchange-seeded stub (D17): file_path IS NULL — no note exists yet.
    if not file_path:
        stub = f"{company_name}. {sector_or}"
        return stub, stub

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
            body = _overview_body(content)
            return (
                f"{company_name}. {sector_or}. {body}",
                _gemma_basis(company_name, sector_or, body),
            )
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass

    stub = f"{company_name}. {sector_or}"
    return stub, stub


def _get_company_text(conn: sqlite3.Connection, company_name: str) -> str:
    """Extract the text content of a company's markdown note for embedding
    (the granite lane of _company_text_pair; kept as the canonical single
    -text helper for callers outside populate)."""
    return _company_text_pair(conn, company_name)[0]


COMPANY_EMBEDDER_ENV = "COMPANY_EMBEDDER"


def _stored_model(conn: sqlite3.Connection) -> str | None:
    """The table's current model stamp, or None when empty/absent."""
    try:
        r = conn.execute("SELECT model FROM company_embeddings LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def resolve_company_embedder(conn: sqlite3.Connection) -> tuple[bool, int, str]:
    """``(gemma_active, dims, model_label)`` for the company surface.

    Adoption default (company_embeddings_gemma_trial, completed.md #368):
    gemma whenever the sidecar/model is available, granite fallback
    otherwise — for tables NOT gemma-stamped. A gemma-stamped table
    REFUSES the fallback at the write path (guard_gemma_stamp), mirroring
    the script/memory selectors; ``COMPANY_EMBEDDER=granite`` is the
    escape that legitimizes a deliberate granite rebuild.
    """
    from helpers.core import gemma_embedder

    env = os.environ.get(COMPANY_EMBEDDER_ENV, "").strip().lower()
    if gemma_embedder.available() and env != "granite":
        return True, gemma_embedder.DIM, gemma_embedder.MODEL_LABEL
    from helpers.core import local_embedder

    return False, local_embedder.DIM, local_embedder.MODEL_ID


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
    """Populate embeddings with the selected local model (selector:
    gemma sidecar when available, granite otherwise — see
    resolve_company_embedder; a gemma-stamped table refuses the granite
    fallback unless COMPANY_EMBEDDER says granite).

    Goes through the shared Q3 content-hash cache (helpers/core/
    embed_cache.py): unchanged companies are cache hits (no embed), changed
    ones re-embed and update the cache — so this path seeds the cache,
    making every later refresh (e.g. ``--maint``) warm. Also GCs rows whose
    company no longer exists in ``entities``.

    Returns the number of rows inserted/updated. Raises SystemExit when the
    resolved embedder is unavailable, the table holds foreign-model rows,
    or the demotion guard fires.
    """
    from helpers.core import gemma_embedder, local_embedder
    from helpers.core.embed_cache import cached_embed_batch
    from helpers.core.gemma_embedder import guard_gemma_stamp

    gemma_active, dims, model_label = resolve_company_embedder(conn)
    guard_gemma_stamp(_stored_model(conn), model_label, os.environ.get(COMPANY_EMBEDDER_ENV))
    if gemma_active:

        def embed_missing(texts: list[str]) -> list[list[float]]:
            return [gemma_embedder.embed_document(t) for t in texts]

    else:
        if not local_embedder.available():
            raise SystemExit(
                "local embedder unavailable — see the download command in "
                "helpers/core/local_embedder.py's docstring."
            )
        embed_missing = local_embedder.embed_documents_parallel

    _ensure_schema(conn, dims)
    _ensure_single_model(conn, model_label)

    if company:
        names = [company]
    else:
        # Noteless stubs (file_path IS NULL, ~5k exchange-seeded) are
        # excluded — a name+sector-only vector is cosine noise.
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM entities WHERE entity_type = 'company'"
                " AND file_path IS NOT NULL ORDER BY name"
            ).fetchall()
        ]

    # Batch embed through the cache: misses go through the pinned spawn
    # pool (parallel_cold_embed proposal, 2026-08-29 — cold populate
    # ~15 min -> ~4 min; warm cycles have ~0 misses and never spawn it).
    # Index side is embed_document — never the BGE query prefix; see
    # local_embedder.
    texts = [_company_text_pair(conn, n)[1 if gemma_active else 0] for n in names]
    # No automatic model-difference purge (2026-10-08): a prior-model row
    # is rollback insurance, dead-text eviction is `make embed-gc`.
    vecs, cache_stats = cached_embed_batch(
        conn,
        texts,
        model_label,
        embed_missing,
        source="company",
    )
    # Stable-write upsert (maint_full_zero_churn F2): an unchanged vector
    # writes NOTHING — INSERT OR REPLACE here used to delete+reinsert every
    # row each cycle, restamping created_at on all of them and forcing a
    # pointless snapshot churn. changed counts rows actually written.
    count = 0
    for name, vec in zip(names, vecs):
        vec_blob = pack_f32(vec)
        cur = conn.execute(
            "INSERT INTO company_embeddings (company_name, embedding, model, created_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(company_name) DO UPDATE SET "
            "    embedding  = excluded.embedding, "
            "    model      = excluded.model, "
            "    created_at = excluded.created_at "
            "WHERE company_embeddings.embedding IS NOT excluded.embedding "
            "   OR company_embeddings.model     IS NOT excluded.model",
            (name, vec_blob, model_label),
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

    Gate (generalized 2026-10-08 for the gemma adoption — the stamp
    cutover itself stays the user-held apply, never mid-housekeeping):

    - a gemma-stamped table with the sidecar down -> the demotion guard's
      SystemExit caught here -> one WARNING, exit 0 (never populate the
      fallback leg silently — the 2026-10-08 07:40 un-migration class).
    - table's model labels hold anything beyond the two real models
      (empty table, pseudo rows, legacy bge) -> one WARNING naming the
      remediation, exit 0.
    - resolved model != stored stamp (granite<->gemma cutover pending) ->
      one WARNING ("--clear" + the apply), exit 0.
    - resolved granite but the local embedder is unavailable -> one
      WARNING, exit 0.
    - otherwise -> cached populate + GC (seconds on a no-change cycle).
    """
    from helpers.core import gemma_embedder, local_embedder
    from helpers.core.gemma_embedder import GemmaStampDemotion, guard_gemma_stamp

    gemma_active, dims, model_label = resolve_company_embedder(conn)
    env = os.environ.get(COMPANY_EMBEDDER_ENV)
    stored = _stored_model(conn)
    try:
        guard_gemma_stamp(stored, model_label, env)
    except GemmaStampDemotion as e:
        print(f"WARNING: {e} — refresh skipped", file=sys.stderr)
        return 0
    if not gemma_active and not local_embedder.available():
        print(
            "WARNING: local granite embedder unavailable — company-embeddings "
            "refresh skipped (table left as-is). Setup: "
            "helpers/core/local_embedder.py module docstring.",
            file=sys.stderr,
        )
        return 0
    models = stats(conn)["models"]
    known = {local_embedder.MODEL_ID, gemma_embedder.MODEL_LABEL}
    if any(m not in known for m in models):
        print(
            f"WARNING: company_embeddings holds model labels {models}, not a "
            "known real model — run --clear + the local-embeddings apply "
            "(maint never migrates stamps). Refresh skipped.",
            file=sys.stderr,
        )
        return 0
    if not models:
        print(
            "WARNING: company_embeddings is empty — run the local-embeddings "
            "apply first (doc/procedures/embeddings.md); maint never "
            "auto-populates. Refresh skipped.",
            file=sys.stderr,
        )
        return 0
    if stored != model_label:
        print(
            f"WARNING: company_embeddings is {stored!r}-stamped but the "
            f"resolved embedder is {model_label!r} — the cutover is the "
            "user-held apply (--clear + the embeddings apply), maint never "
            "migrates stamps. Refresh skipped.",
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
                "SELECT name FROM entities WHERE entity_type = 'company'"
                " AND file_path IS NOT NULL ORDER BY name"
            ).fetchall()
        ]

    count = 0
    for name in names:
        text = _get_company_text(conn, name)
        vec = _pseudo_embedding(text, dims)
        conn.execute(
            "INSERT OR REPLACE INTO company_embeddings (company_name, embedding, model) "
            "VALUES (?, ?, ?)",
            (name, pack_f32(vec), model),
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
            from helpers.core.vec_codec import load_vec

            sample_dim = len(load_vec(r2[0]) or [])
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
        help="Model label. The legacy alias 'bge-small-en-v1.5' (and any "
        "'granite*'/'gemma*' value) populates via the SELECTED local "
        "embedder — resolve_company_embedder: gemma sidecar when "
        "available, granite otherwise, COMPANY_EMBEDDER=granite forces "
        "granite; anything else is a pseudo-embedding label "
        "(default: dry-run-v{dims})",
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

    if args.model in (local_embedder.MODEL_ID, "bge-small-en-v1.5", "granite", "gemma"):
        # Real path: dims + label come from the selector, not the CLI.
        gemma_active, dims, model_label = resolve_company_embedder(conn)
        print(
            f"Generating local embeddings (selected {model_label}, "
            f"dims={dims}, gemma_active={gemma_active})...",
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
