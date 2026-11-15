"""
Tests for the DuckDB-native note_search sidecar (S1 of the note_search
carve-out): helpers/maintenance/rebuild_note_search.py's ``rebuild_note_duckdb``
path (``--db memory/note_search.duckdb``).

The sidecar reuses the same walk/section pipeline as the legacy FTS5 rebuild,
but writes a native DuckDB store (``note_search`` + meta/info stamps) and
builds ``create_fts_index`` over a composed ``searchtext`` column. These tests
build a tiny synthetic findata/ tree under tmp_path, point the rebuild module's
FINDATA + DEFAULT_DB (research.db for entity lookups) at it, rebuild the
sidecar, and assert content hits via native match_bm25, stamps, check-mode
freshness, and idempotency.
"""

import sqlite3

import pytest

from helpers.core.vec_codec import load_vec  # noqa: E402
from helpers.maintenance import rebuild_note_search as rns  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import ENTITIES_DDL  # noqa: E402


pytestmark = [pytest.mark.integration]


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
    """Synthetic findata/ tree + entities research.db; repoint the rebuild
    module's FINDATA + DEFAULT_DB at the tmp tree."""
    findata = tmp_path / "findata"
    (findata / "Companies" / "Agriculture").mkdir(parents=True)
    (findata / "Sectors").mkdir(parents=True)
    (findata / "The_Chatter").mkdir(parents=True)

    (findata / "Companies" / "Agriculture" / "Acme_Feeds.md").write_text(_COMPANY, encoding="utf-8")
    (findata / "Sectors" / "Agriculture.md").write_text(_SECTOR, encoding="utf-8")
    (findata / "The_Chatter" / "Aquaculture_Edition.md").write_text(_NEWSLETTER, encoding="utf-8")

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(ENTITIES_DDL)
    conn.execute(
        "INSERT INTO entities (name, entity_type, sector_classification, "
        "file_path, normalized_name) VALUES (?,?,?,?,?)",
        (
            "Acme_Feeds",
            "company",
            "Agriculture",
            "findata/Companies/Agriculture/Acme_Feeds.md",
            "Acme_Feeds",
        ),
    )
    conn.execute(
        "INSERT INTO entities (name, entity_type, sector_classification, "
        "file_path, normalized_name) VALUES (?,?,?,?,?)",
        (
            "Agriculture",
            "sector",
            None,
            "findata/Sectors/Agriculture.md",
            "Agriculture",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(rns, "FINDATA", findata)
    monkeypatch.setattr(rns, "DEFAULT_DB", db_path)
    return db_path


def _pseudo_embed(text: str) -> list[float]:
    return [0.01] * 64


@pytest.fixture
def fake_local(monkeypatch):
    from helpers.core import local_embedder as LE

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _pseudo_embed)
    monkeypatch.setattr(LE, "embed_query", _pseudo_embed)
    monkeypatch.setattr(
        LE, "embed_documents_parallel", lambda texts: [_pseudo_embed(t) for t in texts]
    )
    return LE


class TestRebuildDuckDB:
    def test_rebuild_builds_sidecar_with_native_fts(
        self, seeded_tree, tmp_path, monkeypatch, fake_local
    ):
        duckdb_path = tmp_path / "note_search.duckdb"
        monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
        stats = rns.rebuild(duckdb_path, write=True)

        assert duckdb_path.exists()
        assert stats["indexed"] == 4  # company 1 + sector 1 + newsletter 2 sections
        assert stats["total_files"] == 3
        assert stats["mode"] == "full"

        con = rns.connect_note_db(duckdb_path)
        try:
            # Native FTS index exists + content search finds shrimp.
            n = con.execute(
                "SELECT COUNT(*) FROM note_search "
                "WHERE fts_main_note_search.match_bm25(id, 'shrimp') IS NOT NULL"
            ).fetchone()[0]
            assert n >= 1
            # Store shape: f32 BLOB embedding + composed searchtext.
            row = con.execute(
                "SELECT title, sector, doc_type, embedding, searchtext FROM note_search "
                "WHERE file_path = ? LIMIT 1",
                ("findata/Companies/Agriculture/Acme_Feeds.md",),
            ).fetchone()
            assert row is not None
            assert row[0] == "Acme_Feeds"
            assert row[1] == "Agriculture"
            assert row[2] == "company"
            assert "shrimp" in row[4]
            assert len(load_vec(row[3])) == 64
            # Stamps live inside the sidecar (research.db untouched by duckdb path).
            assert (
                con.execute(
                    "SELECT value FROM note_search_info WHERE key = 'embed_model'"
                ).fetchone()[0]
                == "pseudo-test"
            )
            assert (
                con.execute(
                    "SELECT value FROM note_search_info WHERE key = 'embed_dims'"
                ).fetchone()[0]
                == "64"
            )
            # Meta rows: one per file.
            assert con.execute("SELECT COUNT(*) FROM note_search_meta").fetchone()[0] == 3
        finally:
            con.close()

    def test_rebuild_idempotent(self, seeded_tree, tmp_path, monkeypatch, fake_local):
        duckdb_path = tmp_path / "note_search.duckdb"
        monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
        s1 = rns.rebuild(duckdb_path, write=True)
        s2 = rns.rebuild(duckdb_path, write=True)
        assert s2["indexed"] == s1["indexed"] == 4

    def test_check_freshness_after_build_and_drift(
        self, seeded_tree, tmp_path, monkeypatch, fake_local
    ):
        duckdb_path = tmp_path / "note_search.duckdb"
        monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
        rns.rebuild(duckdb_path, write=True)

        stats = rns.rebuild(duckdb_path, write=False)
        assert stats["index_stale"] is False

        # Touch a file -> content hash changes -> stale changed.
        changed = rns.FINDATA / "Sectors" / "Agriculture.md"
        changed.write_text(_SECTOR + "\nchanged", encoding="utf-8")
        stats = rns.rebuild(duckdb_path, write=False)
        assert stats["index_stale"] is True
        assert stats["stale_changed"] == ["findata/Sectors/Agriculture.md"]

    def test_check_missing_store_reports_all_new(self, seeded_tree, tmp_path):
        duckdb_path = tmp_path / "never_built.duckdb"
        stats = rns.rebuild(duckdb_path, write=False)
        assert stats["index_stale"] is True
        assert len(stats["stale_new"]) == 3
