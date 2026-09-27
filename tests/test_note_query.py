#!/usr/bin/env python3
"""Tests for `helpers/misc/note_query.py` — the notes query core.

The TUI lane (`search_tui.run_lane("notes", …)`) is a thin adapter over
this module, so what is pinned here is the behaviour BOTH surfaces
depend on: FTS5 query safety, bm25 ranking, `path:line` pointer
correctness, the RRF fusion in both its per-section and per-note forms,
and the degradation path when the semantic matrix is stale.

The real 25 MB semantic matrix and the granite embedder are never
touched: the semantic leg is either stubbed or made to raise.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.misc import note_query as nq  # noqa: E402


def _notes_db(tmp_path: Path) -> Path:
    """Synthetic note_search with the production column layout."""
    db = tmp_path / "n.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE VIRTUAL TABLE note_search USING fts5("
        "doc_type, file_path UNINDEXED, title, sector, content,"
        " embedding UNINDEXED, section_title, anchor UNINDEXED)"
    )
    conn.executemany(
        "INSERT INTO note_search(doc_type, file_path, title, sector, content,"
        " section_title, anchor) VALUES (?,?,?,?,?,?,?)",
        [
            ("company", "a.md", "Alpha", "Tech", "mojo benchmark lane", "Body", 3),
            ("company", "a.md", "Alpha", "Tech", "mojo again, deeper", "Deep", 9),
            ("company", "b.md", "Beta", "Energy", "unrelated text", "Body", 4),
        ],
    )
    conn.commit()
    conn.close()
    return db


# ------------------------------------------------------------------ fts_safe


def test_fts_safe_passes_plain_words_through():
    assert nq.fts_safe("mojo benchmark") == "mojo benchmark"
    assert nq.fts_safe("promoter holding") == "promoter holding"


def test_fts_safe_degrades_punctuation_to_or_tokens():
    """Bare hyphens are FTS5 column-filter syntax (`no such column: dup`)."""
    out = nq.fts_safe("near-dup ceiling")
    assert "near" in out and "dup" in out
    assert "-" not in out, out
    assert '"' in out, "tokens must be quoted so FTS5 treats them literally"


def test_fts_safe_never_returns_an_empty_match():
    assert nq.fts_safe("---") == '""'


# -------------------------------------------------------------- bm25 + pointer


def test_bm25_hits_rank_and_carry_a_resolvable_pointer(tmp_path: Path):
    from helpers.core.db import connect

    db = _notes_db(tmp_path)
    conn = connect(db, read_only=True, wal=False)
    try:
        hits = nq.bm25_hits(conn, "mojo", 10)
    finally:
        conn.close()
    assert hits, "no hits for a term that is in the fixture"
    top = hits[0]
    assert top["path"] == "a.md"
    assert isinstance(top["line"], int), f"pointer lost its line: {top}"
    assert top["title"] == "Alpha" and top["sector"] == "Tech"
    assert "mojo" in top["head"], top["head"]


def test_search_pointer_matches_the_anchor_it_came_from(tmp_path: Path, monkeypatch):
    """path:line must name the section the hit came from — a wrong anchor
    sends the caller to the wrong part of the right file."""
    monkeypatch.setattr(nq, "semantic_hits", lambda *_a, **_k: ([], "stubbed"))
    out = nq.search(_notes_db(tmp_path), "mojo", limit=5)
    assert out["mode"] == "bm25"
    got = {(r["path"], r["line"]) for r in out["results"]}
    assert ("a.md", 3) in got, got
    assert ("a.md", 9) in got, "the second section of a.md must be its own hit"


# ------------------------------------------------------------------- fusion


def _hit(path: str, line: int | None, score: float | None = None) -> dict:
    return {
        "path": path,
        "line": line,
        "score": score,
        "head": f"{path} text",
        "title": "T",
        "section": "S",
        "sector": "Sec",
        "doc_type": "company",
    }


def test_fuse_is_per_section_by_default():
    """Two sections of one note are two candidates — an agent wants the
    exact section, not a collapsed note."""
    fused = nq.fuse([_hit("a.md", 3, 0.5), _hit("a.md", 9, 0.4)], [_hit("b.md", 4, 0.9)], limit=10)
    assert {(f["path"], f["line"]) for f in fused} == {("a.md", 3), ("a.md", 9), ("b.md", 4)}


def test_fuse_per_note_collapses_to_the_best_section():
    fused = nq.fuse(
        [_hit("a.md", 3, 0.5), _hit("a.md", 9, 0.4)],
        [_hit("b.md", 4, 0.9)],
        limit=10,
        per_note=True,
    )
    keys = [(f["path"], f["line"]) for f in fused]
    assert len(keys) == len({p for p, _ in keys}), "per_note must not repeat a note"
    assert ("a.md", 3) in keys, "the better-scoring section survives"


def test_fuse_ranks_a_hit_present_in_both_legs_first():
    fused = nq.fuse(
        [_hit("a.md", 3, 0.5), _hit("b.md", 4, 0.4)],
        [_hit("b.md", 4, 0.9), _hit("c.md", 5, 0.8)],
        limit=3,
    )
    assert fused[0]["path"] == "b.md", "b.md is in both legs, so RRF must favour it"


def test_fuse_keeps_prose_from_the_bm25_leg():
    """A semantic-only hit carries no prose; the fused row must still be
    renderable, so the bm25 row is borrowed when the keys line up."""
    fused = nq.fuse([_hit("a.md", 3, 0.5)], [{"path": "a.md", "line": 3, "score": 0.9}], limit=5)
    assert fused[0]["head"] and fused[0]["title"] == "T"


# --------------------------------------------------------------- degradation


def test_search_degrades_to_bm25_when_the_matrix_is_stale(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("matrix stale — run rebuild-note-search")

    monkeypatch.setattr(nq, "load_notes_matrix", _boom)
    out = nq.search(_notes_db(tmp_path), "mojo", limit=3)
    assert out["mode"] == "bm25"
    assert "stale" in out["note"]
    assert out["results"], "the lexical leg must still answer"


def test_search_hybrid_uses_both_legs_when_both_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        nq, "semantic_hits", lambda *_a, **_k: ([{"path": "b.md", "line": 4, "score": 0.9}], "")
    )
    out = nq.search(_notes_db(tmp_path), "mojo", limit=5)
    assert out["mode"] == "hybrid"
    paths = {r["path"] for r in out["results"]}
    assert {"a.md", "b.md"} <= paths, paths


# ---------------------------------------------------------------------- CLI


def test_cli_json_shape(tmp_path, capsys):
    db = _notes_db(tmp_path)
    rc = nq.main(["mojo", "--db", str(db), "--json", "--bm25", "--limit", "2"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "bm25"
    assert payload["results"] and "path" in payload["results"][0]


def test_cli_reports_no_hits_without_failing(tmp_path, capsys):
    rc = nq.main(["zzzznotpresent", "--db", str(_notes_db(tmp_path)), "--bm25"])
    assert rc == 0
    assert "no hits" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--per-note"])
def test_cli_accepts_flag(tmp_path, monkeypatch, flag, capsys):
    monkeypatch.setattr(nq, "semantic_hits", lambda *_a, **_k: ([], "stubbed"))
    rc = nq.main(["mojo", "--db", str(_notes_db(tmp_path)), flag, "--bm25"])
    assert rc == 0
