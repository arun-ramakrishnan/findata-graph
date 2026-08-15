"""
Tests for the GET /api/search endpoint — free-text search over the note_search
FTS5 index (companies, sectors, super-sectors, and the newsletter corpora).

These tests isolate the endpoint logic: they seed a tiny note_search FTS table
directly (not via rebuild_note_search) and exercise the endpoint's query
handling, doc_type filter, pagination, the 503 index-missing guard, and the
400 paths for empty / malformed queries.

Mirrors the test_api_graph.py pattern: a _seeded_db context manager that
monkey-patches A.get_db_connection to a temp DB with row_factory=sqlite3.Row
(production shape), yielding a Flask test client.
"""

import sqlite3
from contextlib import contextmanager

import pytest

import app as A

# Minimal schema: the entities table (for FK-free isolation we don't actually
# FK-link, but keep the column for realism) + the note_search FTS5 table. The
# embedding column (UNINDEXED) is present so hybrid=true is exercised; a second
# schema (below) drops it to pin the graceful-degradation path.
_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    sector_classification TEXT,
    file_path TEXT
);
CREATE VIRTUAL TABLE note_search USING fts5(
    doc_type,
    file_path UNINDEXED,
    title,
    sector,
    content,
    embedding UNINDEXED,
    tokenize = 'porter unicode61'
);
"""

# Pre-embedding schema: no embedding column (hybrid must degrade gracefully).
_SCHEMA_NO_EMBEDDING = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    sector_classification TEXT,
    file_path TEXT
);
CREATE VIRTUAL TABLE note_search USING fts5(
    doc_type,
    file_path UNINDEXED,
    title,
    sector,
    content,
    tokenize = 'porter unicode61'
);
"""

# (doc_type, file_path, title, sector, content, embedding_json)
# Embeddings: real-ish 3-dim vectors. The two "feed" companies get near-identical
# vectors so cosine similarity meaningfully boosts them over the newsletters.
_SEED = [
    (
        "company", "findata/Companies/Agriculture/Avanti_Feeds.md",
        "Avanti_Feeds", "Agriculture",
        "Leading shrimp feed and fish feed manufacturer. Aquaculture focus.",
        "[1.0, 0.0, 0.0]",
    ),
    (
        "company", "findata/Companies/Agriculture/Sharat_Industries.md",
        "Sharat_Industries", "Agriculture",
        "Shrimp hatchery operations and cattle feed production.",
        "[0.99, 0.1, 0.0]",
    ),
    (
        "sector", "findata/Sectors/Agriculture.md",
        "Agriculture", "",
        "Covers crops, livestock, and aquaculture including shrimp farming.",
        "[0.5, 0.5, 0.5]",
    ),
    (
        "chatter", "findata/The_Chatter/Aquaculture_Edition.md",
        "The Chatter: Aquaculture Edition", "",
        "Shrimp feed revenues grew 20 percent in Q3. Strong demand for fish feed.",
        "[0.0, 1.0, 0.0]",
    ),
    (
        "points_and_figures", "findata/Points_And_Figures/Roots.md",
        "Points & Figures: Roots", "",
        "Agri-input companies benefit from shrimp-feed export growth.",
        "[0.0, 0.5, 0.9]",
    ),
]


@contextmanager
def _seeded_db(tmp_path, *, schema=None, with_embeddings=True):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema or (_SCHEMA if with_embeddings else _SCHEMA_NO_EMBEDDING))
    if with_embeddings:
        conn.executemany(
            "INSERT INTO note_search "
            "(doc_type, file_path, title, sector, content, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            _SEED,
        )
    else:
        conn.executemany(
            "INSERT INTO note_search "
            "(doc_type, file_path, title, sector, content) "
            "VALUES (?, ?, ?, ?, ?)",
            [r[:5] for r in _SEED],
        )
    conn.commit()
    conn.close()

    def _open():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row  # match production connect()
        return c

    saved = A.get_db_connection
    A.get_db_connection = _open  # ty: ignore[invalid-assignment]
    try:
        yield A.app.test_client()
    finally:
        A.get_db_connection = saved


@pytest.fixture
def client(tmp_path):
    with _seeded_db(tmp_path) as c:
        yield c


def _results(resp):
    return resp.get_json()["results"]


def _count(resp):
    return resp.get_json()["total_count"]


# --- tests ------------------------------------------------------------------


class TestSearch:
    def test_search_returns_content_hits_with_highlight(self, client):
        # "shrimp" is in all 5 seeded docs; "<mark>" highlighting is present.
        r = client.get("/api/search?q=shrimp")
        assert r.status_code == 200
        names = sorted(h["title"] for h in _results(r))
        assert names == [
            "Agriculture",
            "Avanti_Feeds",
            "Points & Figures: Roots",
            "Sharat_Industries",
            "The Chatter: Aquaculture Edition",
        ]
        # snippet carries <mark>...</mark> around the match.
        assert all("<mark>" in h["snippet"] for h in _results(r))
        # response shape keys.
        body = r.get_json()
        assert set(body) == {"results", "total_count", "limit", "offset"}

    def test_search_filter_by_doc_type(self, client):
        # type=company narrows to the 2 company docs mentioning shrimp.
        r = client.get("/api/search?q=shrimp&type=company")
        assert r.status_code == 200
        assert _count(r) == 2
        assert all(h["doc_type"] == "company" for h in _results(r))

    def test_search_filter_by_newsletter_type(self, client):
        # type=chatter narrows to the 1 newsletter doc.
        r = client.get("/api/search?q=shrimp&type=chatter")
        assert r.status_code == 200
        assert _count(r) == 1
        assert _results(r)[0]["doc_type"] == "chatter"

    def test_search_pagination(self, client):
        full = client.get("/api/search?q=feed&limit=50")
        total = _count(full)
        # page through 1 at a time; the union of titles must equal the full set
        # with no dupes and no missing rows.
        seen = []
        offset = 0
        while offset < total:
            page = client.get(f"/api/search?q=feed&limit=1&offset={offset}")
            assert _count(page) == total  # total is independent of pagination
            seen.extend(h["title"] for h in _results(page))
            offset += 1
        assert len(seen) == total
        assert len(set(seen)) == total  # no dupes

    def test_search_missing_index_returns_503(self, tmp_path):
        # A DB with NO note_search table -> 503, not 500.
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE entities (name TEXT)")
        conn.commit()
        conn.close()

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        saved = A.get_db_connection
        A.get_db_connection = _open  # ty: ignore[invalid-assignment]
        try:
            client = A.app.test_client()
            r = client.get("/api/search?q=anything")
            assert r.status_code == 503
            assert "not built" in r.get_json()["error"]
        finally:
            A.get_db_connection = saved

    def test_search_malformed_query_returns_400(self, client):
        # FTS5 MATCH raises on stray boolean operators; endpoint must 400.
        r = client.get("/api/search?q=AND%20OR")
        assert r.status_code == 400
        assert "invalid query syntax" in r.get_json()["error"]

    def test_search_empty_q_returns_400(self, client):
        r = client.get("/api/search?q=")
        assert r.status_code == 400
        assert "missing" in r.get_json()["error"]


class TestHybridSearch:
    """hybrid=true RRF-fuses BM25 with cosine similarity over stored embeddings.

    The 3-dim seed vectors are hand-picked: query-embedding is not used
    directly (the endpoint embeds q with its own pseudo-embedder), so these
    tests assert the SHAPE + plumbing (similarity present, ordering by RRF,
    graceful degradation), not exact scores.
    """

    def test_hybrid_returns_similarity_and_shape(self, client):
        r = client.get("/api/search?q=shrimp&hybrid=true&limit=20")
        assert r.status_code == 200
        body = r.get_json()
        assert body["total_count"] == 5
        assert body["limit"] == 20
        for hit in body["results"]:
            assert set(hit) == {
                "doc_type", "file_path", "title", "sector", "snippet",
                "similarity",
            }
            assert hit["similarity"] is not None
            assert isinstance(hit["similarity"], float)

    def test_plain_search_has_null_similarity(self, client):
        # No hybrid -> similarity field present but null (unchanged shape
        # otherwise), so api.ts SearchResult stays valid.
        r = client.get("/api/search?q=shrimp&limit=20")
        assert r.status_code == 200
        for hit in r.get_json()["results"]:
            assert hit["similarity"] is None

    def test_hybrid_reranks_versus_plain(self, client):
        # Same query: plain is pure BM25 (snippet rank order), hybrid re-orders.
        plain = client.get("/api/search?q=feed&limit=20").get_json()["results"]
        hybrid = client.get("/api/search?q=feed&hybrid=true&limit=20").get_json()["results"]
        plain_titles = [h["title"] for h in plain]
        hybrid_titles = [h["title"] for h in hybrid]
        assert len(plain_titles) == len(hybrid_titles) == 4  # "feed" in 4 docs
        # Same doc set, both rank-ordered (no dupes).
        assert sorted(plain_titles) == sorted(hybrid_titles)
        # RRF re-orders: the feed-vector-heavy docs move up vs pure BM25.
        assert hybrid_titles != plain_titles

    def test_hybrid_keeps_pagination_window(self, client):
        # limit+offset window: hybrid fetches top (limit+offset) then slices,
        # so a full page must still return exactly `limit` hits.
        r = client.get("/api/search?q=feed&hybrid=true&limit=3&offset=1")
        assert r.status_code == 200
        assert len(r.get_json()["results"]) == 3

    def test_hybrid_degrades_when_no_embedding_column(self, tmp_path):
        # Pre-embedding schema: hybrid=true must NOT 500 — it falls back to
        # pure FTS (similarity null), preserving the response contract.
        with _seeded_db(tmp_path, with_embeddings=False) as client:
            r = client.get("/api/search?q=shrimp&hybrid=true&limit=20")
            assert r.status_code == 200
            body = r.get_json()
            assert body["total_count"] == 5
            for hit in body["results"]:
                assert hit["similarity"] is None
                assert "snippet" in hit
