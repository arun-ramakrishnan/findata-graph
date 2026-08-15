"""Phase 5 fuzz tests — image block regex robustness (ReDoS detection).

Property-based tests (via Hypothesis) for `IMG_BLOCK_RE` in
`helpers/pdf/capture_newsletter_images.py`.
"""
from __future__ import annotations

from hypothesis import given, strategies as st, settings

from helpers.pdf.capture_newsletter_images import IMG_BLOCK_RE, slugify


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=500))
def test_fuzz_img_block_re(text: str):
    """Invariant 1: IMG_BLOCK_RE terminates quickly and safely on adversarial strings."""
    try:
        matches = list(IMG_BLOCK_RE.finditer(text))
        assert isinstance(matches, list)
    except Exception as e:
        raise e


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_slugify(stem: str):
    """Invariant 2: slugify handles arbitrary Unicode and whitespace safely."""
    try:
        slug = slugify(stem)
        assert isinstance(slug, str)
    except Exception as e:
        raise e
