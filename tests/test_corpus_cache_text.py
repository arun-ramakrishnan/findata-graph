"""Regression: corpus cache hits must carry full Note.text (cache v3).

Cache v2 (8ebd60345) persisted frontmatter+body only and served
Note.text="" on hits under a wrong "no consumer reads Note.text" audit —
sync_tags / verify_notes / derive_cited_in / derive_co_mentions read text,
and `maint --full` pre-warms the cache before running
`sync_tags --corpus --apply`, whose apply path is DELETE-then-insert: a
warm cache would have wiped entity_tags. Found by the 2026-10-10 range
review (finding B1); evidence in doc/local/engineering/code_review.md.

Teeth contract: the mtime-hit and hash-hit assertions each die if their
`text=""` return site comes back; the NULL-row test dies if the v2
self-heal regresses. Conftest's autouse _corpus_db_to_tmp keeps this off
the real memory/corpus.db.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from helpers.core import corpus as corpus_mod
from helpers.core.corpus import Corpus
from helpers.core.frontmatter import split_frontmatter

NOTES = {
    "Companies/Alpha/Alpha_Corp.md": (
        "---\ntitle: Alpha Corp\nsector: Chemicals\n---\n\nAlpha body line.\n"
    ),
    "Companies/Beta/Beta_Corp.md": "---\ntitle: Beta Corp\nsector: Metals\n---\n\nBeta body line.\n",
    "Sectors/Chemicals.md": "---\ntitle: Chemicals\n---\n\nSector body.\n",
}


def _write_vault(root: Path) -> None:
    for rel, text in NOTES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _read(n_path: Path) -> str:
    return Path(n_path).read_text(encoding="utf-8", errors="replace")


def test_warm_cache_carries_text_on_both_hit_paths(tmp_path):
    root = tmp_path / "vault"
    _write_vault(root)

    # Cold load: cache DB does not exist yet -> full walk + v3 cache write.
    cold = Corpus.load(root, use_cache=True)
    assert corpus_mod.CORPUS_DB.exists(), "cold load must build the cache db"
    assert len(cold.notes) == len(NOTES)
    for n in cold.notes:
        assert n.text == _read(n.path)

    # Warm load, tree untouched -> every note rides the mtime-hit path.
    warm = Corpus.load(root, use_cache=True)
    assert len(warm.notes) == len(NOTES)
    for n in warm.notes:
        assert n.text == _read(n.path), f"mtime-hit served empty/stale text for {n.path}"
        # The exact consumer contract that broke: frontmatter extractable
        # from the served text (sync_tags.split_front_matter chain).
        block = split_frontmatter(n.text)[1]
        assert block.strip(), f"frontmatter not extractable from served text: {n.path}"

    # Hash-hit path: bump mtime with identical content on one note.
    target = root / "Companies/Alpha/Alpha_Corp.md"
    st = target.stat()
    os.utime(target, (st.st_atime + 10, st.st_mtime + 10))
    touched = Corpus.load(root, use_cache=True)
    alpha = next(n for n in touched.notes if n.path.name == "Alpha_Corp.md")
    assert alpha.text == NOTES["Companies/Alpha/Alpha_Corp.md"], "hash-hit served empty/stale text"


def test_pre_v3_rows_with_null_text_self_heal(tmp_path):
    root = tmp_path / "vault"
    _write_vault(root)
    Corpus.load(root, use_cache=True)

    # Simulate pre-v3 rows: the text column exists but every row is NULL.
    # Call-time attribute read: the autouse redirect swaps corpus_mod's
    # binding, so a module-level imported name would hit the REAL cache.
    conn = sqlite3.connect(str(corpus_mod.CORPUS_DB))
    try:
        conn.execute("UPDATE corpus_cache SET text = NULL")
        conn.commit()
    finally:
        conn.close()

    healed = Corpus.load(root, use_cache=True)
    assert len(healed.notes) == len(NOTES)
    for n in healed.notes:
        assert n.text == _read(n.path), f"NULL-text row was not self-healed: {n.path}"

    # And the backfill persisted — no NULL rows remain.
    conn = sqlite3.connect(str(corpus_mod.CORPUS_DB))
    try:
        nulls = conn.execute("SELECT COUNT(*) FROM corpus_cache WHERE text IS NULL").fetchone()[0]
    finally:
        conn.close()
    assert nulls == 0
