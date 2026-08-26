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


pytestmark = [pytest.mark.integration]


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


# --------------------------------------------------------------------------- #
# local_embeddings (2026-08-20): the resolver picks the local bge model when  #
# available, threads its dims into the vec0 mirror, and self-heals a          #
# dims change on the next rebuild. Fakes stand in for the model (hermetic).   #
# --------------------------------------------------------------------------- #

def _fake_384(text: str) -> list[float]:
    """Deterministic fake 384-dim unit vector (position 0 = text length)."""
    import math as _math

    v = [0.0] * 384
    v[len(text) % 384] = 1.0
    n = _math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


@pytest.fixture
def fake_local(monkeypatch):
    """Make local_embedder claim availability + serve fake vectors."""
    from helpers.core import local_embedder as LE

    seen: list[str] = []
    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _fake_384)
    monkeypatch.setattr(LE, "embed_query",
                        lambda t: _fake_384("Q:" + t))
    monkeypatch.setattr(LE, "embed_documents",
                        lambda texts: [_fake_384(t) for t in texts])
    monkeypatch.setattr(LE, "_seen", seen, raising=False)
    return LE


class TestLocalEmbedderWiring:
    def test_resolve_embedder_picks_local(self, fake_local):
        fn, dims, label = rns.resolve_embedder()
        assert fn is fake_local.embed_document
        assert dims == 384
        assert label == "bge-small-en-v1.5"

    def test_resolve_embedder_falls_back_with_warning(self, seeded_tree, capsys, monkeypatch):
        # conftest autouse pin has available()->False here; the pseudo
        # fallback must announce itself exactly once no matter how many
        # resolutions happen. (The once-flag is module state: reset it so
        # earlier tests in this process don't consume the warning.)
        monkeypatch.setattr(rns, "_pseudo_warned", False)
        for _ in range(3):
            fn, dims, label = rns.resolve_embedder()
            assert dims == 64
            assert label.startswith("dry-run-v")
            assert fn("x")  # callable, deterministic
        err = capsys.readouterr().err
        assert err.count("WARNING") == 1

    def test_rebuild_uses_local_dims_and_label(self, seeded_tree, fake_local):
        stats = rns.rebuild(seeded_tree, write=True)
        assert stats["embed_model"] == "bge-small-en-v1.5"
        con = sqlite3.connect(str(seeded_tree))
        try:
            rows = con.execute("SELECT embedding FROM note_search").fetchall()
            assert len(rows) == 3
            for (emb,) in rows:
                assert len(json.loads(emb)) == 384
        finally:
            con.close()

    def test_vec_mirror_follows_dims_change(self, seeded_tree, fake_local, monkeypatch):
        """Index at 384 with the local model, then a machine where the model
        is gone (pseudo 64): the rebuild must recreate the vec0 table at 64 —
        not silently keep serving a FLOAT[384] table the 64-dim sync can't
        write into."""
        from helpers.core import vec_search as VS

        rns.rebuild(seeded_tree, write=True)
        # vec sidecar lives next to the db file; query via a vec-loaded conn
        import sqlite_vec

        def _vec_conn():
            c = sqlite3.connect(str(seeded_tree))
            c.enable_load_extension(True)
            c.load_extension(sqlite_vec.loadable_path())
            c.enable_load_extension(False)
            VS._attach_vec_db(c)
            return c

        c = _vec_conn()
        try:
            assert VS.stored_dims(c) == 384
            n384 = c.execute(f"SELECT COUNT(*) FROM {VS.qualified()}").fetchone()[0]  # noqa: S608
        finally:
            c.close()
        assert n384 == 3

        # Model gone -> pseudo rebuild at 64.
        monkeypatch.setattr(fake_local, "available", lambda: False)
        stats = rns.rebuild(seeded_tree, write=True)
        assert stats["embed_model"].startswith("dry-run-v")

        c = _vec_conn()
        try:
            assert VS.stored_dims(c) == 64
            n64 = c.execute(f"SELECT COUNT(*) FROM {VS.qualified()}").fetchone()[0]  # noqa: S608
        finally:
            c.close()
        assert n64 == 3

    def test_stored_embed_dims_gate_helper(self, seeded_tree):
        rns.rebuild(seeded_tree, write=True)
        con = sqlite3.connect(str(seeded_tree))
        try:
            assert rns.stored_embed_dims(con) == 64  # pseudo under autouse pin
        finally:
            con.close()

    def test_embed_cache_cold_then_warm(self, seeded_tree, fake_local):
        """Q3 cache: a cold full rebuild embeds every doc once; a warm
        rebuild with unchanged docs re-embeds NOTHING; an edited doc
        re-embeds exactly itself. Vectors are identical across runs (the
        cache round-trips JSON faithfully)."""
        s1 = rns.rebuild(seeded_tree, write=True)
        assert s1["embed_cache_misses"] == 3
        assert s1["embed_cache_hits"] == 0

        con = sqlite3.connect(str(seeded_tree))
        try:
            cold = con.execute(
                "SELECT file_path, embedding FROM note_search ORDER BY file_path"
            ).fetchall()
        finally:
            con.close()

        s2 = rns.rebuild(seeded_tree, write=True)
        assert s2["embed_cache_hits"] == 3
        assert s2["embed_cache_misses"] == 0

        con = sqlite3.connect(str(seeded_tree))
        try:
            warm = con.execute(
                "SELECT file_path, embedding FROM note_search ORDER BY file_path"
            ).fetchall()
        finally:
            con.close()
        assert warm == cold  # byte-identical vectors via the cache

        co = rns.FINDATA / "Companies" / "Agriculture" / "Acme_Feeds.md"
        co.write_text(co.read_text(encoding="utf-8") + "\nNew product line.\n",
                      encoding="utf-8")
        s3 = rns.rebuild(seeded_tree, write=True, incremental=True)
        assert s3["embed_cache_misses"] == 1  # only the edited doc
        # P2.2: unchanged docs are CARRIED from note_search on the
        # incremental path (mtime match) — cheaper than even a cache hit;
        # the cache is only consulted for docs that actually changed.
        assert s3["embed_cache_hits"] == 0

    def test_check_mode_prewarms_cache(self, seeded_tree, fake_local):
        """--check (write=False) must PERSIST cache rows: the documented
        pre-warm flow depends on it. Regression: inserts used to roll back
        on close because only the writing transaction committed them."""
        from helpers.core import vec_search as VS

        rns.rebuild(seeded_tree, write=False)
        con = sqlite3.connect(str(seeded_tree))
        try:
            VS._attach_vec_db(con)
            n = con.execute(
                "SELECT COUNT(*) FROM vecdb.note_search_emb_cache"
            ).fetchone()[0]
        finally:
            con.close()
        assert n == 3
        # The applying rebuild then hits every entry.
        s = rns.rebuild(seeded_tree, write=True)
        assert s["embed_cache_hits"] == 3
        assert s["embed_cache_misses"] == 0

    def test_embed_cache_keyed_by_model_label(self, seeded_tree, fake_local, monkeypatch):
        """A model swap must re-embed even unchanged docs — the cache key
        includes the model label, so a new label never serves another
        model's vectors."""
        from helpers.core import vec_search as VS

        rns.rebuild(seeded_tree, write=True)  # cache under the bge label
        monkeypatch.setattr(fake_local, "MODEL_ID", "bge-small-en-v1.5-tmp")
        s2 = rns.rebuild(seeded_tree, write=True)
        assert s2["embed_cache_misses"] == 3  # label changed -> no hits
        assert s2["embed_cache_hits"] == 0

        con = sqlite3.connect(str(seeded_tree))
        try:
            VS._attach_vec_db(con)
            groups = dict(con.execute(
                "SELECT model, COUNT(*) FROM vecdb.note_search_emb_cache "
                "GROUP BY model"
            ).fetchall())
        finally:
            con.close()
        assert groups == {"bge-small-en-v1.5": 3, "bge-small-en-v1.5-tmp": 3}

    def test_generation_bump_apply_only(self, seeded_tree, fake_local):
        """B4 (sql_capability_unlocks): note_search is an FTS5 virtual
        table and can't carry the entities/graph_edges generation
        triggers, so the APPLY path bumps db_meta.generation after the
        FTS commit; --check (whose only writes are sidecar cache rows)
        never does."""
        from helpers.core.db import get_generation

        con = sqlite3.connect(str(seeded_tree))
        con.execute(
            "CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        con.execute("INSERT INTO db_meta VALUES ('generation', '50')")
        con.commit()
        con.close()

        # --check: sidecar cache may commit, the generation must not move.
        rns.rebuild(seeded_tree, write=False)
        con = sqlite3.connect(str(seeded_tree))
        assert get_generation(con) == 50
        con.close()

        # apply: exactly the one writer-side increment (nothing here writes
        # entities/graph_edges, so no trigger bumps mix in).
        stats = rns.rebuild(seeded_tree, write=True)
        con = sqlite3.connect(str(seeded_tree))
        assert get_generation(con) == 51
        con.close()
        assert stats["generation_bumped"] == 51


# --------------------------------------------------------------------------- #
# A1: sqlite-vec mirror table (note_search_vec) maintenance                  #
# --------------------------------------------------------------------------- #

class TestVecMirror:
    """The rebuild keeps the vec0 mirror in lock-step with the FTS table."""

    @staticmethod
    def _vec_conn(db_path):
        """Raw connection WITH sqlite-vec loaded (the module is per-conn —
        querying a vec0 table on a bare connection raises 'no such module')."""
        import sqlite_vec

        from helpers.core.vec_search import _attach_vec_db

        conn = sqlite3.connect(str(db_path))
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        conn.enable_load_extension(False)
        _attach_vec_db(conn)  # the vec0 table lives in the sidecar (vecdb)
        return conn


    def test_full_rebuild_populates_vec_table(self, seeded_tree):
        import helpers.maintenance.rebuild_note_search as R

        stats = R.rebuild(R.DEFAULT_DB)
        assert stats["vec_rows"] == 3  # 1 company + 1 sector + 1 newsletter
        conn = self._vec_conn(R.DEFAULT_DB)
        try:
            from helpers.core.vec_search import qualified

            n = conn.execute(
                f"SELECT COUNT(*) FROM {qualified()}"  # noqa: S608  # qualified() constant
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 3

    def test_incremental_rebuild_updates_vec_rows(self, seeded_tree):
        import helpers.maintenance.rebuild_note_search as R

        R.rebuild(R.DEFAULT_DB)
        # Touch one note so its fingerprint changes.
        co = R.FINDATA / "Companies" / "Agriculture" / "Acme_Feeds.md"
        co.write_text(co.read_text(encoding="utf-8") + "\nExtra line.\n", encoding="utf-8")
        stats = R.rebuild(R.DEFAULT_DB, incremental=True)
        assert stats["mode"] == "incremental"
        assert stats["upserts"] == 1
        assert stats["vec_rows"] == 1  # only the changed doc re-mirrored

    def test_incremental_noop_carries_rows(self, seeded_tree):
        """P2.2: a no-change incremental run upserts/deletes NOTHING (rows
        are carried from note_search on mtime match, cache untouched)."""
        import helpers.maintenance.rebuild_note_search as R

        R.rebuild(R.DEFAULT_DB)
        stats = R.rebuild(R.DEFAULT_DB, incremental=True)
        assert stats["mode"] == "incremental"
        assert stats["upserts"] == 0
        assert stats["deletes"] == 0
        # No cache traffic at all (pseudo embedder -> no cache; real model
        # -> carried rows never consult it).
        assert stats.get("embed_cache_hits", 0) == 0
        assert stats.get("embed_cache_misses", 0) == 0

    def test_incremental_entity_side_sector_change_re_upserts(self, seeded_tree):
        """P2.2 guard: a DB-side sector reclassification (file untouched,
        mtime unchanged) must NOT be carried stale — the entities-table
        title/sector check forces a reprocess and the hash diff upserts."""
        import helpers.maintenance.rebuild_note_search as R
        import sqlite3

        R.rebuild(R.DEFAULT_DB)
        con = sqlite3.connect(str(R.DEFAULT_DB))
        try:
            con.execute(
                "UPDATE entities SET sector_classification = 'Chemicals' "
                "WHERE file_path IS NOT NULL AND entity_type = 'company'")
            con.commit()
        finally:
            con.close()
        stats = R.rebuild(R.DEFAULT_DB, incremental=True)
        assert stats["upserts"] >= 1
        con = sqlite3.connect(str(R.DEFAULT_DB))
        try:
            sec = con.execute(
                "SELECT sector FROM note_search WHERE doc_type = 'company' "
                "LIMIT 1").fetchone()[0]
        finally:
            con.close()
        assert sec == "Chemicals"

    def test_incremental_delete_removes_vec_row(self, seeded_tree):
        import helpers.maintenance.rebuild_note_search as R

        R.rebuild(R.DEFAULT_DB)
        (R.FINDATA / "Sectors" / "Agriculture.md").unlink()
        stats = R.rebuild(R.DEFAULT_DB, incremental=True)
        assert stats["deletes"] == 1
        from helpers.core.vec_search import qualified

        conn = self._vec_conn(R.DEFAULT_DB)
        try:
            fps = {
                r[0]
                for r in conn.execute(
                    f"SELECT file_path FROM {qualified()}"  # noqa: S608  # qualified() constant
                )
            }
        finally:
            conn.close()
        assert not any("Agriculture.md" in fp for fp in fps)

    def test_vec_similarity_matches_python_cosine(self, seeded_tree):
        """KNN similarity over the mirrored table == float64 cosine of the
        JSON column (the response contract must not change with A1)."""
        import json as _json
        import math

        import helpers.maintenance.rebuild_note_search as R
        from helpers.core.vec_search import knn_similarities

        R.rebuild(R.DEFAULT_DB)
        conn = self._vec_conn(R.DEFAULT_DB)
        try:
            rows = conn.execute(
                "SELECT file_path, embedding FROM note_search "
                "WHERE embedding IS NOT NULL"
            ).fetchall()
            q = R._default_embed("Acme Feeds shrimp feed")
            got = knn_similarities(conn, q, k=len(rows), dims=R._PSEUDO_DIMS)
            assert got is not None
            for fp, emb in rows:
                vec = _json.loads(emb)
                dot = sum(x * y for x, y in zip(q, vec))
                nq = math.sqrt(sum(x * x for x in q))
                nv = math.sqrt(sum(x * x for x in vec))
                expect = dot / (nq * nv)
                assert got[fp] == pytest.approx(expect, abs=1e-6)
        finally:
            conn.close()

class TestStalenessCheck:
    """--check drift reporting (2026-08-26): note_search must report the
    same changed/new/deleted breakdown + refresh command as rebuild_doc_search
    / rebuild_script_search, and --check must exit 1 on drift (house gate
    doctrine — the advisory note-search-check step previously passed
    silently even when the index was stale)."""

    CO = "findata/Companies/Agriculture/Acme_Feeds.md"

    def _rebuild(self, db_path):
        rns.rebuild(db_path)

    def test_check_fresh_reports_fresh_exit0(self, seeded_tree, fake_local, capsys):
        db_path = seeded_tree
        self._rebuild(db_path)
        stats = rns.rebuild(db_path, write=False)
        assert stats["index_stale"] is False
        assert stats["stale_new"] == [] and stats["stale_changed"] == []
        assert stats["stale_deleted"] == []
        rc = rns.main(["--check"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "index state: FRESH" in err

    def test_check_detects_changed_note(self, seeded_tree, fake_local, capsys):
        db_path = seeded_tree
        self._rebuild(db_path)
        p = rns.FINDATA / "Companies" / "Agriculture" / "Acme_Feeds.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nprobe edit\n",
                     encoding="utf-8")
        stats = rns.rebuild(db_path, write=False)
        assert stats["index_stale"] is True
        assert stats["stale_changed"] == [self.CO]
        assert stats["stale_new"] == [] and stats["stale_deleted"] == []
        rc = rns.main(["--check"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "index state: STALE" in err
        assert f"changed  {self.CO}" in err
        assert "refresh: python3 helpers/maintenance/rebuild_note_search.py" in err

    def test_check_detects_new_note(self, seeded_tree, fake_local, capsys):
        db_path = seeded_tree
        self._rebuild(db_path)
        (rns.FINDATA / "Companies" / "Agriculture" / "Probe_Co.md").write_text(
            "# Probe Co\n\nbody\n", encoding="utf-8")
        stats = rns.rebuild(db_path, write=False)
        assert stats["stale_new"] == ["findata/Companies/Agriculture/Probe_Co.md"]
        rc = rns.main(["--check"])
        assert rc == 1

    def test_check_detects_deleted_note(self, seeded_tree, fake_local):
        db_path = seeded_tree
        self._rebuild(db_path)
        (rns.FINDATA / "Sectors" / "Agriculture.md").unlink()
        stats = rns.rebuild(db_path, write=False)
        assert stats["stale_deleted"] == ["findata/Sectors/Agriculture.md"]
        assert rns.main(["--check"]) == 1

    def test_check_detects_db_side_sector_change(self, seeded_tree, fake_local):
        """Fingerprint hashes title+sector from the entities table, so a
        DB-side reclassify flags the note changed even with an untouched
        file (the documented P2.1 property, now visible in --check)."""
        db_path = seeded_tree
        self._rebuild(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE entities SET sector_classification='Aquaculture' "
            "WHERE file_path=?", (self.CO,))
        conn.commit()
        conn.close()
        stats = rns.rebuild(db_path, write=False)
        assert stats["stale_changed"] == [self.CO]

    def test_write_path_reports_was_stale(self, seeded_tree, fake_local, capsys):
        db_path = seeded_tree
        self._rebuild(db_path)
        p = rns.FINDATA / "Companies" / "Agriculture" / "Acme_Feeds.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nprobe edit\n",
                     encoding="utf-8")
        assert rns.main([]) == 0  # applying rebuild
        err = capsys.readouterr().err
        assert "index was STALE before this rebuild: 1 changed" in err
        assert rns.main(["--check"]) == 0  # converged

