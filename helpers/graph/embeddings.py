#!/usr/bin/env python3
"""
Embedding management for the FinData knowledge graph.

Populates ``company_embeddings`` in the SQLite source-of-truth database
(``memory/research.db``) from company/note text. The embeddings are then
materialised into DuckDB by ``helpers/graph/query.py::_materialise_embeddings()``
on the next ``connect()`` / ``rebuild`` cycle, and queried via
``semantic_neighbors()``.

One mode: deterministic pseudo-embeddings. (A real-API path — OpenAI /
Azure text-embedding-3-small via `--api`/`--provider` — existed from the
module's introduction but was never invoked anywhere: no Makefile target,
procedure, or test called it, and the live DB + tracked snapshot contained
only dry-run vectors. Removed 2026-08-17; see completed.md #115. The
`openai` dependency was dropped with it. If real embeddings are ever
wanted, reintroduce against the then-current API.)

Usage:
    python3 helpers/graph/embeddings.py                      # populate all companies
    python3 helpers/graph/embeddings.py --company "CEAT"     # single company
    python3 helpers/graph/embeddings.py --dims 64           # 64-dim pseudo-embeddings
    python3 helpers/graph/embeddings.py --clear              # wipe existing embeddings

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

from helpers.core.db import connect as db_connect, DEFAULT_DB_PATH


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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_emb_company "
        "ON company_embeddings(company_name)"
    )


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
        b = h[i*4:(i+1)*4]
        val = int.from_bytes(b, byteorder='little', signed=True)
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
        "SELECT file_path, sector_classification FROM entities WHERE name = ?",
        (company_name,)
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


def populate_dry_run(conn: sqlite3.Connection, dims: int = 64, company: str | None = None) -> int:
    """Populate embeddings using deterministic pseudo-embeddings.

    Returns the number of rows inserted/updated.
    """
    _ensure_schema(conn, dims)

    if company:
        names = [company]
    else:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM entities WHERE entity_type = 'company' ORDER BY name"
        ).fetchall()]

    count = 0
    for name in names:
        text = _get_company_text(conn, name)
        vec = _pseudo_embedding(text, dims)
        # Convert list to SQLite-compatible array literal
        vec_str = "[" + ", ".join(repr(v) for v in vec) + "]"
        conn.execute(
            "INSERT OR REPLACE INTO company_embeddings (company_name, embedding, model) "
            "VALUES (?, ?, ?)",
            (name, vec_str, f"dry-run-v{dims}")
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

    models = [row[0] for row in conn.execute(
        "SELECT DISTINCT model FROM company_embeddings ORDER BY model"
    ).fetchall()]

    # Sample dimension from first row (SQLite stores FLOAT[N] as a JSON array string)
    r2 = conn.execute("SELECT embedding FROM company_embeddings LIMIT 1").fetchone()
    if r2:
        try:
            sample_dim = len(ast.literal_eval(r2[0]))
        except Exception:
            sample_dim = None
    else:
        sample_dim = None

    return {"total": total, "models": models, "sample_dim": sample_dim}


def main():
    parser = argparse.ArgumentParser(
        description="Generate and persist company embeddings for VSS vector search."
    )
    parser.add_argument("--model", default=None,
                        help="Embedding model label (default: dry-run-v{dims})")
    parser.add_argument("--dims", type=int, default=None,
                        help="Embedding dimensions (default: 64 dry-run, 1536 API)")
    parser.add_argument("--company", default=None,
                        help="Single company name (default: all companies)")
    parser.add_argument("--clear", action="store_true",
                        help="Delete all existing embeddings before populating")
    parser.add_argument("--stats", action="store_true",
                        help="Print embedding stats and exit")

    args = parser.parse_args()

    conn = db_connect(str(DEFAULT_DB_PATH))

    if args.stats:
        s = stats(conn)
        print(json.dumps(s, indent=2))
        conn.close()
        return 0

    if args.clear:
        n = clear(conn)
        print(f"Cleared {n} embedding rows", file=sys.stderr)

    dims = args.dims or 64
    model = args.model or f"dry-run-v{dims}"
    print(f"Generating pseudo-embeddings (dims={dims}, model={model})..."
          , file=sys.stderr)
    count = populate_dry_run(conn, dims=dims, company=args.company)

    print(f"Inserted/updated {count} embeddings", file=sys.stderr)
    print(f"Stats: {json.dumps(stats(conn))}", file=sys.stderr)

    # Note: after populating, run `make graph-rebuild` to materialise into DuckDB
    print("\nNext: run 'make graph-rebuild' to materialise embeddings into DuckDB",
          file=sys.stderr)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
