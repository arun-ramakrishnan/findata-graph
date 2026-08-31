"""Unit tests for helpers/core/frontmatter.py."""

from __future__ import annotations
import sys
from pathlib import Path

HELPERS = Path(__file__).resolve().parents[1] / "helpers" / "core"
sys.path.insert(0, str(HELPERS))
import frontmatter as fm  # noqa: E402


# ---------------------------------------------------------------------------
# split_frontmatter
# ---------------------------------------------------------------------------
def test_split_frontmatter_no_frontmatter():
    """Text without --- prefix returns empty strings."""
    dashes, body, rest = fm.split_frontmatter("Hello world")
    assert dashes == ""
    assert body == ""
    assert rest == "Hello world"


def test_split_frontmatter_unclosed_frontmatter():
    """--- with no closing --- returns empty strings."""
    dashes, body, rest = fm.split_frontmatter("---\ntitle: Foo\n")
    assert dashes == ""
    assert body == ""
    assert rest == "---\ntitle: Foo\n"


def test_split_frontmatter_normal():
    """Normal frontmatter splits into dashes, body, rest."""
    text = "---\ntitle: Foo\ntags:\n  - a\n---\nBody text"
    dashes, body, rest = fm.split_frontmatter(text)
    assert dashes == "---"
    assert "title: Foo" in body
    assert rest.startswith("\n") or rest.startswith("Body") or "Body" in rest


# ---------------------------------------------------------------------------
# split_frontmatter_with_title
# ---------------------------------------------------------------------------
def test_split_frontmatter_with_title_no_frontmatter():
    """No frontmatter returns (None, text)."""
    title, body = fm.split_frontmatter_with_title("Plain text")
    assert title is None
    assert body == "Plain text"


def test_split_frontmatter_with_title_unclosed():
    """Unclosed frontmatter returns (None, text)."""
    title, body = fm.split_frontmatter_with_title("---\ntitle: Foo\n")
    assert title is None
    assert body == "---\ntitle: Foo\n"


def test_split_frontmatter_with_title_found():
    """Title extracted from frontmatter."""
    text = "---\ntitle: My Note\n---\nBody"
    title, body = fm.split_frontmatter_with_title(text)
    assert title == "My Note"
    assert "Body" in body


def test_split_frontmatter_with_title_quoted():
    """Title with quotes is stripped."""
    text = '---\ntitle: "Quoted Title"\n---\nBody'
    title, body = fm.split_frontmatter_with_title(text)
    assert title == "Quoted Title"


# ---------------------------------------------------------------------------
# extract_tags
# ---------------------------------------------------------------------------
def test_extract_tags_no_frontmatter():
    """No frontmatter returns empty list."""
    assert fm.extract_tags("Just plain text\n") == []


def test_extract_tags_empty_tags():
    """Frontmatter with tags: but no items → empty list."""
    text = "---\ntitle: Foo\ntags:\n---\nBody"
    assert fm.extract_tags(text) == []


def test_extract_tags_blank_line_continues():
    """A blank line inside tags section is skipped (does not stop collection)."""
    text = "---\ntags:\n  - alpha\n\n  - beta\n---\nBody"
    tags = fm.extract_tags(text)
    assert tags == ["alpha", "beta"]


def test_extract_tags_non_tag_line_stops():
    """A non-tag, non-blank line sets in_tags=False."""
    text = "---\ntags:\n  - alpha\nother: value\n  - beta\n---\nBody"
    tags = fm.extract_tags(text)
    assert tags == ["alpha"]


def test_extract_tags_normal():
    """Normal tag extraction."""
    text = "---\ntitle: Foo\ntags:\n  - india\n  - banking\n---\nBody"
    assert fm.extract_tags(text) == ["india", "banking"]


# ---------------------------------------------------------------------------
# strip_frontmatter
# ---------------------------------------------------------------------------
def test_strip_frontmatter_present():
    """Strips frontmatter block."""
    text = "---\ntitle: Foo\n---\nBody here"
    assert fm.strip_frontmatter(text) == "Body here"


def test_strip_frontmatter_absent():
    """No frontmatter → text unchanged."""
    assert fm.strip_frontmatter("No frontmatter") == "No frontmatter"
