"""Phase 5 fuzz tests — image block regex robustness (ReDoS detection).

Property-based tests (via Hypothesis) for `IMG_BLOCK_RE` in
`helpers/pdf/capture_newsletter_images.py`.
Strengthened 2026-08-22 (integration_fuzz_enhancement B7): the original
try/except-raise wrappers asserted nothing — these now pin real
invariants: bounded runtime (deadline, the ReDoS guard), match group
shape, and slugify's no-whitespace/idempotence contract.
"""
from __future__ import annotations

from hypothesis import given, strategies as st, settings

from helpers.pdf.capture_newsletter_images import IMG_BLOCK_RE, slugify

_TEXT = st.text(min_size=0, max_size=500)


@settings(max_examples=100, deadline=500)
@given(_TEXT)
def test_fuzz_img_block_re(text):
    """Invariant 1: IMG_BLOCK_RE terminates quickly (deadline=500, the
    ReDoS guard) and every match captures a quote-free non-empty URL."""
    matches = list(IMG_BLOCK_RE.finditer(text))
    for m in matches:
        url = m.group(1)
        assert url
        assert "'" not in url and '"' not in url


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_fuzz_slugify(stem):
    """Invariant 2: slugify output is whitespace-free, underscore-trimmed,
    has no collapsed runs, and is idempotent."""
    slug = slugify(stem)
    assert isinstance(slug, str)
    assert not any(c.isspace() for c in slug)
    assert slug == slug.strip("_")
    assert "__" not in slug
    assert slugify(slug) == slug
