"""
Tests for the shared query vector (shared_query_vector proposal, S3).

The master_query parent embeds once and fans the vector out instead of
each leg loading the GGUF (4 loads -> 1, ~5s -> ~2s wall). Tiers:

- Hermetic (always run): carrier round-trip/parse/check rules, fan-out
  protocol (same object to every leg, None in bm25/empty), and a tmp
  docs index proving shared-vs-own bit-identical + mismatch fallback +
  garbled-flag fallback at the CLI.
- Live (needs_model): all five cores on the real sidecars — shared vs
  own identical, tampered-label falls back to own (never silent
  garbage).
"""

import importlib.util
import json
import math

import pytest

from helpers.maintenance import rebuild_common as rbc
from helpers.misc import master_query as mq

_BACKEND = importlib.util.find_spec("llama_cpp") is not None
needs_model = pytest.mark.skipif(
    not _BACKEND,
    reason="llama-cpp-python + pinned GGUF not present",
)


@pytest.fixture
def real_backend(monkeypatch):
    """Re-enable the genuine availability check behind the conftest pin
    (same pattern as test_local_embedder.py) — live tests need the real
    granite model, not the pseudo fallback."""
    from helpers.core import local_embedder as le

    monkeypatch.setattr(le, "available", lambda: True)
    assert le.available()


def _qv(model="m", dims=3, vec=(1.0, 0.0, 0.0)):
    return rbc.QueryVector(vector=vec, model=model, dims=dims)


# --------------------------------------------------------------------------- #
# Hermetic: carrier                                                           #
# --------------------------------------------------------------------------- #
class TestCarrier:
    def test_round_trip(self):
        qv = _qv("granite", 3, (0.1, 0.2, 0.3))
        back = rbc.parse_query_vector_flag(qv.to_json())
        assert back == qv

    def test_missing_flag_is_none(self):
        assert rbc.parse_query_vector_flag(None) is None

    def test_garbage_flag_is_none(self, capsys):
        assert rbc.parse_query_vector_flag("not-json") is None
        assert "WARNING" in capsys.readouterr().err

    def test_dims_guarded(self, capsys):
        bad = json.dumps({"model": "m", "dims": 5, "vector": [1.0, 2.0]})
        assert rbc.parse_query_vector_flag(bad) is None
        assert "WARNING" in capsys.readouterr().err

    def test_check_accepts_match(self):
        assert rbc.check_query_vector("m", 3, _qv()) == [1.0, 0.0, 0.0]

    def test_check_missing_label_keeps_dims_only(self):
        # Old sidecars predate the stamp: dims match still scores.
        assert rbc.check_query_vector(None, 3, _qv()) == [1.0, 0.0, 0.0]

    def test_check_rejects_dims_mismatch(self):
        assert rbc.check_query_vector("m", 4, _qv()) is None

    def test_check_rejects_label_mismatch(self):
        # Same dims, different model space must never silently rank.
        assert rbc.check_query_vector("other", 3, _qv()) is None

    def test_check_rejects_nones(self):
        assert rbc.check_query_vector("m", 3, None) is None
        assert rbc.check_query_vector("m", None, _qv()) is None

    def test_make_empty_is_none(self):
        assert rbc.make_query_vector("   ") is None

    def test_make_failure_is_none(self, monkeypatch):
        from helpers.core import local_embedder as le

        monkeypatch.setattr(le, "available", lambda: True)
        monkeypatch.setattr(
            le, "embed_query", lambda _t: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        assert rbc.make_query_vector("hello") is None


# --------------------------------------------------------------------------- #
# Hermetic: fan-out protocol                                                  #
# --------------------------------------------------------------------------- #
class TestFanOutProtocol:
    def test_hybrid_shares_one_object(self, monkeypatch):
        seen = {}

        def spy(lane, query, limit, mode="hybrid", query_vec=None):
            seen[lane] = query_vec
            return [], "0 hits"

        canned = _qv("granite", 3)
        monkeypatch.setattr(rbc, "make_query_vector", lambda _q: canned)
        mq.fan_out("q", ["docs", "scripts", "convo"], 2, lane_runner=spy, parallel=False)
        assert set(seen) == {"docs", "scripts", "convo"}
        assert all(v is canned for v in seen.values())

    def test_bm25_embeds_nothing(self, monkeypatch):
        seen = {}

        def spy(lane, query, limit, mode="hybrid", query_vec=None):
            seen[lane] = query_vec
            return [], "0 hits"

        monkeypatch.setattr(
            rbc,
            "make_query_vector",
            lambda _q: (_ for _ in ()).throw(AssertionError("must not embed")),
        )
        mq.fan_out("q", ["docs", "memory"], 2, mode="bm25", lane_runner=spy, parallel=False)
        assert all(v is None for v in seen.values())

    def test_empty_query_embeds_nothing(self, monkeypatch):
        monkeypatch.setattr(
            rbc,
            "make_query_vector",
            lambda _q: (_ for _ in ()).throw(AssertionError("must not embed")),
        )
        out = mq.fan_out("", ["docs"], 2, parallel=False)
        assert out["docs"][1] == "empty query"


# --------------------------------------------------------------------------- #
# Hermetic docs leg: tmp index + fake embedder                                #
# --------------------------------------------------------------------------- #
_GUIDE = "# Guide\n\n## Cache Design\nhow the cache warms up\n"


@pytest.fixture
def seeded_docs(tmp_path, monkeypatch):
    from helpers.maintenance import rebuild_doc_search as rds

    doc_root = tmp_path / "doc"
    doc_root.mkdir()
    (doc_root / "guide.md").write_text(_GUIDE, encoding="utf-8")
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_DB", tmp_path / "doc_search.db")
    monkeypatch.setattr(rds, "BACKUP_DIR", tmp_path / "db-backup")
    return rds


@pytest.fixture
def fake_local(monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        v = [0.0] * 8
        if "cache" in text.lower():
            v[2] = 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    return LE


class TestDocsHermetic:
    def test_shared_matches_own(self, seeded_docs, fake_local):
        from helpers.core import local_embedder as le

        rds = seeded_docs
        rds.rebuild(write=True)
        conn = rds.connect_doc_db(rds.DOC_DB)
        try:
            own = rds.search_docs(conn, "cache design", limit=5)
            qv = rbc.QueryVector(tuple(le.embed_query("cache design")), le.MODEL_ID, 8)
            shared = rds.search_docs(conn, "cache design", limit=5, query_vec=qv)
        finally:
            conn.close()
        assert shared == own
        assert shared["mode"] == "hybrid"

    def test_label_mismatch_falls_back(self, seeded_docs, fake_local):
        rds = seeded_docs
        rds.rebuild(write=True)
        conn = rds.connect_doc_db(rds.DOC_DB)
        try:
            own = rds.search_docs(conn, "cache design", limit=5)
            bad = rbc.QueryVector(vector=(1.0,) * 8, model="tampered", dims=8)
            fell = rds.search_docs(conn, "cache design", limit=5, query_vec=bad)
        finally:
            conn.close()
        assert fell == own  # fallback to local embed, never silent garbage

    def test_shared_branch_skips_local_load(self, seeded_docs, fake_local, monkeypatch):
        # Own embedder rigged to blow up: a correctly-stamped shared vector
        # must still score hybrid — proving the core took the shared branch
        # and never touched the model.
        from helpers.core import local_embedder as le

        rds = seeded_docs
        rds.rebuild(write=True)

        def _boom(_t: str):
            raise RuntimeError("must not embed locally")

        monkeypatch.setattr(rds, "query_embedder", lambda: (_boom, 8))
        qv = rbc.QueryVector(tuple(le.embed_query("cache design")), le.MODEL_ID, 8)
        conn = rds.connect_doc_db(rds.DOC_DB)
        try:
            out = rds.search_docs(conn, "cache design", limit=5, query_vec=qv)
        finally:
            conn.close()
        assert out["mode"] == "hybrid"
        assert out["results"]

    def test_reject_plus_dead_embedder_degrades_to_bm25(self, seeded_docs, fake_local, monkeypatch):
        # Rejected vector AND dead local embedder: BM25, never a raise.
        rds = seeded_docs
        rds.rebuild(write=True)

        def _boom(_t: str):
            raise RuntimeError("no model here")

        monkeypatch.setattr(rds, "query_embedder", lambda: (_boom, 8))
        bad = rbc.QueryVector(vector=(1.0,) * 8, model="tampered", dims=8)
        conn = rds.connect_doc_db(rds.DOC_DB)
        try:
            out = rds.search_docs(conn, "cache design", limit=5, query_vec=bad)
        finally:
            conn.close()
        assert out["mode"] == "bm25"

    def test_cli_flag_identical_and_garbage_warns(self, seeded_docs, fake_local, capsys):
        from helpers.misc import doc_query
        from helpers.core import local_embedder as le

        rds = seeded_docs
        rds.rebuild(write=True)
        assert doc_query.main(["cache", "--json"]) == 0
        own = json.loads(capsys.readouterr().out)
        qv = rbc.QueryVector(tuple(le.embed_query("cache")), le.MODEL_ID, 8)
        assert doc_query.main(["cache", "--json", "--query-vector", qv.to_json()]) == 0
        assert json.loads(capsys.readouterr().out) == own
        assert doc_query.main(["cache", "--json", "--query-vector", "garbage"]) == 0
        cap = capsys.readouterr()
        assert "WARNING" in cap.err
        assert json.loads(cap.out) == own


# --------------------------------------------------------------------------- #
# Hermetic notes leg: tmp research.db + real matrix files in the          #
# conftest-redirected tmp dir (live matrix is off-limits under pytest)    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def seeded_notes(tmp_path, monkeypatch):
    import sqlite3

    import numpy as np

    from helpers.core.embed_matrix import EmbedMatrixStore
    from helpers.core.vec_codec import pack_f32
    from helpers.misc import note_query as nq

    db = tmp_path / "research.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE VIRTUAL TABLE note_search USING fts5(doc_type, file_path UNINDEXED, "
        "title, sector, content, embedding UNINDEXED, section_title, anchor UNINDEXED, "
        "tokenize = 'porter unicode61')"
    )
    vecs = {
        "(cache.md, cache)": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "zzz.md, other)": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    rows = [
        ("note", fp, "Cache note", "s", "how the cache warms up", pack_f32(v), "Cache", a)
        for (fp, a), v in {
            ("cache.md", "cache"): vecs["(cache.md, cache)"],
            ("zzz.md", "other"): vecs["zzz.md, other)"],
        }.items()
    ]
    conn.executemany(
        "INSERT INTO note_search (doc_type, file_path, title, sector, content, "
        "embedding, section_title, anchor) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    from helpers.core import local_embedder as _le

    conn.execute("INSERT INTO db_meta(key, value) VALUES ('note_embed_model', ?)", (_le.MODEL_ID,))
    conn.commit()
    conn.close()
    ids = ["cache.md#cache", "zzz.md#other"]
    emb = np.array(
        [
            vecs["(cache.md, cache)"],
            vecs["zzz.md, other)"],
        ],
        dtype=np.float32,
    )
    EmbedMatrixStore().build(ids, emb, model="note_search")
    nq.load_notes_matrix.cache_clear()
    return db


class TestNotesHermetic:
    def test_shared_matches_own(self, seeded_notes, fake_local):
        from helpers.core import local_embedder as le
        from helpers.misc import note_query as nq

        own = nq.semantic_hits(seeded_notes, "cache design", 5)
        qv = rbc.QueryVector(tuple(le.embed_query("cache design")), le.MODEL_ID, 8)
        assert nq.semantic_hits(seeded_notes, "cache design", 5, qv) == own
        assert own[0]

    def test_shared_branch_skips_local_load(self, seeded_notes, fake_local, monkeypatch):
        from helpers.core import local_embedder as le
        from helpers.misc import note_query as nq

        def _boom(_t: str):
            raise RuntimeError("must not embed locally")

        monkeypatch.setattr(nq, "note_query_embedder", lambda: (_boom, 8))
        qv = rbc.QueryVector(tuple(le.embed_query("cache design")), le.MODEL_ID, 8)
        rows, reason = nq.semantic_hits(seeded_notes, "cache design", 5, qv)
        assert rows, reason

    def test_label_mismatch_falls_back(self, seeded_notes, fake_local):
        from helpers.misc import note_query as nq

        own = nq.semantic_hits(seeded_notes, "cache design", 5)
        bad = rbc.QueryVector(vector=(1.0,) * 8, model="tampered", dims=8)
        assert nq.semantic_hits(seeded_notes, "cache design", 5, bad) == own


# --------------------------------------------------------------------------- #
# Live: all five cores, shared vs own + fallback                              #
# --------------------------------------------------------------------------- #
_Q = "embed cache"
_LIM = 3


def _live_docs():
    from helpers.maintenance import rebuild_doc_search as rds

    conn = rds.connect_doc_db(rds.DOC_DB)
    return rds, conn


def _live_scripts():
    from helpers.maintenance import rebuild_script_search as rss

    conn = rss.connect_script_db(rss.SCRIPT_DB)
    return rss, conn


def _live_memory():
    from helpers.core.db import connect as db_connect
    from helpers.maintenance import rebuild_memory_search as rms

    conn = db_connect(rms.MEMORY_DB, read_only=True, wal=False)
    return rms, conn


@needs_model
class TestLiveBitIdentical:
    @pytest.fixture(autouse=True)
    def _real(self, real_backend):
        return real_backend

    def test_docs(self):
        rds, conn = _live_docs()
        try:
            own = rds.search_docs(conn, _Q, limit=_LIM)
            assert rds.search_docs(conn, _Q, limit=_LIM, query_vec=rbc.make_query_vector(_Q)) == own
        finally:
            conn.close()

    def test_scripts(self):
        rss, conn = _live_scripts()
        try:
            own = rss.search_scripts(conn, _Q, limit=_LIM)
            assert (
                rss.search_scripts(conn, _Q, limit=_LIM, query_vec=rbc.make_query_vector(_Q)) == own
            )
        finally:
            conn.close()

    def test_memory(self):
        rms, conn = _live_memory()
        try:
            own = rms.search_memories(conn, _Q, limit=_LIM)
            assert (
                rms.search_memories(conn, _Q, limit=_LIM, query_vec=rbc.make_query_vector(_Q))
                == own
            )
        finally:
            conn.close()

    def test_notes(self):
        from helpers.misc import note_query as nq

        own = nq.semantic_hits(nq.DEFAULT_DB, _Q, _LIM)
        assert nq.semantic_hits(nq.DEFAULT_DB, _Q, _LIM, rbc.make_query_vector(_Q)) == own

    def test_convo(self):
        from helpers.misc import convo_query as cq

        con = cq.connect(cq.DEFAULT_DB)
        try:
            own = cq.search(con, _Q, limit=_LIM)
            assert cq.search(con, _Q, limit=_LIM, query_vec=rbc.make_query_vector(_Q)) == own
        finally:
            con.close()


@needs_model
class TestLiveNoLocalLoad:
    """Every own-embed hook rigged to raise: a valid shared vector must
    still score on all five cores — proving no leg touches the model."""

    @pytest.fixture(autouse=True)
    def _real(self, real_backend):
        return real_backend

    def test_all_legs_hybrid_without_local_embed(self, monkeypatch):
        from helpers.core import local_embedder as le
        from helpers.maintenance import rebuild_doc_search as rds

        qv = rbc.make_query_vector(_Q)
        assert qv is not None and len(qv.vector) == 384

        def _boom(_t: str):
            raise RuntimeError("must not embed locally")

        monkeypatch.setattr(rds, "query_embedder", lambda: (_boom, 384))
        monkeypatch.setattr(le, "embed_query", _boom)

        docs_mod, conn = _live_docs()
        try:
            out = docs_mod.search_docs(conn, _Q, limit=_LIM, query_vec=qv)
        finally:
            conn.close()
        assert out["mode"] == "hybrid" and out["results"]

        rss_mod, conn = _live_scripts()
        try:
            out = rss_mod.search_scripts(conn, _Q, limit=_LIM, query_vec=qv)
        finally:
            conn.close()
        assert out["mode"] == "hybrid" and out["results"]

        rms_mod, conn = _live_memory()
        try:
            out = rms_mod.search_memories(conn, _Q, limit=_LIM, query_vec=qv)
        finally:
            conn.close()
        assert out["mode"] == "hybrid" and out["results"]

        # notes: the live matrix is conftest-redirected to tmp under pytest,
        # so the live leg is vacuously empty here — the hermetic
        # TestNotesHermetic pins the notes shared branch instead.

        from helpers.misc import convo_query as cq

        con = cq.connect(cq.DEFAULT_DB)
        try:
            out = cq.search(con, _Q, limit=_LIM, query_vec=qv)
        finally:
            con.close()
        assert out["mode"] == "hybrid" and out["results"]


@needs_model
class TestLiveFallback:
    """Tampered-label vector on every core: reject + local embed = own."""

    @pytest.fixture(autouse=True)
    def _real(self, real_backend):
        return real_backend

    def _tampered(self):
        qv = rbc.make_query_vector(_Q)
        assert qv is not None
        return rbc.QueryVector(vector=qv.vector, model="tampered-label", dims=qv.dims)

    def test_docs(self):
        rds, conn = _live_docs()
        try:
            own = rds.search_docs(conn, _Q, limit=_LIM)
            assert rds.search_docs(conn, _Q, limit=_LIM, query_vec=self._tampered()) == own
        finally:
            conn.close()

    def test_scripts(self):
        rss, conn = _live_scripts()
        try:
            own = rss.search_scripts(conn, _Q, limit=_LIM)
            assert rss.search_scripts(conn, _Q, limit=_LIM, query_vec=self._tampered()) == own
        finally:
            conn.close()

    def test_memory(self):
        rms, conn = _live_memory()
        try:
            own = rms.search_memories(conn, _Q, limit=_LIM)
            assert rms.search_memories(conn, _Q, limit=_LIM, query_vec=self._tampered()) == own
        finally:
            conn.close()

    def test_notes(self):
        from helpers.misc import note_query as nq

        own = nq.semantic_hits(nq.DEFAULT_DB, _Q, _LIM)
        assert nq.semantic_hits(nq.DEFAULT_DB, _Q, _LIM, self._tampered()) == own

    def test_convo(self):
        from helpers.misc import convo_query as cq

        con = cq.connect(cq.DEFAULT_DB)
        try:
            own = cq.search(con, _Q, limit=_LIM)
            assert cq.search(con, _Q, limit=_LIM, query_vec=self._tampered()) == own
        finally:
            con.close()
