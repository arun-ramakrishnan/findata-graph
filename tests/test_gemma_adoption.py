"""Contract tests for the script_search gemma adoption
(script_search_gemma_adoption S3).

Pins the three silent-failure traps: the two-sided prefix contract
(query prefix here, doc basis at the index site), the per-surface
selector (granite stays the default everywhere), and the
identifier router boundary (the repo-banked 54-question set decides).
Hermetic throughout — the sidecar is stubbed at _post_embedding except
the explicitly live class, which skips when the server is down.
"""

import json
import math
import sqlite3

import pytest

from helpers.core import gemma_embedder
from helpers.core.embed_cache import EMBED_CACHE_TABLE, cached_embed_batch
from helpers.maintenance import rebuild_script_search as rss

BANK_PATH = "helpers/misc/script_eval_questions.json"


def _fake_768(seed: float = 0.5) -> list[float]:
    """Deterministic pseudo-768d server vector (unit-ish, nonzero)."""
    return [math.sin(seed + i * 0.01) + 1.5 for i in range(gemma_embedder.SERVER_DIMS)]


@pytest.fixture
def _stub_post(monkeypatch):
    """Capture embedded texts; serve fake 768-d vectors (no sidecar)."""
    seen: list[str] = []

    def _post(text: str, port=None) -> list[float]:
        seen.append(text)
        return _fake_768()

    monkeypatch.setattr(gemma_embedder, "_post_embedding", _post)
    return seen


class TestStamp:
    def test_label_and_dims(self):
        assert gemma_embedder.MODEL_LABEL == "embeddinggemma-2-q8_512"
        assert gemma_embedder.DIM == 512
        assert gemma_embedder.SERVER_DIMS == 768

    def test_truncate_is_512d_unit(self):
        vec = gemma_embedder._truncate(_fake_768())
        assert len(vec) == gemma_embedder.DIM
        assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-6)

    def test_truncate_zero_raises(self):
        with pytest.raises(gemma_embedder.GemmaEmbedderError):
            gemma_embedder._truncate([0.0] * gemma_embedder.SERVER_DIMS)

    def test_wrong_server_dims_raise(self, monkeypatch):
        monkeypatch.setattr(gemma_embedder, "_post_embedding", lambda text, port=None: [1.0] * 384)
        with pytest.raises(gemma_embedder.GemmaEmbedderUnavailable):
            gemma_embedder.embed_document("hello")


class TestPrefixContract:
    """Omission degrades silently (hard-set MRR 1.000 -> 0.880) — the
    embedded text must carry the prefix, so emptying a constant fails
    these tests instead of the ranking months later."""

    def test_query_applies_code_prefix_by_default(self, _stub_post):
        gemma_embedder.embed_query("find widgets")
        assert _stub_post == [gemma_embedder.QUERY_PREFIX_CODE + "find widgets"]

    def test_query_search_task_prefix(self, _stub_post):
        gemma_embedder.embed_query("find widgets", task="search")
        assert _stub_post == [gemma_embedder.QUERY_PREFIX_SEARCH + "find widgets"]

    def test_query_unknown_task_raises(self, _stub_post):
        with pytest.raises(ValueError):
            gemma_embedder.embed_query("find widgets", task="nope")
        assert _stub_post == []

    def test_document_is_raw_basis_never_prefixed(self, _stub_post):
        gemma_embedder.embed_document("title: t | text: body")
        assert _stub_post == ["title: t | text: body"]

    def test_empty_text_raises(self, _stub_post):
        with pytest.raises(ValueError):
            gemma_embedder.embed_query("   ")
        with pytest.raises(ValueError):
            gemma_embedder.embed_document("")
        assert _stub_post == []

    def test_doc_basis_shape(self):
        basis = rss._gemma_basis("widget_audit.py", "Audit widgets.", "import x")
        assert basis.startswith("title: widget_audit.py | text: Audit widgets.\n")

    def test_doc_basis_none_title(self):
        assert rss._gemma_basis("", "p", "c").startswith("title: none | text: p\n")


class TestSelector:
    def test_default_follows_availability(self, monkeypatch):
        monkeypatch.delenv("SCRIPT_EMBEDDER", raising=False)
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: False)
        _fn, _dims, label, active = rss.resolve_script_embedder()
        assert (active, label != gemma_embedder.MODEL_LABEL) == (False, True)
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: True)
        _fn, dims, label, active = rss.resolve_script_embedder()
        assert (active, dims, label) == (True, 512, gemma_embedder.MODEL_LABEL)

    def test_granite_forced_stays_granite(self, monkeypatch, capsys):
        monkeypatch.setenv("SCRIPT_EMBEDDER", "granite")
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: True)
        _fn, _dims, label, active = rss.resolve_script_embedder()
        assert active is False
        assert label != gemma_embedder.MODEL_LABEL
        assert capsys.readouterr().err == ""

    def test_gemma_requested_but_down_falls_back_with_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("SCRIPT_EMBEDDER", "gemma")
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: False)
        _fn, _dims, label, active = rss.resolve_script_embedder()
        assert active is False
        assert label != gemma_embedder.MODEL_LABEL
        assert "SCRIPT_EMBEDDER=gemma" in capsys.readouterr().err

    def test_gemma_requested_and_up_resolves_gemma(self, monkeypatch):
        monkeypatch.setenv("SCRIPT_EMBEDDER", "gemma")
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: True)
        fn, dims, label, active = rss.resolve_script_embedder()
        assert active is True
        assert (dims, label) == (512, gemma_embedder.MODEL_LABEL)
        assert fn is gemma_embedder.embed_document

    def test_query_backend_follows_stamp_not_env(self, monkeypatch):
        monkeypatch.setenv("SCRIPT_EMBEDDER", "granite")
        fn, dims = rss.query_embedder(gemma_embedder.MODEL_LABEL)
        assert (fn, dims) == (gemma_embedder.embed_query, 512)

    def test_query_backend_default_is_shared_path(self, monkeypatch):
        monkeypatch.delenv("SCRIPT_EMBEDDER", raising=False)
        fn, _dims = rss.query_embedder(None)
        assert fn is not gemma_embedder.embed_query


class TestRouterBoundary:
    """The bank decides the router boundary: 8/8 identifier questions
    lexical, 0/46 intent questions misrouted (checked 2026-10-07)."""

    @pytest.fixture
    def bank(self):
        with open(BANK_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_bank_shape(self, bank):
        assert len(bank["intent"]) == 46
        assert len(bank["ident"]) == 8

    def test_identifiers_route_lexical(self, bank):
        for item in bank["ident"]:
            assert rss.is_identifier_query(item["q"]), item["q"]

    def test_intent_never_routes_lexical(self, bank):
        for item in bank["intent"]:
            assert not rss.is_identifier_query(item["q"]), item["q"]

    def test_empty_query_is_not_identifier(self):
        assert rss.is_identifier_query("") is False
        assert rss.is_identifier_query("   ") is False


def _seed_db(path, stamp_label="dry-run-v64", dims=64):
    """Tiny script_search-shaped DB with two rows (FTS + stamp)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE VIRTUAL TABLE script_search USING fts5(title, kind, rel_path, area, purpose, content, embedding UNINDEXED)"
    )
    conn.execute(
        "INSERT INTO script_search VALUES "
        "('helpers/a.py','script','helpers/a.py','misc','widget audit','import audit audit',NULL),"
        "('helpers/b.py','script','helpers/b.py','misc','gate runner','import gate gate',NULL)"
    )
    conn.execute("CREATE TABLE script_search_info(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO script_search_info VALUES ('embed_model', ?), ('embed_dims', ?)",
        (stamp_label, str(dims)),
    )
    conn.commit()
    return conn


class TestVectorShape:
    def test_explicit_vector_only_on_granite_index(self, tmp_path, monkeypatch):
        conn = _seed_db(tmp_path / "s.db")
        monkeypatch.setattr(
            rss,
            "_cosine_leg",
            lambda conn, q, query_vec=None: ([(1, 0.9), (2, 0.1)], {1: 0.9, 2: 0.1}),
        )
        out = rss.search_scripts(conn, "audit widgets", vector_only=True)
        assert out["mode"] == "vector"
        assert [h["path"] for h in out["results"]] == ["helpers/a.py", "helpers/b.py"]
        assert out["results"][0]["similarity"] == pytest.approx(0.9)

    def test_gemma_index_routes_ident_to_lexical(self, tmp_path, monkeypatch):
        conn = _seed_db(tmp_path / "s.db", gemma_embedder.MODEL_LABEL, 512)
        called = []

        def _cos(conn, q, query_vec=None):
            called.append(q)
            return ([(1, 0.9)], {1: 0.9})

        monkeypatch.setattr(rss, "_cosine_leg", _cos)
        out = rss.search_scripts(conn, "fork_map")
        assert out["mode"] == "bm25"
        assert called == []

    def test_gemma_index_routes_paraphrase_to_vector(self, tmp_path, monkeypatch):
        conn = _seed_db(tmp_path / "s.db", gemma_embedder.MODEL_LABEL, 512)
        monkeypatch.setattr(
            rss,
            "_cosine_leg",
            lambda conn, q, query_vec=None: ([(2, 0.8)], {2: 0.8}),
        )
        out = rss.search_scripts(conn, "where is the audit logic")
        assert out["mode"] == "vector"
        assert [h["path"] for h in out["results"]] == ["helpers/b.py"]

    def test_granite_default_stays_hybrid(self, tmp_path, monkeypatch):
        conn = _seed_db(tmp_path / "s.db", "granite-embedding-97m-r2", 384)
        monkeypatch.setattr(
            rss,
            "_cosine_leg",
            lambda conn, q, query_vec=None: ([(1, 0.5)], {1: 0.5}),
        )
        out = rss.search_scripts(conn, "where is the audit logic")
        assert out["mode"] == "hybrid"

    def test_dead_cosine_leg_yields_empty_vector_page(self, tmp_path, monkeypatch):
        conn = _seed_db(tmp_path / "s.db", gemma_embedder.MODEL_LABEL, 512)
        monkeypatch.setattr(rss, "_cosine_leg", lambda conn, q, query_vec=None: ([], {}))
        out = rss.search_scripts(conn, "where is the audit logic")
        assert out == {"mode": "vector", "results": []}


class _RecordingEmbed:
    """Minimal CachedEmbed stand-in for rebuild-harness tests."""

    def __init__(self, fn, label, conn, source="", purge_foreign=False):
        self.hits = self.misses = self.dirty = 0

    def __call__(self, text):
        return [1.0] * 512


class TestCacheNoPurge:
    """The gemma path must NEVER purge_foreign: granite rows stay live on
    the other surfaces while the scripts trial runs."""

    def test_gemma_rebuild_wires_purge_foreign_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCRIPT_EMBEDDER", "gemma")
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: True)
        monkeypatch.setattr(gemma_embedder, "embed_document", lambda text, port=None: [1.0] * 512)
        (tmp_path / "helpers" / "misc").mkdir(parents=True)
        (tmp_path / "helpers" / "misc" / "w.py").write_text('"""Do widgets."""\n')
        (tmp_path / "tests").mkdir()
        (tmp_path / "app.py").write_text('"""App."""\n')
        (tmp_path / "Makefile").write_text("qa: ## Q\n\techo q\n")
        seen = {}

        class _Rec:
            def __init__(self, fn, label, conn, source="", purge_foreign=False):
                seen.update(label=label, purge_foreign=purge_foreign)
                self.hits = self.misses = self.dirty = 0

            def __call__(self, text):
                return [1.0] * 512

        monkeypatch.setattr(rss, "CachedEmbed", _Rec)
        monkeypatch.setattr(rss, "BACKUP_DIR", tmp_path / "db-backup")
        rss.rebuild(
            tmp_path / "s.db",
            helpers_root=tmp_path / "helpers",
            tests_root=tmp_path / "tests",
            app_py=tmp_path / "app.py",
            makefile=tmp_path / "Makefile",
        )
        assert seen["label"] == gemma_embedder.MODEL_LABEL
        assert seen["purge_foreign"] is False

    def test_gemma_stamp_with_sidecar_down_warns_loud(self, tmp_path, monkeypatch, capsys):
        """S3 (llamacpp_unified_server_build): a gemma-stamped index being
        rebuilt while the sidecar is down would silently re-embed granite
        and flip the stamp (the search-fresh revert accident). The rebuild
        must warn on stderr; the rebuild itself still completes."""
        monkeypatch.delenv("SCRIPT_EMBEDDER", raising=False)
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: False)
        monkeypatch.setattr(
            rss, "_script_stored_embed_model", lambda conn: gemma_embedder.MODEL_LABEL
        )
        monkeypatch.setattr(rss, "CachedEmbed", _RecordingEmbed)
        monkeypatch.setattr(rss, "BACKUP_DIR", tmp_path / "db-backup")
        (tmp_path / "helpers" / "misc").mkdir(parents=True)
        (tmp_path / "helpers" / "misc" / "w.py").write_text('"""Do widgets."""\n')
        (tmp_path / "tests").mkdir()
        (tmp_path / "app.py").write_text('"""App."""\n')
        (tmp_path / "Makefile").write_text("qa: ## Q\n\techo q\n")
        rss.rebuild(
            tmp_path / "s.db",
            helpers_root=tmp_path / "helpers",
            tests_root=tmp_path / "tests",
            app_py=tmp_path / "app.py",
            makefile=tmp_path / "Makefile",
        )
        err = capsys.readouterr().err
        assert "UN-MIGRATES the stamp" in err
        assert "make embgemma-server" in err

    def test_foreign_rows_survive_without_purge(self, tmp_path):
        from helpers.core.embed_cache import CachedEmbed

        conn = sqlite3.connect(str(tmp_path / "c.db"))
        cached_embed_batch(
            conn, ["granite text"], "granite-embedding-97m-r2", lambda ts: [[1.0, 0.0] for _ in ts]
        )
        before = conn.execute(f"SELECT COUNT(*) FROM {EMBED_CACHE_TABLE}").fetchone()[0]  # noqa: S608
        CachedEmbed(lambda t: [0.0, 1.0], gemma_embedder.MODEL_LABEL, conn, source="script")
        after = conn.execute(f"SELECT COUNT(*) FROM {EMBED_CACHE_TABLE}").fetchone()[0]  # noqa: S608
        assert (before, after) == (1, 1)


@pytest.mark.skipif(not gemma_embedder.available(), reason="embgemma sidecar down")
class TestLiveSidecar:
    """Needs `make embgemma-server` in another shell. Proves the HTTP
    path end to end (dims, determinism, prefix effect)."""

    def test_dims_and_determinism(self):
        d1 = gemma_embedder.embed_document("title: t | text: shrimp feed")
        d2 = gemma_embedder.embed_document("title: t | text: shrimp feed")
        assert len(d1) == 512
        # Near- not bit-identical: multi-threaded llama-server FP32
        # reductions vary run-to-run (measured cos 0.99984, max diff
        # 2.5e-3, quiet box) — 4 orders below eval resolution, but ==
        # is the wrong assertion.
        assert sum(a * b for a, b in zip(d1, d2)) > 0.999

    def test_prefix_changes_the_query_vector(self):
        raw = gemma_embedder._post_embedding("aquaculture feed")[:512]
        norm = math.sqrt(sum(a * a for a in raw))
        raw = [a / norm for a in raw]
        pre = gemma_embedder.embed_query("aquaculture feed")
        assert sum(a * b for a, b in zip(raw, pre)) < 0.999
