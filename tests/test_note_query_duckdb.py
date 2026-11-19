"""DuckDB-native note_search sidecar query surface (note_search carve-out S2).

Exercises the engine-agnostic note_query API over the `.duckdb` sidecar:
- ``bm25_hits`` dispatch → native ``match_bm25``;
- ``note_snippet_highlighted`` → the <mark> snippet() contract API search
  returns (DuckDB native FTS has no snippet()/highlight());
- ``search_notes_duckdb`` → the /api/search candidate generator (AND-first
  /OR-fill, NOTE-level dedup, OR-space total_count, legacy row contract).

Hermetic: tmp findata/ tree + tmp research.db (entity lookups) + tmp
`.duckdb` sidecar, fake embedder — same shape as
tests/test_rebuild_note_search_duckdb.py fixtures.
"""

import sqlite3

import pytest

from helpers.core.vec_codec import load_vec  # noqa: E402
from helpers.maintenance import rebuild_note_search as rns  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import ENTITIES_DDL  # noqa: E402
from helpers.misc import note_query as nq  # noqa: E402


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


@pytest.fixture
def sidecar(seeded_tree, tmp_path, monkeypatch, fake_local):
    duckdb_path = tmp_path / "note_search.duckdb"
    monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
    rns.rebuild(duckdb_path, write=True)
    return duckdb_path


def _open(path):
    conn = rns.connect_note_db(path)
    return conn


class TestBm25HitsDuckDB:
    def test_routes_by_suffix_and_finds_shrimp(self, sidecar):
        conn = _open(sidecar)
        try:
            hits = nq.bm25_hits(conn, "shrimp", limit=5)
            assert hits
            assert all(h["path"] for h in hits)
            assert all(h["score"] >= 0 for h in hits)  # native score, higher-is-better
        finally:
            conn.close()

    def test_no_match_returns_empty(self, sidecar):
        conn = _open(sidecar)
        try:
            assert nq.bm25_hits(conn, "zzzzqqqq", limit=5) == []
        finally:
            conn.close()


class TestNoteSnippetHighlighted:
    def test_wraps_matching_token_in_mark(self):
        out = nq.note_snippet_highlighted("Leading shrimp feed manufacturer.", "shrimp")
        assert "<mark>shrimp</mark>" in out

    def test_ellipsizes_edges(self):
        body = ("filler " * 50) + "cattle feed" + (" filler" * 40)
        out = nq.note_snippet_highlighted(body, "cattle")
        assert out.startswith("… ")
        assert out.endswith(" …")

    def test_no_match_returns_plain_window(self):
        out = nq.note_snippet_highlighted("plain prose here", "missing")
        assert "<mark>" not in out

    def test_empty_content_returns_empty(self):
        assert nq.note_snippet_highlighted("", "x") == ""


class TestSearchNotesDuckDB:
    def test_candidate_rows_carry_legacy_contract(self, sidecar):
        conn = _open(sidecar)
        try:
            rows, total = nq.search_notes_duckdb(conn, "shrimp", limit=10, offset=0)
            assert total >= 1
            assert rows
            for r in rows:
                assert len(r) == 9
                # (doc_type, file_path, title, sector, embedding, score,
                #  highlighted snippet, section_title, anchor)
                assert r[0] in ("company", "sector", "chatter")
                assert r[1].startswith("findata/")
                assert "<mark>shrimp</mark>" in r[6].casefold() or "<mark>Shrimp</mark>" in r[6]
            # NOTE-level dedup: one row per file_path.
            fps = [r[1] for r in rows]
            assert len(fps) == len(set(fps))
        finally:
            conn.close()

    def test_and_fill_flips_to_or(self, sidecar):
        conn = _open(sidecar)
        try:
            # "cattle feed" co-occurs in the company note alone ("cattle
            # feed"), so AND and OR behave the same; "fish shrimp" appears
            # across different sections/notes -> AND may be empty and OR
            # must fill the page.
            rows, total = nq.search_notes_duckdb(conn, "fish shrimp", limit=10, offset=0)
            assert total >= 1
            assert rows
        finally:
            conn.close()

    def test_doc_type_filter(self, sidecar):
        conn = _open(sidecar)
        try:
            rows, total = nq.search_notes_duckdb(conn, "shrimp", limit=10, doc_type="company")
            assert rows
            assert all(r[0] == "company" for r in rows)
            _, total_all = nq.search_notes_duckdb(conn, "shrimp", limit=10)
            assert total < total_all
        finally:
            conn.close()

    def test_no_match(self, sidecar):
        conn = _open(sidecar)
        try:
            rows, total = nq.search_notes_duckdb(conn, "zzzzqqqq", limit=10)
            assert rows == []
            assert total == 0
        finally:
            conn.close()

    def test_embedding_blob_present(self, sidecar):
        conn = _open(sidecar)
        try:
            rows, _ = nq.search_notes_duckdb(conn, "shrimp", limit=10)
            assert len(load_vec(rows[0][4])) == 64
        finally:
            conn.close()


class TestS2FunnelRouting:
    """S2: note_query's metadata probes route through the sanctioned funnel.

    The .duckdb sidecar opens DuckDB under every selector (extension wins);
    the legacy SQLite .db stays on the byte-identical default branch. Both
    metadata helpers must report the same row_count/dims/model on each
    engine, and ``_is_duckdb`` must detect the raw sidecar connection.
    """

    def test_probe_helpers_route_both_engines(self, seeded_tree, tmp_path, monkeypatch, fake_local):
        # DuckDB sidecar via the real rebuild (writes note_search_info stamps).
        sidecar = tmp_path / "note_search.duckdb"
        monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
        rns.rebuild(sidecar, write=True)

        rc, dims = nq._index_count_dims(sidecar)
        assert rc >= 1
        assert dims == 64
        assert nq._stored_note_model(sidecar) == "pseudo-test"

        # _is_duckdb must detect the raw connection returned by open_note_index.
        conn, engine = nq.open_note_index(sidecar)
        try:
            assert engine == "duckdb"
            assert nq._is_duckdb(conn) is True
        finally:
            conn.close()

    def test_probe_helpers_sqlite_branch(self, seeded_tree, tmp_path, monkeypatch, fake_local):
        # Legacy SQLite store via the real FTS5 rebuild path; the funnel's
        # default SQLite branch must stay byte-identical (no raised error).
        db = tmp_path / "research.db"
        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.execute(ENTITIES_DDL)
        conn.commit()
        conn.close()
        monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
        rns.rebuild(db, write=True)

        rc, dims = nq._index_count_dims(db)
        assert rc >= 1
        assert dims == 64
        assert nq._stored_note_model(db) == "pseudo-test"

        conn2, engine = nq.open_note_index(db)
        try:
            assert engine == "sqlite"
            assert nq._is_duckdb(conn2) is False
        finally:
            conn2.close()


class TestBm25OnlyIndependence:
    def test_bm25_ranks_rows_with_null_embeddings(self, sidecar):
        """bm25-only must work even when embeddings are absent: rows with
        NULL embedding still rank lexically and the candidate rows carry
        the NULL blob without error."""
        import duckdb

        con = duckdb.connect(str(sidecar))
        con.execute("UPDATE note_search SET embedding = NULL")
        con.close()
        conn = _open(sidecar)
        try:
            rows, total = nq.search_notes_duckdb(conn, "shrimp", limit=10)
            assert rows
            assert total >= 1
            assert all(r[4] is None for r in rows)  # zero embeddings, still hit
        finally:
            conn.close()


class TestRebuildCheckModes:
    def test_check_mode_no_stamp(self, sidecar, tmp_path):
        """--check must not write: stamps, mtime and parquet-visible state
        all stay untouched."""
        import duckdb

        before = duckdb.connect(str(sidecar), read_only=True)
        before_info = before.execute(
            "SELECT key, value FROM note_search_info ORDER BY key"
        ).fetchall()
        before.close()
        mtime_before = sidecar.stat().st_mtime_ns

        stats = rns.rebuild(sidecar, write=False)
        assert stats.get("index_stale") is False

        after = duckdb.connect(str(sidecar), read_only=True)
        after_info = after.execute(
            "SELECT key, value FROM note_search_info ORDER BY key"
        ).fetchall()
        after.close()
        assert after_info == before_info
        assert sidecar.stat().st_mtime_ns == mtime_before

    def test_stale_on_new(self, sidecar, tmp_path):
        """A doc added after the build shows as stale_new in check mode,
        then clears after a real rebuild."""
        new_file = tmp_path / "findata" / "Companies" / "Newcomer.md"
        new_file.write_text("---\ntitle: Newcomer\n---\nNewcomer body shrimp.\n")

        stats = rns.rebuild(sidecar, write=False)
        assert stats.get("index_stale") is True
        assert len(stats.get("stale_new", [])) >= 1

        rns.rebuild(sidecar, write=True)
        stats2 = rns.rebuild(sidecar, write=False)
        assert stats2.get("index_stale") is False


class TestLegacyRollback:
    def test_legacy_sqlite_store_routes_and_searches(
        self, seeded_tree, tmp_path, monkeypatch, fake_local
    ):
        """The sqlite rollback route stays green: rebuild into a research.db
        style store, then bm25_hits/open_note_index/search() all route to
        the FTS5 engine."""
        db_path = tmp_path / "research.db"
        # the legacy store IS the entity store — seed entities before rebuild
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute(ENTITIES_DDL)
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
        monkeypatch.setattr(rns, "resolve_embedder", lambda: (_pseudo_embed, 64, "pseudo-test"))
        rns.rebuild(db_path, write=True)

        conn = rns.connect_note_db(db_path)
        try:
            hits = nq.bm25_hits(conn, "shrimp", limit=5)
            assert hits
            # legacy convention: FTS5 bm25() is lower-is-better (raw < 0);
            # the API negates it. Micro-corpus ranks sit near -1e-6, so
            # assert the sign/negation relationship, not a magnitude bound.
            raw = conn.execute(
                "SELECT bm25(note_search) FROM note_search "
                "WHERE file_path = ? AND note_search MATCH ? LIMIT 1",
                (hits[0]["path"], "shrimp"),
            ).fetchone()[0]
            assert raw < 0
            assert abs(hits[0]["score"] + raw) < 1e-9
        finally:
            conn.close()

        conn2, engine = nq.open_note_index(db_path)
        assert engine == "sqlite"
        conn2.close()

        res = nq.search(db_path, "shrimp", limit=3)
        assert res["results"]
        assert res["mode"] in ("hybrid", "bm25")
