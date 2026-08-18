"""Fuzz tests - Paddle PDF->markdown pipeline transforms.

Property-based tests (via Hypothesis) for the pure functions in
`helpers/pdf/pdf_conv_md.py`. These pin "never raises" and output-contract
invariants for the transforms that operate on untrusted/arbitrary input
(Paddle OCR JSONL output, newsletter markdown). Runs inside `make qa`.

Invariants pinned (see doc/improvements/archive/pdf_conv_md_hardening_fuzz.md):
  1. slugify: never raises on arbitrary text; result has no whitespace, no "__",
     no leading/trailing "_".
  2. parse_pages: never raises on arbitrary JSON-ish list; returns a list of
     4-key dicts; well-formed lines are preserved.
  3. image_extension: never raises; returns a string starting with ".".
  4. plan_images: never raises on string-valued image maps; returns (dict, int);
     counter advances by len(images).
  5. to_wikilinks: never raises on arbitrary text + well-shaped plan; returns str.
  6. resolve_markdown: never raises; returns str.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from hypothesis import given, settings, strategies as st

from helpers.pdf.pdf_conv_md import (
    image_extension,
    parse_pages,
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

# Arbitrary JSON-ish values for stressing parse_pages (dict/list/scalars).
_json_st = st.recursive(
    st.one_of(
        st.text(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.booleans(),
        st.none(),
    ),
    lambda children: st.one_of(
        st.lists(children), st.dictionaries(st.text(), children)
    ),
    max_leaves=12,
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
# 2. parse_pages
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(st.lists(_json_st))
def test_fuzz_parse_pages_never_raises(lines):
    pages = parse_pages(lines)
    assert isinstance(pages, list)
    for p in pages:
        assert isinstance(p, dict)
        assert set(p) == {"prunedResult", "markdown", "outputImages", "inputImage"}


@settings(max_examples=100, deadline=None)
@given(_text_st, st.dictionaries(_text_st, _text_st))
def test_fuzz_parse_pages_well_formed_preserved(text, images):
    line = {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {},
                    "markdown": {"text": text, "images": images},
                    "outputImages": {},
                    "inputImage": "https://x/in.png",
                }
            ]
        }
    }
    pages = parse_pages([line])
    assert len(pages) == 1
    assert pages[0]["markdown"]["text"] == text
    assert pages[0]["markdown"]["images"] == images


# ---------------------------------------------------------------------------
# 3. image_extension
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st, st.one_of(st.none(), _text_st))
def test_fuzz_image_extension(url, content_type):
    ext = image_extension(url, content_type)
    assert isinstance(ext, str)
    assert ext.startswith(".")


# ---------------------------------------------------------------------------
# 4. plan_images
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
# 5. to_wikilinks
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
# 6. resolve_markdown
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st, st.dictionaries(_text_st, _text_st))
def test_fuzz_resolve_markdown(text, images):
    out = resolve_markdown(text, images)
    assert isinstance(out, str)
