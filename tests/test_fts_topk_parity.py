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
from helpers.core import duckdb_shared as ds_shared
from helpers.core.duckdb_shared import fts_query_tokens_duckdb as fts_qtd

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
# duckdb_watchlist_hardening.md §2; mechanism updated 2026-11-18 by
# fts_query_token_parity S1/S2). Each entry says WHY the engines differ
# today; the parity test pins the CURRENT behavior so any future engine
# change flips it red and forces the record to be updated (either the
# drift is fixed, or the note becomes stale).
#
# S1 changed the PATH but not these result sets: "cattle-feed" now
# resolves on the SQL top-k leg (['cattle','feed'], dict-honest) instead
# of falling back to the whole-table match_bm25 macro — same rows, 8x
# cheaper, and the overmatch is now the top-k OR ranking rather than the
# macro's. "R&D" is UNFIXABLE at the query side: the analyzer drops
# 1-character tokens (measured on a fixture indexed with the same PRAGMA
# call the rebuilds use), so R&D has no lexical representation in the
# index — no query tokenizer can recover it. Only a substring/ILIKE lane
# or the vector leg can find it, which is why hybrid mode still surfaces
# those 3 doc rows.
_KNOWN_DRIFT = {
    # S1: DuckDB top-k now ORs the analyzer's own parts (cattl/feed →
    # cattle.md via "cattle", plain.md via "feed"); legacy FTS5 keeps
    # "cattle-feed" as one unicode61 phrase token, so only the doc that
    # contains the literal phrase matches.
    "cattle-feed": {
        "duckdb": {"doc/cattle.md", "doc/plain.md"},
        "legacy": {"doc/cattle.md"},
    },
    # DuckDB's analyzer drops 1-char tokens, so "R&D" leaves nothing to
    # index and nothing to resolve — bm25 returns 0 on both the macro and
    # (now) the top-k path. legacy FTS5 phrase-matches art.md.
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


class TestAnalyzerMatchedTokenizer:
    """fts_query_token_parity S1: `fts_query_tokens_duckdb` mirrors the
    analyzer rules pinned 2026-11-18 (separator split, letter/digit split,
    digit-only and 1-char tokens dropped). The contract that matters is
    the PATH a query takes, because the macro fallback is the safety net,
    not an alternative ranking."""

    def test_hyphenated_compound_resolves_on_topk(self, seeded, fake_local):
        """cattle-feed used to fall to the whole-table macro (8x slower,
        overmatching via the OR inside match_bm25). With the analyzer-
        matched splitter both parts resolve, so the top-k leg serves it."""
        import duckdb as _d

        ddb, _sdb = seeded
        rds.rebuild_duckdb(ddb, embed_fn=fake_local.embed_document)
        dconn = rds.connect_doc_db(ddb)
        try:
            assert fts_qtd("cattle-feed") == ["cattle", "feed"]
            rows, mode = ds_shared.fts_topk(
                dconn,
                fts_table="fts_main_doc_search",
                base_table="doc_search",
                tokens=fts_qtd("cattle-feed"),
                limit=5,
            )
            assert mode == "topk", f"cattle-feed still takes {mode}"
            assert rows is not None and len(rows) > 0
            # the splitter must NOT turn R&D into an r-OR-d scan
            assert fts_qtd("R&D") == [], (
                "1-char tokens must drop: the dict carries bare r/d from "
                "other rows and r-OR-d would overmatch everything"
            )
            _ = _d  # keep the import meaningful for the monkeypatch target
        finally:
            dconn.close()

    def test_digit_and_apostrophe_forms_follow_the_analyzer(self):
        # letter/digit boundary: FY2026 -> fy (digit part dropped)
        assert fts_qtd("FY2026") == ["fy"]
        # apostrophe is a separator; the 1-char remainder drops
        assert fts_qtd("don't") == ["don"]
        # underscore is a separator
        assert fts_qtd("co_mention") == ["co", "mention"]
        # digit-only tokens drop; 3M collapses to the 1-char m -> gone
        assert fts_qtd("3M") == []
        # plain queries unchanged
        assert fts_qtd("market share") == ["market", "share"]
        assert fts_qtd("") == []


class TestStemLadderParity:
    """fts_stem_ladder S1/S4 oracle: the ladder must generate the analyzer's
    true stem for every measured morphological class.

    Every pair below was pinned on 2026-11-18 by indexing the word into a
    fixture with the same ``PRAGMA create_fts_index`` call the rebuilds use
    and reading the dict term back (probes: stem_probe{1,2,3}.py). The old
    six-suffix ladder missed 101/400 sampled corpus words (28%) — every
    miss took the whole-table match_bm25 macro detour. The replacement
    superset generator measured 0/400 real-vocabulary misses on the same
    sample; the residual 7 are analyzer stopwords (dropped from the dict
    by design) plus one proper noun. If a pair here goes red, either the
    engine's stemmer changed or a ladder regression narrowed a class —
    re-run the fixture oracle before touching ``_stem_guesses``.
    """

    # word -> measured dict stem (one representative per class + keeps)
    _STEM_BANK = {
        # plurals / ies (ies→i AND ies→y both live: studies→studi,
        # activities→activity→activ)
        "studies": "studi",
        "activities": "activ",
        "boxes": "box",
        "indices": "indic",
        # y→i (universal) and y-drop (ency family)
        "registry": "registri",
        "query": "queri",
        "agency": "agenc",
        "fluency": "fluenci",
        "city": "citi",
        # -ation/-ization/-ication/-ion(s|t)
        "generation": "gener",
        "tokenization": "token",
        "deduplication": "dedupl",
        "edition": "edit",
        "relation": "relat",
        "nation": "nation",  # keeps: m('nat')=0
        # -ize/-ise
        "normalize": "normal",
        "apologize": "apolog",
        # -al/-ical/-ic with m>1
        "critical": "critic",
        "historical": "histor",
        "financial": "financi",
        "minimal": "minim",
        "central": "central",  # keeps: m('centr')=1
        # -ness/-ful at m>0
        "freshness": "fresh",
        "business": "busi",
        "witness": "wit",
        "truthful": "truth",
        # -ment with m>1
        "alignment": "align",
        "assessment": "assess",
        "document": "document",  # keeps: m('docu')=1
        # -ency/-ence with m>1
        "consistency": "consist",
        "currency": "currenc",
        # gemination after ed/ing (ll included, ss kept)
        "planned": "plan",
        "controlled": "control",
        "assessed": "assess",
        "falling": "fall",
        # e-restore after ed
        "stored": "store",
        "created": "creat",
        "agreed": "agre",
        # -er with m>1 ('or' words keep: processor)
        "converter": "convert",
        "computer": "comput",
        "registered": "regist",
        "builder": "builder",  # keeps: m('build')=1
        "processor": "processor",  # keeps: ends 'or', no -er match
        # -ly with m>1 (quickly keeps li via y→i)
        "accurately": "accur",
        "quickly": "quickli",
        # -able/-ible with m>1
        "auditable": "audit",
        "constructible": "construct",
        "salvageable": "salvag",
        # -ility/-ibility/-ability family
        "scalability": "scalabl",
        "visibility": "visibl",
        "compatibility": "compat",
        "availability": "avail",
        # -ivity
        "connectivity": "connect",
        # -ism/-ent/-ive/-ous
        "nondeterminism": "nondetermin",
        "constituents": "constitu",
        "aggressive": "aggress",
        "synchronously": "synchron",
        # e-final keeps under the stemmer's *o rule
        "node": "node",
        "store": "store",
        "monitor": "monitor",
        "operator": "oper",  # -ator with m>1
    }

    def test_ladder_generates_measured_stems(self):
        for word, stem in self._STEM_BANK.items():
            assert stem in ds_shared._stem_guesses(word), (
                f"{word}: stemmer stem {stem!r} missing from ladder candidates "
                f"{ds_shared._stem_guesses(word)}"
            )

    def test_ladder_resolves_fixture_topk(self):
        import duckdb

        words = sorted(self._STEM_BANK)
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE t (id INTEGER, s VARCHAR)")
        con.execute("INSERT INTO t VALUES (1, ?)", [" ".join(words)])
        con.execute("PRAGMA create_fts_index('t', 'id', 's')")
        try:
            for word in words:
                rows, mode = ds_shared.fts_topk(
                    con,
                    fts_table="fts_main_t",
                    base_table="t",
                    tokens=[word],
                    limit=3,
                )
                assert mode == "topk" and rows, (
                    f"{word}: topk leg did not resolve (mode={mode}) — "
                    "ladder regression or stemmer drift; re-run the oracle"
                )
        finally:
            con.close()

    def test_ladder_keeps_exact_token_and_parity_edges(self):
        # the word itself is always a candidate (dict-honest exact hit)
        for word in self._STEM_BANK:
            assert word in ds_shared._stem_guesses(word)
        # parity edge queries from the S1 tokenization slice stay intact
        assert fts_qtd("cattle-feed") == ["cattle", "feed"]
        assert fts_qtd("R&D") == []
        assert "cattl" in ds_shared._stem_guesses("cattle")
        assert "feed" in ds_shared._stem_guesses("feeding")
