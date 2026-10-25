# Tests for helpers/core/embed_cache.py
"""Unit tests for the shared (sha256(text), model) embedding cache.

The per-text ``CachedEmbed`` wrapper is exercised end-to-end through the
rebuild_note_search tests (cold/warm/model-label/pre-warm); these tests pin
the BATCH interface the company-embeddings populate uses, plus the shared
module's own contracts (model keying, degradation, length guard).
"""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from helpers.core.embed_cache import EMBED_CACHE_TABLE, cached_embed_batch


def _conn(tmp_path=None):
    # :memory: main -> _store_path attaches an anonymous in-memory vecdb,
    # so every test is hermetic no matter where tmp_path points (a file
    # main would attach the REAL memory/embed_store.db and leak rows).
    return sqlite3.connect(":memory:")


def _fake_embed():
    """Embedder that records its calls and maps text -> a recognizable vector."""
    calls: list[list[str]] = []

    def fn(texts):
        calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]

    return fn, calls


def _cache_rows(conn):
    return conn.execute(
        f"SELECT text_hash, model FROM {EMBED_CACHE_TABLE}"  # noqa: S608  # constant table name
    ).fetchall()


class TestCachedEmbedBatch:
    def test_cold_embeds_all_in_one_batch_and_seeds(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        assert st == {"hits": 0, "misses": 2, "unique_misses": 2, "dirty": 2}
        assert calls == [["aa", "bbb"]]  # ONE batch call for all misses
        assert [v[0] for v in vecs] == [2.0, 3.0]
        assert len(_cache_rows(conn)) == 2

    def test_warm_serves_from_cache_without_embedding(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        v1, _ = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        v2, st = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        assert st == {"hits": 2, "misses": 0, "unique_misses": 0, "dirty": 0}
        assert len(calls) == 1  # still only the cold call
        assert v1 == v2  # cache round-trips vectors faithfully

    def test_partial_miss_reembeds_only_the_missing_texts(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        vecs, st = cached_embed_batch(conn, ["aa", "cccc"], "m1", fn)
        assert st["hits"] == 1
        assert st["misses"] == 1
        assert calls[-1] == ["cccc"]  # only the new text went to the embedder
        assert vecs[1][0] == 4.0

    def test_cache_is_keyed_by_model_label(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa"], "m1", fn)
        _, st = cached_embed_batch(conn, ["aa"], "m2", fn)
        assert st["misses"] == 1  # other model's vector must never be served
        models = {row[1] for row in _cache_rows(conn)}
        assert models == {"m1", "m2"}

    def test_empty_text_list_never_calls_the_embedder(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, [], "m1", fn)
        assert vecs == []
        assert st == {"hits": 0, "misses": 0, "unique_misses": 0, "dirty": 0}
        assert calls == []

    def test_short_embedder_reply_raises(self, tmp_path):
        conn = _conn(tmp_path)

        def bad_fn(texts):
            return [[0.0, 1.0] for _ in texts[:-1]]  # one vector short

        import pytest

        with pytest.raises(ValueError, match="batch embedder returned"):
            cached_embed_batch(conn, ["a", "b"], "m1", bad_fn)

    def test_no_sidecar_degrades_to_uncached(self, tmp_path, monkeypatch):
        import helpers.core.vec_search as VS

        def boom(conn):
            raise sqlite3.Error("no sidecar for you")

        monkeypatch.setattr(VS, "_attach_vec_db", boom)
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, ["aa"], "m1", fn)
        assert st == {"hits": 0, "misses": 1, "unique_misses": 1, "dirty": 0}
        assert calls == [["aa"]]  # still embedded, just uncached
        assert vecs == [[2.0, 1.0]]

    def test_corrupted_cache_row_counts_as_a_miss(self, tmp_path):
        conn = _conn(tmp_path)
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa"], "m1", fn)
        # Corrupt the stored vector so it can't parse.
        conn.execute(
            f"UPDATE {EMBED_CACHE_TABLE} SET embedding = 'not-json'",  # noqa: S608  # constant table name
        )
        conn.commit()
        _, st = cached_embed_batch(conn, ["aa"], "m1", fn)
        assert st["misses"] == 1
        assert st["hits"] == 0
        assert len(calls) == 2  # re-embedded

    def test_opt_in_rust_hits_leave_misses_to_python(self, tmp_path, monkeypatch):
        from helpers.core import embed_cache as EC
        from helpers.core import vec_search as VS

        store_path = tmp_path / "embed_store.db"
        monkeypatch.setattr(VS, "EMBED_DB_PATH", store_path)
        monkeypatch.setenv(EC._RUST_LOOKUP_ENV, "1")
        monkeypatch.setattr(EC, "_rust_lookup_binary", lambda: tmp_path / "mock-rust")
        conn = sqlite3.connect(tmp_path / "main.db")
        VS._attach_vec_db(conn)
        conn.execute(EC.EMBED_CACHE_DDL)
        conn.commit()

        requests = []

        def fake_run(args, **kwargs):
            assert args == [str(tmp_path / "mock-rust")]
            request = json.loads(kwargs["input"])
            requests.append(request)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "protocol": 1,
                        "hit_vectors": [{"index": 0, "vector": [9.0, 8.0]}],
                        "miss_indices": [1, 2],
                    }
                ),
                stderr="",
            )

        monkeypatch.setattr(EC.subprocess, "run", fake_run)
        fn, calls = _fake_embed()
        vectors, stats = cached_embed_batch(
            conn, ["hot", "cold", "cold"], "m1", fn
        )

        assert requests[0]["db_path"] == str(store_path)
        assert requests[0]["texts"] == ["hot", "cold", "cold"]
        assert vectors == [[9.0, 8.0], [4.0, 1.0], [4.0, 1.0]]
        assert stats == {"hits": 1, "misses": 2, "unique_misses": 1, "dirty": 1}
        assert calls == [["cold"]]
        assert len(_cache_rows(conn)) == 1

    def test_rust_cli_failure_falls_back_to_python(self, tmp_path, monkeypatch, capsys):
        from helpers.core import embed_cache as EC
        from helpers.core import vec_search as VS

        store_path = tmp_path / "embed_store.db"
        monkeypatch.setattr(VS, "EMBED_DB_PATH", store_path)
        monkeypatch.setenv(EC._RUST_LOOKUP_ENV, "1")
        monkeypatch.setattr(EC, "_RUST_LOOKUP_WARNED", False)
        monkeypatch.setattr(EC, "_rust_lookup_binary", lambda: tmp_path / "mock-rust")
        monkeypatch.setattr(
            EC.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=2, stdout="", stderr="read failed"
            ),
        )
        conn = sqlite3.connect(tmp_path / "main.db")
        VS._attach_vec_db(conn)
        conn.execute(EC.EMBED_CACHE_DDL)
        conn.commit()
        fn, calls = _fake_embed()

        vectors, stats = cached_embed_batch(conn, ["miss"], "m1", fn)

        assert vectors == [[4.0, 1.0]]
        assert stats["misses"] == 1
        assert calls == [["miss"]]
        assert "read failed" in capsys.readouterr().err

    def test_opt_in_timing_splits_lookup_from_miss_embed(self, tmp_path, monkeypatch):
        from helpers.core import embed_cache as EC
        from helpers.core import vec_search as VS

        store_path = tmp_path / "embed_store.db"
        monkeypatch.setattr(VS, "EMBED_DB_PATH", store_path)
        monkeypatch.setenv(EC._RUST_LOOKUP_TIMING_ENV, "1")
        monkeypatch.setattr(
            EC,
            "_rust_cache_lookup",
            lambda _conn, _texts, _model: ({0: [9.0, 8.0]}, [1]),
        )
        conn = sqlite3.connect(tmp_path / "main.db")
        VS._attach_vec_db(conn)
        conn.execute(EC.EMBED_CACHE_DDL)
        conn.commit()
        fn, calls = _fake_embed()

        vectors, stats = cached_embed_batch(conn, ["hot", "cold"], "m1", fn)

        assert vectors == [[9.0, 8.0], [4.0, 1.0]]
        assert calls == [["cold"]]
        assert stats["timing"]["backend"] == "rust"
        assert stats["timing"]["cache_lookup_seconds"] >= 0
        assert stats["timing"]["miss_embed_callback_seconds"] >= 0


class TestPurgeIsPerCohort:
    """Multi-model store (embedgemma adoption): the granite indexers run
    purge_foreign on every rebuild — scoped to THEIR source cohort, or a
    doc/memory rebuild deletes the script surface's live gemma rows and
    forces a full ~550-row HTTP re-embed on every search-fresh (hit ten
    times on 2026-10-07)."""

    def _seed_multi_model(self, conn):
        from helpers.core.vec_search import _attach_vec_db

        from helpers.core.embed_cache import EMBED_CACHE_DDL

        _attach_vec_db(conn)  # :memory: main -> hermetic in-memory vecdb
        conn.execute(EMBED_CACHE_DDL)
        rows = [
            ("h-gemma-script", "embeddinggemma-2-q8_512", "script"),
            ("h-granite-script", "granite-embedding-97m-r2", "script"),
            ("h-gemma-doc", "embeddinggemma-2-q8_512", "doc"),
            ("h-granite-doc", "granite-embedding-97m-r2", "doc"),
        ]
        for h, model, source in rows:
            conn.execute(
                f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} VALUES (?, ?, ?, ?)",  # noqa: S608
                (h, model, b"\x00" * 8, source),
            )
        conn.commit()

    def _labels(self, conn):
        return sorted(
            (r[0], r[1])
            for r in conn.execute(f"SELECT text_hash, model FROM {EMBED_CACHE_TABLE}")  # noqa: S608
        )

    def test_doc_purge_keeps_other_cohorts(self, tmp_path):
        from helpers.core.embed_cache import purge_foreign_models

        conn = _conn(tmp_path)
        self._seed_multi_model(conn)
        gone = purge_foreign_models(conn, "granite-embedding-97m-r2", "doc")
        assert gone == 1  # only doc's own gemma row dies
        assert self._labels(conn) == [
            ("h-gemma-script", "embeddinggemma-2-q8_512"),
            ("h-granite-doc", "granite-embedding-97m-r2"),
            ("h-granite-script", "granite-embedding-97m-r2"),
        ]

    def test_legacy_global_purge_when_no_cohort(self, tmp_path):
        from helpers.core.embed_cache import purge_foreign_models

        conn = _conn(tmp_path)
        self._seed_multi_model(conn)
        gone = purge_foreign_models(conn, "granite-embedding-97m-r2")
        assert gone == 2  # source-less callers keep the legacy global GC
        assert self._labels(conn) == [
            ("h-granite-doc", "granite-embedding-97m-r2"),
            ("h-granite-script", "granite-embedding-97m-r2"),
        ]

    def test_batch_purge_uses_its_source_cohort(self, tmp_path):
        from helpers.core.embed_cache import _hash

        conn = _conn(tmp_path)
        self._seed_multi_model(conn)
        fn, calls = _fake_embed()
        cached_embed_batch(
            conn,
            ["aa"],
            "granite-embedding-97m-r2",
            fn,
            source="doc",
            purge_foreign=True,
        )
        assert self._labels(conn) == [
            (_hash("aa"), "granite-embedding-97m-r2"),  # newly embedded
            ("h-gemma-script", "embeddinggemma-2-q8_512"),
            ("h-granite-doc", "granite-embedding-97m-r2"),
            ("h-granite-script", "granite-embedding-97m-r2"),
        ]
        assert calls == [["aa"]]


class TestPoolWorkers:
    """_pool_workers decision logic (pure, hermetic)."""

    def test_below_min_texts_is_in_process(self):
        from helpers.core.local_embedder import _pool_workers

        assert _pool_workers(3, 4) == 0
        assert _pool_workers(7, 8) == 0

    def test_explicit_disable(self):
        from helpers.core.local_embedder import _pool_workers

        assert _pool_workers(100, 0) == 0
        assert _pool_workers(100, 1) == 0

    def test_default_and_clamping(self):
        from helpers.core.local_embedder import _pool_workers

        assert _pool_workers(100, None) == 4
        assert _pool_workers(10, 8) == 8
        assert _pool_workers(3, 8) == 0  # clamped away by text count

    def test_env_knob(self, monkeypatch):
        from helpers.core import local_embedder as LE

        monkeypatch.setenv("EMBED_POOL_WORKERS", "0")
        assert LE._pool_workers(100, None) == 0
        monkeypatch.setenv("EMBED_POOL_WORKERS", "2")
        assert LE._pool_workers(100, None) == 2
        monkeypatch.setenv("EMBED_POOL_WORKERS", "bogus")
        assert LE._pool_workers(100, None) == 4  # unparsable -> default


class TestNoIndexerAutoPurges:
    """2026-10-08 doctrine: NO automatic model-difference purge anywhere.

    The cache is a (text_hash, model) memo — a stamp-mismatched index can
    never READ another model's rows, so foreign rows are rollback
    insurance, not garbage; dead-text eviction belongs to `make embed-gc`.
    Three live incidents (global purge, per-cohort purge, then a
    sidecar-down fallback rebuild that flipped the stamp and purged with
    it) each destroyed the adopted gemma script cache before this landed.
    ``purge_foreign_models`` survives as an explicit one-off retirement
    tool; its tests live in TestPurgeIsPerCohort / test_vec_search."""

    PRODUCTION_FILES = (
        "helpers/core/embed_cache.py",
        "helpers/maintenance/rebuild_note_search.py",
        "helpers/maintenance/rebuild_doc_search.py",
        "helpers/maintenance/rebuild_memory_search.py",
        "helpers/maintenance/rebuild_convo_search.py",
        "helpers/maintenance/rebuild_script_search.py",
        "helpers/graph/embeddings.py",
    )

    def test_no_production_caller_opts_into_purge(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for rel in self.PRODUCTION_FILES:
            for i, line in enumerate(
                (root / rel).read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "purge_foreign=True" in line or "purge_foreign=not" in line:
                    offenders.append(f"{rel}:{i}")
        assert offenders == []


class TestCachedEmbedDegradation:
    """S1 (doc_leg_embed_cache_persistence, 2026-10-17): a busy embed_store
    at construction used to silently degrade a WHOLE pass to uncached
    (377 doc embeds computed that night, zero stored, nothing reported).
    The wrapper must degrade loudly, count store failures, and recover
    lazily instead of staying dead for the pass."""

    def test_init_failure_degrades_loudly(self, tmp_path, capsys, monkeypatch):
        import helpers.core.vec_search as VS

        from helpers.core.embed_cache import CachedEmbed

        def boom(conn):
            raise sqlite3.Error("no sidecar for you")

        monkeypatch.setattr(VS, "_attach_vec_db", boom)
        ce = CachedEmbed(lambda t: [0.0, 1.0], "m1", _conn(tmp_path), source="doc")
        assert ce._ok is False
        line = ce.status_line()
        assert line.startswith("OFF")
        assert "no sidecar for you" in line
        err = capsys.readouterr().err
        assert "[embed-cache] attach failed" in err
        assert "UNCACHED" in err

    def test_calls_still_succeed_uncached(self, tmp_path, monkeypatch):
        import helpers.core.vec_search as VS

        from helpers.core.embed_cache import CachedEmbed

        def boom(conn):
            raise sqlite3.Error("still no sidecar")

        monkeypatch.setattr(VS, "_attach_vec_db", boom)
        calls: list[str] = []
        ce = CachedEmbed(
            lambda t: calls.append(t) or [0.0, 1.0],
            "m1",
            _conn(tmp_path),
            source="doc",
        )
        assert ce("aa") == [0.0, 1.0]
        assert calls == ["aa"]  # embedded, just uncached
        assert ce.misses == 1 and ce.dirty == 0

    def test_lazy_reinit_recovers_mid_pass(self, tmp_path, capsys, monkeypatch):
        import helpers.core.vec_search as VS

        import helpers.core.embed_cache as EC
        from helpers.core.embed_cache import CachedEmbed

        attempts = {"n": 0}
        real_attach = VS._attach_vec_db

        def flaky(conn):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.Error("locked (constructor)")
            real_attach(conn)

        monkeypatch.setattr(VS, "_attach_vec_db", flaky)
        monkeypatch.setattr(EC, "_REINIT_EVERY", 2)
        ce = CachedEmbed(lambda t: [0.0, 1.0], "m1", _conn(tmp_path), source="doc")
        assert ce._ok is False  # constructor hit the busy window
        assert ce("aa") == [0.0, 1.0]  # first miss triggers the lazy retry
        assert ce._ok is True
        assert ce.dirty == 1  # recovered mid-pass: this embed IS cached
        assert "recovered after 0 uncached miss" in capsys.readouterr().err
        assert ce.status_line().startswith("on")

    def test_store_failure_counted_and_surfaced(self, tmp_path, capsys):
        from helpers.core.embed_cache import CachedEmbed

        conn = _conn(tmp_path)
        ce = CachedEmbed(lambda t: [0.0, 1.0], "m1", conn, source="doc")
        conn.execute("DROP TABLE vecdb.embed_cache")
        conn.commit()
        assert ce("aa") == [0.0, 1.0]  # vector still returned
        assert ce._store_errors == 1
        assert "1 store failures" in ce.status_line()
        err = capsys.readouterr().err
        assert "[embed-cache] cache store failed" in err

    def test_healthy_status_is_quiet(self, tmp_path):
        from helpers.core.embed_cache import CachedEmbed

        ce = CachedEmbed(lambda t: [0.0, 1.0], "m1", _conn(tmp_path), source="doc")
        ce("aa")
        assert ce.status_line() == "on (hits=0, misses=1, cached=1)"
