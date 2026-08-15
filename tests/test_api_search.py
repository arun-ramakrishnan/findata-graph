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
# FK-link, but keep the column for realism) + the note_search FTS5 table.
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
    tokenize = 'porter unicode61'
);
"""

# (doc_type, file_path, title, sector, content)
_SEED = [
    (
        "company", "findata/Companies/Agriculture/Avanti_Feeds.md",
        "Avanti_Feeds", "Agriculture",
        "Leading shrimp feed and fish feed manufacturer. Aquaculture focus.",
    ),
    (
        "company", "findata/Companies/Agriculture/Sharat_Industries.md",
        "Sharat_Industries", "Agriculture",
        "Shrimp hatchery operations and cattle feed production.",
    ),
    (
        "sector", "findata/Sectors/Agriculture.md",
        "Agriculture", "",
        "Covers crops, livestock, and aquaculture including shrimp farming.",
    ),
    (
        "chatter", "findata/The_Chatter/Aquaculture_Edition.md",
        "The Chatter: Aquaculture Edition", "",
        "Shrimp feed revenues grew 20 percent in Q3. Strong demand for fish feed.",
    ),
    (
        "points_and_figures", "findata/Points_And_Figures/Roots.md",
        "Points & Figures: Roots", "",
        "Agri-input companies benefit from shrimp-feed export growth.",
    ),
]


@contextmanager
def _seeded_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO note_search (doc_type, file_path, title, sector, content) "
        "VALUES (?,?,?,?,?)",
        _SEED,
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
