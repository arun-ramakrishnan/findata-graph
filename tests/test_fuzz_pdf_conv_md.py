"""Fuzz tests - PDF->markdown pipeline transforms.

Property-based tests (via Hypothesis) for the pure functions in
`helpers/pdf/pdf_conv_md.py`. These pin "never raises" and output-contract
invariants for the transforms that operate on untrusted/arbitrary input
(engine markdown, newsletter markdown). Runs inside `make qa`.

Invariants pinned (see doc/improvements/archive/pipeline/pdf_conv_md_hardening_fuzz.md):
  1. slugify: never raises on arbitrary text; result has no whitespace, no "__",
     no leading/trailing "_".
  2. image_extension: never raises; returns a string starting with ".".
  3. plan_images: never raises on string-valued image maps; returns (dict, int);
     counter advances by len(images).
  4. to_wikilinks: never raises on arbitrary text + well-shaped plan; returns str.
  5. resolve_markdown: never raises; returns str.

(`parse_pages` legs retired with the Paddle cut, D2 2026-10-06 — the Paddle
JSONL parser no longer exists; the teleocr engine's HTTP contract is
stub-tested in test_teleocr_engine.py.)
"""

from __future__ import annotations


from hypothesis import given, settings, strategies as st

from helpers.pdf.pdf_conv_md import (
    image_extension,
    plan_images,
    resolve_markdown,
    slugify,
    to_wikilinks,
)


# Printable-ish text (avoids surrogate/control noise) with unicode + markdown
# punctuation - matches the convention in test_fuzz_frontmatter.py.
_text_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=500,
)


# ---------------------------------------------------------------------------
# 1. slugify
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_slugify(text: str):
    out = slugify(text)
    assert isinstance(out, str)
    assert not any(c.isspace() for c in out)
    assert "__" not in out
    assert not out.startswith("_")
    assert not out.endswith("_")


# ---------------------------------------------------------------------------
# 2. image_extension
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st, st.one_of(st.none(), _text_st))
def test_fuzz_image_extension(url, content_type):
    ext = image_extension(url, content_type)
    assert isinstance(ext, str)
    assert ext.startswith(".")


# ---------------------------------------------------------------------------
# 3. plan_images
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(
    st.integers(min_value=0, max_value=1000),
    st.dictionaries(_text_st, _text_st),
    st.integers(min_value=0, max_value=1000),
    _text_st,
)
def test_fuzz_plan_images(page_index, images, counter, stem):
    plan, new_counter = plan_images(page_index, images, counter, stem)
    assert isinstance(plan, dict)
    assert isinstance(new_counter, int)
    assert new_counter == counter + len(images)
    for rel, item in plan.items():
        assert item["filename"].startswith(stem)
        assert item["url"] == images[rel]


# ---------------------------------------------------------------------------
# 4. to_wikilinks
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(
    _text_st,
    st.dictionaries(
        _text_st,
        st.fixed_dictionaries({"filename": _text_st, "url": _text_st}),
    ),
)
def test_fuzz_to_wikilinks(text, plan):
    out = to_wikilinks(text, plan)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# 5. resolve_markdown
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st, st.dictionaries(_text_st, _text_st))
def test_fuzz_resolve_markdown(text, images):
    out = resolve_markdown(text, images)
    assert isinstance(out, str)
