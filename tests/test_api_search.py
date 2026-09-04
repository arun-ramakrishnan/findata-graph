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


from tests.schema import ENTITIES_4COL  # noqa: E402
from tests.helpers import response_count  # noqa: E402

# Minimal schema: the entities table (for FK-free isolation we don't actually
# FK-link, but keep the column for realism) + the note_search FTS5 table. The
# embedding column (UNINDEXED) is present so hybrid=true is exercised; a second
# schema (below) drops it to pin the graceful-degradation path.
_SCHEMA = (
    ENTITIES_4COL
    + """
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
)

# Pre-embedding schema: no embedding column (hybrid must degrade gracefully).
_SCHEMA_NO_EMBEDDING = (
    ENTITIES_4COL
    + """
CREATE VIRTUAL TABLE note_search USING fts5(
    doc_type,
    file_path UNINDEXED,
    title,
    sector,
    content,
    tokenize = 'porter unicode61'
);
"""
)

# (doc_type, file_path, title, sector, content, embedding_json)
# Embeddings: real-ish 3-dim vectors. The two "feed" companies get near-identical
# vectors so cosine similarity meaningfully boosts them over the newsletters.
_SEED = [
    (
        "company",
        "findata/Companies/Agriculture/Avanti_Feeds.md",
        "Avanti_Feeds",
        "Agriculture",
        "Leading shrimp feed and fish feed manufacturer. Aquaculture focus.",
        "[1.0, 0.0, 0.0]",
    ),
    (
        "company",
        "findata/Companies/Agriculture/Sharat_Industries.md",
        "Sharat_Industries",
        "Agriculture",
        "Shrimp hatchery operations and cattle feed production.",
        "[0.99, 0.1, 0.0]",
    ),
    (
        "sector",
        "findata/Sectors/Agriculture.md",
        "Agriculture",
        "",
        "Covers crops, livestock, and aquaculture including shrimp farming.",
        "[0.5, 0.5, 0.5]",
    ),
    (
        "chatter",
        "findata/The_Chatter/Aquaculture_Edition.md",
        "The Chatter: Aquaculture Edition",
        "",
        "Shrimp feed revenues grew 20 percent in Q3. Strong demand for fish feed.",
        "[0.0, 1.0, 0.0]",
    ),
    (
        "points_and_figures",
        "findata/Points_And_Figures/Roots.md",
        "Points & Figures: Roots",
        "",
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

    from tests.helpers import flask_test_client  # noqa: E402

    with flask_test_client(db_path) as client:
        yield client


@pytest.fixture
def client(tmp_path):
    with _seeded_db(tmp_path) as c:
        yield c


@pytest.fixture(autouse=True)
def _three_dim_query_space(monkeypatch):
    """Pin the hybrid query embedder to the 3-dim space of this module's
    hand-crafted seed vectors (local_embeddings, 2026-08-20).

    The production resolver returns 384 (local bge) or 64 (pseudo); against
    3-dim seeds app.py's vector-space gate correctly degrades to BM25-only,
    which would silently switch every hybrid test onto the degraded path.
    The fixed [0, 1, 0] query keeps the seeded geometry deterministic:
    Chatter ([0,1,0]) is the exact-cosine winner for any query, Avanti
    ([1,0,0]) is near-orthogonal. Tests of the gate itself re-patch
    query_embedder to a mismatching width."""
    from helpers.maintenance import rebuild_note_search as RNS

    monkeypatch.setattr(RNS, "query_embedder", lambda: ((lambda text: [0.0, 1.0, 0.0]), 3))


def _results(resp):
    return resp.get_json()["results"]


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
        assert response_count(r) == 2
        assert all(h["doc_type"] == "company" for h in _results(r))

    def test_search_filter_by_newsletter_type(self, client):
        # type=chatter narrows to the 1 newsletter doc.
        r = client.get("/api/search?q=shrimp&type=chatter")
        assert r.status_code == 200
        assert response_count(r) == 1
        assert _results(r)[0]["doc_type"] == "chatter"

    def test_search_pagination(self, client):
        full = client.get("/api/search?q=feed&limit=50")
        total = response_count(full)
        # page through 1 at a time; the union of titles must equal the full set
        # with no dupes and no missing rows.
        seen = []
        offset = 0
        while offset < total:
            page = client.get(f"/api/search?q=feed&limit=1&offset={offset}")
            assert response_count(page) == total  # total is independent of pagination
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

        from tests.helpers import flask_test_client  # noqa: E402

        with flask_test_client(db_path) as client:
            r = client.get("/api/search?q=anything")
            assert r.status_code == 503
            assert "not built" in r.get_json()["error"]

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
                "doc_type",
                "file_path",
                "title",
                "sector",
                "snippet",
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


# --------------------------------------------------------------------------- #
# A1: the sqlite-vec KNN path (global re-rank) vs the Python-cosine fallback  #
# --------------------------------------------------------------------------- #
class TestHybridKnnPath:
    """Pins that hybrid uses the vec0 KNN map when available and keeps the
    exact response contract when it isn't."""

    def test_hybrid_calls_knn_and_uses_its_similarities(self, client, monkeypatch):
        """Spy on helpers.core.vec_search.knn_similarities: return a crafted
        map (Sharat first globally, Avanti deliberately UNRANKED) and assert
        it drives the response. Avanti keeps BM25 position 0 but loses the
        cosine leg (worst position) — the only asymmetric setup in which RRF
        can flip a rank-0 BM25 doc (a pure 0/1 position swap ties exactly)."""
        from helpers.core import vec_search as VS

        calls = []

        chatter_fp = "findata/The_Chatter/Aquaculture_Edition.md"
        roots_fp = "findata/Points_And_Figures/Roots.md"

        def fake_knn(conn, q_vec, k, dims):
            calls.append((k, dims))
            # Fixture BM25 page for q=feed is [Avanti, Chatter, Sharat,
            # Roots]. Craft: Chatter (BM25 idx 1) globally most-similar and
            # Avanti (BM25 idx 0) deliberately UNRANKED = worst cosine leg.
            return {chatter_fp: 0.9, roots_fp: 0.5}

        monkeypatch.setattr(VS, "knn_similarities", fake_knn)
        r = client.get("/api/search?q=feed&hybrid=true&limit=20")
        assert r.status_code == 200
        hits = r.get_json()["results"]
        assert calls, "KNN path was not exercised"
        assert calls[0][1] == 3  # dims = len(q_vec) from the module's query fixture
        assert calls[0][0] is None  # whole-corpus k
        by_fp = {h["file_path"]: h for h in hits}
        # Crafted similarities pass through verbatim (response contract).
        assert by_fp[chatter_fp]["similarity"] == 0.9
        assert by_fp[roots_fp]["similarity"] == 0.5
        # Chatter takes the top slot: BM25 rank 1 + KNN rank 0 gives
        # 1/62 + 1/61 = 0.03252, beating Avanti's BM25 rank 0 + worst
        # cosine position 1/61 + 1/63 = 0.03227 (knn_order length is 2).
        assert hits[0]["file_path"] == chatter_fp
        # Docs absent from the KNN map get similarity 0.0 (missing-embedding
        # contract preserved by the A1 path).
        assert by_fp["findata/Companies/Agriculture/Avanti_Feeds.md"]["similarity"] == 0.0
        assert by_fp["findata/Companies/Agriculture/Sharat_Industries.md"]["similarity"] == 0.0

    def test_hybrid_falls_back_identically_when_knn_unavailable(self, client, monkeypatch):
        """knn_similarities -> None must reproduce the pre-A1 Python-cosine
        ranking exactly (page-local RRF)."""
        from helpers.core import vec_search as VS

        monkeypatch.setattr(VS, "knn_similarities", lambda *a, **k: None)
        hybrid = client.get("/api/search?q=feed&hybrid=true&limit=20").get_json()["results"]
        for hit in hybrid:
            assert isinstance(hit["similarity"], float)

    def test_hybrid_knn_and_fallback_agree_on_order(self, client, monkeypatch):
        """Behavior preservation: forcing the KNN leg on (spy) vs off must
        not change the fused ordering for a NEUTRAL map — the A1 swap is a
        perf change, not a ranking change. Neutral = every page doc mapped
        in the same relative order the Python cosine would produce."""
        from helpers.core import vec_search as VS

        def _titles():
            r = client.get("/api/search?q=feed&hybrid=true&limit=20")
            return [h["title"] for h in r.get_json()["results"]]

        # The real Python fallback ranks Chatter first for q=feed (its
        # 3-dim seed vector wins the page-local cosine). A KNN map that
        # agrees on the winner must rank Chatter 0 AND push Avanti to KNN
        # rank 2 (Chatter 0.8 > Sharat 0.5 > Avanti 0.3): a doc pair that
        # merely SWAPS ranks (BM25 i / KNN j vs BM25 j / KNN i) contributes
        # identical RRF sums, and the stable sort then keeps BM25 order —
        # so a plain "agreeing" map can still tie the fallback's winner.
        neutral = {
            "findata/The_Chatter/Aquaculture_Edition.md": 0.8,
            "findata/Companies/Agriculture/Sharat_Industries.md": 0.5,
            "findata/Companies/Agriculture/Avanti_Feeds.md": 0.3,
        }
        monkeypatch.setattr(VS, "knn_similarities", lambda *a, **k: dict(neutral))
        with_knn = _titles()
        assert with_knn[0] == "The Chatter: Aquaculture Edition"

        monkeypatch.setattr(VS, "knn_similarities", lambda *a, **k: None)
        without_knn = _titles()
        assert without_knn[0] == "The Chatter: Aquaculture Edition"
        assert sorted(with_knn) == sorted(without_knn)

    def test_hybrid_degrades_on_vector_space_mismatch(self, client, monkeypatch):
        """local_embeddings gate: when the query embedder resolves to a width
        that doesn't match the stored index vectors (e.g. index built with
        bge-384, model file since gone -> pseudo-64 query), the cosine leg is
        meaningless — hybrid must degrade to BM25-only (knn NOT called,
        similarity None) instead of zip-truncating garbage cosines."""
        from helpers.core import vec_search as VS
        from helpers.maintenance import rebuild_note_search as RNS

        calls = []
        monkeypatch.setattr(
            VS,
            "knn_similarities",
            lambda conn, q_vec, k, dims: calls.append((k, dims)) or {},
        )
        # Seeds are 3-dim; resolve the query side to 64-dim pseudo.
        monkeypatch.setattr(
            RNS,
            "query_embedder",
            lambda: ((lambda text: [0.1] * 64), 64),
        )
        r = client.get("/api/search?q=feed&hybrid=true&limit=20")
        assert r.status_code == 200
        assert calls == []  # gate fired before any KNN
        # Degraded contract: similarity collapses to 0.0 floats (q_vec None),
        # ordering is pure BM25 — but the endpoint never 500s.
        for hit in r.get_json()["results"]:
            assert hit["similarity"] == 0.0
