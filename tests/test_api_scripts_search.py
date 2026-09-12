"""
Tests for GET /api/scripts/search — the browser surface over the
script_search sidecar (unified_search proposal S1), wrapping the same
search_scripts() core the script_query CLI wraps. Hermetic: tmp
mini-tree + sidecar, fake embedder (mirrors tests/test_script_query.py;
contract pins mirror tests/test_api_docs.py).

Degradation contract differs from /api/docs/search on purpose: scripts
have NO scan fallback, so a missing/corrupt sidecar is a 503 with the
rebuild command (the CLI's hard-fail contract), while a stale index
still answers with stale: true (the CLI's warn-and-answer contract).
"""

import os
import time

import pytest

from helpers.maintenance import rebuild_script_search as rss  # noqa: E402

pytestmark = [pytest.mark.integration]

_MAKEFILE = (
    ".RECIPEPREFIX := >\n"
    ".PHONY: widget-audit qa\n"
    "widget-audit: ## Run the widget audit pass\n"
    "> python3 helpers/misc/widget_audit.py --apply\n"
    "qa: ## Run the quality gate\n"
    "> python3 tests/test_widget_audit.py\n"
)

_WIDGET_AUDIT = (
    "#!/usr/bin/env python3\n"
    '"""Audit widget diffs across releases.\n\nDetail paragraph.\n"""\n'
    "import argparse\n"
    "def main():\n"
    "    p = argparse.ArgumentParser()\n"
    '    p.add_argument("--apply", action="store_true")\n'
)

_TEST_WIDGET = (
    '"""Tests for the widget audit CLI."""\nfrom helpers.misc import widget_audit  # noqa: F401\n'
)


@pytest.fixture
def client(bare_client):
    """Bare Flask test_client (scripts endpoints never touch research.db)."""
    yield bare_client


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    tree = tmp_path
    (tree / "helpers" / "misc").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "helpers" / "misc" / "widget_audit.py").write_text(_WIDGET_AUDIT)
    (tree / "helpers" / "graph").mkdir()
    (tree / "helpers" / "graph" / "build_tree.py").write_text(
        '"""Build the sector hierarchy tree from DB rows."""\n'
    )
    (tree / "app.py").write_text('"""Flask app serving the FinData API."""\n')
    (tree / "Makefile").write_text(_MAKEFILE)
    (tree / "tests" / "test_widget_audit.py").write_text(_TEST_WIDGET)
    # Retarget ALL module constants (the VAULT_ROOT lesson) — the route's
    # rss import sees these patched values at call time.
    monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
    monkeypatch.setattr(rss, "HELPERS_ROOT", tree / "helpers")
    monkeypatch.setattr(rss, "TESTS_ROOT", tree / "tests")
    monkeypatch.setattr(rss, "APP_PY", tree / "app.py")
    monkeypatch.setattr(rss, "MAKEFILE", tree / "Makefile")
    monkeypatch.setattr(rss, "BACKUP_DIR", tree / "db-backup")
    return tree


@pytest.fixture
def fake_local(seeded, monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        import math

        v = [0.0] * 8
        if "widget" in text.lower():
            v[2] = 1.0
        if "gate" in text.lower():
            v[3] = 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    rss.rebuild(write=True)  # resolve_embedder -> fake via local_embedder
    return LE


class TestScriptsSearchContract:
    def test_200_shape_and_hits(self, fake_local, client):
        r = client.get("/api/scripts/search?q=widget")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data.keys()) == {"query", "mode", "stale", "results"}
        assert data["query"] == "widget"
        assert data["mode"] in ("hybrid", "bm25")
        assert data["stale"] is False
        assert data["results"]
        for hit in data["results"]:
            assert set(hit.keys()) == {
                "path",
                "title",
                "kind",
                "area",
                "purpose",
                "snippet",
                "score",
                "similarity",
            }
            assert hit["snippet"]

    def test_snippet_has_mark_wrapper(self, fake_local, client):
        data = client.get("/api/scripts/search?q=widget").get_json()
        assert "<mark>" in data["results"][0]["snippet"]
        assert "widget" in data["results"][0]["snippet"].lower()

    def test_kind_filter_make(self, fake_local, client):
        data = client.get("/api/scripts/search?q=quality gate&kind=make").get_json()
        assert data["results"]
        assert all(h["kind"] == "make" for h in data["results"])
        assert data["results"][0]["path"] == "qa"

    def test_kind_filter_excludes_other_rows(self, fake_local, client):
        data = client.get("/api/scripts/search?q=widget&kind=test").get_json()
        assert data["results"]
        assert all(h["kind"] == "test" for h in data["results"])

    def test_area_filter(self, fake_local, client):
        data = client.get("/api/scripts/search?q=build&area=graph").get_json()
        assert data["results"]
        assert all(h["area"] == "graph" for h in data["results"])
        assert data["results"][0]["path"] == "helpers/graph/build_tree.py"

    def test_hybrid_off_forces_bm25(self, fake_local, client):
        data = client.get("/api/scripts/search?q=widget&hybrid=0").get_json()
        assert data["mode"] == "bm25"
        assert data["results"]

    def test_hybrid_default_with_embedder(self, fake_local, client):
        data = client.get("/api/scripts/search?q=widget").get_json()
        assert data["mode"] == "hybrid"

    def test_limit_and_offset(self, fake_local, client):
        page1 = client.get("/api/scripts/search?q=widget&limit=1").get_json()
        assert len(page1["results"]) == 1
        page2 = client.get("/api/scripts/search?q=widget&limit=1&offset=1").get_json()
        assert len(page2["results"]) == 1
        assert page1["results"][0]["path"] != page2["results"][0]["path"]

    def test_limit_clamp_high(self, fake_local, client):
        r = client.get("/api/scripts/search?q=widget&limit=99999")
        assert r.status_code == 200

    def test_punctuated_query_safe(self, fake_local, client):
        """Free-text input must never parse as FTS5 syntax."""
        r = client.get("/api/scripts/search?q=widget, audit; how?")
        assert r.status_code == 200
        assert r.get_json()["results"]
        r2 = client.get("/api/scripts/search?q=widget AND (audit OR NOT)")
        assert r2.status_code == 200

    def test_no_matches_empty_list_lexical(self, fake_local, client):
        """The strict-empty contract is the lexical leg: hybrid=0 must
        return []. With the hybrid leg on, cosine-only rows are legitimate
        recall (the OR-union design — question-shaped queries must not
        require token co-occurrence); the fake 8-dim embedder maps a
        garbage query to the zero vector, which sims 0.0 against every
        row. The live box has a real 384-dim embedder, where the same
        query returns low-sim recall rows by the same design."""
        data = client.get("/api/scripts/search?q=zzz_never_matches_zzz&hybrid=0").get_json()
        assert data["results"] == []


class TestScriptsSearchValidation:
    def test_missing_q_400(self, seeded, client):
        r = client.get("/api/scripts/search")
        assert r.status_code == 400
        assert "error" in r.get_json()

    def test_blank_q_400(self, seeded, client):
        r = client.get("/api/scripts/search?q=%20%20")
        assert r.status_code == 400

    def test_bad_limit_400(self, fake_local, client):
        r = client.get("/api/scripts/search?q=widget&limit=abc")
        assert r.status_code == 400

    def test_bad_offset_400(self, fake_local, client):
        r = client.get("/api/scripts/search?q=widget&offset=abc")
        assert r.status_code == 400

    def test_negative_offset_400(self, fake_local, client):
        r = client.get("/api/scripts/search?q=widget&offset=-1")
        assert r.status_code == 400

    def test_unknown_kind_400(self, fake_local, client):
        r = client.get("/api/scripts/search?q=widget&kind=bogus")
        assert r.status_code == 400
        assert "kind" in r.get_json()["error"]


class TestScriptsSearchDegradation:
    def test_missing_sidecar_503_with_rebuild_command(self, seeded, client):
        # seeded but NOT rebuilt — the tmp SCRIPT_DB does not exist yet.
        r = client.get("/api/scripts/search?q=widget")
        assert r.status_code == 503
        body = r.get_json()
        assert "rebuild" in body
        assert "rebuild_script_search" in body["rebuild"]

    def test_stale_index_still_answers(self, fake_local, seeded, client):
        future = time.time() + 10
        app_py = seeded / "app.py"
        os.utime(app_py, (future, future))
        r = client.get("/api/scripts/search?q=widget")
        assert r.status_code == 200
        data = r.get_json()
        assert data["stale"] is True
        assert data["results"]

    def test_corrupt_sidecar_503_never_500(self, fake_local, seeded, client):
        db = seeded / "script_search.db"
        db.write_bytes(b"not a sqlite database at all")
        r = client.get("/api/scripts/search?q=widget")
        assert r.status_code == 503
