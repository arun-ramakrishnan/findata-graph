"""Hermetic tests for the convo_search pipeline (harvest → rebuild → query).

Fixtures build tiny synthetic harness sources in tmp_path (an opencode
sqlite db, a prime jsonl, a zcode rollout) and monkeypatch the source
discovery + embedder so no real model, live db, or snapshot mount is
touched. The real-model wiring itself is covered by the smoke-validated
live path (convo_search proposal, 2026-09-27).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import duckdb
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.maintenance import harvest_conversations as hc  # noqa: E402
from helpers.maintenance import rebuild_convo_search as rcs  # noqa: E402


def _oc_db(path: Path, parts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    con.execute("DELETE FROM part")
    con.execute(
        "INSERT INTO message VALUES ('m1', 's1', 1000, 1000, "
        '\'{"role": "user", "agent": "opencode", "model": "x"}\') '
        "ON CONFLICT(id) DO UPDATE SET data = excluded.data"
    )
    for p in parts:
        con.execute(
            "INSERT OR REPLACE INTO part VALUES (?, 'm1', 's1', ?, ?, ?)",
            (
                p["id"],
                p["tu"],
                p["tu"],
                json.dumps({"type": "text", "text": p["text"]}),
            ),
        )
    con.commit()
    con.close()


@pytest.fixture()
def convo_env(tmp_path, monkeypatch):
    """Synthetic sources + retargeted module constants; returns the tmp root."""
    oc_snap = tmp_path / "snap_old/localhost/home/arun/.local/share/opencode/opencode.db"
    oc_live = tmp_path / "live/opencode.db"
    _oc_db(
        oc_snap,
        [
            {"id": "p1", "tu": 1000, "text": "the igraph decision record"},
            {"id": "p2", "tu": 1001, "text": "layout anchors explained here"},
        ],
    )
    _oc_db(
        oc_live,
        [
            {"id": "p1", "tu": 2000, "text": "the igraph decision record UPDATED"},
            {"id": "p3", "tu": 2001, "text": "a brand new live part"},
        ],
    )
    prime = tmp_path / "home/.prime/agent/sessions/abc.jsonl"
    prime.parent.mkdir(parents=True, exist_ok=True)
    prime.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "pm1",
                "timestamp": 1700000000000,
                "message": {
                    "role": "assistant",
                    "model": "m",
                    "content": [{"type": "text", "text": "prime says hello"}],
                },
            }
        )
        + "\n"
    )
    zc = tmp_path / "home/.zcode/cli/rollout/model-io-sess_z1.jsonl"
    zc.parent.mkdir(parents=True, exist_ok=True)
    zc.write_text(
        json.dumps(
            {
                "requestId": "r1",
                "sessionId": "z1",
                "startedAt": "2026-09-01T00:00:00Z",
                "model": "zm",
                "request": {
                    "messageCount": 1,
                    "messages": [{"role": "user", "content": "zcode asks a question"}],
                },
                "response": {
                    "responseId": "resp1",
                    "text": "zcode answers",
                    "reasoningText": "zcode thinks",
                },
            }
        )
        + "\n"
    )

    monkeypatch.setattr(hc, "SNAP_ROOT", tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home", raising=False)
    # Source lists point at the synthetic dbs (both opencode dbs feed the
    # dedup assertion; prime/zcode live under the patched home).
    monkeypatch.setattr(
        hc,
        "_opencode_sources",
        lambda: [
            (oc_snap, "opencode-snap:t0", True),
            (oc_live, "opencode:live", False),
        ],
    )
    return tmp_path


def test_harvest_cold_dedup_and_incremental(convo_env):
    tmp = convo_env
    db = tmp / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    files = sorted((tmp / "corpus").rglob("*.parquet"))
    assert files, "cold harvest wrote no parquet"
    import pyarrow.parquet as pq

    oc_file = next(f for f in files if "opencode" in str(f))
    rows = {r["part_id"]: r for r in pq.read_table(oc_file).to_pylist()}
    # p1 exists in BOTH dbs — newest time_updated wins (live 2000 > snap 1000)
    assert "UPDATED" in rows["p1"]["text"]
    assert rows["p3"]["text"] == "a brand new live part"
    # the corpus carries no harness column — the directory is the namespace
    assert "opencode" in oc_file.relative_to(tmp).parts
    # every lane harvested: opencode deduped to one session, prime + zcode own theirs
    # (dsh reads the live ~/.dsh — absent here, so no dsh directory lands)
    lanes = {p.relative_to(tmp).parts[1] for p in files}
    assert lanes == {"opencode", "prime-rlm", "zcode"}

    # incremental: every lane quiet, zero new parts
    stats2 = hc.harvest(corpus_root=tmp / "corpus", db=db)
    assert stats2["lanes"].count("total new: 0") == 4


def test_harvest_skips_unreadable_source_and_keeps_the_rest(convo_env, monkeypatch):
    """One corrupt source must not abort the lane (observed 2026-09-29).

    A truncated timeshift image raised a bare `disk I/O error` out of
    `_harvest_opencode_db` and took down every other snapshot AND the live db
    with it. The lane now skips the bad source, names it in its stats, and
    still harvests everything else.
    """
    tmp = convo_env
    oc_snap = tmp / "snap_old/localhost/home/arun/.local/share/opencode/opencode.db"
    oc_live = tmp / "live/opencode.db"
    boom = tmp / "snap_bad/localhost/home/arun/.local/share/opencode/opencode.db"
    boom.parent.mkdir(parents=True, exist_ok=True)
    boom.write_bytes(b"this is not a sqlite database at all")

    real_read = hc._harvest_opencode_db

    def flaky(path, src, hwm):
        if path == boom:
            raise hc.sqlite3.DatabaseError("database disk image is malformed")
        return real_read(path, src, hwm)

    monkeypatch.setattr(hc, "_harvest_opencode_db", flaky)
    monkeypatch.setattr(
        hc,
        "_opencode_sources",
        lambda: [
            (oc_snap, "opencode-snap:t0", True),
            (boom, "opencode-snap:corrupt", True),
            (oc_live, "opencode:live", False),
        ],
    )

    stats = hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    # "lanes" is the lane reports newline-joined — no blank lines, so there is
    # nothing to split on (an earlier draft's split("\n\n") was a silent no-op);
    # assert against the whole report.
    opencode_lane = stats["lanes"]

    # the bad source is named, not swallowed
    assert "SKIP unreadable" in opencode_lane
    assert "corrupt" in opencode_lane
    # ...and the healthy sources either side of it still landed
    assert "opencode-snap:t0" in opencode_lane
    assert "opencode:live" in opencode_lane

    import pyarrow.parquet as pq

    files = sorted((tmp / "corpus").rglob("*.parquet"))
    rows = {
        r["part_id"]: r for f in files if "opencode" in str(f) for r in pq.read_table(f).to_pylist()
    }
    # live's newer p1 still wins the dedup, and its new part is present:
    # proof the harvest continued past the corrupt source
    assert rows["p1"]["text"] == "the igraph decision record UPDATED"
    assert rows["p3"]["text"] == "a brand new live part"


def test_harvest_check_detects_drift(convo_env):
    tmp = convo_env
    db = tmp / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    fresh = hc.harvest(check=True, corpus_root=tmp / "corpus", db=db)
    assert fresh["index_stale"] is False


def test_rebuild_index_and_check(convo_env, monkeypatch):
    tmp = convo_env
    db = tmp / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)

    corpus_files = sorted((tmp / "corpus").rglob("*.parquet"))
    assert corpus_files
    # Retarget the rebuild module's repo root so corpus-relative paths resolve.
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[float(len(t) % 7), 1.0, 0.0] for t in texts],
            {"hits": 0, "misses": len(texts)},
            3,
            "test-model",
        ),
    )

    stats = rcs.rebuild(tmp / "idx" / "convo_search.duckdb", write=True)
    assert stats["indexed"] > 0
    assert stats["total"] == stats["indexed"]
    con = duckdb.connect(str(tmp / "idx" / "convo_search.duckdb"), read_only=True)
    rows = con.execute(
        "SELECT harness, part_id, file_path, embedding FROM convo_search ORDER BY part_id LIMIT 3"
    ).fetchall()
    # every column must survive the Arrow bulk insert intact (T6): ts
    # timezone-naive, text_len == len(snippet), embedding width as stored
    typed = con.execute(
        "SELECT COUNT(*) FROM convo_search "
        "WHERE ts IS NULL OR text_len <> length(snippet) "
        "OR (embedding IS NOT NULL AND len(embedding) <> 3)"
    ).fetchone()
    assert typed is not None
    con.close()
    assert typed[0] == 0, f"{typed[0]} rows lost a column in the bulk insert"
    assert all(r[0] in ("opencode", "prime-rlm", "zcode") for r in rows)
    assert all(r[3] is not None for r in rows)
    # --check on a fresh index: not stale
    fresh = rcs.rebuild(tmp / "idx" / "convo_search.duckdb", write=False)
    assert fresh["index_stale"] is False
    # --check after a corpus change: stale with the changed file named
    _oc_db(tmp / "live/opencode.db", [{"id": "p1", "tu": 3000, "text": "changed again"}])
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    stale = rcs.rebuild(tmp / "idx" / "convo_search.duckdb", write=False)
    assert stale["index_stale"] is True
    assert stale["stale_changed"] or stale["stale_new"]


def test_pointer_expand_resolves_the_right_row(convo_env, monkeypatch):
    """A part-addressed pointer (S4: file:part_id) must read back the EXACT
    corpus row it indexed; legacy row_no pointers still resolve positionally."""
    tmp = convo_env
    from helpers.misc import convo_query as cq

    hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)

    con = cq.connect(idx)
    try:
        rows = con.ddb.execute(
            "SELECT harness, part_id, file_path, row_no, snippet "
            "FROM convo_search ORDER BY harness, part_id"
        ).fetchall()
        assert rows
        for harness, part_id, file_path, row_no, snippet in rows:
            monkeypatch.setattr(cq, "REPO", tmp)
            rec = cq.expand(f"{file_path}:{part_id}")
            # the row the pointer names is the row whose digest we indexed
            assert rec["harness"] == harness
            assert rec["part_id"] == part_id
            assert rec["text"].startswith(snippet[:40])
        # legacy positional pointer (pre-S4 format, found in old logs)
        harness, part_id, file_path, row_no, snippet = rows[0]
        rec = cq.expand(f"{file_path}:{row_no}")
        assert rec["part_id"] == part_id
    finally:
        con.close()


def test_embed_cache_gc_evicts_dead_keeps_live(tmp_path, monkeypatch):
    """T3: unreferenced (trial) cache rows go; referenced rows stay; a missing
    reference index ABORTS instead of emptying the cache; and a source whose
    text basis drifted is reported UNVERIFIED rather than deleted."""
    from helpers.core.embed_cache import CACHE_DDL_BARE, CACHE_TABLE_BARE, _hash
    from helpers.core.vec_codec import pack_f32
    from helpers.maintenance import gc_embed_cache as gec

    store = tmp_path / "embed_store.db"
    con = sqlite3.connect(store)
    con.execute(CACHE_DDL_BARE)
    vec = pack_f32([0.1, 0.2, 0.3])
    for text, source in (
        ("live doc text", "doc"),
        ("live note", "note"),
        ("trial leftover", "company"),
        ("another trial", "convo"),
    ):
        con.execute(
            f"INSERT INTO {CACHE_TABLE_BARE} VALUES (?, ?, ?, ?)",  # noqa: S608
            (_hash(text), "m1", vec, source),
        )
    con.commit()
    con.close()

    ref_db = tmp_path / "doc_search.db"
    rc = sqlite3.connect(ref_db)
    rc.execute("CREATE TABLE doc_search (content TEXT)")
    rc.execute("INSERT INTO doc_search VALUES ('live doc text')")
    rc.commit()
    rc.close()
    note_db = tmp_path / "research.db"
    rn = sqlite3.connect(note_db)
    rn.execute("CREATE TABLE note_search (content TEXT)")
    rn.execute("INSERT INTO note_search VALUES ('live note')")
    rn.commit()
    rn.close()

    convo_db = tmp_path / "convo_search.duckdb_stub"
    cv = sqlite3.connect(convo_db)
    cv.execute("CREATE TABLE convo_search (snippet TEXT)")
    cv.execute("INSERT INTO convo_search VALUES ('live doc text')")
    cv.commit()
    cv.close()

    refs = (
        gec.Ref("doc", ref_db, "SELECT content FROM doc_search"),
        gec.Ref("note", note_db, "SELECT content FROM note_search"),
        gec.Ref("convo", convo_db, "SELECT snippet FROM convo_search"),
    )
    monkeypatch.setattr(gec, "STORE", store)

    out = gec.gc(refs=refs, apply=False)
    # 'company' has no reference label at all → unverified → never deleted
    assert out["unverified"], "a source with no reference index must be unverified"
    assert "company" in out["unverified"]
    # the convo lane's stale row IS verifiable (convo ref present) → dead
    assert out["dead_by_source"] == {"convo": 1}, out["dead_by_source"]

    applied = gec.gc(refs=refs, apply=True)
    assert applied["deleted"] == 1
    left = dict(
        sqlite3.connect(store)
        .execute(f"SELECT text_hash, source FROM {CACHE_TABLE_BARE}")  # noqa: S608  # constant table name from the module under test
        .fetchall()
    )
    assert set(left.values()) == {"doc", "note", "company"}, (
        "the unverified trial lane must survive; only provably dead rows go"
    )
    assert gec.gc(refs=refs, apply=False)["dead"] == 0

    # a missing reference index must abort, not delete
    with pytest.raises(RuntimeError, match="reference index missing"):
        gec.gc(
            refs=refs + (gec.Ref("gone", tmp_path / "nope.db", "SELECT content FROM x"),),
            apply=True,
        )
    assert len(sqlite3.connect(store).execute(f"SELECT 1 FROM {CACHE_TABLE_BARE}").fetchall()) == 3  # noqa: S608


def test_gc_reference_text_basis_matches_the_builders():
    """The GC must reproduce each builder's COMPOSED embed text, not hash the
    stored column — hashing the column made 23,595 live rows look dead."""
    from helpers.maintenance import gc_embed_cache as gec
    from helpers.maintenance.rebuild_doc_search import _EMBED_BODY_CAP
    from helpers.maintenance.rebuild_note_search import _embedding_text

    doc_row = ("T", "S", "b" * 5000)
    assert gec._doc_text(doc_row) == f"T\nS\n{'b' * _EMBED_BODY_CAP}"
    assert gec._doc_text(("T", "S", "short")) == "T\nS\nshort"
    note_row = ("T", "SEC", "S", "b" * 5000)
    assert gec._note_text(note_row) == _embedding_text("T", "SEC", "S", "b" * 5000)
    # identity default for convo
    convo = next(r for r in gec.DEFAULT_REFS if r.label == "convo")
    assert convo.text(("only the snippet",)) == "only the snippet"


def test_embed_cache_dedups_identical_texts_in_one_call():
    """T1: one model call per distinct digest inside a single bulk call."""
    import sqlite3 as _sqlite3

    from helpers.core import embed_cache as ec

    batches: list[list[str]] = []

    def batch_embedder(texts):
        batches.append(list(texts))
        return [[0.1, 0.2] for _ in texts]

    conn = _sqlite3.connect(":memory:")
    texts = ["dup", "dup", "dup", "other", "dup"]
    vecs, stats = ec.cached_embed_batch(
        conn, texts, "m1", batch_embedder, source="convo", purge_foreign=False
    )
    assert len(vecs) == len(texts) and all(v for v in vecs)
    assert stats["misses"] == 5, "per-text miss slots still reported"
    assert stats["unique_misses"] == 2, "duplicate texts must not re-hit the model"
    assert [t for b in batches for t in b] == ["dup", "other"], (
        f"model saw {batches} — one call per distinct digest expected"
    )
    assert stats["dirty"] == 2, "only distinct digests are written back"


def test_index_skips_protocol_markers_but_corpus_keeps_them(convo_env, monkeypatch):
    """T2: step-* parts are corpus-kept but never indexed (regression: the
    filter constant existed once while the read loop ignored it)."""
    tmp = convo_env
    hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    # inject a protocol-marker part into the opencode session corpus
    import pyarrow as pa
    import pyarrow.parquet as pq

    f = next((tmp / "corpus/opencode/conversations").glob("*.parquet"))
    tbl = pq.read_table(f)
    # match the corpus schema exactly (meta is a struct, ts is timestamp[us])
    marker = {
        "part_id": "pstep1",
        "message_id": "m1",
        "session_id": "s1",
        "ts": tbl.column("ts")[0].as_py(),
        "role": "assistant",
        "agent": "",
        "model": "",
        "part_type": "step-finish",
        "text": "tool-calls",
        "meta": tbl.column("meta")[0].as_py(),
        "source": "test",
    }
    pq.write_table(pa.concat_tables([tbl, pa.Table.from_pylist([marker], schema=tbl.schema)]), f)

    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    rcs.rebuild(tmp / "idx" / "convo_search.duckdb", write=True)

    con = duckdb.connect(str(tmp / "idx" / "convo_search.duckdb"), read_only=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM convo_search WHERE part_type LIKE 'step-%'"
        ).fetchone()
        assert n is not None
        n = n[0]
    finally:
        con.close()
    assert n == 0, f"{n} protocol-marker rows reached the index"
    # …and the corpus still holds it (archive of record)
    kept = {r["part_id"] for r in pq.read_table(f).to_pylist()}
    assert "pstep1" in kept, "T2 must not touch the corpus"


def test_part_type_prior_and_kinds_filter(convo_env, monkeypatch):
    """Tool traffic is 56% of the corpus, so the fused ranking gets a
    per-part_type nudge; ``kinds`` is the hard filter. The FTS sidecar is
    populated by the rebuild itself — no hand-written rows."""
    from helpers.misc import convo_query as cq

    tmp = convo_env
    hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    # a tool part whose text duplicates a text part: identical lexical AND
    # vector signal, so only the prior can separate them
    import pyarrow as pa
    import pyarrow.parquet as pq

    f = next((tmp / "corpus/opencode/conversations").glob("*.parquet"))
    tbl = pq.read_table(f)
    tool_row = {
        "part_id": "ptool1",
        "message_id": "m1",
        "session_id": "s1",
        "ts": tbl.column("ts")[0].as_py(),
        "role": "assistant",
        "agent": "",
        "model": "",
        "part_type": "tool",
        "text": "the igraph decision record",
        "meta": tbl.column("meta")[0].as_py(),
        "source": "test",
    }
    pq.write_table(pa.concat_tables([tbl, pa.Table.from_pylist([tool_row], schema=tbl.schema)]), f)

    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)

    con = cq.connect(idx)
    try:
        out = cq.search(con, "igraph decision record", limit=10)
        ptypes = [r["part_type"] for r in out["results"]]
        assert "tool" in ptypes, "the tool row must stay reachable (a nudge, not a ban)"
        assert ptypes[0] in ("text", "reasoning"), (
            f"rank 1 was a {ptypes[0]} row — the tool prior did not apply"
        )

        only_text = cq.search(con, "igraph decision record", limit=10, kinds=["text", "reasoning"])
        kept = {r["part_type"] for r in only_text["results"]}
        assert kept <= {"text", "reasoning"}, kept
        assert len(only_text["results"]) >= 1
        assert len(only_text["results"]) < len(out["results"]), (
            "the kinds filter should drop the tool rows the prior only demoted"
        )
    finally:
        con.close()


def test_incremental_indexes_only_the_delta(convo_env, monkeypatch):
    """T8: a changed session file must cost its DELTA, not its size — and a
    rewritten part_id must still be replaced."""
    tmp = convo_env
    hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    first = rcs.rebuild(idx, write=True)
    assert first["indexed"] >= 3

    def _open():
        return duckdb.connect(str(idx), read_only=True)

    con = _open()
    before = con.execute("SELECT COUNT(*) FROM convo_search").fetchone()[0]
    fts_before = (
        sqlite3.connect(rcs.fts_db_for(idx)).execute("SELECT COUNT(*) FROM convo_fts").fetchone()[0]
    )
    con.close()

    # 1) append ONE new part to the session file (what the harvester does)
    import pyarrow as pa
    import pyarrow.parquet as pq

    f = next((tmp / "corpus/opencode/conversations").glob("*.parquet"))
    tbl = pq.read_table(f)
    new_row = {
        "part_id": "pNEW",
        "message_id": "m1",
        "session_id": "s1",
        "ts": tbl.column("ts")[0].as_py(),
        "role": "assistant",
        "agent": "",
        "model": "",
        "part_type": "text",
        "text": "a brand new delta part",
        "meta": tbl.column("meta")[0].as_py(),
        "source": "test",
    }
    pq.write_table(pa.concat_tables([tbl, pa.Table.from_pylist([new_row], schema=tbl.schema)]), f)

    second = rcs.rebuild(idx, write=True, incremental=True)
    assert second["indexed"] == 1, f"expected a 1-row delta, got {second['indexed']}"
    con = _open()
    after = con.execute("SELECT COUNT(*) FROM convo_search").fetchone()[0]
    assert after == before + 1
    fts_after = (
        sqlite3.connect(rcs.fts_db_for(idx)).execute("SELECT COUNT(*) FROM convo_fts").fetchone()[0]
    )
    assert fts_after == fts_before + 1, "FTS must track the delta, not the file"
    # the untouched rows survived (delta-only, not reinsert)
    assert (
        con.execute(
            "SELECT COUNT(*) FROM convo_search WHERE part_id = 'p1' AND snippet LIKE '%igraph%'"
        ).fetchone()[0]
        == 1
    )
    con.close()

    # 2) rewrite an EXISTING part's text (newest-source-wins) → must replace
    tbl = pq.read_table(f)
    tbl2 = tbl.to_pylist()
    for r in tbl2:
        if r["part_id"] == "p1":
            r["text"] = "REWRITTEN body for p1"
    pq.write_table(pa.Table.from_pylist(tbl2, schema=tbl.schema), f)
    third = rcs.rebuild(idx, write=True, incremental=True)
    assert third["indexed"] == 1, f"only the rewritten part should reindex: {third}"
    con = _open()
    got = con.execute("SELECT snippet FROM convo_search WHERE part_id = 'p1'").fetchone()[0]
    con.close()
    assert "REWRITTEN" in got, got
    # and FTS agrees (the old text is gone, the new one searchable)
    s = sqlite3.connect(rcs.fts_db_for(idx))
    assert s.execute("SELECT COUNT(*) FROM convo_fts WHERE part_id = 'p1'").fetchone()[0] == 1
    s.close()


def test_compaction_noop_and_drops_propagate(convo_env, monkeypatch):
    """S4, corrected model (measured 2026-11-07: the live corpus is
    append-only in practice — 255/255 parts aligned, zero drops, bili
    compression is reversible at the log/backing-db layer so the corpus
    never loses parts):

    - a pure RENUMBER (parts reordered, text + part_ids untouched) must
      be a ZERO-WRITE no-op: no deletes, no re-embeds, no FTS refresh —
      the whole-part rewrite waves this arc set out to kill;
    - pointers are part-addressed, so they resolve correctly even with
      the stale row_no a no-op leaves behind;
    - a genuine DROP (a part vanishing from the parquet) must still
      propagate: the index follows the corpus.
    """
    from helpers.misc import convo_query as cq

    tmp = convo_env
    hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    import pyarrow as pa
    import pyarrow.parquet as pq

    f = next((tmp / "corpus/opencode/conversations").glob("*.parquet"))
    tbl = pq.read_table(f)
    extra = []
    for pid in ("pA", "pB"):
        extra.append(
            {
                "part_id": pid,
                "message_id": "m1",
                "session_id": "s1",
                "ts": tbl.column("ts")[0].as_py(),
                "role": "assistant",
                "agent": "",
                "model": "",
                "part_type": "text",
                "text": f"tail part {pid}",
                "meta": tbl.column("meta")[0].as_py(),
                "source": "test",
            }
        )
    pq.write_table(pa.concat_tables([tbl, pa.Table.from_pylist(extra, schema=tbl.schema)]), f)

    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    embed_calls: list[list[str]] = []
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            embed_calls.extend(texts) or [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    refresh_calls: list[bool] = []
    real_refresh = rcs._refresh_fts_index
    monkeypatch.setattr(
        rcs, "_refresh_fts_index", lambda db: refresh_calls.append(True) or real_refresh(db)
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)
    monkeypatch.setattr(cq, "REPO", tmp)

    def part_set():
        # short-lived read-only open: the write path cannot run while a
        # read_only connection to the same file is alive in-process
        con = cq.connect(idx)
        try:
            return set(
                con.ddb.execute(
                    "SELECT part_id FROM convo_search WHERE file_path LIKE '%opencode%'"
                ).fetchall()
            )
        finally:
            con.close()

    def count():
        con = cq.connect(idx)
        try:
            return con.ddb.execute("SELECT COUNT(*) FROM convo_search").fetchone()[0]
        finally:
            con.close()

    base_set, base_count = part_set(), count()
    assert {("pA",), ("pB",)} <= base_set

    # 1) pure RENUMBER: reverse parquet row order, texts + part_ids intact.
    #    Pre-S4 this flagged the whole file; post-S4 it must write nothing.
    rows = pq.read_table(f).to_pylist()
    pq.write_table(pa.Table.from_pylist(list(reversed(rows)), schema=tbl.schema), f)
    embed_calls.clear()
    no_op = rcs.rebuild(idx, write=True, incremental=True)
    assert no_op["indexed"] == 0, f"renumber must be a no-op, got {no_op}"
    assert not embed_calls, "renumber must not re-embed anything"
    assert part_set() == base_set and count() == base_count

    # 2) pointers resolve by part_id even with the stale row_no
    con = cq.connect(idx)
    try:
        bad = con.ddb.execute(
            "SELECT part_id, file_path FROM convo_search WHERE file_path LIKE '%opencode%'"
        ).fetchall()
    finally:
        con.close()
    assert len(bad) >= 4
    for part_id, file_path in bad:
        rec = cq.expand(f"{file_path}:{part_id}")
        assert rec["part_id"] == part_id

    # 3) a genuine DROP must still propagate (index follows the corpus)
    rows = pq.read_table(f).to_pylist()
    kept = [r for r in rows if r["part_id"] != "pA"]
    pq.write_table(pa.Table.from_pylist(kept, schema=tbl.schema), f)
    rcs.rebuild(idx, write=True, incremental=True)
    assert part_set() == base_set - {("pA",)}, "dropped part must leave the index"
    assert count() == base_count - 1


def test_query_hybrid_fuse(convo_env, monkeypatch):
    tmp = convo_env
    from helpers.misc import convo_query as cq

    db = tmp / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)

    con = cq.connect(idx)
    try:
        # cosine leg stubbed to a fixed ranking; FTS leg is real
        fts_rows = con.sconn.execute(
            "SELECT part_id FROM convo_fts WHERE convo_fts MATCH ? ORDER BY rank LIMIT 5",
            ('"hello"',),
        ).fetchall()
        assert fts_rows, "fts sidecar empty"
        monkeypatch.setattr(
            cq,
            "_cos_hits",
            lambda c, q, limit, dims, query_vec=None, harness=None, source=None: [
                (("prime-rlm", "pm1:0"), 0.9)
            ],
        )
        out = cq.search(con, "hello", limit=3)
        assert out["mode"] == "hybrid"
        assert out["results"], "hybrid search returned nothing"
        r0 = out["results"][0]
        assert {"rank", "score", "harness", "pointer", "head"} <= set(r0)
        assert r0["pointer"].endswith(".parquet:0") or ".parquet:" in r0["pointer"]
        # harness filter narrows to the requested lane
        out_oc = cq.search(con, "hello", limit=10, harness="prime-rlm")
        assert all(r["harness"] == "prime-rlm" for r in out_oc["results"])
    finally:
        con.close()


def test_rebuild_toctou_redo_on_mid_embed_change(convo_env, monkeypatch, capsys):
    """Contention-window S3: a concurrent rebuild that commits inside the
    embed gap must be detected at the write window (BEFORE any write) and
    trigger a clean redo — never a stale-diff double-apply."""
    tmp = convo_env
    db = tmp / "idx" / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[float(len(t) % 7), 1.0, 0.0] for t in texts],
            {"hits": 0, "misses": len(texts)},
            3,
            "test-model",
        ),
    )
    real = rcs._stored_hashes
    calls = {"n": 0}

    def racing(con):
        calls["n"] += 1
        hashes = dict(real(con))
        if calls["n"] == 2:
            # call 2 = the attempt-1 write-window verify: pretend a
            # concurrent rebuild committed a different hash state (on a
            # FIRST rebuild the table is still empty — synthesize the
            # foreign commit instead of mutating nothing)
            if hashes:
                for k in hashes:
                    hashes[k] = "concurrent-" + hashes[k]
                    break
            else:
                hashes["corpus/seed.parquet"] = "concurrent-hash"
        return hashes

    monkeypatch.setattr(rcs, "_stored_hashes", racing)
    stats = rcs.rebuild(db, write=True)  # attempt 1 redoes; attempt 2 lands
    assert calls["n"] >= 4, "verify must have fired on both attempts"
    assert stats["indexed"] > 0 and stats["total"] == stats["indexed"]
    assert "re-reading the diff" in capsys.readouterr().err
    # the landed state is consistent: check mode sees no drift
    fresh = rcs.rebuild(db, write=False)
    assert fresh["index_stale"] is False


def test_rebuild_fts_sync_runs_outside_the_duckdb_window(convo_env, monkeypatch):
    """Contention-window S3 de-nesting: when the FTS sync runs, the
    convo duckdb connection must be CLOSED — an RO open succeeding at
    that moment is the discriminator (an RW holder would refuse it)."""
    tmp = convo_env
    db = tmp / "idx" / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[float(len(t) % 7), 1.0, 0.0] for t in texts],
            {"hits": 0, "misses": len(texts)},
            3,
            "test-model",
        ),
    )
    real = rcs._fts_sync
    observed = {"ro_open_ok": None}

    def probing(sconn, drop_keys, rows, full):
        con = duckdb.connect(str(db), read_only=True)  # fails under an RW holder
        con.close()
        observed["ro_open_ok"] = True
        return real(sconn, drop_keys, rows, full=full)

    monkeypatch.setattr(rcs, "_fts_sync", probing)
    stats = rcs.rebuild(db, write=True)
    assert stats["indexed"] > 0
    assert observed["ro_open_ok"] is True


def test_harness_filter_constrains_legs_not_post_fusion(convo_env, monkeypatch):
    """Narrow-harness starvation (bili lane, live lesson): a generic term
    with 25 dense opencode matches and 1 sparse prime match must still
    return the prime row under harness="prime-rlm". Pre-fix the fixed
    top-20 fusion pool held no prime row to filter down to."""
    import json as _json

    from helpers.misc import convo_query as cq

    tmp = convo_env
    _oc_db(
        tmp / "live/opencode.db",
        [
            {
                "id": f"q{i}",
                "tu": 3000 + i,
                "text": "starvationword starvationword starvationword filler",
            }
            for i in range(25)
        ],
    )
    prime = tmp / "home/.prime/agent/sessions/abc.jsonl"
    with open(prime, "a") as fh:
        fh.write(
            _json.dumps(
                {
                    "type": "message",
                    "id": "pm9",
                    "timestamp": 1700000001000,
                    "message": {
                        "role": "assistant",
                        "model": "m",
                        "content": [
                            {
                                "type": "text",
                                "text": "a long rambling prime note that happens to mention "
                                "starvationword once near the end after much filler "
                                "filler filler filler filler filler filler filler",
                            }
                        ],
                    },
                }
            )
            + "\n"
        )
    db = tmp / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)
    con = cq.connect(idx)
    try:
        # Leg level: the filter must constrain the FTS leg itself.
        leg = cq._fts_hits(con, "starvationword", 20, harness="prime-rlm")
        assert leg, "FTS leg returned nothing under harness filter"
        # End to end with an empty cosine leg: fusion sees FTS only, so a
        # post-fusion filter would have nothing prime to keep.
        monkeypatch.setattr(cq, "_cos_hits", lambda *a, **k: [])
        out = cq.search(con, "starvationword", limit=1, harness="prime-rlm")
        assert out["results"], "narrow-harness hit starved by post-fusion filter"
        assert all(r["harness"] == "prime-rlm" for r in out["results"])
    finally:
        con.close()


class TestDuckNativeFTS:
    """S3: the lexical leg reads the store's native FTS index, with the
    sqlite sidecar as fallback for pre-S3 stores."""

    def _build(self, tmp, monkeypatch):
        db = tmp / "convo_search.duckdb"
        hc.harvest(corpus_root=tmp / "corpus", db=db)
        monkeypatch.setattr(rcs, "REPO", tmp)
        monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
        monkeypatch.setattr(
            rcs,
            "_embed",
            lambda texts: (
                [[float(len(t) % 7), 1.0, 0.0] for t in texts],
                {"hits": 0, "misses": len(texts)},
                3,
                "test-model",
            ),
        )
        stats = rcs.rebuild(tmp / "idx" / "convo_search.duckdb", write=True)
        assert stats["indexed"] > 0
        return tmp / "idx" / "convo_search.duckdb"

    def test_native_fts_answers_and_zero_match_is_empty(self, convo_env, monkeypatch):
        from helpers.misc import convo_query as cq

        idx = self._build(convo_env, monkeypatch)
        cc = cq.connect(idx)
        try:
            hits = cq._fts_hits(cc, "igraph decision", limit=5)
            assert hits, "native FTS returned no hits for a seeded token"
            # the NULL-score guard: once matches < limit, non-matching rows
            # must NOT leak into the tail (probe 2026-11-05)
            assert cq._fts_hits(cc, "qqqqzzzz wvvvyy", limit=5) == []
            filtered = cq._fts_hits(cc, "igraph decision", limit=5, harness="opencode")
            assert filtered and all(k[0] == "opencode" for k, _s in filtered)
        finally:
            cc.close()

    def test_full_text_survives_the_bulk_insert(self, convo_env, monkeypatch):
        idx = self._build(convo_env, monkeypatch)
        con = duckdb.connect(str(idx), read_only=True)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM convo_search "
                "WHERE text IS NULL OR length(text) <> text_len "
                "OR left(text, length(snippet)) <> snippet"
            ).fetchone()[0]
        finally:
            con.close()
        assert n == 0, f"{n} rows lost full text or desynced from snippet"

    def test_falls_back_to_sqlite_when_index_missing(self, convo_env, monkeypatch):
        from helpers.misc import convo_query as cq

        idx = self._build(convo_env, monkeypatch)
        con = duckdb.connect(str(idx))
        try:
            con.execute("LOAD fts")
            con.execute("PRAGMA drop_fts_index('convo_search')")
        finally:
            con.close()
        cc = cq.connect(idx)
        try:
            hits = cq._fts_hits(cc, "igraph", limit=5)
            assert hits, "sqlite fallback leg returned no hits"
        finally:
            cc.close()


def test_no_change_run_skips_fts_refresh(convo_env, monkeypatch):
    """S4: the native-FTS drop+recreate (~34 s over 27M terms rows) is
    pointless when the run wrote nothing — a quiet gate arc must skip it."""
    tmp = convo_env
    hc.harvest(corpus_root=tmp / "corpus", db=tmp / "convo_search.duckdb")
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)

    calls: list[str] = []
    monkeypatch.setattr(rcs, "_refresh_fts_index", lambda db: calls.append(str(db)))
    rcs.rebuild(idx, write=True, incremental=True)
    assert not calls, "zero-change incremental must not drop/recreate the FTS index"

    # a content change must still refresh it
    import pyarrow as pa
    import pyarrow.parquet as pq

    f = next((tmp / "corpus/opencode/conversations").glob("*.parquet"))
    tbl = pq.read_table(f)
    rows = tbl.to_pylist()
    for r in rows:
        if r["part_id"] == "p1":
            r["text"] = "changed body triggers refresh"
    pq.write_table(pa.Table.from_pylist(rows, schema=tbl.schema), f)
    rcs.rebuild(idx, write=True, incremental=True)
    assert len(calls) == 1, "a content change must refresh the native FTS index"


def test_compact_via_copy_parity_and_index_drop(convo_env, monkeypatch, capsys):
    """compact_via_copy must preserve every row + the native FTS macro, and
    must NOT carry the retired HNSW index into the compacted store."""
    import duckdb

    tmp = convo_env
    db = tmp / "convo_search.duckdb"
    hc.harvest(corpus_root=tmp / "corpus", db=db)
    monkeypatch.setattr(rcs, "REPO", tmp)
    monkeypatch.setattr(rcs, "CORPUS_DIR_GLOB", "corpus/*/*/*.parquet")
    monkeypatch.setattr(
        rcs,
        "_embed",
        lambda texts: (
            [[1.0, 0.0] for _ in texts],
            {"hits": 0, "misses": len(texts)},
            2,
            "test-model",
        ),
    )
    idx = tmp / "idx" / "convo_search.duckdb"
    rcs.rebuild(idx, write=True)

    def counts(p):
        c = duckdb.connect(str(p), read_only=True)
        try:
            c.execute("LOAD fts")
            out = {}
            for t in rcs._COMPACT_TABLES:
                try:
                    out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
                except duckdb.CatalogException:
                    out[t] = 0
            ids = c.execute("SELECT harness, part_id FROM convo_search ORDER BY 1, 2").fetchall()
            fts = c.execute(
                "SELECT COUNT(*) FROM (SELECT fts_main_convo_search.match_bm25(part_id, 'hello') s "
                "FROM convo_search) WHERE s IS NOT NULL"
            ).fetchone()[0]
        finally:
            c.close()
        return out, ids, fts

    before_counts, before_ids, before_fts = counts(idx)
    before_size = idx.stat().st_size

    stats = rcs.compact_via_copy(idx)

    after_counts, after_ids, fts_hits = counts(idx)
    assert stats["mode"] == "compact"
    assert after_counts == before_counts, "row-count parity across every compacted table"
    assert after_ids == before_ids, "row identity + order preserved"
    # The token itself may or may not be in this synthetic corpus; what must
    # hold is that the lexical scoring behaves IDENTICALLY after the copy.
    assert fts_hits == before_fts, "native FTS scoring must be unchanged by the COPY"

    # The HNSW index must be gone for GOOD: not rebuilt, not copied back.
    c = duckdb.connect(str(idx), read_only=True)
    try:
        left = c.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'convo_search'"
        ).fetchall()
    finally:
        c.close()
    assert left == [], f"compacted store must carry no ANN index, found {left}"

    # The guard is printed at the start AND right before the swap.
    err = capsys.readouterr().err
    assert err.count(rcs.COMPACT_GUARD) >= 2, "guard must bracket the downtime window"

    # A compaction never grows the store.
    assert idx.stat().st_size <= before_size


def test_compact_failure_leaves_the_live_store_intact(convo_env, monkeypatch):
    """A compaction that dies mid-flight must never touch the live store and
    must not leave its pid-tagged temp behind."""
    import duckdb

    tmp = convo_env
    idx = tmp / "idx" / "convo_search.duckdb"
    idx.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(idx))
    try:
        con.execute(
            "CREATE TABLE convo_search (harness VARCHAR, part_id VARCHAR, embedding FLOAT[2])"
        )
        con.execute("INSERT INTO convo_search VALUES ('h', 'p1', [1.0, 0.0])")
        con.execute("CREATE TABLE convo_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        con.execute("INSERT INTO convo_meta VALUES ('embed_dims', '2')")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    original = idx.read_bytes()

    def boom(path):
        raise RuntimeError("vss exploded")

    monkeypatch.setattr(rcs, "_drop_ann_indexes", boom)
    try:
        rcs.compact_via_copy(idx)
    except RuntimeError as exc:
        assert "vss exploded" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a failed compaction must raise")

    assert idx.read_bytes() == original, "the live store must be untouched after a failed compact"
    leftovers = list(idx.parent.glob("*.copy-*.tmp*"))
    assert leftovers == [], f"failed compaction left temp residue: {leftovers}"
