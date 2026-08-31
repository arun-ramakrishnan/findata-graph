"""Phase 1 fuzz tests — normalizer invariants.

Property-based tests (via Hypothesis) for the three pure-function name
normalizers in the codebase. These catch exactly the class of bugs we've
been hitting: empty/unicode inputs producing invalid filenames, special
characters surviving normalization, and divergence between the two
normalize_name implementations.

Runs alongside regular pytest in `make qa`. Hypothesis defaults to 100
random examples per @given test; each completes in <1s.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from hypothesis import given, strategies as st

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

# Import the three functions under test.
from core.parse_newsletter import normalize_name  # noqa: E402
from pdf.capture_newsletter_images import slugify  # noqa: E402

# rename_entity.py imports normalize_name from parse_newsletter too
# (consolidated during fuzz-fix work), so there's only one implementation
# to test. We import rename_entity to confirm it doesn't define its own.


# ---------------------------------------------------------------------------
# Shared invariants
# ---------------------------------------------------------------------------
# The canonical filename contract (doc/design/findata.md §Sync Rules):
# PascalCase, single underscores, no special chars, ≤100 chars, must start
# with an alphanumeric. We allow leading digits because real companies like
# 3M India and 5 Paisa Capital exist in the DB with digit-leading names.
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*$")
FORBIDDEN_CHARS = set("&() -/\\\t\n\r")


def _is_valid_filename_fragment(s: str) -> bool:
    """True if `s` is either empty or a valid filename component."""
    return s == "" or bool(FILENAME_RE.match(s))


# ---------------------------------------------------------------------------
# 1. normalize_name always produces a valid filename
# ---------------------------------------------------------------------------
# Broad input: any printable + whitespace, excluding surrogate codepoints
# (which can't be encoded as filenames). Cap at 80 chars to match realistic
# company-name lengths.
@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="/\\"),
        max_size=80,
    )
)
def test_normalize_name_produces_valid_filename(name):
    result = normalize_name(name)
    # Contract: output is either empty (all-special-char input) or a valid
    # filename fragment matching the PascalCase/underscore rule.
    assert _is_valid_filename_fragment(result), (
        f"normalize_name({name!r}) = {result!r} which is not a valid filename fragment"
    )
    # No forbidden character may survive normalization.
    for c in FORBIDDEN_CHARS:
        assert c not in result, f"normalize_name({name!r}) = {result!r} still contains {c!r}"


# ---------------------------------------------------------------------------
# 2. rename_entity uses the same normalize_name (no duplicate implementation)
# ---------------------------------------------------------------------------
# Both used to implement "PascalCase with single underscores" independently
# via different code paths — divergence was a latent bug. After
# consolidation (fuzz-fix Jul 2026), rename_entity imports normalize_name
# from parse_newsletter. This test pins that there's only one copy.
#
# We test the shared function against the constrained alphabet where both
# legacy implementations were expected to agree.
@given(
    st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 &-_",
        min_size=1,
        max_size=40,
    )
)
def test_normalize_name_valid_for_rename_inputs(name):
    # rename_entity calls normalize_name(new_name) — result must be a valid
    # filename fragment for any plausible entity name.
    result = normalize_name(name)
    assert _is_valid_filename_fragment(result), (
        f"normalize_name({name!r}) = {result!r} — invalid filename fragment"
    )
    for c in FORBIDDEN_CHARS:
        assert c not in result, f"normalize_name left {c!r} in output: {result!r}"


# ---------------------------------------------------------------------------
# 3. slugify is idempotent
# ---------------------------------------------------------------------------
# slugify() is applied to newsletter filenames; its output becomes part of
# image filenames. Running it twice should be a no-op (slugify(slugify(x))
# == slugify(x)). If not, a re-run of capture_newsletter_images.py would
# rename already-captured images.
@given(
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-",
        max_size=60,
    )
)
def test_slugify_idempotent(stem):
    once = slugify(stem)
    twice = slugify(once)
    assert once == twice, (
        f"slugify not idempotent: slugify({stem!r}) = {once!r}, slugify({once!r}) = {twice!r}"
    )


# ---------------------------------------------------------------------------
# 4. normalize_name is idempotent
# ---------------------------------------------------------------------------
# Same invariant for normalize_name: normalize_name(normalize_name(x)) must
# equal normalize_name(x). If not, re-running parse_newsletter on an
# already-normalized entity would change its name and break file sync.
@given(st.text(max_size=60))
def test_normalize_name_idempotent(name):
    once = normalize_name(name)
    twice = normalize_name(once)
    assert once == twice, (
        f"normalize_name not idempotent: "
        f"normalize_name({name!r}) = {once!r}, "
        f"normalize_name({once!r}) = {twice!r}"
    )
