"""
Tests for the doc/ corpus browser endpoints — GET /api/docs (catalog),
GET /api/docs/content (raw body), and GET /api/docs/search.

Search is two-mode: the doc_search sidecar index (hybrid BM25+cosine,
memory/doc_search.db) when present and fresh, degrading to the #107
filesystem scan. The TestDocsSearch cases below run against the LIVE
repo doc/ tree and therefore must hold in EITHER mode (no mode pin on
the live corpus — a fresh clone scans, a machine with a built index
serves from it). TestDocsSearchModes pins the mode/degradation logic
hermetically against a tmp tree + tmp sidecar.

Unlike /api/search these routes are filesystem-backed (the doc/ design corpus
on disk, NOT the research DB), so they need no DB seeding — just a bare Flask
test_client. The tests pin: catalog shape/ordering/filtering, title
derivation, path-traversal guarding, content fetching, and the search
ranking + snippet shape.
"""

import os
import time
from types import SimpleNamespace

import pytest

import app as A


@pytest.fixture
def client():
    """Bare Flask test_client (docs endpoints never touch the research DB)."""
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
        assert "doc/architecture.md" in paths
        assert "doc/graph_design.txt" in paths
        assert "doc/improvements/completed.md" in paths
        assert any(p.startswith("doc/improvements/archive/") for p in paths)

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
        arch = next(d for d in data["docs"] if d["path"] == "doc/architecture.md")
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
        # The canonical echo is repo-rooted regardless of the requested form.
        assert data["path"] == "doc/architecture.md"
        # Repo-rooted request form resolves identically.
        r2 = client.get("/api/docs/content?path=doc/architecture.md")
        assert r2.status_code == 200
        assert r2.get_json()["content"] == data["content"]
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
    """Live-corpus contract — must hold in scan AND index mode."""

    def test_returns_200_and_hits(self, client):
        r = client.get("/api/docs/search?q=duckdb")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data.keys()) == {"query", "mode", "stale", "results"}
        assert data["query"] == "duckdb"
        assert data["mode"] in ("hybrid", "bm25", "scan")
        assert data["results"]
        for hit in data["results"]:
            assert set(hit.keys()) == {
                "path", "name", "section", "title", "section_title",
                "anchor", "snippet", "score", "similarity",
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
        # Marked term may differ in case/inflection across modes (FTS5
        # porter vs the scan's literal wrap), so pin the wrapper + stem.
        assert "<mark>" in hit["snippet"]
        assert "duckdb" in hit["snippet"].lower()

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
        """Every hit genuinely contains the query, and the top hit is a real
        match (snippet carries it). Both modes centre snippets on a match."""
        data = client.get("/api/docs/search?q=sector").get_json()
        assert data["results"]
        top = data["results"][0]
        assert "sector" in top["snippet"].lower()
        assert "<mark>" in top["snippet"]
        # All hits are genuine matches.
        for hit in data["results"]:
            assert "sector" in hit["snippet"].lower()


# --------------------------------------------------------------------------- #
# Mode/degradation logic — hermetic (tmp doc tree + tmp sidecar).               #
# --------------------------------------------------------------------------- #

_GUIDE = (
    "# Repo Guide\n"
    "\n"
    "## Cache Design\n"
    "how the cache warms up and persists rows\n"
    "\n"
    "## Other\n"
    "unrelated beta prose\n"
)

_NOTES_TXT = "Cache Notes\nplain text about caches\n"


@pytest.fixture
def tmp_doc_env(tmp_path, monkeypatch):
    """Retarget BOTH scan roots and the indexer at a tmp tree + sidecar."""
    from helpers.maintenance import rebuild_doc_search as rds

    doc_root = tmp_path / "doc"
    doc_root.mkdir()
    (doc_root / "guide.md").write_text(_GUIDE, encoding="utf-8")
    (doc_root / "notes.txt").write_text(_NOTES_TXT, encoding="utf-8")
    db = tmp_path / "doc_search.db"
    monkeypatch.setattr(A, "_DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_DB", db)
    return SimpleNamespace(root=doc_root, db=db, rds=rds)


@pytest.fixture
def fake_local(monkeypatch):
    """Fake available embedder sharing one 8-dim keyword space."""
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        import math as _math

        v = [0.0] * 8
        low = text.lower()
        for kw, idx in {"cache": 2, "beta": 1}.items():
            if kw in low:
                v[idx] = 1.0
        n = _math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    return LE


class TestDocsSearchModes:
    def test_scan_when_no_index(self, tmp_doc_env, client):
        data = client.get("/api/docs/search?q=cache").get_json()
        assert data["mode"] == "scan"
        assert data["stale"] is False
        assert data["results"]
        for hit in data["results"]:
            assert hit["anchor"] is None
            assert hit["section_title"] == ""
            assert isinstance(hit["score"], int)

    def test_hybrid_when_index_fresh(self, tmp_doc_env, fake_local, client):
        tmp_doc_env.rds.rebuild(write=True)
        data = client.get("/api/docs/search?q=cache").get_json()
        assert data["mode"] == "hybrid"
        assert data["stale"] is False
        assert data["results"]
        assert isinstance(data["results"][0]["score"], float)
        # Section-level hits: the guide's Cache Design chunk surfaces with
        # its deep-link anchor. (notes.txt may outrank it on BM25 density —
        # both are legitimate top hits, so no cross-file rank pin here.)
        guide_rows = [r for r in data["results"] if r["path"] == "doc/guide.md"]
        assert guide_rows
        cache_row = next(r for r in guide_rows if r["section_title"] == "Cache Design")
        assert cache_row["anchor"] == 3
        assert "<mark>" in cache_row["snippet"]

    def test_stale_index_falls_back_to_scan(self, tmp_doc_env, fake_local, client):
        tmp_doc_env.rds.rebuild(write=True)
        guide = tmp_doc_env.root / "guide.md"
        future = time.time() + 10
        os.utime(guide, (future, future))
        data = client.get("/api/docs/search?q=cache").get_json()
        assert data["mode"] == "scan"
        assert data["stale"] is True
        assert data["results"]  # the scan still serves

    def test_hybrid_zero_forces_bm25(self, tmp_doc_env, fake_local, client):
        tmp_doc_env.rds.rebuild(write=True)
        data = client.get("/api/docs/search?q=cache&hybrid=0").get_json()
        assert data["mode"] == "bm25"
        assert data["results"]

    def test_corrupt_sidecar_never_500s(self, tmp_doc_env, fake_local, client):
        tmp_doc_env.rds.rebuild(write=True)
        tmp_doc_env.db.write_bytes(b"not a sqlite database at all")
        r = client.get("/api/docs/search?q=cache")
        assert r.status_code == 200
        data = r.get_json()
        assert data["mode"] == "scan"
        assert data["results"]

    def test_punctuated_query_is_safe(self, tmp_doc_env, fake_local, client):
        """Free-text input must never parse as FTS5 syntax (the docs surface
        takes natural-language queries, unlike /api/search's MATCH passthrough).
        Quoted tokens whose punctuation strips away must still match."""
        tmp_doc_env.rds.rebuild(write=True)
        r = client.get("/api/docs/search?q=cache, design; how?")
        assert r.status_code == 200
        assert r.get_json()["results"]
        # Bare operators in the old design would have been an fts5 syntax
        # error or a boolean rewrite; quoted, they are just literals.
        r2 = client.get("/api/docs/search?q=cache AND (design OR NOT)")
        assert r2.status_code == 200
