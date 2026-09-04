#!/usr/bin/env python3
"""Fuzz tests for rebuild_note_search's body cleaner + typing helpers
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B5).

_clean_body runs over every newsletter doc in the vault; _doc_type_for /
_newsletter_title classify every path; the _carry_row fast path decides
incremental no-op cycles (P2.2). None had properties.
"""

from __future__ import annotations

import re
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st


from helpers.maintenance import rebuild_note_search as rns  # noqa: E402

_SETTINGS = settings(max_examples=75, deadline=None)

_TEXT = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=300,
)


@_SETTINGS
@given(_TEXT)
def test_clean_body_idempotent_and_never_longer(body):
    once = rns._clean_body(body)
    twice = rns._clean_body(once)
    assert once == twice
    assert len(once) <= len(body)


@_SETTINGS
@given(_TEXT)
def test_clean_body_strips_noise_and_collapses_ws(body):
    out = rns._clean_body(body)
    assert "<img" not in out and "<div" not in out
    assert "\n" not in out and "\t" not in out
    assert "  " not in out  # whitespace collapsed to single


@_SETTINGS
@given(
    st.lists(
        st.sampled_from(
            [
                "Companies",
                "Sectors",
                "Super_Sectors",
                "The_Chatter",
                "Points_And_Figures",
                "The_PlotLines",
                "unmapped_tree",
            ]
        ),
        min_size=1,
        max_size=4,
    ),
    st.text(
        st.characters(blacklist_categories=("Cs",), blacklist_characters="/\\\0"),
        min_size=1,
        max_size=20,
    ).map(lambda s: s.replace("/", "")),
)
def test_doc_type_for_typed_and_path_based(parts, name):
    """_doc_type_for is a pure path-prefix classifier: typed output,
    deterministic, and unmapped trees map to None."""
    rel = Path("/".join(parts)) / (name + ".md")
    d1 = rns._doc_type_for(rel)
    d2 = rns._doc_type_for(rel)
    assert d1 == d2
    assert d1 is None or isinstance(d1, str)
    if parts[0] == "unmapped_tree":
        assert d1 is None


@_SETTINGS
@given(_TEXT)
def test_newsletter_title_typed(text):
    t = rns._newsletter_title(text)
    assert isinstance(t, str)
    assert "\n" not in t
    if t:
        assert "# " not in t  # the H1 marker is stripped


@_SETTINGS
@given(
    st.sampled_from(
        ["company", "sector", "super_sector", "chatter", "points_and_figures", "plotlines"]
    ),
    _TEXT,
    _TEXT,
)
def test_carry_row_is_db_consistency_only(dtype, title_a, title_b):
    """The P2.2 carry contract: an entity row carries iff its entities-row
    metadata matches the stored row; non-entity docs always carry."""
    row = (dtype, "findata/x.md", title_a, "sec", "content", None)
    ent_by_path: dict[str, tuple[str, str | None]] = {"findata/x.md": (title_a, "sec")}
    ent_changed: dict[str, tuple[str, str | None]] = {"findata/x.md": (title_b, "sec")}
    if dtype in ("company", "sector", "super_sector"):
        assert rns._carry_row(row, dtype, "findata/x.md", ent_by_path) is True
        same_meta = title_b == title_a
        assert rns._carry_row(row, dtype, "findata/x.md", ent_changed) is same_meta
    else:
        assert rns._carry_row(row, dtype, "findata/x.md", ent_changed) is True


@_SETTINGS
@given(_TEXT)
def test_newsletter_title_h1_vs_fallback(text):
    """With an H1 present the title is its capture; without one the
    fallback is the empty string (never a guess)."""
    t = rns._newsletter_title(text)
    has_h1 = re.search(r"^# (.+)$", text, re.MULTILINE) is not None
    assert bool(t) == has_h1
    if has_h1:
        m = re.search(r"^# (.+)$", text, re.MULTILINE)
        assert m is not None
        assert t == m.group(1).strip()
