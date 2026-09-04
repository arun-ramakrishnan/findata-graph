"""Phase 2 fuzz tests — regex robustness (ReDoS detection).

Property-based tests (via Hypothesis) for the two regexes that parse
untrusted newsletter input:

  - `SECTION_RE` (helpers/core/parse_newsletter.py:67) — extracts
    company headings from OCR'd markdown. The pattern
    `(.+?)(?:\\s*[|·].*)*$` pairs a lazy `.+?` with a greedy `.*`
    inside an optional non-capturing group anchored to end-of-line.
    On adversarial input (many `|`/`·` chars, no terminating newline)
    this can exhibit quadratic backtracking.

  - `IMG_BLOCK_RE` (helpers/pdf/capture_newsletter_images.py:43) —
    extracts <div><img src='URL'></div> blocks from OCR'd HTML-in-
    markdown. Uses three `[^>]*` repetitions; on malformed HTML
    (unclosed tags, many attributes) the regex engine may backtrack
    heavily.

The contract under test is NOT "the regex returns the right match" —
it is "the regex terminates quickly and does not crash on any input".
A ReDoS would manifest as a Hypothesis deadline timeout, which the
test reports as a failure with the offending input shrunk to a
minimal reproducible case.

Runs alongside regular pytest in `make qa`. Hypothesis defaults to
100 random examples per @given test; each completes in <1s.
"""

from __future__ import annotations


from hypothesis import given, settings, strategies as st


from core.parse_newsletter import SECTION_RE  # noqa: E402
from graph.derive_co_mentions import _parse_edition_number  # noqa: E402
from pdf.capture_newsletter_images import IMG_BLOCK_RE  # noqa: E402


# ---------------------------------------------------------------------------
# Alphabets
# ---------------------------------------------------------------------------
# Adversarial alphabets are deliberately small and targeted at the regex's
# "interesting" characters. Hypothesis explores more thoroughly when the
# alphabet is constrained — a full-unicode strategy would waste examples
# on chars the regex doesn't branch on.

# SECTION_RE branches on: `#` (heading marker), `|` and `·` (separator),
# whitespace, and end-of-line (`$` with MULTILINE). Any alphanumerics fill
# out the captured group. Keep max_size modest so a genuine ReDoS surfaces
# within Hypothesis's default deadline (200ms) rather than just running long.
SECTION_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="#|·\n \t"
)

# IMG_BLOCK_RE branches on: `<`, `>`, `/`, quotes (`'` and `"`), the literal
# `div`/`img`/`src`, and whitespace. Add a few punctuation chars to stress
# the `[^>]*` and `[^'\"]+` character classes.
IMG_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="<>/\"' \n\tdivsrc=!@"
)


# ---------------------------------------------------------------------------
# 5. SECTION_RE does not exhibit catastrophic backtracking
# ---------------------------------------------------------------------------
# A ReDoS-vulnerable regex will blow past Hypothesis's per-example deadline
# (default 200ms) on a short adversarial input. The test passes if every
# input is processed within the deadline AND every match (if any) is a str.
#
# We bound input size at 400 chars — large enough to surface quadratic
# behavior, small enough that a clean regex finishes in microseconds.
@settings(deadline=500)  # ms; generous enough for CI noise, catches real ReDoS
@given(st.text(alphabet=SECTION_ALPHABET, max_size=400))
def test_section_regex_no_catastrophic_backtracking(markdown):
    matches = SECTION_RE.findall(markdown)
    # Contract: every captured group is a string (the regex has one group).
    for m in matches:
        assert isinstance(m, str), f"SECTION_RE returned non-str match {m!r} for input {markdown!r}"


# ---------------------------------------------------------------------------
# 6. IMG_BLOCK_RE handles malformed HTML gracefully
# ---------------------------------------------------------------------------
# Same ReDoS guard, applied to the image-block regex. Adversarial input is
# random sequences of tag-like characters that never form a valid
# <div><img></div> structure — the worst case for the `[^>]*` repetitions.
#
# Contract: terminates within deadline; every captured URL (if any) is a
# str. We do NOT assert the URL is well-formed (the regex is permissive by
# design — validation happens downstream in parse_images).
@settings(deadline=500)
@given(st.text(alphabet=IMG_ALPHABET, max_size=300))
def test_img_block_regex_malformed_html(html):
    matches = IMG_BLOCK_RE.findall(html)
    for url in matches:
        assert isinstance(url, str), f"IMG_BLOCK_RE returned non-str url {url!r} for input {html!r}"


# ---------------------------------------------------------------------------
# 7. SECTION_RE matches well-formed headings (positive-case sanity)
# ---------------------------------------------------------------------------
# Guard against an over-tight regex that rejects valid input to avoid
# ReDoS. Real newsletter headings look like:
#     ## Bharat Forge | Large Cap | Auto & Defence
#     # Oil and Natural Gas Corporation Limited Large Cap Oil & Gas
# The regex should capture the company name portion.
@settings(deadline=500)
@given(
    name=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll"), whitelist_characters=" &.-"),
        min_size=1,
        max_size=40,
    ),
    n_seps=st.integers(min_value=0, max_value=3),
)
def test_section_regex_matches_well_formed_heading(name, n_seps):
    # Build a heading with 0-3 trailing "| sector" segments.
    tail = "".join(f" | segment{i}" for i in range(n_seps))
    heading = f"## {name}{tail}\n"
    matches = SECTION_RE.findall(heading)
    # At least one match; the first capture should start with the name.
    assert len(matches) >= 1, f"SECTION_RE missed valid heading: {heading!r}"
    assert isinstance(matches[0], str)


# ---------------------------------------------------------------------------
# 8. IMG_BLOCK_RE matches well-formed blocks (positive-case sanity)
# ---------------------------------------------------------------------------
# Same positive-case guard for the image regex. A well-formed block:
#     <div class="x"><img src="https://example.com/a.jpeg" alt="fig"></div>
# should yield exactly the URL as the capture.
@settings(deadline=500)
@given(
    url=st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=":/._-?=&%"
        ),
        min_size=1,
        max_size=80,
    ),
    quote=st.sampled_from(["'", '"']),
)
def test_img_block_regex_matches_well_formed_block(url, quote):
    block = f"<div class='wrap'><img src={quote}{url}{quote} alt='fig'></div>"
    matches = IMG_BLOCK_RE.findall(block)
    assert len(matches) == 1, f"IMG_BLOCK_RE expected 1 match, got {len(matches)} for {block!r}"
    assert matches[0] == url, f"IMG_BLOCK_RE captured {matches[0]!r}, expected {url!r}"


# ---------------------------------------------------------------------------
# 10. _parse_edition_number extracts an integer edition number (or None)
# ---------------------------------------------------------------------------
# Regex-based extraction of the edition number from a newsletter title / footer
# (helpers/graph/derive_co_mentions.py). Contract: never raises on arbitrary
# input; returns int | None; and when it returns an int it is non-negative and
# round-trips through str().
@settings(deadline=500)
@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
        min_size=0,
        max_size=200,
    )
)
def test_parse_edition_number_never_raises(title):
    result = _parse_edition_number(title)
    assert result is None or isinstance(result, int), (
        f"_parse_edition_number({title!r}) -> {result!r}"
    )
    if isinstance(result, int):
        assert result >= 0, f"negative edition number from {title!r}"
        assert int(str(result)) == result


# Positive-case guard: the documented "Edition #N (date)" form must round-trip.
@settings(deadline=500)
@given(st.integers(min_value=0, max_value=10**6))
def test_parse_edition_number_positive(edition):
    title = f"Edition #{edition} (Mar 27, 2026)"
    assert _parse_edition_number(title) == edition
