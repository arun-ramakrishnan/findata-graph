# Tests for helpers/core/embed_cache.py
"""Unit tests for the shared (sha256(text), model) embedding cache.

The per-text ``CachedEmbed`` wrapper is exercised end-to-end through the
rebuild_note_search tests (cold/warm/model-label/pre-warm); these tests pin
the BATCH interface the company-embeddings populate uses, plus the shared
module's own contracts (model keying, degradation, length guard).

The store is DuckDB (S0, 2026-11-15): the sqlite ATTACH backend
(``_attach_vec_db`` / ``vecdb`` alias) is gone, and every test uses a
hermetic :memory: DuckDB connection via ``_resolve_cache_conn``.
"""

import duckdb
import pytest

from helpers.core import embed_cache as EC
from helpers.core import vec_search as VS
from helpers.core.embed_cache import EMBED_CACHE_TABLE, cached_embed_batch


def _conn():
    """Hermetic DuckDB cache connection (in-memory store)."""
    return duckdb.connect(":memory:")


def _fake_embed():
    """Embedder that records its calls and maps text -> a recognizable vector."""
    calls: list[list[str]] = []

    def fn(texts):
        calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]

    return fn, calls


def _cache_rows(conn):
    try:
        return conn.execute(
            f"SELECT text_hash, model FROM {EMBED_CACHE_TABLE}"  # noqa: S608  # constant table name
        ).fetchall()
    except duckdb.CatalogException:
        return []  # no cache table -> nothing cached


class TestCachedEmbedBatch:
    def test_cold_embeds_all_in_one_batch_and_seeds(self):
        conn = _conn()
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        assert st == {"hits": 0, "misses": 2, "unique_misses": 2, "dirty": 2}
        assert calls == [["aa", "bbb"]]  # ONE batch call for all misses
        assert [v[0] for v in vecs] == [2.0, 3.0]
        assert len(_cache_rows(conn)) == 2

    def test_warm_serves_from_cache_without_embedding(self):
        conn = _conn()
        fn, calls = _fake_embed()
        v1, _ = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        v2, st = cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        assert st == {"hits": 2, "misses": 0, "unique_misses": 0, "dirty": 0}
        assert len(calls) == 1  # still only the cold call
        assert v1 == v2  # cache round-trips vectors faithfully

    def test_partial_miss_reembeds_only_the_missing_texts(self):
        conn = _conn()
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa", "bbb"], "m1", fn)
        vecs, st = cached_embed_batch(conn, ["aa", "ccc"], "m1", fn)
        assert st == {"hits": 1, "misses": 1, "unique_misses": 1, "dirty": 1}
        assert calls[-1] == ["ccc"]  # only the new text is embedded
        assert vecs[0] == [2.0, 1.0]  # 'aa' came back from cache
        assert vecs[1][0] == 3.0

    def test_cache_is_keyed_by_model_label(self):
        conn = _conn()
        fn, _ = _fake_embed()
        cached_embed_batch(conn, ["aa"], "m1", fn)
        cached_embed_batch(conn, ["aa"], "m2", fn)
        models = {m for _, m in _cache_rows(conn)}
        assert models == {"m1", "m2"}  # same text, different model -> 2 rows

    def test_empty_text_list_never_calls_the_embedder(self):
        conn = _conn()
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, [], "m1", fn)
        assert vecs == []
        assert st == {"hits": 0, "misses": 0, "unique_misses": 0, "dirty": 0}
        assert calls == []

    def test_short_embedder_reply_raises(self):
        conn = _conn()

        def bad_fn(texts):
            return [[0.0, 1.0]]  # too short -> protocol violation

        with pytest.raises(ValueError, match="batch embedder returned"):
            cached_embed_batch(conn, ["aa", "bbb"], "m1", bad_fn)
        # the botched batch must not have been cached
        assert _cache_rows(conn) == []

    def test_no_sidecar_degrades_to_uncached(self, monkeypatch):
        def boom(*args, **kwargs):
            raise duckdb.Error("no sidecar for you")

        monkeypatch.setattr(VS, "_resolve_cache_conn", boom)
        conn = _conn()
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(conn, ["aa"], "m1", fn)
        assert st == {"hits": 0, "misses": 1, "unique_misses": 1, "dirty": 0}
        assert calls == [["aa"]]  # embedder still ran, uncached
        assert vecs == [[2.0, 1.0]]
        assert _cache_rows(conn) == []  # nothing persisted

    def test_corrupted_cache_row_counts_as_a_miss(self):
        conn = _conn()
        fn, calls = _fake_embed()
        cached_embed_batch(conn, ["aa"], "m1", fn)
        # Corrupt the row: a 3-byte blob cannot be unpacked as f32
        # (array('f').frombytes raises ValueError) -> counts as a miss.
        conn.execute("UPDATE embed_cache SET embedding = ?", (b"\x00\x01\x02",))
        vecs, st = cached_embed_batch(conn, ["aa"], "m1", fn)
        assert st == {"hits": 0, "misses": 1, "unique_misses": 1, "dirty": 1}
        assert len(calls) == 2  # re-embedded, and the row was healed
        assert vecs == [[2.0, 1.0]]

    def test_read_through_copies_pool_rows_before_embedding(self, tmp_path, monkeypatch):
        """S4b (duckdb_fts_sql_topk_regression): a cold sidecar cache
        copies donor rows (same text_hash+model) from the shared pool
        before paying for fresh embeddings."""
        from helpers.core.vec_codec import pack_f32

        sidecar = duckdb.connect(str(tmp_path / "sidecar.duckdb"))
        sidecar.execute(EC.EMBED_CACHE_DDL)
        pool = duckdb.connect(str(tmp_path / "pool.duckdb"))
        pool.execute(EC.EMBED_CACHE_DDL)
        donor = "the donor text"
        pool.execute(
            "INSERT OR REPLACE INTO embed_cache (text_hash, model, embedding, source) "
            "VALUES (?, ?, ?, ?)",
            [EC._hash(donor), "m1", pack_f32([9.0, 9.0]), "doc"],
        )
        monkeypatch.setattr(VS, "open_cache_conn", lambda read_only=False: pool)
        fn, calls = _fake_embed()
        vecs, st = cached_embed_batch(sidecar, [donor, "other"], "m1", fn, source="cold")
        assert vecs[0] == [9.0, 9.0]  # donor served from pool, not embedded
        assert vecs[1] == [5.0, 1.0]  # remaining miss embedded normally
        assert st["read_through"] == 1
        assert calls == [["other"]]  # only the real miss hit the embedder
        # donor row now exists locally with the local cohort source; the
        # pool row is untouched (source still "doc") — re-open the pool
        # file read-only (the read-through path closed its handle).
        local = sidecar.execute(
            "SELECT source FROM embed_cache WHERE text_hash = ?",
            [EC._hash(donor)],
        ).fetchone()
        assert local == ("cold",)
        pool_check = duckdb.connect(str(tmp_path / "pool.duckdb"), read_only=True)
        pool_source = pool_check.execute(
            "SELECT source FROM embed_cache WHERE text_hash = ?",
            [EC._hash(donor)],
        ).fetchone()
        assert pool_source == ("doc",)
        pool_check.close()
        sidecar.close()
        pool.close()


class TestPurgeIsPerCohort:
    """Per-cohort purge. The cohort-in-``INSERT OR REPLACE``-statement trick
    is a property of the sqlite-era store; the DuckDB store keeps the same
    table shape, so the fixtures now seed rows directly on the DuckDB conn."""

    def _seed_multi_model(self, conn):
        rows = [
            ("h-gemma-script", "embeddinggemma-2-q8_512", b"\x00" * 8, "script"),
            ("h-granite-script", "granite-embedding-97m-r2", b"\x00" * 8, "script"),
            ("h-gemma-doc", "embeddinggemma-2-q8_512", b"\x00" * 8, "doc"),
            ("h-granite-doc", "granite-embedding-97m-r2", b"\x00" * 8, "doc"),
        ]
        conn.execute(
            f"CREATE TABLE {EMBED_CACHE_TABLE} (text_hash TEXT, model TEXT, embedding BLOB, source TEXT, PRIMARY KEY (text_hash, model))"
        )
        conn.executemany(f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} VALUES (?, ?, ?, ?)", rows)
        conn.commit()

    def _labels(self, conn):
        return sorted(conn.execute(f"SELECT text_hash, model FROM {EMBED_CACHE_TABLE}").fetchall())

    def test_doc_purge_keeps_other_cohorts(self):
        conn = _conn()
        self._seed_multi_model(conn)
        gone = EC.purge_foreign_models(conn, "granite-embedding-97m-r2", "doc")
        assert gone == 1
        # doc cohort keeps only the active model; its gemma rows go,
        # while other cohorts are untouched.
        assert self._labels(conn) == [
            ("h-gemma-script", "embeddinggemma-2-q8_512"),
            ("h-granite-doc", "granite-embedding-97m-r2"),
            ("h-granite-script", "granite-embedding-97m-r2"),
        ]

    def test_legacy_global_purge_when_no_cohort(self):
        conn = _conn()
        self._seed_multi_model(conn)
        # No cohort argument -> global purge: every row of any OTHER
        # model is dropped.
        gone = EC.purge_foreign_models(conn, "granite-embedding-97m-r2")
        assert gone == 2
        assert self._labels(conn) == [
            ("h-granite-doc", "granite-embedding-97m-r2"),
            ("h-granite-script", "granite-embedding-97m-r2"),
        ]

    def test_batch_purge_uses_its_source_cohort(self):
        conn = _conn()
        self._seed_multi_model(conn)
        fn, _ = _fake_embed()
        # cached_embed_batch's own cohort-purge runs at init; only its
        # source cohort is purged when the model label changes.
        cached_embed_batch(
            conn, ["aa"], "granite-embedding-97m-r2", fn, source="doc", purge_foreign=True
        )
        assert self._labels(conn) == [
            (EC._hash("aa"), "granite-embedding-97m-r2"),
            ("h-gemma-script", "embeddinggemma-2-q8_512"),
            ("h-granite-doc", "granite-embedding-97m-r2"),
            ("h-granite-script", "granite-embedding-97m-r2"),
        ]


class TestPoolWorkers:
    def test_factory_returns_callables(self):
        # Only a smoke check of the shared public shape.
        from helpers.core.embed_cache import CachedEmbed

        conn = _conn()
        ce = CachedEmbed(lambda t: [1.0, 0.0], "m1", conn, source="convo")
        assert ce("aa") == [1.0, 0.0]


class TestNoIndexerAutoPurges:
    """No indexer may pass ``purge_foreign=True`` by default: that would
    make rebuilds of one source purge another source's cache rows (an
    unintended cross-cohort flush). The contract is: explicit opt-in only."""

    def test_no_production_indexer_auto_purges(self):
        # Scan the production tree for default-on purges.
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]

        PRODUCTION_FILES = (
            "helpers/core/embed_cache.py",
            "helpers/maintenance/rebuild_note_search.py",
            "helpers/maintenance/rebuild_doc_search.py",
            "helpers/maintenance/rebuild_memory_search.py",
            "helpers/maintenance/rebuild_convo_search.py",
            "helpers/maintenance/rebuild_script_search.py",
            "helpers/graph/embeddings.py",
        )
        offenders = []
        for rel in PRODUCTION_FILES:
            p = root / rel
            if not p.exists():
                continue
            text = p.read_text()
            if "purge_foreign=True" in text or "purge_foreign=not" in text:
                offenders.append(rel)
        assert offenders == []


class TestCachedEmbedDegradation:
    def _make(self, tmp_path, embed_fn=None):
        from helpers.core.embed_cache import CachedEmbed

        conn = _conn()
        ce = CachedEmbed(embed_fn or (lambda t: [0.0, 1.0]), "m1", conn, source="doc")
        return ce, conn

    def test_init_failure_degrades_loudly(self, tmp_path, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise duckdb.Error("no sidecar for you")

        monkeypatch.setattr(VS, "_resolve_cache_conn", boom)
        ce, _ = self._make(tmp_path)
        assert not ce._ok
        assert ce.status_line().startswith("OFF")
        assert "no sidecar for you" in ce.status_line()
        _, err = capsys.readouterr()
        assert "[embed-cache] init failed" in err
        assert "UNCACHED" in err

    def test_calls_still_succeed_uncached(self, tmp_path, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise duckdb.Error("no sidecar for you")

        monkeypatch.setattr(VS, "_resolve_cache_conn", boom)
        ce, conn = self._make(tmp_path)
        assert ce("aa") == [0.0, 1.0]  # falls back to embedding
        assert ce.calls == ["aa"] if hasattr(ce, "calls") else True
        assert ce.misses == 1
        assert ce.dirty == 0
        _, err = capsys.readouterr()
        assert "UNCACHED" in err

    def test_lazy_reinit_recovers_mid_pass(self, tmp_path, monkeypatch, capsys):
        from helpers.core.embed_cache import CachedEmbed

        attempts = {"n": 0}
        real = VS._resolve_cache_conn

        def flaky(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise duckdb.Error("temporary")
            return real(*args, **kwargs)

        monkeypatch.setattr(VS, "_resolve_cache_conn", flaky)
        monkeypatch.setattr(EC, "_REINIT_EVERY", 2)
        conn = _conn()
        ce = CachedEmbed(lambda t: [0.0, 1.0], "m1", conn, source="doc")
        assert not ce._ok  # init failed on first attempt
        assert ce("aa") == [0.0, 1.0]  # first call: still degraded
        assert ce._ok  # retried and recovered
        assert ce.dirty == 1
        _, err = capsys.readouterr()
        assert "recovered after 0 uncached miss" in err
        assert ce.status_line().startswith("on")

    def test_store_failure_counted_and_surfaced(self, tmp_path, monkeypatch, capsys):
        from helpers.core.embed_cache import CachedEmbed

        conn = _conn()
        ce = CachedEmbed(lambda t: [0.0, 1.0], "m1", conn, source="doc")
        conn.execute("DROP TABLE embed_cache")
        assert ce("aa") == [0.0, 1.0]  # vector still returned
        assert ce._store_errors == 1
        assert "1 store failures" in ce.status_line()
        _, err = capsys.readouterr()
        assert "[embed-cache] cache store failed" in err

    def test_healthy_status_is_quiet(self, tmp_path):
        ce, _ = self._make(tmp_path)
        assert ce("aa") == [0.0, 1.0]
        assert ce.status_line() == "on (hits=0, misses=1, cached=1)"
