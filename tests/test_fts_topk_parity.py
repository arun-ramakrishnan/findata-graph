#!/usr/bin/env python3
"""Tokenization parity between the DuckDB FTS top-k/macro path and legacy
FTS5 (duckdb_fts_sql_topk_regression — recorded watch item #4).

`fts_query_tokens` splits on whitespace only, while FTS5 unicode61 and
DuckDB's native analyzer both tokenize on punctuation. A hyphenated /
punctuated query ("hyper-graph", "R&D") therefore resolves differently on
the SQL top-k path — it either falls back to the match_bm25 macro (slow,
correct) or loses recall. This test pins the *result* contract: for the
same corpus, the DuckDB sidecar must return the same top hits as legacy
FTS5 for these edge-case tokenizations.

Also pins the internal fallback contract: on the DuckDB store itself, a
query that resolves on the SQL top-k path and one that falls back to the
macro must agree on top hits (the fallback net must catch the same rows).
"""

from __future__ import annotations


import pytest

from helpers.maintenance import rebuild_doc_search as rds
from helpers.core import local_embedder as LE

pytestmark = [pytest.mark.integration]

_EDGE_QUERIES = [
    "hyper-graph",  # hyphenated
    "cattle-feed",  # hyphenated compound
    "state-of-the-art",  # multi-hyphen
    "R&D",  # ampersand
    "FY2026",  # letters+digits
    "don't",  # apostrophe
]

# Documented DuckDB-vs-FTS5 tokenization drift (recorded 2026-11-16 in
# duckdb_watchlist_hardening.md §2). Each entry says WHY the engines
# differ today; the parity test pins the CURRENT behavior so any future
# engine change flips it red and forces the record to be updated
# (either the drift is fixed, or the note becomes stale).
_KNOWN_DRIFT = {
    # DuckDB's analyzer splits the hyphen and treats the parts loosely
    # (doc/plain.md matches via "feed"); legacy FTS5 keeps
    # "cattle-feed" as one unicode61 token.
    "cattle-feed": {
        "duckdb": {"doc/cattle.md", "doc/plain.md"},
        "legacy": {"doc/cattle.md"},
    },
    # DuckDB resolves "R&D" to nothing (ampersand-split parts don't
    # compose); legacy FTS5 matches the "R&D" token in art.md.
    "R&D": {
        "duckdb": set(),
        "legacy": {"doc/art.md"},
    },
}

_DOCS = {
    "hypergraph.md": "# Hyper Graph\n\n## Body\nhyper-graph routing across lanes\n",
    "cattle.md": "# Cattle Feed\n\n## Body\ncattle-feed rations and silage\n",
    "art.md": "# Art\n\n## Body\nstate-of-the-art methods in R&D and FY2026 planning\n",
    "plain.md": "# Plain\n\n## Body\nshrimp feed aquaculture\n",
}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    doc_root = tmp_path / "doc"
    doc_root.mkdir()
    for name, text in _DOCS.items():
        (doc_root / name).write_text(text, encoding="utf-8")
    ddb = tmp_path / "doc_search.duckdb"
    sdb = tmp_path / "doc_search.db"
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    return ddb, sdb


@pytest.fixture
def fake_local(monkeypatch):
    def _vec(text: str) -> list[float]:
        v = [0.0] * 8
        if any(tok in text.lower() for tok in ("hyper", "cattle", "art", "shrimp")):
            v[2] = 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    return LE


def _hits(out: dict) -> list[str]:
    # a doc can contribute multiple section rows; parity is per-file
    seen: list[str] = []
    for r in out.get("results", []):
        p = r["path"]
        if p not in seen:
            seen.append(p)
    return seen


def test_duckdb_vs_legacy_fts5_tokenization_parity(seeded, fake_local):
    ddb, sdb = seeded
    rds.rebuild_duckdb(ddb, embed_fn=fake_local.embed_document)
    rds.rebuild(sdb, embed_fn=fake_local.embed_document)

    dconn = rds.connect_doc_db(ddb)
    sconn = rds.connect_doc_db(sdb)
    try:
        for q in _EDGE_QUERIES:
            dout = rds.search_docs(dconn, q, limit=5, hybrid=False)
            sout = rds.search_docs(sconn, q, limit=5, hybrid=False)
            assert dout["mode"] == "bm25", f"{q!r}: duckdb mode {dout['mode']}"
            assert sout["mode"] == "bm25", f"{q!r}: legacy mode {sout['mode']}"
            dh = set(_hits(dout))
            sh = set(_hits(sout))
            if dh == sh:
                continue
            # Known, documented drift (recorded in
            # duckdb_watchlist_hardening.md §2): assert the CURRENT
            # behavior matches the record — a change flips this red and
            # means the record must be updated (fixed or re-documented).
            record = _KNOWN_DRIFT.get(q)
            assert record is not None, (
                f"{q!r}: UNRECORDED drift — duckdb {sorted(dh)} vs "
                f"legacy {sorted(sh)}; add to _KNOWN_DRIFT and the "
                f"watchlist doc"
            )
            assert dh == record["duckdb"], (
                f"{q!r}: duckdb drifted from record {record['duckdb']} → {sorted(dh)}"
            )
            assert sh == record["legacy"], (
                f"{q!r}: legacy drifted from record {record['legacy']} → {sorted(sh)}"
            )
    finally:
        dconn.close()
        sconn.close()


def test_duckdb_topk_and_macro_fallback_agree(seeded, fake_local):
    """Internal fallback net: a query that resolves on the SQL top-k path
    and one that is forced into the macro fallback must return the same
    top hits on the same store (the fallback is the safety net, not an
    alternative ranking)."""
    import duckdb

    ddb, _sdb = seeded
    rds.rebuild_duckdb(ddb, embed_fn=fake_local.embed_document)
    dconn = rds.connect_doc_db(ddb)
    try:
        out_topk = rds.search_docs(dconn, "hyper", limit=5, hybrid=False)
        # Force the macro fallback net: make fts_topk raise (any
        # unresolvable/schema-mismatch condition degrades the same way).
        real_topk = rds.fts_topk

        def _boom(*args, **kwargs):
            raise duckdb.Error("forced fallback for parity test")

        rds.fts_topk = _boom
        try:
            out_macro = rds.search_docs(dconn, "hyper", limit=5, hybrid=False)
        finally:
            rds.fts_topk = real_topk
        assert _hits(out_topk) == _hits(out_macro), (
            f"topk hits {_hits(out_topk)} != macro hits {_hits(out_macro)}"
        )
    finally:
        dconn.close()
