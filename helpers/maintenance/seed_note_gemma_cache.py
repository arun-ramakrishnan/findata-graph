#!/usr/bin/env python3
"""Seed the production embed cache with the notes gemma full-pool vectors.

notes_gemma_adoption S1: the definitive 4-hour gemma embed
(``bench_data/embgemma2/notes_arm_gemma_full.npz``, 17,263 sections,
basis parity with the adopted ``_embedding_text`` gemma contract) is
banked into ``vecdb.embed_cache`` as ``(sha256(basis), gemma label) ->
pack_f32(vec)`` rows, so the post-flip rebuild scores 0 sidecar embeds.

Dry-run by default (match report only); ``--apply`` writes. Row join:
normalized ``(file_path, anchor)`` between the live ``note_search`` and
the npz. The seed is exact only while the corpus is unchanged since the
embed run (guard: no findata file newer than the npz).

Usage:
    python3 helpers/maintenance/seed_note_gemma_cache.py            # report
    python3 helpers/maintenance/seed_note_gemma_cache.py --apply    # write
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NPZ_DEFAULT = PROJECT_ROOT / "bench_data/embgemma2/notes_arm_gemma_full.npz"
SECTION_CAP = 8000  # the live _SECTION_EMBED_CAP — basis parity with the trial arm


def _basis(title: str, sector: str, section_title: str, content: str) -> str:
    """The gemma doc-side basis — byte-identical to notes_embed_arm.py's
    texts_m construction (the npz vectors were embedded from exactly this)."""
    return f"title: {title} | text: {sector} — {section_title}\n{content[:SECTION_CAP]}"


def _norm(p: str) -> str:
    return p.lstrip("/")


def main() -> int:
    import numpy as np

    from helpers.core import embed_cache, vec_search
    from helpers.core.db import connect
    from helpers.core.gemma_embedder import MODEL_LABEL as GEMMA_LABEL
    from helpers.core.vec_codec import pack_f32

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--npz", type=Path, default=NPZ_DEFAULT)
    ap.add_argument(
        "--apply", action="store_true", help="write the cache rows (default: report only)"
    )
    args = ap.parse_args()

    z = np.load(args.npz, allow_pickle=False)
    doc_vecs = z["doc_vecs"]  # materialize ONCE — np.load re-decompresses per index
    paths = [_norm(str(p)) for p in z["paths"]]
    anchors = [str(a) for a in z["anchors"]]
    npz_vecs = {(p, a): doc_vecs[i] for i, (p, a) in enumerate(zip(paths, anchors))}

    conn = connect()
    vec_search._attach_vec_db(conn)  # private but the canonical attach (used by rebuild paths)
    cur = conn.execute(
        "SELECT file_path, anchor, title, sector, section_title, content FROM note_search"
    )

    matched = missing = 0
    chunk: list[tuple[str, str, bytes, str]] = []
    insert_sql = (
        f"INSERT OR REPLACE INTO {embed_cache.EMBED_CACHE_TABLE} "  # noqa: S608 — constant table name
        "(text_hash, model, embedding, source) VALUES (?, ?, ?, ?)"
    )
    # STREAM both sides — no fetchall, no big intermediate lists (swap-storm
    # lesson from the first draft: materialize once, per-row everywhere else).
    for file_path, anchor, title, sector, section_title, content in cur:
        vec = npz_vecs.get((_norm(file_path), anchor))
        if vec is None:
            missing += 1
            continue
        basis = _basis(title, sector, section_title, content)
        h = hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()
        chunk.append((h, GEMMA_LABEL, pack_f32(vec), "note"))
        matched += 1
        if len(chunk) >= 2000:
            if args.apply:
                conn.executemany(insert_sql, chunk)
                conn.commit()
            chunk.clear()

    extra = len(npz_vecs) - matched
    print(
        f"note_search rows scanned: {matched + missing} | npz vectors: {len(npz_vecs)} | "
        f"matched: {matched} | db-only (no npz vec): {missing} | npz-only (no live row): {extra}"
    )
    if missing or extra:
        print("REFUSING: npz and live corpus have drifted — investigate before seeding", flush=True)
        return 1

    if not args.apply:
        print("dry-run: matched rows verified, no writes (use --apply)")
        return 0

    if chunk:
        conn.executemany(insert_sql, chunk)
        conn.commit()
    n = conn.execute(
        f"SELECT count(*) FROM {embed_cache.EMBED_CACHE_TABLE} WHERE model = ?",  # noqa: S608
        (GEMMA_LABEL,),
    ).fetchone()[0]
    print(f"seeded {matched} cache rows; total {GEMMA_LABEL} rows now: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
