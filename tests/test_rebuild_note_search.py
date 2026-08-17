"""
Tests for helpers/maintenance/rebuild_note_search.py — the standalone FTS5
rebuild that indexes ALL findata/ markdowns (companies, sectors, super-sectors,
and the newsletter corpora) for free-text content search.

These tests build a tiny synthetic findata/ tree under tmp_path, point the
rebuild module's FINDATA root at it, run the rebuild, and assert the index
behaves correctly (content hits, idempotency, standalone-not-external-content,
newsletter indexing without frontmatter).
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Make the helpers importable (tests run from repo root; this mirrors conftest).
REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.maintenance import rebuild_note_search as rns  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import ENTITIES_DDL  # noqa: E402


# --- fixtures ---------------------------------------------------------------

_COMPANY = """\
---
title: Acme Feeds
type: company
sector: agriculture
market_cap: small_cap
normalized_name: Acme_Feeds
permalink: /companies/agriculture/acme_feeds
tags:
- entity_type/company
- sector/agriculture
created: '2026-01-01'
---

# Acme Feeds

Leading shrimp feed manufacturer. Also makes fish feed and cattle feed.
"""

_SECTOR = """\
---
title: Agriculture
type: sector
super_sector: Consumer_Staples
normalized_name: Agriculture
permalink: /sectors/agriculture
tags:
- entity_type/sector
created: '2026-01-01'
---

# Agriculture Sector

Covers crops, livestock, and aquaculture including shrimp farming.
"""

# Newsletter: NO frontmatter, H1 title, HTML div wrapper + image embed noise.
_NEWSLETTER = """\
<div align="center">

# The Chatter: Aquaculture Edition

Edition #99
</div>

## Acme Feeds | small_cap | agriculture

Shrimp feed revenues grew 20% in Q3. ![img](image1.png)
"""


@pytest.fixture
def seeded_tree(tmp_path, monkeypatch):
    """Build a synthetic findata/ tree + entities DB; repoint the rebuild
    module's FINDATA + DEFAULT_DB at the tmp tree."""
    findata = tmp_path / "findata"
    (findata / "Companies" / "Agriculture").mkdir(parents=True)
    (findata / "Sectors").mkdir(parents=True)
    (findata / "The_Chatter").mkdir(parents=True)

    co_path = findata / "Companies" / "Agriculture" / "Acme_Feeds.md"
    co_path.write_text(_COMPANY, encoding="utf-8")
    (findata / "Sectors" / "Agriculture.md").write_text(_SECTOR, encoding="utf-8")
    (findata / "The_Chatter" / "Aquaculture_Edition.md").write_text(
        _NEWSLETTER, encoding="utf-8"
    )

    # Build the entities DB so the rebuild can look up title + sector by path.
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(ENTITIES_DDL)
    conn.execute(
        "INSERT INTO entities (name, entity_type, sector_classification, "
        "file_path, normalized_name) VALUES (?,?,?,?,?)",
        ("Acme_Feeds", "company", "Agriculture",
         "findata/Companies/Agriculture/Acme_Feeds.md", "Acme_Feeds"),
    )
    conn.execute(
        "INSERT INTO entities (name, entity_type, sector_classification, "
        "file_path, normalized_name) VALUES (?,?,?,?,?)",
        ("Agriculture", "sector", None,
         "findata/Sectors/Agriculture.md", "Agriculture"),
    )
    conn.commit()
    conn.close()

    # Repoint the rebuild module at the synthetic tree + DB.
    monkeypatch.setattr(rns, "FINDATA", findata)
    monkeypatch.setattr(rns, "DEFAULT_DB", db_path)
    return db_path


def _count(con, match):
    return con.execute(
        "SELECT COUNT(*) FROM note_search WHERE note_search MATCH ?", (match,)
    ).fetchone()[0]


# --- tests ------------------------------------------------------------------


class TestRebuild:
    def test_rebuild_creates_table_and_indexes_docs(self, seeded_tree):
        stats = rns.rebuild(seeded_tree, write=True)
        # 1 company + 1 sector + 1 newsletter.
        assert stats["total_docs"] == 3
        assert stats["indexed"] == 3

        con = sqlite3.connect(str(seeded_tree))
        try:
            # Content search finds the shrimp-feed company by body text.
            assert _count(con, "shrimp") >= 1
            # Title + sector come from the entities table for entity docs.
            row = con.execute(
                "SELECT title, sector, doc_type FROM note_search "
                "WHERE note_search MATCH 'Acme_Feeds'"
            ).fetchone()
            assert row is not None
            assert row[0] == "Acme_Feeds"   # normalized_name from entities
            assert row[1] == "Agriculture"  # sector_classification
            assert row[2] == "company"
        finally:
            con.close()

    def test_rebuild_idempotent(self, seeded_tree):
        rns.rebuild(seeded_tree, write=True)
        s1 = rns.rebuild(seeded_tree, write=True)
        # A second rebuild must not duplicate rows (DELETE + reinsert).
        assert s1["indexed"] == 3
        con = sqlite3.connect(str(seeded_tree))
        try:
            # "shrimp" appears in 3 docs: company body, sector body, and the
            # newsletter body. A duplicate rebuild would double this to 6.
            assert _count(con, "shrimp") == 3
        finally:
            con.close()

    def test_standalone_fts_not_external_content(self, seeded_tree):
        rns.rebuild(seeded_tree, write=True)
        con = sqlite3.connect(str(seeded_tree))
        try:
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='note_search'"
            ).fetchone()[0]
            # porter tokenizer => standalone FTS, not external-content mode.
            assert "porter" in sql
            assert "tokenize" in sql
        finally:
            con.close()

    def test_newsletter_without_frontmatter_indexed(self, seeded_tree):
        rns.rebuild(seeded_tree, write=True)
        con = sqlite3.connect(str(seeded_tree))
        try:
            # The newsletter (no frontmatter, no entity row) is indexed by H1.
            rows = con.execute(
                "SELECT title, doc_type FROM note_search "
                "WHERE doc_type = 'chatter'"
            ).fetchall()
            assert len(rows) == 1
            # H1 was "# The Chatter: Aquaculture Edition".
            assert "Aquaculture Edition" in rows[0][0]
            # Its body (shrimp feed commentary) is searchable. Use a SQL AND
            # on doc_type (the FTS5 column-filter MATCH syntax would need the
            # column NAME, not the value).
            n = con.execute(
                "SELECT COUNT(*) FROM note_search "
                "WHERE note_search MATCH 'shrimp' AND doc_type = 'chatter'"
            ).fetchone()[0]
            assert n >= 1
        finally:
            con.close()


class TestEmbeddingColumn:
    """N5 item: note_search carries an `embedding` UNINDEXED column for hybrid
    ranking. Populated by default with a deterministic pseudo-embedding; an
    injected embed_fn overrides it (real-embedding path)."""

    def test_schema_has_embedding_column(self, seeded_tree):
        rns.rebuild(seeded_tree, write=True)
        con = sqlite3.connect(str(seeded_tree))
        try:
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='note_search'"
            ).fetchone()[0]
            assert "embedding UNINDEXED" in sql
        finally:
            con.close()

    def test_all_rows_get_pseudo_embedding(self, seeded_tree):
        stats = rns.rebuild(seeded_tree, write=True)
        assert stats["embedded"] == 3  # 1 company + 1 sector + 1 newsletter

        con = sqlite3.connect(str(seeded_tree))
        try:
            rows = con.execute(
                "SELECT embedding FROM note_search ORDER BY file_path"
            ).fetchall()
            assert len(rows) == 3
            for (emb,) in rows:
                assert emb is not None
                vec = json.loads(emb)
                assert len(vec) == 64
        finally:
            con.close()

    def test_injected_embed_fn_is_used(self, seeded_tree):
        def tiny_embed(text):
            return [1.0, 0.0] if "Acme" in text else [0.0, 1.0]

        stats = rns.rebuild(seeded_tree, write=True, embed_fn=tiny_embed)
        assert stats["embedded"] == 3

        con = sqlite3.connect(str(seeded_tree))
        try:
            row = con.execute(
                "SELECT embedding FROM note_search WHERE title = 'Acme_Feeds'"
            ).fetchone()
            assert json.loads(row[0]) == [1.0, 0.0]
        finally:
            con.close()

    def test_embed_fn_failure_keeps_row_searchable(self, seeded_tree):
        def broken_embed(text):
            raise RuntimeError("embedder down")

        stats = rns.rebuild(seeded_tree, write=True, embed_fn=broken_embed)
        # No crash; rows still indexed, just without embeddings.
        assert stats["indexed"] == 3
        assert stats["embedded"] == 0
        con = sqlite3.connect(str(seeded_tree))
        try:
            row = con.execute(
                "SELECT title FROM note_search WHERE note_search MATCH 'shrimp'"
            ).fetchone()
            assert row is not None
        finally:
            con.close()

    def test_migrates_legacy_table_without_embedding(self, seeded_tree):
        """A pre-embedding note_search (5 columns) must be dropped + recreated
        with the embedding column, not error on the 6-column INSERT."""
        # First build a legacy 5-column table.
        con = sqlite3.connect(str(seeded_tree))
        try:
            con.execute(
                "CREATE VIRTUAL TABLE note_search USING fts5("
                "doc_type, file_path UNINDEXED, title, sector, content, "
                "tokenize = 'porter unicode61')"
            )
            con.execute(
                "INSERT INTO note_search (doc_type, file_path, title, sector, content) "
                "VALUES ('company', 'findata/Companies/Agriculture/Acme_Feeds.md', "
                "'Acme_Feeds', 'Agriculture', 'old body')"
            )
            con.commit()
        finally:
            con.close()

        stats = rns.rebuild(seeded_tree, write=True)
        assert stats["migrated"] is True
        assert stats["indexed"] == 3

        con = sqlite3.connect(str(seeded_tree))
        try:
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='note_search'"
            ).fetchone()[0]
            assert "embedding" in sql
            # Old row gone (rebuilt from files).
            n = con.execute("SELECT COUNT(*) FROM note_search").fetchone()[0]
            assert n == 3
        finally:
            con.close()

    def test_rebuild_is_idempotent_with_embeddings(self, seeded_tree):
        rns.rebuild(seeded_tree, write=True)
        s2 = rns.rebuild(seeded_tree, write=True)
        assert s2["migrated"] is False
        assert s2["indexed"] == 3
        con = sqlite3.connect(str(seeded_tree))
        try:
            assert con.execute(
                "SELECT COUNT(*) FROM note_search"
            ).fetchone()[0] == 3
        finally:
            con.close()
