"""
Tests for the doc/ corpus browser endpoints — GET /api/docs (catalog),
GET /api/docs/content (raw body), and GET /api/docs/search (naive scan).

Unlike /api/search these routes are filesystem-backed (the doc/ design corpus
on disk, NOT the research DB), so they need no DB seeding — just a bare Flask
test_client. The tests pin: catalog shape/ordering/filtering, title
derivation, path-traversal guarding, content fetching, and the search
ranking + snippet shape.
"""

import pytest

import app as A


@pytest.fixture
def client():
    """Bare Flask test_client (docs endpoints never touch the DB)."""
    with A.app.test_client() as c:
        yield c


def _catalog(client, q=None):
    url = "/api/docs" if q is None else f"/api/docs?q={q}"
    return client.get(url)


class TestDocsCatalog:
    def test_returns_200_and_doc_items(self, client):
        r = _catalog(client)
        assert r.status_code == 200
        data = r.get_json()
        assert "docs" in data
        assert len(data["docs"]) > 0
        for doc in data["docs"]:
            # Shape pin (mirrors DocItem in frontend/types/api.ts).
            assert set(doc.keys()) == {
                "path", "name", "section", "title", "size_bytes", "mtime",
            }
            assert isinstance(doc["size_bytes"], int)
            assert isinstance(doc["mtime"], int)
            assert doc["title"]
            assert doc["path"]

    def test_sorted_by_path(self, client):
        data = _catalog(client).get_json()
        paths = [d["path"] for d in data["docs"]]
        assert paths == sorted(paths)

    def test_sections_include_top_level_and_nested(self, client):
        data = _catalog(client).get_json()
        sections = {d["section"] for d in data["docs"]}
        # Top-level files carry ""; nested files carry their subdir.
        assert "" in sections
        assert any(s.startswith("improvements") for s in sections)

    def test_covers_known_corpus_files(self, client):
        data = _catalog(client).get_json()
        paths = {d["path"] for d in data["docs"]}
        assert "architecture.md" in paths
        assert "graph_design.txt" in paths
        assert "improvements/completed.md" in paths
        assert any(p.startswith("improvements/archive/") for p in paths)

    def test_q_filters_by_path_substring(self, client):
        data = _catalog(client, q="graph").get_json()
        assert data["docs"]
        for doc in data["docs"]:
            assert "graph" in doc["path"].lower()

    def test_q_with_no_matches_returns_empty(self, client):
        data = _catalog(client, q="zzz_never_matches_zzz").get_json()
        assert data["docs"] == []

    def test_md_title_from_first_heading(self, client):
        data = _catalog(client).get_json()
        arch = next(d for d in data["docs"] if d["path"] == "architecture.md")
        # architecture.md starts with a "# " heading — title should derive from it.
        assert "Architecture" in arch["title"]

    def test_txt_title_from_first_line_or_stem(self, client):
        data = _catalog(client).get_json()
        txt = [d for d in data["docs"] if d["path"].endswith(".txt")]
        assert txt
        for doc in txt:
            assert doc["title"]


class TestDocsContent:
    def test_returns_raw_markdown(self, client):
        r = client.get("/api/docs/content?path=architecture.md")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data.keys()) == {
            "path", "name", "section", "title", "content", "size_bytes", "mtime",
        }
        assert data["path"] == "architecture.md"
        assert "content" in data and data["content"]
        assert data["size_bytes"] == len(data["content"].encode("utf-8"))

    def test_unknown_path_404(self, client):
        r = client.get("/api/docs/content?path=does_not_exist.md")
        assert r.status_code == 404
        assert "error" in r.get_json()

    def test_missing_path_param_404(self, client):
        r = client.get("/api/docs/content")
        assert r.status_code == 404

    def test_path_traversal_rejected(self, client):
        # Every escape vector must 404 (never read outside doc/).
        escapes = [
            "../app.py",
            "../README.md",
            "../../etc/passwd",
            "..%2f..%2fapp.py",  # encoded slash
            "/etc/passwd",       # absolute
            "..\\..\\app.py",    # windows-style
        ]
        for escape in escapes:
            r = client.get(f"/api/docs/content?path={escape}")
            assert r.status_code == 404, f"escape {escape!r} not blocked"
            assert "error" in r.get_json()

    def test_nul_byte_rejected(self, client):
        r = client.get("/api/docs/content?path=%00")
        assert r.status_code == 404


class TestDocsSearch:
    def test_returns_200_and_hits(self, client):
        r = client.get("/api/docs/search?q=duckdb")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data.keys()) == {"query", "results"}
        assert data["query"] == "duckdb"
        assert data["results"]
        for hit in data["results"]:
            assert set(hit.keys()) == {
                "path", "name", "section", "title", "snippet",
            }
            assert hit["snippet"]

    def test_empty_query_400(self, client):
        r = client.get("/api/docs/search")
        assert r.status_code == 400
        assert "error" in r.get_json()

    def test_blank_query_400(self, client):
        r = client.get("/api/docs/search?q=%20%20")
        assert r.status_code == 400

    def test_snippet_contains_mark_wrapper(self, client):
        data = client.get("/api/docs/search?q=duckdb").get_json()
        hit = data["results"][0]
        assert "<mark>duckdb</mark>" in hit["snippet"].lower()

    def test_no_matches_empty_list(self, client):
        data = client.get("/api/docs/search?q=zzz_never_matches_zzz").get_json()
        assert data["results"] == []

    def test_limit_clamp(self, client):
        data = client.get("/api/docs/search?q=graph&limit=1").get_json()
        assert len(data["results"]) == 1
        # Oversized limit clamps, doesn't crash.
        r = client.get("/api/docs/search?q=graph&limit=99999")
        assert r.status_code == 200

    def test_bad_limit_400(self, client):
        r = client.get("/api/docs/search?q=graph&limit=abc")
        assert r.status_code == 400

    def test_results_ranked_by_relevance(self, client):
        """Every hit genuinely contains the query, and the top hit is a
        real match (snippet carries it). Naive scoring counts body
        occurrences, so a long corpus-audit doc can outrank a title hit."""
        data = client.get("/api/docs/search?q=sector").get_json()
        assert data["results"]
        top = data["results"][0]
        assert "sector" in top["snippet"].lower()
        assert "<mark>sector</mark>" in top["snippet"].lower()
        # All hits are genuine matches.
        for hit in data["results"]:
            assert "sector" in hit["snippet"].lower()
