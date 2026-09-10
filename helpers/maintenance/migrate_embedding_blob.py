#!/usr/bin/env python3
"""embedding_blob_migration S4 — one-shot TEXT→f32-BLOB migration.

Five surfaces (2026-09-10 audit): embed_store.db embed_cache (43,259 rows,
367 MB), research.db note_search (16,479 / 140 MB) and
company_embeddings (1,165 / 9.9 MB), doc_search.db (1,102 / 9.4 MB),
script_search.db (348 / 3.0 MB). All five surfaces are packed IN PLACE,
losslessly at f32 (max |dcos| 8.4e-9, 0/59 neighbor flips measured).
--apply NEVER deletes rows: the original cache WIPE was removed
post-execution (2026-09-10) because re-warming 16.5k sections costs ~1 h
wall — TEXT cache rows pack in place like everything else, and an
already-BLOB cache is a no-op.

FTS5 virtual tables (note_search, doc_search, script_search) take an
in-place UPDATE with a registered pack function — UNINDEXED columns hold
BLOBs fine and the FTS index is untouched. company_embeddings is a plain
table whose live DDL carries ``CHECK (json_array_length(embedding) =
384)`` — JSON-specific, incompatible with BLOB — so it gets the new-table
swap with the BLOB-native DDL from embeddings.py.

VACUUM runs on every touched DB afterwards (freelist is 0 today; the
rewrite leaves the text bytes reclaimable).

Usage::

    python3 helpers/maintenance/migrate_embedding_blob.py --check   # report only
    python3 helpers/maintenance/migrate_embedding_blob.py --apply   # migrate + VACUUM (never deletes rows)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.vec_codec import pack_f32  # noqa: E402

_SURFACES = [
    # (db_relpath, table, key_cols_for_count)
    ("memory/research.db", "note_search", "COUNT(*)"),
    ("memory/research.db", "company_embeddings", "COUNT(*)"),
    ("memory/doc_search.db", "doc_search", "COUNT(*)"),
    ("memory/script_search.db", "script_search", "COUNT(*)"),
    ("memory/embed_store.db", "embed_cache", "COUNT(*)"),
]


def _pack_sql(value):
    """SQL function: TEXT JSON -> f32 BLOB; BLOB passes through."""
    if isinstance(value, (bytes, bytearray)):
        return value
    import json

    try:
        vec = json.loads(value)
    except TypeError, ValueError:
        return value
    return pack_f32(vec) if isinstance(vec, list) and vec else value


def _codec_state(conn, table: str) -> tuple[str, int]:
    """('blob' | 'text' | 'mixed' | 'empty', text_rows)."""
    row = conn.execute(
        f"SELECT SUM(LENGTH(embedding)) FROM {table} "  # noqa: S608  # call-site constant
        "WHERE typeof(embedding) = 'text'"
    ).fetchone()
    text_bytes = row[0] or 0
    n = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE embedding IS NOT NULL"  # noqa: S608
    ).fetchone()[0]
    n_text = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE typeof(embedding) = 'text'"  # noqa: S608
    ).fetchone()[0]
    if n == 0:
        return "empty", 0
    if n_text == 0:
        return "blob", 0
    if n_text == n:
        return "text", text_bytes
    return "mixed", text_bytes


def check() -> int:
    import sqlite3

    bad = 0
    for rel, table, _ in _SURFACES:
        p = _REPO_ROOT / rel
        if not p.exists():
            print(f"  {rel:26} {table:20} ABSENT (skipped)")
            continue
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            state, text_bytes = _codec_state(conn, table)
        finally:
            conn.close()
        flag = "" if state in ("blob", "empty") else "  <-- TEXT"
        bad += 1 if state in ("text", "mixed") else 0
        print(f"  {rel:26} {table:20} {state:6}  text payload {text_bytes / 1e6:8.1f} MB{flag}")
    print("ALL BLOB (clean)" if bad == 0 else f"{bad} surface(s) still TEXT")
    return 0 if bad == 0 else 1


def _migrate_table(conn, table: str) -> int:
    conn.create_function("vec_pack", 1, _pack_sql, deterministic=True)
    cur = conn.execute(
        f"UPDATE {table} SET embedding = vec_pack(embedding) "  # noqa: S608  # call-site constant
        "WHERE typeof(embedding) = 'text'"
    )
    return cur.rowcount if cur.rowcount is not None else 0


def _swap_company_embeddings(conn) -> int:
    """New-table swap: live DDL's CHECK (json_array_length(...)) is
    JSON-specific and rejects BLOB writes — replace with the BLOB-native
    DDL from embeddings.py."""
    conn.execute("DROP TABLE IF EXISTS company_embeddings_new")
    conn.execute(
        """
        CREATE TABLE company_embeddings_new (
            company_name TEXT PRIMARY KEY,
            embedding    BLOB NOT NULL,
            model        TEXT NOT NULL,
            created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
            CHECK (length(embedding) % 4 = 0 AND length(embedding) > 0)
        )
        """
    )
    conn.create_function("vec_pack", 1, _pack_sql, deterministic=True)
    cur = conn.execute(
        """
        INSERT INTO company_embeddings_new (company_name, embedding, model, created_at)
        SELECT company_name, vec_pack(embedding), model, created_at
        FROM company_embeddings
        """
    )
    n = cur.rowcount if cur.rowcount is not None else 0
    conn.execute("DROP TABLE company_embeddings")
    conn.execute("ALTER TABLE company_embeddings_new RENAME TO company_embeddings")
    return n


def apply_migration() -> int:
    import sqlite3

    results: list[tuple[str, str, int]] = []
    for rel, table, _ in _SURFACES:
        p = _REPO_ROOT / rel
        if not p.exists():
            continue
        before = p.stat().st_size
        conn = sqlite3.connect(str(p))
        try:
            if table == "company_embeddings":
                n = _swap_company_embeddings(conn)
                results.append((rel, table, n))
                print(f"  {rel:26} company_embeddings   packed {n:,} rows (new-table swap)")
            else:
                n = _migrate_table(conn, table)
                results.append((rel, table, n))
                print(f"  {rel:26} {table:20} packed {n:,} rows (in-place)")
            conn.commit()
        finally:
            conn.close()
        conn = sqlite3.connect(str(p))
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
        after = p.stat().st_size
        print(f"    VACUUM: {before / 1e6:7.1f} MB -> {after / 1e6:7.1f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report codec state per surface")
    g.add_argument(
        "--apply", action="store_true", help="pack TEXT rows to BLOB + VACUUM (never deletes rows)"
    )
    args = p.parse_args(argv)
    if args.check:
        return check()
    return apply_migration() + check()


if __name__ == "__main__":
    raise SystemExit(main())
