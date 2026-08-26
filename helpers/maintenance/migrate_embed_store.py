#!/usr/bin/env python3
"""One-shot migration of the legacy per-index vec sidecars into the
consolidated embed store (embed_store_consolidation proposal, 2026-08).

Before the consolidation every SQLite index carried its own ``<db>_vec.db``
sidecar hosting a private copy of the content-hash embed cache
(``note_search_emb_cache``); research's also hosted the ``note_search_vec``
vec0 mirror. This script pools everything into ONE store
(``memory/embed_store.db``):

1. Copy cache rows from each legacy sidecar into the pooled ``embed_cache``
   table — INSERT OR IGNORE on the ``(text_hash, model)`` key, so overlap
   between corpora dedupes and re-runs are idempotent. The ``source``
   column records which legacy file a row came from ('doc' / 'script' /
   'legacy-research' — the research sidecar mixed note + company texts and
   is indistinguishable post-hoc; runtime writers stamp their own cohorts).
2. Optionally (--sync-mirror) rebuild ``note_search_vec`` straight from
   live research.db's FTS JSON column via the normal write path, so the
   hybrid-search KNN leg stays warm without relying on first-query backfill.
3. Rename processed legacy files to ``*.migrated.bak`` (+ any -wal/-shm
   siblings) — NEVER delete; cleanup by hand once gates go green.

Cold-cache alternative would re-embed ~16 minutes of corpus; copying
preserves the warm cache. Migration is additive: flip EMBED_DB_PATH back /
un-rename to roll back.

Run AFTER the code flip (vec_search.EMBED_DB_PATH) in the same sitting:
between them, any rebuild or hybrid query would populate the new store
cold.
"""

from __future__ import annotations

import argparse
import json
import sqlite3  # noqa: F401  # sqlite3.Error still caught on the mirror check
import sys
from pathlib import Path

from helpers.core.db import connect

# Repo root bootstrap: helpers/maintenance/migrate_embed_store.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Legacy file -> pooled-cache cohort stamp.
LEGACY_SOURCES = {
    "research.db_vec.db": "legacy-research",
    "doc_search.db_vec.db": "doc",
    "script_search.db_vec.db": "script",
}
_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _copy_cache(
    store: sqlite3.Connection,
    legacy_path: Path,
    label: str,
) -> dict:
    """INSERT OR IGNORE one legacy cache into the pool and COMMIT.

    Committing per source closes the crash window between rename and
    durability (a renamed source whose copy wasn't committed would leave
    the pool silently cold on re-run)."""
    # mode=ro so a file pending rename is never mutated/recovered
    src = connect(legacy_path, read_only=True)
    try:
        rows = src.execute(
            "SELECT text_hash, model, embedding FROM note_search_emb_cache"  # noqa: S608  # fixed legacy table name
        ).fetchall()
    finally:
        src.close()
    with store:
        store.executemany(
            "INSERT OR IGNORE INTO embed_cache "  # noqa: S608  # bare constant name
            "(text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
            [(h, m, e, label) for h, m, e in rows],
        )
        covered = sum(
            1
            for h, m, _e in rows
            if store.execute(
                "SELECT 1 FROM embed_cache WHERE text_hash = ? AND model = ?",  # noqa: S608  # bare constant name
                (h, m),
            ).fetchone()
        )
    return {"file": str(legacy_path), "rows": len(rows), "pooled_now": covered}


def _rename_legacy(legacy_path: Path) -> list[str]:
    """Rename file + journal siblings to *.migrated.bak; returns new names."""
    renamed = []
    for suffix in _SIDECAR_SUFFIXES:
        candidate = Path(str(legacy_path) + suffix)
        if candidate.exists():
            target = Path(str(candidate) + ".migrated.bak")
            candidate.replace(target)
            renamed.append(target.name)
    return renamed


def migrate(store_path: Path, memory_dir: Path, *, rename: bool = True) -> list[dict]:
    """Copy every present legacy cache; optionally rename sources after."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    from helpers.core.embed_cache import CACHE_DDL_BARE

    report: list[dict] = []
    store = connect(store_path)
    try:
        with store:
            store.execute(CACHE_DDL_BARE)
        for fname, label in LEGACY_SOURCES.items():
            legacy_path = memory_dir / fname
            if not legacy_path.exists():
                report.append({"file": fname, "status": "absent"})
                continue
            stats = _copy_cache(store, legacy_path, label)
            stats["label"] = label
            if rename:
                stats["renamed"] = _rename_legacy(legacy_path)
                stats["status"] = "migrated"
            else:
                stats["status"] = "copied (source left in place)"
            report.append(stats)
    finally:
        store.close()
    return report


def sync_mirror(research_db: Path, dims: int | None = None) -> int:
    """Rebuild note_search_vec inside the store from research.db JSON rows.

    Uses the production write path (sync_vec_table full refresh), so the
    dims-mismatch drop-and-rebuild rule applies unchanged."""
    from helpers.core.db import connect
    from helpers.core.vec_search import sync_vec_table

    conn = connect(research_db)
    try:
        if dims is None:
            row = conn.execute(
                "SELECT embedding FROM note_search "
                "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
            ).fetchone()
            dims = len(json.loads(row[0])) if row else 64
        return sync_vec_table(conn, dims, full=True)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_store = _REPO_ROOT / "memory" / "embed_store.db"
    parser.add_argument("--store", type=Path, default=default_store)
    parser.add_argument("--memory-dir", type=Path, default=_REPO_ROOT / "memory")
    parser.add_argument(
        "--no-rename",
        action="store_true",
        help="copy only; do NOT rename processed legacy files (.bak)",
    )
    parser.add_argument(
        "--sync-mirror",
        action="store_true",
        help="also rebuild note_search_vec from live research.db",
    )
    parser.add_argument("--research-db", type=Path, default=_REPO_ROOT / "memory" / "research.db")
    args = parser.parse_args()

    report = migrate(args.store, args.memory_dir, rename=not args.no_rename)
    print(f"store: {args.store}")
    for row in report:
        line = f"  {row['file']}: {row['status']}"
        if row["status"] != "absent":
            line += f" — {row['rows']} rows read ({row['label']}), {row['pooled_now']} pooled"
            if row.get("renamed"):
                line += ", renamed -> " + ", ".join(row["renamed"])
        print(line)

    if args.sync_mirror:
        n = sync_mirror(args.research_db)
        total = 0
        con = connect(args.store)
        try:
            # vec0 is a virtual table: the extension must be loaded in THIS
            # connection before it can even be counted.
            from helpers.core.vec_search import _load_vec_extension

            _load_vec_extension(con)
            total = con.execute("SELECT COUNT(*) FROM note_search_vec").fetchone()[0]  # noqa: S608  # mirror constant
        except sqlite3.Error as e:
            print(f"  mirror check failed: {e}")
        finally:
            con.close()
        print(f"  --sync-mirror: wrote {n} rows; note_search_vec now holds {total}")

    cached_total = 0
    con = connect(args.store)
    try:
        cached_total = con.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]  # noqa: S608  # bare constant name
        per_source = con.execute(
            "SELECT source, COUNT(*) FROM embed_cache GROUP BY source ORDER BY 2 DESC"  # noqa: S608  # bare constant name
        ).fetchall()
    finally:
        con.close()
    print(f"pooled embed_cache: {cached_total} rows {per_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
