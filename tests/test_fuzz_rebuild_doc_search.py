#!/usr/bin/env python3
"""Fuzz tests for rebuild_doc_search's section chunker + MATCH generator
(doc_search_embeddings #148; mirrors test_fuzz_rebuild_note_search.py).

_split_sections runs over every file in doc/ at rebuild time — its chunks
ARE the index rows and `anchor` is the deep-link line number, so a chunker
bug corrupts every search hit. fts_match_expr turns arbitrary agent/user
queries into an FTS5 MATCH expression; a syntax error there would take down
both /api/docs/search and doc_query. Neither had properties.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402

_SETTINGS = settings(max_examples=75, deadline=None)

_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=300,
)

# Chunker inputs: arbitrary line soup over the header alphabet — plain
# lines, '## ' headers (chunk boundaries), '### ' sub-headers (must NOT
# split, they belong to the parent section).
_LINE = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=40,
)
_MARKDOWN = st.lists(
    st.one_of(
        _LINE,
        _LINE.map(lambda s: "## " + s),
        _LINE.map(lambda s: "### " + s),
    ),
    min_size=0,
    max_size=25,
).map(lambda ls: "\n".join(ls))


@_SETTINGS
@given(_MARKDOWN)
def test_split_sections_anchors_point_at_headers(text):
    """Anchors are strictly-increasing 1-based line numbers; a header
    chunk's anchor is its own '## ' line and the stripped header text is
    the chunk title; chunk count never exceeds headers + preamble."""
    chunks = rds._split_sections(text)
    lines = text.split("\n")
    anchors = [a for _, a, _ in chunks]
    assert anchors == sorted(set(anchors))  # strictly increasing
    assert all(1 <= a <= len(lines) for a in anchors)
    for title, anchor, _ in chunks:
        line = lines[anchor - 1]
        if title == "" and anchor == 1 and not line.startswith("## "):
            continue  # the preamble chunk
        assert line.startswith("## "), (title, anchor, line)
        assert line[len("## ") :].strip() == title
    n_h2 = sum(1 for ln in lines if ln.startswith("## "))
    assert len(chunks) <= n_h2 + 1


@_SETTINGS
@given(_MARKDOWN)
def test_split_sections_loses_no_content_line(text):
    """Chunks partition the input lines in order; the only droppable chunk
    (whitespace-only preamble) never carries a non-whitespace line."""
    chunks = rds._split_sections(text)
    src = [ln for ln in text.split("\n") if ln.strip()]
    dst = [ln for _, _, body in chunks for ln in body.split("\n") if ln.strip()]
    assert src == dst


@_SETTINGS
@given(_MARKDOWN)
def test_split_sections_bodies_re_split_stable(text):
    """Each emitted body re-splits to exactly itself: a body can contain
    at most one '## ' line — its own first (any other would have split
    the original chunk), so re-chunking is the identity."""
    for title, _, body in rds._split_sections(text):
        assert body.strip()  # emitted chunks are never whitespace-only
        expected = title if body.startswith("## ") else ""
        assert rds._split_sections(body) == [(expected, 1, body)]


# One real FTS5 table for the MATCH-execution property (empty quoted
# tokens are valid; only the fully-empty expression is a syntax error,
# and search_docs guards that path — q with no tokens returns []).
_FTS = sqlite3.connect(":memory:")
_FTS.execute("CREATE VIRTUAL TABLE t USING fts5(body)")


@_SETTINGS
@given(_TEXT)
def test_fts_match_expr_executes_for_any_query(q):
    """The punctuation-safety contract: every whitespace token of an
    arbitrary query becomes a double-quoted FTS5 phrase, OR-joined —
    executing the expression never raises, and unquoted FTS5 syntax can
    never leak in from user input."""
    expr = rds.fts_match_expr(q)
    if not q.split():
        assert expr == ""  # caller (search_docs) returns [] for this
        return
    assert len(expr.split(" OR ")) == len(q.split())
    for part in expr.split(" OR "):
        assert part.startswith('"') and part.endswith('"')
    _FTS.execute("SELECT count(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()
