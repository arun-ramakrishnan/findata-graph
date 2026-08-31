"""Fuzz tests — frontmatter parser invariants.

Property-based tests (via Hypothesis) for `helpers/core/frontmatter.py`.

Invariants pinned:
  1. strip_frontmatter / split_frontmatter / split_frontmatter_with_title /
     extract_tags NEVER raise on arbitrary text.
  2. strip_frontmatter is idempotent: strip(strip(x)) == strip(x).
  3. strip_frontmatter result never starts with "---\n" (all FM consumed).
  4. split_frontmatter reconstructs: opening + yaml + rest == original (when FM present).
  5. extract_tags always returns a list of non-empty strings.
  6. split_frontmatter_with_title title is None or a non-empty string.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from helpers.core.frontmatter import (
    strip_frontmatter,
    split_frontmatter,
    split_frontmatter_with_title,
    extract_tags,
)

# Restrict to printable-ish chars to avoid degenerate binary noise
# while still covering whitespace, unicode, and markdown punctuation.
_text_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=0,
    max_size=500,
)


# ---------------------------------------------------------------------------
# Invariant 1: never raises
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_strip_frontmatter_never_raises(text: str):
    """No arbitrary text should cause strip_frontmatter to raise."""
    strip_frontmatter(text)


@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_split_frontmatter_never_raises(text: str):
    """No arbitrary text should cause split_frontmatter to raise."""
    split_frontmatter(text)


@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_split_with_title_never_raises(text: str):
    """No arbitrary text should cause split_frontmatter_with_title to raise."""
    split_frontmatter_with_title(text)


@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_extract_tags_never_raises(text: str):
    """No arbitrary text should cause extract_tags to raise."""
    extract_tags(text)


# ---------------------------------------------------------------------------
# Invariant 2: strip_frontmatter idempotent
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_strip_frontmatter_idempotent(text: str):
    """strip(strip(x)) == strip(x)."""
    once = strip_frontmatter(text)
    twice = strip_frontmatter(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Invariant 3: stripped result never starts with ---
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_strip_frontmatter_no_leading_dashes(text: str):
    """After stripping, the result must not start with '---\n'."""
    result = strip_frontmatter(text)
    assert not result.startswith("---\n")


# ---------------------------------------------------------------------------
# Invariant 4: split_frontmatter reconstructs original when FM present
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_split_frontmatter_reconstruction(text: str):
    """When frontmatter is detected, parts concatenate back to original."""
    opening, yaml_body, rest = split_frontmatter(text)
    if opening:  # frontmatter was found
        assert opening == "---"
        assert opening + yaml_body + rest == text


# ---------------------------------------------------------------------------
# Invariant 5: no-FM passthrough
# ---------------------------------------------------------------------------
@settings(max_examples=100, deadline=None)
@given(st.text(min_size=1, max_size=100).filter(lambda t: not t.startswith("---")))
def test_fuzz_split_frontmatter_no_fm_passthrough(text: str):
    """Text not starting with --- returns ('', '', text)."""
    opening, yaml_body, rest = split_frontmatter(text)
    assert opening == ""
    assert yaml_body == ""
    assert rest == text


# ---------------------------------------------------------------------------
# Invariant 6: extract_tags always returns list of non-empty strings
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_extract_tags_returns_nonempty_strings(text: str):
    """Every tag in the result is a non-empty stripped string."""
    tags = extract_tags(text)
    assert isinstance(tags, list)
    for tag in tags:
        assert isinstance(tag, str)
        assert tag.strip() == tag  # already stripped
        assert tag != ""


# ---------------------------------------------------------------------------
# Invariant 7: split_frontmatter_with_title title type
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(_text_st)
def test_fuzz_split_with_title_type(text: str):
    """Title is None or a non-empty string."""
    title, body = split_frontmatter_with_title(text)
    assert title is None or (isinstance(title, str) and title != "")


# ---------------------------------------------------------------------------
# Structured frontmatter: generate valid-ish FM blocks
# ---------------------------------------------------------------------------
_fm_text = st.builds(
    lambda yaml_body, rest: f"---\n{yaml_body}\n---\n{rest}",
    st.text(min_size=0, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
    st.text(min_size=0, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
)


@settings(max_examples=100, deadline=None)
@given(_fm_text)
def test_fuzz_structured_fm_strip_consumes_block(text: str):
    """strip_frontmatter on a well-formed FM block removes the --- ... --- prefix."""
    result = strip_frontmatter(text)
    # Result should not start with ---
    assert not result.startswith("---\n")
    # And should not contain the original yaml_body verbatim as a prefix


@settings(max_examples=100, deadline=None)
@given(_fm_text)
def test_fuzz_structured_fm_split_reconstructs(text: str):
    """split_frontmatter on structured FM reconstructs the original."""
    opening, yaml_body, rest = split_frontmatter(text)
    if opening:
        assert opening + yaml_body + rest == text


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(blacklist_categories=("Cs", "Cc")),  # no control chars in tags
        ),
        min_size=0,
        max_size=5,
    ),
    st.text(min_size=0, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
)
def test_fuzz_extract_tags_from_valid_block(tag_values, body):
    """extract_tags on a tags: block returns exactly the tag values."""
    # Filter out whitespace-only tags (extract_tags strips them).
    tag_values = [t.strip() for t in tag_values if t.strip()]
    if not tag_values:
        tags_yaml = ""
    else:
        tags_yaml = "\ntags:\n" + "\n".join(f"- {t}" for t in tag_values)
    text = f"---\ntitle: Test\n{tags_yaml}\n---\n{body}"
    tags = extract_tags(text)
    assert tags == tag_values
